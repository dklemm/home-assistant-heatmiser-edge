"""Polls every configured thermostat on the bus, once per interval.

The coordinator stays dumb on purpose: it asks the hub for words and hands back
words. Decoding lives in `decode.py` and the entities, so the wire layer and the
presentation layer can each be tested without the other.

The one thing it *does* decide is which registers become entities, in
`platform_for()`. Every platform file filters on that single method, which is why
a register can never appear on two platforms, and why turning controls off is a
one-line change rather than six.

`data` always has an entry for every configured thermostat, answering or not.
That shape is the whole of per-unit availability: one stat going quiet leaves the
others updating normally, and only a bus that has gone entirely silent fails the
config entry.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BINARY,
    CONF_CONTROLS,
    CONF_MODEL,
    CONF_UNIT_ID,
    CONF_UNITS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MODEL_HEAT,
    MODEL_LABELS,
    NON_PROGRAMMABLE_MODES,
    NUMBERS,
    OPERATION_MODES,
    POLL_COUNT,
    POLL_START,
    PROGRAM_MODE_NON_PROGRAMMABLE,
    READ_ONLY_RW,
    REG_FIRMWARE,
    REG_HOLD_DURATION,
    REG_OPERATION_MODE,
    REG_PROGRAM_MODE,
    REG_PROGRAM_TYPE,
    REG_TEMP_FORMAT,
    SELECTS,
    SETTLE_PROBES,
    SUPPRESSED,
    SWITCHES,
)
from .hub import EdgeConnectionError, EdgeHub
from .registers import SCHEDULES, Reg, registers_for

_LOGGER = logging.getLogger(__name__)

type EdgeConfigEntry = ConfigEntry[EdgeCoordinator]


@dataclass(frozen=True)
class UnitConfig:
    """One thermostat as the user configured it."""

    unit_id: int
    model: str
    name: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnitConfig:
        unit_id = int(data[CONF_UNIT_ID])
        return cls(
            unit_id=unit_id,
            model=data[CONF_MODEL],
            name=data.get("name") or f"{MODEL_LABELS[data[CONF_MODEL]]} {unit_id}",
        )

    def as_dict(self) -> dict[str, Any]:
        return {CONF_UNIT_ID: self.unit_id, CONF_MODEL: self.model, "name": self.name}


@dataclass(frozen=True)
class UnitData:
    """One thermostat's last poll."""

    unit_id: int
    ok: bool
    words: dict[int, int] = field(default_factory=dict)
    fahrenheit: bool = False

    def get(self, register: int) -> int | None:
        return self.words.get(register)


def option(entry: ConfigEntry, key: str, default: Any) -> Any:
    """An option, falling back to what the config flow stored, then a default."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class EdgeCoordinator(DataUpdateCoordinator[dict[int, UnitData]]):
    """One poll of the whole bus, per interval."""

    config_entry: EdgeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: EdgeConfigEntry, hub: EdgeHub
    ) -> None:
        self.hub = hub
        self.units: list[UnitConfig] = [
            UnitConfig.from_dict(item) for item in option(entry, CONF_UNITS, [])
        ]
        self.controls_enabled: bool = option(entry, CONF_CONTROLS, True)
        # One pending settle read per thermostat - see `async_refresh_unit`.
        self._settling: dict[int, CALLBACK_TYPE] = {}
        # The weekly program, per unit, and deliberately *not* part of `data` -
        # see `async_read_schedule`. `schedule_read` is when each one was last
        # read, which is the honest state for the entity that carries it: the
        # program is not polled, so how fresh it is is a real question.
        self.schedules: dict[int, dict[int, int]] = {}
        self.schedule_read: dict[int, datetime] = {}
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    # ------------------------------------------------------------------
    # Which registers become entities
    # ------------------------------------------------------------------

    def platform_for(self, unit: UnitConfig, reg: Reg) -> str | None:
        """Which HA platform this register becomes, or None for no entity.

        Read-only registers always become an entity. A *writable* register
        becomes one only if a curated table in `const.py` names it, because the
        write goes to a live heating system and the stat accepts undocumented
        values without complaining.

        Every platform file filters on this one method, so a register can never
        end up on two platforms.
        """
        model = unit.model
        if reg.number in SUPPRESSED[model]:
            # Owned by the climate entity, a read-only mirror of a writable
            # register, or a hazard that never ships.
            return None
        if reg.access == "R":
            return "binary_sensor" if reg.number in BINARY[model] else "sensor"
        if platform := READ_ONLY_RW[model].get(reg.number):
            # Writable, but only the reading is trusted. Survives the controls
            # switch below, since nothing here can write.
            return platform
        if not self.controls_enabled:
            return None
        if reg.number in SELECTS[model]:
            return "select"
        if reg.number in SWITCHES[model]:
            return "switch"
        if reg.number in NUMBERS[model]:
            return "number"
        return None

    def entity_registers(self) -> list[tuple[UnitConfig, Reg]]:
        """Every (thermostat, register) pair that becomes an entity."""
        return [
            (unit, reg)
            for unit in self.units
            for reg in registers_for(unit.model)
            if self.platform_for(unit, reg) is not None
        ]

    def climate_units(self) -> list[UnitConfig]:
        """Only Heat stats get a climate entity.

        A Timer has no temperature at all, so hvac_modes of [off, heat] on one
        would be a lie. Its control surface is the register 32 switch and the
        register 33 select.
        """
        return [unit for unit in self.units if unit.model == MODEL_HEAT]

    def unit_config(self, unit_id: int) -> UnitConfig | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def unit_data(self, unit_id: int) -> UnitData | None:
        """The last poll for one thermostat, or None if it did not answer."""
        data = (self.data or {}).get(unit_id)
        return data if data is not None and data.ok else None

    def operation_mode(self, unit_id: int) -> int | None:
        """Register 33, needed by controls the stat only honours in some modes."""
        data = self.unit_data(unit_id)
        return None if data is None else data.get(REG_OPERATION_MODE)

    def allowed_operation_modes(self, unit_id: int) -> tuple[int, ...]:
        """Which register 33 values this thermostat will actually accept.

        Program mode 03, "None programmable", has no weekly program - so the
        modes that only exist in relation to one are refused, silently: hardware
        2026-08-13, an FC06 write of Hold came back echoing the *old* value.
        Offering them anyway is offering a control that does nothing.

        The thermostat's *current* mode is always included even when it is not
        otherwise on the list. A stat can be left in Schedule and then switched
        to non-programmable, and a `preset_mode` that is not among
        `preset_modes` is one Home Assistant will complain about - the reading
        must stay honest whatever the writing allows.
        """
        unit = self.unit_config(unit_id)
        if unit is None:
            return ()
        modes = tuple(OPERATION_MODES[unit.model])
        data = self.unit_data(unit_id)
        if data is None or data.get(REG_PROGRAM_MODE) != PROGRAM_MODE_NON_PROGRAMMABLE:
            return modes
        current = data.get(REG_OPERATION_MODE)
        allowed = [m for m in modes if m in NON_PROGRAMMABLE_MODES or m == current]
        return tuple(allowed)

    def hold_duration(self, unit_id: int) -> int | None:
        """Register 38, packed. Zero means Hold cannot be entered at all."""
        data = self.unit_data(unit_id)
        return None if data is None else data.get(REG_HOLD_DURATION)

    def program_type(self, unit_id: int) -> int | None:
        """Register 28: whether a Heat runs 4 periods a day or 6."""
        data = self.unit_data(unit_id)
        return None if data is None else data.get(REG_PROGRAM_TYPE)

    def program_mode(self, unit_id: int) -> int | None:
        """Register 29: 5/2, 7 day, 24 hour, or no program at all."""
        data = self.unit_data(unit_id)
        return None if data is None else data.get(REG_PROGRAM_MODE)

    # ------------------------------------------------------------------
    # The weekly program
    # ------------------------------------------------------------------

    async def async_read_schedule(
        self, unit_id: int, *, cached: bool = False
    ) -> dict[int, int] | None:
        """One thermostat's weekly program, or None if it did not answer.

        **Never part of the poll, and never in `data`.** The program is 168
        registers on a Heat and 112 on a Timer, so three and two FC03 packets
        against the interval poll's one - about 0.6 s of a 9600-baud bus per
        thermostat, or 19 s across a full 32-stat wire. Polling it would cost
        that every interval to watch values that only change when somebody
        changes them.

        Keeping it out of `data` matters just as much: `data` is what every
        entity reads and what `async_set_updated_data` republishes, so putting
        170 rarely-moving registers in there would wake every entity on the unit
        for nothing.

        `cached=True` serves the last read if there is one, which is what a UI
        wants when it is redrawing rather than refreshing.
        """
        unit = self.unit_config(unit_id)
        if unit is None:
            return None
        if cached and unit_id in self.schedules:
            return self.schedules[unit_id]

        layout = SCHEDULES[unit.model]
        words = await self.hub.async_read_span(
            unit_id, layout.base, layout.last_register - layout.base + 1
        )
        if words is None:
            # Not cached under a stale key: a thermostat that has gone quiet
            # must not serve a program someone might then edit and write back.
            self.schedules.pop(unit_id, None)
            self.schedule_read.pop(unit_id, None)
        else:
            self.schedules[unit_id] = words
            self.schedule_read[unit_id] = dt_util.utcnow()
        # Not `async_set_updated_data`: that republishes `data`, which the
        # program is deliberately not part of. This just tells the entities
        # carrying it to redraw.
        self.async_update_listeners()
        return words

    async def async_load_schedules(self) -> None:
        """Read every thermostat's program once, in the background.

        In the background because it is seconds of bus time on a wide bus - 32
        stats is around 19 s - and a config entry must not take that long to set
        up. The entity that carries the program simply has no value until this
        lands, which is honest: nothing has read it yet.

        Failures are ignored on purpose. A thermostat that is not answering is
        already unavailable, and the next `get_schedule` will say so properly.
        """
        for unit in self.units:
            try:
                await self.async_read_schedule(unit.unit_id)
            except EdgeConnectionError as err:
                _LOGGER.debug("Could not read %s's weekly program: %s", unit.name, err)
                return

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def bus_device_info(self) -> DeviceInfo:
        """The RS485 bus itself, parent to every thermostat on it.

        It gives the bus-wide facts (the port, the register base) a home, and
        makes the device tree read the way the wiring actually is.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.config_entry.entry_id}_bus")},
            name=f"EDGE bus ({self.hub.label})",
            manufacturer=MANUFACTURER,
            model="RS485 Modbus bus",
        )

    def device_info(self, unit: UnitConfig) -> DeviceInfo:
        entry_id = self.config_entry.entry_id
        data = self.unit_data(unit.unit_id)
        firmware = data.get(REG_FIRMWARE) if data else None
        return DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{unit.unit_id}")},
            name=unit.name,
            manufacturer=MANUFACTURER,
            model=MODEL_LABELS[unit.model],
            sw_version=str(firmware) if firmware else None,
            via_device=(DOMAIN, f"{entry_id}_bus"),
        )

    # ------------------------------------------------------------------
    # The poll
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[int, UnitData]:
        try:
            # A degraded bus must not overrun the interval: at 9600 baud a
            # timing-out unit costs a full timeout each, and 32 of them would
            # otherwise pile polls on top of each other.
            async with asyncio.timeout(
                max(self.update_interval.total_seconds() - 2, 15)
            ):
                raw = await self.hub.async_read_units([u.unit_id for u in self.units])
        except EdgeConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except TimeoutError as err:
            raise UpdateFailed("Poll overran its interval (degraded bus?)") from err

        data = {
            unit.unit_id: self._unit_data(unit, raw.get(unit.unit_id))
            for unit in self.units
        }

        # A dead thermostat is not a dead config entry. It goes unavailable on
        # its own and every other stat keeps updating. The entry only fails when
        # nothing at all answered - which on a shared RS485 wire means the
        # adapter, the wiring or the termination, not one thermostat.
        if data and not any(unit.ok for unit in data.values()):
            raise UpdateFailed("No thermostat on the bus answered")
        return data

    @callback
    def _schedule_settle(self, unit_id: int, step: int = 0) -> None:
        """Keep asking this thermostat until it has acted on the write.

        The immediate read-back lands ~80 ms after the write, which is soon
        enough for the register that was *written* - measured on hardware,
        register 34 already held the new value there - but far too soon for the
        ones that reflect the stat acting on it. Register 7, the live setpoint
        every UI surface actually reads, and the relay at register 2 both lag by
        appreciably longer. Without this they would hold their old values until
        the next scheduled poll, up to `MAX_SCAN_INTERVAL` away.

        Probes run on the widening `SETTLE_PROBES` schedule, publishing whatever
        each one finds. The schedule runs **to its end** rather than stopping at
        the first change, because the stat's registers do not all move together:
        measured on hardware, register 7 caught up 1.6 s after a write while the
        relay at register 2 had not moved yet. Stopping on the first difference
        published the new setpoint and then left the relay stale until the next
        scheduled poll - which is the original complaint, moved rather than
        fixed.

        Re-armed rather than stacked, so a burst of writes - dragging a setpoint
        - runs one schedule from the last of them, not one per write on a bus
        that cannot afford them.
        """
        if (cancel := self._settling.pop(unit_id, None)) is not None:
            cancel()
        if step >= len(SETTLE_PROBES):
            return
        gap = SETTLE_PROBES[step] - (SETTLE_PROBES[step - 1] if step else 0.0)

        async def _probe(_now) -> None:
            self._settling.pop(unit_id, None)
            await self._async_settle_probe(unit_id, SETTLE_PROBES[step])
            self._schedule_settle(unit_id, step + 1)

        self._settling[unit_id] = async_call_later(self.hass, gap, _probe)

    async def _async_settle_probe(self, unit_id: int, elapsed: float) -> None:
        """One settle read, published only if it says something new.

        Compared against what is currently published rather than a snapshot
        taken at write time, so there is no bookkeeping to get stale and the
        question is always "is there something new to show".
        """
        unit = self.unit_config(unit_id)
        if unit is None:
            return
        published = (self.data or {}).get(unit_id)
        try:
            words = await self.hub.async_read_block(unit_id, POLL_START, POLL_COUNT)
        except EdgeConnectionError as err:
            _LOGGER.debug("Settle read for unit %s failed: %s", unit_id, err)
            return
        if words is None:
            return
        if published is not None and published.ok:
            if words == published.words:
                return
            _LOGGER.debug(
                "Unit %s moved %.1f s after the write; registers %s changed",
                unit_id,
                elapsed,
                sorted(n for n, w in words.items() if published.words.get(n) != w),
            )
        data = dict(self.data or {})
        data[unit_id] = self._unit_data(unit, words)
        self.async_set_updated_data(data)

    async def async_shutdown(self) -> None:
        """Drop any pending settle read before the entry goes away."""
        while self._settling:
            self._settling.popitem()[1]()
        await super().async_shutdown()

    async def async_refresh_unit(self, unit_id: int, *, settle: bool = False) -> None:
        """Re-read one thermostat now, and publish it. The read-back after a write.

        `async_request_refresh()` cannot do this job, for two reasons that both
        show up as a control snapping back to its old value in the UI:

        - **It is debounced**, with a 10 s cooldown. The first call runs at once,
          but a second inside that window is deferred to the end of the timer -
          and dragging a setpoint is exactly a burst of writes. Every one after
          the first would show the previous reading for up to ten seconds.
        - **It re-polls the whole bus.** At 9600 baud with the manual's 50 ms
          gap, a wire of 32 thermostats is seconds of stale card for a change to
          one of them - longer still if any of them are silent and timing out.

        So this reads only the stat that was written, one FC03, and patches that
        one entry in `data`. It is still a read-back, not an assumption: what
        lands in the UI is what the thermostat kept.

        A failure here is deliberately quiet. The write itself already succeeded
        or raised; a bus that has since gone away is the next scheduled poll's
        news to break, not a write's.

        `settle` starts the probe schedule that waits for the thermostat to
        act on the write - see `_schedule_settle`. Every write sets it; the
        settle probes do not, or they would never stop.
        """
        unit = self.unit_config(unit_id)
        if unit is None:
            return
        if settle:
            self._schedule_settle(unit_id)
        try:
            words = await self.hub.async_read_block(unit_id, POLL_START, POLL_COUNT)
        except EdgeConnectionError as err:
            _LOGGER.debug("Read-back after writing to unit %s failed: %s", unit_id, err)
            return
        if words is None:
            # The stat took the write and then went quiet. Leaving the previous
            # data in place lets the next poll decide whether it is really gone.
            return
        # A copy, because `data` is what every entity is currently reading.
        data = dict(self.data or {})
        data[unit_id] = self._unit_data(unit, words)
        # Also pushes the next scheduled poll out by a full interval, which is
        # right: this unit is fresh, and the others were not the ones that moved.
        self.async_set_updated_data(data)

    @staticmethod
    def _unit_data(unit: UnitConfig, words: dict[int, int] | None) -> UnitData:
        if words is None:
            return UnitData(unit_id=unit.unit_id, ok=False)
        return UnitData(
            unit_id=unit.unit_id,
            ok=True,
            words=words,
            # Register 21 is the stat's display unit, and every temperature
            # register follows it. A Timer has no temperatures at all.
            fahrenheit=unit.model == MODEL_HEAT and words.get(REG_TEMP_FORMAT) == 1,
        )

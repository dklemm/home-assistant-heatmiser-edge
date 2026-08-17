"""The actions: the writes that have no entity to hang off.

Three of them, for three different reasons.

`set_time` is an *event* — the RTC reads back 0xFFFF once synced, so there is no
clock to show. `set_hold` is three registers in a fixed order that
`climate.set_preset_mode` has nowhere to put. `get_schedule` and `set_schedule`
are the weekly program, which is 126 values per Heat and far too expensive to
poll — see `schedule.py` for why it is neither entities nor part of the poll.

They share their targeting (`_async_targets`), their `CONF_CONTROLS` gate and
their collect-the-failures-and-raise-together rule, which is the poll's
per-unit-availability rule applied to a write: one thermostat off the wall must
not deny the others.

`heatmiser_edge.set_time` — put the right time on a thermostat's clock.

**Why an action and not an entity.** The RTC is write-only in practice: the
manual says registers 47-50 are "automatic assignment 0xffff after the success
of the RTC synchronization", and hardware agrees. So there is no clock to read
back and nothing for a datetime entity to show — setting it is an *event*, which
is exactly what an action is for. The stat's own clock drives the weekly
program, and nothing else in the map exposes it, so an install with no keypad
time set runs its schedule at the wrong hour with no symptom Home Assistant can
see.

**Why FC16.** The four registers are one timestamp. Written one at a time the
stat sees the year, then the month and day, then the hour and minute as three
separate partial timestamps and may sync on a torn one — a stat that lands on
1 January is worse than one that was never set. `EdgeHub.async_write_registers`
has existed since v1 for precisely this.

**Why the clock is device-targeted.** There is no RTC entity to aim at, so the
action takes devices: a thermostat, or the bus itself to mean every thermostat
on it. Both are already in the device registry, and both read naturally in an
automation.

**DST is register 30, a separate write.** The manual documents it as a flag on
the stat (00 = Disabled, 01 = Enabled) with register 12 reporting whether summer
time is currently in force, so the *thermostat* does the seasonal shift, not us.
That leaves one thing genuinely unsettled, recorded in CLAUDE.md: whether a stat
with DST enabled applies its own shift on top of a written timestamp. The action
therefore writes plain local wall-clock time and offers the flag, so anyone who
wants the question to go away can disable DST on the stat and re-sync from Home
Assistant, which knows the real local time anyway.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DATETIME,
    ATTR_DAYS,
    ATTR_DST,
    ATTR_DURATION,
    ATTR_PERIODS,
    ATTR_TEMPERATURE,
    DOMAIN,
    MODE_HOLD,
    PROGRAM_MODE_LABELS,
    PROGRAM_MODE_NON_PROGRAMMABLE,
    REG_DST_ENABLED,
    REG_HOLD_DURATION,
    REG_HOLD_SETPOINT,
    REG_OPERATION_MODE,
    REG_RTC,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_HOLD,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_TIME,
    SETPOINT_MAX_C,
    SETPOINT_MAX_F,
    SETPOINT_MIN_C,
    SETPOINT_MIN_F,
)
from .coordinator import EdgeCoordinator, UnitConfig
from .decode import encode_rtc, encode_temperature, minutes_to_hm
from .hub import EdgeConnectionError
from .schedule import (
    ScheduleError,
    format_week,
    parse_periods,
    plan_day,
    resolve_days,
    usable_periods,
)

_LOGGER = logging.getLogger(__name__)

SET_TIME_SCHEMA = vol.Schema(
    {
        # The whole target vocabulary, not just device_id: Home Assistant's
        # target picker will happily hand back an area, a floor or a label, and
        # a schema that only knew about devices would reject those with a
        # validation error naming a field the user never saw.
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_DATETIME): cv.datetime,
        vol.Optional(ATTR_DST): cv.boolean,
    }
)


# Hold is three registers and an order, not one value - see `_async_write_hold`.
SET_HOLD_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_DURATION): cv.positive_time_period,
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
    }
)


# The weekly program. `days` and `periods` are validated in `schedule.py` rather
# than here: the rules are per-model (a Heat period is a time and a temperature,
# a Timer period is an on and an off) and the errors need to name the day and
# the period, which a voluptuous failure cannot do readably.
GET_SCHEDULE_SCHEMA = vol.Schema({**cv.TARGET_SERVICE_FIELDS})

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_DAYS): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_PERIODS): vol.All(cv.ensure_list, [dict]),
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain's actions. Called once, from `async_setup`."""

    async def async_set_time(call: ServiceCall) -> None:
        await _async_set_time(hass, call)

    async def async_set_hold(call: ServiceCall) -> None:
        await _async_set_hold(hass, call)

    async def async_get_schedule(call: ServiceCall) -> ServiceResponse:
        return await _async_get_schedule(hass, call)

    async def async_set_schedule(call: ServiceCall) -> None:
        await _async_set_schedule(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIME, async_set_time, schema=SET_TIME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_HOLD, async_set_hold, schema=SET_HOLD_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        async_get_schedule,
        schema=GET_SCHEDULE_SCHEMA,
        # ONLY, not OPTIONAL: reading the program costs three FC03 packets, so
        # a call that discards the answer would be spending bus time for
        # nothing. There is no state to change either - it is purely a read.
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, async_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )


async def _async_set_time(hass: HomeAssistant, call: ServiceCall) -> None:
    when = call.data.get(ATTR_DATETIME) or dt_util.now()
    # A datetime picked in the UI arrives naive and already local; one built by a
    # template may carry a timezone. The thermostat has no timezone at all, so
    # everything becomes local wall-clock time before it goes on the wire.
    if when.tzinfo is not None:
        when = dt_util.as_local(when)
    dst: bool | None = call.data.get(ATTR_DST)
    try:
        # Encoded once, and before anything reaches the wire: a timestamp the
        # registers cannot hold should stop the call, not half of a bus.
        words = encode_rtc(when)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="bad_datetime",
            translation_placeholders={"datetime": when.isoformat(sep=" ")},
        ) from err

    targets = _async_targets(hass, call)
    if not targets:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_thermostats_targeted"
        )
    failed: list[str] = []
    touched: list[EdgeCoordinator] = []
    for coordinator, unit in targets:
        try:
            await _async_write_unit(coordinator, unit, words, dst)
        except EdgeConnectionError as err:
            # One thermostat off the wall must not deny the rest their clock —
            # the same rule the poll follows. Every failure is collected and
            # reported together, once every reachable stat has been set.
            _LOGGER.warning("Could not set the time on %s: %s", unit.name, err)
            failed.append(unit.name)
        else:
            if coordinator not in touched:
                touched.append(coordinator)

    for coordinator in touched:
        # Only register 30 has anything to re-read: the RTC words are 0xFFFF by
        # the time we could ask. The refresh is so the daylight-saving switch
        # shows what the stat kept rather than what we asked for.
        await coordinator.async_request_refresh()

    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="set_time_failed",
            translation_placeholders={"names": ", ".join(failed)},
        )


async def _async_write_unit(
    coordinator: EdgeCoordinator,
    unit: UnitConfig,
    words: list[int],
    dst: bool | None,
) -> None:
    """Daylight saving first, then the clock, so the time lands under the flag.

    If the stat does apply its own summer-time shift, doing it the other way
    round would sync the clock under the old setting and only then change how it
    is interpreted.
    """
    if dst is not None:
        await coordinator.hub.async_write_register(
            unit.unit_id, REG_DST_ENABLED, 1 if dst else 0
        )
    await coordinator.hub.async_write_registers(unit.unit_id, REG_RTC, words)


async def _async_set_hold(hass: HomeAssistant, call: ServiceCall) -> None:
    """Put a thermostat into Hold — duration, temperature and mode together.

    **Why an action.** Hold is not a mode you can simply select. Hardware
    2026-08-13: with register 38 (hold duration) at 0, an FC06 write of 2 to
    register 33 comes back echoing the *old* mode — the stat refuses it and says
    nothing. Write a non-zero duration first and the identical write is
    accepted. The keypad tells the same story: its Hold flow asks for hours,
    then minutes, then a temperature, then confirms.

    So Hold is three registers in a fixed order, and `climate.set_preset_mode`
    has nowhere to put a duration. That is the same argument that made the RTC
    an action rather than four entities.
    """
    minutes = int(call.data[ATTR_DURATION].total_seconds() // 60)
    if not 0 < minutes <= 99 * 60 + 59:
        # Zero is the interesting one: it is not merely out of range, it is the
        # exact value the thermostat treats as "no hold", so it would be
        # accepted into register 38 and then silently refuse the mode.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="bad_hold_duration",
            translation_placeholders={"duration": str(call.data[ATTR_DURATION])},
        )
    temperature: float | None = call.data.get(ATTR_TEMPERATURE)

    targets = _async_targets(hass, call)
    if not targets:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_thermostats_targeted"
        )

    failed: list[str] = []
    for coordinator, unit in targets:
        if temperature is not None:
            # The limits follow the stat's own display unit, register 21, the
            # same as every temperature entity on it. Checked before anything is
            # written for this unit, so a bad value cannot leave a half-set hold.
            data = coordinator.unit_data(unit.unit_id)
            fahrenheit = bool(data and data.fahrenheit)
            low = SETPOINT_MIN_F if fahrenheit else SETPOINT_MIN_C
            high = SETPOINT_MAX_F if fahrenheit else SETPOINT_MAX_C
            if not low <= temperature <= high:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="bad_hold_temperature",
                    translation_placeholders={
                        "temperature": str(temperature),
                        "name": unit.name,
                        "low": str(low),
                        "high": str(high),
                    },
                )
        try:
            await _async_write_hold(coordinator, unit, minutes, temperature)
        except EdgeConnectionError as err:
            # One stat off the wall must not deny the rest their hold - the
            # poll's rule, applied to a write, as `set_time` does.
            _LOGGER.warning("Could not set hold on %s: %s", unit.name, err)
            failed.append(unit.name)
        else:
            await coordinator.async_refresh_unit(unit.unit_id, settle=True)

    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="set_hold_failed",
            translation_placeholders={"names": ", ".join(failed)},
        )


async def _async_write_hold(
    coordinator: EdgeCoordinator,
    unit: UnitConfig,
    minutes: int,
    temperature: float | None,
) -> None:
    """Duration, then temperature, then the mode. The order is the whole point.

    Register 33 is written last because it is the one the stat validates: with
    no duration stored it refuses the mode outright. Three FC06s rather than one
    FC16 because they are not contiguous - 38, 34 and 33 - and each is a
    complete, meaningful value on its own, so a failure part way leaves settings
    changed but the stat still in the mode it was in.
    """
    await coordinator.hub.async_write_register(
        unit.unit_id, REG_HOLD_DURATION, minutes_to_hm(minutes)
    )
    if temperature is not None:
        await coordinator.hub.async_write_register(
            unit.unit_id, REG_HOLD_SETPOINT, encode_temperature(temperature)
        )
    await coordinator.hub.async_write_register(
        unit.unit_id, REG_OPERATION_MODE, MODE_HOLD
    )


async def _async_get_schedule(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Read the weekly program off the targeted thermostats.

    Read-only, so it is not gated on `CONF_CONTROLS`: a bus set up read-only
    still has a program worth looking at.

    A thermostat that does not answer is reported in `failed` rather than
    raising, because a response-only action that raises returns nothing at all -
    and one stat off the wall should not cost you the other two's programs. The
    poll's rule again: it fails outright only when *nothing* answered.
    """
    targets = _async_targets(hass, call, writable=False)
    if not targets:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_thermostats_targeted"
        )

    thermostats: list[dict] = []
    failed: list[str] = []
    for coordinator, unit in targets:
        try:
            words = await coordinator.async_read_schedule(unit.unit_id)
        except EdgeConnectionError as err:
            _LOGGER.warning("Could not read the program from %s: %s", unit.name, err)
            words = None
        if words is None:
            failed.append(unit.name)
            continue
        thermostats.append(_schedule_response(coordinator, unit, words))

    if not thermostats:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="get_schedule_failed",
            translation_placeholders={"names": ", ".join(failed)},
        )
    return {"thermostats": thermostats, "failed": failed}


@callback
def _schedule_response(
    coordinator: EdgeCoordinator, unit: UnitConfig, words: dict[int, int]
) -> dict:
    """One thermostat's program, plus the two registers that shape it.

    `periods` is register 28 and says how many of the rows the thermostat
    actually runs; `program_mode` is register 29 and says how many of the seven
    days are independent. Every row is still reported - the registers exist and
    hold values whatever the mode - so a caller can show the extra ones greyed
    rather than lose what is stored in them.
    """
    data = coordinator.unit_data(unit.unit_id)
    fahrenheit = bool(data and data.fahrenheit)
    mode = coordinator.program_mode(unit.unit_id)
    return {
        "unit_id": unit.unit_id,
        "name": unit.name,
        "model": unit.model,
        "program_mode": PROGRAM_MODE_LABELS.get(mode) if mode is not None else None,
        "periods": usable_periods(unit.model, coordinator.program_type(unit.unit_id)),
        "temperature_unit": "°F" if fahrenheit else "°C",
        "schedule": format_week(unit.model, words, fahrenheit),
    }


async def _async_set_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write periods into the weekly program, in bulk.

    **Two passes, and the split is the point.** The first reads each
    thermostat's program and works out the writes it would need, raising on
    anything invalid; only then does the second put registers on the wire. A
    schedule the stat should not be asked to store has to stop the call, not
    half of a bus - and with several thermostats targeted, validating as you go
    would leave the first one edited and the second refused.

    Working out the writes needs the current program anyway, to merge a partial
    edit and to check the day still reads forwards. Having it also means only
    the periods that would actually change are written: editing one period costs
    one FC16 rather than six.
    """
    days: list[str] = call.data[ATTR_DAYS]
    raw_periods: list[dict] = call.data[ATTR_PERIODS]

    targets = _async_targets(hass, call)
    if not targets:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_thermostats_targeted"
        )

    planned: list[tuple[EdgeCoordinator, UnitConfig, list[tuple[int, list[int]]]]] = []
    failed: list[str] = []
    for coordinator, unit in targets:
        try:
            words = await coordinator.async_read_schedule(unit.unit_id)
        except EdgeConnectionError as err:
            _LOGGER.warning("Could not read the program from %s: %s", unit.name, err)
            words = None
        if words is None:
            # Nothing is written to a stat whose current program we could not
            # read: a partial edit merged over a blank is not an edit.
            failed.append(unit.name)
            continue
        try:
            planned.append(
                (coordinator, unit, _plan_unit(coordinator, unit, words, days, raw_periods))
            )
        except ScheduleError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=err.key,
                translation_placeholders={**err.placeholders, "name": unit.name},
            ) from err

    rejected: list[str] = []
    for coordinator, unit, writes in planned:
        try:
            for register, values in writes:
                await coordinator.hub.async_write_registers(
                    unit.unit_id, register, values
                )
        except EdgeConnectionError as err:
            _LOGGER.warning("Could not set the program on %s: %s", unit.name, err)
            failed.append(unit.name)
            continue
        if writes and not await _async_verify(coordinator, unit, writes):
            rejected.append(unit.name)

    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="set_schedule_failed",
            translation_placeholders={"names": ", ".join(failed)},
        )
    if rejected:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="schedule_not_stored",
            translation_placeholders={"names": ", ".join(rejected)},
        )


def _plan_unit(
    coordinator: EdgeCoordinator,
    unit: UnitConfig,
    words: dict[int, int],
    days: list[str],
    raw_periods: list[dict],
) -> list[tuple[int, list[int]]]:
    """Every FC16 one thermostat needs, or a `ScheduleError` and no writes."""
    mode = coordinator.program_mode(unit.unit_id)
    if mode == PROGRAM_MODE_NON_PROGRAMMABLE:
        # There is no weekly program in this mode, so the grid is not what the
        # stat runs against. Storing a schedule it will not use is exactly the
        # control-that-does-nothing this integration refuses to ship.
        raise ScheduleError("schedule_not_programmable")

    data = coordinator.unit_data(unit.unit_id)
    fahrenheit = bool(data and data.fahrenheit)
    available = usable_periods(unit.model, coordinator.program_type(unit.unit_id))
    updates = parse_periods(
        unit.model, raw_periods, periods_available=available, fahrenheit=fahrenheit
    )

    writes: list[tuple[int, list[int]]] = []
    for day in resolve_days(days, mode):
        writes.extend(
            plan_day(
                unit.model,
                words,
                day,
                updates,
                periods_available=available,
                fahrenheit=fahrenheit,
            )
        )
    return writes


async def _async_verify(
    coordinator: EdgeCoordinator, unit: UnitConfig, writes: list[tuple[int, list[int]]]
) -> bool:
    """Re-read the program and check the thermostat kept what it was sent.

    FC16 echoes only the address and the quantity, never the values - so unlike
    the FC06 every entity uses, it cannot reveal a stat that silently clamped
    what it was given. Reading back is the only verification there is, and it
    refreshes the cached program at the same time.

    A stat that goes quiet at this point is not called a failure: the writes
    themselves succeeded, and a bus that has since gone away is the next call's
    news to break.
    """
    try:
        words = await coordinator.async_read_schedule(unit.unit_id)
    except EdgeConnectionError as err:
        _LOGGER.debug("Could not read %s's program back: %s", unit.name, err)
        return True
    if words is None:
        return True
    for register, values in writes:
        stored = [words.get(register + index) for index in range(len(values))]
        if stored != values:
            _LOGGER.warning(
                "%s did not keep the program written to registers %s-%s: "
                "sent %s, stored %s",
                unit.name,
                register,
                register + len(values) - 1,
                values,
                stored,
            )
            return False
    return True


@callback
def _async_targets(
    hass: HomeAssistant, call: ServiceCall, writable: bool = True
) -> list[tuple[EdgeCoordinator, UnitConfig]]:
    """The thermostats behind whatever the call targeted, each one once.

    A device named outright must be one of ours and must be writable, because
    the user meant that device and a silent no-op would be a lie. A device
    reached *through* an area, a floor, a label or an entity is filtered
    instead: "set the time upstairs" should skip the lamps, not fail on them.

    `writable=False` drops the `CONF_CONTROLS` gate, for the one action that
    only reads. A bus set up read-only still has a weekly program worth looking
    at, and refusing to show it would be read-only meaning less than read.

    A bus device stands for every thermostat on it, which is how anyone would
    read "set the time on the EDGE bus". Targeting a bus *and* one of its stats
    is not an error and does not write twice: the key is (entry, unit id).
    """
    selection = TargetSelection(call.data)
    selected = async_extract_referenced_entity_ids(hass, selection, expand_group=False)
    registry = dr.async_get(hass)

    # A named entity resolves to its device, which the helper does not do for us:
    # it answers in entities, and the RTC has none. Any entity of a thermostat
    # will do, since they all sit on the one device.
    device_ids = set(selected.referenced_devices)
    entities = er.async_get(hass)
    for entity_id in selected.referenced:
        entity = entities.async_get(entity_id)
        if entity is not None and entity.device_id:
            device_ids.add(entity.device_id)

    targets: dict[tuple[str, int], tuple[EdgeCoordinator, UnitConfig]] = {}
    for device_id in device_ids:
        named = device_id in selection.device_ids
        device = registry.async_get(device_id)
        if device is None:
            if named:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_device",
                    translation_placeholders={"device_id": device_id},
                )
            continue
        found = False
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            # Identifiers are "{entry_id}_{unit_id}" for a thermostat and
            # "{entry_id}_bus" for the bus, and an entry id never contains "_".
            entry_id, _, suffix = identifier.rpartition("_")
            coordinator = _async_coordinator(hass, entry_id)
            if coordinator is None:
                continue
            if writable and not coordinator.controls_enabled:
                # "Allow changing settings" off means read-only, and an action
                # is no more exempt from that than an entity is. Said out loud
                # when the device was named, so it is not a silent no-op.
                if named:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="read_only",
                        translation_placeholders={
                            "name": device.name_by_user or device.name or device_id
                        },
                    )
                continue
            for unit in coordinator.units:
                if suffix in ("bus", str(unit.unit_id)):
                    targets[(entry_id, unit.unit_id)] = (coordinator, unit)
                    found = True
        if named and not found:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_thermostat",
                translation_placeholders={
                    "name": device.name_by_user or device.name or device_id
                },
            )
    return list(targets.values())


@callback
def _async_coordinator(hass: HomeAssistant, entry_id: str) -> EdgeCoordinator | None:
    """The live coordinator for a config entry, or None if it is not loaded.

    A device belonging to a disabled or failed entry has no hub to write
    through, and `runtime_data` only exists while the entry is set up.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data

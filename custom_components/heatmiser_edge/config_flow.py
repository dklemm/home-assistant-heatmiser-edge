"""Setting up one RS485 bus, and the thermostats on it.

    user -> serial | tcp -> scan (with progress) -> confirm -> entry

The scan is the interesting step. Sweeping unit ids at 9600 baud costs a full
timeout for every id that isn't there, so two things keep it bearable: the probe
reads five registers rather than fifty, and it runs behind a real progress
dialog instead of a frozen form. That is what lets the default be the whole
1-32 range - one field, asked once, and nobody has to know their thermostats'
ids to get started. A user who does know can narrow it and save the wait.

The confirm step exists because the manual gives no model or product-id
register. `detect.guess_model` scores a Heat against a Timer and is right by a
wide margin in practice, but it is still shown as a *default the user confirms*,
with low-confidence guesses flagged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONTROLS,
    CONF_FRAMER,
    CONF_MODEL,
    CONF_PARITY,
    CONF_REGISTER_OFFSET,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONF_TIMEOUT,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    CONF_UNIT_IDS,
    CONF_UNITS,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    FRAMER_RTU,
    FRAMER_SOCKET,
    MAX_SCAN_INTERVAL,
    MAX_TIMEOUT,
    MAX_UNIT_ID,
    MIN_SCAN_INTERVAL,
    MIN_TIMEOUT,
    MIN_UNIT_ID,
    MODEL_LABELS,
    MODELS,
    POLL_COUNT,
    POLL_START,
    SCAN_TIMEOUT,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
)
from .detect import ModelGuess, guess_model, parse_id_list
from .hub import EdgeConnectionError, EdgeHub

_LOGGER = logging.getLogger(__name__)

# The whole valid range: a first-time user need not know their ids, and the
# scan runs behind a progress dialog. Anyone who does know can narrow it.
DEFAULT_UNIT_IDS = f"{MIN_UNIT_ID}-{MAX_UNIT_ID}"

# Dropped in favour of typing 1-32 into the id list, which always meant the
# same thing. Entries written before that still carry the flag, and
# `_reconfigure_schema` translates it so a re-scan does not silently narrow.
_LEGACY_SCAN_ALL = "scan_all"

_MODEL_OPTIONS = [
    SelectOptionDict(value=model, label=MODEL_LABELS[model]) for model in MODELS
]
_OFFSET_OPTIONS = [
    SelectOptionDict(value="auto", label="Detect automatically"),
    SelectOptionDict(value="-1", label="0-based (register N at address N-1)"),
    SelectOptionDict(value="0", label="1-based (register N at address N)"),
]
_FRAMER_OPTIONS = [
    SelectOptionDict(value=FRAMER_RTU, label="RTU over TCP (transparent gateway)"),
    SelectOptionDict(value=FRAMER_SOCKET, label="Modbus TCP"),
]


def _common_schema(defaults: dict[str, Any]) -> dict:
    """The fields both transports share: what to scan, and how to address it."""
    return {
        vol.Required(
            CONF_UNIT_IDS, default=defaults.get(CONF_UNIT_IDS, DEFAULT_UNIT_IDS)
        ): TextSelector(),
        vol.Required(
            CONF_REGISTER_OFFSET, default=defaults.get(CONF_REGISTER_OFFSET, "auto")
        ): SelectSelector(
            SelectSelectorConfig(
                options=_OFFSET_OPTIONS, mode=SelectSelectorMode.DROPDOWN
            )
        ),
    }


SERIAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERIAL_PORT): TextSelector(),
        vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.Coerce(int),
        vol.Required(CONF_BYTESIZE, default=DEFAULT_BYTESIZE): vol.In([5, 6, 7, 8]),
        vol.Required(CONF_PARITY, default=DEFAULT_PARITY): vol.In(["N", "E", "O"]),
        vol.Required(CONF_STOPBITS, default=DEFAULT_STOPBITS): vol.In([1, 2]),
        **_common_schema({}),
    }
)

TCP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): vol.Coerce(int),
        vol.Required(CONF_FRAMER, default=FRAMER_RTU): SelectSelector(
            SelectSelectorConfig(
                options=_FRAMER_OPTIONS, mode=SelectSelectorMode.DROPDOWN
            )
        ),
        **_common_schema({}),
    }
)


class DiscoveredUnit:
    """A thermostat that answered, and what we think it is."""

    def __init__(self, unit_id: int, guess: ModelGuess, words: dict[int, int]) -> None:
        self.unit_id = unit_id
        self.guess = guess
        self.words = words

    @property
    def default_name(self) -> str:
        return f"{MODEL_LABELS[self.guess.model]} {self.unit_id}"


def bus_unique_id(data: dict[str, Any]) -> str:
    """One config entry per bus - a serial port, or a gateway address."""
    if data[CONF_TRANSPORT] == TRANSPORT_TCP:
        return f"tcp:{data[CONF_HOST]}:{data.get(CONF_PORT, DEFAULT_TCP_PORT)}"
    return f"serial:{data[CONF_SERIAL_PORT]}"


def build_scan_hub(data: dict[str, Any]) -> EdgeHub:
    """A hub for discovery: short timeout, so silent ids are cheap."""
    offset = data.get(CONF_REGISTER_OFFSET, "auto")
    return EdgeHub(
        transport=data[CONF_TRANSPORT],
        serial_port=data.get(CONF_SERIAL_PORT),
        baudrate=data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        bytesize=data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
        parity=data.get(CONF_PARITY, DEFAULT_PARITY),
        stopbits=data.get(CONF_STOPBITS, DEFAULT_STOPBITS),
        host=data.get(CONF_HOST),
        port=data.get(CONF_PORT, DEFAULT_TCP_PORT),
        framer=data.get(CONF_FRAMER, FRAMER_RTU),
        timeout=SCAN_TIMEOUT,
        register_offset=None if offset == "auto" else int(offset),
    )


async def async_scan_bus(
    data: dict[str, Any], unit_ids: list[int]
) -> tuple[int, list[DiscoveredUnit]]:
    """Settle the register base, then find out who is on the wire."""
    hub = build_scan_hub(data)
    try:
        await hub.async_connect(unit_ids)
        found: list[DiscoveredUnit] = []
        for unit_id in unit_ids:
            if await hub.async_probe_unit(unit_id) is None:
                continue
            words = await hub.async_read_block(unit_id, POLL_START, POLL_COUNT)
            if words is None:
                continue
            found.append(DiscoveredUnit(unit_id, guess_model(words), words))
        return hub.register_offset, found
    finally:
        await hub.async_close()


class EdgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one RS485 bus."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._unit_ids: list[int] = []
        self._found: list[DiscoveredUnit] = []
        self._scan_task: asyncio.Task | None = None
        self._scan_error: str | None = None

    @property
    def _reconfiguring(self) -> bool:
        """Whether this flow is re-scanning an existing bus.

        The flow's own source carries this; there is no separate flag to keep in
        step with it.
        """
        return self.source == SOURCE_RECONFIGURE

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=[TRANSPORT_SERIAL, TRANSPORT_TCP]
        )

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_transport_step(
            TRANSPORT_SERIAL, SERIAL_SCHEMA, user_input
        )

    async def async_step_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_transport_step(TRANSPORT_TCP, TCP_SCHEMA, user_input)

    async def _async_transport_step(
        self, transport: str, schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_TRANSPORT: transport, **user_input}
            try:
                self._unit_ids = parse_id_list(data[CONF_UNIT_IDS])
            except ValueError:
                errors[CONF_UNIT_IDS] = "invalid_unit_ids"
            else:
                await self.async_set_unique_id(bus_unique_id(data))
                self._abort_if_unique_id_configured()
                self._data = data
                return await self.async_step_scan()
        return self.async_show_form(
            step_id=transport, data_schema=schema, errors=errors
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sweep the bus behind a progress dialog.

        A full 1-32 sweep is around 18 seconds even when nothing answers, which
        is far too long to leave a form looking frozen.
        """
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                self._async_run_scan(), eager_start=False
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scanning",
                progress_task=self._scan_task,
            )
        self._scan_task = None
        if self._scan_error:
            return self.async_show_progress_done(next_step_id="failed")
        return self.async_show_progress_done(next_step_id="confirm")

    async def _async_run_scan(self) -> None:
        try:
            offset, self._found = await async_scan_bus(self._data, self._unit_ids)
        except EdgeConnectionError as err:
            _LOGGER.debug("Scan failed: %s", err)
            self._scan_error = "cannot_connect"
            return
        self._data[CONF_REGISTER_OFFSET] = offset
        self._scan_error = None if self._found else "no_thermostats_found"

    async def async_step_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason=self._scan_error or "cannot_connect")

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name each thermostat and confirm what kind it is."""
        if user_input is not None:
            units = [
                {
                    CONF_UNIT_ID: found.unit_id,
                    CONF_MODEL: user_input[f"model_{found.unit_id}"],
                    "name": user_input[f"name_{found.unit_id}"],
                }
                for found in self._found
            ]
            data = {**self._data, CONF_UNITS: units}
            if self._reconfiguring:
                # `data`, not `data_updates`: a merge cannot remove a key, and
                # `self._data` already started from the entry's own data.
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data=data
                )
            return self.async_create_entry(
                title=f"Heatmiser EDGE ({self._bus_label()})", data=data
            )

        # On a re-scan, a thermostat that is already configured keeps the name
        # and model the user gave it; only new ones fall back to the guess.
        known = {
            unit[CONF_UNIT_ID]: unit
            for unit in (
                self._get_reconfigure_entry().data.get(CONF_UNITS, [])
                if self._reconfiguring
                else []
            )
        }
        fields: dict[Any, Any] = {}
        for found in self._found:
            existing = known.get(found.unit_id, {})
            fields[
                vol.Required(
                    f"name_{found.unit_id}",
                    default=existing.get("name") or found.default_name,
                )
            ] = TextSelector()
            fields[
                vol.Required(
                    f"model_{found.unit_id}",
                    default=existing.get(CONF_MODEL) or found.guess.model,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=_MODEL_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                )
            )
        unsure = [f.unit_id for f in self._found if not f.guess.confident]
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "count": str(len(self._found)),
                "offset": _offset_label(self._data.get(CONF_REGISTER_OFFSET)),
                "unsure": ", ".join(str(u) for u in unsure) or "none",
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-scan an existing bus, to add or remove thermostats.

        This is not an option, because it changes the device set: a stat that
        has appeared needs discovering and one that has gone needs its entities
        removed, and neither can be done from a form.
        """
        entry = self._get_reconfigure_entry()
        transport = entry.data[CONF_TRANSPORT]
        if user_input is not None:
            self._data = {**entry.data, **user_input, CONF_TRANSPORT: transport}
            # The form has just answered the same question the flag used to;
            # leaving it behind would only invite something to read it again.
            self._data.pop(_LEGACY_SCAN_ALL, None)
            try:
                self._unit_ids = parse_id_list(self._data[CONF_UNIT_IDS])
            except ValueError:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=self._reconfigure_schema(entry),
                    errors={CONF_UNIT_IDS: "invalid_unit_ids"},
                )
            return await self.async_step_scan()
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._reconfigure_schema(entry)
        )

    @staticmethod
    def _reconfigure_schema(entry: ConfigEntry) -> vol.Schema:
        """Only the discovery fields; the connection itself is unchanged."""
        stored = entry.data.get(CONF_REGISTER_OFFSET)
        # An entry set up with the old "scan every ID" flag stored an id list
        # that was never used. Showing that list would silently narrow the
        # re-scan, so the flag is spelled out as the range it always meant.
        unit_ids = (
            DEFAULT_UNIT_IDS
            if entry.data.get(_LEGACY_SCAN_ALL)
            else entry.data.get(CONF_UNIT_IDS, DEFAULT_UNIT_IDS)
        )
        return vol.Schema(
            _common_schema(
                {
                    CONF_UNIT_IDS: unit_ids,
                    CONF_REGISTER_OFFSET: (
                        "auto" if stored is None else str(stored)
                    ),
                }
            )
        )

    def _bus_label(self) -> str:
        if self._data[CONF_TRANSPORT] == TRANSPORT_TCP:
            return f"{self._data[CONF_HOST]}:{self._data.get(CONF_PORT, DEFAULT_TCP_PORT)}"
        return str(self._data[CONF_SERIAL_PORT])

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EdgeOptionsFlow:
        return EdgeOptionsFlow()


def _offset_label(offset: Any) -> str:
    if offset == 0:
        return "1-based (register N at address N)"
    if offset == -1:
        return "0-based (register N at address N-1)"
    return "not determined"


class EdgeOptionsFlow(OptionsFlow):
    """Polling, controls, the register base, and each thermostat's name.

    Adding or removing thermostats is deliberately not here: that changes the
    device set and wants a fresh scan, so it belongs to reconfiguration.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry

        def current(key: str, default: Any) -> Any:
            if key in entry.options:
                return entry.options[key]
            return entry.data.get(key, default)

        if user_input is not None:
            units = [
                {
                    CONF_UNIT_ID: unit[CONF_UNIT_ID],
                    CONF_MODEL: user_input[f"model_{unit[CONF_UNIT_ID]}"],
                    "name": user_input[f"name_{unit[CONF_UNIT_ID]}"],
                }
                for unit in current(CONF_UNITS, [])
            ]
            offset = user_input[CONF_REGISTER_OFFSET]
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_TIMEOUT: float(user_input[CONF_TIMEOUT]),
                    CONF_CONTROLS: user_input[CONF_CONTROLS],
                    CONF_REGISTER_OFFSET: None if offset == "auto" else int(offset),
                    CONF_UNITS: units,
                }
            )

        stored_offset = current(CONF_REGISTER_OFFSET, None)
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_TIMEOUT, default=current(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_TIMEOUT,
                    max=MAX_TIMEOUT,
                    step=0.1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_CONTROLS, default=current(CONF_CONTROLS, True)
            ): BooleanSelector(),
            vol.Required(
                CONF_REGISTER_OFFSET,
                default="auto" if stored_offset is None else str(stored_offset),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=_OFFSET_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                )
            ),
        }
        for unit in current(CONF_UNITS, []):
            unit_id = unit[CONF_UNIT_ID]
            fields[
                vol.Required(f"name_{unit_id}", default=unit.get("name", ""))
            ] = TextSelector()
            fields[
                vol.Required(f"model_{unit_id}", default=unit[CONF_MODEL])
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=_MODEL_OPTIONS, mode=SelectSelectorMode.DROPDOWN
                )
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))

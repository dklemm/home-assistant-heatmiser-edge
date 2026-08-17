"""Everything needed to diagnose a bug report without access to the bus.

The raw register snapshot is the valuable part: nearly every question about this
integration ("why is this reading wrong?", "why is this thermostat detected as a
Timer?") is answerable from the words themselves, and both heuristics in
`detect.py` can be re-run against them offline.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL_PORT
from .coordinator import EdgeConfigEntry

# The port or address is the one identifying detail in the entry, and it is not
# needed to understand anything here.
REDACTED = {CONF_HOST, CONF_SERIAL_PORT}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EdgeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    hub = coordinator.hub
    return {
        "entry_data": {k: v for k, v in entry.data.items() if k not in REDACTED},
        "options": dict(entry.options),
        "bus": {
            "transport": hub.transport,
            "framer": hub.framer,
            "timeout": hub.timeout,
            "register_offset": hub.register_offset,
            "consecutive_failures": dict(sorted(hub.unit_failures.items())),
        },
        "last_update_success": coordinator.last_update_success,
        "units": [_unit_diagnostics(coordinator, unit) for unit in coordinator.units],
    }


def _unit_diagnostics(coordinator, unit) -> dict[str, Any]:
    data = (coordinator.data or {}).get(unit.unit_id)
    return {
        "unit_id": unit.unit_id,
        "model": unit.model,
        "answering": bool(data and data.ok),
        "fahrenheit": bool(data and data.fahrenheit),
        "registers": {
            str(number): word for number, word in sorted((data.words if data else {}).items())
        },
    }

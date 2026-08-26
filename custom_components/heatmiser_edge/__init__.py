"""The Heatmiser EDGE integration: one config entry per RS485 bus."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_FRAMER,
    CONF_PARITY,
    CONF_REGISTER_OFFSET,
    DEFAULT_REGISTER_OFFSET,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONF_TIMEOUT,
    CONF_TRANSPORT,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    FRAMER_RTU,
    MODEL_LABELS,
)
from .coordinator import EdgeConfigEntry, EdgeCoordinator, option
from .hub import EdgeConnectionError, EdgeHub
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

# Everything is configured through the config flow; the domain has no YAML of
# its own. Saying so explicitly is what lets `async_setup` exist purely to
# register the domain's actions.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The schedule card, served from `www/` at `/heatmiser_edge/<file>`.
CARD_FILENAME = "heatmiser-edge-schedule-card.js"
_CARD_REGISTERED = f"{DOMAIN}_card"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def build_hub(entry: ConfigEntry) -> EdgeHub:
    """The hub described by an entry's stored connection settings."""
    return EdgeHub(
        transport=entry.data[CONF_TRANSPORT],
        serial_port=entry.data.get(CONF_SERIAL_PORT),
        baudrate=entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        bytesize=entry.data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
        parity=entry.data.get(CONF_PARITY, DEFAULT_PARITY),
        stopbits=entry.data.get(CONF_STOPBITS, DEFAULT_STOPBITS),
        host=entry.data.get(CONF_HOST),
        port=entry.data.get(CONF_PORT, DEFAULT_TCP_PORT),
        framer=entry.data.get(CONF_FRAMER, FRAMER_RTU),
        timeout=option(entry, CONF_TIMEOUT, DEFAULT_TIMEOUT),
        # None means "probe for it". The config flow normally stores an answer,
        # so a restart does not re-probe the bus.
        register_offset=option(entry, CONF_REGISTER_OFFSET, DEFAULT_REGISTER_OFFSET),
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain's actions and its card, once, however many buses.

    The actions target devices rather than entities, so they belong to the
    domain and not to a config entry - and registering them here means an
    automation calling one validates even while a bus is reloading.
    """
    async_register_services(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the schedule card and tell the frontend to load it.

    Shipped inside the integration rather than as a separate HACS frontend
    repository, because the card and the `set_schedule` action are one feature:
    versioning them apart would let a card call an action that is not there.

    The URL carries the integration's version so a browser picks up a new card
    on upgrade instead of serving yesterday's from cache. `add_extra_js_url`
    loads it for every frontend session, which is why it is one small file with
    no dependencies.

    **`after_dependencies`, not `dependencies`.** A Home Assistant with no
    frontend - headless, API-only, or a test harness with no compiled frontend
    installed - must still get the thermostats. Everything the card does is
    reachable from the actions and the entity's attributes anyway, so the card
    is the part that goes missing, never the integration.
    """
    if hass.data.get(_CARD_REGISTERED):
        return
    if "frontend" not in hass.config.components or hass.http is None:
        _LOGGER.debug("No frontend on this instance; the schedule card is not served")
        return
    hass.data[_CARD_REGISTERED] = True
    path = Path(__file__).parent / "www" / CARD_FILENAME
    url = f"/{DOMAIN}/{CARD_FILENAME}"
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await hass.http.async_register_static_paths(
            [StaticPathConfig(url, str(path), True)]
        )
        frontend.add_extra_js_url(hass, f"{url}?v={integration.version}")
    except Exception:  # noqa: BLE001 - the card is not worth failing setup over
        _LOGGER.exception("Could not register the Heatmiser EDGE schedule card")


async def async_setup_entry(hass: HomeAssistant, entry: EdgeConfigEntry) -> bool:
    hub = build_hub(entry)
    coordinator = EdgeCoordinator(hass, entry, hub)
    try:
        await hub.async_connect()
    except EdgeConnectionError as err:
        await hub.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _drop_stale_entities(hass, entry, coordinator)
    _register_bus_device(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The weekly program is seconds of bus time on a wide bus, so it is read
    # after setup rather than during it. The entity that carries it is simply
    # unknown until this lands, which is the truth: nothing has read it yet.
    entry.async_create_background_task(
        hass, coordinator.async_load_schedules(), f"{DOMAIN}-schedules"
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EdgeConfigEntry) -> None:
    """Options changed: rebuild everything rather than reconcile it."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EdgeConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.hub.async_close()
    return unloaded


def _register_bus_device(
    hass: HomeAssistant, entry: EdgeConfigEntry, coordinator: EdgeCoordinator
) -> None:
    """Create the bus device up front, so it exists even before its children.

    Without it a `via_device` reference would dangle on the first setup.
    """
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **coordinator.bus_device_info()
    )


def _drop_stale_entities(
    hass: HomeAssistant, entry: EdgeConfigEntry, coordinator: EdgeCoordinator
) -> None:
    """Remove registry entries this configuration no longer produces.

    Two things make an entity stale, and both are breaking changes that would
    otherwise leave an unavailable "restored" entry behind for ever:

    - a register moving between platforms, since the unique id is the register
      number and does not encode the platform;
    - a thermostat's **model** changing between Heat and Timer, because the two
      variants mean genuinely different things at the same address. A Timer's
      register 3 is an on/off flag where a Heat's is a room temperature, and
      they share a unique id.

    Recorded history stays in the database under the old entity id; dashboards
    and automations naming it need fixing by hand.
    """
    registry = er.async_get(hass)
    wanted: dict[str, str] = {}
    for unit, reg in coordinator.entity_registers():
        unique_id = f"{entry.entry_id}_{unit.unit_id}_{reg.number}"
        wanted[unique_id] = coordinator.platform_for(unit, reg)
    for unit in coordinator.climate_units():
        wanted[f"{entry.entry_id}_{unit.unit_id}_climate"] = Platform.CLIMATE.value
    for unit in coordinator.units:
        # The weekly program is one entity carrying a grid, not a register - so
        # it is not in `entity_registers()` and would otherwise be swept away as
        # something this configuration no longer produces.
        wanted[f"{entry.entry_id}_{unit.unit_id}_schedule"] = Platform.SENSOR.value

    for existing in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        expected = wanted.get(existing.unique_id)
        if expected is None or expected != existing.domain:
            _LOGGER.debug(
                "Removing %s: no longer produced by this configuration",
                existing.entity_id,
            )
            registry.async_remove(existing.entity_id)

    # Keep the device registry's recorded model honest, so a Heat/Timer switch
    # is visible in the UI and not just in the entity set.
    device_registry = dr.async_get(hass)
    for unit in coordinator.units:
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_{unit.unit_id}")}
        )
        if device is not None and device.model != MODEL_LABELS[unit.model]:
            device_registry.async_update_device(
                device.id, model=MODEL_LABELS[unit.model]
            )

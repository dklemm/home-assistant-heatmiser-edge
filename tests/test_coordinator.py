"""The poll, and what it decides.

The headline requirement lives here: one thermostat going quiet must not take
the rest of the house with it.
"""

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heatmiser_edge.const import (
    CONF_CONTROLS,
    CONF_FRAMER,
    CONF_MODEL,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    CONF_UNITS,
    DOMAIN,
    FRAMER_SOCKET,
    MODEL_HEAT,
    MODEL_TIMER,
    SUPPRESSED,
    TRANSPORT_TCP,
)
from custom_components.heatmiser_edge.coordinator import EdgeCoordinator
from custom_components.heatmiser_edge.registers import registers_for

UNITS = [
    {CONF_UNIT_ID: 1, CONF_MODEL: MODEL_HEAT, "name": "Hall"},
    {CONF_UNIT_ID: 2, CONF_MODEL: MODEL_TIMER, "name": "Hot water"},
    {CONF_UNIT_ID: 3, CONF_MODEL: MODEL_HEAT, "name": "Study"},
]


def make_entry(hass, **overrides) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="tcp:127.0.0.1:5020",
        data={
            CONF_TRANSPORT: TRANSPORT_TCP,
            CONF_HOST: "127.0.0.1",
            CONF_PORT: 5020,
            CONF_FRAMER: FRAMER_SOCKET,
            CONF_UNITS: UNITS,
            **overrides,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass, **overrides) -> MockConfigEntry:
    entry = make_entry(hass, **overrides)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_polls_every_configured_thermostat(hass, mock_hub):
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data
    assert set(coordinator.data) == {1, 2, 3}
    assert all(unit.ok for unit in coordinator.data.values())
    assert coordinator.data[1].get(3) == 205


async def test_one_silent_thermostat_does_not_take_the_others_down(
    hass, mock_hub, fake_bus
):
    """The headline requirement.

    A stat taken off the wall, or with a broken spur, must go unavailable on its
    own. Failing the whole config entry would blank every other thermostat in
    the house for one dead device.
    """
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data

    del fake_bus[2]
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data[2].ok is False
    assert coordinator.data[2].words == {}
    assert coordinator.data[1].ok is True
    assert coordinator.data[3].ok is True


async def test_a_wholly_silent_bus_fails_the_entry(hass, mock_hub, fake_bus):
    """Nothing answering means the adapter, the wiring or the termination -
    something shared, and worth surfacing as a broken integration.
    """
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data

    fake_bus.clear()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False


async def test_a_dead_bus_at_startup_is_not_ready(hass, mock_hub, fake_bus):
    fake_bus.clear()
    entry = make_entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_entities_of_a_silent_thermostat_go_unavailable_alone(
    hass, mock_hub, fake_bus
):
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data

    del fake_bus[2]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hot_water_output").state == "unavailable"
    assert hass.states.get("sensor.hall_room_temperature").state == "20.5"


async def test_suppressed_registers_never_become_entities(hass, mock_hub):
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data
    for unit in coordinator.units:
        for reg in registers_for(unit.model):
            if reg.number in SUPPRESSED[unit.model]:
                assert coordinator.platform_for(unit, reg) is None


async def test_a_register_lands_on_at_most_one_platform(hass, mock_hub):
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data
    seen: set[tuple[int, int]] = set()
    for unit, reg in coordinator.entity_registers():
        key = (unit.unit_id, reg.number)
        assert key not in seen
        seen.add(key)


async def test_turning_controls_off_leaves_only_readings(hass, mock_hub):
    """The read-only escape hatch: every control disappears, readings stay.

    The READ_ONLY_RW registers survive it, because nothing there can write.
    """
    entry = await setup_entry(hass, **{CONF_CONTROLS: False})
    coordinator: EdgeCoordinator = entry.runtime_data
    platforms = {
        coordinator.platform_for(unit, reg)
        for unit, reg in coordinator.entity_registers()
    }
    assert platforms <= {"sensor", "binary_sensor"}
    assert hass.states.get("sensor.hall_room_temperature") is not None
    assert hass.states.get("number.hall_frost_set_temperature") is None
    assert hass.states.get("select.hall_tpi") is None
    # The communications id is a writable register that ships as a sensor, so it
    # survives the switch. (It is disabled by default, hence the registry check.)
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_1_31")


async def test_only_heat_units_get_a_climate_entity(hass, mock_hub):
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data
    assert [unit.unit_id for unit in coordinator.climate_units()] == [1, 3]
    assert hass.states.get("climate.hall") is not None
    assert hass.states.get("climate.study") is not None
    assert hass.states.get("climate.hot_water") is None


async def test_the_device_tree_hangs_off_the_bus(hass, mock_hub):
    from homeassistant.helpers import device_registry as dr

    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    bus = registry.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_bus")})
    assert bus is not None
    for unit_id in (1, 2, 3):
        device = registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_{unit_id}")}
        )
        assert device is not None
        assert device.via_device_id == bus.id
    # The firmware version register becomes the device's software version.
    hall = registry.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_1")})
    assert hall.sw_version == "42"


async def test_changing_a_units_model_removes_its_stale_entities(hass, mock_hub):
    """A Timer's register 3 is an on/off flag; a Heat's is a room temperature.

    They share a unique id, so switching the model must sweep the old entity
    away rather than leave an unavailable "restored" entry behind for ever.
    """
    from homeassistant.helpers import entity_registry as er

    entry = await setup_entry(hass)
    assert hass.states.get("sensor.hall_room_temperature") is not None

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_UNITS: [{CONF_UNIT_ID: 1, CONF_MODEL: MODEL_TIMER, "name": "Hall"}],
        },
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    # Register 3 is suppressed on a Timer (it mirrors register 32), so the old
    # room-temperature sensor must be gone, not merely unavailable.
    assert f"{entry.entry_id}_1_3" not in unique_ids
    # ...and so must the climate entity, which only a Heat has.
    assert f"{entry.entry_id}_1_climate" not in unique_ids


async def test_unloading_closes_the_bus(hass, mock_hub):
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("unit_id", [1, 3])
async def test_fahrenheit_is_read_per_thermostat(hass, mock_hub, fake_bus, unit_id):
    """Register 21 is a property of one stat, not of the bus."""
    fake_bus[unit_id][21] = 1
    entry = await setup_entry(hass)
    coordinator: EdgeCoordinator = entry.runtime_data
    assert coordinator.data[unit_id].fahrenheit is True
    assert coordinator.data[2].fahrenheit is False  # the Timer has no temperatures


async def test_diagnostics_carry_the_raw_registers(hass, mock_hub, fake_bus):
    """The raw words are the most useful thing in a bug report: both heuristics
    in detect.py can be re-run against them without access to the bus.
    """
    from custom_components.heatmiser_edge.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    del fake_bus[3]
    entry = await setup_entry(hass)
    report = await async_get_config_entry_diagnostics(hass, entry)

    assert report["bus"]["register_offset"] == -1
    assert report["entry_data"][CONF_UNITS]
    # The port or gateway address is the one identifying detail, and nothing
    # here needs it.
    assert CONF_HOST not in report["entry_data"]

    by_id = {unit["unit_id"]: unit for unit in report["units"]}
    assert by_id[1]["answering"] is True
    assert by_id[1]["registers"]["3"] == 205
    assert by_id[3]["answering"] is False
    assert by_id[3]["registers"] == {}

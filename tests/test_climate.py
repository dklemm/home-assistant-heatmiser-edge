"""The thermostat entity.

The mapping under test is deliberately one register per concept: register 32 is
the on/off mode, register 33 is the preset, register 7 is what the target reads
and register 34 is what a set writes. Every test here pins one of those, because
mixing them up is the easiest way to build a climate card that lies.
"""

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)

from tests.test_coordinator import setup_entry


async def _call(hass, service, **data):
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        service,
        {ATTR_ENTITY_ID: "climate.hall", **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_the_target_is_the_live_setpoint_not_the_last_override(
    hass, mock_hub, fake_bus
):
    """Register 7 is what the stat is actually working to right now.

    Register 34 only holds the last override that was written, and goes stale
    the moment a schedule period starts - so a card reading 34 would show a
    temperature the house is not being heated to.
    """
    fake_bus[1][7] = 195  # the schedule has moved the stat on
    fake_bus[1][34] = 210  # an override written hours ago
    await setup_entry(hass)
    assert hass.states.get("climate.hall").attributes[ATTR_TEMPERATURE] == 19.5


async def test_hvac_mode_follows_register_32_alone(hass, mock_hub, fake_bus):
    """Frost is a preset, not an HVAC mode: the stat is still heating."""
    fake_bus[1][32] = 1
    fake_bus[1][33] = 5  # frost
    await setup_entry(hass)
    state = hass.states.get("climate.hall")
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_PRESET_MODE] == "Frost"


async def test_turning_off_is_register_32(hass, mock_hub, fake_bus):
    fake_bus[1][32] = 0
    await setup_entry(hass)
    assert hass.states.get("climate.hall").state == HVACMode.OFF


async def test_hvac_action_needs_both_the_mode_and_the_relay(hass, mock_hub, fake_bus):
    entry = await setup_entry(hass)
    assert hass.states.get("climate.hall").attributes[ATTR_HVAC_ACTION] == (
        HVACAction.HEATING
    )

    fake_bus[1][2] = 0  # relay open: switched on, but not calling for heat
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("climate.hall").attributes[ATTR_HVAC_ACTION] == (
        HVACAction.IDLE
    )

    fake_bus[1][32] = 0  # switched off entirely
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("climate.hall").attributes[ATTR_HVAC_ACTION] == (
        HVACAction.OFF
    )


async def test_presets_come_from_register_33(hass, mock_hub, fake_bus):
    fake_bus[1][33] = 2
    await setup_entry(hass)
    state = hass.states.get("climate.hall")
    assert state.attributes[ATTR_PRESET_MODE] == "Hold"
    assert state.attributes["preset_modes"] == [
        "Change over",
        "Schedule",
        "Hold",
        "Advanced",
        "Away",
        "Frost",
    ]


async def test_an_undocumented_mode_reads_unknown(hass, mock_hub, fake_bus):
    """Better no preset than an invented one Home Assistant would reject."""
    fake_bus[1][33] = 9
    await setup_entry(hass)
    assert hass.states.get("climate.hall").attributes[ATTR_PRESET_MODE] is None


async def test_setting_a_temperature_writes_register_34(hass, mock_hub, fake_bus):
    """The manual calls 34 "Over right and Hold Set temperature" - which is
    exactly an override until the next schedule period, and exactly what
    dragging a thermostat should do.
    """
    await setup_entry(hass)
    await _call(hass, SERVICE_SET_TEMPERATURE, temperature=21.5)
    assert mock_hub == [(1, 34, 215)]
    assert fake_bus[1][34] == 215


async def test_setting_a_temperature_does_not_force_hold_mode(hass, mock_hub, fake_bus):
    """One service call, one register. Silently changing the operation mode as
    a side effect would take the stat off its schedule for good.
    """
    await setup_entry(hass)
    await _call(hass, SERVICE_SET_TEMPERATURE, temperature=21.5)
    assert [register for _, register, _ in mock_hub] == [34]
    assert fake_bus[1][33] == 1  # still in Schedule


async def test_setting_a_preset_writes_register_33(hass, mock_hub):
    await setup_entry(hass)
    await _call(hass, SERVICE_SET_PRESET_MODE, preset_mode="Away")
    assert mock_hub == [(1, 33, 4)]


async def test_turning_on_and_off_writes_register_32_only(hass, mock_hub, fake_bus):
    """Turning off leaves the preset alone, so turning back on resumes it."""
    await setup_entry(hass)
    await _call(hass, SERVICE_TURN_OFF)
    await _call(hass, SERVICE_TURN_ON)
    assert mock_hub == [(1, 32, 0), (1, 32, 1)]
    assert fake_bus[1][33] == 1


async def test_set_hvac_mode_writes_register_32(hass, mock_hub):
    await setup_entry(hass)
    await _call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.OFF)
    assert mock_hub == [(1, 32, 0)]


async def test_a_fahrenheit_stat_reports_fahrenheit_natively(hass, mock_hub, fake_bus):
    """Register 21 is the stat's own display unit, and every temperature
    register follows it - so the limits must too, or the user can write a value
    the thermostat then clamps.

    Home Assistant is put in US customary units here so the state attributes are
    the entity's native numbers; `test_fahrenheit_is_converted_for_a_metric_home`
    covers the other half.
    """
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    hass.config.units = US_CUSTOMARY_SYSTEM
    fake_bus[1].update({21: 1, 3: 690, 7: 700})
    await setup_entry(hass)
    state = hass.states.get("climate.hall")
    assert state.attributes["min_temp"] == 41.0
    assert state.attributes["max_temp"] == 95.0
    assert state.attributes[ATTR_TEMPERATURE] == 70.0
    assert state.attributes["current_temperature"] == 69.0


async def test_fahrenheit_is_converted_for_a_metric_home(hass, mock_hub, fake_bus):
    """A °F thermostat in a metric household still reads in °C on the card.

    That conversion is Home Assistant's job and only works if the entity
    declares its native unit honestly, rather than pretending everything is °C.
    """
    fake_bus[1].update({21: 1, 3: 690, 7: 700})
    await setup_entry(hass)
    state = hass.states.get("climate.hall")
    assert state.attributes["min_temp"] == 5.0
    assert state.attributes["max_temp"] == 35.0
    assert round(state.attributes[ATTR_TEMPERATURE], 1) == 21.1


async def test_a_fahrenheit_stat_is_written_in_its_own_unit(hass, mock_hub, fake_bus):
    """The value put in register 34 must be in the thermostat's unit, not ours.

    Home Assistant hands the entity a value already converted to its native
    unit, so 72 °F reaches the wire as 720 - no second conversion here.
    """
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    hass.config.units = US_CUSTOMARY_SYSTEM
    fake_bus[1].update({21: 1, 3: 690, 7: 700})
    await setup_entry(hass)
    await _call(hass, SERVICE_SET_TEMPERATURE, temperature=72.0)
    assert mock_hub == [(1, 34, 720)]


async def test_the_current_temperature_follows_the_sensor_selection(
    hass, mock_hub, fake_bus
):
    """Register 25 says which probe the stat controls from."""
    fake_bus[1].update({25: 1, 5: 198})  # remote air sensors only
    await setup_entry(hass)
    assert hass.states.get("climate.hall").attributes["current_temperature"] == 19.8


async def test_a_missing_selected_probe_falls_back_to_the_built_in_sensor(
    hass, mock_hub, fake_bus
):
    """The manual never says which probe register 3 reflects under each
    selection, so if the selected one reads absent the built-in reading is a
    better answer than none at all.
    """
    fake_bus[1].update({25: 1, 5: 0})  # remote air selected, but none fitted
    await setup_entry(hass)
    assert hass.states.get("climate.hall").attributes["current_temperature"] == 20.5


async def test_a_silent_thermostat_only_takes_its_own_climate_down(
    hass, mock_hub, fake_bus
):
    entry = await setup_entry(hass)
    del fake_bus[1]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("climate.hall").state == "unavailable"
    assert hass.states.get("climate.study").state == HVACMode.HEAT


async def test_a_timer_has_no_climate_entity(hass, mock_hub):
    """It has no temperature sensor at all; [off, heat] on one would be a lie."""
    await setup_entry(hass)
    assert hass.states.get("climate.hot_water") is None
    assert hass.states.get("switch.hot_water_timer") is not None
    assert hass.states.get("select.hot_water_operation_mode") is not None

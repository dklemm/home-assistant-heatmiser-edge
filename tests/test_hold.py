"""Operation modes the thermostat will actually accept, and the Hold action.

Both halves of this file exist because of one hardware finding (2026-08-13):
**the EDGE refuses some writes to register 33 and says nothing about it.**

- Under program mode 03, "None programmable", there is no weekly program, and
  the modes that depend on one are refused. Only Change over, Hold and Frost
  remain.
- Hold is refused unless register 38 holds a duration *first*. With 38 at zero
  an FC06 write of 2 to register 33 comes back echoing the old mode.

A control that silently does nothing is the worst kind, so the rule is: never
issue a write the stat will refuse — say why instead.
"""

import pytest
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_PRESET_MODE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.heatmiser_edge.const import (
    ATTR_DURATION,
    ATTR_TEMPERATURE,
    DOMAIN,
    MODE_CHANGE_OVER,
    MODE_HOLD,
    MODE_SCHEDULE,
    PROGRAM_MODE_NON_PROGRAMMABLE,
    REG_HOLD_DURATION,
    REG_HOLD_SETPOINT,
    REG_OPERATION_MODE,
    REG_PROGRAM_MODE,
    SERVICE_SET_HOLD,
)
from tests.test_coordinator import setup_entry


def device_id(hass, entry, suffix: str) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{suffix}"), entry.entry_id
    )
    assert device is not None
    return device.id


async def set_preset(hass, preset: str) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: "climate.hall", ATTR_PRESET_MODE: preset},
        blocking=True,
    )
    await hass.async_block_till_done()


# ----------------------------------------------------------------------
# Which modes are offered
# ----------------------------------------------------------------------


async def test_a_programmable_stat_offers_every_mode(hass, mock_hub, fake_bus):
    """The fake Heat is on program mode 1 (7 day), so nothing is restricted."""
    await setup_entry(hass)
    presets = hass.states.get("climate.hall").attributes[ATTR_PRESET_MODES]
    assert presets == ["Change over", "Schedule", "Hold", "Advanced", "Away", "Frost"]


async def test_non_programmable_offers_only_change_over_hold_and_frost(
    hass, mock_hub, fake_bus
):
    """Program mode 03 has no weekly program, so Schedule, Advanced and Away
    are refused by the stat. Offering them would be offering nothing.
    """
    fake_bus[1][REG_PROGRAM_MODE] = PROGRAM_MODE_NON_PROGRAMMABLE
    fake_bus[1][REG_OPERATION_MODE] = MODE_CHANGE_OVER  # as the real stat sits
    await setup_entry(hass)
    presets = hass.states.get("climate.hall").attributes[ATTR_PRESET_MODES]
    assert presets == ["Change over", "Hold", "Frost"]


async def test_the_current_mode_is_always_offered_even_when_restricted(
    hass, mock_hub, fake_bus
):
    """A stat can be left in Schedule and *then* switched to non-programmable.

    `preset_mode` must keep reporting the truth, and Home Assistant objects to a
    preset that is not among `preset_modes` - so the reading stays on the list
    whatever the writing allows.
    """
    fake_bus[1][REG_PROGRAM_MODE] = PROGRAM_MODE_NON_PROGRAMMABLE
    fake_bus[1][REG_OPERATION_MODE] = MODE_SCHEDULE
    await setup_entry(hass)
    state = hass.states.get("climate.hall")
    assert state.attributes[ATTR_PRESET_MODE] == "Schedule"
    assert state.attributes[ATTR_PRESET_MODES] == [
        "Change over",
        "Schedule",
        "Hold",
        "Frost",
    ]


async def test_selecting_a_refused_mode_raises_and_writes_nothing(
    hass, mock_hub, fake_bus
):
    """The stat would ignore this write. Silence is the bug being fixed."""
    fake_bus[1][REG_PROGRAM_MODE] = PROGRAM_MODE_NON_PROGRAMMABLE
    fake_bus[1][REG_OPERATION_MODE] = MODE_CHANGE_OVER
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await set_preset(hass, "Schedule")
    assert mock_hub == []


async def test_the_timer_mode_select_is_restricted_too(hass, mock_hub, fake_bus):
    """Register 29 is documented identically on a Timer, so the same rule
    applies - inferred rather than measured, since there is no Timer here.
    """
    fake_bus[2][REG_PROGRAM_MODE] = PROGRAM_MODE_NON_PROGRAMMABLE
    fake_bus[2][REG_OPERATION_MODE] = MODE_CHANGE_OVER
    await setup_entry(hass)
    options = hass.states.get("select.hot_water_operation_mode").attributes["options"]
    assert options == ["Change over", "Hold", "Standby"]


# ----------------------------------------------------------------------
# Hold needs a duration
# ----------------------------------------------------------------------


async def test_hold_without_a_duration_raises_and_writes_nothing(
    hass, mock_hub, fake_bus
):
    """Register 38 is 0 on the fake stat, exactly as it was on the real one."""
    await setup_entry(hass)
    assert fake_bus[1][REG_HOLD_DURATION] == 0
    with pytest.raises(ServiceValidationError):
        await set_preset(hass, "Hold")
    assert mock_hub == []


async def test_hold_with_a_duration_already_set_just_writes_the_mode(
    hass, mock_hub, fake_bus
):
    """Nothing to add if the stat already holds a duration - the preset works."""
    fake_bus[1][REG_HOLD_DURATION] = (2 << 8) | 30  # 2h30m
    await setup_entry(hass)
    await set_preset(hass, "Hold")
    assert mock_hub == [(1, REG_OPERATION_MODE, MODE_HOLD)]


# ----------------------------------------------------------------------
# The set_hold action
# ----------------------------------------------------------------------


async def test_set_hold_writes_duration_then_temperature_then_mode(
    hass, mock_hub, fake_bus
):
    """The order is the whole point: register 33 is validated by the stat
    against register 38, so the duration has to land first.
    """
    entry = await setup_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_HOLD,
        {
            "device_id": device_id(hass, entry, "1"),
            ATTR_DURATION: {"hours": 2, "minutes": 30},
            ATTR_TEMPERATURE: 18.5,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_hub == [
        (1, REG_HOLD_DURATION, (2 << 8) | 30),
        (1, REG_HOLD_SETPOINT, 185),
        (1, REG_OPERATION_MODE, MODE_HOLD),
    ]


async def test_set_hold_without_a_temperature_leaves_the_target_alone(
    hass, mock_hub, fake_bus
):
    """Holding at the current target is a real thing to want, and writing an
    invented setpoint to a live heating system is not.
    """
    entry = await setup_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_HOLD,
        {"device_id": device_id(hass, entry, "1"), ATTR_DURATION: {"minutes": 45}},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_hub == [
        (1, REG_HOLD_DURATION, 45),
        (1, REG_OPERATION_MODE, MODE_HOLD),
    ]


async def test_a_zero_duration_is_refused_before_anything_is_written(
    hass, mock_hub, fake_bus
):
    """Zero is not merely out of range: it is the exact value the stat reads as
    "no hold", so it would be accepted into 38 and then refuse the mode.
    """
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_HOLD,
            {"device_id": device_id(hass, entry, "1"), ATTR_DURATION: {"minutes": 0}},
            blocking=True,
        )
    assert mock_hub == []


async def test_a_temperature_outside_the_stats_range_is_refused(
    hass, mock_hub, fake_bus
):
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_HOLD,
            {
                "device_id": device_id(hass, entry, "1"),
                ATTR_DURATION: {"hours": 1},
                ATTR_TEMPERATURE: 40.0,  # above the manual's 35 °C
            },
            blocking=True,
        )
    assert mock_hub == []


async def test_the_bus_device_holds_every_thermostat_on_it(hass, mock_hub, fake_bus):
    """Same targeting rule as `set_time`: the bus stands for all of its stats.

    Only the two Heats — a Timer has no climate and no hold temperature, but it
    does have registers 38 and 33, so it takes the hold too.
    """
    entry = await setup_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_HOLD,
        {"device_id": device_id(hass, entry, "bus"), ATTR_DURATION: {"hours": 1}},
        blocking=True,
    )
    await hass.async_block_till_done()
    held = [unit for unit, register, _ in mock_hub if register == REG_OPERATION_MODE]
    assert sorted(held) == [1, 2, 3]


async def test_a_silent_thermostat_does_not_deny_the_others_their_hold(
    hass, mock_hub, fake_bus
):
    """The poll's rule, applied to a write, exactly as `set_time` does it."""
    entry = await setup_entry(hass)
    del fake_bus[2]  # unit 2 goes off the wall

    from unittest.mock import patch

    from custom_components.heatmiser_edge.hub import EdgeConnectionError, EdgeHub

    real_write = EdgeHub.async_write_register

    async def write(self, unit_id, register, value):
        if unit_id == 2:
            raise EdgeConnectionError("unit 2 is not answering")
        await real_write(self, unit_id, register, value)

    with patch.object(EdgeHub, "async_write_register", write):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_HOLD,
                {
                    "device_id": device_id(hass, entry, "bus"),
                    ATTR_DURATION: {"hours": 1},
                },
                blocking=True,
            )
        await hass.async_block_till_done()

    held = [unit for unit, register, _ in mock_hub if register == REG_OPERATION_MODE]
    assert sorted(held) == [1, 3]

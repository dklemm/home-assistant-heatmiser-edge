"""The `set_time` action.

Two things matter here and nothing else does. The timestamp must reach the wire
as **one** FC16 of four registers, because a torn one syncs the stat to a wrong
time it will then run its schedule against. And targeting must reach the right
thermostats: the action has no entity to aim at, so a device that is not ours,
or a bus that stands for three stats, are both cases the resolver has to get
right on its own.
"""

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heatmiser_edge.const import (
    ATTR_DATETIME,
    ATTR_DST,
    CONF_CONTROLS,
    DOMAIN,
    REG_DST_ENABLED,
    REG_RTC,
    SERVICE_SET_TIME,
)
from custom_components.heatmiser_edge.hub import EdgeConnectionError
from tests.test_coordinator import setup_entry

# 2026-08-12 14:30:45 as the manual packs it: year, month<<8|day,
# hour<<8|minute, second.
WHEN = "2026-08-12 14:30:45"
WORDS = [2026, (8 << 8) | 12, (14 << 8) | 30, 45]


def device_id(hass, entry, suffix: str) -> str:
    """The registry id of one thermostat ("1") or of the bus ("bus")."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{suffix}"), entry.entry_id
    )
    assert device is not None
    return device.id


async def call(hass, target: dict, **data) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE_SET_TIME, {**target, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_the_timestamp_goes_out_as_one_block(hass, mock_hub, fake_bus):
    """Four registers, one FC16, in the manual's packing.

    Written singly the thermostat sees three partial timestamps and the manual
    says it syncs as soon as it likes what it has - so a stat could land on the
    1st of January and run the whole week's schedule against it.
    """
    entry = await setup_entry(hass)
    await call(hass, {"device_id": device_id(hass, entry, "1")}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == [(1, REG_RTC, WORDS)]
    assert [fake_bus[1][n] for n in range(47, 51)] == WORDS


async def test_no_datetime_means_now(hass, mock_hub):
    """The common case is an automation with no fields at all."""
    entry = await setup_entry(hass)
    with patch.object(dt_util, "now", return_value=datetime(2026, 8, 12, 14, 30, 45)):
        await call(hass, {"device_id": device_id(hass, entry, "1")})

    assert mock_hub == [(1, REG_RTC, WORDS)]


async def test_an_aware_datetime_becomes_local_wall_clock(hass, mock_hub):
    """The thermostat has no timezone, so one has to be resolved away here.

    A template can hand us a UTC datetime; putting 13:30 UTC on the wire when
    the house is on BST would set the stat an hour slow.
    """
    await hass.config.async_set_time_zone("Europe/London")
    entry = await setup_entry(hass)
    await call(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        **{ATTR_DATETIME: "2026-08-12T13:30:45+00:00"},
    )

    assert mock_hub == [(1, REG_RTC, WORDS)]


async def test_daylight_saving_is_written_before_the_clock(hass, mock_hub, fake_bus):
    """Register 30 is a separate register, so a separate FC06 - and it goes
    first, so the time lands under the setting the user just chose.
    """
    entry = await setup_entry(hass)
    await call(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        **{ATTR_DATETIME: WHEN, ATTR_DST: False},
    )

    assert mock_hub == [(1, REG_DST_ENABLED, 0), (1, REG_RTC, WORDS)]
    assert fake_bus[1][REG_DST_ENABLED] == 0


async def test_daylight_saving_is_left_alone_when_not_given(hass, mock_hub, fake_bus):
    entry = await setup_entry(hass)
    fake_bus[1][REG_DST_ENABLED] = 1
    await call(hass, {"device_id": device_id(hass, entry, "1")}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == [(1, REG_RTC, WORDS)]
    assert fake_bus[1][REG_DST_ENABLED] == 1


async def test_the_bus_device_means_every_thermostat_on_it(hass, mock_hub):
    """"Set the time on the EDGE bus" reads as all of them, and a Timer needs
    its clock quite as much as a Heat does.
    """
    entry = await setup_entry(hass)
    await call(hass, {"device_id": device_id(hass, entry, "bus")}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == [(1, REG_RTC, WORDS), (2, REG_RTC, WORDS), (3, REG_RTC, WORDS)]


async def test_the_bus_and_one_of_its_thermostats_writes_once(hass, mock_hub):
    """Targeting both is not an error, and must not write unit 1 twice."""
    entry = await setup_entry(hass)
    await call(
        hass,
        {"device_id": [device_id(hass, entry, "bus"), device_id(hass, entry, "1")]},
        **{ATTR_DATETIME: WHEN},
    )

    assert sorted(unit for unit, _, _ in mock_hub) == [1, 2, 3]


async def test_targeting_an_entity_reaches_its_thermostat(hass, mock_hub):
    """No RTC entity exists to aim at, so an entity resolves to its device."""
    entry = await setup_entry(hass)
    await call(hass, {"entity_id": "climate.hall"}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == [(1, REG_RTC, WORDS)]


async def test_a_device_that_is_not_ours_is_refused(hass, mock_hub):
    """Named outright, so a silent no-op would be a lie about what happened."""
    await setup_entry(hass)
    stranger = MockConfigEntry(domain="demo")
    stranger.add_to_hass(hass)
    other = dr.async_get(hass).async_get_or_create(
        config_entry_id=stranger.entry_id, identifiers={("demo", "not-a-stat")}
    )
    with pytest.raises(ServiceValidationError):
        await call(hass, {"device_id": other.id}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == []


async def test_a_read_only_bus_refuses_to_have_its_clock_set(hass, mock_hub):
    """"Allow changing settings" off means read-only, and an action is not
    exempt from that just because it has no entity.
    """
    entry = await setup_entry(hass, **{CONF_CONTROLS: False})
    with pytest.raises(ServiceValidationError):
        await call(
            hass, {"device_id": device_id(hass, entry, "1")}, **{ATTR_DATETIME: WHEN}
        )

    assert mock_hub == []


async def test_targeting_nothing_of_ours_is_refused(hass, mock_hub):
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await call(hass, {"entity_id": "sun.sun"}, **{ATTR_DATETIME: WHEN})

    assert mock_hub == []


async def test_a_silent_thermostat_does_not_deny_the_others_their_clock(
    hass, mock_hub, fake_bus
):
    """The poll's rule, applied to a write: one stat off the wall must not stop
    the other two being set. The failure is still reported, at the end.
    """
    entry = await setup_entry(hass)

    async def write_registers(self, unit_id, register, values):
        if unit_id == 2:
            raise EdgeConnectionError("unit 2 did not answer")
        mock_hub.append((unit_id, register, list(values)))

    with patch(
        "custom_components.heatmiser_edge.hub.EdgeHub.async_write_registers",
        write_registers,
    ):
        with pytest.raises(HomeAssistantError):
            await call(
                hass,
                {"device_id": device_id(hass, entry, "bus")},
                **{ATTR_DATETIME: WHEN},
            )

    assert [unit for unit, _, _ in mock_hub] == [1, 3]


async def test_a_year_outside_the_manuals_range_is_refused(hass, mock_hub):
    """`encode_rtc` guards the manual's 2000-5000, and it is checked once, up
    front — a timestamp the registers cannot hold stops the call, not half a bus.
    """
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            **{ATTR_DATETIME: "1999-08-12 14:30:45"},
        )

    assert mock_hub == []

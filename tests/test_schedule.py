"""The weekly program: reading it, and the writes it does and does not make.

Three things are being defended here.

**The bus.** The program is 168 registers on a Heat, three FC03 packets against
the poll's one, so it must never join the poll and an edit to one period must
cost one FC16 and not six.

**The grid.** Day 0 is Sunday and a period is four registers, so an off-by-one
in either direction lands on a real, plausible-looking register of the wrong day
- the kind of mistake that only shows up as someone's heating coming on at the
wrong time.

**The refusals.** Every check runs before anything reaches the wire, and with
several thermostats targeted that has to hold across all of them: validating as
you go would leave the first stat edited and the second refused.
"""

import pytest
from homeassistant.const import MATCH_ALL
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.heatmiser_edge.const import (
    ATTR_DAYS,
    ATTR_PERIODS,
    CONF_CONTROLS,
    DOMAIN,
    MODEL_HEAT,
    MODEL_TIMER,
    PROGRAM_MODE_24_HOUR,
    PROGRAM_MODE_5_2,
    PROGRAM_MODE_NON_PROGRAMMABLE,
    REG_PROGRAM_MODE,
    REG_PROGRAM_TYPE,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)
from custom_components.heatmiser_edge.schedule import (
    DAYS,
    ScheduleError,
    format_week,
    resolve_days,
    usable_periods,
)
from tests.conftest import heat_schedule, timer_schedule
from tests.test_coordinator import setup_entry
from tests.test_services import device_id

# Manual: register 51 is Sunday Period 1 Hour, and each day is 6 periods of 4
# registers. So Monday Period 1 Hour is 51 + 24 = 75, which is what the
# manual's own table says - the arithmetic is checked against it, not derived
# from the code under test.
MONDAY_P1 = 75
MONDAY_P2 = 79
# Timer: 4 periods of 4 registers, so Monday Period 1 On Hour is 51 + 16 = 67.
TIMER_MONDAY_P1 = 67


async def get_schedule(hass, target: dict) -> dict:
    return await hass.services.async_call(
        DOMAIN, SERVICE_GET_SCHEDULE, target, blocking=True, return_response=True
    )


async def set_schedule(hass, target: dict, days, periods) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        {**target, ATTR_DAYS: days, ATTR_PERIODS: periods},
        blocking=True,
    )
    await hass.async_block_till_done()


# ----------------------------------------------------------------------
# Reading the grid
# ----------------------------------------------------------------------


def test_the_grid_is_read_against_the_manuals_own_day_order():
    """Day 0 is Sunday, and the manual's defaults differ by day.

    Sunday starts at 09:00 and Monday at 07:00, so reading the week off by one
    day would swap them - and be entirely plausible on a dashboard.
    """
    week = format_week(MODEL_HEAT, heat_schedule())

    assert list(week) == list(DAYS)
    assert week["sunday"][0] == {"period": 1, "time": "09:00", "temperature": 21.0}
    assert week["monday"][0] == {"period": 1, "time": "07:00", "temperature": 21.0}
    assert week["monday"][2] == {"period": 3, "time": "16:00", "temperature": 21.0}


def test_hour_24_reads_as_a_period_that_is_not_in_use():
    """The manual's only "off": "The current schedule is invalid when the
    hour = 24". The stored temperature is still reported, because it is a real
    value and an editor switching the period back on should not invent one.
    """
    week = format_week(MODEL_HEAT, heat_schedule())

    assert week["monday"][4] == {"period": 5, "time": None, "temperature": 21.0}


def test_a_timer_period_reads_as_an_on_and_an_off():
    """A Timer's four registers are two times, not a time and a temperature."""
    week = format_week(MODEL_TIMER, timer_schedule())

    assert week["monday"][0] == {"period": 1, "on": "07:00", "off": "09:00"}
    assert week["monday"][3] == {"period": 4, "on": None, "off": None}


def test_a_fahrenheit_stat_reads_its_own_units():
    """Set temperatures follow register 21 like every other temperature: 70 °F
    is 700 on the wire, which the °C plausibility band would still accept but
    would then report as 70.0 - correct only because the unit travels with it.
    """
    words = {51: 9, 52: 0, 53: 700}
    week = format_week(MODEL_HEAT, words, fahrenheit=True)

    assert week["sunday"][0] == {"period": 1, "time": "09:00", "temperature": 70.0}


def test_how_many_periods_a_day_follows_register_28():
    """A Heat's grid is always six, but it only *runs* four unless register 28
    says otherwise - so an editor must be told how many to offer.
    """
    assert usable_periods(MODEL_HEAT, 0) == 4
    assert usable_periods(MODEL_HEAT, 1) == 6
    # A Timer has no register 28 at all; its grid is four and that is that.
    assert usable_periods(MODEL_TIMER, None) == 4


# ----------------------------------------------------------------------
# Which days a call touches
# ----------------------------------------------------------------------


def test_a_day_named_in_5_2_mode_writes_every_day_sharing_its_program():
    """In 5/2 mode the five weekdays are one program, and the manual does not
    say which of the five day blocks the thermostat reads. Writing all of them
    makes the question moot instead of betting on an answer.
    """
    assert resolve_days(["monday"], PROGRAM_MODE_5_2) == [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
    ]
    assert resolve_days(["saturday"], PROGRAM_MODE_5_2) == ["sunday", "saturday"]


def test_24_hour_mode_writes_the_whole_week():
    assert resolve_days(["monday"], PROGRAM_MODE_24_HOUR) == list(DAYS)


def test_7_day_mode_writes_exactly_what_was_named():
    assert resolve_days(["monday", "friday"], 1) == ["monday", "friday"]
    assert resolve_days(["weekend"], 1) == ["sunday", "saturday"]


def test_a_day_that_is_not_a_day_is_refused():
    with pytest.raises(ScheduleError) as err:
        resolve_days(["someday"], 1)
    assert err.value.key == "bad_schedule_day"


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------


async def test_a_period_is_one_fc16_of_its_own(hass, mock_hub, fake_bus):
    """Three registers - hour, minute, set temperature - in one block.

    Not the day's whole 24: six of those are Reserved, and writing zeros into
    undocumented registers on a live heating system is what this integration
    refuses everywhere else. A period is also the right atomic unit, since its
    time and its temperature are one instruction.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [{"period": 1, "time": "06:30", "temperature": 20.5}],
    )

    assert (1, MONDAY_P1, [6, 30, 205]) in mock_hub
    assert [fake_bus[1][n] for n in range(MONDAY_P1, MONDAY_P1 + 4)] == [6, 30, 205, 0]


async def test_only_the_periods_that_change_are_written(hass, mock_hub):
    """The whole point of reading the program first. Editing one period on one
    day costs one FC16; at 9600 baud, writing the other five as well is the
    difference between a quarter of a second and a second and a half.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [
            {"period": 1, "time": "07:00", "temperature": 21.0},  # unchanged
            {"period": 2, "time": "09:30", "temperature": 16.0},  # moved 30 min
        ],
    )

    assert [write for write in mock_hub if isinstance(write[2], list)] == [
        (1, MONDAY_P2, [9, 30, 160])
    ]


async def test_switching_a_period_off_keeps_its_temperature(hass, mock_hub, fake_bus):
    """Two registers, not three: the stored set temperature survives, so the
    period can be switched back on without inventing one.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [{"period": 4, "time": "off"}],
    )

    assert (1, MONDAY_P1 + 3 * 4, [24, 0]) in mock_hub
    # 16.0 °C, exactly as it was before the period was switched off.
    assert fake_bus[1][MONDAY_P1 + 3 * 4 + 2] == 160


async def test_switching_a_period_on_again_keeps_the_stored_temperature(
    hass, mock_hub, fake_bus
):
    """The other half of the same bargain: no temperature given, so the one the
    thermostat still holds is written back with the new time.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [{"period": 5, "time": "23:30"}],
    )

    assert (1, MONDAY_P1 + 4 * 4, [23, 30, 210]) in mock_hub


async def test_a_timer_period_writes_all_four_registers(hass, mock_hub, fake_bus):
    """A Timer's period is an on and an off with nothing reserved between them,
    and half of one would switch on and never off.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "2")},
        ["monday"],
        [{"period": 1, "on": "06:00", "off": "08:30"}],
    )

    assert (2, TIMER_MONDAY_P1, [6, 0, 8, 30]) in mock_hub


async def test_5_2_mode_writes_all_five_weekdays(hass, mock_hub, fake_bus):
    """One named day, five days written - see `resolve_days`."""
    entry = await setup_entry(hass)
    fake_bus[1][REG_PROGRAM_MODE] = PROGRAM_MODE_5_2
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    mock_hub.clear()

    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [{"period": 1, "time": "06:30", "temperature": 20.5}],
    )

    written = [write[1] for write in mock_hub if isinstance(write[2], list)]
    assert written == [MONDAY_P1 + day * 24 for day in range(5)]


# ----------------------------------------------------------------------
# What is refused, before anything reaches the wire
# ----------------------------------------------------------------------


async def test_a_day_that_would_not_run_forwards_is_refused(hass, mock_hub):
    """Checked against the *merged* day, not just what was sent: moving period
    2 back before period 1 is only visible once the two are seen together.
    """
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 2, "time": "06:00", "temperature": 16.0}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_gap_in_a_day_is_refused(hass, mock_hub):
    """The manual's own defaults always park the unused periods at the end, and
    a period following a switched-off one has no defined meaning.
    """
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 2, "time": "off"}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_non_programmable_thermostat_is_refused(hass, mock_hub, fake_bus):
    """Program mode 03 has no weekly program, so storing one would be a control
    that does nothing - the bug the whole curated-table doctrine exists to stop.
    """
    entry = await setup_entry(hass)
    fake_bus[1][REG_PROGRAM_MODE] = PROGRAM_MODE_NON_PROGRAMMABLE
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    mock_hub.clear()

    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 1, "time": "06:30", "temperature": 20.5}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_period_the_thermostat_does_not_run_is_refused(hass, mock_hub, fake_bus):
    """Register 28 at 0 means four periods a day, so period 5 exists in the
    registers but is not something this thermostat runs.
    """
    entry = await setup_entry(hass)
    fake_bus[1][REG_PROGRAM_TYPE] = 0
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    mock_hub.clear()

    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 5, "time": "23:30", "temperature": 18.0}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_temperature_the_stat_will_not_take_is_refused(hass, mock_hub):
    """The manual's 5-35 °C, against the stat's own display unit."""
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 1, "time": "07:00", "temperature": 40.0}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_time_that_is_not_a_time_is_refused(hass, mock_hub):
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 1, "time": "half seven", "temperature": 21.0}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_nothing_is_written_when_one_targeted_stat_refuses(hass, mock_hub, fake_bus):
    """The two-pass split, and the reason for it. Unit 3 is in 4-period mode, so
    a write of period 5 to the bus must leave unit 1 untouched as well - a
    validate-as-you-go loop would already have written it.
    """
    entry = await setup_entry(hass)
    fake_bus[3][REG_PROGRAM_TYPE] = 0
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    mock_hub.clear()

    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "bus")},
            ["monday"],
            [{"period": 5, "time": "23:30", "temperature": 18.0}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


async def test_a_read_only_bus_refuses_to_have_its_program_set(hass, mock_hub):
    """"Allow changing settings" off means read-only, action or not."""
    entry = await setup_entry(hass, **{CONF_CONTROLS: False})
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 1, "time": "06:30", "temperature": 20.5}],
        )

    assert [write for write in mock_hub if isinstance(write[2], list)] == []


# ----------------------------------------------------------------------
# The action's own behaviour
# ----------------------------------------------------------------------


async def test_get_schedule_reports_the_grid_and_what_shapes_it(hass, mock_hub):
    """Registers 28 and 29 travel with the grid, because they are what decides
    how many periods an editor may offer and how many days are independent.
    """
    entry = await setup_entry(hass)
    response = await get_schedule(hass, {"device_id": device_id(hass, entry, "1")})

    (stat,) = response["thermostats"]
    assert stat["unit_id"] == 1
    assert stat["model"] == MODEL_HEAT
    assert stat["periods"] == 6
    assert stat["program_mode"] == "7 day"
    assert stat["temperature_unit"] == "°C"
    assert stat["schedule"]["monday"][0]["time"] == "07:00"
    assert response["failed"] == []


async def test_a_read_only_bus_still_shows_its_program(hass, mock_hub):
    """Read-only has to mean less than read, or it means nothing."""
    entry = await setup_entry(hass, **{CONF_CONTROLS: False})
    response = await get_schedule(hass, {"device_id": device_id(hass, entry, "1")})

    assert len(response["thermostats"]) == 1


async def test_one_silent_thermostat_does_not_hide_the_others_programs(
    hass, mock_hub, fake_bus
):
    """The poll's rule, applied to a read. Raising would return nothing at all,
    so the failure is reported alongside the programs that did come back.
    """
    entry = await setup_entry(hass)
    del fake_bus[2]
    response = await get_schedule(hass, {"device_id": device_id(hass, entry, "bus")})

    assert [stat["unit_id"] for stat in response["thermostats"]] == [1, 3]
    assert response["failed"] == ["Hot water"]


async def test_get_schedule_fails_only_when_nothing_answered(hass, mock_hub, fake_bus):
    entry = await setup_entry(hass)
    fake_bus.clear()
    with pytest.raises(HomeAssistantError):
        await get_schedule(hass, {"device_id": device_id(hass, entry, "bus")})


async def test_a_silent_thermostat_does_not_deny_the_others_their_program(
    hass, mock_hub, fake_bus
):
    """One stat off the wall must not stop the other two being written, and the
    failure is still reported at the end.
    """
    entry = await setup_entry(hass)
    del fake_bus[2]

    with pytest.raises(HomeAssistantError):
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "bus")},
            ["monday"],
            [{"period": 1, "time": "06:30", "temperature": 20.5}],
        )

    written = [write[0] for write in mock_hub if isinstance(write[2], list)]
    assert written == [1, 3]


async def test_a_stat_that_does_not_keep_the_write_is_reported(hass, mock_hub, fake_bus):
    """FC16 echoes the address and the quantity but never the values, so unlike
    the FC06 every entity uses it cannot show a silent clamp. Reading back is
    the only verification there is, and a mismatch has to be said out loud.
    """
    entry = await setup_entry(hass)

    async def clamping_write(self, unit_id, register, values):
        mock_hub.append((unit_id, register, list(values)))
        # Takes the address, keeps the old value - what a refusing stat looks
        # like over FC16.

    with pytest.raises(HomeAssistantError), pytest.MonkeyPatch.context() as patched:
        patched.setattr(
            "custom_components.heatmiser_edge.hub.EdgeHub.async_write_registers",
            clamping_write,
        )
        await set_schedule(
            hass,
            {"device_id": device_id(hass, entry, "1")},
            ["monday"],
            [{"period": 1, "time": "06:30", "temperature": 20.5}],
        )


# ----------------------------------------------------------------------
# The bus
# ----------------------------------------------------------------------


async def test_the_poll_never_reads_the_program(hass, mock_hub, fake_bus):
    """The headline constraint. 168 registers is three FC03 packets against the
    poll's one - about 0.6 s of a 9600-baud bus per thermostat, 19 s across a
    full wire - to watch values that only move when somebody moves them.
    """
    from custom_components.heatmiser_edge.hub import EdgeHub

    entry = await setup_entry(hass)
    coordinator = entry.runtime_data

    spans: list[tuple[int, int, int]] = []
    real_span = EdgeHub.async_read_span

    async def counting_span(self, unit_id, start, count):
        spans.append((unit_id, start, count))
        return await real_span(self, unit_id, start, count)

    reads: list[tuple[int, int, int]] = []
    real_block = EdgeHub.async_read_block

    async def counting_block(self, unit_id, start, count, timeout=None):
        reads.append((unit_id, start, count))
        return await real_block(self, unit_id, start, count)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(EdgeHub, "async_read_span", counting_span)
        patched.setattr(EdgeHub, "async_read_block", counting_block)

        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert spans == [], "the poll read the weekly program"

        # And the program, when asked for explicitly, spans the whole grid in
        # packets no larger than the manual's 60-register limit.
        await coordinator.async_read_schedule(1)

    assert spans == [(1, 51, 168)]
    assert [(start, count) for _, start, count in reads] == [(51, 60), (111, 60), (171, 48)]


# ----------------------------------------------------------------------
# The entity that carries the grid
# ----------------------------------------------------------------------


async def test_the_whole_program_rides_on_one_entity(hass, mock_hub):
    """126 values as attributes on one entity, not 126 entities.

    This is what lets a markdown card or a template render the week with no
    JavaScript at all, and it is why the state is a timestamp: a state is one
    scalar capped at 255 characters, so it cannot be the grid.
    """
    await setup_entry(hass)
    state = hass.states.get("sensor.hall_weekly_program")

    assert state is not None
    assert state.attributes["periods"] == 6
    assert state.attributes["program_mode"] == "7 day"
    assert state.attributes["schedule"]["monday"][0] == {
        "period": 1,
        "time": "07:00",
        "temperature": 21.0,
    }
    # The state says how fresh it is, because the program is never polled.
    assert state.state not in ("unknown", "unavailable")


async def test_the_grid_is_never_recorded(hass, mock_hub):
    """A week of values written to the database on every state change, for
    settings that move perhaps twice a year, is a cost with nothing behind it.
    """
    from custom_components.heatmiser_edge.sensor import EdgeScheduleSensor

    assert MATCH_ALL in EdgeScheduleSensor._unrecorded_attributes


async def test_the_entity_follows_a_write(hass, mock_hub):
    """`set_schedule` re-reads to verify, so the card it feeds must move too -
    otherwise the program would show its old times until the next restart.
    """
    entry = await setup_entry(hass)
    await set_schedule(
        hass,
        {"device_id": device_id(hass, entry, "1")},
        ["monday"],
        [{"period": 1, "time": "06:30", "temperature": 20.5}],
    )

    state = hass.states.get("sensor.hall_weekly_program")
    assert state.attributes["schedule"]["monday"][0] == {
        "period": 1,
        "time": "06:30",
        "temperature": 20.5,
    }


async def test_a_timer_carries_its_own_shape(hass, mock_hub):
    entry = await setup_entry(hass)
    state = hass.states.get("sensor.hot_water_weekly_program")

    assert state.attributes["periods"] == 4
    assert state.attributes["schedule"]["monday"][0] == {
        "period": 1,
        "on": "07:00",
        "off": "09:00",
    }


async def test_the_program_is_not_published_to_entities(hass, mock_hub):
    """Keeping it out of `coordinator.data` is what stops 170 rarely-moving
    registers waking every entity on the unit each time it is read.
    """
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    await coordinator.async_read_schedule(1)

    assert max(coordinator.data[1].words) == 50
    assert coordinator.schedules[1][MONDAY_P1] == 7

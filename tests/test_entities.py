"""What ships, and what each control writes.

Two jobs. First, a snapshot of the exact entity inventory per model - the gate
that adding a register does not quietly change what an existing install has.
Second, the write paths, which matter more than anything else here because they
reach a live heating system.
"""

import pytest
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.helpers import entity_registry as er

from custom_components.heatmiser_edge.const import DOMAIN
from tests.test_coordinator import setup_entry


def inventory(hass, entry, unit_id: int) -> dict[str, str]:
    """{register suffix: platform} for one thermostat, from the registry.

    The registry rather than the states, so entities that ship disabled by
    default are counted too.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{unit_id}_"
    return {
        entity.unique_id.removeprefix(prefix): entity.domain
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.unique_id.startswith(prefix)
    }


async def test_heat_inventory(hass, mock_hub):
    """Exactly what an EDGE Heat produces. Registers 7, 8, 9, 32, 33 and 34 are
    absent because the climate entity owns them.

    Two entities here are not registers at all: the climate entity, and the
    weekly program - which is 126 values carried as attributes on one entity
    rather than 126 entities, and is written by `heatmiser_edge.set_schedule`.
    """
    entry = await setup_entry(hass)
    assert inventory(hass, entry, 1) == {
        "climate": "climate",
        "schedule": "sensor",  # the weekly program, as attributes
        "1": "sensor",  # firmware version
        "2": "binary_sensor",  # relay
        "3": "sensor",  # room temperature
        "4": "sensor",  # floor temperature
        "5": "sensor",  # remote sensor temperature
        "6": "binary_sensor",  # window
        "10": "sensor",  # current schedule period
        "11": "sensor",  # next schedule period
        "12": "binary_sensor",  # daylight saving active
        "13": "sensor",  # rate of change
        "15": "sensor",  # board sensor, before compensation
        "16": "sensor",  # board sensor, after compensation
        "21": "sensor",  # temperature format: writable, but read-only by policy
        "22": "select",  # switching differential
        "23": "number",  # output delay
        "24": "number",  # up/down limit
        "25": "select",  # sensor selection
        "26": "number",  # floor limit
        "27": "select",  # optimum start
        "28": "select",  # program type
        "29": "select",  # program mode
        "30": "switch",  # daylight saving
        "31": "sensor",  # communications id: never writable
        "35": "number",  # advanced setpoint
        "37": "number",  # frost setpoint
        "38": "number",  # hold duration
        "39": "sensor",  # away until, time
        "40": "sensor",  # away until, date
        "41": "sensor",  # away until, year
        "42": "binary_sensor",  # keylock: writable, but semantics unproven
    }


async def test_timer_inventory(hass, mock_hub):
    """An EDGE Timer. No climate, and registers 3 and 9 are absent because they
    mirror the writable 32 and 33.
    """
    entry = await setup_entry(hass)
    assert inventory(hass, entry, 2) == {
        "schedule": "sensor",  # the weekly program, as attributes
        "1": "sensor",
        "2": "binary_sensor",  # output relay
        "4": "sensor",  # current schedule period
        "5": "sensor",  # next schedule period
        "6": "binary_sensor",  # daylight saving active
        "29": "select",  # program mode
        "30": "switch",  # daylight saving
        "31": "sensor",
        "32": "switch",  # the primary control
        "33": "select",  # operation mode
        "34": "switch",  # output override
        "38": "number",  # hold duration
        "39": "sensor",
        "40": "sensor",
        "41": "sensor",
    }


@pytest.mark.parametrize("unit_id", [1, 2, 3])
async def test_the_factory_reset_register_never_ships(hass, mock_hub, unit_id):
    """Register 46 restores factory settings. It is not in the map, not polled,
    and has no entity on either model - the only way to reach it is the field
    CLI, behind an explicit flag.
    """
    entry = await setup_entry(hass)
    assert "46" not in inventory(hass, entry, unit_id)


@pytest.mark.parametrize("register", ["43", "44"])
@pytest.mark.parametrize("unit_id", [1, 2, 3])
async def test_the_tpi_registers_never_ship(hass, mock_hub, unit_id, register):
    """43 (TPI) and 44 (TPI minimum on time) produce nothing at all.

    Hardware 2026-08-13: both read 20, against documented ranges of 0-3 and 0-5,
    on a correctly-aligned block - so we do not know what the values mean. And
    the EDGE keypad menu has no TPI entry, so they are reachable only over
    Modbus and a wrong write cannot be undone at the thermostat. Not even a
    read-only entity: the reading is meaningless until the encoding is known.
    """
    entry = await setup_entry(hass)
    assert register not in inventory(hass, entry, unit_id)


async def test_the_keylock_ships_read_only_and_disabled(hass, mock_hub):
    """"Cancel Keylock (Value = 0), General PassWord: 6343" does not establish
    that writing 6343 locks it. A wrong write could set a password the owner
    cannot clear from the keypad, so it reads only until hardware settles it.
    """
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_1_42"
    )
    assert entity_id is not None
    assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_probe_sensors_ship_disabled(hass, mock_hub):
    """Most installs fit neither a floor nor a remote probe."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    for register in (4, 5):
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_1_{register}"
        )
        assert registry.async_get(entity_id).disabled_by is not None


async def test_an_unfitted_probe_reads_unknown_not_zero_degrees(hass, mock_hub):
    """0 on a floor register means "no probe", not 0.0 °C."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_1_4")
    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unknown"


async def test_enum_sensors_report_labels(hass, mock_hub):
    await setup_entry(hass)
    assert hass.states.get("sensor.hall_current_schedule_period").state == "Period 2"
    assert hass.states.get("sensor.hot_water_next_schedule_period").state == "Period 3"


async def test_an_undocumented_enum_value_reads_unknown(hass, mock_hub, fake_bus):
    """Register 10 documents 0-6; anything else is not a period we can name."""
    fake_bus[1][10] = 9
    await setup_entry(hass)
    assert hass.states.get("sensor.hall_current_schedule_period").state == "unknown"


async def test_diagnostic_registers_are_categorised(hass, mock_hub):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    firmware = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_1_1"
    )
    assert registry.async_get(firmware).entity_category == "diagnostic"
    room = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_1_3")
    assert registry.async_get(room).entity_category is None


# ----------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------


async def test_a_number_writes_its_scaled_value(hass, mock_hub, fake_bus):
    await hass.async_block_till_done()
    await setup_entry(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.hall_frost_set_temperature", "value": 10.5},
        blocking=True,
    )
    assert mock_hub == [(1, 37, 105)]


async def test_the_hold_duration_is_one_entity_in_minutes(hass, mock_hub, fake_bus):
    """Register 38 packs hours and minutes into one word. Two entities writing
    it would be a read-modify-write race, and 210 is what an automation means
    by three and a half hours.
    """
    await setup_entry(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.hall_hold_duration", "value": 210},
        blocking=True,
    )
    assert mock_hub == [(1, 38, 0x031E)]
    assert hass.states.get("number.hall_hold_duration").state == "210.0"


async def test_a_select_writes_the_raw_wire_value(hass, mock_hub):
    """The labels are ours; the numbers are the manual's."""
    await setup_entry(hass)
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.hall_sensor_selection", "option": "Remote air only"},
        blocking=True,
    )
    assert mock_hub == [(1, 25, 1)]


async def test_the_switching_differential_labels_follow_the_display_unit(
    hass, mock_hub, fake_bus
):
    """Same wire values, different text: 10 is 1 °C or 2 °F depending on
    register 21. (Whether the wire values themselves change in °F is unverified
    - see CLAUDE.md.)
    """
    fake_bus[1][21] = 1
    await setup_entry(hass)
    state = hass.states.get("select.hall_switching_differential")
    assert state.state == "2 °F"
    assert state.attributes["options"] == ["1 °F", "2 °F", "4 °F", "6 °F"]


async def test_a_switch_writes_the_documented_polarity(hass, mock_hub, fake_bus):
    await setup_entry(hass)
    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.hot_water_timer"},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.hot_water_timer"},
        blocking=True,
    )
    assert mock_hub == [(2, 32, 0), (2, 32, 1)]


async def test_a_mode_gated_switch_is_unavailable_outside_those_modes(
    hass, mock_hub, fake_bus
):
    """The manual says the Timer's output override applies "in the Hold and
    Advanced mode". Reporting unavailable elsewhere is more honest than
    accepting a write the thermostat ignores.
    """
    entry = await setup_entry(hass)
    assert fake_bus[2][33] == 1  # schedule
    assert hass.states.get("switch.hot_water_output_override").state == "unavailable"

    fake_bus[2][33] = 2  # hold
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.hot_water_output_override").state == "off"


async def test_a_write_is_read_back_not_assumed(hass, mock_hub, fake_bus):
    """The thermostat may clamp a value silently, so the UI must show what it
    actually kept - never what we optimistically sent.

    The fake stat here refuses to go below 10.0 °C on its frost setting, the
    way real firmware enforces its own limits without saying so.
    """
    from unittest.mock import patch

    from custom_components.heatmiser_edge.hub import EdgeHub

    await setup_entry(hass)

    async def clamping_write(self, unit_id, register, value):
        fake_bus[unit_id][register] = max(value, 100)

    with patch.object(EdgeHub, "async_write_register", clamping_write):
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: "number.hall_frost_set_temperature", "value": 7.5},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert fake_bus[1][37] == 100
    assert hass.states.get("number.hall_frost_set_temperature").state == "10.0"


async def test_a_second_write_in_quick_succession_is_still_read_back(
    hass, mock_hub, fake_bus
):
    """Dragging a control is a burst of writes, and every one of them must land.

    `async_request_refresh()` is debounced with a 10 s cooldown: the first call
    runs immediately and the rest are deferred to the end of the timer, so the
    second write here would sit showing the first one's value. That is the
    "it reverts until the next poll" symptom. The read-back is per-unit and
    undebounced instead.
    """
    await setup_entry(hass)

    for value in (7.5, 8.0, 9.0):
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: "number.hall_frost_set_temperature", "value": value},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get("number.hall_frost_set_temperature").state == str(value)


async def test_a_write_reads_back_only_the_thermostat_it_wrote_to(
    hass, mock_hub, fake_bus
):
    """One FC03, not a poll of the whole wire.

    At 9600 baud with the manual's 50 ms gap, re-polling 32 thermostats to show
    a change to one of them is seconds of stale card - and every silent id on
    the bus pays a full timeout on top.
    """
    from unittest.mock import patch

    from custom_components.heatmiser_edge.hub import EdgeHub

    await setup_entry(hass)
    reads: list[int] = []
    real_read_block = EdgeHub.async_read_block

    async def counting_read_block(self, unit_id, start, count, timeout=None):
        reads.append(unit_id)
        return await real_read_block(self, unit_id, start, count)

    with (
        patch.object(EdgeHub, "async_read_block", counting_read_block),
        patch.object(EdgeHub, "async_read_units") as bus_poll,
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: "number.hall_output_delay", "value": 5},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert reads == [1]
    bus_poll.assert_not_called()


async def test_a_write_is_read_back_again_once_the_stat_has_reacted(
    hass, mock_hub, fake_bus, freezer
):
    """The registers that move only *because* of a write need a second look.

    The immediate read-back lands ~50 ms later, before the thermostat has acted:
    the relay at register 2 and the live setpoint at 7 still hold their old
    values. Without the settle read they would keep them until the next
    scheduled poll - the device page lagging the control that changed it.
    """
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.heatmiser_edge.const import SETTLE_PROBES

    await setup_entry(hass)
    assert hass.states.get("binary_sensor.hall_heating").state == "on"

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.hall_output_delay", "value": 5},
        blocking=True,
    )
    await hass.async_block_till_done()

    # The stat reacts only after the immediate read-back has been and gone -
    # and, as on real hardware, not before the first probe either.
    assert hass.states.get("binary_sensor.hall_heating").state == "on"
    for probe in SETTLE_PROBES[:2]:
        freezer.tick(timedelta(seconds=probe))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("binary_sensor.hall_heating").state == "on"

    fake_bus[1][2] = 0
    for probe in SETTLE_PROBES[2:]:
        freezer.tick(timedelta(seconds=probe))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hall_heating").state == "off"


async def test_an_early_change_does_not_cancel_the_rest_of_the_settle(
    hass, mock_hub, fake_bus, freezer
):
    """The stat's registers do not all move at once, so the first change must
    not end the looking.

    Measured on hardware: register 7 caught up 1.6 s after a write while the
    relay at register 2 had not moved yet. A schedule that stopped on the first
    difference published the setpoint and then left the relay stale until the
    next scheduled poll - the original complaint, moved rather than fixed.
    """
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.heatmiser_edge.const import SETTLE_PROBES

    await setup_entry(hass)
    assert hass.states.get("binary_sensor.hall_heating").state == "on"

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.hall_output_delay", "value": 5},
        blocking=True,
    )
    await hass.async_block_till_done()

    async def tick(seconds: float) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    fake_bus[1][3] = 215  # the room moves first, and is picked up
    await tick(SETTLE_PROBES[0] + 0.1)
    assert hass.states.get("sensor.hall_room_temperature").state == "21.5"

    fake_bus[1][2] = 0  # the relay only decides later
    for probe in SETTLE_PROBES[1:]:
        await tick(probe)

    assert hass.states.get("binary_sensor.hall_heating").state == "off"


async def test_a_burst_of_writes_costs_one_settle_read(hass, mock_hub, fake_bus, freezer):
    """Re-armed, not stacked. Dragging a setpoint must not queue one extra FC03
    per step onto a bus that is already the constraint.
    """
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.heatmiser_edge.const import SETTLE_PROBES
    from custom_components.heatmiser_edge.hub import EdgeHub

    await setup_entry(hass)
    reads: list[int] = []
    real_read_block = EdgeHub.async_read_block

    async def counting_read_block(self, unit_id, start, count, timeout=None):
        reads.append(unit_id)
        return await real_read_block(self, unit_id, start, count)

    with patch.object(EdgeHub, "async_read_block", counting_read_block):
        for value in (5, 6, 7):
            await hass.services.async_call(
                "number",
                "set_value",
                {ATTR_ENTITY_ID: "number.hall_output_delay", "value": value},
                blocking=True,
            )
            await hass.async_block_till_done()
        assert len(reads) == 3  # one immediate read-back per write

        for probe in SETTLE_PROBES:
            freezer.tick(timedelta(seconds=probe))
            async_fire_time_changed(hass, dt_util.utcnow())
            await hass.async_block_till_done()

    # One schedule for the burst, not one per write. Nothing on this fake stat
    # reacts to an output-delay write, so the schedule runs to its end - which
    # is the worst case, and still a third of what stacking would cost.
    assert len(reads) == 3 + len(SETTLE_PROBES)


async def test_writes_use_the_single_register_function(hass, mock_hub, fake_bus):
    """Nothing in v1 writes a block. FC16 exists in the hub for the RTC and away
    registers, which must move together, but no entity reaches it yet.
    """
    from unittest.mock import patch

    from custom_components.heatmiser_edge.hub import EdgeHub

    await setup_entry(hass)
    with patch.object(EdgeHub, "async_write_registers") as block_write:
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: "number.hall_output_delay", "value": 5},
            blocking=True,
        )
    block_write.assert_not_called()
    assert mock_hub == [(1, 23, 5)]

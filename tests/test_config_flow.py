"""Setting up a bus.

The scan is the part worth testing hard: it is the only place the register base
and the two thermostat models get decided, and both of those are guesses the
manual cannot confirm.
"""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heatmiser_edge.const import (
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
    DOMAIN,
    FRAMER_SOCKET,
    MODEL_HEAT,
    MODEL_TIMER,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
)
from custom_components.heatmiser_edge.hub import EdgeConnectionError

SCAN_TARGET = "custom_components.heatmiser_edge.config_flow.async_scan_bus"

TCP_INPUT = {
    CONF_HOST: "10.0.0.5",
    CONF_PORT: 502,
    CONF_FRAMER: FRAMER_SOCKET,
    CONF_UNIT_IDS: "1-4",
    CONF_REGISTER_OFFSET: "auto",
}
SERIAL_INPUT = {
    CONF_SERIAL_PORT: "/dev/ttyUSB0",
    CONF_BAUDRATE: 9600,
    CONF_BYTESIZE: 8,
    CONF_PARITY: "N",
    CONF_STOPBITS: 1,
    CONF_UNIT_IDS: "1-4",
    CONF_REGISTER_OFFSET: "auto",
}


def discovered(words_builder, *specs):
    """Build the scan result: (unit_id, model) pairs as DiscoveredUnit objects."""
    from custom_components.heatmiser_edge.config_flow import DiscoveredUnit
    from custom_components.heatmiser_edge.detect import guess_model

    found = []
    for unit_id, model in specs:
        words = (
            words_builder.heat(unit_id)
            if model == MODEL_HEAT
            else words_builder.timer(unit_id)
        )
        found.append(DiscoveredUnit(unit_id, guess_model(words), words))
    return found


async def run_scan(hass, result, found, offset=-1):
    """Drive the progress step to completion and land on `confirm`."""

    async def fake_scan(data, unit_ids):
        return offset, found

    with patch(SCAN_TARGET, fake_scan):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        return await hass.config_entries.flow.async_configure(result["flow_id"])


async def start(hass, transport, user_input):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": transport}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )


async def test_tcp_flow_creates_an_entry(hass, words, mock_hub):
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    found = discovered(words, (1, MODEL_HEAT), (2, MODEL_TIMER))
    result = await run_scan(hass, result, found)
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["count"] == "2"
    assert result["description_placeholders"]["unsure"] == "none"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name_1": "Hall", "model_1": MODEL_HEAT, "name_2": "HW", "model_2": MODEL_TIMER},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_TCP
    # The scan's verdict is stored, so a restart does not re-probe the bus.
    assert result["data"][CONF_REGISTER_OFFSET] == -1
    assert result["data"][CONF_UNITS] == [
        {CONF_UNIT_ID: 1, CONF_MODEL: MODEL_HEAT, "name": "Hall"},
        {CONF_UNIT_ID: 2, CONF_MODEL: MODEL_TIMER, "name": "HW"},
    ]


async def test_serial_flow_creates_an_entry(hass, words, mock_hub):
    result = await start(hass, TRANSPORT_SERIAL, SERIAL_INPUT)
    found = discovered(words, (1, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name_1": "Hall", "model_1": MODEL_HEAT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERIAL_PORT] == "/dev/ttyUSB0"
    assert result["data"][CONF_BAUDRATE] == 9600


async def test_the_model_guess_is_only_a_default(hass, words, mock_hub):
    """The manual has no model register, so the user always gets the last word."""
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    found = discovered(words, (1, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    assert result["data_schema"]({"name_1": "Hall"})["model_1"] == MODEL_HEAT

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name_1": "Hall", "model_1": MODEL_TIMER}
    )
    assert result["data"][CONF_UNITS][0][CONF_MODEL] == MODEL_TIMER


async def test_low_confidence_guesses_are_flagged(hass, words):
    """A thermostat read at the wrong offset is the one way working hardware
    produces a plausible-looking map. It must be pointed out, not accepted.
    """
    from custom_components.heatmiser_edge.config_flow import DiscoveredUnit
    from custom_components.heatmiser_edge.detect import guess_model

    muddled = words.shift(words.heat(1), 1)
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    result = await run_scan(hass, result, [DiscoveredUnit(1, guess_model(muddled), muddled)])
    assert result["description_placeholders"]["unsure"] == "1"


async def test_no_thermostats_aborts_with_something_actionable(hass):
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    result = await run_scan(hass, result, [])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_thermostats_found"


async def test_a_bus_that_will_not_open_aborts(hass):
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)

    async def failing_scan(data, unit_ids):
        raise EdgeConnectionError("no such port")

    with patch(SCAN_TARGET, failing_scan):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_the_same_bus_cannot_be_added_twice(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id="tcp:10.0.0.5:502", data={}
    ).add_to_hass(hass)
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("bad", ["0", "255", "33", "nonsense"])
async def test_impossible_unit_ids_are_rejected(hass, bad):
    """0 disables Modbus on a thermostat and 255 is the radio channel."""
    result = await start(hass, TRANSPORT_TCP, {**TCP_INPUT, CONF_UNIT_IDS: bad})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_UNIT_IDS: "invalid_unit_ids"}


async def test_the_id_list_alone_decides_what_is_swept(hass, words):
    """There is one control, and asking for the whole range is typing it in.

    This replaced a "scan every ID" checkbox that sat next to the id list and
    silently overrode it.
    """
    seen: dict = {}

    async def fake_scan(data, unit_ids):
        seen["ids"] = unit_ids
        return -1, discovered(words, (1, MODEL_HEAT))

    result = await start(hass, TRANSPORT_TCP, {**TCP_INPUT, CONF_UNIT_IDS: "1-32"})
    with patch(SCAN_TARGET, fake_scan):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        await hass.config_entries.flow.async_configure(result["flow_id"])
    assert seen["ids"] == list(range(1, 33))


@pytest.mark.parametrize(
    ("transport", "required"),
    [
        (TRANSPORT_TCP, {CONF_HOST: "10.0.0.5"}),
        (TRANSPORT_SERIAL, {CONF_SERIAL_PORT: "/dev/ttyUSB0"}),
    ],
)
async def test_the_default_id_list_is_the_whole_range(hass, transport, required):
    """A first-time user should not have to know their thermostats' ids."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": transport}
    )
    assert result["data_schema"](required)[CONF_UNIT_IDS] == "1-32"


async def test_an_explicit_offset_is_honoured(hass, words):
    """If a user knows their firmware, detection should not second-guess them."""
    from custom_components.heatmiser_edge.config_flow import build_scan_hub

    hub = build_scan_hub({**TCP_INPUT, CONF_TRANSPORT: TRANSPORT_TCP, CONF_REGISTER_OFFSET: "0"})
    assert hub.register_offset == 0
    hub = build_scan_hub({**TCP_INPUT, CONF_TRANSPORT: TRANSPORT_TCP})
    assert hub.register_offset is None


# ----------------------------------------------------------------------
# Options and reconfiguration
# ----------------------------------------------------------------------


async def test_options_round_trip(hass, mock_hub):
    from tests.test_coordinator import setup_entry

    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 120,
            CONF_TIMEOUT: 2.0,
            CONF_CONTROLS: False,
            CONF_REGISTER_OFFSET: "0",
            "name_1": "Hallway",
            "model_1": MODEL_HEAT,
            "name_2": "Hot water",
            "model_2": MODEL_TIMER,
            "name_3": "Study",
            "model_3": MODEL_HEAT,
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 120
    assert entry.options[CONF_CONTROLS] is False
    assert entry.options[CONF_REGISTER_OFFSET] == 0
    assert entry.options[CONF_UNITS][0]["name"] == "Hallway"
    # Options changing reloads the entry, so the rename takes effect at once.
    assert entry.runtime_data.units[0].name == "Hallway"


async def test_options_can_return_the_offset_to_automatic(hass, mock_hub):
    from tests.test_coordinator import setup_entry

    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            CONF_TIMEOUT: 1.0,
            CONF_CONTROLS: True,
            CONF_REGISTER_OFFSET: "auto",
            "name_1": "Hall",
            "model_1": MODEL_HEAT,
            "name_2": "Hot water",
            "model_2": MODEL_TIMER,
            "name_3": "Study",
            "model_3": MODEL_HEAT,
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_REGISTER_OFFSET] is None


async def test_reconfigure_rescans_and_keeps_existing_names(hass, mock_hub, words):
    """A re-scan must not rename the thermostats the user already named."""
    from tests.test_coordinator import setup_entry

    entry = await setup_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_UNIT_IDS: "1-4", CONF_REGISTER_OFFSET: "auto"},
    )
    found = discovered(words, (1, MODEL_HEAT), (2, MODEL_TIMER), (4, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    assert result["step_id"] == "confirm"
    # Unit 1 keeps "Hall" from the original setup; unit 4 is new, so it falls
    # back to the guess.
    defaults = result["data_schema"]({})
    assert defaults["name_1"] == "Hall"
    assert defaults["name_4"] == "EDGE Heat 4"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name_1": "Hall",
            "model_1": MODEL_HEAT,
            "name_2": "Hot water",
            "model_2": MODEL_TIMER,
            "name_4": "Landing",
            "model_4": MODEL_HEAT,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # The thermostat that has gone is dropped, and the new one is picked up.
    assert [u[CONF_UNIT_ID] for u in entry.data[CONF_UNITS]] == [1, 2, 4]


async def test_a_legacy_scan_all_entry_still_offers_the_whole_range(
    hass, mock_hub, words
):
    """An entry set up with the old checkbox stored an id list it never used.

    Prefilling that list would silently narrow the re-scan and lose whichever
    thermostats sit above it.
    """
    from tests.test_coordinator import setup_entry

    entry = await setup_entry(hass, scan_all=True, unit_ids="1-8")
    result = await entry.start_reconfigure_flow(hass)
    assert result["data_schema"]({})[CONF_UNIT_IDS] == "1-32"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIT_IDS: "1-32", CONF_REGISTER_OFFSET: "auto"}
    )
    result = await run_scan(hass, result, discovered(words, (1, MODEL_HEAT)))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name_1": "Hall", "model_1": MODEL_HEAT}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    # The flag is gone for good, so nothing can read it a second time.
    assert "scan_all" not in entry.data
    assert entry.data[CONF_UNIT_IDS] == "1-32"

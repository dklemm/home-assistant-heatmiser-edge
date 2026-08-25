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
    DEFAULT_REGISTER_OFFSET,
    DOMAIN,
    FRAMER_SOCKET,
    MODEL_HEAT,
    MODEL_TIMER,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
)
from custom_components.heatmiser_edge import config_flow
from custom_components.heatmiser_edge.hub import EdgeConnectionError, EdgeHub


TCP_INPUT = {
    CONF_HOST: "10.0.0.5",
    CONF_PORT: 502,
    CONF_FRAMER: FRAMER_SOCKET,
    CONF_UNIT_IDS: "1-4",
}
SERIAL_INPUT = {
    CONF_SERIAL_PORT: "/dev/ttyUSB0",
    CONF_BAUDRATE: 9600,
    CONF_BYTESIZE: 8,
    CONF_PARITY: "N",
    CONF_STOPBITS: 1,
    CONF_UNIT_IDS: "1-4",
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


def naming(*specs):
    """Each thermostat's name and model, as the form's sections submit them."""
    return {
        f"unit_{unit_id}": {"name": name, CONF_MODEL: model}
        for unit_id, name, model in specs
    }


def patch_bus(found, *, probed=None, connect_error=None):
    """A bus holding exactly `found`, patched at the hub.

    The flow sweeps one id per progress step now, so there is no single scan
    function left to stub - which means these tests drive the real sweep.
    """
    bus = {f.unit_id: f.words for f in found}

    async def fake_connect(self):
        if connect_error is not None:
            raise connect_error

    async def fake_close(self):
        return None

    async def fake_probe(self, unit_id):
        if probed is not None:
            probed.append(unit_id)
        words = bus.get(unit_id)
        return None if words is None else {n: words[n] for n in range(30, 35)}

    async def fake_read_block(self, unit_id, start, count):
        words = bus.get(unit_id)
        if words is None:
            return None
        return {n: words.get(n, 0) for n in range(start, start + count)}

    return (
        patch.object(EdgeHub, "async_connect", fake_connect),
        patch.object(EdgeHub, "async_close", fake_close),
        patch.object(EdgeHub, "async_probe_unit", fake_probe),
        patch.object(EdgeHub, "async_read_block", fake_read_block),
    )


async def drive_scan(hass, result):
    """Step the progress dialog until the sweep is done."""
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def run_scan(hass, result, found, **kwargs):
    """Drive the whole sweep and land on `confirm`."""
    patches = patch_bus(found, **kwargs)
    with patches[0], patches[1], patches[2], patches[3]:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        return await drive_scan(hass, result)


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
        naming((1, "Hall", MODEL_HEAT), (2, "HW", MODEL_TIMER)),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_TCP
    # Setup never asks for the register base; it is an option with a default.
    assert CONF_REGISTER_OFFSET not in result["data"]
    assert result["data"][CONF_UNITS] == [
        {CONF_UNIT_ID: 1, CONF_MODEL: MODEL_HEAT, "name": "Hall"},
        {CONF_UNIT_ID: 2, CONF_MODEL: MODEL_TIMER, "name": "HW"},
    ]


async def test_serial_flow_creates_an_entry(hass, words, mock_hub):
    result = await start(hass, TRANSPORT_SERIAL, SERIAL_INPUT)
    found = discovered(words, (1, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], naming((1, "Hall", MODEL_HEAT))
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERIAL_PORT] == "/dev/ttyUSB0"
    assert result["data"][CONF_BAUDRATE] == 9600


async def test_the_model_guess_is_only_a_default(hass, words, mock_hub):
    """The manual has no model register, so the user always gets the last word."""
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    found = discovered(words, (1, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    assert result["data_schema"]({"unit_1": {}})["unit_1"][CONF_MODEL] == MODEL_HEAT

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], naming((1, "Hall", MODEL_TIMER))
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
    """A dead bus stops the sweep at the first id, not after all 32 timeouts."""
    result = await start(hass, TRANSPORT_TCP, TCP_INPUT)
    probed: list[int] = []
    result = await run_scan(
        hass,
        result,
        [],
        probed=probed,
        connect_error=EdgeConnectionError("no such port"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert probed == []


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


async def test_the_progress_dialog_names_the_unit_and_the_running_total(hass, words):
    """18 seconds of "scanning" says nothing; this says where it has got to.

    Recorded off `async_show_progress` itself, because that is exactly what the
    frontend is handed each time a unit's probe finishes and the flow re-enters
    the step.
    """
    found = discovered(words, (2, MODEL_HEAT))
    result = await start(hass, TRANSPORT_TCP, {**TCP_INPUT, CONF_UNIT_IDS: "1-4"})

    seen = []
    real = config_flow.EdgeConfigFlow.async_show_progress

    def spy(self, **kwargs):
        seen.append(kwargs["description_placeholders"])
        return real(self, **kwargs)

    patches = patch_bus(found)
    # Zero the interval so every id is its own batch, and every id therefore its
    # own render - the mock answers instantly, so a real interval would sweep the
    # whole bus inside one task and there would be nothing to observe.
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        config_flow, "SCAN_PROGRESS_INTERVAL", 0
    ), patch.object(config_flow.EdgeConfigFlow, "async_show_progress", spy):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await drive_scan(hass, result)

    assert [p["unit"] for p in seen] == ["1", "2", "3", "4"]
    # Unit 2 answers, so the count moves from the step that follows its probe.
    assert [p["found"] for p in seen] == ["0", "0", "1", "1"]
    assert result["step_id"] == "confirm"


async def test_the_dialog_is_paced_by_a_clock_not_by_unit_ids(hass, words):
    """Per-id re-renders made the spinner stutter, because ids are not evenly
    timed: a stat that answers takes ~150 ms and an absent one pays a full
    timeout. A batch is a second's worth of ids, however many that is.
    """
    renders = []
    real = config_flow.EdgeConfigFlow.async_show_progress

    def spy(self, **kwargs):
        renders.append(kwargs["description_placeholders"]["unit"])
        return real(self, **kwargs)

    result = await start(hass, TRANSPORT_TCP, {**TCP_INPUT, CONF_UNIT_IDS: "1-32"})
    patches = patch_bus(discovered(words, (1, MODEL_HEAT)))
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        config_flow.EdgeConfigFlow, "async_show_progress", spy
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await drive_scan(hass, result)

    # The mock answers instantly, so a whole 32-id sweep fits in one interval.
    assert renders == ["1"]
    assert result["step_id"] == "confirm"


async def test_the_id_list_alone_decides_what_is_swept(hass, words):
    """There is one control, and asking for the whole range is typing it in.

    This replaced a "scan every ID" checkbox that sat next to the id list and
    silently overrode it.
    """
    probed: list[int] = []
    result = await start(hass, TRANSPORT_TCP, {**TCP_INPUT, CONF_UNIT_IDS: "1-32"})
    await run_scan(hass, result, discovered(words, (1, MODEL_HEAT)), probed=probed)
    assert probed == list(range(1, 33))


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


async def test_the_register_base_defaults_to_the_standard_convention(hass):
    """-1 unless a user says otherwise; there is no search to second-guess them."""
    from custom_components.heatmiser_edge.config_flow import build_scan_hub

    hub = build_scan_hub({**TCP_INPUT, CONF_TRANSPORT: TRANSPORT_TCP})
    assert hub.register_offset == DEFAULT_REGISTER_OFFSET

    hub = build_scan_hub(
        {**TCP_INPUT, CONF_TRANSPORT: TRANSPORT_TCP, CONF_REGISTER_OFFSET: "0"}
    )
    assert hub.register_offset == 0


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
            **naming(
                (1, "Hallway", MODEL_HEAT),
                (2, "Hot water", MODEL_TIMER),
                (3, "Study", MODEL_HEAT),
            ),
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 120
    assert entry.options[CONF_CONTROLS] is False
    assert entry.options[CONF_REGISTER_OFFSET] == 0
    assert entry.options[CONF_UNITS][0]["name"] == "Hallway"
    # Options changing reloads the entry, so the rename takes effect at once.
    assert entry.runtime_data.units[0].name == "Hallway"


async def test_a_rescan_honours_an_offset_set_in_options(hass, mock_hub):
    """The base is an option, so a re-scan must read it from there.

    Setup does not ask for it, so it never reaches `entry.data` - and a re-scan
    at the wrong base finds no thermostats and reads as an empty bus.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="tcp:10.0.0.5:502",
        data={
            CONF_TRANSPORT: TRANSPORT_TCP,
            CONF_HOST: "10.0.0.5",
            CONF_PORT: 502,
            CONF_UNIT_IDS: "1-4",
        },
        options={CONF_REGISTER_OFFSET: 0},
    )
    entry.add_to_hass(hass)
    seen = {}
    real_build = config_flow.build_scan_hub

    def spy(data):
        hub = real_build(data)
        seen["offset"] = hub.register_offset
        return hub

    result = await entry.start_reconfigure_flow(hass)
    patches = patch_bus([])
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        config_flow, "build_scan_hub", spy
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_UNIT_IDS: "1-4"}
        )
        await drive_scan(hass, result)

    assert seen["offset"] == 0


async def test_reconfigure_rescans_and_keeps_existing_names(hass, mock_hub, words):
    """A re-scan must not rename the thermostats the user already named."""
    from tests.test_coordinator import setup_entry

    entry = await setup_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_UNIT_IDS: "1-4"},
    )
    found = discovered(words, (1, MODEL_HEAT), (2, MODEL_TIMER), (4, MODEL_HEAT))
    result = await run_scan(hass, result, found)
    assert result["step_id"] == "confirm"
    # Unit 1 keeps "Hall" from the original setup; unit 4 is new, so it falls
    # back to the guess.
    defaults = result["data_schema"]({"unit_1": {}, "unit_2": {}, "unit_4": {}})
    assert defaults["unit_1"]["name"] == "Hall"
    assert defaults["unit_4"]["name"] == "EDGE Heat 4"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        naming(
            (1, "Hall", MODEL_HEAT),
            (2, "Hot water", MODEL_TIMER),
            (4, "Landing", MODEL_HEAT),
        ),
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
        result["flow_id"], {CONF_UNIT_IDS: "1-32"}
    )
    result = await run_scan(hass, result, discovered(words, (1, MODEL_HEAT)))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], naming((1, "Hall", MODEL_HEAT))
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    # The flag is gone for good, so nothing can read it a second time.
    assert "scan_all" not in entry.data
    assert entry.data[CONF_UNIT_IDS] == "1-32"

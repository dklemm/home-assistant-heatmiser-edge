"""The heuristic that answers what the manual doesn't: Heat or Timer.

Pure, so these tests need neither a bus nor Home Assistant. They are also the
tests to update first when real hardware disagrees: `dev/edge_modbus_test.py
scan` prints the same scores, so a field word map drops straight in here.
"""

import pytest

from custom_components.heatmiser_edge.const import MODEL_HEAT, MODEL_TIMER
from custom_components.heatmiser_edge.detect import (
    MIN_MODEL_CONFIDENCE,
    guess_model,
    parse_id_list,
)


def test_heat_is_recognised(words):
    guess = guess_model(words.heat())
    assert guess.model == MODEL_HEAT
    assert guess.confident
    assert guess.heat > guess.timer


def test_timer_is_recognised(words):
    guess = guess_model(words.timer())
    assert guess.model == MODEL_TIMER
    assert guess.confident
    assert guess.timer > guess.heat


def test_an_idle_heat_stat_is_still_recognised(words):
    """Switched off in midsummer: no relay, no setpoint, nothing running.

    The heuristic leans on stored settings (frost, floor limit, differential)
    precisely so this case still works.
    """
    idle = words.heat(1, {2: 0, 7: 0, 32: 0, 33: 0, 10: 0, 11: 0})
    guess = guess_model(idle)
    assert guess.model == MODEL_HEAT
    assert guess.confident


def test_a_fahrenheit_heat_stat_is_still_recognised(words):
    """Register 21 changes every temperature register, scoring bands included.

    Caught by `dev/fake_edge_server.py --fahrenheit 1`: with °C-only bands a
    perfectly ordinary °F stat scored 3, below the confidence bar, because its
    frost setpoint reads 540 rather than 120.
    """
    imperial = words.heat(
        1, {21: 1, 3: 690, 7: 700, 15: 700, 16: 690, 26: 820, 34: 700, 35: 700, 37: 540}
    )
    guess = guess_model(imperial)
    assert guess.model == MODEL_HEAT
    assert guess.confident


@pytest.mark.parametrize("shift", [1, -1])
def test_a_mis_offset_map_is_not_confidently_classified(words, shift):
    """A real stat read at the wrong offset is the only way to get a
    plausible-looking map out of working hardware. It must not be trusted.
    """
    guess = guess_model(words.shift(words.heat(), shift))
    assert guess.confidence < MIN_MODEL_CONFIDENCE
    assert not guess.confident


def test_correct_maps_clear_the_bar_with_room_to_spare(words):
    """Headroom check: the threshold separates the two cases, not by a hair."""
    for mapping in (words.heat(), words.timer()):
        assert guess_model(mapping).confidence >= MIN_MODEL_CONFIDENCE * 2


def test_parse_id_list_accepts_ranges_and_singles():
    assert parse_id_list("1-4,7") == [1, 2, 3, 4, 7]
    assert parse_id_list("3") == [3]
    assert parse_id_list(" 1 - 3 , 2 ") == [1, 2, 3]  # overlapping, deduplicated


@pytest.mark.parametrize("text", ["0", "255", "33", "0-4", "5-1"])
def test_parse_id_list_rejects_non_thermostat_ids(text):
    """0 disables Modbus on the stat and 255 is the radio channel."""
    with pytest.raises(ValueError):
        parse_id_list(text)


def test_parse_id_list_rejects_nothing_at_all():
    with pytest.raises(ValueError):
        parse_id_list(" , ")

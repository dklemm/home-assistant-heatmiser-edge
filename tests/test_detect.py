"""The two heuristics that answer what the manual doesn't.

Both are pure, so these tests need neither a bus nor Home Assistant. They are
also the tests to update first when real hardware disagrees: `dev/edge_modbus_test.py
scan` prints the same scores, so a field word map drops straight in here.
"""

import pytest

from custom_components.heatmiser_edge.const import MODEL_HEAT, MODEL_TIMER
from custom_components.heatmiser_edge.detect import (
    MIN_MODEL_CONFIDENCE,
    OFFSET_ONE_BASED,
    OFFSET_ZERO_BASED,
    guess_model,
    parse_id_list,
    resolve_offset,
    score_offset,
)


def _by_offset(true_words, true_offset, unit_id):
    """What each candidate offset would read, given the firmware's real one.

    Reading at candidate C when the truth is T shifts every register by C - T.
    """
    return {
        candidate: {
            n: true_words.get(n + candidate - true_offset, 0) for n in range(30, 35)
        }
        for candidate in (OFFSET_ZERO_BASED, OFFSET_ONE_BASED)
    }


def test_offset_detected_on_zero_based_firmware(words):
    """Unit 7 is decisive: nothing else in the 30-34 window can hold a 7."""
    probes = _by_offset(words.heat(7), OFFSET_ZERO_BASED, 7)
    assert score_offset(probes, 7) == OFFSET_ZERO_BASED


def test_offset_detected_on_one_based_firmware(words):
    probes = _by_offset(words.heat(7), OFFSET_ONE_BASED, 7)
    assert score_offset(probes, 7) == OFFSET_ONE_BASED


def test_the_corroboration_checks_rescue_the_obvious_near_miss(words):
    """Register 31 alone is not enough, and the extra checks are what save it.

    On unit 1 the wrong offset also lands a 1 in the "communications id" slot -
    it is really register 32, the thermostat being on. What breaks the tie is
    register 33: at the wrong offset that slot holds register 34, the override
    setpoint, which on a stat set to 21.0 °C reads 210 and is not a mode.
    """
    probes = _by_offset(words.heat(1, {30: 1}), OFFSET_ZERO_BASED, 1)
    assert probes[OFFSET_ONE_BASED][31] == 1  # looks like the id, but isn't
    assert score_offset(probes, 1) == OFFSET_ZERO_BASED


def test_a_factory_fresh_unit_one_is_genuinely_ambiguous(words):
    """The case that really can't be decided from one thermostat.

    Unit 1, switched on, in schedule mode, and never manually overridden - so
    register 34 reads 0. Every corroboration check then passes at *both*
    offsets, because 0 is a valid mode as well as an unset setpoint. Saying
    "don't know" here is what lets `resolve_offset` settle it from another unit
    instead of committing to a coin flip.
    """
    fresh = words.heat(1, {32: 1, 33: 1, 34: 0})
    probes = _by_offset(fresh, OFFSET_ZERO_BASED, 1)
    assert score_offset(probes, 1) is None


def test_one_decisive_unit_settles_the_bus(words):
    votes = {1: None, 2: None, 7: OFFSET_ZERO_BASED}
    assert resolve_offset(votes) == (OFFSET_ZERO_BASED, True)


def test_all_ambiguous_falls_back_to_the_standard_convention():
    """No evidence: 0-based is overwhelmingly the more likely, and the config
    flow exposes an override. The False says "we guessed", so callers can warn.
    """
    assert resolve_offset({1: None, 2: None}) == (OFFSET_ZERO_BASED, False)


def test_majority_wins_when_units_disagree():
    votes = {1: OFFSET_ONE_BASED, 2: OFFSET_ZERO_BASED, 3: OFFSET_ZERO_BASED}
    offset, decisive = resolve_offset(votes)
    assert (offset, decisive) == (OFFSET_ZERO_BASED, True)


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

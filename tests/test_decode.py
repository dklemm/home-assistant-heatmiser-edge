"""Pure decode/encode. No bus, no Home Assistant."""

from datetime import datetime

import pytest

from custom_components.heatmiser_edge.const import MODEL_HEAT, MODEL_TIMER
from custom_components.heatmiser_edge.decode import (
    decode_hm,
    decode_md,
    decode_optional_temperature,
    decode_temperature,
    decode_value,
    decode_year,
    encode_hm,
    encode_md,
    encode_rtc,
    encode_temperature,
    encode_value,
    hm_to_minutes,
    minutes_to_hm,
    to_signed16,
)
from custom_components.heatmiser_edge.registers import reg


def test_temperature_round_trip():
    assert decode_temperature(205) == 20.5
    assert encode_temperature(20.5) == 205
    assert decode_temperature(encode_temperature(21.0)) == 21.0


def test_sub_zero_is_twos_complement():
    """The manual documents "0~0xffff" and says nothing about negatives.

    Read unsigned, 0xFFCE would be 6550.2 °C - not a temperature - so two's
    complement is the only sane reading.
    """
    assert decode_temperature(0xFFCE) == -5.0  # -50 wire units
    assert to_signed16(0xFFFF) == -1
    assert to_signed16(0x7FFF) == 32767
    assert encode_temperature(-5.0) == 0xFFCE


def test_non_values_decode_to_unknown():
    """0xFFFF is the RTC "synced" marker and the classic all-ones artefact."""
    assert decode_temperature(0xFFFF) is None
    assert decode_temperature(0x8000) is None
    assert decode_temperature(None) is None


def test_implausible_readings_decode_to_unknown():
    """Better unknown than a fabricated 3276.7 °C someone might automate on."""
    assert decode_temperature(30000) is None  # 3000.0 °C
    assert decode_temperature(0x8001) is None  # -3276.7 °C
    assert decode_temperature(1500) == 150.0  # the top of the band is still a value


def test_fahrenheit_band_differs_from_celsius():
    assert decode_temperature(2000, fahrenheit=False) is None  # 200.0 °C: implausible
    assert decode_temperature(2000, fahrenheit=True) == 200.0  # 200.0 °F: a hot floor


def test_absent_probes_read_unknown_but_a_cold_room_reads_zero():
    """An unfitted floor probe reads 0, which is not 0.0 °C."""
    assert decode_optional_temperature(4, 0) is None
    assert decode_optional_temperature(5, 0) is None
    assert decode_optional_temperature(3, 0) == 0.0  # register 3 is the built-in sensor
    assert decode_optional_temperature(4, 215) == 21.5


def test_packed_hour_minute():
    assert decode_hm(0x031E) == (3, 30)
    assert encode_hm(3, 30) == 0x031E
    assert hm_to_minutes(0x031E) == 210
    assert minutes_to_hm(210) == 0x031E
    assert minutes_to_hm(0) == 0


def test_packed_hour_minute_rejects_impossible_values():
    assert decode_hm(0x003C) is None  # 60 minutes
    assert decode_hm(0x6400) is None  # 100 hours, above the manual's 99
    assert decode_hm(0xFFFF) is None


def test_minutes_to_hm_caps_at_the_manuals_maximum():
    """99h59m is the documented ceiling; a larger request clamps, not wraps."""
    assert minutes_to_hm(99 * 60 + 59) == encode_hm(99, 59)
    assert minutes_to_hm(500 * 60) == encode_hm(99, 0)
    assert minutes_to_hm(-5) == 0


def test_packed_month_day():
    assert decode_md(0x0C19) == (12, 25)
    assert decode_md(0x0000) is None  # month 0
    assert decode_md(0x0D01) is None  # month 13
    assert decode_md(0x0C40) is None  # day 64


def test_year_band():
    assert decode_year(2026) == 2026
    assert decode_year(1999) is None
    assert decode_year(0xFFFF) is None


def test_decode_value_dispatches_on_kind():
    assert decode_value(reg(MODEL_HEAT, 3), 205) == 20.5
    assert decode_value(reg(MODEL_HEAT, 2), 1) is True
    assert decode_value(reg(MODEL_HEAT, 2), 0) is False
    assert decode_value(reg(MODEL_HEAT, 1), 42) == 42
    assert decode_value(reg(MODEL_HEAT, 10), 3) == 3  # enum stays raw; labels are HA's
    assert decode_value(reg(MODEL_HEAT, 38), 0x031E) == 210
    assert decode_value(reg(MODEL_HEAT, 39), 0x0917) == "09:23"
    assert decode_value(reg(MODEL_HEAT, 40), 0x0C19) == "12-25"
    assert decode_value(reg(MODEL_HEAT, 41), 2026) == 2026
    assert decode_value(reg(MODEL_HEAT, 3), None) is None


def test_the_same_register_decodes_differently_per_model():
    """Register 3 is a room temperature on Heat and an on/off flag on Timer."""
    assert decode_value(reg(MODEL_HEAT, 3), 205) == 20.5
    assert decode_value(reg(MODEL_TIMER, 3), 1) is True


def test_encode_value_round_trips_the_writable_kinds():
    assert encode_value(reg(MODEL_HEAT, 34), 21.5) == 215
    assert encode_value(reg(MODEL_HEAT, 38), 210) == 0x031E
    assert encode_value(reg(MODEL_HEAT, 23), 7) == 7
    assert encode_value(reg(MODEL_HEAT, 33), 2) == 2
    assert encode_value(reg(MODEL_TIMER, 32), 1) == 1


def test_encode_value_refuses_read_only_registers():
    with pytest.raises(ValueError, match="read-only"):
        encode_value(reg(MODEL_HEAT, 7), 21.0)


def test_encode_value_refuses_the_kinds_v1_does_not_write():
    """Away and RTC need a contiguous FC16 block, not a register at a time."""
    for number in (39, 40, 41):
        with pytest.raises(ValueError, match="not writable in v1"):
            encode_value(reg(MODEL_HEAT, number), 1)


def test_encode_rtc_packs_the_manuals_four_registers():
    """Year, month+day, hour+minute, second - in that order, as one block."""
    words = encode_rtc(datetime(2026, 8, 12, 14, 30, 45))
    assert words == [2026, 0x080C, 0x0E1E, 45]
    # The packing is the one `decode_md`/`decode_hm` read back.
    assert decode_md(words[1]) == (8, 12)
    assert decode_hm(words[2]) == (14, 30)
    assert encode_md(8, 12) == words[1]


def test_encode_rtc_refuses_a_year_the_manual_does_not_allow():
    """2000-5000 is the register's documented range; below it the stat would
    take a value we cannot predict rather than reject one we can.
    """
    for year in (1999, 5001):
        with pytest.raises(ValueError, match="outside the manual"):
            encode_rtc(datetime(year, 1, 1, 0, 0, 0))

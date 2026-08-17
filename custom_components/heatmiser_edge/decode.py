"""Pure conversion between raw Modbus words and engineering values.

No I/O, no Home Assistant imports — so the entities, the config flow, the field
CLI in `dev/` and the tests all share exactly one implementation of "what does
this word mean".

Two things the manual does not tell us, and how we handle them:

**Signedness.** Every temperature is documented as "0~0xffff, Value =
Temperature x 10" with no word about sub-zero readings. Read as *unsigned*,
0x8000-0xFFFF would be 3276.8-6553.5 °C, which is not a temperature a
thermostat can measure, so two's complement is strictly the better reading and
is what every other Modbus thermostat does. We then reject anything outside a
plausibility band, which turns "this register holds something we don't
understand" into `unknown` rather than a number someone might automate on.
Confirming the real sub-zero encoding on hardware is an open item in CLAUDE.md.

**Absent probes.** An unfitted floor or remote sensor reads 0, which would
otherwise surface as a confident 0.0 °C. Those two registers report unknown at
zero instead; register 3 (built-in) does not, because 0.0 °C there is a real if
chilly reading.
"""

from __future__ import annotations

from datetime import datetime

from .registers import (
    KIND_BOOL,
    KIND_DURATION_HM,
    KIND_ENUM,
    KIND_PACKED_HM,
    KIND_PACKED_MD,
    KIND_RAW,
    KIND_TEMP,
    KIND_YEAR,
    Reg,
)

# 0xFFFF is the RTC registers' documented "synced" marker and the classic
# all-ones bus artefact; 0x8000 is the two's-complement floor (-3276.8 °C).
# Neither is ever a reading.
RAW_INVALID = frozenset({0x8000, 0xFFFF})

# Plausibility bands in wire units (value x 10). The low end allows a remote air
# sensor mounted outdoors in a hard frost; the high end allows a shorted floor
# probe to read hot rather than silently vanish.
PLAUSIBLE_C = (-400, 1500)  # -40.0 .. +150.0 °C
PLAUSIBLE_F = (-400, 3020)  # -40.0 .. +302.0 °F

# Registers whose zero means "no probe fitted", not "zero degrees".
ABSENT_WHEN_ZERO = frozenset({4, 5})

# The manual's range for every year register.
MIN_YEAR, MAX_YEAR = 2000, 5000


def to_signed16(raw: int) -> int:
    """Interpret a 16-bit word as two's complement."""
    return raw - 0x10000 if raw >= 0x8000 else raw


def decode_temperature(raw: int | None, fahrenheit: bool = False) -> float | None:
    """A temperature register as degrees, or None when it isn't a temperature."""
    if raw is None or raw in RAW_INVALID:
        return None
    value = to_signed16(raw)
    low, high = PLAUSIBLE_F if fahrenheit else PLAUSIBLE_C
    if not low <= value <= high:
        return None
    return round(value / 10, 1)


def decode_optional_temperature(
    number: int, raw: int | None, fahrenheit: bool = False
) -> float | None:
    """As `decode_temperature`, but zero means "absent" on the probe registers."""
    if number in ABSENT_WHEN_ZERO and raw == 0:
        return None
    return decode_temperature(raw, fahrenheit)


def encode_temperature(value: float) -> int:
    """Degrees to a wire word, in whatever unit the stat is displaying."""
    return round(value * 10) & 0xFFFF


def decode_hm(raw: int | None) -> tuple[int, int] | None:
    """A high-byte-hours / low-byte-minutes register as (hours, minutes)."""
    if raw is None or raw in RAW_INVALID:
        return None
    hours, minutes = raw >> 8, raw & 0xFF
    if hours > 99 or minutes > 59:
        return None
    return hours, minutes


def encode_hm(hours: int, minutes: int) -> int:
    return ((hours & 0xFF) << 8) | (minutes & 0xFF)


def hm_to_minutes(raw: int | None) -> int | None:
    """A packed hour/minute register as a single total in minutes."""
    parts = decode_hm(raw)
    return None if parts is None else parts[0] * 60 + parts[1]


def minutes_to_hm(total: int) -> int:
    """Total minutes back to the packed register. Hours cap at the manual's 99."""
    hours, minutes = divmod(max(0, int(total)), 60)
    return encode_hm(min(hours, 99), minutes)


def decode_md(raw: int | None) -> tuple[int, int] | None:
    """A high-byte-month / low-byte-day register as (month, day)."""
    if raw is None or raw in RAW_INVALID:
        return None
    month, day = raw >> 8, raw & 0xFF
    if not 1 <= month <= 12 or day > 31:
        return None
    return month, day


def encode_md(month: int, day: int) -> int:
    """A month/day pair as the packed register `decode_md` reads."""
    return ((month & 0xFF) << 8) | (day & 0xFF)


def encode_rtc(when: datetime) -> list[int]:
    """A timestamp as the four RTC words (manual 47-50), ready for one FC16.

    Written as a block on purpose: a register at a time the stat would see the
    year, then the month and day, then the hour and minute as three separate
    partial timestamps, and the manual says it syncs as soon as it likes what it
    has. `when` is taken as wall-clock time — the thermostat has no notion of a
    timezone, and its own DST setting is register 30.
    """
    if not MIN_YEAR <= when.year <= MAX_YEAR:
        raise ValueError(f"year {when.year} is outside the manual's {MIN_YEAR}-{MAX_YEAR}")
    return [
        when.year,
        encode_md(when.month, when.day),
        encode_hm(when.hour, when.minute),
        when.second,
    ]


def decode_year(raw: int | None) -> int | None:
    if raw is None or not MIN_YEAR <= raw <= MAX_YEAR:
        return None
    return raw


def decode_value(
    reg: Reg, raw: int | None, fahrenheit: bool = False
) -> float | int | bool | str | None:
    """The presentable value of one register, or None for unknown."""
    if raw is None:
        return None
    if reg.kind == KIND_TEMP:
        return decode_optional_temperature(reg.number, raw, fahrenheit)
    if reg.kind == KIND_BOOL:
        return bool(raw)
    if reg.kind == KIND_DURATION_HM:
        return hm_to_minutes(raw)
    if reg.kind == KIND_PACKED_HM:
        parts = decode_hm(raw)
        return None if parts is None else f"{parts[0]:02d}:{parts[1]:02d}"
    if reg.kind == KIND_PACKED_MD:
        parts = decode_md(raw)
        return None if parts is None else f"{parts[0]:02d}-{parts[1]:02d}"
    if reg.kind == KIND_YEAR:
        return decode_year(raw)
    if reg.kind in (KIND_RAW, KIND_ENUM):
        return raw
    raise ValueError(f"unknown register kind {reg.kind!r} on register {reg.number}")


def encode_value(reg: Reg, value: float) -> int:
    """An entity's value back to a wire word.

    Only the kinds an *entity* writes are supported. The away and RTC registers
    are deliberately absent: setting either coherently means writing a
    contiguous block in one FC16, not one register at a time. The RTC has
    `encode_rtc` and the `set_time` action for that; away is still to come.
    """
    if reg.access != "RW":
        raise ValueError(f"register {reg.number} ({reg.key}) is read-only")
    if reg.kind == KIND_TEMP:
        return encode_temperature(value)
    if reg.kind == KIND_DURATION_HM:
        return minutes_to_hm(int(value))
    if reg.kind in (KIND_RAW, KIND_ENUM, KIND_BOOL):
        # Boolean registers pass through unmassaged: SwitchSpec's on/off are the
        # raw wire values the manual documents, and coercing them through bool()
        # would silently rewrite a switch whose "on" is 0.
        return int(value) & 0xFFFF
    raise ValueError(f"register {reg.number} ({reg.key}) is not writable in v1")

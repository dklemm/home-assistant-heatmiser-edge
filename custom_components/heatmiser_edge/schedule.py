"""The weekly program: words to a readable grid, and a grid back to writes.

Pure, like `decode.py` and `detect.py` — no I/O and no Home Assistant imports,
so the actions, the tests and the field CLI share one understanding of the grid.
`registers.SCHEDULES` owns its *shape*; this module owns its *meaning*.

**Why the program is not entities.** Heat is 6 periods x 7 days x 3 meaningful
fields — 126 entities per thermostat, 4032 on a full bus — and each edit would
be its own FC06 with no way to make a period's hour, minute and temperature move
together. It is read on demand and written in bulk instead.

**Hour 24 means the period is unused.** The manual's Note column says so for
every hour register: "The current schedule is invalid when the hour = 24". It is
the only "off" a period has, and it is why a time of `None` here is a value and
not a missing reading.

**Writes are one FC16 per period, not per day.** A Heat day is 24 contiguous
registers, but six of them are Reserved, and writing zeros into undocumented
registers on a live heating system is the thing this integration refuses
everywhere else (see registers 42, 43 and 44 in CLAUDE.md). Hour, minute and
set temperature *are* contiguous, so a 3-register FC16 covers a period while
touching only documented addresses. It is also the right atomic unit: a period
is a complete instruction on its own, so a failure part way leaves earlier
periods changed but every one of them coherent — `set_hold`'s argument again.

Disabling a Heat period writes **two** registers, the hour and the minute, so
the period keeps whatever temperature it had and can be switched back on without
inventing one. A Timer period is four real registers with nothing reserved, so
it always writes all four.

**Note that FC16 does not echo values**, unlike the FC06 every entity uses. A
stat that silently clamps a schedule write says nothing about it, so the re-read
afterwards is the only verification there is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time

from .const import (
    MODEL_HEAT,
    PROGRAM_MODE_5_2,
    PROGRAM_MODE_24_HOUR,
    SETPOINT_MAX_C,
    SETPOINT_MAX_F,
    SETPOINT_MIN_C,
    SETPOINT_MIN_F,
)
from .decode import decode_temperature, encode_temperature
from .registers import SCHEDULES

# The manual's own day order: register 51 is Sunday Period 1.
DAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
DAY_INDEX = {name: index for index, name in enumerate(DAYS)}

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
WEEKEND = ("saturday", "sunday")

# Groups, so an automation can say what it means rather than list five days -
# and so that "weekdays" keeps working when the stat is in 5/2 mode, where the
# five are not independent anyway.
DAY_GROUPS: dict[str, tuple[str, ...]] = {
    "all": DAYS,
    "everyday": DAYS,
    "weekdays": WEEKDAYS,
    "weekend": WEEKEND,
}
DAY_ALIASES = {name[:3]: name for name in DAYS}

# The manual, on every hour register: "The current schedule is invalid when the
# hour = 24." There is no separate enable flag.
UNUSED_HOUR = 24

# Register 28 on a Heat. A Timer's register 28 is Reserved and its grid is four.
PERIODS_FOR_PROGRAM_TYPE = {0: 4, 1: 6}


class ScheduleError(ValueError):
    """A schedule the thermostat should not be asked to store.

    Carries a translation key and its placeholders rather than a formatted
    string, so `services.py` can raise it as a `ServiceValidationError` the user
    reads in their own language. Every one of these is raised *before* anything
    reaches the wire.
    """

    def __init__(self, key: str, **placeholders: object) -> None:
        self.key = key
        self.placeholders = {k: str(v) for k, v in placeholders.items()}
        super().__init__(key)


@dataclass(frozen=True)
class Period:
    """One period of one day, normalised.

    `start is None` marks the period unused — the manual's hour 24. `end` is the
    Timer's off time and is unused on a Heat; `temperature` is the Heat's set
    temperature and is unused on a Timer.
    """

    index: int
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    temperature: float | None = None


def usable_periods(model: str, program_type: int | None) -> int:
    """How many of the grid's periods this thermostat actually runs.

    Register 28 selects 4 or 6 on a Heat; a Timer's grid is four and has no such
    register. The extra two rows still exist in a Heat's registers and still
    read back, so an editor has to be told how many to *offer*.
    """
    layout = SCHEDULES[model]
    if model != MODEL_HEAT:
        return layout.periods
    return PERIODS_FOR_PROGRAM_TYPE.get(program_type, layout.periods)


def resolve_days(names: list[str], program_mode: int | None) -> list[str]:
    """The day names a call touches, in the manual's order, each one once.

    In 5/2 and 24 Hour mode the seven day blocks are **not** independent, and
    the manual does not say which of them the thermostat actually reads. Rather
    than guess, a day named in one of those modes expands to every day sharing
    its program: whichever block the stat reads, it finds what the user asked
    for. That makes the unsettled question moot instead of betting on it.
    """
    wanted: set[str] = set()
    for raw in names:
        name = str(raw).strip().lower()
        if name in DAY_GROUPS:
            wanted.update(DAY_GROUPS[name])
        elif name in DAY_INDEX:
            wanted.add(name)
        elif name in DAY_ALIASES:
            wanted.add(DAY_ALIASES[name])
        else:
            raise ScheduleError("bad_schedule_day", day=raw)
    if not wanted:
        raise ScheduleError("no_schedule_days")

    if program_mode == PROGRAM_MODE_24_HOUR:
        wanted = set(DAYS)
    elif program_mode == PROGRAM_MODE_5_2:
        if wanted & set(WEEKDAYS):
            wanted.update(WEEKDAYS)
        if wanted & set(WEEKEND):
            wanted.update(WEEKEND)
    return [day for day in DAYS if day in wanted]


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------


def read_day(
    model: str, words: dict[int, int], day: str, fahrenheit: bool = False
) -> list[Period]:
    """One day's stored periods, as far as the words can be read."""
    layout = SCHEDULES[model]
    index = DAY_INDEX[day]
    periods: list[Period] = []
    for number in range(1, layout.periods + 1):

        def word(field: str, _n: int = number) -> int | None:
            return words.get(layout.register(index, _n, field))

        if model == MODEL_HEAT:
            periods.append(
                Period(
                    index=number,
                    start=_read_time(word("hour"), word("minute")),
                    # A reading, so deliberately not held to the setpoint
                    # limits: a stat storing something unexpected should show
                    # it. `_parse_temperature` applies the limits on the way in.
                    temperature=decode_temperature(word("settemp"), fahrenheit),
                )
            )
        else:
            periods.append(
                Period(
                    index=number,
                    start=_read_time(word("on_hour"), word("on_min")),
                    end=_read_time(word("off_hour"), word("off_min")),
                )
            )
    return periods


def format_day(model: str, periods: list[Period]) -> list[dict[str, object]]:
    """One day as plain JSON-able data, for the `get_schedule` response.

    The set temperature is reported for an unused period too. It is a real
    stored value, and an editor re-enabling the period should not have to invent
    one.
    """
    rows: list[dict[str, object]] = []
    for period in periods:
        if model == MODEL_HEAT:
            rows.append(
                {
                    "period": period.index,
                    "time": _format_time(period.start),
                    "temperature": period.temperature,
                }
            )
        else:
            rows.append(
                {
                    "period": period.index,
                    "on": _format_time(period.start),
                    "off": _format_time(period.end),
                }
            )
    return rows


def format_week(
    model: str, words: dict[int, int], fahrenheit: bool = False
) -> dict[str, list[dict[str, object]]]:
    """The whole grid, keyed by day name in the manual's order."""
    return {
        day: format_day(model, read_day(model, words, day, fahrenheit)) for day in DAYS
    }


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------


def parse_periods(
    model: str,
    raw: object,
    *,
    periods_available: int,
    fahrenheit: bool = False,
) -> dict[int, Period]:
    """User input to normalised periods, keyed by period number.

    Everything is checked here, before a single register is written: a schedule
    the thermostat cannot store should stop the call, not half a day of it.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ScheduleError("bad_schedule_periods")

    parsed: dict[int, Period] = {}
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ScheduleError("bad_schedule_periods")
        # An explicit period number lets one call edit period 3 alone; without
        # one the list position is the obvious reading.
        number = item.get("period", position)
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise ScheduleError("bad_schedule_period", period=number) from None
        if not 1 <= number <= periods_available:
            raise ScheduleError(
                "bad_schedule_period_number",
                period=number,
                available=periods_available,
            )
        if number in parsed:
            raise ScheduleError("duplicate_schedule_period", period=number)

        if model == MODEL_HEAT:
            parsed[number] = _parse_heat_period(number, item, fahrenheit)
        else:
            parsed[number] = _parse_timer_period(number, item)
    return parsed


def plan_day(
    model: str,
    words: dict[int, int],
    day: str,
    updates: dict[int, Period],
    *,
    periods_available: int,
    fahrenheit: bool = False,
) -> list[tuple[int, list[int]]]:
    """The FC16 writes one day needs, as (first register, values) pairs.

    The updates are merged over what the thermostat currently holds and the
    result is checked as a whole, because ordering is a property of the day and
    not of a period: editing period 3 alone can still leave the day out of
    sequence.

    Only periods whose registers would actually change are returned. An edit to
    one period then costs one FC16 rather than six, which on a 9600-baud bus is
    the difference between a quarter of a second and a second and a half.
    """
    current = {
        period.index: period
        for period in read_day(model, words, day, fahrenheit)
    }
    merged: list[Period] = []
    for number in sorted(current):
        period = updates.get(number, current[number])
        if model == MODEL_HEAT and period.start is not None and period.temperature is None:
            # Enabling a period without naming a temperature keeps the one the
            # stat already holds. If that is not readable there is nothing
            # honest to write, so say so rather than invent a setpoint for
            # someone's heating.
            stored = current[number].temperature
            if stored is None:
                raise ScheduleError(
                    "schedule_needs_a_temperature", day=day, period=number
                )
            period = Period(number, period.start, period.end, stored)
        merged.append(period)

    _check_order(model, day, merged[:periods_available])

    writes: list[tuple[int, list[int]]] = []
    for period in merged:
        if period.index not in updates:
            continue
        register, values = encode_period(model, day, period)
        if [words.get(register + i) for i in range(len(values))] == values:
            continue
        writes.append((register, values))
    return writes


def encode_period(model: str, day: str, period: Period) -> tuple[int, list[int]]:
    """One period as a first register and the words of a single FC16.

    Disabling a Heat period writes only the hour and the minute, so the stored
    set temperature survives and no Reserved register is ever touched.
    """
    layout = SCHEDULES[model]
    index = DAY_INDEX[day]
    if model == MODEL_HEAT:
        base = layout.register(index, period.index, "hour")
        if period.start is None:
            return base, [UNUSED_HOUR, 0]
        assert period.temperature is not None  # plan_day fills this in
        hour, minute = period.start
        return base, [hour, minute, encode_temperature(period.temperature)]

    base = layout.register(index, period.index, "on_hour")
    on = period.start or (UNUSED_HOUR, 0)
    off = period.end or (UNUSED_HOUR, 0)
    return base, [on[0], on[1], off[0], off[1]]


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _read_time(hour: int | None, minute: int | None) -> tuple[int, int] | None:
    """A stored hour/minute pair, or None for a period that is not in use.

    Hour 24 is the manual's "invalid"; anything else out of range is a word we
    cannot read, and both mean the same thing to a caller — there is no period
    here.
    """
    if hour is None or minute is None:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def _format_time(value: tuple[int, int] | None) -> str | None:
    return None if value is None else f"{value[0]:02d}:{value[1]:02d}"


def _parse_time(value: object, number: int) -> tuple[int, int] | None:
    """A user-supplied time, or None for "this period is off".

    None, an empty string and "off" all disable the period, as does the
    thermostat's own 24:00 — someone reading a schedule back and writing it
    again should not have to translate it.
    """
    if value is None:
        return None
    if isinstance(value, dt_time):
        return value.hour, value.minute
    text = str(value).strip().lower()
    if text in ("", "off", "none", "-"):
        return None
    parts = text.split(":")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        raise ScheduleError("bad_schedule_time", time=value, period=number)
    hour, minute = int(parts[0]), int(parts[1])
    if hour == UNUSED_HOUR and minute == 0:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError("bad_schedule_time", time=value, period=number)
    return hour, minute


def _parse_temperature(value: object, number: int, fahrenheit: bool) -> float:
    """A set temperature, against the limits of the stat's own display unit."""
    try:
        temperature = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ScheduleError(
            "bad_schedule_temperature",
            temperature=value,
            period=number,
            low=SETPOINT_MIN_F if fahrenheit else SETPOINT_MIN_C,
            high=SETPOINT_MAX_F if fahrenheit else SETPOINT_MAX_C,
        ) from None
    low = SETPOINT_MIN_F if fahrenheit else SETPOINT_MIN_C
    high = SETPOINT_MAX_F if fahrenheit else SETPOINT_MAX_C
    if not low <= temperature <= high:
        raise ScheduleError(
            "bad_schedule_temperature",
            temperature=temperature,
            period=number,
            low=low,
            high=high,
        )
    return temperature


def _parse_heat_period(number: int, item: dict, fahrenheit: bool) -> Period:
    start = _parse_time(item.get("time"), number)
    if start is None:
        # A disabled period carries no temperature: the write leaves the stored
        # one alone precisely so it is there to come back to.
        return Period(index=number)
    if "temperature" not in item or item["temperature"] is None:
        # Filled in from the thermostat by `plan_day`, which has the words.
        return Period(index=number, start=start)
    return Period(
        index=number,
        start=start,
        temperature=_parse_temperature(item["temperature"], number, fahrenheit),
    )


def _parse_timer_period(number: int, item: dict) -> Period:
    on = _parse_time(item.get("on"), number)
    off = _parse_time(item.get("off"), number)
    if on is None or off is None:
        # A timer period is a pair. Half of one would switch on and never off,
        # or off and never on, so either half missing disables both.
        return Period(index=number)
    return Period(index=number, start=on, end=off)


def _check_order(model: str, day: str, periods: list[Period]) -> None:
    """A day must read forwards, with its unused periods at the end.

    The manual does not say what a thermostat does with periods out of
    sequence, and its own defaults are always ascending with trailing 24s — so
    this is a guard, not a documented rule, and it is recorded as such in
    CLAUDE.md. It runs on the *merged* day, which is what catches an edit to one
    period that would leave the day incoherent.
    """
    previous: tuple[int, int] | None = None
    unused_seen = False
    for period in periods:
        if period.start is None:
            unused_seen = True
            continue
        if unused_seen:
            raise ScheduleError("schedule_gap", day=day, period=period.index)
        if previous is not None and period.start <= previous:
            raise ScheduleError(
                "schedule_out_of_order",
                day=day,
                period=period.index,
                time=_format_time(period.start),
            )
        previous = period.start
        if model != MODEL_HEAT and period.end is not None and period.end <= period.start:
            raise ScheduleError(
                "schedule_off_before_on", day=day, period=period.index
            )

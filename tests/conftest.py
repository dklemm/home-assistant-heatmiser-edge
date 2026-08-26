"""Shared fixtures.

Two layers, matching the two kinds of test:

- `heat_words` / `timer_words` build realistic manual-1..50 word maps. They are
  plain data, used by the pure tests (decode, detect) and by the fake bus.
- `mock_hub` patches `EdgeHub`'s public I/O so a full Home Assistant setup runs
  with no sockets at all, against `FAKE_BUS`. Writes land in `FAKE_BUS`, so a
  read-back sees them; the fixture *yields the write log*, so a test asserts on
  `mock_hub == [(unit_id, register, word)]` - or, for the one FC16 block write,
  `[(unit_id, register, [word, ...])]`.

There is deliberately no `tests/__init__.py`: a second importable copy of these
builders would make mutations invisible to the code under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.heatmiser_edge.hub import EdgeHub

# Every register the manual leaves Reserved reads 0; the RTC block reads its
# documented "already synced" marker.
_RTC = {47: 0xFFFF, 48: 0xFFFF, 49: 0xFFFF, 50: 0xFFFF}

# The weekly program, exactly as the manual's Default value column gives it -
# Sunday and Saturday 09:00/22:00, the weekdays 07:00/09:00/16:00/22:00, and
# every unused period parked at hour 24 while keeping its set temperature.
#
# Building it from real defaults rather than zeros matters: a grid of zeros
# reads as six periods all starting at 00:00, which is out of order, so every
# schedule test would be working against a program no thermostat would hold.
_HEAT_DAYS: dict[int, tuple[tuple[int, int, float], ...]] = {
    0: ((9, 0, 21.0), (22, 0, 16.0), (24, 0, 21.0), (24, 0, 16.0), (24, 0, 21.0), (24, 0, 16.0)),
    6: ((9, 0, 21.0), (22, 0, 16.0), (24, 0, 21.0), (24, 0, 16.0), (24, 0, 21.0), (24, 0, 16.0)),
}
_HEAT_WEEKDAY = (
    (7, 0, 21.0), (9, 0, 16.0), (16, 0, 21.0), (22, 0, 16.0), (24, 0, 21.0), (24, 0, 16.0),
)
_TIMER_DAY = ((7, 0, 9, 0), (16, 0, 20, 0), (24, 0, 24, 0), (24, 0, 24, 0))


def heat_schedule(base: int = 51) -> dict[int, int]:
    """Manual registers 51-218: hour, minute, set temperature, Reserved."""
    words: dict[int, int] = {}
    for day in range(7):
        periods = _HEAT_DAYS.get(day, _HEAT_WEEKDAY)
        for index, (hour, minute, temperature) in enumerate(periods):
            first = base + day * 24 + index * 4
            words[first] = hour
            words[first + 1] = minute
            words[first + 2] = round(temperature * 10)
            words[first + 3] = 0  # Reserved
    return words


def timer_schedule(base: int = 51) -> dict[int, int]:
    """Manual registers 51-162: on hour, on minute, off hour, off minute."""
    words: dict[int, int] = {}
    for day in range(7):
        for index, values in enumerate(_TIMER_DAY):
            first = base + day * 16 + index * 4
            for offset, value in enumerate(values):
                words[first + offset] = value
    return words


def heat_words(unit_id: int = 1, overrides: dict[int, int] | None = None) -> dict[int, int]:
    """A plausible EDGE Heat: 20.5 °C room, 21.0 °C target, schedule mode, on."""
    words = {n: 0 for n in range(1, 51)}
    words.update(
        {
            1: 42,  # firmware version
            2: 1,  # relay on, so hvac_action is heating
            3: 205,  # room 20.5 °C
            4: 0,  # no floor probe fitted
            5: 0,  # no remote probe fitted
            7: 210,  # live setpoint 21.0 °C
            8: 1,  # read-only mirror of 32
            9: 1,  # read-only mirror of 33
            10: 2,  # currently in period 2
            11: 3,
            15: 210,
            16: 205,
            21: 0,  # Celsius
            22: 10,  # 1 °C switching differential
            26: 280,  # floor limit 28.0 °C
            28: 1,  # 6 periods a day, matching the grid built below
            29: 1,  # 7 day program
            31: unit_id,  # the offset discriminator: always the addressed id
            32: 1,  # thermostat on
            33: 1,  # schedule mode
            34: 210,  # last override written
            35: 210,  # advanced setpoint
            37: 120,  # frost 12.0 °C
            43: 1,  # TPI 3 cycles/hour
            44: 1,
        }
    )
    words.update(_RTC)
    words.update(heat_schedule())
    words.update(overrides or {})
    return words


def timer_words(unit_id: int = 2, overrides: dict[int, int] | None = None) -> dict[int, int]:
    """A plausible EDGE Timer: output on, schedule mode, config block Reserved."""
    words = {n: 0 for n in range(1, 51)}
    words.update(
        {
            1: 38,
            2: 1,  # output relay on
            3: 1,  # read-only mirror of 32
            4: 2,
            5: 3,
            9: 1,  # read-only mirror of 33
            29: 1,
            31: unit_id,
            32: 1,
            33: 1,
            34: 0,  # output override off
        }
    )
    words.update(_RTC)
    words.update(timer_schedule())
    words.update(overrides or {})
    return words


def shift_words(words: dict[int, int], by: int) -> dict[int, int]:
    """Re-read a word map at the wrong register offset.

    `by=+1` is what happens when the wire is 0-based and we ask 1-based: asking
    for manual register N returns the word that really belongs to N+1.
    """
    return {n: words.get(n + by, 0) for n in range(1, 51)}


@pytest.fixture
def words() -> SimpleNamespace:
    """The word-map builders, as a fixture.

    Handed over rather than imported, because `tests/` has no `__init__.py` and
    importing across test modules would depend on pytest's sys.path insertion.
    """
    return SimpleNamespace(
        heat=heat_words,
        timer=timer_words,
        shift=shift_words,
        heat_schedule=heat_schedule,
        timer_schedule=timer_schedule,
    )


# The bus a Home Assistant test sees: a Heat, a Timer and a second Heat.
def make_fake_bus() -> dict[int, dict[int, int]]:
    return {
        1: heat_words(1),
        2: timer_words(2),
        3: heat_words(3, {3: 195, 7: 200, 34: 200}),
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_bus() -> dict[int, dict[int, int]]:
    """The mutable bus behind `mock_hub`. Silence a unit by deleting its key."""
    return make_fake_bus()


@pytest.fixture
def mock_hub(fake_bus):
    """Patch EdgeHub's I/O so setup runs against `fake_bus`, with no sockets.

    Yields the write log, so a test can assert exactly which register a control
    wrote - the thing that matters most, because these writes reach a live
    heating system.
    """
    writes: list[tuple[int, int, int]] = []

    async def fake_connect(self) -> None:
        return None

    async def fake_close(self) -> None:
        return None

    async def fake_probe_unit(self, unit_id):
        words = fake_bus.get(unit_id)
        if words is None:
            return None
        return {n: words[n] for n in range(30, 35)}

    async def fake_read_block(self, unit_id, start, count):
        words = fake_bus.get(unit_id)
        if words is None:
            return None
        return {n: words.get(n, 0) for n in range(start, start + count)}

    async def fake_read_units(self, units):
        return {
            unit_id: await fake_read_block(self, unit_id, 1, 50) for unit_id in units
        }

    async def fake_write_register(self, unit_id, register, value) -> None:
        writes.append((unit_id, register, value))
        if unit_id in fake_bus:
            fake_bus[unit_id][register] = value

    async def fake_write_registers(self, unit_id, register, values) -> None:
        # Logged as one entry, not one per register, because the point of FC16
        # is that the block moves together - a test asserting on three separate
        # writes would pass against the very bug this guards against.
        writes.append((unit_id, register, list(values)))
        if unit_id in fake_bus:
            for offset, value in enumerate(values):
                fake_bus[unit_id][register + offset] = value

    with (
        patch.object(EdgeHub, "async_connect", fake_connect),
        patch.object(EdgeHub, "async_close", fake_close),
        patch.object(EdgeHub, "async_probe_unit", fake_probe_unit),
        patch.object(EdgeHub, "async_read_block", fake_read_block),
        patch.object(EdgeHub, "async_read_units", fake_read_units),
        patch.object(EdgeHub, "async_write_register", fake_write_register),
        patch.object(EdgeHub, "async_write_registers", fake_write_registers),
    ):
        yield writes

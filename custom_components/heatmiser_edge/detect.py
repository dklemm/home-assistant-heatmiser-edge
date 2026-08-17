"""Answering the two questions the manual doesn't.

**Is the wire 0-based?** The manual numbers registers from 1 and never says
whether register N sits at wire address N or N-1. Register 31 settles it:
"Communications ID (MODBUS)" necessarily holds the very id you addressed the
request to, so whichever candidate offset makes register 31 read back the unit
id is the right one. That is a self-verifying probe — no guessing, no hardcoded
assumption about the firmware.

**Is this a Heat or a Timer?** There is no model or product-ID register. But the
two variants disagree about most of registers 1-50: a Heat stat stores a frost
setpoint, a floor limit and a switching differential whatever it happens to be
doing, and a Timer marks that entire block Reserved (so it reads zero). Scoring
those against each other separates them by a wide margin; the result is still
only ever a *default the user confirms* in the config flow.

Both are pure functions over word maps, so the hub does the I/O and the tests do
not need any.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    MAX_UNIT_ID,
    MIN_UNIT_ID,
    MODEL_HEAT,
    MODEL_TIMER,
    REG_COMMS_ID,
)

# -1 = the standard Modbus convention (manual register N lives at wire N-1).
#  0 = the firmware numbers the wire exactly as the manual does.
OFFSET_ZERO_BASED = -1
OFFSET_ONE_BASED = 0
OFFSETS = (OFFSET_ZERO_BASED, OFFSET_ONE_BASED)

# Below this the model guess is not trustworthy on its own — the config flow
# flags it rather than quietly accepting it. Calibrated against the worst case
# that matters: a *mis-offset* Heat map (the one situation that produces a
# plausible-looking word map from a real stat) scores at most 4 either way it is
# shifted, while a correctly-read Heat or Timer clears 10.
MIN_MODEL_CONFIDENCE = 5

# The five registers a discovery probe reads (manual 30-34). Small, so a silent
# unit costs one short timeout, and dense in discriminating values.
PROBE_START = 30
PROBE_COUNT = 5


def score_offset(
    words_by_offset: dict[int, dict[int, int]], unit_id: int
) -> int | None:
    """Which candidate offset makes this unit's register 31 read its own id.

    Register 31 on its own is not enough on unit 1: at the wrong offset the same
    slot holds register 32, the thermostat's on/off flag, which also reads 1. So
    the mode and on/off registers corroborate — usually decisively, because at
    the wrong offset the "mode" slot holds the override setpoint (210 for a stat
    set to 21.0 °C), which is not a mode.

    Returns None when even that is ambiguous, which happens on a factory-fresh
    unit 1 whose override setpoint has never been written and so reads 0 — a
    valid mode as well as an unset setpoint. `resolve_offset` breaks that tie
    from another unit rather than guessing here.
    """
    hits = [
        offset
        for offset, words in words_by_offset.items()
        if words.get(REG_COMMS_ID) == unit_id
        # Corroboration: at the true offset, 32 is an on/off flag and 33 is one
        # of six modes. A coincidental match on 31 alone rarely satisfies both.
        and words.get(32, 0) in (0, 1)
        and words.get(33, 99) in range(0, 6)
    ]
    return hits[0] if len(hits) == 1 else None


def resolve_offset(votes: dict[int, int | None]) -> tuple[int, bool]:
    """Combine per-unit verdicts into one answer for the bus.

    Ambiguity is per-unit, and any unit with id >= 6 is decisive, because no
    other register in the 30-34 window can hold a value above 5. So one clear
    voter settles it for everyone. Returns (offset, decisive).
    """
    decided = [offset for offset in votes.values() if offset is not None]
    if not decided:
        # Nothing was decisive. The standard convention is overwhelmingly the
        # more likely, and the config flow exposes an override either way.
        return OFFSET_ZERO_BASED, False
    counts = {offset: decided.count(offset) for offset in set(decided)}
    best = max(counts, key=lambda offset: counts[offset])
    return best, True


@dataclass(frozen=True)
class ModelGuess:
    """Which variant a word map looks like, and how sure we are."""

    model: str
    confidence: int
    heat: int
    timer: int

    @property
    def confident(self) -> bool:
        return self.confidence >= MIN_MODEL_CONFIDENCE


def _in(words: dict[int, int], number: int, low: int, high: int) -> bool:
    value = words.get(number)
    return value is not None and low <= value <= high


def _all_zero(words: dict[int, int], numbers: range | tuple[int, ...]) -> bool:
    return all(words.get(n) == 0 for n in numbers)


# The manual's temperature ranges, x10, in each display unit. Register 21 says
# which one a stat is using, and every temperature register follows it - so a
# °F stat's frost setpoint reads 540, not 120, and °C-only bands would score it
# as not a Heat at all.
_HEAT_BANDS_C = {
    7: (50, 350),  # live setpoint, 5-35 °C
    37: (70, 170),  # frost setpoint, 7-17 °C (Reserved on a Timer)
    26: (200, 450),  # floor limit, 20-45 °C
    3: (50, 400),  # room temperature: a temperature, not a flag
    35: (50, 350),  # advanced setpoint
}
_HEAT_BANDS_F = {
    7: (410, 950),  # 41-95 °F
    37: (450, 630),  # 45-63 °F
    26: (680, 1130),  # 68-113 °F
    3: (410, 1040),
    35: (410, 950),
}


def score_heat(words: dict[int, int]) -> int:
    """How much this map looks like an EDGE Heat.

    The heavy terms are *stored settings*, not live readings: the frost setpoint
    and floor limit are non-volatile and present whatever the stat is currently
    doing, so a Heat scores well even switched off in midsummer.

    Bands follow register 21, the stat's display unit. When the offset is wrong
    register 21 is garbage and we fall back to °C - which is what we want, since
    a mis-offset map should score low, not be rescued by a wider band.
    """
    bands = _HEAT_BANDS_F if words.get(21) == 1 else _HEAT_BANDS_C
    score = 0
    if _in(words, 7, *bands[7]):
        score += 3
    if _in(words, 37, *bands[37]):
        score += 3
    if words.get(22) in (5, 10, 20, 30):  # switching differential legend
        score += 2
    if _in(words, 26, *bands[26]):
        score += 2
    if _in(words, 3, *bands[3]):
        score += 2
    if _in(words, 35, *bands[35]):
        score += 1
    if (
        words.get(21) in (0, 1)
        and words.get(25, 99) <= 4
        and words.get(27, 99) <= 5
    ):  # the config legends all agree
        score += 1
    return score


def score_timer(words: dict[int, int]) -> int:
    """How much this map looks like an EDGE Timer.

    Its biggest term - the whole 21-28 config block reading zero - can never
    fire on a Heat, because register 22 is always one of 5/10/20/30 and 26
    defaults to 280.
    """
    score = 0
    if words.get(3) in (0, 1):  # on/off flag, not a temperature x10
        score += 2
    if words.get(4, 99) <= 4 and words.get(5, 99) <= 4:  # four schedule periods
        score += 2
    if _all_zero(words, (7, 8)):  # Heat's setpoint registers are Reserved here
        score += 2
    if _all_zero(words, range(21, 29)):  # the entire config block is Reserved
        score += 3
    if _all_zero(words, (35, 36, 37)):
        score += 2
    if _all_zero(words, range(42, 46)):
        score += 1
    if words.get(34) in (0, 1):  # Timer Out force
        score += 1
    return score


def guess_model(words: dict[int, int]) -> ModelGuess:
    """Guess the variant from one manual-1..50 block.

    Only meaningful once the offset is settled: a map read at the wrong offset
    scores low on both sides, which `confident` reports honestly rather than
    picking a winner from noise.
    """
    heat, timer = score_heat(words), score_timer(words)
    model = MODEL_HEAT if heat >= timer else MODEL_TIMER
    return ModelGuess(model, abs(heat - timer), heat, timer)


def parse_id_list(text: str) -> list[int]:
    """Parse "1-4,7,11-13" into sorted unique unit ids.

    Raises ValueError with a reason for anything out of range - including 0 and
    255, which are real Modbus ids but never thermostats.
    """
    ids: set[int] = set()
    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk.lstrip("-"):
            low_text, _, high_text = chunk.partition("-")
            low, high = int(low_text), int(high_text)
            if low > high:
                raise ValueError(f"'{chunk}' counts backwards")
            candidates = range(low, high + 1)
        else:
            candidates = range(int(chunk), int(chunk) + 1)
        for value in candidates:
            if not MIN_UNIT_ID <= value <= MAX_UNIT_ID:
                raise ValueError(
                    f"{value} is not a thermostat id "
                    f"(valid ids are {MIN_UNIT_ID}-{MAX_UNIT_ID}; "
                    "0 disables Modbus and 255 is the radio channel)"
                )
            ids.add(value)
    if not ids:
        raise ValueError("no unit ids given")
    return sorted(ids)

"""Answering the question the manual doesn't: is this a Heat or a Timer?

There is no model or product-ID register. But the two variants disagree about
most of registers 1-50: a Heat stat stores a frost setpoint, a floor limit and a
switching differential whatever it happens to be doing, and a Timer marks that
entire block Reserved (so it reads zero). Scoring those against each other
separates them by a wide margin; the result is still only ever a *default the
user confirms* in the config flow.

Pure functions over word maps, so the hub does the I/O and the tests need none.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import MAX_UNIT_ID, MIN_UNIT_ID, MODEL_HEAT, MODEL_TIMER

# Below this the config flow flags the guess rather than quietly accepting it.
# Calibrated against the worst case: a *mis-offset* Heat map - the one situation
# producing a plausible map from real hardware - scores at most 4, while a
# correctly-read Heat or Timer clears 10.
MIN_MODEL_CONFIDENCE = 5

# What a discovery probe reads: small, so an absent id costs one short timeout.
PROBE_START = 30
PROBE_COUNT = 5


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

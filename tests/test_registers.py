"""The register map and the curated tables must agree with each other.

These are regression gates on hand-transcribed data: nothing here needs a bus, a
config entry or Home Assistant, and a mistranscribed address is exactly the kind
of bug that otherwise surfaces as a mysterious wrong reading months later.
"""

from custom_components.heatmiser_edge.const import (
    BINARY,
    DIAGNOSTIC,
    DISABLED_BY_DEFAULT,
    ENUMS,
    MAX_BLOCK,
    MODEL_HEAT,
    MODEL_TIMER,
    MODELS,
    NUMBERS,
    OPERATION_MODES,
    POLL_COUNT,
    POLL_START,
    READ_ONLY_RW,
    SELECTS,
    SUPPRESSED,
    SWITCHES,
)
from custom_components.heatmiser_edge.registers import (
    REGISTERS,
    SCHEDULES,
    reg,
    registers_for,
)


def test_schedule_layouts_end_where_the_manual_does():
    """The manual's last schedule rows are 218 (Heat) and 162 (Timer).

    This is the gate on the whole ScheduleLayout arithmetic: get the stride or
    the period count wrong and these numbers move.
    """
    assert SCHEDULES[MODEL_HEAT].last_register == 218
    assert SCHEDULES[MODEL_TIMER].last_register == 162


def test_schedule_register_addresses_match_the_manual():
    heat = SCHEDULES[MODEL_HEAT]
    assert heat.register(0, 1, "hour") == 51  # Sunday Period1 Hour
    assert heat.register(0, 1, "settemp") == 53
    assert heat.register(1, 1, "hour") == 75  # Monday Period1 Hour
    assert heat.register(6, 6, "settemp") == 217  # Saturday Period6 SetTemp

    timer = SCHEDULES[MODEL_TIMER]
    assert timer.register(0, 1, "on_hour") == 51
    assert timer.register(0, 1, "off_hour") == 53
    assert timer.register(1, 1, "on_hour") == 67  # Monday Period1 On Hour
    assert timer.register(6, 4, "off_min") == 162


def test_v1_poll_fits_one_packet():
    """The manual caps a packet at 60 registers; one poll must be one read."""
    assert POLL_START == 1
    assert POLL_COUNT <= MAX_BLOCK


def test_every_curated_register_exists_in_its_model():
    """A table naming a register the map doesn't define would create nothing.

    SUPPRESSED is deliberately excluded: it is a denylist and legitimately names
    registers (46-50) that never enter the map at all.
    """
    tables = (NUMBERS, SELECTS, SWITCHES, BINARY, READ_ONLY_RW, ENUMS)
    for model in MODELS:
        for table in tables:
            for number in table[model]:
                assert reg(model, number) is not None, (
                    f"{model} table names register {number}, which is not in the map"
                )
        for number in DISABLED_BY_DEFAULT[model] | DIAGNOSTIC[model]:
            assert reg(model, number) is not None


def test_no_register_is_claimed_by_two_platform_tables():
    """One register, at most one entity. platform_for() relies on this."""
    for model in MODELS:
        claims: dict[int, list[str]] = {}
        for name, table in (
            ("number", NUMBERS[model]),
            ("select", SELECTS[model]),
            ("switch", SWITCHES[model]),
            ("read_only_rw", READ_ONLY_RW[model]),
        ):
            for number in table:
                claims.setdefault(number, []).append(name)
        duplicated = {n: names for n, names in claims.items() if len(names) > 1}
        assert not duplicated, f"{model}: {duplicated}"


def test_writable_tables_only_name_writable_registers():
    for model in MODELS:
        for table in (NUMBERS[model], SELECTS[model], SWITCHES[model]):
            for number in table:
                assert reg(model, number).access == "RW", (
                    f"{model} register {number} is read-only but ships as a control"
                )


def test_read_only_tables_only_name_read_only_registers():
    """BINARY and ENUMS describe how to *present* a reading, never a control."""
    for model in MODELS:
        for table in (BINARY[model], ENUMS[model]):
            for number in table:
                register = reg(model, number)
                if register.access == "RW":
                    # A writable register may still be presented read-only, but
                    # only through the READ_ONLY_RW holding pen or a select.
                    assert (
                        number in READ_ONLY_RW[model] or number in SELECTS[model]
                    ), f"{model} register {number} is writable and unaccounted for"


def test_climate_owned_registers_are_suppressed():
    """The Heat climate entity owns 7/8/9/32/33/34; none may also be an entity."""
    for number in (7, 8, 9, 32, 33, 34):
        assert number in SUPPRESSED[MODEL_HEAT]


def test_hazard_registers_never_enter_the_map():
    """46 is a factory reset and 47-50 self-clear; none of them ships.

    Absence from the map is the real guarantee - SUPPRESSED is belt and braces.
    """
    for model in MODELS:
        for number in (46, 47, 48, 49, 50):
            assert reg(model, number) is None
            assert number in SUPPRESSED[model]


def test_the_two_variants_disagree_where_the_manual_says_they_do():
    """Register 3 is a temperature on Heat and an on/off flag on Timer.

    This is why every lookup takes a model, and why changing a unit's model is a
    breaking change for its entities.
    """
    assert reg(MODEL_HEAT, 3).kind == "temp"
    assert reg(MODEL_TIMER, 3).kind == "bool"
    # The Timer marks the whole Heat config block Reserved.
    for number in range(21, 29):
        assert reg(MODEL_TIMER, number) is None
    assert reg(MODEL_TIMER, 37) is None


def test_registers_for_is_sorted_and_complete():
    for model in MODELS:
        numbers = [r.number for r in registers_for(model)]
        assert numbers == sorted(numbers)
        assert set(numbers) == set(REGISTERS[model])


def test_register_keys_are_unique_within_a_model():
    for model in MODELS:
        keys = [r.key for r in registers_for(model)]
        assert len(keys) == len(set(keys))


def test_operation_mode_legends_differ_only_at_mode_five():
    """One register, two label sets: mode 5 is Frost on Heat, Standby on Timer."""
    heat, timer = OPERATION_MODES[MODEL_HEAT], OPERATION_MODES[MODEL_TIMER]
    assert set(heat) == set(timer) == set(range(6))
    assert {k: v for k, v in heat.items() if k != 5} == {
        k: v for k, v in timer.items() if k != 5
    }
    assert heat[5] == "Frost"
    assert timer[5] == "Standby"


def test_every_polled_register_is_within_the_poll_block():
    """v1 reads registers 1-50; nothing in the map may fall outside it."""
    for model in MODELS:
        for register in registers_for(model):
            assert POLL_START <= register.number < POLL_START + POLL_COUNT

"""The EDGE register map, transcribed by hand from the manual.

Source of truth: `docs/EDGE-RS485-MODBUS-Communication-protocol-V1.8.md`. Unlike
CTC's generated map this one is hand-written, because the manual *is* a 50-row
table — a parser would be more code than the data.

`Reg.number` is always the manual's own **1-based register number**, never a wire
address. Whether the wire is 0-based is not stated anywhere in the manual and is
settled empirically by `detect.py`; `EdgeHub.wire()` is the only place that
offset is ever applied. Keeping doc numbers everywhere else means the code reads
against the manual line by line.

The two variants have genuinely different maps at the same addresses — Timer
register 3 is an on/off flag where Heat register 3 is a temperature — so they are
separate dicts and every lookup takes a model.

Registers the manual marks Reserved are simply absent: they carry no meaning, so
they never become entities and never need suppressing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import MODEL_HEAT, MODEL_TIMER

# Reg.kind drives decoding and presentation:
#   temp        - wire value is temperature x 10, in the stat's own unit (reg 21)
#   raw         - a plain unsigned number
#   enum        - a documented legend; the labels live in const.ENUMS
#   bool        - on/off
#   packed_hm   - high byte hours, low byte minutes; presented as "HH:MM"
#   duration_hm - the same packing, presented (and written) as total minutes
#   packed_md   - high byte month, low byte day; presented as "MM-DD"
#   year        - a calendar year
KIND_TEMP = "temp"
KIND_RAW = "raw"
KIND_ENUM = "enum"
KIND_BOOL = "bool"
KIND_PACKED_HM = "packed_hm"
KIND_DURATION_HM = "duration_hm"
KIND_PACKED_MD = "packed_md"
KIND_YEAR = "year"


@dataclass(frozen=True)
class Reg:
    """One register of one EDGE variant."""

    number: int
    key: str
    name: str
    kind: str
    access: str = "R"  # "R" | "RW"
    unit: str | None = None  # explicit unit; temperatures carry theirs via kind
    scale: float = 1.0


def _r(number, key, name, kind, access="R", unit=None, scale=1.0) -> Reg:
    return Reg(number, key, name, kind, access, unit, scale)


# Manual registers 1-20 are read (03) only; 21-50 are read/write (03/06/16).
_HEAT: tuple[Reg, ...] = (
    _r(1, "firmware", "Firmware version", KIND_RAW),
    _r(2, "relay", "Heating", KIND_BOOL),
    _r(3, "room_temperature", "Room temperature", KIND_TEMP, scale=0.1),
    _r(4, "floor_temperature", "Floor temperature", KIND_TEMP, scale=0.1),
    _r(5, "remote_temperature", "Remote sensor temperature", KIND_TEMP, scale=0.1),
    _r(6, "window", "Window open", KIND_BOOL),
    _r(7, "current_setpoint", "Target temperature", KIND_TEMP, scale=0.1),
    _r(8, "onoff_readback", "Thermostat on", KIND_BOOL),
    _r(9, "mode_readback", "Operation mode", KIND_ENUM),
    _r(10, "current_period", "Current schedule period", KIND_ENUM),
    _r(11, "next_period", "Next schedule period", KIND_ENUM),
    _r(12, "dst_active", "Daylight saving active", KIND_BOOL),
    _r(13, "rate_of_change", "Rate of change", KIND_RAW),
    _r(15, "board_temperature_raw", "Board sensor (before compensation)", KIND_TEMP, scale=0.1),
    _r(16, "board_temperature", "Board sensor (after compensation)", KIND_TEMP, scale=0.1),
    _r(21, "temp_format", "Temperature format", KIND_ENUM, "RW"),
    _r(22, "switching_differential", "Switching differential", KIND_ENUM, "RW"),
    _r(23, "output_delay", "Output delay", KIND_RAW, "RW", unit="min"),
    _r(24, "updown_limit", "Up/down limit", KIND_TEMP, "RW", scale=0.1),
    _r(25, "sensor_selection", "Sensor selection", KIND_ENUM, "RW"),
    _r(26, "floor_limit", "Floor limit temperature", KIND_TEMP, "RW", scale=0.1),
    _r(27, "optimum_start", "Optimum start", KIND_ENUM, "RW"),
    _r(28, "program_type", "Program type", KIND_ENUM, "RW"),
    _r(29, "program_mode", "Program mode", KIND_ENUM, "RW"),
    _r(30, "dst_enabled", "Daylight saving", KIND_BOOL, "RW"),
    _r(31, "comms_id", "Communications ID", KIND_RAW, "RW"),
    _r(32, "onoff", "Thermostat on", KIND_BOOL, "RW"),
    _r(33, "operation_mode", "Operation mode", KIND_ENUM, "RW"),
    _r(34, "hold_setpoint", "Override set temperature", KIND_TEMP, "RW", scale=0.1),
    _r(35, "advanced_setpoint", "Advanced set temperature", KIND_TEMP, "RW", scale=0.1),
    _r(37, "frost_setpoint", "Frost set temperature", KIND_TEMP, "RW", scale=0.1),
    _r(38, "hold_duration", "Hold duration", KIND_DURATION_HM, "RW", unit="min"),
    _r(39, "away_time", "Away until (time)", KIND_PACKED_HM, "RW"),
    _r(40, "away_date", "Away until (date)", KIND_PACKED_MD, "RW"),
    _r(41, "away_year", "Away until (year)", KIND_YEAR, "RW"),
    _r(42, "keylock", "Keylock", KIND_BOOL, "RW"),
    _r(43, "tpi", "TPI", KIND_ENUM, "RW"),
    _r(44, "tpi_min_on", "TPI minimum on time", KIND_RAW, "RW", unit="min"),
)

_TIMER: tuple[Reg, ...] = (
    _r(1, "firmware", "Firmware version", KIND_RAW),
    _r(2, "relay", "Output", KIND_BOOL),
    _r(3, "onoff_readback", "Timer on", KIND_BOOL),
    _r(4, "current_period", "Current schedule period", KIND_ENUM),
    _r(5, "next_period", "Next schedule period", KIND_ENUM),
    _r(6, "dst_active", "Daylight saving active", KIND_BOOL),
    _r(9, "mode_readback", "Operation mode", KIND_ENUM),
    _r(29, "program_mode", "Program mode", KIND_ENUM, "RW"),
    _r(30, "dst_enabled", "Daylight saving", KIND_BOOL, "RW"),
    _r(31, "comms_id", "Communications ID", KIND_RAW, "RW"),
    _r(32, "onoff", "Timer", KIND_BOOL, "RW"),
    _r(33, "operation_mode", "Operation mode", KIND_ENUM, "RW"),
    _r(34, "output_force", "Output override", KIND_BOOL, "RW"),
    _r(38, "hold_duration", "Hold duration", KIND_DURATION_HM, "RW", unit="min"),
    _r(39, "away_time", "Away until (time)", KIND_PACKED_HM, "RW"),
    _r(40, "away_date", "Away until (date)", KIND_PACKED_MD, "RW"),
    _r(41, "away_year", "Away until (year)", KIND_YEAR, "RW"),
)

REGISTERS: dict[str, dict[int, Reg]] = {
    MODEL_HEAT: {r.number: r for r in _HEAT},
    MODEL_TIMER: {r.number: r for r in _TIMER},
}


def reg(model: str, number: int) -> Reg | None:
    """The register, or None if this variant does not define it."""
    return REGISTERS[model].get(number)


def registers_for(model: str) -> list[Reg]:
    """Every register this variant defines, in manual order."""
    return [REGISTERS[model][n] for n in sorted(REGISTERS[model])]


@dataclass(frozen=True)
class ScheduleLayout:
    """The weekly program's shape. Declared now; not polled in v1.

    The program is a strict grid: `stride` registers per period, `periods`
    periods per day, seven days from Sunday. Declaring it here means the v2
    schedule feature adds a poll set and a platform, not a new understanding of
    the map — and `last_register` is asserted against the manual's final row
    today, so a stride or period mistake surfaces long before anyone builds on
    it.
    """

    base: int
    periods: int
    stride: int
    fields: tuple[str, ...]

    @property
    def per_day(self) -> int:
        return self.periods * self.stride

    @property
    def last_register(self) -> int:
        return self.base + 7 * self.per_day - 1

    def register(self, day: int, period: int, field: str) -> int:
        """day 0=Sunday..6=Saturday (the manual's order), period 1-based."""
        if not 0 <= day <= 6:
            raise ValueError(f"day must be 0..6, got {day}")
        if not 1 <= period <= self.periods:
            raise ValueError(f"period must be 1..{self.periods}, got {period}")
        return (
            self.base
            + day * self.per_day
            + (period - 1) * self.stride
            + self.fields.index(field)
        )


SCHEDULES: dict[str, ScheduleLayout] = {
    # Heat: Hour / Minute / SetTemp / Reserved, 6 periods a day, 51-218.
    MODEL_HEAT: ScheduleLayout(51, 6, 4, ("hour", "minute", "settemp", "reserved")),
    # Timer: On Hour / On Min / Off Hour / Off Min, 4 periods a day, 51-162.
    MODEL_TIMER: ScheduleLayout(51, 4, 4, ("on_hour", "on_min", "off_hour", "off_min")),
}

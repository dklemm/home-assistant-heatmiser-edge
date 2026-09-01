"""Constants and the curated entity tables for Heatmiser EDGE.

Two kinds of thing live here, and the split matters:

- **Protocol constants** taken from the manual (baud, the 60-register packet cap,
  the 50 ms inter-transaction gap, the valid unit-id range). These are facts.
- **Curated ship-list tables** — which writable register becomes which HA entity,
  with what limits and what legend. These are *policy*: a write goes to a live
  heating system, so a writable register produces an entity only if a table here
  names it. Read-only registers always ship. `EdgeCoordinator.platform_for()` is
  the single gate that reads these.

Register numbers throughout are the manual's own 1-based numbers, never wire
addresses; `EdgeHub.wire()` is the only place the offset is applied.
"""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "heatmiser_edge"
MANUFACTURER = "Heatmiser"

MODEL_HEAT = "heat"
MODEL_TIMER = "timer"
MODELS = (MODEL_HEAT, MODEL_TIMER)
MODEL_LABELS = {MODEL_HEAT: "EDGE Heat", MODEL_TIMER: "EDGE Timer"}

TRANSPORT_SERIAL = "serial"
TRANSPORT_TCP = "tcp"

FRAMER_SOCKET = "socket"
FRAMER_RTU = "rtu"

CONF_TRANSPORT = "transport"
CONF_SERIAL_PORT = "serial_port"
CONF_FRAMER = "framer"
CONF_UNITS = "units"
CONF_UNIT_IDS = "unit_ids"
CONF_CONTROLS = "controls"
CONF_TIMEOUT = "timeout"

CONF_UNIT_ID = "unit_id"
CONF_MODEL = "model"

# The manual states baud and parity only. Byte size and stop bits are not given
# anywhere in it; 8N1 is the near-universal Modbus RTU framing and what every
# working field report uses. Fixed, not asked for: on hardware parity E and O, two
# stop bits and every other baud were all silent, so there is nothing to choose
# between. `dev/edge_modbus_test.py` still takes --baud and friends.
DEFAULT_BAUDRATE = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_TCP_PORT = 502

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 600

DEFAULT_TIMEOUT = 1.0
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 10.0
# Discovery sweeps up to 32 ids, so a silent one must be cheap: half a second
# each keeps a full 1-32 sweep near 18 s instead of a minute.
SCAN_TIMEOUT = 0.5

# How often the scan's progress dialog re-renders. Deliberately a clock and not
# "once per unit id": a stat that answers takes ~150 ms and an absent one pays a
# full SCAN_TIMEOUT, so per-unit updates arrive at wildly uneven intervals and
# the dialog visibly stutters.
SCAN_PROGRESS_INTERVAL = 1.0

INTER_TRANSACTION_GAP = 0.05  # manual: "read and write data interval greater than 50 ms"

# Manual register N at wire N-1: the standard Modbus convention. The manual never
# says, so this was settled on hardware and confirmed across 1-218 (see CLAUDE.md).
# It is fixed, not configurable: every device seen answers to it, a user has no way
# to know theirs, and `EdgeHub._check_register_base` warns if one ever disagrees.
DEFAULT_REGISTER_OFFSET = -1
# Both candidates, for `dev/edge_modbus_test.py detect` - the only thing that still
# reads at a base other than the one above.
REGISTER_OFFSETS = (-1, 0)

# When to ask the thermostat again after a write, until it has actually acted.
# Seconds after the write, not gaps between probes.
#
# The single most important timing fact in this integration, measured on
# hardware 2026-08-13: **the stat takes a write immediately but acts on it
# slowly, and not all at once.** Three findings, each of which cost a wrong
# version to learn.
#
# 1. The write itself is instant, its effect is not. Register 34 held the newly
#    written value in the +80 ms read-back every time - accepted, echoed,
#    stored - while register 7, the live setpoint every UI surface reads, still
#    held the old one. So the immediate read-back finds nothing new, no state
#    change fires, and the card sits still. One read-back can never be enough.
#
# 2. A fixed delay is always wrong. The first version was a single read five
#    seconds after the write, and five seconds is exactly what the user then
#    saw: the UI was waiting out our timer, not the thermostat. Register 7
#    actually caught up at <=0.33 s, +0.36 s and +0.97 s twice - under a second,
#    but not predictably so, which looks like the stat refreshing 7 on its own
#    internal cycle. The wait is just where the write lands in that cycle.
#
# 3. The schedule must not stop at the first change. Registers do not move
#    together: over five writes the relay at register 2 came with register 7 at
#    0.3 s and 0.8 s, and *alone* at 1.6 s twice. Stopping on the first
#    difference published the setpoint and left the relay stale until the next
#    poll - the original complaint, moved rather than fixed. Every probe runs.
#
# Nothing has ever reached 3.0 s; it and 6.0 s are the backstop. Chasing the
# relay further is futile anyway - it is a control *output*, answering to the
# stat's own algorithm rather than to our write, so past a few seconds the scan
# interval is the right instrument and more probes only spend bus time.
SETTLE_PROBES = (0.3, 0.8, 1.6, 3.0, 6.0)
MAX_BLOCK = 60  # manual: "Each send a packet of data, register number cannot exceed 60"

# v1 polls the manual's registers 1-50 only. The weekly program (51-218 on Heat,
# 51-162 on Timer) is declared in registers.SCHEDULES but not polled.
POLL_START = 1
POLL_COUNT = 50

# Ids 0 and 255 are not thermostats: 0 disables Modbus on the stat entirely, and
# 255 is the radio command channel.
MIN_UNIT_ID = 1
MAX_UNIT_ID = 32
VALID_UNIT_IDS = range(MIN_UNIT_ID, MAX_UNIT_ID + 1)

# Every register the code names, in the manual's own order. The curated tables
# below are built from these rather than from bare numbers: a set of integers
# says nothing about what it collects, and the two variants disagree about what
# several of the numbers mean.
#
# That disagreement is the one trap. A bare name is the **Heat** map's meaning,
# which is also the shared one wherever both variants agree; `REG_TIMER_*` names
# the five registers where the Timer puts something else at the same number.
# `tests/test_registers.py` checks every number a table names against that
# model's map, so a Heat name in a Timer table is caught where the two maps
# disagree about which registers exist at all.
REG_FIRMWARE = 1
REG_RELAY = 2
REG_ROOM_TEMP = 3
REG_TIMER_ONOFF_READBACK = 3
REG_FLOOR_TEMP = 4
REG_TIMER_CURRENT_PERIOD = 4
REG_REMOTE_TEMP = 5
REG_TIMER_NEXT_PERIOD = 5
REG_WINDOW = 6
REG_TIMER_DST_ACTIVE = 6
REG_CURRENT_SETPOINT = 7
REG_ONOFF_READBACK = 8
REG_MODE_READBACK = 9
REG_CURRENT_PERIOD = 10
REG_NEXT_PERIOD = 11
REG_DST_ACTIVE = 12
REG_RATE_OF_CHANGE = 13
REG_BOARD_TEMP_RAW = 15
REG_BOARD_TEMP = 16
REG_TEMP_FORMAT = 21  # 0 = degC, 1 = degF; re-reads the meaning of every temperature
REG_SWITCHING_DIFFERENTIAL = 22
REG_OUTPUT_DELAY = 23
REG_UPDOWN_LIMIT = 24
REG_SENSOR_SELECTION = 25
REG_FLOOR_LIMIT = 26
REG_OPTIMUM_START = 27
REG_PROGRAM_TYPE = 28  # 0 = 4 period, 1 = 6 period. Heat only; Reserved on Timer
REG_PROGRAM_MODE = 29  # gates which operation modes the stat will accept
REG_DST_ENABLED = 30
REG_COMMS_ID = 31  # equals the addressed unit id: the register-offset discriminator
REG_ONOFF = 32
REG_OPERATION_MODE = 33
REG_HOLD_SETPOINT = 34
REG_TIMER_OUTPUT_FORCE = 34
REG_ADVANCED_SETPOINT = 35
REG_FROST_SETPOINT = 37
REG_HOLD_DURATION = 38  # high byte hours (0-99), low byte minutes (0-59)
REG_AWAY_TIME = 39
REG_AWAY_DATE = 40
REG_AWAY_YEAR = 41
REG_KEYLOCK = 42
REG_TPI = 43
REG_TPI_MIN_ON = 44
REG_FACTORY_RESET = 46
# The RTC is four contiguous registers holding one timestamp: year, month+day,
# hour+minute, second. They are write-only in practice - each reads back 0xFFFF
# once the stat has taken the time - so they are set by an action, not shown by
# an entity. See `services.py`.
REG_RTC = 47
RTC_BLOCK = 4

# Service (action) names and their fields.
SERVICE_SET_TIME = "set_time"
ATTR_DATETIME = "datetime"
ATTR_DST = "dst"
SERVICE_SET_HOLD = "set_hold"
ATTR_DURATION = "duration"
ATTR_TEMPERATURE = "temperature"
# The weekly program is 168 registers on a Heat and 112 on a Timer - three and
# two FC03 packets, ~0.6 s of a 9600-baud bus per thermostat. Far too expensive
# for the interval poll, and far too many values for entities, so it is read on
# demand and edited in bulk by these two actions. See `schedule.py`.
SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
ATTR_DAYS = "days"
ATTR_PERIODS = "periods"

# A thermostat taken off the wall must not cost a timeout every poll for ever.
UNIT_BACKOFF_AFTER = 3
UNIT_BACKOFF_EVERY = 5

# The manual's operation-mode legend. One register (33), two label sets: mode 5
# is Frost on a Heat stat and Standby on a Timer.
MODE_CHANGE_OVER = 0
MODE_SCHEDULE = 1
MODE_HOLD = 2
MODE_ADVANCED = 3
MODE_AWAY = 4
MODE_FROST = 5

OPERATION_MODES: dict[str, dict[int, str]] = {
    MODEL_HEAT: {
        MODE_CHANGE_OVER: "Change over",
        MODE_SCHEDULE: "Schedule",
        MODE_HOLD: "Hold",
        MODE_ADVANCED: "Advanced",
        MODE_AWAY: "Away",
        MODE_FROST: "Frost",
    },
    MODEL_TIMER: {
        MODE_CHANGE_OVER: "Change over",
        MODE_SCHEDULE: "Schedule",
        MODE_HOLD: "Hold",
        MODE_ADVANCED: "Advanced",
        MODE_AWAY: "Away",
        MODE_FROST: "Standby",
    },
}

# Climate presets are the mode legend, so the two can never drift apart.
PRESET_FOR_MODE: dict[int, str] = OPERATION_MODES[MODEL_HEAT]
MODE_FOR_PRESET: dict[str, int] = {v: k for k, v in PRESET_FOR_MODE.items()}

# Program mode 03, "None programmable", has no weekly program at all - so the
# modes that exist only in relation to one are not available, and the stat
# silently refuses to enter them. Confirmed on hardware 2026-08-13: with the
# stat in this mode, an FC06 write of 2 (Hold) to register 33 came back echoing
# 0. The manual documents none of this.
#
# Change over, Hold and Frost are what remain. Offering the other three would be
# offering a control that does nothing, which is the bug this table exists to
# prevent.
PROGRAM_MODE_5_2 = 0
PROGRAM_MODE_7_DAY = 1
PROGRAM_MODE_24_HOUR = 2
PROGRAM_MODE_NON_PROGRAMMABLE = 3
NON_PROGRAMMABLE_MODES = (MODE_CHANGE_OVER, MODE_HOLD, MODE_FROST)

# Hold needs a duration *first*. Hardware 2026-08-13: with register 38 at 0, a
# write of 2 to register 33 is refused (FC06 echoes back the old value); write a
# non-zero duration to 38 and the identical write is then accepted. The keypad
# agrees - its Hold flow asks for hours, then minutes, then a temperature. So
# Hold is three registers, and `heatmiser_edge.set_hold` writes them in that
# order; the climate preset raises rather than issue a write that will be
# ignored.
HOLD_MAX_HOURS = 99
HOLD_MAX_MINUTES = 59

# Setpoint limits, per the manual's "5~35 degC (41~95 degF)". The wire value is
# always temperature x 10 whichever unit the stat is displaying.
SETPOINT_MIN_C, SETPOINT_MAX_C, SETPOINT_STEP_C = 5.0, 35.0, 0.5
SETPOINT_MIN_F, SETPOINT_MAX_F, SETPOINT_STEP_F = 41.0, 95.0, 1.0


@dataclass(frozen=True)
class NumberSpec:
    """A writable register that ships as a `number`.

    Temperature limits come in degC and degF pairs because the stat's own unit
    (register 21) decides which the wire value means; `unit` of None marks the
    entity as a temperature that follows register 21.
    """

    min_c: float
    max_c: float
    step_c: float
    min_f: float | None = None
    max_f: float | None = None
    step_f: float | None = None
    unit: str | None = None
    category: str | None = "config"
    enabled: bool = True

    def limits(self, fahrenheit: bool) -> tuple[float, float, float]:
        if fahrenheit and self.min_f is not None:
            assert self.max_f is not None and self.step_f is not None
            return self.min_f, self.max_f, self.step_f
        return self.min_c, self.max_c, self.step_c


@dataclass(frozen=True)
class SelectSpec:
    """A writable register whose *complete* value set the manual spells out.

    Only a complete legend qualifies: the stat accepts undocumented values
    silently, so a partial legend would let a user write a state we cannot read
    back.
    """

    options: dict[int, str]
    options_f: dict[int, str] | None = None
    category: str | None = "config"
    enabled: bool = True

    def labels(self, fahrenheit: bool) -> dict[int, str]:
        return self.options_f if fahrenheit and self.options_f else self.options


@dataclass(frozen=True)
class SwitchSpec:
    """A writable register that is boolean by documented evidence.

    Polarity follows the *register*, not HA: `on`/`off` are the raw wire values.
    `requires_mode` names the operation modes (register 33) in which the stat
    honours the write at all - outside them the entity reports unavailable,
    which is more honest than accepting a write the stat ignores.
    """

    on: int
    off: int
    requires_mode: tuple[int, ...] | None = None
    device_class: str | None = None
    category: str | None = "config"
    enabled: bool = True


_PROGRAM_MODE = SelectSpec(
    {0: "5/2 day", 1: "7 day", 2: "24 hour", 3: "Non-programmable"}
)
# The same legend, for the `get_schedule` response - which reports the program
# mode because it is what decides how many of the seven day blocks are
# independent, and so what a schedule editor may offer.
PROGRAM_MODE_LABELS = _PROGRAM_MODE.options
_DST = SwitchSpec(on=1, off=0)

NUMBERS: dict[str, dict[int, NumberSpec]] = {
    MODEL_HEAT: {
        REG_OUTPUT_DELAY: NumberSpec(0, 15, 1, unit="min"),
        # Manual: "00 - 10 degC (0-18 degF)", Value = Settemperature x 10.
        REG_UPDOWN_LIMIT: NumberSpec(0, 10, 0.5, 0, 18, 1),
        REG_FLOOR_LIMIT: NumberSpec(20, 45, 0.5, 68, 113, 1),
        REG_ADVANCED_SETPOINT: NumberSpec(
            SETPOINT_MIN_C, SETPOINT_MAX_C, SETPOINT_STEP_C,
            SETPOINT_MIN_F, SETPOINT_MAX_F, SETPOINT_STEP_F,
            enabled=False,
        ),
        REG_FROST_SETPOINT: NumberSpec(7, 17, 0.5, 45, 63, 1),
        # The hold duration packs hours in the high byte and minutes in the low
        # byte. It ships as ONE entity in minutes: two entities writing one
        # register is a read-modify-write race, and 210 is what an automation
        # means by "3h30m". 99h59m is the manual's maximum.
        REG_HOLD_DURATION: NumberSpec(0, 5999, 1, unit="min"),
    },
    MODEL_TIMER: {
        REG_HOLD_DURATION: NumberSpec(0, 5999, 1, unit="min"),
    },
}

SELECTS: dict[str, dict[int, SelectSpec]] = {
    MODEL_HEAT: {
        # The manual gives degC values 0.5/1/2/3 with "Value = Settemperature x
        # 10", so 5/10/20/30 on the wire, and degF labels 1/2/4/6. Whether the
        # *wire* values change in degF mode is NOT stated - see CLAUDE.md.
        REG_SWITCHING_DIFFERENTIAL: SelectSpec(
            {5: "0.5 °C", 10: "1 °C", 20: "2 °C", 30: "3 °C"},
            {5: "1 °F", 10: "2 °F", 20: "4 °F", 30: "6 °F"},
        ),
        REG_SENSOR_SELECTION: SelectSpec(
            {
                0: "Built-in and remote air",
                1: "Remote air only",
                2: "Remote floor only",
                3: "Floor, built-in and remote air",
                4: "Floor and remote only",
            }
        ),
        REG_OPTIMUM_START: SelectSpec(
            {
                0: "Disabled",
                1: "1 hour",
                2: "2 hours",
                3: "3 hours",
                4: "4 hours",
                5: "5 hours",
            }
        ),
        REG_PROGRAM_TYPE: SelectSpec({0: "4 period", 1: "6 period"}),
        REG_PROGRAM_MODE: _PROGRAM_MODE,
        REG_OPERATION_MODE: SelectSpec(OPERATION_MODES[MODEL_HEAT], category=None),
    },
    MODEL_TIMER: {
        REG_PROGRAM_MODE: _PROGRAM_MODE,
        REG_OPERATION_MODE: SelectSpec(OPERATION_MODES[MODEL_TIMER], category=None),
    },
}

SWITCHES: dict[str, dict[int, SwitchSpec]] = {
    MODEL_HEAT: {
        REG_DST_ENABLED: _DST,
    },
    MODEL_TIMER: {
        REG_DST_ENABLED: _DST,
        REG_ONOFF: SwitchSpec(on=1, off=0, device_class="switch", category=None),
        # "Timer Out force ... In the Hold and Advanced mode": the stat only
        # honours this in modes 2 and 3, so it goes unavailable elsewhere.
        REG_TIMER_OUTPUT_FORCE: SwitchSpec(
            on=1,
            off=0,
            requires_mode=(MODE_HOLD, MODE_ADVANCED),
            device_class="switch",
            category=None,
        ),
    },
}

# Read-only registers that are boolean by documented evidence, mapped to their
# HA device class (None = a plain on/off with no class).
BINARY: dict[str, dict[int, str | None]] = {
    MODEL_HEAT: {
        REG_RELAY: "heat",
        REG_WINDOW: "window",
        REG_DST_ACTIVE: None,
    },
    MODEL_TIMER: {
        REG_RELAY: "running",
        REG_TIMER_DST_ACTIVE: None,
    },
}

# Writable, but the write semantics are not established by the manual, so they
# ship read-only until hardware confirms them. CTC's `READ_ONLY_RW` holding pen.
#
# 21 (Temperature format): writable, but changing the stat's display unit from
#    Home Assistant would change the native unit of every temperature entity on
#    the device at runtime, which restarts their long-term statistics. It is a
#    reading, not a control.
# 31 (Communications ID): writing it moves the stat to another address mid-poll
#    and orphans the HA device. It will never be promoted.
# 42 (Keylock password): "Cancel Keylock (Value = 0), General PassWord: 6343"
#    does not establish that writing 6343 *locks*. A wrong write could set a
#    password the owner cannot clear from the keypad.
# 39-41 (Away until): setting Away coherently means writing the time, the date
#    and the year *together*. Three separate numbers would leave the thermostat
#    on a half-changed deadline in between, so this becomes one datetime entity
#    over FC16 in v2 - and reads only until then.
_AWAY_READBACK = {
    REG_AWAY_TIME: "sensor",
    REG_AWAY_DATE: "sensor",
    REG_AWAY_YEAR: "sensor",
}
READ_ONLY_RW: dict[str, dict[int, str]] = {
    MODEL_HEAT: {
        REG_TEMP_FORMAT: "sensor",
        REG_COMMS_ID: "sensor",
        REG_KEYLOCK: "binary_sensor",
        **_AWAY_READBACK,
    },
    MODEL_TIMER: {REG_COMMS_ID: "sensor", **_AWAY_READBACK},
}

# Registers that produce no entity of their own at all.
#
# Heat 7/8/9/32/33/34 are climate-owned: 8 and 9 are read-only mirrors of the
# writable 32 and 33, and shipping both would put two entities on one concept.
# (Timer has no climate, so its 32/33/34 *are* entities and only its 3/9 mirrors
# are suppressed.) 46 is a hazard - see CLAUDE.md. 47-50 are the RTC: they read
# back 0xFFFF once the stat has synced, so there is no state for an entity to
# hold, and the `set_time` action writes all four in one FC16 instead.
#
# 43 and 44 (TPI, and TPI minimum on time) ship as nothing at all, which is
# stronger than the READ_ONLY_RW holding pen and is deliberate. Hardware
# 2026-08-13: both read **20**, against documented ranges of 0-3 and 0-5, on a
# correctly-aligned block (42 reads 0xFFFF and 47-50 read 0xFFFF either side of
# them). So we do not know what the values mean. Worse, the EDGE keypad menu has
# no TPI entry at all - its 14 features map to registers 21-27, 13 and 28-31 -
# so these two are reachable *only* over Modbus, and a wrong write cannot be
# undone by the person standing at the thermostat. That is register 42's
# argument, and it applies harder here: 42 at least reads back something we can
# show. Even the readings are meaningless until the encoding is known, so there
# is nothing worth putting on a dashboard either.
_NEVER_SHIPPED = frozenset(
    {REG_TPI, REG_TPI_MIN_ON, REG_FACTORY_RESET, *range(REG_RTC, REG_RTC + RTC_BLOCK)}
)
SUPPRESSED: dict[str, frozenset[int]] = {
    # The climate entity owns these six, and 8 and 9 are read-only mirrors of
    # the writable 32 and 33.
    MODEL_HEAT: frozenset(
        {
            REG_CURRENT_SETPOINT,
            REG_ONOFF_READBACK,
            REG_MODE_READBACK,
            REG_ONOFF,
            REG_OPERATION_MODE,
            REG_HOLD_SETPOINT,
        }
    )
    | _NEVER_SHIPPED,
    # A Timer has no climate entity, so only its mirrors go.
    MODEL_TIMER: frozenset({REG_TIMER_ONOFF_READBACK, REG_MODE_READBACK})
    | _NEVER_SHIPPED,
}

# Read-only registers with a documented legend become ENUM sensors: "Period 2"
# beats "2", and a select would let you write a state the stat computes itself.
_SCHEDULE_PERIODS_6 = {
    0: "None",
    1: "Period 1",
    2: "Period 2",
    3: "Period 3",
    4: "Period 4",
    5: "Period 5",
    6: "Period 6",
}
_SCHEDULE_PERIODS_4 = {k: v for k, v in _SCHEDULE_PERIODS_6.items() if k <= 4}

ENUMS: dict[str, dict[int, dict[int, str]]] = {
    MODEL_HEAT: {
        REG_CURRENT_PERIOD: _SCHEDULE_PERIODS_6,
        REG_NEXT_PERIOD: _SCHEDULE_PERIODS_6,
        REG_TEMP_FORMAT: {0: "Celsius", 1: "Fahrenheit"},
    },
    MODEL_TIMER: {
        REG_TIMER_CURRENT_PERIOD: _SCHEDULE_PERIODS_4,
        REG_TIMER_NEXT_PERIOD: _SCHEDULE_PERIODS_4,
    },
}

# Read-only registers that ship disabled: bookkeeping, or an accessory most
# installs do not fit - the floor and remote probes (an unconnected one reads 0,
# which decode.py turns into unknown rather than a fictional 0.0 degC) and the
# window contact at 6, which is a separate part and reads a permanent "closed"
# without one.
_AWAY = frozenset({REG_AWAY_TIME, REG_AWAY_DATE, REG_AWAY_YEAR})
DISABLED_BY_DEFAULT: dict[str, frozenset[int]] = {
    MODEL_HEAT: frozenset(
        {
            REG_FLOOR_TEMP,
            REG_REMOTE_TEMP,
            REG_WINDOW,
            REG_DST_ACTIVE,
            REG_RATE_OF_CHANGE,
            REG_BOARD_TEMP_RAW,
            REG_BOARD_TEMP,
            REG_TEMP_FORMAT,
            REG_COMMS_ID,
            REG_KEYLOCK,
        }
    )
    | _AWAY,
    MODEL_TIMER: frozenset({REG_TIMER_DST_ACTIVE, REG_COMMS_ID}) | _AWAY,
}

# Temperature registers holding a *difference*, not a reading. They must not
# carry a temperature device class: Home Assistant would convert 5 °C to 41 °F,
# which is right for a temperature and wrong for a 5-degree span.
TEMPERATURE_DELTA = frozenset({REG_UPDOWN_LIMIT})

# Read-only registers in the diagnostic entity category.
DIAGNOSTIC: dict[str, frozenset[int]] = {
    MODEL_HEAT: frozenset(
        {
            REG_FIRMWARE,
            REG_CURRENT_PERIOD,
            REG_NEXT_PERIOD,
            REG_DST_ACTIVE,
            REG_RATE_OF_CHANGE,
            REG_BOARD_TEMP_RAW,
            REG_BOARD_TEMP,
            REG_TEMP_FORMAT,
            REG_COMMS_ID,
            REG_KEYLOCK,
        }
    )
    | _AWAY,
    MODEL_TIMER: frozenset(
        {
            REG_FIRMWARE,
            REG_TIMER_CURRENT_PERIOD,
            REG_TIMER_NEXT_PERIOD,
            REG_TIMER_DST_ACTIVE,
            REG_COMMS_ID,
        }
    )
    | _AWAY,
}

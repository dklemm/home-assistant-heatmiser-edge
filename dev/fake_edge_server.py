#!/usr/bin/env python3
"""A fake RS485 bus of EDGE thermostats, over TCP.

Serves three units on one port, which is the whole point: the integration's hard
parts are all *multi-unit* — per-thermostat availability, the shared bus lock,
telling a Heat from a Timer — and none of them can be exercised against a
single-device simulator.

    python dev/fake_edge_server.py                 # 1=Heat, 2=Timer, 3=Heat
    python dev/fake_edge_server.py --offset 0      # pretend the wire is 1-based
    python dev/fake_edge_server.py --silent 2      # unit 2 never answers
    python dev/fake_edge_server.py --fahrenheit 1  # unit 1 displays °F

`--offset` is the important one. Nothing in the manual says whether register N
lives at wire address N or N-1, so `detect.py` probes for it — and this flag is
what lets that detection be tested against both conventions before anyone points
it at real hardware.

It fakes just enough thermostat behaviour to be honest about writes: setting the
override temperature moves the live setpoint, and the read-only mirrors of the
on/off and mode registers follow their writable twins. Without that the climate
card appears to snap back after every change, which looks exactly like a bug and
is not one.

Two ways it is still not a thermostat, both harmless but worth knowing: an
unknown unit id gets an immediate Modbus exception response where a real absent
stat is simply silent (so discovery is faster here than on a real bus), and
nothing simulates the 50 ms turnaround the manual requires.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pymodbus.server import StartAsyncTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.heatmiser_edge.const import (  # noqa: E402
    MODEL_HEAT,
    MODEL_TIMER,
    POLL_START,
    REG_RTC,
    RTC_BLOCK,
)
from custom_components.heatmiser_edge.registers import SCHEDULES  # noqa: E402

DEFAULT_PORT = 5020

# Values chosen so `detect.guess_model` separates the two variants by a wide
# margin against a real socket, not just in unit tests.
_HEAT_SEED = {
    1: 42,  # firmware version
    2: 1,  # relay on
    3: 205,  # room 20.5 °C
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
    28: 1,  # 6 periods a day, matching the six-period grid seeded below
    29: 1,  # 7 day program
    32: 1,  # thermostat on
    33: 1,  # schedule mode
    34: 210,
    35: 210,
    37: 120,  # frost 12.0 °C
    43: 1,  # TPI, 3 cycles/hour
    44: 1,
    47: 0xFFFF,  # the RTC block's documented "already synced" marker
    48: 0xFFFF,
    49: 0xFFFF,
    50: 0xFFFF,
}

_TIMER_SEED = {
    1: 38,
    2: 1,  # output relay on
    3: 1,  # read-only mirror of 32
    4: 2,
    5: 3,
    9: 1,  # read-only mirror of 33
    29: 1,
    32: 1,
    33: 1,
    34: 0,
    47: 0xFFFF,
    48: 0xFFFF,
    49: 0xFFFF,
    50: 0xFFFF,
}

# The manual's schedule defaults. Heat is (hour, minute, settemp); the weekend
# runs a later, simpler day than Monday-Friday.
_HEAT_WEEKDAY = ((7, 0, 210), (9, 0, 160), (16, 0, 210), (22, 0, 160), (24, 0, 210), (24, 0, 160))
_HEAT_WEEKEND = ((9, 0, 210), (22, 0, 160), (24, 0, 210), (24, 0, 160), (24, 0, 210), (24, 0, 160))
# Timer is (on hour, on min, off hour, off min), the same every day.
_TIMER_DAY = ((7, 0, 9, 0), (16, 0, 20, 0), (24, 0, 24, 0), (24, 0, 24, 0))


def _schedule_words(model: str) -> dict[int, int]:
    """The manual's default weekly program, as {register: word}.

    Real defaults rather than zeros, because a grid of zeros reads as six
    periods all starting at 00:00 - a day that does not run forwards, which
    `heatmiser_edge.set_schedule` refuses outright. The fake bus has to hold a
    program the integration would accept, or every schedule edit made against
    it fails for a reason the hardware would never give.
    """
    layout = SCHEDULES[model]
    words: dict[int, int] = {}
    for day in range(7):  # 0 = Sunday, the manual's order
        if model == MODEL_HEAT:
            periods = _HEAT_WEEKEND if day in (0, 6) else _HEAT_WEEKDAY
        else:
            periods = _TIMER_DAY
        for index, values in enumerate(periods, start=1):
            base = layout.register(day, index, layout.fields[0])
            for step, value in enumerate(values):
                words[base + step] = value
    return words


def build_words(model: str, unit_id: int, fahrenheit: bool = False) -> dict[int, int]:
    """One thermostat's full register set, in manual register numbers."""
    layout = SCHEDULES[model]
    words = {n: 0 for n in range(POLL_START, layout.last_register + 1)}
    words.update(_HEAT_SEED if model == MODEL_HEAT else _TIMER_SEED)
    words.update(_schedule_words(model))
    # Register 31 always holds the id it is addressed by. This is what makes the
    # register-offset probe self-verifying, so the simulator must honour it.
    words[31] = unit_id
    if fahrenheit and model == MODEL_HEAT:
        words[21] = 1
        # Every temperature register is in the stat's own unit, so switching to
        # °F changes the stored numbers, not just the label.
        for register in (3, 7, 15, 16, 26, 34, 35, 37):
            words[register] = round(words[register] / 10 * 9 / 5 + 32) * 10
    return words


# Writing the register on the left makes the thermostat update the one on the
# right. The manual documents 8 and 9 as read-only copies of 32 and 33, and
# calls 34 the "Over right and Hold Set temperature" - an override of the live
# setpoint at register 7. A Timer has none of these: its 7 and 8 are Reserved,
# and its own mirrors are at 3 and 9.
_MIRRORS = {
    MODEL_HEAT: {32: (8,), 33: (9,), 34: (7,)},
    MODEL_TIMER: {32: (3,), 33: (9,)},
}


def make_action(model: str, offset: int):
    """A write hook: mirrored registers, the relay, and the RTC's consume-on-sync."""
    mirrors = _MIRRORS[model]

    async def action(function_code, start_address, address, count, registers, values):
        if values is None:  # a read; nothing to mirror
            return None

        def word(register: int) -> int:
            return registers[register + offset - start_address]

        def put(register: int, value: int) -> None:
            registers[register + offset - start_address] = value

        written = set()
        for index, value in enumerate(values):
            register = address + index - offset  # back to a manual register number
            written.add(register)
            for target in mirrors.get(register, ()):
                put(target, value)

        # The relay follows the setpoint, so that hvac_action and the Heating
        # binary sensor have something to actually do. Without it register 2 is
        # a constant, and every "is the boiler on" surface in Home Assistant
        # looks frozen rather than merely untested.
        #
        # A crude model on purpose: on when the live setpoint is above the room,
        # off otherwise, and off outright when the stat is. The switching
        # differential and any TPI cycling are not simulated - what is being
        # exercised here is the plumbing, not the control loop.
        #
        # Read through the *mirrors* (8 for on/off, 7 for the setpoint), not the
        # registers the request wrote. Same reason the mirrors are written to
        # `registers` in the first place: this hook runs before the store is
        # updated, so an address the request covers still reads its old value.
        if model == MODEL_HEAT and written & {32, 34}:
            put(2, 1 if word(8) and word(7) > word(3) else 0)
        # "The value automatic assignment 0xffff when after the success of the
        # RTC synchronization" - so a complete timestamp is swallowed and leaves
        # the marker behind. A partial write is left as written, which is what a
        # torn one would look like on real hardware.
        #
        # Rewriting `values` rather than `registers`, because the action runs
        # *before* the store is updated: anything written to `registers` at an
        # address the request also covers is about to be overwritten. That is
        # why the mirrors above work - they are addresses the request does not
        # touch.
        if written >= set(range(REG_RTC, REG_RTC + RTC_BLOCK)):
            for index in range(len(values)):
                if REG_RTC <= address + index - offset < REG_RTC + RTC_BLOCK:
                    values[index] = 0xFFFF
        return None

    return action


def build_device(model: str, unit_id: int, offset: int, fahrenheit: bool) -> SimDevice:
    """A SimDevice whose wire addresses reflect the chosen register base."""
    words = build_words(model, unit_id, fahrenheit)
    highest = max(words)
    # offset -1: manual register N is at wire N-1, so manual 1 starts at wire 0.
    # offset  0: the firmware numbers the wire exactly as the manual does.
    start = POLL_START + offset
    values = [words.get(n, 0) for n in range(POLL_START, highest + 1)]
    return SimDevice(
        unit_id,
        simdata=[SimData(start, values=values, datatype=DataType.REGISTERS)],
        action=make_action(model, offset),
    )


async def serve(args: argparse.Namespace) -> None:
    layout = [(1, MODEL_HEAT), (2, MODEL_TIMER), (3, MODEL_HEAT)]
    devices = [
        build_device(model, unit_id, args.offset, unit_id in args.fahrenheit)
        for unit_id, model in layout
        if unit_id not in args.silent
    ]
    described = ", ".join(
        f"{unit_id}={model}"
        + (" (silent)" if unit_id in args.silent else "")
        + (" °F" if unit_id in args.fahrenheit else "")
        for unit_id, model in layout
    )
    base = "1-based (manual N at wire N)" if args.offset == 0 else "0-based (manual N at wire N-1)"
    print(f"Fake EDGE bus on {args.host}:{args.port} — units {described}")
    print(f"Register base: {base}")
    await StartAsyncTcpServer(devices, address=(args.host, args.port))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--offset",
        type=int,
        choices=(-1, 0),
        default=-1,
        help="the firmware's register base: -1 = standard 0-based wire, 0 = 1-based",
    )
    parser.add_argument(
        "--silent",
        type=int,
        action="append",
        default=[],
        metavar="UNIT",
        help="drop a unit from the bus, to exercise per-thermostat availability",
    )
    parser.add_argument(
        "--fahrenheit",
        type=int,
        action="append",
        default=[],
        metavar="UNIT",
        help="set register 21 on a unit so it reports in °F",
    )
    args = parser.parse_args()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Field CLI for a Heatmiser EDGE RS485 bus.

The tool to reach for *before* Home Assistant, on a bus you have never talked to.
It imports the integration's own `hub`, `registers`, `decode` and `detect`, so
whatever it prints is exactly what the integration will see — and it needs no
Home Assistant install to run.

    # over a USB RS485 adapter
    python dev/edge_modbus_test.py --port /dev/ttyUSB0 detect
    python dev/edge_modbus_test.py --port /dev/ttyUSB0 scan

    # over an RS485-to-Ethernet gateway (try both framers if nothing answers)
    python dev/edge_modbus_test.py --host 10.0.0.5 --framer rtu scan
    python dev/edge_modbus_test.py --host 127.0.0.1 --tcp-port 5020 dump --unit 1

`detect` and `scan` earn their keep on day one: they answer the two questions the
manual does not — whether the wire is 0-based, and whether a given stat is a Heat
or a Timer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.heatmiser_edge.const import (  # noqa: E402
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    FRAMER_RTU,
    FRAMER_SOCKET,
    MODEL_HEAT,
    MODEL_TIMER,
    OPERATION_MODES,
    POLL_COUNT,
    POLL_START,
    PROGRAM_MODE_LABELS,
    REG_DST_ENABLED,
    REG_PROGRAM_MODE,
    REG_PROGRAM_TYPE,
    REG_RTC,
    RTC_BLOCK,
    DEFAULT_REGISTER_OFFSET,
    REGISTER_OFFSETS,
    SCAN_TIMEOUT,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP,
)
from custom_components.heatmiser_edge.decode import (  # noqa: E402
    decode_value,
    encode_rtc,
)
from custom_components.heatmiser_edge.detect import (  # noqa: E402
    guess_model,
    parse_id_list,
)
from custom_components.heatmiser_edge.hub import (  # noqa: E402
    EdgeConnectionError,
    EdgeHub,
)
from custom_components.heatmiser_edge.registers import (  # noqa: E402
    SCHEDULES,
    reg,
    registers_for,
)
from custom_components.heatmiser_edge.schedule import (  # noqa: E402
    format_week,
    usable_periods,
)

# Writing these on a stat you care about is a bad day. 46 wipes it back to
# factory; 42 can set a keypad password the owner cannot clear; 31 moves the
# stat to a different address and orphans it.
DESTRUCTIVE = {46: "restores factory settings — everything is wiped"}
RISKY = {
    31: "moves this thermostat to a different Modbus address",
    42: "sets the keypad lock password",
}


def build_hub(args: argparse.Namespace, timeout: float | None = None) -> EdgeHub:
    offset = int(args.offset)
    if args.port:
        return EdgeHub(
            transport=TRANSPORT_SERIAL,
            serial_port=args.port,
            baudrate=args.baud,
            bytesize=args.bytesize,
            parity=args.parity,
            stopbits=args.stopbits,
            timeout=timeout or args.timeout,
            register_offset=offset,
        )
    return EdgeHub(
        transport=TRANSPORT_TCP,
        host=args.host,
        port=args.tcp_port,
        framer=args.framer,
        timeout=timeout or args.timeout,
        register_offset=offset,
    )


async def cmd_detect(args: argparse.Namespace) -> int:
    """Print register 31 at both candidate bases, so a human can read it off.

    Register 31 is the id we addressed, so the base it reads back correctly under
    is the right one. Run this when the integration warns that a stat disagrees.
    """
    hub = build_hub(args, timeout=SCAN_TIMEOUT)
    unit_ids = parse_id_list(args.ids)
    try:
        await hub.async_connect()
        print(f"Probing manual registers 30-34 on ids {args.ids}\n")
        print(f"{'unit':>4}  {'offset':>7}  {'reg31':>6}  verdict")
        answered = False
        for unit_id in unit_ids:
            for candidate in REGISTER_OFFSETS:
                hub.register_offset = candidate
                words = await hub.async_probe_unit(unit_id)
                if words is None:
                    continue
                answered = True
                reads_own_id = words.get(31) == unit_id
                mark = "<-- register 31 reads its own id" if reads_own_id else ""
                print(f"{unit_id:>4}  {candidate:>7}  {words.get(31, 0):>6}  {mark}")
        if not answered:
            print("\nNo thermostat answered at either offset.")
            print("Check: A/B polarity, bus termination, the stat's Communications ID")
            print("(0 disables Modbus entirely), and on a gateway try --framer rtu.")
            return 1
        print("\nA unit id of 6 or above is decisive: nothing else in the 30-34")
        print("window can hold a value that high. On id 1 both bases can read 1.")
        return 0
    finally:
        await hub.async_close()


async def cmd_scan(args: argparse.Namespace) -> int:
    """Sweep the bus: which ids answer, and what each one looks like."""
    hub = build_hub(args, timeout=SCAN_TIMEOUT)
    unit_ids = parse_id_list(args.ids)
    try:
        await hub.async_connect()
        print(f"Register offset: {hub.register_offset}\n")
        print(f"{'unit':>4}  {'model':>6}  {'heat':>4}  {'timer':>5}  {'conf':>4}  firmware  note")
        found = 0
        for unit_id in unit_ids:
            words = await hub.async_read_block(unit_id, POLL_START, POLL_COUNT)
            if words is None:
                continue
            found += 1
            guess = guess_model(words)
            note = "" if guess.confident else "LOW CONFIDENCE — confirm by hand"
            print(
                f"{unit_id:>4}  {guess.model:>6}  {guess.heat:>4}  {guess.timer:>5}  "
                f"{guess.confidence:>4}  {words.get(1, 0):>8}  {note}"
            )
        print(f"\n{found} thermostat(s) on {hub.label}")
        return 0 if found else 1
    finally:
        await hub.async_close()


async def cmd_dump(args: argparse.Namespace) -> int:
    """Every register of one thermostat, raw and decoded."""
    hub = build_hub(args)
    try:
        await hub.async_connect()
        words = await hub.async_read_block(args.unit, POLL_START, POLL_COUNT)
        if words is None:
            print(f"Unit {args.unit} did not answer.")
            return 1
        model = args.model or guess_model(words).model
        fahrenheit = model == MODEL_HEAT and words.get(21) == 1
        print(f"Unit {args.unit} — {model}, offset {hub.register_offset}, "
              f"{'°F' if fahrenheit else '°C'}\n")
        if args.raw:
            for number in sorted(words):
                print(f"{number:>3}  {words[number]:>6}  0x{words[number]:04X}")
            return 0
        print(f"{'reg':>3}  {'raw':>6}  {'value':>12}  name")
        for register in registers_for(model):
            raw = words.get(register.number)
            value = decode_value(register, raw, fahrenheit)
            if register.number == 33 and isinstance(value, int):
                value = OPERATION_MODES[model].get(value, value)
            print(
                f"{register.number:>3}  {raw if raw is not None else '-':>6}  "
                f"{str(value):>12}  {register.name}"
            )
        return 0
    finally:
        await hub.async_close()


async def cmd_schedule(args: argparse.Namespace) -> int:
    """The weekly program, as a grid. Read-only.

    Three FC03 packets on a Heat, so it is its own command rather than part of
    `dump` — the register base is settled long before anyone needs this, and
    paying 0.6 s for it on every dump would be a poor trade.

    This is the instrument for the open question in CLAUDE.md about 5/2 and 24
    Hour mode: change a weekday time from the keypad, run this, and see which
    day blocks moved.
    """
    hub = build_hub(args)
    try:
        await hub.async_connect()
        block = await hub.async_read_block(args.unit, POLL_START, POLL_COUNT)
        if block is None:
            print(f"Unit {args.unit} did not answer.")
            return 1
        model = args.model or guess_model(block).model
        fahrenheit = model == MODEL_HEAT and block.get(21) == 1
        layout = SCHEDULES[model]
        words = await hub.async_read_span(
            args.unit, layout.base, layout.last_register - layout.base + 1
        )
        if words is None:
            print(f"Unit {args.unit} went silent reading its program.")
            return 1

        periods = usable_periods(model, block.get(REG_PROGRAM_TYPE))
        mode = PROGRAM_MODE_LABELS.get(block.get(REG_PROGRAM_MODE), "?")
        print(
            f"Unit {args.unit} — {model}, registers {layout.base}-"
            f"{layout.last_register}, {periods} periods a day, program mode {mode}\n"
        )
        for day, rows in format_week(model, words, fahrenheit).items():
            # The rows past register 28's allowance are still printed, in
            # brackets: they hold values, and knowing what is in them is the
            # whole reason for looking.
            shown = [
                (f"[{_row(model, row)}]" if row["period"] > periods else _row(model, row))
                for row in rows
            ]
            print(f"{day:>10}  " + "  ".join(shown))
        return 0
    finally:
        await hub.async_close()


def _row(model: str, row: dict) -> str:
    if model == MODEL_HEAT:
        if row["time"] is None:
            return "  --   ---- "
        return f"{row['time']} {row['temperature']:>5}°"
    if row["on"] is None:
        return "  --  -  --  "
    return f"{row['on']}-{row['off']}"


async def cmd_read(args: argparse.Namespace) -> int:
    hub = build_hub(args)
    try:
        await hub.async_connect()
        words = await hub.async_read_block(args.unit, args.register, 1)
        if words is None:
            print(f"Unit {args.unit} did not answer.")
            return 1
        raw = words[args.register]
        model = args.model or MODEL_HEAT
        register = reg(model, args.register)
        decoded = decode_value(register, raw) if register else raw
        name = register.name if register else "(not in the map)"
        print(f"register {args.register}  raw {raw} (0x{raw:04X})  -> {decoded}  {name}")
        return 0
    finally:
        await hub.async_close()


async def cmd_poll(args: argparse.Namespace) -> int:
    hub = build_hub(args)
    try:
        await hub.async_connect()
        while True:
            words = await hub.async_read_block(args.unit, args.register, 1)
            value = "silent" if words is None else words[args.register]
            print(f"register {args.register}: {value}", flush=True)
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        await hub.async_close()


async def cmd_write(args: argparse.Namespace) -> int:
    """FC06, with the destructive registers behind a deliberate gate."""
    if args.register in DESTRUCTIVE and not args.i_mean_it:
        print(f"Register {args.register} {DESTRUCTIVE[args.register]}.")
        print("Pass --i-mean-it if that is genuinely what you want.")
        return 2
    if args.register in RISKY:
        print(f"WARNING: register {args.register} {RISKY[args.register]}.")
    hub = build_hub(args)
    try:
        await hub.async_connect()
        await hub.async_write_register(args.unit, args.register, args.value)
        # Read back: the stat may clamp an out-of-range value without saying so.
        words = await hub.async_read_block(args.unit, args.register, 1)
        kept = "unknown" if words is None else words[args.register]
        print(f"wrote {args.value} to register {args.register}; it now reads {kept}")
        if words is not None and kept != args.value:
            print("The thermostat clamped or ignored the value.")
        return 0
    except EdgeConnectionError as err:
        print(f"Write failed: {err}")
        return 1
    finally:
        await hub.async_close()


async def cmd_settime(args: argparse.Namespace) -> int:
    """FC16 the RTC block, and show what the stat did with it.

    The read-back is the interesting part on hardware: the manual says all four
    registers become 0xFFFF once the stat has taken the time, so seeing that is
    the only confirmation available - there is no readable clock.
    """
    when = (
        datetime.now()
        if args.datetime is None
        else datetime.fromisoformat(args.datetime)
    )
    hub = build_hub(args)
    try:
        await hub.async_connect()
        if args.dst is not None:
            await hub.async_write_register(args.unit, REG_DST_ENABLED, args.dst)
            print(f"daylight saving (register 30) set to {args.dst}")
        words = encode_rtc(when)
        await hub.async_write_registers(args.unit, REG_RTC, words)
        print(f"wrote {when:%Y-%m-%d %H:%M:%S} as {[hex(w) for w in words]}")
        read = await hub.async_read_block(args.unit, REG_RTC, RTC_BLOCK)
        if read is None:
            print("The thermostat did not answer the read-back.")
            return 1
        kept = [read[n] for n in range(REG_RTC, REG_RTC + RTC_BLOCK)]
        print(f"registers 47-50 now read {[hex(w) for w in kept]}")
        if all(w == 0xFFFF for w in kept):
            print("All 0xFFFF — the manual's marker for a successful sync.")
        else:
            print("Not yet 0xFFFF. Either the sync failed or it is still pending;")
            print("check the time on the keypad before trusting this.")
        return 0
    except EdgeConnectionError as err:
        print(f"Write failed: {err}")
        return 1
    finally:
        await hub.async_close()


def _warn_about_macos_tty(port: str | None) -> None:
    """On macOS the `tty.*` node is the wrong one, and it hangs rather than fails.

    `/dev/tty.*` is the *incoming* node: opening it waits on carrier detect, so
    with DTR deasserted it blocks for ever and looks exactly like a wedged tool.
    `/dev/cu.*` ("call-up") is the outbound node and is what a USB-RS485 adapter
    wants.

    Gated on darwin and matched on the `/dev/tty.` prefix - with the dot -
    because Linux's `/dev/ttyUSB0` is entirely correct and must not be flagged.
    """
    if sys.platform != "darwin" or not port or not port.startswith("/dev/tty."):
        return
    print(
        f"WARNING: on macOS use {port.replace('/dev/tty.', '/dev/cu.', 1)} "
        f"rather than {port}.\n"
        "         The tty.* node blocks on carrier detect and can hang for ever.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial device, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--bytesize", type=int, default=DEFAULT_BYTESIZE)
    parser.add_argument("--parity", default=DEFAULT_PARITY)
    parser.add_argument("--stopbits", type=int, default=DEFAULT_STOPBITS)
    parser.add_argument("--host", help="RS485-to-Ethernet gateway address")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument("--framer", choices=(FRAMER_SOCKET, FRAMER_RTU), default=FRAMER_RTU)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument(
        "--offset",
        default=str(DEFAULT_REGISTER_OFFSET),
        choices=tuple(str(o) for o in REGISTER_OFFSETS),
        help="register base (see `detect`)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_unit(p):
        """--unit and --model belong to the per-thermostat commands.

        They are not global options, because argparse will not accept a global
        option written *after* the subcommand - and "dump --unit 3" is exactly
        how anyone would type it.
        """
        p.add_argument("--unit", type=int, default=1)
        p.add_argument("--model", choices=(MODEL_HEAT, MODEL_TIMER), help="skip the guess")
        return p

    for name, handler, helptext in (
        ("detect", cmd_detect, "decide the register base, showing the evidence"),
        ("scan", cmd_scan, "sweep the bus for thermostats"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--ids", default="1-8", help='unit ids, e.g. "1-32" or "1-4,7"')
        p.set_defaults(handler=handler)

    p = with_unit(sub.add_parser("dump", help="every register of one thermostat"))
    p.add_argument("--raw", action="store_true", help="raw words only, no decoding")
    p.set_defaults(handler=cmd_dump)

    p = with_unit(sub.add_parser("schedule", help="the weekly program, as a grid"))
    p.set_defaults(handler=cmd_schedule)

    p = with_unit(sub.add_parser("read", help="one register"))
    p.add_argument("register", type=int)
    p.set_defaults(handler=cmd_read)

    p = with_unit(sub.add_parser("poll", help="re-read one register on an interval"))
    p.add_argument("register", type=int)
    p.add_argument("-i", "--interval", type=float, default=5.0)
    p.set_defaults(handler=cmd_poll)

    p = with_unit(sub.add_parser("write", help="write one register (FC06)"))
    p.add_argument("register", type=int)
    p.add_argument("value", type=int)
    p.add_argument("--i-mean-it", action="store_true")
    p.set_defaults(handler=cmd_write)

    p = with_unit(sub.add_parser("settime", help="sync the RTC, registers 47-50 (FC16)"))
    p.add_argument(
        "datetime",
        nargs="?",
        help="ISO 8601, e.g. 2026-08-12T14:30:00; defaults to this machine's clock",
    )
    p.add_argument("--dst", type=int, choices=(0, 1), help="also set register 30")
    p.set_defaults(handler=cmd_settime)

    args = parser.parse_args()
    if not args.port and not args.host:
        parser.error("give --port for serial or --host for a TCP gateway")
    _warn_about_macos_tty(args.port)
    try:
        return asyncio.run(args.handler(args))
    except EdgeConnectionError as err:
        print(f"Bus error: {err}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

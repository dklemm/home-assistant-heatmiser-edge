#!/usr/bin/env python3
"""Expose a real RS485 bus on a TCP port, so a container can reach it.

Docker Desktop on macOS runs its containers inside a Linux VM that has no view
of the host's USB tree, so a `devices:` mapping in docker-compose silently gives
the container nothing. This bridges that gap: it holds the serial port open on
the *host* and pumps bytes between it and one TCP client.

Nothing about the protocol changes. The integration talks to this exactly as it
would to a transparent RS485-to-Ethernet gateway - TCP transport with the **RTU**
framer, which it already supports - and the same Modbus RTU frames, CRC and all,
simply travel over a socket for the last hop.

    python dev/serial_tcp_bridge.py --port /dev/cu.usbserial-0001 --listen 5021

On Linux none of this is needed: pass the device straight through with
`devices:` in docker-compose.yml and use the serial transport.

**One client at a time, deliberately.** The bus is half-duplex and shared; two
overlapping clients produce collisions, not two answers. A second connection is
refused while the first is live rather than quietly interleaving with it.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading

import serial

DEFAULT_LISTEN = 5021


def _pump_serial_to_socket(ser: serial.Serial, conn: socket.socket, stop: threading.Event) -> None:
    """Serial -> socket, byte for byte.

    No framing, no buffering games: the RTU framer at the other end finds the
    frame boundaries itself, and anything clever here would only add latency to
    a 50 ms-paced bus.
    """
    while not stop.is_set():
        try:
            waiting = ser.in_waiting
            data = ser.read(waiting or 1)
        except (OSError, serial.SerialException) as err:
            print(f"  serial read failed: {err}", file=sys.stderr)
            stop.set()
            return
        if not data:
            continue
        try:
            conn.sendall(data)
        except OSError:
            stop.set()
            return


def _serve_one(ser: serial.Serial, conn: socket.socket, peer) -> None:
    print(f"  client connected from {peer[0]}:{peer[1]}")
    stop = threading.Event()
    reader = threading.Thread(
        target=_pump_serial_to_socket, args=(ser, conn, stop), daemon=True
    )
    reader.start()
    try:
        while not stop.is_set():
            data = conn.recv(4096)
            if not data:
                break
            # Drop anything the stats said before this request: it belongs to a
            # transaction that has already timed out, and letting it through
            # would desync the client's framer against the *next* reply.
            ser.reset_input_buffer()
            ser.write(data)
            ser.flush()
    except OSError as err:
        print(f"  client error: {err}", file=sys.stderr)
    finally:
        stop.set()
        conn.close()
        reader.join(timeout=1.0)
        print("  client disconnected")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="serial device")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--parity", default="N", choices=("N", "E", "O"))
    parser.add_argument("--bytesize", type=int, default=8)
    parser.add_argument("--stopbits", type=int, default=1)
    parser.add_argument("--listen", type=int, default=DEFAULT_LISTEN)
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        help="0.0.0.0 so Docker can reach it; 127.0.0.1 to keep it local",
    )
    args = parser.parse_args()

    if sys.platform == "darwin" and args.port.startswith("/dev/tty."):
        cu = args.port.replace("/dev/tty.", "/dev/cu.", 1)
        print(f"WARNING: on macOS use {cu} rather than {args.port}.", file=sys.stderr)
        print("         The tty.* node blocks on carrier detect.", file=sys.stderr)

    try:
        ser = serial.Serial(
            args.port,
            baudrate=args.baud,
            bytesize=args.bytesize,
            parity=args.parity,
            stopbits=args.stopbits,
            timeout=0.02,
        )
    except serial.SerialException as err:
        print(f"Could not open {args.port}: {err}", file=sys.stderr)
        return 1

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.listen))
    listener.listen(1)

    settings = f"{args.baud} {args.bytesize}{args.parity}{args.stopbits}"
    print(f"Bridging {args.port} ({settings}) <-> {args.bind}:{args.listen}")
    print("Point the integration at this as a TCP gateway with the RTU framer.")
    print("From a container: host.docker.internal:%d\n" % args.listen)

    try:
        while True:
            conn, peer = listener.accept()
            # Nagle would coalesce a reply with whatever follows it; on a bus
            # paced at 50 ms that is pure added latency.
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            _serve_one(ser, conn, peer)
    except KeyboardInterrupt:
        print("\nstopping")
        return 0
    finally:
        listener.close()
        ser.close()


if __name__ == "__main__":
    sys.exit(main())

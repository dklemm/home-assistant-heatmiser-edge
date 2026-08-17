"""All Modbus I/O for one RS485 bus.

Everything the wire cares about lives here, and nothing else touches pymodbus.
The coordinator asks for words and gets words.

What the bus imposes, and how each is handled:

- **It is half-duplex and shared by every thermostat.** One `asyncio.Lock`
  covers every transaction on every unit id, reads and writes alike. Two
  overlapping requests do not produce two answers, they produce a collision.
- **The manual requires >=50 ms between transactions.** Slept inside the lock,
  and only for the time actually remaining since the previous one ended - see
  `_transact`.
- **A packet may not exceed 60 registers**, so the v1 poll of registers 1-50 is
  exactly one FC03 per thermostat. There is no batching, no bisection and no
  dead-address cache here (unlike the CTC integration): both EDGE variants
  implement 1-50 contiguously, so a register never goes silent on its own. Only
  a whole *unit* does.
- **A missing thermostat costs a full timeout.** After three consecutive
  silences a unit drops to one attempt in five; one success restores it. A stat
  taken off the wall must not tax every poll for ever.
- **The register base is unknown until probed.** `wire()` is the single place
  the manual's 1-based numbers become wire addresses; `async_detect_offset`
  settles which. Every other module speaks manual register numbers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from .const import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DEFAULT_TIMEOUT,
    FRAMER_RTU,
    INTER_TRANSACTION_GAP,
    MAX_BLOCK,
    POLL_COUNT,
    POLL_START,
    TRANSPORT_SERIAL,
    UNIT_BACKOFF_AFTER,
    UNIT_BACKOFF_EVERY,
)
from .detect import OFFSETS, PROBE_COUNT, PROBE_START, resolve_offset, score_offset

_LOGGER = logging.getLogger(__name__)


class EdgeConnectionError(Exception):
    """The bus itself is unusable - the port is gone or the socket is refused.

    Deliberately *not* raised for a silent thermostat: one stat not answering is
    a device-availability fact, not a bus failure, and conflating the two would
    take every other stat down with it.
    """


class EdgeHub:
    """Owns the Modbus client. All thermostat I/O goes through here."""

    def __init__(
        self,
        *,
        transport: str,
        serial_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: int = DEFAULT_STOPBITS,
        host: str | None = None,
        port: int = DEFAULT_TCP_PORT,
        framer: str = FRAMER_RTU,
        timeout: float = DEFAULT_TIMEOUT,
        register_offset: int | None = None,
    ) -> None:
        self.transport = transport
        self.serial_port = serial_port
        self.host = host
        self.port = port
        self.framer = framer
        self.timeout = timeout
        # None means "not yet settled"; async_connect probes for it.
        self.register_offset = register_offset
        self.unit_failures: dict[int, int] = {}

        self._lock = asyncio.Lock()
        self._next_free = 0.0
        self._polls = 0

        if transport == TRANSPORT_SERIAL and not serial_port:
            raise ValueError("serial transport needs a serial port")
        if transport != TRANSPORT_SERIAL and not host:
            raise ValueError("tcp transport needs a host")

        self._serial_settings = (baudrate, bytesize, parity, stopbits)
        # Built on connect, not here: constructing a pymodbus client allocates a
        # socket, and a hub the config flow builds for a probe and then abandons
        # would leak one.
        self._client: Any = None

    def _make_client(self) -> Any:
        # retries=1: pymodbus defaults to 3, which triples the cost of every
        # silent unit. On a 9600-baud shared bus a timeout storm is the failure
        # mode that actually hurts.
        if self.transport == TRANSPORT_SERIAL:
            baudrate, bytesize, parity, stopbits = self._serial_settings
            return AsyncModbusSerialClient(
                self.serial_port,
                framer=FramerType.RTU,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=self.timeout,
                retries=1,
            )
        # Cheap RS485-to-Ethernet gateways split about evenly between real
        # Modbus TCP (MBAP header) and transparent RTU-over-TCP. Choosing wrong
        # looks exactly like a dead bus, so it is a setting.
        return AsyncModbusTcpClient(
            self.host,
            port=self.port,
            framer=(
                FramerType.RTU if self.framer == FRAMER_RTU else FramerType.SOCKET
            ),
            timeout=self.timeout,
            retries=1,
        )

    def __repr__(self) -> str:
        where = (
            self.serial_port
            if self.transport == TRANSPORT_SERIAL
            else f"{self.host}:{self.port}"
        )
        return f"<EdgeHub {self.transport} {where} offset={self.register_offset}>"

    @property
    def label(self) -> str:
        """How this bus is named in the UI and in diagnostics."""
        if self.transport == TRANSPORT_SERIAL:
            return str(self.serial_port)
        return f"{self.host}:{self.port}"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def async_connect(self, units: Sequence[int] | None = None) -> None:
        """Open the port or socket, and settle the register offset if unknown.

        Detection only runs when there are units to ask *and* no offset is
        stored. Passing no units leaves the offset unsettled on purpose, so a
        caller that wants to run detection itself - and report the evidence, as
        `dev/edge_modbus_test.py detect` does - is not pre-empted by a guess.
        """
        if self._client is None:
            self._client = self._make_client()
        if not await self._client.connect():
            raise EdgeConnectionError(f"Could not open {self.label}")
        if self.register_offset is None and units:
            await self.async_detect_offset(units)

    async def async_close(self) -> None:
        if self._client is not None:
            self._client.close()

    async def _async_reconnect(self) -> bool:
        """Re-open a port pymodbus closed underneath us.

        pymodbus counts *consecutive* silences and closes the connection when it
        passes `retries + 3` (`transaction.py`, `count_until_disconnect`), reset
        by any success. With `retries=1` that budget is 6 - and a discovery scan
        of ids 1-8 on a bus holding one thermostat produces far more silences
        than that in a row. The port is fine; only pymodbus's patience ran out.
        """
        if self._client is None:
            return False
        # Release the old handle first. pyserial takes an exclusive lock on the
        # device, so reconnecting without closing races the handle pymodbus has
        # not finished tearing down and fails with EAGAIN on the port.
        self._client.close()
        return bool(await self._client.connect())

    # ------------------------------------------------------------------
    # Addressing
    # ------------------------------------------------------------------

    def wire(self, register: int) -> int:
        """The manual's register number as a wire address.

        The only place the offset is applied. An unsettled offset defaults to
        the standard Modbus convention rather than raising: a probe has to be
        able to run before detection has an answer.
        """
        offset = self.register_offset if self.register_offset is not None else -1
        return register + offset

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    async def _transact(self, call: Callable[[], Awaitable[Any]]) -> Any:
        """Serialise the bus and honour the manual's >=50 ms gap.

        The gap is measured from the *end* of the previous transaction and slept
        inside the lock, for only the time actually remaining. Sleeping a flat
        50 ms would tax every request even when the caller already spent longer
        than that; sleeping outside the lock would let two waiters both read a
        stale `_next_free` and then fire into each other on a half-duplex wire.

        `_next_free` is updated in a `finally` so a *timed-out* unit still paces
        what follows: a stat that answers late must not step on the next unit's
        reply.

        A run of absent unit ids makes pymodbus close the port (see
        `_async_reconnect`), so one reconnect-and-retry happens here rather than
        at each call site - this is the single choke point every read and write
        already funnels through. Retrying is safe for writes too: every v1 write
        is one FC06 of one register, so re-sending it is idempotent.
        """
        loop = asyncio.get_running_loop()
        async with self._lock:
            remaining = self._next_free - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                try:
                    return await call()
                except ConnectionException:
                    # Exactly one retry, and still inside the lock so it cannot
                    # interleave with another unit. A genuinely dead port raises
                    # again and is reported as the bus failure it is.
                    if not await self._async_reconnect():
                        raise
                    return await call()
            finally:
                self._next_free = loop.time() + INTER_TRANSACTION_GAP

    async def async_read_block(
        self, unit_id: int, start: int, count: int
    ) -> dict[int, int] | None:
        """One FC03. Returns {manual register: word}, or None if unit was silent.

        None is genuinely "this thermostat did not answer" - the transport being
        down raises instead, because that is a different problem with a
        different fix.
        """
        if count > MAX_BLOCK:
            raise ValueError(
                f"{count} registers exceeds the manual's {MAX_BLOCK}-register packet limit"
            )

        async def call():
            return await self._client.read_holding_registers(
                self.wire(start), count=count, device_id=unit_id
            )

        try:
            result = await self._transact(call)
        except ConnectionException as err:
            raise EdgeConnectionError(f"{self.label} is not connected: {err}") from err
        except ModbusException as err:
            _LOGGER.debug("Unit %s did not answer: %s", unit_id, err)
            return None
        if result.isError():
            _LOGGER.debug("Unit %s returned an error: %s", unit_id, result)
            return None
        return {start + i: word for i, word in enumerate(result.registers)}

    async def async_read_span(
        self, unit_id: int, start: int, count: int
    ) -> dict[int, int] | None:
        """A contiguous range too long for one packet, as several FC03s.

        The manual caps a packet at 60 registers, and the weekly program is 168
        on a Heat - so unlike everything else here it cannot be one round trip.
        Three of them cost about 0.6 s of a 9600-baud bus, which is why the
        program is read on demand and never by the poll.

        A unit that goes silent part way returns None for the *whole* span
        rather than a partial answer: half a weekly program read as if it were
        whole is worse than no program at all.
        """
        words: dict[int, int] = {}
        address = start
        remaining = count
        while remaining > 0:
            chunk = min(remaining, MAX_BLOCK)
            block = await self.async_read_block(unit_id, address, chunk)
            if block is None:
                return None
            words.update(block)
            address += chunk
            remaining -= chunk
        return words

    async def async_write_register(
        self, unit_id: int, register: int, value: int
    ) -> None:
        """FC06 write-single - the only write v1 makes.

        The manual permits 06 and 16 for every writable register, and 06 is the
        better fit here: every v1 control writes exactly one register, and FC06's
        response echoes the address *and the value*, so a stat that silently
        clamps an out-of-range write tells us what it kept before the read-back
        even runs.
        """

        async def call():
            return await self._client.write_register(
                self.wire(register), value, device_id=unit_id
            )

        try:
            result = await self._transact(call)
        except ModbusException as err:
            raise EdgeConnectionError(
                f"Writing register {register} on unit {unit_id} failed: {err}"
            ) from err
        if result.isError():
            raise EdgeConnectionError(
                f"Unit {unit_id} rejected the write to register {register}"
            )

    async def async_write_registers(
        self, unit_id: int, register: int, values: list[int]
    ) -> None:
        """FC16 write-multiple, for registers that must move together.

        Two callers. The RTC block (47-50): written a register at a time the
        stat would sync to a torn timestamp. And one weekly-program period,
        which is 3 registers on a Heat and 4 on a Timer - a period's hour,
        minute and set temperature are one instruction, and `schedule.py`
        explains why the unit is a period and not the day's whole 24. The away
        deadline (39-41) is the case still to come.

        No *entity* uses this - every one of those writes a single register over
        FC06 - and a test asserts it, so that a control cannot quietly acquire a
        block write by accident.

        Note that FC16's response echoes only the address and the quantity, not
        the values, so unlike FC06 it cannot reveal a stat that silently clamps
        what it was sent. Callers verify by reading back.
        """
        if len(values) > MAX_BLOCK:
            raise ValueError(
                f"{len(values)} registers exceeds the manual's {MAX_BLOCK}-register limit"
            )

        async def call():
            return await self._client.write_registers(
                self.wire(register), values, device_id=unit_id
            )

        try:
            result = await self._transact(call)
        except ModbusException as err:
            raise EdgeConnectionError(
                f"Writing registers {register}+ on unit {unit_id} failed: {err}"
            ) from err
        if result.isError():
            raise EdgeConnectionError(
                f"Unit {unit_id} rejected the write to registers {register}+"
            )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def async_read_units(
        self, units: Sequence[int]
    ) -> dict[int, dict[int, int] | None]:
        """One 50-register block per thermostat, in id order.

        A unit that has been silent for a while is skipped most cycles, and a
        skipped unit reports None - the same as a silent one - so Home Assistant
        shows it unavailable rather than serving stale words.
        """
        self._polls += 1
        results: dict[int, dict[int, int] | None] = {}
        for unit_id in units:
            failures = self.unit_failures.get(unit_id, 0)
            if failures >= UNIT_BACKOFF_AFTER and self._polls % UNIT_BACKOFF_EVERY:
                results[unit_id] = None
                continue
            words = await self.async_read_block(unit_id, POLL_START, POLL_COUNT)
            if words is None:
                self.unit_failures[unit_id] = failures + 1
            else:
                self.unit_failures[unit_id] = 0
            results[unit_id] = words
        return results

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def async_probe_unit(
        self, unit_id: int, offset: int | None = None
    ) -> dict[int, int] | None:
        """Read the five discovery registers (manual 30-34) at a given offset.

        Five, not fifty, because a sweep of 32 ids pays a full timeout for every
        one that isn't there - and this window is where the discriminating
        values live: the communications id, the on/off flag and the mode.
        """
        previous = self.register_offset
        if offset is not None:
            self.register_offset = offset
        try:
            return await self.async_read_block(unit_id, PROBE_START, PROBE_COUNT)
        finally:
            self.register_offset = previous

    async def async_detect_offset(self, units: Sequence[int]) -> int:
        """Settle whether the wire is 0-based, using register 31 as the witness.

        Register 31 holds the communications id, which is by definition the id we
        addressed - so the offset that makes it read back correctly is the right
        one. Ambiguity is per-unit (see `detect.score_offset`), so every
        candidate unit votes and the majority wins.

        An id that is silent at the first candidate offset is not on the wire at
        all, and is not probed again: a thermostat that *is* present but does not
        implement an address answers with an exception response, not silence. So
        a second probe would only buy a second timeout - and on a mostly-empty
        bus those timeouts are what push pymodbus into closing the port.
        """
        votes: dict[int, int | None] = {}
        for unit_id in units:
            probes: dict[int, dict[int, int]] = {}
            for candidate in OFFSETS:
                words = await self.async_probe_unit(unit_id, candidate)
                if words is None:
                    probes.clear()
                    break
                probes[candidate] = words
            if probes:
                votes[unit_id] = score_offset(probes, unit_id)

        offset, decisive = resolve_offset(votes)
        if not decisive:
            _LOGGER.warning(
                "Could not confirm the register base on %s (no thermostat gave a "
                "decisive answer; a unit id of 6 or above always does). Assuming "
                "the standard 0-based convention - override it in the integration "
                "options if readings look shifted by one register",
                self.label,
            )
        self.register_offset = offset
        return offset

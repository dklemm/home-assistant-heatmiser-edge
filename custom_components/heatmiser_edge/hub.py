"""All Modbus I/O for one RS485 bus. Nothing else here touches Modbus.

`modbus-connection` owns the client, the serialising lock and the >=50 ms gap;
this module owns register numbering, per-unit availability and the one retry.

The bus is half-duplex and shared, so `message_spacing=INTER_TRANSACTION_GAP` is
what serialises it - `Pacer` takes no lock at all when the gap is zero.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusSerialParams,
    ModbusTcpParams,
)
from modbus_connection.pymodbus import ModbusConnection

from .const import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_REGISTER_OFFSET,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DEFAULT_TIMEOUT,
    FRAMER_RTU,
    INTER_TRANSACTION_GAP,
    MAX_BLOCK,
    POLL_COUNT,
    POLL_START,
    REG_COMMS_ID,
    TRANSPORT_SERIAL,
    UNIT_BACKOFF_AFTER,
    UNIT_BACKOFF_EVERY,
)
from .detect import PROBE_COUNT, PROBE_START

_LOGGER = logging.getLogger(__name__)


class EdgeConnectionError(Exception):
    """The bus is unusable - the port is gone or the socket is refused.

    Never raised for a silent thermostat: conflating the two would take every
    other stat down with one dead one.
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
        register_offset: int = DEFAULT_REGISTER_OFFSET,
    ) -> None:
        self.transport = transport
        self.serial_port = serial_port
        self.host = host
        self.port = port
        self.framer = framer
        self.timeout = timeout
        # Fixed everywhere the integration builds a hub; only
        # `dev/edge_modbus_test.py detect` ever reads at the other base.
        self.register_offset = register_offset
        self.unit_failures: dict[int, int] = {}

        self._polls = 0
        self._base_warned: set[int] = set()

        if transport == TRANSPORT_SERIAL and not serial_port:
            raise ValueError("serial transport needs a serial port")
        if transport != TRANSPORT_SERIAL and not host:
            raise ValueError("tcp transport needs a host")

        # Safe here: modbus-connection allocates nothing until its first
        # connect, so a probe hub the config flow abandons costs nothing.
        if transport == TRANSPORT_SERIAL:
            params: ModbusSerialParams | ModbusTcpParams = ModbusSerialParams(
                device=serial_port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                framer="rtu",
            )
        else:
            # Cheap gateways split about evenly between real Modbus TCP and
            # transparent RTU-over-TCP, and choosing wrong looks like a dead bus.
            params = ModbusTcpParams(
                host=host,
                port=port,
                framer="rtu" if framer == FRAMER_RTU else "socket",
            )
        self._connection = ModbusConnection(
            params,
            timeout=timeout,
            message_spacing=INTER_TRANSACTION_GAP,  # never zero: see the module docstring
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

    async def async_connect(self) -> None:
        """Open the port or socket.

        Requests connect on demand, so this only exists to fail early: without it
        a dead port costs every id in a discovery sweep a full timeout first.
        """
        try:
            await self._connection.connect()
        except (ModbusError, ValueError) as err:
            raise EdgeConnectionError(f"Could not open {self.label}: {err}") from err

    async def async_close(self) -> None:
        """Close for good - `modbus-connection` will not reopen a closed link."""
        await self._connection.close()

    # ------------------------------------------------------------------
    # Addressing
    # ------------------------------------------------------------------

    def wire(self, register: int) -> int:
        """The manual's register number as a wire address. The only offset site."""
        return register + self.register_offset

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    async def _transact(self, call: Callable[[], Awaitable[Any]]) -> Any:
        """One request, with the retry a mostly-empty bus needs.

        pymodbus drops the link after a run of consecutive silences, and a scan
        of ids 1-32 on a bus holding one thermostat is far more than that. The
        port is fine; only pymodbus's patience ran out. Left alone it surfaces as
        a bus failure and aborts the scan.

        Retrying is safe because every request here is idempotent. It is a second
        *paced* request, so another unit may slot in between the attempts.
        """
        try:
            return await call()
        except ModbusConnectionError:
            # Usually a no-op - the drop already unpublished the client. It earns
            # its place when the link is up but unusable, and it closes before
            # opening, which pyserial's exclusive lock on the device requires.
            with suppress(ModbusError):
                await self._connection.disconnect()
            return await call()

    async def async_read_block(
        self, unit_id: int, start: int, count: int
    ) -> dict[int, int] | None:
        """One FC03. {manual register: word}, or None if the unit was silent.

        A dead transport raises instead: different problem, different fix.
        """
        if count > MAX_BLOCK:
            raise ValueError(
                f"{count} registers exceeds the manual's {MAX_BLOCK}-register packet limit"
            )

        async def call():
            return await self._connection.for_unit(unit_id).read_holding_registers(
                self.wire(start), count
            )

        try:
            words = await self._transact(call)
        except ModbusConnectionError as err:
            # First: every Modbus error below is also a ModbusError.
            raise EdgeConnectionError(f"{self.label} is not connected: {err}") from err
        except ModbusError as err:
            # Timeout, exception response or corrupt frame: no words from this
            # stat, which is not a bus failure.
            _LOGGER.debug("Unit %s did not answer: %s", unit_id, err)
            return None
        block = {start + i: word for i, word in enumerate(words)}
        self._check_register_base(unit_id, block)
        return block

    def _check_register_base(self, unit_id: int, words: dict[int, int]) -> None:
        """Warn if the whole map looks shifted by one register.

        Register 31 holds the Communications ID, so by definition it is the id we
        addressed. Reading anything else means every register is landing one slot
        away - otherwise a silent, plausible failure, with room temperature
        showing the floor probe and nothing raising.

        Runs on every read whose block reaches register 31, which is the poll, the
        config-flow scan and its probe. A schedule read never does.
        """
        reported = words.get(REG_COMMS_ID)
        if reported is None or reported == unit_id or unit_id in self._base_warned:
            return
        self._base_warned.add(unit_id)
        _LOGGER.warning(
            "Unit %s on %s reports its communications id as %s. Every reading "
            "from it is probably shifted by one register - please report this, "
            "with the thermostat's model and code version",
            unit_id,
            self.label,
            reported,
        )

    async def async_read_span(
        self, unit_id: int, start: int, count: int
    ) -> dict[int, int] | None:
        """A range too long for one packet, as several FC03s.

        Only the weekly program needs it: 168 registers on a Heat, ~0.6 s of a
        9600-baud bus, which is why it is read on demand and never polled.

        A unit going silent part way returns None for the *whole* span - half a
        program read as if it were whole is worse than no program.
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
        """FC06 write-single - the only write an entity makes.

        The manual permits 06 and 16; every control writes one register, which is
        one complete value, so there is nothing to tear. `modbus-connection`
        discards FC06's echo, so callers that care read the register back.
        """

        async def call():
            return await self._connection.for_unit(unit_id).write_register(
                self.wire(register), value
            )

        try:
            await self._transact(call)
        except ModbusError as err:
            # Never shrugged off the way a read is: silently doing nothing to a
            # heating system is the worst outcome.
            raise EdgeConnectionError(
                f"Writing register {register} on unit {unit_id} failed: {err}"
            ) from err

    async def async_write_registers(
        self, unit_id: int, register: int, values: list[int]
    ) -> None:
        """FC16 write-multiple, for registers that must move together.

        Two callers: the RTC block (47-50), which written singly would sync the
        stat to a torn timestamp, and one weekly-program period. No *entity* uses
        it, and a test keeps it that way. FC16 never echoes values, so callers
        verify by reading back.
        """
        if len(values) > MAX_BLOCK:
            raise ValueError(
                f"{len(values)} registers exceeds the manual's {MAX_BLOCK}-register limit"
            )

        async def call():
            return await self._connection.for_unit(unit_id).write_registers(
                self.wire(register), values
            )

        try:
            await self._transact(call)
        except ModbusError as err:
            raise EdgeConnectionError(
                f"Writing registers {register}+ on unit {unit_id} failed: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def async_read_units(
        self, units: Sequence[int]
    ) -> dict[int, dict[int, int] | None]:
        """One 50-register block per thermostat, in id order.

        A long-silent unit is skipped most cycles and still reports None, so Home
        Assistant shows it unavailable rather than serving stale words.
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

    async def async_probe_unit(self, unit_id: int) -> dict[int, int] | None:
        """Read the five discovery registers (manual 30-34).

        Five, not fifty, because a sweep of 32 ids pays a full timeout for each
        one that is absent - and this window holds the discriminating values.
        """
        return await self.async_read_block(unit_id, PROBE_START, PROBE_COUNT)

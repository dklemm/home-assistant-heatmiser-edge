"""The bus layer, against modbus-connection's in-memory mock.

These matter most on real hardware: the wire is half-duplex and shared, and
getting the pacing wrong produces intermittent CRC errors that look like bad
wiring.

The double is the library's `MockModbusConnection`, so the stores and error paths
come from upstream. `_BusUnit` adds what a *bus* test needs and a device mock
does not provide: time on the wire, and the pacing wrapper the real backend puts
around every operation. Using the connection's own `_pacer` is deliberate - it
makes the gap and the lock asserted here the library's real implementation.
"""

import asyncio

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    IllegalDataValueError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection._pacing import Pacer
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from custom_components.heatmiser_edge.const import (
    INTER_TRANSACTION_GAP,
    MAX_BLOCK,
    TRANSPORT_TCP,
    UNIT_BACKOFF_AFTER,
    UNIT_BACKOFF_EVERY,
)
from custom_components.heatmiser_edge.hub import EdgeConnectionError, EdgeHub

# Wall-clock assertions need a little slack for the event loop's own scheduling.
TOLERANCE = 0.008

# Enough seeded registers to cover every address these tests read.
SEEDED = 400

# pymodbus's `count_until_disconnect`: `retries + 3`, and the library sets 0.
DISCONNECT_AFTER = 3


class _BusUnit(MockModbusUnit):
    """Paced like `PymodbusUnit` - connect, then run inside `Pacer.paced`.

    Without this the mock answers instantly and every gap assertion is vacuous.
    """

    async def _on_the_wire(self, operation, /, *args):
        await self._conn.connect()
        async with self._conn._pacer.paced(self._unit_id):
            # Timed inside the pacer: a span is wire time, not the gap before it.
            started = asyncio.get_running_loop().time()
            try:
                await asyncio.sleep(self._conn.latency)
                if (err := self._conn.answer_for(self._unit_id)) is not None:
                    raise err
                return await operation(*args)
            finally:
                self._conn.spans.append((started, asyncio.get_running_loop().time()))

    async def read_holding_registers(self, address, count):
        self._conn.reads.append((self._unit_id, address, count))
        return await self._on_the_wire(
            super().read_holding_registers, address, count
        )

    async def write_register(self, address, value):
        self._conn.writes.append((self._unit_id, address, value))
        return await self._on_the_wire(super().write_register, address, value)

    async def write_registers(self, address, values):
        self._conn.block_writes.append((self._unit_id, address, list(values)))
        return await self._on_the_wire(super().write_registers, address, list(values))


class _BusMock(MockModbusConnection):
    """The bus: several units, one wire, one gap.

    `spans` is (started, finished) per transaction - overlapping spans mean two
    requests were on a half-duplex wire at once.

    It also models pymodbus's disconnect budget, which the hub has to survive:
    silences past the budget drop the link and *raise* rather than time out, and
    any success resets the count. `dead` is the distinction that matters - a link
    pymodbus gave up on reconnects, a port that is gone does not.
    """

    def __init__(self, *, silent_units=(), latency=0.005, dead=False):
        super().__init__()
        # The hub sets this on the real connection through the constructor; the
        # mock's takes no parameters, so give its pacer the same gap directly.
        self._pacer = Pacer(INTER_TRANSACTION_GAP)
        self.silent_units = set(silent_units)
        self.latency = latency
        self.dead = dead
        self.connect_calls = 0
        self.drop_next_request = False
        self.spans: list[tuple[float, float]] = []
        self.reads: list[tuple[int, int, int]] = []  # (unit_id, address, count)
        self.writes: list[tuple[int, int, int]] = []  # (unit_id, address, value)
        self.block_writes: list[tuple[int, int, list[int]]] = []
        self.write_events: dict[int, list] = {}  # the library's own WriteEvents
        self._silences = 0

    async def _connect_client(self):
        self.connect_calls += 1
        if self.dead:
            raise ModbusConnectionError(f"could not connect to {self._target}")
        self._silences = 0
        return object()

    def for_unit(self, unit_id):
        if unit_id not in self._units:
            unit = self._units[unit_id] = _BusUnit(self, unit_id)
            # Address-encoded, so the offset arithmetic shows in the values.
            unit.holding[0] = list(range(SEEDED))
            unit.on_write(self.write_events.setdefault(unit_id, []).append)
        return self._units[unit_id]

    def answer_for(self, unit_id):
        """The error a request to `unit_id` gets, or None to let it through."""
        silent = unit_id in self.silent_units
        self._silences = self._silences + 1 if silent else 0
        if self.drop_next_request or self._silences >= DISCONNECT_AFTER:
            # Out of patience. pymodbus drops the link and raises; the port is fine.
            self.drop_next_request = False
            self.simulate_connection_lost()
            return ModbusConnectionError("connection lost")
        return ModbusTimeoutError("No response received") if silent else None


def make_hub(**kwargs) -> tuple[EdgeHub, _BusMock]:
    bus = _BusMock(**kwargs)
    hub = EdgeHub(transport=TRANSPORT_TCP, host="127.0.0.1", register_offset=-1)
    hub._connection = bus
    return hub, bus


async def test_wire_applies_the_offset_but_results_come_back_in_manual_numbers():
    """The offset lives in exactly one place, and never leaks upwards."""
    hub, client = make_hub()
    words = await hub.async_read_block(1, 1, 5)
    assert client.reads == [(1, 0, 5)]  # manual register 1 is wire address 0
    assert sorted(words) == [1, 2, 3, 4, 5]

    hub.register_offset = 0
    await hub.async_read_block(1, 1, 5)
    assert client.reads[-1] == (1, 1, 5)


async def test_transactions_are_paced_across_different_units():
    """The gap belongs to the wire, not one thermostat: pacing per unit would let
    two stats be polled back to back with no gap, which is what a poll does.
    """
    hub, client = make_hub()
    await hub.async_read_block(1, 1, 50)
    await hub.async_read_block(2, 1, 50)
    await hub.async_read_block(3, 1, 50)

    for (_, first_end), (second_start, _) in zip(client.spans, client.spans[1:]):
        assert second_start - first_end >= INTER_TRANSACTION_GAP - TOLERANCE


async def test_writes_are_paced_too():
    """A write is a transaction like any other; the lock covers both."""
    hub, client = make_hub()
    await hub.async_read_block(1, 1, 50)
    await hub.async_write_register(2, 34, 215)
    (_, read_end), (write_start, _) = client.spans
    assert write_start - read_end >= INTER_TRANSACTION_GAP - TOLERANCE


async def test_the_lock_stops_concurrent_requests_overlapping():
    """Half-duplex: two requests in flight do not produce two answers."""
    hub, client = make_hub()
    await asyncio.gather(
        hub.async_read_block(1, 1, 50),
        hub.async_write_register(2, 34, 215),
        hub.async_read_block(3, 1, 50),
    )
    spans = sorted(client.spans)
    for (_, earlier_end), (later_start, _) in zip(spans, spans[1:]):
        assert later_start >= earlier_end


async def test_the_gap_is_the_time_remaining_not_a_flat_sleep():
    """A caller that already waited longer than the gap must not wait again.

    Without this the gap would tax every poll, even at a 60 s interval.
    """
    hub, _ = make_hub(latency=0)
    await hub.async_read_block(1, 1, 50)
    await asyncio.sleep(INTER_TRANSACTION_GAP * 2)  # the wire has long been free

    started = asyncio.get_running_loop().time()
    await hub.async_read_block(1, 1, 50)
    assert asyncio.get_running_loop().time() - started < INTER_TRANSACTION_GAP


async def test_the_gap_is_what_serialises_the_bus():
    """`Pacer` takes no lock when the spacing is zero, so setting the gap to zero
    would quietly put two requests on a half-duplex wire at once.
    """
    hub = EdgeHub(transport=TRANSPORT_TCP, host="127.0.0.1")
    assert hub._connection._pacer._message_spacing == INTER_TRANSACTION_GAP


async def test_a_timed_out_unit_still_paces_what_follows():
    """A stat that answers late must not step on the next unit's reply."""
    hub, client = make_hub(silent_units={1})
    assert await hub.async_read_block(1, 1, 50) is None
    await hub.async_read_block(2, 1, 50)
    (_, first_end), (second_start, _) = client.spans
    assert second_start - first_end >= INTER_TRANSACTION_GAP - TOLERANCE


async def test_a_silent_unit_is_not_an_error():
    """One thermostat missing is a device fact, not a bus failure."""
    hub, _ = make_hub(silent_units={2})
    assert await hub.async_read_block(2, 1, 50) is None
    assert await hub.async_read_block(1, 1, 50) is not None


async def test_a_dead_bus_raises():
    """The port being gone is a different problem with a different fix."""
    hub, _ = make_hub(dead=True)
    with pytest.raises(EdgeConnectionError):
        await hub.async_read_block(1, 1, 50)


async def test_a_run_of_absent_ids_does_not_take_the_bus_down():
    """The config-flow scan: ids 1-32 on a bus holding one thermostat.

    Past the disconnect budget pymodbus raises rather than timing out, so without
    the retry the default scan range could never find a stat at the end of it.
    """
    hub, bus = make_hub(silent_units=set(range(2, 10)))

    for unit_id in range(2, 10):
        # Absent, never a bus failure - even once the link has dropped mid-sweep.
        assert await hub.async_read_block(unit_id, 1, 50) is None

    assert bus.connect_calls > 1, "the run never exhausted the disconnect budget"
    assert await hub.async_read_block(1, 1, 50) is not None


async def test_the_reconnect_is_attempted_once_not_in_a_loop():
    """A port that is really gone must still fail, and fail promptly."""
    hub, bus = make_hub(dead=True)
    with pytest.raises(EdgeConnectionError):
        await hub.async_read_block(1, 1, 50)
    # The request itself, and the retry. Not a third.
    assert bus.connect_calls == 2


async def test_a_write_survives_the_same_spurious_disconnect():
    """Re-sending one FC06 is idempotent, so writes get the retry too."""
    hub, bus = make_hub()
    bus.drop_next_request = True

    await hub.async_write_register(1, 34, 215)
    assert bus.writes[-1] == (1, 33, 215)  # manual 34 -> wire 33


async def test_an_error_response_reads_as_silence():
    """Present, but refusing these registers, is still no words to publish."""
    hub, bus = make_hub()
    bus.for_unit(1).fail_read(0, IllegalDataAddressError())
    assert await hub.async_read_block(1, 1, 50) is None


async def test_writes_use_fc06_not_fc16():
    """The manual allows both; one control writes one complete value."""
    hub, bus = make_hub()
    await hub.async_write_register(3, 34, 215)
    assert bus.writes == [(3, 33, 215)]  # manual 34 -> wire 33
    assert bus.block_writes == []
    assert [e.function_code for e in bus.write_events[3]] == [0x06]


async def test_fc16_exists_for_the_blocks_that_need_it():
    """Not used in v1, but the RTC and away blocks must move atomically."""
    hub, bus = make_hub()
    await hub.async_write_registers(1, 47, [2026, 0x0C19, 0x0917, 30])
    assert bus.block_writes == [(1, 46, [2026, 0x0C19, 0x0917, 30])]
    assert [e.function_code for e in bus.write_events[1]] == [0x10]


async def test_a_rejected_write_raises():
    """A write that does nothing to a heating system is never shrugged off."""
    hub, bus = make_hub()
    bus.for_unit(1).fail_write(33, IllegalDataValueError())
    with pytest.raises(EdgeConnectionError):
        await hub.async_write_register(1, 34, 215)


@pytest.mark.parametrize(
    "call",
    [
        lambda hub: hub.async_read_block(1, 1, MAX_BLOCK + 1),
        lambda hub: hub.async_write_registers(1, 1, [0] * (MAX_BLOCK + 1)),
    ],
)
async def test_the_manuals_packet_limit_is_enforced(call):
    """"Each send a packet of data, register number cannot exceed 60"."""
    hub, _ = make_hub()
    with pytest.raises(ValueError, match="60"):
        await call(hub)


async def test_a_poll_is_one_read_per_thermostat():
    hub, client = make_hub()
    results = await hub.async_read_units([1, 2, 3])
    assert len(client.reads) == 3
    assert all(count == 50 for _, _, count in client.reads)
    assert set(results) == {1, 2, 3}


async def test_a_missing_thermostat_backs_off_and_recovers():
    """A stat taken off the wall must not cost a timeout every poll for ever."""
    hub, client = make_hub(silent_units={2})

    for _ in range(UNIT_BACKOFF_AFTER):
        await hub.async_read_units([1, 2])
    assert hub.unit_failures[2] == UNIT_BACKOFF_AFTER

    attempts_before = len([r for r in client.reads if r[0] == 2])
    polls = UNIT_BACKOFF_EVERY * 4
    for _ in range(polls):
        results = await hub.async_read_units([1, 2])
        # A skipped poll still reports silent, so the unit stays unavailable in
        # Home Assistant rather than serving words from before it vanished.
        assert results[2] is None
    retries = len([r for r in client.reads if r[0] == 2]) - attempts_before
    # Roughly one attempt in UNIT_BACKOFF_EVERY: few enough not to tax the bus,
    # but never zero, or the unit could not come back on its own.
    assert 0 < retries <= polls // UNIT_BACKOFF_EVERY + 1

    # The healthy unit is polled every cycle throughout.
    assert hub.unit_failures[1] == 0
    assert len([r for r in client.reads if r[0] == 1]) == UNIT_BACKOFF_AFTER + polls
    client.silent_units.clear()
    for _ in range(UNIT_BACKOFF_EVERY):
        await hub.async_read_units([1, 2])
    assert hub.unit_failures[2] == 0


async def test_a_shifted_register_base_is_reported(words, caplog):
    """The check that replaced the offset search.

    Register 31 is the id we addressed, so reading anything else means the map is
    landing one slot away. Otherwise silent: nothing raises and every value is
    plausible, the card just shows the wrong things.
    """
    hub, bus = make_hub()
    stat = bus.for_unit(7)
    stat.holding.clear()
    # A 1-based firmware: manual register N sits at wire address N, so reading
    # at the assumed -1 shifts everything by one.
    for register, word in words.heat(7).items():
        stat.holding[register] = word

    await hub.async_read_units([7])
    assert "communications id" in caplog.text

    # Once per unit, not once per poll - a 60 s log spammer helps nobody.
    caplog.clear()
    await hub.async_read_units([7])
    assert "communications id" not in caplog.text


async def test_a_correctly_based_stat_says_nothing(words, caplog):
    hub, bus = make_hub()
    stat = bus.for_unit(7)
    stat.holding.clear()
    for register, word in words.heat(7).items():
        stat.holding[register - 1] = word

    await hub.async_read_units([7])
    assert "communications id" not in caplog.text


async def test_the_base_is_checked_on_the_discovery_probe_too(words, caplog):
    """Onboarding is when a wrong base is worth hearing about.

    The config flow reads through `async_probe_unit`, which covers register 31 -
    so the scan reports the addressing problem rather than leaving it to be
    guessed at from two thermostats that scored badly.
    """
    hub, bus = make_hub()
    stat = bus.for_unit(7)
    stat.holding.clear()
    for register, word in words.heat(7).items():
        stat.holding[register] = word

    await hub.async_probe_unit(7)
    assert "communications id" in caplog.text


async def test_a_block_that_never_reaches_register_31_stays_quiet(caplog):
    """The weekly program is registers 51-218, so there is nothing to check.

    Without this the check reads `None` for a register the block never covered
    and reports every schedule read as a mis-addressed bus.
    """
    hub, _ = make_hub()
    assert await hub.async_read_span(1, 51, 168) is not None
    assert "communications id" not in caplog.text


async def test_connect_reports_a_bus_that_will_not_open():
    hub, _ = make_hub(dead=True)
    with pytest.raises(EdgeConnectionError, match="Could not open"):
        await hub.async_connect()


def test_a_serial_hub_needs_a_port():
    from custom_components.heatmiser_edge.const import TRANSPORT_SERIAL

    with pytest.raises(ValueError, match="serial port"):
        EdgeHub(transport=TRANSPORT_SERIAL)

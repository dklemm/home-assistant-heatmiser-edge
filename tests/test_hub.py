"""The bus layer, against a fake pymodbus client.

These are the tests that matter most on real hardware, because the RS485 bus is
unforgiving in ways nothing above the hub can compensate for: it is half-duplex,
it is shared by every thermostat, and the manual demands a gap between
transactions. Getting any of that wrong produces intermittent CRC errors that
look like bad wiring.
"""

import asyncio

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException

from custom_components.heatmiser_edge.const import (
    INTER_TRANSACTION_GAP,
    MAX_BLOCK,
    TRANSPORT_TCP,
    UNIT_BACKOFF_AFTER,
    UNIT_BACKOFF_EVERY,
)
from custom_components.heatmiser_edge.detect import OFFSETS
from custom_components.heatmiser_edge.hub import EdgeConnectionError, EdgeHub

# Wall-clock assertions need a little slack for the event loop's own scheduling.
TOLERANCE = 0.008


class _Response:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


class FakeClient:
    """A pymodbus client that records what and when, and can play dead.

    `spans` records (started, finished) for every transaction, which is how the
    lock and the inter-transaction gap are asserted: overlapping spans mean two
    requests were on a half-duplex wire at once.

    It also models the real client's disconnect budget, because that is a
    behaviour the hub has to survive rather than an artefact worth faking:
    pymodbus counts *consecutive* silences and closes the connection once they
    pass `retries + 3`, resetting the count on any success. With the hub's
    `retries=1` that is 6, which a discovery scan of a mostly-empty bus reaches
    easily. `port_alive` is the distinction that matters - a port pymodbus gave
    up on reconnects, a port that is genuinely gone does not.
    """

    def __init__(
        self, *, silent_units=(), connected=True, latency=0.005, disconnect_after=6
    ):
        self.reads: list[tuple[int, int, int]] = []  # (device_id, address, count)
        self.writes: list[tuple[int, int, int]] = []  # (device_id, address, value)
        self.block_writes: list[tuple[int, int, list[int]]] = []
        self.spans: list[tuple[float, float]] = []
        self.silent_units = set(silent_units)
        self.connected = connected
        self.port_alive = connected
        self.latency = latency
        self.disconnect_after = disconnect_after
        self.connect_calls = 0
        self._silences = 0

    async def connect(self):
        self.connect_calls += 1
        self.connected = self.port_alive
        self._silences = 0
        return self.connected

    def close(self):
        self.connected = False

    async def _run(self, unit_id):
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(self.latency)
        self.spans.append((started, asyncio.get_running_loop().time()))
        if not self.connected:
            raise ConnectionException("Not connected")
        if unit_id in self.silent_units:
            self._silences += 1
            if self._silences >= self.disconnect_after:
                # pymodbus closes the port itself here. Nothing is wrong with it.
                self.connected = False
            raise ModbusIOException("No response received")
        self._silences = 0

    async def read_holding_registers(self, address, count=1, device_id=1):
        self.reads.append((device_id, address, count))
        await self._run(device_id)
        # Encode the address in the value so the offset arithmetic is visible.
        return _Response([address + i for i in range(count)])

    async def write_register(self, address, value, device_id=1):
        self.writes.append((device_id, address, value))
        await self._run(device_id)
        return _Response()

    async def write_registers(self, address, values, device_id=1):
        self.block_writes.append((device_id, address, list(values)))
        await self._run(device_id)
        return _Response()


def make_hub(**kwargs) -> tuple[EdgeHub, FakeClient]:
    client = FakeClient(**kwargs)
    hub = EdgeHub(transport=TRANSPORT_TCP, host="127.0.0.1", register_offset=-1)
    hub._client = client
    return hub, client


async def test_wire_applies_the_offset_but_results_come_back_in_manual_numbers():
    """The offset lives in exactly one place, and never leaks upwards."""
    hub, client = make_hub()
    words = await hub.async_read_block(1, 1, 5)
    assert client.reads == [(1, 0, 5)]  # manual register 1 is wire address 0
    assert sorted(words) == [1, 2, 3, 4, 5]

    hub.register_offset = 0
    await hub.async_read_block(1, 1, 5)
    assert client.reads[-1] == (1, 1, 5)


async def test_an_unsettled_offset_still_lets_a_probe_run():
    """Detection has to read something before it has an answer."""
    hub, client = make_hub()
    hub.register_offset = None
    await hub.async_read_block(1, 30, 5)
    assert client.reads == [(1, 29, 5)]


async def test_transactions_are_paced_across_different_units():
    """The gap is a property of the wire, not of one thermostat.

    Pacing per unit would let two stats be polled back to back with no gap at
    all - which is exactly what a poll of several thermostats does.
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
    hub._next_free = asyncio.get_running_loop().time() - 1.0  # long since free
    started = asyncio.get_running_loop().time()
    await hub.async_read_block(1, 1, 50)
    assert asyncio.get_running_loop().time() - started < INTER_TRANSACTION_GAP


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
    hub, _ = make_hub(connected=False)
    with pytest.raises(EdgeConnectionError):
        await hub.async_read_block(1, 1, 50)


async def test_a_run_of_absent_ids_does_not_take_the_bus_down():
    """The config-flow scan: ids 1-8 on a bus holding one thermostat.

    pymodbus closes the port after six consecutive silences, so every id after
    the sixth used to fail as a *bus* error - which meant the default scan range
    could never find a thermostat sitting at the end of it.
    """
    hub, client = make_hub(silent_units=set(range(2, 10)))

    for unit_id in range(2, 10):
        # Each absent id reports itself absent - never as a bus failure, even
        # once pymodbus has closed the port underneath us mid-sweep.
        assert await hub.async_read_block(unit_id, 1, 50) is None

    assert client.connect_calls, "the fake never reached pymodbus's disconnect"
    assert await hub.async_read_block(1, 1, 50) is not None


async def test_the_reconnect_is_attempted_once_not_in_a_loop():
    """A port that is really gone must still fail, and fail promptly."""
    hub, client = make_hub(connected=False)
    with pytest.raises(EdgeConnectionError):
        await hub.async_read_block(1, 1, 50)
    assert client.connect_calls == 1


async def test_a_write_survives_the_same_spurious_disconnect():
    """Re-sending one FC06 is idempotent, so writes get the retry too."""
    hub, client = make_hub(silent_units={9})
    for _ in range(client.disconnect_after):
        assert await hub.async_read_block(9, 1, 50) is None
    assert not client.connected

    await hub.async_write_register(1, 34, 215)
    assert client.writes[-1] == (1, 33, 215)  # manual 34 -> wire 33


async def test_an_error_response_reads_as_silence():
    hub, client = make_hub()

    async def error_response(address, count=1, device_id=1):
        return _Response(error=True)

    client.read_holding_registers = error_response
    assert await hub.async_read_block(1, 1, 50) is None


async def test_writes_use_fc06_not_fc16():
    """The manual allows both; FC06 echoes the value the stat actually kept."""
    hub, client = make_hub()
    await hub.async_write_register(3, 34, 215)
    assert client.writes == [(3, 33, 215)]  # manual 34 -> wire 33
    assert client.block_writes == []


async def test_fc16_exists_for_the_blocks_that_need_it():
    """Not used in v1, but the RTC and away blocks must move atomically."""
    hub, client = make_hub()
    await hub.async_write_registers(1, 47, [2026, 0x0C19, 0x0917, 30])
    assert client.block_writes == [(1, 46, [2026, 0x0C19, 0x0917, 30])]


async def test_a_rejected_write_raises():
    hub, client = make_hub()

    async def rejected(address, value, device_id=1):
        return _Response(error=True)

    client.write_register = rejected
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


async def test_detect_offset_settles_the_bus_from_a_decisive_unit(words):
    """End to end through the hub: probe both offsets, then commit to one."""
    truth = {7: words.heat(7)}
    hub, client = make_hub()
    hub.register_offset = None

    async def read(address, count=1, device_id=1):
        stat = truth.get(device_id)
        if stat is None:
            raise ModbusIOException("No response received")
        # The firmware is 0-based: wire address A holds manual register A + 1.
        return _Response([stat.get(address + 1 + i, 0) for i in range(count)])

    client.read_holding_registers = read
    assert await hub.async_detect_offset([7]) == -1
    assert hub.register_offset == -1


async def test_detection_probes_an_absent_unit_once_not_at_both_offsets():
    """A silent id is absent, not mis-addressed, so the second probe is waste.

    A thermostat that is present but does not implement an address answers with
    an exception response; only an absent one says nothing at all. Paying a
    second timeout to ask again is what pushes a mostly-empty bus over
    pymodbus's disconnect budget.
    """
    hub, client = make_hub(silent_units={2, 3})
    hub.register_offset = None
    await hub.async_detect_offset([1, 2, 3])

    for unit_id in (2, 3):
        probes = [r for r in client.reads if r[0] == unit_id]
        assert len(probes) == 1, f"unit {unit_id} was probed {len(probes)} times"
    # The unit that answers is still probed at both candidate offsets.
    assert len([r for r in client.reads if r[0] == 1]) == len(OFFSETS)


async def test_probing_never_leaves_the_offset_disturbed():
    """A probe at a candidate offset must not change the hub's settled answer."""
    hub, _ = make_hub()
    await hub.async_probe_unit(1, offset=0)
    assert hub.register_offset == -1


async def test_connect_reports_a_bus_that_will_not_open():
    hub, _ = make_hub(connected=False)
    with pytest.raises(EdgeConnectionError, match="Could not open"):
        await hub.async_connect([1])


def test_a_serial_hub_needs_a_port():
    from custom_components.heatmiser_edge.const import TRANSPORT_SERIAL

    with pytest.raises(ValueError, match="serial port"):
        EdgeHub(transport=TRANSPORT_SERIAL)

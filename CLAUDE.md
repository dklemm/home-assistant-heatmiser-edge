# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A HACS custom integration (`custom_components/heatmiser_edge/`, domain **heatmiser_edge**) for
Heatmiser EDGE thermostats over RS485 Modbus RTU, plus its dev tooling in `dev/`. Modelled on
`/Users/daniel/ctc-heatpump/home-assistant-ctc-bms` and keeping its doctrine deliberately.

| Path | Role |
|---|---|
| `custom_components/heatmiser_edge/registers.py` | the register map, hand-transcribed from the manual |
| `custom_components/heatmiser_edge/const.py` | protocol constants **and** the curated ship-list tables |
| `custom_components/heatmiser_edge/decode.py` | pure words↔values: temperatures, packed bytes, plausibility |
| `custom_components/heatmiser_edge/detect.py` | pure scoring: Heat vs Timer |
| `custom_components/heatmiser_edge/hub.py` | all Modbus I/O, over `modbus-connection`: the offset, the one retry, per-unit backoff |
| `custom_components/heatmiser_edge/coordinator.py` | the poll, per-unit data, the one `platform_for()` gate |
| `custom_components/heatmiser_edge/schedule.py` | pure grid↔words: the weekly program, and what may be written into it |
| `custom_components/heatmiser_edge/config_flow.py` | onboarding, and the per-id progress sweep |
| `custom_components/heatmiser_edge/services.py` | the writes with no entity: `set_time`, `set_hold`, `get_schedule`, `set_schedule` |
| `dev/fake_edge_server.py` | a fake bus on 127.0.0.1:5020 — three units, both register bases |
| `dev/edge_modbus_test.py` | field CLI: `detect`, `scan`, `dump`, `schedule`, `read`, `poll`, `write`, `settime` |

## Source of truth

`docs/EDGE-RS485-MODBUS-Communication-protocol-V1.8.md` is authoritative and is committed. Register
numbers **everywhere in the code are the manual's own 1-based numbers**, never wire addresses;
`EdgeHub.wire()` is the only place the offset is applied. That means the code reads against the
manual line by line.

The map is hand-written, not generated — it is a 50-row table, and a parser would be more code than
the data.

## Two things the manual does not say, and how they are settled

**Is the wire 0-based?** The manual numbers from 1 and never says. **Settled: it is** — manual N at
wire N−1, the standard Modbus convention, confirmed on hardware across the whole 1–218 range. That
is now `const.DEFAULT_REGISTER_OFFSET`, and there is **no search**. The integration used to probe
both bases and vote across units at onboarding; that was ~236 lines answering a question with one
answer, and it was deleted 2026-08-17.

What replaced it is `EdgeHub._check_register_base`, about ten lines: register 31 holds the
Communications ID, which is by definition the id we addressed, so if it reads back as anything else
the whole map is landing one slot away. It warns once per unit. That keeps the only thing the search
was really worth — the failure mode is otherwise **silent and plausible**, with room temperature
showing the floor probe and the mode select showing a setpoint, and nothing raising.

It hangs off `async_read_block`, not the poll, so it covers **every** read whose block reaches
register 31 — the poll, the config-flow scan and its discovery probe. Onboarding is when a wrong base
is most worth hearing about, and it is the one moment the user can still act on it. Blocks that never
reach 31 (a schedule read is 51–218) are skipped explicitly: without that guard `words.get(31)` is
`None`, `None != unit_id`, and every schedule read reports a mis-addressed bus.

The base stays a config option (−1 or 0, defaulting to −1), so a stat that ever disagreed is fixed
without a release — but **setup never asks for it**. A first-time user has no way to answer, and the
check reports it when it is wrong, so it lives only in the *options* flow, which is where someone
acts on that warning. It is therefore in `entry.options` and not `entry.data`, which is why
`async_step_reconfigure` seeds it with `option()` before a re-scan; gated by
`tests/test_config_flow.py::test_a_rescan_honours_an_offset_set_in_options`.

`dev/edge_modbus_test.py detect` prints register 31 at both bases when that happens — **any unit
with id ≥ 6 is decisive**, because nothing else in the 30–34 window can hold a
value above 5, while on unit 1 both bases can read 1.

**Heat or Timer?** There is no model or product-id register — only "Code version number" at
register 1, which does not distinguish them. `detect.guess_model` scores the two: a Heat stores a
frost setpoint, a floor limit and a switching differential whatever it is currently doing, and a
Timer marks that whole block Reserved so it reads zero. Correctly-read maps separate by 12+; the
threshold is 5, calibrated against the one case that produces a plausible-looking map from working
hardware — a *mis-offset* Heat, which scores at most 4 either way it is shifted.

The result is always a **default the user confirms** in the config flow, never applied silently.

## Hard-won facts (do not re-learn these)

- **`score_heat`'s bands must follow register 21.** A °F thermostat's frost setpoint reads 540, not
  120, so °C-only bands score an ordinary °F stat at 3 and call it unidentifiable. Caught by
  `dev/fake_edge_server.py --fahrenheit 1`, gated by
  `tests/test_detect.py::test_a_fahrenheit_stat_is_still_recognised`.
- **The transport is `modbus-connection`, not raw pymodbus** — Home Assistant's backend-neutral
  abstraction, on its pymodbus backend (`modbus_connection.pymodbus.ModbusConnection`). It owns the
  client, the serialising lock, the inter-transaction gap and the reconnect; `hub.py` owns register
  numbering, per-unit availability and the one retry below. Pin it tightly:
  `modbus-connection[pymodbus]>=4.8.1,<5`, and note the **`[pymodbus]` extra is required** — the
  top-level package is a pure interface and installs no Modbus library at all. Nothing in
  `custom_components/` imports pymodbus any more; `dev/fake_edge_server.py` still does, because the
  library has no server side.
- **The exception split is now typed, and the catch order is load-bearing.**
  `ModbusConnectionError` means the transport is down; `ModbusTimeoutError` means one unit did not
  answer; `ModbusExceptionError` (and its per-code subclasses) means the unit answered with a Modbus
  exception response. **All three subclass `ModbusError`**, so `async_read_block` must catch
  `ModbusConnectionError` *first* — reversing them takes the whole bus offline for one dead
  thermostat. The last two both mean "no words from this stat" and become `None`, which is exactly
  what the old `ModbusIOException` / `result.isError()` pair did.
- **A run of silent ids still drops the link, and `EdgeHub._transact`'s one retry is what absorbs
  it.** pymodbus counts *consecutive* silences in `transaction.py`'s `count_until_disconnect`,
  budgeted at `retries + 3` and reset by *any* success; when it goes negative it calls
  `connection_lost()` **and raises**. The library sets `retries=0` (we used to set 1), so the budget
  is now 3 silences rather than 6 — but each silent id costs one timeout instead of two, which on a
  mostly-empty bus is the better trade. Confirmed on hardware 2026-08-12 with the old numbers:
  `scan --ids 1-3` (4 silent probes) worked and `--ids 1-4` (6) killed the client and aborted the
  whole scan with "Bus error" — which broke the *default* config-flow range on any bus that is not
  fully populated. So `_transact` catches `ModbusConnectionError`, calls `connection.disconnect()`
  and retries exactly once; the library reconnects on the retry's own request. **Use `disconnect()`,
  never `close()`** — `close()` is permanent, and `disconnect()` is close-before-open, which is what
  pyserial's exclusive lock on the device requires. A genuinely dead port raises again on the second
  attempt and is reported as the bus failure it is. Unlike the old hand-rolled version the retry is
  a *second paced request*, so another unit can slot in between the two attempts — harmless, because
  every read is idempotent and every write is one FC06 of one register.
- **`EdgeHub.__init__` builds the connection, and that is now correct.** The old rule was the
  opposite — constructing a pymodbus client allocates a socket, so a hub the config flow builds for
  a probe and abandons leaked one. `BaseModbusConnection.__init__` allocates nothing; its
  `_connect_client` runs on connect. Do not reintroduce the deferral.
- **`ModbusDeviceContext`/`ModbusSequentialDataBlock` are deprecated in pymodbus 3.14** and carry a
  legacy address+1 lookup. `dev/fake_edge_server.py` uses `SimData`/`SimDevice` instead, where wire
  address 0 is `SimData(0, ...)` with no arithmetic at all.
- **Home Assistant's `CachedProperties` metaclass** means `self._attr_native_unit_of_measurement`
  raises `AttributeError` if it was never assigned. Platforms that compute a unit keep their own
  `self._unit` instead.
- **`_reconfigure_entry_id` is a read-only property on `ConfigFlow`.** Use `self.source ==
  SOURCE_RECONFIGURE`.
- **A temperature register holding a *difference* must not carry a temperature device class.** Home
  Assistant would convert a 5 °C span to 41 °F. `const.TEMPERATURE_DELTA` lists them (register 24).
- Home Assistant normalises climate attributes into the user's unit system, so a °F thermostat in a
  metric home reports °C on the card. That is correct, and only works because the entity declares
  its native unit honestly.

## The bus is the constraint

- **Half-duplex and shared.** One lock covers every transaction on every unit id, reads and writes
  alike. It is the library's: `Pacer.paced()` holds it across the whole request.
- **The manual demands >50 ms between transactions.** Passed to the connection as
  `message_spacing=INTER_TRANSACTION_GAP` and slept inside that same lock, for only the time
  actually remaining since the last one *ended* — a flat sleep would tax every poll, and measuring
  from the end means a *timed-out* unit still paces what follows, so a stat that answers late does
  not step on the next one's reply.
- **`Pacer` takes no lock at all when the spacing is zero**, so the half-duplex guarantee above is a
  *consequence* of setting a non-zero gap, not a separate mechanism. Setting
  `INTER_TRANSACTION_GAP` to 0 would quietly put two requests on the wire at once. Gated by
  `tests/test_hub.py::test_the_gap_is_what_serialises_the_bus`.
- **60 registers per packet**, so the poll of registers 1–50 is exactly one FC03 per thermostat.
  There is no batching, bisection or dead-address cache here (unlike CTC): both variants implement
  1–50 contiguously, so a register never goes silent on its own. Only a whole unit does. The one
  thing that does not fit a packet is the weekly program — `EdgeHub.async_read_span` splits it, and
  it is never polled.
- **Entity writes are FC06.** The manual permits 06 and 16; every control writes exactly one
  register, which is one complete value, so there is nothing to tear.
  `async_write_registers` (FC16) exists for the blocks that must move
  atomically: the RTC (47–50) and one schedule period use it today, the away deadline (39–41) is
  still to come. `tests/test_entities.py::test_writes_use_the_single_register_function` keeps an
  entity from quietly acquiring a block write.
- **The FC06 echo is no longer reachable, and nothing relied on it.** FC06's response does echo the
  value the stat kept — that is how the silently-refused writes below were found on hardware — but
  `ModbusUnit.write_register` returns `None` and discards it. The shipped guard against those
  refusals was never the echo: it is `EdgeCoordinator.allowed_operation_modes` declining to issue
  the write at all. If echo verification is ever wanted, it costs a follow-up
  `read_holding_registers` of the same register — which works, because the *written* register takes
  the value immediately (measured: register 34 at +80 ms), unlike register 7. FC16 never echoed
  values in the first place, so its callers always verified by reading back.

## The config-flow sweep reports itself, one id at a time

A 1-32 sweep is ~18 s even when nothing answers, so the scan runs behind a progress dialog that
names **the id being probed and how many thermostats have answered**.

**That shape is forced by Home Assistant, not chosen.** A progress dialog's text is fixed for as
long as its `progress_task` runs — `async_update_progress()` moves the *bar* but never re-renders
the description. The frontend only sees new `description_placeholders` when the flow re-enters the
step, which happens when a `progress_task` completes (`FlowManager._async_handle_step` registers a
done-callback that re-configures the flow). So live text needs the sweep split across several
tasks; do not "simplify" it back into one.

**But a task per unit id is visibly jerky, and that was shipped and reported.** Unit ids are not
evenly timed — a stat that answers costs ~150 ms, an absent one pays a full `SCAN_TIMEOUT` — so the
dialog re-rendered at 0.31 s, then 0.50, then 0.31, and the spinner stuttered on every one. A task
is therefore **a second's worth of ids** (`SCAN_PROGRESS_INTERVAL`), however many that turns out to
be. Measured over 12 ids with 2 present: 12 re-renders at gaps
`0.31, 0.50, 0.51, 0.50, 0.50, 0.50, 0.31, …` became 5 at `1.31, 1.01, 1.31, 1.01`. A batch cannot
stop mid-probe, so it overshoots by whatever the current id costs; that residual unevenness is
inherent.

**There is deliberately no percentage.** `async_update_progress` looks free — it fires an event
rather than re-rendering — but setting a progress value switches the frontend's
`ha-circular-progress` from its indeterminate spinner to a determinate ring, and a re-render drops
the value again. The dialog then flips between two differently-sized widgets on every batch, which
is worse than the original stutter: the spinner stops animating, the ring pops in at a different
size, and everything below it shifts. Shipped and reported. The count lives in the text instead.

**Nothing in the dialog may change size mid-sweep**, for the same reason. The text is one line
carrying the id and a *count* — it used to list the ids found, which needed a cap and an "and N more"
tail to stop the line wrapping on a full bus. A count cannot grow, so that whole guard is gone.

**The confirm step gives each thermostat a `section`, and that is not cosmetic.** Its fields are
built per unit id, so `name_1` and `model_1` have nothing static for `strings.json` to name and Home
Assistant falls back to rendering the raw key as the label — shipped and reported. Enumerating
`name_1`…`name_32` in two files would fix the text and not the layout: `ha-form` leaves a wider gap
after a text field than after a dropdown, so each model reads as belonging to the *next*
thermostat's name. One `section` per unit fixes both at once and costs no strings — the header and
the inner `name`/`model` labels are still key fallbacks, but they are now readable ones. `strings.json`
cannot help here at all: a section's translations live under `sections.<key>.…`, and the key is just
as dynamic. The options flow lists the same two fields per unit and shares `_unit_section`.

- `EdgeConfigFlow` owns the hub across the sweep (`_scan_hub`), because opening and closing a serial
  port 32 times is not the same as opening it once. `async_remove` closes it if the user abandons
  the flow mid-sweep.
- An `EdgeConnectionError` from any probe stops the sweep dead rather than paying 31 more timeouts
  for a bus that is not there. Gated by
  `tests/test_config_flow.py::test_a_bus_that_will_not_open_aborts`, which asserts nothing was probed.
- There is no `async_scan_bus` any more; the flow *is* the sweep. So the flow tests patch `EdgeHub`
  and drive the real thing — `patch_bus()` in `tests/test_config_flow.py`. Two traps there:
  HA advances the flow itself via that done-callback, so a test cannot observe the intermediate
  renders by stepping `async_configure` (`hass.async_block_till_done()` runs the whole sweep) —
  spy on `async_show_progress` instead. And the mock answers instantly, so a whole 32-id sweep fits
  in one batch: a test wanting per-id renders patches `SCAN_PROGRESS_INTERVAL` to 0. Both are gated,
  by `::test_the_progress_dialog_names_the_unit_and_the_running_total` and
  `::test_the_dialog_is_paced_by_a_clock_not_by_unit_ids`.

## Per-unit availability is the headline requirement

`coordinator.data` always has an entry for every configured thermostat, answering or not. One silent
unit leaves `last_update_success` True and only its own entities unavailable; `UpdateFailed` is
raised **only when nothing at all answered**, which on a shared wire means the adapter, the wiring or
the termination. Gated by
`tests/test_coordinator.py::test_one_silent_thermostat_does_not_take_the_others_down`.

## Writes are real

**`EdgeCoordinator.platform_for()` is the only gate on entity creation**, and the reason a register
can never appear on two platforms — every platform file filters on it. Read-only registers always
become an entity; a **writable** register becomes one only if a curated table in `const.py` names
it, because the write goes to a live heating system and the thermostat accepts undocumented values
without complaining.

| table | platform |
|---|---|
| `NUMBERS` | number |
| `SELECTS` | select |
| `SWITCHES` | switch |
| `READ_ONLY_RW` | sensor / binary_sensor — writable, but semantics unproven |
| `SUPPRESSED` | nothing at all |

Never widen a limit or add a select without checking the manual, and only add a select whose
*complete* legend it spells out. `CONF_CONTROLS` gates number/select/switch at once; `READ_ONLY_RW`
survives it, since nothing there can write.

Every write is encode → one FC06 → `EdgeCoordinator.async_refresh_unit()`. **Never optimistic**: the
stat may clamp, and the UI must show what it actually kept.

**The read-back is per-unit and undebounced, and must stay that way.**
`async_request_refresh()` is wrong for it on both counts, and both show up as a control snapping
back to its old value:

- It is debounced with a **10 s cooldown**. The first call runs immediately, but a second inside
  that window is deferred to the end of the timer — and dragging a setpoint is precisely a burst of
  writes, so every one after the first showed the previous reading for up to ten seconds. Gated by
  `tests/test_entities.py::test_a_second_write_in_quick_succession_is_still_read_back`.
- It re-polls **the whole bus**. At 9600 baud with the 50 ms gap, 32 thermostats is seconds of stale
  card for a change to one of them, and every silent id pays a full timeout on top. Gated by
  `tests/test_entities.py::test_a_write_reads_back_only_the_thermostat_it_wrote_to`.

`async_refresh_unit` reads that one stat's 1–50 block, patches its entry in `data` and calls
`async_set_updated_data` — which also pushes the next scheduled poll out by a full interval, since
that unit is now fresh. A silent or failed read-back is deliberately quiet: the write already
succeeded or raised, and a bus that has since gone away is the next poll's news to break.

The climate entity feels this hardest, because a set writes register **34** and `target_temperature`
reads register **7** — nothing in the UI moves at all until the stat has been asked again.
`set_time` keeps the plain `async_request_refresh()`: it spans several units, and only register 30
has anything to re-read.

**One read-back is never enough, because the stat takes a write long before it acts on it.**
Measured on hardware: the +80 ms read-back already shows register 34 holding the new value, while
register 7 — the live setpoint every UI surface reads — still holds the old one, and the relay at
register 2 has not moved either. Both catch up under a second later, at no fixed moment. So
`async_refresh_unit(..., settle=True)` runs the `SETTLE_PROBES` schedule and **stops at the first
read that differs from what is already published**: a stat that reacts in 300 ms shows up in 300 ms,
and nobody waits out a timer. It is **re-armed, not stacked** — a burst of writes runs one schedule
from the last of them — and the probes never set `settle` themselves, or they would never stop.
Pending timers are cancelled in `async_shutdown`. Gated by
`tests/test_entities.py::test_a_write_is_read_back_again_once_the_stat_has_reacted` and
`::test_a_burst_of_writes_costs_one_settle_read`.

### Some writes the thermostat refuses, silently

Two of them, both on register 33, both found on hardware 2026-08-13 and **neither documented**. The
FC06 echo is what exposes them: the response carries back the *old* value instead of the one sent.

- **Program mode 03, "None programmable", allows only Change over, Hold and Frost.** There is no
  weekly program, so the modes that exist in relation to one are refused. `preset_modes` and the
  Timer's mode select are therefore computed, not fixed — `EdgeCoordinator.allowed_operation_modes`
  is the single place that decides. It always keeps the *current* mode on the list even when
  otherwise disallowed, because a stat can be left in Schedule and then switched to
  non-programmable, and a `preset_mode` outside `preset_modes` is something Home Assistant
  complains about. Verified on a Heat; register 29 is documented identically for a Timer, so the
  same rule is applied there by inference.
- **Hold is refused unless register 38 holds a duration first.** With 38 at zero, a write of 2 to
  register 33 echoed back 0; writing a non-zero duration to 38 made the identical write succeed.
  The keypad agrees — its Hold flow asks hours, then minutes, then a temperature.

**The rule this establishes: never issue a write the stat is known to refuse.** Raise a
`ServiceValidationError` that says what is available instead. A control that silently does nothing
is worse than one that is absent, and it was the actual bug report that started this.

### Hold is an action, for the same reason the RTC is: `heatmiser_edge.set_hold`

Hold is three registers in a fixed order — **38 (duration), then 34 (temperature), then 33 (the
mode)** — and `climate.set_preset_mode` has nowhere to put a duration or a temperature. So it is an
action, exactly as the RTC block is.

- **Three FC06s, not one FC16**: 38, 34 and 33 are not contiguous, and each is a complete value on
  its own, so a failure part way leaves settings changed but the mode untouched. That is the safe
  direction — unlike the RTC, where a torn write syncs the stat to a wrong time.
- **33 goes last** because it is the register the stat validates against the others.
- **Duration is validated before anything reaches the wire**, and zero is rejected specially: it is
  not merely out of range, it is the exact value meaning "no hold", so it would be accepted into 38
  and then silently refuse the mode.
- **Temperature is optional** — holding at the current target is a real thing to want, and inventing
  a setpoint for a live heating system is not. Its limits follow the stat's own display unit.
- Targeting, `CONF_CONTROLS` and the collect-failures-and-raise-together rule are `set_time`'s,
  reused.

Gated by `tests/test_hold.py`.

### The weekly program is two actions, never entities: `get_schedule` / `set_schedule`

Registers 51–218 on a Heat (7 days × 6 periods × 4) and 51–162 on a Timer (7 × 4 × 4).
`registers.SCHEDULES` owns the shape, `schedule.py` owns the meaning.

- **Not entities.** 6 × 7 × 3 meaningful fields is **126 entities per Heat**, 4032 on a full bus,
  and each edit would be its own FC06 with no way to move a period's hour, minute and temperature
  together.
- **Not polled, and not in `coordinator.data`.** 168 registers is three FC03 packets against the
  poll's one — ~0.6 s of a 9600-baud bus per stat, ~19 s across 32 — to watch values that only move
  when somebody moves them. `EdgeCoordinator.async_read_schedule` reads on demand and caches into
  `coordinator.schedules`; keeping it out of `data` is what stops 170 rarely-moving registers waking
  every entity on the unit. Gated by `tests/test_schedule.py::test_the_poll_never_reads_the_program`.
- **One FC16 per *period*, not per day.** A Heat day is 24 contiguous registers, but six of them are
  Reserved, and writing zeros into undocumented registers on a live system is what registers 42, 43
  and 44 refuse. Hour/minute/settemp *are* contiguous, so a 3-register FC16 touches only documented
  addresses — and a period is the right atomic unit anyway, `set_hold`'s argument again. A Timer
  period is 4 real registers. **Switching a Heat period off writes only 2** (hour 24, minute 0), so
  the stored set temperature survives and the period can be switched back on without inventing one.
- **Only the periods that actually change are written**, which is why the current program is read
  first. Editing one period costs one FC16 rather than six.
- **Hour 24 is the only "off" a period has** — the manual's "The current schedule is invalid when
  the hour = 24" on every hour row. There is no enable flag.
- **Register 28 says how many periods run (4 or 6), register 29 how many days are independent.**
  Both are already selects, both come free with the poll, and both travel in the `get_schedule`
  response because they are what an editor needs to know what to offer. Every row is still reported:
  the extra two exist and hold values whatever register 28 says.
- **5/2 and 24 Hour mode expand the days written.** The manual never says which of the seven day
  blocks the stat reads in those modes, so naming one day writes every day sharing its program.
  That makes an unsettled question moot instead of betting on an answer.
- **Two passes: plan everything, then write.** With several stats targeted, validating as you go
  would leave the first edited and the second refused. Gated by
  `::test_nothing_is_written_when_one_targeted_stat_refuses`.
- **A day must run forwards, with its unused periods last.** This is a *guard, not a documented
  rule* — the manual says nothing about out-of-order periods, and its own defaults are always
  ascending with trailing 24s. It is checked against the **merged** day, so an edit to one period
  cannot leave the day incoherent.
- Non-programmable (register 29 = 3) is refused outright: there is no program to write.

**The grid still gets an entity — one of them.** `sensor.<name>_weekly_program` carries the whole
week as **attributes**, so a markdown card or a template can render the program with no JavaScript
at all, and a custom card has somewhere to read from without calling an action on every redraw.
This does not breach "one register, one entity": it is not a register, it is 126 of them, and the
alternative was 126 entities.

- **The state is a timestamp — when the program was last read.** A state is one scalar capped at
  255 characters, so it cannot be the grid; and since the program is never polled, how fresh it is
  is a genuine question. Unknown honestly means nothing has read it yet.
- **`_unrecorded_attributes = {MATCH_ALL}`.** A week of values written to the database on every
  state change, for settings that move twice a year, is cost with nothing behind it.
- **Read after setup, not during it**, as an `async_create_background_task`: 32 stats would
  otherwise add ~19 s to setting the entry up.
- Its unique id is `{entry_id}_{unit_id}_schedule`, which cannot collide with the register-numbered
  ones — and `_drop_stale_entities` is told about it explicitly, or it would sweep it away as
  something `entity_registers()` no longer produces.

### The write with no entity and no state: `heatmiser_edge.set_time`

The RTC is **write-only**: registers 47–50 read back 0xFFFF once the stat has taken the time, so
there is no clock to show and nothing for a datetime entity to be — setting it is an *event*, which
is what an action is for. Nothing else in the map exposes the stat's clock, and the weekly program
runs against it, so a stat whose time was never set runs the schedule at the wrong hour with no
symptom Home Assistant can see.

- **One FC16, never four FC06s.** The four registers are one timestamp; written singly the stat sees
  three partial ones and syncs on whichever it likes, which can land it on 1 January.
- **`encode_rtc` lives in `decode.py`**, so the CLI and the action encode identically, and the
  manual's 2000–5000 year range is enforced there.
- **Targeting is by device**, because there is no entity to aim at. A thermostat means itself; the
  **bus device means every thermostat on it**. A device named outright must be ours and must be
  writable, or the call raises; one reached through an area, floor, label or entity is filtered
  silently — "set the time upstairs" skips the lamps.
- **But the picker in `services.yaml` cannot say so, and every `target:` there is bare.** hassfest
  refuses *any* `device` key under `target` — `raise_on_target_device_filter` in
  `script/hassfest/services.py` tests `if "device" in value`, so nesting it under `filter:` does not
  help. Neither way round it is usable: an `entity` filter passes hassfest but hides the bus device,
  which has no entities of its own, and the `device` selector under `fields:` that hassfest's own
  message recommends would give up area, floor, label and entity targeting. So the picker offers
  everything and `_async_targets` does the filtering, which it always did — the filter was only ever
  a UI hint. Do not "fix" the empty `target:` blocks. Gated by
  `tests/test_packaging.py::test_no_action_filters_its_target_by_device`.
- **`CONF_CONTROLS` gates it** like everything else. Read-only means read-only, action or not.
- **A silent thermostat does not deny the others their clock.** Failures are collected and raised
  together after every reachable stat has been set — the poll's rule, applied to a write.
- **DST (register 30) is a separate FC06, written first**, so the time lands under the setting the
  user just chose. It is also already a switch entity; the field exists so one automation can do
  both.

### What does not ship, and why

- **46 (factory reset)** is not in the map at all. Irreversible, one mis-tap away, and Home
  Assistant has no confirmation UI for entity actions. Reachable only via
  `dev/edge_modbus_test.py write 46 1 --i-mean-it`.
- **43 (TPI) and 44 (TPI minimum on time)** ship as *nothing at all* — not even the `READ_ONLY_RW`
  holding pen, because even the readings are meaningless. Both read **20** on hardware, against
  documented ranges of 0–3 and 0–5, on a provably-aligned block (42 reads 0xFFFF and 47–50 read
  0xFFFF either side of them). And **the EDGE keypad has no TPI setting at all** — its menu is 14
  entries and none of them is TPI — so these two are reachable only over Modbus and a wrong write
  *cannot be undone by the person standing at the thermostat*. That is register 42's argument and it
  applies harder here. Gated by `tests/test_entities.py::test_the_tpi_registers_never_ship`.
  **20 is the value to restore** if one is ever written by accident.
- **42 (keylock)** — "Cancel Keylock (Value = 0), General PassWord: 6343" does not establish that
  writing 6343 *locks*. A wrong write could set a password the owner cannot clear from the keypad.
- **21 (temperature format)** — writable, but changing it changes the native unit of every
  temperature entity on the device at runtime, which restarts their long-term statistics.
- **31 (Communications ID)** — writing it moves the thermostat mid-poll and orphans its device. It
  will never be promoted.
- **39–41 (away until)** needs its registers written *together*; it becomes one datetime entity over
  FC16, the way the RTC became `set_time`. Reads only until then.
- **47–50 (RTC)** ship as the `set_time` action, not as entities — see above. They are still in
  `SUPPRESSED` because there is nothing to read: 0xFFFF is all they ever return.
- **Heat 7/8/9/32/33/34** are climate-owned. 8 and 9 are read-only mirrors of the writable 32 and
  33; shipping both would put two entities on one concept. A Timer has no climate, so its 32/33/34
  *are* entities and only its 3/9 mirrors are suppressed.

**Registers 2, 3 and 25 are read by the climate entity and still ship as entities. That is
deliberate — settled 2026-08-13 — and is not the same thing as 8/9.** The rule being kept is "one
register, one *entity*", and it holds: climate adds an *attribute*, not a second entity. Register 3
already shows why that is right — `climate.current_temperature` puts the room on the card while
`sensor.*_room_temperature` carries the long-term statistics, and nobody would want that sensor
removed. Register 2 is the identical relationship: `hvac_action` is an attribute, so boiler run-time
would need a template or `history_stats` to extract, whereas a `binary_sensor` with device class
`heat` gives an on/off timeline and a clean automation trigger for free. A **Timer** has no climate
entity at all, so register 2 is its only surface — suppressing it just for Heat would split the two
models for no gain. What 8 and 9 have that these do not is a *writable* twin: shipping a read-only
mirror alongside the control it mirrors is the thing being avoided. Gated by
`tests/test_entities.py::test_heat_inventory`.

## Reclassification is a breaking change

The unique id is `{entry_id}_{unit_id}_{register}` and encodes neither the platform nor the model.
So two things strand an entity, and `_drop_stale_entities()` sweeps both at setup: a register moving
between platforms, and **a thermostat's model changing between Heat and Timer** — a Timer's register
3 is an on/off flag where a Heat's is a room temperature, and they share a unique id. Recorded
history stays under the old entity id; dashboards naming it need fixing by hand.

## Dev workflow

```sh
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt
pytest -q                          # fast, no sockets

python dev/fake_edge_server.py &                 # 1 = Heat, 2 = Timer, 3 = Heat
python dev/edge_modbus_test.py --host 127.0.0.1 --tcp-port 5020 --framer socket scan --ids 1-4
python dev/fake_edge_server.py --offset 0 &      # the other wire convention
```

**The fake bus is static apart from its write hook.** There is no thermal model and no background
task, so room temperature, the current schedule period and everything else hold whatever
`build_words` set. Only three things move, and only in response to a write: the manual's documented
mirrors (32→8, 33→9, 34→7), the RTC's consume-on-sync, and the relay at register 2 — on when the
live setpoint is above the room and the stat is on, off otherwise. The relay exists so that
`hvac_action` and the Heating binary sensor have something to do; without it register 2 is a
constant and every "is the boiler on" surface looks frozen rather than merely untested. It is not a
control loop: no switching differential, no TPI cycling.

The hook runs **before** the store is updated, so it must read through the mirrors (register 8 for
on/off, 7 for the setpoint) rather than the registers the request just wrote — an address the
request covers still holds its old value there, and one written to `registers` is about to be
overwritten. That single fact explains the shape of the whole hook.

### The live Home Assistant, and the real thermostat

The dev loop here is a real HA in Docker against the real stat, not just pytest:

| what | where |
|---|---|
| `heatmiser-edge-ha` | HA on :8123, with `custom_components/heatmiser_edge` and `dev/ha-config` bind-mounted, so a `docker restart` picks up an edit — including the card, which is served from `www/` inside that mount |
| `heatmiser-edge-fake` | `dev/fake_edge_server.py` on :5020 |
| `dev/serial_tcp_bridge.py --port /dev/cu.usbserial-0001 --listen 5022` | the real thermostat, as TCP |

The config entry points at `host.docker.internal:5022` with framer `rtu` — so **HA talks to the real
stat, not the fake bus**. Check which before concluding anything from what the UI shows.

**The bridge serves one client at a time.** To read the real stat with `dev/edge_modbus_test.py`,
stop HA first (`docker stop heatmiser-edge-ha`), read, then start it — connecting alongside HA does
not work, and two masters on a half-duplex wire would collide anyway.

### On real hardware, on macOS

```sh
python dev/edge_modbus_test.py --port /dev/cu.usbserial-0001 detect --ids 1
python dev/edge_modbus_test.py --port /dev/cu.usbserial-0001 dump --unit 1
```

**Use `/dev/cu.*`, never `/dev/tty.*`.** On macOS the `tty.*` node waits on carrier detect: opening
it with DTR deasserted blocks for ever, which looks exactly like a hung tool. `cu.*` is the outbound
node and is the only correct choice here.

**Do not put a USB-serial adapter behind a monitor hub.** A CP2102 on the Studio Display's hub chain
answered exactly one request, then dropped off the USB bus entirely and took `/dev/cu.usbserial-0001`
with it. Moved to a port on the Mac itself it is 10/10 reliable. Every "the stat has gone silent"
symptom on 2026-08-12 was this and nothing else.

Tests must not open sockets — `pytest-homeassistant-custom-component` fails the teardown if they do.
Any test that creates a config entry needs the `mock_hub` fixture.

`tests/test_hub.py` runs against **`modbus-connection`'s own in-memory mock**
(`MockModbusConnection` / `MockModbusUnit`, also available as the auto-registered `mock_modbus_*`
fixtures), so the stores and every error path come from upstream. Its `_BusUnit` adds the two things
a *bus* test needs and a device mock has no reason to provide: a request that takes time on the
wire, and the pacing wrapper the real pymodbus backend puts around every operation. Reaching into
the connection's `_pacer` there is deliberate — it means the gap and the lock those tests assert are
the library's real implementation, which is what the integration now depends on. `_BusMock` also
models pymodbus's consecutive-silence disconnect, because that is a behaviour the hub has to
survive rather than an artefact worth faking.

**hassfest sorts the manifest**: `domain`, `name`, then strictly alphabetical. Adding a key in the
place that reads best fails CI.

Release: bump `manifest.json` → tag `vX.Y.Z` → GitHub release. CI is hassfest, HACS (`ignore: brands`
permanently) and pytest on 3.14. No ruff, no mypy.

`strings.json` is authored and **copied byte-for-byte** to `translations/en.json`; a test gates that.

A brand icon already ships, but from `home-assistant/brands`, not from this repo:
`custom_integrations/heatmiser_edge` there (added by
[PR #7777](https://github.com/home-assistant/brands/pull/7777)) carries the identical icon/logo
files as `core_integrations/heatmiser` — same bytes, deliberately reusing the manufacturer's mark
for the sibling protocol. Custom-integration folders in that repo cannot symlink to a core one (only
core-to-core symlinks are allowed), so the files are duplicated rather than shared by reference, but
the two show the same flame logo in the integrations list as a result. Confirmed 2026-08-26; the CI
HACS check's `ignore: brands` was removed the same day since the domain does have a valid entry.

## Still open, to settle on hardware

Record each answer here as it lands, so nobody re-derives it.

### Settled on hardware, 2026-08-12 — one Heat, firmware 48, unit id 1, CP2102 adapter

- **The register base is 0-based (offset −1)** — manual N at wire N−1, the standard Modbus
  convention. `detect` called it decisively on unit 1: at offset 0 the "mode" slot reads 200, the
  20.0 °C override setpoint, which is not a mode. **This is the measurement the search was deleted
  on the strength of** — it is now `DEFAULT_REGISTER_OFFSET`, not something re-derived per install.
- **8N1 at 9600 works.** Parity E and O, and 2 stop bits, are all silent — so 8N1 is not merely
  assumed any more. No other baud answered.
- **Model scoring works on real firmware**: heat 14, timer 0, against a threshold of 5.
- **A 50-register read in one packet works** — 105 bytes, CRC good, ~150 ms end to end. The v1
  one-FC03-per-thermostat poll is sound.
- **Response latency is 31–55 ms to first byte**, so `SCAN_TIMEOUT` of 0.5 s is generous.
- **An unfitted floor probe reads 0xFFFE, not 0 and not 0xFFFF.** This is a problem: 0xFFFE is −2
  two's complement, i.e. −0.2 °C, which sits *inside* `decode.py`'s −40…150 °C plausibility band.
  So register 4 currently ships a floor-temperature entity reading −0.2 °C where it should report
  unknown. Register 5 unfitted does read 0, and decodes to unknown correctly. One unit, so the
  sentinel is not yet proven — confirm on a second stat before special-casing 0xFFFE.
- **Register 42 (keylock) reads 0xFFFF** on a stat with no keylock set, and currently decodes to
  `True`. Another reason it stays in `READ_ONLY_RW`.

### Settled on hardware, 2026-08-13 — the same Heat, over `dev/serial_tcp_bridge.py`

Measured from HA's own debug log with `pymodbus` at debug, which timestamps every byte on the wire
and brackets it against the `call_service` and `state_changed` events. **Do it this way.** An
earlier pass at this read the recorder database instead, saw rows carrying correct values, and
concluded the stat propagates instantly — the opposite of the truth. The recorder records *what*
was published, never *which read* published it, and with more than one read per write that is not
enough to reason from.

- **The thermostat takes a write immediately and propagates it slowly, and that gap is the whole
  problem.** Register 34 held the newly written value in the +80 ms read-back every time, echoed
  and stored — while register 7, which every UI surface actually reads, still held the *old*
  target. So the immediate read-back finds nothing new, no state change fires, and the card sits
  still. Over four writes register 7 caught up at ≤0.33 s once, +0.36 s once and +0.97 s twice, and
  the relay at register 2 flipped in the same read as 7. The spread, with no failures and no CRC
  errors, looks like the stat refreshing 7 on an internal cycle: the wait is just where the write
  lands in it. Under a second, but not predictably so — which is why `SETTLE_PROBES` is a schedule
  that stops on first change rather than a single delay.
- **A fixed settle delay is always the wrong answer.** The first version of this was one read five
  seconds after the write, and five seconds is exactly what the user then saw — the UI was waiting
  out our timer, not the thermostat. A widening schedule gives back the ~700 ms the stat needs.
- **But the schedule must not stop at the first change either.** The stat's registers do not move
  together: on one write register 7 caught up at 1.6 s while the relay had not moved at all, and
  stopping there published the setpoint and left the relay stale until the next poll — the original
  complaint, moved rather than fixed. Every probe now runs. Gated by
  `tests/test_entities.py::test_an_early_change_does_not_cancel_the_rest_of_the_settle`.
- **The relay at register 2 lands in a probe of its own about half the time.** Over five writes with
  the full schedule running it came with register 7 at 0.3 s and 0.8 s, and *alone* at 1.6 s twice —
  including the two cases that had previously waited 62 s for the poll. Nothing has ever reached the
  3.0 s probe, so `SETTLE_PROBES` ends at 6.0 s as a backstop and no further: the relay is a control
  *output*, answering to the stat's own algorithm rather than to our write, so past a few seconds
  the scan interval is the right instrument and more probes only spend bus time. Anyone wanting a
  livelier Heating entity should shorten the scan interval.
- **The climate entity and the Heating binary sensor are never out of step.** Every pair of recorder
  rows shares a timestamp to the millisecond, which is `async_set_updated_data` publishing one
  snapshot to every entity on the unit. A device page that looks like it lags the thermostat card is
  not lagging it; both are waiting on the same read.
- **It is not the frontend, and it is not the card's debounce.** Calling `climate.set_temperature`
  from Developer Tools → Actions bypasses the card entirely and took 5166 ms against the card's
  5162 ms — indistinguishable. That control is what proves the latency is ours; run it before
  blaming anything above Home Assistant.
- **The wire itself is healthy and fast.** Eight consecutive polls: 22–26 ms to first byte, 132–135
  ms for the whole 105-byte FC03, no retries, no CRC errors. The byte-at-a-time TCP delivery in the
  pymodbus debug log is not a bridge fault — it is 9600 baud (105 bytes × 10 bits ÷ 9600 = 109 ms),
  and it matches the measurement exactly.

### The keypad menu is numbered differently from the registers

`docs/Edge-Manual.md`'s "Optional Settings — Feature Table" is what the owner sees, and its numbers
are **not** register numbers. The mapping, worth keeping because it is a trap:

| keypad menu | register | |
|---|---|---|
| 01–07 | 21–27 | temperature format → optimum start, in order |
| 08 Rate of Change | **13** | |
| 09 Program Mode | 29 | |
| 10 Daylight Saving | 30 | |
| 11 Communications ID | 31 | |
| 12 Program Type | **28** | note the swap with 09 |

Confirmed on hardware: setting keypad feature **07** to 4 moved register **27** from 0 to 4, and
nothing else in the block changed. So a user reporting "I changed setting 07" means register 27.

The table has 14 rows (A, P, 01–12) and **there is no TPI entry**, nor any installer or engineer
menu anywhere in the manual. Registers 43 and 44 are therefore Modbus-only — which is why they ship
as nothing at all (see above).

### Open, and new as of 2026-08-13

- **What registers 43 and 44 actually hold.** Both read 20 where the manual documents 0–3 and 0–5,
  and neither is reachable from the keypad, so there is no way to read the stat's own idea of the
  setting back and compare. The leading guess is that 43 stores the *cycle time in minutes* rather
  than the manual's enum index — 20 minutes being exactly the manual's `01` = 3 cycles per hour —
  but that does not explain 44 also being 20, and it is only a guess. Settling it means writing a
  known value over Modbus and watching the boiler's behaviour, which is not something to do on
  someone's heating on a whim. Until then, whether TPI is even active is unknown.

### The card: `custom:heatmiser-edge-schedule-card`

Shipped **inside the integration**, not as a separate HACS frontend repository, because the card and
`set_schedule` are one feature — versioning them apart would let a card call an action that is not
there. `async_setup` registers `www/heatmiser-edge-schedule-card.js` at `/heatmiser_edge/<file>` and
calls `frontend.add_extra_js_url` with `?v={version}` from the manifest, so a browser picks up a new
card on upgrade instead of serving yesterday's.

```yaml
type: custom:heatmiser-edge-schedule-card
entity: sensor.edge_heat_1_weekly_program
```

- **`after_dependencies`, never `dependencies`.** A hard dependency on `frontend` breaks the test
  suite outright — `pytest-homeassistant-custom-component` has no `hass_frontend` module, so
  `frontend` fails to set up and takes every config-entry test with it. It would also block a
  headless install for a card it cannot show. Registration is skipped quietly when `frontend` is not
  in `hass.config.components`.
- **No build step and no imports.** The usual workaround for using Lit without a bundler — borrowing
  the base class off an existing element's prototype chain — breaks whenever the frontend
  reshuffles. It is a table of inputs; plain DOM outlives that.
- **It configures from the UI**: `getConfigElement` returns
  `heatmiser-edge-schedule-card-editor`, one `ha-entity-picker` filtered to entities that actually
  carry a programme, plus `getStubConfig` so the picker's preview lands on a real thermostat.
  **`ha-entity-picker` has to be coaxed into existing** — the frontend loads its editor elements
  lazily, so on a fresh page it is undefined and `whenDefined` would wait for ever. Asking the
  built-in entities card for *its* config element is the standard way to make that bundle load; it
  is a side effect, not a use of the card. If it is still missing, the editor falls back to a text
  field, because a blank editor leaves a user no way back to a working card.
- **It never re-renders while you are editing.** Input events mutate the working copy without
  touching the DOM, so a poll landing mid-edit cannot move the field under the cursor. It re-syncs
  from the entity only when nothing is unsaved. Switching day discards unsaved edits deliberately,
  since keeping them would copy one day's changes onto another.
- **Validation stays on the Python side.** The card sends the grid and shows whatever
  `ServiceValidationError` comes back. Reimplementing the rules in JS is how the two drift.
- **It sends the group key**, `weekdays` / `weekend` / `all`, and lets `resolve_days` expand it, so
  the day-grouping rule lives in one place.
- `tests/test_packaging.py::test_the_card_and_the_integration_agree_on_the_grid` gates the three
  things the card hardcodes — day names, register 29's labels, and the attribute keys — because
  nothing the browser runs is covered by pytest.

**When editing the card, hard-refresh.** The static path is registered with cache headers and the
version only changes on release, so a plain reload serves the old file.

### Settled on hardware, 2026-08-13 — the weekly program on the same Heat

`python dev/edge_modbus_test.py --port /dev/cu.usbserial-0001 schedule --unit 1`, with HA stopped.

- **The register base holds all the way to 218.** The whole grid decodes to *exactly* the manual's
  documented default programme, with Sunday and Saturday at 09:00/22:00 and the weekdays at
  07:00/09:00/16:00/22:00 — which is independent corroboration of offset −1 far outside the 1–50
  window that settled it, and of the 4-registers-per-period stride.
- **Register 28 reads 0**, so this stat runs 4 periods a day. Periods 5 and 6 are still stored: hour
  24 with the manual's default temperatures behind them.
- **The grid is fully populated even though register 29 is Non-programmable.** The programme is
  *stored* whether or not the thermostat runs it — which is why the card shows it read-only in that
  mode rather than blanking, and why `get_schedule` does not refuse there.
- This stat's programme has never been customised; it is the factory default throughout.

### Open, and new with the weekly program

- **Which day block a 5/2 or 24 Hour stat actually reads.** The manual never says. `set_schedule`
  sidesteps it by writing every day that shares the program, so the stat behaves correctly whichever
  block it reads — but it is still unknown, and it is what a schedule *editor* needs in order to
  show "Mon–Fri" as one row rather than five. Settle it on hardware: set 5/2 on the keypad, change
  a weekday time from the keypad, then dump 75–98 and 99–122 and see whether both moved.
- **Whether the thermostat cares about period order at all.** We refuse a day that does not run
  forwards, and a period that follows a switched-off one. Both are guards inferred from the manual's
  own defaults, not documented rules — the stat may well accept either. Loosening them is safe;
  they exist because an out-of-order program has no defined behaviour to reason about.
- **Whether a schedule write is accepted while the stat is in Schedule mode**, or whether it needs
  to be idle. Nothing suggests it matters, and the read-back would show it.

### Still open

- **Sub-zero temperatures.** The manual documents "0~0xffff" with no word on negatives. `decode.py`
  reads two's complement and rejects anything outside −40…150 °C. Confirm with a stat in an
  unheated space in winter. Note this is entangled with the 0xFFFE finding above.
- **Register 22 in °F mode.** The manual gives °C values 5/10/20/30 and °F labels 1/2/4/6 without
  saying whether the *wire* values change. We ship the same wire values with °F labels.
- **Whether register 3 tracks the sensor selection in register 25.** This decides
  `climate._CURRENT_TEMP_REGISTER`; the fallback to the built-in sensor means getting it wrong
  degrades rather than breaks.
- Whether writing register 34 in **Schedule** mode really creates an override, or is ignored.
  Change over mode is settled above — 7 follows 34 immediately there — so the remaining question is
  narrowly about the mode that has a period to override. If it is ignored, the climate entity should
  say so rather than offer a control that does nothing.
- Register 42's write semantics — the gate on promoting it out of `READ_ONLY_RW`.
- Whether the thermostat clamps out-of-range writes silently. FC06's echo will tell you.
- **Whether the RTC sync actually lands.** Verified against `dev/fake_edge_server.py` only. On
  hardware: `python dev/edge_modbus_test.py --port /dev/cu.usbserial-0001 settime --unit 1`, then
  check the keypad. The command prints registers 47–50 afterwards; all-0xFFFF is the manual's
  success marker and is the only confirmation available, since there is no readable clock. Note the
  fake server models that consume-on-sync by rewriting `set_values` inside its action hook, because
  the hook runs *before* the store is updated — writing to `registers` at an address the request
  covers is overwritten a moment later.
- **Whether a stat with DST enabled (register 30) shifts a written RTC on top.** The manual makes
  the stat own the seasonal change — register 12 reports whether summer time is *in force* — but
  never says what a written timestamp is taken to mean. `set_time` sends plain local wall-clock
  time, which is unambiguously right with DST disabled and is the reason the action offers the
  flag. Settle it by writing a known time in summer with DST on and reading the keypad: if the stat
  displays an hour later than what was sent, it shifts, and the action should send standard time
  when register 30 is 1.

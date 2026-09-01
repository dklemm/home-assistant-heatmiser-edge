# Heatmiser EDGE (Modbus) for Home Assistant

A HACS custom integration for **Heatmiser EDGE** thermostats over RS485 Modbus — no neoHub, no
cloud account, nothing leaving the house. One config entry covers a whole bus; every thermostat on
it becomes its own Home Assistant device.

- **EDGE Heat** thermostats get a full **climate** entity: current and target temperature, on/off,
  and the thermostat's own operation modes (schedule, hold, advanced, away, frost) as presets.
- **EDGE Timer** (timeclock) units get a switch, a mode select and their relay state.
- Both get their settings as numbers and selects: switching differential, output delay, floor
  limit, optimum start, TPI, program mode, frost setpoint and the rest.
- **One thermostat going quiet doesn't take the others down.** It goes unavailable on its own; the
  rest of the house keeps updating.
- Works over a **USB RS485 adapter** or an **RS485-to-Ethernet gateway**.

## Requirements

An RS485 connection to the thermostats' bus, and a Communications ID (Modbus) set on each
thermostat's own keypad, under **feature 11**. **The factory default of 00 disables Modbus on that
thermostat entirely** — that is the single most common reason nothing is found. Valid IDs are 1–32,
and each must be unique on the bus.

The bus runs at 9600 baud, 8 data bits, no parity, 1 stop bit. The manual specifies the baud and the
parity and is silent on the rest, but nothing else has ever answered on real hardware, so setup does
not ask: the serial step wants the port and nothing more.

## Installation

**HACS** — add `https://github.com/dklemm/home-assistant-heatmiser-edge` as a custom repository of
type *Integration*, install it, restart Home Assistant, then **Settings → Devices & services → Add
integration → Heatmiser EDGE**.

**Manually** — copy `custom_components/heatmiser_edge/` into your `config/custom_components/` and
restart.

## Setup

Choose serial or gateway, give the connection details, and say which unit IDs to look for — the
default `1-32` sweeps the whole range, which takes around 18 seconds, so narrow it if you know the
IDs your thermostats are set to. Then confirm what was found: the EDGE protocol has **no model
register**, so whether each thermostat is a Heat or a Timer is worked out from what its registers
look like — accurate in practice, but it is offered as a default you can change, and any uncertain
guess is flagged.

**Register addressing** is not asked about at all. The manual numbers registers from 1 but never
says whether the wire agrees; it does not — register N lives at address N−1, the standard Modbus
convention, confirmed across the whole register map on hardware. Register 31 holds the ID the
thermostat was addressed by, so every read checks it, and a thermostat that ever disagreed would say
so in the log.

## Two behaviours worth knowing

**Setting a temperature is an override, not a mode change.** It writes the thermostat's "Over right
and Hold Set temperature" register, which holds until the next schedule period — exactly what you
would expect from turning a dial. The integration deliberately does *not* also switch the
thermostat into Hold, which would take it off its programme for good.

**Turning a thermostat off leaves its preset alone**, so turning it back on resumes whatever it was
doing. That is the thermostat's own behaviour, and it is kept.

## Setting the clock

Each thermostat keeps its own clock, and its weekly programme runs against it — a thermostat whose
time was never set heats at the wrong hour, and nothing in Home Assistant will tell you. The
**Heatmiser EDGE: Set time** action fixes it:

```yaml
action: heatmiser_edge.set_time
target:
  device_id: <a thermostat, or the bus to do all of them at once>
```

With no fields it sends Home Assistant's own local time. `datetime` sends a specific time instead,
and `dst` also turns the thermostat's daylight saving on or off — the same setting as the
*Daylight saving* switch. With daylight saving off on the thermostat, it keeps exactly the time sent,
so re-running this in spring and autumn handles the change.

The thermostat cannot be asked what time it thinks it is, so there is nothing to show as an entity
and no read-back to check. Running the action on a schedule — say daily, or on Home Assistant start
— is the way to keep a drifting clock right:

```yaml
triggers:
  - trigger: time
    at: "03:30:00"
actions:
  - action: heatmiser_edge.set_time
    target:
      device_id: <the bus>
```

## Options

Polling interval (60 s by default), response timeout, and each thermostat's name and model. There is also **Allow changing settings** — turn it off and every control
disappears, leaving a read-only integration. Readings are unaffected.

To add or remove a thermostat, use **Reconfigure** on the integration; it re-scans the bus and keeps
the names you have already given.

## What is deliberately not here

- **The weekly programme** (registers 51–218). Editing schedules is planned; polling all of it would
  add over a hundred entities per thermostat for data that changes twice a year.
- **Factory reset** (register 46). It is irreversible and one mis-tap away, so it has no entity at
  all. It is reachable only from the field CLI below, behind an explicit flag.
- **Writing the keypad lock** (register 42). The manual documents that 0 cancels the lock but not
  what writing the password does, so it ships as a read-only sensor until that is confirmed on
  hardware. Same for the away-until deadline, which needs three registers written together — the
  clock needed the same treatment and got it as an action, which is where away is headed.
- **Changing a thermostat's Communications ID** — it would move the thermostat mid-poll and orphan
  its Home Assistant device.

## Troubleshooting

Nothing found? In order: check A and B are not swapped, check the bus is terminated, check each
thermostat's Communications ID is not 0, and on a gateway try the other protocol setting (cheap
gateways are split between real Modbus TCP and transparent RTU-over-TCP).

The field CLI in `dev/` answers these faster than the UI can, and needs no Home Assistant install:

```sh
python dev/edge_modbus_test.py --port /dev/ttyUSB0 detect   # is the wire 0-based?
python dev/edge_modbus_test.py --port /dev/ttyUSB0 scan     # who is out there?
python dev/edge_modbus_test.py --host 10.0.0.5 --framer rtu dump --unit 1
python dev/edge_modbus_test.py --port /dev/ttyUSB0 settime --unit 1    # sync the clock
```

Failing that, **download diagnostics** from the integration page: it contains every raw register
value, which is enough to diagnose almost anything without access to your bus.

## Development

```sh
python3.14 -m venv .venv && source .venv/bin/activate     # Home Assistant needs >= 3.14
pip install -r requirements_dev.txt
pytest -q                                                  # fast, no sockets

python dev/fake_edge_server.py &     # a fake bus: 1 = Heat, 2 = Timer, 3 = Heat
pip install homeassistant
mkdir -p config/custom_components
ln -s "$PWD/custom_components/heatmiser_edge" config/custom_components/heatmiser_edge
hass -c config    # add the integration -> gateway -> 127.0.0.1:5020, Modbus TCP, IDs 1-3
```

The fake server can pretend to be the other register base (`--offset 0`), drop a thermostat off the
bus (`--silent 2`) or run one in °F (`--fahrenheit 1`) — which is how the detection and the
per-thermostat availability get exercised without hardware.

Code changes need a full Home Assistant restart; Python is not hot-reloaded, and the UI's "Reload"
re-runs setup against the old code.

See `CLAUDE.md` for the design decisions and the protocol facts worth not re-deriving.

## Licence

GPL-3.0. Heatmiser and EDGE are trademarks of Heatmiser UK Ltd; this project is not affiliated with
or endorsed by them.

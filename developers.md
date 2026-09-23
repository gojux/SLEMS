# SLEMS – developer notes

## Conventions

- All code, identifiers and comments are in English. User facing texts live in
  `strings.json` / `translations/*.json` (English and German).
- Both READMEs (`README.md`, `README.de.md`) are kept in sync.
- Sign conventions used everywhere:
  - grid power: **+ import / − export**
  - battery power: **+ charge / − discharge**
  - house power = grid + PV − battery
- License: GPL-3.0 (compatible with Omnibattery, from which the Marstek
  register map and control sequence are taken).

## Development environment

Everything runs in Docker Compose; no local Python setup is needed.

```bash
docker compose up -d                  # Home Assistant + two simulated batteries
docker compose logs -f homeassistant  # follow logs
docker compose restart homeassistant  # after code changes
docker compose --profile tests run --rm tests   # run the test suite
docker compose down
```

| Service | Purpose |
|---|---|
| `homeassistant` | HA 2026.9.3 (same version as production) on <http://localhost:8123>. `custom_components/slems` is mounted read-only. |
| `venus-sim-1`, `venus-sim-2` | Modbus TCP simulators of a Venus E 3.0 (`dev/venus_sim/simulator.py`), reachable as host `venus-sim-1` / `venus-sim-2`, port 502. |
| `tests` | pytest in an image based on the HA image, so Python and HA versions match production. |

`dev/config/configuration.yaml` provides simulated measurements
(`sensor.smart_meter_power`, `sensor.pv_power`, driven by `input_number`
sliders) and helpers for a simulated consumer. Everything else in
`dev/config` is created by Home Assistant and ignored by git. To start from
scratch, stop the stack and delete everything in `dev/config` except
`configuration.yaml`.

The simulator deliberately has no dependencies (plain asyncio Modbus TCP) so it
is independent of pymodbus API changes. Like the real device it accepts only a
single TCP connection.

## Architecture

```
custom_components/slems/
  __init__.py        setup/unload, builds one driver per battery subentry
  config_flow.py     main entry (system), options flow, battery subentry flow
  coordinator.py     SlemsCoordinator: polls batteries, reads grid/PV entities → SystemSnapshot
  entity.py          base classes (system device, battery devices)
  sensor.py          system and battery sensors
  select.py          operating mode (off / simulation / active), restored after restart
  drivers/
    base.py          BatteryDriver contract (read_telemetry, apply_power, release_control)
    marstek_venus_e3.py
    modbus_client.py Modbus TCP link with Venus firmware quirks
    ha_entities.py   read-only battery backed by HA entities
```

Planned layers (not implemented yet):

- **Forecast**: consumption forecast for today and tomorrow from recorder
  long-term statistics + weather forecast; PV from the existing HA forecast.
- **Planner**: 15 minute slots, 24–48 h horizon; rule based first, interface
  prepared for a later LP optimisation.
- **Real-time controller**: follows the plan and corrects deviations from the
  grid set point in seconds; the only component that sends commands, and only
  in operating mode *active*.
- **Consumer manager**: switch and number entities, priorities, external block
  via binary sensor.
- **Dashboard**: custom sidebar panel (web component served by the integration).

### Configuration model

- One config entry (`single_config_entry`) holds the system settings in `data`;
  the options flow stores the complete settings in `options`, which then fully
  replace `data` (so clearing an optional field works).
- Every battery is a **config subentry** of type `battery`. Adding, editing or
  removing a subentry triggers the update listener, which reloads the entry.
  The listener is registered at the very start of `async_setup_entry`, so
  subentries added while a reload is still running are not lost.
- Battery entities are registered with `config_subentry_id`, so removing a
  battery also removes its device and entities.

### Adding a battery model

1. Implement `BatteryDriver` in `drivers/<model>.py`.
2. Add the model to `BatteryModel` in `const.py` and to `create_driver()`.
3. Add a subentry step `async_step_<model>` in `config_flow.py` plus
   translations (`strings.json`, `translations/en.json`, `translations/de.json`,
   selector option in `selector.model`).
4. Report optional values via `BatteryTelemetry.extra` and list their keys in
   `extra_telemetry_keys`; matching sensors in `BATTERY_EXTRA_SENSORS` are then
   created automatically.

## Marstek Venus E 3.0

Source: Omnibattery `const/registers_v3.py`, `drivers/marstek.py`,
`infra/modbus_client.py`.

| Register | Key | Type | Scale | Notes |
|---|---|---|---|---|
| 37005 | battery_soc | uint16 | 1 % | see open point below |
| 30001 | battery_power | int16 | 1 W | + charge / − discharge |
| 30006 | ac_power | int16 | 1 W | + discharge / − charge |
| 30100 | battery_voltage | uint16 | 0.01 V | |
| 35000 | internal_temperature | int16 | 0.1 °C | |
| 35100 | inverter_state | uint16 | | 0 sleep, 1 standby, 2 charge, 3 discharge, 4 backup, 5 OTA, 6 bypass |
| 33000 | total_charging_energy | uint32 | 0.01 kWh | |
| 33002 | total_discharging_energy | int32 | 0.01 kWh | |
| 42000 | rs485_control | uint16 | | write 0x55AA = enable, 0x55BB = disable |
| 42010 | force_mode | uint16 | | 0 none, 1 charge, 2 discharge |
| 42020 | set_charge_power | uint16 | W | max 2500 |
| 42021 | set_discharge_power | uint16 | W | max 2500 |

Control sequence (`apply_power`): enable RS485 control → discharge set point →
charge set point → force mode (last). `release_control` zeroes the set points,
sets force mode none and disables RS485 control.

Firmware quirks handled in `modbus_client.py`:

- exception responses carry a wrong MBAP length byte (patched via `trace_packet`),
- ≥150 ms between frames,
- a single TCP connection slot that is released slowly → wait 1 s before reconnecting,
  never reconnect per request,
- retries inside pymodbus (same transaction id) so late replies still match.

Open points to verify on the real device:

- **SoC register**: Omnibattery's code uses 37005 for v3, its register table
  (`site-docs/reference/registers.md`) lists 34002 for `e_v3`. The code is
  what Omnibattery users run in production, so SLEMS uses 37005.
- Omnibattery marks the v3 map as partly untested.
- The device holds only one connection: while Omnibattery is running, a Modbus
  battery in SLEMS cannot connect. Use the read-only *HA entities* battery
  during the transition.

## Decision log

| Date | Decision |
|---|---|
| 2026-09-23 | Custom integration (not an app/add-on): direct entity access, config flow, works on every HA installation type. |
| 2026-09-23 | Batteries as config subentries; count can change at any time. |
| 2026-09-23 | Planner: rule based first, exchangeable for an LP optimisation later. |
| 2026-09-23 | Power distribution between batteries: efficiency optimum. |
| 2026-09-23 | Configuration via config flow and dashboard; own dashboard panel similar to Omnibattery. |
| 2026-09-23 | Default operating mode *simulation*; nothing is sent unless *active*. |
| 2026-09-23 | License GPL-3.0. |

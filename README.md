<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

**English** | [Deutsch](README.de.md)

# SLEMS

SLEMS is a Home Assistant integration that manages home batteries and
controllable consumers. Its goal is to operate the batteries in a grid friendly
way, e.g. by shifting the charging time so that the daily feed-in peak is
absorbed, based on forecasts of consumption and PV production.

The name is a combination of *Slug* (our pet) and *EMS* (energy management system).

> **Status: early development.** SLEMS currently reads batteries and
> measurements and exposes them as entities. Forecasts, planning, consumer
> control and the dashboard are not implemented yet (see [roadmap](#roadmap)).

## Features

| Feature | Status |
|---|---|
| Any number of batteries, added/edited/removed at any time | ✅ |
| Marstek Venus E 3.0 via Modbus TCP | ✅ reading, control prepared |
| Read-only battery from existing entities (e.g. while Omnibattery is in control) | ✅ |
| Smart meter, PV power, PV forecast and weather entity freely selectable | ✅ |
| Operating mode *Off / Simulation (read-only) / Active* | ✅ entity, logic follows |
| Consumption forecast (history + weather) | planned |
| Grid friendly charge planning (peak shaving) | planned |
| Consumer control (switch / power set point, external block) | planned |
| Dashboard with energy flow and daily forecast chart | planned |

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories* → add the URL of this repository, type *Integration*.
2. Install *SLEMS* and restart Home Assistant.

### Manual

Copy `custom_components/slems` into the `custom_components` folder of your
Home Assistant configuration and restart Home Assistant.

Requires Home Assistant 2026.9 or newer.

## Configuration

1. *Settings → Devices & services → Add integration → SLEMS*.
2. Select the grid power entity of your smart meter (positive = import,
   negative = export; enable *invert* if your meter uses the opposite sign),
   optionally PV power, PV forecast and weather.
3. On the SLEMS integration page choose **Add battery** for every battery.

### Batteries

- **Marstek Venus E 3.0**: host/IP, port (502) and Modbus unit ID. The battery
  accepts only **one** Modbus TCP connection. Never let two integrations
  (e.g. SLEMS and Omnibattery) talk to the same battery at the same time.
- **Existing Home Assistant entities (read-only)**: state of charge and power
  sensors of a battery that is controlled by something else. SLEMS never sends
  commands to such a battery. This allows running SLEMS in simulation mode side
  by side with an existing battery integration.

### Operating mode

The entity `select.slems_operating_mode` switches between:

- **Off** – nothing is planned or sent.
- **Simulation** – forecasts and plans are computed and shown, but no command
  is sent to batteries or consumers. This is the default.
- **Active** – plans are executed.

## Roadmap

1. Consumer model (on/off and power controlled, priorities, external block input)
2. Consumption forecast from history and weather
3. Planner (grid friendly charging, peak shaving) and real-time controller
4. Dashboard: energy flow diagram, daily forecast and plan chart

## Development

See [developers.md](developers.md).

## License

GPL-3.0, see [LICENSE](LICENSE). The Marstek Venus register map and control
sequence are based on [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).

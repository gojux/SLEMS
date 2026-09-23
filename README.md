<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

**English** | [Deutsch](README.de.md)

# SLEMS

SLEMS is a Home Assistant integration that manages home batteries and
controllable consumers. Its goal is to operate the batteries in a grid friendly
way, e.g. by shifting the charging time so that the daily feed-in peak is
absorbed, based on forecasts of consumption and PV production.

The name is a combination of *Slug* (our pet slug) and *EMS* (energy
management system); the slug in the logo carries a battery instead of a shell.

> **Status: early development.** SLEMS currently reads batteries and
> measurements and exposes them as entities. Forecasts, planning, consumer
> control and the dashboard are not implemented yet (see [roadmap](#roadmap)).

## Features

| Feature | Status |
|---|---|
| Any number of batteries, added/edited/removed at any time | ✅ |
| Marstek Venus E 3.0 via Modbus TCP | ✅ reading, control prepared |
| Read-only battery from existing entities (e.g. while Omnibattery is in control) | ✅ |
| Smart meter, PV power and weather entity freely selectable | ✅ |
| PV forecast from any solar forecast integration (Forecast.Solar, Solcast, …) | ✅ |
| Consumers with own power/energy sensors, inside or outside the smart meter | ✅ configuration |
| Heat pump as consumer type (weather dependent forecast) | ✅ configuration |
| Operating mode *Off / Simulation (read-only) / Active* | ✅ entity, logic follows |
| Vacation switch | ✅ entity, logic follows |
| Import peak shaving at low state of charge (manually enabled) | ✅ entities, logic follows |
| Consumption forecast (history + weather) | planned |
| Grid friendly charging: absorb PV feed-in peaks | planned |
| Consumer control (switch / power set point, external block, priority) | planned |
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
3. On the SLEMS integration page choose **Add battery** for every battery and
   **Add consumer** for every consumer you want to measure or control.

### PV forecast

Every integration that provides a solar forecast for the Home Assistant energy
dashboard can be selected, e.g. Forecast.Solar or Solcast. Several entries
(e.g. one Forecast.Solar entry per roof plane) are summed up.

### Weather (optional)

The weather entity improves the consumption forecast, especially for a heat
pump. Recommendations for Vorarlberg / the Alpine region:

- **GeoSphere Austria AROME**: high resolution model of the Austrian weather
  service, well suited for alpine valleys. Available in Home Assistant through
  custom integrations such as
  [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) or
  [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS).
  The built-in *GeoSphere Austria* integration only provides station
  measurements, no forecast.
- **Open-Meteo** (built-in integration): no account needed, combines several
  weather models.
- **Met.no** (Home Assistant default): works everywhere, less detailed in
  alpine terrain.

### Consumers

Every consumer needs its own power **and** energy sensor.

- **Included in smart meter**: enable if the consumer is behind the smart
  meter (its consumption is already part of the grid power). Disable for
  consumers on a separate supply.
- **Type**: *heat pump* (heating and hot water, forecast from the weather),
  *heating rod* (e.g. hot water in summer) or *other*. Heat pump and heating
  rod may run at the same time.
- **Control**: none (measurement only), on/off via a switch, or a power set
  point via a number entity in W. For controlled consumers an entity can be
  selected that blocks them externally (while it is on, SLEMS leaves the
  consumer alone), plus a priority (1 = highest).

### Batteries

- **Marstek Venus E 3.0**: host/IP, port (502) and Modbus unit ID. The battery
  accepts only **one** Modbus TCP connection. Never let two integrations
  (e.g. SLEMS and Omnibattery) talk to the same battery at the same time.
- **Existing Home Assistant entities (read-only)**: state of charge and power
  sensors of a battery that is controlled by something else. SLEMS never sends
  commands to such a battery. This allows running SLEMS in simulation mode side
  by side with an existing battery integration.

### Operating mode

The entity *SLEMS Operating mode* switches between:

- **Off** – nothing is planned or sent.
- **Simulation** – forecasts and plans are computed and shown, but no command
  is sent to batteries or consumers. This is the default.
- **Active** – plans are executed.

### Further settings (entities)

- *Vacation* (switch) – the household is away; switch manually or by an automation.
- *Import peak shaving* (switch) – off by default. When enabled and the total
  state of charge is at or below *Peak shaving state of charge threshold*, the
  batteries only discharge to keep the grid import below *Peak shaving grid
  import limit*.

## Roadmap

1. Consumer model: control details (minimum runtimes, hysteresis, …)
2. Consumption forecast from history and weather
3. Planner (grid friendly charging, peak shaving) and real-time controller
4. Dashboard: energy flow diagram, daily forecast and plan chart

## Development

See [developers.md](developers.md).

## License

GPL-3.0, see [LICENSE](LICENSE). The Marstek Venus register map and control
sequence are based on [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).

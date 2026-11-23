# Installation and setup

[Back to the README](../../README.md#quick-start) · [Deutsch](setup.de.md)

<!-- toc -->

- [Requirements](#requirements)
- [Installation](#installation)
  - [Marketplace or HACS (custom repository)](#marketplace-or-hacs-custom-repository)
  - [Manual](#manual)
- [Setup](#setup)
- [Smart meter over Modbus (optional)](#smart-meter-over-modbus-optional)
- [PV forecast](#pv-forecast)
- [Weather (optional)](#weather-optional)
- [Consumption forecast](#consumption-forecast)
- [Language](#language)

<!-- /toc -->

## Requirements

- Home Assistant 2026.10 or later; from 2026.11 the built-in Marketplace,
  before HACS.
- A smart meter in Home Assistant that reports the grid power (import and
  export) continuously, e.g. a Shelly Pro 3EM. The more often it reports, the
  more precisely SLEMS controls (e.g. every second).
- Optional: a PV forecast (e.g. Forecast.Solar), batteries and consumers that
  SLEMS should control.

## Installation

### Marketplace or HACS (custom repository)

Home Assistant 2026.11 replaced HACS with the built-in **Marketplace**
(**Settings → Marketplace**); existing HACS installations move over by
themselves.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

The button opens SLEMS in the Marketplace (or HACS) of your Home Assistant
instance and adds the custom repository; then continue with step 2. Or by
hand:

1. Marketplace: **Settings → Marketplace**, add the custom repository
   `https://github.com/gojux/SLEMS`, type **Integration**. Custom repositories
   need the Marketplace to be connected to GitHub; it asks for it.

   HACS (up to Home Assistant 2026.10): HACS → ⋮ → **Custom repositories** →
   add `https://github.com/gojux/SLEMS`, type **Integration**.
2. Install **SLEMS** and restart Home Assistant.

### Manual

Copy `custom_components/slems` into the `custom_components` folder of your
Home Assistant configuration and restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → SLEMS**.
2. Select the devices of your smart meter and your PV systems (optional,
   preselected from the energy dashboard of Home Assistant; the devices that
   fit best come first, e.g. a Shelly Pro 3EM or the meter of an inverter).
   SLEMS suggests their sensors and fills in what the energy dashboard
   already knows, including the sign of its grid power, the PV forecast
   entries and the weather entity. Check the grid power entity (positive =
   import, negative = export; the form shows its current value; enable
   **Invert grid power sign** if your meter uses the opposite sign), and optionally PV power,
   PV forecast and weather. Several PV systems (e.g. roof and balcony) are
   summed up.
3. On the SLEMS integration page choose **Add battery** for every battery and
   **Add consumer** for every consumer you want to measure or control (see
   [Batteries](batteries.md) and [Consumers](consumers.md)).
4. SLEMS first runs in the operating mode **Observe only**: the dashboard
   shows the forecast and the plan, nothing is controlled yet. To control,
   turn off other controls of the same batteries and consumers and set the
   operating mode to **Active** (see [Operation and control](control.md)).

To change the system settings later, select the gear icon of the SLEMS entry:
a menu offers **Detect devices again** (the guided steps of the first setup)
or one section, **Grid**, **PV & forecast** or **Consumption & calendar**.
Each section is saved on its own; the other settings stay.

**Phases** (optional): if your smart meter measures each phase, you can
select its **Grid power L1**, **L2** and **L3** in the SLEMS settings. SLEMS
then learns which phase each device is on and shows it; this does not
affect the control yet (see
[Phase of batteries and consumers](control.md#phase-of-batteries-and-consumers)).

## Smart meter over Modbus (optional)

The entity of the smart meter is updated once per polling cycle of its
integration, often only about once per second, and then shows only a part of
the meter's values. With **Also read the smart meter over Modbus** SLEMS reads
the grid power directly from a SunSpec meter every 0.5 seconds (0.2 to
5 s). The control sees every value
sooner; the gain is largest with fast batteries.

- SLEMS uses the shared Modbus connection of Home Assistant. As long as the
  inverter integration keeps the inverter's only Modbus TCP connection to
  itself, a Modbus proxy has to be set up, e.g. the app [Modbus
  Proxy](https://github.com/TCzerny/ha-modbusproxy). SLEMS asks the shared
  connection for a timeout of 1 second; if another integration on the same
  device needs longer, the longer one applies. A meter that does not answer
  is reconnected at once, which other integrations on the same device notice
  as a short reconnect.
- SLEMS finds the meters in the SunSpec model chain; with several meters
  (e.g. export+import and consumption) you choose the one at the grid
  connection point, the current power of each helps to recognise it.
- The sign is found automatically by comparing a few values with the grid
  power entity; this needs some import or export (at least 100 W). Otherwise
  choose it by hand.
- The entity stays required: it is the history, the basis of the forecasts
  and the fallback. While no Modbus value is newer than 3 intervals (at least
  5 seconds), SLEMS uses the entity; after 5 minutes without Modbus a repair
  issue appears. The sensor **Smart meter update interval** shows the source as
  attribute `source`.
- When the source is switched, the learned battery response times start
  again, since they contain the delay of the old source.

## PV forecast

Every integration that provides a solar forecast for the Home Assistant energy
dashboard can be selected, e.g. Forecast.Solar or Solcast. Several entries
(e.g. one Forecast.Solar entry per roof plane) are summed up. SLEMS uses the
finest resolution the provider delivers (e.g. 15 or 30 minutes).
Forecast.Solar marks each period by its end; SLEMS takes this into account.

**Recommendation: [Helios Forecast](https://github.com/ReikanYsora/Helios-Forecast)**
(open source, no account or API key, computed locally with weather data from
Open-Meteo). It learns from the production of your own PV system, including
shading by trees or mountains, soiling and an orientation that is a few
degrees off, and uses the existing long-term statistics of your energy sensor
right away. It provides a forecast for the energy dashboard in 15 minute
steps, so it can be selected in SLEMS directly.

## Weather (optional)

The weather entity improves the consumption forecast, especially for a heat
pump. The forecast of the national weather service fits best:

| Country | Recommendation | Why |
|---|---|---|
| Germany | [DWD Weather](https://github.com/FL550/dwd_weather) (HACS) | forecast of the German weather service for a station near you, hourly |
| Austria | [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) or [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS) | high resolution AROME model of GeoSphere Austria, well suited for alpine valleys; the built-in *GeoSphere Austria* integration only provides measurements, no forecast |
| Switzerland | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | local forecast of MeteoSwiss for your postcode, hourly |
| Liechtenstein | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | MeteoSwiss covers Liechtenstein as well |
| Anywhere | **Open-Meteo** (built-in integration) | no account needed, combines several weather models; a good choice without an extra integration |

**Met.no**, the Home Assistant default, works everywhere but is less detailed
in alpine terrain.

## Consumption forecast

SLEMS learns your house's consumption from the long-term statistics of Home
Assistant and forecasts today and tomorrow by the hour: heat pumps by the
outdoor temperature, public holidays like weekends. When the consumption rises
suddenly (e.g. at the start of the heating season), SLEMS raises the forecast
at once. The card **Forecast accuracy** shows how well the consumption and PV
forecasts hit. When switching from another battery integration, choose its
house consumption sensor as **House consumption history**, then SLEMS learns
from the whole history at once.

Details: [Consumption forecast](consumption-forecast.md).

## Language

SLEMS is available in English and German. Home Assistant uses two different
language settings:

- **Entity names** (e.g. **House consumption**, **Operating mode**, also the key
  figures in the dashboard) follow the **server language** under **Settings →
  System → General**. They are set when the integration loads, so reload SLEMS
  after changing it.
- Dialogs, menus, states (e.g. **Observe only**) and the texts of the
  dashboard follow the language in the **user profile**.

If names stay in the wrong language after an update of SLEMS, restart Home
Assistant (translations are only read at startup) and reload the browser
without cache. Entity IDs such as `sensor.slems_house_consumption` keep the
language of their creation; only the displayed names change.

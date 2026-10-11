"""Reading the sensors of Home Assistant's energy dashboard."""

from custom_components.slems.energy_dashboard import EnergyDashboard, read_energy_dashboard


def test_unified_grid_with_inverted_power_and_solar() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "stat_energy_from": "sensor.meter_import",
                "stat_energy_to": "sensor.meter_export",
                "power_config": {"stat_rate_inverted": "sensor.meter_power"},
            },
            {"type": "solar", "stat_energy_from": "sensor.roof_energy", "stat_rate": "sensor.roof_power",
             "config_entry_solar_forecast": ["forecast_roof"]},
            {"type": "solar", "stat_energy_from": "sensor.balcony_energy",
             "config_entry_solar_forecast": ["forecast_roof", "forecast_balcony"]},
            {"type": "battery", "stat_energy_from": "sensor.out", "stat_energy_to": "sensor.in"},
        ],
        "device_consumption": [
            {"stat_consumption": "sensor.heat_pump_energy", "stat_rate": "sensor.heat_pump_power"},
            {"stat_consumption": "sensor.dryer_energy"},
        ],
    }
    assert read_energy_dashboard(prefs) == EnergyDashboard(
        grid_power="sensor.meter_power",
        grid_power_inverted=True,
        grid_import="sensor.meter_import",
        grid_export="sensor.meter_export",
        pv_power=("sensor.roof_power",),
        pv_energy=("sensor.roof_energy", "sensor.balcony_energy"),
        pv_forecast_entries=("forecast_roof", "forecast_balcony"),
        devices={"sensor.heat_pump_energy": "sensor.heat_pump_power", "sensor.dryer_energy": None},
    )


def test_legacy_grid_and_two_power_sensors() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": [{"stat_energy_from": "sensor.import_t1"}, {"stat_energy_from": "sensor.import_t2"}],
                "flow_to": [{"stat_energy_to": "sensor.export"}],
                # Separate sensors per direction: no signed power for SLEMS.
                "power": [{"power_config": {"stat_rate_from": "sensor.p_in", "stat_rate_to": "sensor.p_out"}}],
            }
        ],
        "device_consumption": [],
    }
    dashboard = read_energy_dashboard(prefs)
    assert (dashboard.grid_import, dashboard.grid_export, dashboard.grid_power) == (
        "sensor.import_t1", "sensor.export", None
    )
    assert read_energy_dashboard(None) == EnergyDashboard()

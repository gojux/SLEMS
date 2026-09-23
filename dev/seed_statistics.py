"""Import 60 days of synthetic hourly statistics into the dev instance.

Runs inside the Home Assistant container (uses its aiohttp):
    docker compose cp dev/seed_statistics.py homeassistant:/tmp/
    docker compose exec homeassistant python3 /tmp/seed_statistics.py <access token>

Series: smart meter, PV, heat pump and the SLEMS outdoor temperature. The
heat pump follows 3 kWh/day + 0.8 kWh per K below 15 °C, the temperature
cools down over time with a warm spell 20 to 14 days ago.
"""
import asyncio, json, math, sys
from datetime import datetime, timedelta, timezone
import aiohttp

TOKEN = sys.argv[1]
now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = now - timedelta(days=60)

def temperature(t):
    day = (t - start).days
    # cooling trend with a warm spell 20..14 days ago
    base = 8 - day * 0.15 + (10 if 40 <= day < 46 else 0)
    return base + 4 * math.sin((t.hour - 9) / 24 * 2 * math.pi)

series = {k: [] for k in ("grid", "pv", "hp", "temp")}
t = start
while t < now:
    temp = temperature(t)
    local_hour = (t.hour + 2) % 24  # Europe/Vienna, summer time
    house = 350 + (300 if 17 <= local_hour < 22 else 0)
    daily_temp = 8 - (t - start).days * 0.15 + (10 if 40 <= (t - start).days < 46 else 0)
    hp = (3000 + 800 * max(0.0, 15 - daily_temp)) / 24
    pv = max(0.0, 3000 * math.sin((local_hour - 7) / 12 * math.pi)) if 7 <= local_hour < 19 else 0.0
    series["grid"].append((t, house + hp - pv))
    series["pv"].append((t, pv))
    series["hp"].append((t, hp))
    series["temp"].append((t, temp))
    t += timedelta(hours=1)

META = {
    "grid": ("sensor.smart_meter_power", "W", "power"),
    "pv": ("sensor.pv_power", "W", "power"),
    "hp": ("sensor.heat_pump_power", "W", "power"),
    "temp": ("sensor.slems_outdoor_temperature", "°C", "temperature"),
}

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8123/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            print((await ws.receive_json())["type"])
            for index, (key, (statistic_id, unit, unit_class)) in enumerate(META.items(), 1):
                await ws.send_json({
                    "id": index, "type": "recorder/import_statistics",
                    "metadata": {"statistic_id": statistic_id, "source": "recorder", "name": None,
                                 "unit_of_measurement": unit, "unit_class": unit_class,
                                 "has_sum": False, "mean_type": 1},
                    "stats": [{"start": ts.isoformat(), "mean": v, "min": v, "max": v} for ts, v in series[key]],
                })
                print(statistic_id, (await ws.receive_json()).get("success"))

asyncio.run(main())

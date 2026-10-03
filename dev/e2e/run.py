"""End-to-end test of the SLEMS setup against a fresh Home Assistant.

Onboards Home Assistant, then runs the config flows of SLEMS through the REST
API the frontend uses: a form is filled with its defaults and the values
given for its step; a missing required value fails the test. After each
entry the integration must be loaded and the log free of errors.
Only the standard library: runs in a plain Python image.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

URL = os.environ.get("HA_URL", "http://localhost:8123")
CLIENT = f"{URL}/"
START_TIMEOUT_S = 300
LOAD_TIMEOUT_S = 60


class Failure(Exception):
    """The setup did not work as expected."""


def request(method: str, path: str, body: Any = None, token: str | None = None, form: bool = False) -> Any:
    headers = {}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as err:
        raise Failure(f"{method} {path}: HTTP {err.code} {err.read().decode()[:500]}") from None
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class WebSocket:
    """The Home Assistant websocket API (text frames), for what REST has not."""

    def __init__(self, token: str) -> None:
        parsed = urllib.parse.urlparse(URL)
        self._sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall(
            (
                f"GET /api/websocket HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        response = b""
        while b"\r\n\r\n" not in response:
            response += self._sock.recv(1024)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise Failure("websocket: no upgrade")
        self._buffer = response.split(b"\r\n\r\n", 1)[1]
        self._id = 0
        self._receive()  # auth_required
        self._send({"type": "auth", "access_token": token})
        if self._receive().get("type") != "auth_ok":
            raise Failure("websocket: authentication failed")

    def _send(self, message: dict) -> None:
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x81])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        self._sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise Failure("websocket: closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _receive(self) -> dict:
        message = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            message += self._read(length)
            if first & 0x80:
                return json.loads(message)

    def call(self, message: dict) -> Any:
        """Result of a command; a failed one fails the test."""
        self._id += 1
        self._send({"id": self._id, **message})
        while True:
            reply = self._receive()
            if reply.get("id") == self._id and reply.get("type") == "result":
                if not reply.get("success"):
                    raise Failure(f"websocket {message['type']}: {reply.get('error')}")
                return reply.get("result")

    def close(self) -> None:
        self._sock.close()


def wait_for_home_assistant() -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/api/onboarding", timeout=5):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    raise Failure("Home Assistant did not start")


PASSWORD = "test-only-password"


def login() -> str:
    """Access token of the test user of an onboarded instance (a repeated run)."""
    flow = request("POST", "/auth/login_flow", {"client_id": CLIENT, "handler": ["homeassistant", None], "redirect_uri": CLIENT})
    result = request(
        "POST", f"/auth/login_flow/{flow['flow_id']}", {"client_id": CLIENT, "username": "test", "password": PASSWORD}
    )
    return request(
        "POST", "/auth/token", {"grant_type": "authorization_code", "code": result["result"], "client_id": CLIENT}, form=True
    )["access_token"]


def onboard() -> str:
    """Owner, location, analytics; returns an access token."""
    done = {step["step"] for step in request("GET", "/api/onboarding") if step["done"]}
    if "user" in done:
        return login()
    user = request(
        "POST",
        "/api/onboarding/users",
        {"client_id": CLIENT, "name": "Test", "username": "test", "password": PASSWORD, "language": "de"},
    )
    token = request(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": user["auth_code"], "client_id": CLIENT},
        form=True,
    )["access_token"]
    request("POST", "/api/onboarding/core_config", {}, token)
    request("POST", "/api/onboarding/analytics", {}, token)
    request("POST", "/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": CLIENT}, token)
    return token


def fill(step: dict, values: dict[str, Any]) -> dict[str, Any]:
    """Input for a form step as the frontend sends it: the given values, else
    the defaults and suggested values; every required field needs one."""
    data = {}
    for field in step.get("data_schema", []):
        name = field["name"]
        suggested = (field.get("description") or {}).get("suggested_value")
        if name in values:
            data[name] = values[name]
        elif "default" in field:
            data[name] = field["default"]
        elif suggested is not None:
            # The frontend fills it in, as the value set before.
            data[name] = suggested
        elif field.get("required"):
            raise Failure(f"step {step['step_id']}: no value for required field {name}")
    unknown = set(values) - {field["name"] for field in step.get("data_schema", [])}
    if unknown:
        raise Failure(f"step {step['step_id']}: no fields {sorted(unknown)}")
    return data


def run_flow(token: str, path: str, start: dict, steps: dict[str, Any], label: str) -> dict:
    """A flow from ``start`` to its end. ``steps``: per step id the values of a
    form (or a function of the step returning them), or the option of a menu;
    a step may appear as a list for several visits."""
    visits: dict[str, int] = {}
    result = request("POST", path, start, token)
    while True:
        kind = result.get("type")
        if kind in ("create_entry", "abort"):
            print(f"  {label}: {kind} {result.get('reason') or result.get('title') or ''}".rstrip())
            return result
        step_id = result.get("step_id")
        if result.get("errors"):
            raise Failure(f"{label}: step {step_id} errors {result['errors']}")
        if step_id not in steps:
            fields = [
                f"{f['name']}{'*' if f.get('required') and 'default' not in f else ''}"
                for f in result.get("data_schema", [])
            ]
            raise Failure(f"{label}: unexpected step {step_id} ({kind}); fields (* required): {fields} "
                          f"menu: {result.get('menu_options')}")
        given = steps[step_id]
        if isinstance(given, list):
            index = visits.get(step_id, 0)
            visits[step_id] = index + 1
            given = given[min(index, len(given) - 1)]
        if callable(given):
            given = given(result)
        if kind == "menu":
            body = {"next_step_id": given}
        elif kind == "form":
            body = fill(result, given)
        else:
            raise Failure(f"{label}: step {step_id} of type {kind}")
        result = request("POST", f"{path}/{result['flow_id']}", body, token)


def slems_entry(token: str) -> dict:
    entries = [e for e in request("GET", "/api/config/config_entries/entry", token=token) if e["domain"] == "slems"]
    if len(entries) != 1:
        raise Failure(f"{len(entries)} SLEMS entries")
    return entries[0]


def wait_loaded(token: str, label: str) -> None:
    deadline = time.monotonic() + LOAD_TIMEOUT_S
    state = None
    while time.monotonic() < deadline:
        state = slems_entry(token)["state"]
        if state == "loaded":
            return
        time.sleep(1)
    raise Failure(f"{label}: SLEMS is {state}")


def errors_in_log(token: str) -> list[str]:
    log = request("GET", "/api/error_log", token=token) or ""
    lines = log.splitlines()
    found = []
    for index, line in enumerate(lines):
        if (" ERROR " in line or " CRITICAL " in line) and ("slems" in line.lower() or "Traceback" in "".join(lines[index:index + 3])):
            found.append("\n".join(lines[index:index + 12]))
    return found


def check(token: str, label: str) -> None:
    wait_loaded(token, label)
    errors = errors_in_log(token)
    if errors:
        raise Failure(f"{label}: errors in the log:\n" + "\n---\n".join(errors))


def remove_entries(token: str) -> None:
    """A fresh start for a repeated run against the same instance."""
    for entry in request("GET", "/api/config/config_entries/entry", token=token):
        if entry["domain"] == "slems":
            request("DELETE", f"/api/config/config_entries/entry/{entry['entry_id']}", token=token)


FLOW = "/api/config/config_entries/flow"
SUBENTRY_FLOW = "/api/config/config_entries/subentries/flow"


def setup_system(token: str) -> None:
    run_flow(
        token,
        FLOW,
        {"handler": "slems"},
        {"user": {"grid_power_entity": "sensor.smart_meter_power", "pv_power_entity": "sensor.pv_power"}},
        "system",
    )
    check(token, "system")


def subentry(token: str, kind: str, steps: dict[str, Any], label: str) -> dict:
    """A new subentry (battery, consumer, tariff) of the SLEMS entry."""
    entry_id = slems_entry(token)["entry_id"]
    result = run_flow(token, SUBENTRY_FLOW, {"handler": [entry_id, kind]}, steps, label)
    if result.get("type") != "create_entry":
        raise Failure(f"{label}: {result.get('type')} {result.get('reason')}")
    check(token, label)
    return result


def battery_modbus(token: str) -> None:
    for number in (1, 2):
        subentry(
            token,
            "battery",
            {
                "user": {"model": "marstek_venus_e3"},
                "search": {"host": "manual"},
                "marstek_venus_e3": {"name": f"Venus {number}", "host": f"venus-sim-{number}"},
            },
            f"Marstek Venus E 3.0 {number} (simulator)",
        )


def battery_entities(token: str) -> None:
    # The third simulated Venus through the Modbus and template entities of dev/config.
    subentry(
        token,
        "battery",
        {
            "user": {"model": "ha_entities"},
            "ha_device": {},
            "ha_entities": {
                "name": "Venus 3",
                "soc_entity": "sensor.venus_3_state_of_charge",
                "power_entity": "sensor.venus_3_ac_power",
                "battery_control": "split",
            },
            "ha_split": {
                "charge_entity": "number.venus_3_charge_power",
                "discharge_entity": "number.venus_3_discharge_power",
                "mode_entity": "select.venus_3_force_mode",
                "remote_entity": "switch.venus_3_rs485_control",
            },
            "ha_options": {"mode_charge": "Charge", "mode_discharge": "Discharge", "mode_standby": "Stop"},
            "ha_limits": {},
        },
        "Battery from entities (Venus 3)",
    )


def consumers(token: str) -> None:
    def measured(name: str, kind: str, power: str, energy: str, control: str) -> dict:
        return {"name": name, "consumer_type": kind, "power_entity": power, "energy_entity": energy, "control_mode": control}

    subentry(
        token,
        "consumer",
        {"user": measured("Heat pump", "heat_pump", "sensor.heat_pump_power", "sensor.heat_pump_energy", "none")},
        "Heat pump (measured only)",
    )
    subentry(
        token,
        "consumer",
        {
            "user": measured("Heating rod", "heating_rod", "sensor.heating_rod_power", "sensor.heating_rod_energy", "power"),
            "control": {"control_entity": "input_number.sim_heating_rod_setpoint", "max_power_w": 3000},
        },
        "Heating rod (power set point)",
    )
    subentry(
        token,
        "consumer",
        {
            "user": measured("Wallbox", "wallbox", "sensor.wallbox_power", "sensor.wallbox_energy", "current"),
            "control": {"control_entity": "input_number.sim_wallbox_current"},
        },
        "Wallbox (current)",
    )
    subentry(
        token,
        "consumer",
        {
            "user": measured("Pump", "other", "sensor.heat_pump_power", "sensor.heat_pump_energy", "switch"),
            "control": {"control_entity": "input_boolean.sim_car_connected", "nominal_power_w": 400},
        },
        "Pump (switched)",
    )


def option(step: dict, field: str, *words: str) -> str:
    """The value of the first option of a select field whose label has all ``words``."""
    for item in step["data_schema"]:
        if item["name"] == field:
            for choice in item["selector"]["select"]["options"]:
                value, label = (choice, choice) if isinstance(choice, str) else (choice["value"], choice["label"])
                if all(word in label for word in words):
                    return value
            raise Failure(f"step {step['step_id']}: no option of {field} with {words}")
    raise Failure(f"step {step['step_id']}: no field {field}")


# A made-up tariff (no real prices), as a user pastes it.
TARIFF_YAML = """format: slems-tariff
version: 1
name: Example import
currency: EUR
vat_pct:
  import: {energy: 20, grid: 20, levies: 20}
  export: {energy: 0, grid: 20, levies: 20}
items:
  - {name: Energy, side: import, group: energy, unit: kwh, price: 12.5}
  - {name: Base fee, side: import, group: energy, unit: year, price: 30}
  - {name: Grid, side: import, group: grid, unit: kwh, price: 7}
  - {name: Grid, side: import, group: grid, unit: kwh, price: 3.5, months: [4, 5, 6, 7, 8, 9], time_from: '10:00', time_to: '16:00'}
  - {name: Feed-in, side: export, group: energy, unit: spot, price: -0.5}
"""


def tariffs(token: str) -> None:
    subentry(
        token,
        "tariff",
        {
            "user": "details",
            "details": {"name": "Manual tariff", "role": "comparison"},
            "items": ["add_item", "finish"],
            "item": {"name": "Energy", "side": "import", "group": "energy", "unit": "kwh", "price": 20},
        },
        "Tariff entered by hand",
    )
    subentry(
        token,
        "tariff",
        {"user": "import_yaml", "import_yaml": {"yaml": TARIFF_YAML}, "details": {"role": "comparison"}, "items": "finish"},
        "Tariff pasted as YAML",
    )
    subentry(
        token,
        "tariff",
        {
            "user": "template",
            "template": {"country": "AT"},
            "template_energy": lambda step: {"energy": option(step, "energy", "Wien Energie")},
            "template_rest": lambda step: {
                "grid": option(step, "grid", "Wien", "Netzebene 7"),
                "levies": option(step, "levies", "Haushalt"),
            },
            "template_options": {},
            "details": {"role": "current"},
            "items": "finish",
        },
        "Austrian tariff from templates",
    )
    subentry(
        token,
        "tariff",
        {
            "user": "template",
            "template": {"country": "DE"},
            "template_energy": {},
            "template_rest": lambda step: {
                "grid": option(step, "grid", "Westnetz", "Modul 1 + 3"),
                "levies": option(step, "levies", "1,59"),
            },
            "template_options": lambda step: {"options": [option(step, "options", "bis 6.000 kWh"), option(step, "options", "Steuerbox")]},
            "details": {"role": "comparison"},
            "items": "finish",
        },
        "German grid and levies from templates",
    )
    subentry(
        token,
        "tariff",
        {
            "user": "template",
            "template": {"country": "DE"},
            "template_energy": lambda step: {"energy": option(step, "energy", "25.02.2025")},
            "details": {"role": "comparison"},
            "items": "finish",
        },
        "EEG feed-in from a template",
    )


def options(token: str) -> None:
    entry_id = slems_entry(token)["entry_id"]
    # The smart meter over Modbus (the simulated SunSpec meter) instead of the
    # entity; a clear import, so its sign is found by comparing both.
    request("POST", "/api/services/input_number/set_value",
            {"entity_id": "input_number.sim_house_load", "value": 4000}, token)
    time.sleep(10)
    run_flow(
        token,
        "/api/config/config_entries/options/flow",
        {"handler": entry_id},
        {"init": {"grid_modbus": True}, "grid_modbus": {"grid_modbus_host": "sunspec-meter"}, "grid_meter": {}},
        "Options: smart meter over Modbus",
    )
    check(token, "Options")


def subentries(token: str) -> list[dict]:
    socket_api = WebSocket(token)
    try:
        return socket_api.call({"type": "config_entries/subentries/list", "entry_id": slems_entry(token)["entry_id"]})
    finally:
        socket_api.close()


def reconfigure(token: str) -> None:
    """Every subentry opened again and saved unchanged, as after editing."""
    entry_id = slems_entry(token)["entry_id"]
    for item in subentries(token):
        kind, label = item["subentry_type"], f"Reconfigure {item['subentry_type']} {item['title']}"
        steps: dict[str, Any] = {
            "battery": {
                "reconfigure": {},
                "marstek_venus_e3": {},
                "ha_device": {},
                "ha_entities": {},
                "ha_split": {},
                "ha_options": {},
                "ha_limits": {},
            },
            "consumer": {"user": {}, "control": {}},
            "tariff": {"items": "finish", "details": {}},
        }[kind]
        result = run_flow(token, SUBENTRY_FLOW, {"handler": [entry_id, kind], "subentry_id": item["subentry_id"]}, steps, label)
        if result.get("reason") != "reconfigure_successful":
            raise Failure(f"{label}: {result.get('reason')}")
        check(token, label)


def dashboard(token: str) -> None:
    """The commands of the dashboard answer (simulation, tariff comparison, prices)."""
    socket_api = WebSocket(token)
    try:
        simulation = socket_api.call({"type": "slems/simulate"})
        comparison = socket_api.call({"type": "slems/tariff_comparison"})
        for day in ("today", "tomorrow"):
            socket_api.call({"type": "slems/price_chart", "day": day})
    finally:
        socket_api.close()
    if not comparison.get("tariffs"):
        raise Failure("tariff comparison without tariffs")
    print(f"  simulation: {len(simulation) if isinstance(simulation, (dict, list)) else simulation} parts, "
          f"tariff comparison: {len(comparison['tariffs'])} tariffs")
    states = request("GET", "/api/states", token=token)
    slems = [s for s in states if s["entity_id"].split(".", 1)[1].startswith("slems")]
    unavailable = [s["entity_id"] for s in slems if s["state"] == "unavailable"]
    print(f"  {len(slems)} SLEMS entities, unavailable: {unavailable or 'none'}")
    check(token, "Dashboard")


SCENARIOS = [
    ("Setup", setup_system),
    ("Batteries", battery_modbus),
    ("Battery from entities", battery_entities),
    ("Consumers", consumers),
    ("Tariffs", tariffs),
    ("Options", options),
    ("Reconfigure", reconfigure),
    ("Dashboard", dashboard),
]


def main() -> int:
    print(f"Home Assistant at {URL}")
    wait_for_home_assistant()
    token = onboard()
    print("Onboarded")
    remove_entries(token)
    try:
        for label, scenario in SCENARIOS:
            print(label)
            scenario(token)
    except Failure as err:
        print(f"FAILED: {err}")
        return 1
    print("All setup scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

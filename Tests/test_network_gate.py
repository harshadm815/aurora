"""The network gate: every request AURORA makes is checked and recorded."""

import logging
from pathlib import Path

import httpx
import pytest

from aurora.config import preferences, settings
from aurora.kernel.permissions import PermissionManager
from aurora.network import INTERNET, LOCAL, THIS_PC, NetworkBlocked, NetworkGate, classify
from aurora.network.gate import GatedTransport
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.tool_plugins.research import ArxivTool, WikipediaTool
from aurora.tool_plugins.weather import WeatherTool
from aurora.tools.executor import ToolExecutor
from aurora.tools.tool_manager import ToolManager

PUNE = {"name": "Pune", "admin1": "Maharashtra", "country": "India", "latitude": 18.52, "longitude": 73.86}
FORECAST = {
    "current": {"temperature_2m": 27.4, "apparent_temperature": 29.1, "relative_humidity_2m": 60,
                "weather_code": 2, "wind_speed_10m": 8.2},
    "daily": {"time": ["2026-11-09"], "weather_code": [2], "temperature_2m_max": [31.2],
              "temperature_2m_min": [19.1], "precipitation_probability_max": [10]},
}


def weather_service(request: httpx.Request) -> httpx.Response:
    if "geocoding" in request.url.host:
        return httpx.Response(200, json={"results": [PUNE]})
    return httpx.Response(200, json=FORECAST)


@pytest.fixture
def mode(monkeypatch):
    def set_mode(internet="on", local=True):
        monkeypatch.setattr(settings, "INTERNET_ACCESS", internet)
        monkeypatch.setattr(settings, "LOCAL_NETWORK_ACCESS", local)
    set_mode()
    return set_mode


@pytest.fixture
def gate():
    return NetworkGate()


def weather_client(gate, handler=weather_service):
    return gate.client("Weather", transport=httpx.MockTransport(handler))


# ------------------------------------------------------------- classification

@pytest.mark.parametrize("host, kind", [
    ("localhost", THIS_PC), ("127.0.0.1", THIS_PC), ("::1", THIS_PC),
    ("192.168.1.40", LOCAL), ("10.0.0.5", LOCAL), ("aurora-devices.local", LOCAL), ("esp32", LOCAL),
    ("api.open-meteo.com", INTERNET), ("8.8.8.8", INTERNET),
])
def test_destinations_are_classified(host, kind):
    assert classify(host) == kind


# ------------------------------------------------------------------- policy

def test_allowed_request_is_sent_and_recorded(gate, mode):
    response = weather_client(gate).get("https://api.open-meteo.com/v1/forecast", params={"q": "Pune"})

    assert response.status_code == 200
    [record] = gate.records()
    assert (record.component, record.host, record.kind, record.outcome, record.status) == (
        "Weather", "api.open-meteo.com", INTERNET, "sent", 200)
    assert record.purpose == "live weather"


def test_records_and_logs_never_contain_what_you_asked(gate, mode, caplog):
    caplog.set_level(logging.INFO, logger="aurora")
    weather_client(gate).get("https://geocoding-api.open-meteo.com/v1/search", params={"name": "Quillhaven"})

    assert "Quillhaven" not in repr(gate.records())
    assert not any("Quillhaven" in r.getMessage() for r in caplog.records)
    assert any("geocoding-api.open-meteo.com" in r.getMessage() for r in caplog.records)


def test_a_service_can_only_reach_its_own_hosts(gate, mode):
    with pytest.raises(NetworkBlocked, match="isn't one of"):
        weather_client(gate).get("https://example.com/collect")
    assert gate.records()[-1].outcome == "blocked"


def test_a_redirect_elsewhere_is_blocked(gate, mode):
    def handler(request):
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(302, headers={"Location": "https://tracker.example/x"})
        return httpx.Response(200)

    client = gate.client("Wikipedia", transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(NetworkBlocked):
        client.get("https://en.wikipedia.org/api/rest_v1/page/summary/Pune")
    assert [r.outcome for r in gate.records()] == ["sent", "blocked"]


def test_unknown_components_reach_nothing(gate, mode):
    client = gate.client("SomethingNew", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(NetworkBlocked):
        client.get("https://api.open-meteo.com/")


def test_offline_blocks_the_internet_but_not_your_own_network(gate, mode):
    mode("off")
    with pytest.raises(NetworkBlocked, match="switched off"):
        weather_client(gate).get("https://api.open-meteo.com/v1/forecast")

    gate.allow_host("Sensors", "http://192.168.1.40/readings")
    sensors = gate.client("Sensors", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert sensors.get("http://192.168.1.40/readings").status_code == 200


def test_local_network_can_be_switched_off(gate, mode):
    mode("on", local=False)
    gate.allow_host("Devices", "http://aurora-devices.local")
    devices = gate.client("Devices", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(NetworkBlocked, match="local network"):
        devices.get("http://aurora-devices.local/devices/lamp")


def test_ask_mode_holds_the_first_request_until_approved(gate, mode):
    mode("ask")
    client = weather_client(gate)
    with pytest.raises(NetworkBlocked) as blocked:
        client.get("https://api.open-meteo.com/v1/forecast")
    assert blocked.value.can_ask

    gate.approve("Weather")
    assert client.get("https://api.open-meteo.com/v1/forecast").status_code == 200

    gate.forget_approvals()
    with pytest.raises(NetworkBlocked):
        client.get("https://api.open-meteo.com/v1/forecast")


def test_approval_is_per_service(gate, mode):
    mode("ask")
    gate.approve("Weather")
    wiki = gate.client("Wikipedia", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(NetworkBlocked):
        wiki.get("https://en.wikipedia.org/")


def test_a_blocked_request_is_an_ordinary_transport_failure_for_tools(gate, mode):
    """Code that never heard of the gate still fails safely ("couldn't reach")."""
    mode("off")
    result = WeatherTool(weather_client(gate)).execute(location="Pune")
    assert not result.success
    assert "couldn't reach" in result.message


# ---------------------------------------------------------------- the chat

def executor_for(gate, tool):
    tools = ToolManager(PermissionManager({tool.name: ["network"]}))
    tools.register(tool)
    return ToolExecutor(tools, network=gate)


WEATHER = RouteDecision(Intent.WEATHER, 0.9, "Weather", {"location": "Pune"}, "test")


def test_chat_asks_before_going_online_and_says_what_is_sent(gate, mode):
    mode("ask")
    executor = executor_for(gate, WeatherTool(weather_client(gate)))

    pending = executor.execute(WEATHER)
    assert pending.needs_confirmation
    assert "geocoding-api.open-meteo.com" in pending.message
    assert "the place name" in pending.message
    assert gate.records()[-1].outcome == "blocked"

    result = executor.confirm("Weather", pending)
    assert result.success and "27°C" in result.message
    assert gate.approved() == ["Weather"]

    assert executor.execute(WEATHER).success          # no second question this session


def test_chat_explains_when_internet_is_off(gate, mode):
    mode("off")
    result = executor_for(gate, WeatherTool(weather_client(gate))).execute(WEATHER)
    assert not result.success and not result.needs_confirmation
    assert "Internet access is off" in result.message and "Settings" in result.message


def test_declining_sends_nothing(gate, mode):
    mode("ask")
    seen = []
    executor = executor_for(gate, WeatherTool(weather_client(gate, lambda r: seen.append(r) or weather_service(r))))
    pending = executor.execute(WEATHER)
    executor.declined("Weather", pending)
    assert seen == [] and gate.approved() == []


# ---------------------------------------------------- nothing bypasses it

def test_every_built_in_client_goes_through_the_gate():
    from aurora.devices.drivers import HttpRelayDriver
    from aurora.sensors.sources import HttpSource

    clients = [
        WeatherTool().client, WikipediaTool().client, ArxivTool().client,
        HttpSource("Room", "http://192.168.1.40/readings").client,
        HttpRelayDriver("http://aurora-devices.local", "token").client,
    ]
    for client in clients:
        assert isinstance(client._transport, GatedTransport)
        client.close()


def test_no_module_creates_an_ungated_client():
    root = Path(__file__).resolve().parents[1] / "aurora"
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if "httpx.Client(" in path.read_text(encoding="utf-8") and path.name != "gate.py"]
    assert offenders == []


def test_internet_preferences_are_validated():
    assert preferences._valid("internet_access", "off")
    assert not preferences._valid("internet_access", "always")
    assert preferences._valid("local_network_access", False)

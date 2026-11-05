import logging
from datetime import datetime, timedelta

import httpx
import pytest

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.sensors.hub import SensorHub
from aurora.sensors.reading import Reading, validate
from aurora.sensors.sources import HttpSource, SimulatedSource, SourceError, build_sources
from aurora.sensors.store import SensorStore
from aurora.tool_plugins.sensors import HealthTool, SensorsTool

ESP32 = {
    "device": "aurora-sensors",
    "readings": {"temperature": 23.4, "humidity": 55.0, "pressure": 1008.2,
                 "motion": False, "heart_rate": 72, "spo2": 98},
}


def esp32_client(payload=None, status=200, error=None):
    def handler(request):
        if error:
            raise error
        return httpx.Response(status, json=ESP32 if payload is None else payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def esp32(payload=None, **kwargs):
    return HttpSource("Room ESP32", "http://aurora-sensors.local/readings",
                      client=esp32_client(payload, **kwargs))


def with_readings(**values):
    return {"device": "aurora-sensors", "readings": values}


# --- Validation -----------------------------------------------------------------

@pytest.mark.parametrize(
    "quantity, value, ok",
    [
        ("temperature", 23.4, True),
        ("temperature", 150, False),       # Faulty sensor.
        ("humidity", 101, False),
        ("pressure", 1008, True),
        ("motion", True, True),
        ("motion", 1, False),
        ("heart_rate", 15, False),
        ("spo2", 98, True),
        ("temperature", float("nan"), False),
        ("temperature", "23", False),
        ("radiation", 1, False),
    ],
)
def test_validate(quantity, value, ok):
    assert validate(quantity, value)[0] is ok


@pytest.mark.parametrize(
    "reading, text",
    [
        (Reading("temperature", 23.44, "x"), "23.4 °C"),
        (Reading("humidity", 55.4, "x"), "55%"),
        (Reading("pressure", 1008.2, "x"), "1008 hPa"),
        (Reading("motion", True, "x"), "motion detected"),
        (Reading("heart_rate", 72.4, "x"), "72 bpm"),
    ],
)
def test_formatting(reading, text):
    assert reading.formatted() == text


# --- Sources --------------------------------------------------------------------

def test_esp32_readings():
    readings, problems = esp32().read()

    assert {r.quantity: r.value for r in readings} == ESP32["readings"]
    assert problems == []
    assert not any(r.simulated for r in readings)


def test_esp32_nulls_are_skipped():
    readings, _ = esp32(with_readings(temperature=21.0, heart_rate=None, spo2=None)).read()

    assert [r.quantity for r in readings] == ["temperature"]


def test_esp32_implausible_values_are_rejected():
    readings, problems = esp32(with_readings(temperature=150.0, humidity=40)).read()

    assert [r.quantity for r in readings] == ["humidity"]
    assert problems == ["Room ESP32: ignored temperature (above the plausible range (85 °C))"]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"error": httpx.ConnectError("offline")}, "I can't reach Room ESP32"),
        ({"error": httpx.ReadTimeout("slow")}, "didn't answer in time"),
        ({"payload": {"unexpected": 1}}, "unexpected format"),
        ({"status": 500}, "I can't reach Room ESP32"),
    ],
)
def test_esp32_failures(kwargs, message):
    with pytest.raises(SourceError, match=message):
        esp32(**kwargs).read()


def test_simulator_values_are_plausible_and_marked():
    readings, problems = SimulatedSource(seed=1).read()

    assert problems == []
    assert {r.quantity for r in readings} == {"temperature", "humidity", "pressure", "motion", "heart_rate", "spo2"}
    assert all(r.simulated for r in readings)


def test_build_sources_from_settings():
    sources = build_sources([
        {"name": "Sim", "type": "simulated"},
        {"name": "ESP", "type": "http", "url": "http://10.0.0.5/readings", "location": "office"},
    ])

    assert [type(s).__name__ for s in sources] == ["SimulatedSource", "HttpSource"]
    assert sources[1].location == "office"

    with pytest.raises(ValueError):
        build_sources([{"name": "x", "type": "bluetooth"}])
    with pytest.raises(ValueError):
        build_sources([{"name": "x", "type": "http", "url": "file:///etc/passwd"}])


# --- Storage --------------------------------------------------------------------

def test_health_readings_are_stored_separately():
    store = SensorStore()
    store.add([Reading("temperature", 23.0, "x"), Reading("heart_rate", 70, "x")])

    assert store.count(health=False) == 1
    assert store.count(health=True) == 1
    assert store.latest("heart_rate", health=False) is None
    assert store.latest("heart_rate", health=True).value == 70


def test_health_data_is_deleted_sooner(monkeypatch):
    monkeypatch.setattr(settings, "SENSOR_RETENTION_DAYS", 30)
    monkeypatch.setattr(settings, "HEALTH_RETENTION_DAYS", 7)
    old = datetime.now() - timedelta(days=10)
    store = SensorStore()
    store.add([Reading("temperature", 23.0, "x", time=old), Reading("heart_rate", 70, "x", time=old)])

    assert store.count(health=False) == 1     # 10 days < 30: kept.
    assert store.count(health=True) == 0      # 10 days > 7: deleted.


def test_old_readings_are_not_used():
    store = SensorStore()
    store.add([Reading("temperature", 23.0, "x", time=datetime.now() - timedelta(hours=2))])

    assert store.latest("temperature", max_age=timedelta(minutes=10)) is None


# --- Hub ------------------------------------------------------------------------

class Failing(SimulatedSource):
    def read(self):
        raise SourceError("I can't reach Room ESP32.")


def test_hub_falls_back_to_a_recent_reading():
    store = SensorStore()
    store.add([Reading("temperature", 22.5, "Room ESP32", time=datetime.now() - timedelta(minutes=3))])

    snapshot = SensorHub([Failing("Room ESP32")], store).snapshot({"temperature"})

    assert snapshot.get("temperature").value == 22.5
    assert "temperature" in snapshot.stale
    assert snapshot.problems == ["I can't reach Room ESP32."]


def test_hub_keeps_health_out_unless_asked():
    hub = SensorHub([esp32()], SensorStore())

    assert "heart_rate" not in hub.snapshot({"temperature", "heart_rate"}).readings
    assert hub.snapshot({"heart_rate"}, include_health=True).get("heart_rate").value == 72


# --- Tools ----------------------------------------------------------------------

def test_room_temperature():
    result = SensorsTool(SensorHub([esp32()], SensorStore())).execute(quantity="temperature")

    assert result.message == "Temperature in your room: 23.4 °C (Room ESP32, just now)."


def test_simulated_values_are_labelled():
    result = SensorsTool(SensorHub([SimulatedSource(seed=1)], SensorStore())).execute(quantity="temperature")

    assert result.message.endswith("(Simulated values: no real sensor is connected yet.)")


def test_all_room_readings():
    result = SensorsTool(SensorHub([esp32()], SensorStore())).execute(quantity="all")

    assert result.message.splitlines() == [
        "Room sensors:",
        "  Temperature: 23.4 °C (Room ESP32, just now)",
        "  Humidity: 55% (Room ESP32, just now)",
        "  Air pressure: 1008 hPa (Room ESP32, just now)",
        "  Motion: no motion (Room ESP32, just now)",
    ]
    assert "Heart rate" not in result.message


def test_motion():
    hub = SensorHub([esp32(with_readings(motion=True))], SensorStore())

    assert SensorsTool(hub).execute(quantity="motion").message == "Motion detected in your room (Room ESP32, just now)."


def test_stale_value_is_explained():
    store = SensorStore()
    store.add([Reading("temperature", 22.5, "Room ESP32", time=datetime.now() - timedelta(minutes=3))])

    message = SensorsTool(SensorHub([Failing("Room ESP32")], store)).execute(quantity="temperature").message

    assert "3 minutes ago" in message
    assert "last known value" in message
    assert "Note: I can't reach Room ESP32." in message


def test_no_readings_at_all():
    result = SensorsTool(SensorHub([Failing("Room ESP32")], SensorStore())).execute(quantity="temperature")

    assert result.success is False
    assert result.message == "I have no sensor readings right now. I can't reach Room ESP32."


def test_health_readings_carry_a_disclaimer():
    result = HealthTool(SensorHub([esp32()], SensorStore())).execute(quantity="all")

    assert result.message.splitlines() == [
        "Heart rate: 72 bpm (Room ESP32, just now)",
        "Blood oxygen (SpO2): 98% (Room ESP32, just now)",
        "This is informational only, not a medical measurement. If you feel unwell, please contact a doctor.",
    ]


def test_no_finger_on_the_sensor():
    hub = SensorHub([esp32(with_readings(heart_rate=None, spo2=None))], SensorStore())

    result = HealthTool(hub).execute(quantity="heart_rate")

    assert result.success is False
    assert "Place your fingertip" in result.message


def test_sleep_is_not_supported_yet():
    result = HealthTool(SensorHub([esp32()], SensorStore())).execute(quantity="sleep")

    assert "no sleep tracker is connected" in result.message


# --- Engine ---------------------------------------------------------------------

@pytest.fixture
def make_engine(fake_provider, memory):
    def make(permissions=None):
        hub = SensorHub([esp32()], SensorStore())
        engine = AuroraEngine(Brain(fake_provider), permissions=permissions, memory=memory,
                              tools=[SensorsTool(hub), HealthTool(hub)])
        engine.start()
        return engine
    return make


def test_temperature_question(make_engine, fake_provider):
    reply = make_engine().process_message("What's the temperature in my room?")

    assert reply == "Temperature in your room: 23.4 °C (Room ESP32, just now)."
    assert fake_provider.calls == []


def test_health_needs_its_own_permission(make_engine):
    engine = make_engine(PermissionManager({"Sensors": ["sensors"]}))

    assert engine.process_message("what's the temperature in my room?").startswith("Temperature")
    assert "not allowed to use the Health tool" in engine.process_message("what is my heart rate?")


def test_health_readings_never_enter_long_term_memory(make_engine, memory):
    make_engine().process_message("what is my heart rate?")

    assert memory.memories() == []


def test_sensor_values_are_not_logged(make_engine, caplog):
    caplog.set_level(logging.DEBUG, logger="aurora")
    engine = make_engine()

    engine.process_message("what's the temperature in my room?")
    engine.process_message("what is my heart rate?")

    assert "23.4" not in caplog.text
    assert "72 bpm" not in caplog.text
    assert "Sensor snapshot: 1 readings" in caplog.text

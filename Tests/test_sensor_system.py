"""Phase 14: sensor integration - new quantities, timestamps, permitted readings,
interpretation, history, the Sensor Service, events, notifications and the interface."""

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from conftest import release_widgets

from aurora.config import settings
from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.sensors import reading as reading_module
from aurora.sensors.hub import SensorHub, Snapshot
from aurora.sensors.interpret import (FAIR, GOOD, INFO, POOR, SIMULATED_CAVEAT, day_summary, interpret, trend,
                                      window_advice)
from aurora.sensors.reading import QUANTITIES, ROOM_QUANTITIES, Reading, register_extra, validate
from aurora.sensors.service import SensorService
from aurora.sensors.sources import HttpSource, SimulatedSource, SourceError, build_sources
from aurora.sensors.store import SensorStore
from aurora.tool_plugins.sensors import NO_AIR_SENSOR, SIMULATED_NOTE, HealthTool, SensorsTool

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_quantities():
    """Extra quantities change module-level tables; put them back after each test."""
    quantities, room = dict(QUANTITIES), list(ROOM_QUANTITIES)
    yield
    QUANTITIES.clear()
    QUANTITIES.update(quantities)
    ROOM_QUANTITIES[:] = room


def device(payload, name="Room ESP32", **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    return HttpSource(name, "http://aurora-sensors.local/readings", client=client, **kwargs)


def payload(**readings):
    return {"device": "aurora-sensors", "readings": readings}


class Fixed:
    """A source that returns prepared readings."""

    def __init__(self, readings, problems=(), name="Fixed"):
        self.readings, self.problems, self.name = list(readings), list(problems), name

    def read(self):
        return list(self.readings), list(self.problems)


class Failing:
    name = "Room ESP32"

    def read(self):
        raise SourceError("I can't reach Room ESP32.")


def at(minutes_ago, quantity="temperature", value=22.0, simulated=False, source="Room ESP32"):
    return Reading(quantity, value, source, "room", simulated, datetime.now() - timedelta(minutes=minutes_ago))


# --- Quantities ---------------------------------------------------------------------------

@pytest.mark.parametrize("quantity, value, ok", [
    ("light", 312.5, True), ("light", -1, False), ("light", 200_000, False),
    ("co2", 420, True), ("co2", 100, False), ("co2", 50_000, False),
    ("eco2", 400, True), ("eco2", 399, False),
    ("tvoc", 0, True), ("tvoc", 70_000, False),
    ("pm2_5", 12, True), ("pm2_5", 1001, False), ("pm10", 45, True),
    ("temperature", float("inf"), False), ("light", True, False),
])
def test_new_quantities_are_validated(quantity, value, ok):
    assert validate(quantity, value)[0] is ok


def test_formatting():
    assert Reading("light", 312.4, "x").formatted() == "312 lux"
    assert Reading("co2", 1620, "x").formatted() == "1620 ppm"
    assert Reading("tvoc", 12_500, "x").formatted() == "12,500 ppb"
    assert Reading("pm2_5", 9, "x").formatted() == "9 µg/m³"
    assert QUANTITIES["eco2"].note == "estimated from VOCs, not measured directly"


def test_extra_quantities_from_settings():
    problems = register_extra([
        {"name": "soil_moisture", "label": "Soil moisture", "unit": "%", "minimum": 0, "maximum": 100,
         "decimals": 0},
        {"name": "temperature", "label": "Hijack"},                    # built-in: refused
        {"name": "Bad Name"},
        {"name": "noise", "minimum": 90, "maximum": 30},
        {"name": "uv", "minimum": "low"},
        "not a dict",
    ])
    assert QUANTITIES["soil_moisture"].custom and not QUANTITIES["soil_moisture"].health
    assert "soil_moisture" in ROOM_QUANTITIES
    assert QUANTITIES["temperature"].label == "Temperature"
    assert "noise" not in QUANTITIES and "uv" not in QUANTITIES
    assert len(problems) == 5 and "is a built-in quantity" in problems[0]
    assert validate("soil_moisture", 101) == (False, "above the plausible range (100 %)")
    assert Reading("soil_moisture", 41, "x").formatted() == "41%"


def test_extra_quantities_are_reported_but_not_interpreted():
    register_extra([{"name": "soil_moisture", "label": "Soil moisture", "unit": "%", "minimum": 0, "maximum": 100}])
    hub = SensorHub([device(payload(temperature=22.0, soil_moisture=41))], SensorStore())
    result = SensorsTool(hub).execute(quantity="all")
    line = next(l for l in result.message.splitlines() if "Soil moisture" in l)
    assert line.startswith("  Soil moisture: 41.0%") and line.endswith(")")     # no ": meaning" added
    assert interpret(hub.snapshot({"soil_moisture"}).get("soil_moisture")) is None


def test_hub_reports_bad_extra_quantities(monkeypatch):
    monkeypatch.setattr(settings, "SENSOR_EXTRA_QUANTITIES", [{"name": "x"}])
    hub = SensorHub([Fixed([at(0)])], SensorStore())
    assert any("Extra sensor quantity 'x' skipped" in p for p in hub.snapshot({"temperature"}).problems)


# --- Device interface: timestamps and permitted readings -------------------------------------

def test_ages_set_the_measuring_time():
    readings, problems = device({**payload(eco2=640, tvoc=85, temperature=22.0), "ages": {"eco2": 30, "tvoc": 30}}).read()
    by = {r.quantity: r for r in readings}
    assert problems == []
    assert 28 <= (datetime.now() - by["eco2"].time).total_seconds() <= 33
    assert (datetime.now() - by["temperature"].time).total_seconds() < 3


@pytest.mark.parametrize("age, problem", [
    (301, "ignored CO₂ (estimated) (measured 5 minutes ago, too old)"),
    (-5, "ignored CO₂ (estimated) (it sent an invalid age)"),
    ("soon", "ignored CO₂ (estimated) (it sent an invalid age)"),
    (True, "ignored CO₂ (estimated) (it sent an invalid age)"),
])
def test_old_or_invalid_ages_are_refused(age, problem):
    readings, problems = device({**payload(eco2=640), "ages": {"eco2": age}}).read()
    assert readings == [] and problems == [f"Room ESP32: {problem}"]


def test_only_permitted_quantities_are_used():
    source = device(payload(temperature=22.0, humidity=50, light=300, heart_rate=70), quantities=["temperature", "light"])
    assert sorted(r.quantity for r in source.read()[0]) == ["light", "temperature"]
    built = build_sources([{"name": "Sim", "type": "simulated", "quantities": ["co2"]}])
    assert [r.quantity for r in built[0].read()[0]] == ["co2"]
    with pytest.raises(ValueError, match="must be a list of names"):
        build_sources([{"name": "Sim", "type": "simulated", "quantities": "co2"}])


def test_last_motion_is_kept_as_a_past_reading():
    readings, problems = device({**payload(motion=False), "last_motion_s": 120}).read()
    past = [r for r in readings if r.quantity == "motion" and r.value is True]
    assert problems == [] and len(past) == 1
    assert 118 <= (datetime.now() - past[0].time).total_seconds() <= 123
    assert device({**payload(motion=False), "last_motion_s": 3 * 86400}).read()[0][1:] == []   # over a day: ignored
    assert device({**payload(motion=False), "last_motion_s": "x"}).read()[1] == [
        "Room ESP32: ignored the time of the last motion (invalid)"]
    assert len(device({**payload(motion=False), "last_motion_s": 5}, quantities=["temperature"]).read()[0]) == 0


def test_newest_reading_wins_in_a_snapshot():
    hub = SensorHub([device({**payload(motion=False), "last_motion_s": 60})], SensorStore())
    snapshot = hub.snapshot({"motion"})
    assert snapshot.get("motion").value is False
    assert hub.store.last_motion().value is True


def test_most_recently_measured_value_wins_across_devices():
    older = device({**payload(co2=900), "ages": {"co2": 240}}, name="Hall ESP32")
    newer = device(payload(co2=650), name="Desk ESP32")
    snapshot = SensorHub([older, newer], SensorStore()).snapshot({"co2"})
    assert snapshot.get("co2").value == 650 and snapshot.get("co2").source == "Desk ESP32"


def test_firmware_documented_format_is_accepted():
    """The JSON example in the ESP32 firmware's header is what AURORA reads."""
    source = (ROOT / "firmware" / "esp32_sensor_hub" / "esp32_sensor_hub.ino").read_text(encoding="utf-8")
    block = source[source.index("GET http://aurora-sensors.local/readings"):source.index("null means")]
    example = json.loads(block[block.index("{"):block.rindex("}") + 1])
    readings, problems = device(example).read()
    assert problems == []
    assert {r.quantity for r in readings} >= {"temperature", "humidity", "pressure", "light", "motion", "eco2", "tvoc"}


def test_simulator_stays_plausible_and_marked():
    for hour in range(0, 48):
        clock = (datetime(2026, 11, 8) + timedelta(minutes=37 * hour)).timestamp()
        readings, problems = SimulatedSource(seed=hour, clock=lambda c=clock: c).read()
        assert problems == [] and all(r.simulated for r in readings)
        by = {r.quantity: r.value for r in readings}
        assert by["co2"] >= 450                     # never below outdoor air
        assert 0 <= by["light"] <= 700


# --- History store ---------------------------------------------------------------------------

def test_history_and_duplicates():
    store = SensorStore()
    same = at(5)
    store.add([same, same, at(30), at(60 * 30)])             # the 30-hour-old one is still within retention
    history = store.history("temperature", datetime.now() - timedelta(hours=2))
    assert [round((datetime.now() - r.time).total_seconds() / 60) for r in history] == [30, 5]
    assert store.count() == 3


def test_last_motion_from_history():
    store = SensorStore()
    assert store.last_motion() is None
    store.add([at(10, "motion", True), at(2, "motion", False), at(40, "motion", True)])
    assert round((datetime.now() - store.last_motion().time).total_seconds() / 60) == 10


# --- Interpretation ---------------------------------------------------------------------------

@pytest.mark.parametrize("quantity, value, level, summary", [
    ("temperature", 15.9, POOR, "cold"), ("temperature", 19.9, FAIR, "cool"),
    ("temperature", 20, GOOD, "comfortable"), ("temperature", 26, FAIR, "warm"), ("temperature", 31, POOR, "hot"),
    ("humidity", 25, FAIR, "dry"), ("humidity", 45, GOOD, "comfortable"), ("humidity", 75, POOR, "very humid"),
    ("co2", 999, GOOD, "fresh enough"), ("co2", 1000, FAIR, "getting stuffy"), ("co2", 1500, POOR, "stuffy"),
    ("eco2", 1600, POOR, "stuffy"), ("tvoc", 100, GOOD, "low"), ("tvoc", 700, POOR, "high"),
    ("pm2_5", 15, GOOD, "low"), ("pm2_5", 15.5, FAIR, "above the WHO guideline"),
    ("pm2_5", 60, POOR, "unhealthy"), ("pm10", 120, POOR, "high"),
    ("light", 5, INFO, "dark"), ("light", 400, INFO, "good for reading and desk work"),
])
def test_bands(quantity, value, level, summary):
    meaning = interpret(Reading(quantity, value, "x"))
    assert (meaning.level, meaning.summary) == (level, summary)
    assert meaning.reference


@pytest.mark.parametrize("reading", [None, Reading("motion", True, "x"), Reading("pressure", 1000, "x"),
                                     Reading("heart_rate", 180, "x"), Reading("spo2", 75, "x")])
def test_no_interpretation_without_a_band(reading):
    assert interpret(reading) is None          # health readings are never interpreted


def test_simulated_interpretations_say_so():
    meaning = interpret(Reading("co2", 1600, "Simulator", simulated=True))
    assert meaning.sentence().endswith(f"({SIMULATED_CAVEAT})")


def series(values, minutes_apart=10, quantity="temperature", simulated=False, source="Room ESP32"):
    count = len(values)
    return [at((count - 1 - i) * minutes_apart, quantity, value, simulated, source) for i, value in enumerate(values)]


def test_trends():
    rising = trend(series([20.0, 20.4, 20.9, 21.3, 21.8, 22.2]), "temperature")
    assert rising.direction == "rising" and rising.change == pytest.approx(2.2, abs=0.1)
    assert rising.sentence() == "Temperature is rising: up 2.2 °C in the last 50 minutes."
    assert trend(series([22.0, 22.1, 21.9, 22.0]), "temperature").direction == "steady"
    falling = trend(series([1010, 1009, 1007.5, 1006], minutes_apart=40, quantity="pressure"), "pressure")
    assert falling.direction == "falling" and "2 hours" in falling.sentence()


def test_trend_needs_enough_matching_readings():
    assert trend(series([20.0, 22.0]), "temperature") is None                         # two points
    assert trend(series([20.0, 21.0, 22.0], minutes_apart=2), "temperature") is None   # 4 minutes only
    mixed = series([20.0, 20.5, 21.0, 21.5]) + [at(0, value=30.0, simulated=True)]
    assert trend(mixed, "temperature") is None          # newest is simulated; no simulated history
    assert trend(series([20.0, 21.0, 22.0]), "light") is None
    other = series([20.0, 21.0, 22.0, 23.0], source="Other") + [at(0, value=22.0)]
    assert trend(other, "temperature") is None          # different device


def test_day_summary():
    summary = day_summary(series([19.0, 24.5, 21.0]))
    assert (summary.lowest.value, summary.highest.value, summary.count) == (19.0, 24.5, 3)
    assert summary.average == pytest.approx(21.5)
    assert day_summary([at(0)]) is None


def test_window_advice():
    def r(quantity, value):
        return Reading(quantity, value, "x")

    assert window_advice({"co2": r("co2", 1200)})[0] == "yes"
    assert window_advice({"humidity": r("humidity", 72), "co2": r("co2", 600)})[0] == "yes"
    assert window_advice({"co2": r("co2", 600), "humidity": r("humidity", 45)})[0] == "no"
    assert window_advice({"humidity": r("humidity", 45)})[0] == "unknown"
    assert window_advice({"pm2_5": r("pm2_5", 80)})[0] == "unknown"      # dust isn't a reason to open a window
    assert window_advice({}) == ("unknown", [])


# --- The Sensors tool -----------------------------------------------------------------------

def tool(*sources, store=None):
    return SensorsTool(SensorHub(list(sources), store or SensorStore()))


def test_co2_answer_with_meaning_and_guide():
    result = tool(device(payload(co2=1620))).execute(quantity="co2")
    first, guide = result.message.splitlines()[:2]
    assert re.fullmatch(r"CO₂ in your room: 1620 ppm \(Room ESP32, \d\d:\d\d, just now\): stuffy\. "
                        r"Open a window: the air needs changing\.", first)
    assert guide.startswith("Guide: Outdoor air is about 420 ppm")


def test_estimated_co2_is_used_and_labelled():
    result = tool(device(payload(eco2=900))).execute(quantity="co2")
    assert result.message.startswith("CO₂ (estimated) in your room: 900 ppm")
    assert "Estimated from VOCs" in result.message


def test_missing_sensors_are_explained_not_invented():
    assert tool(device(payload(temperature=22.0))).execute(quantity="pm2_5").message == NO_AIR_SENSOR
    light = tool(device(payload(temperature=22.0))).execute(quantity="light")
    assert not light.success and light.message == "I have no ambient light reading: no connected sensor reports it."
    assert tool(Failing()).execute(quantity="light").message == "I have no sensor readings right now. I can't reach Room ESP32."
    air = tool(device(payload(temperature=22.0))).execute(quantity="air")
    assert not air.success and air.message.startswith(NO_AIR_SENSOR)


def test_air_overview_and_verdict(tmp_path):
    result = tool(device(payload(co2=1700, tvoc=100, pm2_5=8))).execute(quantity="air")
    lines = result.message.splitlines()
    assert lines[0] == "Air in your room:"
    assert lines[-1] == "Needs attention: CO₂. Open a window: the air needs changing."
    fresh = SensorStore(tmp_path / "other.db")          # no history from the first device
    good = tool(device(payload(co2=600, tvoc=100)), store=fresh).execute(quantity="air").message
    assert good.splitlines()[-1] == "All good (CO₂ and VOCs)."


def test_comfort_and_window():
    hub_payload = payload(temperature=27.0, humidity=50, co2=700, light=50)
    comfort = tool(device(hub_payload)).execute(quantity="comfort").message
    assert comfort.splitlines()[0] == "Your room:" and "Mostly fine; temperature could be better." in comfort
    assert tool(device(payload(co2=1300))).execute(quantity="window").message.startswith(
        "Yes, opening a window would help: CO₂ is 1300 ppm (getting stuffy).")
    assert tool(device(payload(temperature=22.0))).execute(quantity="window").message == \
        "I can't tell: no CO₂ or VOC sensor is connected."


def test_stale_values_never_drive_window_advice():
    store = SensorStore()
    store.add([at(3, "co2", 1800)])
    result = tool(Failing(), store=store).execute(quantity="window")
    assert result.message.startswith("I can't tell")


def test_overview_lists_what_has_no_reading():
    result = tool(device(payload(temperature=22.0, eco2=500))).execute(quantity="all")
    assert result.message.splitlines()[-1] == ("No reading for: humidity, air pressure, ambient light, motion, "
                                               "VOCs and fine dust (PM2.5).")


def test_single_answer_includes_a_trend_from_history():
    store = SensorStore()
    store.add(series([20.0, 20.5, 21.0, 21.5, 22.0]))
    result = tool(device(payload(temperature=22.4)), store=store).execute(quantity="temperature")
    assert result.message.splitlines()[-1].startswith("Temperature is rising: up")


def test_history_view():
    store = SensorStore()
    empty = tool(Fixed([]), store=store).execute(quantity="humidity", view="trend")
    assert not empty.success and "Turn on background watching" in empty.message
    store.add(series([40, 44, 48, 52], quantity="humidity"))
    result = tool(Fixed([]), store=store).execute(quantity="humidity", view="trend")
    assert result.message.splitlines()[0].startswith("Humidity over the last 24 hours (4 readings): lowest 40%")
    assert result.message.splitlines()[1] == "Humidity is rising: up 12% in the last 30 minutes."
    assert not tool(Fixed([]), store=store).execute(quantity="motion", view="trend").success


def test_history_view_of_simulated_data_is_labelled():
    store = SensorStore()
    store.add(series([600, 700, 800], quantity="co2", simulated=True, source="Simulator"))
    assert tool(Fixed([]), store=store).execute(quantity="co2", view="trend").message.endswith(SIMULATED_NOTE)


def test_last_motion_view():
    store = SensorStore()
    assert "haven't seen any motion" in tool(Fixed([]), store=store).execute(view="last_motion").message
    store.add([at(12, "motion", True)])
    message = tool(Fixed([]), store=store).execute(view="last_motion").message
    assert re.fullmatch(r"Motion was last seen today at \d\d:\d\d \(12 minutes ago, Room ESP32\)\.", message) \
        or "on " in message      # just after midnight


def test_health_is_never_interpreted():
    hub = SensorHub([device(payload(heart_rate=180, spo2=75))], SensorStore())
    message = HealthTool(hub).execute(quantity="all").message
    assert "180 bpm" in message and "informational only" in message
    for word in ("high", "low", "poor", "good", "stuffy", "comfortable"):
        assert word not in message.split("This is informational")[0].lower()


# --- Routing ---------------------------------------------------------------------------------

@pytest.mark.parametrize("message, arguments", [
    ("what is the co2 level?", {"quantity": "co2"}),
    ("how is the air in my room?", {"quantity": "air"}),
    ("is it stuffy in here?", {"quantity": "air"}),
    ("should I open a window?", {"quantity": "window"}),
    ("is my room comfortable?", {"quantity": "comfort"}),
    ("how bright is it in here?", {"quantity": "light"}),
    ("what is the pm2.5", {"quantity": "pm2_5"}),
    ("what's the VOC level", {"quantity": "tvoc"}),
    ("how has the temperature in my room changed today?", {"quantity": "temperature", "view": "trend"}),
    ("co2 trend", {"quantity": "co2", "view": "trend"}),
    ("when was the last motion?", {"quantity": "motion", "view": "last_motion"}),
    ("check my sensors", {"quantity": "all"}),
])
def test_sensor_questions(message, arguments):
    decision = IntentRouter().route(message)
    assert decision.intent is Intent.SENSORS and dict(decision.arguments) == arguments


@pytest.mark.parametrize("message", ["air quality in Delhi", "what's the temperature in Pune?",
                                     "is the light on in my room", "turn on the light", "temperature today"])
def test_not_sensor_questions(message):
    assert IntentRouter().route(message).intent is not Intent.SENSORS


def test_extra_quantities_are_routed_by_label():
    register_extra([{"name": "soil_moisture", "label": "Soil moisture", "unit": "%", "minimum": 0, "maximum": 100}])
    decision = IntentRouter().route("what is the soil moisture?")
    assert decision.intent is Intent.SENSORS and dict(decision.arguments) == {"quantity": "soil_moisture"}


# --- Sensor Service: events and alerts -------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def service(source, permitted=lambda: True, store=None):
    events, seen = EventBus(), []
    events.subscribe("*", seen.append)
    clock = Clock()
    hub = SensorHub([source], store or SensorStore())
    return SensorService(hub, events, permitted=permitted, clock=clock), seen, clock


def names(seen):
    return [event.name for event in seen]


def test_poll_publishes_readings_with_times_and_never_health():
    svc, seen, _ = service(device(payload(temperature=22.0, co2=600, heart_rate=70)))
    svc.poll()
    [event] = [e for e in seen if e.name == topics.SENSOR_READINGS]
    readings = event.data["readings"]
    assert set(readings) == {"temperature", "co2"}
    assert readings["temperature"]["value"] == 22.0 and readings["temperature"]["source"] == "Room ESP32"
    assert datetime.fromisoformat(readings["temperature"]["time"]) <= datetime.now()
    assert event.data["simulated"] is False and event.source == "SensorService"


def test_alerts_when_poor_with_cooldown():
    svc, seen, clock = service(device(payload(co2=1700, humidity=50)))
    svc.poll()
    alerts = [e.data for e in seen if e.name == topics.SENSOR_ALERT]
    assert len(alerts) == 1 and alerts[0]["quantity"] == "co2"
    assert alerts[0]["message"] == "CO₂ in your room is 1700 ppm: stuffy. Open a window: the air needs changing."
    svc.poll()
    clock.now += 29 * 60
    svc.poll()
    assert len([e for e in seen if e.name == topics.SENSOR_ALERT]) == 1        # within the cooldown
    clock.now += 2 * 60
    svc.poll()
    assert len([e for e in seen if e.name == topics.SENSOR_ALERT]) == 2
    assert list(svc.alerts)[0]["quantity"] == "co2"


def test_no_alerts_for_fair_stale_or_unknown_readings(tmp_path):
    svc, seen, _ = service(device(payload(co2=1200, temperature=27.0, pressure=950.0)))
    svc.poll()
    assert topics.SENSOR_ALERT not in names(seen)
    store = SensorStore(tmp_path / "stale.db")          # only an old, poor value
    store.add([at(3, "co2", 2500)])
    stale, seen2, _ = service(Failing(), store=store)
    snapshot = stale.poll()
    assert snapshot.get("co2").value == 2500 and "co2" in snapshot.stale
    assert topics.SENSOR_ALERT not in names(seen2)


def test_simulated_alerts_say_so():
    svc, seen, _ = service(Fixed([Reading("co2", 1800, "Simulator", simulated=True)]))
    svc.poll()
    alert = next(e.data for e in seen if e.name == topics.SENSOR_ALERT)
    assert alert["simulated"] and alert["message"].endswith("(Simulated reading, not your real room.)")


def test_watching_is_off_by_default_and_needs_permission(monkeypatch):
    svc, seen, _ = service(device(payload(temperature=22.0)))
    svc.start()
    assert not svc.monitoring and topics.SENSOR_READINGS not in names(seen)
    refused, _, _ = service(device(payload(temperature=22.0)), permitted=lambda: False)
    assert refused.start_monitoring() == "AURORA isn't allowed to read sensors (the Sensors permission is off)."
    assert not refused.monitoring


def test_watching_runs_in_the_background_and_stops(monkeypatch):
    monkeypatch.setattr(settings, "SENSOR_MONITOR_SECONDS", 0.05)
    svc, seen, _ = service(device(payload(temperature=22.0)))
    assert svc.start_monitoring() is None
    deadline = datetime.now() + timedelta(seconds=5)
    while names(seen).count(topics.SENSOR_READINGS) < 3 and datetime.now() < deadline:
        threading.Event().wait(0.02)
    svc.stop_monitoring()
    assert names(seen).count(topics.SENSOR_READINGS) >= 3 and not svc.monitoring
    assert names(seen)[0] == topics.SENSOR_MONITORING and seen[0].data["on"] is True
    assert seen[-1].name == topics.SENSOR_MONITORING and seen[-1].data == {"on": False}


def test_watching_stops_when_permission_is_taken_away(monkeypatch):
    monkeypatch.setattr(settings, "SENSOR_MONITOR_SECONDS", 0.02)
    allowed = {"yes": True}
    svc, seen, _ = service(device(payload(temperature=22.0)), permitted=lambda: allowed["yes"])
    svc.start_monitoring()
    allowed["yes"] = False
    deadline = datetime.now() + timedelta(seconds=5)
    while svc.monitoring and datetime.now() < deadline:
        threading.Event().wait(0.02)
    assert not svc.monitoring


def test_monitor_on_at_start_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "SENSOR_MONITOR", True)
    monkeypatch.setattr(settings, "SENSOR_MONITOR_SECONDS", 60)
    svc, seen, _ = service(device(payload(temperature=22.0)))
    svc.start()
    try:
        assert svc.monitoring
    finally:
        svc.stop()
    assert not svc.monitoring


def test_values_are_never_logged(caplog):
    svc, _, _ = service(device(payload(temperature=23.7, co2=1888)))
    with caplog.at_level(logging.DEBUG):
        svc.poll()
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "23.7" not in logged and "1888" not in logged
    assert "Sensor watch: 2 readings" in logged


def test_engine_registers_the_sensor_service(fake_provider, memory):
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from aurora.kernel.permissions import PermissionManager

    hub = SensorHub([device(payload(temperature=22.0))], SensorStore())
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=[SensorsTool(hub)],
                          permissions=PermissionManager({}))
    engine.start()
    sensors = engine.service_manager.services["Sensors"]
    assert sensors.hub is hub and engine.status()["services"]["Sensors"] == "Running"
    assert sensors.start_monitoring().startswith("AURORA isn't allowed")
    engine.shutdown()
    no_sensors = AuroraEngine(Brain(fake_provider), memory=memory, tools=[])
    assert "Sensors" not in no_sensors.service_manager.services


# --- Interface ------------------------------------------------------------------------------

from PySide6.QtWidgets import QApplication  # noqa: E402

from aurora.config import preferences  # noqa: E402
from aurora.interface.backend import Backend, describe_event, wait_for  # noqa: E402
from aurora.events.event import Event  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(settings, "SENSOR_MONITOR_SECONDS", 0.05)
    hub = SensorHub([device(payload(temperature=22.5, humidity=50, co2=1700, light=320))], SensorStore())
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory,
                                                    tools=[SensorsTool(hub), HealthTool(hub)]))
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()
    release_widgets()


def test_activity_feed_wording():
    alert = describe_event(Event(topics.SENSOR_ALERT, {"quantity": "co2", "level": "poor", "simulated": True}))
    assert alert.text == "Sensor alert: CO₂ is poor (simulated)"
    assert describe_event(Event(topics.SENSOR_MONITORING, {"on": True})).text == "Background sensor watching on"


def test_sensor_centre_watching_cards_and_alerts(backend):
    from aurora.interface.sensors_view import SensorsView

    page = SensorsView(backend)
    page.on_engine_ready()
    assert not page.watch.isChecked() and "off" in page.watch_status.text()
    page.watch.setChecked(True)
    assert wait_for(lambda: page.cards["co2"].value.text() == "1700 ppm", 10)
    assert page.cards["co2"].meaning.text() == "Stuffy" and page.cards["co2"].badge.text() == "LIVE"
    assert page.cards["pm2_5"].badge.text() == "NO SENSOR" and page.cards["pm2_5"].value.text() == "—"
    assert wait_for(lambda: "CO₂ in your room is 1700 ppm" in page.alert_list.item(0).text(), 10)
    assert "Watching" in page.watch_status.text() and backend.preferences["sensor_monitoring"] is True
    assert settings.SENSOR_MONITOR is True
    page.watch.setChecked(False)
    assert wait_for(lambda: not backend.sensor_service().monitoring, 10)


def test_sensor_centre_refuses_watching_without_permission(backend):
    from aurora.interface.sensors_view import SensorsView

    backend.engine.permissions = type(backend.engine.permissions)({})
    page = SensorsView(backend)
    page.watch.setChecked(True)
    assert not page.watch.isChecked()
    assert page.watch_status.text() == "AURORA isn't allowed to read sensors (the Sensors permission is off)."


def test_sensor_centre_history_chart(backend):
    from aurora.interface.sensors_view import SensorsView

    backend.tool("Sensors").hub.store.add(series([20.0, 20.5, 21.0, 21.5]))
    page = SensorsView(backend)
    page.history_choice.setCurrentIndex(page.history_choice.findData("temperature"))
    page.refresh_history()
    assert wait_for(lambda: len(page.chart.points) == 4, 10)
    assert page.history_text.text().startswith("Temperature over the last 24 hours (4 readings)")
    page.chart.resize(600, 240)
    page.chart.grab()            # paints without errors


def test_readings_from_event_ignores_bad_entries():
    from aurora.interface.sensors_view import readings_from_event

    readings, stale = readings_from_event({"readings": {
        "temperature": {"value": 22.0, "time": "2026-11-08T10:00:00", "source": "x", "stale": True},
        "nonsense": {"value": 1, "time": "2026-11-08T10:00:00"},
        "co2": {"value": 500, "time": "yesterday"},
        "humidity": "bad",
    }})
    assert list(readings) == ["temperature"] and stale == {"temperature"}


def test_dashboard_room_card(backend):
    from aurora.interface.dashboard import DashboardView

    page = DashboardView(backend)
    now = datetime.now().isoformat(timespec="seconds")
    backend.sensor_readings.emit({"readings": {
        "temperature": {"value": 22.5, "time": now, "source": "Room ESP32"},
        "co2": {"value": 1700, "time": now, "source": "Room ESP32"},
    }})
    assert page.room_card.value.text() == "22.5 °C"
    assert page.room_card.detail.text().startswith("air stuffy · check CO₂ · ")
    backend.sensor_readings.emit({"readings": {
        "temperature": {"value": 22.5, "time": now, "source": "Simulator", "simulated": True}}})
    assert page.room_card.detail.text().endswith("SIMULATED")


def test_tray_notifies_only_when_allowed(backend, monkeypatch):
    from PySide6.QtGui import QIcon

    from aurora.interface.windows.tray import Tray

    tray = Tray(backend, QIcon(), None)
    monkeypatch.setattr(settings, "SENSOR_NOTIFICATIONS", False)
    backend.sensor_alert.emit({"message": "CO₂ is high"})
    assert tray.notified == []
    monkeypatch.setattr(settings, "SENSOR_NOTIFICATIONS", True)
    backend.sensor_alert.emit({"message": "CO₂ is high"})
    assert tray.notified == ["CO₂ is high"]


def test_preferences_for_sensors(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    prefs = preferences.defaults()
    assert prefs["sensor_monitoring"] is False and prefs["sensor_notifications"] is True
    prefs.update({"sensor_monitoring": True, "sensor_notifications": False})
    preferences.save(prefs)
    preferences.apply(preferences.load())
    assert settings.SENSOR_MONITOR is True and settings.SENSOR_NOTIFICATIONS is False

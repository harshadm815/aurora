"""
Command Centre tests. Qt runs offscreen (no window appears); the real
engine is used with a fake language model and the usual isolated
databases, network/camera/microphone blocks from conftest.py.
"""

import json
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.brain.brain import Brain
from aurora.config import preferences, settings
from aurora.interface import system_info
from aurora.interface.backend import Backend, describe_event, wait_for
from aurora.kernel.engine import AuroraEngine
from aurora.knowledge.fact import Fact
from aurora.providers.base_provider import BaseProvider
from aurora.tool_plugins.registry import default_tools

from conftest import FakeProvider
from test_weather import client_returning


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class StreamingProvider(BaseProvider):
    def __init__(self, pieces=("Hello", " from", " AURORA")):
        self.pieces = list(pieces)

    def chat(self, messages):
        return "".join(self.pieces)

    def chat_stream(self, messages):
        yield from self.pieces


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setattr(preferences, "PATH", path)
    return path


@pytest.fixture
def backend(qapp, memory, prefs_file):
    def factory():
        return AuroraEngine(Brain(StreamingProvider()), memory=memory,
                            tools=default_tools(memory, client_returning()))

    b = Backend(engine_factory=factory)
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()


# --- Engine streaming (backend change) -----------------------------------------------

def test_engine_streams_model_replies(memory):
    engine = AuroraEngine(Brain(StreamingProvider()), memory=memory, tools=[])
    engine.start()
    pieces = []

    reply = engine.process_message("hello", on_token=pieces.append)

    assert pieces == ["Hello", " from", " AURORA"]
    assert reply == "Hello from AURORA"


def test_streaming_reply_still_gets_memory_note(memory):
    engine = AuroraEngine(Brain(StreamingProvider()), memory=memory, tools=[])
    engine.start()

    reply = engine.process_message("my name is Harry", on_token=lambda piece: None)

    assert reply == "Hello from AURORA\n\n(Remembered Name: Harry)"


def test_without_callback_nothing_changes(memory):
    engine = AuroraEngine(Brain(FakeProvider()), memory=memory, tools=[])
    engine.start()

    assert engine.process_message("hello") == "Test reply."


def test_personality_comes_from_settings(memory, monkeypatch):
    monkeypatch.setattr(settings, "PERSONALITY_PROMPT", "You are a test.")
    provider = FakeProvider()
    engine = AuroraEngine(Brain(provider), memory=memory, tools=[])
    engine.start()

    engine.process_message("hello")

    assert provider.calls[0][0] == {"role": "system", "content": "You are a test."}


# --- Preferences ----------------------------------------------------------------------

def test_preferences_round_trip(prefs_file, monkeypatch):
    values = preferences.load()
    values.update({"model": "phi3", "accent": "purple", "memory_announce": False})
    preferences.save(values)

    loaded = preferences.load()
    assert loaded["model"] == "phi3" and loaded["accent"] == "purple"

    monkeypatch.setattr(settings, "DEFAULT_MODEL", "tinyllama")
    monkeypatch.setattr(settings, "MEMORY_ANNOUNCE", True)
    preferences.apply(loaded)
    assert settings.DEFAULT_MODEL == "phi3"
    assert settings.MEMORY_ANNOUNCE is False


def test_preferences_cannot_change_anything_else(prefs_file):
    prefs_file.write_text(json.dumps({
        "PERMISSION_GRANTS": {"Devices": ["hardware_control"]},
        "DATABASE_PATH": "C:/elsewhere.db",
        "accent": "red",                 # Not an allowed accent.
        "voice_rate": True,              # Wrong type.
        "memory_context_limit": 500,     # Out of range.
    }), encoding="utf-8")

    loaded = preferences.load()

    assert "PERMISSION_GRANTS" not in loaded and "DATABASE_PATH" not in loaded
    assert loaded["accent"] == "cyan"
    assert loaded["voice_rate"] == settings.VOICE_RATE
    assert loaded["memory_context_limit"] == settings.MEMORY_CONTEXT_LIMIT


def test_unreadable_preferences_fall_back_to_defaults(prefs_file):
    prefs_file.write_text("{not json", encoding="utf-8")

    assert preferences.load() == preferences.defaults()


# --- System information -------------------------------------------------------------

def test_human_formats():
    assert system_info.human_bytes(None) == "unavailable"
    assert system_info.human_bytes(3_500_000_000) == "3.3 GB"
    assert system_info.human_duration(3725) == "1h 2m"
    assert system_info.human_duration(42) == "42s"


@pytest.mark.skipif(not system_info.IS_WINDOWS, reason="Windows readings")
def test_real_windows_readings():
    memory = system_info.system_memory()
    assert memory is not None and 0 < memory.percent <= 100
    assert system_info.process_memory_bytes() > 0
    meter = system_info.CpuMeter()
    assert meter.percent() is None          # First call has nothing to compare with.
    import time
    time.sleep(0.2)
    assert 0 <= meter.percent() <= 100


# --- Backend --------------------------------------------------------------------------

def test_chat_through_the_backend_streams(backend):
    pieces, replies = [], []

    backend.send_message("hello", replies.append, pytest.fail, on_token=pieces.append)

    assert wait_for(lambda: replies, 10)
    assert pieces == ["Hello", " from", " AURORA"]
    assert replies == [("Hello from AURORA", False, "Sources: language model (training data, not checked)")]


def test_backend_reports_pending_confirmation(backend):
    replies = []
    backend.send_message("remember that my exam is on Friday", replies.append, pytest.fail)
    wait_for(lambda: replies, 10)

    backend.send_message("forget my exam", replies.append, pytest.fail)

    assert wait_for(lambda: len(replies) == 2, 10)
    assert replies[1][1] is True       # The chat shows Yes / No buttons.


def test_activity_feed_has_no_user_content(backend):
    replies = []
    backend.send_message("my name is Zorblax", replies.append, pytest.fail)
    wait_for(lambda: replies, 10)
    wait_for(lambda: False, 0.2)

    texts = [a.text for a in backend.activities]
    assert any("Request routed to Brain" in t for t in texts)
    assert any("Memory added: Name" in t for t in texts)
    assert not any("Zorblax" in t for t in texts)


def test_log_view_has_no_user_content(backend, caplog):
    caplog.set_level(logging.INFO, logger="aurora")
    replies = []
    backend.send_message("remember that Quillhaven is lovely", replies.append, pytest.fail)
    wait_for(lambda: replies, 10)
    wait_for(lambda: False, 0.2)

    assert backend.logs
    assert not any("Quillhaven" in line.message for line in backend.logs)


def test_start_failure_is_reported(qapp, prefs_file):
    def broken():
        raise RuntimeError("no engine")

    b = Backend(engine_factory=broken)
    failures = []
    b.engine_failed.connect(failures.append)
    b.start()

    assert wait_for(lambda: failures, 5)
    assert "no engine" in failures[0]
    b.shutdown()


def test_saving_preferences_updates_the_running_engine(backend, prefs_file):
    backend.save_preferences({"personality": "You are AURORA, testing.", "model": "phi3"})
    wait_for(lambda: backend.engine.brain.provider.__dict__.get("model") == "phi3"
             or backend.engine.conversation.messages[0]["content"] == "You are AURORA, testing.", 5)

    assert backend.engine.conversation.messages[0]["content"] == "You are AURORA, testing."
    assert json.loads(prefs_file.read_text(encoding="utf-8"))["model"] == "phi3"


def test_describe_event_ignores_unknown_events():
    from aurora.events.event import Event

    assert describe_event(Event("something.else")) is None


# --- Window and pages -----------------------------------------------------------------

@pytest.fixture
def window(backend):
    from aurora.interface.main_window import MainWindow

    w = MainWindow(backend)
    w.show()
    yield w
    w.close()


def test_every_page_opens(window):
    for name in window.pages:
        window.open_page(name)
        assert window.stack.currentWidget() is window.pages[name]


def test_chat_page_round_trip(window):
    chat = window.pages["Chat"]
    window.open_page("Chat")

    chat.send("what is 12 * 7")
    assert wait_for(lambda: not chat.busy, 10)
    chat.send("hello")
    assert wait_for(lambda: not chat.busy, 10)

    from aurora.interface.chat import Bubble

    bubbles = [b.raw for b in chat.findChildren(Bubble)]
    assert "12 * 7 = 84" in bubbles
    assert "Hello from AURORA" in bubbles


def test_chat_escapes_html(window):
    from aurora.interface.chat import Bubble

    bubble = Bubble("AURORA", "<img src='file:///C:/secret.png'> **bold**")

    assert "<img" not in bubble.text.text()


def test_memory_page_lists_and_filters(window, backend):
    backend.engine.memory.learn(Fact("Personal", "User", "Name", "Harry", 5))
    backend.engine.memory.learn(Fact("Note", "User", "Note", "exam on Friday", 3))
    page = window.pages["Memory"]
    window.open_page("Memory")
    assert wait_for(lambda: page.table.rowCount() == 2, 5)

    page.category.setCurrentText("Note")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 3).text() == "exam on Friday"

    page.category.setCurrentText("All categories")
    page.search.setText("harry")
    assert page.table.rowCount() == 1


def test_tools_page_shows_real_states(window):
    page = window.pages["Tools"]
    window.open_page("Tools")
    states = {page.table.item(r, 0).text(): page.table.item(r, 1).text() for r in range(page.table.rowCount())}

    assert states["Calculator"] == "● Available"
    assert "Weather" in states and "Camera" in states


def test_tools_page_calculator(window):
    page = window.pages["Tools"]
    page.expression.setText("2 ** 10")
    page._calculate()

    assert wait_for(lambda: page.answer.text() == "2 ** 10 = 1024", 5)


def test_sensor_page_labels_simulated_values(window):
    page = window.pages["Sensors"]
    window.open_page("Sensors")

    assert wait_for(lambda: page.cards["temperature"].badge.text() == "SIMULATED", 5)
    assert page.cards["co2"].badge.text() == "SIMULATED"            # the simulator now covers air too
    assert page.cards["temperature"].meaning.text() != ""             # with what it means
    assert page.health_cards["heart_rate"].value.text() == "—"   # Not fetched until asked.


def test_vision_page_never_starts_the_camera_by_itself(window):
    page = window.pages["Vision"]
    window.open_page("Vision")

    assert page.thread is None
    assert page.live_badge.text() == "CAMERA OFF"
    assert not page.analyse.isEnabled()


def test_dashboard_shows_real_state(window, backend):
    page = window.pages["Dashboard"]
    window.open_page("Dashboard")
    page._refresh_status()

    assert page.ai_card.value.text() == "Online"
    # The fake provider has no model name; the card says so rather than inventing one.
    assert page.model_card.value.text() == "unknown"

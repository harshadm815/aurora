"""
The Command Centre's look and live state: the AI core and its states, the
top bar, round gauges and the redesigned dashboard. No network, no
microphone, no real model.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.brain.brain import Brain
from aurora.config import preferences, settings
from aurora.interface.backend import Backend, wait_for
from aurora.interface.components import core_orb
from aurora.interface.components.gauges import RingGauge, level_colour
from aurora.interface.components.top_bar import internet_summary
from aurora.interface.dashboard import state_detail, upcoming, when_text
from aurora.kernel.engine import AuroraEngine
from aurora.providers.base_provider import BaseProvider
from aurora.network import gate as network_gate

from conftest import FakeProvider


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory))
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()


def settle(app, seconds=0.3):
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# --- The AI core ------------------------------------------------------------------------

def test_every_state_has_a_label_and_still_states_never_animate():
    assert set(core_orb.LABELS) == set(core_orb.STATES)
    assert core_orb.frame_interval("error") is None and core_orb.frame_interval("offline") is None
    assert core_orb.frame_interval("idle") > core_orb.frame_interval("thinking")       # idle repaints less


def test_orb_draws_every_state(qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage, QPainter

    images = {}
    for state in core_orb.STATES:
        image = QImage(120, 120, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        core_orb.paint(painter, QPointF(60, 60), 120, state, 1.0, QColor("#22d3ee"), 0.5)
        painter.end()
        images[state] = image
    assert all(not image.isNull() for image in images.values())
    assert images["thinking"] != images["idle"] and images["error"] != images["offline"]


def test_orb_timer_runs_only_when_visible_animated_and_busy(qapp):
    orb = core_orb.CoreOrb()
    orb.set_state("thinking")
    assert not orb.running                       # not shown yet
    orb.show()
    assert orb.running
    orb.set_state("offline")
    assert not orb.running
    orb.set_state("idle")
    assert orb.running
    orb.set_animated(False)
    assert not orb.running
    orb.set_animated(True)
    orb.hide()
    assert not orb.running
    orb.set_state("listening")
    orb.set_level(3.0)
    assert orb.level == 1.0
    orb.set_state("idle")
    assert orb.level == 0.0
    orb.deleteLater()


# --- Backend: what AURORA is doing --------------------------------------------------------

def test_ai_state_follows_a_streamed_reply(backend, qapp):
    class Streaming(BaseProvider):
        def chat(self, messages):
            return "Hello there"

        def chat_stream(self, messages):
            yield from ("Hello", " there")

    backend.engine.brain.provider = Streaming()
    seen = []
    backend.ai_state_changed.connect(seen.append)
    assert backend.ai_state == "idle"
    replies = []
    backend.send_message("hi", replies.append, lambda error: replies.append(error), on_token=lambda piece: None)
    assert backend.ai_state == "thinking"
    assert wait_for(lambda: bool(replies), 10)
    settle(qapp)
    assert seen[0] == "thinking" and "responding" in seen and backend.ai_state == "idle"


def test_ai_state_error_offline_and_voice(backend, qapp):
    backend._reply_failed()
    assert backend.ai_state == "error"
    backend._error_until = 0
    backend._refresh_ai_state()
    assert backend.ai_state == "idle"
    backend._voice_state("listening")
    assert backend.ai_state == "listening"
    backend._voice_state("ready")
    assert backend.ai_state == "idle"
    backend.pause()
    assert backend.ai_state == "offline"
    backend._set_state("failed")
    assert backend.ai_state == "error"


# --- Gauges, top bar, dashboard helpers --------------------------------------------------

def test_gauge_colours():
    accent = "#22d3ee"
    assert level_colour(40, accent) == accent
    assert level_colour(80, accent) != accent and level_colour(95, accent) != level_colour(80, accent)
    assert level_colour(100, accent, more_is_better=True) == accent
    assert level_colour(60, accent, more_is_better=True) == level_colour(80, accent)        # amber
    assert level_colour(None, accent) != accent


def test_gauge_text(qapp):
    gauge = RingGauge("CPU")
    gauge.set(42.4)
    assert gauge.text == "42%" and gauge.percent == 42.4
    gauge.set(150, "x")
    assert gauge.percent == 100
    gauge.set(None)
    assert gauge.text == "—"
    gauge.deleteLater()


def test_internet_summary_reflects_the_gate(monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "off")
    assert internet_summary()[:2] == ("Internet off", "off")
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "ask")
    monkeypatch.setattr(network_gate, "_approved", {"News"})
    text, state, tooltip = internet_summary()
    assert text == "Internet: asks first · 1 allowed" and state == "warning" and "News" in tooltip


def test_upcoming_lists_reminders_and_active_tasks_soonest_first():
    now = datetime(2026, 11, 12, 9, 0)
    reminders = [SimpleNamespace(due=now + timedelta(hours=3), text="call home")]
    tasks = [SimpleNamespace(active=True, name="desk light on", next_run=lambda _: now + timedelta(hours=1)),
             SimpleNamespace(active=False, name="paused task", next_run=lambda _: now),
             SimpleNamespace(active=True, name="never", next_run=lambda _: None)]
    assert upcoming(reminders, tasks, now) == [(now + timedelta(hours=1), "desk light on", "Scheduled"),
                                               (now + timedelta(hours=3), "call home", "Reminder")]
    assert when_text(now + timedelta(hours=2), now) == "11:00"
    assert when_text(now + timedelta(days=1), now) == "tomorrow 09:00"
    assert state_detail("idle", "tinyllama").endswith("tinyllama")


def test_window_top_bar_and_dashboard(backend, qapp):
    from aurora.interface.main_window import MainWindow

    window = MainWindow(backend, use_tray=False)
    window.show()
    try:
        bar = window.top_bar
        assert bar.ai.text_label.text() == "AURORA · Ready"
        window.open_page("Memory")
        assert bar.page.text() == window.pages["Memory"].title
        dashboard = window.pages["Dashboard"]
        backend._set_activity("thinking")
        assert dashboard.state_label.text() == "Thinking" and bar.ai.text_label.text() == "AURORA · Thinking"
        assert dashboard.orb.state == "thinking"
        backend._set_activity(None)

        sent = []
        window.pages["Chat"].send = sent.append
        window.open_page("Dashboard")
        dashboard.ask_box.setText("what's 2 + 2?")
        dashboard._ask()
        assert sent == ["what's 2 + 2?"] and window.current == "Chat" and dashboard.ask_box.text() == ""

        window.open_page("Dashboard")
        dashboard._refresh_status()
        assert dashboard.ai_card.value.text() == "Online" and "Brain" in dashboard.ai_card.detail.text()
        assert dashboard.tools_gauge.text.endswith(f"/{len(backend.status()['tools'])}")
    finally:
        window.close()

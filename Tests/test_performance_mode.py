"""
Performance mode, the voice waveform and the Dashboard's Connections
panel. No network, no microphone, no real model, no Chromium.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.brain.brain import Brain
from aurora.config import preferences, settings
from aurora.interface import performance
from aurora.interface.backend import Backend, wait_for
from aurora.interface.components import core_orb, waveform
from aurora.interface.dashboard import browser_line, internet_line, research_line
from aurora.kernel.engine import AuroraEngine

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


# --- Pressure ------------------------------------------------------------------------

def test_pressure_needs_three_busy_samples_and_three_calm_ones():
    p = performance.Pressure()
    assert not p.update(95, 50) and not p.update(95, 50)          # two busy samples aren't enough
    assert p.update(95, 50) and "CPU 95%" in p.reason
    assert p.update(75, 50)                                      # between calm and high: stays on
    assert p.update(40, 50) and p.update(40, 50)                 # the 75 is still in the window
    assert p.update(40, 50) is False and p.reason == ""


def test_pressure_from_memory_and_one_spike_doesnt_count():
    p = performance.Pressure()
    for cpu in (20, 99, 20):
        p.update(cpu, 50)
    assert not p.under_pressure
    for _ in range(3):
        p.update(10, 93)
    assert p.under_pressure and "RAM 93%" in p.reason


def test_missing_readings_never_mean_pressure():
    p = performance.Pressure()
    for _ in range(5):
        p.update(None, None)
    assert not p.under_pressure


def test_modes():
    calm, busy = performance.Pressure(), performance.Pressure()
    for _ in range(3):
        busy.update(99, 50)
    assert performance.reduced("on", calm) and not performance.reduced("off", busy)
    assert performance.reduced("auto", busy) and not performance.reduced("auto", calm)
    assert performance.describe("off", busy) == "" and performance.describe("auto", calm) == ""
    assert "busy" in performance.describe("auto", busy) and "Settings" in performance.describe("on", calm)


def test_preference_is_checked():
    assert preferences.defaults()["performance_mode"] == "auto"
    assert preferences._valid("performance_mode", "on")
    assert not preferences._valid("performance_mode", "turbo")


# --- The AI core in performance mode -------------------------------------------------

def test_reduced_core_is_still_while_idle_and_slower_while_busy(qapp):
    assert core_orb.frame_interval("idle", reduced=True) is None
    assert core_orb.frame_interval("thinking", reduced=True) == core_orb.REDUCED_FRAME_MS
    assert core_orb.frame_interval("thinking", reduced=True) > core_orb.frame_interval("thinking")
    assert core_orb.frame_interval("error", reduced=True) is None

    orb = core_orb.CoreOrb()
    orb.show()
    try:
        orb.set_state("idle")
        assert orb.running
        orb.set_reduced(True)
        assert not orb.running
        orb.set_state("thinking")
        assert orb.running and orb._timer.interval() == core_orb.REDUCED_FRAME_MS
        orb.set_reduced(False)
        assert orb._timer.interval() == core_orb.BUSY_FRAME_MS
    finally:
        orb.close()


# --- Voice waveform ------------------------------------------------------------------

def test_waveform_moves_only_while_listening_or_speaking(qapp):
    wave = waveform.Waveform()
    wave.show()
    try:
        wave.set_state("idle")
        wave.push(0.8)                                   # ignored: not listening
        assert not wave.running and max(wave.heights()) == 0

        wave.set_state("listening")
        assert not wave.running                          # redrawn by the microphone, not a timer
        wave.push(0.5)
        wave.push(2.0)
        assert wave.heights()[-2:] == [0.5, 1.0]

        wave.set_state("speaking")
        assert wave.running and wave._timer.interval() == waveform.FRAME_MS
        wave.set_reduced(True)
        assert wave._timer.interval() == waveform.REDUCED_FRAME_MS
        wave.set_animated(False)
        assert not wave.running and len(wave.heights()) == waveform.BARS

        wave.set_state("thinking")
        assert not wave.running and max(wave.heights()) == 0
        wave.grab()                                      # paints without errors
    finally:
        wave.close()


def test_speaking_pattern_stays_in_range():
    for t in (0.0, 0.37, 5.2, 100.0):
        heights = waveform.speaking_heights(t)
        assert len(heights) == waveform.BARS and all(0.0 < h <= 1.0 for h in heights)


# --- Connections ---------------------------------------------------------------------

def test_internet_line():
    services = {"Weather": {"name": "Weather"}, "Browser": {"name": "AURORA Browser"}}
    assert internet_line("off", [], services).startswith("Off")
    assert internet_line("on", [], services).startswith("On")
    assert internet_line("ask", [], services) == "Asks first · nothing allowed yet"
    assert internet_line("ask", ["Browser", "Weather"], services).endswith("AURORA Browser, Weather")


def test_browser_line():
    assert browser_line(None) == "Not open" and browser_line([]) == "No tabs open"
    assert browser_line([("New tab", "")]) == "1 tab open"
    tabs = [("A", "a.org"), ("B", "b.org"), ("A2", "a.org"), ("C", "c.org"), ("D", "d.org")]
    assert browser_line(tabs) == "5 tabs open · a.org, b.org, c.org +1"


def test_research_line():
    assert research_line([]).startswith("No projects yet")
    session = SimpleNamespace(time=datetime(2026, 11, 10, 9, 0))
    older = SimpleNamespace(time=datetime(2026, 11, 1, 9, 0))
    projects = [SimpleNamespace(name="Dark matter", sessions=[older]),
                SimpleNamespace(name="Black holes", sessions=[session, older])]
    assert research_line(projects) == "2 projects · 3 sessions · last: Black holes, 10 Nov"


def test_dashboard_shows_performance_waveform_and_connections(backend, qapp, monkeypatch):
    from aurora.interface.main_window import MainWindow

    monkeypatch.setattr(settings, "INTERNET_ACCESS", "off")
    window = MainWindow(backend, use_tray=False)
    window.show()
    try:
        dashboard = window.pages["Dashboard"]
        assert dashboard.browser_row.text() == "Not open"           # the browser engine isn't started
        assert dashboard.internet_row.text().startswith("Off")

        backend._set_activity("listening")
        assert dashboard.waveform.mode == "listening"
        backend._set_activity(None)
        assert dashboard.waveform.mode == "idle"

        for _ in range(3):
            backend.pressure.update(97, 40)
        backend._update_performance()
        assert backend.performance_reduced and dashboard.orb.reduced and dashboard.waveform.reduced
        assert not dashboard.performance_label.isHidden() and "CPU 97%" in dashboard.performance_label.text()

        backend.save_preferences({**backend.preferences, "performance_mode": "off"})
        assert not backend.performance_reduced and not dashboard.orb.reduced
        assert dashboard.performance_label.isHidden()

        dashboard._refresh_slow()
        assert wait_for(lambda: (qapp.processEvents() or True) and
                        dashboard.research_row.text() not in ("", "Loading…"), 5)
    finally:
        window.close()

"""
Settings save themselves (no button to forget). Uses a temporary
preferences file; the Wake Engine helper and the Windows startup entry
are replaced, so nothing is started or changed on this PC.
"""

import json
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.config import preferences, settings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp, tmp_path, monkeypatch):
    from aurora.interface import settings_view
    from aurora.interface.backend import Backend
    from aurora.wake import launcher

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    calls = []
    monkeypatch.setattr(launcher, "reload_helper", lambda: calls.append("reload") or False)
    monkeypatch.setattr(launcher, "start_helper", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(launcher, "stop_helper", lambda: calls.append("stop"))
    monkeypatch.setattr(launcher, "helper_startup_enabled", lambda registry=None: False)
    monkeypatch.setattr(launcher, "set_helper_startup", lambda enabled, registry=None: calls.append(("startup", enabled)))
    monkeypatch.setattr(settings_view.SettingsView, "_apply_startup", lambda self: True)
    backend = Backend(engine_factory=lambda: None)
    view = settings_view.SettingsView(backend)
    view.calls = calls
    yield view
    backend.voice.shutdown()


def _saved(view) -> dict:
    path = preferences.PATH
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _wait(qapp, until, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.02)
    return until()


def test_a_tick_box_saves_itself(page, qapp):
    page.wake_enabled.setChecked(True)
    assert _wait(qapp, lambda: _saved(page).get("wake_enabled") is True)
    assert "start" in page.calls                                  # the helper is started at once
    assert page.save_status.text().startswith("✓  Saved at")
    page.wake_enabled.setChecked(False)
    assert _wait(qapp, lambda: _saved(page).get("wake_enabled") is False) and "stop" in page.calls


def test_lists_and_numbers_save_themselves(page, qapp):
    page.wake_key.setCurrentIndex(page.wake_key.findData("space"))
    page.voice_rate.setValue(210)
    assert _wait(qapp, lambda: _saved(page).get("wake_key") == "space" and _saved(page).get("voice_rate") == 210)
    assert page.backend.preferences["voice_rate"] == 210


def test_personality_saves_after_typing_stops(page, qapp):
    page.personality.setPlainText("You are AURORA. Be brief.")
    assert _wait(qapp, lambda: _saved(page).get("personality") == "You are AURORA. Be brief.", seconds=4)


def test_a_change_is_not_lost_when_leaving_the_page(page, qapp):
    page.accent.setCurrentText("purple")
    page.on_hidden()                                              # before the short delay has passed
    assert _saved(page).get("accent") == "purple"


def test_a_model_ollama_lacks_is_not_saved_but_the_rest_is(page, qapp):
    page.backend.ollama["models"] = ["tinyllama:latest"]
    page.model.setCurrentText("AuroraML")
    page.animations.setChecked(not page.animations.isChecked())
    page.auto_save()
    saved = _saved(page)
    assert saved["model"] == page.backend.preferences["model"] != "AuroraML"
    assert saved["animations"] == page.animations.isChecked()
    assert "doesn't have 'AuroraML'" in page.save_status.text() and page.save_status.text().startswith("⚠")
    page.model.setCurrentText("tinyllama")
    page.auto_save()
    assert _saved(page)["model"] == "tinyllama" and page.save_status.text().startswith("✓")


def test_an_invalid_wake_phrase_is_not_saved(page, qapp):
    page.wake_phrase.setText("wake up 123!")
    page.auto_save()
    assert _saved(page).get("wake_phrase", settings.WAKE_PHRASE) == settings.WAKE_PHRASE
    assert "wake phrase" in page.save_status.text()


def test_save_now_button_and_nothing_to_save(page, qapp):
    page.save()
    page.save()
    assert page.save_status.text() == "✓  Everything is saved."


def test_the_page_says_changes_apply_immediately(page):
    assert "immediately" in page.subtitle

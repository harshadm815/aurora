"""
Voice conversations (Chat's conversation mode and waking AURORA), the
Briefing tool, and spoken reminders. Speech and listening are replaced by
scripts: no microphone, no speakers, no network.
"""

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from aurora.config import settings
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins import briefing
from aurora.voice import conversation


# --- Phrases ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["That's all.", "goodbye", "Aurora, that's all", "OK thank you that's all",
                                  "stop listening", "Never mind!", "we're done"])
def test_end_phrases(text):
    assert conversation.is_end(text)


@pytest.mark.parametrize("text", ["what's the weather", "no", "all the best", "remind me at 6", ""])
def test_not_end_phrases(text):
    assert not conversation.is_end(text)


def test_yes_and_no():
    assert conversation.is_yes("Yes please.") and conversation.is_yes("sure, go ahead")
    assert conversation.is_no("Not now") and conversation.is_no("no thanks")
    assert not conversation.is_yes("yes but tell me the weather") and not conversation.is_no("nothing")


# --- Briefing ----------------------------------------------------------------------------

NOW = datetime(2026, 11, 14, 9, 0)


def _reminder(text, due):
    return SimpleNamespace(text=text, due=due)


def _task(name, when, active=True):
    return SimpleNamespace(name=name, active=active, next_run=lambda now: when)


def _project(name, *times):
    return SimpleNamespace(name=name, sessions=[SimpleNamespace(time=t, summary=f"Read two papers on {name}.")
                                               for t in times])


def test_briefing_gathers_only_today():
    b = briefing.gather(
        [_reminder("call home", NOW.replace(hour=18)), _reminder("pay rent", NOW - timedelta(hours=2)),
         _reminder("dentist", NOW + timedelta(days=2))],
        [_task("Morning lights", NOW.replace(hour=12)), _task("Weekly backup", NOW + timedelta(days=3)),
         _task("Paused", NOW.replace(hour=10), active=False)],
        [_project("dark matter", NOW - timedelta(days=1)), _project("old", NOW - timedelta(days=10))], NOW)
    assert [r.text for r in b.today] == ["call home"] and [r.text for r in b.overdue] == ["pay rent"]
    assert [name for _, name in b.tasks] == ["Morning lights"]
    assert b.research[0].name == "dark matter"
    assert briefing.summary_line(b) == "You have two reminders, one scheduled task and a research update today."
    text = briefing.text(b)
    assert text.startswith("Here's your briefing for Saturday 14 November.")
    assert "Overdue: pay rent (was due 07:00)" in text and "at 18:00, call home" in text
    assert "at 12:00, Morning lights" in text and "dark matter was on Friday" in text
    assert "dentist" not in text and "Weekly backup" not in text and "Paused" not in text and "old" not in text


def test_empty_briefing_says_so():
    b = briefing.gather([], [], [_project("old", NOW - timedelta(days=5))], NOW)
    assert b.empty and briefing.summary_line(b) == ""
    assert "Nothing is scheduled" in briefing.text(b)


def test_counts_are_words():
    assert briefing.count(1, "reminder") == "one reminder" and briefing.count(3, "task") == "three tasks"
    assert briefing.count(12, "task") == "12 tasks"


@pytest.mark.parametrize("text", ["give me my briefing", "brief me", "what's on today", "whats on today",
                                  "what do I have today", "briefing please", "Aurora, read me my morning briefing",
                                  "what's my day look like"])
def test_briefing_is_routed(text):
    assert IntentRouter().route(text).intent is Intent.BRIEFING


@pytest.mark.parametrize("text", ["morning briefing", "my briefing", "briefing"])
def test_plain_names_stay_free_for_routines(text):
    """'when I say morning briefing, …' must still be teachable as a routine."""
    assert IntentRouter().route(text).intent is not Intent.BRIEFING


@pytest.mark.parametrize("text,intent", [("what is 12 times 7", Intent.CALCULATOR),
                                         ("remind me at 6 to call home", Intent.REMINDER),
                                         ("what is the weather today", Intent.WEATHER)])
def test_other_requests_are_unchanged(text, intent):
    assert IntentRouter().route(text).intent is intent


def test_briefing_tool_through_the_engine(memory):
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    engine = AuroraEngine(Brain(FakeProvider()), memory=memory)
    engine.start()
    try:
        later = datetime.now().replace(hour=23, minute=58, second=0, microsecond=0)
        if later > datetime.now():
            engine.tool_manager.get("Reminders").scheduler.add("water the plants", later)
        text = engine.process_message("give me my briefing")
        assert "Here's your briefing" in text
        if later > datetime.now():
            assert "water the plants" in text
    finally:
        engine.shutdown()


# --- Conversations in the Command Centre ---------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)
    return until()


@pytest.fixture
def chat_window(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.config import preferences
    from aurora.interface.backend import Backend, wait_for
    from aurora.interface.main_window import MainWindow
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    backend = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory))
    window = MainWindow(backend, use_tray=False)
    backend.start()
    assert wait_for(lambda: backend.engine is not None, 10)
    said, answers, listened = [], [], []

    def speak(text, on_done=None):
        said.append(text)
        if on_done:
            on_done(True)

    def listen(on_heard, on_error):
        listened.append(1)
        on_heard(answers.pop(0) if answers else "")

    monkeypatch.setattr(backend.voice, "speak", speak)
    monkeypatch.setattr(backend.voice, "listen", listen)
    window.said, window.answers, window.listened = said, answers, listened
    yield window
    window.close()
    backend.shutdown()


def _bubbles(chat):
    from aurora.interface.chat import Bubble
    return [b.raw if hasattr(b, "raw") else "" for b in chat.findChildren(Bubble)]


def test_conversation_goes_back_and_forth_until_thats_all(chat_window, qapp):
    chat = chat_window.pages["Chat"]
    chat_window.answers.extend(["what is 12 times 7", "what is 3 plus 4", "that's all"])
    chat.start_conversation()
    assert _pump(qapp, 15, lambda: not chat.in_conversation)
    said = chat_window.said
    assert said[0] == "12 * 7 = 84" and said[1] == "3 + 4 = 7"
    assert said[-1] == "Okay. I'm here if you need me." and len(chat_window.listened) == 3
    assert not chat.talk_button.isChecked()


def test_silence_ends_the_conversation(chat_window, qapp):
    chat = chat_window.pages["Chat"]
    chat.start_conversation()                              # nobody answers
    assert not chat.in_conversation and "Conversation ended" in chat.voice_status.text()
    assert chat_window.said == []


def test_stop_ends_the_conversation(chat_window, qapp):
    chat = chat_window.pages["Chat"]
    chat_window.answers.extend(["what is 2 plus 2"])
    stopped = []

    def speak(text, on_done=None):
        chat_window.said.append(text)
        chat.stop_voice()                                  # Stop pressed while AURORA speaks
        stopped.append(1)
        if on_done:
            on_done(False)

    chat_window.backend_speak = speak
    from aurora.interface import chat as chat_module   # noqa: F401
    chat.backend.voice.speak = speak
    chat.start_conversation()
    _pump(qapp, 10, lambda: stopped)
    assert stopped and not chat.in_conversation and len(chat_window.listened) == 1   # never listened again


def test_offer_yes_gives_the_briefing(chat_window, qapp):
    chat = chat_window.pages["Chat"]
    chat_window.answers.extend(["yes please", "that's all"])
    chat.start_conversation("I'm awake. You have one reminder today. Would you like a briefing?",
                            offer="give me my briefing")
    assert _pump(qapp, 15, lambda: not chat.in_conversation)
    said = chat_window.said
    assert said[0].endswith("Would you like a briefing?") and said[1].startswith("Here's your briefing")


def test_offer_no_asks_how_to_help(chat_window, qapp):
    chat = chat_window.pages["Chat"]
    chat_window.answers.extend(["not now", "goodbye"])
    chat.start_conversation("Would you like a briefing?", offer="give me my briefing")
    assert _pump(qapp, 10, lambda: not chat.in_conversation)
    assert chat_window.said[1] == "Okay. How can I help?"
    assert not any(s.startswith("Here's your briefing") for s in chat_window.said)


def test_waking_offers_a_briefing_when_something_is_due(chat_window, qapp):
    later = datetime.now() + timedelta(minutes=5)
    if later.date() != datetime.now().date():
        pytest.skip("too close to midnight for a reminder 'today'")
    chat_window.backend.engine.tool_manager.get("Reminders").scheduler.add("stretch", later)
    chat_window.answers.extend(["that's all"])
    chat_window.wake()
    assert _pump(qapp, 10, lambda: chat_window.said)
    assert chat_window.said[0] == "I'm awake. You have one reminder today. Would you like a briefing?"


def test_waking_with_nothing_due_just_greets(chat_window, qapp):
    chat_window.answers.extend(["that's all"])
    chat_window.wake()
    assert _pump(qapp, 10, lambda: chat_window.said)
    assert chat_window.said[0] == settings.WAKE_GREETING


def test_reminders_are_spoken_only_when_switched_on(chat_window, qapp):
    chat_window.say_reminder({"text": "call home"})
    assert chat_window.said == []
    chat_window.backend.preferences["speak_reminders"] = True
    chat_window.say_reminder({"text": "call home"})
    assert chat_window.said == ["Reminder: call home"]

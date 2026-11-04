"""End-to-end message handling: router → executor → tool → reply."""

import httpx
import pytest

from aurora.brain.brain import Brain
from aurora.events import topics
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.tool_plugins.registry import default_tools
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

from test_weather import client_returning


@pytest.fixture
def engine(fake_provider, memory):
    engine = AuroraEngine(
        Brain(fake_provider),
        memory=memory,
        tools=default_tools(memory, client_returning()),
    )
    engine.start()
    yield engine
    engine.shutdown()


def test_calculator_answers_instead_of_the_brain(engine, fake_provider):
    reply = engine.process_message("What is 12 * (7 + 3)?")

    assert reply == "12 * (7 + 3) = 120"
    assert fake_provider.calls == []  # The language model wasn't asked.


def test_conversation_still_goes_to_the_brain(engine, fake_provider):
    assert engine.process_message("hello there") == "Test reply."
    assert len(fake_provider.calls) == 1


def test_tool_replies_are_part_of_the_conversation(engine, fake_provider):
    engine.process_message("What is 2 + 2?")
    engine.process_message("thanks!")

    history = fake_provider.calls[0]
    assert {"role": "assistant", "content": "2 + 2 = 4"} in history


def test_remember_then_recall(engine):
    assert engine.process_message("remember that my exam is on Friday").startswith("Noted.")

    reply = engine.process_message("what do you know about my exam?")

    assert "my exam is on Friday" in reply


def test_weather(engine):
    reply = engine.process_message("what's the weather in Pune?")

    assert reply.startswith("Pune, Maharashtra, India, now: 27°C")


def test_weather_failure_is_reported_not_guessed(fake_provider, memory):
    engine = AuroraEngine(
        Brain(fake_provider), memory=memory,
        tools=default_tools(memory, client_returning(error=httpx.ConnectError("offline"))),
    )
    engine.start()

    reply = engine.process_message("what's the weather in Pune?")

    assert "I won't guess the weather" in reply
    assert fake_provider.calls == []


# --- Confirmation flow ----------------------------------------------------

def remember_exam(engine):
    engine.process_message("remember that my exam is on Friday")


def test_forget_asks_and_deletes_on_yes(engine, memory):
    remember_exam(engine)

    question = engine.process_message("forget my exam")
    assert "Should I go ahead?" in question
    assert len(memory.memories()) == 1

    assert engine.process_message("yes") == "Done. I deleted 1 memory."
    assert memory.memories() == []


def test_forget_cancelled_on_no(engine, memory):
    remember_exam(engine)
    engine.process_message("forget my exam")

    assert engine.process_message("no") == "Okay, I won't do that."
    assert len(memory.memories()) == 1


def test_a_new_message_cancels_the_pending_action(engine, memory, fake_provider):
    remember_exam(engine)
    engine.process_message("forget my exam")

    reply = engine.process_message("hello there")

    assert reply == "Test reply."
    assert len(memory.memories()) == 1
    assert engine.process_message("yes") == "Test reply."  # Nothing pending any more.
    assert len(memory.memories()) == 1


def test_clear_cancels_the_pending_action(engine, memory):
    remember_exam(engine)
    engine.process_message("forget my exam")

    engine.clear_conversation()
    engine.process_message("yes")

    assert len(memory.memories()) == 1


def test_confirmation_events(engine):
    seen = []
    engine.events.subscribe(topics.CONFIRMATION_REQUESTED, seen.append)
    engine.events.subscribe(topics.CONFIRMATION_ANSWERED, seen.append)
    remember_exam(engine)

    engine.process_message("forget my exam")
    engine.process_message("yes")

    assert [e.name for e in seen] == [topics.CONFIRMATION_REQUESTED, topics.CONFIRMATION_ANSWERED]
    assert seen[1].data == {"tool": "Forget", "approved": True}


# --- Permissions and failures ---------------------------------------------

def test_tool_without_permission_explains_how_to_grant(fake_provider, memory):
    engine = AuroraEngine(
        Brain(fake_provider), permissions=PermissionManager(), memory=memory,
        tools=default_tools(memory, client_returning()),
    )
    engine.start()

    reply = engine.process_message("what's the weather in Pune?")

    assert "not allowed to use the Weather tool" in reply
    assert "'network'" in reply
    assert fake_provider.calls == []
    assert engine.tool_status()["Weather"] == "Blocked (needs network)"
    assert engine.tool_status()["Calculator"] == "Ready"


class CrashingCalculator(BaseTool):
    def __init__(self):
        super().__init__("Calculator")

    def execute(self, **_):
        raise RuntimeError("boom")


def test_crashing_tool_is_reported(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=[CrashingCalculator()])
    failures = []
    engine.events.subscribe(topics.TOOL_FAILED, failures.append)
    engine.start()

    reply = engine.process_message("2 + 2")

    assert reply == "The Calculator tool failed unexpectedly (boom)."
    assert failures[0].data == {"tool": "Calculator", "error": "boom"}


def test_intent_without_a_registered_tool_falls_back_to_brain(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=[])
    engine.start()

    assert engine.process_message("2 + 2") == "Test reply."


def test_default_grants_allow_the_built_in_tools(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory)

    assert set(engine.tool_status().values()) == {"Ready"}

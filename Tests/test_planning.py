import pytest

from aurora.brain.brain import Brain
from aurora.events import topics
from aurora.kernel.engine import AuroraEngine
from aurora.planning.planner import Planner
from aurora.routing.intent import Intent
from aurora.tool_plugins.registry import default_tools

from test_weather import client_returning


@pytest.fixture(scope="module")
def planner():
    return Planner()


def steps(planner, message):
    plan = planner.plan(message)
    return [
        (s.decision.intent.value, dict(s.decision.arguments)) for s in plan.steps
    ] if plan.is_multi_step else "single"


# --- Splitting --------------------------------------------------------------

def test_weather_for_several_places_then_calculation(planner):
    assert steps(planner, "What is the weather in Pune and Delhi, then calculate 12 * 7") == [
        ("weather", {"location": "Pune"}),
        ("weather", {"location": "Delhi"}),
        ("calculator", {"expression": "12 * 7"}),
    ]


@pytest.mark.parametrize(
    "message, expressions",
    [
        ("what is 2+2 and 3*5", ["2+2", "3*5"]),
        ("what is 2 + 2, 3 * 4 and 10 / 4", ["2 + 2", "3 * 4", "10 / 4"]),
        ("2+2; 3+3", ["2+2", "3+3"]),
        ("What's 2+2? And what's 3+3?", ["2+2", "3+3"]),
    ],
)
def test_several_calculations(planner, message, expressions):
    assert [args["expression"] for _, args in steps(planner, message)] == expressions


def test_tool_then_question_for_the_brain(planner):
    assert steps(planner, "What is the weather in Pune tomorrow and what should I wear?") == [
        ("weather", {"location": "Pune", "when": "tomorrow"}),
        ("conversation", {}),
    ]


def test_statement_and_tool(planner):
    assert steps(planner, "my name is Harry and what is 2+2") == [
        ("conversation", {}),
        ("calculator", {"expression": "2+2"}),
    ]


def test_place_carries_over_to_the_next_weather_step(planner):
    assert steps(planner, "weather in Pune. will it rain tomorrow?") == [
        ("weather", {"location": "Pune"}),
        ("weather", {"location": "Pune", "when": "tomorrow"}),
    ]


def test_follow_up_inherits_place_and_day_and_is_merged(planner):
    assert steps(planner, "What is the weather in Pune tomorrow and should I carry an umbrella? Keep it short.") == [
        ("weather", {"location": "Pune", "when": "tomorrow"}),
        ("conversation", {}),
    ]


def test_remember_owns_the_rest_of_the_sentence(planner):
    assert steps(planner, "what is 2+2 and remember that I like tea and coffee") == [
        ("calculator", {"expression": "2+2"}),
        ("remember", {"content": "I like tea and coffee"}),
    ]


@pytest.mark.parametrize(
    "message",
    [
        "I like tea and coffee",                                 # One statement.
        "Hi, my name is Harry and I live in Pune",               # No tools.
        "remember that the weather in Pune and Delhi was great", # Command owns it all.
        "I need 3/4 cup of flour and 2 eggs",                    # Not a calculation.
        "what is 12 * (7 + 3)?",                                 # One tool.
        "weather in Pune and Pune",                              # Duplicate merged.
        "hello there",
        "",
    ],
)
def test_single_step(planner, message):
    assert steps(planner, message) == "single"


def test_deletion_is_never_combined(planner):
    plan = planner.plan("what is 2+2 and forget my exam")

    assert [s.decision.intent for s in plan.steps] == [Intent.CALCULATOR]
    assert "separate message" in plan.notes[0]


def test_forget_alone_is_a_single_step(planner):
    plan = planner.plan("forget my exam")

    assert not plan.is_multi_step
    assert plan.steps[0].decision.intent is Intent.FORGET


def test_long_requests_are_truncated():
    plan = Planner(max_steps=3).plan("1+1; 2+2; 3+3; 4+4")

    assert len(plan.steps) == 3
    assert plan.truncated


def test_plan_to_dict_has_no_user_text(planner):
    plan = planner.plan("weather in Quillhaven and what is 2+2")

    assert "Quillhaven" not in str(plan.to_dict())


# --- Running plans in the engine ------------------------------------------

@pytest.fixture
def engine(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory,
                          tools=default_tools(memory, client_returning()))
    engine.start()
    yield engine
    engine.shutdown()


def test_tool_only_plan_does_not_use_the_brain(engine, fake_provider):
    reply = engine.process_message("what is 2+2 and 3*5")

    assert reply == "2+2 = 4\n\n3*5 = 15"
    assert fake_provider.calls == []


def test_weather_for_two_places(engine):
    reply = engine.process_message("weather in Pune and Delhi")

    # The fake weather service returns Pune for any place; two lookups were made.
    assert reply.count("(Source: Open-Meteo)") == 2


def test_brain_gets_tool_results_for_the_conversational_part(engine, fake_provider):
    reply = engine.process_message("What is 12 * 7 and can you explain why multiplication matters?")

    assert reply == "12 * 7 = 84\n\nTest reply."
    sent = fake_provider.calls[0]
    tool_context = [m for m in sent if m["role"] == "system" and "tools produced" in m["content"]]
    assert tool_context and "- 12 * 7 = 84" in tool_context[0]["content"]
    # The full original message is still what the user said.
    assert sent[-1]["content"] == "What is 12 * 7 and can you explain why multiplication matters?"


def test_statement_in_a_plan_is_still_learned(engine, memory):
    reply = engine.process_message("my name is Harry and what is 2+2")

    assert reply.startswith("2+2 = 4\n\nTest reply.")
    assert "(Remembered Name: Harry)" in reply
    assert [(f.attribute, f.value) for f in memory.memories()] == [("Name", "Harry")]


def test_plan_with_remember(engine, memory):
    engine.process_message("what is 2+2 and remember that my exam is on Friday")

    assert [f.value for f in memory.memories()] == ["my exam is on Friday"]


def test_deletion_in_a_plan_is_not_performed(engine, memory):
    engine.process_message("remember that my exam is on Friday")

    reply = engine.process_message("what is 2+2 and forget my exam")

    assert reply.startswith("2+2 = 4")
    assert "separate message" in reply
    assert len(memory.memories()) == 1
    assert engine.pending is None


def test_truncated_plan_says_so(engine, monkeypatch):
    monkeypatch.setattr(engine.planner, "max_steps", 2)

    reply = engine.process_message("1+1; 2+2; 3+3")

    assert reply.startswith("1+1 = 2\n\n2+2 = 4")
    assert "first 2 parts" in reply


def test_plan_events(engine):
    seen = []
    for name in (topics.PLAN_CREATED, topics.PLAN_STEP_COMPLETED, topics.PLAN_COMPLETED):
        engine.events.subscribe(name, seen.append)

    engine.process_message("what is 2+2 and 3*5")

    assert [e.name for e in seen] == [
        topics.PLAN_CREATED, topics.PLAN_STEP_COMPLETED,
        topics.PLAN_STEP_COMPLETED, topics.PLAN_COMPLETED,
    ]
    assert seen[0].data["steps"] == [
        {"intent": "calculator", "target": "Calculator"},
        {"intent": "calculator", "target": "Calculator"},
    ]


def test_single_step_messages_are_unchanged(engine, fake_provider):
    assert engine.process_message("what is 2+2") == "2+2 = 4"
    assert engine.process_message("hello") == "Test reply."

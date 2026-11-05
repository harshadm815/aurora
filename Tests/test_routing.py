import pytest

from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, message):
    return router.route(message)


# --- RouteDecision model --------------------------------------------------

def test_decision_validates_confidence():
    with pytest.raises(ValueError):
        RouteDecision(Intent.CONVERSATION, 1.5)


def test_decision_validates_intent():
    with pytest.raises(TypeError):
        RouteDecision("conversation", 0.5)


def test_decision_arguments_are_read_only():
    decision = RouteDecision(Intent.CALCULATOR, 0.9, "Calculator", {"expression": "1+1"})

    with pytest.raises(TypeError):
        decision.arguments["expression"] = "import os"


def test_decision_to_dict():
    decision = RouteDecision(Intent.WEATHER, 0.9, "Weather", {"location": "Pune"}, "why")

    assert decision.to_dict() == {
        "intent": "weather",
        "confidence": 0.9,
        "target": "Weather",
        "arguments": {"location": "Pune"},
        "reason": "why",
        "alternatives": [],
    }


# --- Calculator -----------------------------------------------------------

@pytest.mark.parametrize(
    "message, expression",
    [
        ("12 * (7 + 3)", "12 * (7 + 3)"),
        ("what is 5 plus 3?", "5 + 3"),
        ("What's 10 divided by 4", "10 / 4"),
        ("calculate 6 times 7", "6 * 7"),
        ("what is 3 x 4", "3 * 4"),
        ("Aurora, calculate 2^3", "2 ** 3"),
        ("-5 + 3", "-5 + 3"),
        ("how much is 2 to the power of 8?", "2 ** 8"),
    ],
)
def test_arithmetic_goes_to_calculator(router, message, expression):
    decision = route(router, message)

    assert decision.intent is Intent.CALCULATOR
    assert decision.target == "Calculator"
    assert decision.arguments["expression"] == expression
    assert decision.confidence >= 0.9


@pytest.mark.parametrize(
    "message",
    [
        # Regressions: the old router sent anything with - or / to the calculator.
        "This is a well-known fact",
        "check http://example.com/a/1",
        "and/or",
        "my birthday is 2004-11-04",
        "call me at 555-1234",
        "12/25/2026 is Christmas",
        # Numbers without arithmetic.
        "I have 3 cats",
        "the year 2026",
    ],
)
def test_non_arithmetic_is_not_calculator(router, message):
    assert route(router, message).intent is not Intent.CALCULATOR


def test_arithmetic_inside_a_sentence_stays_conversation_with_alternative(router):
    decision = route(router, "I need 3/4 cup of flour for the cake")

    assert decision.intent is Intent.CONVERSATION
    assert decision.alternatives[0].intent is Intent.CALCULATOR


# --- Weather --------------------------------------------------------------

@pytest.mark.parametrize(
    "message, location, when",
    [
        ("what is the weather in Pune tomorrow?", "Pune", "tomorrow"),
        ("weather", None, "now"),
        ("forecast for Mumbai this weekend", "Mumbai", "this weekend"),
        ("will it rain today?", None, "today"),
        ("how hot is it in New Delhi right now", "New Delhi", "right now"),
        ("Aurora, what's the weather like?", None, "now"),
    ],
)
def test_weather(router, message, location, when):
    decision = route(router, message)

    assert decision.intent is Intent.WEATHER
    assert decision.target == "Weather"
    assert decision.arguments.get("location") == location
    assert decision.arguments.get("when", "now") == when  # Absent means "now".


@pytest.mark.parametrize(
    "message",
    [
        "what is the temperature in my room?",   # Sensor, not weather.
        "my laptop temperature is high",
        "Purple Rain is my favourite song",       # Not a question about rain.
        "I have a fever, what temperature is dangerous?",
    ],
)
def test_not_weather(router, message):
    assert route(router, message).intent is not Intent.WEATHER


# --- Memory ---------------------------------------------------------------

@pytest.mark.parametrize(
    "message, content",
    [
        ("remember that my exam is on Friday", "my exam is on Friday"),
        ("Aurora, please remember I like tea", "I like tea"),
        ("can you remember that my sister is Priya", "my sister is Priya"),
        ("don't forget my dentist appointment is at 5", "my dentist appointment is at 5"),
        ("note that the lab key is in drawer 2.", "the lab key is in drawer 2"),
    ],
)
def test_remember(router, message, content):
    decision = route(router, message)

    assert decision.intent is Intent.REMEMBER
    assert decision.target == "Memory"
    assert decision.arguments["content"] == content


@pytest.mark.parametrize(
    "message, query",
    [
        ("what do you know about me?", "me"),
        ("do you remember my name?", "my name"),
        ("what is my favourite colour?", "favourite colour"),
        ("what did I tell you about Mars", "Mars"),
        ("remember what I told you about Mars?", "Mars"),
    ],
)
def test_recall(router, message, query):
    decision = route(router, message)

    assert decision.intent is Intent.RECALL
    assert decision.target == "Memory"
    assert decision.arguments["query"] == query


@pytest.mark.parametrize(
    "message, query",
    [
        ("forget my old address", "my old address"),
        ("forget about the meeting", "the meeting"),
        ("delete the memory about my exam", "my exam"),
        ("Aurora, erase memories about Rahul", "Rahul"),
    ],
)
def test_forget(router, message, query):
    decision = route(router, message)

    assert decision.intent is Intent.FORGET
    assert decision.arguments["query"] == query


@pytest.mark.parametrize(
    "message",
    [
        "I can't remember the formula",   # Not a command to AURORA.
        "my favourite colour is blue",     # A statement; automatic extraction is Milestone 5.
        "forget it",                       # "Never mind".
        "delete my files",                 # Not about memory.
        "hello there",
    ],
)
def test_conversation(router, message):
    decision = route(router, message)

    assert decision.intent is Intent.CONVERSATION
    assert decision.target == "Brain"


# --- Router behaviour -----------------------------------------------------

def test_empty_message_asks_for_clarification(router):
    assert route(router, "   ").needs_clarification


def test_explicit_remember_beats_weather_keyword(router):
    decision = route(router, "remember that the weather in Pune was great")

    assert decision.intent is Intent.REMEMBER


def test_near_tie_between_tools_asks_for_clarification():
    def a(original, body):
        return RouteDecision(Intent.WEATHER, 0.8, "Weather")

    def b(original, body):
        return RouteDecision(Intent.CALCULATOR, 0.75, "Calculator")

    decision = IntentRouter(rules=(a, b)).route("anything")

    assert decision.intent is Intent.CLARIFY
    assert decision.target is None
    assert [alt.intent for alt in decision.alternatives][:2] == [Intent.WEATHER, Intent.CALCULATOR]


def test_failing_rule_is_skipped():
    def broken(original, body):
        raise RuntimeError("boom")

    def calc(original, body):
        return RouteDecision(Intent.CALCULATOR, 0.9, "Calculator")

    decision = IntentRouter(rules=(broken, calc)).route("1+1")

    assert decision.intent is Intent.CALCULATOR


def test_winner_lists_alternatives_best_first(router):
    decision = route(router, "12 * (7 + 3)")

    assert decision.alternatives[-1].intent is Intent.CONVERSATION


def test_explicit_forget_beats_calculator_content(router):
    decision = route(router, "forget that 2 + 2 is 5")

    assert decision.intent is Intent.FORGET


@pytest.mark.parametrize(
    "message, query",
    [
        ("where do I live?", "live"),
        ("what do I study", "study"),
        ("where do I work?", "work"),
        ("what do I like?", "like"),
        ("who am I?", "me"),
    ],
)
def test_recall_questions_about_the_user(router, message, query):
    decision = route(router, message)

    assert decision.intent is Intent.RECALL
    assert decision.arguments["query"] == query


def test_what_do_i_need_is_not_recall(router):
    assert route(router, "what do I need for the exam?").intent is Intent.CONVERSATION

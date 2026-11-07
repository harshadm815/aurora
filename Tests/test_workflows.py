"""Phase 8: Numbers tool, workflows, validation and error recovery."""

import logging

import httpx
import pytest

from aurora.brain.brain import Brain
from aurora.events import topics
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.planning.planner import Planner
from aurora.routing.intent import Intent
from aurora.routing.numbers_rule import extract_numbers, numbers_rule
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.calculator import CalculatorTool
from aurora.tool_plugins.numbers import NumbersTool, compute_percentage, compute_statistic
from aurora.tool_plugins.registry import default_tools
from aurora.tool_plugins.weather import WeatherTool
from aurora.tools.executor import ToolExecutor
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.tool_manager import ToolManager
from aurora.workflows import validation
from aurora.workflows.builder import WorkflowBuilder
from aurora.workflows.operations import Operation, OperationError, apply, parse_operation, split_trailing_operations
from aurora.workflows.runner import WorkflowRunner
from aurora.workflows.values import ImplausibleValue, Value, check, pick, values_from
from aurora.workflows.workflow import DONE, FAILED, SKIPPED
from test_weather import FORECAST, client_returning

EXAMPLE = "Find the average of these numbers and explain the result: 4, 8, 15, 16, 23, 42"


# ============================================================ Numbers tool


@pytest.mark.parametrize("operation,numbers,value", [
    ("mean", [4, 8, 15, 16, 23, 42], 18),
    ("mean", [1, 2], 1.5),
    ("median", [3, 1, 9, 4], 3.5),
    ("median", [5, 1, 3], 3),
    ("mode", [1, 2, 2, 3], 2),
    ("sum", [0.1, 0.2], 0.3),
    ("sum", [1000, 2500], 3500),
    ("count", [7, 7, 7], 3),
    ("min", [4, -2, 9], -2),
    ("max", [4, -2, 9], 9),
    ("range", [4, -2, 9], 11),
    ("stdev", [2, 4, 4, 4, 5, 5, 7, 9], 2),
])
def test_statistics(operation, numbers, value):
    assert compute_statistic(operation, numbers).value == value


def test_mode_without_a_single_answer():
    assert compute_statistic("mode", [1, 2, 3]).value is None
    assert compute_statistic("mode", [1, 1, 2, 2]).value is None
    assert "2 modes" in compute_statistic("mode", [1, 1, 2, 2]).summary


@pytest.mark.parametrize("operation,a,b,value", [
    ("percent_of", 15, 80, 12), ("what_percent", 12, 80, 15),
    ("increase", 80, 15, 92), ("decrease", 80, 15, 68), ("percent_of", 12.5, 8, 1),
])
def test_percentages(operation, a, b, value):
    assert compute_percentage(operation, a, b).value == value


def test_explanation_of_the_blueprint_example():
    result = NumbersTool().execute(operations=("mean",), numbers=(4, 8, 15, 16, 23, 42), explain=True)
    assert result.success
    assert result.message == (
        "The average of these 6 numbers is 18.\n\n"
        "Add them up: 4 + 8 + 15 + 16 + 23 + 42 = 108.\n"
        "Divide by how many there are: 108 ÷ 6 = 18."
    )
    assert result.data["value"] == 18 and result.data["explained"]


def test_skewed_average_is_explained_with_the_median():
    out = compute_statistic("mean", [1, 1, 1, 100])
    assert out.value == 25.75
    assert "median (middle value) is 1" in out.explanation[-1] and "pulled up by 100" in out.explanation[-1]


def test_several_calculations_at_once():
    result = NumbersTool().execute(operations=("mean", "median"), numbers=(3, 1, 9, 4))
    assert result.data["values"] == {"mean": 4.25, "median": 3.5}
    assert result.message == "The average of these 4 numbers is 4.25.\n\nThe median of these 4 numbers is 3.5."


def test_stated_count_must_match():
    result = NumbersTool().execute(operations=("mean",), numbers=(1, 2, 3), stated_count=4)
    assert not result.success and "You mentioned 4 numbers, but I found 3" in result.message


@pytest.mark.parametrize("kwargs", [
    {"operations": ("mean",), "numbers": ()},
    {"operations": ("stdev",), "numbers": (5,)},
    {"operations": ("mean",), "numbers": (1, float("nan"))},
    {"operations": ("mean",), "numbers": (1, 1e16)},
    {"operations": ("mean",), "numbers": tuple(range(1001))},
    {"operations": ("mean",), "numbers": (1, True)},
    {"operations": ("what_percent",), "numbers": (5, 0)},
    {"operations": ("percent_of",), "numbers": (1, 2, 3)},
    {"operations": ("cube",), "numbers": (1, 2)},
])
def test_invalid_number_requests_fail_cleanly(kwargs):
    result = NumbersTool().execute(**kwargs)
    assert not result.success and result.message.startswith("I couldn't calculate that")


def test_numbers_tool_needs_no_permissions():
    assert NumbersTool().required_permissions == ()


# ============================================================ routing


@pytest.mark.parametrize("message,arguments", [
    (EXAMPLE, {"operations": ("mean",), "numbers": (4, 8, 15, 16, 23, 42), "explain": True}),
    ("what is the average of 4, 8 and 15", {"operations": ("mean",), "numbers": (4, 8, 15)}),
    ("what is the sum of 3, 5 and 9", {"operations": ("sum",), "numbers": (3, 5, 9)}),
    ("find the median of 3, 1, 9, 4", {"operations": ("median",), "numbers": (3, 1, 9, 4)}),
    ("mean and median of 3 1 9 4", {"operations": ("mean", "median"), "numbers": (3, 1, 9, 4)}),
    ("calculate 15% of 80", {"operation": "percent_of", "numbers": (15, 80)}),
    ("what percent is 12 of 80", {"operation": "what_percent", "numbers": (12, 80)}),
    ("what percentage of 80 is 12", {"operation": "what_percent", "numbers": (12, 80)}),
    ("increase 80 by 15%", {"operation": "increase", "numbers": (80, 15)}),
    ("reduce 80 by 15 percent", {"operation": "decrease", "numbers": (80, 15)}),
    ("average of these 3 numbers 10 20 30", {"operations": ("mean",), "numbers": (10, 20, 30), "stated_count": 3}),
    ("average of -2.5, 3 and .5", {"operations": ("mean",), "numbers": (-2.5, 3, 0.5)}),
    ("total of 1,000 and 2,500", {"operations": ("sum",), "numbers": (1000, 2500)}),
    ("show your working: standard deviation of 2, 4, 4", {"operations": ("stdev",), "numbers": (2, 4, 4), "explain": True}),
])
def test_number_questions_route_to_the_numbers_tool(message, arguments):
    decision = IntentRouter().route(message)
    assert decision.intent is Intent.NUMBERS and decision.target == "Numbers"
    assert dict(decision.arguments) == arguments


@pytest.mark.parametrize("message,intent", [
    ("what is the average temperature in Pune", Intent.WEATHER),
    ("what is 12 * (7 + 3)", Intent.CALCULATOR),
    ("what is 3 + 4 times 2", Intent.CALCULATOR),
    ("who is the president of France", Intent.KNOWLEDGE),
    ("I have 3 cats and 2 dogs", Intent.CONVERSATION),
    ("the total of my 2 bills", Intent.CONVERSATION),
    ("what is the mean of life", Intent.KNOWLEDGE),
])
def test_other_messages_are_not_taken(message, intent):
    assert IntentRouter().route(message).intent is intent


@pytest.mark.parametrize("text,numbers", [
    ("4, 8, 15", [4, 8, 15]),
    ("4,8,15", [4, 8, 15]),
    ("1,000 and 2,500", [1000, 2500]),
    ("1,000, 2,500", [1, 0, 2, 500]),          # documented: a comma-space list makes every comma a separator
    ("−3 and 2.5", [-3, 2.5]),
    ("x2 and 7", [7]),
])
def test_number_extraction(text, numbers):
    assert extract_numbers(text) == numbers


def test_numbers_rule_needs_two_numbers():
    assert numbers_rule("average of 5", "average of 5") is None


# ============================================================ values


def test_weather_values_are_rounded_like_the_reply():
    data = {"when": "now", "forecast": {"current": {"temperature_2m": 26.6, "apparent_temperature": 29.2,
                                                     "relative_humidity_2m": 60, "wind_speed_10m": 8.4}}}
    values = values_from("Weather", data)
    assert [(v.name, v.number, v.unit) for v in values] == [
        ("temperature", 27, "°C"), ("feels like", 29, "°C"), ("humidity", 60, "%"), ("wind", 8, "km/h")]


def test_forecasts_for_other_days_give_no_single_value():
    assert values_from("Weather", {"when": "tomorrow", "forecast": FORECAST}) == []


@pytest.mark.parametrize("value", [
    Value("temperature", 900, "°C", measured=True),
    Value("humidity", 140, "%", measured=True),
    Value("result", float("inf")),
    Value("result", 1e16),
])
def test_implausible_values_are_rejected(value):
    with pytest.raises(ImplausibleValue):
        check(value)


def test_a_computed_temperature_is_not_held_to_weather_limits():
    check(Value("temperature", 108, "°C"))           # e.g. a doubled 54 °C, not a reading


def test_pick():
    values = [Value("temperature", 27, "°C"), Value("humidity", 60, "%")]
    assert pick(values, None).name == "temperature"
    assert pick(values, "humidity").number == 60
    assert pick(values, "temp").name == "temperature"
    assert pick(values, "wind") is None and pick([], None) is None


# ============================================================ operations


@pytest.mark.parametrize("message,rest,operations", [
    ("what's the weather in Pune and double the temperature", "what's the weather in Pune", [("multiply", 2, "temperature")]),
    ("what is 12 * 7, then add 5 and halve the result", "what is 12 * 7", [("add", 5, None), ("divide", 2, "result")]),
    ("weather in Pune. What is that in kelvin?", "weather in Pune.", [("convert", None, None)]),
    ("average of 4, 8 and 15 and divide it by 3", "average of 4, 8 and 15", [("divide", 3, None)]),
    ("I want to double my savings", "I want to double my savings", []),
    ("what is 3 + 4 times 2", "what is 3 + 4 times 2", []),
    ("what is 10 minus 3", "what is 10 minus 3", []),
])
def test_split_trailing_operations(message, rest, operations):
    left, found = split_trailing_operations(message)
    assert left == rest
    assert [(o.kind, o.operand, o.label) for o in found] == operations


def test_parse_operation_needs_the_whole_text():
    assert parse_operation("and double it").kind == "multiply"
    assert parse_operation("double it please") is None
    assert parse_operation("let's double it") is None


@pytest.mark.parametrize("operation,value,result,line", [
    (Operation("multiply", 2), Value("temperature", 27, "°C"), 54, "Doubled: 27°C × 2 = 54°C."),
    (Operation("divide", 2), Value("result", 89), 44.5, "Halved: 89 ÷ 2 = 44.5."),
    (Operation("add", 5), Value("temperature", 21, "°C"), 26, "Added: 21°C + 5°C = 26°C."),
    (Operation("convert", unit="°F"), Value("temperature", 27, "°C"), 80.6, "The temperature in Fahrenheit: 27 × 9/5 + 32 = 80.6°F."),
    (Operation("convert", unit="K"), Value("temperature", 21, "°C"), 294.15, "The temperature in kelvin: 21 + 273.15 = 294.15 K."),
    (Operation("round"), Value("result", 44.5), 44, "Rounded: 44.5 rounded = 44."),
])
def test_apply(operation, value, result, line):
    new, text = apply(operation, value)
    assert new.number == result and text == line


@pytest.mark.parametrize("operation,value", [
    (Operation("convert", unit="°F"), Value("humidity", 60, "%")),
    (Operation("convert", unit="°F"), Value("result", 9)),
    (Operation("divide", 0), Value("result", 5)),
    (Operation("square"), Value("temperature", 20, "°C")),
])
def test_impossible_operations_are_refused(operation, value):
    with pytest.raises(OperationError):
        apply(operation, value)


# ============================================================ validation


def test_unexplained_numbers():
    assert validation.unexplained_numbers("Doubled: 27°C × 2 = 54°C.", [27, 54]) == set()
    assert validation.unexplained_numbers("Doubled: 27°C × 2 = 55°C.", [27, 54]) == {"55"}
    assert validation.unexplained_numbers("−3 + 5 = 2", [-3, 5, 2]) == set()


def test_numbers_messages_only_contain_accounted_numbers():
    for kwargs in ({"operations": ("mean",), "numbers": (1, 1, 1, 100)},
                   {"operations": ("stdev",), "numbers": (2, 4, 4, 4, 5, 5, 7, 9)},
                   {"operations": ("median", "range"), "numbers": (3, 1, 9, 4)},
                   {"operations": ("increase",), "numbers": (80, 15)}):
        result = NumbersTool().execute(explain=True, **kwargs)
        assert validation.unexplained_numbers(result.message, validation.numbers_tool_allowed(result.data)) == set()


# ============================================================ builder


@pytest.fixture
def builder():
    return WorkflowBuilder(Planner())


def test_builder_makes_workflows_for_numbers_and_follow_ups(builder):
    flow = builder.build(EXAMPLE)
    assert [s.kind for s in flow.steps] == ["tool"] and flow.steps[0].decision.target == "Numbers"

    flow = builder.build("what's the weather in Pune and double the temperature")
    assert [(s.kind, s.depends_on) for s in flow.steps] == [("tool", None), ("operation", 1)]
    assert flow.steps[0].decision.arguments["location"] == "Pune"

    flow = builder.build("what is 12 * 7, then add 5 and halve the result")
    assert [(s.kind, s.depends_on) for s in flow.steps] == [("tool", None), ("operation", 1), ("operation", 2)]

    flow = builder.build("Weather in Pune. Then double it.")
    assert [s.kind for s in flow.steps] == ["tool", "operation"]


@pytest.mark.parametrize("message", [
    "hello there", "what is 12 * 7", "what's the weather in Pune",
    "double it",                                       # nothing to apply it to
    "weather in Pune and tell me a joke",              # a conversational part
    "turn on the fan and double it",                   # devices need confirmation
    "forget my exam and double it",
])
def test_builder_leaves_other_messages_alone(builder, message):
    assert builder.build(message) is None


# ============================================================ runner


class Flaky(BaseTool):
    """Fails `failures` times with a retryable error, then succeeds."""

    def __init__(self, name="Weather", failures=1, retryable=True):
        super().__init__(name)
        self.failures, self.retryable, self.calls = failures, retryable, 0

    def execute(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            return ToolResult(False, "The weather service took too long to respond.", retryable=self.retryable)
        return ToolResult(True, "Pune now: 27°C.", {"when": "now", "forecast": {"current": {"temperature_2m": 27}}})


def runner_with(*tools):
    manager = ToolManager(PermissionManager({}))
    for tool in tools:
        manager.register(tool)
    sleeps = []
    return WorkflowRunner(ToolExecutor(manager), sleep=sleeps.append, retry_delay=0.5), sleeps


DOUBLE = "what's the weather in Pune and double the temperature"


def test_runner_completes_a_dependent_workflow(builder):
    runner, sleeps = runner_with(Flaky(failures=0))
    trace = runner.run(builder.build(DOUBLE))
    assert trace.succeeded and sleeps == []
    assert trace.message == "Pune now: 27°C.\n\nDoubled: 27°C × 2 = 54°C."
    assert trace.record(2).values[0].number == 54


def test_temporary_failure_is_retried_once(builder):
    tool = Flaky(failures=1)
    runner, sleeps = runner_with(tool)
    trace = runner.run(builder.build(DOUBLE))
    assert trace.succeeded and tool.calls == 2 and sleeps == [0.5]
    assert trace.record(1).attempts == 2


def test_dependent_step_is_skipped_when_its_input_fails(builder):
    tool = Flaky(failures=5)
    runner, sleeps = runner_with(tool)
    trace = runner.run(builder.build(DOUBLE))
    assert tool.calls == 2 and len(sleeps) == 1                     # one retry, no more
    assert [r.status for r in trace.records] == [FAILED, SKIPPED]
    assert "(I tried 2 times.)" in trace.message
    assert "I didn't double the temperature, because the step it needed" in trace.message
    assert "54" not in trace.message                                # nothing invented


def test_permanent_failures_are_not_retried(builder):
    tool = Flaky(failures=5, retryable=False)
    runner, sleeps = runner_with(tool)
    trace = runner.run(builder.build(DOUBLE))
    assert tool.calls == 1 and sleeps == []


def test_implausible_tool_values_are_not_used(builder):
    class Broken(Flaky):
        def execute(self, **kwargs):
            return ToolResult(True, "Pune now: 900°C.", {"when": "now", "forecast": {"current": {"temperature_2m": 900}}})

    runner, _ = runner_with(Broken())
    trace = runner.run(builder.build(DOUBLE))
    assert [r.status for r in trace.records] == [FAILED, SKIPPED]
    assert "can't trust" in trace.message and "900°C." not in trace.message.split("\n")[0]


def test_missing_value_is_reported_not_guessed(builder):
    runner, _ = runner_with(Flaky(failures=0))
    trace = runner.run(builder.build("what's the weather in Pune and double the humidity"))
    assert trace.record(2).status == FAILED
    assert "didn't give me a humidity" in trace.message


def test_independent_steps_still_run_after_a_failure(builder):
    runner, _ = runner_with(Flaky(failures=5, retryable=False), CalculatorTool())
    flow = builder.build("what's the weather in Pune, then what is 6 * 7 and double it")
    trace = runner.run(flow)
    assert [r.status for r in trace.records] == [FAILED, DONE, DONE]
    assert "6 * 7 = 42" in trace.message and "Doubled: 42 × 2 = 84." in trace.message


def test_steps_asking_for_confirmation_are_refused(builder):
    class Asks(BaseTool):
        def __init__(self):
            super().__init__("Calculator")

        def execute(self, **kwargs):
            return ToolResult(True, "Sure?", confirm=lambda: ToolResult(True, "done"))

    runner, _ = runner_with(Asks())
    trace = runner.run(builder.build("what is 6 * 7 and double it"))
    assert trace.record(1).status == FAILED and "needs your confirmation" in trace.message


def test_validation_replaces_text_with_unaccounted_numbers(builder, monkeypatch):
    import aurora.workflows.runner as runner_module

    real = runner_module.apply
    monkeypatch.setattr(runner_module, "apply", lambda op, v: (real(op, v)[0], "Doubled: 27°C × 2 = 55°C."))
    runner, _ = runner_with(Flaky(failures=0))
    trace = runner.run(builder.build(DOUBLE))
    assert trace.validation_problems and trace.record(2).message == "Result: 54°C."


def test_trace_to_dict_has_no_user_text(builder):
    runner, _ = runner_with(Flaky(failures=0))
    data = runner.run(builder.build(DOUBLE)).to_dict()
    assert data["steps"] == [
        {"kind": "tool", "target": "Weather", "depends_on": None, "status": "done", "attempts": 1},
        {"kind": "operation", "target": "multiply", "depends_on": 1, "status": "done", "attempts": 1},
    ]
    assert "Pune" not in str(data)


# ============================================================ weather retryable


@pytest.mark.parametrize("error,status,retryable", [
    (httpx.ConnectTimeout("slow"), 200, True),
    (httpx.ConnectError("down"), 200, True),
    (None, 503, True),
    (None, 429, True),
    (None, 404, False),
])
def test_weather_marks_temporary_failures(error, status, retryable):
    result = WeatherTool(client_returning(status=status, error=error)).execute(location="Pune")
    assert not result.success and result.retryable is retryable


def test_unknown_place_is_not_retryable():
    result = WeatherTool(client_returning(geocode={"results": []})).execute(location="Nowhere")
    assert not result.success and not result.retryable


# ============================================================ engine


@pytest.fixture
def engine(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), memory=memory, tools=default_tools(memory, client_returning()))
    engine.start()
    yield engine
    engine.shutdown()


def test_engine_answers_the_blueprint_example_without_the_brain(engine, fake_provider):
    reply = engine.process_message(EXAMPLE)
    assert reply.startswith("The average of these 6 numbers is 18.")
    assert "108 ÷ 6 = 18" in reply
    assert fake_provider.calls == []
    assert engine.last_workflow.succeeded


@pytest.mark.parametrize("message,expected", [
    ("what is the average of 4, 8 and 15", "The average of these 3 numbers is 9."),
    ("what is the sum of 3, 5 and 9", "The sum of these 3 numbers is 17."),
    ("calculate 15% of 80", "15% of 80 is 12."),
    ("find the median of 3, 1, 9, 4", "The median of these 4 numbers is 3.5."),
])
def test_engine_number_questions(engine, fake_provider, message, expected):
    assert engine.process_message(message) == expected
    assert fake_provider.calls == []


def test_engine_weather_then_double(engine, fake_provider):
    reply = engine.process_message(DOUBLE)
    assert reply.endswith("Doubled: 27°C × 2 = 54°C.")
    assert "Pune, Maharashtra, India, now: 27°C" in reply
    assert fake_provider.calls == []


def test_engine_emits_workflow_events_without_user_text(engine):
    seen = []
    engine.events.subscribe(topics.WORKFLOW_COMPLETED, lambda event: seen.append(event.data))
    engine.process_message(DOUBLE)
    assert seen and seen[0]["succeeded"] and "Pune" not in str(seen[0])


def test_engine_still_uses_plans_and_the_brain_otherwise(engine, fake_provider):
    assert engine.process_message("hello there") == "Test reply."
    assert engine.last_workflow is None
    engine.process_message("what's the weather in Pune and what should I wear?")
    assert engine.last_workflow is None and fake_provider.calls


def test_engine_logs_no_user_numbers(engine, caplog):
    with caplog.at_level(logging.DEBUG):
        engine.process_message("what is the average of 4817, 9923 and 1234")
    assert "4817" not in caplog.text and "9923" not in caplog.text


def test_terminal_plan_shows_workflows(engine, capsys):
    from main import print_workflow

    print_workflow(engine.workflow(DOUBLE))
    out = capsys.readouterr().out
    assert "1. Weather (location=Pune)" in out and "2. multiply 2 (uses the temperature from step 1)" in out

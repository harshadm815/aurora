import pytest

from aurora.tool_plugins.calculator import CalculationError, CalculatorTool, evaluate
from aurora.tools.tool_manager import ToolManager


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("12 * (7 + 3)", 120),
        ("2 + 3 * 4", 14),
        ("10 / 4", 2.5),
        ("10 // 4", 2),
        ("10 % 4", 2),
        ("2 ** 10", 1024),
        ("-5 + 3", -2),
        ("  1.5 * 2  ", 3.0),
    ],
)
def test_arithmetic(expression, expected):
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hacked')",
        "open('secrets.txt')",
        "x + 1",
        "(1).__class__",
        "[1, 2, 3]",
        "'a' * 3",
        "True + 1",
        "lambda: 1",
    ],
)
def test_rejects_non_arithmetic(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


@pytest.mark.parametrize(
    "expression",
    ["", "   ", "1 +", "1 / 0", "9 ** 9 ** 9", "10 ** 200", "1+" * 200 + "1"],
)
def test_rejects_invalid_or_dangerous_input(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_tool_returns_message_instead_of_raising():
    result = CalculatorTool().execute("1 / 0")

    assert result.startswith("Invalid expression")


def test_tool_manager_runs_calculator_case_insensitively():
    manager = ToolManager()
    manager.register(CalculatorTool())

    assert manager.list_tools() == ["calculator"]
    assert manager.execute("Calculator", "12 * (7 + 3)") == 120


def test_tool_manager_unknown_tool():
    with pytest.raises(ValueError):
        ToolManager().execute("Teleporter")

"""
Calculator tool backed by a safe arithmetic evaluator.

Expressions are parsed with Python's ``ast`` module and only numbers,
parentheses and arithmetic operators are accepted. Names, function
calls, attribute access and everything else are rejected, so input
from a user or an LLM can never execute code.
"""

import ast
import operator

from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT = 1000
MAX_RESULT_MAGNITUDE = 10 ** 100

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculationError(ValueError):
    """Raised when an expression is invalid or not allowed."""


def _check_size(value):
    if abs(value) > MAX_RESULT_MAGNITUDE:
        raise CalculationError("Result is too large.")
    return value


def _evaluate_node(node):

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculationError("Only numbers are allowed.")
        return node.value

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate_node(node.operand))

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)

        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculationError("Exponent is too large.")

        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except ZeroDivisionError:
            raise CalculationError("Division by zero.") from None
        except OverflowError:
            raise CalculationError("Result is too large.") from None

        if isinstance(result, complex):
            raise CalculationError("Complex results are not supported.")

        return _check_size(result)

    raise CalculationError("Unsupported expression.")


def evaluate(expression: str) -> int | float:
    """
    Safely evaluates an arithmetic expression such as ``12 * (7 + 3)``.

    Raises CalculationError for anything that is not plain arithmetic.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise CalculationError("Empty expression.")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculationError("Expression is too long.")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        raise CalculationError("Invalid expression.") from None

    return _evaluate_node(tree.body)


class CalculatorTool(BaseTool):

    def __init__(self):
        super().__init__("Calculator")

    def execute(self, expression: str = "", **_) -> ToolResult:

        try:
            value = evaluate(expression)
        except CalculationError as error:
            return ToolResult(False, f"I couldn't calculate that: {error}")

        return ToolResult(True, f"{expression} = {format_number(value)}", value)


def format_number(value: int | float) -> str:
    """Shows 2.5 as 2.5, 3.0 as 3, and avoids float noise like 0.30000000000000004."""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.10g}"
    return str(value)

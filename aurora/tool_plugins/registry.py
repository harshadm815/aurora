import httpx

from aurora.memory.manager import MemoryManager
from aurora.tool_plugins.calculator import CalculatorTool
from aurora.tool_plugins.memory_tools import ForgetTool, RecallTool, RememberTool
from aurora.tool_plugins.weather import WeatherTool
from aurora.tools.tool import BaseTool


def default_tools(
    memory: MemoryManager,
    http_client: httpx.Client | None = None,
) -> list[BaseTool]:
    """The tools AURORA ships with."""
    return [
        CalculatorTool(),
        RememberTool(memory),
        RecallTool(memory),
        ForgetTool(memory),
        WeatherTool(http_client),
    ]

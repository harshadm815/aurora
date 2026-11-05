import httpx

from aurora.memory.manager import MemoryManager
from aurora.tool_plugins.calculator import CalculatorTool
from aurora.tool_plugins.camera import CameraTool
from aurora.tool_plugins.documents import DocumentsTool
from aurora.tool_plugins.memory_tools import ForgetTool, RecallTool, RememberTool
from aurora.tool_plugins.research import ArxivTool, WikipediaTool
from aurora.tool_plugins.sensors import HealthTool, SensorsTool
from aurora.sensors.hub import SensorHub
from aurora.tool_plugins.weather import WeatherTool
from aurora.tools.tool import BaseTool


def default_tools(
    memory: MemoryManager,
    http_client: httpx.Client | None = None,
) -> list[BaseTool]:
    """The tools AURORA ships with."""
    hub = SensorHub(client=http_client)   # Shared by the room and health tools.
    return [
        CalculatorTool(),
        RememberTool(memory),
        RecallTool(memory),
        ForgetTool(memory),
        WeatherTool(http_client),
        WikipediaTool(http_client),
        ArxivTool(http_client),
        DocumentsTool(),
        CameraTool(),
        SensorsTool(hub),
        HealthTool(hub),
    ]

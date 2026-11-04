from aurora.tool_plugins.calculator import CalculatorTool
from aurora.tools.tool_manager import ToolManager


manager = ToolManager()

manager.register(
    CalculatorTool()
)

print("Installed tools:")

print(manager.list_tools())

print()

print(
    manager.execute(
        "Calculator",
        "12 * (7 + 3)"
    )
)
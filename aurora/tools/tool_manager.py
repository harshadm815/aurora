from aurora.kernel.permissions import PermissionManager
from aurora.tools.tool import BaseTool


class ToolManager:
    """
    Registry of tools. Every call is checked against the tool's
    ``required_permissions`` before it runs (deny by default).
    """

    def __init__(self, permissions: PermissionManager | None = None):
        self.permissions = permissions or PermissionManager()
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        key = tool.name.lower()
        if key in self.tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self.tools[key] = tool

    def get(self, name: str):
        return self.tools.get(name.lower())

    def execute(self, name: str, /, *args, **kwargs):
        # ``name`` is positional-only so a tool argument called "name"
        # (e.g. the document to read) can't collide with it.

        tool = self.get(name)

        if tool is None:
            raise ValueError(
                f"Tool '{name}' not found."
            )

        for permission in tool.required_permissions:
            self.permissions.require(tool.name, permission)

        return tool.execute(*args, **kwargs)

    def list_tools(self):
        return list(self.tools.keys())

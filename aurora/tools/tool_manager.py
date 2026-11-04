from aurora.tools.tool import BaseTool


class ToolManager:

    def __init__(self):
        self.tools = {}

    def register(self, tool: BaseTool):
        self.tools[tool.name.lower()] = tool

    def get(self, name: str):
        return self.tools.get(name.lower())

    def execute(self, name: str, *args, **kwargs):

        tool = self.get(name)

        if tool is None:
            raise ValueError(
                f"Tool '{name}' not found."
            )

        return tool.execute(*args, **kwargs)

    def list_tools(self):
        return list(self.tools.keys())
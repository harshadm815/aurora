from aurora.tools.tool import BaseTool


class CalculatorTool(BaseTool):

    def __init__(self):
        super().__init__("Calculator")

    def execute(self, expression):

        try:
            return eval(expression)

        except Exception:
            return "Invalid expression."
"""
Research assistant: runs the research team for a topic and offers to
save the session to your research log (only after you say yes).
"""

from aurora.agents.research_log import ResearchLog
from aurora.agents.research_session import ResearchSession, build_report
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool


class ResearchAssistantTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ, Permission.MEMORY_WRITE)

    def __init__(self, log: ResearchLog | None = None):
        super().__init__("Research")
        self._log = log
        self.tools = None      # The ToolManager, attached by the engine.
        self.progress = None   # Shows which agent is working (set by main.py).

    def attach_tools(self, tools) -> None:
        self.tools = tools

    @property
    def log(self) -> ResearchLog:
        if self._log is None:
            self._log = ResearchLog()
        return self._log

    def execute(self, action: str = "session", topic: str = "", **_) -> ToolResult:
        if action == "list":
            return self.list()
        if not topic.strip():
            return ToolResult(False, "Which research topic?")
        if self.tools is None:
            return ToolResult(False, "The research team isn't connected to AURORA's tools.")

        session = ResearchSession(self.tools, self.log, self.progress)
        board = session.run(topic)
        report = build_report(board)

        def save() -> ToolResult:
            return ToolResult(True, session.save(board))

        return ToolResult(
            True,
            f"{report}\n\nSave this session to your research log? (yes / no)",
            board,
            confirm=save,
        )

    def list(self) -> ToolResult:
        projects = self.log.projects()
        if not projects:
            return ToolResult(True, "You have no research projects yet. Try: \"continue my research on <topic>\".", [])
        lines = []
        for project in projects:
            last = project.last_session
            when = f"last session {last.time:%d %b %Y}" if last else "no sessions yet"
            lines.append(f"  - {project.name} ({len(project.sessions)} session(s), {when})")
        return ToolResult(True, "Your research projects:\n" + "\n".join(lines), projects)

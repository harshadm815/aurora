"""
A research session: "AURORA, continue my research on black hole cosmogenesis."

The coordinator runs the team in order:

    Memory agent → Documents agent → Research agent → Analyst agent → Reviewer agent

and the report below is assembled only from what they found, each part
labelled with its source.
"""

from collections.abc import Callable
from datetime import datetime

from aurora.agents.base import Blackboard, Coordinator
from aurora.agents.research_agents import (
    AnalystAgent, DocumentsAgent, MemoryAgent, ResearchAgent, ReviewerAgent,
)
from aurora.agents.research_log import ResearchLog
from aurora.memory.auto import describe
from aurora.tools.tool_manager import ToolManager
from aurora.tools.untrusted import one_line, shorten


def _ago(when: datetime, now: datetime) -> str:
    days = (now.date() - when.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def build_report(board: Blackboard, now: datetime | None = None) -> str:
    now = now or datetime.now()
    lines = [f"Research session: {board.topic}"]

    project = board.get("project")
    if project and project.last_session:
        last = project.last_session
        lines.append(f"Last session: {last.time:%d %b %Y} ({_ago(last.time, now)}). {last.summary}")
    else:
        lines.append("This is a new research topic for me.")

    memories = board.get("memories", [])
    if memories:
        lines.append("\nFrom your memory:")
        lines += [f"  - {describe(fact)}" for fact in memories[:4]]

    passages = board.get("passages", [])
    if passages:
        lines.append(f"\nFrom your notes ({len(passages)} passage{'s' if len(passages) != 1 else ''}):")
        lines += [f"  - {p.citation}: \"{shorten(one_line(p.text), 160)}\"" for p in passages[:3]]

    questions = board.get("open_questions", [])
    if questions:
        lines.append("\nOpen questions in your notes:")
        lines += [f"  - {q}" for q in questions]

    background = board.get("background")
    if background:
        lines.append("\nBackground (Wikipedia):")
        lines += [f"  {line}" for line in background.splitlines()]

    papers = board.get("papers", [])
    if papers:
        new = sum(1 for p in papers if p["new"])
        since = " since your last session" if project and project.last_session else ""
        lines.append(f"\nNewest matching papers (arXiv), {new} new{since}:")
        for number, paper in enumerate(papers, start=1):
            tag = "[NEW] " if paper["new"] else ""
            lines.append(f"  {number}. {tag}{paper['title']} ({paper['published']})")
            lines.append(f"     {paper['url']}")
            if paper.get("matches"):
                lines.append(f"     Related to your notes: {', '.join(paper['matches'])}")
        lines.append("  (Preprints: not all have been peer-reviewed.)")

    steps = board.get("next_steps", [])
    if steps:
        lines.append("\nSuggested next steps:")
        lines += [f"  {number}. {step}" for number, step in enumerate(steps, start=1)]

    review = board.get("review", {})
    lines.append("\nReview:")
    answered = review.get("answered", [])
    lines.append(f"  Sources checked: {', '.join(answered) if answered else 'none'}.")
    for problem in board.problems:
        lines.append(f"  ⚠ {problem}")
    if not review.get("complete", False):
        lines.append("  This report is incomplete; missing parts are listed above.")
    lines.append("  Everything above is quoted from those sources; nothing was written by the language model.")

    return "\n".join(lines)


class ResearchSession:

    def __init__(self, tools: ToolManager, log: ResearchLog | None = None,
                 progress: Callable[[str], None] | None = None):
        self.tools = tools
        self.log = log or ResearchLog()
        self.progress = progress

    def team(self):
        return [
            MemoryAgent(self.tools, self.log),
            DocumentsAgent(self.tools),
            ResearchAgent(self.tools),
            AnalystAgent(self.tools),
            ReviewerAgent(self.tools),
        ]

    def run(self, topic: str) -> Blackboard:
        board = Blackboard(topic=one_line(topic))
        return Coordinator(self.team(), self.progress).run(board)

    def save(self, board: Blackboard) -> str:
        papers = board.get("papers", [])
        new = sum(1 for p in papers if p["new"])
        summary = (f"Looked at {len(papers)} recent paper{'s' if len(papers) != 1 else ''} ({new} new) "
                   f"and {len(board.get('passages', []))} passage(s) from your notes.")
        project = self.log.record(board.topic, summary, [p["url"] for p in papers])
        return f"Saved to your research log for \"{project.name}\" ({len(project.sessions)} session(s) so far)."

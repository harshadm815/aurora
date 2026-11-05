"""
Agent framework.

An agent is a specialist with one job. It reads what earlier agents
found from a shared ``Blackboard``, does its job (usually by calling
AURORA's tools, so every permission check still applies), and writes its
findings back. A coordinator runs agents in order.

Agents here are deterministic: they coordinate verified tools rather
than asking the small local model to reason freely, which it can't do
reliably. A model-backed agent can implement the same interface later.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aurora.kernel.permissions import PermissionDeniedError
from aurora.tools.result import ToolResult
from aurora.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)


@dataclass
class Blackboard:
    """Shared working space for one task."""

    topic: str
    facts: dict[str, Any] = field(default_factory=dict)
    sources_ok: list[str] = field(default_factory=list)       # Sources that answered.
    problems: list[str] = field(default_factory=list)          # What went wrong, in plain words.

    def put(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def get(self, key: str, default=None):
        return self.facts.get(key, default)


class Agent:
    """One specialist. Subclasses set ``name``, ``icon``, ``task`` and implement ``run``."""

    name = "Agent"
    icon = "•"
    task = "working"

    def __init__(self, tools: ToolManager | None = None):
        self.tools = tools

    def run(self, board: Blackboard) -> None:
        raise NotImplementedError

    def use_tool(self, board: Blackboard, tool: str, source: str, **arguments) -> ToolResult | None:
        """
        Calls a tool through the ToolManager (so permissions apply).
        Records success or the problem on the blackboard.
        """
        if self.tools is None or self.tools.get(tool) is None:
            board.problems.append(f"{source} isn't available.")
            return None
        try:
            result = self.tools.execute(tool, **arguments)
        except PermissionDeniedError as error:
            board.problems.append(f"{source} is switched off (needs the '{error.permission.value}' permission).")
            return None
        except Exception as error:
            logger.exception("%s failed while using %s", self.name, tool)
            board.problems.append(f"{source} failed ({type(error).__name__}).")
            return None

        if result.success:
            board.sources_ok.append(source)
        else:
            board.problems.append(f"{source}: {result.message.splitlines()[0]}")
        return result


class Coordinator:
    """Runs agents in order, reporting progress."""

    def __init__(self, agents: list[Agent], progress: Callable[[str], None] | None = None):
        self.agents = agents
        self.progress = progress

    def run(self, board: Blackboard) -> Blackboard:
        for agent in self.agents:
            if self.progress:
                self.progress(f"{agent.icon}  {agent.name}: {agent.task}…")
            try:
                agent.run(board)
            except Exception as error:
                # One failing agent never stops the others.
                logger.exception("%s failed", agent.name)
                board.problems.append(f"The {agent.name.lower()} failed ({type(error).__name__}).")
        return board

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """
    What a tool hands back to the kernel.

    - ``success``: whether the tool did what was asked.
    - ``message``: the reply to show the user. Tools write this
      themselves so facts (a calculation, a temperature) reach the user
      exactly as computed, without being re-phrased by the language model.
    - ``data``: the raw result, for other components and tests.
    - ``confirm``: set when the tool wants to do something consequential
      (such as deleting memories). The kernel asks the user first and
      only calls ``confirm()`` if they say yes.
    - ``retryable``: the failure may be temporary (a timeout, a server
      error), so trying again could work. Workflows retry such steps once.
    - ``sources``: where the answer came from (aurora.knowledge.sources):
      a document page, a live service, AURORA's memory, a calculation…
    - ``undo``: for an action that can be reversed, a function that
      reverses it (returning a ToolResult), and ``undo_label`` saying what
      it will do ("turn the desk light back off"). "undo that" asks the
      user before calling it.
    - ``simulated``: the action affected a simulated device (audit trail).
    """

    success: bool
    message: str
    data: Any = None
    confirm: Callable[[], "ToolResult"] | None = None
    retryable: bool = False
    sources: list = field(default_factory=list)
    undo: Callable[[], "ToolResult"] | None = None
    undo_label: str = ""
    simulated: bool = False

    @property
    def needs_confirmation(self) -> bool:
        return self.confirm is not None

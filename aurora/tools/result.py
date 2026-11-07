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
    """

    success: bool
    message: str
    data: Any = None
    confirm: Callable[[], "ToolResult"] | None = None
    retryable: bool = False
    sources: list = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.confirm is not None

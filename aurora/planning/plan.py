from dataclasses import dataclass, field

from aurora.routing.decision import RouteDecision


@dataclass(frozen=True)
class Step:
    """One part of a request: the text it came from and how to handle it."""

    text: str
    decision: RouteDecision

    @property
    def uses_tool(self) -> bool:
        return self.decision.uses_tool

    def describe(self) -> str:
        arguments = ", ".join(f"{k}={v}" for k, v in self.decision.arguments.items())
        target = self.decision.target or "-"
        return f"{self.decision.intent.value} -> {target}" + (f" ({arguments})" if arguments else "")


@dataclass
class Plan:
    """
    How AURORA will handle a message.

    A single-step plan is handled exactly as before (route → tool or Brain).
    A multi-step plan runs its tool steps in order, then gives any
    conversational parts to the Brain together with the tool results.
    """

    message: str
    steps: list[Step] = field(default_factory=list)
    truncated: bool = False
    reason: str = ""
    # Things to tell the user about how the request was handled.
    notes: list[str] = field(default_factory=list)

    @property
    def is_multi_step(self) -> bool:
        return len(self.steps) > 1 or bool(self.notes)

    @property
    def tool_steps(self) -> list[Step]:
        return [step for step in self.steps if step.uses_tool]

    @property
    def conversation_steps(self) -> list[Step]:
        return [step for step in self.steps if not step.uses_tool]

    def to_dict(self) -> dict:
        # Intents and targets only; step text is the user's words.
        return {
            "steps": [
                {"intent": s.decision.intent.value, "target": s.decision.target}
                for s in self.steps
            ],
            "truncated": self.truncated,
        }

"""
The structure of a workflow and the record of running one.

A Workflow is an ordered list of steps. A step is either
    - a tool step: a RouteDecision for the ToolExecutor, or
    - an operation step: an Operation applied to a value produced by an
      earlier step (`depends_on`).

Running a workflow produces a WorkflowTrace: for every step, its status
(done / failed / skipped), how many attempts it took, the values it
produced and the message it contributed. Traces make workflows testable
and explainable, and contain no user text in their to_dict() form, so
they can be logged or shown as events.
"""

from dataclasses import dataclass, field

from aurora.routing.decision import RouteDecision
from aurora.workflows.operations import Operation
from aurora.workflows.values import Value

DONE, FAILED, SKIPPED = "done", "failed", "skipped"


@dataclass(frozen=True)
class WorkflowStep:
    number: int                          # 1-based
    text: str                            # the user's words for this step
    decision: RouteDecision | None = None
    operation: Operation | None = None
    depends_on: int | None = None        # number of the step whose value this one uses

    @property
    def kind(self) -> str:
        return "operation" if self.operation is not None else "tool"

    def describe(self) -> str:
        if self.operation is not None:
            label = f" the {self.operation.label}" if self.operation.label else ""
            return f"{self.operation.describe()} (uses{label} from step {self.depends_on})"
        args = ", ".join(f"{k}={v}" for k, v in self.decision.arguments.items())
        return f"{self.decision.target}" + (f" ({args})" if args else "")


@dataclass
class Workflow:
    message: str
    steps: list[WorkflowStep]
    reason: str = ""


@dataclass
class StepRecord:
    step: WorkflowStep
    status: str = SKIPPED
    attempts: int = 0
    message: str = ""
    values: list[Value] = field(default_factory=list)
    problem: str = ""                    # why it failed or was skipped (for the user)
    sources: list = field(default_factory=list)   # where a tool step's answer came from


@dataclass
class WorkflowTrace:
    workflow: Workflow
    records: list[StepRecord]
    message: str = ""
    validation_problems: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return all(r.status == DONE for r in self.records)

    def record(self, number: int) -> StepRecord:
        return self.records[number - 1]

    def to_dict(self) -> dict:
        """Metadata only: kinds, targets, statuses, attempts. No user text."""
        return {
            "steps": [
                {
                    "kind": r.step.kind,
                    "target": r.step.decision.target if r.step.decision else r.step.operation.kind,
                    "depends_on": r.step.depends_on,
                    "status": r.status,
                    "attempts": r.attempts,
                }
                for r in self.records
            ],
            "succeeded": self.succeeded,
            "validation_problems": len(self.validation_problems),
        }

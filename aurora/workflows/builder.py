"""
Turns a message into a Workflow, when it needs one.

A workflow is built when a message
  - asks for a calculation on numbers (the Numbers tool), so its answer
    goes through response validation, or
  - asks to do something with a value an earlier part produced:
        "what's the weather in Pune and double the temperature"
        "what is 12 * 7, then add 5 and halve the result"
        "average of 4, 8 and 15 and convert it to Fahrenheit"  (refused: not a temperature)

Everything else returns None and is handled as before (single step or
the Planner's plan, with the Brain answering conversational parts).

Steps:
 1. Trailing follow-up operations are split off the end of the message
    ("... and double the temperature").
 2. The rest is planned by the existing Planner. Conversational plan
    steps that are themselves operations ("Weather in Pune. Double it.")
    become operation steps.
 3. Each operation depends on the step just before it, so operations
    chain: "double it and add 5" adds 5 to the doubled value.

No workflow is built (None) if any part would need the Brain, a
confirmation (devices, forgetting) or clarification, or if an operation
has nothing before it to work on.
"""

from aurora.planning.planner import Planner
from aurora.routing.intent import Intent
from aurora.workflows.operations import parse_operation, split_trailing_operations
from aurora.workflows.workflow import Workflow, WorkflowStep

# Tools whose results can't be the input of a workflow, or that need a
# confirmation: they are never part of one.
_EXCLUDED = {Intent.FORGET, Intent.DEVICES, Intent.RESEARCH, Intent.REMEMBER, Intent.VISION}


class WorkflowBuilder:

    def __init__(self, planner: Planner | None = None):
        self.planner = planner or Planner()

    def build(self, message: str) -> Workflow | None:
        message = (message or "").strip()
        if not message:
            return None
        rest, trailing = split_trailing_operations(message)
        if not rest.strip(" ,.;"):
            return None                       # only an operation, with nothing to apply it to

        plan = self.planner.plan(rest)
        if plan.notes or not plan.steps:
            return None

        parts = []                            # ("tool", step) or ("operation", Operation, text)
        for step in plan.steps:
            if step.uses_tool:
                if step.decision.intent in _EXCLUDED or step.decision.needs_clarification:
                    return None
                parts.append(("tool", step.decision, step.text))
                continue
            operation = parse_operation(step.text)
            if operation is None:
                return None                   # a conversational part: the Brain's job, not a workflow
            parts.append(("operation", operation, step.text))
        parts += [("operation", op, op.text) for op in trailing]

        has_operation = any(kind == "operation" for kind, *_ in parts)
        has_numbers = any(kind == "tool" and item.intent is Intent.NUMBERS for kind, item, _ in parts)
        if not (has_operation or has_numbers):
            return None
        if parts[0][0] == "operation":
            return None

        steps = []
        for number, (kind, item, text) in enumerate(parts, start=1):
            if kind == "tool":
                steps.append(WorkflowStep(number, text, decision=item))
            else:
                steps.append(WorkflowStep(number, text, operation=item, depends_on=number - 1))
        if len(steps) > self.planner.max_steps + len(trailing):
            return None
        reason = f"{len(steps)} step{'s' if len(steps) > 1 else ''}" + (
            f", {sum(1 for s in steps if s.operation)} using earlier results" if has_operation else "")
        return Workflow(message, steps, reason)

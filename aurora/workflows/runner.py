"""
Runs a Workflow step by step, with validation and error recovery.

For each step:
  tool step       executed by the ToolExecutor (so permission checks and
                  failure handling are the same as everywhere else).
                  If it fails with a *retryable* problem (timeout, server
                  error) it is tried once more after a short pause.
                  Its values are extracted and checked for plausibility;
                  an implausible value makes the step fail.
  operation step  applied to the value of the step it depends on.

Error recovery:
  - A step whose input step failed or was skipped is skipped, and the
    reply says why ("I didn't double the temperature, because the
    weather lookup failed."). Steps that don't depend on it still run.
  - Nothing is ever guessed: a missing value is reported, not invented.
  - A step that asks for confirmation is refused (workflows never
    include such tools; this is a second line of defence).

Response validation: text AURORA composes (operation results, Numbers
explanations) must contain only numbers the workflow can account for;
see validation.py.
"""

import logging
import time

from aurora.tools.executor import ToolExecutor
from aurora.workflows import validation
from aurora.workflows.operations import OperationError, apply
from aurora.workflows.values import ImplausibleValue, Value, pick, values_from
from aurora.workflows.workflow import DONE, FAILED, SKIPPED, StepRecord, Workflow, WorkflowTrace

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0


def _what(step) -> str:
    """Short description of a step for messages: 'double the temperature'."""
    return step.operation.text if step.operation else f"the {step.decision.target} step"


class WorkflowRunner:

    def __init__(self, executor: ToolExecutor, sleep=time.sleep,
                 max_attempts: int = MAX_ATTEMPTS, retry_delay: float = RETRY_DELAY_SECONDS):
        self.executor = executor
        self.sleep = sleep
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    def run(self, workflow: Workflow) -> WorkflowTrace:
        trace = WorkflowTrace(workflow, [StepRecord(step) for step in workflow.steps])
        for record in trace.records:
            step = record.step
            if step.depends_on is not None:
                source = trace.record(step.depends_on)
                if source.status != DONE:
                    record.status = SKIPPED
                    record.problem = (f"I didn't {_what(step)}, because the step it needed "
                                      f"({_what(source.step)}) {'failed' if source.status == FAILED else 'was skipped'}.")
                    continue
                self._run_operation(record, source, trace)
            else:
                self._run_tool(record, trace)
        trace.message = self._compose(trace)
        logger.info("Workflow: %s", [(r.step.kind, r.status, r.attempts) for r in trace.records])
        return trace

    # ------------------------------------------------------------ tool steps

    def _run_tool(self, record: StepRecord, trace: WorkflowTrace) -> None:
        step = record.step
        if not self.executor.can_handle(step.decision):
            record.status, record.problem = FAILED, f"I don't have the {step.decision.target} tool available."
            return
        while True:
            record.attempts += 1
            result = self.executor.execute(step.decision)
            if result.needs_confirmation:
                result.confirm = None
                record.status = FAILED
                record.problem = "That needs your confirmation, so please ask for it on its own."
                return
            if result.success or not result.retryable or record.attempts >= self.max_attempts:
                break
            logger.info("Retrying %s after a temporary failure", step.decision.target)
            self.sleep(self.retry_delay * record.attempts)

        record.message = result.message
        if not result.success:
            record.status = FAILED
            if result.retryable and record.attempts > 1:
                record.message += f" (I tried {record.attempts} times.)"
            return
        try:
            record.values = values_from(step.decision.target, result.data)
        except ImplausibleValue as error:
            record.status = FAILED
            record.message = ""
            record.problem = (f"The {step.decision.target} tool returned a value I can't trust "
                              f"({error}), so I won't use it.")
            return
        if step.decision.target == "Numbers" and isinstance(result.data, dict):
            unexplained = validation.unexplained_numbers(result.message, validation.numbers_tool_allowed(result.data))
            if unexplained:
                self._invalid(trace, record, unexplained)
                record.message = "\n".join(f"Result: {v.shown()}" for v in record.values[:1]) or "I calculated it, but couldn't present the working reliably."
        record.status = DONE

    # ------------------------------------------------------- operation steps

    def _run_operation(self, record: StepRecord, source: StepRecord, trace: WorkflowTrace) -> None:
        operation = record.step.operation
        record.attempts = 1
        value: Value | None = pick(source.values, operation.label)
        if value is None:
            record.status = FAILED
            wanted = f"a {operation.label}" if operation.label else "a single number"
            record.problem = f"I can't {operation.text}: the step before it didn't give me {wanted} to use."
            return
        try:
            new, line = apply(operation, value)
        except (OperationError, ImplausibleValue) as error:
            record.status = FAILED
            record.problem = f"I can't {operation.text}: {error}."
            return
        allowed = [value.number, new.number] + ([operation.operand] if operation.operand is not None else [])
        unexplained = validation.unexplained_numbers(line, allowed)
        if unexplained:
            self._invalid(trace, record, unexplained)
            line = f"Result: {new.shown()}."
        record.values = [new]
        record.message = line
        record.status = DONE

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _invalid(trace: WorkflowTrace, record: StepRecord, numbers: set[str]) -> None:
        problem = f"step {record.step.number}: unexplained numbers {sorted(numbers)}"
        trace.validation_problems.append(problem)
        logger.warning("Workflow response validation failed at step %d", record.step.number)

    @staticmethod
    def _compose(trace: WorkflowTrace) -> str:
        parts = []
        for record in trace.records:
            text = record.message if record.status == DONE else (record.message or record.problem)
            if record.status == FAILED and record.message and record.problem:
                text = f"{record.message}\n{record.problem}"
            if text:
                parts.append(text)
        return "\n\n".join(parts)

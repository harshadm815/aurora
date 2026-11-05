"""
Rule-based planner: breaks a message into steps.

    "What's the weather in Pune and Delhi, then calculate 12 * 7"
        1. weather  Pune
        2. weather  Delhi
        3. calculator  12 * 7

    "What's the weather in Pune tomorrow and what should I wear?"
        1. weather  Pune, tomorrow
        2. conversation  (the Brain answers using the weather result)

How it splits:

1. Strong separators always split: "then", "after that", "also",
   ";", and sentence ends ("?", "!", ".").
2. "and" / commas split only if at least one part is a tool request and
   every other part is a real clause (3+ words). So "I like tea and
   coffee" stays one statement.
3. "remember that …" / "forget …" own everything after them:
   "remember that I like tea and coffee" is one note.
4. A weather request for several places becomes one step per place.
5. A weather step with no place uses the place from the step before it.
6. Identical steps are merged.

A plan is only multi-step if it has at least two steps and at least one
of them uses a tool. Messages that need confirmation (forget) are never
split, so a deletion can't be buried inside a longer request.
"""

import re

from aurora.config import settings
from aurora.planning.plan import Plan, Step
from aurora.routing.decision import BRAIN, RouteDecision
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter

_STRONG = re.compile(
    r"(?<=[?!.])\s+(?=\S)"
    r"|\s*[;]\s*"
    r"|,?\s+(?:and\s+then|then|after\s+that|and\s+also|also)\s+",
    re.IGNORECASE,
)

# Keeps the separators so parts can be glued back together.
_WEAK = re.compile(r"(\s*,\s*(?:and\s+)?|\s+and\s+)", re.IGNORECASE)

_LOCATION_LIST = re.compile(r"\s*(?:,\s*(?:and\s+)?|\s+and\s+)\s*", re.IGNORECASE)

_COMMANDS = {Intent.REMEMBER, Intent.FORGET}

# Actions that need the user's confirmation are only accepted on their own.
_SEPARATE_ONLY = {
    Intent.FORGET: "To delete memories, ask me in a separate message (for example: \"forget my exam\").",
    Intent.DEVICES: "To control a device, ask me in a separate message so you can confirm it "
                    "(for example: \"turn on the desk light\").",
    Intent.RESEARCH: "For a research session, ask me in a separate message "
                     "(for example: \"continue my research on dark matter\").",
}

# "And what's 3+3?" -> "what's 3+3?"
_LEADING_JOINER = re.compile(r"^(?:and|also|then|plus|so)\s+", re.IGNORECASE)

MIN_CLAUSE_WORDS = 3


def _conversation(decision: RouteDecision) -> RouteDecision:
    return RouteDecision(Intent.CONVERSATION, 0.5, BRAIN, {}, "Part of a longer request.")


class Planner:

    def __init__(self, router: IntentRouter | None = None, max_steps: int | None = None):
        self.router = router or IntentRouter()
        self.max_steps = settings.PLAN_MAX_STEPS if max_steps is None else max_steps

    def plan(self, message: str) -> Plan:
        message = (message or "").strip()
        whole = self.router.route(message)

        single = Plan(message, [Step(message, whole)], reason="Handled as one request.")

        if not message or whole.intent in _COMMANDS or whole.intent in _SEPARATE_ONLY:
            return single

        texts: list[str] = []
        for segment in _STRONG.split(message):
            segment = _LEADING_JOINER.sub("", segment.strip(" ,"))
            if segment:
                texts.extend(self._split_weak(segment))

        steps: list[Step] = []
        for text in texts:
            decision = self.router.route(text)
            if decision.needs_clarification:
                decision = _conversation(decision)
            steps.extend(self._expand(text, decision))

        steps = self._carry_location(steps)
        steps = self._merge_duplicates(steps)

        notes = []
        for intent, note in _SEPARATE_ONLY.items():
            # Only matters when there is something else in the message too.
            if len(steps) > 1 and any(step.decision.intent is intent for step in steps):
                # Never bury a deletion or a device command inside a longer
                # request: do the rest and ask for it on its own.
                steps = [step for step in steps if step.decision.intent is not intent]
                notes.append(note)
        if notes and not any(step.uses_tool for step in steps):
            return Plan(message, [], reason="Only actions needing confirmation were combined.", notes=notes)

        if not notes and (len(steps) < 2 or not any(step.uses_tool for step in steps)):
            return single

        plan = Plan(message, steps, reason=f"Split into {len(steps)} steps.", notes=notes)
        if len(steps) > self.max_steps:
            plan.steps = steps[: self.max_steps]
            plan.truncated = True
        return plan

    # --- Splitting ----------------------------------------------------------

    def _split_weak(self, segment: str) -> list[str]:
        if self.router.route(segment).intent in _COMMANDS:
            return [segment]

        pieces = _WEAK.split(segment)
        if len(pieces) < 3:
            return [segment]

        # pieces = [part, separator, part, separator, part, ...]
        parts: list[str] = []
        decisions: list[RouteDecision] = []
        index = 0
        while index < len(pieces):
            part = pieces[index].strip()
            decision = self.router.route(part) if part else None

            if decision is not None and decision.intent in _COMMANDS:
                # A command owns the rest of the segment.
                parts.append("".join(pieces[index:]).strip())
                decisions.append(self.router.route(parts[-1]))
                break

            if part:
                parts.append(part)
                decisions.append(decision)
            index += 2

        if len(parts) < 2 or not any(d.uses_tool for d in decisions):
            return [segment]

        if not all(d.uses_tool or len(p.split()) >= MIN_CLAUSE_WORDS for p, d in zip(parts, decisions)):
            return [segment]

        return parts

    @staticmethod
    def _expand(text: str, decision: RouteDecision) -> list[Step]:
        """One weather step per place: 'weather in Pune and Delhi'."""
        location = decision.arguments.get("location")
        if decision.intent is not Intent.WEATHER or not location:
            return [Step(text, decision)]

        places = [p.strip() for p in _LOCATION_LIST.split(location) if p.strip()]
        if len(places) < 2:
            return [Step(text, decision)]

        return [
            Step(text, RouteDecision(
                decision.intent, decision.confidence, decision.target,
                {**decision.arguments, "location": place}, decision.reason,
            ))
            for place in places
        ]

    # --- Context between steps ----------------------------------------------

    @staticmethod
    def _carry_location(steps: list[Step]) -> list[Step]:
        """
        A weather step that doesn't name a place or day uses the ones
        from the weather step before it:
        'Weather in Pune tomorrow. Should I take an umbrella?' -> both Pune, tomorrow.
        """
        result = []
        last: dict = {}
        for step in steps:
            decision = step.decision
            if decision.intent is Intent.WEATHER:
                inherited = {
                    key: last[key] for key in ("location", "when")
                    if key in last and key not in decision.arguments
                }
                if inherited:
                    decision = RouteDecision(
                        decision.intent, decision.confidence, decision.target,
                        {**decision.arguments, **inherited},
                        decision.reason + " (details taken from the previous step)",
                    )
                last = dict(decision.arguments)
            result.append(Step(step.text, decision))
        return result

    @staticmethod
    def _merge_duplicates(steps: list[Step]) -> list[Step]:
        result = []
        seen = set()
        for step in steps:
            if step.uses_tool:
                key = (step.decision.intent, tuple(sorted(step.decision.arguments.items())))
                if key in seen:
                    continue
                seen.add(key)
            result.append(step)
        return result

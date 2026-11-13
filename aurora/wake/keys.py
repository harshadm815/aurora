"""
When does a key press mean "AURORA, listen"?

A pure state machine fed with keyboard events and clock ticks. It never
touches Windows, so every rule below is tested directly.

Two activation keys:

**Right Ctrl** (the default). Held on its own for WAKE_HOLD_SECONDS, it
starts listening; released, it stops. Its key presses always reach the
other programs (Right Ctrl alone does nothing anywhere), so nothing you
type is affected. Pressing any other key while it's down means you're
using a shortcut (Ctrl+C…): that cancels.

**Space** (optional). Space is typed all the time, so its press is held
back until it's clear what you meant:
  - released quickly            → a normal space is typed (on release)
  - another key pressed first   → the space and that key are typed, in order
  - held for WAKE_HOLD_SECONDS  → listening starts; no space is typed
  - with Ctrl, Alt, Shift or Win held, Space is never intercepted
The cost: a space appears on release rather than on press, and holding
Space no longer repeats spaces.

Outputs: ``Decision(swallow, inject)`` for each event (whether to stop the
event reaching other programs, and keys to type instead), and actions on
``self.actions``: "start" (open the microphone), "stop" (key released
while listening) and "cancel" (another key pressed while listening).
"""

from dataclasses import dataclass, field

VK_SPACE = 0x20
VK_RCONTROL = 0xA3
KEYS = {"right_ctrl": VK_RCONTROL, "space": VK_SPACE}
LABELS = {"right_ctrl": "Right Ctrl", "space": "Space"}

IDLE, PENDING, ACTIVE, SUPPRESSED, TYPED = "idle", "pending", "active", "suppressed", "typed"
# TYPED: Space was let through as a normal space (another key followed it); it stays an
# ordinary key, repeats included, until it's released.


@dataclass
class Decision:
    swallow: bool = False
    inject: list[tuple[int, bool]] = field(default_factory=list)    # (virtual key, down?)


PASS = Decision()


class HoldKey:

    def __init__(self, key: str = "right_ctrl", hold_seconds: float = 0.45):
        if key not in KEYS:
            raise ValueError(f"Unknown activation key '{key}'. Choose from: {', '.join(KEYS)}.")
        self.key = key
        self.vk = KEYS[key]
        self.intercepts = key == "space"          # Space presses are held back; Right Ctrl's never are
        self.hold_seconds = hold_seconds
        self.state = IDLE
        self.pressed_at = 0.0
        self.actions: list[str] = []

    # --------------------------------------------------------------- events

    def key_down(self, vk: int, now: float, modifiers: bool = False, injected: bool = False) -> Decision:
        if injected:
            return PASS                            # our own typing (or another program's): never ours to judge
        if vk == self.vk:
            return self._own_down(now, modifiers)
        return self._other_down(vk)

    def key_up(self, vk: int, now: float, injected: bool = False) -> Decision:
        if injected or vk != self.vk:
            return PASS
        state, self.state = self.state, IDLE
        if state == PENDING:
            if self.intercepts:
                return Decision(True, [(self.vk, True), (self.vk, False)])     # it was a normal space
            return PASS
        if state == ACTIVE:
            self.actions.append("stop")
            return Decision(self.intercepts)
        if state == SUPPRESSED and self.intercepts:
            return Decision(True)                  # its press was never typed either
        return PASS

    def tick(self, now: float) -> None:
        """Called regularly: a key held long enough starts listening."""
        if self.state == PENDING and now - self.pressed_at >= self.hold_seconds:
            self.state = ACTIVE
            self.actions.append("start")

    def take_actions(self) -> list[str]:
        actions, self.actions = self.actions, []
        return actions

    # -------------------------------------------------------------- details

    def _own_down(self, now: float, modifiers: bool) -> Decision:
        if self.state == TYPED:
            return PASS                            # an ordinary space being held: let it repeat
        if self.state == IDLE:
            if modifiers:
                return PASS                        # Ctrl+Space, Win+Space…: someone else's shortcut
            self.state, self.pressed_at = PENDING, now
            return Decision(self.intercepts)
        # Key repeat while held: never typed while it might be (or is) an activation.
        return Decision(self.intercepts and self.state in (PENDING, ACTIVE, SUPPRESSED))

    def _other_down(self, vk: int) -> Decision:
        if self.state == PENDING:
            if self.intercepts:
                # Typing fast ("a b" with keys overlapping): type the space, then this key, in order.
                self.state = TYPED
                return Decision(True, [(self.vk, True), (vk, True)])
            self.state = SUPPRESSED                # Right Ctrl + another key: a shortcut
            return PASS
        if self.state == ACTIVE:
            self.state = SUPPRESSED
            self.actions.append("cancel")
            return PASS
        return PASS

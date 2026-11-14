"""
What the Mobile Companion's server may ask of AURORA (aurora/mobile/server.py).

The server runs on its own threads; everything that touches the engine
goes through the Backend exactly as Chat does (same conversation, same
permissions, same confirmations), and the exchange also appears in Chat
on the PC ("You (phone: Pixel)").
"""

import threading
from datetime import datetime

from aurora.config import settings

CHAT_TIMEOUT = 300           # seconds: TinyLlama on this laptop can take a while


class MobileBridge:

    def __init__(self, backend, timeout: float = CHAT_TIMEOUT):
        self.backend = backend
        self.timeout = timeout

    def chat(self, text: str, device: str) -> dict:
        done = threading.Event()
        outcome = {}

        def replied(result):
            reply, needs_confirmation, *rest = result
            outcome.update(reply=reply, needs_confirmation=needs_confirmation, sources=rest[0] if rest else "")
            self.backend.remote_exchange.emit(device, text, reply)
            done.set()

        def failed(error):
            outcome["error"] = str(error)
            done.set()

        self.backend.on_gui(lambda _: self.backend.send_message(text, replied, failed, source="phone"))
        if not done.wait(self.timeout):
            raise TimeoutError("AURORA took too long to answer.")
        if "error" in outcome:
            return {"reply": f"Something went wrong: {outcome['error']}", "needs_confirmation": False,
                    "sources": ""}
        return outcome

    def pending(self) -> bool:
        engine = self.backend.engine
        return engine is not None and getattr(engine, "pending", None) is not None

    def status(self) -> dict:
        sample = self.backend.last_sample or {}
        memory = sample.get("memory")
        engine = self.backend.engine
        return {
            "ai": self.backend.ai_state,
            "state": self.backend.state,
            "model": engine.status().get("model", settings.DEFAULT_MODEL) if engine else settings.DEFAULT_MODEL,
            "cpu": sample.get("cpu"),
            "ram": getattr(memory, "percent", None),
            "version": settings.VERSION,
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    def history(self, limit: int = 40) -> list[dict]:
        engine = self.backend.engine
        if engine is None:
            return []
        messages = [m for m in engine.conversation.messages if m.get("role") in ("user", "assistant")]
        return [{"role": m["role"], "text": m["content"]} for m in messages[-limit:]]

    def briefing(self) -> str:
        tool = self.backend.tool("Briefing")
        if tool is None:
            return "AURORA is still starting."
        from aurora.tool_plugins.briefing import text
        return text(tool.gather())

    def reminders(self) -> list[dict]:
        tool = self.backend.tool("Reminders")
        if tool is None:
            return []
        return [{"text": r.text, "due": r.due.isoformat(timespec="minutes")}
                for r in sorted(tool.scheduler.pending(), key=lambda r: r.due)]

    def earlier_reminders(self) -> list[dict]:
        """Today's reminders that already went off, so the phone's Today still shows them."""
        tool = self.backend.tool("Reminders")
        if tool is None:
            return []
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return [{"text": r.text, "due": r.due.isoformat(timespec="minutes")}
                for r in tool.scheduler.shown_since(midnight)]

    def memories(self) -> list[dict]:
        engine = self.backend.engine
        if engine is None:
            return []
        return [{"value": f.value, "category": f.category} for f in engine.memory.memories()[:300]]

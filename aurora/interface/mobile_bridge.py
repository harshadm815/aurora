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


def _epoch(moment) -> float | None:
    return moment.timestamp() if moment is not None else None


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
        services = []
        if engine is not None:
            try:
                services = [{"name": name, "running": str(value).lower() == "running"}
                            for name, value in (engine.status().get("services") or {}).items()]
            except Exception:
                services = []
        return {
            "services": services,
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
        return [{"id": r.id, "text": r.text, "due": r.due.isoformat(timespec="minutes")}
                for r in sorted(tool.scheduler.pending(), key=lambda r: r.due)]

    def earlier_reminders(self) -> list[dict]:
        """Today's reminders that already went off, so the phone's Today still shows them."""
        tool = self.backend.tool("Reminders")
        if tool is None:
            return []
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return [{"text": r.text, "due": r.due.isoformat(timespec="minutes")}
                for r in tool.scheduler.shown_since(midnight)]

    def file_received(self, path, device: str) -> None:
        """A file the phone shared was saved; tell the PC (a tray notice)."""
        self.backend.phone_file.emit(str(path), device)

    def memories(self) -> list[dict]:
        engine = self.backend.engine
        if engine is None:
            return []
        return [{"id": f.id, "value": f.value, "category": f.category,
                 "updated": _epoch(f.updated or f.created)} for f in engine.memory.memories()[:300]]

    # --- Changes from the phone (the server checks the "Memories" permission first) ---------------

    def _memory(self):
        engine = self.backend.engine
        if engine is None:
            raise RuntimeError("AURORA is still starting.")
        return engine.memory

    def add_memory(self, value: str) -> bool:
        """A note you chose to keep (the Memory tab's Add, or picked after a call)."""
        from aurora.knowledge.fact import EXPLICIT, Fact
        self._memory().learn(Fact("Note", "User", "Note", value, 3), source=EXPLICIT, source_text=value)
        return True

    def _changed_on_pc_since(self, memory_id: int, changed_at: float | None) -> bool:
        """True if the PC's copy changed after the phone's offline change (then the PC's newer one stays)."""
        if changed_at is None:
            return False
        for fact in self._memory().memories():
            if fact.id == memory_id:
                updated = _epoch(fact.updated or fact.created)
                return updated is not None and updated > changed_at
        return False

    def edit_memory(self, memory_id: int, value: str, changed_at: float | None = None) -> bool | str:
        if self._changed_on_pc_since(memory_id, changed_at):
            return "kept"
        return self._memory().edit(memory_id, value)

    def forget_memory(self, memory_id: int, changed_at: float | None = None) -> bool | str:
        if self._changed_on_pc_since(memory_id, changed_at):
            return "kept"
        return self._memory().forget(memory_id)

    def import_exchange(self, text: str, reply: str, device: str) -> None:
        """A conversation the phone had on its own (offline model) joins the shared one, without asking AURORA
        again: AURORA on the PC then knows what was said, and it appears in Chat ("phone, offline")."""
        engine = self.backend.engine
        if engine is None:
            raise RuntimeError("AURORA is still starting.")

        def add(_):
            engine.conversation.add_user(text)
            engine.conversation.add_assistant(reply)
            self.backend.remote_exchange.emit(f"{device}, offline", text, reply)

        self.backend.on_gui(add)

    def clear_conversation(self) -> None:
        self.backend.on_gui(lambda _: self.backend.clear_conversation())

    def control(self, action: str) -> str:
        """The phone's few remote commands. Returns what happened, in words."""
        backend = self.backend
        if action == "pause":
            if backend.state != "running":
                return "AURORA isn't running."
            backend.on_gui(lambda _: backend.pause())
            return "Pausing AURORA (the model is unloaded to free memory)."
        if action == "start":
            if backend.state == "running":
                return "AURORA is already running."
            backend.on_gui(lambda _: backend.resume())
            return "Starting AURORA."
        if action == "stop_speaking":
            backend.on_gui(lambda _: backend.voice.stop())
            return "AURORA stopped speaking on the PC."
        raise ValueError(action)

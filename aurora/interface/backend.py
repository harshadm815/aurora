"""
The bridge between the Command Centre and AURORA's engine.

- One ``AuroraEngine`` for the whole application: the interface never
  creates a second engine, brain or database.
- Every engine call runs on ONE background thread, in order, so the
  window never freezes and the engine is never used by two threads at
  once. Other slow work (Ollama checks, memory queries, image analysis)
  runs on a small separate pool.
- Results are always delivered back on the GUI thread through
  ``Dispatcher``.
- The activity feed and log view only ever show metadata (event names,
  tool names, intents), never what the user typed.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from aurora.config import preferences, settings
from aurora.events import topics
from aurora.events.bus import ALL_EVENTS
from aurora.interface import performance, system_info
from aurora.knowledge.sources import caption
from aurora.sensors.reading import QUANTITIES

logger = logging.getLogger(__name__)


class Dispatcher(QObject):
    """Lives on the GUI thread; runs callbacks there, whichever thread asks."""

    deliver = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.deliver.connect(self._run)

    @Slot(object, object)
    def _run(self, callback, value):
        try:
            callback(value)
        except Exception:
            logger.exception("Interface callback failed")


class _Task(QRunnable):

    def __init__(self, function, dispatcher, on_result, on_error):
        super().__init__()
        self.function, self.dispatcher = function, dispatcher
        self.on_result, self.on_error = on_result, on_error
        self.setAutoDelete(True)

    def run(self):
        try:
            value = self.function()
        except Exception as error:
            logger.exception("Background task failed")
            if self.on_error:
                self.dispatcher.deliver.emit(self.on_error, error)
            return
        if self.on_result:
            self.dispatcher.deliver.emit(self.on_result, value)


@dataclass(frozen=True)
class Activity:
    time: datetime
    text: str
    kind: str   # info | ok | warning | error


@dataclass(frozen=True)
class LogLine:
    time: datetime
    level: str
    name: str
    message: str


class _QtLogHandler(logging.Handler):
    """Keeps recent log lines (AURORA's logs hold no user content) for the monitor."""

    def __init__(self, backend: "Backend"):
        super().__init__(level=logging.INFO)
        self.backend = backend

    def emit(self, record):
        try:
            line = LogLine(datetime.fromtimestamp(record.created), record.levelname, record.name,
                           record.getMessage())
        except Exception:
            return
        self.backend.dispatcher.deliver.emit(self.backend._add_log, line)


# How events are described in the activity feed. Only metadata is used.
def describe_event(event) -> Activity | None:
    data = event.data if isinstance(event.data, dict) else {}
    now = event.timestamp
    name = event.name
    if name == topics.KERNEL_STARTED:
        return Activity(now, "AURORA kernel started", "ok")
    if name == topics.KERNEL_STOPPED:
        return Activity(now, "AURORA kernel stopped", "info")
    if name == topics.SERVICE_FAILED:
        return Activity(now, f"Service failed: {data.get('service')}", "error")
    if name == topics.ROUTE_DECIDED:
        return Activity(now, f"Request routed to {data.get('target')} ({data.get('intent')})", "info")
    if name == topics.TOOL_EXECUTED:
        state = "succeeded" if data.get("success") else "reported a problem"
        return Activity(now, f"{data.get('tool')} tool {state}", "ok" if data.get("success") else "warning")
    if name == topics.TOOL_FAILED:
        return Activity(now, f"{data.get('tool')} tool failed", "error")
    if name == topics.PERMISSION_DENIED:
        return Activity(now, f"Permission denied: {data.get('component')} needs {data.get('permission')}", "warning")
    if name == topics.MEMORY_LEARNED:
        return Activity(now, f"Memory {data.get('action')}: {data.get('attribute')}", "ok")
    if name == topics.CONFIRMATION_REQUESTED:
        return Activity(now, f"Waiting for your confirmation ({data.get('tool')})", "warning")
    if name == topics.CONFIRMATION_ANSWERED:
        answer = "approved" if data.get("approved") else "declined"
        return Activity(now, f"You {answer} a {data.get('tool')} action", "info")
    if name == topics.PLAN_CREATED:
        return Activity(now, f"Multi-step plan with {len(data.get('steps', []))} steps", "info")
    if name == topics.CONVERSATION_CLEARED:
        return Activity(now, "Conversation cleared", "info")
    if name == topics.MOTION_DETECTED:
        return Activity(now, "Motion detected", "warning")
    if name == topics.SENSOR_ALERT:
        label = QUANTITIES.get(data.get("quantity"), None)
        what = label.label if label else "A sensor"
        return Activity(now, f"Sensor alert: {what} is {data.get('level', 'poor')}"
                             + (" (simulated)" if data.get("simulated") else ""), "warning")
    if name == topics.REMINDER_DUE:
        return Activity(now, "Reminder shown", "info")
    if name == topics.REMINDERS_MISSED:
        count = len(data.get("reminders", []))
        return Activity(now, f"{count} reminder{'s' if count != 1 else ''} fell due while AURORA was closed", "warning")
    if name == topics.TASK_RAN:
        outcome = {True: ("done", "ok"), False: ("failed", "error"), None: ("skipped", "warning")}[data.get("success")]
        return Activity(now, f"Scheduled task {data.get('id')} {outcome[0]}", outcome[1])
    if name == topics.SENSOR_MONITORING:
        return Activity(now, "Background sensor watching " + ("on" if data.get("on") else "off"), "info")
    return None


class Backend(QObject):
    """Everything the views need from AURORA, without blocking the GUI."""

    engine_ready = Signal()
    engine_failed = Signal(str)
    activity_added = Signal(object)        # Activity
    log_added = Signal(object)             # LogLine
    ollama_checked = Signal(object)        # dict: {"ok": bool, "detail": str, "models": [...]}
    preferences_changed = Signal(dict)
    system_sampled = Signal(dict)          # Real CPU / RAM readings (None where unavailable)
    state_changed = Signal(str)            # starting | running | paused | failed | stopped
    sensor_readings = Signal(dict)         # sensor.readings from background watching
    sensor_alert = Signal(dict)            # sensor.alert: {"quantity", "level", "message", "simulated", ...}
    sensor_monitoring = Signal(bool)       # background watching switched on / off
    reminder_due = Signal(dict)            # {"id", "text"}
    reminders_missed = Signal(list)        # [{"text", "due"}] found at start
    task_ran = Signal(dict)                # a scheduled task ran or was skipped
    # What AURORA is doing right now, for the AI core and the top bar:
    # idle | thinking | responding | listening | speaking | processing | error | offline
    ai_state_changed = Signal(str)
    performance_changed = Signal(bool, str)   # graphics reduced?, one line for the Dashboard

    def __init__(self, engine_factory: Callable | None = None, parent=None):
        super().__init__(parent)
        self.dispatcher = Dispatcher()
        self._engine_factory = engine_factory
        self.engine = None
        self.state = "starting"
        self._shut_down = False
        self.preferences = preferences.load()

        self._engine_pool = QThreadPool(self)
        self._engine_pool.setMaxThreadCount(1)          # The engine is used by one thread only.
        self._io_pool = QThreadPool(self)
        self._io_pool.setMaxThreadCount(2)

        self.activities: deque[Activity] = deque(maxlen=200)
        self.logs: deque[LogLine] = deque(maxlen=500)
        self.tool_errors: dict[str, str] = {}
        self.ollama = {"ok": None, "detail": "Not checked yet", "models": []}

        self._log_handler = _QtLogHandler(self)
        logging.getLogger("aurora").addHandler(self._log_handler)

        self._ollama_timer = QTimer(self)
        self._ollama_timer.timeout.connect(self.check_ollama)

        # One shared sampler: CPU use is measured between two samples, so
        # every view must see the same series.
        # AURORA's own models (aurora_core): runtime and training runs.
        from aurora.interface.core_models import CoreModels
        self.core = CoreModels(self)
        # Voice: microphone (push to talk), spoken replies, stop.
        from aurora.interface.voice_control import VoiceController
        self.voice = VoiceController(self)
        self.ai_state = "processing"            # starting up
        self._activity: str | None = None       # thinking / responding / listening / speaking
        self._error_until = 0.0                 # a failed reply shows "error" briefly
        self.voice.state_changed.connect(self._voice_state)
        self._vision = None          # VisionAnalyzer for chosen pictures, when there's no Camera tool
        self.missed_reminders: list[dict] = []     # reminders due while AURORA was closed (shown once)

        self._cpu = system_info.CpuMeter()
        self._cpu.percent()
        self.last_sample: dict = {}
        self._sample_timer = QTimer(self)
        self._sample_timer.timeout.connect(self._sample)
        self._sample_timer.start(2000)
        self.pressure = performance.Pressure()
        self.performance_reduced = False
        self.performance_text = ""
        self._update_performance()

    # --- Threads ------------------------------------------------------------

    def run_engine(self, function, on_result=None, on_error=None) -> None:
        """Runs ``function`` on the engine thread (in order with other engine calls)."""
        self._engine_pool.start(_Task(function, self.dispatcher, on_result, on_error))

    def run_io(self, function, on_result=None, on_error=None) -> None:
        """Runs ``function`` on the background pool (doesn't touch engine state)."""
        self._io_pool.start(_Task(function, self.dispatcher, on_result, on_error))

    def on_gui(self, callback, value=None) -> None:
        """Calls ``callback(value)`` on the GUI thread, from any thread."""
        self.dispatcher.deliver.emit(callback, value)

    def _sample(self) -> None:
        memory = system_info.system_memory()
        self.last_sample = {
            "cpu": self._cpu.percent(),
            "memory": memory,
            "process": system_info.process_memory_bytes(),
            "uptime": system_info.uptime_seconds(),
            "cores": system_info.cpu_count(),
        }
        self.system_sampled.emit(self.last_sample)
        self.pressure.update(self.last_sample["cpu"], memory.percent if memory is not None else None)
        self._update_performance()

    def _update_performance(self) -> None:
        mode = self.preferences.get("performance_mode", "auto")
        reduced = performance.reduced(mode, self.pressure)
        text = performance.describe(mode, self.pressure)
        if (reduced, text) != (self.performance_reduced, self.performance_text):
            self.performance_reduced, self.performance_text = reduced, text
            if reduced:
                logger.info("Performance mode on (%s)", mode)
            self.performance_changed.emit(reduced, text)

    # --- Start / stop -------------------------------------------------------

    def start(self) -> None:
        preferences.apply(self.preferences)

        def create():
            if self._engine_factory is not None:
                engine = self._engine_factory()
            else:
                from aurora.kernel.engine import AuroraEngine
                engine = AuroraEngine()
            engine.events.subscribe(ALL_EVENTS, self._on_event)
            engine.start()
            return engine

        def ready(engine):
            self.engine = engine
            self._set_state("running")
            self.engine_ready.emit()
            self.check_ollama()
            self._ollama_timer.start(30_000)

        def failed(error):
            self._set_state("failed")
            self.engine_failed.emit(f"AURORA couldn't start: {error}")

        self._set_state("starting")
        self.run_engine(create, ready, failed)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            logger.info("AURORA %s", state)
            self.state_changed.emit(state)
        self._refresh_ai_state()

    # --- What AURORA is doing (the AI core) ---------------------------------

    def _refresh_ai_state(self) -> None:
        if self.state == "failed":
            state = "error"
        elif self.state in ("paused", "stopped"):
            state = "offline"
        elif self.state == "starting":
            state = "processing"
        elif self._activity is not None:
            state = self._activity
        elif time.monotonic() < self._error_until:
            state = "error"
        else:
            state = "idle"
        if state != self.ai_state:
            self.ai_state = state
            self.ai_state_changed.emit(state)

    def _set_activity(self, activity: str | None) -> None:
        self._activity = activity
        self._refresh_ai_state()

    def _voice_state(self, state: str) -> None:
        if state in ("listening", "speaking"):
            self._set_activity(state)
        elif self._activity in ("listening", "speaking"):
            self._set_activity(None)

    def _reply_failed(self) -> None:
        self._error_until = time.monotonic() + 5
        self._set_activity(None)
        QTimer.singleShot(5100, self._refresh_ai_state)

    @property
    def paused(self) -> bool:
        return self.state == "paused"

    def pause(self) -> None:
        """
        Stops AURORA's services and plugins and asks Ollama to unload the
        model, freeing its memory. The window stays open; ``resume()`` starts
        everything again.
        """
        if self.engine is None or self.state != "running":
            return
        engine = self.engine
        self._ollama_timer.stop()

        def work():
            engine.shutdown()
            unload = getattr(engine.brain.provider, "unload", None)
            if unload is not None:
                try:
                    unload()
                except Exception:
                    logger.warning("Couldn't unload the model (Ollama not reachable?)")

        self._set_state("paused")
        self.run_engine(work)
        self.core.unload()            # AURORA's own model too (a training run keeps going)
        self.voice.stop()             # and stop listening or speaking

    def resume(self) -> None:
        """Starts AURORA again after ``pause()`` (or retries after a failed start)."""
        if self.state == "failed" and self.engine is None:
            self.start()
            return
        if self.engine is None or self.state != "paused":
            return
        engine = self.engine

        def ready(_):
            self._set_state("running")
            self.engine_ready.emit()
            self.check_ollama()
            self._ollama_timer.start(30_000)

        self._set_state("starting")
        self.run_engine(engine.start, ready, lambda error: self._set_state("failed"))

    def shutdown(self, timeout_ms: int = 10_000) -> None:
        """
        Stops everything AURORA runs: timers, reminders, the engine (services
        and plugins) and both worker pools, waiting for work in progress.
        Safe to call more than once.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self._ollama_timer.stop()
        self._sample_timer.stop()
        self.core.shutdown()          # stops a training run and unloads AURORA's model
        self.voice.shutdown()         # microphone closed, speech stopped, voice thread ended

        if self.engine is not None:
            engine = self.engine
            reminders = engine.tool_manager.get("Reminders")
            if reminders is not None:
                reminders.scheduler.stop()          # saved reminders stay for next time
            self.run_engine(engine.shutdown)

        engine_done = self._engine_pool.waitForDone(timeout_ms)
        io_done = self._io_pool.waitForDone(timeout_ms)
        if not (engine_done and io_done):
            logger.warning("Some background work didn't finish within %d ms", timeout_ms)
        self._set_state("stopped")
        logging.getLogger("aurora").removeHandler(self._log_handler)

    # --- Events and logs ----------------------------------------------------

    def _on_event(self, event) -> None:
        # Called on the engine thread.
        if event.name == topics.TOOL_FAILED and isinstance(event.data, dict):
            self.on_gui(lambda data: self.tool_errors.__setitem__(data.get("tool"), data.get("error", "")), event.data)
        if event.name == topics.SENSOR_READINGS and isinstance(event.data, dict):
            self.on_gui(self.sensor_readings.emit, event.data)
        if event.name == topics.SENSOR_ALERT and isinstance(event.data, dict):
            self.on_gui(self.sensor_alert.emit, event.data)
        if event.name == topics.SENSOR_MONITORING and isinstance(event.data, dict):
            self.on_gui(self.sensor_monitoring.emit, bool(event.data.get("on")))
        if event.name == topics.REMINDER_DUE and isinstance(event.data, dict):
            self.on_gui(self.reminder_due.emit, event.data)
        if event.name == topics.REMINDERS_MISSED and isinstance(event.data, dict):
            self.missed_reminders = list(event.data.get("reminders", []))
            self.on_gui(self.reminders_missed.emit, self.missed_reminders)
        if event.name == topics.TASK_RAN and isinstance(event.data, dict):
            self.on_gui(self.task_ran.emit, event.data)
        activity = describe_event(event)
        if activity is not None:
            self.on_gui(self._add_activity, activity)

    def _add_activity(self, activity: Activity) -> None:
        self.activities.appendleft(activity)
        self.activity_added.emit(activity)

    def _add_log(self, line: LogLine) -> None:
        self.logs.append(line)
        self.log_added.emit(line)

    # --- Chat ---------------------------------------------------------------

    def send_message(self, text: str, on_reply, on_error, on_token=None) -> None:
        """
        Sends ``text`` to the engine. ``on_token`` gets the model's reply as
        it is written (when streaming is on); ``on_reply`` gets
        (full reply, needs_confirmation) at the end.
        """
        if self.engine is None:
            on_error(RuntimeError("AURORA is still starting."))
            return
        if self.state != "running":
            on_error(RuntimeError("AURORA is paused. Start it again from the tray menu or the Dashboard."
                                  if self.paused else f"AURORA is {self.state}."))
            return
        engine = self.engine
        stream = self.preferences.get("chat_streaming", True) and on_token is not None

        def token(piece):
            if self._activity == "thinking":
                self._set_activity("responding")
            on_token(piece)

        def work():
            callback = (lambda piece: self.on_gui(token, piece)) if stream else None
            reply = engine.process_message(text, on_token=callback)
            return reply, engine.pending is not None, caption(getattr(engine, "last_sources", []))

        def replied(result):
            if self._activity in ("thinking", "responding"):
                self._set_activity(None)
            on_reply(result)

        def failed(error):
            self._reply_failed()
            on_error(error)

        self._set_activity("thinking")
        self.run_engine(work, replied, failed)

    # --- Sensors ------------------------------------------------------------

    def sensor_service(self):
        if self.engine is None:
            return None
        return self.engine.service_manager.services.get("Sensors")

    def set_sensor_monitoring(self, on: bool, save: bool = True) -> str | None:
        """Turns background sensor watching on or off. Returns why it couldn't
        be turned on, or None. Starting and stopping happen off the GUI thread."""
        if save:
            self.preferences["sensor_monitoring"] = on
            preferences.save(self.preferences)
            preferences.apply(self.preferences)
        service = self.sensor_service()
        if service is None:
            return "Sensors aren't available."
        if on:
            if not service.permitted():
                return "AURORA isn't allowed to read sensors (the Sensors permission is off)."
            self.run_io(service.start_monitoring)
        else:
            self.run_io(service.stop_monitoring)
        return None

    # --- Pictures -----------------------------------------------------------

    def vision_analyzer(self):
        """The Camera tool's analyser (models already loaded), or one of our own.
        Analysing a picture the user chose doesn't use the camera."""
        tool = self.tool("Camera")
        if tool is not None:
            return tool.analyzer
        if self._vision is None:
            from aurora.vision.analyzer import VisionAnalyzer

            self._vision = VisionAnalyzer()
        return self._vision

    def describe_image(self, path):
        """A grounded SceneReport for an image file (call off the GUI thread)."""
        from aurora.vision.image_input import load_image

        return self.vision_analyzer().analyse(load_image(path), read_text=True)

    def clear_conversation(self, on_done=None) -> None:
        if self.engine is not None:
            self.run_engine(self.engine.clear_conversation, on_done)

    # --- Status -------------------------------------------------------------

    def status(self) -> dict:
        """A quick, non-blocking snapshot for the views (GUI thread)."""
        if self.engine is None:
            return {"running": False}
        engine = self.engine
        try:
            status = engine.status()
        except Exception:
            logger.exception("Reading engine status failed")
            status = {"running": engine.running}
        status["model"] = getattr(engine.brain.provider, "model", "unknown")
        status["state"] = self.state
        status["auto_memory"] = engine.auto_memory_enabled
        return status

    def check_ollama(self) -> None:
        """Checks Ollama and the active model in the background."""
        if self.engine is None:
            return
        provider = self.engine.brain.provider

        def work():
            models = provider.installed_models() if hasattr(provider, "installed_models") else []
            provider.health_check()
            return models

        def ok(models):
            self.ollama = {"ok": True, "detail": "Connected", "models": models}
            self.ollama_checked.emit(self.ollama)

        def failed(error):
            self.ollama = {"ok": False, "detail": str(error) or type(error).__name__,
                           "models": self.ollama.get("models", [])}
            self.ollama_checked.emit(self.ollama)

        self.run_io(work, ok, failed)

    # --- Preferences --------------------------------------------------------

    def save_preferences(self, values: dict) -> None:
        self.preferences.update(values)
        preferences.save(self.preferences)
        preferences.apply(self.preferences)

        if self.engine is not None:
            engine = self.engine

            def live():
                # Use the new model and personality from now on.
                if hasattr(engine.brain.provider, "model"):
                    engine.brain.provider.model = settings.DEFAULT_MODEL
                from aurora.personality.personality import Personality
                engine.conversation.messages[0] = Personality().system_message()
                engine.auto_memory.context_limit = settings.MEMORY_CONTEXT_LIMIT

            self.run_engine(live, lambda _: self.check_ollama())
        self.voice.apply_settings()      # voice, speed and volume take effect at once
        if "sensor_monitoring" in values:
            self.set_sensor_monitoring(bool(values["sensor_monitoring"]), save=False)
        self.preferences_changed.emit(dict(self.preferences))
        self._update_performance()
        logger.info("Preferences saved")

    # --- Helpers for views --------------------------------------------------

    def tool(self, name: str):
        return self.engine.tool_manager.get(name) if self.engine is not None else None

    def missing_permissions(self, component: str, permissions) -> list:
        if self.engine is None:
            return list(permissions)
        return self.engine.permissions.missing(component, permissions)


def timestamp() -> str:
    return datetime.now().strftime("%H:%M")


def wait_for(condition, timeout: float = 5.0) -> bool:
    """For tests: spins the Qt event loop until ``condition()`` or timeout."""
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False

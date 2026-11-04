import pytest

from aurora.brain.brain import Brain
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService
from aurora.kernel.service_manager import ServiceManager
from aurora.events import topics
from aurora.events.bus import ALL_EVENTS
from aurora.kernel.context import KernelContext
from aurora.kernel.permissions import Permission, PermissionDeniedError, PermissionManager
from aurora.plugins.plugin import BasePlugin
from aurora.plugins.plugin_manager import PluginManager
from aurora.plugins.weather_plugin import WeatherPlugin


class GoodService(BaseService):
    def start(self):
        self.state = ServiceState.RUNNING

    def stop(self):
        self.state = ServiceState.STOPPED


class BrokenService(BaseService):
    def start(self):
        raise RuntimeError("boom")

    def stop(self):
        self.state = ServiceState.STOPPED


class RecordingPlugin(BasePlugin):
    def __init__(self, name, fail=False):
        super().__init__(name)
        self.fail = fail
        self.initialized = False

    def initialize(self):
        if self.fail:
            raise RuntimeError("boom")
        self.initialized = True

    def shutdown(self):
        self.initialized = False


# --- ServiceManager -------------------------------------------------------

def test_services_start_and_stop():
    manager = ServiceManager()
    manager.register(GoodService("A"))

    manager.start_all()
    assert manager.status() == {"A": "Running"}

    manager.stop_all()
    assert manager.status() == {"A": "Stopped"}


def test_broken_service_does_not_block_others():
    manager = ServiceManager()
    manager.register(BrokenService("Broken"))
    manager.register(GoodService("Good"))

    manager.start_all()

    assert manager.status() == {"Broken": "Error", "Good": "Running"}


def test_duplicate_service_name_rejected():
    manager = ServiceManager()
    manager.register(GoodService("A"))

    with pytest.raises(ValueError):
        manager.register(GoodService("A"))


def test_get_unknown_service():
    with pytest.raises(KeyError):
        ServiceManager().get("Nope")


# --- PluginManager --------------------------------------------------------

class RecordingPlugin(BasePlugin):
    def __init__(self, name, fail=False, needs=()):
        super().__init__(name)
        self.fail = fail
        self.required_permissions = tuple(needs)
        self.initialized = False

    def initialize(self):
        if self.fail:
            raise RuntimeError("boom")
        self.initialized = True

    def shutdown(self):
        self.initialized = False


def test_broken_plugin_is_disabled_and_others_still_run():
    manager = PluginManager()
    broken = RecordingPlugin("Broken", fail=True)
    good = RecordingPlugin("Good")
    manager.register(broken)
    manager.register(good)

    manager.initialize_all()

    assert broken.enabled is False
    assert good.initialized is True
    assert manager.status() == {"Broken": "Disabled", "Good": "Running"}

    manager.shutdown_all()
    assert good.initialized is False


def test_plugin_without_permission_is_blocked():
    context = KernelContext()
    blocked = []
    context.events.subscribe(topics.PLUGIN_BLOCKED, blocked.append)
    manager = PluginManager(context)
    camera = RecordingPlugin("Camera", needs=[Permission.CAMERA])
    manager.register(camera)

    manager.initialize_all()

    assert camera.initialized is False
    assert camera.enabled is False
    assert blocked[0].data == {"plugin": "Camera", "missing": ["camera"]}


def test_plugin_with_permission_runs_and_gets_context():
    context = KernelContext(permissions=PermissionManager({"Camera": ["camera"]}))
    manager = PluginManager(context)
    camera = RecordingPlugin("Camera", needs=[Permission.CAMERA])
    manager.register(camera)

    manager.initialize_all()

    assert camera.initialized is True
    assert camera.context is context
    camera.require(Permission.CAMERA)  # Granted: no error.
    with pytest.raises(PermissionDeniedError):
        camera.require(Permission.FILESYSTEM_READ)


def test_plugin_can_publish_events():
    context = KernelContext()
    received = []
    context.events.subscribe("sensor.reading", received.append)
    manager = PluginManager(context)
    plugin = RecordingPlugin("Sensor")
    manager.register(plugin)
    manager.initialize_all()

    plugin.publish("sensor.reading", 21.5)

    assert received[0].source == "Sensor"
    assert received[0].data == 21.5


def test_weather_plugin_needs_network_grant():
    manager = PluginManager()
    weather = WeatherPlugin()
    manager.register(weather)

    manager.initialize_all()

    assert manager.status() == {"Weather": "Disabled"}


def test_duplicate_plugin_rejected():
    manager = PluginManager()
    manager.register(RecordingPlugin("A"))

    with pytest.raises(ValueError):
        manager.register(RecordingPlugin("A"))


# --- Engine ---------------------------------------------------------------

def make_engine(fake_provider, **kwargs):
    return AuroraEngine(Brain(fake_provider), **kwargs)


def test_engine_has_single_brain(fake_provider):
    brain = Brain(fake_provider)
    engine = AuroraEngine(brain)

    assert engine.brain is brain
    assert engine.service_manager.get("Brain").brain is brain


def test_engine_requires_start(fake_provider):
    engine = make_engine(fake_provider)

    with pytest.raises(RuntimeError):
        engine.process_message("Hello")


def test_engine_conversation_round_trip(fake_provider):
    engine = make_engine(fake_provider)
    engine.start()

    reply = engine.process_message("Hello")

    assert reply == "Test reply."
    sent = fake_provider.calls[0]
    assert sent[0]["role"] == "system"
    assert sent[-1] == {"role": "user", "content": "Hello"}

    history = engine.conversation.history()
    assert history[-1] == {"role": "assistant", "content": "Test reply."}

    engine.shutdown()
    assert engine.running is False
    assert engine.service_manager.status() == {"Brain": "Stopped"}


def test_clear_keeps_only_system_message(fake_provider):
    engine = make_engine(fake_provider)
    engine.start()
    engine.process_message("Hello")

    engine.clear_conversation()

    history = engine.conversation.history()
    assert len(history) == 1
    assert history[0]["role"] == "system"


def test_engine_publishes_lifecycle_and_message_events_in_order(fake_provider):
    engine = make_engine(fake_provider)
    seen = []
    engine.events.subscribe(ALL_EVENTS, lambda event: seen.append(event.name))

    engine.start()
    engine.process_message("Hello")
    engine.clear_conversation()
    engine.shutdown()

    assert seen == [
        topics.SERVICE_STARTED,
        topics.KERNEL_STARTED,
        topics.MESSAGE_RECEIVED,
        topics.ROUTE_DECIDED,
        topics.RESPONSE_GENERATED,
        topics.CONVERSATION_CLEARED,
        topics.KERNEL_STOPPING,
        topics.SERVICE_STOPPED,
        topics.KERNEL_STOPPED,
    ]


def test_engine_plugins_follow_kernel_lifecycle(fake_provider):
    engine = make_engine(
        fake_provider, permissions=PermissionManager({"Weather": ["network"]})
    )
    engine.register_plugin(WeatherPlugin())

    engine.start()
    assert engine.status() == {
        "running": True,
        "services": {"Brain": "Running"},
        "plugins": {"Weather": "Running"},
    }

    with pytest.raises(RuntimeError):
        engine.register_plugin(RecordingPlugin("Late"))

    engine.shutdown()
    assert engine.status()["plugins"] == {"Weather": "Stopped"}


def test_start_and_shutdown_are_idempotent(fake_provider):
    engine = make_engine(fake_provider)
    stops = []
    engine.events.subscribe(topics.KERNEL_STOPPED, stops.append)

    engine.start()
    engine.start()
    engine.shutdown()
    engine.shutdown()

    assert len(stops) == 1


def test_engine_uses_settings_grants_by_default(fake_provider, monkeypatch):
    from aurora.config import settings

    monkeypatch.setattr(settings, "PERMISSION_GRANTS", {"Weather": ["network"]})

    engine = make_engine(fake_provider)

    assert engine.permissions.is_allowed("Weather", Permission.NETWORK)


def test_brain_service_errors_when_provider_unavailable(fake_provider):
    def unavailable():
        raise ConnectionError("Ollama is not running")

    fake_provider.health_check = unavailable
    engine = make_engine(fake_provider)

    engine.start()

    assert engine.status()["services"] == {"Brain": "Error"}


def test_engine_routes_each_message_but_brain_still_answers(fake_provider):
    from aurora.routing.intent import Intent

    engine = make_engine(fake_provider)
    routes = []
    engine.events.subscribe(topics.ROUTE_DECIDED, routes.append)
    engine.start()

    reply = engine.process_message("12 * (7 + 3)")

    assert reply == "Test reply."  # Tool execution arrives in Milestone 4.
    assert engine.last_route.intent is Intent.CALCULATOR
    assert routes[0].data["arguments"] == {"expression": "12 * (7 + 3)"}

import pytest

from aurora.brain.brain import Brain
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService
from aurora.kernel.service_manager import ServiceManager
from aurora.plugins.plugin import BasePlugin
from aurora.plugins.plugin_manager import PluginManager


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

def test_broken_plugin_is_disabled_and_others_still_run():
    manager = PluginManager()
    broken = RecordingPlugin("Broken", fail=True)
    good = RecordingPlugin("Good")
    manager.register(broken)
    manager.register(good)

    manager.initialize_all()

    assert broken.enabled is False
    assert good.initialized is True

    manager.shutdown_all()
    assert good.initialized is False


# --- Engine ---------------------------------------------------------------

def test_engine_has_single_brain(fake_provider):
    brain = Brain(fake_provider)
    engine = AuroraEngine(brain)

    assert engine.brain is brain
    assert engine.service_manager.get("Brain").brain is brain


def test_engine_requires_start(fake_provider):
    engine = AuroraEngine(Brain(fake_provider))

    with pytest.raises(RuntimeError):
        engine.process_message("Hello")


def test_engine_conversation_round_trip(fake_provider):
    engine = AuroraEngine(Brain(fake_provider))
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
    engine = AuroraEngine(Brain(fake_provider))
    engine.start()
    engine.process_message("Hello")

    engine.clear_conversation()

    history = engine.conversation.history()
    assert len(history) == 1
    assert history[0]["role"] == "system"

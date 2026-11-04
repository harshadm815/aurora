import pytest

from aurora.events.bus import ALL_EVENTS, EventBus
from aurora.events.event import Event


def test_subscribers_receive_matching_events():
    bus = EventBus()
    received = []
    bus.subscribe("greeting", received.append)

    bus.publish(Event("greeting", data="hi"))
    bus.publish(Event("other", data="ignored"))

    assert [event.data for event in received] == ["hi"]


def test_event_has_timestamp_and_source():
    event = Event("x", source="Tester")

    assert event.timestamp is not None
    assert event.source == "Tester"


@pytest.mark.parametrize("name", ["", "   ", None, 42])
def test_event_name_is_validated(name):
    with pytest.raises(ValueError):
        Event(name)


def test_only_events_can_be_published():
    with pytest.raises(TypeError):
        EventBus().publish("kernel.started")


def test_subscriber_must_be_callable():
    with pytest.raises(TypeError):
        EventBus().subscribe("x", "not callable")


def test_failing_subscriber_does_not_block_others():
    bus = EventBus()
    received = []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe("x", broken)
    bus.subscribe("x", received.append)

    bus.emit("x", data=1)  # Must not raise.

    assert [event.data for event in received] == [1]


def test_wildcard_receives_everything():
    bus = EventBus()
    received = []
    bus.subscribe(ALL_EVENTS, received.append)

    bus.emit("a")
    bus.emit("b")

    assert [event.name for event in received] == ["a", "b"]


def test_unsubscribe():
    bus = EventBus()
    received = []
    bus.subscribe("x", received.append)
    bus.unsubscribe("x", received.append)

    bus.emit("x")

    assert received == []


def test_same_callback_is_not_subscribed_twice():
    bus = EventBus()
    received = []
    bus.subscribe("x", received.append)
    bus.subscribe("x", received.append)

    bus.emit("x")

    assert len(received) == 1

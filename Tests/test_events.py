from aurora.events.bus import EventBus
from aurora.events.event import Event


def test_subscribers_receive_matching_events():
    bus = EventBus()
    received = []
    bus.subscribe("greeting", received.append)

    bus.publish(Event("greeting", data="hi"))
    bus.publish(Event("other", data="ignored"))

    assert [event.data for event in received] == ["hi"]


def test_event_has_timestamp():
    assert Event("x").timestamp is not None

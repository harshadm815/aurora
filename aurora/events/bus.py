import logging
from collections import defaultdict
from collections.abc import Callable

from aurora.events.event import Event

logger = logging.getLogger(__name__)

Subscriber = Callable[[Event], None]

ALL_EVENTS = "*"


class EventBus:
    """
    Synchronous publish/subscribe.

    - Subscribers registered for ``"*"`` receive every event.
    - A subscriber that raises is logged and skipped; the remaining
      subscribers still receive the event, and the publisher is never
      interrupted.
    """

    def __init__(self):
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, event_name: str, callback: Subscriber) -> None:
        if not callable(callback):
            raise TypeError("Subscriber must be callable.")

        if callback not in self._subscribers[event_name]:
            self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Subscriber) -> None:
        if callback in self._subscribers.get(event_name, []):
            self._subscribers[event_name].remove(callback)

    def publish(self, event: Event) -> None:

        if not isinstance(event, Event):
            raise TypeError("Only Event objects can be published.")

        logger.debug("Event %s from %s", event.name, event.source)

        # Copy so a subscriber can unsubscribe during delivery.
        callbacks = (
            list(self._subscribers.get(event.name, []))
            + list(self._subscribers.get(ALL_EVENTS, []))
        )

        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                logger.exception(
                    "Subscriber %r failed while handling %s",
                    callback, event.name,
                )

    def emit(self, name: str, data=None, source: str = "unknown") -> None:
        """Shortcut for ``publish(Event(name, data, source))``."""
        self.publish(Event(name, data, source))

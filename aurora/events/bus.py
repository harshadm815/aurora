from collections import defaultdict

from aurora.events.event import Event


class EventBus:

    def __init__(self):
        self.subscribers = defaultdict(list)

    def subscribe(self, event_name, callback):
        self.subscribers[event_name].append(callback)

    def publish(self, event: Event):

        for callback in self.subscribers[event.name]:
            callback(event)
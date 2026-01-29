from threading import Event
from typing import Dict

class OrderStateStore:
    def __init__(self):
        self._states: Dict[str, dict] = {}
        self._events: Dict[str, Event] = {}

    def register(self, order_id: str):
        self._events[order_id] = Event()

    def update(self, order_id: str, data: dict):
        self._states[order_id] = data
        if order_id in self._events:
            self._events[order_id].set()

    def wait(self, order_id: str, timeout: int = 10) -> dict:
        ev = self._events.get(order_id)
        if not ev:
            raise RuntimeError("Order not registered")

        if not ev.wait(timeout):
            raise TimeoutError("Order fill timeout")

        return self._states.get(order_id)

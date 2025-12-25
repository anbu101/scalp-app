import asyncio
from collections import deque
from typing import Deque, List

class LogBus:
    """
    Central log bus:
    - Receives logs from engine
    - Keeps rolling in-memory buffer for UI
    """

    def __init__(self, max_items: int = 500):
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._buffer: Deque[str] = deque(maxlen=max_items)

    async def publish(self, message: str):
        # store for UI
        self._buffer.append(message)
        # push to async consumers if any
        await self._queue.put(message)

    def snapshot(self) -> List[str]:
        """Return current log buffer for UI"""
        return list(self._buffer)

    async def subscribe(self):
        """Async iterator for future WS use"""
        while True:
            yield await self._queue.get()


# 🔑 SINGLETON
log_bus = LogBus()

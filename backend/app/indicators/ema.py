from collections import deque


class EMA:
    def __init__(self, length: int):
        self.length = length
        self.alpha = 2 / (length + 1)
        self.value = None
        self._buf = deque(maxlen=length)

    def update(self, price: float):
        # Collect initial prices
        if self.value is None:
            self._buf.append(price)

            # Not enough data → Pine returns na
            if len(self._buf) < self.length:
                return None

            # First EMA seed = SMA(length)
            self.value = sum(self._buf) / self.length
            return self.value

        # Normal EMA update
        self.value = self.alpha * price + (1 - self.alpha) * self.value
        return self.value


class SMA:
    def __init__(self, length: int):
        self.length = length
        self.buf = deque(maxlen=length)

    def update(self, value: float):
        self.buf.append(value)
        if len(self.buf) < self.length:
            return None
        return sum(self.buf) / self.length

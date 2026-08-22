from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after = retry_after


class AttemptLimiter:
    """Small in-process limiter for the single supported API process."""

    def __init__(
        self,
        *,
        attempts: int = 5,
        window_seconds: int = 300,
        maximum_keys: int = 10_000,
    ) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.maximum_keys = maximum_keys
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _active_entries(self, key: str, now: float) -> deque[float] | None:
        entries = self._entries.get(key)
        if entries is None:
            return None
        cutoff = now - self.window_seconds
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if not entries:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entries

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            entries = self._active_entries(key, now)
            if entries is not None and len(entries) >= self.attempts:
                raise RateLimitExceeded(max(1, int(entries[0] + self.window_seconds - now)))

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            entries = self._active_entries(key, now)
            if entries is None:
                while len(self._entries) >= self.maximum_keys:
                    self._entries.popitem(last=False)
                entries = deque()
                self._entries[key] = entries
            entries.append(now)
            self._entries.move_to_end(key)

    def consume(self, key: str) -> None:
        """Atomically reserve an attempt before expensive authentication work."""

        now = time.monotonic()
        with self._lock:
            entries = self._active_entries(key, now)
            if entries is not None and len(entries) >= self.attempts:
                raise RateLimitExceeded(max(1, int(entries[0] + self.window_seconds - now)))
            if entries is None:
                while len(self._entries) >= self.maximum_keys:
                    self._entries.popitem(last=False)
                entries = deque()
                self._entries[key] = entries
            entries.append(now)
            self._entries.move_to_end(key)

    def clear(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def refund(self, key: str) -> None:
        with self._lock:
            entries = self._entries.get(key)
            if not entries:
                return
            entries.pop()
            if not entries:
                self._entries.pop(key, None)

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._entries)

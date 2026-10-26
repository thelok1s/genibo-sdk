"""Time sources: real time for hardware, virtual time for deterministic simulation."""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since an arbitrary origin (monotonic)."""
        ...

    def sleep(self, seconds: float, cancel: threading.Event | None = None) -> bool:
        """Sleep; return False if *cancel* was set before the time elapsed."""
        ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float, cancel: threading.Event | None = None) -> bool:
        if seconds <= 0:
            return not (cancel and cancel.is_set())
        if cancel is None:
            time.sleep(seconds)
            return True
        return not cancel.wait(seconds)


class VirtualClock:
    """Advances only when someone sleeps; makes simulator runs instantaneous and repeatable."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._t

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._t += max(0.0, seconds)

    def sleep(self, seconds: float, cancel: threading.Event | None = None) -> bool:
        if cancel is not None and cancel.is_set():
            return False
        self.advance(seconds)
        return True

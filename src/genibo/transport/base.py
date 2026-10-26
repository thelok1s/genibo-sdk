"""Transport interface: a byte pipe to a robot (or to the simulator)."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque


class Transport(ABC):
    """A bidirectional byte stream.

    ``is_hardware`` is True only for transports that reach a physical device
    (serial port, TCP socket). The simulator and in-memory transports report
    False, and the CLI / studio label them as simulated.
    """

    kind: str = "abstract"
    is_hardware: bool = False

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, timeout: float) -> bytes:
        """Return available bytes, waiting up to *timeout* seconds; ``b""`` on timeout."""

    def __enter__(self) -> Transport:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class MemoryTransport(Transport):
    """In-process pipe for tests: records writes, serves injected bytes."""

    kind = "memory"

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._rx: deque[bytes] = deque()
        self._cv = threading.Condition()
        self._open = False

    @property
    def description(self) -> str:
        return "memory (test double, not hardware)"

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        with self._cv:
            self._open = False
            self._cv.notify_all()

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        if not self._open:
            from genibo.errors import TransportError

            raise TransportError("memory transport is closed")
        self.writes.append(bytes(data))

    def inject(self, data: bytes) -> None:
        with self._cv:
            self._rx.append(bytes(data))
            self._cv.notify_all()

    def read(self, timeout: float) -> bytes:
        with self._cv:
            if not self._rx and self._open:
                self._cv.wait(timeout)
            if not self._rx:
                return b""
            return b"".join(self._rx.popleft() for _ in range(len(self._rx)))

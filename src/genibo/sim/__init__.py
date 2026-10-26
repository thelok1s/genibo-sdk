"""Protocol-level simulator. Simulated behaviour is never hardware evidence."""

from __future__ import annotations

import threading

from genibo.sim.device import ASSUMPTIONS, ServoState, SimEvent, VirtualGenibo
from genibo.transport.base import Transport


class SimTransport(Transport):
    """Transport whose far end is a :class:`VirtualGenibo` in this process."""

    kind = "simulator"
    is_hardware = False

    def __init__(self, device: VirtualGenibo) -> None:
        self.device = device
        self._rx = bytearray()
        self._cv = threading.Condition()
        self._open = False

    @property
    def description(self) -> str:
        return f"simulator (robot 0x{self.device.robot_id:02x}; inferred behaviour, not hardware)"

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
        reply = self.device.receive(data)
        if reply:
            with self._cv:
                self._rx += reply
                self._cv.notify_all()

    def read(self, timeout: float) -> bytes:
        with self._cv:
            if not self._rx and self._open:
                self._cv.wait(timeout)
            data = bytes(self._rx)
            self._rx.clear()
            return data


__all__ = ["ASSUMPTIONS", "ServoState", "SimEvent", "SimTransport", "VirtualGenibo"]

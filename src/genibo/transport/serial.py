"""Bluetooth SPP / USB serial transport (pyserial).

The legacy tool opened ``COM<n>`` at 115200 baud, 8 data bits, no parity, one
stop bit. On macOS the paired Genibo appears as ``/dev/tty.<name>`` (or
``/dev/cu.<name>``), on Linux as ``/dev/rfcommN`` after ``rfcomm bind``.
"""

from __future__ import annotations

from dataclasses import dataclass

from genibo.errors import TransportError
from genibo.protocol.constants import DEFAULT_BAUDRATE
from genibo.transport.base import Transport


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as lp

    return [
        PortInfo(p.device, p.description or "", p.hwid or "")
        for p in sorted(lp.comports(), key=lambda p: p.device)
    ]


def normalize_port(port: str | int) -> str:
    """Accept the legacy integer form (``connect(5, ...)`` meant ``COM5``)."""
    if isinstance(port, int):
        return f"COM{port}"
    return port


class SerialTransport(Transport):
    kind = "serial"
    is_hardware = True

    def __init__(
        self, port: str | int, baudrate: int = DEFAULT_BAUDRATE, *, write_timeout: float = 5.0
    ) -> None:
        self.port = normalize_port(port)
        self.baudrate = baudrate
        self.write_timeout = write_timeout
        self._ser = None

    @property
    def description(self) -> str:
        return f"serial {self.port} @ {self.baudrate} 8N1"

    def open(self) -> None:
        if self._ser is not None:
            return
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - pyserial is a hard dependency
            raise TransportError("pyserial is not installed") from exc
        try:
            self._ser = serial.Serial(
                self.port,
                self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=self.write_timeout,
            )
        except (OSError, ValueError) as exc:
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def write(self, data: bytes) -> None:
        if self._ser is None:
            raise TransportError(f"{self.port} is not open")
        try:
            self._ser.write(data)
            self._ser.flush()
        except Exception as exc:
            raise TransportError(f"write to {self.port} failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        ser = self._ser
        if ser is None:
            raise TransportError(f"{self.port} is not open")
        try:
            # Changing the timeout reconfigures the port on some platforms; only do it when needed.
            if ser.timeout != timeout:
                ser.timeout = timeout
            first = ser.read(1)
            if not first:
                return b""
            waiting = ser.in_waiting
            return first + (ser.read(waiting) if waiting else b"")
        except Exception as exc:
            raise TransportError(f"read from {self.port} failed: {exc}") from exc

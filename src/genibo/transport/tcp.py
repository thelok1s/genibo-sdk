"""TCP transport (one socket). ``Ojw.CGenibo`` opened six of these, ports 6100..6105."""

from __future__ import annotations

import socket

from genibo.errors import TransportError
from genibo.transport.base import Transport


class TcpTransport(Transport):
    kind = "tcp"
    is_hardware = True

    def __init__(self, host: str, port: int, *, connect_timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self._sock: socket.socket | None = None

    @property
    def description(self) -> str:
        return f"tcp {self.host}:{self.port}"

    def open(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def write(self, data: bytes) -> None:
        if self._sock is None:
            raise TransportError(f"{self.description} is not connected")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"send to {self.description} failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        sock = self._sock
        if sock is None:
            raise TransportError(f"{self.description} is not connected")
        sock.settimeout(timeout)
        try:
            data = sock.recv(65536)
        except TimeoutError:
            return b""
        except OSError as exc:
            raise TransportError(f"receive from {self.description} failed: {exc}") from exc
        if not data:
            raise TransportError(f"{self.description} closed by peer")
        return data

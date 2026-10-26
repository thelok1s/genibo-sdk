"""Client for the TCP interface used by ``Ojw.CGenibo`` (six channels, ports 6100..6105).

The legacy example only ever *commented out* this path (``Connect(ip)``,
``Auth("geniibo", "1234")``, ``ActionPlay(true, "7.act", ...)``). Its frames are
golden-tested, but which Genibo models expose this TCP service is unknown.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from genibo.errors import ResponseTimeout
from genibo.link import Link
from genibo.protocol import commands as cmd
from genibo.protocol.constants import TCP_BASE_PORT, TCP_ID_BASE, TcpChannel, TcpCommand
from genibo.protocol.frames import Frame, LinkFrame
from genibo.transport.base import Transport
from genibo.transport.tcp import TcpTransport

log = logging.getLogger("genibo.net")


class GeniboNet:
    def __init__(
        self,
        host: str,
        *,
        base_port: int = TCP_BASE_PORT,
        transport_factory: Callable[[str, int], Transport] = TcpTransport,
        encoding: str = cmd.DEFAULT_ENCODING,
    ) -> None:
        self.host = host
        self.base_port = base_port
        self.encoding = encoding
        self._factory = transport_factory
        self.links: dict[TcpChannel, Link] = {}
        self._listeners: list[Callable[[TcpChannel, Frame], None]] = []

    def open(self) -> GeniboNet:
        """Open all six channels; on any failure close the ones already open (as the legacy code did)."""
        try:
            for ch in TcpChannel:
                link = Link(self._factory(self.host, self.base_port + int(ch)), robot_id=None).open()
                link.add_listener(lambda f, ch=ch: self._dispatch(ch, f))
                self.links[ch] = link
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        for link in self.links.values():
            link.close()
        self.links.clear()

    def __enter__(self) -> GeniboNet:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def on_frame(self, cb: Callable[[TcpChannel, Frame], None]) -> None:
        self._listeners.append(cb)

    def _dispatch(self, ch: TcpChannel, f: Frame) -> None:
        for cb in list(self._listeners):
            cb(ch, f)

    def send(self, channel: TcpChannel, command: int, payload: bytes = b"") -> bytes:
        return self.links[channel].send(cmd.tcp_frame(channel, command, payload))

    def auth(self, user: str, password: str, timeout: float = 2.0) -> dict[TcpChannel, bool | None]:
        """Send credentials on every channel; return each channel's verdict (None = no reply)."""
        results: dict[TcpChannel, bool | None] = {}
        for frame in cmd.tcp_auth(user, password, encoding=self.encoding):
            ch = TcpChannel(frame.robot_id - TCP_ID_BASE)

            def match(f: Frame) -> bool:
                return isinstance(f, LinkFrame) and f.command == TcpCommand.AUTH

            try:
                reply = self.links[ch].request(frame, match, timeout)
                assert isinstance(reply, LinkFrame)
                results[ch] = reply.payload[:1] != b"\x00"
            except ResponseTimeout:
                results[ch] = None
        return results

    def action_play(
        self,
        name: str,
        *,
        master: bool = True,
        sound: bool = False,
        emoticon: bool = False,
        speed_percent: int = 100,
    ) -> bytes:
        frame = cmd.tcp_action_play(
            name,
            master=master,
            sound=sound,
            emoticon=emoticon,
            speed_percent=speed_percent,
            encoding=self.encoding,
        )
        return self.links[TcpChannel.MOTION].send(frame)

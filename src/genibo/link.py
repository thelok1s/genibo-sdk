"""Link: owns a transport, a reader thread and request/reply matching."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from genibo.errors import ResponseTimeout, TransportError
from genibo.protocol.constants import BROADCAST_ID
from genibo.protocol.describe import describe
from genibo.protocol.frames import BusPacket, DecodeIssue, Frame, LinkFrame, StreamDecoder
from genibo.transport.base import Transport

log = logging.getLogger("genibo.link")

Matcher = Callable[[Frame], bool]
TraceHook = Callable[[str, bytes, "Frame | DecodeIssue | None"], None]


@dataclass
class _Waiter:
    match: Matcher
    event: threading.Event = field(default_factory=threading.Event)
    result: Frame | None = None


class Link:
    """Sends frames and routes incoming frames to waiters and listeners.

    Incoming link frames are accepted when their ID equals ``robot_id`` or the
    broadcast ID (as the legacy receive path did); bare bus packets (MPSU
    replies) are always accepted. Pass ``robot_id=None`` to accept everything.
    """

    def __init__(
        self, transport: Transport, *, robot_id: int | None = None, poll_interval: float = 0.05
    ) -> None:
        self.transport = transport
        self.robot_id = robot_id
        self.poll_interval = poll_interval
        self._decoder = StreamDecoder()
        self._send_lock = threading.Lock()
        self._waiters: list[_Waiter] = []
        self._wlock = threading.Lock()
        self._listeners: list[Callable[[Frame], None]] = []
        self._trace: list[TraceHook] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: BaseException | None = None

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> Link:
        self.transport.open()
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._reader, name="genibo-link-reader", daemon=True)
        self._thread.start()
        log.info(
            "link open: %s",
            self.transport.description,
            extra={"event": "open", "hardware": self.transport.is_hardware},
        )
        return self

    def close(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None
        self.transport.close()
        log.info("link closed: %s", self.transport.description, extra={"event": "close"})

    @property
    def is_open(self) -> bool:
        return self._thread is not None and self.transport.is_open

    def __enter__(self) -> Link:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ hooks
    def add_listener(self, cb: Callable[[Frame], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def add_trace(self, hook: TraceHook) -> Callable[[], None]:
        """``hook(direction, raw_bytes, decoded)`` for every tx/rx/discard (studio packet log)."""
        self._trace.append(hook)
        return lambda: self._trace.remove(hook) if hook in self._trace else None

    def _emit_trace(self, direction: str, raw: bytes, item: Frame | DecodeIssue | None) -> None:
        for hook in list(self._trace):
            try:
                hook(direction, raw, item)
            except Exception:  # a broken UI hook must never break the link
                log.exception("trace hook failed")

    # ------------------------------------------------------------------ sending
    def send(self, frame: Frame) -> bytes:
        if not self.transport.is_open:
            raise TransportError(f"{self.transport.description} is not open")
        if self.error is not None:
            raise TransportError(f"link reader failed: {self.error}")
        data = frame.encode()
        with self._send_lock:
            self.transport.write(data)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("tx %s", describe(frame).summary, extra={"event": "tx", "hex": data.hex()})
        self._emit_trace("tx", data, frame)
        return data

    def request(self, frame: Frame, match: Matcher, timeout: float) -> Frame:
        """Send *frame* and wait for the first incoming frame accepted by *match*."""
        w = _Waiter(match)
        with self._wlock:
            self._waiters.append(w)
        try:
            self.send(frame)
            if not w.event.wait(timeout):
                if self.error is not None:
                    raise TransportError(f"link reader failed: {self.error}")
                raise ResponseTimeout(
                    f"no reply to {describe(frame).summary} within {timeout:.2f}s on {self.transport.description}"
                )
            if w.result is None:
                raise TransportError(f"link reader failed: {self.error}")
            return w.result
        finally:
            with self._wlock:
                if w in self._waiters:
                    self._waiters.remove(w)

    def wait_for(self, match: Matcher, timeout: float) -> Frame:
        w = _Waiter(match)
        with self._wlock:
            self._waiters.append(w)
        try:
            if not w.event.wait(timeout):
                raise ResponseTimeout(f"no matching frame within {timeout:.2f}s")
            if w.result is None:
                raise TransportError(f"link reader failed: {self.error}")
            return w.result
        finally:
            with self._wlock:
                if w in self._waiters:
                    self._waiters.remove(w)

    # ------------------------------------------------------------------ receiving
    def _accept(self, frame: Frame) -> bool:
        if self.robot_id is None or isinstance(frame, BusPacket):
            return True
        return frame.robot_id in (self.robot_id, BROADCAST_ID) or self.robot_id == BROADCAST_ID

    def _dispatch(self, frame: Frame) -> None:
        with self._wlock:
            for w in self._waiters:
                if not w.event.is_set() and w.match(frame):
                    w.result = frame
                    w.event.set()
                    break
        for cb in list(self._listeners):
            try:
                cb(frame)
            except Exception:
                log.exception("listener failed")

    def feed(self, data: bytes) -> None:
        """Decode bytes as if received (used by the reader thread and by tests)."""
        for item in self._decoder.feed(data):
            if isinstance(item, DecodeIssue):
                log.warning(
                    "rx discarded %d bytes: %s",
                    len(item.discarded),
                    item.reason,
                    extra={"event": "discard", "hex": item.discarded.hex()},
                )
                self._emit_trace("discard", item.discarded, item)
                continue
            raw = item.encode()
            if log.isEnabledFor(logging.DEBUG):
                log.debug("rx %s", describe(item).summary, extra={"event": "rx", "hex": raw.hex()})
            self._emit_trace("rx", raw, item)
            if isinstance(item, LinkFrame | BusPacket) and self._accept(item):
                self._dispatch(item)
            else:
                log.debug("rx ignored (other robot id 0x%02x)", getattr(item, "robot_id", -1))

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.transport.read(self.poll_interval)
            except TransportError as exc:
                if self._stop.is_set():
                    return
                self.error = exc
                log.error("link reader stopped: %s", exc, extra={"event": "error"})
                with self._wlock:
                    for w in self._waiters:
                        w.event.set()
                return
            if data:
                self.feed(data)

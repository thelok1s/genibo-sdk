"""Byte-level framing for the two packet formats the Genibo host links use.

Link frame (Bluetooth serial ``CGenibo_Bt.send_data`` and TCP ``CGenibo.SendData``)::

    FF | id | cmd | len (4 bytes, big endian) | payload (len bytes) | chk

    chk = XOR(id, cmd, len bytes, payload) & 0x7F

Bus packet (servo / MPSU packet; tunnelled in link command 0x81, or sent bare by
``drbluetooth_mpsu_send_data``)::

    FF FF | size | id | cmd | cs1 | cs2 | data
    size = 7 + len(data)            (one byte, so len(data) <= 248)
    cs1  = XOR(size, id, cmd, data) & 0xFE
    cs2  = ~cs1 & 0xFE

Both layouts are verified byte-for-byte against the legacy C# implementation by
``tests/test_golden_csharp.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from genibo.errors import FrameError
from genibo.protocol.constants import BUS_HEADER_SIZE, LINK_HEADER, MAX_BUS_PACKET

#: Upper bound accepted by the stream decoder for a link payload. The legacy
#: format allows 2**31; real replies are small, and file transfers in this
#: repository's scenarios are far below this. Larger declared lengths are
#: treated as corruption so a bit error cannot stall the decoder forever.
MAX_LINK_PAYLOAD = 16 * 1024 * 1024


def _check_byte(name: str, value: int) -> None:
    if not 0 <= value <= 0xFF:
        raise FrameError(f"{name} must fit in one byte, got {value!r}")


def link_checksum(body: Iterable[int]) -> int:
    """Checksum of a link frame: XOR over id..payload, masked with 0x7F."""
    acc = 0
    for b in body:
        acc ^= b
    return acc & 0x7F


def bus_checksums(size: int, device_id: int, command: int, data: bytes) -> tuple[int, int]:
    """(cs1, cs2) of a bus packet, as ``serial_make_checksum`` computes them."""
    acc = size ^ device_id ^ command
    for b in data:
        acc ^= b
    cs1 = acc & 0xFE
    cs2 = (~cs1) & 0xFE
    return cs1, cs2


@dataclass(frozen=True)
class LinkFrame:
    """One frame on the Bluetooth serial or TCP link."""

    robot_id: int
    command: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        _check_byte("robot_id", self.robot_id)
        _check_byte("command", self.command)
        if self.robot_id == LINK_HEADER:
            raise FrameError("robot_id 0xFF is indistinguishable from a bus packet header")
        if len(self.payload) > 0xFFFFFFFF:
            raise FrameError("payload too large for the 32-bit length field")

    def encode(self) -> bytes:
        n = len(self.payload)
        body = bytes((self.robot_id, self.command)) + n.to_bytes(4, "big") + self.payload
        return bytes((LINK_HEADER,)) + body + bytes((link_checksum(body),))

    @classmethod
    def decode(cls, data: bytes) -> LinkFrame:
        """Decode exactly one complete frame (strict: no trailing bytes)."""
        if len(data) < 8 or data[0] != LINK_HEADER:
            raise FrameError("not a link frame (needs FF header and at least 8 bytes)")
        n = int.from_bytes(data[3:7], "big")
        if len(data) != 8 + n:
            raise FrameError(f"length field says {n} payload bytes but frame has {len(data) - 8}")
        expected = link_checksum(data[1:-1])
        if data[-1] != expected:
            raise FrameError(f"link checksum mismatch: got 0x{data[-1]:02x}, expected 0x{expected:02x}")
        return cls(data[1], data[2], bytes(data[7:-1]))


@dataclass(frozen=True)
class BusPacket:
    """One servo-bus / MPSU packet (``FF FF size id cmd cs1 cs2 data``)."""

    device_id: int
    command: int
    data: bytes = b""

    def __post_init__(self) -> None:
        _check_byte("device_id", self.device_id)
        _check_byte("command", self.command)
        if BUS_HEADER_SIZE + len(self.data) > MAX_BUS_PACKET:
            raise FrameError(
                f"bus packet data is {len(self.data)} bytes; the one-byte size field allows at most "
                f"{MAX_BUS_PACKET - BUS_HEADER_SIZE}"
            )

    @property
    def size(self) -> int:
        return BUS_HEADER_SIZE + len(self.data)

    def encode(self) -> bytes:
        cs1, cs2 = bus_checksums(self.size, self.device_id, self.command, self.data)
        return bytes((0xFF, 0xFF, self.size, self.device_id, self.command, cs1, cs2)) + self.data

    @classmethod
    def decode(cls, data: bytes) -> BusPacket:
        if len(data) < BUS_HEADER_SIZE or data[0] != 0xFF or data[1] != 0xFF:
            raise FrameError("not a bus packet (needs FF FF header and at least 7 bytes)")
        size = data[2]
        if size != len(data):
            raise FrameError(f"size field says {size} bytes but packet has {len(data)}")
        payload = bytes(data[7:])
        cs1, cs2 = bus_checksums(size, data[3], data[4], payload)
        if (data[5], data[6]) != (cs1, cs2):
            raise FrameError(
                f"bus checksum mismatch: got {data[5]:02x} {data[6]:02x}, expected {cs1:02x} {cs2:02x}"
            )
        return cls(data[3], data[4], payload)


Frame = LinkFrame | BusPacket


@dataclass(frozen=True)
class DecodeIssue:
    """Bytes the stream decoder had to discard, and why."""

    reason: str
    discarded: bytes


@dataclass
class StreamDecoder:
    """Incremental decoder for the byte stream coming back from the robot.

    Accepts link frames and bare bus packets interleaved, as the legacy
    ``ReceiveDataCallback`` does. Unlike the legacy parser it handles
    zero-length link frames correctly and resynchronises after corruption by
    discarding one byte at a time. Discarded bytes are reported, never hidden.
    """

    max_payload: int = MAX_LINK_PAYLOAD
    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[Frame | DecodeIssue]:
        self._buf.extend(data)
        out: list[Frame | DecodeIssue] = []
        while True:
            item = self._next()
            if item is None:
                return out
            out.append(item)

    @property
    def pending(self) -> int:
        return len(self._buf)

    def _discard(self, n: int, reason: str) -> DecodeIssue:
        dropped = bytes(self._buf[:n])
        del self._buf[:n]
        return DecodeIssue(reason, dropped)

    def _next(self) -> Frame | DecodeIssue | None:
        buf = self._buf
        if not buf:
            return None
        if buf[0] != LINK_HEADER:
            # Skip garbage up to the next header byte in one step.
            nxt = buf.find(LINK_HEADER)
            return self._discard(len(buf) if nxt < 0 else nxt, "noise before header")
        if len(buf) < 2:
            return None
        if buf[1] == 0xFF:
            if len(buf) < 3:
                return None
            size = buf[2]
            if size < BUS_HEADER_SIZE:
                return self._discard(1, f"bus packet size {size} < {BUS_HEADER_SIZE}")
            if len(buf) < size:
                return None
            raw = bytes(buf[:size])
            try:
                pkt = BusPacket.decode(raw)
            except FrameError as exc:
                return self._discard(1, str(exc))
            del buf[:size]
            return pkt
        if len(buf) < 7:
            return None
        n = int.from_bytes(buf[3:7], "big")
        if n > self.max_payload:
            return self._discard(1, f"declared payload {n} exceeds limit {self.max_payload}")
        total = 8 + n
        if len(buf) < total:
            return None
        raw = bytes(buf[:total])
        try:
            frame = LinkFrame.decode(raw)
        except FrameError as exc:
            return self._discard(1, str(exc))
        del buf[:total]
        return frame

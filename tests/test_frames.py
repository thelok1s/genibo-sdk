"""Framing invariants beyond the golden scenarios (property-based)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from genibo.errors import FrameError
from genibo.protocol.describe import describe_stream
from genibo.protocol.frames import (
    BusPacket,
    DecodeIssue,
    LinkFrame,
    StreamDecoder,
    bus_checksums,
    link_checksum,
)

robot_ids = st.integers(0, 0xFE)
bytes_any = st.binary(max_size=600)


@given(robot_ids, st.integers(0, 255), bytes_any)
def test_link_frame_roundtrip(rid: int, cmd: int, payload: bytes) -> None:
    f = LinkFrame(rid, cmd, payload)
    raw = f.encode()
    assert raw[0] == 0xFF and len(raw) == 8 + len(payload)
    assert raw[-1] <= 0x7F  # checksum never collides with the header byte
    assert LinkFrame.decode(raw) == f


@given(st.integers(0, 0xFF), st.integers(0, 255), st.binary(max_size=248))
def test_bus_packet_roundtrip(dev: int, cmd: int, data: bytes) -> None:
    p = BusPacket(dev, cmd, data)
    raw = p.encode()
    cs1, cs2 = bus_checksums(raw[2], dev, cmd, data)
    assert raw[5:7] == bytes((cs1, cs2)) and cs1 & 1 == 0 and cs2 & 1 == 0
    assert BusPacket.decode(raw) == p


def test_bus_packet_size_limit() -> None:
    BusPacket(0xFE, 6, bytes(248))
    with pytest.raises(FrameError):
        BusPacket(0xFE, 6, bytes(249))


def test_robot_id_ff_is_rejected() -> None:
    with pytest.raises(FrameError):
        LinkFrame(0xFF, 0x63)


@given(
    st.lists(st.tuples(robot_ids, st.integers(0, 255), st.binary(max_size=80)), max_size=8),
    st.integers(1, 17),
)
def test_stream_decoder_any_chunking(frames: list[tuple[int, int, bytes]], chunk: int) -> None:
    expected = [LinkFrame(*f) for f in frames]
    data = b"".join(f.encode() for f in expected)
    dec = StreamDecoder()
    got = []
    for i in range(0, len(data), chunk):
        got += dec.feed(data[i : i + chunk])
    assert got == expected and dec.pending == 0


@given(st.binary(min_size=1, max_size=40), robot_ids, st.binary(max_size=30))
def test_stream_decoder_resyncs_after_noise(noise: bytes, rid: int, payload: bytes) -> None:
    good = LinkFrame(rid, 0x63, payload)
    noise = noise.replace(b"\xff", b"\x00")  # noise without header bytes
    items = StreamDecoder().feed(noise + good.encode())
    assert items[-1] == good
    assert all(isinstance(i, DecodeIssue) for i in items[:-1])


def test_corrupted_frame_is_reported_and_next_frame_recovered() -> None:
    a = bytearray(LinkFrame(0x20, 0x63, b"\x10\x20").encode())
    a[-1] ^= 0x01
    b = LinkFrame(0x20, 0x41, b"\x01").encode()
    items = StreamDecoder().feed(bytes(a) + b)
    assert items[-1] == LinkFrame(0x20, 0x41, b"\x01")
    assert any(isinstance(i, DecodeIssue) and "checksum" in i.reason for i in items)


def test_interleaved_bus_and_link_frames() -> None:
    bus = BusPacket(0x40, 0x54, bytes([70, 1, 9, 0, 0]))
    link = LinkFrame(0x20, 0xF1)
    assert StreamDecoder().feed(bus.encode() + link.encode() + bus.encode()) == [bus, link, bus]


def test_checksum_definition() -> None:
    assert link_checksum([0x20, 0x63, 0, 0, 0, 0]) == 0x43


def test_describe_stream_reports_incomplete_tail() -> None:
    raw = LinkFrame(0x20, 0x63).encode()
    out = describe_stream(raw + raw[:3])
    assert "INCOMPLETE" in out[-1].summary

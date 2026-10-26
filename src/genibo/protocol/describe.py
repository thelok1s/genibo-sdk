"""Human-readable disassembly of frames (used by the CLI inspector, logs and studio)."""

from __future__ import annotations

from dataclasses import dataclass, field

from genibo.protocol.constants import (
    FILENAME_FIELD,
    TCP_ID_BASE,
    BusCommand,
    JogFlag,
    LinkCommand,
    ServoRegister,
    StatusCode,
    TcpChannel,
)
from genibo.protocol.convert import ticks_to_ms
from genibo.protocol.frames import BusPacket, DecodeIssue, LinkFrame, StreamDecoder


@dataclass
class Field:
    name: str
    value: str
    offset: int = -1  # byte offset in the encoded frame, -1 if not applicable
    length: int = 0


@dataclass
class Description:
    summary: str
    fields: list[Field] = field(default_factory=list)
    children: list[Description] = field(default_factory=list)

    def lines(self, indent: str = "") -> list[str]:
        out = [indent + self.summary]
        for f in self.fields:
            loc = f"[{f.offset}:{f.offset + f.length}] " if f.offset >= 0 else ""
            out.append(f"{indent}  {loc}{f.name} = {f.value}")
        for c in self.children:
            out.extend(c.lines(indent + "  "))
        return out

    def __str__(self) -> str:
        return "\n".join(self.lines())


def _cname(enum: type, value: int) -> str:
    try:
        return f"{enum(value).name}(0x{value:02x})"  # type: ignore[call-arg]
    except ValueError:
        return f"0x{value:02x}"


def _name_field(raw: bytes) -> str:
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    terminated = b"\x00" in raw
    return repr(text) + ("" if terminated else " (no terminator)")


def flags_text(flags: int) -> str:
    if flags == 0:
        return "0x00 (position)"
    names = [f.name for f in JogFlag if f and f.name and flags & f]
    return f"0x{flags:02x} ({'|'.join(n for n in names if n)})"


def bus_summary(pkt: BusPacket) -> str:
    """One-line meaning of a bus packet (used in listings and logs)."""
    d, c = pkt.data, pkt.command
    who = "all" if pkt.device_id == 0xFE else f"0x{pkt.device_id:02x}"
    if c in (BusCommand.S_JOG, BusCommand.MPSU_MOTOR_MOVE):
        if not d:
            return f"{_cname(BusCommand, c)} to {who} with no servos"
        n = (len(d) - 1) // 4
        ids = ",".join(str(d[1 + i * 4 + 3]) for i in range(n))
        return f"{_cname(BusCommand, c)} {n} servo(s) [{ids}] in {d[0]} ticks ({ticks_to_ms(d[0]):.1f} ms)"
    if c == BusCommand.RAM_WRITE and len(d) >= 3 and d[0] == ServoRegister.TORQUE_CONTROL:
        mode = {0x00: "FREE", 0x40: "DRIVER", 0x20: "SERVO", 0x60: "ON"}.get(d[2], f"0x{d[2]:02x}")
        return f"torque {mode} -> servo {who}"
    if c == BusCommand.MPSU_PLAY_MOTION and len(d) >= 2:
        return f"MPSU {who} motion " + ("STOP" if d[0] == 0xFE else f"#{d[0]}" + (" ready" if d[1] else ""))
    if c == BusCommand.MPSU_PLAY_TASK and d:
        return f"MPSU {who} task " + ("STOP" if d[0] == 0xFE else f"#{d[0]}")
    if c == BusCommand.MPSU_HEADLED_BUZZ and len(d) >= 2:
        return f"MPSU {who} head LED {d[0]} buzz {d[1]}"
    return f"{_cname(BusCommand, c)} to {who} ({len(d)} data bytes)"


def describe_bus(pkt: BusPacket, base: int = 0) -> Description:
    d = Description(
        f"BUS id=0x{pkt.device_id:02x} {_cname(BusCommand, pkt.command)} size={pkt.size}: {bus_summary(pkt)}"
    )
    data = pkt.data
    off = base + 7
    if pkt.command in (BusCommand.S_JOG, BusCommand.MPSU_MOTOR_MOVE) and data:
        t = data[0]
        d.fields.append(Field("playtime", f"{t} ticks ({ticks_to_ms(t):.1f} ms)", off, 1))
        body = data[1:]
        if len(body) % 4:
            d.fields.append(Field("warning", f"{len(body)} trailing bytes are not a multiple of 4"))
        for i in range(len(body) // 4):
            lo, hi, fl, sid = body[i * 4 : i * 4 + 4]
            raw = lo | (hi << 8)
            signed = raw - 0x10000 if raw & 0x8000 else raw
            d.fields.append(
                Field(
                    f"servo[{i}]",
                    f"id={sid} value={raw} (signed {signed}) flags={flags_text(fl)}",
                    off + 1 + i * 4,
                    4,
                )
            )
    elif pkt.command == BusCommand.RAM_WRITE and len(data) >= 2:
        addr, n = data[0], data[1]
        d.fields.append(Field("address", _cname(ServoRegister, addr), off, 1))
        d.fields.append(Field("length", str(n), off + 1, 1))
        d.fields.append(Field("data", data[2:].hex(" "), off + 2, len(data) - 2))
        if addr == ServoRegister.TORQUE_CONTROL and len(data) >= 3:
            v = data[2]
            mode = {
                0x00: "torque free",
                0x40: "driver only (brake)",
                0x20: "servo only",
                0x60: "torque on",
            }.get(v, "?")
            d.fields.append(Field("torque", f"0x{v:02x} {mode}"))
    elif pkt.command == BusCommand.MPSU_PLAY_MOTION and len(data) >= 2:
        idx = "STOP(0xfe)" if data[0] == 0xFE else str(data[0])
        d.fields.append(Field("motion", idx, off, 1))
        d.fields.append(Field("ready_only", str(bool(data[1])), off + 1, 1))
        if len(data) >= 4:
            d.fields.append(Field("delay_ms", str(data[2] | (data[3] << 8)), off + 2, 2))
    elif pkt.command == BusCommand.MPSU_PLAY_TASK and data:
        d.fields.append(Field("task", "STOP(0xfe)" if data[0] == 0xFE else str(data[0]), off, 1))
    elif pkt.command == BusCommand.MPSU_HEADLED_BUZZ and len(data) >= 2:
        d.fields.append(Field("head_led", str(data[0]), off, 1))
        d.fields.append(Field("buzz", str(data[1]), off + 1, 1))
    elif (
        pkt.command
        in (
            BusCommand.MPSU_ROM_WRITE,
            BusCommand.MPSU_RAM_WRITE,
            BusCommand.MPSU_RAM_READ,
            BusCommand.MPSU_ROM_READ,
        )
        and len(data) >= 2
    ):
        d.fields.append(Field("address", str(data[0]), off, 1))
        d.fields.append(Field("length", str(data[1]), off + 1, 1))
        if len(data) > 2:
            d.fields.append(Field("data", data[2:].hex(" "), off + 2, len(data) - 2))
    elif data:
        d.fields.append(Field("data", data.hex(" "), off, len(data)))
    return d


def describe_link(frame: LinkFrame, *, tcp: bool | None = None) -> Description:
    """Describe a link frame. ``tcp=None`` guesses from the ID (0x90..0x95 = TCP channel)."""
    p = frame.payload
    is_tcp = tcp if tcp is not None else TCP_ID_BASE <= frame.robot_id < TCP_ID_BASE + len(TcpChannel)
    who = (
        f"TCP channel {_cname(TcpChannel, frame.robot_id - TCP_ID_BASE)}"
        if is_tcp
        else ("robot BROADCAST(0xfe)" if frame.robot_id == 0xFE else f"robot 0x{frame.robot_id:02x}")
    )
    cmd = f"0x{frame.command:02x}" if is_tcp else _cname(LinkCommand, frame.command)
    d = Description(f"LINK {who} {cmd} payload={len(p)}B")
    off = 7
    c = frame.command
    if is_tcp:
        if c in (0x33, 0x34) and len(p) == 19:
            d.fields += [
                Field("name", _name_field(p[:16]), off, 16),
                Field("sound", str(bool(p[16])), off + 16, 1),
                Field("emoticon", str(bool(p[17])), off + 17, 1),
                Field("speed_percent", str(p[18]), off + 18, 1),
            ]
        elif p:
            d.fields.append(Field("payload", p.hex(" "), off, len(p)))
        return d
    if c == LinkCommand.PLAY and len(p) in (52, 60):
        nm = p[1 : 1 + FILENAME_FIELD].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        d.summary = f"LINK {who} PLAY {'motion' if len(p) == 60 else 'task'} {nm!r} ({'master' if p[0] == 0 else 'user'})"
        kind = "motion" if len(p) == 60 else "task"
        d.fields += [
            Field("kind", kind),
            Field("storage", "master" if p[0] == 0 else "user", off, 1),
            Field("name", _name_field(p[1 : 1 + FILENAME_FIELD]), off + 1, FILENAME_FIELD),
        ]
        if len(p) == 60:
            sec = int.from_bytes(p[52:56], "big")
            usec = int.from_bytes(p[56:60], "big")
            d.fields.append(
                Field("start", "now" if sec == usec == 0 else f"unix {sec}.{usec:06d}", off + 52, 8)
            )
    elif c in (LinkCommand.FILE_LIST, LinkCommand.FILE_DELETE_ALL) and len(p) == 1:
        d.fields.append(Field("storage", "master" if p[0] == 0 else "user", off, 1))
    elif c in (LinkCommand.FILE_DELETE, LinkCommand.FILE_FETCH) and len(p) == 52:
        d.fields += [
            Field("storage", "master" if p[0] == 0 else "user", off, 1),
            Field("name", _name_field(p[1:]), off + 1, FILENAME_FIELD),
        ]
    elif c == LinkCommand.FILE_SEND and len(p) >= 56:
        d.fields += [
            Field("storage", "master" if p[0] == 0 else "user", off, 1),
            Field("index", str(int.from_bytes(p[1:3], "big")), off + 1, 2),
            Field("total", str(int.from_bytes(p[3:5], "big")), off + 3, 2),
            Field("name", _name_field(p[5:56]), off + 5, FILENAME_FIELD),
            Field("content", f"{len(p) - 56} bytes", off + 56, len(p) - 56),
        ]
    elif c == LinkCommand.SET_TIME and len(p) == 8:
        d.fields.append(
            Field("time", f"unix {int.from_bytes(p[:4], 'big')}.{int.from_bytes(p[4:], 'big'):06d}", off, 8)
        )
    elif c == LinkCommand.AUTH and len(p) == 42:
        d.fields += [Field("user", _name_field(p[:21]), off, 21), Field("password", "*****", off + 21, 21)]
    elif c == LinkCommand.STATUS and len(p) >= 2:
        d.summary = f"LINK {who} STATUS for 0x{p[0]:02x}: {_cname(StatusCode, p[1])}"
        d.fields += [
            Field("for_command", f"0x{p[0]:02x}", off, 1),
            Field("code", _cname(StatusCode, p[1]), off + 1, 1),
        ]
    elif c == LinkCommand.SERVO_BUS:
        try:
            inner = BusPacket.decode(p)
            d.summary = f"LINK {who} SERVO_BUS(0x81): {bus_summary(inner)}"
            d.children.append(describe_bus(inner, base=off))
        except ValueError as exc:
            d.fields.append(Field("error", f"inner packet invalid: {exc}"))
    elif p:
        d.fields.append(
            Field("payload", p.hex(" ") if len(p) <= 64 else p[:64].hex(" ") + " ...", off, len(p))
        )
    return d


def describe(item: LinkFrame | BusPacket | DecodeIssue) -> Description:
    if isinstance(item, LinkFrame):
        return describe_link(item)
    if isinstance(item, BusPacket):
        return describe_bus(item)
    return Description(
        f"DISCARDED {len(item.discarded)}B: {item.reason}", [Field("bytes", item.discarded.hex(" "))]
    )


def describe_stream(data: bytes) -> list[Description]:
    """Split raw bytes into frames and describe each (unterminated tail reported)."""
    dec = StreamDecoder()
    out = [describe(x) for x in dec.feed(data)]
    if dec.pending:
        out.append(Description(f"INCOMPLETE {dec.pending} trailing bytes"))
    return out

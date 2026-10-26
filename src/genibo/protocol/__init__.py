"""Wire protocol of the Genibo host links (pure functions, no I/O).

* :mod:`genibo.protocol.frames` - link frames, bus packets, stream decoder
* :mod:`genibo.protocol.commands` - builders for every legacy command
* :mod:`genibo.protocol.replies` - decoders for robot replies
* :mod:`genibo.protocol.convert` - float32-exact angle/time conversions
* :mod:`genibo.protocol.describe` - disassembler for logs and inspection
"""

from genibo.protocol import commands, convert, replies
from genibo.protocol.constants import (
    BROADCAST_ID,
    BusCommand,
    JogFlag,
    LinkCommand,
    StatusCode,
    Storage,
    TcpChannel,
    TcpCommand,
    Torque,
)
from genibo.protocol.describe import describe, describe_stream
from genibo.protocol.frames import BusPacket, DecodeIssue, LinkFrame, StreamDecoder

__all__ = [
    "BROADCAST_ID",
    "BusCommand",
    "BusPacket",
    "DecodeIssue",
    "JogFlag",
    "LinkCommand",
    "LinkFrame",
    "StatusCode",
    "Storage",
    "StreamDecoder",
    "TcpChannel",
    "TcpCommand",
    "Torque",
    "commands",
    "convert",
    "describe",
    "describe_stream",
    "replies",
]

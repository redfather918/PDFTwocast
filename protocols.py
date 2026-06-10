"""
火山引擎播客大模型 WebSocket 二进制协议实现
基于 volcano-engine-podcast 开源项目
"""

import io
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, List

import websockets


class MsgType(IntEnum):
    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111
    ServerACK = AudioOnlyServer


class MsgTypeFlagBits(IntEnum):
    NoSeq = 0
    PositiveSeq = 0b1
    LastNoSeq = 0b10
    NegativeSeq = 0b11
    WithEvent = 0b100


class VersionBits(IntEnum):
    Version1 = 1


class HeaderSizeBits(IntEnum):
    HeaderSize4 = 1


class SerializationBits(IntEnum):
    Raw = 0
    JSON = 0b1
    Thrift = 0b11
    Custom = 0b1111


class CompressionBits(IntEnum):
    None_ = 0
    Gzip = 0b1
    Custom = 0b1111


class EventType(IntEnum):
    None_ = 0
    StartConnection = 1
    FinishConnection = 2
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52
    StartSession = 100
    CancelSession = 101
    FinishSession = 102
    SessionStarted = 150
    SessionCanceled = 151
    SessionFinished = 152
    SessionFailed = 153
    UsageResponse = 154
    TaskRequest = 200
    PodcastRoundStart = 360
    PodcastRoundResponse = 361
    PodcastRoundEnd = 362
    PodcastEnd = 363


@dataclass
class Message:
    version: VersionBits = VersionBits.Version1
    header_size: HeaderSizeBits = HeaderSizeBits.HeaderSize4
    type: MsgType = MsgType.Invalid
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    serialization: SerializationBits = SerializationBits.JSON
    compression: CompressionBits = CompressionBits.None_

    event: EventType = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        msg = cls()
        msg.unmarshal(data)
        return msg

    def marshal(self) -> bytes:
        buffer = io.BytesIO()
        header = [
            (self.version << 4) | self.header_size,
            (self.type << 4) | self.flag,
            (self.serialization << 4) | self.compression,
        ]
        header_size = 4 * self.header_size
        if padding := header_size - len(header):
            header.extend([0] * padding)
        buffer.write(bytes(header))

        writers = self._get_writers()
        for writer in writers:
            writer(buffer)
        return buffer.getvalue()

    def unmarshal(self, data: bytes) -> None:
        buffer = io.BytesIO(data)
        version_and_header_size = buffer.read(1)[0]
        self.version = VersionBits(version_and_header_size >> 4)
        self.header_size = HeaderSizeBits(version_and_header_size & 0x0F)

        type_and_flag = buffer.read(1)[0]
        self.type = MsgType(type_and_flag >> 4)
        self.flag = MsgTypeFlagBits(type_and_flag & 0x0F)

        s_and_c = buffer.read(1)[0]
        self.serialization = SerializationBits(s_and_c >> 4)
        self.compression = CompressionBits(s_and_c & 0x0F)

        header_size = 4 * self.header_size
        if padding_size := header_size - 3:
            buffer.read(padding_size)

        readers = self._get_readers()
        for reader in readers:
            reader(buffer)

    def _get_writers(self) -> List[Callable[[io.BytesIO], None]]:
        writers = []
        if self.flag == MsgTypeFlagBits.WithEvent:
            writers.extend([self._write_event, self._write_session_id])
        if self.type in [MsgType.FullClientRequest, MsgType.FullServerResponse,
                         MsgType.FrontEndResultServer, MsgType.AudioOnlyClient,
                         MsgType.AudioOnlyServer]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                writers.append(self._write_sequence)
        elif self.type == MsgType.Error:
            writers.append(self._write_error_code)
        writers.append(self._write_payload)
        return writers

    def _get_readers(self) -> List[Callable[[io.BytesIO], None]]:
        readers = []
        if self.type in [MsgType.FullClientRequest, MsgType.FullServerResponse,
                         MsgType.FrontEndResultServer, MsgType.AudioOnlyClient,
                         MsgType.AudioOnlyServer]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                readers.append(self._read_sequence)
        elif self.type == MsgType.Error:
            readers.append(self._read_error_code)
        if self.flag == MsgTypeFlagBits.WithEvent:
            readers.extend([self._read_event, self._read_session_id, self._read_connect_id])
        readers.append(self._read_payload)
        return readers

    def _write_event(self, b: io.BytesIO) -> None:
        b.write(struct.pack(">i", self.event))

    def _write_session_id(self, b: io.BytesIO) -> None:
        if self.event in [EventType.StartConnection, EventType.FinishConnection,
                          EventType.ConnectionStarted, EventType.ConnectionFailed]:
            return
        sid = self.session_id.encode("utf-8")
        b.write(struct.pack(">I", len(sid)))
        if sid:
            b.write(sid)

    def _write_sequence(self, b: io.BytesIO) -> None:
        b.write(struct.pack(">i", self.sequence))

    def _write_error_code(self, b: io.BytesIO) -> None:
        b.write(struct.pack(">I", self.error_code))

    def _write_payload(self, b: io.BytesIO) -> None:
        size = len(self.payload)
        b.write(struct.pack(">I", size))
        b.write(self.payload)

    def _read_event(self, b: io.BytesIO) -> None:
        d = b.read(4)
        if d:
            self.event = EventType(struct.unpack(">i", d)[0])

    def _read_session_id(self, b: io.BytesIO) -> None:
        if self.event in [EventType.StartConnection, EventType.FinishConnection,
                          EventType.ConnectionStarted, EventType.ConnectionFailed,
                          EventType.ConnectionFinished]:
            return
        d = b.read(4)
        if d:
            size = struct.unpack(">I", d)[0]
            if size:
                self.session_id = b.read(size).decode("utf-8")

    def _read_connect_id(self, b: io.BytesIO) -> None:
        if self.event in [EventType.ConnectionStarted, EventType.ConnectionFailed,
                          EventType.ConnectionFinished]:
            d = b.read(4)
            if d:
                size = struct.unpack(">I", d)[0]
                if size:
                    self.connect_id = b.read(size).decode("utf-8")

    def _read_sequence(self, b: io.BytesIO) -> None:
        d = b.read(4)
        if d:
            self.sequence = struct.unpack(">i", d)[0]

    def _read_error_code(self, b: io.BytesIO) -> None:
        d = b.read(4)
        if d:
            self.error_code = struct.unpack(">I", d)[0]

    def _read_payload(self, b: io.BytesIO) -> None:
        d = b.read(4)
        if d:
            size = struct.unpack(">I", d)[0]
            if size:
                self.payload = b.read(size)


async def receive_message(ws) -> Message:
    data = await ws.recv()
    if isinstance(data, bytes):
        return Message.from_bytes(data)
    raise ValueError(f"Unexpected text message: {data}")


async def wait_for_event(ws, msg_type: MsgType, event_type: EventType) -> Message:
    msg = await receive_message(ws)
    if msg.type != msg_type or msg.event != event_type:
        raise ValueError(f"Expected {msg_type}/{event_type}, got {msg.type}/{msg.event}: {msg}")
    return msg


async def start_connection(ws) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.StartConnection
    msg.payload = b"{}"
    await ws.send(msg.marshal())


async def finish_connection(ws) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.FinishConnection
    msg.payload = b"{}"
    await ws.send(msg.marshal())


async def start_session(ws, payload: bytes, session_id: str) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.StartSession
    msg.session_id = session_id
    msg.payload = payload
    await ws.send(msg.marshal())


async def finish_session(ws, session_id: str) -> None:
    msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
    msg.event = EventType.FinishSession
    msg.session_id = session_id
    msg.payload = b"{}"
    await ws.send(msg.marshal())

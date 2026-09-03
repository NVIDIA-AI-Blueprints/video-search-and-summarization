# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import re
import struct
import unittest
from unittest import mock

from vss_agent_gateway.connectors.websocket_client import (
    WebSocketError,
    WebSocketProtocolError,
    WebSocketTimeoutError,
    connect_websocket,
)

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _server_frame(opcode: int, payload: bytes, *, final: bool = True) -> bytes:
    first = (0x80 if final else 0) | opcode
    if len(payload) < 126:
        return bytes((first, len(payload))) + payload
    if len(payload) <= 0xFFFF:
        return bytes((first, 126)) + struct.pack("!H", len(payload)) + payload
    return bytes((first, 127)) + struct.pack("!Q", len(payload)) + payload


def _decode_client_frame(frame: bytes) -> tuple[int, bytes]:
    opcode = frame[0] & 0x0F
    assert frame[1] & 0x80
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", frame[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        length = struct.unpack("!Q", frame[offset : offset + 8])[0]
        offset += 8
    mask = frame[offset : offset + 4]
    offset += 4
    payload = bytes(
        value ^ mask[index % 4]
        for index, value in enumerate(frame[offset : offset + length])
    )
    return opcode, payload


class _FakeSocket:
    def __init__(
        self,
        frames: bytes = b"",
        *,
        valid_accept: bool = True,
    ) -> None:
        self.frames = frames
        self.valid_accept = valid_accept
        self.incoming = bytearray()
        self.writes: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False
        self.upgraded = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, value: bytes) -> None:
        self.writes.append(value)
        if self.upgraded:
            return
        self.upgraded = True
        match = re.search(rb"\r\nSec-WebSocket-Key: ([^\r\n]+)", value)
        assert match is not None
        key = match.group(1)
        accept = base64.b64encode(
            hashlib.sha1(key + _GUID, usedforsecurity=False).digest()
        )
        if not self.valid_accept:
            accept = b"invalid"
        response = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: keep-alive, Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        self.incoming.extend(response + self.frames)

    def recv(self, length: int) -> bytes:
        if not self.incoming:
            raise TimeoutError("fixture timeout")
        value = bytes(self.incoming[:length])
        del self.incoming[:length]
        return value

    def shutdown(self, _how: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class WebSocketClientTest(unittest.TestCase):
    def _connect(
        self,
        fake: _FakeSocket,
        *,
        max_message_bytes: int = 1024,
    ):
        with mock.patch(
            "vss_agent_gateway.connectors.websocket_client.socket.create_connection",
            return_value=fake,
        ):
            return connect_websocket(
                "ws://openclaw.test:18789/gateway",
                timeout=3,
                headers={"X-VSS-Test": "yes"},
                max_message_bytes=max_message_bytes,
            )

    def test_upgrade_send_and_receive_text(self) -> None:
        fake = _FakeSocket(_server_frame(0x1, b"hello"))
        connection = self._connect(fake)

        request = fake.writes[0]
        self.assertIn(b"GET /gateway HTTP/1.1\r\n", request)
        self.assertIn(b"Host: openclaw.test:18789\r\n", request)
        self.assertIn(b"X-VSS-Test: yes\r\n", request)
        self.assertEqual(connection.recv(), "hello")

        connection.send("world")
        opcode, payload = _decode_client_frame(fake.writes[-1])
        self.assertEqual(opcode, 0x1)
        self.assertEqual(payload, b"world")
        connection.close()
        self.assertTrue(fake.closed)

    def test_ping_is_answered_while_fragmented_text_is_reassembled(self) -> None:
        fake = _FakeSocket(
            _server_frame(0x9, b"ping")
            + _server_frame(0x1, b"hel", final=False)
            + _server_frame(0x0, b"lo")
        )
        connection = self._connect(fake)

        self.assertEqual(connection.recv(), "hello")
        opcode, payload = _decode_client_frame(fake.writes[-1])
        self.assertEqual(opcode, 0xA)
        self.assertEqual(payload, b"ping")

    def test_invalid_upgrade_accept_is_rejected_and_closed(self) -> None:
        fake = _FakeSocket(valid_accept=False)
        with self.assertRaises(WebSocketProtocolError):
            self._connect(fake)
        self.assertTrue(fake.closed)

    def test_receive_timeout_is_normalized(self) -> None:
        connection = self._connect(_FakeSocket())
        with self.assertRaises(WebSocketTimeoutError):
            connection.recv()

    def test_oversized_message_is_rejected_before_allocation(self) -> None:
        fake = _FakeSocket(_server_frame(0x1, b"12345"))
        connection = self._connect(fake, max_message_bytes=4)
        with self.assertRaises(WebSocketProtocolError):
            connection.recv()

    def test_oversized_fragmented_message_is_rejected(self) -> None:
        fake = _FakeSocket(
            _server_frame(0x1, b"123", final=False) + _server_frame(0x0, b"45")
        )
        connection = self._connect(fake, max_message_bytes=4)
        with self.assertRaises(WebSocketProtocolError):
            connection.recv()

    def test_rejects_header_injection_before_connecting(self) -> None:
        with (
            mock.patch(
                "vss_agent_gateway.connectors.websocket_client.socket.create_connection"
            ) as create_connection,
            self.assertRaises(WebSocketError),
        ):
            connect_websocket(
                "ws://openclaw.test/",
                timeout=3,
                headers={"X-Test": "safe\r\nInjected: true"},
            )
        create_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()

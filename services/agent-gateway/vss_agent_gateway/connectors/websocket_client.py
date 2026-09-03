# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small RFC 6455 client for the gateway's JSON WebSocket transport.

The agent gateway intentionally has no third-party runtime packages. This
module implements only the client features its connectors need: a direct
``ws``/``wss`` upgrade, text and binary messages, fragmentation, ping/pong,
timeouts, and a close handshake. Extensions and subprotocols are deliberately
not negotiated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import ssl
import struct
import threading
from collections.abc import Mapping
from urllib.parse import urlsplit

_ACCEPT_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_HANDSHAKE_BYTES = 65_536
_DEFAULT_MAX_MESSAGE_BYTES = 26_214_400
_HEADER_NAME_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_RESERVED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "sec-websocket-accept",
        "sec-websocket-extensions",
        "sec-websocket-key",
        "sec-websocket-protocol",
        "sec-websocket-version",
        "upgrade",
        "user-agent",
    }
)


class WebSocketError(RuntimeError):
    """Base error for WebSocket transport failures."""


class WebSocketTimeoutError(WebSocketError):
    """The socket exceeded its configured timeout."""


class WebSocketConnectionClosedError(WebSocketError):
    """The peer closed the WebSocket or underlying connection."""


class WebSocketProtocolError(WebSocketError):
    """The peer violated the negotiated RFC 6455 protocol."""


def _read_http_upgrade(
    connection: socket.socket,
) -> tuple[bytes, bytes]:
    response = bytearray()
    while b"\r\n\r\n" not in response:
        if len(response) >= _MAX_HANDSHAKE_BYTES:
            raise WebSocketProtocolError("WebSocket upgrade headers are oversized")
        try:
            chunk = connection.recv(min(4096, _MAX_HANDSHAKE_BYTES - len(response)))
        except TimeoutError as error:
            raise WebSocketTimeoutError("WebSocket upgrade timed out") from error
        except OSError as error:
            raise WebSocketError("WebSocket upgrade failed") from error
        if not chunk:
            raise WebSocketConnectionClosedError(
                "connection closed during WebSocket upgrade"
            )
        response.extend(chunk)
    header, remainder = bytes(response).split(b"\r\n\r\n", 1)
    return header, remainder


def _parse_upgrade_headers(raw: bytes) -> tuple[int, dict[str, list[str]]]:
    try:
        lines = raw.decode("iso-8859-1").split("\r\n")
    except UnicodeDecodeError as error:  # pragma: no cover - ISO-8859-1 is total
        raise WebSocketProtocolError("invalid WebSocket upgrade response") from error
    status = lines[0].split(" ", 2)
    if len(status) < 2 or not status[0].startswith("HTTP/"):
        raise WebSocketProtocolError("invalid WebSocket upgrade status line")
    try:
        status_code = int(status[1])
    except ValueError as error:
        raise WebSocketProtocolError("invalid WebSocket upgrade status") from error
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if not line or line[:1].isspace() or ":" not in line:
            raise WebSocketProtocolError("invalid WebSocket upgrade header")
        name, value = line.split(":", 1)
        normalized = name.strip().lower()
        if not normalized:
            raise WebSocketProtocolError("invalid WebSocket upgrade header name")
        headers.setdefault(normalized, []).append(value.strip())
    return status_code, headers


def _header_tokens(headers: dict[str, list[str]], name: str) -> set[str]:
    return {
        token.strip().lower()
        for value in headers.get(name, [])
        for token in value.split(",")
        if token.strip()
    }


def _validate_upgrade(raw: bytes, key: str) -> None:
    status, headers = _parse_upgrade_headers(raw)
    if status != 101:
        raise WebSocketProtocolError(f"WebSocket upgrade returned HTTP status {status}")
    if "websocket" not in _header_tokens(headers, "upgrade"):
        raise WebSocketProtocolError("WebSocket upgrade header is missing")
    if "upgrade" not in _header_tokens(headers, "connection"):
        raise WebSocketProtocolError("WebSocket connection upgrade was not accepted")
    accept_values = headers.get("sec-websocket-accept", [])
    expected = base64.b64encode(
        # SHA-1 is mandated by RFC 6455 here; it is not used for security.
        hashlib.sha1(key.encode("ascii") + _ACCEPT_GUID, usedforsecurity=False).digest()
    ).decode("ascii")
    if len(accept_values) != 1 or not hmac.compare_digest(accept_values[0], expected):
        raise WebSocketProtocolError("WebSocket accept value is invalid")
    if headers.get("sec-websocket-extensions"):
        raise WebSocketProtocolError("unsolicited WebSocket extension")
    if headers.get("sec-websocket-protocol"):
        raise WebSocketProtocolError("unsolicited WebSocket subprotocol")


def _endpoint_parts(endpoint: str) -> tuple[str, int, str, bool]:
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        secure = parsed.scheme == "wss"
        if parsed.scheme not in {"ws", "wss"} or not host:
            raise ValueError
        port = parsed.port or (443 if secure else 80)
    except ValueError as error:
        raise WebSocketError("invalid WebSocket endpoint") from error
    if parsed.username or parsed.password or parsed.fragment:
        raise WebSocketError("invalid WebSocket endpoint")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    if not target.startswith("/") or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in target
    ):
        raise WebSocketError("invalid WebSocket request target")
    try:
        encoded_host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise WebSocketError("invalid WebSocket endpoint host") from error
    return encoded_host, port, target, secure


def _host_header(host: str, port: int, secure: bool) -> str:
    rendered = f"[{host}]" if ":" in host else host
    default_port = 443 if secure else 80
    return rendered if port == default_port else f"{rendered}:{port}"


class WebSocketConnection:
    """Blocking, thread-safe-for-send RFC 6455 client connection."""

    def __init__(
        self,
        connection: socket.socket,
        buffered: bytes = b"",
        *,
        max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        self._socket = connection
        self._buffer = bytearray(buffered)
        self._max_message_bytes = max_message_bytes
        self._send_lock = threading.Lock()
        self._closed = False

    def settimeout(self, timeout: float) -> None:
        self._socket.settimeout(timeout)

    def _read_exact(self, length: int) -> bytes:
        while len(self._buffer) < length:
            try:
                chunk = self._socket.recv(max(4096, length - len(self._buffer)))
            except TimeoutError as error:
                raise WebSocketTimeoutError("WebSocket receive timed out") from error
            except OSError as error:
                raise WebSocketConnectionClosedError(
                    "WebSocket connection ended"
                ) from error
            if not chunk:
                raise WebSocketConnectionClosedError("WebSocket connection ended")
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:length])
        del self._buffer[:length]
        return value

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        final = bool(first & 0x80)
        if first & 0x70:
            raise WebSocketProtocolError("unsupported WebSocket extension bits")
        opcode = first & 0x0F
        if second & 0x80:
            raise WebSocketProtocolError("server WebSocket frames must not be masked")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
            if length < 126:
                raise WebSocketProtocolError("non-canonical WebSocket frame length")
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
            if length < 65_536 or length & (1 << 63):
                raise WebSocketProtocolError("invalid WebSocket frame length")
        if opcode >= 0x8:
            if not final or length > 125:
                raise WebSocketProtocolError("invalid WebSocket control frame")
        elif length > self._max_message_bytes:
            raise WebSocketProtocolError("WebSocket message is oversized")
        return final, opcode, self._read_exact(length)

    @staticmethod
    def _encoded_frame(opcode: int, payload: bytes) -> bytes:
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return header + mask + masked

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        frame = self._encoded_frame(opcode, payload)
        with self._send_lock:
            if self._closed:
                raise WebSocketConnectionClosedError("WebSocket connection is closed")
            try:
                self._socket.sendall(frame)
            except OSError as error:
                raise WebSocketConnectionClosedError("WebSocket send failed") from error

    def send(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("WebSocketConnection.send requires text")
        payload = value.encode("utf-8")
        if len(payload) > self._max_message_bytes:
            raise WebSocketProtocolError("WebSocket message is oversized")
        self._send_frame(0x1, payload)

    def recv(self) -> str | bytes:
        message_opcode: int | None = None
        fragments = bytearray()
        while True:
            final, opcode, payload = self._read_frame()
            if opcode == 0x8:
                if len(payload) == 1:
                    raise WebSocketProtocolError("invalid WebSocket close payload")
                try:
                    self._send_frame(0x8, payload)
                except WebSocketError:
                    pass
                self._terminate()
                raise WebSocketConnectionClosedError("WebSocket peer closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in {0x1, 0x2}:
                if message_opcode is not None:
                    raise WebSocketProtocolError(
                        "new WebSocket message interrupted fragmentation"
                    )
                message_opcode = opcode
            elif opcode == 0x0:
                if message_opcode is None:
                    raise WebSocketProtocolError(
                        "unexpected WebSocket continuation frame"
                    )
            else:
                raise WebSocketProtocolError("unsupported WebSocket frame opcode")
            if len(fragments) + len(payload) > self._max_message_bytes:
                raise WebSocketProtocolError("WebSocket message is oversized")
            fragments.extend(payload)
            if not final:
                continue
            result = bytes(fragments)
            if message_opcode == 0x2:
                return result
            try:
                return result.decode("utf-8")
            except UnicodeDecodeError as error:
                raise WebSocketProtocolError(
                    "WebSocket text frame is not UTF-8"
                ) from error

    def _terminate(self) -> None:
        with self._send_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()

    def close(self) -> None:
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except WebSocketError:
            pass
        self._terminate()


def connect_websocket(
    endpoint: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
) -> WebSocketConnection:
    """Open and validate a direct RFC 6455 connection."""

    host, port, target, secure = _endpoint_parts(endpoint)
    supplied_headers = dict(headers or {})
    for name, value in supplied_headers.items():
        if not name or len(name) > 128 or not set(name) <= _HEADER_NAME_CHARACTERS:
            raise WebSocketError("invalid WebSocket header name")
        if name.lower() in _RESERVED_HEADERS:
            raise WebSocketError("reserved WebSocket header override")
        if len(value) > 8_192 or any(
            ord(character) < 0x20 or ord(character) > 0x7E for character in value
        ):
            raise WebSocketError("invalid WebSocket header value")
    connection: socket.socket | None = None
    try:
        connection = socket.create_connection((host, port), timeout=timeout)
        if secure:
            connection = ssl.create_default_context().wrap_socket(
                connection, server_hostname=host
            )
        connection.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_headers = {
            "Host": _host_header(host, port, secure),
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": key,
            "Sec-WebSocket-Version": "13",
            "User-Agent": "vss-agent-gateway/1.0",
            **supplied_headers,
        }
        lines = [f"GET {target} HTTP/1.1"]
        lines.extend(f"{name}: {value}" for name, value in request_headers.items())
        connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        raw_headers, buffered = _read_http_upgrade(connection)
        _validate_upgrade(raw_headers, key)
        return WebSocketConnection(
            connection,
            buffered,
            max_message_bytes=max_message_bytes,
        )
    except WebSocketError:
        if connection is not None:
            connection.close()
        raise
    except OSError as error:
        if connection is not None:
            connection.close()
        raise WebSocketError("WebSocket connection failed") from error

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Native OpenClaw Gateway protocol connector.

The connector owns only protocol translation. OpenClaw still owns the agent,
skills, tools, policy, model, and durable session. Structured tool results are
inspected privately for VSS UI artifacts but are never replayed to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import websocket
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ..config import GatewayConfig
from ..contract import ConnectorEvent, CreateRunRequest
from ..json_codec import strict_json_loads
from .base import Connector, ConnectorError

PROTOCOL_VERSION = 4
CLIENT_ID = "openclaw-control-ui"
CLIENT_MODE = "webchat"
CLIENT_PLATFORM = "linux"
CLIENT_DEVICE_FAMILY = "server"
REQUESTED_SCOPES = ("operator.read", "operator.write")
CLIENT_CAPABILITIES = ("tool-events", "session-scoped-events")
MAX_FRAME_CHARS = 26_214_400
DEVICE_STATE_VERSION = 1
DEVICE_STATE_FILENAME = "openclaw-device.json"


@dataclass(frozen=True, slots=True)
class _DeviceState:
    private_key: Ed25519PrivateKey
    device_id: str
    public_key: str
    device_token: str | None = None


@dataclass(frozen=True, slots=True)
class _ActiveRun:
    socket: object
    session_key: str
    upstream_run_id: str


class _HandshakeRejected(RuntimeError):
    def __init__(self, error: object) -> None:
        super().__init__("OpenClaw rejected the connection")
        self.error = error


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or len(value) > 4096:
        raise ValueError("invalid base64url value")
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url value")
    return decoded


def _valid_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if (
        not token
        or len(token) > 8_192
        or any(character.isspace() for character in token)
    ):
        return None
    return token


class OpenClawWebSocketConnector(Connector):
    """Translate OpenClaw protocol-v4 events into the VSS run contract."""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._endpoint = f"{config.backend_url}{config.backend_path}"
        self._lock = threading.RLock()
        self._active_runs: dict[str, _ActiveRun] = {}
        self._state_path = Path(config.backend_state_dir) / DEVICE_STATE_FILENAME
        self._device = self._load_or_create_device()

    @property
    def protocol(self) -> str:
        return "openclaw-ws"

    @property
    def capabilities(self) -> dict[str, object]:
        return {
            **super().capabilities,
            "tool_events": "native",
            "cancellation": "native",
        }

    def _load_or_create_device(self) -> _DeviceState:
        directory = self._state_path.parent
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_stat = directory.stat()
        except OSError as error:
            raise ConnectorError(
                "OpenClaw device state directory is unavailable",
                code="backend_state_error",
            ) from error
        if not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_mode & 0o077:
            raise ConnectorError(
                "OpenClaw device state directory must be private (mode 0700)",
                code="backend_state_error",
            )

        try:
            return self._read_device()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            raise ConnectorError(
                "OpenClaw device state is invalid",
                code="backend_state_error",
            ) from error

        private_key = Ed25519PrivateKey.generate()
        state = self._device_from_private_key(private_key)
        try:
            self._write_device(state, replace=False)
            return state
        except FileExistsError:
            # Another startup won the atomic create race.
            try:
                return self._read_device()
            except (OSError, ValueError, TypeError) as error:
                raise ConnectorError(
                    "OpenClaw device state is invalid",
                    code="backend_state_error",
                ) from error
        except OSError as error:
            raise ConnectorError(
                "OpenClaw device state could not be persisted",
                code="backend_state_error",
            ) from error

    @staticmethod
    def _device_from_private_key(
        private_key: Ed25519PrivateKey,
        device_token: str | None = None,
    ) -> _DeviceState:
        public_raw = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        return _DeviceState(
            private_key=private_key,
            device_id=hashlib.sha256(public_raw).hexdigest(),
            public_key=_base64url_encode(public_raw),
            device_token=device_token,
        )

    def _read_device(self) -> _DeviceState:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._state_path, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_mode & 0o077:
                raise ValueError("device state must be a private regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(32_769)
            if len(raw) > 32_768:
                raise ValueError("device state is oversized")
        finally:
            os.close(descriptor)
        payload = strict_json_loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != DEVICE_STATE_VERSION
        ):
            raise ValueError("unsupported device state")
        encoded_private_key = payload.get("private_key")
        if not isinstance(encoded_private_key, str):
            raise ValueError("device state has no private key")
        private_raw = _base64url_decode(encoded_private_key)
        if len(private_raw) != 32:
            raise ValueError("device private key has the wrong length")
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
        raw_token = payload.get("device_token")
        device_token = None if raw_token is None else _valid_token(raw_token)
        if raw_token is not None and device_token is None:
            raise ValueError("device token is invalid")
        return self._device_from_private_key(private_key, device_token)

    def _serialized_device(self, state: _DeviceState) -> bytes:
        private_raw = state.private_key.private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )
        payload: dict[str, object] = {
            "version": DEVICE_STATE_VERSION,
            "private_key": _base64url_encode(private_raw),
        }
        if state.device_token:
            payload["device_token"] = state.device_token
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode()

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - defensive OS boundary
                raise OSError("device state write made no progress")
            view = view[written:]

    def _write_device(self, state: _DeviceState, *, replace: bool) -> None:
        if not replace:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._state_path, flags, 0o600)
            try:
                self._write_all(descriptor, self._serialized_device(state))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return

        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".openclaw-device.", dir=self._state_path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            self._write_all(descriptor, self._serialized_device(state))
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, self._state_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    def _save_device_token(self, token: str) -> None:
        with self._lock:
            if token == self._device.device_token:
                return
            state = self._device_from_private_key(self._device.private_key, token)
            try:
                self._write_device(state, replace=True)
            except OSError as error:
                raise ConnectorError(
                    "OpenClaw device token could not be persisted",
                    code="backend_state_error",
                ) from error
            self._device = state

    def _clear_device_token(self) -> None:
        with self._lock:
            if self._device.device_token is None:
                return
            state = self._device_from_private_key(self._device.private_key)
            try:
                self._write_device(state, replace=True)
            except OSError as error:
                raise ConnectorError(
                    "OpenClaw device token could not be cleared",
                    code="backend_state_error",
                ) from error
            self._device = state

    @staticmethod
    def _device_auth_payload(
        state: _DeviceState,
        *,
        signed_at: int,
        nonce: str,
        credential: str | None,
    ) -> str:
        return "|".join(
            (
                "v3",
                state.device_id,
                CLIENT_ID,
                CLIENT_MODE,
                "operator",
                ",".join(REQUESTED_SCOPES),
                str(signed_at),
                credential or "",
                nonce,
                CLIENT_PLATFORM,
                CLIENT_DEVICE_FAMILY,
            )
        )

    def _connect_params(
        self,
        *,
        signed_at: int,
        nonce: str,
        credential: str | None,
    ) -> dict[str, object]:
        with self._lock:
            state = self._device
        signature_payload = self._device_auth_payload(
            state,
            signed_at=signed_at,
            nonce=nonce,
            credential=credential,
        )
        auth: dict[str, str] = {}
        if credential:
            auth["token"] = credential
        params: dict[str, object] = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": CLIENT_ID,
                "version": "vss-agent-gateway/1.0",
                "platform": CLIENT_PLATFORM,
                "mode": CLIENT_MODE,
                "deviceFamily": CLIENT_DEVICE_FAMILY,
            },
            "role": "operator",
            "scopes": list(REQUESTED_SCOPES),
            "caps": list(CLIENT_CAPABILITIES),
            "commands": [],
            "permissions": {},
            "locale": "en-US",
            "userAgent": "vss-agent-gateway/1.0",
            "device": {
                "id": state.device_id,
                "publicKey": state.public_key,
                "signature": _base64url_encode(
                    state.private_key.sign(signature_payload.encode("utf-8"))
                ),
                "signedAt": signed_at,
                "nonce": nonce,
            },
        }
        if auth:
            params["auth"] = auth
        return params

    def _open_socket(self) -> object:
        headers = [
            f"{key}: {value}" for key, value in self._config.backend_headers.items()
        ]
        try:
            connection = websocket.create_connection(
                self._endpoint,
                timeout=min(self._config.request_timeout_seconds, 15.0),
                header=headers,
                enable_multithread=True,
            )
            connection.settimeout(self._config.request_timeout_seconds)
            return connection
        except (
            OSError,
            websocket.WebSocketException,
        ) as error:
            raise ConnectorError(
                "OpenClaw Gateway is unreachable",
                code="backend_unreachable",
                retryable=True,
            ) from error

    @staticmethod
    def _close_socket(connection: object) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    @staticmethod
    def _recv(connection: object) -> dict[str, object]:
        try:
            raw = connection.recv()  # type: ignore[attr-defined]
        except websocket.WebSocketTimeoutException as error:
            raise ConnectorError(
                "OpenClaw Gateway timed out",
                code="backend_timeout",
                retryable=True,
            ) from error
        except (OSError, websocket.WebSocketException) as error:
            raise ConnectorError(
                "OpenClaw Gateway stream ended unexpectedly",
                code="backend_stream_error",
                retryable=True,
            ) from error
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ConnectorError(
                    "OpenClaw Gateway emitted a non-UTF-8 frame",
                    code="invalid_backend_event",
                ) from error
        if not isinstance(raw, str) or len(raw) > MAX_FRAME_CHARS:
            raise ConnectorError(
                "OpenClaw Gateway emitted an invalid or oversized frame",
                code="invalid_backend_event",
            )
        try:
            payload = strict_json_loads(raw)
        except ValueError as error:
            raise ConnectorError(
                "OpenClaw Gateway emitted invalid JSON",
                code="invalid_backend_event",
            ) from error
        if not isinstance(payload, dict):
            raise ConnectorError(
                "OpenClaw Gateway emitted a non-object frame",
                code="invalid_backend_event",
            )
        return payload

    @staticmethod
    def _send(connection: object, frame: dict[str, object]) -> None:
        try:
            connection.send(  # type: ignore[attr-defined]
                json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
            )
        except (OSError, websocket.WebSocketException) as error:
            raise ConnectorError(
                "OpenClaw Gateway stream ended unexpectedly",
                code="backend_stream_error",
                retryable=True,
            ) from error

    def _request(
        self, connection: object, method: str, params: dict[str, object]
    ) -> str:
        request_id = str(uuid.uuid4())
        self._send(
            connection,
            {"type": "req", "id": request_id, "method": method, "params": params},
        )
        return request_id

    def _await_response(
        self,
        connection: object,
        request_id: str,
        *,
        pending_events: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        while True:
            frame = self._recv(connection)
            if frame.get("type") == "event":
                if pending_events is not None:
                    pending_events.append(frame)
                continue
            if frame.get("type") != "res" or frame.get("id") != request_id:
                continue
            if frame.get("ok") is not True:
                raise _HandshakeRejected(frame.get("error"))
            payload = frame.get("payload")
            if not isinstance(payload, dict):
                raise ConnectorError(
                    "OpenClaw Gateway returned an invalid RPC response",
                    code="invalid_backend_response",
                )
            return payload

    @staticmethod
    def _handshake_error(error: object) -> ConnectorError:
        details = error.get("details") if isinstance(error, dict) else None
        detail_code = details.get("code") if isinstance(details, dict) else None
        if detail_code == "PAIRING_REQUIRED":
            request_id = details.get("requestId") if isinstance(details, dict) else None
            suffix = ""
            if (
                isinstance(request_id, str)
                and request_id
                and len(request_id) <= 128
                and not any(ord(character) < 32 for character in request_id)
            ):
                suffix = f" (request {request_id})"
            return ConnectorError(
                "OpenClaw device pairing is required"
                f"{suffix}; approve this gateway device and retry",
                code="backend_pairing_required",
            )
        if detail_code in {
            "AUTH_TOKEN_MISMATCH",
            "AUTH_SCOPE_MISMATCH",
            "DEVICE_AUTH_SIGNATURE_INVALID",
        }:
            return ConnectorError(
                "OpenClaw Gateway authentication failed",
                code="backend_auth_error",
            )
        return ConnectorError(
            "OpenClaw Gateway rejected the connection",
            code="backend_handshake_error",
        )

    @staticmethod
    def _stale_device_token(error: object) -> bool:
        if not isinstance(error, dict) or not isinstance(error.get("details"), dict):
            return False
        return error["details"].get("code") in {
            "AUTH_TOKEN_MISMATCH",
            "AUTH_IDENTITY_HEADER_REQUIRED",
        }

    def _connect_once(self, credential: str | None) -> object:
        connection = self._open_socket()
        try:
            challenge = self._recv(connection)
            challenge_payload = challenge.get("payload")
            if (
                challenge.get("type") != "event"
                or challenge.get("event") != "connect.challenge"
                or not isinstance(challenge_payload, dict)
            ):
                raise ConnectorError(
                    "OpenClaw Gateway did not send a connect challenge",
                    code="invalid_backend_handshake",
                )
            nonce = challenge_payload.get("nonce")
            signed_at = challenge_payload.get("ts")
            if (
                not isinstance(nonce, str)
                or not nonce
                or len(nonce) > 4096
                or not isinstance(signed_at, int)
                or isinstance(signed_at, bool)
                or signed_at < 0
            ):
                raise ConnectorError(
                    "OpenClaw Gateway sent an invalid connect challenge",
                    code="invalid_backend_handshake",
                )
            connect_id = self._request(
                connection,
                "connect",
                self._connect_params(
                    signed_at=signed_at,
                    nonce=nonce,
                    credential=credential,
                ),
            )
            hello = self._await_response(connection, connect_id)
            if hello.get("type") != "hello-ok" or hello.get("protocol") != 4:
                raise ConnectorError(
                    "OpenClaw Gateway negotiated an unsupported protocol",
                    code="unsupported_backend_protocol",
                )
            auth = hello.get("auth")
            scopes = auth.get("scopes") if isinstance(auth, dict) else None
            if (
                not isinstance(scopes, list)
                or any(not isinstance(scope, str) for scope in scopes)
                or not set(REQUESTED_SCOPES) <= set(scopes)
            ):
                raise ConnectorError(
                    "OpenClaw Gateway did not grant chat read/write scopes",
                    code="backend_scope_error",
                )
            features = hello.get("features")
            methods = features.get("methods") if isinstance(features, dict) else None
            events = features.get("events") if isinstance(features, dict) else None
            if (
                not isinstance(methods, list)
                or any(not isinstance(method, str) for method in methods)
                or not {"chat.send", "chat.abort"} <= set(methods)
                or not isinstance(events, list)
                or any(not isinstance(event, str) for event in events)
                or "chat" not in events
                or not ({"agent", "session.tool"} & set(events))
            ):
                raise ConnectorError(
                    "OpenClaw Gateway lacks required chat or tool-event capabilities",
                    code="unsupported_backend_protocol",
                )
            issued_token = auth.get("deviceToken") if isinstance(auth, dict) else None
            normalized_token = _valid_token(issued_token)
            if issued_token is not None and normalized_token is None:
                raise ConnectorError(
                    "OpenClaw Gateway returned an invalid device token",
                    code="invalid_backend_handshake",
                )
            if normalized_token:
                self._save_device_token(normalized_token)
            return connection
        except Exception:
            self._close_socket(connection)
            raise

    def _connect(self) -> object:
        with self._lock:
            stored_token = self._device.device_token
        if stored_token:
            try:
                return self._connect_once(stored_token)
            except _HandshakeRejected as error:
                if not self._stale_device_token(error.error):
                    raise self._handshake_error(error.error) from error
                self._clear_device_token()
        try:
            return self._connect_once(self._config.backend_token)
        except _HandshakeRejected as error:
            raise self._handshake_error(error.error) from error

    def _session_key(self, thread_id: str) -> str:
        secret = (
            self._config.gateway_token
            or self._config.backend_token
            or "vss-agent-gateway"
        )
        digest = hmac.new(
            secret.encode("utf-8"),
            f"vss-ui:{thread_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:40]
        return f"agent:main:vss-ui-{digest}"

    @staticmethod
    def _message(request: CreateRunRequest) -> str:
        if (
            request.instructions is None
            and len(request.input) == 1
            and request.input[0].role == "user"
        ):
            return request.input[0].content
        parts: list[str] = []
        if request.instructions:
            parts.append(f"VSS UI instructions:\n{request.instructions}")
        parts.extend(
            f"{message.role.capitalize()}:\n{message.content}"
            for message in request.input
        )
        return "\n\n".join(parts)

    @staticmethod
    def _safe_identifier(value: object, fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 256
            or any(ord(character) < 32 for character in normalized)
        ):
            return fallback
        return normalized

    @staticmethod
    def _final_text(payload: dict[str, object]) -> str | None:
        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return None
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return "".join(text_parts) or None

    def _normalize_event(
        self,
        frame: dict[str, object],
        *,
        session_key: str,
        upstream_run_id: str,
        started_tools: set[str],
        completed_tools: set[str],
        tool_names: dict[str, str],
        saw_text: list[bool],
    ) -> tuple[list[ConnectorEvent], bool]:
        if frame.get("type") != "event":
            return [], False
        event_name = frame.get("event")
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return [], False
        if payload.get("sessionKey") != session_key:
            return [], False
        event_run_id = payload.get("runId")
        if isinstance(event_run_id, str) and event_run_id != upstream_run_id:
            return [], False

        if event_name == "chat":
            state = payload.get("state")
            delta = payload.get("deltaText")
            if state == "delta" and isinstance(delta, str) and delta:
                saw_text[0] = True
                return [ConnectorEvent("message.delta", {"delta": delta})], False
            if state == "final":
                final_text = self._final_text(payload) if not saw_text[0] else None
                events = (
                    [ConnectorEvent("message.delta", {"delta": final_text})]
                    if final_text
                    else []
                )
                return events, True
            if state in {"error", "failed"}:
                raise ConnectorError(
                    "OpenClaw agent run failed",
                    code="backend_run_failed",
                )
            if state in {"aborted", "cancelled"}:
                raise ConnectorError(
                    "OpenClaw agent run was aborted",
                    code="backend_run_aborted",
                )
            return [], False

        if event_name == "agent":
            if payload.get("stream") != "tool":
                return [], False
            tool_data = payload.get("data")
        elif event_name == "session.tool":
            tool_data = payload.get("data", payload)
        else:
            return [], False
        if not isinstance(tool_data, dict):
            return [], False

        fallback_id = f"tool-{payload.get('seq', 'unknown')}"
        tool_call_id = self._safe_identifier(
            tool_data.get("toolCallId") or tool_data.get("id"), fallback_id
        )
        name = self._safe_identifier(
            tool_data.get("name") or tool_data.get("tool"),
            tool_names.get(tool_call_id, "Agent tool"),
        )
        tool_names[tool_call_id] = name
        phase = str(
            tool_data.get("phase") or tool_data.get("status") or "start"
        ).lower()
        events: list[ConnectorEvent] = []
        if phase in {"start", "started", "running", "in_progress"}:
            if tool_call_id not in started_tools:
                started_tools.add(tool_call_id)
                events.append(
                    ConnectorEvent(
                        "tool.started",
                        {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "payload": "Running",
                        },
                    )
                )
            return events, False
        if phase in {"update", "delta", "progress"}:
            return [], False
        if phase not in {"result", "complete", "completed", "error", "failed"}:
            return [], False
        if tool_call_id in completed_tools:
            return [], False
        completed_tools.add(tool_call_id)
        if tool_call_id not in started_tools:
            started_tools.add(tool_call_id)
            events.append(
                ConnectorEvent(
                    "tool.started",
                    {
                        "tool_call_id": tool_call_id,
                        "name": name,
                        "payload": "Running",
                    },
                )
            )
        failed = phase in {"error", "failed"} or tool_data.get("isError") is True
        if failed:
            events.append(
                ConnectorEvent(
                    "tool.failed",
                    {
                        "tool_call_id": tool_call_id,
                        "name": name,
                        "error": "Tool failed in OpenClaw",
                    },
                )
            )
        else:
            result = tool_data.get("result")
            completed: dict[str, object] = {
                "tool_call_id": tool_call_id,
                "name": name,
                "payload": "Completed",
            }
            if result is not None:
                completed["_artifact_source"] = result
            events.append(ConnectorEvent("tool.completed", completed))
        return events, False

    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: Event,
    ) -> Iterator[ConnectorEvent]:
        connection = self._connect()
        session_key = self._session_key(request.thread_id)
        with self._lock:
            self._active_runs[run_id] = _ActiveRun(connection, session_key, run_id)
        try:
            pending_events: list[dict[str, object]] = []
            send_id = self._request(
                connection,
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": self._message(request),
                    "idempotencyKey": run_id,
                },
            )
            try:
                accepted = self._await_response(
                    connection,
                    send_id,
                    pending_events=pending_events,
                )
            except _HandshakeRejected as error:
                raise ConnectorError(
                    "OpenClaw rejected the chat request",
                    code="backend_request_rejected",
                ) from error
            upstream_run_id = self._safe_identifier(accepted.get("runId"), run_id)
            if accepted.get("status") not in {None, "started", "accepted"}:
                raise ConnectorError(
                    "OpenClaw did not start the agent run",
                    code="backend_request_rejected",
                )
            with self._lock:
                self._active_runs[run_id] = _ActiveRun(
                    connection, session_key, upstream_run_id
                )

            started_tools: set[str] = set()
            completed_tools: set[str] = set()
            tool_names: dict[str, str] = {}
            saw_text = [False]
            frames: Iterator[dict[str, object]] = iter(pending_events)
            while True:
                for frame in frames:
                    if cancel_event.is_set():
                        return
                    events, terminal = self._normalize_event(
                        frame,
                        session_key=session_key,
                        upstream_run_id=upstream_run_id,
                        started_tools=started_tools,
                        completed_tools=completed_tools,
                        tool_names=tool_names,
                        saw_text=saw_text,
                    )
                    yield from events
                    if terminal:
                        return
                if cancel_event.is_set():
                    return
                frames = iter((self._recv(connection),))
        except ConnectorError:
            if cancel_event.is_set():
                return
            raise
        finally:
            with self._lock:
                active = self._active_runs.get(run_id)
                if active is not None and active.socket is connection:
                    self._active_runs.pop(run_id, None)
            self._close_socket(connection)

    def cancel(self, run_id: str) -> None:
        with self._lock:
            active = self._active_runs.get(run_id)
        if active is None:
            return
        try:
            self._request(
                active.socket,
                "chat.abort",
                {
                    "sessionKey": active.session_key,
                    "runId": active.upstream_run_id,
                },
            )
        except ConnectorError:
            pass
        finally:
            self._close_socket(active.socket)

#!/usr/bin/env python3
"""Validate shared-decode requests and proxy RT-CV's native REST API."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_CAMERA_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_STATUS_DIR = Path(os.getenv("RTVI_CV_IPC_STATUS_DIR", "/run/rtvi-cv-ipc-status"))
_SOCKET_ROOT = Path(
    os.getenv("RTVI_CV_IPC_SOCKET_ROOT", "/run/vss/frame-broker")
).resolve()
_STREAM_STATUS: dict[str, Path] = {}


def _shared_uri(camera_id: str, source: dict[str, Any]) -> str:
    required = {
        "type": "shared_decode",
        "protocol_version": "1.0",
        "transport": "cuda_ipc",
    }
    if any(source.get(key) != value for key, value in required.items()):
        raise ValueError("unsupported shared-decode protocol")
    if not isinstance(source.get("session_token"), str) or not source["session_token"]:
        raise ValueError("session_token is required")
    if not isinstance(source.get("consumer_id"), str) or not source["consumer_id"]:
        raise ValueError("consumer_id is required")
    device_id = source.get("device_id")
    if isinstance(device_id, bool) or not isinstance(device_id, int) or device_id < 0:
        raise ValueError("device_id must be a nonnegative integer")
    socket = Path(str(source.get("control_socket", "")))
    if not socket.is_absolute() or ".." in socket.parts:
        raise ValueError("control_socket must be an absolute normalized path")
    resolved_parent = socket.parent.resolve()
    if resolved_parent != _SOCKET_ROOT and _SOCKET_ROOT not in resolved_parent.parents:
        raise ValueError("control_socket is outside RTVI_CV_IPC_SOCKET_ROOT")
    _STATUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    status_file = _STATUS_DIR / f"{camera_id}.json"
    status_file.write_text('{"status":"registered","last_pts_ns":0}\n')
    _STREAM_STATUS[camera_id] = status_file
    query = urllib.parse.urlencode(
        {"gpu_id": device_id, "status_file": str(status_file)}
    )
    return f"vssipc://{urllib.parse.quote(str(socket))}?{query}"


class Handler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:9001"

    def _body(self) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        return self.rfile.read(length) if length else b""

    def _json_error(self, status: int, message: str) -> None:
        body = json.dumps({"code": "BadRequest", "message": message}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _status(self) -> bool:
        prefix = "/api/v1/stream/status/"
        if self.command != "GET" or not self.path.startswith(prefix):
            return False
        camera_id = urllib.parse.unquote(self.path[len(prefix) :])
        status_file = _STREAM_STATUS.get(camera_id)
        if status_file is None or not status_file.is_file():
            self._json_error(404, "stream status was not found")
            return True
        body = status_file.read_bytes()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _proxy(self) -> None:
        if self._status():
            return
        body = self._body()
        camera_id: str | None = None
        try:
            if self.command == "POST" and self.path == "/api/v1/stream/add":
                payload = json.loads(body)
                value = payload.get("value", payload.get("event"))
                if not isinstance(value, dict):
                    raise ValueError("stream/add requires value or event")
                camera_id = value.get("camera_id")
                if not isinstance(camera_id, str) or not _CAMERA_ID.fullmatch(
                    camera_id
                ):
                    raise ValueError("camera_id is invalid")
                frame_source = value.get("frame_source")
                camera_url = value.get("camera_url")
                if (frame_source is None) == (not camera_url):
                    raise ValueError(
                        "exactly one of camera_url and frame_source must be provided"
                    )
                if frame_source is not None:
                    if not isinstance(frame_source, dict):
                        raise ValueError("frame_source must be an object")
                    value["camera_url"] = _shared_uri(camera_id, frame_source)
                    value.pop("frame_source", None)
                    body = json.dumps(payload).encode()
            elif self.path == "/api/v1/stream/remove" and body:
                payload = json.loads(body)
                value = payload.get("value", payload.get("event"))
                if isinstance(value, dict) and isinstance(
                    value.get("camera_id"), str
                ):
                    camera_id = value["camera_id"]
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json_error(400, str(error))
            return

        request = urllib.request.Request(
            f"{self.upstream}{self.path}",
            data=body if self.command in {"POST", "PUT", "DELETE"} else None,
            method=self.command,
            headers={"content-type": self.headers.get("content-type", "application/json")},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read()
                status = response.status
                content_type = response.headers.get(
                    "content-type", "application/json"
                )
        except urllib.error.HTTPError as error:
            response_body = error.read()
            status = error.code
            content_type = error.headers.get("content-type", "application/json")
        except urllib.error.URLError:
            self._json_error(503, "RTVI-CV control API is unavailable")
            return
        if status >= 400 and camera_id is not None:
            status_file = _STREAM_STATUS.pop(camera_id, None)
            if status_file is not None:
                status_file.unlink(missing_ok=True)
        elif (
            status < 400
            and camera_id is not None
            and self.path == "/api/v1/stream/remove"
        ):
            status_file = _STREAM_STATUS.pop(camera_id, None)
            if status_file is not None:
                status_file.unlink(missing_ok=True)
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_DELETE = _proxy

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[rtvi-cv-shared-api] {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--upstream", default="http://127.0.0.1:9001")
    args = parser.parse_args()
    Handler.upstream = args.upstream.rstrip("/")
    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Optional OpenAI-compatible logging proxy for OpenClaw LLM request observability."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4) if text else 0


def _safe_preview(text: str, *, max_length: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def _message_fields(message: dict[str, Any], *, include_preview: bool) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, list):
        text = json.dumps(content, separators=(",", ":"))
    elif content is None:
        text = ""
    else:
        text = str(content)
    fields: dict[str, Any] = {
        "role": message.get("role"),
        "name": message.get("name"),
        "chars": len(text),
        "estimated_tokens": _estimate_tokens(text),
        "content_hash": sha256(text.encode()).hexdigest(),
    }
    if include_preview and text:
        fields["content_preview"] = _safe_preview(text)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        fields["tool_calls"] = tool_calls
    return fields


def _append_log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def _forward_request(
    *,
    upstream: str,
    path: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, str], bytes]:
    url = f"{upstream.rstrip('/')}{path}"
    req = request.Request(url, data=body, method=method)
    for key, value in headers.items():
        if key.lower() == "authorization":
            continue
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=300) as response:
            response_body = response.read()
            response_headers = dict(response.headers.items())
            return response.status, response_headers, response_body
    except error.HTTPError as http_error:
        response_body = http_error.read()
        response_headers = dict(http_error.headers.items())
        return http_error.code, response_headers, response_body


class OpenAILoggingProxyHandler(BaseHTTPRequestHandler):
    upstream: str
    log_path: Path
    include_preview: bool

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _write_response(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in {"transfer-encoding", "connection"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        body = self._read_body()
        headers = dict(self.headers.items())
        status, response_headers, response_body = _forward_request(
            upstream=self.upstream,
            path=self.path,
            method="GET",
            headers=headers,
            body=body or None,
        )
        self._write_response(status, response_headers, response_body)

    def do_POST(self) -> None:
        body = self._read_body()
        headers = dict(self.headers.items())
        payload: dict[str, Any] | None = None
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                payload = None

        if payload is not None and self.path.endswith("/chat/completions"):
            messages = payload.get("messages") or []
            record = {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "model": payload.get("model"),
                "messages_count": len(messages),
                "estimated_prompt_tokens": sum(
                    _estimate_tokens(
                        json.dumps(message.get("content"), separators=(",", ":"))
                        if isinstance(message.get("content"), list)
                        else str(message.get("content") or "")
                    )
                    for message in messages
                ),
                "per_message": [_message_fields(message, include_preview=self.include_preview) for message in messages],
                "tool_calls_present": any(message.get("tool_calls") for message in messages),
            }
            status, response_headers, response_body = _forward_request(
                upstream=self.upstream,
                path=self.path,
                method="POST",
                headers=headers,
                body=body,
            )
            if response_body:
                try:
                    response_json = json.loads(response_body.decode("utf-8"))
                    usage = response_json.get("usage")
                    if usage:
                        record["provider_prompt_tokens"] = usage.get("prompt_tokens")
                        record["provider_completion_tokens"] = usage.get("completion_tokens")
                        record["response_usage"] = usage
                    tool_calls = (
                        response_json.get("choices", [{}])[0]
                        .get("message", {})
                        .get("tool_calls")
                    )
                    if tool_calls:
                        record["response_tool_calls"] = tool_calls
                except json.JSONDecodeError:
                    pass
            _append_log(self.log_path, record)
            self._write_response(status, response_headers, response_body)
            return

        status, response_headers, response_body = _forward_request(
            upstream=self.upstream,
            path=self.path,
            method="POST",
            headers=headers,
            body=body or None,
        )
        self._write_response(status, response_headers, response_body)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI-compatible logging proxy")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8787)
    parser.add_argument("--upstream", required=True, help="Upstream OpenAI-compatible base URL")
    parser.add_argument("--log-path", type=Path, default=Path("/tmp/openclaw-llm-requests.jsonl"))
    parser.add_argument("--include-previews", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    handler_class = type(
        "ConfiguredOpenAILoggingProxyHandler",
        (OpenAILoggingProxyHandler,),
        {
            "upstream": args.upstream.rstrip("/"),
            "log_path": args.log_path,
            "include_preview": args.include_previews,
        },
    )
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), handler_class)
    logger.info(
        "OpenAI logging proxy listening on http://%s:%s -> %s (log=%s)",
        args.listen_host,
        args.listen_port,
        args.upstream,
        args.log_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Call the VSS Orchestrator's Streamable HTTP MCP endpoint without mcp extras."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "http://host.openshell.internal:9988/mcp"
PROTOCOL_VERSION = "2024-11-05"


class McpError(RuntimeError):
    """Raised when the remote MCP server cannot complete a request."""


def _decode_messages(body: bytes, content_type: str) -> list[dict[str, Any]]:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []

    if "text/event-stream" not in content_type and not text.startswith("data:"):
        try:
            return [json.loads(text)]
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP returned invalid JSON: {text}") from exc

    messages: list[dict[str, Any]] = []
    for event in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
        if not data:
            continue
        try:
            messages.append(json.loads(data))
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP returned invalid SSE JSON: {data}") from exc
    return messages


class StreamableHttpMcp:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session_id: str | None = None
        self.next_id = 1

    def _post(self, payload: dict[str, Any], *, expect_response: bool) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION

        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                messages = _decode_messages(response.read(), response.headers.get("Content-Type", ""))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise McpError(f"MCP HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise McpError(f"Could not reach MCP server at {self.url}: {exc.reason}") from exc

        if not expect_response:
            return None

        request_id = payload["id"]
        for message in messages:
            if message.get("id") == request_id:
                if "error" in message:
                    raise McpError(f"MCP error: {json.dumps(message['error'])}")
                return message.get("result", {})
        raise McpError("MCP response did not include the expected JSON-RPC result")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            expect_response=True,
        )
        return result or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            },
            expect_response=False,
        )

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vss-hermes-wrapper", "version": "1.0"},
            },
        )
        self.notify("notifications/initialized")


def parse_arguments(parts: list[str]) -> dict[str, Any]:
    if not parts:
        return {}

    value = " ".join(parts)
    if value == "-":
        value = sys.stdin.read()
    elif value.startswith("@"):
        with open(value[1:], encoding="utf-8") as handle:
            value = handle.read()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise McpError(f"arguments must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise McpError("arguments must be a JSON object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("VSS_ORCHESTRATOR_MCP_URL", DEFAULT_URL))
    parser.add_argument("command", nargs="?", default="help", help="health, list, or a VSS tool name")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Optional JSON object, - for stdin, or @path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command in {"help", "-h", "--help"}:
        build_parser().print_help()
        return 0

    client = StreamableHttpMcp(args.url)
    try:
        client.initialize()
        if args.command == "health":
            result = {"status": "ok", "url": args.url}
        elif args.command in {"list", "tools", "tools/list"}:
            result = client.request("tools/list")
        else:
            command = args.command
            arguments = args.arguments
            if command == "call":
                if not arguments:
                    raise McpError("call requires a VSS tool name")
                command = arguments[0]
                arguments = arguments[1:]
            tool_name = command.replace("-", "_")
            if "__" not in tool_name:
                tool_name = f"vss_orchestrator__{tool_name}"
            result = client.request("tools/call", {"name": tool_name, "arguments": parse_arguments(arguments)})
    except McpError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

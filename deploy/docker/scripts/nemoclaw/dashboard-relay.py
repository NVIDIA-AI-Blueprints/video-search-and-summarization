#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TCP relay: non-loopback LISTEN addresses -> the sandbox dashboard forward on loopback.

NemoClaw keeps the sandbox dashboard forward on 127.0.0.1:<dashboard-port>: its
forward recovery (`nemoclaw <sandbox> connect|recover|start`) retires a 0.0.0.0
forward as stale and re-creates the loopback one, and it inspects that port with
`lsof -i4TCP:<port>`, so a second listener on the port - on any address - makes
recovery refuse to touch the forward at all. Off-loopback clients therefore get
their own port: this relay listens on it and pipes bytes to the forward. It is
protocol-agnostic, so the OpenClaw control UI's WebSocket passes through.

Clients that need it:
  - the `vss-agent-ui` container, whose `host.docker.internal` alias is the Docker
    daemon's default-bridge gateway - a host address, never loopback;
  - the Brev secure-link edge, which reaches the instance port from off-loopback.

Started by deploy_nemoclaw.ipynb section 3.5, which chooses the bind addresses at
run time (they differ per host and per Docker daemon) and attributes a running
relay to its sandbox through `--sandbox` on the command line.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

_TAG = "[dashboard-relay]"


def _log(message: str) -> None:
    sys.stderr.write(f"{_TAG} {message}\n")
    sys.stderr.flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sandbox", required=True, help="sandbox name; attribution only, kept on the command line")
    parser.add_argument("--listen", default="0.0.0.0", help="comma-separated bind addresses (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=18790, help="listen port on every bind address (0 = ephemeral)")
    parser.add_argument("--upstream", default="127.0.0.1:18789", help="host:port of the dashboard forward")
    args = parser.parse_args(argv)
    args.hosts = [h.strip() for h in args.listen.split(",") if h.strip()]
    if not args.hosts:
        parser.error("--listen needs at least one address")
    host, _, port = args.upstream.rpartition(":")
    if not host or not port.isdigit():
        parser.error("--upstream must be host:port")
    args.upstream_host, args.upstream_port = host.strip("[]"), int(port)
    return args


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction, then pass EOF on as a half-close so the other direction
    keeps flowing (a client may shut its write side and still expect the reply)."""
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, asyncio.IncompleteReadError):
        if not writer.is_closing():
            writer.close()


async def _close(writer: asyncio.StreamWriter) -> None:
    if not writer.is_closing():
        writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


async def relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream: tuple[str, int]) -> None:
    peer = client_writer.get_extra_info("peername")
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(*upstream)
    except OSError as exc:
        # The forward is down (kernel gone, sandbox rebuilt): refuse the client cleanly
        # instead of holding it open; section 3.5 re-establishes the forward.
        _log(f"{peer} -> {upstream[0]}:{upstream[1]} unavailable: {exc}")
        await _close(client_writer)
        return
    try:
        await asyncio.gather(
            _pump(client_reader, upstream_writer),
            _pump(upstream_reader, client_writer),
        )
    finally:
        await asyncio.gather(_close(upstream_writer), _close(client_writer))


async def serve(hosts: list[str], port: int, upstream: tuple[str, int]) -> None:
    server = await asyncio.start_server(
        lambda r, w: relay(r, w, upstream), host=hosts, port=port, reuse_address=True
    )
    bound = ", ".join(f"{s.getsockname()[0]}:{s.getsockname()[1]}" for s in server.sockets)
    # stdout, one line, so a supervisor can wait for it: the address:port pairs actually bound.
    print(f"{_TAG} listening on {bound} -> {upstream[0]}:{upstream[1]}", flush=True)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(serve(args.hosts, args.port, (args.upstream_host, args.upstream_port)))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        _log(f"cannot listen on {args.listen}:{args.port}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

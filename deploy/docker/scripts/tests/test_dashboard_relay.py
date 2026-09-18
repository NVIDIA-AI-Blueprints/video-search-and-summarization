# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import re
import socket
import socketserver
import subprocess
import sys
import threading
import unittest
from pathlib import Path

RELAY_PATH = Path(__file__).parents[1] / "nemoclaw" / "dashboard-relay.py"
MODULE_SPEC = importlib.util.spec_from_file_location("dashboard_relay_under_test", RELAY_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {RELAY_PATH}")
relay = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(relay)


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(65536):
            self.request.sendall(data)


class _ReplyAfterEof(socketserver.BaseRequestHandler):
    """Reads to EOF, then answers: the reply only exists after the client half-closes."""

    def handle(self) -> None:
        received = b""
        while data := self.request.recv(65536):
            received += data
        self.request.sendall(b"got:" + received)


class _ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ParseArgsTests(unittest.TestCase):
    def test_hosts_are_split_and_upstream_parsed(self) -> None:
        args = relay.parse_args(
            ["--sandbox", "demo", "--listen", "172.17.0.1, 10.0.0.5", "--port", "18790", "--upstream", "127.0.0.1:18789"]
        )
        self.assertEqual(args.hosts, ["172.17.0.1", "10.0.0.5"])
        self.assertEqual((args.upstream_host, args.upstream_port), ("127.0.0.1", 18789))

    def test_defaults_target_the_dashboard_forward(self) -> None:
        args = relay.parse_args(["--sandbox", "demo"])
        self.assertEqual(args.hosts, ["0.0.0.0"])
        self.assertEqual(args.port, 18790)
        self.assertEqual((args.upstream_host, args.upstream_port), ("127.0.0.1", 18789))

    def test_a_bad_upstream_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            relay.parse_args(["--sandbox", "demo", "--upstream", "nowhere"])


class RelayProcessTests(unittest.TestCase):
    """The script as section 3.5 runs it: a subprocess whose first stdout line names the bound sockets."""

    def _start(self, upstream_port: int) -> tuple[subprocess.Popen, int]:
        process = subprocess.Popen(
            [
                sys.executable, str(RELAY_PATH), "--sandbox", "demo",
                "--listen", "127.0.0.1", "--port", "0", "--upstream", f"127.0.0.1:{upstream_port}",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(process.wait, timeout=5)
        self.addCleanup(process.kill)
        ready = process.stdout.readline()
        match = re.search(r"listening on 127\.0\.0\.1:(\d+) -> 127\.0\.0\.1:(\d+)", ready)
        self.assertIsNotNone(match, ready)
        self.assertEqual(int(match.group(2)), upstream_port)
        return process, int(match.group(1))

    def test_bytes_cross_the_relay_both_ways(self) -> None:
        echo = _ThreadedServer(("127.0.0.1", 0), _Echo)
        threading.Thread(target=echo.serve_forever, daemon=True).start()
        self.addCleanup(echo.shutdown)
        _, relay_port = self._start(echo.server_address[1])
        with socket.create_connection(("127.0.0.1", relay_port), timeout=5) as client:
            client.sendall(b"GET /health HTTP/1.1\r\n\r\n")
            self.assertEqual(client.recv(65536), b"GET /health HTTP/1.1\r\n\r\n")

    def test_a_client_half_close_still_receives_the_reply(self) -> None:
        server = _ThreadedServer(("127.0.0.1", 0), _ReplyAfterEof)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        _, relay_port = self._start(server.server_address[1])
        with socket.create_connection(("127.0.0.1", relay_port), timeout=5) as client:
            client.sendall(b"ping")
            client.shutdown(socket.SHUT_WR)
            reply = b""
            while chunk := client.recv(65536):
                reply += chunk
            self.assertEqual(reply, b"got:ping")

    def test_a_dead_upstream_closes_the_client_instead_of_hanging(self) -> None:
        process, relay_port = self._start(_free_port())
        with socket.create_connection(("127.0.0.1", relay_port), timeout=5) as client:
            client.settimeout(5)
            self.assertEqual(client.recv(1), b"")
        process.kill()
        process.wait(timeout=5)
        self.assertIn("unavailable", process.stderr.read())


if __name__ == "__main__":
    unittest.main()

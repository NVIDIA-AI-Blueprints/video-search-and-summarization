# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The optional Relay sink observes the CLI without becoming part of its contract."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import time
from typing import ClassVar

import click
import pytest

import vss_cli
from vss_cli import _relay
from vss_cli.config import ConfigError

_ENV = ("VSS_RELAY_URL", "VSS_RELAY_RUN", "VSS_RELAY_TRIAL")


class _Sink(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *,
        status: int = 204,
        redirect_to: str | None = None,
        stalled: bool = False,
    ) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.status = status
        self.redirect_to = redirect_to
        self.release = threading.Event()
        if not stalled:
            self.release.set()
        self.events: list[dict[str, object]] = []
        self.lock = threading.Lock()
        self.start_seen = threading.Event()
        self.end_seen = threading.Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/relay/events"


class _Handler(BaseHTTPRequestHandler):
    server: ClassVar[_Sink]

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        event = json.loads(self.rfile.read(length))
        event["_headers"] = {key.lower(): value for key, value in self.headers.items()}
        event["_path"] = self.path
        with self.server.lock:
            self.server.events.append(event)
        if event.get("scope_category") == "start":
            self.server.start_seen.set()
        elif event.get("scope_category") == "end":
            self.server.end_seen.set()

        self.server.release.wait(timeout=2)
        self.send_response(self.server.status)
        if self.server.redirect_to:
            self.send_header("Location", self.server.redirect_to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serve(**kwargs: object):
    server = _Sink(**kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _enable(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    *,
    run: str = "run-1",
    trial: str = "trial__abc",
) -> None:
    monkeypatch.setenv("VSS_RELAY_URL", url)
    monkeypatch.setenv("VSS_RELAY_RUN", run)
    monkeypatch.setenv("VSS_RELAY_TRIAL", trial)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def _public(event: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def test_start_is_live_and_end_is_correlated_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_arg = "do-not-export-this-query"
    secret_output = "stdout-stays-local"
    secret_error = "stderr-stays-local"
    secret_env = "environment-stays-local"
    run, trial = "r" * 255, "t" * 255

    with _serve() as sink:
        _enable(monkeypatch, sink.url, run=run, trial=trial)
        monkeypatch.setenv("UNRELATED_SECRET", secret_env)

        def command() -> int:
            # The start event must be delivered while the command is still running,
            # not reconstructed after it returns.
            assert sink.start_seen.wait(timeout=0.75)
            print(secret_output)
            print(secret_error, file=sys.stderr)
            return 7

        assert (
            _relay.run_with_events(
                ["search", "run", "embed", "--query", secret_arg, "unknown-tail"],
                command,
            )
            == 7
        )
        assert sink.end_seen.wait(timeout=0.75)

        with sink.lock:
            events = list(sink.events)

    captured = capsys.readouterr()
    assert captured.out == secret_output + "\n"
    assert captured.err == secret_error + "\n"
    assert [event["scope_category"] for event in events] == ["start", "end"]
    start, end = map(_public, events)
    assert start["data_schema"] == end["data_schema"] == "nvidia.vss.cli.lifecycle/v1"
    assert start["kind"] == end["kind"] == "scope"
    assert start["category"] == end["category"] == "tool"
    assert start["name"] == end["name"] == "vss search run embed"
    assert start["uuid"] == end["uuid"]
    assert start["metadata"]["session_id"] == end["metadata"]["session_id"] == f"vss-eval/{run}/{trial}"
    assert "duration_ms" not in start["metadata"]
    assert 0 <= end["metadata"]["duration_ms"] < 1_000
    assert "exit_code" not in start.get("data", {})
    assert end["data"] == {"exit_code": 7, "error": "CLI exited with code 7"}
    assert events[0]["_headers"]["x-relay-run"] == run
    assert events[0]["_headers"]["x-relay-trial"] == trial
    assert events[0]["_path"] == events[1]["_path"] == "/relay/events"

    encoded = json.dumps(events)
    for private in (secret_arg, secret_output, secret_error, secret_env, "unknown-tail"):
        assert private not in encoded
    assert _all_keys(start | end).isdisjoint(
        {"args", "argv", "command", "group", "prompt", "stdout", "stderr", "env", "environment"}
    )


@pytest.mark.parametrize("missing", _ENV)
def test_missing_context_does_not_start_a_worker(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    _clear(monkeypatch)
    values = {
        "VSS_RELAY_URL": "http://127.0.0.1:9/relay/events",
        "VSS_RELAY_RUN": "run-1",
        "VSS_RELAY_TRIAL": "trial-1",
    }
    for name, value in values.items():
        if name != missing:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(threading.Thread, "start", lambda _self: pytest.fail("worker started"))

    assert _relay.run_with_events(["vios", "list"], lambda: 19) == 19


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("VSS_RELAY_URL", "relay.internal/events"),
        ("VSS_RELAY_URL", "ftp://relay.internal/events"),
        ("VSS_RELAY_URL", "http://user@relay.internal/events"),
        ("VSS_RELAY_URL", "http://relay.internal/events?q=secret"),
        ("VSS_RELAY_URL", "https://relay.internal/events#fragment"),
        ("VSS_RELAY_RUN", "."),
        ("VSS_RELAY_RUN", "../escape"),
        ("VSS_RELAY_RUN", "r" * 256),
        ("VSS_RELAY_TRIAL", "trial/other"),
        ("VSS_RELAY_TRIAL", "header\r\ninjection"),
    ],
)
def test_invalid_url_or_identity_does_not_start_a_worker(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    _enable(monkeypatch, "http://127.0.0.1:9/relay/events")
    monkeypatch.setenv(field, value)
    monkeypatch.setattr(threading.Thread, "start", lambda _self: pytest.fail("worker started"))

    assert _relay.run_with_events(["vlm", "run"], lambda: 23) == 23


def test_http_failure_is_not_retried_or_observable_by_the_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _serve(status=503) as sink:
        _enable(monkeypatch, sink.url)

        def command() -> int:
            assert sink.start_seen.wait(timeout=0.75)
            print("command output")
            print("command diagnostic", file=sys.stderr)
            return 3

        assert _relay.run_with_events(["vios", "clip", "--sensor", "cam"], command) == 3
        assert sink.end_seen.wait(timeout=0.75)
        with sink.lock:
            events = list(sink.events)

    assert [event["scope_category"] for event in events] == ["start", "end"]
    captured = capsys.readouterr()
    assert captured.out == "command output\n"
    assert captured.err == "command diagnostic\n"


def test_slow_receiver_cannot_hold_the_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _serve(stalled=True) as sink:
        _enable(monkeypatch, sink.url)
        started = time.monotonic()

        def command() -> int:
            assert sink.start_seen.wait(timeout=0.75)
            print("still returned")
            return 0

        assert _relay.run_with_events(["summarize", "list"], command) == 0
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
        assert sink.end_seen.wait(timeout=0.75)

    assert capsys.readouterr().out == "still returned\n"


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serve() as destination:
        with _serve(status=307, redirect_to=destination.url) as source:
            _enable(monkeypatch, source.url)
            assert _relay.run_with_events(["analytics", "places"], lambda: 0) == 0
            assert source.end_seen.wait(timeout=0.75)
            with source.lock:
                source_events = list(source.events)
            with destination.lock:
                destination_events = list(destination.events)

    assert [event["scope_category"] for event in source_events] == ["start", "end"]
    assert destination_events == []


def test_a_worker_stalled_before_http_cannot_keep_the_cli_process_alive() -> None:
    """A socket timeout does not bound DNS/setup; process exit must still be bounded."""
    code = """
import threading
from vss_cli import _relay
def stalled(*args):
    threading.Event().wait()
_relay._post_events = stalled
def command():
    print('normal output')
    return 7
raise SystemExit(_relay.run_with_events(['vlm', 'run'], command))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            **os.environ,
            "VSS_RELAY_URL": "http://127.0.0.1:9/relay/events",
            "VSS_RELAY_RUN": "test-run",
            "VSS_RELAY_TRIAL": "test-trial",
        },
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 7
    assert result.stdout == "normal output\n"
    assert result.stderr == ""


def test_command_name_stops_at_the_last_allowlisted_public_word(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        (["private-root", "search", "run"], "vss"),
        (["search", "private-action", "run"], "vss search"),
        (["search", "run", "private-action"], "vss search run"),
        (["search", "run", "embed", "private-value"], "vss search run embed"),
        (["vios", "add", "memory", "--type", "video"], "vss vios add"),
    ]
    with _serve() as sink:
        _enable(monkeypatch, sink.url)
        for args, expected in cases:
            sink.start_seen.clear()
            sink.end_seen.clear()
            with sink.lock:
                before = len(sink.events)
            assert _relay.run_with_events(args, lambda: 0) == 0
            assert sink.end_seen.wait(timeout=0.75)
            with sink.lock:
                emitted = sink.events[before:]
            assert [event["name"] for event in emitted] == [expected, expected]


@pytest.mark.parametrize(
    ("raised", "exit_code"),
    [
        (SystemExit(37), 37),
        (SystemExit(None), 0),
        (SystemExit("private system-exit message"), 1),
        (KeyboardInterrupt(), 130),
        (RuntimeError("private exception message"), 1),
    ],
    ids=["system-exit-int", "system-exit-none", "system-exit-text", "keyboard-interrupt", "exception"],
)
def test_base_exception_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException,
    exit_code: int,
) -> None:
    with _serve() as sink:
        _enable(monkeypatch, sink.url)

        def command() -> int:
            assert sink.start_seen.wait(timeout=0.75)
            raise raised

        with pytest.raises(BaseException) as caught:
            _relay.run_with_events(["memory", "query", "--query", "private"], command)
        assert sink.end_seen.wait(timeout=0.75)
        with sink.lock:
            events = list(sink.events)

    assert caught.value is raised
    assert [event["scope_category"] for event in events] == ["start", "end"]
    expected_data: dict[str, object] = {"exit_code": exit_code}
    if exit_code:
        expected_data["error"] = f"CLI exited with code {exit_code}"
    assert events[-1]["data"] == expected_data
    encoded = json.dumps(events)
    assert "private system-exit message" not in encoded
    assert "private exception message" not in encoded


@pytest.mark.parametrize(
    ("effect", "expected", "stream", "text"),
    [
        (None, 0, "out", "normal output"),
        (ConfigError("configuration missing"), 4, "err", "vss: configuration missing"),
        (click.UsageError("bad invocation"), 2, "err", "bad invocation"),
        (click.Abort(), 130, "err", "vss: aborted"),
    ],
    ids=["normal", "configuration", "usage", "abort"],
)
def test_root_dispatch_behavior_is_identical_with_telemetry_off_and_on(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    effect: BaseException | None,
    expected: int,
    stream: str,
    text: str,
) -> None:
    _clear(monkeypatch)

    class Root:
        def main(self, **_kwargs: object) -> None:
            if effect is not None:
                raise effect
            print(text)

    monkeypatch.setattr(vss_cli, "build_root", lambda: Root())
    assert vss_cli.main(["search", "list"]) == expected
    baseline = capsys.readouterr()
    assert text in getattr(baseline, stream)
    with _serve() as sink:
        _enable(monkeypatch, sink.url)
        assert vss_cli.main(["search", "list"]) == expected
        assert sink.end_seen.wait(timeout=0.75)
        with sink.lock:
            events = list(sink.events)
    assert capsys.readouterr() == baseline
    assert [event["scope_category"] for event in events] == ["start", "end"]
    assert events[-1]["data"]["exit_code"] == expected

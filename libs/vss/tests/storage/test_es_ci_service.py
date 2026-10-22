# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CI startup diagnosis must distinguish service failure from storage failures."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def check():
    path = Path(__file__).resolve().parents[2] / ".github/scripts/check_es_service.py"
    spec = importlib.util.spec_from_file_location("check_es_service", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (socket.gaierror(-2, "no service alias"), "dns"),
        (ConnectionRefusedError(111, "not listening"), "connection"),
        (TimeoutError("not reachable"), "connection"),
    ],
)
def test_network_failures_are_classified(check, monkeypatch, error, category):
    def resolve(*args, **kwargs):
        if category == "dns":
            raise error
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.2", 9200))]

    monkeypatch.setattr(check.socket, "getaddrinfo", resolve)

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise error

        def close(self):
            pass

    monkeypatch.setattr(check.http.client, "HTTPConnection", Connection)
    result = check.probe()
    assert result["category"] == category
    assert result["ready"] is False
    assert result["retryable"] is True


@pytest.mark.parametrize(
    ("status", "product", "payload", "category", "retryable"),
    [
        (200, "Elasticsearch", {"version": {"number": "8.19.3"}}, "ready", False),
        (200, None, {"version": {"number": "8.19.3"}}, "wrong-service", False),
        (200, "Elasticsearch", {}, "wrong-service", False),
        (401, None, {}, "http", False),
        (403, None, {}, "http", False),
        (503, None, {}, "http", True),
    ],
)
def test_http_readiness_is_verified(
    check, monkeypatch, status, product, payload, category, retryable
):
    monkeypatch.setattr(
        check.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.2", 9200))
        ],
    )

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path):
            assert (method, path) == ("GET", "/")

        def getresponse(self):
            return SimpleNamespace(
                status=status,
                getheader=lambda name: product,
                read=lambda limit: json.dumps(payload).encode(),
            )

        def close(self):
            pass

    monkeypatch.setattr(check.http.client, "HTTPConnection", Connection)
    result = check.probe()
    assert result["category"] == category
    assert result["retryable"] is retryable
    assert result["ready"] is (category == "ready")


def test_preflight_records_failure_once_instead_of_starting_native_tests(
    check, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        check,
        "probe",
        lambda: {"ready": False, "retryable": False, "category": "wrong-service"},
    )
    report = tmp_path / "startup.json"
    assert check.main(report_path=report) == 1
    assert json.loads(report.read_text())["category"] == "wrong-service"


def test_preflight_retries_startup_but_stops_at_deadline(check, tmp_path, monkeypatch):
    attempts = []
    now = [0.0]

    def probe():
        attempts.append(True)
        return {"ready": False, "retryable": True, "category": "connection"}

    monkeypatch.setattr(check, "probe", probe)
    monkeypatch.setattr(check.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        check.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    assert check.main(report_path=tmp_path / "startup.json", timeout_s=2.0) == 1
    assert len(attempts) == 3

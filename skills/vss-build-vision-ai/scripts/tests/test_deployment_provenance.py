# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for stale images, checkout drift and ingress-only failures."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_deployment_provenance as provenance


def fake_docker(monkeypatch, tmp_path, *, ids="container-id", services="alert-bridge"):
    resolved = tmp_path / "resolved.yml"
    resolved.write_text(
        "services: {alert-bridge: {environment: {SECRET: never-publish}}}"
    )

    def command(*args):
        if args[0] == "git":
            return "actual-sha"
        if "--services" in args:
            return services
        if "--quiet" in args:
            return ids
        if args[:3] == ("docker", "image", "inspect"):
            assert args[3] == "sha256:running-old-image"
            return json.dumps(
                [
                    {
                        "RepoDigests": ["registry/alert@sha256:old-digest"],
                        "Config": {
                            "Env": ["SECRET=never-publish"],
                            "Labels": {
                                "org.opencontainers.image.revision": "old-source-sha",
                                "unrelated-secret-label": "never-publish",
                            },
                        },
                    }
                ]
            )
        return json.dumps(
            [
                {
                    "Name": "/vss-alert-bridge",
                    "Image": "sha256:running-old-image",
                    "Config": {
                        "Image": "registry/alert:develop-latest",
                        "Env": ["SECRET=never-publish"],
                        "Labels": {"com.docker.compose.service": "alert-bridge"},
                    },
                }
            ]
        )

    monkeypatch.setattr(provenance, "command", command)
    return resolved


def test_running_image_identity_and_secrets(monkeypatch, tmp_path):
    resolved = fake_docker(monkeypatch, tmp_path)
    report = provenance.collect(tmp_path, resolved, "actual-sha")
    assert (
        report["containers"][0]["source_labels"]["org.opencontainers.image.revision"]
        == "old-source-sha"
    )
    assert report["containers"][0]["repo_digests"] == [
        "registry/alert@sha256:old-digest"
    ]
    assert report["errors"] == []
    assert "never-publish" not in json.dumps(report)


def test_checkout_drift_fails(monkeypatch, tmp_path):
    resolved = fake_docker(monkeypatch, tmp_path)
    assert (
        "expected eval SHA"
        in provenance.collect(tmp_path, resolved, "expected-sha")["errors"][0]
    )


def test_empty_project_cannot_pass(monkeypatch, tmp_path):
    resolved = fake_docker(monkeypatch, tmp_path, ids="")
    assert provenance.collect(tmp_path, resolved, None)["errors"]


def test_missing_service_container_fails(monkeypatch, tmp_path):
    resolved = fake_docker(
        monkeypatch, tmp_path, services="alert-bridge\nvss-behavior-analytics-alerts"
    )
    assert (
        "vss-behavior-analytics-alerts"
        in provenance.collect(tmp_path, resolved, None)["errors"][0]
    )


@pytest.mark.parametrize(
    "direct,ingress,reason",
    [(404, 404, "directly"), (200, 404, "routing"), (200, 200, None)],
)
def test_direct_vs_ingress_failure(monkeypatch, direct, ingress, reason):
    monkeypatch.setattr(
        provenance,
        "probe_config",
        lambda origin: {
            "http_status": direct if origin == "direct" else ingress,
            "config_api": (direct if origin == "direct" else ingress) == 200,
        },
    )
    report = {"errors": []}
    provenance.check_alert_api(report, "direct", "ingress")
    if reason:
        assert reason in report["errors"][0]
    else:
        assert not report["errors"]


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, '{"status":"success","configs":[],"count":0}', True),
        (200, "<html>UI catch-all</html>", False),
        (200, "[]", False),
        (404, '{"detail":"Not Found"}', False),
        (503, '{"detail":"backend unavailable"}', False),
        (302, "", False),
    ],
)
def test_http_contract(status, body, expected):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == provenance.CONFIG_PATH
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/login")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = provenance.probe_config(f"http://127.0.0.1:{server.server_port}")
        assert result == {"http_status": status, "config_api": expected}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_failure_report_retained_with_private_permissions(monkeypatch, tmp_path):
    import os

    resolved = fake_docker(monkeypatch, tmp_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_deployment_provenance.py",
            "--repo-root",
            str(tmp_path),
            "--resolved",
            str(resolved),
            "--output",
            str(output),
            "--expected-checkout-sha",
            "expected-sha",
        ],
    )
    assert provenance.main() == 1
    report = json.loads(output.read_text())
    assert report["containers"][0]["image_id"] == "sha256:running-old-image"
    assert any("requires --alert-direct-origin" in error for error in report["errors"])
    assert os.stat(output).st_mode & 0o777 == 0o600

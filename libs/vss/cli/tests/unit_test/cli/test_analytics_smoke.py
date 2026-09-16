# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI smoke test against a local Video Analytics API fixture."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import threading
from typing import TYPE_CHECKING
from typing import ClassVar
from urllib.parse import urlsplit

import vss_cli
from vss_cli import config as config_mod

if TYPE_CHECKING:
    import pytest


class _AnalyticsHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        type(self).requests.append(self.path)
        status = 200
        if path == "/video-analytics-api/livez":
            body: object = {"isAlive": True}
        elif path == "/video-analytics-api/incidents":
            body = {"incidents": []}
        elif path == "/video-analytics-api/config/calibration":
            body = {
                "sensors": [
                    {
                        "id": "cam-1",
                        "place": [{"value": "San Jose"}, {"value": "First"}],
                    }
                ]
            }
        else:
            status, body = 404, {"error": "not routed"}
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_cli_smoke_uses_only_video_analytics_api(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AnalyticsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    _AnalyticsHandler.requests = []
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    try:
        assert vss_cli.main(["configure", "--base-url", base_url]) == 0
        capsys.readouterr()

        assert vss_cli.main(["configure", "check"]) == 0
        capsys.readouterr()

        assert vss_cli.main(["analytics", "incidents", "--limit", "10"]) == 0
        assert json.loads(capsys.readouterr().out) == {"count": 0, "incidents": []}

        assert vss_cli.main(["analytics", "sensors"]) == 0
        assert json.loads(capsys.readouterr().out) == {"count": 1, "sensors": ["cam-1"]}

        assert vss_cli.main(["analytics", "places"]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "count": 1,
            "places": {"San Jose": ["First"]},
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert any(request.startswith("/video-analytics-api/incidents?") for request in _AnalyticsHandler.requests)
    assert all("9901" not in request and "/va-mcp" not in request for request in _AnalyticsHandler.requests)

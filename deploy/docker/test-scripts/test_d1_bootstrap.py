# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise first-run registration without changing a live VSS deployment."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

path = Path(__file__).resolve().parents[1] / "developer-profiles/dev-profile-d1-cosmos/bootstrap.py"
spec = importlib.util.spec_from_file_location("d1_bootstrap", path)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

SOURCES = {
    "d1-flowing": "rtsp://d1-fixture-rtsp:8554/flowing",
    "d1-queued": "rtsp://d1-fixture-rtsp:8554/queued",
}


def test_first_run_and_restart_preserve_rules():
    records = {"streams": {}, "rules": {}}
    sensors = {}
    mutations = []

    def respond(request):
        collection, _, key = request.url.path.removeprefix("/v1/").partition("/")
        if request.method == "GET":
            return httpx.Response(200, json=list(records[collection].values()))
        assert request.method == "PUT"
        assert key not in records[collection]
        row = {"id": key, "config": json.loads(request.content), "revision": 7}
        records[collection][key] = row
        mutations.append((collection, key))
        return httpx.Response(200, json=row)

    def cli(*args):
        if args[:2] == ("vios", "list"):
            return {"sensors": list(sensors.values())}
        if args[:2] == ("vios", "add"):
            name = args[-1]
            assert name not in sensors
            sensors[name] = {"name": name, "sensor_id": name, "is_main": True, "source": args[4]}
            mutations.append(("sensor", name))
        else:
            assert args[:2] == ("configure", "--base-url")
        return None

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        bootstrap.configure(client, "http://ingest", "http://analytics", "http://vss", SOURCES, cli)
        assert len(mutations) == 6
        assert records["rules"]["d1-flowing"]["config"]["config_revision"] == 7
        assert records["rules"]["d1-flowing"]["config"]["enabled"]
        assert not records["rules"]["d1-queued"]["config"]["enabled"]

        # Simulate an operator editing a trigger. A repeat startup leaves it alone.
        records["rules"]["d1-flowing"]["config"]["value"] = 0.9
        bootstrap.configure(client, "http://ingest", "http://analytics", "http://vss", SOURCES, cli)
        assert len(mutations) == 6
        assert records["rules"]["d1-flowing"]["config"]["value"] == 0.9

        # A reused sensor name with different footage must never be reassigned.
        sensors["d1-queued"]["source"] = "rtsp://different.example/input"
        with pytest.raises(ValueError, match="different or ambiguous sensor"):
            bootstrap.configure(client, "http://ingest", "http://analytics", "http://vss", SOURCES, cli)
        assert len(mutations) == 6


def test_refuse_missing_or_credential_bearing_sources():
    with pytest.raises(ValueError, match="D1_FLOWING_RTSP_URL"):
        bootstrap.read_sources({})
    with pytest.raises(ValueError, match="without embedded credentials") as exc:
        bootstrap.read_sources({"D1_FLOWING_RTSP_URL": "rtsp://user:password@example.test/stream"})
    assert "password" not in str(exc.value)

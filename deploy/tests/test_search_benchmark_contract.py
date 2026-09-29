# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Search main ingress and VIOS notification contract used by benchmark prepare."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "helm/developer-profiles/dev-profile-search"


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_rendered_search_routes_and_embedding_webhook_model():
    subprocess.run(["helm", "dependency", "build", str(CHART)], check=True, capture_output=True)
    rendered = subprocess.run(
        ["helm", "template", "benchmark", str(CHART), "--set-string", "rtviEmbedModelName=custom-embed"],
        check=True, capture_output=True, text=True).stdout
    objects = [o for o in yaml.safe_load_all(rendered) if isinstance(o, dict)]
    rules = [rule for o in objects if o.get("kind") == "Ingress"
             for rule in o["spec"].get("rules", []) if rule.get("host")]
    main = [r for r in rules if {"/api", "/vst", "/rtvi-embed"} <=
            {p.get("path") for p in r.get("http", {}).get("paths", [])}]
    assert len(main) == 1
    assert all(r["host"] != main[0]["host"] for r in rules if r is not main[0])
    configs = [json.loads(o["data"]["notification_config.json"])
               for o in objects if o.get("kind") == "ConfigMap"
               and "notification" in o.get("metadata", {}).get("name", "")
               and "notification_config.json" in o.get("data", {})]
    assert len(configs) == 2
    for config in configs:
        assert config["webhooks"]["enabled"] is True
        hooks = [h for h in config["webhooks"]["items"]
                 if h.get("id") == "rtvi-embed-camera-streaming"]
        assert len(hooks) == 1 and hooks[0]["enabled"] is True
        request = hooks[0]["request"][0]
        assert request["url"].endswith("/v1/stream/add")
        assert request["user_defined_metadata"]["model"] == "custom-embed"

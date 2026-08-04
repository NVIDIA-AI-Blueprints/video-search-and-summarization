# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "envoy" / "generate_envoy_config_xds_mw.py"
TEMPLATE_DIR = REPO_ROOT / "envoy" / "templates"


def test_envoy_generator_intercepts_http_lifecycle_paths_before_data_plane(tmp_path):
    config = tmp_path / "config.yml"
    out = tmp_path / "envoy.yaml"
    config.write_text(
        """
docker-workload-rtvi-cv:
  wl_obj_name: vss-rtvi-cv
  port: 4004
  enable: true
  WDM_MS_LISTENER_PORT: 10001
  WDM_LIFECYCLE_INGRESS_MODE: http
  WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER: streamid
  WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH: /api/v1/stream/add
  WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD: POST
  WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH: /api/v1/stream/remove
  WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD: POST
  WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH: /api/v1/stream/add
  WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD: POST
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--config",
            str(config),
            "--out",
            str(out),
            "--template-dir",
            str(TEMPLATE_DIR),
            "--controller-host",
            "sdr-controller",
            "--controller-port",
            "5003",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = out.read_text(encoding="utf-8")

    assert "port_value: 10001" in rendered
    assert "name: lifecycle_vss_rtvi_cv_add" in rendered
    assert "name: lifecycle_vss_rtvi_cv_delete" in rendered
    assert 'substitution: "/sdrc/vss-rtvi-cv/api/v1/stream/add"' in rendered
    assert 'substitution: "/sdrc/vss-rtvi-cv/api/v1/stream/remove"' in rendered
    assert rendered.count('substitution: "/sdrc/vss-rtvi-cv/api/v1/stream/add"') == 1
    assert rendered.index("name: lifecycle_vss_rtvi_cv_add") < rendered.index(
        "name: upstream-cluster"
    )


def test_envoy_generator_does_not_emit_lifecycle_routes_in_message_bus_mode(tmp_path):
    config = tmp_path / "config.yml"
    out = tmp_path / "envoy.yaml"
    config.write_text(
        """
docker-workload-rtvi-cv:
  wl_obj_name: vss-rtvi-cv
  port: 4004
  enable: true
  WDM_MS_LISTENER_PORT: 10001
  WDM_LIFECYCLE_INGRESS_MODE: message-bus
  WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH: /api/v1/stream/add
  WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD: POST
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--config",
            str(config),
            "--out",
            str(out),
            "--template-dir",
            str(TEMPLATE_DIR),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = out.read_text(encoding="utf-8")

    assert "lifecycle_vss_rtvi_cv_add" not in rendered
    assert "name: upstream-cluster" in rendered

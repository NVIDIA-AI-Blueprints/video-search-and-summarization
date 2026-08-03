# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _mock_run_workloads_deps():
    """Insert stubs for heavy optional deps so run_workloads can be imported."""
    for mod_name in ("redis", "ruamel", "ruamel.yaml", "requests", "flask",
                     "flask_kafka", "simple_settings", "prometheus_client",
                     "grpclib", "grpclib.server", "opentelemetry",
                     "opentelemetry.sdk", "opentelemetry.sdk.trace",
                     "opentelemetry.sdk.trace.export",
                     "opentelemetry.exporter",
                     "opentelemetry.exporter.otlp",
                     "opentelemetry.exporter.otlp.proto",
                     "opentelemetry.exporter.otlp.proto.grpc",
                     "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
                     "opentelemetry.trace",
                     "werkzeug", "werkzeug.middleware", "werkzeug.middleware.proxy_fix"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    # Specific attributes Flask needs
    flask_mock = sys.modules.get("flask", MagicMock())
    for attr in ("Flask", "Response", "g", "request", "jsonify", "render_template"):
        if not hasattr(flask_mock, attr):
            setattr(flask_mock, attr, MagicMock())

    ruamel_yaml = sys.modules.get("ruamel.yaml", MagicMock())
    yaml_instance = MagicMock()
    yaml_instance.load.return_value = {}
    ruamel_yaml.YAML = MagicMock(return_value=yaml_instance)


def _import_run_workloads():
    if "run_workloads" in sys.modules:
        return sys.modules["run_workloads"]
    _mock_run_workloads_deps()
    import run_workloads as rw
    return rw


def test_dashboard_lifecycle_config_prefers_workload_entry(monkeypatch):
    rw = _import_run_workloads()

    monkeypatch.delenv("WDM_LIFECYCLE_INGRESS_MODE", raising=False)
    defaults = {
        "WDM_LIFECYCLE_INGRESS_MODE": "message-bus",
        "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER": "default-streamid",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH": "/defaults/add",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD": "PUT",
    }
    entry = {
        "wl_obj_name": "perception-sdr",
        "WDM_LIFECYCLE_INGRESS_MODE": "http",
        "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER": "streamid",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH": "/sdrc/v1/streams",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH": "/sdrc/v1/streams",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD": "DELETE",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH": "/sdrc/v1/streams/reprovision",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD": "POST",
        "WDM_WL_ID_FIELD": "camera_id",
        "WDM_WL_CHANGE_ID_REPROVISION": "reprovision",
    }

    cfg = rw._dashboard_lifecycle_config(defaults, entry)

    assert cfg["mode"] == "http-header"
    assert cfg["stream_id_header"] == "streamid"
    assert cfg["add_path"] == "/sdrc/v1/streams"
    assert cfg["add_method"] == "POST"
    assert cfg["delete_path"] == "/sdrc/v1/streams"
    assert cfg["delete_method"] == "DELETE"
    assert cfg["reprovision_path"] == "/sdrc/v1/streams/reprovision"
    assert cfg["reprovision_method"] == "POST"
    assert cfg["id_field"] == "camera_id"
    assert cfg["reprovision_change_id"] == "reprovision"
    assert cfg["binding_warning"] == ""


def test_dashboard_lifecycle_config_defaults_to_message_bus(monkeypatch):
    rw = _import_run_workloads()

    monkeypatch.delenv("WDM_LIFECYCLE_INGRESS_MODE", raising=False)

    cfg = rw._dashboard_lifecycle_config({}, {"wl_obj_name": "vss-rtvi-cv"})

    assert cfg["mode"] == "message-bus"
    assert cfg["binding_warning"] == ""


def test_dashboard_lifecycle_config_allows_shared_add_reprovision_binding(
    monkeypatch,
):
    rw = _import_run_workloads()

    monkeypatch.delenv("WDM_LIFECYCLE_INGRESS_MODE", raising=False)
    entry = {
        "wl_obj_name": "vss-rtvi-cv",
        "WDM_LIFECYCLE_INGRESS_MODE": "http",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH": "/api/v1/stream/add",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH": "/api/v1/stream/remove",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH": "/api/v1/stream/add",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD": "POST",
    }

    cfg = rw._dashboard_lifecycle_config({}, entry)

    assert cfg["mode"] == "http-header"
    assert cfg["binding_warning"] == ""


def test_dashboard_lifecycle_config_warns_on_unsupported_duplicate_binding(
    monkeypatch,
):
    rw = _import_run_workloads()

    monkeypatch.delenv("WDM_LIFECYCLE_INGRESS_MODE", raising=False)
    entry = {
        "wl_obj_name": "vss-rtvi-cv",
        "WDM_LIFECYCLE_INGRESS_MODE": "http",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH": "/api/v1/stream",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH": "/api/v1/stream",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH": "/api/v1/stream/reprovision",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD": "POST",
    }

    cfg = rw._dashboard_lifecycle_config({}, entry)

    assert cfg["mode"] == "http-header"
    assert cfg["binding_warning"] == (
        "Duplicate HTTP lifecycle binding POST /api/v1/stream "
        "is used by add and delete; only add and reprovision may share a binding."
    )


def test_dashboard_template_has_header_lifecycle_and_route_mapping_hooks():
    template = Path(REPO_ROOT, "templates", "dashboard.html").read_text()

    assert "data-lifecycle-mode" in template
    assert "data-lifecycle-stream-id-header" in template
    assert "id=\"route-mapping-link\"" in template
    assert "function sendLifecycleRequest" in template
    assert "function openRouteMappingModal" in template
    assert "current_streamid_address_mapping" in template
    assert "sensor-reprovision-btn" in template
    assert "lifecycle-badge" in template
    assert "data-lifecycle-binding-warning" in template
    assert "add-workload-lifecycle-summary" in template

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

"""Build envoy/envoy_config_xds_mw.yaml from repo root config.yml.

Renders envoy/templates/envoy_config_xds_mw.yaml.j2 (Jinja2). For each workload with
WDM_MS_LISTENER_PORT and port, emits one listener; static_resources.clusters are
xds_cluster (REST CDS), optional xds_cluster_grpc (gRPC ADS CDS), and sdrc_direct
(same controller host/port).
Workload listeners: /sdrc → sdrc_direct without requiring upstream-cluster (prefix_rewrite); other paths need upstream-cluster (Lua) for CDS else 503.
A dedicated listener on port 8010 (or WDM_SDRC_DIRECT_LISTENER_PORT when set and non-empty) forwards paths
without upstream-cluster to sdrc_direct (no Lua). Each workload listener inlines envoy/templates/envoy_xds_mw.lua.j2
with that stanza's wl_obj_name.

xds_cluster and sdrc_direct socket_address values are filled from WDM_CONTROLLER_HOST
and WDM_CONTROLLER_PORT (defaults localhost and 5002) so the generated YAML contains
literal host/port (no runtime placeholders).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader


_HTTP_LIFECYCLE_MODE_ALIASES = frozenset(("http", "http-header", "http_header"))
_LIFECYCLE_ROUTE_FIELDS = (
    (
        "add",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD",
        "POST",
    ),
    (
        "delete",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD",
        "DELETE",
    ),
    (
        "reprovision",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD",
        "POST",
    ),
)


def _slug(workload_key: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", workload_key.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "wl"


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]+", "_", str(value)).strip("_") or "route"


def _as_bool(value) -> bool:
    """Truthy if value is Python True or common WDM truthy strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _duration(value, default: str = "300s") -> str:
    """Envoy route timeout string (e.g. '5s'). Empty/None falls back to default;
    a bare number gets an 's' suffix so config.yml may use 5 or '5s'."""
    s = "" if value is None else str(value).strip()
    if not s:
        s = default
    if re.fullmatch(r"\d+(\.\d+)?", s):
        s = f"{s}s"
    return s


def _parse_target_port_mapping(value) -> dict:
    """WDM_TARGET_PORT_MAPPING is a JSON string like '{"pod1": 30001}' (or a dict)."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _config_value(defaults: dict, entry: dict, key: str, fallback=None):
    value = entry.get(key)
    if value is not None and (not isinstance(value, str) or value.strip() != ""):
        return value
    value = defaults.get(key) if isinstance(defaults, dict) else None
    if value is not None and (not isinstance(value, str) or value.strip() != ""):
        return value
    return fallback


def _normalize_envoy_lifecycle_path(path: str) -> str:
    split = urlsplit(str(path or ""))
    normalized = split.path or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized or "/"


def _normalize_backend_lifecycle_path(path: str) -> str:
    normalized = _normalize_envoy_lifecycle_path(path)
    if normalized == "/sdrc":
        return "/"
    if normalized.startswith("/sdrc/"):
        normalized = normalized[len("/sdrc") :]
    return normalized or "/"


def _path_match_regex(path: str) -> str:
    normalized = _normalize_envoy_lifecycle_path(path)
    if normalized == "/":
        return r"^/?(?:\?.*)?$"
    return r"^%s/?(?:\?.*)?$" % re.escape(normalized)


def _build_lifecycle_routes(defaults: dict, entry: dict, wl_obj_name: str) -> list[dict]:
    mode = str(_config_value(defaults, entry, "WDM_LIFECYCLE_INGRESS_MODE", "") or "")
    if mode.strip().lower() not in _HTTP_LIFECYCLE_MODE_ALIASES:
        return []

    routes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for action, path_key, method_key, default_method in _LIFECYCLE_ROUTE_FIELDS:
        path = _config_value(defaults, entry, path_key)
        method = str(_config_value(defaults, entry, method_key, default_method) or "")
        method = method.strip().upper()
        if not path or not method:
            continue

        envoy_path = _normalize_envoy_lifecycle_path(str(path))
        binding = (method, envoy_path)
        if binding in seen:
            continue
        seen.add(binding)

        backend_path = _normalize_backend_lifecycle_path(str(path))
        routes.append(
            {
                "action": action,
                "method": method,
                "path": envoy_path,
                "path_regex": _path_match_regex(envoy_path),
                "route_name": "lifecycle_%s_%s"
                % (_sanitize_name(wl_obj_name), action),
                "rewrite_path": "/sdrc/%s%s" % (wl_obj_name, backend_path),
            }
        )
    return routes


def _build_headerless_endpoints(entry: dict) -> list[dict]:
    """Endpoints for the static headerless_service cluster, derived from this
    workload's WDM_TARGET_PORT_MAPPING. Each pod's port becomes a 127.0.0.1
    endpoint (containers run network_mode: host so loopback is the right address).
    """
    mapping = json.loads(entry.get("HEADERLESS_SERVICE_ENDPOINTS"))
    print(f"mapping: {mapping}")
    endpoints: list[dict] = []
    for endpoint in mapping:
        try:
            host, port = endpoint.split(":")
            endpoints.append({"address": host, "port_value": int(port)})
        except (TypeError, ValueError):
            continue
    print(f"headerless_endpoints: {endpoints}")
    return endpoints


def _load_config(path: Path) -> dict:
    try:
        from ruamel.yaml import YAML
    except ImportError:
        import yaml as pyyaml  # type: ignore

        with path.open(encoding="utf-8") as f:
            data = pyyaml.safe_load(f)
    else:
        y = YAML(typ="safe")
        with path.open(encoding="utf-8") as f:
            data = y.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"Expected mapping at top level: {path}")
    return data


def _gather_workloads(cfg: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for key, entry in cfg.items():
        if not isinstance(entry, dict):
            continue
        if "WDM_MS_LISTENER_PORT" not in entry or "port" not in entry:
            continue
        out.append((str(key), entry))
    out.sort(key=lambda t: int(t[1]["WDM_MS_LISTENER_PORT"]))
    return out


def _build_workload_rows(
    workloads: list[tuple[str, dict]], defaults: dict | None = None
) -> list[dict]:
    defaults = defaults or {}
    rows: list[dict] = []
    for wl_key, entry in workloads:
        slug = _slug(wl_key)
        lp = int(entry["WDM_MS_LISTENER_PORT"])
        app_port = int(entry["port"])
        wl_obj = entry.get("wl_obj_name") or wl_key
        wl_obj_name = str(entry.get("wl_obj_name") or wl_key)
        base = _sanitize_name(wl_obj)
        # /sdrc upstream path uses config wl_obj_name (e.g. sdrc-example-app-1), not the sanitized Envoy route name.
        sdrc_prefix_rewrite = f"/sdrc/{wl_obj_name}"

        # Per-workload settings baked into envoy_xds_mw.lua.j2 / envoy_config_xds_mw.yaml.j2
        # at render time. WDM_WL_REDIS_SERVER / WDM_WL_REDIS_PORT remain runtime
        # os.getenv lookups (deployment-wide on the Envoy container); the rest
        # are sourced per-workload because they need to differ between listeners.
        noheadertargetroute = _as_bool(entry.get("NOHEADERTARGETROUTE"))
        envoy_route_header = str(entry.get("ENVOY_ROUTE_HEADER") or "x-stream-id").strip()
        # Per-workload upstream route timeout for the cluster_header route
        # (normal CDS traffic). Defaults to 5s when ENVOY_ROUTE_TIMEOUT is unset.
        envoy_route_timeout = _duration(entry.get("ENVOY_ROUTE_TIMEOUT"))
        # Each workload that opts in via NOHEADERTARGETROUTE=true gets its own
        # static cluster headerless_service_<slug> populated only from this
        # workload's HEADERLESS_SERVICE_ENDPOINTS, so header-less traffic that
        # arrives on listener A never lands on a pod owned by listener B.
        headerless_endpoints = (
            _build_headerless_endpoints(entry) if noheadertargetroute else []
        )
        headerless_cluster_name = (
            f"headerless_service_{slug}" if noheadertargetroute else ""
        )

        rows.append(
            {
                "key": wl_key,
                "slug": slug,
                "wl_obj_name": wl_obj_name,
                "sdrc_prefix_rewrite": sdrc_prefix_rewrite,
                "listener_name": f"svc_listener_{slug}",
                "listener_port": lp,
                "app_port": app_port,
                "stat_prefix": f"ingress_http_{slug}",
                "route_config_name": f"{base}_route",
                "vhost_name": f"{base}_service",
                "noheadertargetroute": noheadertargetroute,
                "envoy_route_header": envoy_route_header,
                "envoy_route_timeout": envoy_route_timeout,
                "headerless_cluster_name": headerless_cluster_name,
                "headerless_endpoints": headerless_endpoints,
                "lifecycle_routes": _build_lifecycle_routes(
                    defaults, entry, wl_obj_name
                ),
            }
        )
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yml (default: repo root config.yml)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: envoy/envoy_config_xds_mw.yaml beside this script)",
    )
    p.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Directory containing envoy_config_xds_mw.yaml.j2 (default: envoy/templates/)",
    )
    p.add_argument(
        "--controller-host",
        type=str,
        default=None,
        help="xds_cluster address (default: env WDM_CONTROLLER_HOST, else localhost)",
    )
    p.add_argument(
        "--controller-port",
        type=int,
        default=None,
        help="xds_cluster port (default: env WDM_CONTROLLER_PORT, else 5002)",
    )
    p.add_argument(
        "--sdrc-direct-listener-port",
        type=int,
        default=None,
        help="Dedicated /sdrc listener port (default 8010 if WDM_SDRC_DIRECT_LISTENER_PORT unset or empty)",
    )
    p.add_argument(
        "--admin-port",
        type=int,
        default=None,
        help="Envoy admin port (default: env ENVOY_ADMIN_PORT, else 9901)",
    )
    args = p.parse_args()
    repo = Path(__file__).resolve().parent.parent
    envoy_dir = Path(__file__).resolve().parent
    config_path = args.config or (repo / "config.yml")
    out_path = args.out or (envoy_dir / "envoy_config_xds_mw.yaml")
    template_dir = args.template_dir or (envoy_dir / "templates")
    lua_j2 = template_dir / "envoy_xds_mw.lua.j2"

    controller_host = (
        (args.controller_host or "").strip()
        or os.environ.get("WDM_CONTROLLER_HOST", "").strip()
        or "localhost"
    )
    if args.controller_port is not None:
        controller_port = int(args.controller_port)
    else:
        controller_port = _env_int("WDM_CONTROLLER_PORT", 5002)

    default_admin_port = 9901
    if args.admin_port is not None:
        admin_port = int(args.admin_port)
    else:
        raw_admin = os.environ.get("ENVOY_ADMIN_PORT", "").strip()
        if not raw_admin:
            admin_port = default_admin_port
        else:
            try:
                admin_port = int(raw_admin)
            except ValueError:
                admin_port = default_admin_port

    default_sdrc_listener_port = 8010
    if args.sdrc_direct_listener_port is not None:
        sdrc_direct_listener_port = int(args.sdrc_direct_listener_port)
    else:
        raw = os.environ.get("WDM_SDRC_DIRECT_LISTENER_PORT", "").strip()
        if not raw:
            sdrc_direct_listener_port = default_sdrc_listener_port
        else:
            try:
                sdrc_direct_listener_port = int(raw)
            except ValueError:
                sdrc_direct_listener_port = default_sdrc_listener_port

    if not lua_j2.is_file():
        print(f"Missing Lua Jinja template: {lua_j2}", file=sys.stderr)
        return 1

    cfg = _load_config(config_path)
    _cfg_defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}

    def _cfg_int(key: str, default: int) -> int:
        raw = os.environ.get(key, "").strip() or str(_cfg_defaults.get(key, "")).strip()
        try:
            v = int(raw) if raw else default
        except (TypeError, ValueError):
            return default
        if not 1 <= v <= 65535:
            print(f"Warning: {key} value {raw!r} is out of range (1-65535); using {default}", file=sys.stderr)
            return default
        return v

    raw_grpc_enabled = os.environ.get("WDM_XDS_GRPC_ADS_ENABLED")
    if raw_grpc_enabled is None or str(raw_grpc_enabled).strip() == "":
        raw_grpc_enabled = _cfg_defaults.get("WDM_XDS_GRPC_ADS_ENABLED")
    grpc_xds_enabled = _as_bool(raw_grpc_enabled)
    grpc_xds_port = _cfg_int("GRPC_XDS_PORT", 4001)

    workloads = _gather_workloads(cfg)
    if not workloads:
        print(
            "No workload entries with both WDM_MS_LISTENER_PORT and port; nothing to emit.",
            file=sys.stderr,
        )
        return 1

    for wl_key, entry in workloads:
        try:
            int(entry["WDM_MS_LISTENER_PORT"])
            int(entry["port"])
        except (TypeError, ValueError) as e:
            print(f"{wl_key}: invalid WDM_MS_LISTENER_PORT or port: {e}", file=sys.stderr)
            return 1

    workload_rows = _build_workload_rows(workloads, _cfg_defaults)
    header_mapping_lines = [
        f"{wk}: listener :{e['WDM_MS_LISTENER_PORT']} → app port {e['port']}"
        for wk, e in workloads
    ]

    # One headerless static cluster per workload that opted in via
    # NOHEADERTARGETROUTE=true. Each cluster uses only that workload's
    # HEADERLESS_SERVICE_ENDPOINTS, keeping header-less traffic scoped to
    # the listener that received it.
    headerless_clusters: list[dict] = []
    for row in workload_rows:
        if not row.get("noheadertargetroute"):
            continue
        endpoints = row.get("headerless_endpoints") or []
        if not endpoints:
            continue
        headerless_clusters.append(
            {
                "name": row["headerless_cluster_name"],
                "endpoints": endpoints,
            }
        )

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template("envoy_config_xds_mw.yaml.j2")
    body = tpl.render(
        config_basename=config_path.name,
        header_mapping_lines=header_mapping_lines,
        controller_host=controller_host,
        controller_port=controller_port,
        grpc_xds_enabled=grpc_xds_enabled,
        grpc_xds_port=grpc_xds_port,
        sdrc_direct_listener_port=sdrc_direct_listener_port,
        admin_port=admin_port,
        workloads=workload_rows,
        headerless_clusters=headerless_clusters,
    )
    out_path.write_text(body, encoding="utf-8")
    xds_mode = (
        f"gRPC ADS xds_cluster_grpc {controller_host}:{grpc_xds_port}"
        if grpc_xds_enabled
        else f"REST xds_cluster {controller_host}:{controller_port}"
    )
    print(
        f"Wrote {out_path} ({len(workloads)} workload listeners + sdrc :{sdrc_direct_listener_port}, "
        f"{xds_mode}, sdrc_direct {controller_host}:{controller_port}) from Jinja template"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

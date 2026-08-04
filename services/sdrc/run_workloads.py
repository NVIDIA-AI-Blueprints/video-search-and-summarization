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

"""
Multi-workload wrapper for app.py.

Reads config.yml: optional "defaults" (override config.py Config defaults), plus name -> { wl_obj_name, port, enable, ... }. Either:
- Single workload: if WDM_WL_OBJECT_NAME is set, it must equal some entry's ``wl_obj_name`` (not the YAML section key).
  Launches app.py with that entry's port; the child env ``WDM_WL_OBJECT_NAME`` is that same ``wl_obj_name``.
- Multi workload: if WDM_WL_OBJECT_NAME is not set, launches one app.py process per
  enabled config entry, each with a unique port and WDM_WL_OBJECT_NAME set to that entry's
  ``wl_obj_name``. Optionally runs a Flask router that proxies by URI path: /sdrc/<wl_obj_name>/...
  is forwarded to the app.py instance for that workload.

Config path: WDM_WORKLOADS_CONFIG env or ./config.yml
Router port: ROUTER_PORT env or 5002 (fails if that port is in use; no fallback to the next port).

Per-workload process command: by default ``python app.py`` (or ``python -m app`` / frozen ``sdr``).
Override with ``--worker-cmd '...'``, env ``WDM_APP_WORKER_CMD``, or ``defaults:`` / workload entry
``WDM_APP_WORKER_CMD`` in config.yml (e.g. ``./sdr`` for a binary next to the repo). Shell-style
quoting is supported (see :func:`shlex.split`).

If a worker exits non-zero, it is restarted after ``WDM_APP_WORKER_RESTART_DELAY_SECONDS`` (default 3),
unless ``WDM_APP_WORKER_RESTART`` is 0/false/no. Exit code 0 ends the supervisor loop (single workload)
without restart.

Worker **subprocess** stdout/stderr (the process started for ``WDM_APP_WORKER_CMD``) is appended to
``logs/worker-<sanitized-stem>.log`` under the same base directory as the parent (so binary + workers
land under ``<exe-dir>/logs/``) and **also** copied to the parent's stdout. Disable file logging with
``WDM_APP_WORKER_LOG_TO_FILE=0`` (child inherits stdout/stderr only; no ``logs/worker-*.log``).
"""
import argparse
import json
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import redis
from ruamel.yaml import YAML

import requests
from flask import Flask, Response, g, request, jsonify, render_template

from config import Config, wdm_config_env_defaults, wdm_config_env_keys
from lib.lifecycle.http_header import (
    normalize_lifecycle_ingress_mode,
    normalize_lifecycle_method,
    normalize_lifecycle_path,
)
from lib.wdm_router_openapi import router_openapi_document
from lib.wdm_swagger_ui import openapi_public_server_root, register_wdm_swagger_ui
from lib.logging import configure_root_logging

# run_workloads-only vars (not on Config) still shown in startup env dump.
_RUN_WORKLOADS_EXTRA_PRINT_KEYS = frozenset({
    "ROUTER_HOST",
    "ROUTER_PORT",
    "DASHBOARD_HEALTH_INTERVAL_SECONDS",
    "WDM_WORKLOADS_CONFIG",
    "WDM_APP_WORKER_CMD",
    "WDM_APP_WORKER_RESTART",
    "WDM_APP_WORKER_RESTART_DELAY_SECONDS",
    "WDM_APP_WORKER_LOG_TO_FILE",
    # Used when building KUBERNETES_URL in config.py but not Config class attributes
    "KUBERNETES_HOST",
    "KUBERNETES_PORT",
})


def _runtime_base_dir() -> str:
    """Directory with app sources (dev), Cython .so modules, or sdr next to frozen executable."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _default_workloads_config_path(repo_root: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(repo_root), "config.yml")
    return os.path.join(repo_root, "config.yml")


def _parse_worker_cmd_override(raw: Optional[str]) -> Optional[list]:
    """Split a shell-style command string into argv; None if empty."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    parts = shlex.split(s)
    return parts if parts else None


def _resolve_worker_cmd_override(
    defaults: dict,
    entry: Optional[dict],
    cli_override: Optional[str],
) -> Optional[str]:
    """CLI > env > per-workload entry > config defaults."""
    if cli_override is not None and str(cli_override).strip():
        return str(cli_override).strip()
    ev = os.environ.get("WDM_APP_WORKER_CMD")
    if ev is not None and str(ev).strip():
        return str(ev).strip()
    if entry:
        v = entry.get("WDM_APP_WORKER_CMD")
        if v is not None and str(v).strip():
            return str(v).strip()
    if isinstance(defaults, dict):
        v = defaults.get("WDM_APP_WORKER_CMD")
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _app_worker_cmd(repo_root: str, override: Optional[str] = None) -> list:
    """Command to run one workload: optional override, else app.py / ``python -m app`` / frozen sdr."""
    parsed = _parse_worker_cmd_override(override)
    if parsed:
        return parsed
    if getattr(sys, "frozen", False):
        return [os.path.join(repo_root, "sdr")]
    app_py = os.path.join(repo_root, "app.py")
    if os.path.isfile(app_py):
        return [sys.executable, app_py]
    return [sys.executable, "-m", "app"]


def _worker_restart_enabled(defaults: dict) -> bool:
    return _wdm_bool_env_or_defaults("WDM_APP_WORKER_RESTART", os.environ, defaults, default=True)


def _worker_restart_delay_seconds(defaults: dict) -> float:
    raw = os.environ.get("WDM_APP_WORKER_RESTART_DELAY_SECONDS")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    d = defaults.get("WDM_APP_WORKER_RESTART_DELAY_SECONDS") if isinstance(defaults, dict) else None
    if d is not None and str(d).strip() != "":
        try:
            return max(0.0, float(d))
        except (ValueError, TypeError):
            pass
    return 3.0


def _worker_subprocess_log_enabled(defaults: dict) -> bool:
    """When true, worker stdout+stderr → logs/worker-*.log and the parent's stdout (tee)."""
    return _wdm_bool_env_or_defaults("WDM_APP_WORKER_LOG_TO_FILE", os.environ, defaults, default=True)


def _sanitize_worker_log_stem(stem: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(stem).strip())[:120]
    return s or "worker"


def _worker_subprocess_log_path(repo_root: str, stem: str) -> str:
    log_dir = os.path.join(repo_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"worker-{_sanitize_worker_log_stem(stem)}.log")


def _worker_stdout_tee_runner(proc: subprocess.Popen, log_fp) -> None:
    """Drain ``proc.stdout`` (merged stderr) into ``log_fp`` and :data:`sys.stdout` (line-buffered text)."""
    log = logging.getLogger(__name__)
    try:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except BrokenPipeError:
                pass
            log_fp.write(line)
            log_fp.flush()
    except Exception:
        log.exception("copying worker stdout to console and log file failed")
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass


def _log_worker_cmd_used(
    log: logging.Logger,
    *,
    w_cmd_override: Optional[str],
    argv: list,
    context: str,
) -> None:
    """Log resolved WDM_APP_WORKER_CMD string (or default) and final argv."""
    if w_cmd_override is not None and str(w_cmd_override).strip():
        raw = str(w_cmd_override).strip()
    else:
        raw = "(not set; default worker command)"
    log.info(
        "%s WDM_APP_WORKER_CMD=%r argv=%s",
        context,
        raw,
        argv,
    )


def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Workload config not found: {config_path}")
    with open(path, "r") as f:
        if path.suffix in (".yml", ".yaml"):
            yaml_loader = YAML()
            data = yaml_loader.load(f)
            return data if data is not None else {}
        return json.load(f)


def _find_config_entry_by_wl_obj_name(config: dict, wl_obj_name: str) -> Optional[Tuple[str, dict]]:
    """Return (config section key, entry) for the first entry whose wl_obj_name matches."""
    for name, entry in config.items():
        if isinstance(entry, dict) and entry.get("wl_obj_name") == wl_obj_name:
            return name, entry
    return None


# Not passed to worker processes (run_workloads / supervisor only).
_RUN_WORKLOADS_ENV_EXCLUDE = frozenset({
    "WDM_APP_WORKER_CMD",
    "WDM_APP_WORKER_RESTART",
    "WDM_APP_WORKER_RESTART_DELAY_SECONDS",
    "WDM_APP_WORKER_LOG_TO_FILE",
})


def build_env(defaults: dict, entry: dict, wl_obj_name: str, port: int) -> dict:
    """Build env for app.py: Config defaults, config.yml defaults, process env, then per-workload entry overrides."""
    env = dict(wdm_config_env_defaults())
    if defaults:
        for k, v in defaults.items():
            if v is not None and k not in _RUN_WORKLOADS_ENV_EXCLUDE:
                env[k] = str(v)
    env.update(os.environ)
    env["WDM_WL_OBJECT_NAME"] = wl_obj_name
    env["PORT"] = str(port)
    for k, v in entry.items():
        if k not in ("wl_obj_name", "port", "enable") and k not in _RUN_WORKLOADS_ENV_EXCLUDE and v is not None:
            env[k] = str(v)
    return env


def run_app(
    env_override: dict,
    repo_root: str,
    worker_cmd_override: Optional[str] = None,
    defaults: Optional[dict] = None,
    wl_obj_name: Optional[str] = None,
) -> int:
    """Run one worker; optionally restart on non-zero exit (see WDM_APP_WORKER_RESTART)."""
    d = defaults if isinstance(defaults, dict) else {}
    env = os.environ.copy()
    env.update(env_override)
    log = logging.getLogger(__name__)
    restart = _worker_restart_enabled(d)
    delay = _worker_restart_delay_seconds(d)
    log.info(
        "single workload: worker restart_on_nonzero=%s delay_s=%s",
        restart,
        delay,
    )
    log_to_file = _worker_subprocess_log_enabled(d)
    wl_stem = wl_obj_name if wl_obj_name is not None else env.get("WDM_WL_OBJECT_NAME", "single")
    while True:
        cmd = _app_worker_cmd(repo_root, worker_cmd_override)
        _log_worker_cmd_used(
            log,
            w_cmd_override=worker_cmd_override,
            argv=cmd,
            context="single workload:",
        )
        out_fp = None
        tee_thread = None
        try:
            if log_to_file:
                wpath = _worker_subprocess_log_path(repo_root, wl_stem)
                log.info(
                    "single workload: worker subprocess stdout/stderr → %s and parent stdout",
                    wpath,
                )
                out_fp = open(wpath, "a", encoding="utf-8", errors="replace", buffering=1)
                p = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                tee_thread = threading.Thread(
                    target=_worker_stdout_tee_runner,
                    args=(p, out_fp),
                    name="worker-stdout-tee",
                    daemon=True,
                )
                tee_thread.start()
                rc = p.wait()
                tee_thread.join(timeout=120)
                if tee_thread.is_alive():
                    log.warning("single workload: worker stdout tee thread did not finish after wait")
            else:
                rc = subprocess.run(cmd, env=env, cwd=repo_root).returncode
        finally:
            if out_fp is not None:
                out_fp.close()
        if rc == 0:
            return 0
        if not restart:
            return rc
        log.warning(
            "single workload: worker exited with code %s; retrying after %s s",
            rc,
            delay,
        )
        time.sleep(delay)


def _supervise_workload_worker(
    *,
    name: str,
    wl_obj_name: str,
    port: int,
    env: dict,
    repo_root: str,
    worker_cmd_override: Optional[str],
    defaults: dict,
    shutdown: threading.Event,
) -> None:
    """Run Popen in a loop until shutdown or exit 0 / restart disabled."""
    log = logging.getLogger(__name__)
    restart = _worker_restart_enabled(defaults)
    delay = _worker_restart_delay_seconds(defaults)
    log.info(
        "workload name=%r wl_obj_name=%r port=%s: worker restart_on_nonzero=%s delay_s=%s",
        name,
        wl_obj_name,
        port,
        restart,
        delay,
    )
    log_to_file = _worker_subprocess_log_enabled(defaults)
    while not shutdown.is_set():
        cmd = _app_worker_cmd(repo_root, worker_cmd_override)
        _log_worker_cmd_used(
            log,
            w_cmd_override=worker_cmd_override,
            argv=cmd,
            context=f"workload name={name!r} wl_obj_name={wl_obj_name!r} port={port}:",
        )
        out_fp = None
        tee_thread = None
        popen_kw = {"cwd": repo_root}
        if log_to_file:
            wpath = _worker_subprocess_log_path(repo_root, f"{name}-{wl_obj_name}-{port}")
            log.info(
                "workload name=%r: worker subprocess stdout/stderr → %s and parent stdout",
                name,
                wpath,
            )
            out_fp = open(wpath, "a", encoding="utf-8", errors="replace", buffering=1)
            popen_kw["stdout"] = subprocess.PIPE
            popen_kw["stderr"] = subprocess.STDOUT
            popen_kw["text"] = True
            popen_kw["encoding"] = "utf-8"
            popen_kw["errors"] = "replace"
            popen_kw["bufsize"] = 1
        try:
            p = subprocess.Popen(cmd, env=env, **popen_kw)
        except OSError as e:
            if out_fp is not None:
                out_fp.close()
            log.error("workload name=%r Popen failed argv=%s: %s", name, cmd, e)
            if shutdown.is_set():
                return
            if not restart:
                return
            time.sleep(delay)
            continue
        if log_to_file and out_fp is not None:
            tee_thread = threading.Thread(
                target=_worker_stdout_tee_runner,
                args=(p, out_fp),
                name=f"worker-stdout-tee-{name}",
                daemon=True,
            )
            tee_thread.start()
        log.info(
            "Started workload name=%r wl_obj_name=%r port=%s pid=%s",
            name,
            wl_obj_name,
            port,
            p.pid,
        )
        rc = None
        try:
            while True:
                if shutdown.is_set():
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                    return
                try:
                    rc = p.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except Exception:
            log.exception("workload name=%r supervisor error", name)
            if shutdown.is_set():
                return
            time.sleep(delay)
            continue
        finally:
            if tee_thread is not None:
                if tee_thread.is_alive():
                    try:
                        if p.poll() is None:
                            p.terminate()
                            try:
                                p.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                p.kill()
                    except Exception:
                        pass
                tee_thread.join(timeout=120)
                if tee_thread.is_alive():
                    log.warning(
                        "workload name=%r: worker stdout tee thread did not finish",
                        name,
                    )
            if out_fp is not None:
                out_fp.close()
        if shutdown.is_set():
            return
        if rc == 0:
            log.info("workload name=%r exited 0; supervisor stopping for this worker", name)
            return
        if not restart:
            log.warning("workload name=%r exited %s; restart disabled", name, rc)
            return
        log.warning(
            "workload name=%r wl_obj_name=%r exited code=%s; retrying after %s s",
            name,
            wl_obj_name,
            rc,
            delay,
        )
        time.sleep(delay)


def _parse_port_mapping(raw):  # str | dict -> dict or None
    """Parse WDM_TARGET_PORT_MAPPING from string or return dict as-is. Returns None on parse failure."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _wdm_bool_from_value(v, default: bool = False) -> bool:
    """Coerce YAML/config value to bool (1/true/yes/on vs 0/false/no/off). None or empty string → default."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip()
    if not s:
        return default
    return s.lower() not in ("0", "false", "no", "off", "")


def _wdm_bool_env_or_defaults(key: str, env: dict, defaults: dict, default: bool = False) -> bool:
    """True if env key or defaults key is a truthy WDM flag (1/true/yes/on; false for 0/false/no/off)."""
    raw = env.get(key)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() not in ("0", "false", "no", "off", "")
    d = defaults.get(key) if isinstance(defaults, dict) else None
    return _wdm_bool_from_value(d, default=default)


def _grpc_ads_enabled_for_multiworkload(defaults: dict, enabled_workloads: list) -> bool:
    """Whether the multi-workload router should own the single pod-level ADS listener.

    Resolution order: env → config.yml defaults → per-workload entries.
    At the per-workload tier, *any* workload with the flag set to true wins
    (true-wins semantics). An explicit false on one workload does not veto a
    true on another — use env or defaults to set a pod-wide value.
    """
    raw = os.environ.get("WDM_XDS_GRPC_ADS_ENABLED")
    if raw is not None and str(raw).strip() != "":
        return _wdm_bool_from_value(raw, default=False)

    d = defaults.get("WDM_XDS_GRPC_ADS_ENABLED") if isinstance(defaults, dict) else None
    if d is not None and str(d).strip() != "":
        return _wdm_bool_from_value(d, default=False)

    for _name, entry in enabled_workloads:
        if not isinstance(entry, dict):
            continue
        v = entry.get("WDM_XDS_GRPC_ADS_ENABLED")
        if v is not None and str(v).strip() != "" and _wdm_bool_from_value(v, default=False):
            return True
    return False


def _grpc_ads_port(defaults: dict) -> int:
    raw = os.environ.get("GRPC_XDS_PORT")
    if raw is None or str(raw).strip() == "":
        raw = defaults.get("GRPC_XDS_PORT") if isinstance(defaults, dict) else None
    if raw is None or str(raw).strip() == "":
        raw = getattr(Config, "GRPC_XDS_PORT", 4001)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 4001
    if not 1 <= port <= 65535:
        logging.getLogger(__name__).warning(
            "GRPC_XDS_PORT value %r is out of range (1-65535); defaulting to 4001", raw
        )
        return 4001
    return port


def _grpc_ads_poll_interval_seconds(defaults: dict) -> float:
    raw = os.environ.get("GRPC_XDS_POLL_INTERVAL_SECONDS")
    if raw is None or str(raw).strip() == "":
        raw = (
            defaults.get("GRPC_XDS_POLL_INTERVAL_SECONDS")
            if isinstance(defaults, dict)
            else None
        )
    if raw is None or str(raw).strip() == "":
        return 1.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _entry_prefers_pod_dns_for_xds(entry: dict, xds_app_config: dict) -> bool:
    """Match envoyxDS per-workload WDM_XDS_USE_POD_DNS (config.yml workload block overrides xds_app_config)."""
    if isinstance(entry, dict) and "WDM_XDS_USE_POD_DNS" in entry:
        return _wdm_bool_from_value(entry["WDM_XDS_USE_POD_DNS"], default=True)
    return bool(xds_app_config.get("WDM_XDS_USE_POD_DNS", True))


def build_xds_app_config(defaults: dict, workload_config: Optional[dict] = None) -> dict:
    """Build minimal app_config for envoyxDS from defaults, workload config, and env (Redis + port mapping)."""
    log = logging.getLogger(__name__)
    env = os.environ
    app_config = {}

    # WDM_WL_REDIS_SERVER: env > config defaults > "localhost"
    v = env.get("WDM_WL_REDIS_SERVER") or (defaults.get("WDM_WL_REDIS_SERVER") if isinstance(defaults.get("WDM_WL_REDIS_SERVER"), str) else None) or "localhost"
    app_config["WDM_WL_REDIS_SERVER"] = v
    if env.get("WDM_WL_REDIS_SERVER"):
        log.info("build_xds_app_config: WDM_WL_REDIS_SERVER=%s (from env)", v)
    elif defaults.get("WDM_WL_REDIS_SERVER"):
        log.info("build_xds_app_config: WDM_WL_REDIS_SERVER=%s (from config defaults)", v)
    else:
        log.info("build_xds_app_config: WDM_WL_REDIS_SERVER=%s (fallback)", v)

    # WDM_WL_REDIS_PORT: env > config defaults > 6379
    v = env.get("WDM_WL_REDIS_PORT") or (defaults.get("WDM_WL_REDIS_PORT") if defaults.get("WDM_WL_REDIS_PORT") is not None else None) or 6379
    if isinstance(v, str):
        try:
            v = int(v)
        except ValueError:
            v = 6379
    app_config["WDM_WL_REDIS_PORT"] = v
    if env.get("WDM_WL_REDIS_PORT") is not None and str(env.get("WDM_WL_REDIS_PORT")).strip():
        log.info("build_xds_app_config: WDM_WL_REDIS_PORT=%s (from env)", v)
    elif defaults.get("WDM_WL_REDIS_PORT") is not None:
        log.info("build_xds_app_config: WDM_WL_REDIS_PORT=%s (from config defaults)", v)
    else:
        log.info("build_xds_app_config: WDM_WL_REDIS_PORT=%s (fallback)", v)

    # WDM_TARGET_PORT_MAPPING: env > config defaults > config.py default. Workloads without this key in config.yml get this default in clusterXDs.
    raw = env.get("WDM_TARGET_PORT_MAPPING")
    source = "env"
    if not raw:
        raw = defaults.get("WDM_TARGET_PORT_MAPPING") if isinstance(defaults.get("WDM_TARGET_PORT_MAPPING"), (str, dict)) else None
        source = "config defaults"
    parsed = _parse_port_mapping(raw) if raw else None
    if parsed:
        app_config["WDM_TARGET_PORT_MAPPING"] = parsed
        log.info("build_xds_app_config: WDM_TARGET_PORT_MAPPING=%s (from %s)", parsed, source)
    else:
        from config import Config
        app_config["WDM_TARGET_PORT_MAPPING"] = Config.WDM_TARGET_PORT_MAPPING
        if raw:
            log.info("build_xds_app_config: WDM_TARGET_PORT_MAPPING=%s (from config.py, parse failed for %s)", app_config["WDM_TARGET_PORT_MAPPING"], source)
        else:
            log.info("build_xds_app_config: WDM_TARGET_PORT_MAPPING=%s (default is set in config.py, used by app.py)", app_config["WDM_TARGET_PORT_MAPPING"])

    app_config["WDM_WL_OBJECT_NAME"] = ""  # not used when workload_config is passed

    # ENVOY_ROUTE_URL_PREFIX, ENVOY_ROUTE_URL_PREFIX_REWRITE, ENVOY_REQUEST_TIMEOUT: required by envoyxDS.routeXDs
    v = env.get("ENVOY_ROUTE_URL_PREFIX") or (defaults.get("ENVOY_ROUTE_URL_PREFIX") if isinstance(defaults.get("ENVOY_ROUTE_URL_PREFIX"), str) else None) or "/"
    app_config["ENVOY_ROUTE_URL_PREFIX"] = v
    v = env.get("ENVOY_ROUTE_URL_PREFIX_REWRITE") or (defaults.get("ENVOY_ROUTE_URL_PREFIX_REWRITE") if isinstance(defaults.get("ENVOY_ROUTE_URL_PREFIX_REWRITE"), str) else None) or "/hello"
    app_config["ENVOY_ROUTE_URL_PREFIX_REWRITE"] = v
    v = env.get("ENVOY_REQUEST_TIMEOUT")
    if v is not None and str(v).strip() != "":
        try:
            app_config["ENVOY_REQUEST_TIMEOUT"] = int(v)
        except ValueError:
            app_config["ENVOY_REQUEST_TIMEOUT"] = 5
    elif defaults.get("ENVOY_REQUEST_TIMEOUT") is not None:
        try:
            app_config["ENVOY_REQUEST_TIMEOUT"] = int(defaults["ENVOY_REQUEST_TIMEOUT"])
        except (ValueError, TypeError):
            app_config["ENVOY_REQUEST_TIMEOUT"] = 5
    else:
        from config import Config
        app_config["ENVOY_REQUEST_TIMEOUT"] = getattr(Config, "ENVOY_REQUEST_TIMEOUT", 5)

    # Optional: proxy /dashboard (and subpaths) on Envoy to WDM run_workloads router (CDS + RDS).
    # Disable with WDM_ENVOY_DASHBOARD_PROXY=0. Override host/port with WDM_CONTROLLER_HOST / WDM_CONTROLLER_PORT .
    dash_on = env.get("WDM_ENVOY_DASHBOARD_PROXY", "1").strip().lower() not in ("0", "false", "no", "off")
    app_config["WDM_ENVOY_DASHBOARD_PROXY"] = dash_on
    if dash_on:
        app_config["WDM_CONTROLLER_HOST"] = env.get(
            "WDM_CONTROLLER_HOST", "localhost"
        ).strip() or "localhost"
        dp = env.get("WDM_CONTROLLER_PORT") or env.get("ROUTER_PORT") or "5002"
        try:
            app_config["WDM_CONTROLLER_PORT"] = int(dp)
        except (ValueError, TypeError):
            app_config["WDM_CONTROLLER_PORT"] = 5002
        log.info(
            "build_xds_app_config: WDM_CONTROLLER_HOST=%s WDM_CONTROLLER_PORT=%s (WDM_ENVOY_DASHBOARD_PROXY enabled)",
            app_config["WDM_CONTROLLER_HOST"],
            app_config["WDM_CONTROLLER_PORT"],
        )

    # Optional CDS cluster name (e.g. mypod-websocket) used when upstream-cluster header is unset.
    # Avoids 503 on /hello WebSocket when Lua cannot resolve stream id in Redis (see envoyxDS._rds_tail_routes_cluster_header_or_fallback).
    fb_cluster = env.get("WDM_ENVOY_FALLBACK_CLUSTER", "").strip()
    if fb_cluster:
        app_config["WDM_ENVOY_FALLBACK_CLUSTER"] = fb_cluster
        log.info("build_xds_app_config: WDM_ENVOY_FALLBACK_CLUSTER=%s", fb_cluster)

    app_config["WDM_XDS_USE_IP_ADDRESS"] = _wdm_bool_env_or_defaults(
        "WDM_XDS_USE_IP_ADDRESS", os.environ, defaults, default=False
    )
    app_config["WDM_XDS_USE_POD_DNS"] = _wdm_bool_env_or_defaults(
        "WDM_XDS_USE_POD_DNS", os.environ, defaults, default=True
    )
    app_config["WDM_XDS_USE_IP_ADDRESS"] = _merge_workload_xds_flags_into_app_config(
        workload_config, "WDM_XDS_USE_IP_ADDRESS", app_config["WDM_XDS_USE_IP_ADDRESS"]
    )
    app_config["WDM_XDS_USE_POD_DNS"] = _merge_workload_xds_flags_into_app_config(
        workload_config, "WDM_XDS_USE_POD_DNS", app_config["WDM_XDS_USE_POD_DNS"]
    )
    if app_config["WDM_XDS_USE_POD_DNS"] or app_config["WDM_XDS_USE_IP_ADDRESS"]:
        log.info(
            "build_xds_app_config: WDM_XDS_USE_POD_DNS=%s WDM_XDS_USE_IP_ADDRESS=%s",
            app_config["WDM_XDS_USE_POD_DNS"],
            app_config["WDM_XDS_USE_IP_ADDRESS"],
        )

    return app_config


def _merge_workload_xds_flags_into_app_config(
    workload_config: Optional[dict],
    flag_key: str,
    current: bool,
) -> bool:
    """
    If env did not set an XDS flag, use the value from workload config.yml entries when all enabled workloads agree.
    (``current`` already includes config ``defaults:`` from _wdm_bool_env_or_defaults.)
    """
    ev = os.environ.get(flag_key)
    if ev is not None and str(ev).strip() != "":
        return current
    if not isinstance(workload_config, dict) or not workload_config:
        return current
    vals = []
    for _name, ent in workload_config.items():
        if not isinstance(ent, dict) or not ent.get("enable", True):
            continue
        if flag_key in ent:
            vals.append(_wdm_bool_from_value(ent[flag_key], default=(flag_key == "WDM_XDS_USE_POD_DNS")))
    if not vals:
        return current
    if all(v == vals[0] for v in vals):
        return vals[0]
    log = logging.getLogger(__name__)
    log.warning(
        "build_xds_app_config: %s differs across workloads; using env/defaults value %s",
        flag_key,
        current,
    )
    return current


def build_redis_stream_config(defaults: dict) -> Optional[dict]:
    """Build Redis stream config for global add (XADD): stream from WDM_REDIS_MSG_KEY, field, host, port."""
    env = os.environ
    stream = env.get("WDM_REDIS_MSG_KEY") or (
        defaults.get("WDM_REDIS_MSG_KEY") if isinstance(defaults.get("WDM_REDIS_MSG_KEY"), str) else None
    ) or "vst_events"
    field = env.get("WDM_WL_REDIS_MSG_FIELD") or (defaults.get("WDM_WL_REDIS_MSG_FIELD") if isinstance(defaults.get("WDM_WL_REDIS_MSG_FIELD"), str) else None) or "sensor.id"
    host = env.get("WDM_WL_REDIS_SERVER") or (defaults.get("WDM_WL_REDIS_SERVER") if isinstance(defaults.get("WDM_WL_REDIS_SERVER"), str) else None) or "localhost"
    port = env.get("WDM_WL_REDIS_PORT") or (defaults.get("WDM_WL_REDIS_PORT") if defaults.get("WDM_WL_REDIS_PORT") is not None else None) or 6379
    if isinstance(port, str):
        try:
            port = int(port)
        except ValueError:
            port = 6379
    return {"stream": stream, "field": field, "host": host, "port": port}


def _first_enabled_workload_str(workload_config: Optional[dict], key: str) -> Optional[str]:
    """First non-empty string value for ``key`` on an enabled workload entry (config.yml block)."""
    if not isinstance(workload_config, dict):
        return None
    for entry in workload_config.values():
        if not isinstance(entry, dict) or not entry.get("enable", True):
            continue
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _dashboard_config_value(defaults: dict, entry: dict, key: str, fallback=None):
    if isinstance(entry, dict) and entry.get(key) is not None:
        value = entry.get(key)
        if not isinstance(value, str) or value.strip() != "":
            return value
    env_value = os.environ.get(key)
    if env_value is not None and str(env_value).strip() != "":
        return env_value
    if isinstance(defaults, dict) and defaults.get(key) is not None:
        value = defaults.get(key)
        if not isinstance(value, str) or value.strip() != "":
            return value
    return getattr(Config, key, fallback)


def _dashboard_lifecycle_header(defaults: dict, entry: dict) -> str:
    header = _dashboard_config_value(
        defaults, entry, "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER", None
    )
    if header is not None and str(header).strip():
        return str(header).strip()

    for key in ("ENVOY_ROUTE_HEADER", "ENVOYROUTEHEADER"):
        header = _dashboard_config_value(defaults, entry, key, None)
        if header is not None and str(header).strip():
            return str(header).strip()
    return "id"


def _dashboard_lifecycle_binding_warning(lifecycle_config: dict) -> str:
    if lifecycle_config.get("mode") != "http-header":
        return ""

    seen = {}
    for action, method_key, path_key in (
        ("add", "add_method", "add_path"),
        ("delete", "delete_method", "delete_path"),
        ("reprovision", "reprovision_method", "reprovision_path"),
    ):
        method = normalize_lifecycle_method(lifecycle_config.get(method_key))
        path = normalize_lifecycle_path(lifecycle_config.get(path_key))
        if not method or not path:
            continue
        binding = (method, path)
        previous = seen.get(binding)
        if previous is not None:
            if {previous, action} == {"add", "reprovision"}:
                continue
            return (
                "Duplicate HTTP lifecycle binding %s %s is used by %s and %s; "
                "only add and reprovision may share a binding."
            ) % (method, path, previous, action)
        seen[binding] = action
    return ""


def _dashboard_lifecycle_config(defaults: dict, entry: dict) -> dict:
    mode = normalize_lifecycle_ingress_mode(
        _dashboard_config_value(defaults, entry, "WDM_LIFECYCLE_INGRESS_MODE")
    )
    lifecycle_config = {
        "mode": mode,
        "stream_id_header": _dashboard_lifecycle_header(defaults, entry),
        "add_path": str(
            _dashboard_config_value(
                defaults,
                entry,
                "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH",
            )
        ),
        "add_method": str(
            _dashboard_config_value(
                defaults, entry, "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD", "POST"
            )
        ).upper(),
        "delete_path": str(
            _dashboard_config_value(
                defaults,
                entry,
                "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH",
            )
        ),
        "delete_method": str(
            _dashboard_config_value(
                defaults, entry, "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD", "DELETE"
            )
        ).upper(),
        "reprovision_path": str(
            _dashboard_config_value(
                defaults,
                entry,
                "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH",
            )
        ),
        "reprovision_method": str(
            _dashboard_config_value(
                defaults,
                entry,
                "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD",
                "POST",
            )
        ).upper(),
        "id_field": str(
            _dashboard_config_value(defaults, entry, "WDM_WL_ID_FIELD", "camera_id")
        ),
        "reprovision_change_id": str(
            _dashboard_config_value(
                defaults, entry, "WDM_WL_CHANGE_ID_REPROVISION", "reprovision"
            )
        ),
    }
    lifecycle_config["binding_warning"] = _dashboard_lifecycle_binding_warning(
        lifecycle_config
    )
    return lifecycle_config


def build_kafka_global_add_config(
    defaults: dict, workload_config: Optional[dict] = None
) -> Optional[dict]:
    """Bootstrap URL, topic, and record key for dashboard Kafka produce.

    Resolution order for each setting: process env → config.yml ``defaults:`` → first enabled workload
    entry → :class:`config.Config` (for bootstrap only, matches app.py).
    """
    env = os.environ
    raw_bs = env.get("WDM_KFK_BOOTSTRAP_URL")
    if raw_bs is not None and str(raw_bs).strip() != "":
        bootstrap = str(raw_bs).strip()
    else:
        d_bs = defaults.get("WDM_KFK_BOOTSTRAP_URL") if isinstance(defaults.get("WDM_KFK_BOOTSTRAP_URL"), str) else None
        if d_bs and str(d_bs).strip():
            bootstrap = str(d_bs).strip()
        else:
            w_bs = _first_enabled_workload_str(workload_config, "WDM_KFK_BOOTSTRAP_URL")
            if w_bs:
                bootstrap = w_bs
            else:
                bootstrap = str(getattr(Config, "WDM_KFK_BOOTSTRAP_URL", "") or "").strip() or "localhost:9092"
    if not bootstrap:
        return None

    topic = env.get("WDM_MSG_TOPIC") or (
        defaults.get("WDM_MSG_TOPIC") if isinstance(defaults.get("WDM_MSG_TOPIC"), str) else None
    ) or _first_enabled_workload_str(workload_config, "WDM_MSG_TOPIC") or "mdx-notification"

    msg_key = env.get("WDM_KAFKA_MSG_KEY") or (
        defaults.get("WDM_KAFKA_MSG_KEY") if isinstance(defaults.get("WDM_KAFKA_MSG_KEY"), str) else None
    ) or _first_enabled_workload_str(workload_config, "WDM_KAFKA_MSG_KEY") or "sensor"

    return {"bootstrap_servers": bootstrap, "topic": topic, "msg_key": msg_key}


def create_router_app(
    wl_obj_name_to_port: dict,
    workload_config: dict,
    xds_app_config: dict,
    backend_host: str = "127.0.0.1",
    health_interval_seconds: int = 15,
    workload_config_all: Optional[dict] = None,
    redis_stream_config: Optional[dict] = None,
    kafka_global_add_config: Optional[dict] = None,
    config_defaults: Optional[dict] = None,
) -> Flask:
    """Flask app that routes /sdrc/<wl_obj_name>/... to the app.py instance on the workload's port."""
    from lib.xDS.envoyxDS import envoyxDS

    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
    envy = envoyxDS(xds_app_config)

    if str(os.environ.get("WDM_TRUST_PROXY_HEADERS", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
    ):
        from werkzeug.middleware.proxy_fix import ProxyFix

        _pfx = str(os.environ.get("WDM_TRUST_PROXY_PREFIX", "1")).strip().lower() not in (
            "0",
            "false",
            "no",
        )
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_port=0,
            x_prefix=1 if _pfx else 0,
        )

    register_wdm_swagger_ui(
        app,
        "/api/docs",
        "../../openapi.json",
        "SDR Controller API",
        blueprint_name="swagger_ui_wdm_router",
    )

    @app.route("/openapi.json", methods=["GET"])
    def router_openapi_json():
        doc = dict(router_openapi_document())
        try:
            root = openapi_public_server_root()
            if root:
                doc["servers"] = [{"url": root + "/", "description": "This deployment"}]
        except Exception:
            pass
        return Response(
            json.dumps(doc, indent=2),
            mimetype="application/vnd.oai.openapi+json;version=3.0",
        )

    _DASHBOARD_CORS_OVERRIDE = "X-SDRC-Dashboard-CORS-Override"
    _ACAH_DASHBOARD = (
        "content-type, accept, upgrade, connection, sec-websocket-key, sec-websocket-version, "
        "sec-websocket-extensions, sec-websocket-protocol, x-wl-object-name, x-stream-id, id, "
        "x-wdm-dashboard-cors-override"
    )

    @app.before_request
    def _wdm_router_request_timer_start():
        g._wdm_request_t0 = time.perf_counter()

    @app.before_request
    def _cors_preflight_when_dashboard_override_requested():
        if request.method != "OPTIONS":
            return None
        acrh = (request.headers.get("Access-Control-Request-Headers") or "").lower()
        if "x-wdm-dashboard-cors-override" not in acrh:
            return None
        return Response("", status=204)

    @app.after_request
    def _apply_cors_when_dashboard_override(resp):
        acrh = (request.headers.get("Access-Control-Request-Headers") or "").lower()
        if request.headers.get(_DASHBOARD_CORS_OVERRIDE) or (
            request.method == "OPTIONS" and "x-wdm-dashboard-cors-override" in acrh
        ):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
            resp.headers["Access-Control-Allow-Headers"] = _ACAH_DASHBOARD
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.after_request
    def _wdm_router_request_timer_log(resp):
        t0 = getattr(g, "_wdm_request_t0", None)
        if t0 is not None:
            elapsed = time.perf_counter() - t0
            app.logger.info(
                "http_request %s %s status=%s elapsed_s=%.6f",
                request.method,
                request.path,
                resp.status_code,
                elapsed,
            )
        return resp

    # Build workload list for dashboard (all workloads, including disabled): name, wl_obj_name, main_url, enabled
    all_config = workload_config_all if workload_config_all is not None else workload_config
    dashboard_workloads = []
    for name, entry in sorted(all_config.items(), key=lambda x: x[0]):
        lifecycle_config = _dashboard_lifecycle_config(config_defaults or {}, entry)
        dashboard_workloads.append(
            {
                "name": name,
                "wl_obj_name": entry["wl_obj_name"],
                "main_url": f"/sdrc/{entry['wl_obj_name']}/",
                "enabled": entry.get("enable", True),
                "cluster_type": (
                    str(entry.get("WDM_CLUSTER_TYPE") or "").strip().lower()
                ),
                "lifecycle": lifecycle_config,
            }
        )

    @app.route("/", methods=["GET"])
    def index():
        links = [f"/sdrc/{name}/" for name in sorted(wl_obj_name_to_port.keys())]
        body = json.dumps(
            {
                "workloads": list(wl_obj_name_to_port.keys()),
                "paths": links,
                "openapi": "/openapi.json",
                "api_docs": "/api/docs/",
            },
            indent=2,
        )
        return Response(body, mimetype="application/json")

    @app.route("/dashboard", methods=["GET"])
    def dashboard():
        return render_template(
            "dashboard.html",
            workloads=dashboard_workloads,
            health_interval_seconds=health_interval_seconds,
            health_api_url="/dashboard/health",
            global_add_redis_available=redis_stream_config is not None,
            global_add_kafka_available=kafka_global_add_config is not None,
        )

    @app.route("/dashboard/health", methods=["GET"])
    def dashboard_health():
        """Return health status, sensor count, and pod count of each workload."""
        result = {}
        health_path = "/healthz"
        # Use name_id_url endpoint: returns dict keyed by WDM_WL_ID_FIELD (e.g. camera_id)
        streams_path = "/current_distributed_streams_name_id_url"
        replica_data_path = "/get_wl_replica_data"
        timeout = 3
        for wl_obj_name, port in wl_obj_name_to_port.items():
            url = f"http://{backend_host}:{port}{health_path}"
            try:
                resp = requests.get(url, timeout=timeout)
                if resp.status_code == 200:
                    result[wl_obj_name] = {"status": "up", "status_code": resp.status_code}
                    try:
                        streams_resp = requests.get(
                            f"http://{backend_host}:{port}{streams_path}",
                            timeout=timeout,
                        )
                        if streams_resp.status_code == 200:
                            streams_data = streams_resp.json()
                            # Dict keyed by id (e.g. camera_id); count = number of sensor ids
                            result[wl_obj_name]["sensor_count"] = (
                                len(streams_data) if isinstance(streams_data, dict) else 0
                            )
                        else:
                            result[wl_obj_name]["sensor_count"] = None
                    except (requests.RequestException, ValueError, TypeError):
                        result[wl_obj_name]["sensor_count"] = None
                    try:
                        replica_resp = requests.get(
                            f"http://{backend_host}:{port}{replica_data_path}",
                            timeout=timeout,
                        )
                        if replica_resp.status_code == 200:
                            replica_data = replica_resp.json()
                            # total_replicas = all pods; running_pods = running only
                            result[wl_obj_name]["pod_count"] = replica_data.get("total_replicas")
                            if result[wl_obj_name]["pod_count"] is None:
                                result[wl_obj_name]["pod_count"] = replica_data.get("running_pods")
                        else:
                            result[wl_obj_name]["pod_count"] = None
                    except (requests.RequestException, ValueError, TypeError):
                        result[wl_obj_name]["pod_count"] = None
                else:
                    result[wl_obj_name] = {"status": "down", "status_code": resp.status_code, "error": f"HTTP {resp.status_code}"}
            except requests.RequestException as e:
                result[wl_obj_name] = {"status": "down", "error": str(e)}
        return jsonify(result)

    def _workload_config_with_pod_list():
        """Enrich workload_config with pod_list from each active workload (pod name -> dns/name, pod name -> ip)."""
        enriched = {}
        pod_list_path = "/pod_list"
        timeout = 3
        for wl_name, entry in workload_config.items():
            entry_copy = dict(entry)
            if not entry_copy.get("enable", True):
                enriched[wl_name] = entry_copy
                continue
            wl_obj_name = entry_copy.get("wl_obj_name")
            port = wl_obj_name_to_port.get(wl_obj_name) if wl_obj_name else None
            if port is None:
                port = entry_copy.get("port")
            if port is not None:
                try:
                    resp = requests.get(
                        f"http://{backend_host}:{port}{pod_list_path}",
                        timeout=timeout,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        pods = data.get("pods") or []
                        prefer_dns = _entry_prefers_pod_dns_for_xds(entry_copy, xds_app_config)
                        # pod name -> address (pod DNS for clusterXDS; podIp only when not forcing DNS)
                        cluster_pod_list = {}
                        cluster_pod_ip_list = {}
                        for p in pods:
                            pod_name = p.get("podName", "")
                            if not pod_name:
                                continue
                            dns = (p.get("podDns") or p.get("podName") or "").strip() or pod_name
                            cluster_pod_list[pod_name] = dns
                            if not prefer_dns and p.get("podIp"):
                                cluster_pod_ip_list[pod_name] = str(p.get("podIp", "")).strip()
                        entry_copy["cluster_pod_list"] = cluster_pod_list
                        entry_copy["cluster_pod_ip_list"] = cluster_pod_ip_list
                except (requests.RequestException, ValueError, TypeError, KeyError):
                    pass
            enriched[wl_name] = entry_copy
        return enriched

    def _default_route_resource_names(config_with_pods: dict) -> list:
        return [
            entry["wl_obj_name"]
            for entry in config_with_pods.values()
            if entry.get("enable", True) and entry.get("wl_obj_name")
        ]

    class _RouterAdsXds:
        """ADS adapter backed by the router's all-workload xDS view."""

        def clusterXDs(self):
            resp = envy.clusterXDs(_workload_config_with_pod_list())
            if isinstance(resp, dict):
                resp["version_info"] = self._get_xds_version()
            return resp

        def routeXDs(self, resource_names=None):
            # None and [] are both treated as "no filter" — triggers full pod-list fan-out.
            # The ADS layer normalises empty wire lists to None before calling here.
            if resource_names:
                rn_list = list(resource_names)
            else:
                rn_list = _default_route_resource_names(_workload_config_with_pod_list())
            resp = envy.routeXDs(resource_names=rn_list)
            if isinstance(resp, dict):
                resp["version_info"] = self._get_xds_version()
            return resp

        def _get_xds_version(self):
            versions = []
            for entry in workload_config.values():
                if not isinstance(entry, dict) or not entry.get("enable", True):
                    continue
                wl_obj_name = entry.get("wl_obj_name")
                if not wl_obj_name:
                    continue
                key = "%s-xds-version" % wl_obj_name
                try:
                    value = envy.redis_connection.get(key)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "router ADS: failed to read xDS version key %s",
                        key,
                        exc_info=True,
                    )
                    value = None
                version = value if isinstance(value, str) else "1"
                versions.append("%s:%s" % (wl_obj_name, version))
            return "|".join(sorted(versions)) if versions else "1"

    app.wdm_ads_xds = _RouterAdsXds()

    @app.route("/v3/discovery:clusters", methods=["POST"])
    def indexClusterXDS():
        return jsonify(app.wdm_ads_xds.clusterXDs())

    @app.route("/dashboard/clusterxds", methods=["GET"])
    def dashboard_clusterxds():
        """Return clusterXDS output for display in the dashboard."""
        return jsonify(app.wdm_ads_xds.clusterXDs())

    @app.route("/dashboard/config_yml", methods=["GET"])
    def dashboard_config_yml():
        """Return config.yml (workload config) path and content for display."""
        repo_root = _runtime_base_dir()
        config_path = os.environ.get("WDM_WORKLOADS_CONFIG") or _default_workloads_config_path(
            repo_root
        )
        path_obj = Path(config_path)
        if not path_obj.is_file():
            return jsonify({"error": "Config file not found", "path": config_path}), 404
        try:
            content = path_obj.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return jsonify({"error": str(e), "path": config_path}), 500
        return jsonify({"path": str(path_obj.resolve()), "content": content})

    @app.route("/dashboard/global_add", methods=["POST"])
    def dashboard_global_add():
        """Send JSON payload to Redis (XADD) or Kafka (produce). Select with ?transport=redis|kafka."""
        if request.content_type != "application/json":
            return jsonify({"error": "Content-Type must be application/json"}), 400
        transport = (request.args.get("transport") or "redis").strip().lower()
        if transport not in ("redis", "kafka"):
            return jsonify({"error": "transport must be redis or kafka"}), 400
        try:
            payload = request.get_json(force=True)
        except Exception as e:
            return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
        if payload is None:
            return jsonify({"error": "JSON body required"}), 400

        if transport == "redis":
            if not redis_stream_config:
                return jsonify({"error": "Global add (Redis) not configured"}), 503
            stream = redis_stream_config["stream"]
            field = redis_stream_config["field"]
            host = redis_stream_config["host"]
            port = redis_stream_config["port"]
            try:
                r = redis.StrictRedis(host=host, port=port, decode_responses=True)
                msg_value = json.dumps(payload)
                msg_id = r.xadd(stream, {field: msg_value})
                return jsonify(
                    {"status": "ok", "transport": "redis", "stream": stream, "msg_id": msg_id}
                ), 200
            except redis.RedisError as e:
                return jsonify({"error": "Redis error", "detail": str(e)}), 500

        if not kafka_global_add_config:
            return jsonify({"error": "Global add (Kafka) not configured"}), 503
        from kafka import KafkaProducer
        from kafka.errors import KafkaError

        kcfg = kafka_global_add_config
        bs = kcfg["bootstrap_servers"]
        if isinstance(bs, str):
            servers = [s.strip() for s in bs.split(",") if s.strip()]
        else:
            servers = bs
        producer = None
        try:
            producer = KafkaProducer(bootstrap_servers=servers)
            key_b = str(kcfg["msg_key"]).encode("utf-8")
            value_b = json.dumps(payload).encode("utf-8")
            future = producer.send(str(kcfg["topic"]), key=key_b, value=value_b)
            producer.flush()
            meta = future.get(timeout=15)
            return jsonify(
                {
                    "status": "ok",
                    "transport": "kafka",
                    "topic": meta.topic,
                    "partition": meta.partition,
                    "offset": meta.offset,
                }
            ), 200
        except KafkaError as e:
            logging.getLogger(__name__).warning(
                "dashboard global_add kafka failed (bootstrap=%s topic=%s): %s",
                servers,
                kcfg.get("topic"),
                e,
            )
            return jsonify({"error": "Kafka error", "detail": str(e)}), 500
        finally:
            if producer is not None:
                producer.close()

    @app.route("/v3/discovery:routes", methods=["POST"])
    def indexRouteXDS():
        """RDS via envy.routeXDs(resource_names=...). Request resource_names selects subsets; if omitted, all enabled wl_obj_name values from config_with_pods."""
        try:
            body = request.get_json(force=True, silent=True) or {}
        except Exception:
            body = {}
        resource_names = body.get("resource_names") or []

        config_with_pods = _workload_config_with_pod_list()
        if resource_names:
            rn_list = resource_names
        else:
            rn_list = _default_route_resource_names(config_with_pods)
        out = app.wdm_ads_xds.routeXDs(resource_names=rn_list)
        if resource_names and (not isinstance(out, dict) or not out.get("resources")):
            err = "no route configs for requested resource_names"
        else:
            err = None

        if err:
            return Response(
                json.dumps({"error": err, "resource_names": resource_names}),
                status=404,
                mimetype="application/json",
            )
        return jsonify(out)

    @app.route("/sdrc/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def proxy(path: str):
        parts = path.strip("/").split("/", 1)
        wl_obj_name = parts[0]
        backend_path = ("/" + parts[1]) if len(parts) > 1 else "/"
        if wl_obj_name not in wl_obj_name_to_port:
            return Response(
                json.dumps({"error": "unknown workload", "wl_obj_name": wl_obj_name}),
                status=404,
                mimetype="application/json",
            )
        port = wl_obj_name_to_port[wl_obj_name]
        url = f"http://{backend_host}:{port}{backend_path}"
        if request.query_string:
            url += "?" + request.query_string.decode("utf-8")
        headers = {
            k: v for k, v in request.headers if k.lower() not in ("host", "connection", "content-length")
        }
        try:
            resp = requests.request(
                method=request.method,
                url=url,
                headers=headers,
                data=request.get_data(),
                timeout=60,
                allow_redirects=False,
            )
            excluded = {"transfer-encoding", "content-encoding", "connection"}
            resp_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
            return Response(
                resp.content,
                status=resp.status_code,
                headers=resp_headers,
                mimetype=resp.headers.get("Content-Type"),
            )
        except requests.RequestException as e:
            return Response(
                json.dumps({"error": "proxy failed", "detail": str(e)}),
                status=502,
                mimetype="application/json",
            )

    return app


def _start_controller_background_watchers() -> None:
    """PodErrorWatcher, AgentWatcher, Autoscaler from lib.controller (run via run_workloads.py)."""
    from lib.controller import AgentWatcher, Autoscaler, PodErrorWatcher

    if Config.REPROVISION_ENABLED:
        PodErrorWatcher()
    if Config.AGENT_CHECK_ENABLED:
        AgentWatcher()
    if Config.AUTOSCALE_ENABLED:
        Autoscaler()


def print_env_settings(env: Optional[dict] = None) -> None:
    """Print WDM-relevant environment variables (keys from config.py Config + run_workloads extras)."""
    source = env if env is not None else os.environ
    keys = wdm_config_env_keys() | _RUN_WORKLOADS_EXTRA_PRINT_KEYS
    print("=== SDRC environment settings (before app.py) ===")
    for k in sorted(source.keys()):
        if k in keys:
            print(f"  {k}={source[k]}")
    print("=== End SDRC environment settings ===")


def _configure_logging() -> None:
    """Root logging for run_workloads, router, and controller (same setup as app.py)."""
    configure_root_logging("run-workloads", _runtime_base_dir())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run SDRC workloads (single app or multi + router).",
    )
    parser.add_argument(
        "--worker-cmd",
        "-W",
        metavar="CMD",
        help="Command to run per workload instead of python app.py (e.g. ./sdr). "
        "Overrides WDM_APP_WORKER_CMD env and config.yml.",
    )
    args = parser.parse_args()
    worker_cmd_cli = args.worker_cmd

    _configure_logging()
    repo_root = _runtime_base_dir()
    config_path = os.environ.get("WDM_WORKLOADS_CONFIG") or _default_workloads_config_path(
        repo_root
    )
    raw = load_config(config_path)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    config = {k: v for k, v in raw.items() if k != "defaults" and isinstance(v, dict)}

    # Validate config: each workload entry must have wl_obj_name
    for name, entry in config.items():
        if "wl_obj_name" not in entry:
            print(f"config.yml: entry '{name}' must have 'wl_obj_name'", file=sys.stderr)
            return 1

    def is_enabled(entry: dict) -> bool:
        return entry.get("enable", True)

    wl_filter = os.environ.get("WDM_WL_OBJECT_NAME", "").strip()

    if wl_filter:
        # Single workload: WDM_WL_OBJECT_NAME must match an entry's wl_obj_name (see config.yml).
        found = _find_config_entry_by_wl_obj_name(config, wl_filter)
        if found is None:
            wlnames = sorted(
                {
                    e["wl_obj_name"]
                    for e in config.values()
                    if isinstance(e, dict) and isinstance(e.get("wl_obj_name"), str)
                }
            )
            print(
                f"WDM_WL_OBJECT_NAME='{wl_filter}' does not match any config entry's wl_obj_name. "
                f"wl_obj_name values: {wlnames}",
                file=sys.stderr,
            )
            return 1
        workload_key, entry = found
        if not is_enabled(entry):
            print(
                f"Workload '{workload_key}' (wl_obj_name='{wl_filter}') is disabled (enable=false) in config.yml",
                file=sys.stderr,
            )
            return 1
        wl_obj_name = entry["wl_obj_name"]
        port = entry.get("port", int(defaults.get("PORT", 5002)))
        _start_controller_background_watchers()
        env = build_env(defaults, entry, wl_obj_name, port)
        print_env_settings(env)
        w_cmd = _resolve_worker_cmd_override(defaults, entry, worker_cmd_cli)
        return run_app(
            env,
            repo_root,
            worker_cmd_override=w_cmd,
            defaults=defaults,
            wl_obj_name=wl_obj_name,
        )
    else:
        # Multi workload: launch one process per enabled config entry, then run Flask router
        enabled = [(n, e) for n, e in config.items() if is_enabled(e)]
        if not enabled:
            print("No enabled workloads in config.yml", file=sys.stderr)
            return 1
        _start_controller_background_watchers()
        base_port = int(defaults.get("PORT", 5002)) if isinstance(defaults.get("PORT"), (int, str)) else 5002
        if isinstance(base_port, str):
            try:
                base_port = int(base_port)
            except ValueError:
                base_port = 5002
        # Resolve ports first so we can detect duplicates (same port → only one app binds; router still maps both names to that port → wrong backend).
        resolved_ports = []
        for i, (name, entry) in enumerate(enabled):
            p = entry.get("port", base_port + i)
            if isinstance(p, str):
                try:
                    p = int(p)
                except ValueError:
                    print(
                        f"config.yml: workload '{name}' has invalid port={entry.get('port')!r}",
                        file=sys.stderr,
                    )
                    return 1
            resolved_ports.append((name, entry, p))
        port_users = {}
        for name, _entry, p in resolved_ports:
            port_users.setdefault(p, []).append(name)
        dup = {port: names for port, names in port_users.items() if len(names) > 1}
        if dup:
            for port, names in sorted(dup.items()):
                print(
                    f"config.yml: port {port} is used by multiple enabled workloads: {names}. "
                    "Each workload needs a unique port so app.py can bind and the router proxies to the correct process.",
                    file=sys.stderr,
                )
            return 1

        _router_port = os.environ.get("ROUTER_PORT") or (defaults.get("ROUTER_PORT") if isinstance(defaults.get("ROUTER_PORT"), (int, str)) else None) or "5002"
        try:
            router_port = int(_router_port)
        except (TypeError, ValueError):
            router_port = 5002
        router_host = os.environ.get("ROUTER_HOST") or (defaults.get("ROUTER_HOST") if isinstance(defaults.get("ROUTER_HOST"), str) else None) or "0.0.0.0"

        multi_grpc_ads_enabled = _grpc_ads_enabled_for_multiworkload(defaults, enabled)
        if multi_grpc_ads_enabled:
            from lib.xDS.grpc_xds_server import (
                can_start_grpc_xds_server,
                start_grpc_xds_server,
            )

            grpc_ads_port = _grpc_ads_port(defaults)
            if grpc_ads_port in port_users:
                print(
                    f"GRPC_XDS_PORT {grpc_ads_port} conflicts with workload HTTP port used by {port_users[grpc_ads_port]}. "
                    "Set GRPC_XDS_PORT to a pod-local port that is not used by a workload or router.",
                    file=sys.stderr,
                )
                return 1
            if grpc_ads_port == router_port:
                print(
                    f"GRPC_XDS_PORT {grpc_ads_port} conflicts with ROUTER_PORT {router_port}. "
                    "Set GRPC_XDS_PORT to a distinct pod-local port.",
                    file=sys.stderr,
                )
                return 1
            if not is_port_free("0.0.0.0", grpc_ads_port):
                print(
                    f"GRPC_XDS_PORT {grpc_ads_port} is not available. "
                    "gRPC ADS is enabled and Envoy will use ADS for CDS, so startup cannot safely fall back to REST CDS.",
                    file=sys.stderr,
                )
                return 1
            if not can_start_grpc_xds_server({"WDM_XDS_GRPC_ADS_ENABLED": True}):
                print(
                    "WDM_XDS_GRPC_ADS_ENABLED=true but gRPC ADS dependencies are unavailable. "
                    "Envoy will use ADS for CDS, so startup cannot safely fall back to REST CDS.",
                    file=sys.stderr,
                )
                return 1
            logging.getLogger(__name__).info(
                "multi workload: gRPC ADS enabled; router will own GRPC_XDS_PORT=%s and child workers will not bind ADS",
                grpc_ads_port,
            )
        shutdown_workers = threading.Event()
        supervisor_threads: List[threading.Thread] = []
        wl_obj_name_to_port = {}
        for name, entry, port in resolved_ports:
            wl_obj_name = entry["wl_obj_name"]
            wl_obj_name_to_port[wl_obj_name] = port
            env = build_env(defaults, entry, wl_obj_name, port)
            if multi_grpc_ads_enabled:
                env["WDM_XDS_GRPC_ADS_ENABLED"] = "false"
            print_env_settings(env)
            w_cmd = _resolve_worker_cmd_override(defaults, entry, worker_cmd_cli)
            t = threading.Thread(
                target=_supervise_workload_worker,
                kwargs={
                    "name": name,
                    "wl_obj_name": wl_obj_name,
                    "port": port,
                    "env": env,
                    "repo_root": repo_root,
                    "worker_cmd_override": w_cmd,
                    "defaults": defaults,
                    "shutdown": shutdown_workers,
                },
                name=f"wdm-worker-{name}",
                daemon=True,
            )
            t.start()
            supervisor_threads.append(t)

        if not is_port_free(router_host, router_port):
            print(
                f"Router: port {router_port} is not available; exiting (no alternate port)",
                file=sys.stderr,
            )
            shutdown_workers.set()
            for t in supervisor_threads:
                t.join(timeout=10)
            return 1
        print(f"Router: http://{router_host}:{router_port}/sdrc/<wl_obj_name>/... -> app.py instances")
        _health_interval = os.environ.get("DASHBOARD_HEALTH_INTERVAL_SECONDS") or defaults.get("DASHBOARD_HEALTH_INTERVAL_SECONDS")
        try:
            health_interval_seconds = int(_health_interval) if _health_interval is not None else 15
        except (TypeError, ValueError):
            health_interval_seconds = 15
        print(f"Dashboard: http://{router_host}:{router_port}/dashboard (health check every {health_interval_seconds}s)")
        print(f"API docs (Swagger UI): http://{router_host}:{router_port}/api/docs/")
        print(f"OpenAPI JSON: http://{router_host}:{router_port}/openapi.json")
        enabled_workload_config = {name: entry for name, entry in config.items() if is_enabled(entry)}
        xds_app_config = build_xds_app_config(defaults, enabled_workload_config)
        redis_stream_config = build_redis_stream_config(defaults)
        kafka_global_add_config = build_kafka_global_add_config(defaults, enabled_workload_config)
        router_app = create_router_app(
            wl_obj_name_to_port,
            enabled_workload_config,
            xds_app_config,
            health_interval_seconds=health_interval_seconds,
            workload_config_all=config,
            redis_stream_config=redis_stream_config,
            kafka_global_add_config=kafka_global_add_config,
            config_defaults=defaults,
        )
        if multi_grpc_ads_enabled:
            ads_config = dict(xds_app_config)
            ads_config["WDM_XDS_GRPC_ADS_ENABLED"] = True
            ads_config["GRPC_XDS_PORT"] = grpc_ads_port
            ads_config["GRPC_XDS_POLL_INTERVAL_SECONDS"] = (
                _grpc_ads_poll_interval_seconds(defaults)
            )
            ads_thread = threading.Thread(
                target=start_grpc_xds_server,
                args=(router_app.wdm_ads_xds, ads_config),
                name="wdm-router-grpc-ads",
                daemon=True,
            )
            ads_thread.start()
            logging.getLogger(__name__).info(
                "multi workload: gRPC ADS server thread starting on port %s",
                ads_config["GRPC_XDS_PORT"],
            )
        try:
            router_app.run(host=router_host, port=router_port, use_reloader=False, threaded=True)
        finally:
            shutdown_workers.set()
            for t in supervisor_threads:
                t.join(timeout=10)
        return 0


if __name__ == "__main__":
    sys.exit(main())

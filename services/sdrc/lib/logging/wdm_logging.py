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

"""SDRC logging configuration.

Environment:
  WDM_LOG_LEVEL   - DEBUG|INFO|WARNING|ERROR|CRITICAL (default INFO)
  WDM_LOG_FORMAT  - text|json (default text; use json for log shippers)
  WDM_LOG_TO_FILE - 1/true to write rotating files under logs/ (default true)

Hot-path poll detail stays available at DEBUG; production INFO is for state changes.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Optional

# Use importlib so PyInstaller bundles stdlib logging.handlers (not confused with lib.logging).
_handlers = importlib.import_module("logging.handlers")
RotatingFileHandler = _handlers.RotatingFileHandler

_log_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "sdrc_log_context", default={}
)

# Third-party / library loggers that spam INFO during steady-state polls.
_NOISY_LOGGER_LEVELS = {
    "redis_lock": logging.WARNING,
    "redis_lock.acquire": logging.WARNING,
    "urllib3": logging.WARNING,
    "urllib3.connectionpool": logging.WARNING,
    "docker": logging.WARNING,
    "docker.utils.config": logging.WARNING,
    "docker.auth": logging.WARNING,
    "kafka": logging.WARNING,
}

_RESERVED_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "wl_object_name",
        "event",
        "sdrc_fields",
        "trace_id",
        "span_id",
    }
)


def bind_context(**kwargs: Any):
    """Bind key/value pairs onto the current context (inherited by later log lines)."""
    current = dict(_log_context.get() or {})
    for key, value in kwargs.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return _log_context.set(current)


def reset_context(token) -> None:
    _log_context.reset(token)


def clear_context() -> None:
    _log_context.set({})


def get_context() -> dict:
    return dict(_log_context.get() or {})


def parse_log_level(value: Optional[str], default: int = logging.INFO) -> int:
    if value is None or str(value).strip() == "":
        return default
    name = str(value).strip().upper()
    if name.isdigit():
        return int(name)
    return getattr(logging, name, default)


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_log_format() -> str:
    raw = (os.environ.get("WDM_LOG_FORMAT") or "text").strip().lower()
    return "json" if raw == "json" else "text"


def _otel_ids() -> tuple[str, str]:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and getattr(ctx, "is_valid", False):
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return "", ""


class WlObjectNameFilter(logging.Filter):
    """Inject workload object name into every log record."""

    def __init__(self, wl_name):
        super().__init__()
        self.wl_name = wl_name

    def filter(self, record):
        record.wl_object_name = self.wl_name
        return True


class ContextAndTraceFilter(logging.Filter):
    """Merge contextvars + OpenTelemetry trace/span ids onto each record.

    Also overrides the source bracket (``wl_object_name``) from the bound
    ``component`` so in-process controller threads can emit ``[controller]``
    without reinstalling root handlers that were configured as ``[router]``.
    """

    def filter(self, record):
        ctx = _log_context.get() or {}
        for key, value in ctx.items():
            if not hasattr(record, key) or getattr(record, key) in (None, ""):
                setattr(record, key, value)
        _apply_source_bracket(record, ctx)
        trace_id, span_id = _otel_ids()
        if not getattr(record, "trace_id", None):
            record.trace_id = trace_id
        if not getattr(record, "span_id", None):
            record.span_id = span_id
        if not hasattr(record, "event"):
            record.event = ""
        return True


def _apply_source_bracket(record: logging.LogRecord, ctx: Optional[Mapping[str, Any]] = None) -> None:
    """Set ``wl_object_name`` from component context when present."""
    ctx = ctx or {}
    component = getattr(record, "component", None) or ctx.get("component")
    if component == "controller":
        record.wl_object_name = "controller"
        return
    if component == "router":
        record.wl_object_name = "router"
        return
    if component == "envoy":
        record.wl_object_name = "envoy"
        return
    if component == "workload":
        wl = getattr(record, "workload", None) or ctx.get("workload")
        if wl:
            record.wl_object_name = f"workload:{wl}"
            return
    if not getattr(record, "wl_object_name", None):
        record.wl_object_name = (
            ctx.get("workload") or ctx.get("wl_object_name") or "-"
        )


def _record_fields(record: logging.LogRecord) -> dict:
    fields: dict[str, Any] = {}
    sdrc_fields = getattr(record, "sdrc_fields", None)
    if isinstance(sdrc_fields, Mapping):
        fields.update(sdrc_fields)
    for key, value in record.__dict__.items():
        if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
            continue
        if value is None or value == "":
            continue
        if key in fields:
            continue
        fields[key] = value
    return fields


def _format_kv(fields: Mapping[str, Any]) -> str:
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if any(ch.isspace() for ch in text) or "=" in text:
            text = json.dumps(text, default=str)
        parts.append(f"{key}={text}")
    return " ".join(parts)


class JsonFormatter(logging.Formatter):
    """One JSON object per line for collectors / Loki / jq."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        workload = getattr(record, "wl_object_name", None)
        if workload and workload != "-":
            # Bracket may be "workload:name"; also emit structured fields.
            payload["source"] = workload
        component = getattr(record, "component", None) or (
            (_log_context.get() or {}).get("component")
        )
        if component:
            payload["component"] = component
        wl = getattr(record, "workload", None) or (
            (_log_context.get() or {}).get("workload")
        )
        if wl:
            payload["workload"] = wl
        elif isinstance(workload, str) and workload.startswith("workload:"):
            payload["workload"] = workload.split(":", 1)[1]
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id
        for key, value in _record_fields(record).items():
            if key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-skimmable text with stable key=value fields."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(levelname)s [%(wl_object_name)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        if not getattr(record, "wl_object_name", None):
            record.wl_object_name = "-"
        base = super().format(record)
        extras: MutableMapping[str, Any] = {}
        event = getattr(record, "event", None)
        if event:
            extras["event"] = event
        for key, value in _record_fields(record).items():
            if key in ("wl_object_name", "workload") and value == getattr(
                record, "wl_object_name", None
            ):
                continue
            extras[key] = value
        for key in ("trace_id", "span_id"):
            val = getattr(record, key, None)
            if val:
                extras[key] = val
        kv = _format_kv(extras)
        if kv:
            return f"{base} {kv}"
        return base


def wdm_log_formatter(fmt: Optional[str] = None):
    """Return formatter for the requested format (text|json)."""
    chosen = (fmt or _env_log_format()).strip().lower()
    if chosen == "json":
        return JsonFormatter()
    return TextFormatter()


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: Optional[str] = None,
    **fields: Any,
) -> None:
    """Emit a structured event. Fields are preserved in both text and JSON formats."""
    extra: dict[str, Any] = {"event": event, "sdrc_fields": dict(fields)}
    for key, value in fields.items():
        if key.isidentifier() and key not in _RESERVED_RECORD_KEYS:
            extra[key] = value
    logger.log(level, message or event, extra=extra)


class RateLimitedLogger:
    """Log identical messages at most once per interval; emit count on repeat."""

    def __init__(self, interval_s: float = 30.0):
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, int]] = {}

    def should_log(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            entry = self._state.get(key)
            # Absent entry means never logged; monotonic() is boot-relative and can
            # be smaller than interval_s, so it cannot be compared against a sentinel.
            if entry is None:
                self._state[key] = (now, 0)
                return True, 0
            last, count = entry
            if now - last >= self.interval_s:
                self._state[key] = (now, 0)
                return True, count
            self._state[key] = (last, count + 1)
            return False, 0


_rate_limiters: dict[float, RateLimitedLogger] = {}
_rate_limiters_lock = threading.Lock()


def _get_rate_limiter(interval_s: float) -> RateLimitedLogger:
    """Return a process-wide limiter for ``interval_s`` (stateful across calls)."""
    key = float(interval_s)
    with _rate_limiters_lock:
        limiter = _rate_limiters.get(key)
        if limiter is None:
            limiter = RateLimitedLogger(interval_s=key)
            _rate_limiters[key] = limiter
        return limiter


def log_rate_limited(
    logger: logging.Logger,
    level: int,
    key: str,
    msg: str,
    *args: Any,
    interval_s: float = 30.0,
    **kwargs: Any,
) -> None:
    """Rate-limit repeated identical log lines; includes suppressed_count when repeating."""
    limiter = _get_rate_limiter(interval_s)
    ok, suppressed = limiter.should_log(key)
    if not ok:
        return
    if suppressed:
        kwargs.setdefault("extra", {})
        extra = dict(kwargs.get("extra") or {})
        fields = dict(extra.get("sdrc_fields") or {})
        fields["suppressed_count"] = suppressed
        extra["sdrc_fields"] = fields
        extra["suppressed_count"] = suppressed
        kwargs["extra"] = extra
        msg = f"{msg} (suppressed_count={suppressed})"
    logger.log(level, msg, *args, **kwargs)


def _apply_noisy_logger_levels() -> None:
    for name, level in _NOISY_LOGGER_LEVELS.items():
        logging.getLogger(name).setLevel(level)


def configure_root_logging(
    wl_log_prefix: str,
    repo_root: str,
    max_bytes: int = 200000,
    backup_count: int = 2,
    component: Optional[str] = None,
) -> None:
    """Configure root logger: optional rotating file under logs/ + stdout.

    ``component`` is a stable source tag for filtering muxed docker logs:
      - ``workload`` → bracket ``[workload:<wl_log_prefix>]``
      - ``router`` → ``[router]`` (run_workloads / sdr-controller orchestrator)
      - ``controller`` → ``[controller]``
    Every line also includes ``component=...`` (and ``workload=...`` for workers).
    Envoy is tagged separately via ``--log-format`` in the entrypoint (``[envoy]``).
    """
    level = parse_log_level(os.environ.get("WDM_LOG_LEVEL"), logging.INFO)
    fmt_name = _env_log_format()
    formatter = wdm_log_formatter(fmt_name)

    component_name = (
        (component or os.environ.get("WDM_LOG_COMPONENT") or "sdrc").strip().lower()
        or "sdrc"
    )
    if component_name == "workload":
        display_name = f"workload:{wl_log_prefix}"
    elif component_name in ("router", "controller", "envoy"):
        display_name = component_name
    else:
        display_name = (
            f"{component_name}:{wl_log_prefix}" if wl_log_prefix else component_name
        )

    wl_name_filter = WlObjectNameFilter(display_name)
    context_filter = ContextAndTraceFilter()

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(wl_name_filter)
    stdout_handler.addFilter(context_filter)
    root_logger.addHandler(stdout_handler)

    if _env_truthy("WDM_LOG_TO_FILE", default=True):
        log_dir = os.path.join(repo_root, "logs")
        log_file = os.path.join(log_dir, f"{wl_log_prefix}-wdm-services.log")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(wl_name_filter)
        file_handler.addFilter(context_filter)
        root_logger.addHandler(file_handler)

    _apply_noisy_logger_levels()

    ctx = {"component": component_name}
    if component_name == "workload":
        ctx["workload"] = wl_log_prefix
    elif wl_log_prefix and component_name not in ("router", "controller"):
        ctx["workload"] = wl_log_prefix
    bind_context(**ctx)

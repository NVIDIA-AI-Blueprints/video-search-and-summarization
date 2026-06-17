# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Logging configuration for the sensor microservice.

Gives the `sensor_ms.*` loggers a timestamped, leveled format on stdout (so `docker logs` shows
when each lifecycle/error event happened), independent of uvicorn's own access logger. Controlled by:
  LOG_LEVEL          DEBUG|INFO|WARNING|ERROR (default INFO)
  LOG_HEALTH_ACCESS  1 to keep the /v1/ready|live|startup access-log spam (default: filtered out)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

_HEALTH_PATHS = ("/v1/ready", "/v1/live", "/v1/startup")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _iso_millis(record: logging.LogRecord) -> str:
    """ISO-8601 UTC timestamp with millisecond precision and a `Z` suffix, e.g. 2026-06-17T09:26:14.123Z."""
    dt = datetime.fromtimestamp(record.created, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


class _IsoMillisFormatter(logging.Formatter):
    """Timestamped formatter (logging's default `datefmt` path uses time.strftime, which only formats
    whole seconds, so we override formatTime to add milliseconds + Z)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return _iso_millis(record)


class _RenameUvicornErrorFilter(logging.Filter):
    """Relabel the `uvicorn.error` logger to `uvicorn` in the output. uvicorn routes ALL server
    lifecycle messages (startup INFO, warnings, errors) through a logger confusingly named
    `uvicorn.error`; the real severity is the level, not the name. Shown as `uvicorn` to avoid the
    'is this an error?' confusion."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return True


class _HealthProbeFilter(logging.Filter):
    """Drop uvicorn access-log lines for the health probe endpoints. They are polled every few
    seconds by Docker/k8s and otherwise bury the operationally meaningful logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in _HEALTH_PATHS)


def configure_logging() -> None:
    """Idempotently configure the sensor_ms package logger and quiet health-probe access logs."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    default_fmt = _IsoMillisFormatter(_LOG_FORMAT)

    pkg = logging.getLogger("sensor_ms")
    pkg.setLevel(level)
    if not any(getattr(h, "_sensor_ms_handler", False) for h in pkg.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(default_fmt)
        handler._sensor_ms_handler = True  # type: ignore[attr-defined]
        pkg.addHandler(handler)
        pkg.propagate = False  # our own handler -> don't double-emit via the root logger

    # Apply the same timestamped format to uvicorn's own loggers so EVERY line carries a timestamp
    # (uvicorn's startup messages and access logs are otherwise unprefixed). uvicorn configures these
    # loggers before importing the app, so their handlers already exist here -- we just swap the
    # formatter. The access logger keeps uvicorn's field substitution (client_addr/request_line/...)
    # but gains the timestamp.
    for name in ("uvicorn", "uvicorn.error"):
        ulog = logging.getLogger(name)
        for h in ulog.handlers:
            h.setFormatter(default_fmt)
    # Relabel uvicorn.error -> uvicorn in the displayed name (it's not error-only).
    uerr = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, _RenameUvicornErrorFilter) for f in uerr.filters):
        uerr.addFilter(_RenameUvicornErrorFilter())
    try:
        from uvicorn.logging import AccessFormatter

        class _TsAccessFormatter(AccessFormatter):
            def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
                return _iso_millis(record)

        access_fmt = _TsAccessFormatter(
            '%(asctime)s %(levelname)s uvicorn.access: %(client_addr)s - "%(request_line)s" %(status_code)s',
            use_colors=False,
        )
        for h in logging.getLogger("uvicorn.access").handlers:
            h.setFormatter(access_fmt)
    except Exception:  # uvicorn missing/changed -> leave access logs as-is
        pass

    if os.environ.get("LOG_HEALTH_ACCESS", "0") != "1":
        access = logging.getLogger("uvicorn.access")
        if not any(isinstance(f, _HealthProbeFilter) for f in access.filters):
            access.addFilter(_HealthProbeFilter())

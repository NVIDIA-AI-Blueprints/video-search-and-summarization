# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical naming helpers for VSS performance results."""

from __future__ import annotations

from datetime import datetime, timezone


def normalize_service_name(service: str) -> str:
    return service.replace(" ", "-").replace("_", "-").lower()


def generate_run_id(
    service: str,
    config_id: str = "",
    *,
    suffix: str = "",
    now: datetime | None = None,
) -> str:
    """Return ``{service}_{config_id}_{YYYYMMDD}_{HHMMSS}`` in UTC."""
    timestamp = now or datetime.now(timezone.utc)
    parts = [normalize_service_name(service)]
    if config_id:
        parts.append(config_id.strip().replace(" ", "-"))
    parts.extend((timestamp.strftime("%Y%m%d"), timestamp.strftime("%H%M%S")))
    run_id = "_".join(parts)
    return f"{run_id}_{suffix.strip()}" if suffix else run_id


def generate_result_filename(*args, **kwargs) -> str:
    return f"{generate_run_id(*args, **kwargs)}.json"

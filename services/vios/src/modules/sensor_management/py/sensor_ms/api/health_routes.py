# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Liveness / readiness / startup probes (sensor_management_apis.cpp:79)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/v1/live")
async def live() -> dict:
    return {}


@router.get("/v1/ready")
async def ready() -> dict:
    # TODO(P1): real readiness (DB reachable, adaptor connected).
    return {}


@router.get("/v1/startup")
async def startup() -> dict:
    # TODO(P1): real startup check (web server running).
    return {}

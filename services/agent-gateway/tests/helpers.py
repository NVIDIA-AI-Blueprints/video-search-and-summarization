# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
from typing import Self

from vss_agent_gateway.config import GatewayConfig


def make_config(**overrides: object) -> GatewayConfig:
    values: dict[str, object] = {
        "bind_host": "127.0.0.1",
        "bind_port": 0,
        "gateway_token": None,
        "backend_protocol": "responses",
        "backend_url": "http://backend.test",
        "backend_path": "/v1/responses",
        "backend_token": "backend-secret",
        "backend_model": "agent",
        "backend_session_field": "user",
        "backend_session_header": None,
        "backend_headers": {},
        "request_timeout_seconds": 5.0,
        "run_retention_seconds": 3600,
        "max_runs": 100,
        "max_events_per_run": 1000,
    }
    values.update(overrides)
    return GatewayConfig(**values)  # type: ignore[arg-type]


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "text/event-stream") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connector interface: one implementation per wire protocol, not per harness."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from threading import Event

from ..contract import ConnectorEvent, CreateRunRequest


class ConnectorError(RuntimeError):
    """A structured upstream failure safe to expose to the UI."""

    def __init__(
        self, message: str, *, code: str = "backend_error", retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class Connector(ABC):
    """Translate one upstream protocol into the VSS run/event contract."""

    @property
    @abstractmethod
    def protocol(self) -> str:
        """Stable protocol connector identifier."""

    @property
    def capabilities(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "streaming": True,
            "tool_events": "best_effort",
            # Artifact extraction sits above connectors, so every text-streaming
            # connector supports the same backend-neutral envelope.
            "artifacts": True,
            "interactions": False,
        }

    @abstractmethod
    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: Event,
    ) -> Iterator[ConnectorEvent]:
        """Run one turn and yield normalized non-terminal events."""

    @abstractmethod
    def cancel(self, run_id: str) -> None:
        """Promptly interrupt an active upstream response when possible."""

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run lifecycle orchestration independent of HTTP and upstream protocol."""

from __future__ import annotations

import logging
import threading

from .config import GatewayConfig
from .connectors.base import Connector, ConnectorError
from .contract import PROTOCOL_VERSION, CreateRunRequest
from .store import RunRecord, RunStore

LOGGER = logging.getLogger(__name__)


class GatewayService:
    def __init__(self, config: GatewayConfig, connector: Connector) -> None:
        self.config = config
        self.connector = connector
        self.store = RunStore(
            retention_seconds=config.run_retention_seconds,
            max_runs=config.max_runs,
            max_events_per_run=config.max_events_per_run,
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "transport": "sse",
            "features": {
                "reconnect": True,
                "cancellation": True,
                "idempotent_run_creation": True,
                "interaction_responses": False,
                "artifacts": False,
            },
            "connector": self.connector.capabilities,
            "event_types": [
                "run.started",
                "message.delta",
                "reasoning.delta",
                "tool.started",
                "tool.arguments.delta",
                "tool.requested",
                "tool.completed",
                "tool.failed",
                "artifact.created",
                "interaction.required",
                "run.completed",
                "run.failed",
                "run.cancelled",
            ],
            "limits": {
                "max_events_per_run": self.config.max_events_per_run,
                "run_retention_seconds": self.config.run_retention_seconds,
            },
        }

    def create_run(
        self,
        request: CreateRunRequest,
        *,
        idempotency_key: str | None,
    ) -> tuple[RunRecord, bool]:
        record, replayed = self.store.create(request, idempotency_key=idempotency_key)
        if replayed:
            return record, True
        record.append(
            "run.started",
            {
                "surface": request.surface,
                "connector_protocol": self.connector.protocol,
            },
        )
        worker = threading.Thread(
            target=self._run_worker,
            args=(record,),
            daemon=True,
            name=f"agent-gateway-{record.run_id[-8:]}",
        )
        worker.start()
        return record, False

    def _run_worker(self, record: RunRecord) -> None:
        try:
            for event in self.connector.run(
                record.request,
                run_id=record.run_id,
                cancel_event=record.cancel_event,
            ):
                if record.cancel_event.is_set():
                    break
                if event.type.startswith("run."):
                    raise ConnectorError(
                        "connector emitted a reserved terminal event",
                        code="connector_contract_error",
                    )
                record.append(event.type, event.data)
            if record.cancel_event.is_set():
                self.store.finish(
                    record, "run.cancelled", {"reason": "client_cancelled"}
                )
            else:
                self.store.finish(record, "run.completed")
        except ConnectorError as error:
            if record.cancel_event.is_set():
                self.store.finish(
                    record, "run.cancelled", {"reason": "client_cancelled"}
                )
            else:
                self.store.finish(
                    record,
                    "run.failed",
                    {
                        "error": {
                            "code": error.code,
                            "message": str(error),
                            "retryable": error.retryable,
                        },
                    },
                )
        except Exception:
            LOGGER.exception("unexpected connector failure for run %s", record.run_id)
            if record.cancel_event.is_set():
                self.store.finish(
                    record, "run.cancelled", {"reason": "client_cancelled"}
                )
            else:
                self.store.finish(
                    record,
                    "run.failed",
                    {
                        "error": {
                            "code": "gateway_internal_error",
                            "message": "the gateway could not complete this run",
                            "retryable": False,
                        },
                    },
                )

    def cancel_run(self, run_id: str) -> RunRecord:
        record = self.store.get(run_id)
        if not record.terminal:
            record.cancel_event.set()
            try:
                self.connector.cancel(run_id)
            except Exception:
                LOGGER.exception("connector cancellation failed for run %s", run_id)
        return record

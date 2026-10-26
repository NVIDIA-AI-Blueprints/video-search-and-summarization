# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Local, explicitly bounded gateways for one remote-model workflow pilot.

The coordinator creates the session before service startup, keeps credentials
local, and exposes these loopback routes through owned SSH reverse forwards.
Fresh gateways are mandatory for every workflow; no in-place quota resets.
"""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from benchmarks.providers import Binding, private_key
from benchmarks.workflow.provider_budget import WorkflowLedger, WorkflowPolicy
from benchmarks.workflow.relay import Relay


class RemoteWorkflowSession:
    """Four workflow attempts at most; unknown settlement ends the session."""

    def __init__(
        self, root: Path, *, credential_file: Path, credential_name="INFERENCEHUB_TOKEN"
    ):
        self.key = private_key(credential_file, credential_name)
        self.ledger = WorkflowLedger(
            root, max_requests=96, max_tokens=196608, wall_s=1200
        )
        self.attempts = 0
        self.active = False

    @contextmanager
    def workflow(self, attempt, bindings: list[Binding]):
        if (
            self.active
            or self.ledger.halted
            or self.ledger.remaining_s <= 0
            or self.attempts >= 4
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", attempt)
            or not 1 <= len(bindings) <= 2
            or len({b.role for b in bindings}) != len(bindings)
            or any(
                b.placement != "remote" or b.request_settings is None for b in bindings
            )
        ):
            raise ValueError("workflow_gateway_admission")
        self.attempts += 1
        self.active = True
        trial = None
        try:
            trial = GatewayTrial(self, attempt, bindings)
            trial.start()
            yield trial
        except BaseException:
            self.ledger.halted = True
            raise
        finally:
            try:
                if trial is not None:
                    trial.close()
            except BaseException:
                self.ledger.halted = True
                raise
            finally:
                self.active = False


class GatewayTrial:
    def __init__(self, session, attempt, bindings):
        from benchmarks.common import software_hash

        self.session = session
        self.root = session.ledger.root / attempt
        self.root.mkdir(mode=0o700, exist_ok=False)
        self.bindings = bindings
        self.relays = {}
        self.threads = {}
        self.stops = {}
        self.closed = self.armed = False
        self._write(
            "intent.json",
            {
                "schema_version": "workflow_gateway_trial.1",
                "software_sha256": software_hash(Path(__file__).resolve().parents[2]),
                "bindings": [b.model_dump() for b in bindings],
                "caption_max_tokens": 2048,
                "summary_max_tokens": 2048,
                "extra_transport": "owned_loopback_ssh_reverse_forward",
                "provider_forward_retries": 0,
                "upstream_client_may_retry": True,
                "effective_settings_verified": False,
                "acceptance_eligible": False,
            },
        )

    def _write(self, name, value):
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, indent=2, allow_nan=False)

    def start(self):
        for binding in self.bindings:
            role = binding.role
            gateway = Relay(
                binding.base_url,
                binding.model,
                str(uuid4()),
                deadline_s=90,
                provider_binding=binding,
                provider_key=self.session.key,
                provider_generation_cap=2048,
                workflow_policy=WorkflowPolicy(
                    role, 16 if role == "vlm" else 8, 32768 if role == "vlm" else 8192
                ),
                workflow_ledger=self.session.ledger,
            )
            self.relays[role] = gateway
            gateway.timeout = 0.05
            stop = threading.Event()
            self.stops[role] = stop

            def serve(server=gateway, stopped=stop):
                while not stopped.is_set():
                    server.handle_request()

            thread = threading.Thread(
                target=serve,
                daemon=True,
            )
            self.threads[role] = thread
            thread.start()

    def routes(self):
        return {
            role: {"local_port": r.server_port, "path": f"/{r.token}/v1"}
            for role, r in self.relays.items()
        }

    def arm(self):
        # Call only after owned RTVI/frontend startup; VLM requires its sole
        # text-only startup response. These admission mutations are local.
        if self.armed or self.closed or self.session.ledger.halted:
            raise ValueError("workflow_gateway_arm")
        if not all(r.admit() for r in self.relays.values()):
            self.session.ledger.halted = True
            raise ValueError("workflow_gateway_startup_not_valid")
        self.armed = True

    def close(self):
        if self.closed:
            return
        self.closed = True
        settled = True
        cleanup_failures = {}
        for role, gateway in self.relays.items():
            try:
                gateway.sealed = True
                stop = self.stops.get(role)
                if stop is not None:
                    stop.set()
                thread = self.threads.get(role)
                if thread is not None and thread.ident is not None:
                    thread.join(timeout=3)
                settled &= (
                    thread is None or not thread.is_alive()
                ) and not gateway.active
            except BaseException as error:
                settled = False
                cleanup_failures[role] = type(error).__name__
            finally:
                try:
                    gateway.server_close()
                except BaseException as error:
                    settled = False
                    cleanup_failures[role] = type(error).__name__
        complete = (
            bool(self.relays)
            and self.armed
            and settled
            and all(r.snapshot()["complete"] for r in self.relays.values())
        )
        if not complete:
            self.session.ledger.halted = True
        self._write(
            "witness.json",
            {
                "schema_version": "workflow_gateway_witness.1",
                "complete": complete,
                "threads_settled": settled,
                "cleanup_failures": cleanup_failures,
                "ledger_halted": self.session.ledger.halted,
                "roles": {role: r.snapshot() for role, r in self.relays.items()},
                "provider_frame_consumption_verified": False,
                "acceptance_eligible": False,
            },
        )
        if cleanup_failures:
            raise RuntimeError("workflow_gateway_cleanup")

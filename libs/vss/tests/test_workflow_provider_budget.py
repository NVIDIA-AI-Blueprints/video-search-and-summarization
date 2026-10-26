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

"""Fail-stop remote workflow budget and ordered multimodal input witnesses."""

import base64
import hashlib
import json
import os
from uuid import uuid4

import httpx
import pytest
from test_workflow_qualification import running

from benchmarks import providers
from benchmarks.workflow.provider_budget import (
    WorkflowLedger,
    WorkflowPolicy,
    jpeg_witness,
)
from benchmarks.workflow.relay import Relay


def binding(role="vlm"):
    return providers.Binding(
        label="super-vl" if role == "vlm" else "ultra",
        role=role,
        placement="remote",
        base_url="https://inference-api.nvidia.com/v1",
        model="test-model",
        response_model="test-model",
        request_settings=providers.RequestedSettings(
            temperature=0,
            top_p=1,
            seed=1729,
            reasoning_control="chat_template_disabled",
        ),
    )


def body(frames=0, *, size=8):
    images = [b"\xff\xd8" + bytes([i]) * size + b"\xff\xd9" for i in range(frames)]
    return {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "caption"}]
                + [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(im).decode()
                        },
                    }
                    for im in images
                ],
            }
        ],
        "max_tokens": 1024,
    }


def ledger(tmp_path, **kw):
    if os.name != "posix":
        pytest.skip("provider ledger requires POSIX directory fsync and permissions")
    return WorkflowLedger(
        tmp_path / "ledger",
        **(dict(max_requests=96, max_tokens=196608, wall_s=1200) | kw),
    )


def relay(budget, role="vlm"):
    configured = binding(role)
    return Relay(
        configured.base_url,
        configured.model,
        str(uuid4()),
        provider_binding=configured,
        provider_key="private-fake-key",
        provider_generation_cap=2048,
        workflow_policy=WorkflowPolicy(
            role, 16 if role == "vlm" else 8, 32768 if role == "vlm" else 8192
        ),
        workflow_ledger=budget,
        deadline_s=3,
    )


def successful(evidence=None):
    return (
        {
            "status": "completed_probe",
            "finish_reason": "stop",
            "reported_output_within_requested_cap": True,
            "usage": {"prompt_tokens": 32, "completion_tokens": 4},
            **(evidence or {}),
        },
        {
            "model": "test-model",
            "choices": [{"finish_reason": "stop", "message": {"content": "caption"}}],
            "usage": {"prompt_tokens": 32, "completion_tokens": 4},
        },
    )


def post(base, gateway, payload):
    return httpx.post(
        base + "/" + gateway.token + "/v1/chat/completions",
        json=payload,
        trust_env=False,
    )


def test_multimodal_workflow_witnesses_large_ordered_body_and_controls(
    tmp_path, monkeypatch
):
    budget = ledger(tmp_path)
    witnessed = []

    async def fake(configured, payload, **kw):
        # Durable debit must exist before a provider is called.
        assert (budget.root / f"{budget.requests:03d}-intent.json").is_file()
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["max_tokens"] == 2048
        witnessed.append(payload)
        return successful()

    monkeypatch.setattr(providers, "complete", fake)
    with relay(budget) as gateway, running(gateway) as base:
        assert post(base, gateway, body()).status_code == 200
        assert gateway.admit()
        request = body(8, size=160000)  # Larger than the legacy 1 MiB LLM limit.
        assert len(json.dumps(request)) > 1024**2
        assert post(base, gateway, request).status_code == 200
        expected = jpeg_witness(request, expected=8)
        assert gateway.records[1]["frame_delivery"] == expected
        assert (
            expected["decoded_jpeg_bytes_sha256"][0]
            != expected["decoded_jpeg_bytes_sha256"][1]
        )
        assert (
            gateway.records[1]["client_request_sha256"]
            != gateway.records[1]["request_sha256"]
        )
        gateway.sealed = True
        assert gateway.snapshot()["complete"]
    assert budget.requests == 2 and budget.tokens == 4096
    assert len(witnessed) == 2
    assert "private-fake-key" not in "".join(
        p.read_text() for p in budget.root.iterdir()
    )
    with pytest.raises(FileExistsError):
        ledger(tmp_path)


@pytest.mark.parametrize("count", [0, 1, 7, 9])
def test_measured_vlm_rejects_wrong_image_count_before_provider(
    tmp_path, monkeypatch, count
):
    async def fake(*args, **kw):
        return successful()

    monkeypatch.setattr(providers, "complete", fake)
    budget = ledger(tmp_path)
    with relay(budget) as gateway, running(gateway) as base:
        assert post(base, gateway, body()).status_code == 200
        assert gateway.admit()
        assert post(base, gateway, body(count)).status_code == 400
        assert budget.halted and budget.requests == 1
        assert post(base, gateway, body(8)).status_code == 400
        assert budget.requests == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/image.jpg",
        "data:video/mp4;base64,AAAA",
        "data:image/jpeg;base64,not_base64",
    ],
)
def test_only_bounded_inline_jpeg_bytes_are_admitted(url):
    request = body(1)
    request["messages"][0]["content"][1]["image_url"]["url"] = url
    with pytest.raises(ValueError):
        jpeg_witness(request, expected=1)


def test_ordered_hashes_are_byte_hashes():
    request = body(2)
    hashes = jpeg_witness(request, expected=2)["decoded_jpeg_bytes_sha256"]
    raw = b"\xff\xd8" + bytes([0]) * 8 + b"\xff\xd9"
    assert hashes[0] == hashlib.sha256(raw).hexdigest()
    request["messages"][0]["content"][1:] = reversed(
        request["messages"][0]["content"][1:]
    )
    assert (
        jpeg_witness(request, expected=2)["decoded_jpeg_bytes_sha256"] == hashes[::-1]
    )


def test_unknown_settlement_halts_other_role_without_reforward(tmp_path, monkeypatch):
    calls = []

    async def fake(*args, **kw):
        calls.append(1)
        return {"upstream_settlement": "unknown"}, None

    monkeypatch.setattr(providers, "complete", fake)
    budget = ledger(tmp_path)
    with relay(budget, "llm") as llm, relay(budget) as vlm:
        with running(llm) as base:
            assert post(base, llm, body()).status_code == 400
            assert post(base, llm, body()).status_code == 400
        with running(vlm) as base:
            assert post(base, vlm, body()).status_code == 400
        assert budget.halted and len(calls) == 1


@pytest.mark.parametrize(
    "evidence",
    [
        {"finish_reason": "length"},
        {"disabled_reasoning_contradicted": True},
        {"reported_output_within_requested_cap": False},
        {"usage": {}},
    ],
)
def test_response_control_failure_seals_global_experiment(
    tmp_path, monkeypatch, evidence
):
    async def fake(*args, **kw):
        return successful(evidence)

    monkeypatch.setattr(providers, "complete", fake)
    budget = ledger(tmp_path)
    with relay(budget) as gateway, running(gateway) as base:
        assert post(base, gateway, body()).status_code == 400
        assert budget.halted and not gateway.admit()


def test_ledger_quota_and_expiry_before_forward(tmp_path):
    budget = ledger(tmp_path, max_requests=1, max_tokens=2048)
    n = budget.debit(2048, role="llm", request_sha256="a" * 64)
    budget.settle(n, valid=True)
    with pytest.raises(ValueError):
        budget.debit(1, role="vlm", request_sha256="b" * 64)
    assert budget.requests == 1 and budget.halted
    timed = WorkflowLedger(
        tmp_path / "expired", max_requests=2, max_tokens=2048, wall_s=1
    )
    timed.started -= 2
    with pytest.raises(ValueError):
        timed.debit(1, role="vlm", request_sha256="b" * 64)
    assert timed.requests == 0


def test_vlm_requires_fresh_single_text_startup_before_arm(tmp_path, monkeypatch):
    async def fake(*args, **kw):
        return successful()

    monkeypatch.setattr(providers, "complete", fake)
    budget = ledger(tmp_path)
    with relay(budget) as gateway:
        assert not gateway.admit()  # Missing startup is not evidence.
    with relay(budget) as gateway, running(gateway) as base:
        assert post(base, gateway, body()).status_code == 200
        assert post(base, gateway, body()).status_code == 400
        assert budget.requests == 1 and budget.halted


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_gateway_session_owns_fresh_roles_and_persists_final_witness(
    tmp_path, monkeypatch
):
    from benchmarks.workflow.provider_gateway import RemoteWorkflowSession

    private = tmp_path / "private.env"
    private.write_text("INFERENCEHUB_TOKEN=private-fake-key\n")
    private.chmod(0o600)

    async def fake(*args, **kw):
        return successful()

    monkeypatch.setattr(providers, "complete", fake)
    session = RemoteWorkflowSession(tmp_path / "session", credential_file=private)
    previous_ports = set()
    for index in range(2):
        with session.workflow(f"case-{index}", [binding(), binding("llm")]) as trial:
            assert len(trial.routes()) == 2
            vlm, llm = trial.relays["vlm"], trial.relays["llm"]
            assert vlm.token not in previous_ports
            previous_ports.add(vlm.token)
            assert (
                post(f"http://127.0.0.1:{vlm.server_port}", vlm, body()).status_code
                == 200
            )
            trial.arm()
            assert (
                post(f"http://127.0.0.1:{vlm.server_port}", vlm, body(8)).status_code
                == 200
            )
            assert (
                post(f"http://127.0.0.1:{llm.server_port}", llm, body()).status_code
                == 200
            )
        witness = json.loads((trial.root / "witness.json").read_bytes())
        assert witness["complete"] and witness["threads_settled"]
        assert not witness["ledger_halted"]
        assert len(witness["roles"]["vlm"]["records"]) == 2
        assert len(witness["roles"]["llm"]["records"]) == 1
    assert session.ledger.requests == 6 and session.ledger.tokens == 12288
    assert all(not t.is_alive() for t in trial.threads.values())


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_gateway_startup_failure_closes_session_and_retains_witness(
    tmp_path, monkeypatch
):
    from benchmarks.workflow.provider_gateway import RemoteWorkflowSession

    private = tmp_path / "private.env"
    private.write_text("INFERENCEHUB_TOKEN=private-fake-key\n")
    private.chmod(0o600)
    session = RemoteWorkflowSession(tmp_path / "session", credential_file=private)
    with pytest.raises(ValueError, match="startup_not_valid"):
        with session.workflow("missing-warmup", [binding()]) as trial:
            trial.arm()
    assert session.ledger.halted and not session.active
    assert not json.loads((trial.root / "witness.json").read_bytes())["complete"]
    with pytest.raises(ValueError):
        with session.workflow("should-not-start", [binding("llm")]):
            raise AssertionError("closed pilot")


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_partial_gateway_thread_start_failure_does_not_wait_for_unstarted_server(
    tmp_path, monkeypatch
):
    import threading

    from benchmarks.workflow.provider_gateway import RemoteWorkflowSession

    private = tmp_path / "private.env"
    private.write_text("INFERENCEHUB_TOKEN=private-fake-key\n")
    private.chmod(0o600)
    session = RemoteWorkflowSession(tmp_path / "session", credential_file=private)
    original = threading.Thread.start
    count = [0]

    def start(thread):
        count[0] += 1
        if count[0] == 2:
            raise RuntimeError("thread_start_fault")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="thread_start_fault"):
        with session.workflow("partial", [binding(), binding("llm")]):
            raise AssertionError("must not yield")
    assert session.ledger.halted and not session.active
    witness = json.loads((session.ledger.root / "partial/witness.json").read_bytes())
    assert not witness["complete"] and witness["threads_settled"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_final_gateway_witness_failure_halts_session(tmp_path, monkeypatch):
    from benchmarks.workflow.provider_gateway import GatewayTrial, RemoteWorkflowSession

    private = tmp_path / "private.env"
    private.write_text("INFERENCEHUB_TOKEN=private-fake-key\n")
    private.chmod(0o600)
    session = RemoteWorkflowSession(tmp_path / "session", credential_file=private)
    original = GatewayTrial._write

    def write(trial, name, value):
        if name == "witness.json":
            raise OSError("disk_full")
        return original(trial, name, value)

    monkeypatch.setattr(GatewayTrial, "_write", write)
    with pytest.raises(OSError, match="disk_full"):
        with session.workflow("disk-full", [binding("llm")]):
            pass
    assert session.ledger.halted and not session.active


def test_gateway_partial_registration_closes_every_listener_and_retains_witness(
    tmp_path,
):
    from benchmarks.workflow.provider_gateway import GatewayTrial

    class Listener:
        active = 0
        sealed = False

        def __init__(self, failing=False):
            self.closed = False
            self.failing = failing

        def server_close(self):
            self.closed = True
            if self.failing:
                raise OSError("close_fault")

        def snapshot(self):
            return {"complete": False, "active": 0}

    budget = ledger(tmp_path)
    trial = GatewayTrial.__new__(GatewayTrial)
    trial.closed = False
    trial.armed = False
    trial.session = type("Session", (), {"ledger": budget})()
    trial.root = tmp_path / "partial"
    trial.root.mkdir()
    first, second = Listener(True), Listener()
    trial.relays = {"vlm": first, "llm": second}
    trial.stops = {}
    trial.threads = {}
    with pytest.raises(RuntimeError, match="workflow_gateway_cleanup"):
        trial.close()
    assert first.closed and second.closed and budget.halted
    witness = json.loads((trial.root / "witness.json").read_bytes())
    assert not witness["complete"] and not witness["threads_settled"]
    assert witness["cleanup_failures"] == {"vlm": "OSError"}

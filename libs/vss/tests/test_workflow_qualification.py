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

"""Real local HTTP chains, one-shot attribution, privacy and failure boundaries."""

import asyncio
import hashlib
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from benchmarks.workflow.config import Workflow
from benchmarks.workflow.file_witness import FinalFileCaptionWitness
from benchmarks.workflow.operation import operation
from benchmarks.workflow.relay import Relay, controlled_request
from vss.storage import InMemoryStorageAdapter


@contextmanager
def running(server):
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, body, mime="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def data(self, code, body):
        self.send(code, json.dumps(body).encode())

    def do_GET(self):
        asset = self.path.split("/")[-1]
        if asset in self.server.assets:
            self.data(200, {"id": asset})
        else:
            self.data(
                400, {"code": "BadParameter", "message": "No such resource " + asset}
            )

    def do_DELETE(self):
        asset = self.path.split("/")[-1]
        if self.server.cleanup_failure:
            self.data(500, {})
        else:
            self.server.assets.discard(asset)
            self.send(204, b"")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/files"):
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: "
                + self.headers["Content-Type"].encode()
                + b"\r\n\r\n"
                + raw
            )
            asset = next(
                p.get_content()
                for p in message.iter_parts()
                if p.get_param("name", header="content-disposition") == "id"
            )
            self.server.assets.add(asset)
            self.data(200, {"id": asset})
            return
        body = json.loads(raw)
        if self.path.endswith("/chat/completions"):
            is_frame = body["model"] == "test-vlm"
            if is_frame:
                self.server.frame_calls += 1
            else:
                self.server.llm_calls += 1
                self.server.llm_started.set()
            if self.server.slow:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                while not self.server.slow_release.wait(0.02):
                    try:
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    except OSError:
                        break
                return
            self.data(
                200,
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": (self.server.frame_model_override or body["model"])
                    if is_frame
                    else body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "events": [
                                            {
                                                "start_time": 0,
                                                "end_time": 1,
                                                "type": "test",
                                                "description": "PRIVATE_CAPTION",
                                            }
                                        ]
                                    }
                                )
                                if is_frame
                                else "PRIVATE_SUMMARY",
                            },
                            "finish_reason": self.server.reason,
                        }
                    ],
                    "usage": self.server.usage,
                },
            )
            return
        if self.path.endswith("/generate_captions"):
            assert body["id"] in self.server.assets
            items = []
            for index in range(self.server.count):
                items.append(
                    {
                        "chunk_id": index,
                        "start_time": index * 30,
                        "end_time": min((index + 1) * 30, self.server.duration),
                        "frame_count": 8,
                        "content": json.dumps(
                            {
                                "events": [
                                    {
                                        "start_time": index * 30,
                                        "end_time": min(
                                            (index + 1) * 30, self.server.duration
                                        ),
                                        "type": "test",
                                        "description": "PRIVATE_CAPTION",
                                    }
                                ]
                            }
                        ),
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "finish_reason": "stop",
                    }
                )
            if self.server.reverse:
                items.reverse()
            if self.server.caption_transform:
                items = self.server.caption_transform(items)
            data = (
                b"".join(
                    b"data: "
                    + json.dumps(
                        {
                            "id": self.server.query,
                            "model": "test-vlm",
                            "chunk_responses": [item],
                        }
                    ).encode()
                    + b"\n\n"
                    for item in items
                )
                + b"data: [DONE]\n\n"
            )
            self.send(200, data, "text/event-stream")
            return
        if self.path.endswith("/summarize"):
            self.server.normal_started.set()
            if not self.server.normal_release.wait(10):
                return
            time.sleep(self.server.normal_delay)
            with httpx.Client(trust_env=False, timeout=3) as client:
                response = client.post(
                    self.server.relay_url + "/chat/completions",
                    json={
                        "model": "test-llm",
                        "max_tokens": 512,
                        "messages": [
                            {"role": "user", "content": "PRIVATE_NORMAL_PROMPT"}
                        ],
                    },
                )
            self.server.normal_finished.set()
            if response.status_code != 200:
                self.data(500, {})
                return
            payload = {
                "video_summary": "PRIVATE_SUMMARY",
                "events": [
                    {"description": "PRIVATE_EVENT"}
                    for _ in range(self.server.event_count)
                ],
            }
            self.data(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": json.dumps(payload)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_chunks_processed": self.server.count},
                },
            )
            return
        self.data(404, {})


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    fixture.daemon_threads = True
    fixture.assets = set()
    fixture.count = fixture.event_count = 1
    fixture.duration = 2
    fixture.reverse = fixture.cleanup_failure = fixture.slow = False
    fixture.caption_transform = None
    fixture.llm_calls = 0
    fixture.frame_calls = 0
    fixture.frame_model_override = None
    fixture.normal_delay = 0
    fixture.llm_started = threading.Event()
    fixture.slow_release = threading.Event()
    fixture.normal_started = threading.Event()
    fixture.normal_release = threading.Event()
    fixture.normal_release.set()
    fixture.normal_finished = threading.Event()
    fixture.reason = "stop"
    fixture.usage = {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}
    fixture.query = str(uuid4())
    with (
        running(fixture) as base,
        Relay(base + "/llm/v1", "test-llm", str(uuid4()), deadline_s=2) as relay,
        running(relay) as relay_base,
    ):
        fixture.relay_url = relay_base + "/" + relay.token + "/v1"
        for key, url in {
            "M8_NORMAL": base + "/normal",
            "M8_RTVI": base,
            "M8_LLM": fixture.relay_url,
            "M8_ES": base + "/es",
            "M8_RELAY": relay_base + "/control/" + relay.token,
        }.items():
            monkeypatch.setenv(key, url)
        video = tmp_path / "private-video.mp4"
        video.write_bytes(b"controlled media HTTP fixture")
        manifest = tmp_path / "stack.json"
        manifest.write_text("{}")
        profile = Workflow(
            arm="library",
            video=video,
            video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
            duration_s=2,
            normal_url_env="M8_NORMAL",
            rtvi_url_env="M8_RTVI",
            llm_url_env="M8_LLM",
            es_url_env="M8_ES",
            relay_control_env="M8_RELAY",
            vlm_model="test-vlm",
            llm_model="test-llm",
            stack_manifest=manifest,
            stack_manifest_sha256=hashlib.sha256(b"{}").hexdigest(),
            normal_config_sha256="0" * 64,
        )
        try:
            yield profile, fixture, relay
        finally:
            fixture.slow_release.set()
            fixture.normal_release.set()


@pytest.mark.parametrize("arm", ["normal", "library"])
async def test_complete_http_workflows_are_diagnostic_and_private(
    workflow, arm, monkeypatch
):
    profile, fixture, relay = workflow
    monkeypatch.setenv("VSS_STORAGE_BACKEND", "file")
    monkeypatch.setenv("VSS_STORAGE_ROOT", "/must/not/be/used")
    result = await operation(
        profile.model_copy(update={"arm": arm}), 1, store_factory=InMemoryStorageAdapter
    )
    assert result["status"] == "completed_diagnostic", result
    assert result["qualified"] is False and result["comparative_statistics"] is None
    assert result["upload_identity_verified"] and result["cleanup"]["asset_deleted"]
    assert result["llm_relay"]["sealed"] and result["llm_relay"]["complete"]
    assert len(result["llm_relay"]["records"]) == fixture.llm_calls == 1
    assert not fixture.assets
    assert all(
        marker not in json.dumps(result)
        for marker in (
            "PRIVATE_",
            "127.0.0.1",
            str(profile.video),
            relay.token,
            fixture.query,
        )
    )


async def test_normal_five_chunks_four_events_are_distinct_facts(workflow):
    profile, fixture, _ = workflow
    fixture.count = 5
    fixture.event_count = 4
    result = await operation(profile.model_copy(update={"arm": "normal"}), 5)
    assert result["status"] == "completed_diagnostic"
    assert result["service_reported_chunk_count"] == 5 and result["event_count"] == 4
    assert result["raw_normal_caption_coverage"] == "unavailable"


async def test_startup_completion_is_retained_before_one_way_admission(workflow):
    profile, fixture, relay = workflow
    assert request(relay).status_code == 200
    result = await operation(profile.model_copy(update={"arm": "normal"}), 1)
    assert result["status"] == "completed_diagnostic", result
    assert [r["phase"] for r in result["llm_relay"]["records"]] == [
        "before_client_admission",
        "after_client_admission",
    ]
    assert fixture.llm_calls == 2 and relay.admitted
    assert (
        httpx.post(
            f"http://127.0.0.1:{relay.server_port}/control/{relay.token}/arm",
            trust_env=False,
        ).status_code
        == 409
    )


async def test_library_diagnostic_cap_reaches_actual_provider_request(workflow):
    profile, _, _ = workflow
    result = await operation(
        profile.model_copy(update={"summary_max_tokens": 2048}),
        1,
        store_factory=InMemoryStorageAdapter,
    )
    assert result["status"] == "completed_diagnostic"
    assert result["llm_relay"]["records"][0]["generation"]["max_tokens"] == 2048


@pytest.mark.parametrize(
    "state", ["failed_startup", "active_startup", "already_admitted", "sealed"]
)
def test_admission_cannot_reopen_or_erase_incomplete_startup(workflow, state):
    _, _, relay = workflow
    if state == "failed_startup":
        relay.records.append({"status": "failed", "phase": "before_client_admission"})
    if state == "active_startup":
        relay.active = 1
    if state == "already_admitted":
        relay.admitted = True
    if state == "sealed":
        relay.sealed = True
    assert not relay.admit()
    assert relay.rejected == 1
    assert not relay.snapshot()["complete"]


@pytest.mark.parametrize("failure", ["length", "missing_usage", "ordering", "cleanup"])
async def test_adverse_completion_and_cleanup_fail_closed(workflow, failure):
    profile, fixture, _ = workflow
    if failure == "length":
        fixture.reason = "length"
    if failure == "missing_usage":
        fixture.usage = {}
    if failure == "ordering":
        fixture.count = 2
        fixture.duration = 60
        fixture.reverse = True
        profile = profile.model_copy(update={"duration_s": 60})
    if failure == "cleanup":
        fixture.cleanup_failure = True
    result = await operation(
        profile, fixture.count, store_factory=InMemoryStorageAdapter
    )
    assert result["status"] == "failed"
    assert result["failure"]
    assert result["llm_relay"]["sealed"]
    if failure == "ordering":
        assert fixture.llm_calls == 0
        assert result["failure"] != "cleanup_or_observation"
    if failure == "cleanup":
        assert result["cleanup"]["asset_deleted"] is False


def request(relay, body=None):
    return httpx.post(
        f"http://127.0.0.1:{relay.server_port}/{relay.token}/v1/chat/completions",
        json=body or {"model": "test-llm", "max_tokens": 512, "messages": []},
        trust_env=False,
        timeout=3,
    )


def test_relay_seal_is_irreversible_and_replay_is_visible(workflow):
    _, fixture, relay = workflow
    assert request(relay).status_code == 200
    httpx.post(
        f"http://127.0.0.1:{relay.server_port}/control/{relay.token}/seal",
        trust_env=False,
    )
    assert request(relay).status_code == 409
    assert fixture.llm_calls == 1 and relay.snapshot()["rejected"] == 1
    assert not relay.snapshot()["complete"]


def test_controlled_relay_witnesses_actual_forwarded_settings(workflow, monkeypatch):
    from benchmarks.workflow.relay import Handler

    _, _, relay = workflow
    relay.generation_cap = 2048
    seen = []
    original = Handler.forward

    async def forward(self, raw, headers):
        seen.append(json.loads(raw))
        return await original(self, raw, headers)

    monkeypatch.setattr(Handler, "forward", forward)
    assert (
        request(
            relay,
            {
                "model": "test-llm",
                "messages": [{"role": "user", "content": "captions"}],
            },
        ).status_code
        == 200
    )
    body = seen[0]
    assert {
        key: body[key] for key in ("max_tokens", "temperature", "top_p", "seed")
    } == {
        "max_tokens": 2048,
        "temperature": 0,
        "top_p": 1,
        "seed": 1729,
    }
    assert body["messages"][0]["content"].startswith("/no_think\n")
    record = relay.records[0]
    assert record["generation_policy"] == "deterministic_no_think.v1"
    assert record["request_sha256"] != record["client_request_sha256"]
    assert record["requested_controls"]["effective_settings_verified"] is False
    assert record["generation"]["seed"] == 1729


def test_controlled_request_does_not_mutate_prompts_or_repeat_directive():
    body = {
        "messages": [
            {"role": "system", "content": "/no_think\nORIGINAL"},
            {"role": "user", "content": "VIDEO_CAPTIONS"},
        ]
    }
    result = controlled_request(body, 2048)
    assert result["messages"] == body["messages"]
    assert "seed" not in body
    assert controlled_request(result, 2048) == result


@pytest.mark.parametrize(
    "extra",
    [
        {"max_completion_tokens": 2048},
        {"reasoning_effort": "high"},
        {"chat_template_kwargs": {"enable_thinking": True}},
        {"reasoning": {"effort": "high"}},
        {"enable_reasoning": True},
    ],
)
def test_controlled_request_rejects_ambiguous_cap_or_reasoning(extra):
    with pytest.raises(ValueError):
        controlled_request(
            {"messages": [{"role": "user", "content": "text"}], **extra}, 2048
        )


def test_relay_overflow_and_slow_drip_response_invalidate_evidence(workflow):
    _, fixture, relay = workflow
    relay.max_records = 1
    assert request(relay).status_code == 200
    assert request(relay).status_code == 429
    assert relay.dropped == 1
    assert fixture.llm_calls == 1


def test_whole_response_deadline_and_active_fence(workflow):
    _, fixture, relay = workflow
    fixture.slow = True
    relay.deadline_s = 2
    thread = threading.Thread(target=lambda: request_catching_disconnect(relay))
    thread.start()
    try:
        assert fixture.llm_started.wait(5), "upstream must receive the request"
        assert relay.active == 1
        relay.sealed = True
        assert not relay.snapshot()["complete"]
        thread.join(timeout=5)
        deadline = time.monotonic() + 5
        while relay.active and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not thread.is_alive() and relay.active == 0
        assert relay.records[0]["status"] == "failed"
        assert request(relay).status_code == 409
        assert fixture.llm_calls == 1
    finally:
        fixture.slow_release.set()
        thread.join(timeout=5)


def request_catching_disconnect(relay):
    try:
        request(relay)
    except httpx.HTTPError:
        pass


def test_header_and_body_slow_drip_deadlines(workflow):
    _, fixture, relay = workflow
    relay.deadline_s = 0.1
    for initial in (
        b"POST /",
        (
            f"POST /{relay.token}/v1/chat/completions HTTP/1.0\r\n"
            "Content-Length: 100\r\n\r\n"
        ).encode(),
    ):
        with socket.create_connection(relay.server_address, timeout=1) as sock:
            sock.sendall(initial)
            for _ in range(20):
                try:
                    sock.sendall(b" ")
                except OSError:
                    break
                time.sleep(0.01)
            sock.settimeout(0.5)
            try:
                assert sock.recv(4096) == b"" or not relay.snapshot()["complete"]
            except (ConnectionResetError, ConnectionAbortedError):
                pass
    assert fixture.llm_calls == 0


async def test_timeout_seals_old_route_against_late_remote_model_calls(workflow):
    profile, fixture, relay = workflow
    fixture.normal_release.clear()
    profile = profile.model_copy(update={"arm": "normal", "work_timeout_s": 5})
    task = asyncio.create_task(operation(profile, 1))
    try:
        assert await asyncio.to_thread(fixture.normal_started.wait, 5)
        result = await asyncio.wait_for(task, 15)
        assert result["status"] == "failed" and result["failure"] == "TimeoutError"
        assert result["remote_inference_settlement"] == "not_proven"
        assert relay.sealed
        # Release remote work only after the abandoned route is sealed, then
        # wait for its actual response rather than assuming a scheduling delay.
        fixture.normal_release.set()
        assert await asyncio.to_thread(fixture.normal_finished.wait, 5)
        assert fixture.llm_calls == 0
        assert relay.rejected == 1
        assert not relay.snapshot()["complete"]
    finally:
        fixture.normal_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "header",
    [
        "Content-Length: -1",
        "Content-Length: 1048577",
        "Content-Length: 2\r\nContent-Length: 2",
        "Transfer-Encoding: chunked",
    ],
)
def test_relay_rejects_ambiguous_or_oversized_request_framing(workflow, header):
    _, fixture, relay = workflow
    with socket.create_connection(relay.server_address, timeout=1) as sock:
        sock.sendall(
            (
                f"POST /{relay.token}/v1/chat/completions HTTP/1.0\r\n"
                + header
                + "\r\n\r\n{}"
            ).encode()
        )
        assert b"502" in sock.recv(4096)
    assert fixture.llm_calls == 0
    assert relay.records[0]["status"] == "failed"


def test_existing_output_cannot_replay_operation(tmp_path, monkeypatch, capsys):
    from benchmarks.workflow import __main__ as cli

    output = tmp_path / "report.json"
    output.write_text("original attempt")
    monkeypatch.setattr(
        "sys.argv", ["workflow", "--config", "/missing", "--output", str(output)]
    )
    monkeypatch.setattr(
        cli, "load", lambda *_: pytest.fail("must reject replay before loading")
    )
    assert cli.main() == 1
    assert output.read_text() == "original attempt"
    report = json.loads(capsys.readouterr().out)
    assert report["qualified"] is False and report["comparative_statistics"] is None


def test_idle_partial_header_after_success_is_not_complete(workflow):
    _, fixture, relay = workflow
    assert request(relay).status_code == 200
    with socket.create_connection(relay.server_address, timeout=1) as sock:
        sock.sendall(
            (
                f"POST /{relay.token}/v1/chat/completions HTTP/1.0\r\nContent-Len"
            ).encode()
        )
        time.sleep(1.1)
        assert sock.recv(4096) == b""
    relay.sealed = True
    assert relay.rejected == 1 and not relay.snapshot()["complete"]
    assert fixture.llm_calls == 1


def file_witness(profile, count=2):
    profile = profile.model_copy(update={"duration_s": count * 30})
    asset = str(uuid4())
    witness = FinalFileCaptionWitness(profile, count, asset)
    request = httpx.Request(
        "POST",
        "http://example.invalid/v1/generate_captions",
        headers={"x-stream-id": asset},
        json={
            "id": asset,
            "model": profile.vlm_model,
            "prompt": profile.caption_prompt,
            "stream": True,
            "chunk_duration": 30,
            "chunk_overlap_duration": 0,
            **profile.caption_settings(),
        },
    )
    return witness, request


def caption_event(index, **updates):
    return {
        "chunk_id": index,
        "start_time": str(index * 30),
        "end_time": str((index + 1) * 30),
        "frame_count": 8,
        "content": "PRIVATE_CAPTION",
        "input_tokens": 10,
        "output_tokens": 5,
        "finish_reason": "stop",
        **updates,
    }


def sse(items, query=None, **updates):
    return (
        b"data: "
        + json.dumps(
            {
                "id": query or "00000000-0000-0000-0000-000000000001",
                "model": "test-vlm",
                "chunk_responses": items,
                **updates,
            }
        ).encode()
        + b"\n\n"
    )


def test_final_file_contract_accepts_fragmented_unique_set_not_append_order(workflow):
    from benchmarks.e2e.witness import CaptionWitness, WitnessError

    profile, _, _ = workflow
    witness, request_ = file_witness(profile)
    witness.bind_request(request_)
    wire = sse([caption_event(1), caption_event(0)]) + b"data: [DONE]\n\n"
    for byte in wire:
        witness.feed(bytes([byte]))
    evidence = witness.finish()
    assert evidence["arrival_order"] == [1, 0]
    assert [c["index"] for c in evidence["chunks"]] == [0, 1]
    assert evidence["caption_completion"] == "reported_stop"
    assert evidence["request_settings_and_upload_id_verified"]
    assert all(
        v not in json.dumps(evidence)
        for v in ("PRIVATE_", witness.asset, "example.invalid")
    )
    with pytest.raises(WitnessError):
        CaptionWitness("test-vlm", 60).feed(wire)
    assert profile.caption_contract == "append_only_final_chunks.v1"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "revision",
        "missing",
        "index",
        "bool_index",
        "window",
        "overlap",
        "nan",
        "bool_time",
        "bad_time",
        "empty",
        "frames",
        "bool_usage",
        "missing_usage",
        "length",
        "over_cap_stop",
        "unknown_at_cap",
        "unknown_finish",
        "model",
        "query",
        "provider_error",
        "empty_list",
        "terminal_only",
        "no_terminal",
        "trailing",
        "twice_done",
        "duplicate_json_key",
        "bad_chunk",
        "bad_identity",
    ],
)
def test_final_file_contract_rejects_adverse_streams(workflow, mutation):
    from benchmarks.e2e.witness import WitnessError

    profile, _, _ = workflow
    witness, request_ = file_witness(profile)
    witness.bind_request(request_)
    items = [caption_event(0), caption_event(1)]
    changes = {
        "index": {"chunk_id": 2},
        "bool_index": {"chunk_id": True},
        "window": {"start_time": "31"},
        "overlap": {"start_time": "29.95"},
        "nan": {"end_time": "nan"},
        "bool_time": {"start_time": False},
        "bad_time": {"start_time": "not-a-number"},
        "empty": {"content": " "},
        "frames": {"frame_count": False},
        "bool_usage": {"input_tokens": True},
        "missing_usage": {"output_tokens": None},
        "length": {"finish_reason": "length"},
        "over_cap_stop": {"output_tokens": 257},
        "unknown_at_cap": {"output_tokens": 256, "finish_reason": None},
        "unknown_finish": {"finish_reason": "PRIVATE_REASON"},
    }
    if mutation in changes:
        items[1].update(changes[mutation])
    if mutation in ("duplicate", "revision"):
        items = [
            items[0],
            caption_event(
                0,
                content="PRIVATE_REVISION"
                if mutation == "revision"
                else "PRIVATE_CAPTION",
            ),
        ]
    if mutation == "missing":
        items = items[:1]
    if mutation == "empty_list":
        items = []
    if mutation == "bad_chunk":
        items = [None]
    kwargs = {"model": "wrong"} if mutation == "model" else {}
    if mutation == "provider_error":
        kwargs["error"] = "PRIVATE_ERROR"
    wire = sse(items, **kwargs)
    if mutation == "query":
        wire = sse(items[:1]) + sse(items[1:], query=str(uuid4()))
    if mutation == "bad_identity":
        wire = sse(items, id=True)
    if mutation == "duplicate_json_key":
        wire = wire.replace(b'"model":', b'"model":"test-vlm","model":')
    if mutation == "terminal_only":
        wire = b""
    if mutation != "no_terminal":
        wire += b"data: [DONE]\n\n"
    if mutation == "trailing":
        wire += b"data: partial"
    if mutation == "twice_done":
        wire += b"data: [DONE]\n\n"
    with pytest.raises(WitnessError):
        witness.feed(wire)
        witness.finish()
    assert witness.failure is not None
    assert "PRIVATE_" not in json.dumps(witness.observation())


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "model",
        "prompt",
        "stream",
        "chunk_duration",
        "chunk_overlap_duration",
        "max_tokens",
        "enable_audio",
        "url",
        "header",
        "method",
        "path",
        "query",
        "duplicate",
    ],
)
def test_final_file_request_binding_rejects_drift_before_dispatch(workflow, mutation):
    from benchmarks.e2e.witness import WitnessError

    witness, req = file_witness(workflow[0])
    body = json.loads(req.content)
    headers = dict(req.headers)
    method, path = "POST", "http://example.invalid/v1/generate_captions"
    if mutation == "header":
        headers["x-stream-id"] = str(uuid4())
    elif mutation == "method":
        method = "PUT"
    elif mutation == "path":
        path += "/other"
    elif mutation == "query":
        path += "?id=other"
    elif mutation == "duplicate":
        witness.bind_request(req)
    elif mutation == "url":
        body["url"] = "PRIVATE_URL"
    else:
        body[mutation] = "PRIVATE_OVERRIDE"
    with pytest.raises(WitnessError):
        witness.bind_request(httpx.Request(method, path, headers=headers, json=body))
    assert witness.failure and "PRIVATE_" not in json.dumps(witness.observation())


async def test_unordered_file_http_chain_preserves_public_assembly_and_unknown_finish(
    workflow,
):
    profile, fixture, _ = workflow
    fixture.count = fixture.event_count = 2
    fixture.duration = 60
    fixture.reverse = True
    fixture.caption_transform = lambda items: [
        {k: v for k, v in item.items() if k != "finish_reason"} for item in items
    ]
    result = await operation(
        profile.model_copy(
            update={"duration_s": 60, "caption_contract": "final_file_chunk_set.v1"}
        ),
        2,
        store_factory=InMemoryStorageAdapter,
    )
    assert result["status"] == "completed_diagnostic", result
    evidence = result["caption_witness"]
    assert (
        evidence["arrival_order"] == [1, 0]
        and evidence["caption_completion"] == "unqualified"
    )
    assert result["caption_count"] == 2 and result["qualified"] is False
    assert result["comparative_statistics"] is None and fixture.llm_calls == 1
    assert not fixture.assets and all(result["cleanup"].values())


async def test_file_caption_truncation_blocks_store_and_summary(workflow):
    profile, fixture, _ = workflow
    fixture.caption_transform = lambda items: [
        {**item, "output_tokens": 256, "finish_reason": None} for item in items
    ]

    class NoWrites(InMemoryStorageAdapter):
        async def write(self, *args, **kwargs):
            pytest.fail("caption rejection must precede ES writes")

    result = await operation(
        profile.model_copy(update={"caption_contract": "final_file_chunk_set.v1"}),
        1,
        store_factory=NoWrites,
    )
    assert (
        result["status"] == "failed" and result["failure_stage"] == "library_workflow"
    )
    assert (
        result["caption_stream_observation"]["failure"] == "caption_truncated_or_at_cap"
    )
    assert fixture.llm_calls == 0 and "store_write" not in result["stages_s"]
    assert not fixture.assets and result["cleanup"]["asset_deleted"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX private workflow diagnostics")
def test_workflow_cli_sizes_observers_for_long_workflow(
    workflow, tmp_path, monkeypatch, capsys
):
    from benchmarks.workflow import __main__ as cli

    profile, fixture, _ = workflow
    profile = profile.model_copy(
        update={
            "observation": "resources",
            "store_backend": "memory",
            "work_timeout_s": 600,
            "cleanup_timeout_s": 60,
        }
    )
    output = tmp_path / "observed-workflow.json"
    monkeypatch.setattr(
        sys, "argv", ["workflow", "--config", "unused.json", "--output", str(output)]
    )
    monkeypatch.setattr(cli, "load", lambda _: profile)
    monkeypatch.setattr(cli, "preflight", lambda _: {"occupied_windows": [0]})
    assert cli.main() == 0
    report = json.loads(output.read_text())
    duration = profile.work_timeout_s + 8 * profile.cleanup_timeout_s
    policy = report["observation_policy"]
    assert duration / policy["worker_interval_s"] < 6000
    assert duration / policy["gpu_interval_s"] < 4000
    assert report["observer_complete"] and not fixture.assets
    capsys.readouterr()


def test_final_file_contract_admits_occupied_windows_with_empty_sub100ms_tail(workflow):
    profile, _, _ = workflow
    profile = profile.model_copy(update={"duration_s": 300.033067})
    witness, _ = file_witness(profile, 10)
    # Preserve admitted duration instead of ceil(duration/30)==11 windows.
    witness.duration = profile.duration_s
    witness.profile = profile
    _, req = file_witness(profile, 10)
    body = json.loads(req.content)
    body["id"] = witness.asset
    req = httpx.Request(
        "POST", str(req.url), headers={"x-stream-id": witness.asset}, json=body
    )
    witness.bind_request(req)
    witness.feed(
        sse([caption_event(i) for i in reversed(range(10))]) + b"data: [DONE]\n\n"
    )
    assert len(witness.finish()["chunks"]) == 10


@pytest.mark.parametrize(
    "field", [None, "content", "time", "frames", "usage", "asset", "count"]
)
def test_final_file_normalized_outputs_match_before_storage(workflow, field):
    from benchmarks.e2e.witness import WitnessError
    from vss.ingestion import RequestSource
    from vss.ingestion.backend import _to_chunk_response

    witness, req = file_witness(workflow[0])
    witness.bind_request(req)
    items = [
        caption_event(0, start_time="0.0000000009"),
        caption_event(1, end_time="59.9999999991"),
    ]
    witness.feed(sse(items) + b"data: [DONE]\n\n")
    chunks = [
        _to_chunk_response(RequestSource(source_id=witness.asset), item)
        for item in items
    ]
    if field == "content":
        chunks[0].vlm_response = "different"
    if field == "time":
        chunks[0].chunk.start_pts += 1
    if field == "frames":
        chunks[0].rtvi_frame_count += 1
    if field == "usage":
        chunks[0].vlm_stats["output_tokens"] += 1
    if field == "asset":
        chunks[0].chunk.sourceId = "different"
    if field == "count":
        chunks.pop()
    if field:
        with pytest.raises(WitnessError, match="normalized_chunk_mismatch"):
            witness.validate_normalized(chunks)
        assert witness.failure == "normalized_chunk_mismatch"
    else:
        witness.validate_normalized(chunks)


async def test_normalization_rejection_precedes_storage_and_summary(
    workflow, monkeypatch
):
    from benchmarks.e2e.witness import WitnessError

    profile, fixture, _ = workflow

    def mismatch(self, chunks):
        self.failure = "normalized_chunk_mismatch"
        raise WitnessError(self.failure)

    monkeypatch.setattr(FinalFileCaptionWitness, "validate_normalized", mismatch)

    class NoWrites(InMemoryStorageAdapter):
        async def write(self, *args, **kwargs):
            pytest.fail("normalized rejection must precede ES writes")

    result = await operation(
        profile.model_copy(update={"caption_contract": "final_file_chunk_set.v1"}),
        1,
        store_factory=NoWrites,
    )
    assert result["status"] == "failed" and result["failure"] == "WitnessError"
    assert (
        result["caption_stream_observation"]["failure"] == "normalized_chunk_mismatch"
    )
    assert fixture.llm_calls == 0 and "store_write" not in result["stages_s"]
    assert not fixture.assets and result["cleanup"]["asset_deleted"]

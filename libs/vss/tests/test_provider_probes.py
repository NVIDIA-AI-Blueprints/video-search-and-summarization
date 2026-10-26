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

"""Actual HTTP serialization through a mock transport, no external inference."""

import asyncio
import json
import os

import httpx
import pytest

from benchmarks import providers as p

SECRET = "PRIVATE_PROVIDER_TOKEN_123456"


def binding(**changes):
    return p.Binding.model_validate(
        dict(
            label="remote-test",
            role="llm",
            placement="remote",
            base_url="https://inference-api.nvidia.com/v1",
            model="test/model",
            response_model="test/model",
        )
        | changes
    )


def request(cap=1024):
    return {
        "model": "test/model",
        "messages": [{"role": "user", "content": "private input"}],
        "max_tokens": cap,
        "temperature": 0,
        "seed": 1729,
    }


def response(**changes):
    return {
        "model": "test/model",
        "choices": [
            {"message": {"content": "coherent output"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        **changes,
    }


def mock(monkeypatch, handler):
    original = httpx.AsyncClient

    class Body(httpx.AsyncByteStream):
        def __init__(self, value):
            self.value = value

        async def __aiter__(self):
            yield self.value

    async def streaming(req):
        value = handler(req)
        if hasattr(value, "__await__"):
            value = await value
        return httpx.Response(
            value.status_code, headers=value.headers, stream=Body(value.content)
        )

    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(streaming))

    monkeypatch.setattr(p.httpx, "AsyncClient", factory)


@pytest.mark.parametrize(
    "url",
    [
        "http://inference-api.nvidia.com/v1",
        "https://inference-api.nvidia.com.evil/v1",
        "https://user:password@inference-api.nvidia.com/v1",
        "https://inference-api.nvidia.com/v1?q=secret",
        "https://inference-api.nvidia.com:443/v1",
        "https://inference-api.nvidia.com/v1/",
    ],
)
def test_remote_route_fail_closed(url):
    with pytest.raises(ValueError):
        p.Binding.model_validate(binding().model_dump() | {"base_url": url})


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_private_literal_credentials(tmp_path):
    path = tmp_path / ".env"
    path.write_text('UNRELATED=ignored\nexport INFERENCEHUB_TOKEN="' + SECRET + '"\n')
    path.chmod(0o600)
    assert p.private_key(path, "INFERENCEHUB_TOKEN") == SECRET
    path.chmod(0o644)
    with pytest.raises(ValueError):
        p.private_key(path, "INFERENCEHUB_TOKEN")
    path.chmod(0o600)
    for text in (
        "INFERENCEHUB_TOKEN=$(dangerous)",
        "INFERENCEHUB_TOKEN=" + SECRET + "\nINFERENCEHUB_TOKEN=" + SECRET,
    ):
        path.write_text(text)
        with pytest.raises(ValueError):
            p.private_key(path, "INFERENCEHUB_TOKEN")
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        p.private_key(link, "INFERENCEHUB_TOKEN")


def test_success_preserves_inputs_and_controls(monkeypatch):
    body = request()

    def handler(req):
        assert str(req.url) == "https://inference-api.nvidia.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer " + SECRET
        assert json.loads(req.content) == body
        return httpx.Response(200, json=response())

    mock(monkeypatch, handler)
    result, payload = asyncio.run(p.complete(binding(), body, key=SECRET, timeout_s=3))
    assert result["status"] == "completed_probe"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["completion_tokens"] == 4
    assert not result["effective_settings_verified"]
    assert not result["placement_comparison_qualified"]
    assert payload == response()
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("code", [301, 401, 429, 500])
def test_errors_never_return_bodies_or_follow_redirects(monkeypatch, code):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            code, text=SECRET, headers={"Location": "https://other.invalid/"}
        )

    mock(monkeypatch, handler)
    result, payload = asyncio.run(
        p.complete(binding(), request(), key=SECRET, timeout_s=3)
    )
    assert len(calls) == 1
    assert payload is None
    assert result["status"] == "failed"
    assert result["http_status"] == code
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "body",
    [
        response(model="unannounced/alias"),
        response(choices=[{"message": {"content": SECRET}, "finish_reason": "stop"}]),
    ],
)
def test_success_credentials_or_wrong_model_are_rejected(monkeypatch, body):
    mock(monkeypatch, lambda req: httpx.Response(200, json=body))
    result, payload = asyncio.run(
        p.complete(binding(), request(), key=SECRET, timeout_s=3)
    )
    assert result["status"] == "failed" and payload is None
    assert SECRET not in json.dumps(result)


def test_decoded_credential_echo_is_rejected(monkeypatch):
    raw = json.dumps(
        response(choices=[{"message": {"content": SECRET}, "finish_reason": "stop"}])
    ).replace("PRIVATE_", "PRIVATE\\u005f")
    assert SECRET not in raw
    mock(monkeypatch, lambda req: httpx.Response(200, content=raw))
    result, payload = asyncio.run(
        p.complete(binding(), request(), key=SECRET, timeout_s=3)
    )
    assert result["status"] == "failed" and payload is None
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "value", ["private text", {"secret": "hidden"}, -1, True, 10000001]
)
def test_reasoning_counter_has_a_scalar_bound(monkeypatch, value):
    mock(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "completion_tokens_details": {"reasoning_tokens": value},
                }
            ),
        ),
    )
    result, _ = asyncio.run(p.complete(binding(), request(), key=SECRET, timeout_s=3))
    assert result["reasoning_usage"] is None


@pytest.mark.parametrize("field,value", [("n", 2), ("best_of", 4), ("n", True)])
def test_multiple_completion_controls_reject_before_http(monkeypatch, field, value):
    def unexpected(req):
        raise AssertionError("must not call provider")

    mock(monkeypatch, unexpected)
    with pytest.raises(ValueError):
        asyncio.run(
            p.complete(binding(), request() | {field: value}, key=SECRET, timeout_s=3)
        )


def test_deadline_and_no_retries(monkeypatch, tmp_path):
    calls = []

    async def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout(SECRET)

    mock(monkeypatch, handler)
    session = p.ProbeSession(tmp_path / "session")
    result = asyncio.run(session.probe(binding(), request(), key=SECRET))
    assert result["upstream_settlement"] == "unknown"
    assert session.halted and len(calls) == 1
    assert session.requests == 1 and session.tokens == 1024
    with pytest.raises(ValueError):
        asyncio.run(session.probe(binding(), request(), key=SECRET))
    assert SECRET not in "".join(x.read_text() for x in session.root.iterdir())


def test_session_budget_is_shared_across_models(monkeypatch, tmp_path):
    async def fake(binding, body, **kwargs):
        assert 0 < kwargs["timeout_s"] <= 90
        return {
            "status": "completed_probe",
            "finish_reason": "stop",
            "reported_output_within_requested_cap": True,
        }, response()

    monkeypatch.setattr(p, "complete", fake)
    session = p.ProbeSession(tmp_path / "session")
    for i in range(8):
        asyncio.run(session.probe(binding(label=f"model-{i}"), request(), key=SECRET))
    assert session.tokens == 8192 and session.requests == 8
    with pytest.raises(ValueError):
        asyncio.run(session.probe(binding(), request(), key=SECRET))
    with pytest.raises(FileExistsError):
        p.ProbeSession(session.root)
    if os.name == "posix":
        assert all(x.stat().st_mode & 0o077 == 0 for x in session.root.iterdir())


def test_session_mixed_token_caps_and_wall_budget(monkeypatch, tmp_path):
    async def fake(*args, **kwargs):
        return {
            "status": "completed_probe",
            "finish_reason": "stop",
            "reported_output_within_requested_cap": True,
        }, response()

    monkeypatch.setattr(p, "complete", fake)
    session = p.ProbeSession(tmp_path / "mixed")
    for cap in (2048, 1024, 2048, 2048, 1024):
        asyncio.run(session.probe(binding(), request(cap), key=SECRET))
    assert session.tokens == 8192 and session.requests == 5
    with pytest.raises(ValueError):
        asyncio.run(session.probe(binding(), request(1), key=SECRET))
    expired = p.ProbeSession(tmp_path / "expired")
    expired.started -= 601
    with pytest.raises(ValueError):
        asyncio.run(expired.probe(binding(), request(), key=SECRET))
    assert expired.requests == 0


def test_truncation_halts_remaining_probes(monkeypatch, tmp_path):
    mock(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                choices=[{"message": {"content": "cut off"}, "finish_reason": "length"}]
            ),
        ),
    )
    session = p.ProbeSession(tmp_path / "session")
    result = asyncio.run(session.probe(binding(), request(), key=SECRET))
    assert result["finish_reason"] == "length" and session.halted
    assert (session.root / "00-response-private.json").exists()


def test_native_stop_with_excess_reported_usage_halts(monkeypatch, tmp_path):
    mock(
        monkeypatch,
        lambda req: httpx.Response(
            200, json=response(usage={"prompt_tokens": 20, "completion_tokens": 1899})
        ),
    )
    session = p.ProbeSession(tmp_path / "excess")
    result = asyncio.run(session.probe(binding(), request(1024), key=SECRET))
    assert result["finish_reason"] == "stop"
    assert not result["reported_output_within_requested_cap"]
    assert session.halted


def test_disabled_reasoning_with_native_reasoning_text_halts(monkeypatch, tmp_path):
    mock(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                choices=[
                    {
                        "message": {
                            "content": "answer",
                            "reasoning_content": "private provider reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ]
            ),
        ),
    )
    session = p.ProbeSession(tmp_path / "reasoning")
    body = request() | {"chat_template_kwargs": {"enable_thinking": False}}
    result = asyncio.run(session.probe(binding(), body, key=SECRET))
    assert result["disabled_reasoning_contradicted"] and session.halted
    assert "private provider reasoning" not in json.dumps(result)


def test_multimodal_role_has_separate_request_budget(monkeypatch):
    body = request()
    body["messages"][0]["content"] = "x" * (1024**2 + 1)
    mock(monkeypatch, lambda req: httpx.Response(200, json=response()))
    with pytest.raises(ValueError):
        asyncio.run(p.complete(binding(), body, key=SECRET, timeout_s=3))
    b = p.Binding.model_validate(binding().model_dump() | {"role": "vlm"})
    result, _ = asyncio.run(p.complete(b, body, key=SECRET, timeout_s=3))
    assert result["status"] == "completed_probe"


@pytest.mark.parametrize("status", [200, 401, 429, 302])
def test_real_relay_uses_server_owned_auth_and_redacts_errors(monkeypatch, status):
    from uuid import uuid4

    from test_workflow_qualification import running

    from benchmarks.workflow.relay import Relay

    witnessed = []

    def handler(req):
        witnessed.append(req.headers["Authorization"])
        return httpx.Response(
            status, json=response() if status == 200 else {"error": SECRET}
        )

    mock(monkeypatch, handler)
    with Relay(
        binding().base_url,
        binding().model,
        str(uuid4()),
        provider_binding=binding(),
        provider_key=SECRET,
        deadline_s=3,
    ) as relay:
        with running(relay) as base:
            with httpx.Client(trust_env=False, timeout=3) as client:
                result = client.post(
                    base + "/" + relay.token + "/v1/chat/completions",
                    json=request(),
                    headers={"Authorization": "Bearer UNTRUSTED_CLIENT_KEY"},
                )
            assert witnessed == ["Bearer " + SECRET]
            assert result.status_code == (200 if status == 200 else 502)
            assert SECRET not in result.text
            assert SECRET not in json.dumps(relay.snapshot())
            if status != 200:
                assert relay.records[0]["status"] == "failed"
                assert relay.records[0]["provider_witness"]["http_status"] == status
                assert relay.sealed
                again = httpx.post(
                    base + "/" + relay.token + "/v1/chat/completions",
                    json=request(),
                    trust_env=False,
                )
                assert again.status_code == 409 and len(witnessed) == 1


def test_remote_relay_enforces_operation_token_budget(monkeypatch):
    from uuid import uuid4

    from test_workflow_qualification import running

    from benchmarks.workflow.relay import Relay

    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=response())

    mock(monkeypatch, handler)
    with Relay(
        binding().base_url,
        binding().model,
        str(uuid4()),
        provider_binding=binding(),
        provider_key=SECRET,
        deadline_s=3,
    ) as relay:
        with running(relay) as base:
            with httpx.Client(trust_env=False) as client:
                for _ in range(4):
                    assert (
                        client.post(
                            base + "/" + relay.token + "/v1/chat/completions",
                            json=request(2048),
                        ).status_code
                        == 200
                    )
                assert (
                    client.post(
                        base + "/" + relay.token + "/v1/chat/completions",
                        json=request(2048),
                    ).status_code
                    == 502
                )
            assert len(calls) == 4 and relay.sealed and relay.provider_tokens == 8192


def test_remote_relay_disallows_universal_no_think_override():
    from uuid import uuid4

    from benchmarks.workflow.relay import Relay

    with pytest.raises(ValueError):
        Relay(
            binding().base_url,
            binding().model,
            str(uuid4()),
            provider_binding=binding(),
            provider_key=SECRET,
            generation_cap=2048,
        )


@pytest.mark.parametrize("mode", ["chat_template_disabled", "reasoning_effort_none"])
def test_model_specific_controls_preserve_inputs_and_match_both_clients(mode):
    settings = p.RequestedSettings(
        temperature=0, top_p=1, seed=1729, reasoning_control=mode
    )
    normal = request() | {"temperature": 0.2, "top_p": 0.7}
    library = request()
    del library["temperature"]
    controlled = [
        p.controlled_provider_request(body, 2048, settings)
        for body in (normal, library)
    ]
    assert controlled[0] == controlled[1]
    assert controlled[0]["messages"] == normal["messages"]
    assert controlled[0]["max_tokens"] == 2048
    assert normal["temperature"] == 0.2
    with pytest.raises(ValueError):
        p.controlled_provider_request(
            normal | {"reasoning_effort": "high"}, 2048, settings
        )


def test_real_relay_witnesses_declared_remote_controls(monkeypatch):
    from uuid import uuid4

    from test_workflow_qualification import running

    from benchmarks.workflow.relay import Relay

    witnessed = []

    def handler(req):
        witnessed.append(json.loads(req.content))
        return httpx.Response(200, json=response())

    mock(monkeypatch, handler)
    configured = binding(
        request_settings=p.RequestedSettings(
            temperature=0,
            top_p=1,
            seed=1729,
            reasoning_control="chat_template_disabled",
        )
    )
    with Relay(
        configured.base_url,
        configured.model,
        str(uuid4()),
        provider_binding=configured,
        provider_key=SECRET,
        provider_generation_cap=2048,
        deadline_s=3,
    ) as relay:
        with running(relay) as base:
            assert (
                httpx.post(
                    base + "/" + relay.token + "/v1/chat/completions",
                    json=request(),
                    trust_env=False,
                ).status_code
                == 200
            )
            sent = witnessed[0]
            assert sent["max_tokens"] == 2048 and sent["temperature"] == 0
            assert sent["chat_template_kwargs"] == {"enable_thinking": False}
            assert sent["messages"] == request()["messages"]
            assert (
                relay.records[0]["generation_policy"] == "declared_provider_controls.v1"
            )
            assert (
                relay.snapshot()["generation_policy"] == "declared_provider_controls.v1"
            )
            assert not relay.records[0]["provider_witness"][
                "effective_settings_verified"
            ]

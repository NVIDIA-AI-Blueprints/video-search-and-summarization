# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""The capability probes: the dry run, every refusal, and a live session against a fake transport (never a model)."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from evalv2 import probe
from evalv2.adapters.openai_compat import ENDPOINTS, OpenAICompatAdapter
from evalv2.adapters.registry import capabilities_for
from evalv2.core.hashing import sha256_json
from evalv2.core.netguard import no_network
from evalv2.tasks.judges import build_request, load_judges

NANO, SUPER, OMNI = probe.VIDEO_MODELS
ENV = {"VLM_GATEWAY_BASE_URL": "https://switchyard.example/v1", "VLM_GATEWAY_API_KEY": "gateway-key-never-stored",
       "NVIDIA_API_KEY": "catalog-key-never-stored"}
EVAL_DIR = Path(__file__).resolve().parents[2]
VALID = {"probe_frame_count": 16, "probe_scene": "A road at dusk.", "probe_motion": "some"}


def _never(call):
    raise AssertionError(f"no adapter may be built: {call.id}")


@pytest.fixture
def clips(tmp_path, monkeypatch):
    """Two synthetic dev clips standing in for the manifest's (the real ones are private data)."""
    data_root = tmp_path / "data"
    index = {}
    for clip, size, duration in ((probe.SHORT_CLIP, 1504, 8.1), (probe.LONG_CLIP, 4512, 25.4)):
        content = hashlib.sha256(clip.encode()).digest() * (size // 32)
        path = data_root / "videos" / "x" / f"{clip}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        index[clip] = {"incident_id": clip, "local_path": f"eval_data/videos/x/{clip}.mp4", "size_bytes": len(content),
                       "sha256": hashlib.sha256(content).hexdigest(), "duration_seconds": duration}
    monkeypatch.setattr(probe, "_clip_index", lambda: (index, set(index)))
    return data_root, index


class _ProbeServer:
    """Switchyard and the catalog as httpx.MockTransport: replies chosen per model, clip and frame count."""

    def __init__(self, clip_index, data_root, *, judge_status=200):
        self.by_bytes = {(data_root / "videos" / "x" / f"{c}.mp4").read_bytes(): c for c in clip_index}
        self.judge_status, self.requests = judge_status, []

    def _reply(self, content, prompt_tokens):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 300,
                                                   "completion_tokens_details": {"reasoning_tokens": 200}}})

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((str(request.url), request.headers.get("authorization"), body))
        if "response_format" not in body:  # a judge
            if self.judge_status != 200:
                return httpx.Response(self.judge_status, text="forbidden")
            if body["model"] == "openai/gpt-oss-20b":
                return self._reply('{"score": 0.9, "reasoning": "The same event."}', 180)
            return self._reply('```json\n{"score": 0.85, "reasoning": "Same."}\n```', 190)
        url = body["messages"][0]["content"][0]["video_url"]["url"]
        clip = self.by_bytes[base64.b64decode(url.split(",", 1)[1])]
        if body["model"] == OMNI:
            return httpx.Response(400, text=f"inline video refused: {url[:80]}")
        if body["model"] == NANO:  # num_frames ignored, a fixed frame rate: tokens grow with the clip
            return self._reply(json.dumps(VALID), 3000 if clip == probe.LONG_CLIP else 1000)
        return self._reply("<think>Looking.</think>" + json.dumps(VALID), 1080)  # fixed count, reasoning inline


def _factory(server):
    def make(call):
        spec = load_judges().get(call.judge_id) if call.judge_id else None
        model, endpoint = (spec.model, spec.endpoint) if spec else (call.model, call.endpoint)
        return OpenAICompatAdapter(f"probe-{model}", model, ENDPOINTS[endpoint], capabilities_for(model),
                                   transport=httpx.MockTransport(server), environ=ENV)
    return make


def _approval(tmp_path, **overrides):
    marker = {"approval": probe.APPROVAL_VERSION, "approved_by": "owner (test)", "approved_at": "2026-11-01",
              "max_calls": 12, "max_tokens": 300_000, **overrides}
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(marker))
    return path


def _live_args(tmp_path, data_root, *extra):
    return ["--live", "--max-calls", "11", "--max-tokens", "300000", "--approval", str(_approval(tmp_path)),
            "--data-root", str(data_root), "--out", str(tmp_path / "out"), *extra]


# ---------------------------------------------------------------------------------------------------------------
# The plan and the dry run
# ---------------------------------------------------------------------------------------------------------------


def test_the_default_plan_fits_the_approved_caps():
    plan = probe.build_plan()
    assert [c.id for c in plan] == [f"{kind}:{model}" for model in probe.VIDEO_MODELS
                                    for kind in ("schema_inline", "frames_many", "long_clip")] + [
        f"judge:{j}" for j in probe.JUDGE_IDS]
    video = [c for c in plan if c.kind != "judge"]
    assert {c.tokens_bound for c in video} == {probe.VIDEO_PROMPT_TOKENS_BOUND + probe.VIDEO_MAX_TOKENS}
    assert [(c.clip, c.num_frames) for c in video[:3]] == [(probe.SHORT_CLIP, 8), (probe.SHORT_CLIP, 64),
                                                          (probe.LONG_CLIP, 64)]
    assert all(c.requires == f"schema_inline:{c.model}" for c in video if c.kind != "schema_inline")
    worst = sum(c.tokens_bound for c in plan)
    assert len(plan) == 11 and worst == 294_202 and worst <= 300_000
    with_continuity = probe.build_plan(judges=[*probe.JUDGE_IDS, probe.CONTINUITY_JUDGE_ID])
    assert len(with_continuity) == 12 and sum(c.tokens_bound for c in with_continuity) <= 300_000
    single = probe.build_plan(kinds=["frames_many"], models=[NANO], judges=[])
    assert [c.id for c in single] == [f"frames_many:{NANO}"] and single[0].requires is None
    with pytest.raises(probe.ProbeRefused, match="not Track A video subjects"):
        probe.build_plan(models=["openai/gpt-oss-20b"])
    with pytest.raises(probe.ProbeRefused, match="unknown probe kinds"):
        probe.build_plan(kinds=["bbox"])


def test_the_dry_run_prints_each_request_as_stored_and_its_worst_case_without_the_network(capsys):
    with no_network():
        assert probe.main([], environ={}, adapter_factory=_never) == 0
    statement = json.loads(capsys.readouterr().out)
    assert statement["mode"].startswith("dry run") and statement["totals"]["calls"] == 11
    assert statement["totals"]["worst_case_tokens"] == 294_202
    assert statement["totals"]["by_ledger"]["inference"] == {"calls": 9, "worst_case_tokens": 289_728}
    assert statement["credentials"] == {"nvidia-api-catalog": ["NVIDIA_API_KEY"],
                                        "switchyard": ["VLM_GATEWAY_API_KEY", "VLM_GATEWAY_BASE_URL"]}
    manifest = {c["incident_id"]: c for c in json.loads(
        (EVAL_DIR / "eval_reproducibility" / "dataset-manifest-v1.json").read_text())["clips"]}
    first = statement["calls"][0]
    short = manifest[probe.SHORT_CLIP]
    assert first["request"]["messages"][0]["content"][0]["video_url"]["url"] == (
        f"data:video/mp4;base64,[omitted: {short['size_bytes']} bytes, sha256 {short['sha256']}]")
    assert first["request"]["response_format"]["json_schema"]["strict"] is True
    assert first["request"]["temperature"] == 0.0 and first["request"]["max_tokens"] == probe.VIDEO_MAX_TOKENS
    assert [c["request"]["media_io_kwargs"]["video"]["num_frames"] for c in statement["calls"][:3]] == [8, 64, 64]
    assert first["request_sha256"] == sha256_json(first["request"])
    judge = statement["calls"][-2]
    spec = load_judges().get(probe.JUDGE_IDS[0])
    assert judge["request"] == build_request(spec, "incident", *probe.JUDGE_PROBE_TEXTS)
    assert statement["clips"][probe.LONG_CLIP]["split"] == "dev"


def test_the_prompt_never_names_the_schema_keys_and_the_schema_uses_only_p1s_keywords():
    for key in probe.PROBE_SCHEMA["properties"]:
        assert key not in probe.PROBE_PROMPT and "json" not in probe.PROBE_PROMPT.lower()
    contract = json.loads((EVAL_DIR.parent / "contracts" / "incident_report.schema.json").read_text())
    used: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                used.add(key)
                if key == "properties":
                    for child in value.values():
                        walk(child)
                elif key not in ("enum", "required"):
                    walk(value)
    walk(contract)
    probe_keys: set[str] = set()
    for name, prop in probe.PROBE_SCHEMA["properties"].items():
        probe_keys |= set(prop)
        assert name
    assert (probe_keys | set(probe.PROBE_SCHEMA)) <= used


def test_a_test_half_clip_is_never_probed():
    split = json.loads((EVAL_DIR / "eval_reproducibility" / "split-v2.json").read_text())
    with pytest.raises(probe.ProbeRefused, match="not a dev clip"):
        probe.clip_meta(split["test"][0])
    assert probe.SHORT_CLIP in split["dev"] and probe.LONG_CLIP in split["dev"]
    assert probe.clip_meta(probe.SHORT_CLIP)["size_bytes"] <= probe.MAX_INLINE_BYTES


# ---------------------------------------------------------------------------------------------------------------
# Refusals: nothing is built, sent or written
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("args", "environ", "message"), [
    (["--live", "--max-calls", "11", "--max-tokens", "300000"], ENV, "no approval"),
    (["--live"], {**ENV, probe.APPROVAL_ENV: "1"}, "explicit --max-calls and --max-tokens"),
    (["--live", "--max-calls", "13", "--max-tokens", "300000"], {**ENV, probe.APPROVAL_ENV: "1"},
     "--max-calls must be from 1 to 12"),
    (["--live", "--max-calls", "11", "--max-tokens", "100000"], {**ENV, probe.APPROVAL_ENV: "1"},
     "worst case is 294202 tokens"),
    (["--live", "--max-calls", "10", "--max-tokens", "300000"], {**ENV, probe.APPROVAL_ENV: "1"},
     "the plan has 11 calls"),
    (["--live", "--max-calls", "11", "--max-tokens", "300000"], {probe.APPROVAL_ENV: "1"}, "missing credentials"),
    (["--live", "--max-calls", "11", "--max-tokens", "300000"], {**ENV, probe.APPROVAL_ENV: "yes"}, "no approval"),
])
def test_a_live_run_is_refused_before_anything_is_sent(tmp_path, capsys, args, environ, message):
    code = probe.main([*args, "--out", str(tmp_path / "out")], environ=environ, adapter_factory=_never)
    err = capsys.readouterr().err
    assert code == 2 and message in json.loads(err)["refused"]
    assert not (tmp_path / "out").exists()
    assert "never-stored" not in err, "credential values are never printed"


@pytest.mark.parametrize(("overrides", "message"), [
    ({"max_calls": 5}, "exceed the approved 5 calls"),
    ({"max_tokens": 200_000}, "exceed the approved"),
    ({"approval": "something-else"}, "is not an evalv2-probe-approval-v1 marker"),
    ({"approved_by": ""}, "approved_by and approved_at"),
    ({"max_calls": 20}, "from 1 to 12"),
    ({"kinds": ["schema_inline", "judge"]}, "does not cover kinds ['frames_many', 'long_clip']"),
    ({"judges": [probe.JUDGE_IDS[0]]}, "does not cover judges"),
])
def test_an_approval_marker_must_cover_the_session(tmp_path, capsys, overrides, message):
    marker = str(_approval(tmp_path, **overrides))
    args = ["--live", "--max-calls", "11", "--max-tokens", "300000", "--approval", marker,
            "--out", str(tmp_path / "out")]
    assert probe.main(args, environ=ENV, adapter_factory=_never) == 2
    assert message in json.loads(capsys.readouterr().err)["refused"] and not (tmp_path / "out").exists()


def test_a_clip_that_differs_from_the_manifest_is_refused_before_any_call(tmp_path, capsys, clips):
    data_root, _ = clips
    (data_root / "videos" / "x" / f"{probe.LONG_CLIP}.mp4").write_bytes(b"tampered")
    code = probe.main(_live_args(tmp_path, data_root), environ=ENV, adapter_factory=_never)
    assert code == 2 and "differs from the manifest" in json.loads(capsys.readouterr().err)["refused"]
    assert not (tmp_path / "out" / "probes.jsonl").exists()


# ---------------------------------------------------------------------------------------------------------------
# A live session against a fake transport
# ---------------------------------------------------------------------------------------------------------------


def test_a_live_session_records_every_probe_and_its_findings(tmp_path, capsys, clips):
    data_root, index = clips
    server = _ProbeServer(index, data_root)
    assert probe.main(_live_args(tmp_path, data_root), environ=ENV, adapter_factory=_factory(server)) == 0
    summary = json.loads(capsys.readouterr().out)
    nano, sup, omni = (summary["models"][m] for m in (NANO, SUPER, OMNI))
    assert nano["inline_video_accepted"] and nano["strict_json_schema"] is True
    assert nano["frame_sampling"]["num_frames"] == "ignored"
    assert nano["frame_sampling"]["clip_length"] == "depends on clip length"
    assert sup["strict_json_schema"] is True and sup["frame_sampling"]["clip_length"] == "independent of clip length"
    assert omni["inline_video_accepted"] is False and omni["strict_json_schema"] is None
    assert nano["frames_self_report"]["schema_inline"] == 16
    gpt, mistral = (summary["judges"][j] for j in probe.JUDGE_IDS)
    assert gpt == {"answered": True, "expected_json": True, "score": 0.9, "reasoning_tokens": 200,
                   "finish_reason": "stop"}
    assert mistral["expected_json"] is False and mistral["score"] == 0.85, "fenced: scored, but not the exact JSON"
    assert summary["calls"] == {"answered": 8, "failed": 1, "skipped": 2} and summary["sent"] == 9

    out = tmp_path / "out"
    lines = [json.loads(line) for line in (out / "probes.jsonl").read_text().splitlines()]
    assert [line["kind"] for line in lines] == ["session"] + ["probe"] * 11 + ["summary"]
    assert lines[0]["approval"]["source"] == "marker" and lines[0]["limits"] == {"max_calls": 11,
                                                                               "max_tokens": 300000}
    skipped = [line for line in lines if line.get("status") == "skipped"]
    assert {line["probe"] for line in skipped} == {f"frames_many:{OMNI}", f"long_clip:{OMNI}"}
    omni_line = next(line for line in lines if line.get("probe") == f"schema_inline:{OMNI}")
    assert omni_line["findings"]["http_status"] == 400
    assert omni_line["failure"]["detail"] == "inline video refused: data:video/mp4;base64,[omitted]", "no echoed bytes"

    events = [json.loads(line) for path in (out / "budget").glob("*.jsonl") for line in path.read_text().splitlines()]
    reserves = [e for e in events if e["event"] == "reserve"]
    assert len(reserves) == 9 and all(e["tokens_bound"] in (32_192, 2_237) for e in reserves)
    order = [e["event"] for e in events]
    assert all(order[i] == "reserve" for i in range(0, len(order), 2)), "every attempt is reserved before it settles"

    sent = [body for _, _, body in server.requests if "response_format" in body]
    data = base64.b64decode(sent[0]["messages"][0]["content"][0]["video_url"]["url"].split(",", 1)[1])
    assert data == (data_root / "videos" / "x" / f"{probe.SHORT_CLIP}.mp4").read_bytes(), "the real clip is sent"
    stored = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert base64.b64encode(data)[:64] not in stored and b"[omitted: 1504 bytes, sha256" in stored
    assert b"never-stored" not in stored
    assert {url for url, _, _ in server.requests} == {"https://switchyard.example/v1/chat/completions",
                                                      "https://integrate.api.nvidia.com/v1/chat/completions"}


def test_the_dry_run_names_the_hash_of_what_the_live_session_stores(tmp_path, capsys, clips):
    data_root, index = clips
    probe.main([], environ={})
    planned = {c["id"]: c["request_sha256"] for c in json.loads(capsys.readouterr().out)["calls"]}
    server = _ProbeServer(index, data_root)
    probe.main(_live_args(tmp_path, data_root), environ=ENV, adapter_factory=_factory(server))
    capsys.readouterr()
    lines = [json.loads(line) for line in (tmp_path / "out" / "probes.jsonl").read_text().splitlines()]
    recorded = {line["probe"]: line["request_sha256"] for line in lines if line.get("status") in ("answered",
                                                                                                    "failed")}
    assert recorded and all(planned[probe_id] == digest for probe_id, digest in recorded.items())


def test_sessions_append_and_never_rewrite(tmp_path, capsys, clips):
    data_root, index = clips
    args = ["--only", "judge", "--live", "--max-calls", "2", "--max-tokens", "10000",
            "--approval", str(_approval(tmp_path)), "--data-root", str(data_root), "--out", str(tmp_path / "out")]
    assert probe.main(args, environ=ENV, adapter_factory=_factory(_ProbeServer(index, data_root))) == 0
    first = (tmp_path / "out" / "probes.jsonl").read_bytes()
    assert probe.main(args, environ=ENV, adapter_factory=_factory(_ProbeServer(index, data_root))) == 0
    capsys.readouterr()
    second = (tmp_path / "out" / "probes.jsonl").read_bytes()
    assert second.startswith(first) and second.count(b'"kind":"session"') == 2


def test_an_endpoint_that_refuses_the_key_skips_its_later_probes(tmp_path, capsys, clips):
    data_root, index = clips
    server = _ProbeServer(index, data_root, judge_status=403)
    args = ["--only", "judge", "--live", "--max-calls", "2", "--max-tokens", "10000",
            "--approval", str(_approval(tmp_path)), "--data-root", str(data_root), "--out", str(tmp_path / "out")]
    assert probe.main(args, environ=ENV, adapter_factory=_factory(server)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["calls"] == {"answered": 0, "failed": 1, "skipped": 1} and len(server.requests) == 1
    lines = [json.loads(line) for line in (tmp_path / "out" / "probes.jsonl").read_text().splitlines()]
    assert lines[2]["reason"] == "the endpoint refused the credentials"


# ---------------------------------------------------------------------------------------------------------------
# Reading replies
# ---------------------------------------------------------------------------------------------------------------


def test_replies_are_read_by_form_and_checked_against_the_schema():
    assert probe.parse_reply(json.dumps(VALID)) == (VALID, "json")
    assert probe.parse_reply("<think>x</think>\n" + json.dumps(VALID)) == (VALID, "json_after_reasoning")
    assert probe.parse_reply("```json\n" + json.dumps(VALID) + "\n```") == (VALID, "fenced")
    assert probe.parse_reply("There are about 16 frames.") == (None, "prose")
    assert probe.schema_errors(VALID) == []
    assert probe.schema_errors({**VALID, "extra": 1}) and probe.schema_errors({**VALID, "probe_motion": "lots"})
    assert probe.schema_errors({k: v for k, v in VALID.items() if k != "probe_scene"})


@pytest.mark.parametrize(("tokens", "frames", "length", "per_frame"), [
    ((1000, 1000, 1000), "ignored", "independent of clip length", None),
    ((500, 3000, 3000), "honoured", "independent of clip length", 44.6),
    ((1000, 1000, 3000), "ignored", "depends on clip length", None),
    ((1000, 1050, 1050), "unclear", "independent of clip length", None),
    ((None, 1000, 1000), "unknown", "independent of clip length", None),
])
def test_frame_sampling_is_read_from_the_prompt_tokens(tokens, frames, length, per_frame):
    reading = probe.frame_sampling(*tokens)
    assert (reading["num_frames"], reading["clip_length"], reading["tokens_per_frame"]) == (frames, length, per_frame)


def test_the_probe_store_compacts_inline_clips_and_keeps_other_urls(tmp_path):
    clip = b"\x00\x01video-bytes" * 50
    body = {"model": NANO, "messages": [{"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": probe.data_url(clip)}},
        {"type": "image_url", "image_url": {"url": "https://cdn.example/frame.jpg"}}]}]}
    store = probe.CompactingStore(tmp_path / "store")
    stored = store.get(store.put("request", body))
    assert stored["messages"][0]["content"][0]["video_url"]["url"] == (
        f"data:video/mp4;base64,[omitted: {len(clip)} bytes, sha256 {hashlib.sha256(clip).hexdigest()}]")
    assert stored["messages"][0]["content"][1] == body["messages"][0]["content"][1]
    assert body["messages"][0]["content"][0]["video_url"]["url"] == probe.data_url(clip), "the request is unchanged"
    assert probe.compact_data_url("data:video/mp4;base64,@@@").endswith("that are not valid base64]")
    assert store.get(store.put("response", {"x": 1})) == {"x": 1}


def test_external_candidates_are_probed_on_the_catalog_only_when_named():
    from evalv2 import probe as pr

    default = pr.build_plan()
    assert all(c.endpoint == "switchyard" for c in default if c.kind != "judge"), "the default plan is unchanged"
    assert not any(c.kind == "over_60s" for c in default)
    plan = pr.build_plan(kinds=["schema_inline", "frames_many", "long_clip", "over_60s"],
                         models=["google/gemma-4-31b-it"])
    assert [c.kind for c in plan] == ["schema_inline", "frames_many", "long_clip", "over_60s"]
    assert {c.endpoint for c in plan} == {"nvidia-api-catalog"} and plan[-1].clip == pr.OVER_60S_CLIP
    assert all(c.requires == "schema_inline:google/gemma-4-31b-it" for c in plan[1:])
    with pytest.raises(pr.ProbeRefused, match="external candidates"):
        pr.build_plan(models=["qwen/qwen3-vl-8b-instruct"])


def test_kimis_two_probes_fit_the_approved_fifty_thousand_tokens():
    from evalv2 import probe as pr

    plan = pr.build_plan(kinds=["schema_inline", "p1_short"], models=["moonshotai/kimi-k2.6"])
    assert [c.kind for c in plan] == ["schema_inline", "p1_short"] and {c.endpoint for c in plan} == {"nvidia-api-catalog"}
    assert sum(c.tokens_bound for c in plan) <= 50_000
    p1 = pr.request_for(plan[1], {pr.SHORT_CLIP: "data:video/mp4;base64,AA"})
    from evalv2.requests.p1 import build_p1_request
    assert p1 == build_p1_request("moonshotai/kimi-k2.6", "data:video/mp4;base64,AA"), "the unchanged P1 request"
    assert pr.request_for(plan[0], {pr.SHORT_CLIP: "u"})["max_tokens"] == 2048


def test_gemmas_four_probes_fit_the_approved_budget():
    from evalv2 import probe as pr

    plan = pr.build_plan(kinds=["schema_inline", "frames_many", "long_clip", "over_60s"], models=["google/gemma-4-31b-it"])
    assert len(plan) == 4 and sum(c.tokens_bound for c in plan) <= 130_000


def test_openrouter_candidates_carry_pinned_no_retention_routing():
    from evalv2 import probe as pr

    plan = pr.build_plan(kinds=["schema_inline"], models=["google/gemma-4-31b-it", "qwen/qwen3.5-9b"],
                         endpoint="openrouter")
    assert {c.endpoint for c in plan} == {"openrouter"}
    for call in plan:
        adapter = pr.live_adapter(call)
        provider = adapter.extra_body["provider"]
        assert provider["allow_fallbacks"] is False and provider["data_collection"] == "deny"
        assert provider["require_parameters"] is True and provider["order"]
    with pytest.raises(pr.ProbeRefused, match="--endpoint"):
        pr.build_plan(kinds=["schema_inline"], models=["qwen/qwen3.5-9b"], endpoint="switchyard")

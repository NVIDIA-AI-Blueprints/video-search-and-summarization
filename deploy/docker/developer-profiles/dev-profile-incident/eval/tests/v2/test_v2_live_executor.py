# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The live executor end to end, with a fake upstream (Track A) and a fake agent (Track B): no network, no model.

Covers the approval marker, caps refusing before sending, the test-split guard, pacing on a fake clock, retries, every
attempt recorded, the run manifest, resume (byte-identical results, nothing written to a sealed run), and B3.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import httpx
import pytest

from evalv2 import approval as approvals
from evalv2 import cli
from evalv2.clips import ClipError, ClipResolver
from evalv2.core.artifacts import ArtifactRef
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.config import RunConfig
from evalv2.core.pacing import Pacer
from evalv2.executor import LiveExecutor, LiveRefused, RunStopped, schedule
from evalv2.requests import vss as requests_vss

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_incident_objects import _report  # noqa: E402
from test_v2_track_b import FakeAgent, _answer, _provenance  # noqa: E402

NANO, SUPER = "nvidia/cosmos-3-nano-reasoner", "nvidia/cosmos-3-super-reasoner"
DEV, TEST = ["Assault001", "Burglary002"], ["Fighting003"]
SPLIT = {"version": "split-v2", "dev": DEV, "test": TEST}
CLIPS = [{"incident_id": c, "r2_key": f"anomaly/{c}_x264.mp4", "duration_seconds": 30.0} for c in DEV + TEST]
ENV = {"VLM_GATEWAY_BASE_URL": "https://gateway.example/v1", "VLM_GATEWAY_API_KEY": "secret-key",
       "R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY": "ak", "R2_SECRET_KEY": "sk", "R2_BUCKET": "bucket",
       "EVALV2_VSS_AGENT_URL": "http://127.0.0.1:8765"}
USAGE = {"prompt_tokens": 9000, "completion_tokens": 1000, "total_tokens": 10000}


def _track_a(**changes):
    raw = {"experiment": "v2-test-track-a", "split": {"version": "split-v2", "half": "dev"},
           "subjects": [{"id": "nano-direct", "adapter": "openai_compat", "endpoint": "switchyard", "model": NANO},
                        {"id": "super-direct", "adapter": "openai_compat", "endpoint": "switchyard", "model": SUPER}],
           "tasks": {"incident_objects": {"options": {"judges": {"primary": "catalog-gpt-oss-20b-v1"}}}},
           "repeats": 2, "seed": 20261101, "retry_policy": "retry-v2", "purpose": "pilot", "budget_mode": "hard_stop",
           "caps": [{"ledger": "inference", "max_attempts": 30, "max_tokens": 600000},
                    {"ledger": "judge", "max_attempts": 100, "max_tokens": 200000}]}
    raw.update(changes)
    return RunConfig.model_validate(raw)


def _track_b(profile="production-v4", **changes):
    raw = {"experiment": "v2-test-track-b", "split": {"version": "split-v2", "half": "dev"},
           "subjects": [{"id": "super-pipeline", "adapter": "vss", "endpoint": "experiment-agent", "model": SUPER,
                         "profile": profile}],
           "tasks": {"pipeline": {}}, "repeats": 1, "seed": 20261101, "purpose": "pilot", "budget_mode": "hard_stop",
           "caps": [{"ledger": "inference", "max_attempts": 10, "max_tokens": 200000},
                    {"ledger": "grounding", "max_attempts": 200, "max_tokens": 800000},
                    {"ledger": "zoom", "max_attempts": 400, "max_tokens": 1000000},
                    {"ledger": "caption", "max_attempts": 100, "max_tokens": 1000000}]}
    raw.update(changes)
    return RunConfig.model_validate(raw)


def _datasets(clips=DEV):
    return {"split": SPLIT, "half": "dev", "clips": list(clips), "inputs": {"split-v2/split-v2.json": "a" * 64}}


def _marker(tmp_path, config, items, **changes):
    marker = approvals.draft(config, items)
    marker.update(approved_by="Owner (test)", approved_at="2026-11-01", note="test", **changes)
    path = tmp_path / f"approval-{config.experiment}.json"
    path.write_text(json.dumps(marker))
    return approvals.read_approval(path)


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Upstream:
    """A fake OpenAI-compatible gateway: scripted replies, every request counted (and its URL kept)."""

    def __init__(self, replies=None, clock=None, crash_at=None):
        self.requests, self.replies, self.clock, self.crash_at = [], list(replies or []), clock, crash_at

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.crash_at is not None and len(self.requests) == self.crash_at:
            raise KeyboardInterrupt("the harness host went away")
        if self.clock is not None:
            self.clock.now += 1.0
        if self.replies:
            status, payload = self.replies.pop(0)
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(_report())},
                                                      "finish_reason": "stop"}], "usage": USAGE})


def _signer():
    counter = {"n": 0}

    def sign(key, ttl):
        counter["n"] += 1
        return f"https://acct.r2.example/bucket/{key}?X-Amz-Signature=sig{counter['n']}&X-Amz-Expires={ttl}"

    return sign


def _executor(tmp_path, config, *, out=None, upstream=None, clock=None, marker=None, datasets=None, pacer=None,
              adapter_kwargs=None, environ=ENV):
    datasets = datasets or _datasets()
    items = cli.plan(config, lambda cfg: datasets)
    clock = clock or Clock()
    marker = marker if marker is not None else _marker(tmp_path, config, items)
    _, hard_stop = cli.preflight_problems(config, items)
    if adapter_kwargs is None:
        adapter_kwargs = {"transport": httpx.MockTransport(upstream or Upstream())}
    return LiveExecutor(config, datasets, items, out=out or tmp_path / "run", marker=marker, hard_stop=hard_stop,
                        manifest_clips=CLIPS, signer=_signer(), adapter_kwargs=adapter_kwargs, environ=environ,
                        pacer=pacer or Pacer(rpm=60, parallel=1, tpm={}, clock=clock, sleep=clock.sleep),
                        code_dir=tmp_path, sleep=clock.sleep, clock=clock)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- Track A end to end -----------------------------------------------------------------------------------------


def test_track_a_runs_records_every_attempt_and_seals(tmp_path):
    upstream = Upstream()
    executor = _executor(tmp_path, _track_a(), upstream=upstream)
    summary = executor.run()
    assert summary["sealed"] and summary["items"] == 8 and summary["outcomes"] == {"scored": 8}
    assert len(upstream.requests) == 8
    attempts = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()]
    assert len(attempts) == 8
    for row in attempts:
        assert row["outcome"] == "ok" and row["tokens"]["total"] == 10000 and row["latency_ms"] is not None
        assert len(row["prompt_sha256"]) == 64 and len(row["request_sha256"]) == 64
        stored = executor.store.get(ArtifactRef("request", row["request_sha256"]))
        assert "X-Amz-Signature" not in json.dumps(stored), "the signed URL is never stored"
    manifest = json.loads((tmp_path / "run" / "store" / "runs" / "v2-test-track-a" / "manifest.json").read_text())
    assert manifest["config_sha256"] == approvals.config_sha256(_track_a())
    assert manifest["approval"]["sha256"] == executor.marker["sha256"]
    assert manifest["inputs"] == _datasets()["inputs"] and manifest["track"] == "A"
    assert manifest["requests"]["p1"]["prompt_sha256"] == attempts[0]["prompt_sha256"]
    assert manifest["endpoints"]["nano-direct"]["api_key_env"] == "VLM_GATEWAY_API_KEY"
    assert "secret-key" not in json.dumps(manifest) and "sk" not in manifest["endpoints"]
    assert manifest["budget"]["caps"][0]["attempts"] == 8
    assert executor.store.run("v2-test-track-a").verify() == []


def test_the_schedule_is_seeded_and_interleaved():
    config = _track_a()
    items = cli.plan(config, lambda cfg: _datasets())["incident_objects"]
    order = schedule(items, ["nano-direct", "super-direct"], 2, config.seed)
    assert order == schedule(list(reversed(items)), ["nano-direct", "super-direct"], 2, config.seed)
    assert [i.repeat for i in order] == [1, 1, 1, 1, 2, 2, 2, 2]
    first = [order[0].subject, order[2].subject]
    assert first[0] != first[1], "the first subject rotates with the position"


def test_a_rate_limit_is_retried_after_backoff_and_both_attempts_are_recorded(tmp_path):
    clock = Clock()
    upstream = Upstream(replies=[(429, {"error": "slow down"})])
    executor = _executor(tmp_path, _track_a(repeats=1, subjects=[_track_a().subjects[0].model_dump()]),
                         upstream=upstream, clock=clock)
    assert executor.run()["outcomes"] == {"scored": 2}
    rows = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()]
    assert [r["outcome"] for r in rows] == ["rate_limited", "ok", "ok"] and 60.0 in clock.slept


# --- refusals before anything is sent ---------------------------------------------------------------------------


def test_no_marker_means_refused(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.json"
    path.write_text(_track_a().model_dump_json())
    monkeypatch.setenv(cli.LIVE_ENV, "1")
    code = cli.main(["run", "--config", str(path), "--live", "--out", str(tmp_path / "run")],
                    load_datasets=lambda cfg: _datasets())
    assert code == 2 and "needs an approval marker" in capsys.readouterr().out
    assert not (tmp_path / "run").exists()


def test_a_marker_for_another_config_or_wrong_caps_is_refused(tmp_path):
    config = _track_a()
    items = cli.plan(config, lambda cfg: _datasets())
    upstream = Upstream()
    other = _marker(tmp_path, config, items, config_sha256="0" * 64)
    with pytest.raises(LiveRefused, match="edited config needs a new approval"):
        _executor(tmp_path, config, upstream=upstream, marker=other).run()
    low = _marker(tmp_path, config, items, caps=[{"ledger": "inference", "max_attempts": 30, "max_tokens": 100}],
                  max_total_tokens=100)
    with pytest.raises(LiveRefused) as refused:
        _executor(tmp_path, config, upstream=upstream, marker=low).run()
    assert any("exceeds the approved 100" in p for p in refused.value.problems)
    assert any("sum to 600000, over the approved 100" in p for p in refused.value.problems)
    few = _marker(tmp_path, config, items, max_items=7)
    with pytest.raises(LiveRefused, match="8 work items, over the approved 7"):
        _executor(tmp_path, config, upstream=upstream, marker=few).run()
    assert upstream.requests == [], "nothing was sent"


def test_the_marker_file_is_validated(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"approval": approvals.APPROVAL_VERSION, "experiment": "x"}))
    with pytest.raises(approvals.ApprovalRefused, match="approved_by must be"):
        approvals.read_approval(bad)
    bad.write_text("{not json")
    with pytest.raises(approvals.ApprovalRefused, match="unreadable"):
        approvals.read_approval(bad)
    config = _track_a()
    marker = approvals.draft(config, cli.plan(config, lambda cfg: _datasets()))
    marker.update(approved_by="o", approved_at="d", enabled_profiles=["production-v4"])
    bad.write_text(json.dumps(marker))
    with pytest.raises(approvals.ApprovalRefused, match="not gated"):
        approvals.read_approval(bad)


def test_caps_refuse_before_sending(tmp_path):
    config = _track_a(caps=[{"ledger": "inference", "max_attempts": 3, "max_tokens": 600000},
                            {"ledger": "judge", "max_attempts": 1, "max_tokens": 1}])
    upstream = Upstream()
    summary = _executor(tmp_path, config, upstream=upstream).run()
    assert len(upstream.requests) == 3, "the fourth attempt was refused before it was sent"
    assert summary["outcomes"] == {"scored": 3} and summary["open"] == 5 and not summary["sealed"]
    refused = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()][3:]
    assert all(r["outcome"] == "budget_exceeded" and r["sent"] is False for r in refused)


def test_the_token_cap_refuses_at_the_reservation_bound(tmp_path):
    config = _track_a(caps=[{"ledger": "inference", "max_attempts": 30, "max_tokens": 80000},
                            {"ledger": "judge", "max_attempts": 1, "max_tokens": 1}])
    upstream = Upstream()
    summary = _executor(tmp_path, config, upstream=upstream).run()
    # 35,000 is reserved per attempt and settled to 10,000: 4 calls fit before 80,000 cannot take another bound.
    assert len(upstream.requests) == summary["outcomes"]["scored"] == 5


def test_a_test_clip_is_refused(tmp_path):
    upstream = Upstream()
    with pytest.raises(LiveRefused, match="never touches the test half"):
        _executor(tmp_path, _track_a(), upstream=upstream, datasets=_datasets(DEV + TEST)).run()
    resolver = ClipResolver(CLIPS, SPLIT, purpose="pilot", signer=_signer())
    with pytest.raises(ClipError, match="test-half clip"):
        resolver.url("Fighting003")
    assert upstream.requests == []
    with pytest.raises(ValueError, match="dev half only"):
        _track_a(split={"version": "split-v2", "half": "test"}, freeze_manifest="f" * 64)


def test_missing_credentials_are_named_never_printed(tmp_path):
    env = {k: v for k, v in ENV.items() if k not in ("VLM_GATEWAY_API_KEY", "R2_SECRET_KEY")}
    with pytest.raises(LiveRefused) as refused:
        _executor(tmp_path, _track_a(), environ=env).run()
    text = " ".join(refused.value.problems)
    assert "VLM_GATEWAY_API_KEY" in text and "R2_SECRET_KEY" in text and "secret" not in text.replace("SECRET", "")


def test_urls_are_resigned_before_they_expire():
    clock = Clock()
    resolver = ClipResolver(CLIPS, SPLIT, purpose="pilot", signer=_signer(), ttl_s=3600, refresh_margin_s=900,
                            clock=clock)
    first = resolver.url("Assault001")
    clock.now += 2000
    assert resolver.url("Assault001") == first and resolver.signed == 1
    clock.now += 800  # 800 s left: under the margin
    assert resolver.url("Assault001") != first and resolver.signed == 2
    with pytest.raises(ClipError, match="not in dataset-manifest-v1"):
        resolver.url("Nope001")


# --- resume -----------------------------------------------------------------------------------------------------


def test_resume_is_byte_identical(tmp_path):
    whole = _executor(tmp_path, _track_a(), out=tmp_path / "whole")
    whole.run()
    upstream = Upstream(crash_at=4)
    with pytest.raises(KeyboardInterrupt):
        _executor(tmp_path, _track_a(), out=tmp_path / "split", upstream=upstream).run()
    assert not (tmp_path / "split" / "store" / "runs" / "v2-test-track-a" / "SEALED").exists()
    resumed_upstream = Upstream()
    summary = _executor(tmp_path, _track_a(), out=tmp_path / "split", upstream=resumed_upstream).run()
    assert summary["sealed"] and summary["sent_items"] == 5 and len(resumed_upstream.requests) == 5
    for kind in ("incident-objects-prediction", "request", "response"):
        assert _tree(tmp_path / "whole" / "store" / "objects" / kind) == \
            _tree(tmp_path / "split" / "store" / "objects" / kind), kind
    finals = [{k: Checkpoint(d / "checkpoint.jsonl").final_result(k)["prediction_ref"]
               for k in Checkpoint(d / "checkpoint.jsonl").final_keys()} for d in (tmp_path / "whole",
                                                                                     tmp_path / "split")]
    assert finals[0] == finals[1]
    before = _tree(tmp_path / "split")
    again = Upstream()
    assert _executor(tmp_path, _track_a(), out=tmp_path / "split", upstream=again).run()["sealed"]
    assert again.requests == [] and _tree(tmp_path / "split") == before, "a sealed run is never written again"


def test_a_resume_with_a_changed_config_is_refused(tmp_path):
    config = _track_a(caps=[{"ledger": "inference", "max_attempts": 3, "max_tokens": 600000},
                            {"ledger": "judge", "max_attempts": 1, "max_tokens": 1}])
    _executor(tmp_path, config).run()
    changed = _track_a(caps=[{"ledger": "inference", "max_attempts": 4, "max_tokens": 600000},
                             {"ledger": "judge", "max_attempts": 1, "max_tokens": 1}])
    with pytest.raises(LiveRefused, match="config changed since this run started"):
        _executor(tmp_path, changed).run()


def test_open_items_resume_from_the_ledger_and_a_new_marker_is_recorded(tmp_path):
    config = _track_a(caps=[{"ledger": "inference", "max_attempts": 3, "max_tokens": 600000},
                            {"ledger": "judge", "max_attempts": 1, "max_tokens": 1}])
    _executor(tmp_path, config).run()
    upstream = Upstream()
    summary = _executor(tmp_path, config, upstream=upstream).run()
    assert upstream.requests == [] and summary["open"] == 5, "the ledger log keeps the cap reached"
    approvals_log = (tmp_path / "run" / "approvals.jsonl").read_text().splitlines()
    assert len(approvals_log) == 1, "the same marker is recorded once"


# --- pacing -----------------------------------------------------------------------------------------------------


def test_pacing_respects_rpm_and_the_models_token_window_on_a_fake_clock(tmp_path):
    clock = Clock()
    pacer = Pacer(rpm=3, parallel=1, tpm={NANO: 25000}, estimates={NANO: 10000}, clock=clock, sleep=clock.sleep)
    config = _track_a(subjects=[_track_a().subjects[0].model_dump()], repeats=2)
    _executor(tmp_path, config, upstream=Upstream(clock=clock), clock=clock, pacer=pacer).run()
    starts = [at for at, _ in pacer.starts]
    assert len(starts) == 4
    for i, start in enumerate(starts):
        window = [s for s in starts if start - 60 < s <= start]
        assert len(window) <= 3, "never more than rpm requests in a minute"
        assert sum(10000 for _ in window[:-1]) + 10000 <= 25000 or i == 0, "the token window holds"
    assert starts[2] - starts[0] >= 60 - 1e-9, "the third call waits for the first call's tokens to leave the window"
    assert pacer.waited_s > 0


def test_the_pacer_limits_calls_in_flight_and_gaps():
    clock = Clock()
    pacer = Pacer(rpm=None, parallel=2, tpm={}, min_gap_s=20, clock=clock, sleep=clock.sleep)
    first, second = pacer.acquire(NANO), pacer.acquire(NANO)
    assert pacer.delay(NANO) == pytest.approx(0.5), "a third call waits for a slot"
    first.done(100)
    assert pacer.delay(NANO) == pytest.approx(20.0), "and then for the gap after the last call ended"
    clock.now += 20
    assert pacer.delay(NANO) == 0.0
    second.done(None)


def test_a_retry_counts_against_the_request_limit():
    clock = Clock()
    pacer = Pacer(rpm=2, parallel=4, tpm={}, clock=clock, sleep=clock.sleep)
    pacer.acquire(NANO)
    pacer.retry_gate(NANO)
    pacer.acquire(SUPER)
    assert clock.slept == [60.0], "the third request in a minute waits"


# --- Track B end to end -----------------------------------------------------------------------------------------


def _caption_answer(enabled, skipped=None):
    stages = {"p1": {"model": SUPER, "calls": 1, "total_tokens": 9000, "calls_without_usage": 0},
              "grounding": {"model": SUPER, "calls": 2, "total_tokens": 900, "calls_without_usage": 0},
              "zoom": {"model": SUPER, "calls": 2, "total_tokens": 900, "calls_without_usage": 0}}
    if enabled:
        stages["caption"] = {"model": SUPER, "calls": 3, "total_tokens": 21000, "calls_without_usage": 0}
    provenance = _provenance(sha="feedbeef", flags={"chunking": {"window_seconds": 20.0, "enabled": enabled}},
                             stages=stages)
    status, body = _answer(provenance)
    body["experiment"]["caption"] = {"store": "inline", "skipped": skipped, "calls": 3 if enabled else 0}
    return status, body


def test_track_b_runs_pins_the_agent_and_seals(tmp_path):
    agent = FakeAgent(_answer(), _answer())
    executor = _executor(tmp_path, _track_b(), adapter_kwargs={"post": agent})
    summary = executor.run()
    assert summary["sealed"] and summary["outcomes"] == {"scored": 2} and len(agent.calls) == 2
    assert all(url.startswith("http://127.0.0.1:8765/api/v1/incidents/") for url, _ in agent.calls)
    assert all("X-Amz-Signature=sig" in body["video_url"] for _, body in agent.calls), "the agent gets a signed URL"
    manifest = json.loads((tmp_path / "run" / "store" / "runs" / "v2-test-track-b" / "manifest.json").read_text())
    assert manifest["agent"]["agent"]["git_sha"] == "abc123" and manifest["track"] == "B"
    assert manifest["endpoints"]["agent_url"] == "http://127.0.0.1:8765"
    rows = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()]
    assert rows[0]["tokens"]["inference"] == {"attempts": 1, "tokens": 30000} and rows[0]["status"] == 200
    assert executor.pacer.parallel == 1


def test_track_b_resumes_against_the_stored_agent_pin(tmp_path):
    config = _track_b()
    with pytest.raises(RuntimeError, match="went away"):
        _executor(tmp_path, config, adapter_kwargs={"post": FakeAgent(_answer(), RuntimeError("went away"))}).run()
    drifted = FakeAgent(_answer(_provenance(sha="other")))
    with pytest.raises(RunStopped, match="AgentDriftError"):
        _executor(tmp_path, config, adapter_kwargs={"post": drifted}).run()
    events = (tmp_path / "run" / "events.jsonl").read_text()
    assert '"event":"stopped"' in events


def test_the_caption_profile_needs_the_marker_to_enable_it(tmp_path):
    config = _track_b("production-v4-captions")
    items = cli.plan(config, lambda cfg: _datasets())
    agent = FakeAgent(_caption_answer(True), _caption_answer(False, "video of 30 s or less"))
    with pytest.raises(LiveRefused, match="gated on base64-probe"):
        _executor(tmp_path, config, adapter_kwargs={"post": agent}).run()
    assert agent.calls == []
    marker = _marker(tmp_path, config, items, enabled_profiles=["production-v4-captions"])
    executor = _executor(tmp_path, config, adapter_kwargs={"post": agent}, marker=marker)
    summary = executor.run()
    assert summary["sealed"], "chunking.enabled differs between the two clips and the run goes on (B3)"
    assert all(body["experiment_captions"] == "inline" and body["max_caption_calls"] == 30 for _, body in agent.calls)
    refs = [Checkpoint(tmp_path / "run" / "checkpoint.jsonl").final_result(k)["prediction_ref"]
            for k in Checkpoint(tmp_path / "run" / "checkpoint.jsonl").final_keys()]
    passes = sorted((executor.store.get(ArtifactRef.parse(r))["caption_pass"]["chunking_enabled"]) for r in refs)
    assert passes == [False, True]


def test_a_track_b_hold_over_the_cap_is_refused_before_sending(tmp_path):
    config = _track_b(caps=[{"ledger": "inference", "max_attempts": 10, "max_tokens": 200000},
                            {"ledger": "grounding", "max_attempts": 200, "max_tokens": 800000},
                            {"ledger": "zoom", "max_attempts": 120, "max_tokens": 1000000}])
    agent = FakeAgent(_answer(), _answer())
    summary = _executor(tmp_path, config, adapter_kwargs={"post": agent}).run()
    # One run holds 80 zoom calls; after it releases to 6, 6 + 80 > 120 is still fine, so both fit; at 85 they do not.
    assert len(agent.calls) == 2 and summary["sealed"]
    tight = copy.deepcopy(config.model_dump())
    tight["caps"][2]["max_attempts"] = 79
    agent = FakeAgent(_answer())
    summary = _executor(tmp_path, RunConfig.model_validate(tight), out=tmp_path / "tight",
                        adapter_kwargs={"post": agent}).run()
    assert agent.calls == [] and summary["open"] == 2


def test_profile_builder_sends_the_caption_fields():
    built = requests_vss.build_analyze_request(requests_vss.PROFILES["production-v4-captions"], video_url="u",
                                               model=SUPER, duration_seconds=40.0,
                                               enabled=["production-v4-captions"])
    assert built["body"]["experiment_captions"] == "inline" and built["body"]["max_caption_calls"] == 30

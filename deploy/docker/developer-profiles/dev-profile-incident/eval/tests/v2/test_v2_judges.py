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
"""The third-family judge (X19): registry rules, family checks, selection by config, pinned request bodies, cache
identity, and live judging through a fake transport (no network, no model call)."""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

from evalv2 import cli
from evalv2.adapters.openai_compat import ENDPOINTS
from evalv2.adapters.registry import load_capabilities
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.config import RunConfig
from evalv2.core.netguard import no_network
from evalv2.core.retry import POLICIES
from evalv2.judge_stage import judge_ledger, live_factory
from evalv2.references import chat as refs
from evalv2.requests.judge import TEMPLATE_OVERHEAD_TOKENS, prompt_bytes, tokens_bound
from evalv2.tasks.chat import scoring as chat_scoring
from evalv2.tasks.incident_objects.scoring import embedder_similarity, stored_similarity
from evalv2.tasks.judges import (
    JUDGES_PATH,
    JudgeConfigError,
    build_request,
    judge_identity,
    judge_problems,
    load_judges,
    parse_score,
    prompt_sha256,
    registry_problems,
    select_judge,
    task_judges,
)
from evalv2.tasks.judging import CachedJudge, JudgeCache, JudgeIdentity, JudgeVerdict, RecordingJudge

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GT
from test_v2_incident_objects import _embed, _v2_score, frozen, v1_record  # noqa: F401  (fixtures used by name)

PRIMARY, SENSITIVITY = "catalog-gpt-oss-20b-v1", "catalog-mistral-large-4-v1"
CONTINUITY = "switchyard-nemotron-3-nano-v1"
GOLDEN = json.loads((Path(__file__).resolve().parent / "fixtures" / "judge-requests-v1.json").read_text("utf-8"))
CATALOG = json.loads((JUDGES_PATH.parents[1] / "adapters" / "catalog_rb1.json").read_text())
SUBJECTS = ["nvidia/cosmos-3-nano-reasoner", "nvidia/cosmos-3-super-reasoner",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia/nemotron-3-ultra",
            "nvidia/nemotron-3-ultra-550b-a55b"]
GENERATORS = ["anthropic/claude", "claude-opus-4", "anthropic/claude-sonnet-4-5"]
KEY = "catalog-key-never-stored"
ENV = {"NVIDIA_API_KEY": KEY, "VLM_GATEWAY_BASE_URL": "https://switchyard.example/v1",
       "VLM_GATEWAY_API_KEY": "gateway-key-never-stored"}


def _config(task_judges_by_task, *, judge_cap=True, subjects=SUBJECTS):
    return RunConfig.model_validate({
        "experiment": "judge-wiring", "split": {"half": "dev"}, "seed": 1,
        "subjects": [{"id": f"s{i}", "adapter": "openai_compat", "model": model, "endpoint": "switchyard"}
                     for i, model in enumerate(subjects)],
        "tasks": {task: {"options": {} if judges is None else {"judges": judges}}
                  for task, judges in task_judges_by_task.items()},
        "caps": [{"ledger": "judge", "max_attempts": 100, "max_tokens": 1_000_000}] if judge_cap else []})


def _wire(body):
    """A body as httpx sends ``json=`` (``httpx._content.encode_json``): compact UTF-8, key order kept."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class _JudgeServer:
    """A scripted judge endpoint for httpx.MockTransport: ``score`` maps the two texts to the score it returns."""

    def __init__(self, *replies, score=None):
        self.replies, self.score, self.requests = list(replies), score, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.score is not None:
            user = json.loads(request.content)["messages"][1]["content"]
            expected, predicted = re.match(r"EXPECTED: (.*)\nPREDICTED: (.*)", user, re.DOTALL).groups()
            reply = (json.dumps({"score": self.score(expected, predicted), "reasoning": "compared"}), "stop")
        else:
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, httpx.Response):
            return reply
        content, finish = reply if isinstance(reply, tuple) else (reply, "stop")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                      "finish_reason": finish}],
                                         "usage": {"prompt_tokens": 180, "completion_tokens": 60,
                                                   "completion_tokens_details": {"reasoning_tokens": 40}}})


def _ask(tmp_path, server, *, spec_caps=None, policy="none", subject="s0", task="incident_objects"):
    registry = load_judges()
    primary = registry.get(PRIMARY)
    ledger_spec = primary if spec_caps is None else dataclasses.replace(primary, **spec_caps)
    ledger = judge_ledger(ledger_spec, log_path=tmp_path / "budget.jsonl")
    ask = live_factory(store=ArtifactStore(tmp_path / "store"), ledger=ledger, policy=POLICIES[policy],
                       subject=subject, task=task, transport=httpx.MockTransport(server), environ=ENV,
                       sleep=lambda s: None)
    return ask, ledger


# ---------------------------------------------------------------------------------------------------------------
# Registry and families
# ---------------------------------------------------------------------------------------------------------------


def test_the_registry_holds_a_primary_a_sensitivity_and_a_continuity_judge():
    registry = load_judges()
    primary, sensitivity, continuity = (registry.get(i) for i in (PRIMARY, SENSITIVITY, CONTINUITY))
    assert (primary.role, primary.model, primary.endpoint) == ("primary", "openai/gpt-oss-20b", "nvidia-api-catalog")
    assert (sensitivity.role, sensitivity.model) == ("sensitivity", "mistralai/mistral-large-4-1t-a52b")
    assert (continuity.role, continuity.model, continuity.endpoint) == (
        "continuity", "nvidia/nemotron-3-nano-30b-a3b", "switchyard")
    assert continuity.kinds == ("incident",) and continuity.parse_retries == 2
    for spec in (primary, sensitivity):
        assert set(spec.kinds) == {"incident", "fact", "unsupported", "contradiction"} and spec.parse_retries == 0
    for spec in registry.judges.values():  # hard caps on every judge, per request and per stage
        assert spec.max_output_tokens == 1500 and spec.max_prompt_bytes > 0
        assert spec.caps()[0].ledger == "judge" and spec.max_requests > 0 and spec.max_tokens > 0


def test_the_catalog_key_comes_from_the_existing_endpoint_and_is_never_in_the_registry():
    endpoint = ENDPOINTS["nvidia-api-catalog"]
    assert (endpoint.api_key_env, endpoint.default_base_url) == ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1")
    for entry in json.loads(JUDGES_PATH.read_text())["judges"]:
        assert not [field for field in entry if "key" in field.lower() or "url" in field.lower()], entry["id"]


def test_every_judge_is_registered_and_listed_on_its_endpoint_in_rb1():
    capabilities = load_capabilities()
    for spec in load_judges().judges.values():
        assert spec.model in capabilities and spec.endpoint in capabilities[spec.model].listed_on, spec.id
        assert spec.model in CATALOG["endpoints"][spec.endpoint]["models"], spec.id


def test_the_budgeted_judges_stay_within_the_owner_approved_budget():
    registry = load_judges()
    budget = registry.budget
    assert set(budget["judges"]) == {PRIMARY, SENSITIVITY}
    assert (budget["max_requests"], budget["max_tokens"]) == (2000, 3_000_000)
    members = [registry.get(j) for j in budget["judges"]]
    assert sum(s.max_requests for s in members) <= 2000 and sum(s.max_tokens for s in members) <= 3_000_000


@pytest.mark.parametrize("judge", ["openai/gpt-oss-20b", "mistralai/mistral-large-4-1t-a52b"])
def test_the_catalog_judges_pass_the_family_check_against_every_subject_and_claude(judge):
    for subject in SUBJECTS:
        chat_scoring.check_judge_family(judge, subjects=[subject], generators=GENERATORS)
    chat_scoring.check_judge_family(judge, subjects=SUBJECTS, generators=GENERATORS)


@pytest.mark.parametrize("judge", ["nvidia/nemotron-3-nano-30b-a3b", "nvidia/nemotron-nano-3-30b-a3b",
                                   "nvidia/llama-3.1-nemotron-ultra-253b-v1", "nvidia/nemotron-3-super-120b-a12b",
                                   "nvidia/cosmos-reason2-8b", "nvidia/cosmos-3-super-reasoner",
                                   "anthropic/claude-opus-4", "claude-sonnet-4-5"])
def test_nemotron_cosmos_and_claude_judges_are_refused(judge):
    with pytest.raises(chat_scoring.JudgeFamilyError):
        chat_scoring.check_judge_family(judge, subjects=SUBJECTS, generators=GENERATORS)


def test_a_registry_with_a_same_family_primary_or_an_overspent_budget_is_refused(tmp_path):
    raw = json.loads(JUDGES_PATH.read_text())
    nemotron = json.loads(json.dumps(raw))
    nemotron["judges"][0]["model"] = "nvidia/nemotron-3-super-120b-a12b"
    path = tmp_path / "judges.json"
    path.write_text(json.dumps(nemotron))
    with pytest.raises(JudgeConfigError, match="nemotron"):
        load_judges(path)
    overspent = json.loads(json.dumps(raw))
    overspent["judges"][0]["caps"]["max_requests"] = 1600
    path.write_text(json.dumps(overspent))
    with pytest.raises(JudgeConfigError, match="request caps sum past the approved 2000"):
        load_judges(path)
    widened = json.loads(json.dumps(raw))
    widened["judges"][2]["kinds"] = ["incident", "fact"]
    path.write_text(json.dumps(widened))
    with pytest.raises(JudgeConfigError, match="continuity judge judges only the incident kind"):
        load_judges(path)
    assert registry_problems(load_judges()) == []


# ---------------------------------------------------------------------------------------------------------------
# Selection by run config
# ---------------------------------------------------------------------------------------------------------------


def test_the_incident_and_chat_tasks_select_their_judges_by_config():
    config = _config({"incident_objects": {"primary": PRIMARY, "sensitivity": SENSITIVITY, "continuity": CONTINUITY},
                      "chat": {"primary": PRIMARY, "sensitivity": SENSITIVITY}})
    assert judge_problems(config) == []
    assert select_judge(config, "incident_objects").model == "openai/gpt-oss-20b"
    assert select_judge(config, "incident_objects", "sensitivity").model == "mistralai/mistral-large-4-1t-a52b"
    assert select_judge(config, "incident_objects", "continuity").model == "nvidia/nemotron-3-nano-30b-a3b"
    assert select_judge(config, "chat").id == PRIMARY
    with pytest.raises(JudgeConfigError, match="names no continuity judge"):
        select_judge(config, "chat", "continuity")


def test_v1s_judge_can_only_be_the_continuity_judge_and_only_for_incident_fields():
    as_primary = _config({"incident_objects": {"primary": CONTINUITY}})
    problems = judge_problems(as_primary)
    assert any("is a continuity judge, not a primary judge" in p for p in problems)
    for_chat = _config({"chat": {"primary": PRIMARY, "continuity": CONTINUITY}})
    assert any("cannot judge ['contradiction', 'fact', 'unsupported']" in p for p in judge_problems(for_chat))
    with pytest.raises(JudgeConfigError):
        select_judge(as_primary, "incident_objects")


def test_preflight_refuses_unknown_judges_missing_caps_and_untaken_judges():
    config = _config({"incident_objects": {"primary": "catalog-unknown"}}, judge_cap=False)
    problems = cli.preflight(config, {})
    assert any("unknown judge 'catalog-unknown'" in p for p in problems)
    assert any("must cap the judge ledger" in p for p in problems)
    dashboard = _config({"dashboard": {"primary": PRIMARY}})
    assert any("has no judged checks" in p for p in cli.preflight(dashboard, {}))
    no_primary = _config({"incident_objects": {"sensitivity": SENSITIVITY}})
    assert any("names a primary judge" in p for p in cli.preflight(no_primary, {}))
    assert cli.preflight(_config({"incident_objects": None}, judge_cap=False), {}) == [], "no judge: unchanged"


def test_a_subject_of_the_judges_family_makes_the_selection_fail():
    config = _config({"incident_objects": {"primary": PRIMARY}}, subjects=[*SUBJECTS, "openai/gpt-oss-120b"])
    assert any("'gpt' family" in p for p in judge_problems(config))


def test_a_sensitivity_judge_of_the_primarys_family_checks_nothing(tmp_path):
    raw = json.loads(JUDGES_PATH.read_text())
    raw["judges"][1]["model"] = "openai/gpt-oss-120b"
    raw["judges"][1]["id"] = "catalog-gpt-oss-120b-v1"
    raw["budget"]["judges"] = [PRIMARY, "catalog-gpt-oss-120b-v1"]
    path = tmp_path / "judges.json"
    path.write_text(json.dumps(raw))
    config = _config({"incident_objects": {"primary": PRIMARY, "sensitivity": "catalog-gpt-oss-120b-v1"}})
    assert any("shares the primary judge's family" in p for p in judge_problems(config, load_judges(path)))


# ---------------------------------------------------------------------------------------------------------------
# Request bodies and the parse rule
# ---------------------------------------------------------------------------------------------------------------


def test_v1s_continuity_judge_request_is_byte_equal_to_v1s_own_body(monkeypatch):
    import eval_gt
    import methodology

    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 0.7}'},
                                                      "finish_reason": "stop"}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setenv("INCIDENT_LLM_BASE_URL", "http://judge.invalid/v1")
    monkeypatch.delenv("INCIDENT_LLM_API_KEY", raising=False)
    monkeypatch.setattr(eval_gt.httpx, "post", fake_post)
    expected, predicted = GOLDEN["texts"]["expected"], GOLDEN["texts"]["predicted"]
    assert eval_gt._judge_once(expected, predicted).data == 0.7
    spec = load_judges().get(CONTINUITY)
    built = build_request(spec, "incident", expected, predicted)
    assert json.dumps(built) == json.dumps(sent["json"]), "same keys, order and values as V1's body"
    assert _wire(built) == _wire(sent["json"]), "same bytes on the wire"
    assert spec.model == methodology.JUDGE_MODEL and dict(spec.settings) == methodology.JUDGE_REQUEST_SETTINGS
    assert prompt_sha256("incident") == methodology.judge_prompt_sha256()
    assert build_request(spec, "incident", None, None)["messages"][1]["content"] == "EXPECTED: \nPREDICTED: "


def test_every_judge_request_body_equals_its_golden():
    registry = load_judges()
    expected, predicted = GOLDEN["texts"]["expected"], GOLDEN["texts"]["predicted"]
    built = {f"{spec.id}/{kind}": build_request(spec, kind, expected, predicted)
             for spec in registry.judges.values() for kind in spec.kinds}
    assert list(built) == list(GOLDEN["requests"])
    for name, body in built.items():
        assert json.dumps(body) == json.dumps(GOLDEN["requests"][name]), name


def test_the_wire_bytes_through_the_adapter_are_the_goldens(tmp_path):
    server = _JudgeServer('{"score": 0.9, "reasoning": "same"}')
    ask, _ = _ask(tmp_path, server)
    registry = load_judges()
    expected, predicted = GOLDEN["texts"]["expected"], GOLDEN["texts"]["predicted"]
    for kind in registry.get(PRIMARY).kinds:
        ask(registry.get(PRIMARY), kind)(expected, predicted)
    golden = [GOLDEN["requests"][f"{PRIMARY}/{kind}"] for kind in registry.get(PRIMARY).kinds]
    assert [r.content for r in server.requests] == [_wire(body) for body in golden]
    assert {str(r.url) for r in server.requests} == {"https://integrate.api.nvidia.com/v1/chat/completions"}
    assert {r.headers["authorization"] for r in server.requests} == {f"Bearer {KEY}"}


def test_the_parse_rule_is_v1s():
    import eval_gt

    contents = ['{"score": 0.8, "reasoning": "x"}', '```json\n{"score": 0.4}\n```', "```\n{\"score\": 0.6}\n```",
                "Score: 0.75", "The answer is 1", '{"score": 1.5}', '{"score": "0.3"}', '{"score": null}',
                "no number", "", '{"reasoning": "close"} 0.2', '{"score": NaN}', 'score 0.95 and {"score": 0.1}',
                '{"a": {"score": 0.5}}', "1.0", "0", '{"score": -0.1, "reasoning": "x"}', "{broken json 0.3"]
    for content in contents:
        assert parse_score(content) == eval_gt._extract_score(content), content


def test_the_token_bound_covers_text_template_and_output():
    spec = load_judges().get(PRIMARY)
    request = build_request(spec, "incident", "a" * 100, "b" * 50)
    assert tokens_bound(request) == prompt_bytes(request) + TEMPLATE_OVERHEAD_TOKENS + 1500
    assert prompt_bytes(build_request(spec, "incident", "é", "")) == prompt_bytes(
        build_request(spec, "incident", "ab", "")), "bytes, not characters"


# ---------------------------------------------------------------------------------------------------------------
# Cache identity
# ---------------------------------------------------------------------------------------------------------------


def test_the_cache_identity_covers_model_prompt_settings_and_endpoint(tmp_path):
    registry = load_judges()
    primary = registry.get(PRIMARY)
    base = judge_identity(primary, "incident")
    assert base.model == "openai/gpt-oss-20b" and base.prompt_sha256 == prompt_sha256("incident")
    assert base.settings["request"] == {"temperature": 0.0, "max_tokens": 1500}
    assert base.settings["endpoint"] == "nvidia-api-catalog" and base.settings["parser"] == "v1-extract-score"
    variants = {
        "model": judge_identity(dataclasses.replace(primary, model="openai/gpt-oss-120b"), "incident"),
        "prompt (another kind)": judge_identity(primary, "fact"),
        "prompt (an edited file)": JudgeIdentity(base.model, "0" * 64, base.settings),
        "settings": judge_identity(dataclasses.replace(primary, settings={"temperature": 0.0, "max_tokens": 2000}),
                                   "incident"),
        "endpoint": judge_identity(dataclasses.replace(primary, endpoint="local-openai"), "incident"),
        "parse retries": judge_identity(dataclasses.replace(primary, parse_retries=1), "incident"),
        "sensitivity judge": judge_identity(registry.get(SENSITIVITY), "incident"),
        "continuity judge": judge_identity(registry.get(CONTINUITY), "incident"),
    }
    keys = {name: identity.key("e", "p") for name, identity in variants.items()}
    assert base.key("e", "p") not in keys.values() and len(set(keys.values())) == len(keys)
    path = tmp_path / "judge.jsonl"
    RecordingJudge(JudgeCache(path, base), lambda e, p: JudgeVerdict(0.9, raw='{"score": 0.9}'))("e", "p")
    assert CachedJudge(JudgeCache(path, base))("e", "p").score == 0.9
    for name, identity in variants.items():
        verdict = CachedJudge(JudgeCache(path, identity))("e", "p")
        assert verdict.score is None and verdict.error.startswith("not judged"), name
    assert json.loads(path.read_text().splitlines()[0])["raw"] == '{"score": 0.9}', "the raw reply is kept"


# ---------------------------------------------------------------------------------------------------------------
# Live judging through a fake transport
# ---------------------------------------------------------------------------------------------------------------


def test_a_live_judge_charges_the_judge_ledger_and_never_stores_the_key(tmp_path):
    server = _JudgeServer('{"score": 0.8, "reasoning": "same event"}')
    ask, ledger = _ask(tmp_path, server)
    judge = ask(load_judges().get(PRIMARY), "incident")
    verdict = judge("A man punches another man.", "One man hits another.")
    assert verdict.ok and verdict.score == 0.8 and verdict.raw.startswith('{"score": 0.8')
    events = [json.loads(line) for line in (tmp_path / "budget.jsonl").read_text().splitlines()]
    request = json.loads(server.requests[0].content)
    assert events[0]["event"] == "reserve" and events[0]["ledger"] == "judge" and events[0]["subject"] == "s0"
    assert events[0]["tokens_bound"] == tokens_bound(request), "reserved at the request's worst case before sending"
    assert events[1] == {"event": "settle", "id": events[0]["id"], "tokens_used": 240}
    assert ledger.totals("judge").attempts == 1 and ledger.totals("judge").tokens == 240
    stored = b"".join(p.read_bytes() for p in (tmp_path / "store").rglob("*") if p.is_file())
    assert KEY.encode() not in stored and b"same event" in stored


@pytest.mark.parametrize(("reply", "error"), [
    (('{"score": 0.9', "length"), "truncated at max_tokens=1500"),
    ("I cannot tell.", "could not extract a score"),
    ('{"score": 1.4, "reasoning": "x"}', "could not extract a score"),
    (httpx.Response(500, text="upstream error"), "judge call failed: http_error"),
])
def test_unscored_outcomes_are_never_scores_and_a_v2_judge_never_re_asks(tmp_path, reply, error):
    server = _JudgeServer(reply)
    ask, _ = _ask(tmp_path, server)
    verdict = ask(load_judges().get(PRIMARY), "incident")("expected", "predicted")
    assert verdict.score is None and error in verdict.error and len(server.requests) == 1


def test_the_continuity_judge_re_asks_a_scoreless_reply_twice_as_v1_did(tmp_path):
    server = _JudgeServer("No score here.", "Still none.", '{"score": 0.6, "reasoning": "ok"}')
    ask, ledger = _ask(tmp_path, server)
    verdict = ask(load_judges().get(CONTINUITY), "incident")("expected", "predicted")
    assert verdict.score == 0.6 and len(server.requests) == 3 and ledger.totals("judge").attempts == 3
    assert str(server.requests[0].url) == "https://switchyard.example/v1/chat/completions"


def test_an_input_over_the_limit_or_past_the_caps_is_never_sent(tmp_path):
    server = _JudgeServer('{"score": 0.5, "reasoning": "x"}')
    ask, ledger = _ask(tmp_path, server, spec_caps={"max_requests": 1})
    judge = ask(load_judges().get(PRIMARY), "incident")
    too_long = judge("x" * 70_000, "y")
    assert too_long.score is None and "max_prompt_bytes" in too_long.error and server.requests == []
    assert judge("a", "b").score == 0.5
    refused = judge("c", "d")
    assert refused.score is None and "budget_exceeded" in refused.error
    assert len(server.requests) == 1 and ledger.totals("judge").attempts == 1


def test_the_incident_objects_judge_selected_by_config_scores_live_then_offline(frozen, v1_record, tmp_path):  # noqa: F811
    def score(expected, predicted):
        return 0.9 if expected.strip().lower() == predicted.strip().lower() else 0.2  # the V1 fixture's judge

    config = _config({"incident_objects": {"primary": PRIMARY}})
    server = _JudgeServer(score=score)
    ask, ledger = _ask(tmp_path, server)
    judges = task_judges(config, "incident_objects", cache_dir=tmp_path / "cache", ask=ask)
    assert set(judges) == {"incident"}
    video = v1_record["videos"][0]
    live = _v2_score(frozen, video, embedder_similarity(_embed), judges["incident"])
    assert live["incident_field_scores"] == video["incident_field_scores"], "same scores as V1's own record"
    assert ledger.totals("judge").attempts == len(server.requests) > 0
    with no_network():
        offline_judges = task_judges(config, "incident_objects", cache_dir=tmp_path / "cache")
        offline = _v2_score(frozen, video, stored_similarity(live["similarity"]), offline_judges["incident"])
    assert offline_judges["incident"].misses == []
    assert {k: v for k, v in offline.items() if k != "similarity"} == {k: v for k, v in live.items()
                                                                        if k != "similarity"}


def test_the_chat_judges_selected_by_config_score_live_then_offline(tmp_path):
    facts = ["man in red hits man in blue", "a wooden stick is used"]
    record = refs.build_record("incident.facts", {"incident_id": "Assault001"}, GT, question="Q facts",
                               paraphrases=["P1", "P2"], dataset="chat-v0",
                               generator={"prompt": "gen-chat-wording-v1", "model": "claude-opus"}, facts=facts)
    answer = chat_scoring.ChatAnswer(text="The man in red hits the man in blue.", context="Assault001: a fight.")

    def score(expected, predicted):
        return 1 if expected.split()[-1].lower() in predicted.lower() else 0

    config = _config({"chat": {"primary": PRIMARY}})
    server = _JudgeServer(score=score)
    ask, _ = _ask(tmp_path, server, task="chat")
    judges = task_judges(config, "chat", cache_dir=tmp_path / "cache", ask=ask)
    assert set(judges) == {"fact", "unsupported", "contradiction"}
    first = chat_scoring.score_answer(record, answer, corpus="oracle", ground_truth_text="ground truth",
                                      judges=judges)
    assert first["facts"] == {"stated": 1, "scored": 2, "total": 2, "recall": 0.5}
    prompts = {json.loads(r.content)["messages"][0]["content"].split(".")[0] for r in server.requests}
    assert len(prompts) == 3, "each judged check sends its own hash-pinned prompt"
    with no_network():
        offline = task_judges(config, "chat", cache_dir=tmp_path / "cache")
        second = chat_scoring.score_answer(record, answer, corpus="oracle", ground_truth_text="ground truth",
                                           judges=offline)
    assert second == first and all(not j.misses for j in offline.values())

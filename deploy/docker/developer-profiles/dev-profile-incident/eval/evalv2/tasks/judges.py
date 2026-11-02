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
"""Judges chosen by configuration (X19): which judge a task uses, the exact request, the cache identity, and the
live ``ask`` a judge stage hands to :class:`~evalv2.tasks.judging.RecordingJudge`.

- **Registry** (``judges.json``): each judge by id, with its role (``primary``, ``sensitivity`` or ``continuity``),
  model, adapter and endpoint, pinned request settings, the judged kinds it may score, its parse retries, a
  per-request input limit and hard request and token caps per judge stage. Loading refuses a primary or
  sensitivity judge that shares a family with a V2 subject or the reference generator.
- **Selection:** a task names its judges in its options, ``{"judges": {"primary": id, "sensitivity": id,
  "continuity": id}}``. ``incident_objects`` and ``pipeline`` use the ``incident`` kind (V1's
  ``judge-prompt-v1``); the chat scorers use ``fact``, ``unsupported`` and ``contradiction``. A judge's role
  must match its slot, so V1's Nemotron judge can only ever be the continuity judge, and only for ``incident``.
- **Identity:** model, prompt SHA-256, endpoint, request settings, user template, parse rule and parse retries.
  Changing any of them misses every cached verdict.
- **Live judging** (an approval-gated stage): :class:`LiveJudge` sends one judged pair through a
  :class:`~evalv2.adapters.base.ModelAdapter`, every attempt reserved on the ``judge`` ledger at its worst case
  before it is sent. A failed call, a reply cut off at ``max_tokens``, an input over the limit, or a reply without
  a score in [0, 1] is unscored, never a score.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalv2.adapters.base import CallContext, CallResult, ModelAdapter
from evalv2.core.budget import Cap
from evalv2.core.private import ensure_private
from evalv2.requests.judge import USER_TEMPLATE_ID, build_judge_request, prompt_bytes, tokens_bound
from evalv2.tasks.chat.scoring import JUDGE_PROMPTS, JudgeFamilyError, check_judge_family, model_family
from evalv2.tasks.judging import CachedJudge, Judge, JudgeCache, JudgeIdentity, JudgeVerdict, RecordingJudge

JUDGES_PATH = Path(__file__).resolve().with_name("judges.json")
REGISTRY_VERSION = "evalv2-judges-v1"
EVAL_DIR = Path(__file__).resolve().parents[2]
# V1's description and item-text judge prompt (methodology.judge_prompt()): read as a file, never imported or edited.
V1_JUDGE_PROMPT = EVAL_DIR / "methodology" / "judge-prompt-v1.txt"
PROMPTS: dict[str, Path] = {"incident": V1_JUDGE_PROMPT, **JUDGE_PROMPTS}
ROLES = ("primary", "sensitivity", "continuity")
TASK_KINDS: dict[str, tuple[str, ...]] = {"incident_objects": ("incident",), "pipeline": ("incident",),
                                          "chat": ("fact", "unsupported", "contradiction")}
PARSER_ID = "v1-extract-score"  # eval_gt._extract_score's rules, re-implemented below and pinned by a test
LEDGER = "judge"
_SPEC_KEYS = frozenset({"id", "role", "model", "adapter", "endpoint", "settings", "kinds", "parse_retries",
                        "max_prompt_bytes", "caps", "status", "notes"})
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class JudgeConfigError(ValueError):
    """A judge registry or a run config's judge selection breaks a rule; nothing was sent."""


@dataclass(frozen=True)
class JudgeSpec:
    id: str
    role: str
    model: str
    adapter: str
    endpoint: str
    settings: Mapping[str, Any] = field(hash=False)
    kinds: tuple[str, ...]
    parse_retries: int
    max_prompt_bytes: int
    max_requests: int
    max_tokens: int
    status: str = ""
    notes: str = ""

    @property
    def max_output_tokens(self) -> int:
        return int(self.settings["max_tokens"])

    def caps(self) -> list[Cap]:
        """The judge's hard ceilings, as caps on the ``judge`` ledger of its own judge stage."""
        return [Cap(LEDGER, max_attempts=self.max_requests, max_tokens=self.max_tokens)]


@dataclass(frozen=True)
class JudgeRegistry:
    version: str
    judges: Mapping[str, JudgeSpec] = field(hash=False)
    reference_generators: tuple[str, ...]
    subject_models: tuple[str, ...]
    budget: Mapping[str, Any] = field(hash=False)

    def get(self, judge_id: str) -> JudgeSpec:
        if judge_id not in self.judges:
            raise JudgeConfigError(f"unknown judge {judge_id!r}; known: {sorted(self.judges)}")
        return self.judges[judge_id]


def _spec(entry: Mapping[str, Any]) -> JudgeSpec:
    unknown = sorted(set(entry) - _SPEC_KEYS)
    if unknown:
        raise JudgeConfigError(f"judge {entry.get('id')!r}: unknown keys {unknown}")
    caps = entry.get("caps") or {}
    try:
        return JudgeSpec(id=str(entry["id"]), role=str(entry["role"]), model=str(entry["model"]),
                         adapter=str(entry["adapter"]), endpoint=str(entry["endpoint"]),
                         settings=dict(entry["settings"]), kinds=tuple(entry["kinds"]),
                         parse_retries=int(entry["parse_retries"]), max_prompt_bytes=int(entry["max_prompt_bytes"]),
                         max_requests=int(caps["max_requests"]), max_tokens=int(caps["max_tokens"]),
                         status=str(entry.get("status", "")), notes=str(entry.get("notes", "")))
    except (KeyError, TypeError, ValueError) as exc:
        raise JudgeConfigError(f"judge {entry.get('id')!r}: malformed entry ({exc})") from exc


def registry_problems(registry: JudgeRegistry) -> list[str]:
    """Every rule a registry breaks: roles, kinds, settings, caps, families, and the approved budget."""
    problems: list[str] = []
    if registry.version != REGISTRY_VERSION:
        problems.append(f"registry version is {registry.version!r}, expected {REGISTRY_VERSION!r}")
    for spec in registry.judges.values():
        where = f"judge {spec.id!r}"
        if not _ID.match(spec.id):
            problems.append(f"{where}: invalid id")
        if spec.role not in ROLES:
            problems.append(f"{where}: role {spec.role!r} is not one of {list(ROLES)}")
        if spec.adapter != "openai_compat":
            problems.append(f"{where}: judges speak chat completions (adapter openai_compat)")
        if not spec.kinds or set(spec.kinds) - set(PROMPTS):
            problems.append(f"{where}: kinds must be a non-empty subset of {sorted(PROMPTS)}")
        max_tokens = spec.settings.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            problems.append(f"{where}: settings.max_tokens must be a positive integer (the output cap)")
        if not isinstance(spec.settings.get("temperature"), int | float):
            problems.append(f"{where}: settings.temperature must be pinned")
        if not 0 <= spec.parse_retries <= 2:
            problems.append(f"{where}: parse_retries must be 0, 1 or 2")
        if spec.max_prompt_bytes <= 0 or spec.max_requests <= 0 or spec.max_tokens <= 0:
            problems.append(f"{where}: max_prompt_bytes and both caps must be positive")
        if spec.role == "continuity":
            if spec.kinds != ("incident",):
                problems.append(f"{where}: a continuity judge judges only the incident kind (V1's prompt)")
        else:
            try:
                check_judge_family(spec.model, subjects=registry.subject_models,
                                   generators=registry.reference_generators)
            except JudgeFamilyError as exc:
                problems.append(f"{where}: {exc}")
    budget = registry.budget or {}
    members = [registry.judges[j] for j in budget.get("judges", []) if j in registry.judges]
    missing = sorted(set(budget.get("judges", [])) - set(registry.judges))
    if missing:
        problems.append(f"the budget names unknown judges {missing}")
    if members:
        if sum(s.max_requests for s in members) > int(budget["max_requests"]):
            problems.append(f"the budgeted judges' request caps sum past the approved {budget['max_requests']}")
        if sum(s.max_tokens for s in members) > int(budget["max_tokens"]):
            problems.append(f"the budgeted judges' token caps sum past the approved {budget['max_tokens']}")
    return problems


def load_judges(path: Path = JUDGES_PATH) -> JudgeRegistry:
    """The judge registry, refused whole when any rule is broken."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = [_spec(entry) for entry in raw["judges"]]
    ids = [s.id for s in specs]
    if len(ids) != len(set(ids)):
        raise JudgeConfigError(f"duplicate judge ids: {ids}")
    registry = JudgeRegistry(version=str(raw.get("version")), judges={s.id: s for s in specs},
                             reference_generators=tuple(raw.get("reference_generators", ())),
                             subject_models=tuple(raw.get("v2_subject_models", ())),
                             budget=dict(raw.get("budget") or {}))
    problems = registry_problems(registry)
    if problems:
        raise JudgeConfigError("; ".join(problems))
    return registry


# ---------------------------------------------------------------------------------------------------------------
# The request, the identity and the reply
# ---------------------------------------------------------------------------------------------------------------


def _require_kind(spec: JudgeSpec, kind: str) -> None:
    if kind not in spec.kinds:
        raise JudgeConfigError(f"judge {spec.id!r} does not judge {kind!r}; it judges {list(spec.kinds)}")


def prompt_text(kind: str) -> str:
    return PROMPTS[kind].read_text(encoding="utf-8")


def prompt_sha256(kind: str) -> str:
    return hashlib.sha256(PROMPTS[kind].read_bytes()).hexdigest()


def judge_identity(spec: JudgeSpec, kind: str) -> JudgeIdentity:
    """Everything that decides a verdict besides the two texts; any change makes every cached verdict a miss."""
    _require_kind(spec, kind)
    return JudgeIdentity(spec.model, prompt_sha256(kind),
                         {"endpoint": spec.endpoint, "request": dict(spec.settings), "user_template": USER_TEMPLATE_ID,
                          "parser": PARSER_ID, "parse_retries": spec.parse_retries})


def build_request(spec: JudgeSpec, kind: str, expected: str | None, predicted: str | None) -> dict[str, Any]:
    _require_kind(spec, kind)
    return build_judge_request(spec.model, prompt_text(kind), expected, predicted, spec.settings)


_SCORE_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_SCORE_BARE_OBJECT = re.compile(r"(\{[\s\S]*\"score\"[\s\S]*\})")
_SCORE_FALLBACK = re.compile(r"([01](?:\.\d+)?)")


def _valid_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if 0.0 <= score <= 1.0 else None


def parse_score(content: str | None) -> float | None:
    """V1's score rule (``eval_gt._extract_score``): a fenced JSON object, else a bare one holding ``score``, else
    the first 0/1-shaped number; a value outside [0, 1] (or NaN) is no score, never clamped."""
    if not content:
        return None
    for pattern in (_SCORE_JSON_BLOCK, _SCORE_BARE_OBJECT):
        match = pattern.search(content)
        if not match:
            continue
        try:
            return _valid_score(json.loads(match.group(1)).get("score"))
        except (json.JSONDecodeError, AttributeError):
            continue
    match = _SCORE_FALLBACK.search(content)
    return _valid_score(match.group(1)) if match else None


def expected_json(content: str | None) -> dict[str, Any]:
    """Whether a reply is the JSON every judge prompt asks for, ``{"score": <0..1>, "reasoning": "<text>"}``, alone.

    The scorer reads replies with the more lenient :func:`parse_score`; this stricter reading is what the capability
    probe reports (a fenced reply is noted, not refused).
    """
    text = (content or "").strip()
    fenced = _SCORE_JSON_BLOCK.fullmatch(text)
    body = fenced.group(1) if fenced else text
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return {"json_object": False, "fenced": bool(fenced), "keys": [], "score_valid": False}
    if not isinstance(value, dict):
        return {"json_object": False, "fenced": bool(fenced), "keys": [], "score_valid": False}
    score = value.get("score")
    return {"json_object": True, "fenced": bool(fenced), "keys": sorted(value),
            "score_valid": not isinstance(score, bool) and _valid_score(score) is not None,
            "reasoning_is_text": isinstance(value.get("reasoning"), str)}


# ---------------------------------------------------------------------------------------------------------------
# Live judging (approval-gated: only a judge stage with an approved budget builds one)
# ---------------------------------------------------------------------------------------------------------------


class LiveJudge:
    """One judge and kind through a model adapter: the ``ask`` of a :class:`RecordingJudge`.

    Every attempt goes through ``adapter.complete`` on the ``judge`` ledger, reserved at the request's worst case
    (:func:`evalv2.requests.judge.tokens_bound`) before it is sent; the adapter applies the run's retry policy to
    infrastructure failures. A reply without a score is re-asked with the identical request only up to the judge's
    ``parse_retries`` (V1's continuity judge: 2; a V2 judge: 0).
    """

    def __init__(self, spec: JudgeSpec, kind: str, adapter: ModelAdapter, ctx: CallContext) -> None:
        _require_kind(spec, kind)
        if adapter.model != spec.model:
            raise JudgeConfigError(f"adapter serves {adapter.model!r}, judge {spec.id!r} is {spec.model!r}")
        self.spec, self.kind, self.adapter = spec, kind, adapter
        self.ctx = dataclasses.replace(ctx, budget_ledger=LEDGER)
        self.prompt = prompt_text(kind)
        self.results: list[CallResult] = []

    def __call__(self, expected: str | None, predicted: str | None) -> JudgeVerdict:
        request = build_judge_request(self.spec.model, self.prompt, expected, predicted, self.spec.settings)
        size = prompt_bytes(request)
        if size > self.spec.max_prompt_bytes:
            return JudgeVerdict(None, f"not judged: the input is {size} bytes, over the judge's "
                                      f"max_prompt_bytes {self.spec.max_prompt_bytes} (nothing was sent)")
        ctx = dataclasses.replace(self.ctx, tokens_bound=tokens_bound(request))
        error = ""
        for _ in range(1 + self.spec.parse_retries):
            result = self.adapter.complete(request, ctx)
            self.results.append(result)
            if not result.ok:
                failure = result.failure
                kind = failure.kind if failure else "unknown"
                return JudgeVerdict(None, f"judge call failed: {kind}: {(failure.detail if failure else '')[:200]}")
            if result.finish_reason == "length":
                return JudgeVerdict(None, f"judge reply truncated at max_tokens={self.spec.max_output_tokens} "
                                          "(finish_reason=length)", raw=result.text)
            score = parse_score(result.text)
            if score is not None:
                return JudgeVerdict(score, raw=result.text)
            error = "could not extract a score in [0, 1] from the judge reply"
        return JudgeVerdict(None, error)


# ---------------------------------------------------------------------------------------------------------------
# Selection by run config, and the judges a scorer receives
# ---------------------------------------------------------------------------------------------------------------


def configured_judges(config: Any, task: str) -> dict[str, str]:
    """The judge ids a task's options name, by slot (``{}`` when the task names none)."""
    raw = config.tasks[task].options.get("judges")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not all(isinstance(v, str) for v in raw.values()):
        raise JudgeConfigError(f"task {task!r}: options.judges must map slots to judge ids")
    return {str(slot): judge_id for slot, judge_id in raw.items()}


def task_problems(config: Any, task: str, registry: JudgeRegistry) -> list[str]:
    """Why a task's judge selection cannot be used: slots, roles, kinds and families."""
    try:
        slots = configured_judges(config, task)
    except JudgeConfigError as exc:
        return [str(exc)]
    if not slots:
        return []
    problems: list[str] = []
    kinds = TASK_KINDS.get(task)
    if kinds is None:
        problems.append(f"task {task!r} has no judged checks, so it takes no judges")
    unknown_slots = sorted(set(slots) - set(ROLES))
    if unknown_slots:
        problems.append(f"task {task!r}: unknown judge slots {unknown_slots}; slots are {list(ROLES)}")
    if "primary" not in slots:
        problems.append(f"task {task!r}: a task that names judges names a primary judge")
    subjects = [subject.model for subject in config.subjects]
    families: dict[str, str] = {}
    for slot, judge_id in slots.items():
        if slot not in ROLES:
            continue
        spec = registry.judges.get(judge_id)
        if spec is None:
            problems.append(f"task {task!r}: unknown judge {judge_id!r}; known: {sorted(registry.judges)}")
            continue
        if spec.role != slot:
            problems.append(f"task {task!r}: judge {judge_id!r} is a {spec.role} judge, not a {slot} judge")
        missing = sorted(set(kinds or ()) - set(spec.kinds))
        if missing:
            problems.append(f"task {task!r}: judge {judge_id!r} cannot judge {missing}")
        if spec.role != "continuity":
            families[slot] = model_family(spec.model)
            try:
                check_judge_family(spec.model, subjects=[*subjects, *registry.subject_models],
                                   generators=registry.reference_generators)
            except JudgeFamilyError as exc:
                problems.append(f"task {task!r}: {exc}")
    if "sensitivity" in families and families.get("primary") == families["sensitivity"]:
        problems.append(f"task {task!r}: the sensitivity judge shares the primary judge's family "
                        f"{families['sensitivity']!r}, so it checks nothing")
    return problems


def judge_problems(config: Any, registry: JudgeRegistry | None = None) -> list[str]:
    """Run-level checks for preflight: every task's selection, and a cap on the judge ledger when any judge is named."""
    if not any(task.options.get("judges") is not None for task in config.tasks.values()):
        return []
    registry = registry or load_judges()
    problems = [problem for task in config.tasks for problem in task_problems(config, task, registry)]
    if not any(cap.ledger == LEDGER for cap in config.caps):
        problems.append("a run that names judges must cap the judge ledger (a caps entry with ledger 'judge')")
    return problems


def select_judge(config: Any, task: str, slot: str = "primary", registry: JudgeRegistry | None = None) -> JudgeSpec:
    """The judge a task's config puts in ``slot``; refuses a selection that breaks any rule."""
    registry = registry or load_judges()
    problems = task_problems(config, task, registry)
    if problems:
        raise JudgeConfigError("; ".join(problems))
    judge_id = configured_judges(config, task).get(slot)
    if judge_id is None:
        raise JudgeConfigError(f"task {task!r} names no {slot} judge")
    return registry.get(judge_id)


def task_judges(config: Any, task: str, *, cache_dir: Path, slot: str = "primary",
                ask: Callable[[JudgeSpec, str], Judge] | None = None,
                registry: JudgeRegistry | None = None) -> dict[str, Judge]:
    """The judges a task's scorer takes, by kind: ``{"incident": ...}`` for ``score_video``'s ``judge``, or
    ``{"fact", "unsupported", "contradiction"}`` for the chat scorer's ``judges``.

    Offline (no ``ask``) each is a :class:`CachedJudge` that never calls anything; in an approved judge stage
    ``ask(spec, kind)`` gives the live judge behind a :class:`RecordingJudge`. The caches live under ``cache_dir``,
    one file per judge and kind; a live stage writes only where Git cannot commit, since a raw reply may quote the
    judged ground-truth or reference text.
    """
    spec = select_judge(config, task, slot, registry)
    cache_dir = Path(cache_dir)
    if ask is not None:
        ensure_private(cache_dir)
    judges: dict[str, Judge] = {}
    for kind in TASK_KINDS[task]:
        cache = JudgeCache(cache_dir / f"{spec.id}.{kind}.jsonl", judge_identity(spec, kind))
        judges[kind] = RecordingJudge(cache, ask(spec, kind)) if ask is not None else CachedJudge(cache)
    return judges

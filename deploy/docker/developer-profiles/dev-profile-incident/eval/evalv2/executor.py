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

"""The live executor: ``run --live`` for Track A (``incident_objects``), Track B (``pipeline``) and C1c
(``dashboard``, "Ask for a chart": text only, no clips) configs.

It wires the pieces that already exist, and decides nothing about content:

    work items -> seeded schedule -> pacer -> adapter (budget reserved or held before every attempt)
               -> append-only artifact store -> checkpoint -> resume

**Before anything is sent** it refuses, naming every reason:

- preflight problems (``budget_mode: hard_stop`` turns caps below the worst case into recorded risks, not problems);
- no approval marker, or one that does not fit the run (:mod:`evalv2.approval`): the config's hash, purpose and
  half, the item count, every cap, the run-wide token total, and any gated profile;
- a pilot that would touch a test-half clip, in the plan or at signing time (:mod:`evalv2.clips`);
- a dirty eval tree (unless ``allow_dirty``), a missing credential, or a resume whose config or inputs changed.

**Every attempt is recorded** in ``attempts.jsonl``: model, prompt SHA-256, request hash (the stored request's
SHA-256, its signed URL redacted), tokens, latency, waiting, outcome and failure kind. The run manifest
(``manifest.start.json``, write-once, and the sealed manifest) pins the eval commit, the config hash, the dataset
hashes, the approval marker, the caps, the pacing, the endpoints by variable name and, for Track B, the agent's
identity.

**Resume** is the same command on the same ``--out``. Final items (an output, valid or not, or a failure V1's
``recovery-v1`` never retries) are skipped and never re-sent; open items (an infrastructure failure, a budget
refusal) are tried again, and each try is recorded. Budget counts resume from the ledger log, Track B's pinned agent
from ``agent.jsonl``, and URLs are signed afresh. A run whose every item is final is sealed; resuming a sealed run
writes nothing.

Layout of ``--out`` (private: it must be ignored by Git or outside the repository):
``store/`` (artifacts, ``runs/<run id>/index.jsonl``), ``manifest.start.json``, ``approvals.jsonl``,
``budget.jsonl``, ``checkpoint.jsonl``, ``attempts.jsonl``, ``agent.jsonl``, ``events.jsonl``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2 import approval as approvals
from evalv2.adapters.base import AdapterConfigError, CallContext, CallResult, PipelineResult
from evalv2.adapters.openai_compat import ENDPOINTS
from evalv2.adapters.registry import build_adapter, build_pipeline_adapter
from evalv2.adapters.vss import AGENT_URL_ENV, DEFAULT_AGENT_URL, AgentDriftError, AgentRefusedError, ProfileMismatchError
from evalv2.clips import ClipError, ClipResolver, missing_r2_env
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetExceeded, BudgetLedger
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.hashing import canonical_json, sha256_json
from evalv2.core.manifest import build_run_manifest, git_info, input_drift
from evalv2.core.pacing import Pacer, gateway_limits
from evalv2.core.private import ensure_private
from evalv2.core.retry import POLICIES
from evalv2.requests.p1 import p1_provenance
from evalv2.requests.vss import ProfileGatedError, profile_for
from evalv2.tasks.base import PredictionRecord, WorkItem

EVAL_DIR = Path(__file__).resolve().parents[1]
EXECUTOR_VERSION = "evalv2-executor-v1"
TRACKS = {"incident_objects": "A", "pipeline": "B", "dashboard": "C"}
SINGLE_CALL = ("A", "C")  # one adapter call per item through an OpenAI-compatible endpoint, paced like Track A
TRACK_B_GAP_S = 20.0  # V1's inter_call_delay_s between analyses (one at a time: an analysis fills 4 in flight itself)
STOPPING = (AgentDriftError, AgentRefusedError, ProfileMismatchError, ProfileGatedError, AdapterConfigError, ClipError)


class LiveRefused(RuntimeError):
    """The run will not start; ``problems`` names every reason. Nothing was sent."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class RunStopped(RuntimeError):
    """The run stopped part-way (agent drift, a refusal, a configuration error); finished items are kept."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json(dict(row)) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def schedule(items: Sequence[WorkItem], subjects: Sequence[str], repeats: int, seed: int) -> list[WorkItem]:
    """The items in V1's seeded, interleaved order (``experiment_runner.schedule``, D10).

    Per repeat the clips are shuffled with ``random.Random(seed + repeat)``; at each clip the subjects run interleaved,
    the first rotating with the position, so no subject always runs first or in one block.
    """
    from experiment_runner import schedule as v1_schedule

    by_key = {(item.subject, item.item_id, item.repeat): item for item in items}
    clips = sorted({item.item_id for item in items})
    order = []
    for repeat, _, clip, k in v1_schedule(list(subjects), clips, repeats, seed):
        item = by_key.pop((subjects[k], clip, repeat), None)
        if item is not None:
            order.append(item)
    if by_key:
        raise ValueError(f"{len(by_key)} work items fall outside the schedule")
    return order


@dataclasses.dataclass
class RunPaths:
    out: Path

    def __getattr__(self, name: str) -> Path:
        files = {"store": "store", "start": "manifest.start.json", "approvals": "approvals.jsonl",
                 "budget": "budget.jsonl", "checkpoint": "checkpoint.jsonl", "attempts": "attempts.jsonl",
                 "agent": "agent.jsonl", "events": "events.jsonl"}
        if name not in files:
            raise AttributeError(name)
        return self.out / files[name]


class _Recording:
    """One item's view of a shared adapter: it keeps the calls' results so their attempts can be recorded."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.id, self.model, self.capabilities = inner, inner.id, inner.model, inner.capabilities
        self.results: list[Any] = []

    def complete(self, request: Mapping[str, Any], ctx: CallContext) -> CallResult:
        result = self.inner.complete(request, ctx)
        self.results.append(result)
        return result

    def analyze(self, incident_id: str, request: Mapping[str, Any], ctx: CallContext) -> PipelineResult:
        result = self.inner.analyze(incident_id, request, ctx)
        self.results.append(result)
        return result


def _track(config: Any) -> str:
    tracks = {TRACKS.get(name) for name in config.tasks}
    if None in tracks:
        raise LiveRefused([f"no live track for tasks {sorted(n for n in config.tasks if n not in TRACKS)}"])
    if len(tracks) != 1:
        raise LiveRefused([("a live run is one track: Track A (incident_objects), Track B (pipeline) or C1c "
                            "(dashboard)")])
    return tracks.pop()


def default_pacer(track: str, *, rpm: int | None = None, parallel: int | None = None) -> Pacer:
    limits = gateway_limits()
    if track == "B":
        return Pacer(rpm=None, parallel=1, tpm={}, min_gap_s=TRACK_B_GAP_S)
    return Pacer(rpm=rpm or limits["rpm"], parallel=min(parallel or limits["parallel"], limits["parallel"]))


class LiveExecutor:
    """One live run of one config into one private directory; :meth:`run` executes or resumes it."""

    def __init__(self, config: Any, datasets: Mapping[str, Any], items: Mapping[str, list[WorkItem]], *, out: Path,
                 marker: Mapping[str, Any], hard_stop: Sequence[str] = (), manifest_clips: Sequence[Mapping[str, Any]],
                 signer: Callable[[str, int], str], pacer: Pacer | None = None,
                 adapter_kwargs: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None,
                 code_dir: Path = EVAL_DIR, allow_dirty: bool = False, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = lambda message: None) -> None:
        self.config, self.datasets, self.items, self.marker = config, datasets, items, dict(marker)
        self.track = _track(config)
        self.paths = RunPaths(Path(out))
        self.hard_stop = list(hard_stop)
        self.environ = os.environ if environ is None else environ
        self.adapter_kwargs = dict(adapter_kwargs or {})
        self.code_dir, self.allow_dirty, self.sleep, self.log = Path(code_dir), allow_dirty, sleep, log
        self.pacer = pacer or default_pacer(self.track)
        self.resolver = ClipResolver(manifest_clips, datasets["split"], purpose=config.purpose, signer=signer,
                                     clock=clock)
        self.run_id = config.experiment
        self.store = ArtifactStore(self.paths.store)
        self._write_lock = threading.Lock()
        self._pinned: dict[str, Any] | None = None
        self._stop = threading.Event()

    # --- checks before anything is sent -----------------------------------------------------------------------------

    def refusals(self) -> list[str]:
        from evalv2 import cli

        config, problems = self.config, []
        needs = cli.worst_cases(config, dict(self.items))
        problems += approvals.run_problems(self.marker, config, self.items, {key[2] for key in needs})
        if config.purpose == "pilot":
            test = set(self.datasets["split"].get("test") or ())
            held_out = sorted({i.item_id for task_items in self.items.values() for i in task_items} & test)
            if held_out or config.split.half != "dev":
                problems.append(f"a pilot never touches the test half; the plan names {held_out[:5]}")
        code = git_info(self.code_dir)
        if code["dirty"] and not self.allow_dirty:
            problems.append(f"the eval tree at {self.code_dir} has uncommitted changes: commit them so the manifest "
                            "pins the code that ran (or pass --allow-dirty, recorded in the manifest)")
        problems += self._credential_problems()
        return problems

    def _credential_problems(self) -> list[str]:
        problems = []
        if self.track in SINGLE_CALL:
            for subject in self.config.subjects:
                endpoint = ENDPOINTS.get(subject.endpoint or "")
                if endpoint is None:
                    problems.append(f"subject {subject.id}: unknown endpoint {subject.endpoint!r}")
                    continue
                for name in (endpoint.base_url_env, endpoint.api_key_env):
                    if name == endpoint.base_url_env and endpoint.default_base_url:
                        continue
                    if not self.environ.get(name):
                        problems.append(f"subject {subject.id}: set {name}")
        missing = missing_r2_env(self.environ) if self.track != "C" else []  # C1c sends no clip
        if missing:
            problems.append(f"R2 is not configured for signing clip URLs: set {missing}")
        return sorted(set(problems))

    # --- the run ----------------------------------------------------------------------------------------------------

    def _manifest(self, datasets_inputs: Mapping[str, str]) -> dict[str, Any]:
        config = self.config
        code = git_info(self.code_dir)
        extra: dict[str, Any] = {
            "executor": EXECUTOR_VERSION, "track": self.track, "purpose": config.purpose, "half": config.split.half,
            "budget_mode": config.budget_mode, "hard_stop": self.hard_stop, "allow_dirty": self.allow_dirty,
            "dirty_at_start": code["dirty"],
            "approval": {key: self.marker.get(key) for key in ("sha256", "experiment", "config_sha256", "purpose",
                                                                "half", "approved_by", "approved_at", "max_items",
                                                                "max_total_tokens", "caps", "enabled_profiles",
                                                                "judge", "note")},
            "caps": {"config": [cap.model_dump(mode="json") for cap in config.caps],
                     "approval": [dataclasses.asdict(cap) for cap in approvals.marker_caps(self.marker)]},
            "plan": {name: {"items": len(task_items), "keys_sha256": sha256_json(sorted(i.key for i in task_items))}
                     for name, task_items in self.items.items()},
            "schedule_seed": config.seed, "retry_policy": dataclasses.asdict(POLICIES[config.retry_policy]),
            "pacing": self.pacer.settings(),
            "endpoints": self._endpoints(),
            "requests": self._request_provenance(),
        }
        if self.track != "C":
            extra["clip_urls"] = {"signer": "r2 presigned GET (read-only)", "ttl_s": self.resolver.ttl_s,
                                  "refresh_margin_s": self.resolver.margin_s}
        return build_run_manifest(run_id=self.run_id, config=config.model_dump(mode="json"), inputs=datasets_inputs,
                                  code_dir=self.code_dir, extra=extra)

    def _endpoints(self) -> dict[str, Any]:
        if self.track == "B":
            return {"agent_url": self.environ.get(AGENT_URL_ENV) or DEFAULT_AGENT_URL, "agent_url_env": AGENT_URL_ENV}
        return {s.id: {"endpoint": s.endpoint, "base_url_env": ENDPOINTS[s.endpoint].base_url_env,
                       "api_key_env": ENDPOINTS[s.endpoint].api_key_env} for s in self.config.subjects}

    def _request_provenance(self) -> dict[str, Any]:
        if self.track == "A":
            prompt_id = self.config.tasks["incident_objects"].options.get("prompt_id")
            return {"p1": p1_provenance(prompt_id)}
        if self.track == "C":
            from evalv2.requests.chart import chart_provenance

            return {"chart": chart_provenance()}
        return {s.id: profile_for(s.profile, s.request).to_dict() for s in self.config.subjects}

    def _start(self) -> None:
        """Write the start manifest once, or check that a resume is the same run."""
        manifest = self._manifest(self.datasets.get("inputs", {}))
        if self.paths.start.exists():
            recorded = json.loads(self.paths.start.read_text(encoding="utf-8"))
            problems = []
            if recorded["config_sha256"] != manifest["config_sha256"]:
                problems.append("the config changed since this run started (start a new --out for a new config)")
            problems += input_drift(recorded["inputs"], manifest["inputs"])
            if recorded.get("plan") != manifest["plan"]:
                problems.append("the work items changed since this run started")
            if problems:
                raise LiveRefused([f"cannot resume {self.paths.out}: {p}" for p in problems])
        else:
            self.paths.out.mkdir(parents=True, exist_ok=True)
            self.paths.start.write_bytes(canonical_json(manifest))
        seen = {row.get("sha256") for row in _read_jsonl(self.paths.approvals)}
        if self.marker.get("sha256") not in seen:
            _append(self.paths.approvals, {"at": _now(), **{k: v for k, v in self.marker.items() if k != "path"}})
        pins = _read_jsonl(self.paths.agent)
        self._pinned = pins[-1]["pinned"] if pins else None

    def _ledger(self) -> BudgetLedger:
        caps = [*self.config.budget_caps(), *approvals.marker_caps(self.marker)]
        return BudgetLedger(caps, self.paths.budget)

    def _adapters(self) -> dict[str, Any]:
        adapters: dict[str, Any] = {}
        kwargs = self.adapter_kwargs
        for subject in self.config.subjects:
            if self.track in SINGLE_CALL:
                adapters[subject.id] = build_adapter(subject, environ=self.environ, **kwargs)
            else:
                adapter = build_pipeline_adapter(subject, environ=self.environ, **kwargs)
                adapters[subject.id] = adapter
        if self.track == "B":  # one agent serves every subject: one pin, shared, and restored on resume
            shared = self._pinned
            for adapter in adapters.values():
                adapter.pinned = shared
        return adapters

    def _task(self, name: str) -> Any:
        from evalv2.tasks.dashboard.runner import DashboardTask
        from evalv2.tasks.incident_objects.runner import IncidentObjectsTask
        from evalv2.tasks.pipeline.runner import PipelineTask

        if name == "dashboard":
            return DashboardTask()
        if name == "incident_objects":
            return IncidentObjectsTask(video_url=self.resolver.url, video_duration=self.resolver.duration)
        return PipelineTask(video_url=self.resolver.url, video_duration=self.resolver.duration,
                            enabled_profiles=self.marker.get("enabled_profiles", ()))

    def _precheck(self, item: WorkItem, ledger: BudgetLedger) -> str | None:
        """Why an item cannot be sent now within the caps (it is then recorded open, never paced or sent)."""
        try:
            if self.track in SINGLE_CALL:
                from evalv2.tasks.dashboard import runner as dashboard_runner
                from evalv2.tasks.incident_objects import runner as incident_runner

                task = dashboard_runner if self.track == "C" else incident_runner
                ledger.check(task.LEDGER, subject=item.subject, task=item.task, attempts=1,
                             tokens=task.TOKENS_PER_ATTEMPT)
            else:
                for name, (attempts, tokens) in profile_for(item.spec.get("profile"),
                                                            item.spec.get("overrides")).worst_case().items():
                    ledger.check(name, subject=item.subject, task=item.task, attempts=attempts, tokens=tokens)
        except BudgetExceeded as exc:
            return str(exc)
        return None

    def _record(self, item: WorkItem, record: PredictionRecord, checkpoint: Checkpoint,
                attempts: Sequence[Mapping[str, Any]]) -> None:
        ref = self.store.put("prediction-record", dataclasses.asdict(record))
        with self._write_lock:
            for row in attempts:
                _append(self.paths.attempts, row)
            self.store.run(self.run_id).record(item.key, ref, outcome=record.outcome, failure_kind=record.failure_kind,
                                               subject=item.subject, item_id=item.item_id, repeat=item.repeat)
            checkpoint.record(item.key, record.outcome, ref=str(ref), failure_kind=record.failure_kind,
                              subject=item.subject, item_id=item.item_id, repeat=item.repeat,
                              prediction_ref=record.prediction_ref)

    def _attempt_rows(self, item: WorkItem, results: Sequence[Any], record: PredictionRecord) -> list[dict[str, Any]]:
        base = {"work_key": item.key, "task": item.task, "subject": item.subject, "model": item.spec.get("model"),
                "item_id": item.item_id, "repeat": item.repeat}
        rows = []
        if self.track in SINGLE_CALL:
            stages = record.provenance.get("stages") or {}
            prompt = (stages.get("p1") or stages.get("chart") or {}).get("prompt_sha256")
            for result in results:
                request_sha = result.request_ref.sha256
                for attempt in result.attempts:
                    usage = {}
                    if attempt.get("response_ref"):
                        body = self.store.get(ArtifactRef.parse(attempt["response_ref"]))
                        usage = (body or {}).get("usage") or {}
                    timing = attempt.get("timing") or {}
                    kind = attempt.get("failure")
                    rows.append({**base, "attempt": attempt["attempt"], "prompt_sha256": prompt,
                                 "request_sha256": request_sha, "status": attempt.get("status"),
                                 "outcome": "ok" if kind is None else kind, "failure_kind": kind,
                                 "tokens": {"prompt": usage.get("prompt_tokens"),
                                            "completion": usage.get("completion_tokens"),
                                            "total": usage.get("total_tokens")},
                                 "latency_ms": timing.get("wall_ms"), "waited_ms": timing.get("waited_ms"),
                                 "host_suspended": timing.get("host_suspended"),
                                 "response_ref": attempt.get("response_ref"), "at": _now()})
            return rows
        for result in results:
            provenance = result.provenance or {}
            p1 = (provenance.get("stages") or {}).get("p1") or {}
            rows.append({**base, "attempt": 1, "prompt_sha256": p1.get("prompt_sha256"),
                         "prompts": provenance.get("prompts"),
                         "request_sha256": ArtifactRef.parse(result.request_ref).sha256, "status": result.status,
                         "outcome": result.outcome, "failure_kind": record.failure_kind,
                         "tokens": {ledger: dict(row) for ledger, row in result.charged.items()},
                         "latency_ms": round(result.elapsed_ms, 1), "p1_attempts": len(result.p1_attempts),
                         "response_ref": result.response_ref, "at": _now()})
        return rows

    def _execute(self, item: WorkItem, adapters: Mapping[str, Any], ledger: BudgetLedger,
                 checkpoint: Checkpoint) -> None:
        if self._stop.is_set():
            return
        try:
            self._execute_one(item, adapters, ledger, checkpoint)
        except BaseException:
            self._stop.set()  # no queued item starts after a failure that stops the run
            raise

    def _execute_one(self, item: WorkItem, adapters: Mapping[str, Any], ledger: BudgetLedger,
                     checkpoint: Checkpoint) -> None:
        refused = self._precheck(item, ledger)
        if refused is not None:
            record = PredictionRecord(item.key, item.task, item.subject, item.item_id, item.repeat,
                                      outcome="budget_exceeded", failure={"kind": "budget_exceeded", "detail": refused})
            self._record(item, record, checkpoint, [{"work_key": item.key, "subject": item.subject,
                                                     "item_id": item.item_id, "repeat": item.repeat,
                                                     "outcome": "budget_exceeded", "failure_kind": "budget_exceeded",
                                                     "detail": refused, "sent": False, "at": _now()}])
            return
        model = str(item.spec.get("model"))
        recording = _Recording(adapters[item.subject])
        ticket = self.pacer.acquire(model)

        def sleep(seconds: float) -> None:  # a retry's backoff, then the request limit (the slot is kept)
            self.sleep(seconds)
            self.pacer.retry_gate(model)

        ctx = CallContext(store=self.store, ledger=ledger, policy=POLICIES[self.config.retry_policy],
                          subject=item.subject, task=item.task, sleep=sleep)
        tokens: int | None = None
        try:
            try:
                record = self._task(item.task).execute(item, recording, ctx)
            except BudgetExceeded as exc:  # Track B's hold: refused before the request was sent
                record = PredictionRecord(item.key, item.task, item.subject, item.item_id, item.repeat,
                                          outcome="budget_exceeded",
                                          failure={"kind": "budget_exceeded", "detail": str(exc)})
            tokens = (record.usage or {}).get("total_tokens") if self.track in SINGLE_CALL else None
        finally:
            ticket.done(tokens)
            self._save_pin(adapters)
        self._record(item, record, checkpoint, self._attempt_rows(item, recording.results, record))
        self.log(f"{item.subject} {item.item_id} #{item.repeat}: {record.outcome}")

    def _save_pin(self, adapters: Mapping[str, Any]) -> None:
        if self.track != "B":
            return
        pinned = next((a.pinned for a in adapters.values() if a.pinned is not None), None)
        with self._write_lock:
            if pinned is not None and pinned != self._pinned:
                for adapter in adapters.values():
                    adapter.pinned = pinned
                self._pinned = json.loads(json.dumps(pinned))
                _append(self.paths.agent, {"at": _now(), "pinned": self._pinned})

    def run(self) -> dict[str, Any]:
        """Execute every open item (or resume), then seal the run when every item is final."""
        run = self.store.run(self.run_id)
        if run.sealed:
            return {"run_id": self.run_id, "sealed": True, "resumed": True, "sent_items": 0,
                    "manifest_ref": str(run.manifest_ref())}
        refused = self.refusals()
        if refused:
            raise LiveRefused(refused)
        ensure_private(self.paths.out)
        self._start()
        ledger, checkpoint = self._ledger(), Checkpoint(self.paths.checkpoint)
        adapters = self._adapters()
        subjects = [s.id for s in self.config.subjects]
        order = [item for name in self.items
                 for item in schedule(self.items[name], [s for s in subjects
                                                         if any(i.subject == s for i in self.items[name])],
                                      self.config.repeats, self.config.seed)]
        todo = [item for item in order if not checkpoint.is_final(item.key)]
        _append(self.paths.events, {"at": _now(), "event": "start" if len(todo) == len(order) else "resume",
                                    "open": len(todo), "items": len(order), "approval": self.marker.get("sha256")})
        stopped: BaseException | None = None
        with ThreadPoolExecutor(max_workers=self.pacer.parallel) as pool:
            futures = []
            for item in todo:
                futures.append(pool.submit(self._execute, item, adapters, ledger, checkpoint))
            done, pending = wait(futures, return_when=FIRST_EXCEPTION)
            for future in done:
                if future.exception() is not None:
                    stopped = future.exception()
                    self._stop.set()
                    break
            for future in pending:
                future.cancel()
            for future in pending:
                if not future.cancelled():
                    try:
                        future.result()
                    except BaseException as exc:  # a second failure while stopping: keep the first
                        stopped = stopped or exc
        if stopped is not None:
            _append(self.paths.events, {"at": _now(), "event": "stopped", "error": type(stopped).__name__,
                                        "detail": str(stopped)[:500]})
            if isinstance(stopped, STOPPING):
                raise RunStopped(f"{type(stopped).__name__}: {stopped}") from stopped
            raise stopped
        checkpoint = Checkpoint(self.paths.checkpoint)
        open_items = [item.key for item in order if not checkpoint.is_final(item.key)]
        outcomes = Counter(checkpoint.final_result(item.key)["outcome"] for item in order
                           if checkpoint.is_final(item.key))
        summary = {"run_id": self.run_id, "track": self.track, "items": len(order), "sent_items": len(todo),
                   "final": len(order) - len(open_items), "open": len(open_items), "outcomes": dict(outcomes),
                   "budget": ledger.snapshot(), "signed_urls": self.resolver.signed, "paced_wait_s":
                   round(self.pacer.waited_s, 3), "hard_stop": self.hard_stop, "sealed": False}
        if not open_items:
            start = json.loads(self.paths.start.read_text(encoding="utf-8"))
            ref = run.seal({**start, "sealed_at": _now(), "agent": self._pinned, "budget": ledger.snapshot(),
                            "outcomes": dict(outcomes), "approvals": [row.get("sha256") for row in
                                                                       _read_jsonl(self.paths.approvals)]})
            summary.update(sealed=True, manifest_ref=str(ref))
        _append(self.paths.events, {"at": _now(), "event": "end", "open": len(open_items), "sealed": summary["sealed"]})
        return summary


def load_manifest_clips() -> list[dict[str, Any]]:
    from evalv2.datasets import registry as dataset_registry

    manifest = dataset_registry.resolve("dataset-manifest-v1")
    clips: list[dict[str, Any]] = json.loads(manifest.path.read_text(encoding="utf-8"))["clips"]
    return clips

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

"""``score``: score a live run's stored predictions, with the judge and embeddings cached so a re-score is offline.

It reads the run directory ``run --live`` wrote (final items only: an open item has no result yet) and scores
each prediction with the existing scorers, exactly as V1 scores (a run without model output is scored as V1's empty
prediction, and the aggregators leave it out by outcome):

- **Track A** (``incident_objects``): incident fields and objects (A6, ``score_video``), aggregated per subject
  (``aggregate``: V1's continuity view plus V2's type, severity, timestamp and attribute metrics).
- **Track B** (``pipeline``): the three stages of each run, ``p1``, ``p1_agent`` and ``final`` (``score_stage``),
  aggregated per subject and stage, the zoom effect (``p1_agent`` to ``final``, paired), the crop and zoom cap
  summary (``cap_summary``), and the crops against box references (J5) when the config names a ``box_references``
  dataset (RA1); otherwise crops are reported unscored.

**Similarity** is MiniLM (``sentence-transformers/all-MiniLM-L6-v2``, V1's matcher) computed in-process from the local
model cache (``HF_HUB_OFFLINE=1``: nothing is downloaded), every vector cached in ``scoring/embeddings.jsonl``.

**Judging** goes through the judge registry (``tasks/judges.json``; the task's ``options.judges`` slot, primary by
default). A live score needs ``EVALV2_ALLOW_LIVE=1`` and an approval marker with a ``judge`` budget; every judge
attempt is reserved before it is sent on two ledgers at once: the judge's own hard ceilings in a log shared by every
scoring stage on this machine (``--judge-ledger-dir``, so the approved 2,000 calls and 3 M tokens hold across runs),
and the run's judge cap and the marker's judge budget in the run's ``scoring/judge-budget.jsonl``. Verdicts go to the
judge cache (``scoring/judge-cache/``).

**C1c** (``dashboard``): :func:`run_dashboard_score` scores the stored chart replies against the dashboard references
with code only (no judge, no embedding): spec outcomes, per-field accuracy, refusals and, from the reference set's
frozen corpus snapshot, figures (the production SQL in the SQL harness, or the recount).

**Offline** (``--offline``) uses the caches only, under the network guard: a verdict or vector that is not cached
leaves its field unscored, never failed, and nothing is sent. ``--offline --embed-local`` also computes missing
vectors with the local MiniLM (still under the network guard, from the local model cache, and cached as above), so
the object matching is scored while judge fields stay cache-only: no judge budget is needed.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap, Reservation
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.hashing import canonical_json, sha256_json
from evalv2.core.private import ensure_private
from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.aggregate import aggregate, severity_metrics, with_failed_runs
from evalv2.tasks.incident_objects.scoring import SimilarityUnavailableError, embedder_similarity, score_video
from evalv2.tasks.judging import Judge

SCORE_VERSION = "evalv2-score-stage-v3"  # v2: severity-v2-mae, outputs under scoring/<version>/; v3: Track B stages keep failed runs in severity
# The files whose bytes define the scores; their SHA-256 and the eval commit go into every summary.
SCORER_FILES = ("evalv2/score_stage.py", "evalv2/tasks/incident_objects/aggregate.py",
                "evalv2/tasks/incident_objects/scoring.py", "evalv2/tasks/pipeline/scoring.py")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # methodology.EMBEDDING_MODEL (V1's matcher)
VIDEO_BLOCKS = ("incident_field_scores", "entities", "instruments", "assets", "scoring_errors")


class EmbeddingCache:
    """An append-only JSONL of vectors keyed by model and text, so a re-score needs no embedder."""

    def __init__(self, path: Path, model: str = EMBEDDING_MODEL) -> None:
        self.path, self.model = Path(path), model
        self._vectors: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._vectors[row["key"]] = row["vector"]

    def key(self, text: str) -> str:
        return sha256_json({"model": self.model, "text": text})

    def get(self, text: str) -> list[float] | None:
        return self._vectors.get(self.key(text))

    def put(self, text: str, vector: Sequence[float]) -> None:
        key = self.key(text)
        with self._lock:
            if key in self._vectors:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                handle.write(canonical_json({"key": key, "model": self.model, "vector": [float(x) for x in vector]})
                             + b"\n")
            self._vectors[key] = [float(x) for x in vector]

    def __len__(self) -> int:
        return len(self._vectors)


def cached_embedder(cache: EmbeddingCache, compute: Callable[[list[str]], list[list[float]]] | None
                    ) -> Callable[[list[str]], list[list[float]]]:
    """Vectors from the cache, computing (and caching) the misses; offline (no ``compute``) a miss is unscored."""

    def embed(texts: list[str]) -> list[list[float]]:
        missing = sorted({text for text in texts if cache.get(text) is None})
        if missing:
            if compute is None:
                raise SimilarityUnavailableError(f"not embedded: {len(missing)} texts have no cached vector (offline)")
            for text, vector in zip(missing, compute(missing), strict=True):
                cache.put(text, vector)
        return [list(cache.get(text) or []) for text in texts]

    return embed


def local_minilm() -> Callable[[list[str]], list[list[float]]]:
    """MiniLM in this process from the local Hugging Face cache (no download), encoded as V1's server encodes."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    loaded: list[Any] = []

    def compute(texts: list[str]) -> list[list[float]]:
        if not loaded:
            from sentence_transformers import SentenceTransformer

            loaded.append(SentenceTransformer(EMBEDDING_MODEL))
        vectors: list[list[float]] = loaded[0].encode(texts, normalize_embeddings=False).tolist()
        return vectors

    return compute


@dataclasses.dataclass
class _StackedReservation:
    parts: list[Reservation]


class StackedLedger:
    """Several ledgers charged as one: an attempt is reserved on every ledger, or on none (``BudgetExceeded``)."""

    def __init__(self, ledgers: Sequence[BudgetLedger]) -> None:
        self.ledgers = list(ledgers)
        self._lock = threading.Lock()

    def reserve(self, ledger: str, *, subject: str, task: str, tokens_bound: int) -> _StackedReservation:
        with self._lock:
            for book in self.ledgers:
                book.check(ledger, subject=subject, task=task, attempts=1, tokens=tokens_bound)
            return _StackedReservation([book.reserve(ledger, subject=subject, task=task, tokens_bound=tokens_bound)
                                        for book in self.ledgers])

    def settle(self, reservation: _StackedReservation, *, tokens_used: int | None) -> None:
        for book, part in zip(self.ledgers, reservation.parts, strict=True):
            book.settle(part, tokens_used=tokens_used)

    def snapshot(self) -> list[dict[str, Any]]:
        return [book.snapshot() for book in self.ledgers]


def final_predictions(out: Path) -> list[dict[str, Any]]:
    """Every final item of a run directory, in a stable order: ``{task, subject, item_id, repeat, outcome, ...}``."""
    store = ArtifactStore(Path(out) / "store")
    checkpoint = Checkpoint(Path(out) / "checkpoint.jsonl")
    rows = []
    for key in sorted(checkpoint.final_keys()):
        entry = checkpoint.final_result(key) or {}
        record = store.get(ArtifactRef.parse(entry["ref"]))
        rows.append({**record, "work_key": key})
    rows.sort(key=lambda r: (r["subject"], r["item_id"], r["repeat"]))
    return rows


def _video(clip: str, subject: str, repeat: int, outcome: str, gt_entry: Mapping[str, Any],
           prediction: Mapping[str, Any], scored: Mapping[str, Any], stage: str | None = None) -> dict[str, Any]:
    video = {"incident_id": clip, "subject": subject, "repeat": repeat, "outcome": outcome,
             "ground_truth": gt_entry["p1_shaped"], "prediction": dict(prediction),
             **{block: scored[block] for block in VIDEO_BLOCKS}}
    if stage is not None:
        video["stage"] = stage
    return video


def score_run(config: Any, out: Path, *, gt_index: Mapping[str, Any], sheets: Mapping[str, list[dict[str, Any]]],
              judge: Judge, embed: Callable[[list[str]], list[list[float]]],
              box_references: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Score every final prediction of the run in ``out``; returns ``{"summary", "videos"}``."""
    store = ArtifactStore(Path(out) / "store")
    rows = final_predictions(out)
    gt_store = v1.gt_store(dict(sheets))
    similarity = embedder_similarity(embed)
    by_clip = gt_index["by_incident_id"]
    videos: list[dict[str, Any]] = []
    tasks = sorted({row["task"] for row in rows})
    summary: dict[str, Any] = {"score_version": SCORE_VERSION, "scorer": scorer_provenance(),
                               "experiment": config.experiment, "tasks": tasks, "final_items": len(rows),
                               "subjects": {}}
    if "incident_objects" in tasks:
        per_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in (r for r in rows if r["task"] == "incident_objects"):
            clip, run_key = row["item_id"], f"{row['subject']}#{row['repeat']}"
            artifact = store.get(ArtifactRef.parse(row["prediction_ref"])) if row.get("prediction_ref") else None
            prediction = artifact["prediction"] if artifact else v1.empty_prediction()
            v1.put_prediction(gt_store, clip, run_key, dict(prediction))
            gt_entry = by_clip[clip]
            scored = score_video(gt_store, clip, run_key, similarity=similarity, judge=judge,
                                 out_of_taxonomy=v1.is_out_of_taxonomy({"ground_truth": gt_entry["p1_shaped"]}))
            video = _video(clip, row["subject"], row["repeat"], row["outcome"], gt_entry, prediction, scored)
            per_subject[row["subject"]].append(video)
            videos.append(video)
        for subject, subject_videos in sorted(per_subject.items()):
            summary["subjects"][subject] = {"incident_objects": aggregate(subject_videos)}
    if "pipeline" in tasks:
        _score_pipeline(rows, store, gt_store, by_clip, similarity, judge, box_references, summary, videos)
    errors = [e["error"] for v in videos for e in v["scoring_errors"]]
    summary["unscored"] = {"fields": len(errors), "not_judged": sum(e.startswith("not judged") for e in errors),
                           "not_embedded": sum("not embedded" in e for e in errors)}
    return {"summary": summary, "videos": videos}


def _score_pipeline(rows: Sequence[Mapping[str, Any]], store: ArtifactStore, gt_store: Any,
                    by_clip: Mapping[str, Any], similarity: Any, judge: Judge,
                    box_references: Sequence[Mapping[str, Any]] | None, summary: dict[str, Any],
                    videos: list[dict[str, Any]]) -> None:
    from evalv2.tasks.crops import scoring as crop_scoring
    from evalv2.tasks.pipeline import crops
    from evalv2.tasks.pipeline.runner import STAGES
    from evalv2.tasks.pipeline.scoring import paired_effect, score_stage

    stage_videos: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    scored_by_stage: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    artifacts: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    crop_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    caption: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in (r for r in rows if r["task"] == "pipeline"):
        subject, clip, run_key = row["subject"], row["item_id"], f"{row['subject']}#{row['repeat']}"
        gt_entry = by_clip[clip]
        out_of_taxonomy = v1.is_out_of_taxonomy({"ground_truth": gt_entry["p1_shaped"]})
        if not row.get("prediction_ref"):  # no reply: V1 scores the empty prediction, left out by outcome
            prediction = v1.empty_prediction()
            v1.put_prediction(gt_store, clip, f"{run_key}:p1", dict(prediction))
            scored = score_video(gt_store, clip, f"{run_key}:p1", similarity=similarity, judge=judge,
                                 out_of_taxonomy=out_of_taxonomy)
            video = _video(clip, subject, row["repeat"], row["outcome"], gt_entry, prediction, scored, "p1")
            stage_videos[subject]["p1"].append(video)
            videos.append(video)
            continue
        artifact = store.get(ArtifactRef.parse(row["prediction_ref"]))
        artifacts[subject].append(artifact)
        flags = artifact.get("caption_pass") or {}
        caption[subject]["runs"] += 1
        caption[subject]["ran"] += int(bool(flags.get("ran")))
        caption[subject]["chunking_enabled"] += int(bool(flags.get("chunking_enabled")))
        for stage in STAGES:
            scored = score_stage(gt_store, artifact, stage, incident_id=clip, run_id=run_key, similarity=similarity,
                                 judge=judge, out_of_taxonomy=out_of_taxonomy)
            if scored is None:
                continue
            outcome = row["outcome"] if stage == "p1" else "scored"
            video = _video(clip, subject, row["repeat"], outcome, gt_entry, artifact["predictions"][stage], scored,
                           stage)
            stage_videos[subject][stage].append(video)
            scored_by_stage[subject][stage][f"{clip}#{row['repeat']}"] = scored
            videos.append(video)
        if box_references is not None:
            entries = crops.score_run(artifact, scored_by_stage[subject]["final"].get(f"{clip}#{row['repeat']}"),
                                      crops.reference_index(box_references, clip))
            crop_rows[subject].extend(entries)
    for subject in sorted(set(stage_videos) | set(artifacts)):
        block: dict[str, Any] = {"stages": {stage: aggregate(stage_videos[subject][stage])
                                            for stage in STAGES if stage_videos[subject][stage]},
                                 "zoom_effect": paired_effect(scored_by_stage[subject]["p1_agent"],
                                                              scored_by_stage[subject]["final"]),
                                 "caps": crops.cap_summary(artifacts[subject]),
                                 "caption_pass": dict(caption[subject])}
        for stage, stage_block in block["stages"].items():  # failed runs stay in every stage's severity
            if stage != "p1":
                stage_block["severity"] = severity_metrics(with_failed_runs(
                    stage_videos[subject][stage], stage_videos[subject]["p1"], stage))
        block["crops"] = (crop_scoring.aggregate(crop_rows[subject]) if box_references is not None
                          else {"unscored": "no box_references dataset in the config (RA1 not registered)"})
        summary["subjects"].setdefault(subject, {})["pipeline"] = block


def with_judge_endpoint(config: Any, task: str, slot: str, judge_id: str) -> Any:
    """The config with ``slot``'s judge swapped for ``judge_id``: the same model and role served elsewhere.

    The pre-registered judge is a model, a prompt and settings; only where it is served may change, so a judge with a
    different model, role, settings or kinds is refused. The summary names the judge id that scored."""
    from evalv2.tasks.judges import configured_judges, load_judges

    registry = load_judges()
    configured = configured_judges(config, task)
    if slot not in configured:
        raise ScoreRefused([f"task {task!r} names no {slot} judge to serve elsewhere"])
    current = registry.get(configured[slot])
    chosen = registry.get(judge_id)
    differs = [name for name in ("model", "role", "kinds", "parse_retries", "max_prompt_bytes")
               if getattr(current, name) != getattr(chosen, name)]
    if dict(current.settings) != dict(chosen.settings):
        differs.append("settings")
    if differs and not (differs == ["model"] and _same_weights(current.model, chosen.model)):
        raise ScoreRefused([f"judge {judge_id} is not {current.id} served elsewhere: it differs in {differs}"])
    options = dict(config.tasks[task].options)
    options["judges"] = {**dict(options.get("judges") or {}), slot: judge_id}
    tasks = {**config.tasks, task: config.tasks[task].model_copy(update={"options": options})}
    return config.model_copy(update={"tasks": tasks})


# Endpoint ids for one model's weights where two endpoints name it differently (catalog vs OpenRouter).
SAME_WEIGHTS = {frozenset({"mistralai/mistral-large-4-1t-a52b", "mistralai/mistral-large-4-0"})}


def _same_weights(a: str, b: str) -> bool:
    return a == b or frozenset({a, b}) in SAME_WEIGHTS


def scorer_provenance() -> dict[str, Any]:
    """The eval commit (and whether the tree was dirty) and the SHA-256 of every scorer file."""
    import hashlib

    from evalv2.core.manifest import git_info
    from evalv2.executor import EVAL_DIR

    return {**git_info(EVAL_DIR), "files_sha256": {name: hashlib.sha256((EVAL_DIR / name).read_bytes()).hexdigest()
                                                   for name in SCORER_FILES}}


def output_dir(out: Path, slot: str) -> Path:
    """``scoring/<score version>/<slot>``: a new score version never overwrites an earlier one's outputs."""
    return Path(out) / "scoring" / SCORE_VERSION / slot


def write_outputs(out: Path, slot: str, result: Mapping[str, Any], mode: str) -> Path:
    """``scoring/<version>/<slot>/<mode>-summary.json`` and ``<mode>-videos.jsonl`` (private: they quote the GT).

    Outputs of score versions before v2 sit in ``scoring/<slot>/`` and are left as they are."""
    directory = output_dir(out, slot)
    ensure_private(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{mode}-summary.json").write_text(json.dumps(result["summary"], indent=1, sort_keys=True) + "\n",
                                                    encoding="utf-8")
    with (directory / f"{mode}-videos.jsonl").open("wb") as handle:
        for video in result["videos"]:
            handle.write(canonical_json(video) + b"\n")
    return directory


def judge_run_caps(config: Any, marker: Mapping[str, Any] | None) -> list[Cap]:
    """The run's judge caps: the config's judge caps, and the marker's judge budget when there is one."""
    caps = [cap for cap in config.budget_caps() if cap.ledger == "judge"]
    judge = (marker or {}).get("judge")
    if judge:
        caps.append(Cap("judge", judge["max_requests"], judge["max_tokens"]))
    return caps


def marker_problems(marker: Mapping[str, Any], config: Any) -> list[str]:
    """Why a marker does not approve this run's live judge stage."""
    from evalv2.approval import config_sha256

    problems = []
    if marker.get("experiment") != config.experiment:
        problems.append(f"the marker approves {marker.get('experiment')!r}, not {config.experiment!r}")
    if marker.get("config_sha256") != config_sha256(config):
        problems.append("the marker pins another config (an edited config needs a new approval)")
    judge = marker.get("judge")
    if not judge:
        problems.append("the marker approves no judge budget (a judge block with max_requests and max_tokens)")
    else:
        for cap in config.caps:
            if cap.ledger == "judge" and ((cap.max_attempts or 0) > judge["max_requests"]
                                          or (cap.max_tokens or 0) > judge["max_tokens"]):
                problems.append(f"the config's judge cap exceeds the approved {judge['max_requests']} requests and "
                                f"{judge['max_tokens']} tokens")
    return problems


class ScoreRefused(RuntimeError):
    """The score will not start; nothing was sent."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


JUDGE_RPM = 40  # assumed for the catalog judge until the probe measures it (readiness doc, section 4)


def run_score(config: Any, out: Path, *, datasets: Mapping[str, Any], sheets: Mapping[str, list[dict[str, Any]]],
              offline: bool, slot: str = "primary", marker: Mapping[str, Any] | None = None,
              embed_local: bool = False, judge_override: str | None = None,
              judge_ledger_dir: Path | None = None, environ: Mapping[str, str] | None = None, transport: Any = None,
              compute: Callable[[list[str]], list[list[float]]] | None = None,
              sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None
              ) -> dict[str, Any]:
    """Score the run in ``out`` (see the module docstring); ``transport``, ``compute``, ``sleep`` exist for tests."""
    import time

    from evalv2.core.netguard import no_network
    from evalv2.core.pacing import Pacer
    from evalv2.core.retry import POLICIES
    from evalv2.judge_stage import live_factory
    from evalv2.tasks.judges import select_judge, task_judges

    out = Path(out)
    tasks = [name for name in config.tasks if name in ("incident_objects", "pipeline")]
    if len(tasks) != 1:
        raise ScoreRefused([(f"score runs one Track A or Track B task (a dashboard config: run_dashboard_score); "
                             f"the config names {sorted(config.tasks)}")])
    task = tasks[0]
    if not (out / "checkpoint.jsonl").exists():
        raise ScoreRefused([f"{out} holds no run (no checkpoint.jsonl)"])
    ensure_private(out)
    if judge_override:
        config = with_judge_endpoint(config, task, slot, judge_override)
    spec = select_judge(config, task, slot)
    cache_dir = out / "scoring" / "judge-cache"
    vectors = EmbeddingCache(out / "scoring" / "embeddings.jsonl")
    box = (datasets.get("box_references") or {}).get("records")
    if offline:
        with no_network():
            judges = task_judges(config, task, cache_dir=cache_dir, slot=slot)
            local = (compute or local_minilm()) if embed_local else None
            result = score_run(config, out, gt_index=datasets["incident_gt"], sheets=sheets, judge=judges["incident"],
                               embed=cached_embedder(vectors, local), box_references=box)
        misses = len(getattr(judges["incident"], "misses", []))
        result["summary"]["mode"] = {"offline": True, "judge": spec.id, "slot": slot, "judge_cache_misses": misses,
                                     "embed_local": embed_local}
        write_outputs(out, slot, result, "offline")
        return result
    environ = os.environ if environ is None else environ
    problems = [] if marker is not None else ["a live score needs an approval marker with a judge budget (--approval)"]
    if marker is not None:
        problems += marker_problems(marker, config)
    from evalv2.adapters.openai_compat import ENDPOINTS

    endpoint = ENDPOINTS[spec.endpoint]
    if not environ.get(endpoint.api_key_env):
        problems.append(f"judge {spec.id}: set {endpoint.api_key_env}")
    if judge_ledger_dir is None:
        problems.append("a live score needs --judge-ledger-dir (the judge ledger shared by every scoring stage)")
    if problems:
        raise ScoreRefused(problems)
    assert judge_ledger_dir is not None
    ensure_private(judge_ledger_dir)
    shared = BudgetLedger(spec.caps(), Path(judge_ledger_dir) / f"{spec.id}.jsonl")
    run_ledger = BudgetLedger(judge_run_caps(config, marker), out / "scoring" / "judge-budget.jsonl")
    ledger = StackedLedger([shared, run_ledger])
    sleep = sleep or time.sleep
    pacer = Pacer(rpm=JUDGE_RPM, parallel=1, tpm={}, clock=clock or time.monotonic, sleep=sleep)

    def paced_retry_sleep(seconds: float) -> None:
        sleep(seconds)
        pacer.retry_gate(spec.model)

    ask = live_factory(store=ArtifactStore(out / "store"), ledger=ledger,  # type: ignore[arg-type]
                       policy=POLICIES[config.retry_policy], subject=config.experiment, task=f"score:{task}",
                       transport=transport, environ=environ, sleep=paced_retry_sleep)

    def paced_ask(judge_spec: Any, kind: str) -> Judge:
        live = ask(judge_spec, kind)

        def call(expected: str | None, predicted: str | None) -> Any:
            ticket = pacer.acquire(judge_spec.model)
            try:
                return live(expected, predicted)
            finally:
                ticket.done(None)

        return call

    judges = task_judges(config, task, cache_dir=cache_dir, slot=slot, ask=paced_ask)
    embed = cached_embedder(vectors, compute or local_minilm())
    result = score_run(config, out, gt_index=datasets["incident_gt"], sheets=sheets, judge=judges["incident"],
                       embed=embed, box_references=box)
    result["summary"]["mode"] = {"offline": False, "judge": spec.id, "slot": slot,
                                 "judge_calls": getattr(judges["incident"], "calls", None),
                                 "judge_budget": ledger.snapshot(), "approval": (marker or {}).get("sha256")}
    write_outputs(out, slot, result, "live")
    return result


# --- C1c: dashboard "Ask for a chart" --------------------------------------------------------------------------------

DASHBOARD_SCORE_VERSION = "evalv2-dashboard-score-v1"
DASHBOARD_SCORER_FILES = ("evalv2/score_stage.py", "evalv2/tasks/dashboard/scoring.py",
                          "evalv2/tasks/dashboard/report.py", "evalv2/references/dashboard.py",
                          "evalv2/references/dashboard_figures.py", "evalv2/sql/harness.py")
FIGURE_SOURCES = ("sql", "recount", "none")


def dashboard_output_dir(out: Path, slot: str) -> Path:
    return Path(out) / "scoring" / DASHBOARD_SCORE_VERSION / slot


def _sql_figures(harness: Any, snapshot: Mapping[str, Any], now: Any, sources: dict[str, int]
                 ) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Dashboard figures from the production SQL on the loaded snapshot.

    An all-time call that counts nothing would read ``CURRENT_DATE`` (the harness refuses it); that call, and only
    that call, takes the recount on the frozen clock, which the SQL agreement test pins to the SQL everywhere else.
    """
    from evalv2.references import dashboard_figures
    from evalv2.sql.harness import SqlHarnessError

    def fetch(query: Mapping[str, Any]) -> Mapping[str, Any]:
        if query.get("p_period_start") is None:
            try:
                result = harness.dashboard_open_period(**query)
            except SqlHarnessError:
                sources["recount_clock_dependent"] += 1
                return dashboard_figures.dashboard(snapshot, query, now=now)
        else:
            result = harness.dashboard(**query)
        sources["sql"] += 1
        return dict(result)

    return fetch


def run_dashboard_score(config: Any, out: Path, *, datasets: Mapping[str, Any], figures: str = "sql",
                        slot: str = "primary", allowlist: Mapping[str, Any] | None = None,
                        harness_start: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Score a C1c run's stored chart replies (no model call: spec scoring is code, figures come from the snapshot).

    ``figures``: ``sql`` (the production ``get_incident_dashboard`` in the SQL harness; needs pgserver and psycopg),
    ``recount`` (the Python recount, equal to the SQL by test) or ``none`` (spec scores only). Writes
    ``scoring/<version>/<slot>/dashboard-summary.json`` and ``dashboard-items.jsonl`` (private).
    """
    from contextlib import ExitStack
    from datetime import datetime

    from evalv2.core.netguard import no_network
    from evalv2.references import dashboard_figures
    from evalv2.references.dashboard import load_allowlist
    from evalv2.sql.harness import snapshot_sha256
    from evalv2.tasks.dashboard import report
    from evalv2.tasks.dashboard.runner import DATASET_ROLE, TASK

    out = Path(out)
    if TASK not in config.tasks:
        raise ScoreRefused([f"the config runs no {TASK!r} task"])
    if figures not in FIGURE_SOURCES:
        raise ScoreRefused([f"--figures must be one of {FIGURE_SOURCES}"])
    if not (out / "checkpoint.jsonl").exists():
        raise ScoreRefused([f"{out} holds no run (no checkpoint.jsonl)"])
    block = datasets.get(DATASET_ROLE) or {}
    snapshot = block.get("snapshot")
    if figures != "none" and snapshot is None:
        raise ScoreRefused([(f"figures need the reference set's corpus snapshot ({block.get('dataset')} pins none); "
                             "pass --figures none for spec scores only")])
    ensure_private(out)
    allowlist = dict(allowlist) if allowlist is not None else load_allowlist()
    sources: dict[str, int] = defaultdict(int)
    with ExitStack() as stack:
        figures_of = None
        if figures != "none":
            now = datetime.fromisoformat(snapshot["taken_at"])
            if figures == "sql":
                from evalv2.sql.harness import SqlHarness

                try:
                    harness = stack.enter_context((harness_start or SqlHarness.start)())
                except ImportError as exc:
                    raise ScoreRefused([(f"--figures sql needs pgserver and psycopg ({exc}): run with uv run --with "
                                         "pgserver==0.1.4 --with 'psycopg[binary]>=3.2,<4', or pass --figures "
                                         "recount")]) from exc
                harness.load(snapshot)
                fetch = _sql_figures(harness, snapshot, now, sources)
            else:
                def fetch(query: Mapping[str, Any]) -> Mapping[str, Any]:
                    sources["recount"] += 1
                    return dashboard_figures.dashboard(snapshot, query, now=now)

            def chart_of(spec: Mapping[str, Any] | None) -> Mapping[str, Any]:
                return dashboard_figures.chart(spec, chart_type=report.CHART_TYPE, timezone=report.TIMEZONE, now=now,
                                               allowlist=allowlist, fetch=fetch)

            figures_of = report.cached_figures(chart_of)
        with no_network():  # pgserver is a local socket started above; scoring itself never reaches a network
            scored = report.score_rows(final_predictions(out), ArtifactStore(out / "store"), block["records"],
                                       allowlist, figures_of=figures_of)
    import hashlib

    from evalv2.core.manifest import git_info
    from evalv2.executor import EVAL_DIR

    run = ArtifactStore(out / "store").run(config.experiment)
    summary = {
        "score_version": DASHBOARD_SCORE_VERSION, "report_version": report.REPORT_VERSION,
        "experiment": config.experiment, "sealed": run.sealed,
        "scorer": {**git_info(EVAL_DIR), "files_sha256": {name: hashlib.sha256((EVAL_DIR / name).read_bytes())
                                                          .hexdigest() for name in DASHBOARD_SCORER_FILES}},
        "references": {"dataset": block.get("dataset"), "records": len(block["records"]),
                       "tiers": dict(sorted(Counter(r["tier"] for r in block["records"]).items()))},
        "figures": {"source": figures, "chart_type": report.CHART_TYPE, "timezone": report.TIMEZONE,
                    "clock": snapshot["taken_at"] if snapshot else None,
                    "snapshot_sha256": snapshot_sha256(snapshot) if snapshot else None, "calls": dict(sources)},
        "bootstrap": {"unit": "reference record (repeats kept together)", "resamples": report.RESAMPLES,
                      "seed": report.SEED, "confidence": report.CONFIDENCE},
        "final_items": sum(s["items"] for s in scored["subjects"].values()),
        "subjects": scored["subjects"],
    }
    directory = dashboard_output_dir(out, slot)
    ensure_private(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dashboard-summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                                                      encoding="utf-8")
    with (directory / "dashboard-items.jsonl").open("wb") as handle:
        for item in scored["items"]:
            handle.write(canonical_json(item) + b"\n")
    return {"summary": summary, "items": scored["items"]}

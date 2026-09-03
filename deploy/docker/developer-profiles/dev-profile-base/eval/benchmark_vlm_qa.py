#!/usr/bin/env python3
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
"""E2E video Q&A benchmark over ``vss vlm run`` (NAT / vss-agent free).

Loads questions and videos from a DSS dataset named by ``--dataset-name`` (via the
``nvdataset`` CLI), asks each QA item against a deployed Cosmos Reason 3 RT-VLM
through the VSS CLI, then reports per-item and aggregate **latency** and LLM-judge
**accuracy**. The reference dataset is ``vss-devx-base``; neither it nor its tenancy
is hardcoded, so any dataset of the same shape works.

Out of scope: tool-calling accuracy and trajectory evaluation.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT_S = 300
# vss-devx-base is ~290 MB. Generous enough for a slow link, but still an upper bound:
# a stalled DSS would otherwise hang the run before a single item is measured.
DEFAULT_DOWNLOAD_TIMEOUT_S = 1800
DEFAULT_NUM_FRAMES = 20
# Watchdog margin over `vss vlm --timeout`. The CLI bounds its own HTTP call; this
# only covers a CLI that never returns at all, so one stuck item cannot cost the
# whole run. Wide enough that a CLI honouring its timeout always wins the race.
WATCHDOG_GRACE_S = 60
# How long to wait for a killed child's pipes to close before giving up on its output.
WATCHDOG_REAP_S = 10
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
EXPLICIT_VIDEO_KEYS = ("video", "video_file", "video_name", "sensor", "sensor_id", "media")

# Same criteria as customized_qa_evaluator (config.yml qa_evaluator prompt).
QA_JUDGE_PROMPT = """You are an expert evaluator assessing a Question Answering (QA) system's response accuracy.

Question Asked: {question}

Agent's Answer: {answer}

Ground Truth Answer: {reference}

EVALUATION TASK:
Compare the agent's answer against the ground truth and determine if they are semantically equivalent with a nuanced score between 0.0 and 1.0.

EVALUATION CRITERIA:

1. **Factual Correctness**: Does the agent's answer convey the same factual information as the ground truth?
    - For Yes/No questions: The boolean value must match exactly.
    - For counting questions: The number must exactly match the ground truth.
    - For temporal questions: Allow ±5 seconds tolerance for timestamps.
    - For descriptive questions: Key facts and details must align.

2. **Completeness**: Does the agent's answer include all key information from the ground truth?
    - Partial answers should receive partial credit.
    - Additional correct details beyond ground truth are acceptable.

3. **Semantic Equivalence**: Different phrasings of the same answer are acceptable.
    - "Yes" and "Yes, a worker dropped one box" are equivalent for a Yes/No question.
    - "60 seconds" and "at the 1 minute mark" are equivalent.
    - "No" and "The worker is not wearing a safety vest" are equivalent.

SCORING GUIDELINES:
- 1.0: Perfect match - answer is factually correct and complete
- 0.8-0.9: Essentially correct with minor omissions or slight imprecision
- 0.6-0.7: Partially correct - captures main point but missing some details
- 0.4-0.5: Mixed - some correct elements but significant errors or omissions
- 0.2-0.3: Mostly incorrect but shows some understanding
- 0.0-0.1: Completely wrong or irrelevant answer

IMPORTANT NOTES:
- Focus on SEMANTIC correctness, not exact text matching.

OUTPUT:
Think through your evaluation step by step, then output ONLY a single decimal number
(your score from 0.0 to 1.0) on the final line.
"""

# `<agent-think>` is gone with vss-agent, which nothing here calls. `<think>` stays
# because the judge is pluggable: the prompt above asks it to work step by step, and a
# reasoning model wraps that working in `<think>`, whose digits would otherwise be read
# as the score.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SCORE_RE = re.compile(r"(?<![\d.])(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)(?![\d.])")


def repo_root_from_here() -> Path:
    """``eval/`` lives five levels under the repository root."""
    return Path(__file__).resolve().parents[5]


def default_dataset_dir(root: Path, dataset_name: str) -> Path:
    """Where a dataset of this name would have been unpacked, deployed or not."""
    compose_data = root / "deploy" / "docker" / "data-dir" / "agent_eval" / "dataset" / dataset_name
    if compose_data.exists():
        return compose_data
    return Path(__file__).resolve().parent / "dataset" / dataset_name


def vss_command(root: Path) -> list[str]:
    """Project-local ``vss`` — never PATH, docker exec, or a constructed endpoint."""
    return [
        "uv",
        "run",
        "--project",
        str(root / "services" / "agent"),
        "--no-dev",
        "--extra",
        "cli",
        "vss",
    ]


def percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def latency_stats(latencies: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(latencies)
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered) if ordered else None,
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "p95": percentile(ordered, 95),
        "p99": percentile(ordered, 99),
    }


def strip_think_tags(text: str) -> str:
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


def parse_score(text: str) -> tuple[float, str]:
    """Return ``(score, reasoning)`` from an LLM-judge completion."""
    reasoning = strip_think_tags(text)
    lines = [line.strip() for line in reasoning.splitlines() if line.strip()]
    candidates = list(reversed(lines)) if lines else [reasoning]
    for line in candidates:
        matches = list(_SCORE_RE.finditer(line))
        if not matches:
            continue
        score = float(matches[-1].group(1))
        return score, reasoning
    raise ValueError(f"could not extract a score in [0.0, 1.0] from judge output: {text[:300]!r}")


def json_line_with(stdout: str, key: str) -> dict[str, Any] | None:
    """Last JSON object on its own line carrying ``key``.

    The CLI emits one compact object per line plus a trailing event marker, so the
    payload is selected by the field wanted rather than by position.
    """
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and key in payload:
            return payload
    return None


def parse_vlm_stdout(stdout: str) -> dict[str, Any]:
    """Select the JSON object that carries ``.answer``, skipping the completion marker."""
    payload = json_line_with(stdout, "answer")
    if payload is None:
        raise ValueError(f"vss vlm run produced no JSON object with an answer field: {stdout[-500:]!r}")
    return payload


def load_dataset(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "dataset"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
    raise ValueError(f"{path} is not a JSON array of eval items")


def evaluation_methods(item: dict[str, Any]) -> list[str]:
    methods = item.get("evaluation_method") or []
    if isinstance(methods, str):
        return [methods]
    if isinstance(methods, list):
        return [str(m) for m in methods]
    return []


def is_qa_item(item: dict[str, Any]) -> bool:
    if "qa" not in evaluation_methods(item):
        return False
    ground_truth = item.get("ground_truth")
    if not isinstance(ground_truth, str) or not ground_truth.strip():
        return False
    # A report item points at a reference JSON rather than carrying answer text.
    return not (ground_truth.rstrip().endswith(".json") and "report" in Path(ground_truth).name)


def index_videos(videos_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not videos_dir.is_dir():
        return index
    for path in sorted(videos_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            index[path.stem] = path
            index[path.name] = path
    return index


def trajectory_sensor_ids(item: dict[str, Any]) -> list[str]:
    """Clip names taken from the expected tool calls in ``trajectory_ground_truth``.

    vss-devx-base has no video field: the clip is named in the query prose and, for
    most items, in the expected ``video_understanding`` call's ``sensor_id``. The
    latter is structured data rather than prose, so it is the better source even
    though scoring the trajectory itself is out of scope here.
    """
    steps = item.get("trajectory_ground_truth")
    if not isinstance(steps, list):
        return []
    found: list[str] = []
    for step in steps:
        params = step.get("params") if isinstance(step, dict) else None
        if not isinstance(params, dict):
            continue
        for key in ("sensor_id", "sensor", "video"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
    return found


def indexed_video(raw: str, video_index: dict[str, Path]) -> Path | None:
    candidate = Path(raw)
    for token in (raw, candidate.name, candidate.stem):
        if token in video_index:
            return video_index[token]
    return candidate if candidate.is_file() else None


def resolve_video(item: dict[str, Any], video_index: dict[str, Path], query: str) -> Path:
    for key in EXPLICIT_VIDEO_KEYS:
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            match = indexed_video(raw, video_index)
            if match is not None:
                return match
    for raw in trajectory_sensor_ids(item):
        match = indexed_video(raw, video_index)
        if match is not None:
            return match
    unique_stems = sorted({path.stem for path in video_index.values()}, key=len, reverse=True)
    for stem in unique_stems:
        if re.search(rf"(?<![\w.-]){re.escape(stem)}(?![\w.-])", query):
            return video_index[stem]
    for stem in unique_stems:
        if stem in query:
            return video_index[stem]
    if len(unique_stems) == 1:
        return video_index[unique_stems[0]]
    raise FileNotFoundError(
        f"item {item.get('id')!r}: could not match query to a video in the dataset. "
        f"Name the clip in the query, or set one of {EXPLICIT_VIDEO_KEYS}."
    )


@dataclass
class QaItem:
    id: str
    query: str
    ground_truth: str
    video_path: Path


@dataclass
class Selection:
    """QA items to run, plus the ones deliberately left out and why."""

    items: list[QaItem]
    skipped: list[dict[str, str]]


def select_qa_items(
    raw_items: list[dict[str, Any]],
    video_index: dict[str, Path],
    *,
    limit: int | None = None,
) -> Selection:
    selected: list[QaItem] = []
    skipped: list[dict[str, str]] = []
    if limit is not None and limit <= 0:
        return Selection(items=selected, skipped=skipped)
    for item in raw_items:
        if not is_qa_item(item):
            continue
        query = str(item.get("query") or "")
        # Not every qa item is answerable by one VLM call: vss-devx-base includes
        # clarification tests whose whole point is that no clip is named. Skipping is
        # right, but the ids are reported so a shrinking denominator stays visible.
        try:
            video_path = resolve_video(item, video_index, query)
        except FileNotFoundError as exc:
            skipped.append({"id": str(item.get("id")), "reason": str(exc)})
            continue
        selected.append(
            QaItem(
                id=str(item.get("id")),
                query=query,
                ground_truth=str(item["ground_truth"]),
                video_path=video_path,
            )
        )
        if limit is not None and len(selected) >= limit:
            break
    return Selection(items=selected, skipped=skipped)


def dss_credential(env: dict[str, str] | None = None) -> str | None:
    """Name the DSS credential ``nvdataset`` will use, or ``None`` if it has none.

    ``NVDATASET_API_KEY`` is a *Personal* key scoped to the NVIDIA Dataset Service --
    not the global NGC key used by the NGC CLI, which is why an ``NGC_API_KEY`` that
    works elsewhere still gets 403 here. ``NGC_API_KEY`` is accepted for backward
    compatibility. Starfleet SSO (``nvdataset auth login``) needs no key at all, so a
    stored token counts too; refusing to start in that case would be wrong.
    """
    env = os.environ if env is None else env
    for name in ("NVDATASET_API_KEY", "NVDATASET_NGC_API_KEY", "NGC_API_KEY"):
        if env.get(name):
            return name
    dotenv = env.get("NVDATASET_DOTENV_PATH")
    if dotenv and Path(dotenv).expanduser().is_file():
        return f"NVDATASET_DOTENV_PATH={dotenv}"
    if (Path(env.get("HOME", "~")).expanduser() / ".nvdataset" / "starfleet_token.json").is_file():
        return "starfleet token (nvdataset auth login)"
    return None


def download_dataset(
    dest: Path,
    *,
    name: str,
    nvdataset_bin: str = "nvdataset",
    timeout_s: float = DEFAULT_DOWNLOAD_TIMEOUT_S,
) -> None:
    """Fetch the dataset, bounded.

    An unresponsive DSS must not hang the run before a single item is measured, so
    this gets the same watchdog as the VLM calls. Output is left uncaptured: the
    download is the long step, its progress is the only sign of life, and it is where
    ``nvdataset`` prints why a fetch was refused.

    Tenancy is `nvdataset`'s own configuration, not ours: it reads
    ``NVDATASET_TENANTID``/``NVDATASET_GROUPID`` from the environment, or the context
    saved by ``nvdataset auth context add``. Naming a tenant here would silently
    redirect anyone whose dataset lives elsewhere.
    """
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [nvdataset_bin, "download", name, str(dest)]
    print(f"+ {' '.join(cmd)}", file=sys.stderr)
    returncode, _stdout, _stderr, timed_out = run_bounded(cmd, timeout_s, capture=False)
    if timed_out:
        raise RuntimeError(f"`{nvdataset_bin} download` exceeded {timeout_s:g}s and was killed")
    if returncode != 0:
        raise RuntimeError(
            f"`{nvdataset_bin} download` failed with exit {returncode}. If it reported a "
            "missing tenant, set NVDATASET_TENANTID (and NVDATASET_GROUPID) or select a "
            "context with `nvdataset auth context use`."
        )


def dataset_ready(dataset_dir: Path, dataset_file: str) -> bool:
    return (dataset_dir / dataset_file).is_file() and (dataset_dir / "videos").is_dir()


def judge_completions_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/v1/chat/completions"


def judge_api_key(judge_url: str, env: dict[str, str] | None = None) -> str | None:
    """Pick the credential for the judge endpoint.

    A run needs ``NGC_API_KEY`` for the dataset download, so it is normally set in
    the same shell as the judge call. Falling back to it unconditionally would send
    an NVIDIA credential to whatever third-party host serves the judge -- which both
    fails auth and discloses the key. NVIDIA keys are therefore only used for NVIDIA
    or on-premise endpoints; anything else needs a key named for the judge.
    """
    env = os.environ if env is None else env
    explicit = env.get("EVAL_LLM_JUDGE_API_KEY") or env.get("OPENAI_API_KEY")
    if explicit:
        return explicit
    host = (urllib.parse.urlparse(judge_url).hostname or "").lower()
    nvidia_host = host.endswith("nvidia.com") or host in {"localhost", "127.0.0.1", "::1"} or host.startswith("10.")
    if nvidia_host:
        return env.get("NVIDIA_API_KEY") or env.get("NGC_API_KEY")
    return None


def judge_answer(
    *,
    question: str,
    answer: str,
    reference: str,
    judge_url: str,
    judge_model: str,
    timeout_s: int,
) -> tuple[float, str]:
    payload = {
        "model": judge_model,
        "temperature": 0.0,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": QA_JUDGE_PROMPT.format(question=question, answer=answer, reference=reference),
            }
        ],
    }
    request = urllib.request.Request(
        judge_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = judge_api_key(judge_url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM judge HTTP {exc.code}: {detail}") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM judge response missing choices[0].message.content: {body!r}") from exc
    if not isinstance(content, str):
        raise RuntimeError("LLM judge content is not a string")
    return parse_score(content)


def run_bounded(
    cmd: list[str],
    timeout_s: float,
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool]:
    """Run ``cmd`` with a hard upper bound, returning ``(rc, stdout, stderr, timed_out)``.

    The child gets its own session so a timeout kills the whole group. ``vss`` runs
    under ``uv``, so signalling only the direct child orphans the python process that
    is actually talking to the VLM: it survives, keeps occupying the GPU, and inflates
    the latency measured for every item after it.

    ``capture=False`` leaves the child on this process's streams so a long download
    keeps reporting progress; the bound and the group kill are unaffected.
    """
    pipe = subprocess.PIPE if capture else None
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=pipe,
        stderr=pipe,
        text=True,
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return proc.returncode, stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=WATCHDOG_REAP_S)
        except subprocess.TimeoutExpired:
            # Reaping has to be bounded too, or the watchdog reintroduces the hang it
            # exists to prevent. Draining the pipes waits for EOF, which needs every
            # writer closed -- and a writer can outlive the group kill: `killpg` may be
            # refused, leaving only the direct child killed, or a grandchild may have
            # started its own session and escaped the group. Abandon the output; the
            # item is already recorded as killed, and its stdout was never going to be
            # parsed.
            stdout, stderr = "", ""
        # SIGKILL leaves no status to read when the pipes were abandoned above.
        returncode = proc.returncode if proc.returncode is not None else -signal.SIGKILL
        return returncode, stdout or "", stderr or "", True


def vios_sensor_names(vss: list[str], timeout_s: int) -> set[str] | None:
    """Names already registered in VIOS, or ``None`` when VIOS cannot be reached."""
    returncode, stdout, _stderr, timed_out = run_bounded([*vss, "vios", "list", "--raw"], timeout_s)
    if timed_out or returncode != 0:
        return None
    payload = json_line_with(stdout, "sensors")
    if payload is None:
        return None
    return {str(s["name"]) for s in payload.get("sensors", []) if isinstance(s, dict) and s.get("name")}


def register_sensor(vss: list[str], video_path: Path, timeout_s: int) -> str | None:
    """Register a clip with VIOS, returning the sensor name it was filed under."""
    returncode, stdout, _stderr, timed_out = run_bounded([*vss, "vios", "add", str(video_path), "--raw"], timeout_s)
    if timed_out or returncode != 0:
        return None
    payload = json_line_with(stdout, "name")
    return str(payload["name"]) if payload else None


def resolve_sensors(vss: list[str], items: list[QaItem], timeout_s: int) -> dict[Path, str]:
    """Map each clip to a VIOS sensor name, registering the ones not yet known.

    Addressing by sensor is not a preference: ``--file`` inlines the clip as base64,
    which the VLM rejects outright for the larger clips in vss-devx-base. A sensor
    lets RT-VLM fetch the clip by URL instead, so clip length stops mattering.
    """
    known = vios_sensor_names(vss, timeout_s)
    if known is None:
        print(
            "vss: VIOS unavailable; falling back to inline --file. Clips over ~10 MB are "
            "expected to fail -- run `vss configure check` and confirm vst is ok.",
            file=sys.stderr,
        )
        return {}
    sensors: dict[Path, str] = {}
    for path in sorted({item.video_path for item in items}):
        if path.stem in known:
            sensors[path] = path.stem
            continue
        name = register_sensor(vss, path, timeout_s)
        if name is None:
            print(f"vss: could not register {path.name} with VIOS; sending it inline", file=sys.stderr)
            continue
        print(f"registered {path.name} as sensor {name!r}", file=sys.stderr)
        sensors[path] = name
    return sensors


def run_vlm_item(
    *,
    vss: list[str],
    item: QaItem,
    timeout_s: int,
    num_frames: int,
    model: str | None,
    sensor: str | None = None,
) -> tuple[str | None, float, int, str, str | None]:
    source = ["--sensor", sensor] if sensor else ["--file", str(item.video_path)]
    cmd = [
        *vss,
        "vlm",
        "run",
        "--prompt",
        item.query,
        *source,
        "--no-persist",
        "--intent",
        "qa",
        "--timeout",
        str(timeout_s),
        "--num-frames",
        str(num_frames),
    ]
    if model:
        cmd.extend(["--model", model])
    watchdog_s = timeout_s + WATCHDOG_GRACE_S
    started = time.perf_counter()
    returncode, stdout, stderr, timed_out = run_bounded(cmd, watchdog_s)
    elapsed = time.perf_counter() - started
    if timed_out:
        return (
            None,
            elapsed,
            returncode,
            f"benchmark watchdog killed `vss vlm run` after {watchdog_s}s; "
            f"the CLI did not honour its own --timeout {timeout_s}s",
            None,
        )
    if returncode != 0:
        err = (stderr or stdout or "").strip()
        return None, elapsed, returncode, err, None
    try:
        body = parse_vlm_stdout(stdout)
    except ValueError as exc:
        return None, elapsed, returncode, str(exc), None
    answer = strip_think_tags(str(body.get("answer") or ""))
    served_model = body.get("model")
    return answer, elapsed, returncode, "", str(served_model) if served_model else None


@dataclass
class ItemResult:
    id: str
    query: str
    video: str
    answer: str | None
    ground_truth: str
    score: float | None
    latency_seconds: float
    error: str
    exit_code: int
    #: Model the deployment reported serving the answer, as opposed to the one
    #: requested. A run is not interpretable without knowing which VLM produced it.
    served_model: str | None = None


def write_outputs(output_dir: Path, results: list[ItemResult], extra: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    scored = [item.score for item in results if item.score is not None]
    latencies = [item.latency_seconds for item in results]
    summary = {
        "count": len(results),
        "n_failed": sum(1 for item in results if item.error),
        "accuracy": {
            "n_scored": len(scored),
            "mean": statistics.fmean(scored) if scored else None,
        },
        "latency_seconds": latency_stats(latencies),
        "items": [asdict(item) for item in results],
        **extra,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    workflow = [{"id": item.id, "query": item.query, "output": item.answer, "error": item.error} for item in results]
    (output_dir / "workflow_output.json").write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    qa_items = [
        {
            "id": item.id,
            "score": item.score,
            "reasoning": {
                "question": item.query,
                "generated_answer": item.answer,
                "ground_truth": item.ground_truth,
                "error": item.error or None,
            },
        }
        for item in results
    ]
    qa_output = {
        "average_score": statistics.fmean(scored) if scored else None,
        "eval_output_items": qa_items,
    }
    (output_dir / "qa_evaluator_output.json").write_text(json.dumps(qa_output, indent=2) + "\n", encoding="utf-8")
    latency_output = {
        "average_latency_seconds": statistics.fmean(latencies) if latencies else None,
        "items": [{"id": item.id, "query": item.query, "latency_seconds": item.latency_seconds} for item in results],
    }
    (output_dir / "latency_summary.json").write_text(json.dumps(latency_output, indent=2) + "\n", encoding="utf-8")
    csv_path = output_dir / "summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "query", "video", "score", "latency_seconds", "error"],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "id": item.id,
                    "query": item.query,
                    "video": item.video,
                    "score": "" if item.score is None else item.score,
                    "latency_seconds": f"{item.latency_seconds:.3f}",
                    "error": item.error,
                }
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None, help="VSS checkout. Defaults to this file's repo.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Path to the extracted vss-devx-base directory.",
    )
    # No default dataset: naming one here would bake this team's DSS coordinates into
    # the blueprint. The reference dataset is `vss-devx-base` / `dataset_single_turn.json`
    # -- see the eval README.
    parser.add_argument("--dataset-name", required=True, help="DSS dataset to evaluate, e.g. vss-devx-base.")
    parser.add_argument(
        "--dataset-file",
        required=True,
        help="QA file inside the dataset, e.g. dataset_single_turn.json.",
    )
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Evaluate at most N QA items.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Per-call vss vlm --timeout seconds.")
    parser.add_argument(
        "--download-timeout",
        type=float,
        default=DEFAULT_DOWNLOAD_TIMEOUT_S,
        help="Upper bound on the nvdataset download, in seconds.",
    )
    parser.add_argument("--num-frames", type=int, default=DEFAULT_NUM_FRAMES)
    parser.add_argument("--model", default=None, help="RT-VLM model id. Default: whatever vss configure recorded.")
    parser.add_argument(
        "--judge-base-url",
        default=os.environ.get("EVAL_LLM_JUDGE_BASE_URL") or os.environ.get("LLM_BASE_URL"),
        help="OpenAI-compatible LLM used as judge (EVAL_LLM_JUDGE_BASE_URL).",
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("EVAL_LLM_JUDGE_NAME") or os.environ.get("LLM_NAME"),
        help="Judge model id (EVAL_LLM_JUDGE_NAME).",
    )
    parser.add_argument("--skip-judge", action="store_true", help="Collect latency only; skip accuracy scoring.")
    parser.add_argument(
        "--inline-media",
        action="store_true",
        help="Send clips inline with --file instead of registering VIOS sensors. Fails on large clips.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve items and videos; do not call the VLM.")
    parser.add_argument("--nvdataset-bin", default="nvdataset")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = (args.repo_root or Path(os.environ.get("VSS_REPO_ROOT", repo_root_from_here()))).resolve()
    dataset_dir = (
        args.dataset_dir or Path(os.environ.get("VSS_EVAL_DATASET", default_dataset_dir(root, args.dataset_name)))
    ).resolve()
    output_dir = (args.output_dir or (dataset_dir.parent.parent / "results" / "vlm_qa")).resolve()
    if not args.skip_download and (args.force_download or not dataset_ready(dataset_dir, args.dataset_file)):
        credential = dss_credential()
        if credential is None:
            print(
                f"vss: no DSS credential for downloading {args.dataset_name}. Either set "
                "NVDATASET_API_KEY to a Personal Key scoped to 'NVIDIA Dataset Service' "
                "(https://org.ngc.nvidia.com/setup/personal-keys, with the org switched to the "
                "one owning the dataset), or run `nvdataset auth login` for Starfleet SSO.",
                file=sys.stderr,
            )
            return 2
        print(f"dss credential: {credential}", file=sys.stderr)
        try:
            download_dataset(
                dataset_dir,
                name=args.dataset_name,
                nvdataset_bin=args.nvdataset_bin,
                timeout_s=args.download_timeout,
            )
        except (RuntimeError, OSError) as exc:
            print(f"vss: {exc}", file=sys.stderr)
            return 3
    if not dataset_ready(dataset_dir, args.dataset_file):
        print(f"vss: dataset not found at {dataset_dir} (need {args.dataset_file} and videos/)", file=sys.stderr)
        return 2

    raw_items = load_dataset(dataset_dir / args.dataset_file)
    video_index = index_videos(dataset_dir / "videos")
    if not video_index:
        print(f"vss: no videos under {dataset_dir / 'videos'}", file=sys.stderr)
        return 2
    selection = select_qa_items(raw_items, video_index, limit=args.limit)
    items = selection.items
    if not items:
        print(
            "vss: no QA items in the dataset (evaluation_method includes qa with a text ground_truth)", file=sys.stderr
        )
        return 2

    print(f"resolved {len(items)} QA item(s) from {dataset_dir / args.dataset_file}", file=sys.stderr)
    for skip in selection.skipped:
        print(f"skipped {skip['id']}: {skip['reason']}", file=sys.stderr)
    if args.dry_run:
        for item in items:
            print(f"{item.id}\t{item.video_path.name}\t{item.query}")
        return 0

    vss = vss_command(root)
    judge_url = None
    if not args.skip_judge:
        if not args.judge_base_url or not args.judge_model:
            print(
                "vss: set --judge-base-url and --judge-model (or EVAL_LLM_JUDGE_BASE_URL / EVAL_LLM_JUDGE_NAME); "
                "or pass --skip-judge for latency only",
                file=sys.stderr,
            )
            return 2
        judge_url = judge_completions_url(args.judge_base_url)

    sensors = {} if args.inline_media else resolve_sensors(vss, items, args.timeout)

    results: list[ItemResult] = []
    for item in items:
        print(f"running {item.id} on {item.video_path.name} …", file=sys.stderr)
        answer, latency, exit_code, error, served_model = run_vlm_item(
            vss=vss,
            item=item,
            timeout_s=args.timeout,
            num_frames=args.num_frames,
            model=args.model,
            sensor=sensors.get(item.video_path),
        )
        score: float | None = None
        if answer and judge_url is not None:
            try:
                score, _reasoning = judge_answer(
                    question=item.query,
                    answer=answer,
                    reference=item.ground_truth,
                    judge_url=judge_url,
                    judge_model=args.judge_model,
                    timeout_s=min(120, args.timeout),
                )
            except (RuntimeError, ValueError) as exc:
                error = str(exc)
        elif not answer and not error:
            error = "empty VLM answer"
        results.append(
            ItemResult(
                id=item.id,
                query=item.query,
                video=item.video_path.name,
                answer=answer,
                ground_truth=item.ground_truth,
                score=score,
                latency_seconds=latency,
                error=error,
                exit_code=exit_code,
                served_model=served_model,
            )
        )

    served_models = sorted({item.served_model for item in results if item.served_model})
    extra = {
        "dataset_dir": str(dataset_dir),
        "dataset_name": args.dataset_name,
        "dataset_file": args.dataset_file,
        "skipped": selection.skipped,
        "media_addressing": "inline_file" if args.inline_media else "vios_sensor",
        "model_requested": args.model,
        "model_served": served_models[0] if len(served_models) == 1 else served_models or None,
        "num_frames": args.num_frames,
        "judge_model": None if args.skip_judge else args.judge_model,
        "scope": "video_qa_via_vss_vlm",
        "non_goals": ["tool_calling_accuracy", "trajectory_evaluation"],
        "backend": "rt_vlm",
    }
    write_outputs(output_dir, results, extra)
    scored = [item.score for item in results if item.score is not None]
    latencies = [item.latency_seconds for item in results]
    mean_acc = statistics.fmean(scored) if scored else None
    mean_lat = statistics.fmean(latencies) if latencies else None
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "count": len(results),
                "accuracy_mean": mean_acc,
                "latency_seconds_mean": mean_lat,
                "n_failed": sum(1 for item in results if item.error),
            }
        )
    )
    return 0 if all(not item.error for item in results) else 3


if __name__ == "__main__":
    sys.exit(main())

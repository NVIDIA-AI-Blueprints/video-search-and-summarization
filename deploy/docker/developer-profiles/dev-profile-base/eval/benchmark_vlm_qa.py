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

Loads questions and videos from the DSS dataset ``vss-devx-base`` (via the
``nvdataset`` CLI), asks each QA item against a deployed Cosmos Reason 3
RT-VLM through the VSS CLI, then reports per-item and aggregate **latency**
and LLM-judge **accuracy**.

Out of scope: tool-calling accuracy and trajectory evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DATASET_NAME = "vss-devx-base"
DEFAULT_DATASET_FILE = "dataset_single_turn.json"
DEFAULT_NVDATASET_TENANT = "0573334707593577"
DEFAULT_NVDATASET_GROUP = "vss-bp-team"
DEFAULT_TIMEOUT_S = 300
DEFAULT_NUM_FRAMES = 20
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

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_AGENT_THINK_RE = re.compile(r"<agent-think>.*?</agent-think>", re.DOTALL)
_SCORE_RE = re.compile(r"(?<![\d.])(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)(?![\d.])")


def repo_root_from_here() -> Path:
    """``eval/`` lives five levels under the repository root."""
    return Path(__file__).resolve().parents[5]


def default_dataset_dir(root: Path) -> Path:
    compose_data = root / "deploy" / "docker" / "data-dir" / "agent_eval" / "dataset" / DEFAULT_DATASET_NAME
    if compose_data.exists():
        return compose_data
    return Path(__file__).resolve().parent / "dataset" / DEFAULT_DATASET_NAME


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
    cleaned = _AGENT_THINK_RE.sub("", text)
    cleaned = _THINK_RE.sub("", cleaned)
    return cleaned.strip()


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


def parse_vlm_stdout(stdout: str) -> dict[str, Any]:
    """Select the JSON object that carries ``.answer``, skipping the completion marker."""
    last_error: Exception | None = None
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict) and "answer" in payload:
            return payload
    raise ValueError(f"vss vlm run produced no JSON object with an answer field: {stdout[-500:]!r}") from last_error


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
    methods = evaluation_methods(item)
    if methods and "qa" not in methods:
        return False
    ground_truth = item.get("ground_truth")
    if not isinstance(ground_truth, str) or not ground_truth.strip():
        return False
    if ground_truth.rstrip().endswith(".json") and "report" in Path(ground_truth).name:
        return False
    return True


def index_videos(videos_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not videos_dir.is_dir():
        return index
    for path in sorted(videos_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            index[path.stem] = path
            index[path.name] = path
    return index


def resolve_video(item: dict[str, Any], video_index: dict[str, Path], query: str) -> Path:
    for key in EXPLICIT_VIDEO_KEYS:
        raw = item.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = Path(raw)
        for token in (raw, candidate.name, candidate.stem):
            if token in video_index:
                return video_index[token]
        if candidate.is_file():
            return candidate
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


def select_qa_items(
    raw_items: list[dict[str, Any]],
    video_index: dict[str, Path],
    *,
    limit: int | None = None,
) -> list[QaItem]:
    selected: list[QaItem] = []
    for item in raw_items:
        if not is_qa_item(item):
            continue
        query = str(item.get("query") or "")
        video_path = resolve_video(item, video_index, query)
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
    return selected


def download_dataset(
    dest: Path,
    *,
    name: str = DEFAULT_DATASET_NAME,
    nvdataset_bin: str = "nvdataset",
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("NVDATASET_TENANTID", DEFAULT_NVDATASET_TENANT)
    env.setdefault("NVDATASET_GROUPID", DEFAULT_NVDATASET_GROUP)
    cmd = [nvdataset_bin, "download", name, str(dest)]
    print(f"+ {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, check=True, env=env)


def dataset_ready(dataset_dir: Path, dataset_file: str) -> bool:
    return (dataset_dir / dataset_file).is_file() and (dataset_dir / "videos").is_dir()


def judge_completions_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/v1/chat/completions"


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
    api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NGC_API_KEY") or os.environ.get("OPENAI_API_KEY")
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


def run_vlm_item(
    *,
    vss: list[str],
    item: QaItem,
    timeout_s: int,
    num_frames: int,
    model: str | None,
) -> tuple[str | None, float, int, str]:
    cmd = [
        *vss,
        "vlm",
        "run",
        "--prompt",
        item.query,
        "--file",
        str(item.video_path),
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
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, elapsed, proc.returncode, err
    try:
        body = parse_vlm_stdout(proc.stdout)
    except ValueError as exc:
        return None, elapsed, proc.returncode, str(exc)
    answer = strip_think_tags(str(body.get("answer") or ""))
    return answer, elapsed, proc.returncode, ""


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
    workflow = [
        {"id": item.id, "query": item.query, "output": item.answer, "error": item.error} for item in results
    ]
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
        "items": [
            {"id": item.id, "query": item.query, "latency_seconds": item.latency_seconds} for item in results
        ],
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
    parser.add_argument("--dataset-file", default=DEFAULT_DATASET_FILE)
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Evaluate at most N QA items.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Per-call vss vlm --timeout seconds.")
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
    parser.add_argument("--dry-run", action="store_true", help="Resolve items and videos; do not call the VLM.")
    parser.add_argument("--nvdataset-bin", default="nvdataset")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = (args.repo_root or Path(os.environ.get("VSS_REPO_ROOT", repo_root_from_here()))).resolve()
    dataset_dir = (args.dataset_dir or Path(os.environ.get("VSS_EVAL_DATASET", default_dataset_dir(root)))).resolve()
    output_dir = (args.output_dir or (dataset_dir.parent.parent / "results" / "vlm_qa")).resolve()
    if not args.skip_download and (args.force_download or not dataset_ready(dataset_dir, args.dataset_file)):
        if not os.environ.get("NGC_API_KEY"):
            print("vss: NGC_API_KEY is required to download vss-devx-base", file=sys.stderr)
            return 2
        download_dataset(dataset_dir, name=args.dataset_name, nvdataset_bin=args.nvdataset_bin)
    if not dataset_ready(dataset_dir, args.dataset_file):
        print(f"vss: dataset not found at {dataset_dir} (need {args.dataset_file} and videos/)", file=sys.stderr)
        return 2

    raw_items = load_dataset(dataset_dir / args.dataset_file)
    video_index = index_videos(dataset_dir / "videos")
    if not video_index:
        print(f"vss: no videos under {dataset_dir / 'videos'}", file=sys.stderr)
        return 2
    try:
        items = select_qa_items(raw_items, video_index, limit=args.limit)
    except FileNotFoundError as exc:
        print(f"vss: {exc}", file=sys.stderr)
        return 2
    if not items:
        print("vss: no QA items in the dataset (evaluation_method includes qa with a text ground_truth)", file=sys.stderr)
        return 2

    print(f"resolved {len(items)} QA item(s) from {dataset_dir / args.dataset_file}", file=sys.stderr)
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

    results: list[ItemResult] = []
    for item in items:
        print(f"running {item.id} on {item.video_path.name} …", file=sys.stderr)
        answer, latency, exit_code, error = run_vlm_item(
            vss=vss,
            item=item,
            timeout_s=args.timeout,
            num_frames=args.num_frames,
            model=args.model,
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
            )
        )

    extra = {
        "dataset_dir": str(dataset_dir),
        "dataset_file": args.dataset_file,
        "model": args.model,
        "num_frames": args.num_frames,
        "judge_model": None if args.skip_judge else args.judge_model,
        "scope": "video_qa_via_vss_vlm",
        "non_goals": ["tool_calling_accuracy", "trajectory_evaluation"],
        "backend": "rt_vlm (Cosmos Reason 3 when that is the configured deployment model)",
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

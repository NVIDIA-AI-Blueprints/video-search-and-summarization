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

"""Verified preliminary video reports, comparisons and immutable baseline candidates."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from .artifacts import bundle_bytes, reserve, verify_inventory
from .media import file_hash
from .results import Evidence
from .statistics import describe
from .video import verify_trial
from .video_results import VideoResult, identity


def load(path: Path) -> VideoResult:
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError("result file limit")
    result = VideoResult.model_validate_json(path.read_bytes())
    root = path.with_suffix(".artifacts")
    collections = set()
    for trial in result.trials:
        ids = verify_trial(trial, root)
        if collections.intersection(ids):
            raise ValueError("collection reused between independent workers")
        collections.update(ids)
    refs = [
        ref
        for trial in result.trials
        for ref in (
            [p.trace for p in trial.periods if p.trace]
            + ([trial.workflow.trace] if trial.workflow else [])
        )
    ]
    verify_inventory(root, refs, complete=result.status == "ok")
    bundle_bytes(root)
    verify_candidate_seal(path)
    return result


def summarize(result: VideoResult) -> dict[str, object]:
    if result.status != "ok":
        return {
            "status": result.status,
            "failure": result.failure,
            "completed_pairs": sum(t.status == "ok" for t in result.trials),
            "statistics": None,
        }
    disabled = [
        next(p for p in t.periods if p.mode == "disabled") for t in result.trials
    ]
    enabled = [next(p for p in t.periods if p.mode == "enabled") for t in result.trials]
    overhead = [
        on.wall_s / off.wall_s - 1 for on, off in zip(enabled, disabled, strict=True)
    ]
    media = result.trials[0].media
    assert media is not None
    summary: dict[str, object] = {
        "status": "preliminary_unapproved",
        "pairs": len(result.trials),
        "media_sha256": media.sha256,
        "video_seconds": media.duration_s,
        "frames": len(media.pts_ns),
        "width": media.width,
        "height": media.height,
        "disabled_wall_s": describe([p.wall_s for p in disabled]).model_dump(),
        "enabled_wall_s": describe([p.wall_s for p in enabled]).model_dump(),
        "enabled_frames_per_s": describe(
            [p.frames / p.wall_s for p in enabled]
        ).model_dump(),
        "disabled_frames_per_s": describe(
            [p.frames / p.wall_s for p in disabled]
        ).model_dump(),
        "enabled_media_seconds_per_s": describe(
            [media.duration_s / p.wall_s for p in enabled]
        ).model_dump(),
        "paired_relative_collection_overhead": describe(overhead).model_dump(),
        "self_rss_sampled_peak_bytes": describe(
            [
                max(s.self_rss_bytes for s in t.resources.samples)
                for t in result.trials
                if t.resources is not None
            ]
        ).model_dump(),
        "family_policy": "marginal_descriptive_intervals_no_approved_gates",
        "acceptance_eligible": False,
    }
    if result.configuration.offline_e2e:
        summary["offline_e2e_wall_s"] = describe(
            [t.workflow.wall_s for t in result.trials if t.workflow is not None]
        ).model_dump()
        summary["offline_provider_mode"] = "controlled_http_no_model_performance_claim"
    return summary


def compare(candidate: VideoResult, baseline: VideoResult) -> dict[str, object]:
    if candidate.status != "ok" or baseline.status != "ok":
        return {"status": "invalid", "reason": "incomplete_evidence"}
    if (
        candidate.trials[0].environment.cpu_model_sha256 is None
        or baseline.trials[0].environment.cpu_model_sha256 is None
    ):
        return {"status": "incompatible", "reason": "hardware_identity_unavailable"}
    if candidate.configuration != baseline.configuration or identity(
        candidate.trials[0]
    ) != identity(baseline.trials[0]):
        return {"status": "incompatible", "reason": "identity_mismatch"}
    # Single prespecified endpoint: enabled decode wall-time median. Two median
    # intervals use Bonferroni alpha/2, yielding a conservative 95% ratio interval.
    current = describe(
        [
            next(p.wall_s for p in t.periods if p.mode == "enabled")
            for t in candidate.trials
        ],
        confidence=0.975,
    )
    previous = describe(
        [
            next(p.wall_s for p in t.periods if p.mode == "enabled")
            for t in baseline.trials
        ],
        confidence=0.975,
    )
    interval = None
    if current.interval and previous.interval:
        interval = (
            current.interval[0] / previous.interval[1] - 1,
            current.interval[1] / previous.interval[0] - 1,
        )
    return {
        "status": "descriptive" if interval else "inconclusive",
        "reason": "unapproved_runner_and_thresholds",
        "relative_median_change": current.median / previous.median - 1,
        "relative_median_interval": interval,
        "confidence": 0.95,
        "multiplicity": "bonferroni_two_median_intervals_single_endpoint",
        "acceptance_eligible": False,
    }


class CandidateSeal(Evidence):
    approved: Literal[False] = False
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    traces: dict[str, str]
    bytes: int = Field(ge=0)
    purpose: Literal["unapproved_baseline_candidate"] = "unapproved_baseline_candidate"


def verify_candidate_seal(path: Path) -> None:
    seal_path = path.parent / "seal.json"
    if not seal_path.exists():
        return
    if path.name != "result.json" or seal_path.stat().st_size > 8 * 1024**2:
        raise ValueError("candidate seal identity or byte limit")
    seal = CandidateSeal.model_validate_json(seal_path.read_bytes())
    root = path.with_suffix(".artifacts")
    traces = {
        p.relative_to(path.parent).as_posix(): file_hash(p)
        for p in root.rglob("*")
        if p.is_file()
    }
    if (
        seal.evidence_sha256 != file_hash(path)
        or seal.traces != traces
        or seal.bytes != path.stat().st_size + bundle_bytes(root)
        or any(
            p.name not in {"result.json", "result.artifacts", "seal.json"}
            for p in path.parent.iterdir()
        )
        or path.is_symlink()
        or seal_path.is_symlink()
    ):
        raise ValueError("candidate seal mismatch")


def register_candidate(source: Path, destination: Path) -> None:
    result = load(source)
    if result.status != "ok" or result.configuration.repetitions < 10:
        raise ValueError("baseline candidates need ten complete independent pairs")
    if result.trials[0].environment.cpu_model_sha256 is None:
        raise ValueError("baseline candidate hardware identity unavailable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with reserve(destination):
        if destination.exists():
            raise FileExistsError("baseline candidates are immutable")
        with tempfile.TemporaryDirectory(
            prefix=".candidate-", dir=destination.parent
        ) as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            shutil.copy2(source, staging / "result.json")
            shutil.copytree(
                source.with_suffix(".artifacts"), staging / "result.artifacts"
            )
            # Verify copied bytes and traces rather than only the source's manifest.
            if load(staging / "result.json") != result:
                raise ValueError("candidate changed during copying")
            traces = {
                p.relative_to(staging).as_posix(): file_hash(p)
                for p in sorted((staging / "result.artifacts").rglob("*"))
                if p.is_file()
            }
            seal = CandidateSeal(
                evidence_sha256=file_hash(staging / "result.json"),
                traces=traces,
                bytes=bundle_bytes(staging),
            )
            (staging / "seal.json").write_text(seal.model_dump_json(indent=2) + "\n")
            staging.rename(destination)


def rst_report(paths: list[Path]) -> str:
    lines = [
        "Preliminary CPU video results",
        "=============================",
        "",
        "These local development results have no approved performance verdict. Decode",
        "timing includes count/PTS/RGB correctness checks and scalar collection when",
        "enabled. Resource samples include oracle arrays, both modes and trace",
        "retention;",
        "they are non-atomic lower bounds and may double-count shared pages.",
        "Offline E2E",
        "uses real encoding/HTTP/library calls with controlled responses; model",
        "inference",
        "performance and generated-description accuracy remain unmeasured.",
        "",
        "Trials use fixed balanced AB/BA order, fresh processes, warm-cache runs and",
        "prespecified repetitions. Intervals are marginal 95% distribution-free median",
        "intervals under independent representative-trial assumptions. Local",
        "load/power",
        "conditions are uncontrolled; intervals do not confer runner qualification.",
        "",
        ".. list-table:: Results",
        "   :header-rows: 1",
        "",
        "   * - Artifact",
        "     - Input",
        "     - Pairs",
        "     - Decode FPS (enabled median)",
        "     - Collection overhead (median / 95% interval)",
        "     - Offline E2E seconds (median)",
    ]
    for path in paths:
        result = load(path)
        if result.status != "ok":
            lines.extend(
                [
                    f"   * - {path.name}",
                    "     - Incomplete",
                    "     - —",
                    "     - —",
                    f"     - {result.failure}",
                    "     - —",
                ]
            )
            continue
        media = result.trials[0].media
        assert media is not None
        periods = [
            next(p for p in t.periods if p.mode == "enabled") for t in result.trials
        ]
        disabled = [
            next(p for p in t.periods if p.mode == "disabled") for t in result.trials
        ]
        fps = describe([p.frames / p.wall_s for p in periods])
        overhead = describe(
            [p.wall_s / d.wall_s - 1 for p, d in zip(periods, disabled, strict=True)]
        )
        interval = (
            "insufficient samples"
            if overhead.interval is None
            else (
                f"{overhead.interval[0] * 100:.1f}% to "
                f"{overhead.interval[1] * 100:.1f}%"
            )
        )
        workflow = [t.workflow.wall_s for t in result.trials if t.workflow is not None]
        e2e = f"{describe(workflow).median:.3f}" if workflow else "disabled"
        lines.extend(
            [
                f"   * - {path.name}",
                f"     - {media.width}x{media.height}, {media.duration_s:.1f}s",
                f"     - {len(periods)}",
                f"     - {fps.median:.1f}",
                f"     - {overhead.median * 100:.1f}% / {interval}",
                f"     - {e2e}",
            ]
        )
    lines.extend(["", "Evidence identities", "-------------------", ""])
    for path in paths:
        lines.append(f"* ``{path.name}`` SHA-256: ``{file_hash(path)}``.")
    lines.extend(
        [
            "",
            "Median method: non-interpolated binomial order-statistic intervals;",
            "the attained",
            "coverage is reported in JSON and may exceed the nominal confidence.",
            "Small N",
            "returns insufficient evidence and p95 is omitted below100 independent",
            "trials.",
            "See `NIST median confidence limits <https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mediancl.htm>`_",
            "and `SciPy quantile-test notes <https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.quantile_test.html>`_.",
            "",
            "GPU, live-service, approved overhead/threshold budgets, production-",
            "scale memory",
            "qualification and M7 shared decoding remain open.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--register-candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.register_candidate:
            if len(args.inputs) != 1 or args.compare or args.output:
                parser.error("candidate registration takes one input and destination")
            register_candidate(args.inputs[0], args.register_candidate)
            return 0
        if args.compare:
            if len(args.inputs) != 1:
                parser.error("comparison takes one candidate and one baseline")
            comparison = compare(load(args.inputs[0]), load(args.compare))
            print(json.dumps(comparison, indent=2))
            return 0 if comparison["status"] == "descriptive" else 3
        if args.output:
            with args.output.open("x") as output:
                output.write(rst_report(args.inputs))
        else:
            print(json.dumps([summarize(load(path)) for path in args.inputs], indent=2))
        return 0
    except (OSError, ValueError):
        parser.error("invalid, incomplete, modified or conflicting evidence")


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The rerank post-pass scores both orders on the same saved stage-1 hits."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import report_raw_clip_eval as report
import rerank_raw_clip_eval as rerank


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def hit(sensor, start, end, similarity):
    return {
        "sensor_id": sensor,
        "start_time": f"2025-01-01T00:00:{start:02d}Z",
        "end_time": f"2025-01-01T00:00:{end:02d}Z",
        "similarity": similarity,
    }


QUERIES = [
    {
        "query_id": "event_1",
        "query": "forklift turning",
        "query_domain": "event",
        "relevant_clip_ids": ["clip-b"],
    },
    {
        "query_id": "pas_1",
        "query": "person in a red shirt",
        "query_domain": "pas",
        "relevant_clip_ids": ["clip-a"],
    },
]


@pytest.fixture
def evaluated(tmp_path):
    """A completed two-depth embed-only run whose stage-1 order is wrong for event_1."""
    data, run = tmp_path / "data", tmp_path / "run"
    for name in ("a", "b"):
        (data / "clips").mkdir(parents=True, exist_ok=True)
        (data / "clips" / f"{name}.mp4").touch()
    write(
        data / "additional_gt/clips_gt.json",
        {"gallery": [{"chunk_id": "clip-a"}, {"chunk_id": "clip-b"}]},
    )
    write(data / "gt/queries_gt.json", {"queries": QUERIES})
    manifest = data / "ingest.json"
    write(
        manifest,
        {
            "complete": True,
            "upload_timestamp": "2025-01-01T00:00:00",
            "clips": [
                {
                    "dataset_id": f"clip-{n}",
                    "sensor_id": f"s{n}",
                    "filename": f"{n}.mp4",
                    "duration_s": 10.0,
                    "success": True,
                }
                for n in ("a", "b")
            ],
        },
    )
    inputs = [
        manifest,
        data / "gt/queries_gt.json",
        data / "additional_gt/clips_gt.json",
    ]
    write(
        run / "identity.json",
        {
            "run_id": "run-1",
            "source_commit": "abc",
            "modes": ["embed-only"],
            "top_k_values": [1, 2],
            "multi_k": True,
            "input_sha256": {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
            },
        },
    )
    write(run / "queries.json", {"queries": QUERIES})
    write(run / "complete.json", {"all_modes_complete": True})
    hits = [hit("sa", 2, 4, 0.9), hit("sb", 5, 9, 0.8)]
    for query in QUERIES:
        for k in (1, 2):
            write(
                run
                / "captures/embed-only"
                / f"top-k-{k}"
                / f"{query['query_id']}.json",
                {
                    "path": "embed",
                    "payload": {"query": query["query"]},
                    "data": hits[:k],
                },
            )
    common = [
        "--run-dir",
        str(run),
        "--dataset-root",
        str(data),
        "--ingestion-manifest",
        str(manifest),
    ]
    return run, common


class ReverseScorer:
    """Prefers later stage-1 hits, so it flips each query's order."""

    def __init__(self):
        self.calls = []

    def score(self, query, windows, fps, max_frames):
        self.calls.append((query, fps, max_frames, windows))
        return [(float(w.index), {"fps": fps}) for w in windows]

    def frames(self, window, fps, max_frames):
        return min(max_frames, max(1, round((window.end_s - window.start_s) * fps)))


def test_identity_reranker_reproduces_the_original_metrics(evaluated):
    _run, common = evaluated
    out = rerank.run(
        rerank.parse_args(
            [
                *common,
                "--output-name",
                "noop",
                "--reranker",
                "identity",
                "--fps",
                "1",
                "4",
            ]
        )
    )
    assert sorted(p.name for p in out.iterdir()) == [
        "comparison.json",
        "complete.json",
        "fps-1_frames-32",
        "identity.json",
    ]
    summary = json.loads((out / "fps-1_frames-32/summary.json").read_text())
    by_k = summary["embed-only"]["map_percent_by_top_k"]
    for k in ("1", "2"):
        assert by_k[k]["reranked"] == by_k[k]["stage1-pool"] == by_k[k]["baseline"]
    assert by_k["1"]["reranked"]["event"] == 0
    assert by_k["1"]["reranked"]["by_route"] == {"embed": 50.0}


def test_reranked_order_is_scored_against_the_same_stage1_pool(evaluated):
    run, common = evaluated
    scorer = ReverseScorer()
    out = rerank.run(
        rerank.parse_args(
            [
                *common,
                "--output-name",
                "sweep",
                "--fps",
                "0.5",
                "2",
                "--max-frames",
                "8",
                "--fusion-alphas",
                "1",
                "--window-pad-s",
                "1",
                "--query-text",
                "request",
            ]
        ),
        scorer=scorer,
    )
    assert {(fps, frames) for _, fps, frames, _ in scorer.calls} == {(0.5, 8), (2.0, 8)}
    first = scorer.calls[0][3]
    assert [(w.sensor_id, w.start_s, w.end_s) for w in first] == [
        ("sa", 1.0, 5.0),
        ("sb", 4.0, 10.0),
    ]
    summary = json.loads((out / "fps-2_frames-8/summary.json").read_text())
    k1 = summary["embed-only"]["map_percent_by_top_k"]["1"]
    assert (k1["stage1-pool"]["event"], k1["reranked"]["event"]) == (0, 100)
    assert (k1["stage1-pool"]["pas"], k1["reranked"]["pas"]) == (100, 0)
    # alpha=1 is the stage-1 score alone, so it restores the original order.
    assert k1["fused-a1.00"] == k1["stage1-pool"]
    # Padded windows are 4 s and 6 s: capped at 8 frames at 2 fps, 2 and 3 at 0.5 fps.
    assert summary["embed-only"]["mean_frames_per_candidate"] == 8
    slow = json.loads((out / "fps-0.5_frames-8/summary.json").read_text())
    assert slow["embed-only"]["mean_frames_per_candidate"] == 2.5
    capture = json.loads(
        (out / "fps-2_frames-8/captures/embed-only/event_1.json").read_text()
    )
    assert capture["orders"]["reranked"] == [1, 0]
    assert capture["rankings"]["reranked"] == ["clip-b", "clip-a"]
    assert capture["query_text"] == "forklift turning"
    rows = json.loads((out / "comparison.json").read_text())["rows"]
    reranked = [r for r in rows if r["variant"] == "reranked" and r["k"] == 1]
    assert {r["setting"] for r in reranked} == {"fps-0.5_frames-8", "fps-2_frames-8"}
    assert all(r["delta_overall_vs_stage1_pool"] == 0 for r in reranked)
    html = report.rerank_section(run)
    assert "Reranker comparison" in html and "fps-0.5_frames-8" in html
    assert "100.00%" in html and "+0.00 pts" in html
    with pytest.raises(FileExistsError):
        rerank.run(
            rerank.parse_args([*common, "--output-name", "sweep"]), scorer=scorer
        )


def test_unfinished_or_foreign_runs_are_refused(evaluated):
    run, common = evaluated
    (run / "complete.json").unlink()
    with pytest.raises(ValueError, match="completed"):
        rerank.run(
            rerank.parse_args([*common, "--output-name", "x"]), scorer=ReverseScorer()
        )
    write(run / "complete.json", {"all_modes_complete": True})
    gt = Path(common[3]) / "gt/queries_gt.json"
    gt.write_text(gt.read_text() + "\n")
    with pytest.raises(ValueError, match="differs from the inputs"):
        rerank.run(
            rerank.parse_args([*common, "--output-name", "x"]), scorer=ReverseScorer()
        )
    assert not (run / "rerank").exists()


def test_failed_scores_fall_back_to_stage1_order_after_scored_hits():
    windows = [
        rerank.Window(i, "s", Path("v.mp4"), 0, 1, score)
        for i, score in enumerate([0.9, 0.8, 0.7, 0.6])
    ]
    assert rerank.order_by_scores(windows, [None, 1.0, None, 1.0]) == [1, 3, 0, 2]
    assert rerank.order_by_scores(windows, [0.0, 0.0, 2.0, 0.0]) == [2, 0, 1, 3]
    assert rerank.fused_scores(windows, [None, 5.0, 1.0, 3.0], 0.0)[0] is None
    with pytest.raises(ValueError, match="got 1 scores"):
        rerank.order_by_scores(windows, [1.0])


def test_hit_windows_are_clip_relative_and_clamped():
    clips = {"s": {"path": Path("v.mp4"), "duration_s": 6.0}}
    origin = datetime(2025, 1, 1, tzinfo=UTC)
    window = rerank.hit_window(hit("s", 1, 9, 0.5), 3, clips, origin, 2.0)
    assert (window.index, window.start_s, window.end_s) == (3, 0.0, 6.0)
    with pytest.raises(ValueError, match="outside the gallery"):
        rerank.hit_window(hit("x", 1, 2, 0.5), 0, clips, origin, 0.0)
    with pytest.raises(ValueError, match="invalid time bounds"):
        rerank.hit_window(hit("s", 4, 2, 0.5), 0, clips, origin, 0.0)
    with pytest.raises(ValueError, match="no time bounds"):
        rerank.hit_window({"sensor_id": "s"}, 0, clips, origin, 0.0)


@pytest.mark.parametrize(
    "extra",
    [
        ["--fps", "0"],
        ["--fps", "2", "2"],
        ["--max-frames", "1"],
        ["--rerank-depth", "0"],
        ["--fusion-alphas", "1.5"],
        ["--window-pad-s", "-1"],
        ["--reranker", "identity", "--lora-path", "adapter"],
        ["--output-name", "../escape"],
    ],
)
def test_invalid_sampling_and_output_settings_are_rejected(extra):
    base = ["--run-dir", "r", "--dataset-root", "d", "--ingestion-manifest", "m"]
    with pytest.raises(SystemExit):
        rerank.parse_args([*base, "--output-name", "x", *extra])


def test_sweep_warms_up_each_setting_and_flags_a_shallow_stage1(evaluated):
    _run, common = evaluated
    scorer = ReverseScorer()
    out = rerank.run(
        rerank.parse_args(
            [*common, "--output-name", "warm", "--fps", "1", "2", "--rerank-depth", "5"]
        ),
        scorer=scorer,
    )
    # Two settings x (one untimed warm-up + two measured queries).
    assert len(scorer.calls) == 6
    identity = json.loads((out / "identity.json").read_text())
    assert identity["rerank_depth_limited_by_stage1"] is True
    assert identity["warmup_queries_per_setting"] == 1


def test_a_failed_setting_keeps_the_finished_ones(evaluated):
    run, common = evaluated

    class FailsAtTwoFps(ReverseScorer):
        def score(self, query, windows, fps, max_frames):
            if fps == 2:
                raise RuntimeError("GPU lost")
            return super().score(query, windows, fps, max_frames)

    args = rerank.parse_args([*common, "--output-name", "partial", "--fps", "1", "2"])
    with pytest.raises(RuntimeError, match="GPU lost"):
        rerank.run(args, scorer=FailsAtTwoFps())
    out = run / "rerank/partial"
    rows = json.loads((out / "comparison.json").read_text())["rows"]
    assert {r["setting"] for r in rows} == {"fps-1_frames-32"}
    assert json.loads((out / "failed.json").read_text())["type"] == "RuntimeError"
    assert not (out / "complete.json").exists()
    assert "incomplete" in report.rerank_section(run)


def test_changed_clip_videos_are_refused(evaluated):
    run, common = evaluated
    manifest = Path(common[5])
    value = json.loads(manifest.read_text())
    value["file_sha256"] = {"a.mp4": "0" * 64}
    manifest.write_text(json.dumps(value))
    identity = json.loads((run / "identity.json").read_text())
    identity["input_sha256"][str(manifest)] = hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    (run / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="a.mp4 differs from the ingested file"):
        rerank.run(
            rerank.parse_args([*common, "--output-name", "x"]), scorer=ReverseScorer()
        )


def test_latency_ignores_queries_without_hits_and_counts_out_of_clip_windows():
    def row(candidates, seconds, outside):
        return {
            "path": "attribute",
            "domain": "pas",
            "candidates": candidates,
            "rerank_s": seconds,
            "failures": 0,
            "frames": [4] * candidates,
            "windows": [{"outside_clip": outside}] * candidates,
            "ap": {"reranked": {"10": 1.0}},
        }

    summary = rerank.summarize(
        [row(2, 0.5, True), row(0, 0.0, False)], ["reranked"], [10]
    )
    assert summary["rerank_latency"]["mean_ms"] == 500
    assert summary["queries_without_candidates"] == 1
    assert summary["windows_outside_clip"] == 2
    clips = {"s": {"path": Path("v.mp4"), "duration_s": 6.0}}
    origin = datetime(2025, 1, 1, tzinfo=UTC)
    late = rerank.hit_window(hit("s", 7, 9, 0.5), 0, clips, origin, 0.0)
    assert (late.start_s, late.end_s, late.outside_clip) == (6.0, 6.0, True)
    assert not rerank.hit_window(
        hit("s", 1, 9, 0.5), 0, clips, origin, 0.0
    ).outside_clip


def test_main_writes_the_report_unless_told_not_to(evaluated):
    run, common = evaluated
    rerank.main(
        [*common, "--output-name", "quiet", "--reranker", "identity", "--no-report"]
    )
    assert not (run / "report.html").exists()
    rerank.main([*common, "--output-name", "loud", "--reranker", "identity"])
    assert "Reranker comparison" in (run / "report.html").read_text()

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Full-gallery CLI metrics compared with the actual external Drive scorer."""

import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import drive_scoring, CliQueryBackend
import run_eval_flows as runner


@pytest.fixture
def originals():
    repo = next(
        p for p in Path(__file__).resolve().parents if (p / "libs/vss/core").is_dir()
    )
    path = Path(
        os.environ.get(
            "DRIVE_SCRIPTS_DIR",
            repo.parent / "drive/cosmos_embed1_anomaly_detection/scripts",
        )
    )
    if not (path / "evaluate_domain_test.py").is_file():
        pytest.skip("Original Drive scorer required; set DRIVE_SCRIPTS_DIR")
    return path


@pytest.fixture
def inputs(tmp_path, originals):
    gallery = [{"chunk_id": f"clip{i}"} for i in range(12)]
    queries = [
        {
            "query": "repeated",
            "query_domain": "event",
            "chunk_id": "clip0",
            "slice": "specific",
            "relevant_clip_ids": ["clip0", "clip4", "outside"],
        },
        {
            "query": "repeated",
            "query_domain": "event",
            "chunk_id": "outside",
            "slice": "anomaly",
            "relevant_clip_ids": ["clip1"],
        },
        {
            "query": "pas",
            "query_domain": "pas",
            "slice": "pas",
            "chunk_id": "clip2",
            "relevant_clip_ids": ["clip2", "clip8"],
        },
        {"query": "excluded", "near_universal": True, "relevant_clip_ids": ["clip0"]},
        {"query": "absent", "chunk_id": "outside", "relevant_clip_ids": ["outside"]},
    ]
    gt = {"meta": {}, "gallery": gallery, "queries": queries}
    data = tmp_path / "subset.json"
    data.write_text(json.dumps(gt))
    records = [
        {
            "dataset_id": g["chunk_id"],
            "sensor_id": f"sensor{i}",
            "filename": f"clip{i}.mp4",
        }
        for i, g in enumerate(gallery)
    ]
    ingestion = tmp_path / "ingestion.json"
    ingestion.write_text(json.dumps({"clips": records}))
    context = drive_scoring.prepare(str(data), str(ingestion), str(originals))
    return context, data, ingestion


@pytest.mark.parametrize("size", [2, 12])
def test_all_metrics_identical_to_original_drive(inputs, tmp_path, size):
    context, _, _ = inputs
    gt = context["gt"]
    gt["gallery"] = gt["gallery"][:size]
    context["gallery"] = context["gallery"][:size]
    context["sources"] = context["sources"][:size]
    rng = np.random.default_rng(23)
    text = rng.normal(size=(len(gt["queries"]), 5)).astype(np.float32)
    video = rng.normal(size=(size, 5)).astype(np.float32)
    # Force exact score ties, including positive/negative relevance ties.
    video[1] = video[0]
    scorer = context["scorer"]
    sims = scorer.l2_normalize(text) @ scorer.l2_normalize(video).T
    order = np.argsort(-sims, axis=1)
    expected, expected_rows = scorer.score(gt, text, video, top_k=size)

    class Backend:
        def __init__(self):
            self.calls = []

        def search(self, query):
            # A repeated query still corresponds to two independent GT rows.
            qi = len(self.calls)
            self.calls.append(query)
            return [
                {
                    "sensor_id": f"sensor{j}",
                    "similarity": float(sims[qi, j]),
                    "critic_result": {"result": "rejected"},
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T00:01:00Z",
                }
                for j in order[qi]
            ], 0.01

    backend = Backend()
    output = drive_scoring.run(context, backend, str(tmp_path / "out.json"))
    assert output["metrics"] == expected
    assert backend.calls == (
        ["repeated", "repeated", "pas"] if size == 12 else ["repeated", "repeated"]
    )
    for actual, wanted in zip(output["query_results"], expected_rows):
        assert {key: actual[key] for key in wanted} == wanted
    events = [
        r
        for r in expected_rows
        if gt["queries"][r["query_index"]].get("query_domain") == "event"
    ]
    assert output["event_summary"]["groups"]["event_only"] == scorer.aggregate(
        events, size
    )
    assert output["config"]["n_no_gallery_relevance_excluded"] == (
        1 if size == 12 else 2
    )
    assert backend.top_k == size
    assert backend.min_cosine_similarity == -1
    assert backend.search_path == "embed"
    assert backend.merge_adjacent is False
    assert backend.critic_eval_count == 1
    assert backend.video_sources == context["sources"]


def test_returned_order_is_preserved_not_sorted_by_similarity(inputs):
    context, _, _ = inputs
    results = [{"sensor_id": f"sensor{i}", "similarity": i / 20} for i in range(12)]
    row = drive_scoring.score_query(context, 0, results, 0)
    assert row["top"][0] == ("clip0", 0.0)
    assert row["rank"] == 1
    assert row["ap"] == (1 + 2 / 5) / 2


@pytest.mark.parametrize(
    "failure", ["missing", "duplicate", "foreign", "nonfinite", "no_score"]
)
def test_invalid_rankings_fail_instead_of_scoring_partial_gallery(inputs, failure):
    context, _, _ = inputs
    results = [
        {"sensor_id": f"sensor{i}", "similarity": 0.123456789} for i in range(12)
    ]
    if failure == "missing":
        results.pop()
    elif failure == "duplicate":
        results[-1] = results[0]
    elif failure == "foreign":
        results[0]["sensor_id"] = "foreign"
    elif failure == "nonfinite":
        results[0]["similarity"] = float("nan")
    else:
        results[0].pop("similarity")
    with pytest.raises(ValueError):
        drive_scoring.score_query(context, 0, results, 0)


def test_missing_ingestion_ids_fail_before_queries(inputs, originals):
    _, data, ingestion = inputs
    ingestion.write_text(json.dumps({"clips": []}))
    with pytest.raises(ValueError, match="exactly match"):
        drive_scoring.prepare(str(data), str(ingestion), str(originals))


def test_full_gallery_argv_scopes_sources_and_disables_threshold():
    backend = CliQueryBackend(
        ["vss"], top_k=80, min_cosine_similarity=-1, video_sources=["clip1", "clip2"]
    )
    argv = backend.build_argv("original query")
    assert argv[argv.index("--query") + 1] == "original query"
    assert argv[argv.index("--top-k") + 1] == "80"
    assert argv[argv.index("--min-cosine-similarity") + 1] == "-1"
    assert argv.count("--video-source") == 2
    assert "--no-merge-adjacent" in argv


def test_drive_mode_dry_run_never_decomposes_ingests_or_queries(
    inputs, originals, monkeypatch
):
    _, data, ingestion = inputs
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval_flows.py",
            "--endpoint",
            "http://example",
            "--scoring",
            "drive",
            "--drive-data",
            str(data),
            "--drive-scripts-dir",
            str(originals),
            "--ingestion-manifest",
            str(ingestion),
            "--skip-ingest",
            "--dry-run",
        ],
    )
    backend = CliQueryBackend(["vss"])
    monkeypatch.setattr(runner, "build_query_backend", lambda args: backend)

    def forbidden(*args, **kwargs):
        pytest.fail("Dry run must not perform network work or decomposition")

    monkeypatch.setattr(backend, "search", forbidden)
    monkeypatch.setattr(runner, "build_ingest_backend", forbidden)
    monkeypatch.setattr(runner.flows, "LiveDecomposer", forbidden)
    runner.main()


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--search-path", "attribute"],
        ["--merge-adjacent"],
        ["--source-type", "rtsp"],
        ["--tolerate-unanswered", "1"],
    ],
)
def test_drive_options_reject_incompatible_configuration(extra):
    argv = [
        "--endpoint",
        "http://example",
        "--scoring",
        "drive",
        "--drive-data",
        "data",
        "--drive-scripts-dir",
        "scripts",
        "--ingestion-manifest",
        "ingestion",
    ]
    if extra:
        argv += ["--skip-ingest", *extra]
    with pytest.raises(SystemExit):
        runner.parse_args(argv)


def test_existing_segment_mode_defaults_unchanged():
    args = runner.parse_args(["--endpoint", "http://example", "--scoring", "segments"])
    assert args.scoring == "segments"
    assert args.top_k == 5
    assert args.no_decompose is False

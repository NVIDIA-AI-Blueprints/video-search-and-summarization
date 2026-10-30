# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The runnable raw eval preserves canonical GT, CLI parity, and failure semantics."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_raw_clip_eval as runner


def test_backend_is_cli_by_default_and_sdk_comparison_is_explicit():
    common = [
        "--dataset-root",
        "data",
        "--ingestion-manifest",
        "ingest.json",
        "--output-dir",
        "out",
        "--modes",
        "embed-only",
    ]
    assert runner.parse_args(common).execution_backend == "cli"
    assert (
        runner.parse_args(
            [*common, "--execution-backend", "sdk", "--compare-cli"]
        ).embedding_cache_policy
        == "query"
    )
    with pytest.raises(SystemExit):
        runner.parse_args([*common, "--compare-cli"])
    with pytest.raises(SystemExit):
        runner.parse_args(common[:-1] + ["live-routed"])


def fixture_dataset(tmp_path):
    def write(p, x):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(x))

    (tmp_path / "clips").mkdir()
    (tmp_path / "clips/a.mp4").touch()
    write(
        tmp_path / "additional_gt/clips_gt.json", {"gallery": [{"chunk_id": "clip-a"}]}
    )
    q = {
        "query_id": "event_a",
        "query": "forklift",
        "query_domain": "event",
        "relevant_clip_ids": ["clip-a"],
    }
    write(tmp_path / "gt/queries_gt.json", {"queries": [q]})
    write(
        tmp_path / "ingest.json",
        {
            "complete": True,
            "upload_timestamp": "2025-01-01T00:00:00",
            "clips": [
                {
                    "dataset_id": "clip-a",
                    "sensor_id": "sensor",
                    "filename": "a.mp4",
                    "duration_s": 5.5,
                    "success": True,
                }
            ],
        },
    )
    return q


def test_query_selection_reads_ids_only_and_uses_canonical_gt(tmp_path):
    canonical = fixture_dataset(tmp_path)
    selection = tmp_path / "select.json"
    selection.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query_id": "event_a",
                        "query": "oracle rewrite",
                        "relevant_clip_ids": [],
                        "decomposition": {"path": "fusion"},
                    }
                ]
            }
        )
    )
    qs, _, sensors, scope = runner.load_inputs(
        tmp_path, tmp_path / "ingest.json", selection
    )
    assert qs == [canonical]
    assert sensors == {"sensor": "clip-a"}
    assert scope["timestamp_end"] == "2025-01-01T00:00:06Z"
    selection.write_text('["event_a", "event_a"]')
    with pytest.raises(ValueError, match="unique"):
        runner.load_inputs(tmp_path, tmp_path / "ingest.json", selection)


def test_incomplete_or_foreign_gallery_never_runs(tmp_path):
    fixture_dataset(tmp_path)
    p = tmp_path / "ingest.json"
    manifest = runner.read(p)
    manifest["complete"] = False
    runner.save(p, manifest)
    with pytest.raises(ValueError, match="complete gallery"):
        runner.load_inputs(tmp_path, p, None)
    manifest["complete"] = True
    manifest["clips"][0]["dataset_id"] = "foreign"
    runner.save(p, manifest)
    with pytest.raises(ValueError, match="identities differ"):
        runner.load_inputs(tmp_path, p, None)


@pytest.mark.parametrize(
    "mode,path",
    [
        ("embed-only", "embed"),
        ("attribute-only", "attribute"),
        ("live-routed", "fusion"),
    ],
)
def test_same_payload_builds_matching_cli_command(mode, path):
    args = SimpleNamespace(
        top_k=10,
        min_cosine_similarity=-1,
        candidate_limit=100000,
        max_hits_per_source=1,
    )
    plan = (
        {
            "path": "fusion",
            "query": "walking",
            "attributes": ["red shirt"],
            "video_sources": ["source"],
            "top_k": 99,
        }
        if mode == "live-routed"
        else {}
    )
    selected, p = runner.build_request(
        "a person walking in a red shirt",
        mode,
        plan,
        {"timestamp_start": "start", "timestamp_end": "end"},
        args,
    )
    assert selected == path and p["top_k"] == 10
    argv = runner.cli_argv(["vss"], path, p, False)
    assert argv[:4] == ["vss", "search", "run", path]
    assert {"--no-critic", "--no-persist", "--no-merge-adjacent", "--raw"} <= set(argv)
    if path == "attribute":
        assert p["attributes"] == ["a person walking in a red shirt"]
        assert "--candidate-limit" in argv
    if path == "fusion":
        assert p["query"] == "walking" and p["attributes"] == ["red shirt"]
        assert argv[argv.index("--video-source") + 1] == "source"
        assert "--candidate-limit" not in argv


def test_unsupported_live_route_never_falls_back():
    with pytest.raises(ValueError, match="never silently falls back"):
        runner.build_request(
            "q", "live-routed", {"path": "object"}, {}, SimpleNamespace(top_k=10)
        )


def test_cli_failure_does_not_become_empty_results():
    with pytest.raises(RuntimeError, match="CLI exited 3"):
        runner.capture_cli([sys.executable, "-c", "import sys;sys.exit(3)"])
    with pytest.raises(ValueError, match="exactly one result"):
        runner.capture_cli([sys.executable, "-c", "print('{}')"])
    valid = runner.capture_cli(
        [sys.executable, "-c", """print('{"data": [], "persisted": false}')"""]
    )
    assert valid["data"] == [] and valid["exit_code"] == 0


def test_unavailable_routing_reference_option_is_not_exposed():
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--dataset-root",
                "data",
                "--ingestion-manifest",
                "ingest.json",
                "--output-dir",
                "out",
                "--routing-reference",
                "routes.json",
            ]
        )

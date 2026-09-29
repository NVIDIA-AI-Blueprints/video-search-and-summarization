# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The harness runner refuses unprepared or mismatched benchmark work."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_eval_flows as runner
from flows.dataset_spec import load_dataset_spec


@pytest.mark.parametrize("task", ["segment", "clip"])
def test_arbitrary_dataset_spec_registers_subsets(tmp_path: Path, task: str) -> None:
    root = tmp_path / "new-benchmark"
    (root / "videos").mkdir(parents=True)
    (root / "videos" / "clip.mp4").write_bytes(b"video")
    (root / "dataset.json").write_text('{"question": {"video": "clip.mp4"}}')
    (root / "hard.json").write_text('{"question": {"video": "clip.mp4"}}')
    spec_path = tmp_path / "benchmark-spec.json"
    spec_path.write_text(json.dumps({"name": "new-benchmark", "task": task,
        "subsets": {"": "dataset.json", "hard": "hard.json"}, "videos": ["clip.mp4"]}))
    spec = load_dataset_spec(spec_path, tmp_path)
    assert spec["task"] == task
    from flows import DATASETS, dataset_meta
    assert DATASETS["new-benchmark"]["hard"] == "new-benchmark/hard.json"
    assert dataset_meta("new-benchmark").task == task


def test_dataset_spec_rejects_traversal(tmp_path: Path) -> None:
    path = tmp_path / "benchmark-spec.json"
    path.write_text(json.dumps({"name": "safe", "task": "clip",
        "subsets": {"": "../outside.json"}, "videos": ["clip.mp4"]}))
    with pytest.raises(ValueError, match="ground truth filename"):
        load_dataset_spec(path, tmp_path)


def test_score_receipt_rejects_mismatch_and_unverified_anchor(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    identity = {"target_id": "target", "gateway_origin": "http://vss",
        "vios_origin": "http://vios", "chart_version": "1", "image_digest": "sha256:abc",
        "dataset_checksum": "abc", "dataset": "warehouse"}
    receipt = {"version": 1, "status": "prepared", **identity,
        "index_ok": True, "anchors_ok": True, "index": {"covered": True},
        "expected_source_count": 1, "verified_source_count": 1,
        "anchors": {"clip.mp4": {"checked": True, "found": True,
                               "matches_expected_anchor": True}}}
    receipt_path.write_text(json.dumps(receipt))
    args = runner.parse_args(["--endpoint", "http://vss", "--vss-base-url", "http://vss",
        "--vst-url", "http://vios", "--receipt", str(receipt_path),
        "--target-id", "target", "--chart-version", "1", "--image-digest", "sha256:abc",
        "--dataset-checksum", "abc"])
    assert runner._validate_receipt(args) == receipt
    args.image_digest = "sha256:different"
    with pytest.raises(SystemExit, match="image_digest"):
        runner._validate_receipt(args)
    args.image_digest = "sha256:abc"
    receipt["anchors"]["clip.mp4"]["found"] = False
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(SystemExit, match="anchors"):
        runner._validate_receipt(args)


def test_search_chart_notification_source_enables_embed_webhook() -> None:
    repo = Path(__file__).resolve().parents[5]
    config = json.loads((repo / "deploy/helm/developer-profiles/dev-profile-search"
                         / "configs/vios/notification_config.json").read_text())
    assert config["webhooks"]["enabled"] is True
    embed = [item for item in config["webhooks"]["items"] if "embed" in item.get("id", "")]
    assert embed and all(item.get("enabled") is True for item in embed)
    assert "__RTVI_EMBED_ADDRESS__" in json.dumps(embed)


def test_strict_index_probe_checks_each_source_even_when_broad_search_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.flows, "list_sensor_streams", lambda _: {"id": "clip.mp4"})

    class Probe:
        def __init__(self, source: str | None) -> None:
            self.source = source

        def search(self, _: str) -> tuple[list[dict[str, str]], float]:
            return ([{"video_name": "clip.mp4"}] if self.source is None else [], 0.0)

    monkeypatch.setattr(runner, "_probe_backend", lambda _q, _k, source=None: Probe(source))
    result = runner.probe_index_coverage(object(), ["clip.mp4"], vst_url="http://vios",
        attempts=1, backoff_s=0, strict_per_source=True)
    assert result["covered"] is False
    assert result["missing_count"] == 1


def test_strict_index_probe_rejects_hits_from_another_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.flows, "list_sensor_streams", lambda _: {"id": "clip.mp4"})

    class Probe:
        def search(self, _: str) -> tuple[list[dict[str, str]], float]:
            return ([{"video_name": "unrelated.mp4"}], 0.0)

    monkeypatch.setattr(runner, "_probe_backend", lambda *_a, **_k: Probe())
    result = runner.probe_index_coverage(object(), ["clip.mp4"], vst_url="http://vios",
        attempts=1, backoff_s=0, strict_per_source=True)
    assert result["covered"] is False
    assert result["missing_count"] == 1


def test_attribute_probe_requires_a_hit_from_each_uploaded_source(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_sources: list[str] = []

    class Query:
        vss_cmd = ("vss",)
        cwd = None

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            seen_sources.append(kwargs["decompositions"][runner.PROBE_QUERY]["video_sources"][0])

        def search(self, _: str) -> tuple[list[dict[str, str]], float]:
            return ([{"video_name": "unrelated.mp4"}], 0.0)

    monkeypatch.setattr(runner.flows, "CliQueryBackend", Probe)
    result = runner.probe_attribute_coverage(Query(), ["clip.mp4"], {"clip.mp4": "clip.mp4"},
        ["person"], attempts=1, backoff_s=0)
    assert seen_sources == ["clip.mp4"]
    assert result["covered"] is False


def test_attribute_probe_verifies_each_source_even_with_vios_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    class Query:
        vss_cmd = ("vss",)
        cwd = None

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            self.source = kwargs["decompositions"][runner.PROBE_QUERY]["video_sources"][0]

        def search(self, _: str) -> tuple[list[dict[str, str]], float]:
            return ([{"video_name": self.source}], 0.0)

    monkeypatch.setattr(runner.flows, "CliQueryBackend", Probe)
    result = runner.probe_attribute_coverage(Query(), ["a.mp4", "b.mp4"],
        {"a.mp4": "a_20250101_000000_e0482.mp4", "b.mp4": "b.mp4"},
        ["person"], attempts=1, backoff_s=0)
    assert result["covered"] is True
    assert result["sources_verified"] == 2


def test_attribute_score_receipt_needs_per_source_evidence(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    identity = {"target_id": "target", "gateway_origin": "http://vss",
        "vios_origin": "http://vios", "chart_version": "1", "image_digest": "sha256:abc",
        "dataset_checksum": "abc", "dataset": "warehouse"}
    receipt = {"version": 1, "status": "prepared", **identity,
        "index_ok": True, "anchors_ok": True,
        "index": {"covered": True, "attribute_ready": True, "attribute_sources_verified": 0},
        "expected_source_count": 1, "verified_source_count": 1,
        "anchors": {"clip.mp4": {"checked": True, "found": True,
                               "matches_expected_anchor": True}}}
    receipt_path.write_text(json.dumps(receipt))
    args = runner.parse_args(["--endpoint", "http://vss", "--vss-base-url", "http://vss",
        "--vst-url", "http://vios", "--receipt", str(receipt_path),
        "--target-id", "target", "--chart-version", "1", "--image-digest", "sha256:abc",
        "--dataset-checksum", "abc", "--search-path", "attribute", "--attribute", "person"])
    with pytest.raises(SystemExit, match="attribute index"):
        runner._validate_receipt(args)


def test_ingest_discovers_all_onboarded_video_extensions(tmp_path: Path) -> None:
    names = ["a.mp4", "b.mkv", "c.mov", "d.avi"]
    for name in names:
        (tmp_path / name).write_bytes(b"video")

    class Backend:
        name = "fake"

        def upload(self, path: Path) -> dict:
            return {"video_name": path.name, "success": True, "sensor_id": path.stem,
                    "upload_latency_s": 0.01, "phases": {}}

    result = runner.ingest_videos(Backend(), tmp_path)
    assert [row["video_name"] for row in result["per_file"]] == names


def test_fixed_benchmark_score_ignores_carried_query_decompositions(tmp_path: Path) -> None:
    data = {"schema_version": 3, "task": "clip", "queries": {"person walking": {
        "relevant_clips": ["clip"],
        "decomposition": {"attributes": ["person"], "has_action": False}}}}
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "dataset.json").write_text(json.dumps(data))
    runner.flows.DATASETS["fixture"] = {"": "fixture/dataset.json"}

    def score(fixed_search_path: bool) -> tuple[str, dict]:
        backend = runner.flows.CliQueryBackend(["vss"], search_path="embed")

        def search(query: str) -> tuple[list, float]:
            backend.executed_plans.append(backend.plan_for_query(query))
            return [], 0.01

        backend.search = search
        result = runner.run_evaluation(backend, tmp_path, "fixture", "", task="clip",
                                       output_file=str(tmp_path / f"{fixed_search_path}.json"),
                                       fixed_search_path=fixed_search_path)
        return backend.executed_plans[0]["path"], result

    assert score(False)[0] == "attribute"
    path, result = score(True)
    assert path == "embed"
    assert result["summary"]["total_queries"] == 1


def test_dataset_spec_rejects_ambiguous_video_stems(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    (root / "videos").mkdir(parents=True)
    for name in ("clip.mp4", "clip.mov"):
        (root / "videos" / name).write_bytes(b"video")
    (root / "dataset.json").write_text('{"query": ["clip"]}')
    spec_path = tmp_path / "benchmark-spec.json"
    spec_path.write_text(json.dumps({"name": "fixture", "task": "clip",
                                     "subsets": {"": "dataset.json"},
                                     "videos": ["clip.mp4", "clip.mov"]}))
    with pytest.raises(ValueError, match="duplicate video stem"):
        load_dataset_spec(spec_path, tmp_path)

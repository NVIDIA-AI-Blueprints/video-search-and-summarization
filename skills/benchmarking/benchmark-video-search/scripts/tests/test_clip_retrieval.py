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
"""Tests for clip-level retrieval support (physicalAI-event-videos-test).

Covers the plan-review fixes inline: stems-not-``.mp4`` (F1), dedupe by
clip before precision (F2), k capped at top_k (F3), near_universal exclusion (F4),
task-aware HIT@k in the summary (F5), and the ``unpack_dataset`` clip-dict
arm that must precede the ``or []`` chain (F6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/tests -> scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows
import run_eval_flows as rf

# -- metrics.evaluate_clip_query -------------------------------------------------


def test_clip_scorer_matches_stem_not_filename() -> None:
    """A retrieved `<stem>_<timestamp>.mp4` prefix-matches the GT stem; a GT
    string with ``.mp4`` would never match. [F1]"""
    res = flows.evaluate_clip_query(
        "q",
        [{"video_name": "CHAD_1_086_1_0_20250921_000000_abcd.mp4"}],
        ["CHAD_1_086_1_0"],
        0.1,
        hit_ks=(1, 5, 10),
    )
    assert res["true_positives"] == 1
    assert res["recall"] == 1.0


def test_clip_scorer_dedupes_segments_per_clip() -> None:
    """A clip chunked at ingest can return several segments sharing one
    ``video_name``; they must count as 1 retrieved / 1 relevant, not N/1. [F2]"""
    res = flows.evaluate_clip_query(
        "q",
        [
            {
                "video_name": "CHAD_1_086_1_0_2025.mp4",
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T00:00:02Z",
            },
            {
                "video_name": "CHAD_1_086_1_0_2025.mp4",
                "start_time": "2025-01-01T00:00:02Z",
                "end_time": "2025-01-01T00:00:04Z",
            },
        ],
        ["CHAD_1_086_1_0"],
        0.1,
        hit_ks=(1, 5, 10),
    )
    assert res["total_retrieved"] == 1  # deduped to one clip
    assert res["true_positives"] == 1
    assert res["precision"] == 1.0


def test_clip_scorer_claims_each_relevant_clip_once() -> None:
    """Two results matching the same relevant clip claim it once; the second is
    a false positive, mirroring segment scoring's one-claim-per-GT rule."""
    res = flows.evaluate_clip_query(
        "q",
        [
            {"video_name": "CHAD_1_086_1_0_2025.mp4"},
            {"video_name": "CHAD_1_086_1_0_2026.mp4"},  # same clip, renamed
        ],
        ["CHAD_1_086_1_0"],
        0.1,
        hit_ks=(1, 5, 10),
    )
    assert res["true_positives"] == 1
    assert res["false_positives"] == 1


def test_clip_scorer_k_set_is_what_caller_passed() -> None:
    """hit_at_k has exactly the requested k; the caller caps k at --top-k
    because clip has no segment expansion. [F3]"""
    res = flows.evaluate_clip_query("q", [], ["CHAD_1"], 0.1, hit_ks=(1, 5, 10))
    assert set(res["hit_at_k"]) == {1, 5, 10}


# -- routing.unpack_dataset (F6) -------------------------------------------------


def test_unpack_clip_dict_does_not_zero_out() -> None:
    """A schema_version 3 clip dict must load as clip annotations, not fall
    through ``or []`` and silently zero every query. [F6]"""
    data = {
        "schema_version": 3,
        "task": "clip",
        "queries": {
            "q1": {
                "relevant_clips": ["CHAD_1_086_1_0"],
                "decomposition": {"query": "q1", "attributes": [], "has_action": True},
            },
            "q2": {"relevant_clips": ["CHAD_2_064_1_0", "CHAD_2_064_1_1"]},
        },
    }
    annotations, decompositions = flows.unpack_dataset(data)
    assert annotations["q1"] == ["CHAD_1_086_1_0"]
    assert annotations["q2"] == ["CHAD_2_064_1_0", "CHAD_2_064_1_1"]
    assert decompositions["q1"]["has_action"] is True
    assert "q2" not in decompositions  # no decomposition on q2


def test_unpack_segment_shapes_are_unchanged() -> None:
    """Legacy (bare list) and extended (segments + decomposition) still load
    as segment annotations -- regression guard for the new clip arm."""
    seg = [
        {
            "video_name": "v",
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:05Z",
        }
    ]
    legacy, _ = flows.unpack_dataset({"queries": {"q1": seg}})
    extended, _ = flows.unpack_dataset({"queries": {"q1": {"segments": seg}}})
    assert legacy["q1"] == seg
    assert extended["q1"] == seg


# -- dataset metadata --------------------------------------------------------


def test_dataset_meta_defaults_to_segment_umbrella() -> None:
    """Unknown datasets default to the umbrella segment task; the clip
    dataset is wired to its own DSS source + clip scoring."""
    assert flows.dataset_meta("warehouse").task == "segment"
    assert flows.dataset_meta("warehouse").dss == flows.DSS_DATASET_NAME
    assert flows.dataset_meta("warehouse").prefix_filter is True

    clip = flows.dataset_meta("physicalAI-event-videos-test")
    assert clip.task == "clip"
    assert clip.dss == "physicalAI-event-videos-test"
    assert clip.prefix_filter is False
    assert clip.hit_ks == (1, 5, 10)


def test_datasets_registry_keeps_subset_map_shape() -> None:
    """DATASETS stays dict[str, dict[str,str]] for run_eval.py parity; the new
    dataset has the expected subsets."""
    assert isinstance(flows.DATASETS["physicalAI-event-videos-test"], dict)
    assert (
        flows.DATASETS["physicalAI-event-videos-test"][""]
        == "physicalAI-event-videos-test/dataset.json"
    )
    assert flows.DATASETS["physicalAI-event-videos-test"]["event"].endswith(
        "dataset_event.json"
    )
    assert flows.DATASETS["physicalAI-event-videos-test"]["pas"].endswith(
        "dataset_pas.json"
    )


# -- _summarize task-aware k-set (F5) + favg ABSENT exclusion (P1 #7) ---------


def _clip_result(query: str, hits: list[dict], relevant: list[str], had: bool) -> dict:
    r = flows.evaluate_clip_query(query, hits, relevant, 0.1, hit_ks=(1, 5, 10))
    r["critic_filtered"] = flows.evaluate_clip_query(
        query, hits, relevant, 0.1, hit_ks=(1, 5, 10)
    )
    r["_had_verification"] = had
    return r


def test_summarize_uses_clip_k_set() -> None:
    """A clip run's summary reports HIT@{1,5,10}, not the segment [1,3,5,10]. [F5]"""
    res = _clip_result(
        "q", [{"video_name": "CHAD_1_086_1_0_2025.mp4"}], ["CHAD_1_086_1_0"], had=True
    )
    summ = rf._summarize(
        [res],
        dataset="physicalAI-event-videos-test",
        subset="",
        wall_clock_s=0.1,
        concurrency=1,
        sources_seen={"verification"},
        upload_stats=None,
        verdicts={"confirmed": 1},
        path_counts={"embed": 1},
    )
    assert [k for k in summ if k.startswith("HIT@")] == ["HIT@1", "HIT@5", "HIT@10"]


def test_summarize_critic_filtered_excludes_absent_queries() -> None:
    """A mixed run where one query is VERIFICATION_ABSENT must not average
    its raw numbers under the critic-filtered label. [P1 #7]"""
    hit = _clip_result(
        "hit", [{"video_name": "CHAD_1_086_1_0_2025.mp4"}], ["CHAD_1_086_1_0"], had=True
    )
    miss = _clip_result(
        "miss", [{"video_name": "OTHER.mp4"}], ["CHAD_1_086_1_0"], had=False
    )
    summ = rf._summarize(
        [hit, miss],
        dataset="physicalAI-event-videos-test",
        subset="",
        wall_clock_s=0.2,
        concurrency=1,
        sources_seen={"verification"},
        upload_stats=None,
        verdicts={"confirmed": 1},
        path_counts={"embed": 2},
    )
    # critic_filtered aggregates over the 1 verified query only (hit recall 1.0),
    # not the 2-query n (which would drag it to 0.5).
    assert summ["critic_filtered"]["avg_recall"] == 1.0


# -- preprocessing adapter ---------------------------------------------------


def _write_raw_layout(root: Path) -> Path:
    """A miniature physicalAI-event-videos-test layout on disk."""
    (root / "clips").mkdir(parents=True)
    (root / "gt").mkdir(parents=True)
    for name in ("CHAD_1_086_1_0.mp4", "CHAD_2_064_1_0.mp4", "CHAD_2_064_1_1.mp4"):
        (root / "clips" / name).write_bytes(b"")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "physicalAI-event-videos-test",
                "clips": [
                    {"chunk_id": "CHAD/1_086_1#0", "path": "clips/CHAD_1_086_1_0.mp4"},
                    {"chunk_id": "CHAD/2_064_1#0", "path": "clips/CHAD_2_064_1_0.mp4"},
                    {"chunk_id": "CHAD/2_064_1#1", "path": "clips/CHAD_2_064_1_1.mp4"},
                ],
            }
        )
    )
    (root / "gt" / "queries_gt.json").write_text(
        json.dumps(
            {
                "meta": {"n_queries": 3, "n_gallery": 3},
                "queries": [
                    {
                        "query": "a forklift moves left",
                        "query_id": "event_0000",
                        "query_domain": "event",
                        "slice": "specific",
                        "near_universal": False,
                        "relevant_clip_ids": ["CHAD/1_086_1#0"],
                        "n_relevant": 1,
                    },
                    {
                        "query": "a person in a red jacket",
                        "query_id": "pas_0000",
                        "query_domain": "pas",
                        "slice": "easy",
                        "near_universal": False,
                        "relevant_clip_ids": ["CHAD/2_064_1#0", "CHAD/2_064_1#1"],
                        "n_relevant": 2,
                    },
                    {
                        "query": "a nearly universal scene",
                        "query_id": "event_0001",
                        "query_domain": "event",
                        "slice": "scene",
                        "near_universal": True,
                        "relevant_clip_ids": ["CHAD/1_086_1#0"],
                        "n_relevant": 1,
                    },
                ],
            }
        )
    )
    return root


def test_make_clip_dataset_writes_stems_and_excludes_near_universal(
    tmp_path: Path,
) -> None:
    """The adapter resolves chunk_id -> stems (no .mp4) [F1], drops
    near_universal queries [F4], writes event/pas subsets, and links
    videos/ -> clips/."""
    from flows.preprocess.make_clip_dataset import make_clip_dataset

    root = _write_raw_layout(tmp_path / "physicalAI-event-videos-test")
    make_clip_dataset(root, "physicalAI-event-videos-test")

    data = json.loads((root / "dataset.json").read_text())
    assert data["task"] == "clip"
    q = data["queries"]
    assert "a forklift moves left" in q
    assert q["a forklift moves left"]["relevant_clips"] == [
        "CHAD_1_086_1_0"
    ]  # stem, not .mp4 [F1]
    assert (
        q["a forklift moves left"]["decomposition"]["has_action"] is True
    )  # event -> fusion [F9]
    assert (
        q["a person in a red jacket"]["decomposition"]["has_action"] is False
    )  # pas -> attribute [F9]
    assert "a nearly universal scene" not in q  # near_universal excluded [F4]

    event = json.loads((root / "dataset_event.json").read_text())["queries"]
    assert set(event) == {"a forklift moves left"}  # event subset
    pas = json.loads((root / "dataset_pas.json").read_text())["queries"]
    assert set(pas) == {"a person in a red jacket"}  # pas subset

    assert (root / "videos").is_symlink() or (root / "videos").is_dir()
    assert (root / "videos" / "CHAD_1_086_1_0.mp4").exists()


def test_make_clip_dataset_is_idempotent(tmp_path: Path) -> None:
    """A second run with no changes to queries_gt.json does no work (and does
    not raise)."""
    from flows.preprocess.make_clip_dataset import make_clip_dataset

    root = _write_raw_layout(tmp_path / "physicalAI-event-videos-test")
    make_clip_dataset(root, "physicalAI-event-videos-test")
    out = root / "dataset.json"
    before = out.read_text()
    make_clip_dataset(root, "physicalAI-event-videos-test")  # idempotent
    assert out.read_text() == before


# -- _print_summary P0 pin + effective_hit_ks cap + F1 negative ----------------


def test_print_summary_does_not_keyerror_on_clip_k_set(capsys) -> None:
    """_print_summary must derive the HIT@k set from the summary (task-aware),
    not the hardcoded segment [1,3,5,10], or a clip run (HIT@1,5,10) KeyErrors at HIT@3
    before the result file is written. P0 from adversarial review."""
    import contextlib
    import io

    res = _clip_result("q", [{"video_name": "CHAD_1_086_1_0_2025.mp4"}], ["CHAD_1_086_1_0"], had=True)
    summ = rf._summarize(
        [res], dataset="physicalAI-event-videos-test", subset="", wall_clock_s=0.1,
        concurrency=1, sources_seen={"verification"}, upload_stats=None,
        verdicts={"confirmed": 1}, path_counts={"embed": 1},
    )
    # Must not raise; must print exactly the clip k-set, not HIT@3.
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rf._print_summary(summ)
    out = buf.getvalue()
    assert "HIT@1" in out and "HIT@5" in out and "HIT@10" in out
    assert "HIT@3" not in out


def test_effective_hit_ks_capped_at_top_k() -> None:
    """`--top-k 5` must cap the clip hit@k set to (1,5); 10 is unmeasurable
    without segment expansion. Pins run_eval_flows.py:1494 (F3) -- the line the suite did not cover."""
    args = rf.parse_args([
        "--endpoint", "http://h:8000", "--dataset", "physicalAI-event-videos-test",
        "--top-k", "5", "--skip-download", "--dry-run",
    ])
    meta = flows.dataset_meta(args.dataset)
    parsed = tuple(int(k) for k in args.hit_ks.split(",")) if args.hit_ks else meta.hit_ks
    effective = tuple(k for k in parsed if k <= args.top_k)
    assert effective == (1, 5)  # 10 capped out


def test_clip_scorer_gt_with_extension_never_matches() -> None:
    """A GT string with ``.mp4`` never matches: ``video_name_matches`` strips
    ``.mp4`` from the retrieved name only and prefix-matches, so the adapter
    must store stems. Negative case for F1."""
    res = flows.evaluate_clip_query(
        "q", [{"video_name": "CHAD_1_086_1_0_2025.mp4"}],
        ["CHAD_1_086_1_0.mp4"], 0.1, hit_ks=(1, 5, 10),  # wrong: extension on GT
    )
    assert res["true_positives"] == 0
    assert res["recall"] == 0.0

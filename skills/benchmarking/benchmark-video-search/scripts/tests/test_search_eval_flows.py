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

"""Tests for the pluggable search-eval flow backends.

The new search flow needs a live deployment to exercise end to end, so
everything that CAN be checked without one is checked here: result
normalization across both wire formats, the silent-unfiltered-scoring trap,
CLI argument construction per retrieval path, stdout parsing, decomposition
routing, and VST name matching.

    python3 -m pytest scripts/tests/test_search_eval_flows.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

# scripts/tests -> scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows

# Representative hits in each wire format. Field names taken from
# vss_core.search_core.models.search.SearchResult (new) and
# vss_agents.tools.search.SearchResult / CriticResult (legacy).
NEW_FLOW_HIT: dict[str, Any] = {
    "video_name": "warehouse_01.mp4",
    "description": "a forklift moves left",
    "start_time": "2025-01-01T00:00:10Z",
    "end_time": "2025-01-01T00:00:15Z",
    "sensor_id": "abc-123",
    "screenshot_url": "http://vst/screenshot.jpg",
    "similarity": 0.82,
    "object_ids": ["7"],
    "verification": {"result": "confirmed", "criteria_met": {"forklift": True}},
}

LEGACY_HIT: dict[str, Any] = {
    **{k: v for k, v in NEW_FLOW_HIT.items() if k != "verification"},
    "critic_result": {"result": "rejected", "criteria_met": {"forklift": False}},
}

NO_VERIFICATION_HIT: dict[str, Any] = {k: v for k, v in NEW_FLOW_HIT.items() if k != "verification"}

SCORING_FIELDS = ("video_name", "start_time", "end_time", "similarity")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_new_flow_verification_is_read() -> None:
    result = flows.normalize_result(NEW_FLOW_HIT)
    assert result["verification"]["result"] == "confirmed"
    assert result["_verification_source"] == "verification"


def test_legacy_critic_result_is_read() -> None:
    result = flows.normalize_result(LEGACY_HIT)
    assert result["verification"]["result"] == "rejected"
    assert result["_verification_source"] == "critic_result"


def test_both_flows_normalize_to_identical_scoring_fields() -> None:
    """The whole point: metrics must not be able to tell the flows apart."""
    new = flows.normalize_result(NEW_FLOW_HIT)
    legacy = flows.normalize_result(LEGACY_HIT)
    assert {k: new[k] for k in SCORING_FIELDS} == {k: legacy[k] for k in SCORING_FIELDS}


def test_missing_verification_block_is_flagged_absent() -> None:
    result = flows.normalize_result(NO_VERIFICATION_HIT)
    assert result["verification"]["result"] == "unverified"
    assert result["_verification_source"] == flows.VERIFICATION_ABSENT


def test_null_verification_is_absent_not_a_verdict() -> None:
    """``verification: null`` means the critic never ran for this hit.

    Treating it as an empty verdict would re-enable critic-filtered metrics on
    a response that was never verified.
    """
    result = flows.normalize_result({**NEW_FLOW_HIT, "verification": None})
    assert result["_verification_source"] == flows.VERIFICATION_ABSENT


def test_similarity_score_alias_accepted() -> None:
    result = flows.normalize_result({**NO_VERIFICATION_HIT, "similarity_score": 0.5, "similarity": None})
    assert result["similarity"] in (0.5, None)


def test_for_scoring_drops_bookkeeping_keys() -> None:
    scored = flows.for_scoring(flows.normalize_results([NEW_FLOW_HIT]))
    assert all(not k.startswith("_") for k in scored[0])
    assert scored[0]["video_name"] == "warehouse_01.mp4"


# ---------------------------------------------------------------------------
# The regression this layer exists to prevent
# ---------------------------------------------------------------------------


def test_legacy_reader_silently_misses_new_flow_rejections() -> None:
    """Documents the bug, so nobody 'simplifies' the normalizer away.

    A reader that knows only ``critic_result`` finds zero rejections against
    the new flow and scores an UNFILTERED set under a filtered label.
    """
    raw = [NEW_FLOW_HIT, {**NEW_FLOW_HIT, "verification": {"result": "rejected"}}]
    missed_by_legacy_reader = sum(
        1 for r in raw if (r.get("critic_result") or {}).get("result") == "rejected"
    )
    assert missed_by_legacy_reader == 0

    _, rejected = flows.filter_rejected(flows.normalize_results(raw))
    assert rejected == 1


def test_absent_verification_is_detectable() -> None:
    assert not flows.has_verification(flows.normalize_results([NO_VERIFICATION_HIT]))
    assert flows.has_verification(flows.normalize_results([NEW_FLOW_HIT]))


# ---------------------------------------------------------------------------
# CLI argument construction
# ---------------------------------------------------------------------------


def test_embed_path_argv() -> None:
    backend = flows.CliQueryBackend(["vss"], search_path="embed", top_k=10, min_cosine_similarity=0.3)
    argv = backend.build_argv("red forklift")
    assert argv[:4] == ["vss", "search", "run", "embed"]
    assert "--query" in argv and "red forklift" in argv
    assert "--attribute" not in argv
    assert argv[argv.index("--top-k") + 1] == "10"


def test_attribute_path_takes_no_query() -> None:
    backend = flows.CliQueryBackend(["vss"], search_path="attribute", attributes=["white jacket"])
    argv = backend.build_argv("ignored for this path")
    assert "--query" not in argv
    assert "white jacket" in argv


def test_fusion_path_carries_both_legs() -> None:
    backend = flows.CliQueryBackend(["vss"], search_path="fusion", attributes=["red hard hat"])
    argv = backend.build_argv("person running")
    assert "--query" in argv and "--attribute" in argv


def test_merging_is_off_by_default_for_comparability() -> None:
    """Upstream merges by default; the eval must not, or the baseline breaks."""
    assert "--no-merge-adjacent" in flows.CliQueryBackend(["vss"]).build_argv("q")
    assert "--no-merge-adjacent" not in flows.CliQueryBackend(["vss"], merge_adjacent=True).build_argv("q")


def test_invalid_search_path_rejected_before_any_request() -> None:
    try:
        flows.CliQueryBackend(["vss"], search_path="nonsense")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown search path")


# ---------------------------------------------------------------------------
# CLI output parsing
# ---------------------------------------------------------------------------


def test_parses_searchoutput_envelope() -> None:
    hits, messages, timings = flows.parse_cli_output(
        '{"data": [{"video_name": "a.mp4"}], "search_messages": ["hi"]}'
    )
    assert len(hits) == 1
    assert messages == ["hi"]
    assert timings == {}


def test_parses_stage_timings_when_present() -> None:
    """Deployments carrying the search_core timings change report per-stage cost."""
    hits, _messages, timings = flows.parse_cli_output(
        '{"data": [], "search_messages": [], "timings": {"stages": '
        '{"embed_search: ES search execution": '
        '{"total_s": 0.7395, "self_s": 0.7395, "calls": 1, "concurrent_children": 0.0}}, '
        '"total_s": 1.4964}}'
    )
    assert hits == []
    assert timings["total_s"] == 1.4964
    stage = timings["stages"]["embed_search: ES search execution"]
    assert stage["self_s"] == 0.7395
    assert stage["concurrent_children"] == 0.0


def test_absent_timings_are_empty_not_zero() -> None:
    """A deployment without the change must not look like a search that took no time."""
    _hits, _messages, timings = flows.parse_cli_output('{"data": []}')
    assert timings == {}


def test_timings_are_kept_per_query_not_in_one_slot() -> None:
    """Queries run concurrently; a single slot would cross-contaminate them."""
    backend = flows.CliQueryBackend(["vss"])
    assert backend.timings_by_query == {}
    backend.timings_by_query["q1"] = {"total_s": 1.0}
    backend.timings_by_query["q2"] = {"total_s": 2.0}
    assert backend.timings_by_query["q1"]["total_s"] == 1.0


def test_parses_bare_list() -> None:
    hits, messages, _timings = flows.parse_cli_output('[{"video_name": "a.mp4"}]')
    assert len(hits) == 1
    assert messages == []


def test_parses_empty_and_null_payloads() -> None:
    assert flows.parse_cli_output("")[0] == []
    assert flows.parse_cli_output("   \n ")[0] == []
    assert flows.parse_cli_output('{"data": [], "search_messages": []}')[0] == []
    assert flows.parse_cli_output('{"data": null}')[0] == []


def test_parses_ndjson_with_a_trailing_job_event() -> None:
    """The real CLI shape: result envelope + lifecycle event, both on stdout.

    Regression for the bug that made a working CLI report mAP 0.0000 -- a
    whole-buffer json.loads raises "Extra data" here, and 106 of 121 queries
    were silently discarded.
    """
    stdout = (
        '{"data": [{"video_name": "Vandalism040_x264.mp4"}], "search_messages": [], '
        '"job_id": "search-01M1DV0K", "persisted": false, "record": "absent"}\n'
        '{"event":"vss_job_completed","group":"search","job_id":"search-01M1DV0K",'
        '"status":"completed","exit_hint":0}'
    )
    hits, messages, _timings = flows.parse_cli_output(stdout)
    assert len(hits) == 1
    assert hits[0]["video_name"] == "Vandalism040_x264.mp4"
    assert messages == []


def test_job_event_alone_is_not_mistaken_for_results() -> None:
    hits, _, _ = flows.parse_cli_output('{"event":"vss_job_completed","status":"completed"}')
    assert hits == []


def test_ndjson_search_messages_are_surfaced() -> None:
    stdout = '{"data": [], "search_messages": ["degraded to attribute-only"]}\n{"event":"x"}'
    hits, messages, _timings = flows.parse_cli_output(stdout)
    assert hits == []
    assert messages == ["degraded to attribute-only"]


def test_tolerates_banner_before_json() -> None:
    hits, _, _ = flows.parse_cli_output('WARNING: something\n{"data": [{"video_name": "a.mp4"}]}')
    assert len(hits) == 1


def test_garbage_output_does_not_raise() -> None:
    assert flows.parse_cli_output("not json at all")[0] == []


# ---------------------------------------------------------------------------
# Exit-code policy
# ---------------------------------------------------------------------------


def test_environment_faults_are_fatal_not_zero_scores() -> None:
    """Exit 5 means nothing was ingested -- scoring it 0.0 fakes a regression."""
    assert all(flows.is_fatal_exit(c) for c in (2, 3, 4, 5))
    assert not flows.is_fatal_exit(0)


def test_undocumented_exit_codes_are_also_fatal() -> None:
    """Regression: a broken CLI install exits 1, which is not in the table.

    Treating undocumented codes as soft failures scored 121 queries at mAP
    0.0000 in a real run against a stale venv -- the exact silent-zero this
    policy exists to prevent.
    """
    assert flows.is_fatal_exit(1)
    assert flows.is_fatal_exit(127)


# ---------------------------------------------------------------------------
# /complete retry policy
# ---------------------------------------------------------------------------


def test_502_is_retried_not_failed() -> None:
    """Observed on 10.86.12.161: 502 on attempt 1, 200 with chunks on attempt 2."""
    assert flows.classify_complete_failure(502, "Bad Gateway") == flows.COMPLETE_RETRY
    assert flows.classify_complete_failure(500, "boom") == flows.COMPLETE_RETRY
    assert flows.classify_complete_failure(503, "") == flows.COMPLETE_RETRY


def test_duplicate_camera_id_means_the_work_already_landed() -> None:
    """Not a failure: a prior attempt registered the stream before erroring.

    warehouse_sample returned this on every retry yet had working embeddings
    and searchable results, so retrying forever would have been wrong and
    failing the ingest would have been wrong too.
    """
    body = (
        'RTVI-CV add failed: RTVI-CV returned 500: {"reason": '
        '"STREAM_ADD_FAIL, Duplicate Camera id, unable to add stream"}'
    )
    assert flows.classify_complete_failure(502, body) == flows.COMPLETE_ALREADY_REGISTERED
    # Case-insensitive: the marker is matched against a lowercased body.
    assert flows.classify_complete_failure(500, "DUPLICATE CAMERA ID") == flows.COMPLETE_ALREADY_REGISTERED


def test_client_errors_are_fatal_not_retried() -> None:
    """A 400 will never succeed on retry; burning the budget hides the cause."""
    assert flows.classify_complete_failure(400, "bad request") == flows.COMPLETE_FATAL
    assert flows.classify_complete_failure(404, "no such sensor") == flows.COMPLETE_FATAL


# ---------------------------------------------------------------------------
# Concurrent-mutation detection
# ---------------------------------------------------------------------------


def test_inventory_change_is_detected() -> None:
    """A shared box wiped mid-run must not yield silently-wrong metrics.

    Observed on 10.86.12.161: 17 ingested sources were replaced by an unrelated
    set while the eval was in progress.
    """
    before = {"ok": True, "count": 3, "names": ["a", "b", "c"]}
    after = {"ok": True, "count": 2, "names": ["a", "z"]}
    diff = flows.compare_inventory(before, after)
    assert diff["stable"] is False
    assert diff["disappeared"] == ["b", "c"]
    assert diff["appeared"] == ["z"]


def test_unchanged_inventory_is_stable() -> None:
    snap = {"ok": True, "count": 2, "names": ["a", "b"]}
    assert flows.compare_inventory(snap, dict(snap))["stable"] is True


def test_unavailable_inventory_is_unknown_not_stable() -> None:
    """Unknown must not be reported as 'stable' -- that would be a false pass."""
    diff = flows.compare_inventory({"ok": False, "names": []}, {"ok": True, "count": 1, "names": ["a"]})
    assert diff["stable"] is None


# ---------------------------------------------------------------------------
# VST name matching
# ---------------------------------------------------------------------------


def test_name_variants_cover_both_vst_spellings() -> None:
    """VST is inconsistent about the extension.

    Observed on 10.86.12.161 (2026-08-25), both spellings present at once::

        warehouse_safety_0001        <- stem only
        sample-drone-bridge.mp4      <- extension retained
    """
    registered = {"warehouse_safety_0001", "sample-drone-bridge.mp4"}
    assert flows.is_registered("warehouse_safety_0001.mp4", registered)
    assert flows.is_registered("sample-drone-bridge.mp4", registered)
    assert not flows.is_registered("never_uploaded.mp4", registered)


def test_name_matching_is_case_insensitive() -> None:
    assert flows.is_registered("Warehouse_Sample.MP4", {"warehouse_sample"})


def test_sensor_streams_payload_is_parsed_to_id_name_pairs(monkeypatch) -> None:
    """``/sensor/streams`` nests one dict per stream id; --clear depends on this."""

    class _Resp:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> list:
            return [
                {"abc-123": [{"name": "warehouse_sample", "url": "rtsp://x"}]},
                {"def-456": [{"name": "Assault036_x264"}]},
                {"ghi-789": []},  # registered but no stream yet
                {},  # defensive: malformed entry
            ]

    monkeypatch.setattr(
        "flows.readiness.requests.get", lambda *_a, **_k: _Resp()
    )
    streams = flows.list_sensor_streams("http://host:30888")
    assert streams == {"abc-123": "warehouse_sample", "def-456": "Assault036_x264"}


def test_sensor_list_url_does_not_double_the_vst_prefix() -> None:
    assert flows.sensor_list_url("http://h:30888") == "http://h:30888/vst/api/v1/sensor/list"
    assert flows.sensor_list_url("http://h:30888/") == "http://h:30888/vst/api/v1/sensor/list"


# ---------------------------------------------------------------------------
# Decomposition-driven routing
# ---------------------------------------------------------------------------


def test_routing_rule_matches_the_four_cli_paths() -> None:
    """Derived from the paths' input models, not invented.

    See QUERY_DECOMPOSITION_PROMPT in vss_agents/tools/search.py.
    """
    assert flows.route({"attributes": [], "has_action": True}) == "embed"
    assert flows.route({"attributes": ["person in red"], "has_action": True}) == "fusion"
    assert flows.route({"attributes": ["person in red"], "has_action": False}) == "attribute"
    assert flows.route({"object_ids": [7]}) == "object"


def test_object_ids_win_over_attributes() -> None:
    """An explicit id lookup is an identity query, not a search."""
    assert flows.route({"object_ids": [7], "attributes": ["person in red"]}) == "object"


def test_missing_has_action_defaults_to_fusion_not_attribute() -> None:
    """Fusion still returns the embedding leg; attribute would drop it.

    has_action is REQUIRED in the agent's prompt, but a hand-written
    decomposition may omit it, and the safer default keeps candidates.
    """
    assert flows.route({"attributes": ["person in red"]}) == "fusion"


def test_plan_without_decomposition_reproduces_fixed_flags() -> None:
    """An un-annotated dataset must behave exactly as before."""
    plan = flows.plan_for("a person walking", None, default_path="embed", default_attributes=["x"])
    assert plan["path"] == "embed"
    assert plan["query"] == "a person walking"
    assert plan["attributes"] == ["x"]
    assert plan["routed"] is False


def test_plan_uses_the_rewritten_query_not_the_raw_text() -> None:
    """Decomposition strips time ranges and source names out of the query.

    Embedding the raw text would treat "between 1pm and 2pm" as visual content.
    """
    decomposition = {
        "query": "man pushing cart wearing beige shirt",
        "attributes": ["person wearing beige shirt"],
        "has_action": True,
        "timestamp_start": "2025-01-01T13:00:00Z",
        "video_sources": ["Endeavor heart"],
    }
    plan = flows.plan_for("Find a man pushing a cart wearing a beige shirt between 1 pm and 2 pm", decomposition)
    assert plan["path"] == "fusion"
    assert plan["query"] == "man pushing cart wearing beige shirt"
    assert plan["timestamp_start"] == "2025-01-01T13:00:00Z"
    assert plan["video_sources"] == ["Endeavor heart"]


def test_routed_argv_carries_the_decomposed_request() -> None:
    backend = flows.CliQueryBackend(
        ["vss"],
        decompositions={
            "Person wearing a hardhat dropping a box": {
                "query": "person dropping box wearing hardhat",
                "attributes": ["person wearing a hardhat"],
                "has_action": True,
            }
        },
    )
    argv = backend.build_argv("Person wearing a hardhat dropping a box")
    assert argv[:4] == ["vss", "search", "run", "fusion"]
    assert "person dropping box wearing hardhat" in argv
    assert "person wearing a hardhat" in argv


def test_unrouted_query_in_a_routed_run_falls_back_to_the_default_path() -> None:
    """A partially annotated dataset must not crash on the gaps."""
    backend = flows.CliQueryBackend(["vss"], decompositions={"other": {"attributes": ["x"]}})
    argv = backend.build_argv("Person climbing a ladder")
    assert argv[:4] == ["vss", "search", "run", "embed"]


# ---------------------------------------------------------------------------
# --original-query: the question, sent alongside the arguments derived from it
# ---------------------------------------------------------------------------


def test_original_query_carries_the_question_the_decomposition_replaced() -> None:
    """`--query` gets the rewrite; `--original-query` gets what was asked.

    These are different strings by design -- the decomposition strips the time
    range and source name so they are not embedded as visual content -- and the
    critic wants the second one.
    """
    asked = "Find a man pushing a cart wearing a beige shirt between 1 pm and 2 pm"
    backend = flows.CliQueryBackend(
        ["vss"],
        decompositions={
            asked: {
                "query": "man pushing cart wearing beige shirt",
                "attributes": ["man wearing a beige shirt"],
                "has_action": True,
            }
        },
    )
    argv = backend.build_argv(asked)
    assert argv[argv.index("--query") + 1] == "man pushing cart wearing beige shirt"
    assert argv[argv.index("--original-query") + 1] == asked


def test_object_path_sends_the_question_even_though_it_sends_no_query() -> None:
    """Without this the object path is never verified at all.

    ``object`` passes only ``--object-id``, so ``SearchInput.query`` is empty
    and ``attributes`` is empty; the host's reconstruction produces "" and
    returns before reaching the critic. The question is the only text there is.
    """
    backend = flows.CliQueryBackend(
        ["vss"],
        decompositions={"Where did person 7 go?": {"object_ids": [7]}},
    )
    argv = backend.build_argv("Where did person 7 go?")
    assert argv[:4] == ["vss", "search", "run", "object"]
    assert "--query" not in argv
    assert argv[argv.index("--original-query") + 1] == "Where did person 7 go?"


def test_original_query_can_be_withheld_to_reproduce_an_older_run() -> None:
    backend = flows.CliQueryBackend(["vss"], pass_original_query=False)
    assert "--original-query" not in backend.build_argv("red forklift")
    assert backend.describe()["pass_original_query"] is False


def test_original_query_is_on_by_default_and_recorded_in_the_flow_block() -> None:
    """Default-on, and the result file says which way it ran.

    A run that silently withheld it is not comparable with one that did not --
    same retrieval, different critic verdicts -- so the flag has to survive into
    the artifact rather than being reconstructable only from the command line.
    """
    backend = flows.CliQueryBackend(["vss"])
    assert "--original-query" in backend.build_argv("red forklift")
    assert backend.describe()["pass_original_query"] is True


def test_blank_question_sends_no_flag() -> None:
    """Click would take `--original-query ''`; the host would then ignore it."""
    assert "--original-query" not in flows.CliQueryBackend(["vss"]).build_argv("   ")


def test_unsupported_flag_is_detected_from_help_not_from_a_failed_query(
    monkeypatch: Any,
) -> None:
    """The probe reads --help, so an old CLI costs one subprocess, not a run."""
    import subprocess

    class _Proc:
        def __init__(self, code: int, out: str) -> None:
            self.returncode, self.stdout, self.stderr = code, out, ""

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kw: Any) -> Any:
        seen.append(argv)
        return _Proc(0, "Options:\n  --query TEXT\n  --original-query TEXT\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert flows.cli_supports_flag(["vss"], "--original-query") is True
    assert seen[0] == ["vss", "search", "run", "embed", "--help"]

    def only_query(*_a: Any, **_k: Any) -> Any:
        return _Proc(0, "Options:\n  --query TEXT\n")

    monkeypatch.setattr(subprocess, "run", only_query)
    assert flows.cli_supports_flag(["vss"], "--original-query") is False


def test_an_unhelpful_probe_reports_absent_rather_than_present(monkeypatch: Any) -> None:
    """Failure to answer must not be read as yes.

    Guessing "supported" costs the whole run (exit 2 on query 1); guessing
    "unsupported" costs a slightly worse critic question.
    """
    import subprocess

    class _Proc:
        returncode, stdout, stderr = 2, "", "no such command"

    def usage_error(*_a: Any, **_k: Any) -> Any:
        return _Proc()

    monkeypatch.setattr(subprocess, "run", usage_error)
    assert flows.cli_supports_flag(["vss"], "--original-query") is False

    def boom(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("vss")

    monkeypatch.setattr(subprocess, "run", boom)
    assert flows.cli_supports_flag(["vss"], "--original-query") is False


def test_the_flag_matches_the_field_the_cli_actually_declares() -> None:
    """Pin the eval's flag name to the product model that defines it.

    Skips where ``vss_cli`` is not importable, which is the normal case when
    these tests run without the agent package installed -- so green here does
    not prove the flag exists, only that it has not been renamed underneath us.
    """
    try:
        from vss_cli.search.group import SearchGroup
        from vss_cli.search.group import _Common
    except Exception as error:  # pragma: no cover - depends on the environment
        pytest.skip(f"vss_cli is not importable here: {error}")

    assert "original_query" in _Common.model_fields, (
        "the eval sends --original-query on every path, so it has to live on _Common"
    )
    # Every path inherits _Common, so every path accepts it -- including
    # `object`, which has no other text field.
    for action in SearchGroup.actions:
        assert "original_query" in action.Input.model_fields, action.name


def test_load_decompositions_accepts_sidecar_and_dataset_shapes(tmp_path: Path) -> None:
    sidecar = tmp_path / "s.json"
    sidecar.write_text('{"q1": {"attributes": ["person in red"], "has_action": true}}')
    assert flows.load_decompositions(sidecar)["q1"]["attributes"] == ["person in red"]

    dataset = tmp_path / "d.json"
    dataset.write_text('{"queries": {"q1": {"segments": [], "decomposition": {"attributes": ["a"]}}}}')
    assert flows.load_decompositions(dataset)["q1"]["attributes"] == ["a"]


def test_legacy_dataset_shape_still_loads() -> None:
    """queries[q] is the segment list -- every existing dataset."""
    annotations, decompositions = flows.unpack_dataset(
        {"queries": {"q1": [{"video_name": "a", "start_time": "t"}]}}
    )
    assert annotations["q1"] == [{"video_name": "a", "start_time": "t"}]
    assert decompositions == {}


def test_extended_dataset_shape_carries_decompositions() -> None:
    """Decompositions live next to the ground truth they belong to."""
    annotations, decompositions = flows.unpack_dataset(
        {
            "queries": {
                "q1": {
                    "segments": [{"video_name": "a", "start_time": "t"}],
                    "decomposition": {"attributes": ["person in red"], "has_action": True},
                }
            }
        }
    )
    assert annotations["q1"] == [{"video_name": "a", "start_time": "t"}]
    assert decompositions["q1"]["attributes"] == ["person in red"]


def test_extended_shape_without_a_decomposition_is_fine() -> None:
    """A partially annotated dataset must not lose its ground truth."""
    annotations, decompositions = flows.unpack_dataset(
        {"queries": {"q1": {"segments": [{"video_name": "a"}]}}}
    )
    assert annotations["q1"] == [{"video_name": "a"}]
    assert decompositions == {}


def test_annotations_shape_is_identical_across_both_dataset_forms() -> None:
    """Scoring must not be able to tell which form the dataset used."""
    segments = [{"video_name": "a", "start_time": "t"}]
    legacy, _ = flows.unpack_dataset({"queries": {"q1": segments}})
    extended, _ = flows.unpack_dataset({"queries": {"q1": {"segments": segments}}})
    assert legacy == extended


def test_path_distribution_counts_executed_plans() -> None:
    plans = [{"path": "embed"}, {"path": "fusion"}, {"path": "embed"}]
    assert flows.path_distribution(plans) == {"embed": 2, "fusion": 1}


# ---------------------------------------------------------------------------
# CLI discovery
# ---------------------------------------------------------------------------


def test_explicit_cmd_wins_and_is_shell_split() -> None:
    """shlex, not str.split -- a quoted path with spaces must survive."""
    argv, how = flows.resolve_vss_cmd(explicit_cmd="'/opt/my tools/vss' --flag")
    assert argv == ["/opt/my tools/vss", "--flag"]
    assert how == "--vss-cmd"


def test_repo_root_without_the_cli_package_is_rejected(tmp_path: Path) -> None:
    """The submodule pin is exactly this case -- it predates the CLI split."""
    (tmp_path / "services/agent").mkdir(parents=True)
    try:
        flows.resolve_vss_cmd(repo_root=str(tmp_path))
    except FileNotFoundError as e:
        assert "packages/vss_cli" in str(e)
        return
    raise AssertionError("expected FileNotFoundError for a checkout without the CLI")


def test_repo_root_with_the_cli_package_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "services/agent/packages/vss_cli").mkdir(parents=True)
    argv, how = flows.resolve_vss_cmd(repo_root=str(tmp_path))
    assert argv[:3] == ["uv", "run", "--project"]
    assert "--vss-repo-root" in how


def test_configure_origin_uses_the_unified_port_not_the_agent_port() -> None:
    """The agent port does not route /elasticsearch or /rtvi-embed.

    Pointing `vss configure` at it finds 1/7 services and every query then
    exits 4 -- observed on 10.86.12.161.
    """
    assert flows.vss_origin_for("http://10.87.88.126:8000") == "http://10.87.88.126:7777"
    assert flows.vss_origin_for("http://host:8000", port=9999) == "http://host:9999"


# ---------------------------------------------------------------------------
# Drift guard against run_eval.py
#
# flows/metrics.py was vendored from run_eval.py so this flow can stand alone.
# What must not drift is everything EXCEPT the one intentional divergence:
# match_segment moved from equal-start to half-open overlap, so evaluate_query
# no longer agrees with the legacy runner on event-bounded ground truth. That
# is asserted here rather than assumed, so the exception stays visible.
#
# These tests SKIP when run_eval.py is not importable, which is the normal case
# in this repo -- it lives in ci-vss-oss. A green run therefore does not prove
# parity, and pytest.skip says so out loud where a bare `return` did not.
# DELETE THIS SECTION when run_eval.py is removed.
# ---------------------------------------------------------------------------


def _legacy():
    """Import run_eval.py, or skip if it has already been removed."""
    try:
        from search_eval import run_eval
    except Exception:
        return None
    return run_eval


HITS_FOR_SCORING = [
    {"video_name": "warehouse_sample_20250101_000000_e0482.mp4",
     "start_time": "2025-01-01T00:00:05Z", "end_time": "2025-01-01T00:00:22Z"},
    {"video_name": "other_video.mp4",
     "start_time": "2025-01-01T00:01:00Z", "end_time": "2025-01-01T00:01:05Z"},
]
GROUND_TRUTH = [
    {"video_name": "warehouse_sample", "start_time": "2025-01-01T00:00:05Z",
     "end_time": "2025-01-01T00:00:10Z"},
    {"video_name": "warehouse_sample", "start_time": "2025-01-01T00:00:15Z",
     "end_time": "2025-01-01T00:00:20Z"},
]


def test_vendored_metrics_agree_where_the_grid_makes_them_agree() -> None:
    """Identical scores on chunk-shaped ground truth, which is most of it.

    GROUND_TRUTH here is 5s and grid-aligned, and for that shape overlap and
    equal-start accept exactly the same results -- so the divergence is
    invisible and the two runners must still agree exactly.
    """
    legacy = _legacy()
    if legacy is None:
        pytest.skip("run_eval.py not importable (lives in ci-vss-oss); parity unverified")

    mine = flows.evaluate_query("q", HITS_FOR_SCORING, GROUND_TRUTH, 1.23)
    theirs = legacy.evaluate_query("q", HITS_FOR_SCORING, GROUND_TRUTH, 1.23)
    assert mine == theirs


def test_vendored_metrics_diverge_on_event_bounded_ground_truth() -> None:
    """...and deliberately disagree once the ground truth leaves the grid.

    Pinning the divergence stops someone "restoring parity" later by reverting
    the fix, and documents which baselines are incomparable.
    """
    legacy = _legacy()
    if legacy is None:
        pytest.skip("run_eval.py not importable (lives in ci-vss-oss); parity unverified")

    event_gt = [{"video_name": "warehouse_sample",
                 "start_time": "2025-01-01T00:00:02Z", "end_time": "2025-01-01T00:00:07Z"}]
    hit = [{"video_name": "warehouse_sample.mp4",
            "start_time": "2025-01-01T00:00:00Z", "end_time": "2025-01-01T00:00:05Z"}]
    assert flows.evaluate_query("q", hit, event_gt, 1.0)["recall"] == 1.0
    assert legacy.evaluate_query("q", hit, event_gt, 1.0)["recall"] == 0.0


def test_vendored_segmentation_matches_run_eval() -> None:
    legacy = _legacy()
    if legacy is None:
        pytest.skip("run_eval.py not importable (lives in ci-vss-oss); parity unverified")
    assert flows.post_process_api_results(HITS_FOR_SCORING) == legacy.post_process_api_results(
        HITS_FOR_SCORING
    )
    assert flows.SEGMENT_SIZE == legacy.SEGMENT_SIZE
    assert flows.HIT_K_VALUES == legacy.HIT_K_VALUES


def test_vendored_dataset_registry_matches_run_eval() -> None:
    """Shared dataset entries must not drift; new-flow-only ones may be added.

    The guard exists to catch a path or subset being silently changed in one
    runner and not the other -- not to freeze the registry. A dataset built for
    the new flow (physicalai-dev) has no reason to exist in run_eval.py, which
    is being retired, so extra keys here are allowed and *changed* keys are not.
    """
    legacy = _legacy()
    if legacy is None:
        pytest.skip("run_eval.py not importable (lives in ci-vss-oss); parity unverified")
    shared = set(flows.DATASETS) & set(legacy.DATASETS)
    assert shared == set(legacy.DATASETS), (
        f"run_eval.py has datasets the new flow lacks: {set(legacy.DATASETS) - shared}"
    )
    for name in sorted(shared):
        assert flows.DATASETS[name] == legacy.DATASETS[name], f"{name} drifted"
    assert flows.DEFAULT_DATA_DIR == legacy.DEFAULT_DATA_DIR
    assert flows.vst_url_for("http://h:8000") == legacy._get_vst_url("http://h:8000")


def test_flow_package_imports_nothing_from_run_eval() -> None:
    """The point of vendoring: deleting run_eval.py must not break this flow."""
    import pathlib

    flows_dir = pathlib.Path(flows.__file__).parent
    offenders = []
    for module in flows_dir.glob("*.py"):
        for line in module.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import run_eval", "from run_eval")):
                offenders.append(f"{module.name}: {stripped}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


def test_registries_expose_the_implemented_backends() -> None:
    assert set(flows.INGEST_BACKENDS) == {"legacy-put", "agent-3step", "vst-direct"}
    assert set(flows.QUERY_BACKENDS) == {"cli"}


def test_the_default_ingest_flow_is_the_one_that_proves_it_indexed() -> None:
    """vst-direct is available but must not become the default by accident.

    Only `agent-3step` returns a chunk count and pins the timestamp anchor the
    ground truth is written against. A silent switch would turn "nothing
    indexed" and "nothing matched" into the same result.
    """
    import run_eval_flows as rf

    parsed = rf.parse_args(["--endpoint", "http://host:8000"])
    assert parsed.ingest_flow == "agent-3step"


# ---------------------------------------------------------------------------
# vst-direct: what the UI does, minus the two steps that produced evidence
# ---------------------------------------------------------------------------


def test_vst_direct_posts_to_vios_and_never_calls_the_agent() -> None:
    backend = flows.VstDirectIngest("http://host:30888/")
    assert backend.upload_url == "http://host:30888/vst/api/v1/storage/file"
    described = backend.describe()
    assert described["flow"] == "vst-direct"
    # Stated, not inferred: a reader comparing this against a 3-step run has to
    # know the success criterion changed.
    assert "none" in described["ingest_proof"]


def test_vst_direct_reports_no_chunk_count_rather_than_zero(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`None` means nobody counted; `0` would mean counted and empty.

    The three-step flow fails an upload that reports zero chunks. There is no
    equivalent signal here, so the record must not manufacture one.
    """
    import requests

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really an mp4")

    class _Resp:
        status_code = 200
        text = '{"sensorId": "abc-123"}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"sensorId": "abc-123"}

    posted: list[str] = []

    def fake_post(url: str, **kw: Any) -> Any:
        posted.append(url)
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    record = flows.VstDirectIngest("http://host:30888").upload(video)

    assert posted == ["http://host:30888/vst/api/v1/storage/file"]
    assert record["success"] is True
    assert record["sensor_id"] == "abc-123"
    assert record["chunks_processed"] is None
    assert record["ingest_proof"] == "none"


def test_vst_direct_checks_the_anchor_instead_of_assuming_it(monkeypatch: Any) -> None:
    """Ground truth is offsets from 2025-01-01; VIOS chooses the real anchor.

    The webhook request bodies in the search profile's notification_config.json
    are empty, so nothing passes creation_time the way the agent's /complete
    does. If VIOS anchors elsewhere, every query scores zero and it reads as a
    retrieval collapse rather than an ingest problem.
    """
    import requests

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    backend = flows.VstDirectIngest("http://host:30888")

    def anchored(*_a: Any, **_k: Any) -> Any:
        return _Resp({"abc-123": [{"startTime": "2025-01-01T00:00:00.000Z",
                                   "endTime": "2025-01-01T00:00:30.000Z"}]})

    monkeypatch.setattr(requests, "get", anchored)
    good = backend.verify_anchor("abc-123")
    assert good["found"] is True
    assert good["matches_expected_anchor"] is True

    def wall_clock(*_a: Any, **_k: Any) -> Any:
        return _Resp({"abc-123": [{"startTime": "2026-09-09T11:04:00.000Z",
                                   "endTime": "2026-09-09T11:04:30.000Z"}]})

    monkeypatch.setattr(requests, "get", wall_clock)
    bad = backend.verify_anchor("abc-123")
    assert bad["found"] is True
    assert bad["matches_expected_anchor"] is False


def test_an_unreachable_vst_is_unchecked_not_a_passing_anchor(monkeypatch: Any) -> None:
    import requests

    def boom(*_a: Any, **_k: Any) -> Any:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    result = flows.VstDirectIngest("http://host:30888").verify_anchor("abc-123")
    assert result["checked"] is False
    assert "matches_expected_anchor" not in result


def test_default_vss_cmd_uses_the_project_local_cli() -> None:
    cmd = flows.default_vss_cmd("/home/me/vss")
    assert cmd[:3] == ["uv", "run", "--project"]
    assert "/home/me/vss/services/agent" in cmd
    # --extra cli ships the `vss` executable; the base distribution does not.
    assert "--extra" in cmd and "cli" in cmd


# ---------------------------------------------------------------------------
# Selective pruning (--only-dataset)
# ---------------------------------------------------------------------------


def _fake_streams() -> dict[str, str]:
    return {
        "id-ours-1": "1219",                 # dataset video, stem spelling
        "id-ours-2": "video_00570.mp4",      # dataset video, extension retained
        "id-theirs": "someone_elses_clip",   # foreign
        "id-theirs2": "warehouse_sample",    # foreign
    }


def _dataset_dir(tmp_path: Path) -> Path:
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "1219.mp4").write_bytes(b"")
    (videos / "video_00570.mp4").write_bytes(b"")
    return videos


def test_prune_deletes_only_foreign_sources(tmp_path: Path, monkeypatch: Any) -> None:
    """The dataset's own videos survive; everything else goes.

    Both spellings VST uses -- stem and stem.mp4 -- must be recognised as ours,
    or the prune deletes a fixture that ingest then re-uploads.
    """
    import run_eval_flows as rf

    deleted: list[str] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

    monkeypatch.setattr(rf.flows, "list_sensor_streams", lambda *_a, **_k: _fake_streams())
    monkeypatch.setattr(
        rf.requests, "delete",
        lambda url, **_k: (deleted.append(url.rsplit("/", 1)[-1]), _Resp())[1],
    )

    out = rf.prune_foreign_videos("http://agent:8000", "http://vst:30888", _dataset_dir(tmp_path))

    assert sorted(deleted) == ["id-theirs", "id-theirs2"]
    assert out["kept"] == 2
    assert out["deleted"] == 2
    assert out["found"] == 4


def test_prune_refuses_when_the_dataset_has_no_videos(tmp_path: Path, monkeypatch: Any) -> None:
    """An empty video dir makes every source look foreign.

    Without this guard --only-dataset silently becomes --clear, which is the
    one thing it exists to avoid.
    """
    import run_eval_flows as rf

    monkeypatch.setattr(rf.flows, "list_sensor_streams", lambda *_a, **_k: _fake_streams())
    monkeypatch.setattr(
        rf.requests, "delete",
        lambda *_a, **_k: pytest.fail("must not delete anything when the dataset is empty"),
    )

    empty = tmp_path / "videos"
    empty.mkdir()
    with pytest.raises(SystemExit, match="no video files"):
        rf.prune_foreign_videos("http://agent:8000", "http://vst:30888", empty)


def test_prune_is_a_noop_when_every_source_belongs_to_the_dataset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import run_eval_flows as rf

    monkeypatch.setattr(
        rf.flows, "list_sensor_streams",
        lambda *_a, **_k: {"id-ours-1": "1219", "id-ours-2": "video_00570.mp4"},
    )
    monkeypatch.setattr(
        rf.requests, "delete",
        lambda *_a, **_k: pytest.fail("nothing foreign, so nothing should be deleted"),
    )

    out = rf.prune_foreign_videos("http://agent:8000", "http://vst:30888", _dataset_dir(tmp_path))
    assert out == {"found": 2, "kept": 2, "deleted": 0, "names": []}


# ---------------------------------------------------------------------------
# Flow description vs live decomposition
# ---------------------------------------------------------------------------


class _Backend:
    """Minimal stand-in: describe() reports routing from `decompositions`."""

    def __init__(self) -> None:
        self.decompositions: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "routing": "per-query (from decompositions)" if self.decompositions else "fixed",
            "decompositions": len(self.decompositions),
        }


class _Decomposer:
    @staticmethod
    def describe() -> dict[str, Any]:
        return {"llm_url": "http://llm:30081", "model": "nemotron", "temperature": 0.0}


def test_flow_records_live_decomposition_before_any_query_runs() -> None:
    """A live decomposer fills `decompositions` only as queries run.

    describe() is called before the first query, so on its own it reports
    `routing: fixed, decompositions: 0` for a run that decomposes everything --
    and the saved flow block then tells the reader one path was measured.
    """
    import run_eval_flows as rf

    backend = _Backend()
    assert backend.describe()["routing"] == "fixed"  # the trap

    out = rf.describe_query_flow(backend, _Decomposer())
    assert out["routing"] == "per-query (live decomposition)"
    assert out["live_decomposition"]["model"] == "nemotron"


def test_dry_run_reports_planned_decomposition_without_building_one() -> None:
    """The dry run builds no decomposer -- that would call the LLM."""
    import run_eval_flows as rf

    out = rf.describe_query_flow(_Backend(), None, planned_llm_url="http://llm:30081")
    assert out["routing"] == "per-query (live decomposition)"
    assert out["live_decomposition"]["llm_url"] == "http://llm:30081"
    assert out["live_decomposition"]["model"] == "(discovered at run time)"


def test_flow_reports_false_when_nothing_decomposes() -> None:
    import run_eval_flows as rf

    out = rf.describe_query_flow(_Backend(), None)
    assert out["routing"] == "fixed"
    assert out["live_decomposition"] is False


def test_llm_origin_is_derived_from_the_agent_endpoint() -> None:
    """Decomposition must not depend on the caller remembering a flag.

    The NIM is not behind the unified origin, so it is derived the same way VST
    is: same host, its own port.
    """
    assert flows.llm_url_for("http://10.86.12.161:8000") == "http://10.86.12.161:30081"
    assert flows.llm_url_for("http://host:8000", 31000) == "http://host:31000"
    assert flows.llm_url_for("https://host:8000/") == "https://host:30081"


def test_vst_and_llm_origins_do_not_collide() -> None:
    endpoint = "http://10.86.12.161:8000"
    assert flows.vst_url_for(endpoint) != flows.llm_url_for(endpoint)


# ---------------------------------------------------------------------------
# Decomposition fallback when the LLM is unreachable
# ---------------------------------------------------------------------------


def test_load_decompositions_reads_a_provenance_answer_key(tmp_path: Path) -> None:
    """The sidecar keys its map under `expected_decomposition`, not `queries`."""
    p = tmp_path / "devset_provenance.json"
    p.write_text(json.dumps({
        "n_videos": 32,
        "expected_decomposition": {
            "person running": {"query": "person running", "attributes": [], "has_action": True},
            "person in a red cap running": {
                "query": "person in a red cap running",
                "attributes": ["person in a red cap"],
                "has_action": True,
            },
        },
    }))
    out = flows.load_decompositions(p)
    assert set(out) == {"person running", "person in a red cap running"}
    assert flows.route(out["person running"]) == "embed"
    assert flows.route(out["person in a red cap running"]) == "fusion"


def test_sidecar_is_found_only_when_it_carries_an_answer_key(tmp_path: Path) -> None:
    (tmp_path / "with-key").mkdir()
    (tmp_path / "with-key" / "devset_provenance.json").write_text(
        json.dumps({"expected_decomposition": {"q": {"attributes": []}}})
    )
    (tmp_path / "no-key").mkdir()
    (tmp_path / "no-key" / "devset_provenance.json").write_text(json.dumps({"n_videos": 4}))
    (tmp_path / "no-file").mkdir()

    assert flows.sidecar_decompositions_for(tmp_path, "with-key") is not None
    assert flows.sidecar_decompositions_for(tmp_path, "no-key") is None
    assert flows.sidecar_decompositions_for(tmp_path, "no-file") is None


def test_flow_records_that_the_run_fell_back(tmp_path: Path) -> None:
    """A fallback run must not read as a deliberate fixed-path choice."""
    import run_eval_flows as rf

    fb = {"attempted": "http://llm:30081", "error": "refused",
          "fell_back_to": "dataset answer key (devset_provenance.json)"}
    out = rf.describe_query_flow(_Backend(), None, fallback=fb)
    assert out["live_decomposition"]["fell_back_to"].startswith("dataset answer key")
    assert out["live_decomposition"]["error"] == "refused"


# ---------------------------------------------------------------------------
# Names the runner reads but never binds
# ---------------------------------------------------------------------------


def test_no_function_reads_a_name_it_never_binds() -> None:
    """Catch the NameError class of bug that only a live run would surface.

    `run_evaluation` once read `decompose_fallback`, a local of `main()` that was
    never passed in. Nothing here called `run_evaluation` -- it needs a
    deployment -- so the tests stayed green while every real run raised
    NameError at the flow-description line.

    Walking the AST costs nothing and covers every function in the module. A
    name loaded inside a function must be bound by that function, by one of its
    enclosing functions (closures are legitimate and this module uses them), by
    the module, or by builtins.
    """
    import ast
    import builtins

    source = (Path(__file__).resolve().parents[1] / "run_eval_flows.py").read_text()
    tree = ast.parse(source)

    def bindings(node: ast.AST) -> set[str]:
        """Names this scope binds, not descending into nested scopes."""
        out: set[str] = set()
        args = getattr(node, "args", None)
        if isinstance(args, ast.arguments):
            out |= {a.arg for a in args.args + args.kwonlyargs + args.posonlyargs}
            if args.vararg:
                out.add(args.vararg.arg)
            if args.kwarg:
                out.add(args.kwarg.arg)

        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
                continue  # its interior is a scope of its own
            if isinstance(n, ast.Lambda):
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                out.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out |= {(a.asname or a.name).split(".")[0] for a in n.names}
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                out |= set(n.names)
            stack.extend(ast.iter_child_nodes(n))
        return out

    def loads(node: ast.AST) -> set[str]:
        """Names this scope reads, not descending into nested scopes."""
        out: set[str] = set()
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                out.add(n.id)
            stack.extend(ast.iter_child_nodes(n))
        return out

    module_scope = bindings(tree) | set(dir(builtins))
    unbound: dict[str, set[str]] = {}

    def visit(node: ast.AST, enclosing: set[str], path: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = enclosing | bindings(child)
                name = f"{path}.{child.name}" if path else child.name
                missing = loads(child) - scope
                if missing:
                    unbound[name] = missing
                visit(child, scope, name)
            else:
                visit(child, enclosing, path)

    visit(tree, module_scope, "")
    assert not unbound, f"names read but never bound: {unbound}"


# ---------------------------------------------------------------------------
# Segment matching: overlap, not equal start times
# ---------------------------------------------------------------------------

_F = "%Y-%m-%dT%H:%M:%SZ"


def _window(video: str, start_s: int, length_s: int = 5) -> dict[str, str]:
    import datetime

    base = datetime.datetime(2025, 1, 1) + datetime.timedelta(seconds=start_s)
    return {
        "video_name": video,
        "start_time": base.strftime(_F),
        "end_time": (base + datetime.timedelta(seconds=length_s)).strftime(_F),
    }


def test_chunk_aligned_ground_truth_matches_exactly_as_before() -> None:
    """vad-r1-v2 is 100% 5s and 100% grid-aligned; its scoring must not move."""
    gt = [_window("vidA", 10)]
    assert flows.match_segment(_window("vidA", 10), gt)[0] == 0
    for off_by_one in (0, 5, 15, 20):
        assert flows.match_segment(_window("vidA", off_by_one), gt)[0] == -1


def test_a_result_inside_an_event_matches_it() -> None:
    """physicalai-dev segments are event bounds, often off the 5s grid.

    Results are snapped to that grid, so equal-start matching made 23.6% of its
    queries unscoreable and threw away 57% of the time its ground truth covers.
    """
    event = [_window("vidA", 7, length_s=27)]  # 00:07 -> 00:34, off-grid start
    for inside in (5, 10, 20, 30):
        assert flows.match_segment(_window("vidA", inside), event)[0] == 0, inside
    for outside in (0, 35, 60):
        assert flows.match_segment(_window("vidA", outside), event)[0] == -1, outside


def test_adjacent_segments_do_not_overlap() -> None:
    """Half-open intervals: [10,15) and [15,20) touch but do not overlap."""
    gt = [_window("vidA", 15)]
    assert flows.match_segment(_window("vidA", 10), gt)[0] == -1
    assert flows.match_segment(_window("vidA", 15), gt)[0] == 0


def test_overlap_does_not_turn_one_event_into_several_hits() -> None:
    """Each ground-truth segment is still claimed once."""
    event = [_window("vidA", 7, length_s=27)]
    out = flows.evaluate_query("q", [_window("vidA", s) for s in (10, 15, 20)], event, 1.0)
    assert out["true_positives"] == 1
    assert out["recall"] == 1.0


def test_a_different_video_never_matches() -> None:
    gt = [_window("vidA", 10)]
    assert flows.match_segment(_window("vidB", 10), gt)[0] == -1

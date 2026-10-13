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

"""Unit tests for the parts that decide what gets measured.

    uv run --with pyyaml --with requests --with pytest python3 -m pytest scripts/tests

Nothing here touches the network. The tests cover the logic that silently
changes a result if it is wrong: run identity, the sweep order, how big a run
is, how the shared budget hands out slots, the percentiles, and the ingest gate
that decides a sweep may start at all.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.budget import SampleBudget  # noqa: E402
from bench.corpus import (  # noqa: E402
    FILENAME_STEM,
    VST_UUID,
    GateEntry,
    UploadOutcome,
    VideoTarget,
    _multipart_parts,
    _sensor_key_for,
    build_video_targets,
    converge_expected,
    find_duplicate_sensor_ids,
    normalize_gate_indices,
    upload_videos,
)
from bench.cli import (  # noqa: E402
    CliNotFoundError,
    prepare_sweep_config,
    build_argv,
    is_environment_fault,
    parse_cli_output,
    require_origin,
)
from bench.decompose import (  # noqa: E402
    DecompositionError,
    find_repo_root,
    load_reasoning_controls,
    require_llm_url,
)
from bench.models import (  # noqa: E402
    SUMMARY_FIELDS,
    QueryItem,
    RequestRecord,
    RunConfig,
)
from bench.plan import (  # noqa: E402
    build_execution_plan,
    build_query_set,
    coerce_critic_eval_count,
    load_config,
    queries_for_path,
    samples_per_query,
)
from bench.routing import (  # noqa: E402
    ROUTABLE_PATHS,
    SEARCH_PATHS,
    path_distribution,
    pinned_plan,
    plan_for,
    route,
)
from bench.stats import compute_per_query_stats, percentile  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.fixture
def query_set() -> list[QueryItem]:
    return [
        QueryItem(id="q1", query="a", expected_path="attribute"),
        QueryItem(id="q2", query="b", expected_path="event"),
        QueryItem(id="q3", query="c", expected_path="fusion"),
    ]


# --- the shipped config ----------------------------------------------------


def test_shipped_config_builds_a_plan():
    config = load_config(CONFIG_PATH)
    queries = build_query_set(config)
    runs = build_execution_plan(config, query_set=queries)
    assert len(queries) == 3
    # critic off/on x 3 paths x 3 top_k x 4 concurrency.
    # Pinned to this specific product (not computed from the config) so a
    # change to the matrix fails here and is deliberate.
    assert len(runs) == 72
    assert len({r.run_id for r in runs}) == len(runs)
    assert [r.critic for r in runs[:36]] == [False] * 36
    assert [r.path for r in runs[:12]] == ["attribute"] * 12
    for path, qid in (("attribute", "q1"), ("event", "q2"), ("fusion", "q3")):
        assert [q.id for q in queries_for_path(queries, path)] == [qid]


def test_shipped_config_covers_three_distinct_paths():
    """The whole corpus design rests on one query per path, so check it holds."""
    queries = build_query_set(load_config(CONFIG_PATH))
    paths = [q.expected_path for q in queries]
    assert sorted(paths) == ["attribute", "event", "fusion"]
    assert all(p in SEARCH_PATHS for p in paths)


def test_a_query_expecting_a_nonexistent_path_is_rejected():
    """``PATH 1`` was the old taxonomy; a stale config must not route silently."""
    with pytest.raises(ValueError, match="PATH 1"):
        build_query_set({"query_set": [
            {"id": "q1", "query": "a", "expected_path": "PATH 1"},
        ]})


# --- routing a decomposition to a CLI path ---------------------------------


@pytest.mark.parametrize(
    "decomposition,expected",
    [
        ({"object_ids": [7]}, "object"),
        ({"object_ids": [7], "attributes": ["a"]}, "object"),
        ({"attributes": [], "has_action": True}, "event"),
        ({}, "event"),
        ({"attributes": ["white shirt"], "has_action": True}, "fusion"),
        ({"attributes": ["white shirt"], "has_action": False}, "attribute"),
    ],
)
def test_route(decomposition, expected):
    assert route(decomposition) == expected


def test_an_absent_has_action_routes_to_fusion():
    """The prompt marks it REQUIRED and the model still omits it.

    Fusion covers both halves, so an omission costs recall rather than giving
    an attribute-only query no text match at all.
    """
    assert route({"attributes": ["a"]}) == "fusion"


def test_every_routable_path_is_a_real_cli_path():
    assert set(ROUTABLE_PATHS) <= set(SEARCH_PATHS)


def test_tag_is_reachable_only_by_pinning():
    """Nothing in the decomposition contract asks for keyword tag retrieval."""
    assert "tag" not in ROUTABLE_PATHS
    assert pinned_plan("a", "tag")["path"] == "tag"


def test_pinning_an_unknown_path_is_rejected():
    with pytest.raises(ValueError, match="nonsense"):
        pinned_plan("a", "nonsense")


def test_path_distribution_keeps_zeroes():
    """An omitted key reads as 'never asked', which is the opposite of 'never
    matched' -- a path no query reaches has to show up as 0."""
    plans = {"q1": plan_for("a", {"attributes": []})}
    distribution = path_distribution(plans)
    assert distribution["event"] == 1
    assert distribution["object"] == 0
    assert set(distribution) == set(SEARCH_PATHS)


# --- run identity ----------------------------------------------------------


def test_run_id_without_path_is_unchanged_by_the_path_feature():
    """A run_id must not gain a suffix just because --path exists.

    Otherwise --resume would see every previously completed run as pending.
    """
    assert RunConfig(critic_eval_count=1, top_k=5, concurrency=10).run_id == (
        "critic_1_topk_5_c10"
    )


def test_run_id_with_path_is_distinct():
    assert RunConfig(
        critic_eval_count=None, top_k=20, concurrency=1, path="fusion"
    ).run_id == "critic_all_topk_20_c1_fusion"


# --- the plan --------------------------------------------------------------


def test_critic_stays_outermost_so_an_abort_keeps_a_whole_phase(query_set):
    """An aborted sweep must still deliver a complete first critic setting."""
    config = {"sweep": {"critic_eval_count": [1, None], "top_k": [5],
                        "concurrency": [1, 5]}}
    runs = build_execution_plan(
        config, paths=["attribute", "event"], query_set=query_set
    )
    order = [r.critic_eval_count for r in runs]
    assert order.index(None) > max(
        i for i, value in enumerate(order) if value == 1
    )


def test_grouped_finishes_a_path_before_starting_the_next(query_set):
    config = {"sweep": {"critic_eval_count": [None], "top_k": [5, 10],
                        "concurrency": [1]}}
    runs = build_execution_plan(
        config, paths=["attribute", "event"], path_order="grouped",
        query_set=query_set,
    )
    assert [r.path for r in runs] == [
        "attribute", "attribute", "event", "event",
    ]


def test_interleaved_runs_the_paths_of_one_cell_back_to_back(query_set):
    config = {"sweep": {"critic_eval_count": [None], "top_k": [5, 10],
                        "concurrency": [1]}}
    runs = build_execution_plan(
        config, paths=["attribute", "event"], path_order="interleaved",
        query_set=query_set,
    )
    assert [r.path for r in runs] == [
        "attribute", "event", "attribute", "event",
    ]


def test_a_path_matching_no_query_is_rejected(query_set):
    with pytest.raises(ValueError, match="object"):
        build_execution_plan({}, paths=["object"], query_set=query_set)


def test_a_path_outside_the_cli_taxonomy_is_rejected(query_set):
    with pytest.raises(ValueError, match="PATH 1"):
        build_execution_plan({}, paths=["PATH 1"], query_set=query_set)


def test_resolved_paths_override_the_configured_expectation(query_set):
    """A --path is validated against what routing produced, not what was hoped.

    q1 expects `attribute` but routed to `fusion`, so a fusion run must be
    accepted and an attribute run rejected.
    """
    resolved = {"q1": "fusion", "q2": "event", "q3": "fusion"}
    runs = build_execution_plan(
        {"sweep": {"top_k": [5], "concurrency": [1]}},
        paths=["fusion"], query_set=query_set, resolved_paths=resolved,
    )
    assert len(runs) == 1
    with pytest.raises(ValueError, match="attribute"):
        build_execution_plan(
            {}, paths=["attribute"], query_set=query_set, resolved_paths=resolved,
        )


def test_duplicate_critic_values_collapse(query_set):
    runs = build_execution_plan(
        {"sweep": {"top_k": [5], "concurrency": [1]}},
        critic_eval_count=["all", None, "none"],
        query_set=query_set,
    )
    assert len(runs) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [(None, None), ("all", None), ("null", None), ("", None),
     (1, 1), ("3", 3), (10, 10)],
)
def test_coerce_critic_eval_count(raw, expected):
    assert coerce_critic_eval_count(raw) == expected


def test_critic_eval_count_rejects_zero():
    """The CLI bounds the flag at 1, so 0 would exit 2 on every request."""
    with pytest.raises(ValueError, match=">= 1"):
        coerce_critic_eval_count(0)


def test_critic_eval_count_rejects_a_boolean():
    """``critic_eval_count: false`` is a config carried over from use_critic.

    Read as an int it would become 0 and abort the sweep on request one.
    """
    with pytest.raises(ValueError, match="not a switch"):
        coerce_critic_eval_count(False)


def test_duplicate_query_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate query id"):
        build_query_set({"query_set": [
            {"id": "q1", "query": "a"}, {"id": "q1", "query": "b"},
        ]})


def test_queries_for_path(query_set):
    assert [q.id for q in queries_for_path(query_set, "event")] == ["q2"]
    assert len(queries_for_path(query_set, "")) == 3
    resolved = {"q1": "fusion", "q2": "fusion", "q3": "event"}
    assert [
        q.id for q in queries_for_path(query_set, "fusion", resolved)
    ] == ["q1", "q2"]


# --- building the command --------------------------------------------------


def test_each_path_gets_only_its_own_flags():
    """``run embed`` has no --attribute and ``run object`` has no --query.

    Click exits 2 on an unknown option, which aborts the whole sweep, so this
    is the difference between a run and a wall of failures.
    """
    decomposition = {"attributes": ["white shirt"], "has_action": False}
    argv = build_argv(["vss"], plan_for("a person", decomposition), top_k=5)
    assert argv[:4] == ["vss", "search", "run", "attribute"]
    assert "--query" not in argv
    assert argv[argv.index("--attribute") + 1] == "white shirt"

    embed = build_argv(["vss"], plan_for("a person", {}), top_k=5)
    assert embed[:4] == ["vss", "search", "run", "embed"]
    assert "--attribute" not in embed
    assert embed[embed.index("--query") + 1] == "a person"

    objects = build_argv(["vss"], plan_for("a", {"object_ids": [7, 8]}), top_k=5)
    assert "--query" not in objects
    assert objects.count("--object-id") == 2


def test_fusion_carries_both_halves():
    plan = plan_for("a person carrying a box",
                    {"attributes": ["yellow hat"], "has_action": True})
    argv = build_argv(["vss"], plan, top_k=5)
    assert argv[3] == "fusion"
    assert "--query" in argv and "--attribute" in argv


def test_merge_adjacent_defaults_off_so_top_k_rows_come_back():
    argv = build_argv(["vss"], plan_for("a", {}), top_k=20)
    assert "--no-merge-adjacent" in argv
    assert argv[argv.index("--top-k") + 1] == "20"


def _write_cli_config(home, services):
    path = home / ".vss"
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps({
            "version": 1,
            "base_url": "http://vss-search.10.0.0.1.nip.io",
            "services": services,
        }),
        encoding="utf-8",
    )
    return path / "config.json"


def test_repointing_a_service_leaves_discovery_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("VSS_CONFIG_HOME", raising=False)
    user = _write_cli_config(tmp_path, {
        "elasticsearch": {
            "url": "http://vss-search.10.0.0.1.nip.io/elasticsearch",
            "indices": ["mdx-raw-1"],
        },
        "vst": {"url": "http://vss-search.10.0.0.1.nip.io/vst"},
    })

    direct = "http://elasticsearch.vss-search.svc.cluster.local:9200"
    sweep_home = tmp_path / "sweep"
    result = prepare_sweep_config(
        ["vss"], "http://origin", sweep_home, {"elasticsearch": direct},
        configure=False,
    )

    assert result["applied"] == {"elasticsearch": direct}
    assert result["has_rt_vlm"] is False
    written = json.loads((sweep_home / "config.json").read_text(encoding="utf-8"))
    assert written["services"]["elasticsearch"]["url"] == direct
    # What the backend holds does not change with the route taken to it.
    assert written["services"]["elasticsearch"]["indices"] == ["mdx-raw-1"]
    # An untouched service keeps going through the ingress.
    assert written["services"]["vst"]["url"].endswith("/vst")
    # The user's own config is never rewritten.
    original = json.loads(user.read_text(encoding="utf-8"))
    assert original["services"]["elasticsearch"]["url"].endswith("/elasticsearch")


def test_a_misspelled_service_stops_the_run(tmp_path, monkeypatch):
    # Ignoring it would leave that leg on the proxy while the report read as a
    # clean direct-to-service measurement.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("VSS_CONFIG_HOME", raising=False)
    _write_cli_config(tmp_path, {"elasticsearch": {"url": "http://es/"}})
    sweep_home = tmp_path / "sweep"

    with pytest.raises(CliNotFoundError, match="rt_embedd"):
        prepare_sweep_config(
            ["vss"], "http://origin", sweep_home,
            {"rt_embedd": "http://embed:8000"}, configure=False,
        )
    assert not (sweep_home / "config.json").exists()


def test_the_sweeps_top_k_wins_over_the_decompositions():
    """top_k is a sweep axis. A decomposition that emits one would pin the cell
    to a value it was not asked to measure, while the run_id still said 20."""
    plan = plan_for("a", {"top_k": 3})
    argv = build_argv(["vss"], plan, top_k=20)
    assert argv[argv.index("--top-k") + 1] == "20"


def test_critic_eval_count_is_omitted_when_unset():
    """Omitting it verifies every hit; there is no value meaning 'off'."""
    assert "--critic-eval-count" not in build_argv(
        ["vss"], plan_for("a", {}), top_k=5, critic_eval_count=None
    )
    argv = build_argv(["vss"], plan_for("a", {}), top_k=5, critic_eval_count=2)
    assert argv[argv.index("--critic-eval-count") + 1] == "2"


def test_min_cosine_similarity_only_goes_to_the_paths_that_accept_it():
    embed = build_argv(
        ["vss"], plan_for("a", {}), top_k=5, min_cosine_similarity=0.3
    )
    assert "--min-cosine-similarity" in embed
    attribute = build_argv(
        ["vss"],
        plan_for("a", {"attributes": ["x"], "has_action": False}),
        top_k=5, min_cosine_similarity=0.3,
    )
    assert "--min-cosine-similarity" not in attribute


# --- reading the CLI's answer ----------------------------------------------


def test_ndjson_output_is_read_past_the_lifecycle_events():
    """The CLI writes one JSON document per line.

    Parsing the buffer as a single document raises "Extra data", and swallowing
    that once dropped 106 of 121 results and reported mAP 0.0000.
    """
    stdout = (
        '{"event": "search_started"}\n'
        '{"event": "progress", "percent": 50}\n'
        '{"data": [{"id": 1}, {"id": 2}], "search_messages": ["ok"]}\n'
    )
    hits, messages = parse_cli_output(stdout)
    assert len(hits) == 2
    assert messages == ["ok"]


def test_a_single_document_response_still_parses():
    hits, _ = parse_cli_output('{"data": [{"id": 1}]}')
    assert len(hits) == 1


def test_an_empty_result_is_a_result_not_a_parse_failure():
    hits, _ = parse_cli_output('{"event": "done"}\n{"data": []}')
    assert hits == []


def test_output_with_no_data_document_is_an_error():
    """Better to fail loudly than to score a lifecycle event as zero hits."""
    with pytest.raises(ValueError, match="data"):
        parse_cli_output('{"event": "search_started"}')


def test_empty_output_is_an_error():
    with pytest.raises(ValueError, match="no output"):
        parse_cli_output("   ")


# --- exit codes ------------------------------------------------------------


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_environment_faults_abort(code):
    assert is_environment_fault(code)


@pytest.mark.parametrize("code", [0, 1, 124, -1])
def test_a_failed_query_does_not_abort(code):
    """A query that simply finds nothing, or times out once, stays countable."""
    assert not is_environment_fault(code)


# --- deriving sibling endpoints -------------------------------------------
#
# Both ports below are host-level: 7777 is HAProxy on a node, 30081 is the
# NIM's NodePort. Swapping the port on a Service DNS name yields an address
# nothing listens on, and the sweep would only find out after ingest.


def test_neither_endpoint_is_ever_guessed():
    """Blank must stop the run, not fall back to the Docker port.

    Under Compose these sit at 7777 and 30081 on the agent's host, which is
    where the sibling skill derives them from. Helm shares neither host nor
    port, so a derived value would be an address nothing serves -- and the
    sweep would only discover that after ingest.
    """
    with pytest.raises(CliNotFoundError, match="cli.origin_url"):
        require_origin({"origin_url": ""})
    with pytest.raises(CliNotFoundError):
        require_origin({})
    with pytest.raises(DecompositionError, match="services.llm_url"):
        require_llm_url({"vss_agent_url": "http://10.86.12.161:8000"})


def test_a_configured_endpoint_is_taken_as_given():
    assert require_origin(
        {"origin_url": "http://vss-search.10.86.7.90.nip.io/"}
    ) == "http://vss-search.10.86.7.90.nip.io"
    assert require_llm_url(
        {"llm_url": "http://nemotron-35-lightning-30b-a3b:8000"}
    ) == "http://nemotron-35-lightning-30b-a3b:8000"


def test_an_overridden_llm_url_reaches_the_dry_run():
    """--llm-url must be folded in before the plan is printed.

    Applied where it is used instead, the dry run would name the config's
    endpoint while the sweep contacted another one -- and the plan is the
    output people read before committing hours.
    """
    import argparse

    import run_throughput

    config = {"services": {"llm_url": "http://configured:8000"}}
    run_throughput.apply_setup_overrides(
        config, argparse.Namespace(ingest=False, llm_url="http://overridden:8000")
    )
    assert require_llm_url(config["services"]) == "http://overridden:8000"


def test_summary_carries_what_the_critic_did():
    """critic_eval_count is an axis, so the summary must evidence it.

    Without these columns a run whose critic never fired is indistinguishable
    from one where it did: same run_id, same columns, just faster.
    """
    for column in ("critic_ran_count", "critic_confirmed", "critic_rejected",
                   "critic_unverified"):
        assert column in SUMMARY_FIELDS


def test_reasoning_is_turned_off_for_the_deployed_model():
    """The deployed model reasons by default, and then returns no content.

    The chat template defaults enable_thinking to true, so a decomposition
    request that omits the kwarg comes back as pure reasoning with
    content=null. The agent disables it; so must this.
    """
    repo_root = find_repo_root()
    if repo_root is None:
        pytest.skip("no VSS checkout above this skill")

    _, extras = load_reasoning_controls(
        repo_root, "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    assert extras == {"chat_template_kwargs": {"enable_thinking": False}}


def test_a_model_the_agent_cannot_quieten_is_not_faked():
    """No kwarg invented for a model the product does not list.

    Sending enable_thinking to a model whose template ignores it would look
    like reasoning was off while it was on.
    """
    repo_root = find_repo_root()
    if repo_root is None:
        pytest.skip("no VSS checkout above this skill")

    tag, extras = load_reasoning_controls(repo_root, "gpt-4o-mini")
    assert extras == {}
    assert tag is None


def test_smoke_shrinks_the_sweep_to_one_cheap_cell():
    """One cell per critic setting and path, a handful of requests, no clock floor.

    Asserted against the shipped config so that adding a sweep axis later
    cannot quietly turn --smoke back into a multi-hour run.
    """
    import run_throughput

    config = load_config(CONFIG_PATH)
    run_throughput.apply_smoke_overrides(config)

    runs = build_execution_plan(config)
    assert [(r.critic, r.path) for r in runs] == [
        (critic, path)
        for critic in (False, True)
        for path in ("attribute", "event", "fusion")
    ]
    assert runs[0].concurrency == 2, "needs >1 worker to exercise the barrier"
    assert config["execution"]["min_run_duration_sec"] == 0

    budget = samples_per_query(config["execution"], runs[0].concurrency, 3)
    assert budget * 3 <= 12, "a smoke run must not cost minutes per query"


def test_smoke_leaves_the_gates_alone():
    """The corpus gate, health gate and run lock are what it exists to test."""
    import run_throughput

    config = load_config(CONFIG_PATH)
    before = {
        "require_min_docs": dict(config["corpus"]["require_min_docs"]),
        "health_gate": config["execution"].get("health_gate_timeout_sec"),
        "lock": dict(config["execution"].get("run_lock") or {}),
    }
    run_throughput.apply_smoke_overrides(config)

    assert config["corpus"]["require_min_docs"] == before["require_min_docs"]
    assert config["execution"].get("health_gate_timeout_sec") == before["health_gate"]
    assert dict(config["execution"].get("run_lock") or {}) == before["lock"]


def test_the_shipped_config_sets_both():
    """Neither can be inferred, so a config missing one fails here.

    Rather than two hours into a sweep, at path resolution.
    """
    config = load_config(CONFIG_PATH)
    assert require_origin(config["cli"])
    assert require_llm_url(config["services"])


def test_elasticsearch_is_reached_directly_not_through_the_ingress():
    """The ingress guards /elasticsearch against DELETE; the purge needs it.

    Routed through the ingress the purge fails quietly and the sweep measures
    the previous corpus alongside the new one.
    """
    config = load_config(CONFIG_PATH)
    origin = config["cli"]["origin_url"]
    assert not config["services"]["elasticsearch_url"].startswith(origin)


# --- run sizing ------------------------------------------------------------


def test_budget_grows_with_concurrency_to_hold_the_rounds_floor():
    exec_config = {"min_rounds_per_worker": 10,
                   "min_measured_requests_per_run": 200}
    # One query, so samples-per-query is the run total.
    assert samples_per_query(exec_config, concurrency=1, num_queries=1) == 200
    assert samples_per_query(exec_config, concurrency=5, num_queries=1) == 200
    # 10 rounds x 20 workers overtakes the flat floor.
    assert samples_per_query(exec_config, concurrency=20, num_queries=1) == 200
    assert samples_per_query(exec_config, concurrency=40, num_queries=1) == 400


def test_budget_divides_across_the_queries_a_run_cycles():
    exec_config = {"min_measured_requests_per_run": 200}
    assert samples_per_query(exec_config, concurrency=1, num_queries=3) == 67
    assert samples_per_query(exec_config, concurrency=1, num_queries=1) == 200


def test_budget_never_rounds_below_what_was_asked_for():
    """200 over 3 queries is 66.67; rounding down would under-run every cell."""
    assert samples_per_query(
        {"min_measured_requests_per_run": 200}, 1, 3
    ) * 3 >= 200


def test_flat_budget_when_neither_floor_is_configured():
    assert samples_per_query({"min_samples_per_query": 30}, 10, 3) == 30


# --- the shared budget -----------------------------------------------------


def test_budget_is_shared_not_per_worker():
    """Raising concurrency must shorten the run, not multiply the work."""
    budget = SampleBudget(["q1"], target_per_query=3)
    assert [budget.claim("q1") for _ in range(4)] == [True, True, True, False]


def test_a_failed_request_returns_its_slot():
    budget = SampleBudget(["q1"], target_per_query=1)
    assert budget.claim("q1")
    budget.release("q1")
    assert budget.claim("q1"), "a failure must not consume a sample"


def test_the_attempt_cap_stops_a_dead_service_spinning():
    budget = SampleBudget(["q1"], target_per_query=2)
    attempts = 0
    while budget.claim("q1"):
        budget.release("q1")
        attempts += 1
        assert attempts < 100, "claim loop did not terminate"
    assert attempts == 6  # 3x the target


def test_round_robin_skips_an_exhausted_query():
    budget = SampleBudget(["q1", "q2"], target_per_query=1)
    assert budget.next_claimable(0) == (0, "q1")
    budget.commit("q1")
    # q1 is done, so a scan starting at q1 must land on q2 rather than stop.
    assert budget.next_claimable(0) == (1, "q2")
    budget.commit("q2")
    assert budget.next_claimable(0) is None


def test_the_window_is_closed_until_it_is_opened():
    """A budget whose window never opened must stop at the target."""
    budget = SampleBudget(["q1"], target_per_query=1, min_duration_sec=999)
    assert budget.claim("q1")
    budget.commit("q1")
    assert not budget.claim("q1")
    assert not budget.extended


def test_the_duration_floor_hands_out_slots_past_the_target():
    budget = SampleBudget(["q1"], target_per_query=1, min_duration_sec=60)
    budget.open_window()
    assert budget.claim("q1")
    budget.commit("q1")
    assert budget.claim("q1"), "the clock floor should keep the window open"
    budget.commit("q1")
    assert budget.extended


def test_an_extension_keeps_the_extra_samples_level():
    """Analysis groups by query_id, so an extension must not favour one query."""
    budget = SampleBudget(["q1", "q2"], target_per_query=1, min_duration_sec=60)
    budget.open_window()
    for qid in ("q1", "q2"):
        assert budget.claim(qid)
        budget.commit(qid)
    assert budget.claim("q1")
    budget.commit("q1")
    # q1 is now ahead, so it must wait for q2.
    assert not budget.claim("q1")
    assert budget.claim("q2")


# --- statistics ------------------------------------------------------------


def test_percentile_matches_numpy_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 2.5
    assert percentile(values, 100) == 4.0
    assert percentile(values, 25) == pytest.approx(1.75)


def test_percentile_of_a_single_sample():
    assert percentile([7.0], 95) == 7.0


def test_percentile_of_nothing_is_an_error_not_a_zero():
    with pytest.raises(ValueError):
        percentile([], 50)


def _record(qid: str, latency: float, warmup=False, exit_code=0, error="",
            hits=1):
    return RequestRecord(
        run_id="r", query_id=qid, query_text="", worker_id=1, iteration=0,
        is_warmup=warmup, critic_eval_count=None, top_k=5, concurrency=1,
        path="event", latency_sec=latency, exit_code=exit_code, error=error,
        result_count=hits,
    )


def test_stats_exclude_warmup_and_failures():
    records = [
        _record("q1", 1.0),
        _record("q1", 2.0),
        _record("q1", 99.0, warmup=True),
        _record("q1", 99.0, exit_code=1, error="boom"),
        # An exit 0 whose output did not parse is not a measurement of a search.
        _record("q1", 99.0, exit_code=0, error="unreadable CLI output"),
    ]
    stats = compute_per_query_stats(records)
    assert stats["q1"]["count"] == 2
    assert stats["q1"]["max"] == 2.0


def test_succeeded_requires_both_exit_zero_and_readable_output():
    assert _record("q1", 1.0).succeeded
    assert not _record("q1", 1.0, error="unreadable CLI output").succeeded
    assert not _record("q1", 1.0, exit_code=3).succeeded


def test_a_search_that_found_nothing_is_still_a_measurement():
    """Excluding empty results would bias the distribution towards the
    queries that happen to match."""
    assert _record("q1", 1.0, hits=0).succeeded
    stats = compute_per_query_stats([_record("q1", 1.0, hits=0)])
    assert stats["q1"]["count"] == 1


# --- the ingest gate -------------------------------------------------------
#
# This is what decides a sweep may start. Every one of these is a way the gate
# could pass on a corpus that is not there.

GATE = {"mdx-raw-*": "sensorId.keyword", "mdx-embed-filtered-*": "sensor.id.keyword"}


def _uploads(*sensor_ids: str) -> list[UploadOutcome]:
    # The gate key is the filename stem (nvstreamer writes that into
    # mdx-raw-*.sensorId, not VST's opaque UUID), so make the stem match the
    # id each test asks for by naming the uploaded file after the sensor.
    return [
        UploadOutcome(source_path="a.mp4", upload_filename=f"{sid}.mp4",
                      success=True, sensor_id=f"vst-uuid-{i}")
        for i, sid in enumerate(sensor_ids)
    ]


def test_every_copy_owes_the_same_configured_count():
    targets = build_video_targets(
        _uploads("s1", "s2"), GATE,
        {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360},
    )
    assert [t.sensor_id for t in targets] == ["s1", "s2"]
    assert all(
        t.expected == {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360}
        for t in targets
    )


def test_a_partly_configured_expectation_is_no_expectation():
    """Half a bar would gate one index and silently wave the other through."""
    targets = build_video_targets(_uploads("s1"), GATE, {"mdx-raw-*": 54000})
    assert targets[0].expected == {}


def test_target_sensor_id_is_the_filename_stem_not_the_vst_uuid():
    """The gate queries ES by sensor key, and nvstreamer keys documents by
    filename stem; keying by VST's REST UUID instead would match zero docs and
    the gate would time out with sensor_field_mismatch even on a good ingest.
    """
    upload = UploadOutcome(
        source_path="/videos/clip.mp4",
        upload_filename="searchbench-20261012-000-clip.mp4",
        success=True,
        sensor_id="a639800e-f74c-43be-a6ee-fa3e99d70e69",
    )
    targets = build_video_targets([upload], GATE, {})
    assert len(targets) == 1
    assert targets[0].sensor_id == "searchbench-20261012-000-clip"
    # VST UUID is kept so the embed index -- which keys on it -- can be gated
    # by the right identifier.
    assert targets[0].vst_uuid == "a639800e-f74c-43be-a6ee-fa3e99d70e69"


def test_gate_entry_shorthand_defaults_to_the_filename_stem():
    """A bare field name is the nvstreamer case, the common one for this chart.

    Making it explicit for every pattern would turn every config into a wall
    of three-line dicts for the one in three that needs it.
    """
    entries = normalize_gate_indices({"mdx-raw-*": "sensorId.keyword"})
    assert entries["mdx-raw-*"] == GateEntry(
        field="sensorId.keyword", sensor_key=FILENAME_STEM,
    )


def test_gate_entry_dict_form_pins_the_embed_index_to_the_vst_uuid():
    entries = normalize_gate_indices({
        "mdx-embed-filtered-*": {
            "field": "sensor.id.keyword",
            "sensor_key": "vst_uuid",
        },
    })
    assert entries["mdx-embed-filtered-*"].sensor_key == VST_UUID


def test_gate_entry_rejects_an_unknown_sensor_key():
    """A typo must stop the sweep, not fall through to a wrong default."""
    with pytest.raises(ValueError, match="sensor_key"):
        normalize_gate_indices({"mdx-raw-*": {"field": "x", "sensor_key": "guid"}})


def test_sensor_key_picker_matches_the_index_the_target_queries():
    target = VideoTarget(
        upload_filename="clip-0.mp4",
        sensor_id="clip-0",
        vst_uuid="abc-123",
    )
    stem_entry = GateEntry(field="sensorId.keyword", sensor_key=FILENAME_STEM)
    uuid_entry = GateEntry(field="sensor.id.keyword", sensor_key=VST_UUID)
    assert _sensor_key_for(target, stem_entry) == "clip-0"
    assert _sensor_key_for(target, uuid_entry) == "abc-123"


def test_failed_and_sensorless_uploads_get_no_target():
    uploads = [
        UploadOutcome(source_path="a.mp4", upload_filename="s1.mp4",
                      success=True, sensor_id="vst-uuid-1"),
        UploadOutcome(source_path="a.mp4", upload_filename="s2.mp4",
                      success=True, sensor_id=""),
        UploadOutcome(source_path="a.mp4", upload_filename="s3.mp4",
                      success=False, sensor_id="vst-uuid-3"),
    ]
    targets = build_video_targets(uploads, GATE, {"mdx-raw-*": 1,
                                                  "mdx-embed-filtered-*": 1})
    assert [t.sensor_id for t in targets] == ["s1"]


def test_a_video_is_done_only_when_every_gated_index_has_it():
    target = VideoTarget("copy-0", "s1",
                         expected={"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360})
    target.observed = {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 359}
    assert target.met is False
    target.observed = {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360}
    assert target.met is True


def test_an_expectation_of_zero_is_never_met():
    """Otherwise an index nobody has a number for passes while empty."""
    target = VideoTarget("copy-0", "s1", expected={"mdx-raw-*": 0})
    target.observed = {"mdx-raw-*": 0}
    assert target.met is False


def test_no_expectation_at_all_is_never_met():
    assert VideoTarget("copy-0", "s1").met is False


def test_the_furthest_along_copy_sets_the_bar_for_the_rest():
    targets = build_video_targets(_uploads("s1", "s2", "s3"), GATE, {})
    targets[0].observed = {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360}
    targets[1].observed = {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360}
    targets[2].observed = {"mdx-raw-*": 27000, "mdx-embed-filtered-*": 180}

    converge_expected(targets, GATE)

    assert all(
        t.expected == {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 360}
        for t in targets
    )
    assert [t.met for t in targets] == [True, True, False]


def test_convergence_cannot_pass_an_index_no_copy_has_written():
    """Equal-at-zero is the failure mode convergence is most exposed to."""
    targets = build_video_targets(_uploads("s1", "s2"), GATE, {})
    for target in targets:
        target.observed = {"mdx-raw-*": 54000, "mdx-embed-filtered-*": 0}

    converge_expected(targets, GATE)

    assert all(t.met is False for t in targets)


def test_uploads_come_back_in_upload_order_not_completion_order():
    videos = [Path(f"v{i}.mp4") for i in range(5)]

    def upload(index: int, video: Path) -> UploadOutcome:
        # Reverse the finishing order, so ordering by completion would show.
        time.sleep((len(videos) - index) * 0.01)
        return UploadOutcome(source_path=str(video), upload_filename=video.name,
                             success=True, sensor_id=f"s{index}")

    outcomes = upload_videos(videos, concurrency=5, upload=upload)
    assert [o.sensor_id for o in outcomes] == ["s0", "s1", "s2", "s3", "s4"]


def test_a_thread_that_raises_still_appears_in_the_record():
    """Otherwise the upload count disagrees with the corpus size, silently."""
    def upload(index: int, video: Path) -> UploadOutcome:
        if index == 1:
            raise RuntimeError("connection reset")
        return UploadOutcome(source_path=str(video), upload_filename=video.name,
                             success=True, sensor_id=f"s{index}")

    outcomes = upload_videos([Path("a.mp4"), Path("b.mp4")], 2, upload)
    assert len(outcomes) == 2
    assert outcomes[1].success is False
    assert "connection reset" in outcomes[1].error


def test_shared_sensor_ids_are_reported():
    """One file uploaded N times is exactly what a content dedup would collapse."""
    uploads = [
        UploadOutcome(source_path="a.mp4", upload_filename="a", success=True,
                      sensor_id="same"),
        UploadOutcome(source_path="a.mp4", upload_filename="b", success=True,
                      sensor_id="same"),
        UploadOutcome(source_path="a.mp4", upload_filename="c", success=True,
                      sensor_id="other"),
    ]
    assert find_duplicate_sensor_ids(uploads) == {"same": ["a", "b"]}


def test_multipart_length_can_be_computed_without_reading_the_video():
    """The body is assembled by hand precisely so a 500 MB file is never buffered."""
    head, tail = _multipart_parts(
        boundary="BOUND",
        filename="clip.mp4",
        content_type="video/mp4",
        fields={"filename": "clip.mp4"},
    )
    assert b'name="filename"' in head
    assert b'name="mediaFile"; filename="clip.mp4"' in head
    assert head.endswith(b"\r\n\r\n")
    assert tail == b"\r\n--BOUND--\r\n"


# ---------------------------------------------------------------------------
# Critic on/off and per-path sweeps
# ---------------------------------------------------------------------------


def _sweep(**sweep):
    return {"sweep": {"top_k": [5], "concurrency": [1], **sweep}}


def test_without_a_critic_axis_every_run_has_the_critic_on():
    runs = build_execution_plan(_sweep(critic_eval_count=[1, None]))
    assert [(r.critic, r.critic_label) for r in runs] == [(True, "1"), (True, "all")]


def test_critic_off_is_one_setting_and_on_expands_per_count():
    runs = build_execution_plan(
        _sweep(critic=[False, True], critic_eval_count=[1, None])
    )
    assert [r.run_id for r in runs] == [
        "critic_off_topk_5_c1", "critic_1_topk_5_c1", "critic_all_topk_5_c1",
    ]
    assert runs[0].critic_eval_count is None


@pytest.mark.parametrize("value, expected", [
    ("on", True), ("off", False), (True, True), (False, False),
])
def test_critic_switch_reads_yaml_and_cli_spellings(value, expected):
    runs = build_execution_plan(_sweep(critic=[value]))
    assert runs[0].critic is expected


def test_critic_switch_rejects_a_count():
    with pytest.raises(ValueError, match="on or off"):
        build_execution_plan(_sweep(critic=["5"]))


def test_cli_critic_flag_overrides_the_config():
    runs = build_execution_plan(_sweep(critic=["off", "on"]), critic=["on"])
    assert [r.critic for r in runs] == [True]


def test_sweep_paths_gives_one_run_per_path_and_a_flag_overrides_it():
    config = _sweep(paths=["attribute", "event"])
    assert [r.path for r in build_execution_plan(config)] == ["attribute", "event"]
    assert [r.path for r in build_execution_plan(config, paths=["fusion"])] == ["fusion"]


def test_sweep_paths_rejects_an_unknown_path():
    with pytest.raises(ValueError, match="embed"):
        build_execution_plan(_sweep(paths=["embed"]))


def test_critic_off_config_drops_only_the_vlm_route(tmp_path):
    from bench.cli import write_critic_off_config

    sweep_home = tmp_path / "sweep"
    sweep_home.mkdir()
    services = {"rt_vlm": {"url": "http://vlm"}, "vst": {"url": "http://vst"}}
    (sweep_home / "config.json").write_text(json.dumps({"services": services}))

    target = write_critic_off_config(sweep_home, tmp_path / "off")
    written = json.loads(target.read_text())
    assert written["services"] == {"vst": {"url": "http://vst"}}
    original = json.loads((sweep_home / "config.json").read_text())
    assert "rt_vlm" in original["services"]


def test_probe_reports_a_dead_address():
    import socket

    from bench.cli import probe_service_urls

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        live = f"http://127.0.0.1:{srv.getsockname()[1]}"
        failures = probe_service_urls(
            {"live": live, "bad": "not a url", "dead": "http://127.0.0.1:1"},
            timeout_sec=1.0,
        )
    assert set(failures) == {"bad", "dead"}


def test_summary_row_fills_every_column():
    from bench.models import SUMMARY_FIELDS, RunSummary
    from bench.run import summary_row

    summary = RunSummary(
        run_id="critic_off_topk_5_c1_event", critic_eval_count=None, top_k=5,
        concurrency=1, path="event", critic=False, query_count=1,
        latency_stats={"p50": 1.0, "p95": 2.0, "p99": 3.0, "mean": 1.5},
    )
    row = summary_row(summary)
    assert set(row) == set(SUMMARY_FIELDS)
    assert row["path"] == "event"
    assert row["critic"] not in ("", None, True)
    assert row["p95_sec"] != ""


def test_service_of_strips_pod_suffixes():
    from bench.run import service_of

    assert service_of("vss-rtvi-cv-7d9f8b6c4d-x2k9p") == "vss-rtvi-cv"
    assert service_of("elasticsearch-master-0") == "elasticsearch-master"


def test_validate_path_selection_uses_resolved_paths():
    from bench.plan import validate_path_selection

    queries = [QueryItem(id="q1", query="a", expected_path="event")]
    validate_path_selection(["event"], queries)
    with pytest.raises(ValueError):
        validate_path_selection(["event"], queries, {"q1": "fusion"})


def test_corpus_config_rejects_a_gate_index_never_counted():
    from bench.corpus import validate_corpus_config

    config = {"corpus": {
        "es_index_patterns": ["mdx-embed-filtered-*"],
        "ingest": {"enabled": True, "readiness": {}},
    }}
    with pytest.raises(ValueError, match="mdx-raw"):
        validate_corpus_config(config)
    config["corpus"]["ingest"]["enabled"] = False
    validate_corpus_config(config)


def test_pod_log_scan_reads_the_written_files(tmp_path):
    from bench.resources import scan_pod_log_errors

    (tmp_path / "vss-agent-abc__agent.log").write_text(
        "ok\nERROR boom\nERROR ignored-noise\n"
    )
    result = scan_pod_log_errors(tmp_path, ["ERROR"], ["ignored-noise"])
    assert result["total_errors"] == 1
    assert result["per_pod"] == {"vss-agent-abc/agent": 1}
    assert scan_pod_log_errors(tmp_path / "none", ["ERROR"])["scan_status"] == "no_logs"


def _send(monkeypatch, run_config, cli_options):
    import subprocess as sp

    from bench import worker
    from bench.models import QueryPlan

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs.get("env")
        return sp.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    plan = QueryPlan("q2", "x", path="event", plan={"path": "event", "query": "x"})
    record = worker.send_search_request(
        vss_cmd=["vss"], query_item=QueryItem(id="q2", query="x"), query_plan=plan,
        run_config=run_config, worker_id=0, iteration=0, is_warmup=False,
        cli_options=cli_options,
    )
    return seen, record


def test_a_critic_off_request_runs_against_the_critic_off_config(monkeypatch):
    run = RunConfig(critic_eval_count=3, top_k=5, concurrency=1, critic=False)
    seen, record = _send(monkeypatch, run, {"critic_off_config_home": "/tmp/off"})
    assert seen["env"]["VSS_CONFIG_HOME"] == "/tmp/off"
    assert "--critic-eval-count" not in seen["argv"]
    assert record.critic is False


def test_a_critic_on_request_inherits_the_environment(monkeypatch):
    run = RunConfig(critic_eval_count=1, top_k=5, concurrency=1)
    seen, _ = _send(monkeypatch, run, {"critic_off_config_home": "/tmp/off"})
    assert seen["env"] is None


def test_a_critic_off_request_without_a_prepared_config_is_refused(monkeypatch):
    run = RunConfig(critic_eval_count=None, top_k=5, concurrency=1, critic=False)
    with pytest.raises(ValueError, match="critic-off config"):
        _send(monkeypatch, run, {})

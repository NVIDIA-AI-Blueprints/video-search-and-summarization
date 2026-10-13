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

"""Data models for the throughput benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryItem:
    """One query in the fixed query set."""

    id: str
    query: str
    #: Which CLI path this query is expected to route to. Checked against what
    #: decomposition actually produces -- a mismatch is reported, and analysis
    #: groups by the resolved path, never by this.
    expected_path: str = ""


@dataclass
class QueryPlan:
    """A query's resolved search path and arguments, fixed before measuring.

    Produced once per query by decomposing it against the deployment's LLM.
    Holding it fixed for the whole sweep is what makes a per-path comparison
    mean anything: the LLM is called at temperature 0 but is not guaranteed
    deterministic, and a query that routed differently between requests would
    put two pipelines in one latency distribution.
    """

    query_id: str
    query_text: str
    path: str = ""
    routed: bool = True
    plan: dict[str, Any] = field(default_factory=dict)
    decomposition: dict[str, Any] = field(default_factory=dict)
    #: Seconds the LLM took. Outside the measured window by construction, and
    #: recorded here so the cost is visible rather than merely excluded.
    decompose_sec: float = 0.0
    expected_path: str = ""
    status: str = "not_run"
    error: str = ""


@dataclass
class RunConfig:
    """One cell of the sweep matrix.

    ``path`` is the optional fourth dimension. Empty means one run covers the
    whole query set with workers cycling round-robin. Set to a CLI path such
    as ``"fusion"`` and the run measures only that path's queries, so latency
    and resource use attribute to one path instead of to a mixture.

    ``critic`` False turns verification off. The CLI has no flag for that --
    ``--critic-eval-count`` is bounded at 1 and omitting it verifies every hit
    -- so a critic-off run points the CLI at a copy of its config with the
    ``rt_vlm`` route removed, which is the one condition under which it skips
    the critic. ``critic_eval_count`` is how many hits a critic-on run verifies
    and is ignored when the critic is off.
    """

    critic_eval_count: int | None
    top_k: int
    concurrency: int
    path: str = ""
    critic: bool = True

    @property
    def critic_label(self) -> str:
        """``off``, ``all`` when every hit is verified, else the cap."""
        if not self.critic:
            return "off"
        return "all" if self.critic_eval_count is None else str(self.critic_eval_count)

    @property
    def run_id(self) -> str:
        """Directory name for this run, and the key ``--resume`` matches on.

        The path fragment is appended only when a path is set, so an unfiltered
        sweep keeps ids an earlier sweep would have used and a resume does not
        see every run as pending again.
        """
        base = (
            f"critic_{self.critic_label}_topk_{self.top_k}_c{self.concurrency}"
        )
        return f"{base}_{self.path}" if self.path else base


@dataclass
class RequestRecord:
    """Raw measurement for a single search request."""

    run_id: str
    query_id: str
    query_text: str
    worker_id: int
    iteration: int
    is_warmup: bool
    critic_eval_count: int | None
    top_k: int
    concurrency: int
    #: The CLI path this request actually ran. Known, not inferred: the
    #: benchmark chose it and put it on the command line.
    path: str = ""
    critic: bool = True
    started_at: str = ""
    completed_at: str = ""
    #: Wall clock around the subprocess, so it includes interpreter startup.
    #: That is the honest number for "what a CLI search costs", and
    #: ``cli_spawn_cost_sec`` in the run summary is what it should be compared
    #: against before any of it is attributed to the cluster.
    latency_sec: float = 0.0
    exit_code: int = -1
    result_count: int = 0
    error: str = ""
    #: Only on failure, and truncated. Enough to tell a wedged VLM from a bad
    #: flag without putting a megabyte of traceback in every record.
    stderr_excerpt: str = ""
    critic_ran: bool = False
    critic_confirmed: int = 0
    critic_rejected: int = 0
    critic_unverified: int = 0
    search_messages: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """Whether this request produced a usable search result.

        Not the same as exit 0. The CLI can exit 0 and write output that does
        not parse, and the worker records that in ``error`` while leaving the
        exit code alone -- the code is what the process said and is worth
        keeping verbatim. Such a response is not a measurement of a search: its
        latency is the latency of whatever went wrong, and counting it would
        let a broken deployment fill the sample budget and quietly lower every
        percentile.

        Zero hits *is* a success. An empty result is a real answer with a real
        latency, and excluding it would bias the distribution towards whichever
        queries happen to match.
        """
        return self.exit_code == 0 and not self.error


@dataclass
class RunSummary:
    """Aggregated result for one sweep cell."""

    run_id: str
    critic_eval_count: int | None
    top_k: int
    concurrency: int
    #: Which CLI path this run measured alone, "" for the whole set.
    path: str = ""
    critic: bool = True
    #: Median seconds for a bare ``vss --version``. The floor under every
    #: latency here, and at high concurrency the thing the host is busy doing.
    #: Published so a reader can tell a slow cluster from a saturated client.
    cli_spawn_cost_sec: float = 0.0
    #: How many queries the run cycled through. The sample budget is per query,
    #: so this is what turns the per-query target into the run's total.
    query_count: int = 0
    started_at: str = ""
    completed_at: str = ""
    wall_clock_sec: float = 0.0
    #: When the measured window opened, i.e. when the last worker cleared the
    #: post-warmup barrier. Every other collector cuts on this boundary; the pod
    #: logs deliberately do not, so anything counted out of them has to cut here
    #: or it describes a longer window than the latency beside it.
    measurement_started_at: str = ""
    total_requests: int = 0
    warmup_requests: int = 0
    measured_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    #: Measured over the post-warmup window only, so warmup inflates neither the
    #: numerator nor the denominator.
    queries_per_sec: float = 0.0
    measurement_wall_clock_sec: float = 0.0
    #: False when the post-warmup barrier did not release cleanly, meaning the
    #: measured window neither started at full concurrency nor could be timed
    #: apart from warmup. Recorded rather than left to look like a normal
    #: result, because such a run's throughput is not comparable.
    measurement_window_clean: bool = True
    #: How many measured requests carried critic evidence. ``critic_eval_count``
    #: is a sweep axis, so a run where the critic silently did not run would
    #: otherwise be indistinguishable from one where it did -- same run_id,
    #: same columns, lower latency.
    critic_ran_count: int = 0
    #: Summed over measured requests. ``unverified`` is the hits the critic was
    #: not asked about, which is what ``critic_eval_count`` caps: at top_k 20
    #: and a cap of 1, nineteen hits reach the caller unverified.
    critic_confirmed: int = 0
    critic_rejected: int = 0
    critic_unverified: int = 0
    #: Latency percentiles over every successful measured request of the run,
    #: whatever query it carried. ``per_query_stats`` splits the same samples.
    latency_stats: dict[str, float] = field(default_factory=dict)
    #: Resolved path of each query the run cycled, ``q1=attribute;...``. For a
    #: path run this is one entry; for the round-robin mix it is the mixture.
    resolved_paths: str = ""
    #: Seconds the pre-run health gate took, None when the gate was skipped.
    health_gate_sec: float | None = None
    per_query_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    prometheus_metrics: dict[str, Any] = field(default_factory=dict)
    pod_log_errors: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    cooldown: dict[str, Any] = field(default_factory=dict)


#: Columns identifying the sweep cell, leading every CSV.
CELL_FIELDS = ["run_id", "critic", "critic_eval_count", "top_k", "concurrency", "path"]

#: Column order of ``summary.csv``. One row per run.
SUMMARY_FIELDS = [
    *CELL_FIELDS,
    "query_count", "resolved_paths", "wall_clock_sec", "measurement_wall_clock_sec",
    "measurement_window_clean", "total_requests", "warmup_requests",
    "measured_requests", "success_count", "failure_count", "queries_per_sec",
    "p50_sec", "p95_sec", "p99_sec", "mean_sec",
    "cli_spawn_cost_sec",
    "critic_ran_count", "critic_confirmed", "critic_rejected", "critic_unverified",
    "pod_log_error_count", "health_gate_sec", "cooldown_reason", "cooldown_waited_sec",
    "started_at", "measurement_started_at", "completed_at",
]

#: Column order of ``per_query_summary.csv``. One row per (run, query).
PER_QUERY_FIELDS = [
    *CELL_FIELDS,
    "query_id", "resolved_path", "count",
    "p50_sec", "p95_sec", "p99_sec", "mean_sec", "min_sec", "max_sec", "std_sec",
]

#: Column order of ``resources_summary.csv``: one row per (run, metric, service),
#: flattened out of ``prometheus_metrics.json`` so it can be plotted directly.
RESOURCE_FIELDS = [*CELL_FIELDS, "metric", "service", "series", "avg", "peak"]

#: Column order of each run's ``per_query_latency.csv``. One row per measured request.
LATENCY_FIELDS = [
    "run_id", "query_id", "path", "worker_id", "iteration",
    "critic", "critic_eval_count", "top_k", "concurrency",
    "latency_sec", "exit_code", "result_count", "critic_ran",
    "critic_confirmed", "critic_rejected", "critic_unverified",
    "started_at", "completed_at", "error",
]

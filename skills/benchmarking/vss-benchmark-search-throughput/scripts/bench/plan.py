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

"""Turning a config into a list of runs, and sizing each one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import QueryItem, RunConfig
from .routing import EVENT, FUSION, SEARCH_PATHS, TAG


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump_config(config: dict[str, Any]) -> str:
    """The effective config, as written beside each run's results.

    Here rather than in the entrypoint so that yaml stays an implementation
    detail of this package: the entrypoint importing it directly would run
    before the dependency check in ``bench/__init__``, and a missing package
    would surface as a traceback instead of the message that names all of
    them.
    """
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def build_query_set(config: dict[str, Any]) -> list[QueryItem]:
    raw = config.get("query_set") or []
    if not raw:
        raise ValueError("config.query_set is empty -- nothing to benchmark")
    seen: set[str] = set()
    items: list[QueryItem] = []
    for entry in raw:
        qid = str(entry.get("id") or "").strip()
        text = str(entry.get("query") or "").strip()
        if not qid or not text:
            raise ValueError(f"config.query_set entry needs both id and query: {entry!r}")
        # Duplicate ids would collide in the sample budget and in every
        # per-query artifact, silently merging two queries into one row.
        if qid in seen:
            raise ValueError(f"duplicate query id {qid!r} in config.query_set")
        seen.add(qid)
        expected = str(entry.get("expected_path") or "").strip()
        if expected and expected not in SEARCH_PATHS:
            raise ValueError(
                f"query {qid!r} has expected_path {expected!r}, which is not a "
                f"CLI search path (expected one of: {', '.join(SEARCH_PATHS)})"
            )
        items.append(QueryItem(id=qid, query=text, expected_path=expected))
    return items


#: Paths a pinned query can run: they need only ``--query``. ``attribute`` and
#: ``object`` need arguments only a decomposition produces.
PINNABLE_PATHS = (EVENT, FUSION, TAG)


def read_pin_paths(config: dict[str, Any], query_set: list[QueryItem]) -> dict[str, str]:
    """``decomposition.pin_paths``, validated against the query set."""
    raw = (config.get("decomposition", {}) or {}).get("pin_paths") or {}
    pins = {str(k): str(v) for k, v in raw.items()}
    known = {q.id for q in query_set}
    for qid, path in pins.items():
        if qid not in known:
            raise ValueError(f"decomposition.pin_paths names {qid!r}, which is not in query_set")
        if path not in PINNABLE_PATHS:
            raise ValueError(
                f"decomposition.pin_paths pins {qid!r} to {path!r}; a pinned query "
                f"can only run {', '.join(PINNABLE_PATHS)}"
            )
    return pins


def queries_for_path(
    query_set: list[QueryItem],
    path: str,
    resolved: dict[str, str] | None = None,
) -> list[QueryItem]:
    """The queries one run measures: those on ``path``, or all when empty.

    ``resolved`` maps query id to the path decomposition actually produced. Pass
    it and selection uses it, which is the accurate choice and is available
    because routing now happens in this script, before the sweep, instead of
    being read out of agent logs afterwards.

    Without it, selection falls back to ``expected_path``. That is only for
    building a plan early -- to validate a ``--path`` flag before the LLM has
    been contacted -- and a run selected that way can hold queries that route
    elsewhere.
    """
    if not path:
        return list(query_set)
    if resolved is not None:
        return [q for q in query_set if resolved.get(q.id) == path]
    return [q for q in query_set if q.expected_path == path]


def coerce_critic_eval_count(value: Any) -> int | None:
    """Read a sweep entry as a ``--critic-eval-count`` value.

    ``null`` means "no cap": the CLI verifies every hit. "Critic off" is not a
    count -- the flag is bounded at 1 -- and is the separate ``sweep.critic``
    axis instead.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            "critic_eval_count is a count, not a switch. Use null to verify "
            "every hit, or an integer >= 1 to cap how many are verified."
        )
    text = str(value).strip().lower()
    if text in {"", "null", "none", "all"}:
        return None
    try:
        count = int(text)
    except ValueError:
        raise ValueError(
            f"cannot read {value!r} as a critic_eval_count; expected an "
            "integer >= 1, or null to verify every hit"
        ) from None
    if count < 1:
        raise ValueError(
            f"critic_eval_count must be >= 1, got {count}. The CLI rejects 0; "
            "use null to verify every hit."
        )
    return count


def coerce_critic_switch(value: Any) -> bool:
    """Read a ``sweep.critic`` entry. YAML 1.1 already turns on/off into bools."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"on", "true", "yes", "1"}:
        return True
    if text in {"off", "false", "no", "0"}:
        return False
    raise ValueError(f"cannot read {value!r} as a critic switch; expected on or off")


def _critic_settings(
    sweep: dict[str, Any],
    critic: list[Any] | None,
    critic_eval_count: list[Any] | None,
) -> list[tuple[bool, int | None]]:
    """``(critic on?, eval count)`` pairs, in sweep order.

    Without ``sweep.critic`` every run has the critic on and the axis is
    ``critic_eval_count``, as before. With it, each ``on`` expands to one run per
    ``critic_eval_count`` value and ``off`` is a single setting.
    """
    raw_counts = (
        critic_eval_count
        if critic_eval_count is not None
        else sweep.get("critic_eval_count", [None])
    )
    if not isinstance(raw_counts, list):
        raw_counts = [raw_counts]
    counts = list(dict.fromkeys(coerce_critic_eval_count(v) for v in raw_counts))
    if not counts:
        raise ValueError(
            "no critic_eval_count values selected; expected null and/or integers >= 1"
        )

    raw_switches = critic if critic is not None else sweep.get("critic")
    if raw_switches is None:
        return [(True, c) for c in counts]
    if not isinstance(raw_switches, list):
        raw_switches = [raw_switches]
    settings: list[tuple[bool, int | None]] = []
    for on in dict.fromkeys(coerce_critic_switch(v) for v in raw_switches):
        if on:
            settings.extend((True, c) for c in counts)
        else:
            settings.append((False, None))
    if not settings:
        raise ValueError("sweep.critic is empty; expected on and/or off")
    return settings


def build_execution_plan(
    config: dict[str, Any],
    top_k: list[int] | None = None,
    concurrency: list[int] | None = None,
    critic_eval_count: list[Any] | None = None,
    critic: list[Any] | None = None,
    paths: list[str] | None = None,
    path_order: str | None = None,
    query_set: list[QueryItem] | None = None,
    resolved_paths: dict[str, str] | None = None,
) -> list[RunConfig]:
    """Build the list of runs from config plus command-line overrides.

    ``query_set`` and ``resolved_paths`` are passed through to
    :func:`validate_path_selection`; omit ``query_set`` to skip that check.
    """
    sweep = config.get("sweep") or {}
    # Duplicates would run the same configuration twice under one run_id, and
    # the second would overwrite the first's directory.
    top_k_values = _positive_ints("top_k", top_k or sweep.get("top_k") or [5, 10, 20])
    concurrency_values = _positive_ints(
        "concurrency", concurrency or sweep.get("concurrency") or [1, 5, 10, 20]
    )
    critic_settings = _critic_settings(sweep, critic, critic_eval_count)

    # [""] is the unfiltered case: one run per cell over the whole query set.
    path_values = list(dict.fromkeys(paths or sweep.get("paths") or [""]))
    for path in path_values:
        if path and path not in SEARCH_PATHS:
            raise ValueError(
                f"path {path!r} is not a search path "
                f"(expected one of: {', '.join(SEARCH_PATHS)})"
            )
    if query_set is not None:
        validate_path_selection(path_values, query_set, resolved_paths)

    order = path_order or sweep.get("path_order", "grouped")
    if order not in ("interleaved", "grouped"):
        raise ValueError(f"sweep.path_order must be 'interleaved' or 'grouped', got {order!r}")

    # Either way, path stays *below* the critic setting. Above it the sweep
    # would run one path under every critic setting before touching the next,
    # and an abort -- which a wedged VLM has caused repeatedly -- would leave
    # whole paths with no data at all. Keeping critic outermost is what lets an
    # aborted sweep still deliver a complete first critic setting.
    runs: list[RunConfig] = []
    for critic_on, eval_count in critic_settings:
        for outer in path_values if order == "grouped" else [None]:
            for tk in top_k_values:
                for cc in concurrency_values:
                    inner = [outer] if order == "grouped" else path_values
                    for p in inner:
                        runs.append(
                            RunConfig(
                                critic_eval_count=eval_count,
                                top_k=tk,
                                concurrency=cc,
                                path=p or "",
                                critic=critic_on,
                            )
                        )
    return runs


def _positive_ints(name: str, values: list[Any]) -> list[int]:
    try:
        ints = list(dict.fromkeys(int(v) for v in values))
    except (TypeError, ValueError):
        raise ValueError(f"{name} values must be integers, got {values!r}") from None
    if any(v < 1 for v in ints):
        raise ValueError(f"{name} values must be >= 1, got {values!r}")
    return ints


def validate_path_selection(
    paths: list[str],
    query_set: list[QueryItem],
    resolved_paths: dict[str, str] | None = None,
) -> None:
    """Reject a path that no query routes to.

    With ``resolved_paths`` this checks what decomposition produced. Without
    it, it can only check ``expected_path``, which is a guess -- so the check
    is skipped when any query has no expectation, rather than failing a sweep
    whose queries would have routed correctly.
    """
    if resolved_paths is None and any(not q.expected_path for q in query_set):
        return
    source = (
        sorted(set(resolved_paths.values()))
        if resolved_paths is not None
        else sorted({q.expected_path for q in query_set})
    )
    for path in paths:
        if path and not queries_for_path(query_set, path, resolved_paths):
            raise ValueError(
                f"path {path!r} is selected, but no query in config.query_set "
                f"routes there (available: {', '.join(source) or 'none'})"
            )


def samples_per_query(
    exec_config: dict[str, Any],
    concurrency: int,
    num_queries: int,
) -> int:
    """Measured samples per query for one (concurrency, query count) pair.

    A flat budget cannot serve both ends of the sweep. The measured window opens
    at full concurrency on the barrier but closes by draining: each worker stops
    once it has spent its share, so the tail runs at falling concurrency while
    the clock keeps counting. With a run total of N shared across C workers each
    does N/C rounds, and the loss is worth roughly one round however large the
    budget, so the error goes as 1/rounds. Measured: time-weighted concurrency
    reached 67% of nominal at 2.5 rounds per worker, 82% at 4, 92% at 7 and 100%
    at 31.

    At the other end the binding constraint is not concurrency but sample count:
    c1 already holds full concurrency with a single round, yet a run of 10
    requests puts the 95% interval on the mean past +/-400% for the bimodal
    queries, and a p95 over 10 samples is just their maximum.

    So the run total is the larger of a flat floor and a per-worker round count,
    and the per-query target follows from dividing by however many queries the
    run cycles through. This sizes the run in requests only;
    ``execution.min_run_duration_sec`` adds a floor on the clock that can carry
    a run well past the number returned here.
    """
    rounds = exec_config.get("min_rounds_per_worker")
    floor = exec_config.get("min_measured_requests_per_run")
    if rounds is None and floor is None:
        return int(exec_config.get("min_samples_per_query", 30))

    run_total = max(int(floor or 0), int(rounds or 0) * concurrency)
    # Round up so a run never lands under the budget it was asked for.
    return max(1, -(-run_total // max(1, num_queries)))


def completed_run_ids(output_dir: Path) -> set[str]:
    """Run IDs already finished in this output directory.

    ``run_summary.json`` is written last, so its presence is what marks a run
    complete for ``--resume``.
    """
    if not output_dir.exists():
        return set()
    return {
        child.name
        for child in output_dir.iterdir()
        if child.is_dir() and (child / "run_summary.json").exists()
    }

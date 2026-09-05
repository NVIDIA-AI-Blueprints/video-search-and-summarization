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

"""Query backend: the ``vss search run`` CLI."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from typing import Any

from . import routing
from .base import SEARCH_TIMEOUT

#: The four retrieval paths declared by vss_cli.search_group.SearchGroup.
SEARCH_PATHS = ("embed", "attribute", "fusion", "object")

#: Documented in skills/vss-search-archive/references/cli_usage.md.
CLI_EXIT_MEANINGS = {
    0: "success",
    2: "invalid input (unknown flag or bad value)",
    3: "backend unreachable",
    4: "configuration -- not configured, foreign config, or a required service absent",
    5: "not found -- a searched index does not exist (nothing ingested yet)",
}

#: Exit codes that mean an environment fault rather than a bad query. These
#: abort the run instead of contributing a zero score.
#:
#: Kept for reporting, but see ``is_fatal_exit``: the real policy is that ANY
#: non-zero exit aborts. A broken CLI install exits 1, which is not in the
#: documented table, and treating "undocumented" as "soft" scored 121 queries
#: at 0.0 in a real run -- precisely the silent-zero this module exists to stop.
CLI_FATAL_EXITS = {2, 3, 4, 5}


def is_fatal_exit(code: int) -> bool:
    """Any non-zero exit is fatal.

    There is no ``vss search run`` exit code that means "this one query failed
    but the rest are fine". Success is 0; everything else is an environment or
    usage fault that will recur for every remaining query, so continuing only
    manufactures zeros that look like a retrieval collapse.
    """
    return code != 0


class CliExitError(RuntimeError):
    """A ``vss search run`` invocation failed in a way that must not be scored.

    Exit 4 (misconfiguration) and exit 5 (index absent) would otherwise be
    indistinguishable from a genuine zero-recall query. Scoring them as 0.0
    reports a catastrophic accuracy regression when the real problem is that
    nothing was ingested.
    """



class CliQueryBackend:
    """``vss search run <path>`` as a subprocess.

    This is the layer the OpenClaw agent ultimately reaches through the
    ``vss-search-archive`` skill, so exercising it measures the retrieval half
    of the new UI flow without an LLM in the loop -- deterministic, and
    directly comparable to the REST baseline.

    Two modes, depending on whether decompositions are supplied:

    * **Fixed** (no decompositions) -- every query uses one caller-chosen path.
      Fine for embed-only regression, but not comparable to the agent, which
      decomposes and routes per query.
    * **Routed** (decompositions given) -- the path and arguments come from the
      decomposition the agent would have produced, so the CLI receives the same
      structured request the agent's retrieval leg receives. This is what makes
      a CLI-vs-agent comparison measure *retrieval* rather than the presence or
      absence of a decomposition step.

    Neither mode performs decomposition itself. That is an LLM call, and doing
    it here would put a model in the eval loop; see routing.py.
    """

    name = "cli"

    def __init__(
        self,
        vss_cmd: list[str],
        search_path: str = "embed",
        top_k: int = 5,
        min_cosine_similarity: float | None = None,
        source_type: str = "video_file",
        attributes: list[str] | None = None,
        # Deliberately the OPPOSITE of the upstream CLI default. Upstream merges
        # contiguous same-sensor windows and reports the mean of their scores,
        # which changes both the hit count (the precision denominator) and the
        # score semantics. The historical REST baseline has unmerged windows, so
        # defaulting to merged here would silently invalidate every comparison
        # against it. Callers wanting product-default behaviour must ask.
        merge_adjacent: bool = False,
        cwd: str | None = None,
        timeout: int = SEARCH_TIMEOUT,
        decompositions: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if search_path not in SEARCH_PATHS:
            raise ValueError(f"search_path must be one of {SEARCH_PATHS}, got {search_path!r}")
        self.vss_cmd = vss_cmd
        self.search_path = search_path
        self.top_k = top_k
        self.min_cosine_similarity = min_cosine_similarity
        self.source_type = source_type
        self.attributes = attributes or []
        self.merge_adjacent = merge_adjacent
        self.cwd = cwd
        self.timeout = timeout
        #: query text -> decomposition. Empty means fixed-flag behaviour.
        self.decompositions = decompositions or {}
        #: plans actually executed, so the summary can report the path split.
        self.executed_plans: list[dict[str, Any]] = []
        #: query text -> per-stage timings, when the deployment reports them.
        #: Keyed per query rather than "the last one", because queries run
        #: concurrently and a single slot would hand one query another's
        #: numbers. Dict assignment is atomic under the GIL, so no lock.
        self.timings_by_query: dict[str, dict[str, Any]] = {}

    def describe(self) -> dict[str, Any]:
        # In routed mode `search_path` is only the fallback for queries the
        # decompositions do not cover, so labelling it "search_path" reads as
        # though every query used it. Name it for what it is.
        path_key = "fallback_path" if self.decompositions else "search_path"
        return {
            "backend": self.name,
            "vss_cmd": " ".join(self.vss_cmd),
            "routing": "per-query (from decompositions)" if self.decompositions else "fixed",
            path_key: self.search_path,
            "top_k": self.top_k,
            "min_cosine_similarity": self.min_cosine_similarity,
            "source_type": self.source_type,
            "attributes": self.attributes,
            "merge_adjacent": self.merge_adjacent,
            "decompositions": len(self.decompositions),
        }

    def plan_for_query(self, query: str) -> dict[str, Any]:
        """The retrieval plan for one query.

        With no decompositions supplied this is the fixed-flag behaviour; with
        them, the path and arguments are derived per query the way the agent's
        decomposition step would have.
        """
        return routing.plan_for(
            query,
            self.decompositions.get(query),
            default_path=self.search_path,
            default_attributes=self.attributes,
            default_source_type=self.source_type,
        )

    def build_argv(self, query: str, plan: dict[str, Any] | None = None) -> list[str]:
        """Assemble the invocation. Split out so it is unit-testable offline."""
        plan = plan or self.plan_for_query(query)
        path = plan["path"]
        argv = [*self.vss_cmd, "search", "run", path]

        # Each path accepts only its own fields; passing another path's flag is
        # a usage error, not something the CLI ignores.
        if path in ("embed", "fusion"):
            argv += ["--query", plan["query"]]
        if path in ("attribute", "fusion"):
            for attr in plan["attributes"]:
                argv += ["--attribute", attr]
        if path == "object":
            for object_id in plan["object_ids"]:
                argv += ["--object-id", str(object_id)]

        argv += ["--source-type", plan["source_type"]]
        argv += ["--top-k", str(plan.get("top_k") or self.top_k)]

        for source in plan.get("video_sources") or []:
            argv += ["--video-source", source]
        if plan.get("timestamp_start"):
            argv += ["--timestamp-start", plan["timestamp_start"]]
        if plan.get("timestamp_end"):
            argv += ["--timestamp-end", plan["timestamp_end"]]

        if self.min_cosine_similarity is not None and path in ("embed", "fusion"):
            argv += ["--min-cosine-similarity", str(self.min_cosine_similarity)]

        if not self.merge_adjacent:
            argv.append("--no-merge-adjacent")

        argv.append("--raw")
        return argv

    def search(self, query: str) -> tuple[list[dict[str, Any]], float]:
        plan = self.plan_for_query(query)
        self.executed_plans.append(plan)
        argv = self.build_argv(query, plan)
        start = time.time()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            latency = time.time() - start
            print(f"  CLI timeout after {self.timeout}s: {shlex.join(argv)}")
            return [], latency
        latency = time.time() - start

        if is_fatal_exit(proc.returncode):
            meaning = CLI_EXIT_MEANINGS.get(proc.returncode, "undocumented exit code")
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise CliExitError(
                f"vss exited {proc.returncode} ({meaning}): {detail}\n  command: {shlex.join(argv)}"
            )

        hits, _messages, timings = parse_cli_output(proc.stdout)
        if timings:
            self.timings_by_query[query] = timings
        return hits, latency


def parse_cli_output(stdout: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Extract ``(hits, search_messages, timings)`` from ``vss search run`` stdout.

    The CLI writes **NDJSON on stdout** -- one JSON document per line, not one
    document total. A search prints at least two::

        {"data": [...], "search_messages": [...], "job_id": "search-01M1...",
         "persisted": false, "record": "absent"}
        {"event": "vss_job_completed", "group": "search", "job_id": "...",
         "status": "completed", "exit_hint": 0}

    Both go to stdout; stderr is empty. Parsing the buffer as a single document
    therefore raises "Extra data" and silently discards a perfectly good result
    set -- which is exactly what happened: 106 of 121 queries in a real run were
    dropped this way and the eval reported mAP 0.0000 against an index that was
    answering correctly.

    So: parse line by line, skip lifecycle events, and return the first document
    that carries a payload. A whole-buffer parse is tried first so a
    pretty-printed (multi-line) document still works.
    """
    text = (stdout or "").strip()
    if not text:
        return [], [], {}

    documents: list[Any] = []

    # Single document, possibly pretty-printed across lines.
    try:
        documents.append(json.loads(text))
    except json.JSONDecodeError:
        # NDJSON: one document per line. Unparseable lines are banners or
        # progress noise, not results, so they are skipped rather than fatal.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not documents:
        print(f"  Could not parse CLI output as JSON: {text[:200]}")
        return [], [], {}

    for payload in documents:
        if isinstance(payload, list):
            return payload, [], {}
        if not isinstance(payload, dict):
            continue
        # Job lifecycle events carry no results; keep looking.
        if "event" in payload and "data" not in payload:
            continue
        if "data" in payload:
            messages = list(payload.get("search_messages") or [])
            for message in messages:
                print(f"  [search_message] {message}")
            # Present only on deployments carrying the search_core timings
            # change; absent means nobody collected, not zero time.
            timings = payload.get("timings") or {}
            return payload.get("data") or [], messages, timings

    # Documents parsed, but none held a payload -- report it rather than
    # returning an empty result that would score as zero recall.
    print(f"  CLI output had no 'data' field: {text[:200]}")
    return [], [], {}

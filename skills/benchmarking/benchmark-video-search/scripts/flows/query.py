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
from pathlib import Path
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

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class QueryUnanswerableError(RuntimeError):
    """This query produced no answer, and no answer is not the same as no hits.

    A timeout, unparseable stdout, or a payload with no ``data`` envelope all
    mean the backend never told us what it found. Returning ``[]`` for those
    makes them arithmetically identical to a search that ran perfectly and
    matched nothing, so they land in the aggregate as zero recall and publish a
    protocol fault as a retrieval regression.

    Distinct from :class:`CliExitError`, which is fatal for the whole run: this
    one is per query. The caller decides whether to abort or to exclude it, but
    it must not score it.
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
        # Send the pre-decomposition question along with the decomposed
        # arguments, so the critic verifies what the user asked rather than a
        # string the host reassembles from the retrieval arguments. Default-on
        # because it is what the REST path does (`tools/search.py` captures
        # `original_query` BEFORE decomposition), so omitting it is what makes
        # the two flows incomparable. Requires a CLI carrying
        # `--original-query`; run_eval_flows probes for it.
        pass_original_query: bool = True,
        critic_eval_count: int | None = None,
        # Dense / full-gallery mode: request every clip in one pass. Only a marker
        # here -- the caller (build_query_backend) sets top_k/min_cosine_similarity/
        # merge_adjacent to the dense values; describe() surfaces it for the reader.
        full_gallery: bool = False,
        attribute_input: str | None = None,
        candidate_limit: int | None = None,
        attribute_min_similarity: float | None = None,
        critic_enabled: bool | None = None,
        critic_frame_budget: int | None = None,
        critic_input_width: int | None = None,
        critic_input_height: int | None = None,
        critic_diagnostic_dir: str | None = None,
        retrieval_pool_size: int | None = None,
        frame_scoring: str = "auto",
        frame_lookup_transport: str = "auto",
    ) -> None:
        if search_path not in SEARCH_PATHS:
            raise ValueError(
                f"search_path must be one of {SEARCH_PATHS}, got {search_path!r}"
            )
        if critic_eval_count is not None and critic_eval_count < 1:
            raise ValueError("critic_eval_count must be at least 1")
        if frame_scoring not in ("auto", "source", "indexed"):
            raise ValueError("invalid frame_scoring")
        if frame_lookup_transport not in ("auto", "single", "msearch"):
            raise ValueError("invalid frame_lookup_transport")
        self.frame_scoring = frame_scoring
        self.frame_lookup_transport = frame_lookup_transport
        self.frame_profiles: dict[str, dict[str, Any]] = {}
        self.vss_cmd = vss_cmd
        self.search_path = search_path
        self.top_k = top_k
        self.min_cosine_similarity = min_cosine_similarity
        self.source_type = source_type
        self.attributes = attributes or []
        self.merge_adjacent = merge_adjacent
        self.cwd = cwd
        self.timeout = timeout
        self.pass_original_query = pass_original_query
        self.critic_eval_count = critic_eval_count
        self.full_gallery = full_gallery
        self.attribute_input = attribute_input
        self.require_decomposition = False
        self.critic_enabled = critic_enabled
        self.critic_frame_budget = critic_frame_budget
        self.critic_input_width = critic_input_width
        self.critic_input_height = critic_input_height
        self.critic_diagnostic_dir = critic_diagnostic_dir
        if retrieval_pool_size is not None and (
            type(retrieval_pool_size) is not int or not 1 <= retrieval_pool_size <= 100000
        ):
            raise ValueError("retrieval_pool_size must be an integer in [1, 100000]")
        self.retrieval_pool_size = retrieval_pool_size
        self.candidate_limit = candidate_limit
        self.attribute_min_similarity = attribute_min_similarity
        #: query text -> decomposition. Empty means fixed-flag behaviour.
        self.decompositions = decompositions or {}
        self.ambiguous_query_texts: set[str] = set()
        #: plans actually executed, so the summary can report the path split.
        self.executed_plans: list[dict[str, Any]] = []
        #: query text -> per-stage timings, when the deployment reports them.
        #: Keyed per query rather than "the last one", because queries run
        #: concurrently and a single slot would hand one query another's
        #: numbers. Dict assignment is atomic under the GIL, so no lock.
        self.timings_by_query: dict[str, dict[str, Any]] = {}
        self.metadata_by_query: dict[str, dict[str, Any]] = {}

    def readiness(self, sources: list[str], require_attributes: bool = True,
                  content_contract: str | Path | None = None) -> dict[str, Any]:
        argv = [*self.vss_cmd, "search", "readiness", "--raw",
                "--require-attributes" if require_attributes else "--no-require-attributes"]
        for source in sources:
            argv.extend(["--video-source", source])
        if content_contract is not None:
            argv.extend(["--content-contract", str(Path(content_contract).resolve())])
        proc = subprocess.run(argv, capture_output=True, text=True, check=False,
                              cwd=self.cwd, timeout=self.timeout)
        # Exit PARTIAL is an observed, not-yet-ready index; all other errors
        # retain the CLI failure rather than being parsed as an empty result.
        if proc.returncode not in (0, 6):
            raise CliExitError(f"Readiness exited {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}",
                               returncode=proc.returncode)
        body = json.loads(proc.stdout)
        if not isinstance(body, dict) or type(body.get("ready")) is not bool:
            raise ValueError("Malformed CLI readiness result")
        if (proc.returncode == 0) != body["ready"]:
            raise ValueError("CLI readiness exit disagrees with response")
        return body

    def capture_frame_capabilities(self, source_types: set[str] | None = None) -> None:
        """Resolve live execution identity before inference and resume validation."""
        profiles = {}
        for source_type in sorted(source_types or {self.source_type}):
            argv = [*self.vss_cmd, "search", "frame-capabilities", "--raw",
                    "--source-type", source_type, "--frame-scoring", self.frame_scoring,
                    "--frame-lookup-transport", self.frame_lookup_transport]
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, check=False,
                                      cwd=self.cwd, timeout=self.timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CliExitError("Frame capability probe failed") from exc
            if proc.returncode:
                raise CliExitError(f"Frame capability probe exited {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}",
                                   returncode=proc.returncode)
            try:
                profile = json.loads(proc.stdout)
                if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                    raise ValueError("missing enabled flag")
                if profile["enabled"] and not profile.get("fingerprint"):
                    raise ValueError("missing execution fingerprint")
            except (ValueError, TypeError) as exc:
                raise CliExitError("Malformed frame capability profile") from exc
            profiles[source_type] = profile
        self.frame_profiles = profiles

    def capture_deployment_provenance(self) -> None:
        """Read recorded deployment models via the CLI without probing inference."""
        self.deployment_identity = {"status": "unavailable"}
        try:
            result = subprocess.run(
                [*self.vss_cmd, "configure", "show"],
                capture_output=True,
                text=True,
                check=False,
                cwd=self.cwd,
            )
            if result.returncode != 0:
                self.deployment_models = {
                    "status": f"configure show exited {result.returncode}"
                }
                return
            recorded = json.loads(result.stdout)
            self.deployment_identity = {
                "base_url": recorded.get("base_url"),
                "services": {
                    name: {
                        "url": service.get("url"),
                        "models": service.get("models", []),
                    }
                    for name, service in recorded.get("services", {}).items()
                    if isinstance(service, dict)
                },
            }
            self.deployment_models = {
                name: service.get("models", [])
                for name, service in recorded.get("services", {}).items()
                if isinstance(service, dict)
            }
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.deployment_models = {"status": f"unavailable: {type(exc).__name__}"}

    def describe(self) -> dict[str, Any]:
        # In routed mode `search_path` is only the fallback for queries the
        # decompositions do not cover, so labelling it "search_path" reads as
        # though every query used it. Name it for what it is.
        path_key = "fallback_path" if self.decompositions else "search_path"
        description = {
            "backend": self.name,
            "vss_cmd": " ".join(self.vss_cmd),
            "routing": "per-query (from decompositions)"
            if self.decompositions
            else "fixed",
            path_key: self.search_path,
            "top_k": self.top_k,
            "min_cosine_similarity": self.min_cosine_similarity,
            "source_type": self.source_type,
            "attributes": self.attributes,
            "merge_adjacent": self.merge_adjacent,
            "decompositions": len(self.decompositions),
            "pass_original_query": self.pass_original_query,
            "critic_eval_count": self.critic_eval_count,
            "full_gallery": self.full_gallery,
        }

        for name in (
            "attribute_input",
            "candidate_limit",
            "attribute_min_similarity",
            "critic_enabled",
            "critic_frame_budget",
            "critic_input_width",
            "critic_input_height",
            "critic_diagnostic_dir",
            "decomposition_source",
            "retrieval_pool_size",
            "deployment_identity",
            "frame_scoring",
            "frame_lookup_transport",
            "frame_profiles",
        ):
            value = getattr(self, name, None)
            if value is not None:
                description[name] = value
        return description

    def decomposition_for_query(self, query: str, query_id: str | None = None):
        if query_id is not None and query_id in self.decompositions:
            return self.decompositions[query_id]
        if query in self.ambiguous_query_texts:
            return None
        return self.decompositions.get(query)

    def plan_for_query(self, query: str, query_id: str | None = None) -> dict[str, Any]:
        """The retrieval plan for one query.

        With no decompositions supplied this is the fixed-flag behaviour; with
        them, the path and arguments are derived per query the way the agent's
        decomposition step would have.
        """
        if self.attribute_input is not None:
            attrs = [query]
            if self.attribute_input == "decomposed":
                saved = self.decomposition_for_query(query, query_id)
                if not isinstance(saved, dict):
                    raise QueryUnanswerableError(
                        "missing decomposition for attribute replay"
                    )
                try:
                    # This comparison consumes only attributes. Route/source/time
                    # fields are deliberately outside its scope, even when invalid.
                    routing.validate_decomposition({
                        "attributes": saved.get("attributes"),
                        **({"_decomposition_error": saved["_decomposition_error"]}
                           if "_decomposition_error" in saved else {}),
                    })
                except ValueError as exc:
                    raise QueryUnanswerableError(
                        f"invalid decomposition for attribute replay: {exc}"
                    ) from exc
                attrs = saved.get("attributes")
                if (
                    not isinstance(attrs, list)
                    or not attrs
                    or any(not isinstance(a, str) or not a.strip() for a in attrs)
                ):
                    raise QueryUnanswerableError(
                        "invalid or empty extracted attributes"
                    )
            return routing.plan_for(
                query,
                None,
                default_path="attribute",
                default_attributes=attrs,
                default_source_type=self.source_type,
            )
        decomposition = self.decomposition_for_query(query, query_id)
        if decomposition is not None:
            try:
                routing.validate_decomposition(decomposition)
            except ValueError as exc:
                raise QueryUnanswerableError(f"invalid decomposition: {exc}") from exc
        if self.require_decomposition and (
            not isinstance(decomposition, dict) or not decomposition
        ):
            raise QueryUnanswerableError(
                "missing or invalid saved decomposition for live-routed query"
            )
        if self.require_decomposition and routing.route(decomposition) == "object":
            raise QueryUnanswerableError(
                "object-id routing is outside the benchmark embed/attribute/fusion scenarios"
            )
        return routing.plan_for(
            query,
            decomposition,
            default_path=self.search_path,
            default_attributes=self.attributes,
            default_source_type=self.source_type,
        )

    def build_argv(
        self,
        query: str,
        plan: dict[str, Any] | None = None,
        query_id: str | None = None,
    ) -> list[str]:
        """Assemble the invocation. Split out so it is unit-testable offline."""
        plan = plan or self.plan_for_query(query, query_id)
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

        # The question, alongside the arguments derived from it. Retrieval
        # ignores this; the critic reads it instead of the string the host
        # would otherwise rebuild out of `--query` and `--attribute`.
        #
        # `query` is the dataset key -- what a user typed -- while
        # `plan["query"]` is the decomposition's rewrite of it. On `object` this
        # is the difference between verifying and not: that path sends no text
        # at all, so the host finds nothing to ask about and skips the critic.
        if self.pass_original_query and query.strip():
            argv += ["--original-query", query]

        if self.candidate_limit is not None and path in ("embed", "attribute"):
            argv += ["--candidate-limit", str(self.candidate_limit)]
        if self.attribute_min_similarity is not None and path in (
            "attribute",
            "fusion",
        ):
            argv += ["--attribute-min-similarity", str(self.attribute_min_similarity)]
        argv += ["--frame-scoring", self.frame_scoring,
                 "--frame-lookup-transport", self.frame_lookup_transport]
        argv += ["--source-type", plan["source_type"]]
        returned = plan.get("top_k") or self.top_k
        if self.retrieval_pool_size is not None:
            returned = min(returned, self.retrieval_pool_size)
        argv += ["--top-k", str(returned)]
        if self.critic_enabled is False:
            argv += ["--no-critic"]
        for flag, value in (
            ("--critic-frame-budget", self.critic_frame_budget),
            ("--critic-input-width", self.critic_input_width),
            ("--critic-input-height", self.critic_input_height),
            ("--critic-diagnostic-dir", self.critic_diagnostic_dir),
        ):
            if value is not None:
                argv += [flag, str(value)]
        if self.critic_eval_count is not None:
            argv += ["--critic-eval-count", str(self.critic_eval_count)]

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

    def search(
        self, query: str, query_id: str | None = None
    ) -> tuple[list[dict[str, Any]], float]:
        plan = self.plan_for_query(query, query_id)
        if query_id is not None:
            plan["query_id"] = query_id
        plan["cli_subcommand"] = f"search run {plan['path']}"
        plan["effective_argv"] = self.build_argv(query, plan)
        self.executed_plans.append(plan)
        argv = self.build_argv(query, plan)
        start = time.time()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
            )
        except subprocess.TimeoutExpired as e:
            # A 300s hang is an environment fault, not a query that found
            # nothing. Scoring it 0 was the same silent-zero this module's exit
            # handling exists to prevent, arriving by a different route.
            raise QueryUnanswerableError(
                f"vss timed out after {self.timeout}s: {shlex.join(argv)}"
            ) from e
        latency = time.time() - start

        if is_fatal_exit(proc.returncode):
            meaning = CLI_EXIT_MEANINGS.get(proc.returncode, "undocumented exit code")
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise CliExitError(
                f"vss exited {proc.returncode} ({meaning}): {detail}\n  command: {shlex.join(argv)}",
                returncode=proc.returncode,
            )

        metadata: dict[str, Any] = {}
        hits, _messages, timings = parse_cli_output(proc.stdout, metadata=metadata)
        if metadata:
            self.metadata_by_query[query_id or query] = metadata
        if timings:
            self.timings_by_query[query_id or query] = timings
        diagnostics = metadata.get("frame_refinement") or []
        expected = self.frame_profiles.get(plan["source_type"])
        failure = any(row.get("status") == "failed" for row in diagnostics)
        attempted = any(row.get("kind") == "lookup" and row.get("status") in
                        {"succeeded", "no_match", "failed"} for row in diagnostics)
        capabilities = [row for row in diagnostics if row.get("kind") == "capabilities"]
        if attempted and (expected is None or not capabilities):
            failure = True
            metadata["frame_profile_mismatch"] = {"reason": "attempted refinement without verified execution identity"}
        for row in diagnostics:
            if row.get("kind") == "capabilities" and expected is not None:
                actual = row.get("profile", row)
                if actual.get("fingerprint") != expected.get("fingerprint"):
                    failure = True
                    metadata["frame_profile_mismatch"] = {"expected": expected, "actual": actual}
        if failure:
            # Evidence survives the exception and is copied into the query record.
            metadata["candidate_pool"] = hits
            self.metadata_by_query[query_id or query] = metadata
            raise QueryUnanswerableError("Frame refinement failed or execution profile changed; candidate pool retained in evidence")
        return hits, latency


def parse_cli_output(
    stdout: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
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
        raise QueryUnanswerableError(
            "CLI produced empty stdout; no search result envelope was returned"
        )

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
        raise QueryUnanswerableError(f"CLI output was not JSON: {text[:200]}")

    for payload in documents:
        if isinstance(payload, list):
            return _validate_hits(payload), [], {}
        if not isinstance(payload, dict):
            continue
        # Job lifecycle events carry no results; keep looking.
        if "event" in payload and "data" not in payload:
            continue
        if "data" in payload:
            if metadata is not None and "candidate_coverage" in payload:
                metadata["candidate_coverage"] = payload["candidate_coverage"]
            if "frame_refinement" in payload and payload["frame_refinement"] is not None:
                diagnostics = payload["frame_refinement"]
                if not isinstance(diagnostics, list) or any(not isinstance(row, dict) for row in diagnostics):
                    raise QueryUnanswerableError("CLI frame_refinement must be a list of objects")
                if metadata is not None:
                    metadata["frame_refinement"] = diagnostics
            hits = _validate_hits(payload["data"])
            messages = payload.get("search_messages", [])
            if not isinstance(messages, list) or any(
                not isinstance(m, str) for m in messages
            ):
                raise QueryUnanswerableError(
                    "CLI search_messages must be a list of strings"
                )
            for message in messages:
                print(f"  [search_message] {message}")
            # Present only on deployments carrying the search_core timings
            # change; absent means nobody collected, not zero time.
            timings = payload.get("timings") or {}
            if not isinstance(timings, dict):
                raise QueryUnanswerableError("CLI timings must be an object")
            return hits, messages, timings

    # Documents parsed, but none held a payload. A search that matched nothing
    # still emits `"data": []`; no `data` key at all means the CLI answered in a
    # shape this parser does not know -- a version skew, not zero recall.
    raise QueryUnanswerableError(f"CLI output had no 'data' field: {text[:200]}")


def _validate_hits(value):
    if not isinstance(value, list) or any(not isinstance(hit, dict) for hit in value):
        raise QueryUnanswerableError(
            "CLI data must be a list of search result objects; only [] is an empty ranking"
        )
    return value

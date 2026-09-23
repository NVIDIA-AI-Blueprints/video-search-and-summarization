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

"""Pluggable ingest and query backends for the search eval.

``run_eval.py`` calls two endpoints directly: ``PUT /api/v1/videos-for-search``
to ingest and ``POST /api/v1/search`` to query. Both are moving, and they are
moving *independently*, so each gets its own axis here rather than the eval
being forked per flow.

    ingest:  legacy-put | agent-3step | vst-direct   (GAP-1 closed)
    query:   cli                             (openclaw    -> GAP-3)

Metric code never sees a wire format -- it sees the canonical dict from
:func:`normalize_result`. Adding a backend means one class plus one registry
entry; no metric code changes.

Each backend documents the deployment behaviour it was written against.
"""

from __future__ import annotations

from .base import (
    COMPLETE_TIMEOUT,
    CONTENT_TYPES,
    DEFAULT_UPLOAD_TIMESTAMP,
    DEFAULT_VSS_ORIGIN_PORT,
    REPO_ROOT,
    SEARCH_TIMEOUT,
    SUBMODULE_ROOT,
    UPLOAD_TIMEOUT,
    UPLOAD_URL_TIMEOUT,
    VST_LIST_TIMEOUT,
    IngestBackend,
    QueryBackend,
    cli_supports_flag,
    configured_base_url,
    default_vss_cmd,
    ensure_vss_configured,
    has_cli_package,
    preflight_vss_cmd,
    resolve_vss_cmd,
    vss_origin_for,
)
from .dataset import (
    DATASET_META,
    DATASETS,
    DEFAULT_DATA_DIR,
    DSS_DATASET_NAME,
    DatasetMeta,
    aggregate_upload_stats,
    dataset_meta,
    download_from_dss,
    llm_url_for,
    load_dataset_file,
    print_upload_summary,
    sidecar_decompositions_for,
    vst_url_for,
)
from .decompose import DecompositionError, LiveDecomposer
from .decompose import load_prompt as load_decomposition_prompt
from .ingest import (
    COMPLETE_ALREADY_REGISTERED,
    COMPLETE_FATAL,
    COMPLETE_RETRY,
    AgentThreeStepIngest,
    LegacyPutIngest,
    VstDirectIngest,
    classify_complete_failure,
)
from .metrics import (
    HIT_K_VALUES,
    SEGMENT_SIZE,
    align_ts_to_segment,
    evaluate_clip_query,
    evaluate_query,
    format_inline,
    match_clip,
    match_segment,
    parse_ts,
    post_process_api_results,
    video_name_matches,
)
from .normalize import (
    VERIFICATION_ABSENT,
    filter_rejected,
    for_scoring,
    has_verification,
    normalize_result,
    normalize_results,
    verdict_counts,
    verification_sources,
)
from .query import (
    CLI_EXIT_MEANINGS,
    CLI_FATAL_EXITS,
    SEARCH_PATHS,
    CliExitError,
    CliQueryBackend,
    QueryUnanswerableError,
    is_fatal_exit,
    parse_cli_output,
)
from .readiness import (
    compare_inventory,
    inventory_snapshot,
    is_registered,
    list_sensor_names,
    list_sensor_streams,
    name_variants,
    sensor_list_url,
    wait_for_sources,
)
from .routing import (
    ATTRIBUTE,
    EMBED,
    FUSION,
    OBJECT,
    load_decompositions,
    path_distribution,
    plan_for,
    route,
    unpack_dataset,
)

#: Ingest backends by ``--ingest-flow`` name.
#:
#: "vst-direct" is what the UI does -- see ci-vss-oss commit 0bdfc8d (the eval's
#: previous home), which skipped six UI E2E specs because "UI video upload /
#: RTSP add no longer call Agent ingest APIs". It is NOT the default, because
#: the agent's ``/complete`` is the only step that returns a chunk count and
#: the only step that pins the timestamp anchor the dataset's ground truth is
#: written against; see :class:`VstDirectIngest`.
INGEST_BACKENDS = {
    LegacyPutIngest.name: LegacyPutIngest,
    AgentThreeStepIngest.name: AgentThreeStepIngest,
    VstDirectIngest.name: VstDirectIngest,
}

#: Query backends by ``--query-flow`` name.
#:
#: rest-api (POST /api/v1/search) was removed once the CLI became the path the
#: product actually uses. Baselines captured through it are kept under
#: eval/results/search_eval/baselines/ -- re-running them needs run_eval.py,
#: which still queries that endpoint.
#:
#: "openclaw" is absent pending GAP-3. It would drive the full new UI flow --
#: chat -> OpenClaw agent -> vss-search-archive skill -> vss search run -- and
#: so measure ROUTING quality: whether the agent picks the right path and
#: attributes. Everything here measures RETRIEVAL quality instead, with the
#: routing supplied. Deliberately not named "agent": `rest-api --agent-mode`
#: already exercises the NAT agent's decomposition, which is a different
#: decision-maker. It needs a NemoClaw sandbox, an LLM, and a decision about
#: whether CI takes an LLM dependency.
QUERY_BACKENDS = {
    CliQueryBackend.name: CliQueryBackend,
}

__all__ = [
    "ATTRIBUTE",
    "CLI_EXIT_MEANINGS",
    "CLI_FATAL_EXITS",
    "COMPLETE_ALREADY_REGISTERED",
    "COMPLETE_FATAL",
    "COMPLETE_RETRY",
    "COMPLETE_TIMEOUT",
    "CONTENT_TYPES",
    "DATASETS",
    "DATASET_META",
    "DEFAULT_DATA_DIR",
    "DEFAULT_UPLOAD_TIMESTAMP",
    "DEFAULT_VSS_ORIGIN_PORT",
    "DSS_DATASET_NAME",
    "EMBED",
    "FUSION",
    "HIT_K_VALUES",
    "INGEST_BACKENDS",
    "OBJECT",
    "QUERY_BACKENDS",
    "REPO_ROOT",
    "SEARCH_PATHS",
    "SEARCH_TIMEOUT",
    "SEGMENT_SIZE",
    "SUBMODULE_ROOT",
    "UPLOAD_TIMEOUT",
    "UPLOAD_URL_TIMEOUT",
    "VERIFICATION_ABSENT",
    "VST_LIST_TIMEOUT",
    "AgentThreeStepIngest",
    "CliExitError",
    "CliQueryBackend",
    "DatasetMeta",
    "DecompositionError",
    "IngestBackend",
    "LegacyPutIngest",
    "LiveDecomposer",
    "QueryBackend",
    "QueryUnanswerableError",
    "VstDirectIngest",
    "aggregate_upload_stats",
    "align_ts_to_segment",
    "classify_complete_failure",
    "cli_supports_flag",
    "compare_inventory",
    "configured_base_url",
    "dataset_meta",
    "default_vss_cmd",
    "download_from_dss",
    "ensure_vss_configured",
    "evaluate_clip_query",
    "evaluate_query",
    "filter_rejected",
    "for_scoring",
    "format_inline",
    "has_cli_package",
    "has_verification",
    "inventory_snapshot",
    "is_fatal_exit",
    "is_registered",
    "list_sensor_names",
    "list_sensor_streams",
    "llm_url_for",
    "load_dataset_file",
    "load_decomposition_prompt",
    "load_decompositions",
    "match_clip",
    "match_segment",
    "name_variants",
    "normalize_result",
    "normalize_results",
    "parse_cli_output",
    "parse_ts",
    "path_distribution",
    "plan_for",
    "post_process_api_results",
    "preflight_vss_cmd",
    "print_upload_summary",
    "resolve_vss_cmd",
    "route",
    "sensor_list_url",
    "sidecar_decompositions_for",
    "unpack_dataset",
    "verdict_counts",
    "verification_sources",
    "video_name_matches",
    "vss_origin_for",
    "vst_url_for",
    "wait_for_sources",
]

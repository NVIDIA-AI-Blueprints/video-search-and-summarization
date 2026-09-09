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

    ingest:  legacy-put | agent-3step        (vst-direct  -> GAP-1)
    query:   cli                             (openclaw    -> GAP-3)

Metric code never sees a wire format -- it sees the canonical dict from
:func:`normalize_result`. Adding a backend means one class plus one registry
entry; no metric code changes.

Each backend documents the deployment behaviour it was written against.
"""

from __future__ import annotations

from .base import COMPLETE_TIMEOUT
from .base import CONTENT_TYPES
from .base import DEFAULT_UPLOAD_TIMESTAMP
from .base import DEFAULT_VSS_ORIGIN_PORT
from .base import REPO_ROOT
from .base import SEARCH_TIMEOUT
from .base import SUBMODULE_ROOT
from .base import UPLOAD_TIMEOUT
from .base import UPLOAD_URL_TIMEOUT
from .base import VST_LIST_TIMEOUT
from .base import IngestBackend
from .base import QueryBackend
from .base import cli_supports_flag
from .base import configured_base_url
from .base import default_vss_cmd
from .base import ensure_vss_configured
from .base import has_cli_package
from .base import preflight_vss_cmd
from .base import resolve_vss_cmd
from .base import vss_origin_for
from .dataset import DATASETS
from .dataset import DEFAULT_DATA_DIR
from .dataset import DSS_DATASET_NAME
from .dataset import aggregate_upload_stats
from .dataset import download_from_dss
from .dataset import llm_url_for
from .dataset import load_dataset_file
from .dataset import print_upload_summary
from .dataset import sidecar_decompositions_for
from .dataset import vst_url_for
from .decompose import DecompositionError
from .decompose import LiveDecomposer
from .decompose import load_prompt as load_decomposition_prompt
from .ingest import COMPLETE_ALREADY_REGISTERED
from .ingest import COMPLETE_FATAL
from .ingest import COMPLETE_RETRY
from .ingest import AgentThreeStepIngest
from .ingest import LegacyPutIngest
from .ingest import VstDirectIngest
from .ingest import classify_complete_failure
from .metrics import HIT_K_VALUES
from .metrics import SEGMENT_SIZE
from .metrics import align_ts_to_segment
from .metrics import evaluate_query
from .metrics import format_inline
from .metrics import match_segment
from .metrics import parse_ts
from .metrics import post_process_api_results
from .metrics import video_name_matches
from .normalize import VERIFICATION_ABSENT
from .normalize import filter_rejected
from .normalize import for_scoring
from .normalize import has_verification
from .normalize import normalize_result
from .normalize import normalize_results
from .normalize import verdict_counts
from .normalize import verification_sources
from .query import CLI_EXIT_MEANINGS
from .query import CLI_FATAL_EXITS
from .query import SEARCH_PATHS
from .query import CliExitError
from .query import CliQueryBackend
from .query import is_fatal_exit
from .query import parse_cli_output
from .readiness import compare_inventory
from .readiness import inventory_snapshot
from .readiness import is_registered
from .readiness import list_sensor_names
from .readiness import list_sensor_streams
from .readiness import name_variants
from .readiness import sensor_list_url
from .readiness import wait_for_sources
from .routing import ATTRIBUTE
from .routing import EMBED
from .routing import FUSION
from .routing import OBJECT
from .routing import load_decompositions
from .routing import path_distribution
from .routing import plan_for
from .routing import route
from .routing import unpack_dataset

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
    "DecompositionError",
    "IngestBackend",
    "LegacyPutIngest",
    "LiveDecomposer",
    "QueryBackend",
    "VstDirectIngest",
    "aggregate_upload_stats",
    "align_ts_to_segment",
    "classify_complete_failure",
    "cli_supports_flag",
    "compare_inventory",
    "configured_base_url",
    "default_vss_cmd",
    "download_from_dss",
    "ensure_vss_configured",
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

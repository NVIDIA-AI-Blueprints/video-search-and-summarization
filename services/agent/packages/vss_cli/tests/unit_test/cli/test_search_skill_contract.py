# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-component contract checks for the archive-search skill and CLI."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

import pytest

from vss_cli.search import _parse_args

REPOSITORY_ROOT = Path(__file__).resolve().parents[7]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "vss-search-archive"
ADAPTER_PATH = REPOSITORY_ROOT / ".github" / "skill-eval" / "adapters" / "vss-search-archive" / "generate.py"
GENERIC_JUDGE_PATH = REPOSITORY_ROOT / ".github" / "skill-eval" / "verifiers" / "generic_judge.py"
REMOVED_FLAGS = (
    "--use-critic",
    "--no-use-critic",
    "--vlm-media-mode",
    "--vst-clip-enable-audio",
    "--search-max-iterations",
)


def test_skill_and_eval_do_not_require_removed_cli_contract() -> None:
    visible_contract_files = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.md"))]
    contract_files = list(visible_contract_files)
    contract_files.extend(sorted((SKILL_ROOT / "evals").glob("*.json")))

    for path in contract_files:
        text = path.read_text(encoding="utf-8")
        for flag in REMOVED_FLAGS:
            assert flag not in text, f"{path.relative_to(REPOSITORY_ROOT)} still requires removed flag {flag}"

    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "critic frame fetches" not in skill_text
    assert "Critic needs authentication" not in skill_text
    assert "${AGENT_URL}/generate" in skill_text
    assert "${VSS_PUBLIC_URL%/}/generate" in skill_text
    assert "VSS_PUBLIC_URL" in skill_text
    assert "VSS_VIOS_URL" in skill_text
    assert "never starts a `kubectl port-forward`" in skill_text
    assert "${AGENT_URL}/health" in skill_text
    assert "${AGENT_URL}/docs" not in skill_text
    assert "vss search run --help" in skill_text
    assert "The public Agent endpoint is the supported search interface" in skill_text
    assert 'UPLOAD_FILENAME="${UPLOAD_FILENAME:-${SOURCE_FILENAME}}"' in skill_text
    assert "use that same value" in skill_text
    assert 'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"' in skill_text
    assert '"${VSS_REPO_ROOT}/services/agent/pyproject.toml"' in skill_text
    assert "set VSS_REPO_ROOT explicitly" in skill_text
    assert "uv run --project services/agent" not in skill_text
    assert "shared VST/RTVI service defaults" in skill_text
    assert "`.env` followed by `generated.env`" in skill_text
    assert "do not construct a Brev hostname" in skill_text
    assert "## Video Search Results" in skill_text
    assert "## Verification Step" in skill_text
    assert "Never paste raw JSON wrappers" in skill_text
    assert '"streamId: ${STREAM_ID}"' not in skill_text
    assert "without adding routing headers" in skill_text
    assert 'curl -sfS --connect-timeout 10 --max-time 300 -X POST "${UPLOAD_URL}"' in skill_text
    assert '--max-time 300 -X POST "${AGENT_URL}/api/v1/videos/${SENSOR}/complete"' in skill_text
    assert "mandatory for every file ingestion" in skill_text
    assert "Never call the deprecated single-step" in skill_text
    assert "Do not select or invoke it" in skill_text
    assert "same, unmodified" in skill_text
    assert "SCREENSHOT_URL` must come only from the CLI hit" in skill_text
    assert "ACTUAL_ORIGIN" in skill_text
    assert "Do not assume" in skill_text
    assert "is exported in the operation shell" in skill_text
    assert "discover_docker" in skill_text
    assert "discover_kubernetes" not in skill_text
    assert "SEARCH_COMMAND=(" in skill_text
    assert '--deployment docker --profile "${PROFILE}"' in skill_text
    assert 'SEARCH_JSON=$("${SEARCH_COMMAND[@]}")' in skill_text
    assert "SEARCH_JSON=$(curl -sfS --connect-timeout 10 --max-time 3600" in skill_text
    assert '-X POST "${AGENT_URL}/generate"' in skill_text
    assert "host CLI's Kubernetes deployment selector" in skill_text
    assert 'jq -e \'type == "object"' in skill_text
    assert "Do **not** run the Docker CLI" in skill_text
    assert "Require a nonempty `SEARCH_TEXT`" in skill_text
    assert "Do not call `jq` on `.data`" in skill_text
    assert "present `SEARCH_TEXT` under `## Video Search Results`" in skill_text
    assert 'if [ "${HIT_COUNT}" -gt 0 ]; then' in skill_text
    assert "A zero-length `data` array has zero media URLs to validate" in skill_text
    assert '"${VALIDATED_COUNT}" -eq "${HIT_COUNT}"' in skill_text
    assert 'url.scheme != "https"' in skill_text
    assert "non-global media origin is forbidden" in skill_text
    assert "VERIFY_PIXELS" in skill_text
    assert "mktemp -d /tmp/vss-search-verification" in skill_text
    assert "inspect every saved file" in skill_text
    assert "RUNTIME_JSON=$(" in skill_text
    assert "RuntimeSnapshot.from_config_file" in skill_text
    assert '"video_embed_index":r.video_embed_index' in skill_text
    assert '"behavior_index":r.behavior_index' in skill_text
    assert '"raw_index":r.frames_index' in skill_text
    assert "Do not reuse" in skill_text
    assert "ELASTIC_SEARCH_INDEX` for behavior or raw-data checks" in skill_text
    assert "`EMBED_INDEX`, `sensor.id.keyword`, resolved VST sensor UUID" in skill_text
    assert "`BEHAVIOR_INDEX`, `sensor.id.keyword`, canonical source name" in skill_text
    assert "`RAW_INDEX`, `sensorId.keyword`, canonical source name" in skill_text
    decomposition_text = (SKILL_ROOT / "references/query_decomposition.md").read_text(encoding="utf-8")
    cli_usage_text = (SKILL_ROOT / "references/cli_usage.md").read_text(encoding="utf-8")
    deployment_resolution_text = (SKILL_ROOT / "references/deployment_resolution.md").read_text(encoding="utf-8")
    assert "deployment_resolution.md" in skill_text
    assert "VST_EXTERNAL_URL" in deployment_resolution_text
    assert "VST_API_BASE" in deployment_resolution_text
    assert "openapi.json" in deployment_resolution_text
    assert "VSS_STREAMER_URL" in deployment_resolution_text
    assert '"video_sources": ["warehouse-ladder"]' in decomposition_text
    assert '"video_sources": ["sample-warehouse-ladder"]' not in decomposition_text
    assert "names only the video embedding index" in cli_usage_text
    assert re.search(r"must not be\s+reused as the behavior or raw index", cli_usage_text)


def test_eval_is_valid_json() -> None:
    for path in sorted((SKILL_ROOT / "evals").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))


def test_harbor_eval_matches_the_retrieval_cli_contract() -> None:
    spec = json.loads((SKILL_ROOT / "evals/search.json").read_text(encoding="utf-8"))
    expects = spec["expects"]
    assert len(expects) == 7
    assert spec["profile"] == "search"
    assert spec["deploy_mode"] == "remote-all"
    assert [len(expect["checks"]) for expect in expects] == [7, 3, 6, 7, 5, 5, 4]

    contract = json.dumps(spec)
    operation_contract = json.dumps(expects[1:])
    assert ".brevlab.com" not in contract
    assert "VST_EXTERNAL_URL" in contract
    assert "BREV_ENV_ID" in expects[0]["query"]
    assert "BREV_LINK_DOMAIN" in expects[0]["query"]
    assert "do not construct a secure-link hostname" in expects[0]["query"]
    assert "neither localhost nor an internal IP" in expects[0]["checks"][0]
    assert "bounded GET" in expects[0]["checks"][0]
    assert expects[0]["query"].index("bounded GET") < expects[0]["query"].index("download and extract")
    assert "if the first probe is ready, continue immediately" in expects[0]["query"]
    assert "http://localhost:8000/health" in expects[0]["checks"][0]
    assert "vss search run --help" in expects[0]["checks"][0]
    assert "${VSS_REPO_ROOT}/services/agent" in contract
    assert "uv run --project services/agent" not in contract
    assert "http://localhost:8000/docs" not in contract
    assert "polled `GET http://localhost:30888/vst/api/v1/sensor/list`" not in contract
    assert "http://localhost:30888" not in operation_contract
    assert "http://localhost:8000/api/v1/videos" not in operation_contract

    for step_index in (2, 3, 4):
        assert "up to three" in expects[step_index]["query"].lower()
        checks = " ".join(expects[step_index]["checks"])
        assert "vss search run" in checks
        assert "`--output json`" in checks
        assert "`--raw`" in checks
        assert "resolved source" in checks
        assert "top-k 3" in checks or "top_k` 3" in checks

    assert "--search-mode embed" in " ".join(expects[2]["checks"])
    assert "explicitly with `--query` rather than as a positional argument" in " ".join(expects[2]["checks"])
    assert "completed successfully" in " ".join(expects[2]["checks"])
    fusion_checks = " ".join(expects[3]["checks"])
    assert "search_mode` `fusion`" in fusion_checks
    assert "white jacket" in fusion_checks
    assert "VST_EXTERNAL_URL" in fusion_checks
    assert "without adding a `streamId` routing header" in " ".join(expects[2]["checks"])
    assert "without adding a `streamId` routing header" in fusion_checks
    assert "http://localhost:30888" not in json.dumps(expects[0]["checks"])
    # Setup checks[1]/2] are the RT-VLM readiness gates added for search-profile
    # remote-all; RUNTIME_JSON.vst_url lives at checks[4] after that insertion.
    assert "vss-rtvi-vlm" in expects[0]["checks"][1]
    assert "RT-VLM `/v1/models`" in expects[0]["checks"][2]
    assert "RUNTIME_JSON.vst_url" in expects[0]["checks"][4]

    judge_prompt = GENERIC_JUDGE_PATH.read_text(encoding="utf-8")
    assert "assistant tool calls only" in judge_prompt
    assert "whole-file grep therefore produces false failures" in judge_prompt

    setup_checks = " ".join(expects[0]["checks"])
    assert "authoritative `RUNTIME_JSON` resolver" in setup_checks
    assert "configured in `deploy/docker/developer-profiles/dev-profile-search/generated.env`" not in setup_checks
    assert "`warehouse-ladder`" in setup_checks
    assert "canonical upload filename `warehouse-ladder.mp4`" in setup_checks
    assert "never invoked the deprecated single-step" in setup_checks
    assert "same canonical source" in setup_checks
    assert "vss-rtvi-vlm" in setup_checks
    assert "RT-VLM `/v1/models`" in setup_checks
    setup_query = expects[0]["query"]
    assert "RUNTIME_JSON" in setup_query
    assert "RuntimeSnapshot.from_config_file" in setup_query
    assert "never use `ELASTIC_SEARCH_INDEX` for behavior or raw queries" in setup_query
    assert "Make setup idempotent" in setup_query
    assert "exact or deduplicated remnants" in setup_query
    assert "agent-backed three-step workflow" in setup_query
    assert "`POST /api/v1/videos`" in setup_query
    assert "`POST /api/v1/videos/{sensor}/complete`" in setup_query
    assert "Never invoke the deprecated single-step" in setup_query
    assert "mdx-embed-filtered-2025-01-01" in setup_checks
    assert "mdx-behavior-2025-01-01" in setup_checks
    assert "mdx-raw-2025-01-01" in setup_checks
    assert "a count from `mdx-embed-filtered-2025-01-01` did not satisfy" in setup_checks

    assert "warehouse-ladder" in expects[3]["query"]
    assert "sample-warehouse-ladder" not in expects[3]["query"]
    assert "`warehouse_sample`" in expects[2]["query"]
    assert "`warehouse_sample`" in expects[4]["query"]
    for step_index in (2, 4):
        assert "`--search-mode embed`" in expects[step_index]["query"]
        assert "`--video-source warehouse_sample`" in expects[step_index]["query"]
        assert "`--top-k 3`" in expects[step_index]["query"]
    assert "VERIFY_PIXELS=true" in expects[3]["query"]
    assert "`--search-mode fusion`" in expects[3]["query"]
    assert '--attribute "white jacket"' in expects[3]["query"]
    assert "`--video-source warehouse-ladder`" in expects[3]["query"]
    assert "`--top-k 3`" in expects[3]["query"]

    negative_checks = " ".join(expects[4]["checks"])
    assert "output returned zero hits" not in negative_checks
    assert "assuming an absent object must yield zero" in negative_checks

    deletion_checks = " ".join(expects[5]["checks"])
    assert "authoritative `RUNTIME_JSON` resolver" in deletion_checks
    assert "required status `success`" in deletion_checks
    assert "sensor.id.keyword" in deletion_checks
    assert "sensorId.keyword" in deletion_checks
    assert "warehouse-ladder" in deletion_checks
    assert "sample-warehouse-ladder" not in deletion_checks
    assert "RUNTIME_JSON" in deletion_checks
    assert "mdx-embed-filtered-2025-01-01" in deletion_checks
    assert "mdx-behavior-2025-01-01" in deletion_checks
    assert "mdx-raw-2025-01-01" in deletion_checks

    kubernetes_contract = expects[6]
    assert kubernetes_contract["scenario"] == "kubernetes-ingress-contract"
    assert "VSS_PUBLIC_URL=https://vss-search.example.com" in kubernetes_contract["query"]
    kubernetes_checks = " ".join(kubernetes_contract["checks"])
    assert "/vst/api/v1/sensor/list" in kubernetes_checks
    assert "POST https://vss-search.example.com/generate" in kubernetes_checks
    assert "input_message" in kubernetes_checks
    assert "port-forward" in kubernetes_checks
    assert "direct `:9200`" in kubernetes_checks


def test_documented_run_flags_are_accepted_by_run_parser() -> None:
    args = _parse_args(
        [
            "--deployment",
            "docker",
            "--profile",
            "search",
            "--query",
            "person in a white jacket climbing a ladder",
            "--attribute",
            "white jacket",
            "--search-mode",
            "fusion",
            "--video-source",
            "warehouse-ladder",
            "--top-k",
            "10",
            "--output",
            "json",
            "--raw",
        ],
        operation="run",
    )
    assert args.query
    assert args.attributes == ["white jacket"]
    assert args.search_mode == "fusion"
    assert args.output == "json"
    assert args.raw is True


def _load_adapter():
    module_spec = importlib.util.spec_from_file_location("vss_search_archive_adapter", ADAPTER_PATH)
    assert module_spec is not None and module_spec.loader is not None
    adapter = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(adapter)
    return adapter


def test_harbor_adapter_renders_each_step_and_propagates_verifier_failure(tmp_path: Path) -> None:
    adapter = _load_adapter()
    spec = {
        "_source_path": "search.json",
        "skills": ["vss-search-archive", "vss-deploy-profile"],
        "profile": "search",
        "deploy_mode": "remote-all",
        "resources": {"platforms": {"L40S": {"gpu_count": 1}}},
        "expects": [
            {"query": "setup {{platform}} {{profile}}", "checks": ["setup {{platform}}"]},
            {"query": "search {{platform}} {{profile}}", "checks": ["search {{profile}}"]},
        ],
    }

    adapter.generate_task("L40S", "search", spec, tmp_path, SKILL_ROOT, None, None)

    first = tmp_path / "search" / "l40s" / "step-1"
    second = tmp_path / "search" / "l40s" / "step-2"
    first_instruction = (first / "instruction.md").read_text()
    assert first_instruction.startswith(adapter.PREAMBLE)
    assert "http://localhost:8000/health" in first_instruction
    assert "vss search run --help" in first_instruction
    assert "BREV_ENV_ID" in first_instruction
    assert "BREV_LINK_DOMAIN" in first_instruction
    assert "never construct a Brev hostname" in first_instruction
    assert "fully expanded `VST_EXTERNAL_URL`" in first_instruction
    assert "bounded GET" in first_instruction
    assert first_instruction.index("bounded GET") < first_instruction.index("downloading")
    assert 'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"' in first_instruction
    assert "${VSS_REPO_ROOT}/services/agent/pyproject.toml" in first_instruction
    assert 'uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev' in first_instruction
    assert "http://localhost:8000/docs" not in first_instruction
    assert "single `RUNTIME_JSON` resolver" in first_instruction
    assert "`ELASTIC_SEARCH_INDEX` is only the embedding index" in first_instruction
    assert "make setup idempotent on reused hosts" in first_instruction
    assert "exact readiness tuples independently" in first_instruction
    assert "mandatory three-step file ingestion flow exactly" in first_instruction
    assert "POST the filename to `${AGENT_URL}/api/v1/videos`" in first_instruction
    assert "`${AGENT_URL}/api/v1/videos/${SENSOR}/complete`" in first_instruction
    assert "Never call the deprecated single-step `PUT /api/v1/videos-for-search/{filename}`" in first_instruction
    second_instruction = (second / "instruction.md").read_text()
    assert second_instruction.startswith(adapter.PREAMBLE)
    assert "Do not redeploy" in second_instruction
    assert "search L40S search" in second_instruction
    assert "discovered VST/VIOS connectivity" in second_instruction
    assert "concrete value with `--search-mode`" in second_instruction
    assert "with `--query` (never as a positional argument)" in second_instruction
    assert "--search-mode <mode>" not in second_instruction
    assert "--output json --raw" in second_instruction
    assert "never paste raw JSON" in second_instruction
    assert "## Video Search Results" in second_instruction
    assert "## Verification Step" in second_instruction
    assert "without adding a VST `streamId` routing header" in second_instruction
    assert "same unmodified returned URL" in second_instruction
    assert "Never substitute `VST_EXTERNAL_URL`, localhost" in second_instruction
    assert "do not assume it is exported in the shell" in second_instruction
    assert "Do not look for a global executable" in second_instruction
    assert "report its error and stop" in second_instruction
    assert "${VSS_REPO_ROOT}/services/agent/pyproject.toml" in second_instruction
    assert 'uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev' in second_instruction
    assert "For a resolved search request" in second_instruction
    assert "For a deletion request, do not run search" in second_instruction
    assert "exact stdout is captured as `SEARCH_JSON`" in second_instruction
    assert 'if ! SEARCH_JSON=$("${SEARCH_COMMAND[@]}")' in second_instruction
    assert "number of successfully validated hits equals its length" in second_instruction
    assert "neon-pink negative retrieval step" in second_instruction
    assert "may legitimately return zero" in second_instruction
    assert "require both origins to use public HTTPS" in second_instruction
    assert "inspect every saved file's pixels" in second_instruction
    assert "/generate" not in second_instruction
    assert "asking the user to clarify the source" in second_instruction
    assert "explicitly request ingestion" in second_instruction
    assert 'discover_docker_host_endpoints("search")' in second_instruction
    assert "distinct embedding, behavior, and raw indexes" in second_instruction
    assert "poll the exact three index/field/value tuples to zero" in second_instruction
    rendered = json.loads((second / "tests" / "search.json").read_text())
    assert rendered["expects"][1]["checks"] == ["search search"]
    assert "{{" not in json.dumps(rendered)
    verifier = (second / "tests" / "test.sh").read_text()
    assert "set -euo pipefail" in verifier
    assert "exit 0" not in verifier
    solution = (first / "solution" / "solve.sh").read_text()
    assert "/health" in solution
    assert "/docs" not in solution
    assert "vss search run --help" in solution
    assert 'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"' in solution
    assert 'test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml"' in solution
    assert 'test -f "${PROFILE_DIR}/.env" -a -f "${PROFILE_DIR}/generated.env"' in solution
    assert 'uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev' in solution


def test_harbor_adapter_generation_is_deterministic(tmp_path: Path) -> None:
    adapter = _load_adapter()
    spec = json.loads((SKILL_ROOT / "evals/search.json").read_text(encoding="utf-8"))
    spec["_source_path"] = "search.json"
    first = tmp_path / "first"
    second = tmp_path / "second"

    adapter.generate_task("RTXPRO6000BW", "search", spec, first, SKILL_ROOT, None, None)
    adapter.generate_task("RTXPRO6000BW", "search", spec, second, SKILL_ROOT, None, None)

    first_files = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_files == second_files
    kubernetes_instruction = (first / "search" / "rtxpro6000bw" / "step-7" / "instruction.md").read_text()
    assert "read-only Kubernetes Ingress contract check" in kubernetes_instruction
    assert "public Agent /generate route" in kubernetes_instruction
    assert "Do not deploy, redeploy, execute the example commands" in kubernetes_instruction


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec.update(profile="alerts"), "spec.profile must be search"),
        (lambda spec: spec.update(deploy_mode="local"), "spec.deploy_mode must be remote-all"),
        (
            lambda spec: spec["resources"]["platforms"].update(UNKNOWN={"gpu_count": 1}),
            "unsupported platform",
        ),
        (
            lambda spec: spec["resources"]["platforms"]["RTXPRO6000BW"].update(gpu_count=0),
            "positive integer",
        ),
    ],
)
def test_harbor_adapter_rejects_invalid_specs(mutation, message: str) -> None:
    adapter = _load_adapter()
    spec = json.loads((SKILL_ROOT / "evals/search.json").read_text(encoding="utf-8"))
    mutation(spec)

    with pytest.raises(ValueError, match=message):
        adapter._validate_spec(spec)


def test_harbor_adapter_rejects_unresolved_placeholders(tmp_path: Path) -> None:
    adapter = _load_adapter()
    spec = json.loads((SKILL_ROOT / "evals/search.json").read_text(encoding="utf-8"))
    spec["_source_path"] = "search.json"
    spec["expects"][0]["query"] += " {{unknown}}"

    with pytest.raises(ValueError, match="unresolved placeholders"):
        adapter.generate_task("RTXPRO6000BW", "search", spec, tmp_path, SKILL_ROOT, None, None)


def test_harbor_adapter_accepts_optional_hints_and_default_gpu_count(tmp_path: Path) -> None:
    adapter = _load_adapter()
    spec = json.loads((SKILL_ROOT / "evals/search.json").read_text(encoding="utf-8"))
    spec["_source_path"] = "search.json"
    spec.pop("profile")
    spec.pop("deploy_mode")
    spec["resources"]["platforms"]["RTXPRO6000BW"].pop("gpu_count")

    adapter.generate_task("RTXPRO6000BW", "search", spec, tmp_path, SKILL_ROOT, None, None)

    task = tmp_path / "search" / "rtxpro6000bw" / "step-1" / "task.toml"
    assert "gpu_count = 1" in task.read_text(encoding="utf-8")

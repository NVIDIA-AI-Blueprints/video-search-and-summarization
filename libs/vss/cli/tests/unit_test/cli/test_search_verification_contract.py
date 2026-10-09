# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-component contract for the focused vss-search-archive skill.

The skill is search-only: it resolves an already registered source, chooses a
retrieval path, runs ``vss search run``, and reports the CLI evidence honestly.
Ingestion and deletion live in ``vss-manage-video-io-storage`` and appear only
as persisted Harbor setup/cleanup steps. These tests pin the compact contract
without coupling to deleted lifecycle recipes or scripted command copying.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[6]  # libs/vss/cli/tests/unit_test/cli -> repo root
SEARCH_SKILL = REPOSITORY_ROOT / "skills" / "operations" / "vss-search-archive"
ASK_VIDEO_SKILL = REPOSITORY_ROOT / "skills" / "operations" / "vss-ask-video"
SEARCH_ADAPTER = REPOSITORY_ROOT / ".github" / "skill-eval" / "adapters" / "vss-search-archive" / "generate.py"


def _stamped_version() -> str:
    """The repository's one stamped version (.github/version-convention.md).

    Skill versions are written by .github/scripts/stamp_versions.py from the
    nearest v* tag, never by hand, so a test must not pin a literal: it would
    break on every tag. The containers.env line carries the same stamp.
    """
    env = (REPOSITORY_ROOT / "deploy/docker/containers.env").read_text(encoding="utf-8")
    match = re.search(r'^VSS_VERSION="([^"$\n]+)"', env, re.MULTILINE)
    assert match, "deploy/docker/containers.env has no stamped VSS_VERSION line"
    return match.group(1)


def _load_adapter(path: Path, name: str) -> ModuleType:
    """Import an adapter so assertions run against what it actually emits, not
    against where implicit string concatenation happens to wrap in the source."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_matching(checks: list[str], needle: str) -> str:
    """Return the single check containing `needle`. Looking checks up by list
    position breaks the moment one is inserted or reordered — which is exactly
    what the spec edits these tests guard keep doing."""
    matches = [check for check in checks if needle in check]
    assert len(matches) == 1, f"expected exactly one check containing {needle!r}, got {len(matches)}"
    return matches[0]


def test_search_skill_is_a_compact_search_contract() -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(main.split())

    assert f'version: "{_stamped_version()}"' in main
    assert 'vss-requires: "search"' in main
    assert len(main.splitlines()) < 240
    # Search-only: no lifecycle recipes, no Brev/origin setup, no raw endpoints.
    assert "source_setup.md" not in main
    assert "ingest.md" not in main
    assert "delete.md" not in main
    assert "select_brev_origin" not in main
    assert "VST_EXTERNAL_URL" not in main
    assert "VSS_ORIGIN" not in main
    assert "Natural-language Agent responses" not in main
    assert "curl " not in main
    # Default critic + the all-unverified gate are the contract.
    assert "The CLI attempts critic verification by default" in main
    assert "every displayed result in the nonempty set is `unverified" in normalized
    assert "Never hand off a partially verified result set" in normalized

    # The recipe hard-enforces the missing-source refusal: a resolved scope that
    # came back empty must not collapse into an unrestricted search.
    assert "SOURCE_SCOPED" in main
    assert "Resolved source scope is empty; refusing an unrestricted search" in main
    assert re.search(r"^VIDEO_SOURCES=\(\)", main, re.MULTILINE) is None
    assert "declare -p VIDEO_SOURCES" in main


def test_search_skill_passes_the_exact_original_query_to_every_path() -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    cli_usage = (SEARCH_SKILL / "references" / "cli_usage.md").read_text(encoding="utf-8")

    assert ': "${ORIGINAL_QUERY:?set the exact pre-decomposition user question}"' in main
    assert '--original-query "${ORIGINAL_QUERY}"' in main
    assert 'search run "${SEARCH_PATH}"' in main
    assert "pre-decomposition user sentence" in cli_usage


def test_search_skill_captures_exit_status_separately_and_keeps_exit_6() -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    cli_usage = (SEARCH_SKILL / "references" / "cli_usage.md").read_text(encoding="utf-8")
    normalized = " ".join(main.split())

    # The old `if ! SEARCH_JSON=$(...)` hid the real exit code; the contract now
    # captures stdout and the status separately and treats exit 6 as partial.
    assert 'SEARCH_JSON=$("${SEARCH_COMMAND[@]}")' in main
    assert 'if SEARCH_JSON=$("${SEARCH_COMMAND[@]}"); then' in main
    assert "STATUS=$?" in main
    assert "if ! SEARCH_JSON=" not in main
    assert "Exit 6" in main
    assert "do not rerun" in normalized
    # cli_usage documents the partial exit so a caller can branch on it.
    assert "6 | partial" in cli_usage or "Exit 6" in cli_usage


def test_search_recipe_preserves_scope_and_partial_status_with_errexit(tmp_path: Path) -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    recipe = next(block for block in re.findall(r"```bash\n(.*?)```", main, re.DOTALL) if "SEARCH_COMMAND=" in block)
    command_log = tmp_path / "command.txt"
    setup = (
        "set -euo pipefail\n"
        'vss() { printf "%s\\n" "$@" > "$COMMAND_LOG"; printf \'{"data":[]}\\n\'; return 6; }\n'
        "SEARCH_PATH=embed SOURCE_TYPE=video_file ORIGINAL_QUERY=forklifts SOURCE_SCOPED=true\n"
    )
    scoped = subprocess.run(
        ["bash", "-c", setup + "VIDEO_SOURCES=(resolved-uuid)\n" + recipe + '\n[ "$STATUS" -eq 6 ]'],
        env={**os.environ, "COMMAND_LOG": str(command_log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert scoped.returncode == 0, scoped.stderr
    assert "--video-source\nresolved-uuid\n" in command_log.read_text(encoding="utf-8")

    command_log.unlink()
    empty = subprocess.run(
        ["bash", "-c", setup + "VIDEO_SOURCES=()\n" + recipe],
        env={**os.environ, "COMMAND_LOG": str(command_log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert empty.returncode != 0
    assert "refusing an unrestricted search" in empty.stderr
    assert not command_log.exists()


def test_path_flags_and_partial_failure_contract() -> None:
    main = " ".join((SEARCH_SKILL / "SKILL.md").read_text().split())
    reference = (SEARCH_SKILL / "references/cli_usage.md").read_text()
    assert "`--query` for embed/fusion/tag" in main
    assert "`--attribute` for attribute/fusion" in main
    assert "`--object-id` for object" in main
    assert "`--timestamp-start` / `--timestamp-end`" in main
    assert "--help` once" in main
    assert "Without `data`, report the supplied failure" in main
    assert "retry an individual stage" in main
    assert "retry only the failed stage" not in reference
    link = re.search(r"\[AGENTS.md\]\(([^)]+)\)", reference)
    assert link and (SEARCH_SKILL / "references" / link.group(1)).resolve() == REPOSITORY_ROOT / "AGENTS.md"


def test_verification_verdict_must_agree_with_expected_criteria() -> None:
    text = " ".join((SEARCH_SKILL / "references/result_verification.md").read_text().split())
    assert "Fix their exact keys before viewing evidence" in text
    assert "exactly the expected criteria keys" in text
    assert "Accept `confirmed` only when every criterion is `true`" in text
    assert "accept `rejected` only when at least one criterion is `false`" in text
    assert "confirmed response with a false criterion" in text
    assert "no missing or unexpected keys" in text
    assert "single repair allowance" in text
    assert "valid semantic `unverified`" in text


def test_raw_inventory_refresh_is_bounded_and_discloses_absence(tmp_path: Path) -> None:
    reference = (SEARCH_SKILL / "references/cli_usage.md").read_text()
    recipe = next(
        block for block in re.findall(r"```bash\n(.*?)```", reference, re.DOTALL) if "RECORDED_ORIGIN=" in block
    )
    for raw_present in (True, False):
        calls = tmp_path / ("present" if raw_present else "absent")
        setup = r"""set -euo pipefail
vss() {
  printf '%s\n' "$*" >> "$CALLS"
  if [ "$*" = 'configure --base-url https://public.example' ]; then return 0; fi
  local indices='[]'
  if [ "$(wc -l < "$CALLS")" -gt 1 ] && [ "$RAW_PRESENT" = true ]; then indices='["mdx-raw-2026"]'; fi
  printf '{"base_url":"https://public.example","services":{"elasticsearch":{"indices":%s}}}\n' "$indices"
}
"""
        result = subprocess.run(
            ["bash", "-c", setup + recipe],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CALLS": str(calls), "RAW_PRESENT": str(raw_present).lower()},
        )
        assert result.returncode == 0, result.stderr
        assert calls.read_text().splitlines() == [
            "configure show",
            "configure --base-url https://public.example",
            "configure show",
        ]
        assert ("Frame enrichment unavailable" in result.stderr) is not raw_present

    # A raw index recorded on the first read needs no refresh at all.
    calls = tmp_path / "already-recorded"
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""set -euo pipefail
vss() {
  printf '%s\n' "$*" >> "$CALLS"
  printf '{"base_url":"https://public.example","services":{"elasticsearch":{"indices":["mdx-raw-2026"]}}}\n'
}
"""
            + recipe,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CALLS": str(calls)},
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == ["configure show"]
    assert "Frame enrichment unavailable" not in result.stderr


def test_zero_candidates_may_not_be_reported_as_absence() -> None:
    """An empty result set is a fact about retrieval, not about the recording.

    Forbidding only the conclusion left the two routes to it open, and an agent
    took both: it described what the footage contained and argued the object was
    not one you would expect to find there. Name them, and say why the inference
    does not hold.
    """
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(main.split())

    assert "a fact about retrieval, not about the video" in normalized
    assert "describe what the footage contains" in normalized
    assert "argue it is not something you would expect there" in normalized
    assert "a threshold or embedding gap yields the same empty result as a genuine absence" in normalized


def test_neutrality_covers_the_final_reply_and_resolved_identifiers() -> None:
    """Neutrality scoped to progress messages let an agent obey it and still
    close by naming the model, the endpoint and the resolved sensor UUID. The
    rule has to reach the reply the user actually reads, and cover the
    identifiers the workflow resolves along the way."""
    verification = (SEARCH_SKILL / "references" / "result_verification.md").read_text(encoding="utf-8")
    normalized = " ".join(verification.split())

    assert "Keep progress and the final reply implementation-neutral" in normalized
    assert "name the source as the user did rather than by its resolved UUID" in normalized
    assert "those describe how the answer was produced, not what was seen" in normalized


def test_search_handoff_resolves_bounded_clip_for_existing_ask_video(tmp_path: Path) -> None:
    """The retained verification reference maps the synthetic interval and mints
    the clip through the CLI. The mapping is this skill's job; resolving the
    stream, minting the URL and normalising it are the CLI's. The stub therefore
    asserts the mapped bounds reach `vios clip` and returns an already-normalised
    media_url, because that is what the command guarantees its callers.
    """
    verification = (SEARCH_SKILL / "references" / "result_verification.md").read_text(encoding="utf-8")
    blocks = [
        block for block in re.findall(r"```bash\n(.*?)```", verification, flags=re.DOTALL) if "MAPPED_BOUNDS" in block
    ]
    assert len(blocks) == 1
    assert "map_interval_to_timeline" in blocks[0]
    assert "vios clip --sensor" in blocks[0]
    assert "VST_API_BASE" not in blocks[0], "clip resolution is the CLI's job now"
    # `vss vios clip` already normalises the URL onto the configured origin, so
    # the recipe needs no config read and exports nothing but VIDEO_URL.
    assert "configure show" not in blocks[0]
    assert "VSS_PUBLIC_URL" not in blocks[0]

    # A stub `vss` on PATH with a `python` beside it, laid out like an installed
    # CLI's venv bin/: the recipe finds vss_core through the CLI's own
    # interpreter, so this also exercises that lookup.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A wrapper, not a symlink: a symlinked venv python loses its venv.
    (bin_dir / "python").write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    (bin_dir / "python").chmod(0o755)
    stub = bin_dir / "vss"
    stub.write_text(
        """#!/bin/sh
case "$*" in
  'vios timeline --sensor sensor-1')
    printf '%s\n' '{"recorded":true,"segments":[{"start_time":"2026-08-01T12:00:00.000Z","end_time":"2026-08-01T12:01:00.000Z"}]}'
    ;;
  'vios clip --sensor sensor-1 --start-time 2026-08-01T12:00:00.000Z --end-time 2026-08-01T12:00:10.000Z')
    printf '%s\n' '{"media_url":"https://public.example/vst/storage/temp_files/clip.mp4?token=a"}'
    ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
"""
    )
    stub.chmod(0o755)
    script = (
        """set -euo pipefail
HIT_SENSOR_ID=sensor-1
HIT_START=2025-01-01T00:00:00Z
HIT_END=2025-01-01T00:00:10Z
"""
        + blocks[0]
        + """
test "${VIDEO_URL}" = 'https://public.example/vst/storage/temp_files/clip.mp4?token=a'
"""
    )
    subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )


def test_ask_video_routes_vss_questions_through_cli_memory_and_vlm() -> None:
    ask_video = (ASK_VIDEO_SKILL / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(ask_video.split())
    evals = json.loads((ASK_VIDEO_SKILL / "evals/evals.json").read_text(encoding="utf-8"))
    harbor_evals = json.loads(
        (ASK_VIDEO_SKILL / "evals/base_profile_video_understanding.json").read_text(encoding="utf-8")
    )
    evals_by_id = {case["id"]: case for case in evals}

    assert f'version: "{_stamped_version()}"' in ask_video
    assert "user-confirmed vss-search-archive handoff with a pre-resolved bounded VIDEO_URL" in ask_video
    assert "Search agent Markdown memory using the harness-native memory search" in normalized
    assert "Markdown search is not a `vss` command" in normalized
    assert "Never send raw Markdown documents to the VSS judge" in normalized
    assert "If introspection is enabled, call `vss memory introspect`" in normalized
    assert "If introspection is disabled or unconfigured" in normalized
    assert "do not enable it or rewrite static configuration automatically" in normalized
    assert "Users and the agent may still configure and enable introspection" in normalized
    assert "Do not run `vss memory query` immediately before introspection" in normalized
    assert "Do not run `vss vlm run` after a completed or partial result" in normalized
    assert "Never pass `--record-id` alone" in normalized
    assert "Complete child identity: `--job-id`, `--record-type`, and `--record-id`" in normalized
    assert "OpenClaw may execute every tool call in a fresh shell" in normalized
    assert "Capture stdout and the exit code separately" in normalized
    assert "Only one direct VLM fallback is allowed" in normalized
    assert "Do not call an OpenAI-compatible `/chat/completions` endpoint directly" in normalized
    assert "curl " not in ask_video
    assert {
        "hot-context-sufficient",
        "markdown-sufficient",
        "markdown-pointer-introspection-enabled",
        "no-markdown-introspection-enabled",
        "introspection-disabled",
        "introspection-unconfigured",
        "exact-stored-job",
        "explicit-fresh-window",
        "introspection-partial",
        "no-memory-grounded-window",
        "no-memory-without-scope",
        "invalid-child-identity",
        "direct-file-vlm",
        "separate-shell-cli",
    } <= evals_by_id.keys()
    assert any(
        "Does not call VLM" in behavior for behavior in evals_by_id["no-memory-without-scope"]["expected_behavior"]
    )
    assert any(
        "Uses one vss vlm run" in behavior for behavior in evals_by_id["no-memory-grounded-window"]["expected_behavior"]
    )
    serialized_harbor = json.dumps(harbor_evals)
    assert "vss vlm run --file" in serialized_harbor
    assert "data:video/mp4;base64" not in serialized_harbor
    assert "call RT-VLM directly" in serialized_harbor
    assert (ASK_VIDEO_SKILL / "evals/direct_vlm_video_understanding.json").exists() is False


def test_search_harbor_eval_has_roles_and_unrevealing_search_prompts(tmp_path: Path) -> None:
    spec = json.loads((SEARCH_SKILL / "evals" / "search.json").read_text(encoding="utf-8"))
    serialized = json.dumps(spec)
    adapter = SEARCH_ADAPTER.read_text(encoding="utf-8")
    adapter_module = _load_adapter(SEARCH_ADAPTER, "search_archive_adapter")
    operation_preamble = adapter_module.OPERATION_PREAMBLE
    deployment_query = spec["expects"][0]["query"]
    ingestion_query = spec["expects"][1]["query"]
    deployment_checks = spec["expects"][0]["checks"]
    ingestion_checks = spec["expects"][1]["checks"]

    assert len(spec["expects"]) == 11  # 10 search steps + tag-keyword-search
    assert spec["expects"][0]["scenario"] == "deploy-search-profile"
    assert spec["expects"][1]["scenario"] == "ingest-search-fixtures"
    assert spec["expects"][-1]["scenario"] == "delete-search-fixture"
    assert spec["expects"][-1]["role"] == "cleanup"
    assert "vss-ask-video" in spec["skills"]
    assert "libs/vss" in serialized and "vss search run --help" in serialized
    assert "critic_result.result" in serialized
    assert "confirmed" in serialized
    assert "rejected" in serialized
    assert "unverified" in serialized
    assert "VERIFY_PIXELS" not in serialized
    assert "visually inspect screenshot pixels" in adapter
    assert "when every hit in the nonempty displayed result set remains unverified" in adapter
    assert "or prose layout is not required" in adapter
    assert "always use the exact heading `## Video Search Results`" not in adapter
    assert "timeout_sec = 600.0" in adapter

    # Roles follow the setup, setup, search..., cleanup sequence.
    roles = [expect.get("role") for expect in spec["expects"]]
    assert roles == ["setup", "setup"] + ["search"] * (len(spec["expects"]) - 3) + ["cleanup"]
    assert spec["expects"][0]["role"] == "setup"
    assert spec["expects"][1]["role"] == "setup"

    # Path-selection search steps must not hand the agent the path and flags:
    # choosing the path is what the check measures. Contract steps (k8s, rtsp)
    # are explicitly "show the commands", so naming the CLI there is the point.
    adapter_module.generate_task("RTXPRO6000BW", "search", spec, tmp_path, SEARCH_SKILL, None, None, None)
    contract_indices = {
        i
        for i, e in enumerate(spec["expects"])
        if e.get("scenario")
        in {
            "kubernetes-ingress-contract",
            "rtsp-live-stream-search-contract",
        }
    }
    for i, expect in enumerate(spec["expects"]):
        if expect.get("role") != "search" or i in contract_indices:
            continue
        q = (tmp_path / f"search/rtxpro6000bw/step-{i + 1}/instruction.md").read_text().lower()
        for path in ("embed", "attribute", "fusion", "object", "tag"):
            assert f"run {path}" not in q, f"step {i + 1} prompt reveals the path"
        assert "--video-source" not in q, f"step {i + 1} prompt reveals --video-source"
        assert "--source-type" not in q, f"step {i + 1} prompt reveals --source-type"

    # The adapter drives the CLI's typed exit status and keeps exit 6 results.
    assert "if ! SEARCH_JSON" not in adapter
    assert "exit 6" in operation_preamble.lower()
    assert "search_messages" in operation_preamble

    # Cold deployment and fixture ingestion are separate persisted steps. This
    # prevents model initialization from consuming the ingestion budget and
    # removes any incentive to repair/redeploy midway through source setup.
    assert "Do not ingest sample media in this step" in deployment_query
    assert "Download files here only for the NemoClaw fixture staging" in deployment_query
    assert "initial workflow Compose activity was allowed" in _check_matching(
        deployment_checks, "select_brev_origin.sh"
    )
    assert "already deployed, healthy, configured" in ingestion_query
    assert "invoking `/vss-build-vision-ai`" in ingestion_query
    assert "fail rather than running `docker compose`" in ingestion_query
    assert any("deadline-reset recovery loop" in check for check in ingestion_checks)

    # Current search indices use the VST sensor ID for embed/fusion source
    # scoping and the source name for attribute/object; source_type selects the
    # upload/live partition independently.
    for step in (3, 4, 5):
        step_checks = " ".join(spec["expects"][step]["checks"])
        assert "sensor ID" in step_checks
        assert "--source-type video_file" in step_checks
    skill_text = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "| `embed` | preserved `.sensor_id` |" in skill_text
    assert "| `fusion` | preserved `.sensor_id`" in skill_text

    # search_group._runtime_from sets vst_external_url to deployment.base_url, so
    # the host CLI stamps the `vss configure` origin into every screenshot_url.
    # VST_EXTERNAL_URL drives the Agent-served path only: telling the agent to
    # edit it, or to recreate services, cannot change CLI media URLs at all.
    for step in (3, 4):
        media_checks = [check for check in spec["expects"][step]["checks"] if "media URL" in check]
        assert any("origin recorded by `vss configure`" in check for check in media_checks)
        assert not any("VST_EXTERNAL_URL" in check for check in media_checks)
    assert any(
        "same scheme, host, and effective port as the origin recorded by `vss configure`" in check
        for check in spec["expects"][3]["checks"]
    )

    origin_check = _check_matching(deployment_checks, "select_brev_origin.sh")
    assert "`vss configure --base-url` with the returned origin" in origin_check
    assert "No post-deployment VST_EXTERNAL_URL edits or routing loops occurred" in origin_check
    assert "exactly once" in deployment_query and "empty string when none" in deployment_query
    assert "report media_scope and the host-local media limitation" in deployment_query
    assert "nonredirecting" in deployment_query
    assert len(spec["expects"][1]["checks"]) == 3
    assert not any("evaluation verifier" in check.lower() for check in spec["expects"][1]["checks"])

    # The search spec no longer demands a raw Elasticsearch count from the
    # runtime skill; the verifier owns the bounded read-only index checks.
    assert "/_count" not in serialized  # no raw Elasticsearch _count API path (gpu_count is unrelated)

    # The verification scenario is still a single bundled step.
    verification_steps = [
        expect for expect in spec["expects"] if expect.get("scenario") == "confirmed-search-result-verification"
    ]
    assert len(verification_steps) == 1
    assert any(
        "at most one additional request" in check and "repair malformed structured output" in check
        for check in verification_steps[0]["checks"]
    )
    assert "ask_video_skill_dir" in adapter
    assert '(ask_video_skill_dir, "vss-ask-video")' in adapter

    forklift_checks = spec["expects"][3]["checks"]
    assert any("default critic behavior for every returned forklift hit" in check for check in forklift_checks)

    # Public-link absence permits a supplied host origin, never an invented hostname.
    assert "without inventing hostnames" in serialized.lower()

    # The verification step is self-contained: it supplies an explicit bounded
    # hit so a fresh agent turn can act without prior-step display state.
    verification = next(e for e in spec["expects"] if e.get("scenario") == "confirmed-search-result-verification")
    assert "2025-01-01T00:00:00Z" in verification["query"]
    assert "2025-01-01T00:00:20Z" in verification["query"]


def test_search_routing_eval_handles_exit6_and_negative_triggers() -> None:
    cases = json.loads((SEARCH_SKILL / "evals" / "evals.json").read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}

    # Retained: a partially verified set never triggers fallback.
    partial = by_id["search-archive-partially-verified"]
    assert "does not offer or invoke" in partial["ground_truth"]
    assert any("does not invoke vss-ask-video" in behavior for behavior in partial["expected_behavior"])

    # New: an exit-6 reasoning case keeps the usable hits and discloses the
    # limitation instead of rerunning or treating partial as failure.
    exit6 = by_id["search-archive-exit6-partial"]
    assert "exit 6" in exit6["ground_truth"].lower()
    assert "persisted" in exit6["ground_truth"].lower()
    assert any("does not rerun" in behavior for behavior in exit6["expected_behavior"])
    assert any("reports" in behavior and "once" in behavior for behavior in exit6["expected_behavior"])

    no_data = by_id["search-archive-exit6-without-data"]
    assert "without data" in no_data["ground_truth"].lower()
    assert any("supplied failure" in item for item in no_data["expected_behavior"])

    # Pure ingestion and deletion do not activate the search skill.
    for neg_id in ("search-archive-ingest-only", "search-archive-delete-only"):
        case = by_id[neg_id]
        assert case.get("should_trigger") is False
        assert "search skill" in case["ground_truth"]

    combined = by_id["search-archive"]
    assert "`vss vios add`" in combined["ground_truth"]
    assert "Agent-tier guard" not in json.dumps(combined)

    # The RTSP routing case no longer requires a direct Elasticsearch count.
    rtsp = by_id["search-archive-rtsp-live-stream"]
    rtsp_text = json.dumps(rtsp)
    assert "Elasticsearch" not in rtsp_text
    assert "_count" not in rtsp_text
    assert any("does not poll a search index directly" in behavior for behavior in rtsp["expected_behavior"])


def test_origin_selector_uses_one_bounded_probe_and_declared_host_fallback(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts" / "select_brev_origin.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$@" >>"${CURL_ARGS}"
count=$(cat "${CURL_COUNT}" 2>/dev/null || printf '0')
printf '%s' "$((count + 1))" >"${CURL_COUNT}"
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
printf '%s' "${CURL_BODY}" >"${output}"
printf '%s' "${CURL_STATUS}"
exit "${CURL_EXIT:-0}"
"""
    )
    fake_curl.chmod(0o755)
    cases = [
        ("", "000", "", 0, "host-local"),
        ("https://public.example/", "000", "", 7, "host-local"),
        ("https://public.example/", "302", "<html>login</html>", 0, "host-local"),
        ("https://public.example/", "503", "{}", 0, "host-local"),
        ("https://public.example/", "200", "not-json", 0, "host-local"),
        ("https://public.example/", "200", '{"type":"vst"}', 0, "host-local"),
        ("https://public.example/", "200", '{"type":"vst","version":3}', 0, "host-local"),
        ("https://public.example/", "200", '{"type":"not-vst","version":"3.2.0"}', 0, "host-local"),
        ("https://public.example/", "200", '{"type":"vst","version":""}', 0, "host-local"),
        ("https://public.example/", "200", '{"type":"vst","version":"3.2.0"}', 0, "public"),
        ("https://public.example:8443/", "200", '{"type":"vst","version":"3.2.0"}', 0, "public"),
        ("https://[2001:db8::1]:8443/", "200", '{"type":"vst","version":"3.2.0"}', 0, "public"),
    ]
    for number, (public, status, body, transport_exit, scope) in enumerate(cases):
        count_file = tmp_path / f"count-{number}"
        args_file = tmp_path / f"args-{number}"
        completed = subprocess.run(
            [str(selector), public, "http://deployment.example:7777/"],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "CURL_BODY": body,
                "CURL_COUNT": str(count_file),
                "CURL_ARGS": str(args_file),
                "CURL_STATUS": status,
                "CURL_EXIT": str(transport_exit),
            },
        )
        assert completed.returncode == 0, (number, completed.stderr)
        expected_origin = public.removesuffix("/") if scope == "public" else "http://deployment.example:7777"
        assert json.loads(completed.stdout) == {"origin": expected_origin, "media_scope": scope}
        if not public:
            assert not count_file.exists()
            assert not args_file.exists()
            continue
        assert count_file.read_text() == "1"
        args = args_file.read_text().splitlines()
        assert args[args.index("--connect-timeout") + 1] == "5"
        assert args[args.index("--max-time") + 1] == "15"
        assert args[args.index("--max-redirs") + 1] == "0"
        assert "-L" not in args and "--location" not in args
        assert args[-1] == f"{public.removesuffix('/')}/vst/api/v1/sensor/version"


def test_origin_selector_rejects_invalid_public_origins_before_probe(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts" / "select_brev_origin.sh"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
printf called >"${CURL_CALLED}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
printf '%s' '{"type":"vst","version":"3.2.0"}' >"${output}"
printf 200
"""
    )
    fake_curl.chmod(0o755)
    for public in (
        "http://public.example",
        "ftp://public.example",
        "public.example",
        "https://",
        "https:///",
        "https://public.example/path",
        "https://public.example?query=value",
        "https://public.example#fragment",
        "https://public.example/path/",
        "https://public.example invalid",
    ):
        called = tmp_path / "curl-called"
        completed = subprocess.run(
            [str(selector), public, "http://deployment.example:7777/"],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "CURL_CALLED": str(called),
            },
        )
        assert completed.returncode == 0, (public, completed.stderr)
        assert json.loads(completed.stdout) == {
            "origin": "http://deployment.example:7777",
            "media_scope": "host-local",
        }, public
        assert completed.stderr, public
        assert not called.exists(), public


def test_origin_selector_failures_have_empty_stdout(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts" / "select_brev_origin.sh"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text('#!/bin/sh\nprintf called >"${CURL_CALLED}"\nexit 99\n')
    fake_curl.chmod(0o755)
    for args in (
        [],
        ["https://public.example"],
        ["", ""],
        ["https://public.example", ""],
        ["https://public.example", "/"],
        ["", "http://deployment.example", "extra"],
    ):
        completed = subprocess.run(
            [str(selector), *args],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "CURL_CALLED": str(tmp_path / "curl-called"),
            },
        )
        assert completed.returncode != 0, args
        assert completed.stdout == "", args
        assert completed.stderr, args
        assert not (tmp_path / "curl-called").exists(), args


def test_search_adapter_bundles_ask_video_and_emits_role_metadata(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(SEARCH_ADAPTER),
            "--output-dir",
            str(tmp_path),
            "--skill-dir",
            str(SEARCH_SKILL),
            "--deploy-skill-dir",
            str(REPOSITORY_ROOT / "skills/vss-build-vision-ai"),
            "--video-io-skill-dir",
            str(REPOSITORY_ROOT / "skills/operations/vss-manage-video-io-storage"),
            "--ask-video-skill-dir",
            str(ASK_VIDEO_SKILL),
            "--spec",
            str(SEARCH_SKILL / "evals/search.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    deployment_instruction = (tmp_path / "search/rtxpro6000bw/step-1/instruction.md").read_text(encoding="utf-8")
    ingestion_instruction = (tmp_path / "search/rtxpro6000bw/step-2/instruction.md").read_text(encoding="utf-8")
    spec = json.loads((SEARCH_SKILL / "evals/search.json").read_text(encoding="utf-8"))
    for step, instruction in ((0, deployment_instruction), (1, ingestion_instruction)):
        assert spec["expects"][step]["query"].replace("{{platform}}", "RTXPRO6000BW") in instruction
    assert "Do not ingest sample media in this step" in deployment_instruction
    assert "Download files here only for the NemoClaw fixture staging" in deployment_instruction
    assert "already deployed, healthy, configured" in ingestion_instruction
    assert "invoking `/vss-build-vision-ai`" in ingestion_instruction

    verification_step = tmp_path / "search/rtxpro6000bw/step-7"
    assert (verification_step / "skills/vss-ask-video/SKILL.md").is_file()
    instruction = (verification_step / "instruction.md").read_text(encoding="utf-8")
    assert "supplied synthetic, unverified bounded hit" in instruction

    # Deletion is the terminal cleanup step (moved from step 8 to step 11).
    cleanup_step = tmp_path / "search/rtxpro6000bw/step-11"
    assert (cleanup_step / "instruction.md").is_file()
    cleanup_instruction = (cleanup_step / "instruction.md").read_text(encoding="utf-8")
    assert "terminal evaluation cleanup" in cleanup_instruction
    assert "vss vios delete --type video --sensor <name>" in cleanup_instruction

    # Role metadata is copied into task.toml so a reporting view can score
    # search-role steps separately from setup and cleanup.
    assert 'role = "setup"' in (tmp_path / "search/rtxpro6000bw/step-1/task.toml").read_text(encoding="utf-8")
    assert 'role = "search"' in (tmp_path / "search/rtxpro6000bw/step-4/task.toml").read_text(encoding="utf-8")
    assert 'role = "cleanup"' in (tmp_path / "search/rtxpro6000bw/step-11/task.toml").read_text(encoding="utf-8")


def test_search_adapter_solve_script_matches_role(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(SEARCH_ADAPTER),
            "--output-dir",
            str(tmp_path),
            "--skill-dir",
            str(SEARCH_SKILL),
            "--deploy-skill-dir",
            str(REPOSITORY_ROOT / "skills/vss-build-vision-ai"),
            "--video-io-skill-dir",
            str(REPOSITORY_ROOT / "skills/operations/vss-manage-video-io-storage"),
            "--ask-video-skill-dir",
            str(ASK_VIDEO_SKILL),
            "--spec",
            str(SEARCH_SKILL / "evals/search.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # A search-role gold solution depends on the CLI being configured, not on
    # the VSS Agent being healthy — search reaches Elasticsearch/VST through
    # the CLI, never the Agent.
    search_solve = (tmp_path / "search/rtxpro6000bw/step-4/solution/solve.sh").read_text(encoding="utf-8")
    assert "VSS_AGENT_URL" not in search_solve
    assert "/health" not in search_solve
    assert "vss configure show" in search_solve
    assert "vss search run --help" in search_solve

    # A setup-role gold solution still depends on a live deployment.
    setup_solve = (tmp_path / "search/rtxpro6000bw/step-1/solution/solve.sh").read_text(encoding="utf-8")
    assert "/health" in setup_solve


def test_search_adapter_test_script_probes_es_for_setup_and_cleanup(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(SEARCH_ADAPTER),
            "--output-dir",
            str(tmp_path),
            "--skill-dir",
            str(SEARCH_SKILL),
            "--deploy-skill-dir",
            str(REPOSITORY_ROOT / "skills/vss-build-vision-ai"),
            "--video-io-skill-dir",
            str(REPOSITORY_ROOT / "skills/operations/vss-manage-video-io-storage"),
            "--ask-video-skill-dir",
            str(ASK_VIDEO_SKILL),
            "--spec",
            str(SEARCH_SKILL / "evals/search.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # The verifier — not the agent — owns the read-only Elasticsearch tuple
    # checks for the persisted setup (ingest) and cleanup (delete) steps, so the
    # agent is never told to poll ES. Search and contract steps delegate to the
    # generic LLM judge alone.
    ingest_test = (tmp_path / "search/rtxpro6000bw/step-2/tests/test.sh").read_text(encoding="utf-8")
    cleanup_test = (tmp_path / "search/rtxpro6000bw/step-11/tests/test.sh").read_text(encoding="utf-8")
    search_test = (tmp_path / "search/rtxpro6000bw/step-4/tests/test.sh").read_text(encoding="utf-8")
    assert "es_count" in ingest_test and "/_count" in ingest_test
    assert "es_count" in cleanup_test and "/_count" in cleanup_test
    assert "SECONDS + 900" in ingest_test
    assert "SECONDS + 600" in cleanup_test
    assert "|| printf '0'" not in ingest_test
    assert "|| printf '0'" not in cleanup_test
    for script in (ingest_test, cleanup_test, search_test):
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    # A failed backend count must stay a failure: zero is a valid cleanup
    # count, so converting transport errors to zero would falsely pass cleanup.
    count_function = ingest_test.split("es_count() {", 1)[1].split("\n}", 1)[0]
    failed_count = subprocess.run(
        [
            "bash",
            "-c",
            "set -o pipefail\nes_count() {" + count_function + "\n}\n"
            "ES_URL=http://127.0.0.1:1\nes_count idx field value",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed_count.returncode != 0
    assert not failed_count.stdout.strip()
    # Setup preserves the UUID under the current deployment's config identity;
    # cleanup uses it for the embedding tuple after VST has removed the sensor.
    assert "warehouse-ladder" in cleanup_test
    assert "state_file" in ingest_test and "state_file" in cleanup_test
    assert 'LADDER_UUID=$(cat "${STATE_FILE}")' in cleanup_test
    assert 'es_count "${EMBED_IDX}" sensor.id.keyword "${LADDER_UUID}"' in cleanup_test
    # Search-role steps never get the ES probe.
    assert "es_count" not in search_test and "/_count" not in search_test
    # Every step still falls through to the generic LLM judge.
    assert "generic_judge.py" in ingest_test
    assert "generic_judge.py" in cleanup_test
    assert "generic_judge.py" in search_test

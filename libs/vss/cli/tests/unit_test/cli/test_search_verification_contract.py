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


def _load_adapter(path: Path, name: str) -> ModuleType:
    """Import an adapter so preamble assertions run against the text the agent
    actually receives. Matching the raw source instead couples the contract to
    where the implicit string concatenation happens to wrap, so a formatting-only
    reflow that leaves the emitted instruction.md byte-identical would fail."""
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

    assert 'version: "3.4.0"' in main
    assert 'vss-requires: "search"' in main
    assert len(main.splitlines()) < 200
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
    recipe = next(
        block for block in re.findall(r"```bash\n(.*?)```", main, re.DOTALL)
        if "SEARCH_COMMAND=" in block
    )
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
VST_URL=https://public.example
HIT_SENSOR_ID=sensor-1
HIT_START=2025-01-01T00:00:00Z
HIT_END=2025-01-01T00:00:10Z
"""
        + blocks[0]
        + """
test "${VIDEO_URL}" = 'https://public.example/vst/storage/temp_files/clip.mp4?token=a'
test "${VSS_PUBLIC_URL}" = 'https://public.example'
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

    assert 'version: "3.3.0"' in ask_video
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


def test_search_harbor_eval_has_roles_and_unrevealing_search_prompts() -> None:
    spec = json.loads((SEARCH_SKILL / "evals" / "search.json").read_text(encoding="utf-8"))
    serialized = json.dumps(spec)
    adapter = SEARCH_ADAPTER.read_text(encoding="utf-8")
    adapter_module = _load_adapter(SEARCH_ADAPTER, "search_archive_adapter")
    deployment_preamble = adapter_module.DEPLOYMENT_PREAMBLE
    ingestion_preamble = adapter_module.INGESTION_PREAMBLE
    operation_preamble = adapter_module.OPERATION_PREAMBLE

    assert len(spec["expects"]) == 11  # 10 search steps + tag-keyword-search
    assert spec["expects"][0]["scenario"] == "deploy-search-profile"
    assert spec["expects"][1]["scenario"] == "ingest-search-fixtures"
    assert spec["expects"][-1]["scenario"] == "delete-search-fixture"
    assert spec["expects"][-1]["role"] == "cleanup"
    assert "vss-ask-video" in spec["skills"]
    assert "libs/vss" in serialized and "vss search run --help" in serialized
    assert "verification.result" in serialized
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
        q = expect["query"].lower()
        assert "vss search run" not in q, f"step {i + 1} prompt reveals the path"
        assert "--video-source" not in q, f"step {i + 1} prompt reveals --video-source"
        assert "--source-type" not in q, f"step {i + 1} prompt reveals --source-type"

    # The adapter drives the CLI's typed exit status and keeps exit 6 results.
    assert "if ! SEARCH_JSON" not in adapter
    assert "exit 6" in operation_preamble.lower()
    assert "search_messages" in operation_preamble

    # Cold deployment and fixture ingestion are separate persisted steps. This
    # prevents model initialization from consuming the ingestion budget and
    # removes any incentive to repair/redeploy midway through source setup.
    assert "do not download or ingest sample media" in deployment_preamble
    assert "Initial profile deployment activity is not a routing violation" in deployment_preamble
    assert "preceding step already deployed" in ingestion_preamble
    assert "do not invoke `/vss-build-vision-ai`" in ingestion_preamble
    assert "do not poll elasticsearch or build an endpoint yourself" in ingestion_preamble.lower()
    assert "the verifier refreshes configuration after observing the lazy indexes" in ingestion_preamble
    assert "evaluation verifier" in _check_matching(spec["expects"][1]["checks"], "fresh `vss configure show`")

    # The search spec no longer demands a raw Elasticsearch count from the
    # runtime skill; the verifier owns the bounded read-only index checks.
    assert "/_count" not in serialized  # no raw Elasticsearch _count API path (gpu_count is unrelated)

    # The verification scenario is still a single bundled step.
    verification_steps = [
        expect for expect in spec["expects"] if expect.get("scenario") == "confirmed-search-result-verification"
    ]
    assert len(verification_steps) == 1
    assert any(
        "at most one additional request only to repair malformed structured output" in check
        for check in verification_steps[0]["checks"]
    )
    assert "ask_video_skill_dir" in adapter
    assert '(ask_video_skill_dir, "vss-ask-video")' in adapter

    forklift_checks = spec["expects"][3]["checks"]
    assert any("one bounded critic attempt for every returned forklift hit" in check for check in forklift_checks)

    # The ban exists to stop invented hostnames, but the correction it mandates
    # builds the documented one — an unscoped prohibition contradicts it.
    assert "do not invent a hostname" in serialized

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

    # Pure ingestion and deletion do not activate the search skill. The selected
    # source workflow must respect the VIOS skill's Agent-tier guard.
    for neg_id in ("search-archive-ingest-only", "search-archive-delete-only"):
        case = by_id[neg_id]
        assert case.get("should_trigger") is False
        assert "search skill" in case["ground_truth"]

    # The RTSP routing case no longer requires a direct Elasticsearch count.
    rtsp = by_id["search-archive-rtsp-live-stream"]
    rtsp_text = json.dumps(rtsp)
    assert "Elasticsearch" not in rtsp_text
    assert "_count" not in rtsp_text
    assert any("does not poll a search index directly" in behavior for behavior in rtsp["expected_behavior"])


def test_public_probe_rejects_redirects_and_accepts_vst_json(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts" / "select_brev_origin.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
count=$(cat "${CURL_COUNT}" 2>/dev/null || printf '0')
printf '%s' "$((count + 1))" >"${CURL_COUNT}"
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
printf '%s' "${CURL_BODY}" >"${output}"
printf '%s' "${CURL_STATUS}"
"""
    )
    fake_curl.chmod(0o755)

    def run_probe(status: int, body: str, expected_origin: str) -> None:
        count_file = tmp_path / f"count-{status}"
        completed = subprocess.run(
            [str(selector), "https://public.example", "http://10.0.0.1:7777"],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "CURL_BODY": body,
                "CURL_COUNT": str(count_file),
                "CURL_STATUS": str(status),
            },
        )
        assert json.loads(completed.stdout)["origin"] == expected_origin
        assert count_file.read_text() == "1"

    run_probe(302, "<html>login</html>", "http://10.0.0.1:7777")
    run_probe(200, '{"type":"vst","version":"3.2.0"}', "https://public.example")


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
    assert "deploys and validates the search profile only" in deployment_instruction
    assert "do not download or ingest sample media" in deployment_instruction
    assert "preceding step already deployed" in ingestion_instruction
    assert "do not invoke `/vss-build-vision-ai`" in ingestion_instruction

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
        ["bash", "-c", "set -o pipefail\nes_count() {" + count_function + "\n}\n"
         "ES_URL=http://127.0.0.1:1\nes_count idx field value"],
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

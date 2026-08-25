# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-component contract for default search-result verification."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[7]
SEARCH_SKILL = REPOSITORY_ROOT / "skills" / "vss-search-archive"
ASK_VIDEO_SKILL = REPOSITORY_ROOT / "skills" / "vss-ask-video"
SEARCH_ADAPTER = REPOSITORY_ROOT / ".github/skill-eval/adapters/vss-search-archive/generate.py"


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


def test_search_skill_uses_default_critic_and_unverified_only_fallback() -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    verification = (SEARCH_SKILL / "references/result_verification.md").read_text(encoding="utf-8")
    cli_usage = (SEARCH_SKILL / "references/cli_usage.md").read_text(encoding="utf-8")
    normalized_main = " ".join(main.split())
    normalized_verification = " ".join(verification.split())

    assert len(main.splitlines()) < 500
    assert 'version: "3.3.0"' in main
    assert "The CLI attempts critic verification by default" in main
    assert 'VSS_ORIGIN=$("${VSS[@]}" configure show' in main
    assert "Do not repeat public-origin selection" in main
    assert "Would you like me to verify the unverified search results?" in main
    assert "only when every displayed result is" in normalized_main
    assert "Never hand off a partially verified result set" in normalized_main
    assert "Verification is fail-open" in cli_usage
    assert "If any hit is `confirmed` or `rejected`, do not delegate any hit" in normalized_verification
    assert "Do not require or add a search-specific mode" in verification
    assert "ordinary user-supplied `VIDEO_URL` interface" in verification
    assert "VERIFY_" not in verification
    assert "VERIFY_PIXELS" not in main


def test_search_handoff_resolves_bounded_clip_for_existing_ask_video() -> None:
    """The recipe maps the synthetic interval and mints the clip through the CLI.

    The mapping is this skill's job; resolving the stream, minting the URL and
    normalising it are the CLI's. The stub therefore asserts the mapped bounds
    reach `vios clip` and returns an already-normalised media_url, because that
    is what the command guarantees its callers.
    """
    verification = (SEARCH_SKILL / "references/result_verification.md").read_text(encoding="utf-8")
    blocks = [
        block for block in re.findall(r"```bash\n(.*?)```", verification, flags=re.DOTALL) if "MAPPED_BOUNDS" in block
    ]
    assert len(blocks) == 1
    assert "map_interval_to_timeline" in blocks[0]
    assert "vios clip --sensor" in blocks[0]
    assert "VST_API_BASE" not in blocks[0], "clip resolution is the CLI's job now"

    script = (
        """set -euo pipefail
vss_stub() {
  case "$*" in
    'vios timeline --sensor sensor-1')
      printf '%s\n' '{"recorded":true,"segments":[{"start_time":"2026-08-01T12:00:00.000Z","end_time":"2026-08-01T12:01:00.000Z"}]}'
      ;;
    'vios clip --sensor sensor-1 --start-time 2026-08-01T12:00:00.000Z --end-time 2026-08-01T12:00:10.000Z')
      printf '%s\n' '{"media_url":"https://public.example/vst/storage/temp_files/clip.mp4?token=a"}'
      ;;
    *) echo "unexpected: $*" >&2; return 9 ;;
  esac
}
VSS_REPO_ROOT_SAVED="${VSS_REPO_ROOT}"
VST_URL=https://public.example
HIT_SENSOR_ID=sensor-1
HIT_START=2025-01-01T00:00:00Z
HIT_END=2025-01-01T00:00:10Z
"""
        + blocks[0].replace(
            'VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" \\\n  --no-dev --extra cli vss)',
            "VSS=(vss_stub)",
        )
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
        env={**os.environ, "VSS_REPO_ROOT": str(REPOSITORY_ROOT)},
    )


def test_ask_video_accepts_only_pre_resolved_confirmed_search_handoff() -> None:
    ask_video = (ASK_VIDEO_SKILL / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(ask_video.split())

    assert 'version: "3.2.0"' in ask_video
    assert "user-confirmed vss-search-archive handoff with a pre-resolved bounded VIDEO_URL" in ask_video
    assert "Treat that URL as Path A; do not rerun search or resolve a different interval" in normalized
    assert "The caller owns verdict validation and any fallback" in normalized
    assert "do not rerun search, resolve a sensor, broaden the clip, or choose another interval" in normalized


def test_search_harbor_eval_exercises_cli_verification_contract() -> None:
    spec = json.loads((SEARCH_SKILL / "evals/search.json").read_text(encoding="utf-8"))
    serialized = json.dumps(spec)
    adapter = SEARCH_ADAPTER.read_text(encoding="utf-8")
    adapter_module = _load_adapter(SEARCH_ADAPTER, "search_archive_adapter")
    deployment_preamble = adapter_module.DEPLOYMENT_PREAMBLE
    ingestion_preamble = adapter_module.INGESTION_PREAMBLE
    deployment_checks = spec["expects"][0]["checks"]
    ingestion_checks = spec["expects"][1]["checks"]

    assert len(spec["expects"]) == 9
    assert spec["expects"][0]["scenario"] == "deploy-search-profile"
    assert spec["expects"][1]["scenario"] == "ingest-search-fixtures"
    assert "vss-ask-video" in spec["skills"]
    assert "--extra cli vss search run" in serialized
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

    # Cold deployment and fixture ingestion are separate persisted steps. This
    # prevents model initialization from consuming the ingestion budget and
    # removes any incentive to repair/redeploy midway through source setup.
    assert "do not download or ingest sample media" in deployment_preamble
    assert "Initial profile deployment activity is not a routing violation" in deployment_preamble
    assert "preceding step already deployed" in ingestion_preamble
    assert "do not invoke `/vss-deploy-profile`" in ingestion_preamble
    assert "`docker compose up`" in ingestion_preamble
    assert any("one bounded source-setup deadline" in check for check in ingestion_checks)

    # Current search indices use the VST sensor ID for embed/fusion source
    # scoping and the source name for attribute/object; source_type selects the
    # upload/live partition independently.
    for step in (3, 4, 5):
        assert "sensor ID" in spec["expects"][step]["query"]
        assert "--source-type video_file" in spec["expects"][step]["query"]
    assert "sensor ID as `--video-source` for `embed` and `fusion`" in adapter

    # search_group._runtime_from sets vst_external_url to deployment.base_url, so
    # the host CLI stamps the `vss configure` origin into every screenshot_url.
    # VST_EXTERNAL_URL drives the Agent-served path only: telling the agent to
    # edit it, or to recreate services, cannot change CLI media URLs at all.
    for step in (3, 4):
        media_check = _check_matching(spec["expects"][step]["checks"], "media URL")
        assert "origin recorded by `vss configure`" in media_check
        assert "VST_EXTERNAL_URL" not in media_check
    assert "host-reachable origin" in _check_matching(spec["expects"][3]["checks"], "media URL")

    origin_check = _check_matching(deployment_checks, "select_brev_origin.sh")
    assert "`vss configure` recorded the selected origin" in origin_check
    assert "neither edited `VST_EXTERNAL_URL` nor looped on routing" in origin_check
    assert "documented host-reachable fallback" in adapter
    assert "explicitly label the media URLs host-local" in adapter
    assert "redirects disabled" in deployment_preamble

    verification_steps = [
        expect for expect in spec["expects"] if expect.get("scenario") == "confirmed-search-result-verification"
    ]
    assert len(verification_steps) == 1
    verification = verification_steps[0]
    assert "Yes, verify this one result now" in verification["query"]
    assert any(
        "at most one additional request only to repair malformed structured output" in check
        for check in verification["checks"]
    )
    assert "ask_video_skill_dir" in adapter
    assert '(ask_video_skill_dir, "vss-ask-video")' in adapter

    forklift_checks = spec["expects"][3]["checks"]
    assert any("one bounded critic attempt for every returned forklift hit" in check for check in forklift_checks)

    # The ban exists to stop invented hostnames, but the correction it mandates
    # builds the documented one — an unscoped prohibition contradicts it.
    assert "do not invent a hostname" in serialized


def test_search_routing_eval_rejects_partial_set_fallback() -> None:
    cases = json.loads((SEARCH_SKILL / "evals/evals.json").read_text(encoding="utf-8"))
    partial = next(case for case in cases if case["id"] == "search-archive-partially-verified")

    assert "does not offer or invoke" in partial["ground_truth"]
    assert any("does not invoke vss-ask-video" in behavior for behavior in partial["expected_behavior"])


def test_source_lifecycle_uses_current_configure_contract() -> None:
    lifecycle = (SEARCH_SKILL / "references/source_lifecycle.md").read_text(encoding="utf-8")
    origin_selector = (SEARCH_SKILL / "scripts/select_brev_origin.sh").read_text(encoding="utf-8")
    # Prose assertions run against a whitespace-normalized copy so rewrapping a
    # paragraph or indenting it under a list marker doesn't fail the contract.
    prose = " ".join(lifecycle.split())

    assert "vss_cli.deployment" not in lifecycle
    assert "RuntimeSnapshot" not in lifecycle
    assert 'configure --base-url "${VSS_ORIGIN}"' in lifecycle
    assert "configure show" in lifecycle
    assert "--extra cli" in lifecycle
    assert "dev-profile-sample-data:3.2.0" in lifecycle
    assert "mktemp -d" in lifecycle
    assert "Never send a mutating request directly" in lifecycle
    assert "if it is absent, continue" in lifecycle
    assert "must not block fixture download, Agent-backed ingestion, or index readiness" in prose
    assert "ONE shared 40-minute source-setup budget, not 40 minutes each" in prose
    assert "Deployment and public-origin selection are prerequisite work outside this ingestion budget" in prose
    assert "SEARCH_READINESS_DEADLINE:=$(($(date +%s) + 2400))" in lifecycle
    assert "CURRENT_EPOCH < SEARCH_READINESS_DEADLINE" in lifecycle
    assert "is the one sanctioned construction" in prose
    assert "--max-redirs 0" in origin_selector
    assert '.type == "vst"' in origin_selector
    assert origin_selector.count("curl ") == 1
    assert "Do not issue a public-origin `curl` before or after it" in prose
    assert "readiness_timeout 300" in lifecycle
    assert "readiness_timeout 900" in lifecycle
    assert 'max-time "${DELETE_TIMEOUT}"' in lifecycle
    assert 'max-time "${COUNT_TIMEOUT}"' in lifecycle
    assert "DELETE_READINESS_DEADLINE=$(($(date +%s) + 600))" in lifecycle
    assert "delete_timeout()" in lifecycle
    assert 'max-time "${DELETE_TIMEOUT}"' in lifecycle
    assert 'delete_index_count "${BEHAVIOR_INDEX}" sensor.id.keyword' in lifecycle
    assert 'delete_index_count "${RAW_INDEX}" sensorId.keyword' in lifecycle
    assert "SAMPLE_RTVI_LOG == 1" not in lifecycle
    assert "Never keep an otherwise-ready setup waiting for an exact log message" in prose

    # The host CLI stamps the `vss configure` origin into screenshot_url, so the
    # lifecycle must point at that lever and must not send the agent off editing
    # VST_EXTERNAL_URL (which only feeds the Agent-served path) to change it.
    assert "The host CLI stamps the origin you gave `vss configure`" in prose
    assert "Editing `VST_EXTERNAL_URL` in `generated.env` cannot change them" in prose
    assert "`VST_EXTERNAL_URL` governs the Agent-served path" in prose


def test_public_probe_rejects_redirects_and_accepts_vst_json(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts/select_brev_origin.sh"
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


def test_readiness_timeout_caps_each_blocking_request() -> None:
    lifecycle = (SEARCH_SKILL / "references/source_lifecycle.md").read_text(encoding="utf-8")
    match = re.search(r"(readiness_timeout\(\) \{.*?\n\})", lifecycle, flags=re.DOTALL)
    assert match is not None
    script = f"""set -euo pipefail
{match.group(1)}
SEARCH_READINESS_DEADLINE=$(($(date +%s) + 3))
value=$(readiness_timeout 900)
[ "$value" -ge 1 ] && [ "$value" -le 3 ]
SEARCH_READINESS_DEADLINE=$(($(date +%s) - 1))
! readiness_timeout 30
"""
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)


def test_setup_recipes_cannot_reset_or_bypass_global_deadline() -> None:
    lifecycle = (SEARCH_SKILL / "references/source_lifecycle.md").read_text(encoding="utf-8")
    source_setup = lifecycle.split("## Pre-ingestion cleanup", 1)[1].split("## Delete source", 1)[0]
    shell = "\n".join(re.findall(r"```bash\n(.*?)```", source_setup, flags=re.DOTALL))

    assert shell.count("SEARCH_READINESS_DEADLINE:=$(($(date +%s) + 2400))") == 1
    assert not re.search(r"(?m)^(?:DEADLINE|READINESS_DEADLINE|CLEANUP_DEADLINE)=", shell)
    assert re.findall(r"\$\(date \+%s\) \+ (\d+)", shell) == ["2400"]
    assert not re.search(r"--max-time\s+[0-9]+(?:\s|$)", shell)


def test_delete_recipe_is_bounded_and_checks_all_cleanup_tuples() -> None:
    lifecycle = (SEARCH_SKILL / "references/source_lifecycle.md").read_text(encoding="utf-8")
    blocks = [
        block
        for block in re.findall(r"```bash\n(.*?)```", lifecycle, flags=re.DOTALL)
        if "DELETE_READINESS_DEADLINE=" in block
    ]
    assert len(blocks) == 1
    script = f"""set -euo pipefail
curl() {{
  case "$*" in
    *'-X DELETE'*) printf '%s\n' '{{"status":"success"}}' ;;
    *'/_count'*) printf '%s\n' '{{"count":0}}' ;;
    *) return 9 ;;
  esac
}}
# Source listing is `vss vios list` now, not a curl. Stub it in the CLI's own
# shape -- {{count, sensors:[...]}} -- so the recipe's jq is exercised against
# what the command actually returns.
vss_stub() {{
  case "$*" in
    'vios list') printf '%s\n' '{{"count":0,"type":null,"sensors":[]}}' ;;
    *) return 9 ;;
  esac
}}
VSS=(vss_stub)
AGENT_URL=https://public.example
ES_URL=http://elasticsearch:9200
SAVED_SENSOR_ID=sensor-1
SAVED_SOURCE_NAME=warehouse-ladder
EMBED_INDEX=mdx-embed-filtered-2025-01-01
BEHAVIOR_INDEX=mdx-behavior-2025-01-01
RAW_INDEX=mdx-raw-2025-01-01
{blocks[0]}
"""
    completed = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    assert "delete_status=success vst_present=false counts=0,0,0" in completed.stdout


def test_search_adapter_bundles_ask_video_for_confirmation(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(SEARCH_ADAPTER),
            "--output-dir",
            str(tmp_path),
            "--skill-dir",
            str(SEARCH_SKILL),
            "--deploy-skill-dir",
            str(REPOSITORY_ROOT / "skills/vss-deploy-profile"),
            "--video-io-skill-dir",
            str(REPOSITORY_ROOT / "skills/vss-manage-video-io-storage"),
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
    assert "do not invoke `/vss-deploy-profile`" in ingestion_instruction

    verification_step = tmp_path / "search/rtxpro6000bw/step-7"
    assert (verification_step / "skills/vss-ask-video/SKILL.md").is_file()
    instruction = (verification_step / "instruction.md").read_text(encoding="utf-8")
    assert "explicit post-results confirmation" in instruction

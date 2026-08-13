# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-component contracts for archived-search operations and verification."""

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


def _load_adapter(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("search_archive_adapter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_search_skill_owns_project_cli_and_result_contract() -> None:
    main = (SEARCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
    verification = (SEARCH_SKILL / "references/result_verification.md").read_text(encoding="utf-8")

    assert 'version: "3.3.0"' in main
    assert 'VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)' in main
    assert "each exact `screenshot_url` as `Media URL:`" in main
    assert "only when every displayed result is" in " ".join(main.split())
    assert "Never hand off a partially verified result set" in " ".join(main.split())
    assert "assets/search_result_template.md" not in main
    assert main.count("## Troubleshooting") == 1
    assert "exactly these four keys" in verification
    assert "Invoke the skill exactly once" in verification
    assert "must not contain the term `VLM`" in verification


def test_source_operations_are_self_contained_and_bounded() -> None:
    lifecycle = (SEARCH_SKILL / "references/source_lifecycle.md").read_text(encoding="utf-8")
    ingest = (SEARCH_SKILL / "scripts/ingest_search_fixtures.sh").read_text(encoding="utf-8")
    delete = (SEARCH_SKILL / "scripts/delete_search_source.sh").read_text(encoding="utf-8")

    assert "ingest_search_fixtures.sh" in lifecycle
    assert "delete_search_source.sh" in lifecycle
    assert "source_setup_budget.sh" not in lifecycle
    assert "verify_source_cleanup.sh" not in lifecycle
    assert "DEADLINE=$(($(date +%s) + TIMEOUT_SECONDS))" in ingest
    assert ingest.count("ngc registry resource download-version") == 1
    assert 'timeout --foreground "${REQUEST_TIMEOUT}"' in ingest
    assert 'name == "airport"' not in ingest
    assert ingest.index("WAREHOUSE_LADDER_UPLOAD=") < ingest.index("complete_upload()")
    assert 'wait "${SAMPLE_PID}" || SAMPLE_STATUS=$?' in ingest
    assert 'wait "${LADDER_PID}" || LADDER_STATUS=$?' in ingest
    assert "sensor.id.keyword" in ingest and "sensorId.keyword" in ingest
    assert delete.count("-X DELETE") == 1
    assert "any(.[]; .sensorId == $id or .name == $name)" in delete
    assert 'index_count "${BEHAVIOR_INDEX}" sensor.id.keyword "${CANONICAL_NAME}"' in delete
    assert 'index_count "${RAW_INDEX}" sensorId.keyword "${CANONICAL_NAME}"' in delete


def test_delete_operation_emits_complete_structured_result(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    agent = checkout / "services/agent"
    agent.mkdir(parents=True)
    (agent / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
printf '%s\n' '{"base_url":"https://vss.example","services":{"elasticsearch":{"url":"http://es:9200","indices":["mdx-embed-filtered-2025-01-01","mdx-behavior-2025-01-01","mdx-raw-2025-01-01"]}}}'
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
output=
delete=false
args="$*"
for arg in "$@"; do
  [ "$arg" = DELETE ] && delete=true
done
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
if [ "$delete" = true ]; then
  printf '%s' '{"status":"success","message":"deleted"}' >"$output"
  printf '200'
elif [ ! -f "${VST_SEEN}" ]; then
  : >"${VST_SEEN}"
  printf '%s\n' '[{"sensorId":"sensor-1","name":"warehouse-ladder"}]'
elif [[ "$args" == *'/_count'* ]]; then
  printf '%s\n' '{"count":0}'
elif [ "${MALFORMED_VST:-0}" = 1 ]; then
  printf '%s\n' '[{}]'
else
  printf '%s\n' '[]'
fi
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    vst_seen = tmp_path / "vst-seen"
    completed = subprocess.run(
        [str(SEARCH_SKILL / "scripts/delete_search_source.sh"), "warehouse-ladder", "10"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "VSS_REPO_ROOT": str(checkout),
            "VST_SEEN": str(vst_seen),
        },
    )
    result = json.loads(completed.stdout)
    assert result["delete"]["status"] == "success"
    assert result["vst_present"] is False
    assert [result[key]["count"] for key in ("embedding", "behavior", "raw")] == [0, 0, 0]

    vst_seen.unlink()
    malformed = subprocess.run(
        [str(SEARCH_SKILL / "scripts/delete_search_source.sh"), "warehouse-ladder", "10"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "VSS_REPO_ROOT": str(checkout),
            "VST_SEEN": str(vst_seen),
            "MALFORMED_VST": "1",
        },
    )
    assert malformed.returncode == 1
    error = json.loads(malformed.stderr)
    assert error["error"] == "VST source listing was not a valid sensor array during cleanup verification"


def test_ingest_operation_rejects_malformed_vst_convergence(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    agent = checkout / "services/agent"
    agent.mkdir(parents=True)
    (agent / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
printf '%s\n' '{"base_url":"https://vss.example","services":{"elasticsearch":{"url":"http://es:9200","indices":[]},"rt_embed":{"url":"http://embed:8000","models":["embed-model"]},"rtvi_cv":{"url":"http://cv:9000"}}}'
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
args="$*"
case "$args" in
  *'-X DELETE'*) printf '%s\n' '{"status":"success"}' ;;
  *'/health'*) printf '%s\n' '{}' ;;
  *'/vst/api/v1/sensor/list'*)
    count=$(cat "${VST_CALLS}" 2>/dev/null || printf '0')
    count=$((count + 1))
    printf '%s' "$count" >"${VST_CALLS}"
    case "$count" in
      1) printf '%s\n' '[]' ;;
      2) printf '%s\n' '[{"sensorId":"old-sensor","name":"warehouse_sample"}]' ;;
      *) printf '%s\n' '[{}]' ;;
    esac
    ;;
  *'/v1/models'*) printf '%s\n' '{"data":[{"id":"embed-model"}]}' ;;
  *'/api/v1/ready'*) printf '%s\n' '{"ds-ready":"YES"}' ;;
  *) exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_ngc = fake_bin / "ngc"
    fake_ngc.write_text(
        '#!/usr/bin/env bash\nprintf reached >"${NGC_REACHED}"\n',
        encoding="utf-8",
    )
    fake_ngc.chmod(0o755)
    ngc_reached = tmp_path / "ngc-reached"

    completed = subprocess.run(
        [str(SEARCH_SKILL / "scripts/ingest_search_fixtures.sh"), "30"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "VSS_REPO_ROOT": str(checkout),
            "VST_CALLS": str(tmp_path / "vst-calls"),
            "NGC_REACHED": str(ngc_reached),
        },
    )

    assert completed.returncode == 1
    error = json.loads(completed.stderr)
    assert error["error"] == "fixture absence listing was not a valid sensor array"
    assert not ngc_reached.exists()


def test_search_eval_uses_operations_without_rubric_queries() -> None:
    spec = json.loads((SEARCH_SKILL / "evals/search.json").read_text(encoding="utf-8"))
    adapter = _load_adapter(SEARCH_ADAPTER)
    ingestion = spec["expects"][1]
    deletion = spec["expects"][7]

    assert len(spec["expects"]) == 9
    assert len(ingestion["query"]) < 300
    assert len(deletion["query"]) < 180
    for operation in spec["expects"][3:7]:
        assert len(operation["query"]) < 300
        assert "vss search run" not in operation["query"]
        assert "--video-source" not in operation["query"]
    assert "rebase" not in spec["expects"][6]["query"]
    assert any("ingest_search_fixtures.sh" in check for check in ingestion["checks"])
    assert any("delete_search_source.sh" in check for check in deletion["checks"])
    assert "PROJECT_CLI_PREAMBLE" not in SEARCH_ADAPTER.read_text(encoding="utf-8")
    assert "self-contained fixture-ingestion operation" in adapter.INGESTION_PREAMBLE
    assert "result-verification reference" in adapter.VERIFICATION_PREAMBLE


def test_search_adapter_bundles_all_operation_skills(tmp_path: Path) -> None:
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
    steps = sorted((tmp_path / "search/rtxpro6000bw").glob("step-*"))
    assert len(steps) == 9
    assert (steps[1] / "skills/vss-search-archive/scripts/ingest_search_fixtures.sh").is_file()
    assert (steps[7] / "skills/vss-search-archive/scripts/delete_search_source.sh").is_file()
    assert (steps[6] / "skills/vss-ask-video/SKILL.md").is_file()


def test_search_handoff_resolves_bounded_clip_for_existing_ask_video() -> None:
    verification = (SEARCH_SKILL / "references/result_verification.md").read_text(encoding="utf-8")
    blocks = [
        block for block in re.findall(r"```bash\n(.*?)```", verification, flags=re.DOTALL) if "CLIP_RESPONSE=" in block
    ]
    assert len(blocks) == 1
    assert "map_interval_to_timeline" in blocks[0]
    assert '--data-urlencode "startTime=${CLIP_START}"' in blocks[0]
    assert '--data-urlencode "endTime=${CLIP_END}"' in blocks[0]


def test_ask_video_checks_http_status_before_parsing() -> None:
    ask_video = (ASK_VIDEO_SKILL / "SKILL.md").read_text(encoding="utf-8")
    blocks = [
        block for block in re.findall(r"```bash\n(.*?)```", ask_video, flags=re.DOTALL) if "/chat/completions" in block
    ]
    assert len(blocks) == 1
    block = blocks[0]
    assert "-o \"${RESPONSE_FILE}\" -w '%{http_code}'" in block
    assert '[[ "${HTTP_CODE}" =~ ^2[0-9][0-9]$ ]]' in block
    assert "jq -er '.choices[0].message.content" in block
    assert "Confirmed search-result single-attempt override" not in ask_video


def test_ask_video_sends_remote_key_only_within_configured_url_boundary() -> None:
    ask_video = (ASK_VIDEO_SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert '[[ "${ENDPOINT_BASE}" == "${REMOTE_BASE}" ]]' in ask_video
    assert '[[ "${ENDPOINT_BASE}" == "${REMOTE_BASE}/"* ]]' in ask_video
    assert '[[ "${VLM_ENDPOINT%/}" == "${VLM_REMOTE_URL%/}"* ]]' not in ask_video


def test_standalone_embed_override_disables_legacy_kafka_switch() -> None:
    override = (REPOSITORY_ROOT / "deploy/helm/services/rtvi/charts/rtvi-embed/overrides_rtvi_embed.yaml").read_text(
        encoding="utf-8"
    )

    assert 'messageBus: ""' in override
    assert 'kafkaEnabled: ""' in override
    template = (REPOSITORY_ROOT / "deploy/helm/services/rtvi/charts/rtvi-embed/templates/deployment.yaml").read_text(
        encoding="utf-8"
    )
    assert 'value: {{ .Values.kafkaEnabled | default "" | quote }}' in template


def test_public_probe_rejects_redirects_and_accepts_vst_json(tmp_path: Path) -> None:
    selector = SEARCH_SKILL / "scripts/select_brev_origin.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
printf '%s' "${CURL_BODY}" >"${output}"
printf '%s' "${CURL_STATUS}"
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    for status, body, expected in (
        (302, "<html>login</html>", "http://10.0.0.1:7777"),
        (200, '{"type":"vst","version":"3.2.0"}', "https://public.example"),
    ):
        completed = subprocess.run(
            [str(selector), "https://public.example", "http://10.0.0.1:7777"],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "CURL_BODY": body,
                "CURL_STATUS": str(status),
            },
        )
        assert json.loads(completed.stdout)["origin"] == expected

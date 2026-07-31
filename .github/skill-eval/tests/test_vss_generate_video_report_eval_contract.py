# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the base-profile video-report eval contract."""

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_SKILL_DIR = REPO_ROOT / "skills/vss-generate-video-report"
REPORT_SKILL = REPORT_SKILL_DIR / "SKILL.md"
REPORT_SPEC = REPORT_SKILL_DIR / "evals/base_profile_report.json"
BASE_REFERENCE = REPO_ROOT / "skills/vss-deploy-profile/references/base.md"
DEPLOY_EVALS = REPO_ROOT / "skills/vss-deploy-profile/evals/evals.json"
ADAPTER = (
    REPO_ROOT
    / ".github/skill-eval/adapters/vss-generate-video-report/generate.py"
)
LOCAL_RT_VLM_ENV_OVERRIDES = [
    "VLM_MODE=local_shared",
    "VLM_MODEL_TYPE=rtvi",
    "VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final",
    "VLM_NAME_SLUG=none",
]


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_generate_video_report_adapter", ADAPTER
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _step_contract(step: dict) -> str:
    return "\n".join([step["query"], *step["checks"]])


def test_base_report_eval_uses_integrated_local_rt_vlm() -> None:
    """Keep the report chain aligned with the base profile migrated in #1351."""
    report_spec = json.loads(REPORT_SPEC.read_text())
    steps = report_spec["expects"]
    serialized = json.dumps(report_spec)

    assert "localhost:30082" not in serialized
    assert "nvidia-cosmos3-reasoner" not in serialized

    for index in (0, 2, 3, 4):
        contract = _step_contract(steps[index])
        assert "8018" in contract
        assert "cosmos3-nano-reasoner" in contract

    for index in (0, 2):
        contract = _step_contract(steps[index])
        assert "vss-rtvi-vlm" in contract
        assert "`VLM_MODE=local`" in contract
        assert "`VLM_MODE=local_shared`" in contract
        assert "`VLM_MODEL_TYPE=rtvi`" in contract
        assert "VIA_VLM_ENDPOINT=" in contract
        assert "non-`none` CR3 `MODEL_PATH`" in contract
        assert "VLM_MODEL_TO_USE=cosmos-reason3" in contract
        assert "remote" in contract.lower()

    expected_overrides = json.dumps(LOCAL_RT_VLM_ENV_OVERRIDES)
    assert (
        f"`env_overrides={expected_overrides}` exactly" in steps[0]["query"]
    )
    assert len(steps[0]["checks"]) == 6
    override_check = steps[0]["checks"][0]
    assert "vss_orchestrator__docker_generate" in override_check
    assert "containing all four required key/value pairs in any order" in override_check
    for override in LOCAL_RT_VLM_ENV_OVERRIDES:
        assert f"`{override}`" in override_check
    assert "Unrelated extra overrides are allowed" in override_check
    assert "conflicting value for any of those four VLM keys" in override_check
    assert "docker inspect vss-agent" in override_check
    assert "an exact `VLM_MODE=local` or `VLM_MODE=local_shared`" in override_check
    assert "`VLM_MODE=local_shared`" in override_check
    assert "`VLM_MODEL_TYPE=rtvi`" in override_check
    assert (
        "`VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final`"
        in override_check
    )

    assert "nim_nvidia_cosmos3-nano-reasoner_bf16-final" in steps[0]["query"]

    report_call = _step_contract(steps[3])
    assert "port 8018 at `/v1/chat/completions`" in report_call
    assert "matching an id advertised by GET `/v1/models`" in report_call
    assert "A fallback to a non-CR3 model" in report_call


def test_report_eval_uses_the_caller_host_for_worker_services() -> None:
    """NemoClaw reaches worker ports through HOST_IP, not sandbox localhost."""
    report_spec = json.loads(REPORT_SPEC.read_text())
    serialized = json.dumps(report_spec)

    assert "http://localhost:" not in serialized
    assert "http://${HOST_IP:-localhost}:8018" in serialized
    assert "http://${HOST_IP:-localhost}:30888" in serialized
    assert "caller's `HOST_IP` (falling back to localhost)" in serialized

    for index in range(3, 8):
        contract = _step_contract(report_spec["expects"][index])
        if "/generate" in contract:
            assert "regardless of hostname" in contract


def test_report_and_deploy_guidance_match_the_base_rt_vlm_runtime() -> None:
    """Prevent skill prose from relabeling standalone port 30082 as base."""
    report_skill = REPORT_SKILL.read_text()
    selection_rule = report_skill.split("Selection rule:", 1)[1].split(
        "Probe `/v1/models`", 1
    )[0]
    base_reference = BASE_REFERENCE.read_text()
    deploy_eval = json.loads(DEPLOY_EVALS.read_text())[0]
    deploy_contract = "\n".join(
        [deploy_eval["ground_truth"], *deploy_eval["expected_behavior"]]
    )

    assert (
        'http://${HOST_IP:-localhost}:${RTVI_VLM_PORT:-8018}/v1/models" | jq'
        in report_skill
    )
    assert "# integrated RT-VLM" in report_skill
    assert "docker exec vss-agent sh" not in report_skill
    assert "docker inspect vss-agent" in report_skill
    assert "docker port vss-rtvi-vlm 8000/tcp" in report_skill
    assert report_skill.count('if [ -z "${HOST_IP:-}" ]; then') == 2
    assert report_skill.count('HOST_IP="$_v"; export HOST_IP') == 2
    assert report_skill.count(
        "Preserve a caller/sandbox routing alias"
    ) == 2
    assert (
        'VLM_ENDPOINT="http://${HOST_IP:-localhost}:${RTVI_VLM_PORT:-8018}/v1"'
        in selection_rule
    )
    assert 'if [ -z "${VLM_ENDPOINT:-}" ]; then' in selection_rule
    assert 'VLM_MODEL="${VLM_NAME:-}"' in selection_rule
    assert 'VLM_MODEL="${RTVI_VLM_MODEL_TO_USE}"' not in selection_rule
    assert '"${VLM_ENDPOINT}/models"' in selection_rule
    assert "VLM NIM (default)" not in base_reference
    assert "vlm_${VLM_MODE}_${VLM_NAME_SLUG}" not in base_reference
    assert "VLM_NAME_SLUG=cosmos" not in base_reference
    assert "NIM_GPU_MEMORY_UTILIZATION" not in base_reference
    assert "VLM_NIM_KVCACHE_PERCENT" not in base_reference
    assert "nvidia-cosmos3-reasoner" not in base_reference
    assert "| Integrated RT-VLM (default) | `vss-rtvi-vlm` | 8018 |" in base_reference
    assert "RTVI_VLM_MODEL_PATH" in base_reference
    assert "VLM_MODEL_TYPE=rtvi" in base_reference
    assert "VLM_NAME_SLUG=none" in base_reference
    assert "port 30082" not in deploy_contract
    assert "vss-rtvi-vlm, port 8018" in deploy_contract


def test_mode_b_eval_preserves_the_skill_read_only_boundary() -> None:
    """Do not grade the report skill on forbidden synthetic incident writes."""
    report_spec = json.loads(REPORT_SPEC.read_text())
    report_skill = REPORT_SKILL.read_text()
    lookup_step = _step_contract(report_spec["expects"][6])

    assert "strictly read-only" in lookup_step
    assert "video_analytics__get_incidents" in lookup_step
    assert "max_count=1" in lookup_step
    assert "does NOT write to Elasticsearch" in lookup_step
    assert "Insert one VLM-verified incident" not in lookup_step
    assert "Mode B is strictly read-only analytics retrieval" in report_skill
    assert "Never write, seed, backfill, or mutate" in report_skill


def test_report_chain_is_single_gpu_safe_and_documents_latest_incidents() -> None:
    """Keep Mode A before teardown and make the alerts handoff deterministic."""
    report_spec = json.loads(REPORT_SPEC.read_text())
    steps = report_spec["expects"]
    report_skill = REPORT_SKILL.read_text()

    assert "new report on warehouse_safety_0001" in steps[4]["query"]

    alerts_step = _step_contract(steps[5])
    assert "VSS_AUTO_DEPLOY=true" in alerts_step
    assert "/vss-deploy-profile -p alerts -m real-time" in alerts_step
    assert "MODE=2d_vlm" in alerts_step
    for device_setting in (
        "LLM_DEVICE_ID=0",
        "VLM_DEVICE_ID=0",
        "RT_VLM_DEVICE_ID=0",
        "FIXED_SHARED_DEVICE_IDS=0",
    ):
        assert device_setting in alerts_step
    assert "never requests GPU 1" in alerts_step

    assert "strictly read-only" in steps[6]["query"]
    assert steps[7]["query"] == "Give me a report on the last incident."
    assert "latest/single-incident path" in _step_contract(steps[7])
    assert "do not require or invent `start_time` / `end_time`" in report_skill
    assert '"max_count": 1' in report_skill
    assert "Do not ask the user for\na separate range." in report_skill


def test_generated_report_dataset_preserves_rt_vlm_contract() -> None:
    """Ensure adapter generation does not rewrite the corrected spec."""
    adapter = _load_adapter()
    report_spec = json.loads(REPORT_SPEC.read_text())
    report_spec["_source_path"] = str(REPORT_SPEC)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_root = Path(temp_dir)
        adapter.generate_task(
            "RTXPRO6000BW",
            "base",
            report_spec,
            output_root,
            REPORT_SKILL_DIR,
            REPO_ROOT / "skills/vss-deploy-profile",
            REPO_ROOT / "skills/vss-manage-video-io-storage",
            REPO_ROOT / "skills/vss-query-analytics",
        )

        generated_root = output_root / "base/rtxpro6000bw"
        step_dirs = sorted(
            generated_root.glob("step-*"),
            key=lambda path: int(path.name.removeprefix("step-")),
        )
        assert [path.name for path in step_dirs] == [
            f"step-{index}" for index in range(1, 9)
        ]

        for index, step_dir in enumerate(step_dirs, 1):
            instruction = (step_dir / "instruction.md").read_text()
            task_metadata = (step_dir / "task.toml").read_text()
            verifier = (step_dir / "tests/test.sh").read_text()

            assert instruction.startswith(adapter.PREAMBLE)
            assert "{{platform}}" not in instruction
            assert f"step_index = {index}" in task_metadata
            assert "step_count = 8" in task_metadata
            assert (
                f"check_count = {len(report_spec['expects'][index - 1]['checks'])}"
                in task_metadata
            )
            assert f"--step {index}" in verifier

        step_one_instruction = (step_dirs[0] / "instruction.md").read_text()
        step_three_instruction = (step_dirs[2] / "instruction.md").read_text()
        step_five_instruction = (step_dirs[4] / "instruction.md").read_text()
        step_six_instruction = (step_dirs[5] / "instruction.md").read_text()
        generated_spec = (
            step_dirs[3]
            / "tests/base_profile_report.json"
        ).read_text()
        task_metadata = (step_dirs[0] / "task.toml").read_text()
        solve_stub = (step_dirs[0] / "solution/solve.sh").read_text()

    assert "${HOST_IP:-localhost}:8018" in step_one_instruction
    assert (
        f"env_overrides={json.dumps(LOCAL_RT_VLM_ENV_OVERRIDES)}"
        in step_one_instruction
    )
    assert "RTXPRO6000BW" in step_one_instruction
    assert "${HOST_IP:-localhost}:8018" in step_three_instruction
    assert "new report on warehouse_safety_0001" in step_five_instruction
    assert "/vss-deploy-profile -p alerts -m real-time" in step_six_instruction
    assert "{{platform}}" not in generated_spec
    assert "{{range .Config.Env}}{{println .}}{{end}}" in generated_spec
    assert "localhost:30082" not in generated_spec
    assert "http://localhost:" not in generated_spec
    assert "port 8018 at `/v1/chat/completions`" in generated_spec
    assert "Mode A uses the base profile's local integrated CR3 RT-VLM" in task_metadata
    assert "gpu_count = 1" in task_metadata
    assert "min_root_disk_gb = 220" in task_metadata
    assert "FULL-REMOTE" not in task_metadata
    assert "Reference prerequisite smoke check" in solve_stub
    assert 'HOST_IP="${HOST_IP:-localhost}"' in solve_stub
    assert '"http://${HOST_IP}:8018/v1/models"' in solve_stub
    assert "Gold solution" not in solve_stub


def test_adapter_rejects_unknown_eval_placeholders() -> None:
    """Unknown prose placeholders must block instead of reaching Harbor."""
    adapter = _load_adapter()

    with pytest.raises(ValueError, match="unresolved eval placeholders: mystery"):
        adapter._substitute_spec(
            {"expects": [{"query": "{{mystery}}", "checks": []}]},
            "RTXPRO6000BW",
        )

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the generated LVS skill-evaluation request contract.

The adapter preamble guides every generated evaluation step, while the eval
specification supplies scenario-specific instructions and grader checks. These
tests keep both layers aligned on the single-request and media-reuse behavior.
"""

import importlib.util
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_SPEC = REPO_ROOT / "skills/vss-summarize-video/evals/lvs_profile_summarize.json"
API_OPS_SPEC = REPO_ROOT / "skills/vss-summarize-video/evals/lvs_api_ops.json"
ADAPTER = REPO_ROOT / ".github/skill-eval/adapters/vss-summarize-video/generate.py"
SUMMARIZE_SKILL = REPO_ROOT / "skills/vss-summarize-video/SKILL.md"
SUMMARIZE_REFERENCES = (
    REPO_ROOT / "skills/vss-summarize-video/references/end-to-end-example.md",
    REPO_ROOT / "skills/vss-summarize-video/references/hitl-prompts.md",
    REPO_ROOT / "skills/vss-summarize-video/references/video-summarization-api.md",
)
LVS_RESPONSE_FILTER = """{
  usage: (.usage // {}),
  result: (.choices[0].message.content | fromjson | {video_summary, events})
}"""


def _load_adapter():
    """Load the eval adapter directly without requiring it to be a package."""
    spec = importlib.util.spec_from_file_location("vss_summarize_generate", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_preamble_enforces_single_terminal_summarization_request() -> None:
    """Ensure every generated trial receives the common safe-call contract."""
    preamble = _load_adapter().PREAMBLE

    assert "invoke the vss-summarize-video skill" in preamble
    assert "exactly one POST /v1/summarize" in preamble
    assert "chunk_duration=10" in preamble
    assert "do not retry" in preamble
    assert "/v1/chat/completions" in preamble
    assert "/v1/generate_captions" in preamble
    assert "retry only" not in preamble


def test_api_ops_adapter_renders_platform_for_agent_and_verifier(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    raw_spec = json.loads(API_OPS_SPEC.read_text(encoding="utf-8"))
    raw_spec["_source_path"] = str(API_OPS_SPEC)

    adapter.generate_task(
        "RTXPRO6000BW",
        "lvs",
        raw_spec,
        tmp_path,
        REPO_ROOT / "skills/vss-summarize-video",
        REPO_ROOT / "skills/vss-deploy-profile",
        None,
    )

    step_dirs = sorted(
        (tmp_path / "lvs/rtxpro6000bw").glob("step-*"),
        key=lambda path: int(path.name.removeprefix("step-")),
    )
    assert len(step_dirs) == len(raw_spec["expects"])
    for step_dir in step_dirs:
        instruction = (step_dir / "instruction.md").read_text(
            encoding="utf-8"
        )
        verifier_spec = (
            step_dir / "tests/lvs_api_ops.json"
        ).read_text(encoding="utf-8")
        assert "{{platform}}" not in instruction
        assert "{{platform}}" not in verifier_spec

    assert "RTXPRO6000BW" in (
        step_dirs[0] / "instruction.md"
    ).read_text(encoding="utf-8")


def test_summarization_steps_enforce_the_same_request_contract() -> None:
    """Ensure each LVS scenario makes the shared contract grader-visible."""
    queries = json.loads(EVAL_SPEC.read_text())["expects"][1:]

    for query in queries:
        contract = "\n".join([query["query"], *query["checks"]])
        assert "Invoke and follow the vss-summarize-video skill" in query["query"]
        assert "First make the video available through VIOS" in query["query"]
        assert "exactly one POST" in contract
        assert "chunk_duration" in contract
        assert "/v1/chat/completions" in contract
        assert "/v1/generate_captions" in contract
        assert "without retrying" in query["query"]
        assert "only if it is absent" in query["query"]
        assert "remove the prior eval upload" not in query["query"]


def test_summarization_checks_assign_each_behavior_once() -> None:
    """Keep request counting and direct-VLM prohibition in distinct checks."""
    checks = json.loads(EVAL_SPEC.read_text())["expects"][1]["checks"]

    assert "exactly one POST operation" in checks[0]
    assert "tool_call_id" in checks[0]
    assert "steps[].tool_calls" in checks[0]
    assert "multiple curl invocations, loops, or scripts" in checks[0]
    assert "/v1/chat/completions" in checks[-1]
    assert "/v1/generate_captions" in checks[-1]
    assert "one POST" not in checks[-1]


def test_summarization_uses_one_ordered_workflow_without_return_protocol() -> None:
    """Keep VIOS preparation in the ordered workflow and its loaded reference."""
    eval_spec = json.loads(EVAL_SPEC.read_text())
    summarize_skill = SUMMARIZE_SKILL.read_text()
    normalized_summarize_skill = " ".join(summarize_skill.split())
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()

    assert "Recorded Video Workflow" in summarize_skill
    assert "Prepare the Video Through VIOS" in summarize_skill
    assert "Execute VIOS API operations directly" in summarize_skill
    assert "do not invoke a separate skill" in normalized_summarize_skill
    assert "Invoke and follow the `vss-manage-video-io-storage` skill" not in summarize_skill
    assert "vss-manage-video-io-storage" not in eval_spec["skills"]
    assert '"$VIOS_API/sensor/list"' in end_to_end_example
    assert '"$VIOS_API/sensor/$SENSOR_ID/streams"' in end_to_end_example
    assert (
        '"$VIOS_API/storage/file/$FILENAME?timestamp=$UPLOAD_TIMESTAMP"'
        in end_to_end_example
    )
    assert 'Content-Type: application/octet-stream' in end_to_end_example
    assert 'Content-Length: $FILE_SIZE' in end_to_end_example
    assert '--upload-file "$SOURCE_FILE"' in end_to_end_example
    assert '"$VIOS_API/storage/$STREAM_ID/timelines"' in end_to_end_example
    assert '"$VIOS_API/storage/file/$STREAM_ID/url"' in end_to_end_example
    assert 'sub("^http://http://"; "http://")' in end_to_end_example
    assert "map(.startTime) | min" in end_to_end_example
    assert "map(.endTime) | max" in end_to_end_example
    assert "Stage 1: Select the Backend" in summarize_skill
    assert "Stage 2: Prepare the Video Through VIOS" in summarize_skill
    assert "Stage 3: Collect LVS Settings" in summarize_skill
    assert "Stage 4: Discover the Contract and Submit Once" in summarize_skill
    assert "Stage 5: Present the Result" in summarize_skill
    assert "full timeline, and fresh clip URL" in normalized_summarize_skill
    assert "Do not choose an arbitrary `/tmp` video" in normalized_summarize_skill
    assert "NvStreamer" in summarize_skill
    assert "Completion gate" not in summarize_skill
    assert "Step 2 fallback" not in end_to_end_example
    assert "Step 2 scenario/events" not in end_to_end_example
    assert 'headers={"Range": "bytes=0-0"}' in end_to_end_example
    assert "response.read(1)" in end_to_end_example
    assert "lightweight `curl` shim" in summarize_skill
    assert "entire video into tool output" in summarize_skill


def test_empty_lvs_results_preserve_processing_evidence() -> None:
    """Require empty results to retain evidence of LVS media processing."""
    summarize_skill = SUMMARIZE_SKILL.read_text()
    normalized_skill = " ".join(summarize_skill.split())

    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()

    assert "usage: (.usage // {})" in end_to_end_example
    assert "usage.total_chunks_processed" in summarize_skill
    assert "positive integer confirms processing" in normalized_skill
    assert "processing was not confirmed" in normalized_skill
    assert 'Do not claim "no detections."' in normalized_skill


def test_live_lvs_calls_use_runtime_openapi_contract() -> None:
    """Require live requests to discover their schema from the running LVS."""
    summarize_skill_text = SUMMARIZE_SKILL.read_text()
    summarize_skill = " ".join(summarize_skill_text.split())
    api_reference = (
        REPO_ROOT / "skills/vss-summarize-video/references/video-summarization-api.md"
    ).read_text()
    normalized_reference = " ".join(api_reference.split())

    assert "load before constructing any live LVS operation" in summarize_skill
    assert "Runtime OpenAPI Discovery" in summarize_skill
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    assert '"$VIDEO_SUMMARIZATION_URL/openapi.json"' in end_to_end_example
    assert '.paths["/v1/summarize"].post.requestBody' in end_to_end_example
    assert '"$BASE_URL/openapi.json"' in api_reference
    assert "same service instance that will receive the request" in normalized_reference
    assert "running service's `/openapi.json` is authoritative" in normalized_reference
    assert "stop before a mutating or inference request" in normalized_reference


def test_lvs_response_filter_is_consistent_and_executable() -> None:
    """Keep the documented jq response filter consistent and executable."""
    normalized_filter = " ".join(LVS_RESPONSE_FILTER.split())
    documents = SUMMARIZE_REFERENCES

    for document in documents:
        assert normalized_filter in " ".join(document.read_text().split())

    valid_cases = (
        ({"usage": {"total_chunks_processed": 2}}, 2),
        ({"usage": {"total_chunks_processed": 0}}, 0),
        ({}, None),
    )
    content = json.dumps({"video_summary": "", "events": []})
    for envelope, expected_chunks in valid_cases:
        envelope["choices"] = [{"message": {"content": content}}]
        result = subprocess.run(
            ["jq", "-e", LVS_RESPONSE_FILTER],
            input=json.dumps(envelope),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        parsed = json.loads(result.stdout)
        assert parsed["result"] == {"video_summary": "", "events": []}
        assert parsed["usage"].get("total_chunks_processed") == expected_chunks

    invalid_cases = (
        {"usage": {"total_chunks_processed": 0}, "choices": []},
        {
            "usage": {"total_chunks_processed": 0},
            "choices": [{"message": {"content": "not json"}}],
        },
    )
    for envelope in invalid_cases:
        result = subprocess.run(
            ["jq", "-e", LVS_RESPONSE_FILTER],
            input=json.dumps(envelope),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0

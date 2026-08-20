# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the generated LVS skill-evaluation request contract.

The adapter preamble guides every generated evaluation step, while the eval
specification supplies scenario-specific instructions and grader checks. These
tests keep both layers aligned on the single-request and media-reuse behavior.
"""

import importlib.util
import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_SPEC = REPO_ROOT / "skills/vss-summarize-video/evals/lvs_profile_summarize.json"
ADAPTER = REPO_ROOT / ".github/skill-eval/adapters/vss-summarize-video/generate.py"
SUMMARIZE_SKILL = REPO_ROOT / "skills/vss-summarize-video/SKILL.md"
SUMMARIZE_REFERENCES = (
    REPO_ROOT / "skills/vss-summarize-video/references/end-to-end-example.md",
    REPO_ROOT / "skills/vss-summarize-video/references/hitl-prompts.md",
    REPO_ROOT / "skills/vss-summarize-video/references/video-summarization-api.md",
)
CLI_REFERENCE = REPO_ROOT / "skills/vss-summarize-video/references/cli_usage.md"
#: The direct-API filter, for the paths that still call LVS by hand (the API
#: reference and the approved VLM fallback).
LVS_RESPONSE_FILTER = """{
  usage: (.usage // {}),
  result: (.choices[0].message.content | fromjson | {video_summary, events})
}"""
#: The CLI filter. ``vss summarize run`` nests the same envelope under
#: ``summary`` and adds the job envelope around it, so the workflow's own
#: documents must reach through that key or they describe a shape the command
#: does not emit.
CLI_RESPONSE_FILTER = """{
  usage: (.summary.usage // {}),
  result: (.summary.choices[0].message.content | fromjson | {video_summary, events})
}"""
#: Documents that describe the ordered workflow, which now runs through the CLI.
CLI_WORKFLOW_REFERENCES = (
    REPO_ROOT / "skills/vss-summarize-video/references/end-to-end-example.md",
    REPO_ROOT / "skills/vss-summarize-video/references/hitl-prompts.md",
)


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
    assert "exactly one `vss summarize run` invocation" in preamble
    assert "--chunk-duration 10" in preamble
    assert "do not re-run it" in preamble
    assert "/v1/chat/completions" in preamble
    assert "/v1/generate_captions" in preamble
    assert "retry only" not in preamble


def test_preamble_pins_the_cli_to_the_recorded_deployment() -> None:
    """A trial must drive the project-local CLI, not a hand-rolled request.

    The eval is the only place that catches an agent which "helpfully" falls
    back to curl when a flag is refused: that path skips the job id, the
    marker and the memory write, so the trial would pass on a summary that
    nothing can recall.
    """
    preamble = _load_adapter().PREAMBLE

    assert "--extra cli" in preamble
    assert "vss configure" in preamble
    assert "pass no endpoint, index, or model flag" in preamble
    assert "never hand-roll a POST /v1/summarize" in preamble
    assert "read the job_id and the persist object from it" in preamble
    assert "Do not read the record back" in preamble


def test_summarization_steps_enforce_the_same_request_contract() -> None:
    """Ensure each LVS scenario makes the shared contract grader-visible."""
    queries = json.loads(EVAL_SPEC.read_text())["expects"][1:]

    for query in queries:
        contract = "\n".join([query["query"], *query["checks"]])
        assert "Invoke and follow the vss-summarize-video skill" in query["query"]
        assert "First make the video available through VIOS" in query["query"]
        assert "exactly one `vss summarize run`" in contract
        assert "chunk-duration" in contract
        assert "/v1/chat/completions" in contract
        assert "/v1/generate_captions" in contract
        assert "without re-running the job" in query["query"]
        assert "only if it is absent" in query["query"]
        assert "remove the prior eval upload" not in query["query"]


def test_summarization_steps_require_a_persisted_job() -> None:
    """The step must prove the run persisted, not just that it answered.

    Persistence is the whole point of routing the skill through the CLI, and
    it is invisible in the summary text: without a check on the marker's
    job_id and `persist` object, a run that silently degraded to exit 6 would
    still look like a pass. The marker carries that evidence itself, so the
    proof costs no read-back.
    """
    spec = json.loads(EVAL_SPEC.read_text())
    setup, summarize = spec["expects"][0], spec["expects"][1]

    setup_contract = "\n".join([setup["query"], *setup["checks"]])
    assert "--extra cli" in setup_contract
    assert "vss configure --base-url http://localhost:7777" in setup["query"]
    # The LVS container port routes no Elasticsearch, so a deployment recorded
    # from it cannot persist -- the trial has to record the ingress origin.
    assert "never the LVS container port 38111" in setup["query"]
    assert "`elasticsearch` service" in setup_contract

    contract = "\n".join([summarize["query"], *summarize["checks"]])
    assert "completion marker" in contract
    assert "--creation-time 2025-01-01T00:00:00.000Z" in contract
    assert "`persist` object" in contract
    assert "status is `complete`" in contract
    # The count ties what was returned to what was written, which is the part a
    # silent degradation would get wrong.
    assert "`events` count equals the number of entries" in contract


def test_summarization_never_reads_its_own_write_back() -> None:
    """Verifying recall is the memory skill's job, not this one's.

    The marker already reports the write, so a read-back adds a step and a
    failure mode without adding evidence. Reconciling a timeout is the one
    read this skill still owns, and the contract has to leave room for it.
    """
    summarize = json.loads(EVAL_SPEC.read_text())["expects"][1]
    contract = "\n".join([summarize["query"], *summarize["checks"]])

    assert "does not verify the write by reading it back" in contract
    assert "no direct Elasticsearch query" in contract
    assert "recalling memory belongs to a separate skill" in contract
    assert "Reconciling an unknown outcome after a timeout" in contract
    # The retired requirement, in the two shapes it was written in.
    assert "confirms persistence by running" not in contract
    assert "output.ext.events" not in contract


def test_no_check_can_fail_merely_for_being_inapplicable() -> None:
    """A conditional check has to say what the false branch scores.

    Harbor failed the check reading "If both video_summary and events are
    empty, summary.usage.total_chunks_processed > 0" on a run where the
    service returned a summary and six events: with its precondition false
    the judge had nothing to affirm, and an inapplicable condition scored the
    same as an unmet one. Every conditional states its own vacuous verdict.
    """
    checks = json.loads(EVAL_SPEC.read_text())["expects"][1]["checks"]

    conditional = [
        check
        for check in checks
        if check.lstrip().startswith("If ") or "conditional" in check.lower()
    ]
    assert conditional, "the empty-results check is expected to be conditional"
    for check in conditional:
        assert "does not apply" in check, check
        assert "PASS" in check, check


def test_summarization_checks_assign_each_behavior_once() -> None:
    """Keep request counting and direct-VLM prohibition in distinct checks."""
    checks = json.loads(EVAL_SPEC.read_text())["expects"][1]["checks"]

    assert "exactly one `vss summarize run` operation" in checks[0]
    assert "tool_call_id" in checks[0]
    assert "steps[].tool_calls" in checks[0]
    assert "multiple invocations, loops, or scripts" in checks[0]
    assert "/v1/chat/completions" in checks[-1]
    assert "/v1/generate_captions" in checks[-1]
    assert "one `vss summarize run`" not in checks[-1]


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
    assert (
        "Invoke and follow the `vss-manage-video-io-storage` skill"
        not in summarize_skill
    )
    assert "vss-manage-video-io-storage" not in eval_spec["skills"]
    assert '"$VIOS_API/sensor/list"' in end_to_end_example
    assert '"$VIOS_API/sensor/$SENSOR_ID/streams"' in end_to_end_example
    assert (
        '"$VIOS_API/storage/file/$FILENAME?timestamp=$UPLOAD_TIMESTAMP"'
        in end_to_end_example
    )
    assert "Content-Type: application/octet-stream" in end_to_end_example
    assert "Content-Length: $FILE_SIZE" in end_to_end_example
    assert '--upload-file "$SOURCE_FILE"' in end_to_end_example
    assert '"$VIOS_API/storage/$STREAM_ID/timelines"' in end_to_end_example
    assert '"$VIOS_API/storage/file/$STREAM_ID/url"' in end_to_end_example
    assert 'sub("^http://http://"; "http://")' in end_to_end_example
    assert "map(.startTime) | min" in end_to_end_example
    assert "map(.endTime) | max" in end_to_end_example
    assert "Stage 1: Select the Backend" in summarize_skill
    assert "Stage 2: Prepare the Video Through VIOS" in summarize_skill
    assert "Stage 3: Collect LVS Settings" in summarize_skill
    assert "Stage 4: Submit Once Through the CLI" in summarize_skill
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

    assert "usage: (.summary.usage // {})" in end_to_end_example
    assert "usage.total_chunks_processed" in summarize_skill
    assert "positive integer confirms processing" in normalized_skill
    assert "processing was not confirmed" in normalized_skill
    assert 'Do not claim "no detections."' in normalized_skill


def test_live_lvs_calls_use_runtime_openapi_contract() -> None:
    """Require a hand-built LVS request to discover its schema from the service.

    Runtime discovery now scopes to the paths that still construct a request
    themselves -- the direct-API reference and the VLM fallback. The ordered
    workflow does not build one at all, so requiring the schema fetch there
    would mandate a call with nothing to validate.
    """
    summarize_skill_text = SUMMARIZE_SKILL.read_text()
    summarize_skill = " ".join(summarize_skill_text.split())
    api_reference = (
        REPO_ROOT / "skills/vss-summarize-video/references/video-summarization-api.md"
    ).read_text()
    normalized_reference = " ".join(api_reference.split())

    assert (
        "load before constructing a live LVS operation **by hand**" in summarize_skill
    )
    assert "Runtime OpenAPI Discovery" in summarize_skill
    assert "the CLI owns that" in summarize_skill
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    # The workflow reaches LVS only through the CLI now.
    assert "/openapi.json" not in end_to_end_example
    assert '"$BASE_URL/openapi.json"' in api_reference
    assert "same service instance that will receive the request" in normalized_reference
    assert "running service's `/openapi.json` is authoritative" in normalized_reference
    assert "stop before a mutating or inference request" in normalized_reference


def _run_filter(jq_filter: str, payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", "-e", jq_filter],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_lvs_response_filter_is_consistent_and_executable() -> None:
    """Keep each documented jq filter consistent with the shape it parses.

    Two filters, because there are two shapes: the CLI wraps the service
    envelope in a job envelope, so a document that describes one path with the
    other's filter tells the agent to read a key that is not there.
    """
    direct = " ".join(LVS_RESPONSE_FILTER.split())
    through_cli = " ".join(CLI_RESPONSE_FILTER.split())

    api_reference = SUMMARIZE_REFERENCES[2]
    assert direct in " ".join(api_reference.read_text().split())
    for document in CLI_WORKFLOW_REFERENCES:
        text = " ".join(document.read_text().split())
        assert through_cli in text, document
        assert direct not in text, document

    content = json.dumps({"video_summary": "", "events": []})
    valid_cases = (
        ({"usage": {"total_chunks_processed": 2}}, 2),
        ({"usage": {"total_chunks_processed": 0}}, 0),
        ({}, None),
    )
    for envelope, expected_chunks in valid_cases:
        envelope["choices"] = [{"message": {"content": content}}]
        for jq_filter, payload in (
            (LVS_RESPONSE_FILTER, envelope),
            (CLI_RESPONSE_FILTER, {"job_id": "summarize-01JZX8", "summary": envelope}),
        ):
            result = _run_filter(jq_filter, payload)
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
        assert _run_filter(LVS_RESPONSE_FILTER, envelope).returncode != 0
        assert _run_filter(CLI_RESPONSE_FILTER, {"summary": envelope}).returncode != 0


def test_the_skill_loads_a_cli_reference_that_documents_the_exit_codes() -> None:
    """Stage 4 delegates to the CLI, so its contract must be loadable.

    The exit codes carry the part of the contract the payload cannot: 6 and 7
    both mean the summarization already happened, and an agent that reads them
    as ordinary failures will re-run an expensive job.
    """
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())
    cli_reference = CLI_REFERENCE.read_text()

    assert "references/cli_usage.md" in summarize_skill
    assert "load before Stage 4" in summarize_skill
    assert "Issue exactly one `vss summarize run`" in summarize_skill
    assert "One run is one `POST /v1/summarize`" in summarize_skill
    assert "never replace a failed run with hand-rolled curl" in summarize_skill

    assert "--extra cli" in cli_reference
    assert "vss configure" in cli_reference
    for exit_code in ("| 0 |", "| 2 |", "| 3 |", "| 4 |", "| 5 |", "| 6 |", "| 7 |"):
        assert exit_code in cli_reference
    assert "never re-run the job" in cli_reference
    # A timeout hands back an identifier, not a job the service will resume.
    assert "not resumable" in cli_reference
    # --creation-time is what makes a persisted event a timestamp rather than
    # an offset, which is the difference between a recallable record and a 6.
    assert "--creation-time" in cli_reference
    assert "cannot write its events and degrades to\nexit 6" in cli_reference


def test_a_marker_is_only_read_when_a_job_exists() -> None:
    """A call refused before the job precedes any marker to parse.

    The recipe redirects stdout to a file, so an unguarded `tail -1 | jq` on a
    refused call reports a jq error instead of the diagnostic that says which
    flag was wrong or that nothing is configured.
    """
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())
    cli_reference = CLI_REFERENCE.read_text()

    assert '[ ! -s "$SUMMARIZE_OUT" ]' in end_to_end_example
    assert "no job was created" in end_to_end_example
    assert "prints no marker at all" in summarize_skill
    assert "before* a job exists" in cli_reference
    # Exit 4 is the only code that is always refused ahead of the job.
    assert summarize_skill.count("no job, no marker") == 1
    # The unqualified promise this replaced sent readers back to a marker that
    # a refused call never wrote.
    assert "Every outcome, success or failure, names a" not in summarize_skill


def test_an_exit_2_that_carries_a_job_is_not_called_marker_less() -> None:
    """LVS rejecting a submitted request exits 2 *after* the job is minted.

    The CLI writes the record before it posts, so a 4xx comes back as exit 2 with
    a marker naming a job already closed as failed. Prose that promises exit 2
    never carries one tells the operator to re-run a job that exists and throws
    away the id that identifies it, so emptiness -- not the code -- is the test.
    """
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())
    # Normalized: both claims wrap across lines in the reference.
    cli_reference = " ".join(CLI_REFERENCE.read_text().split())

    assert "LVS rejected the request it was sent" in summarize_skill
    assert "a request LVS rejects has already minted a job" in cli_reference
    assert "does carry a marker" in end_to_end_example
    for document in (summarize_skill, cli_reference):
        assert "mptiness, not the exit code" in document
    assert "Exits 2 and 4 are refused before a job exists" not in summarize_skill
    assert "Exits 2 and 4 are the exception" not in cli_reference


def test_a_rejected_submission_is_reported_rather_than_resubmitted() -> None:
    """The exit 2 that carries a marker has already spent this request's one POST.

    One run is one `POST /v1/summarize`, and the eval grades exactly one of them,
    so a single exit-2 row answering both flavors with "fix the call, then run
    once" turns a rejection LVS already recorded into a second submission for the
    same user request.
    """
    skill_rows = [row for row in SUMMARIZE_SKILL.read_text().splitlines() if row.startswith("| 2 |")]
    cli_rows = [row for row in CLI_REFERENCE.read_text().splitlines() if row.startswith("| 2 |")]
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())
    cli_reference = " ".join(CLI_REFERENCE.read_text().split())
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()

    # Each document splits exit 2 by whether a marker exists, because that is
    # what says whether anything was submitted.
    for rows in (skill_rows, cli_rows):
        assert len(rows) == 2
        submitted = [row for row in rows if "marker" in row]
        assert len(submitted) == 1
        # The row that names a job orders a report, never another run.
        assert "run once" not in submitted[0]
    assert "report the failure with that `job_id`" in "".join(skill_rows)
    assert "the submission is spent" in "".join(cli_rows)
    assert "the one submission this request had is spent" in summarize_skill
    assert "a marker means the post already happened and was refused" in cli_reference
    assert "report this job, do not resubmit it" in end_to_end_example


def test_read_examples_pass_the_only_since_the_cli_accepts() -> None:
    """`--since` takes an instant; a duration is rejected at the boundary.

    The CLI validates `--since` as ISO-8601, so a documented `--since 1h` is a
    command that exits 2 the moment an agent copies it.
    """
    cli_reference = CLI_REFERENCE.read_text()

    summarize_invocations = [
        line for line in cli_reference.splitlines() if "vss summarize" in line and "--since" in line
    ]
    assert summarize_invocations
    for line in summarize_invocations:
        assert re.search(r"--since\s+\d+[smhdw]\b", line) is None, line
        assert re.search(r"--since\s+\d{4}-\d{2}-\d{2}T", line), line
    assert "not a duration" in cli_reference


def test_a_failure_marker_is_never_parsed_as_a_summary() -> None:
    """Only exits 0 and 6 carry `summary`; the rest carry the failure instead.

    A run that dies or times out after minting the job replaces `summary` with
    `status`/`record`/`error`, so reaching for `.summary.choices[0]` there makes
    jq fail on a missing field -- and that jq error, not the marker, is what the
    operator sees. The failing exits print their marker and stop.
    """
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    cli_reference = CLI_REFERENCE.read_text()

    summary_parse = end_to_end_example.index(".summary.choices[0].message.content | fromjson")
    dispatch = end_to_end_example.index('case "$SUMMARIZE_EXIT" in')
    guarded = end_to_end_example[dispatch:summary_parse]
    # Exits 3, 5 and 7 leave before the parse; 0 and 6 are the only fall-through.
    assert guarded.count("return 1 2>/dev/null || exit 1") == 2
    assert "{job_id, status, record, error}" in guarded
    assert "Only exits 0 and 6 carry a summary" in end_to_end_example
    assert "only exits\n0 and 6 carry a summary to parse" in cli_reference

    # Every marker read takes the last line: stdout may carry prose before it,
    # so handing jq the whole file is a parse of something else.
    assert 'tail -1 "$SUMMARIZE_OUT" | jq -e \'{' in end_to_end_example
    assert 'tail -1 "$SUMMARIZE_OUT" | jq -e \'.persist\'' in end_to_end_example
    assert '\' "$SUMMARIZE_OUT"' not in end_to_end_example


def test_the_handles_worth_is_documented_as_present_on_every_marker() -> None:
    """`record` is total, so the docs must not present it as a failure-only key.

    Documented as something failures add, a reader takes its absence to mean
    success and switches on the wrong thing -- on the one path that matters,
    since the success marker is the one almost every run prints.
    """
    cli_reference = " ".join(CLI_REFERENCE.read_text().split())
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())

    assert "`record` is on every marker" in cli_reference
    assert '"record": "closed"' in cli_reference
    for worth in ("closed", "absent", "stale"):
        assert f"`{worth}`" in cli_reference, worth
    assert "on every marker, `record`" in summarize_skill


def test_a_summary_is_filed_under_a_sensor_not_a_stream() -> None:
    """`--video-id` is the sensor id, on both preparation paths.

    A VIOS upload answers with both a `sensorId` and a `streamId`, so the
    recipe reads the sensor one rather than resting on the two coinciding.
    A summary filed under a stream id is unreachable by `list --sensor-id`
    and by sensor-keyed recall -- persisted, but lost.
    """
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())

    assert "SENSOR_ID=$(jq -er '.sensorId' /tmp/vios-upload.json)" in end_to_end_example
    assert 'VIDEO_ID="$SENSOR_ID"' in end_to_end_example
    # The old fallback silently persisted a stream id whenever the upload path ran.
    assert "${SENSOR_ID:-$STREAM_ID}" not in end_to_end_example
    assert "do not persist under a stream id" in end_to_end_example
    assert "never the stream id" in summarize_skill


def test_the_media_start_is_never_a_constant_for_media_already_present() -> None:
    """`--creation-time` is the upload anchor, or VIOS's own timeline start.

    The upload path knows the timestamp it anchored the timeline to. A
    recording that was already there has its own start, and stamping it with
    the upload constant would turn every persisted event into a confidently
    wrong instant.
    """
    end_to_end_example = SUMMARIZE_REFERENCES[0].read_text()

    assert 'CREATION_TIME="${UPLOADED_AT:-$START_TIME}"' in end_to_end_example
    assert 'UPLOADED_AT="$UPLOAD_TIMESTAMP"' in end_to_end_example
    assert 'CREATION_TIME="${UPLOAD_TIMESTAMP:-' not in end_to_end_example


def test_the_skill_keeps_endpoint_resolution_out_of_the_summarize_request() -> None:
    """No endpoint flags: `vss configure` is the single source of the LVS origin."""
    summarize_skill = " ".join(SUMMARIZE_SKILL.read_text().split())

    assert (
        "Endpoints come from the deployment `vss configure` recorded" in summarize_skill
    )
    assert "Configure against the ingress origin, never `:38111`" in summarize_skill
    # The model came from LVS's own /models before; now it comes from the
    # recorded deployment, so the skill must not re-derive it.
    assert "The summarization model needs no discovery" in summarize_skill

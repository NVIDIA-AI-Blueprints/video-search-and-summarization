# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the vss-search-archive Harbor adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / ".github/skill-eval/adapters/vss-search-archive/generate.py"
SPEC_PATH = REPO_ROOT / "skills/operations/vss-search-archive/evals/search.json"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_search_archive_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _search_spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


def test_non_object_expect_is_rejected_as_validation_error() -> None:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["expects"][2] = "not-an-object"

    with pytest.raises(TypeError, match=r"spec\.expects\[3\] must be an object"):
        adapter._validate_spec(spec)


def test_verification_scenario_requires_ask_video_skill() -> None:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["skills"].remove("vss-ask-video")

    with pytest.raises(ValueError, match="requires vss-ask-video"):
        adapter._validate_spec(spec)


def test_saved_fixture_uuid_survives_same_origin_config_refresh(tmp_path: Path) -> None:
    adapter = _load_adapter()
    verifier = adapter.generate_test_script(2, "search.json", "ingest-search-fixtures")
    state_function = (
        "state_file() {"
        + verifier.split("state_file() {", 1)[1].split("\n}", 1)[0]
        + "\n}"
    )
    state_function = state_function.replace(
        "/tmp/skill-eval/fixture-state", str(tmp_path)
    )

    def state_path(base_url: str, written_at: str, run_id: str = "trial-1") -> Path:
        config = json.dumps({"base_url": base_url, "written_at": written_at})
        result = subprocess.run(
            ["bash", "-c", f"{state_function}\nstate_file"],
            env={
                **os.environ,
                "CONFIG_JSON": config,
                "GITHUB_RUN_ID": run_id,
                "EVAL_SLUG": "search-fixtures",
            },
            text=True,
            capture_output=True,
            check=True,
        )
        return Path(result.stdout.strip())

    saved_path = state_path("https://vss.example", "2026-10-05T12:00:00Z")
    saved_path.parent.mkdir(parents=True)
    saved_path.write_text("ladder-sensor-uuid\n")

    refreshed_path = state_path("https://vss.example", "2026-10-05T12:05:00Z")
    assert refreshed_path == saved_path
    assert refreshed_path.read_text() == "ladder-sensor-uuid\n"
    assert state_path("https://another.example", "2026-10-05T12:05:00Z") != saved_path
    assert (
        state_path("https://vss.example", "2026-10-05T12:05:00Z", "trial-2")
        != saved_path
    )


@pytest.mark.parametrize(
    ("assertion_name", "budget"),
    [("_INGEST_ASSERT", 900), ("_CLEANUP_ASSERT", 600)],
)
def test_fixture_listing_cannot_outlive_verifier_deadline(
    tmp_path: Path, assertion_name: str, budget: int
) -> None:
    adapter = _load_adapter()
    fake_vss = tmp_path / "vss"
    fake_vss.write_text("#!/bin/sh\nexec sleep 8\n")
    fake_vss.chmod(0o755)
    saved_uuid = tmp_path / "ladder.uuid"
    saved_uuid.write_text("ladder-sensor-uuid\n")
    assertion = getattr(adapter, assertion_name).replace(
        f"SECONDS + {budget}", "SECONDS + 1"
    )
    script = (
        adapter._ES_TUPLE_PROBE.split("CONFIG_JSON=$(vss configure show", 1)[0]
        + f"state_file() {{ printf '%s\\n' '{saved_uuid}'; }}\n"
        + assertion
    )

    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=4,
        check=False,
    )

    assert result.returncode == 1
    assert "vss vios list" in result.stderr


def test_generated_deployment_instruction_allows_host_local_origin(
    tmp_path: Path,
) -> None:
    root = _generated_dataset(tmp_path)
    instruction = (root / "step-1/instruction.md").read_text()
    assert "select_brev_origin.sh" in instruction
    assert "host-local" in instruction
    assert "media_scope" in instruction
    for forbidden in (
        "do not accept a host-local origin",
        "If any is missing, stop",
        "If any required link is missing, stop",
        "Never fabricate a host-local origin for a missing published link",
    ):
        assert forbidden not in instruction
    assert "7777" in instruction
    assert "brev.md#host-local-search-eval" in instruction
    assert "Wait for image pulls and bring-up to finish" in instruction
    assert "checking their exit status" in instruction
    assert "wait on that session before proceeding" in instruction
    assert "A pull still in progress does not complete this task" in instruction


def test_host_local_search_eval_bypasses_browser_only_brev_gates() -> None:
    references = REPO_ROOT / "skills/vss-build-vision-ai/references"
    prerequisites = (references / "prerequisites.md").read_text()
    brev = (references / "brev.md").read_text()
    assert "brev.md#host-local-search-eval" in prerequisites
    assert "For a browser-facing deployment" in prerequisites
    branch = brev.split('<a id="host-local-search-eval"></a>', 1)[1].split(
        "## Setup flow", 1
    )[0]
    assert "during preflight" in branch
    assert "skip the browser-only secure-link overrides" in branch
    assert "external browser verification" in branch
    assert "HOST_IP" in branch and "EXTERNAL_IP" in branch
    assert "composition.md" in branch and "readiness.md" in branch
    setup = brev.split("## Setup flow", 1)[1]
    assert "For an explicitly authorized host-local search eval" in setup
    assert "its missing-link stop does not apply to that branch" in setup


def test_eval_checks_grade_partial_results_and_original_verification_intent() -> None:
    steps = _search_spec()["expects"]
    for step in (steps[3], steps[9]):
        checks = " ".join(step["checks"])
        assert "exit 0 or exit 6" in checks
        assert "data" in checks
        assert "disclos" in checks
    fusion = " ".join(steps[4]["checks"])
    assert "--original-query" in fusion
    assert "exact original user sentence" in fusion
    assert "white-jacket and ladder-climbing intent" in fusion
    critic = " ".join(steps[3]["checks"])
    assert "If the current critic preflight succeeded" in critic
    assert "technical preflight failure may disable verification" in critic
    assert "disclosed from search_messages" in critic


def test_eval_verification_checks_allow_technical_failure_and_bound_repair() -> None:
    verification = _search_spec()["expects"][6]
    checks = " ".join(verification["checks"])
    assert "If a technical resolution failure occurred" in verification["checks"][0]
    assert "When timeline resolution succeeded" in verification["checks"][1]
    assert (
        "technical timeline/mapping/clip failure may stop resolution"
        in verification["checks"][1]
    )
    assert "technical clip-resolution failure may stop before a VLM request" in checks
    assert (
        "at most one additional request solely to repair malformed structured output"
        in checks
    )
    assert "must not cause alternate-endpoint or credential retries" in checks
    assert "never" in checks and "retried a valid semantic verdict" in checks
    assert "technical failure is reported as unavailable" in checks
    assert "never converted into an invented verdict" in checks


def test_eval_rtsp_query_and_cleanup_checks_match_agent_responsibility() -> None:
    steps = _search_spec()["expects"]
    assert "person walking through the gate" in steps[8]["query"]
    assert "vss search run embed" in " ".join(steps[8]["checks"])
    cleanup = " ".join(steps[10]["checks"])
    assert "without adding its own retry or timeout loop" in cleanup
    assert (
        "scripted verifier independently owns bounded index cleanup convergence"
        in cleanup
    )
    assert "successful vss vios list" in cleanup


def test_build_skill_routes_agent_backed_source_registration_to_vios() -> None:
    skill = (REPO_ROOT / "skills/vss-build-vision-ai/SKILL.md").read_text()
    assert "if it carries no in-stack agent" not in skill
    assert "register once with `vss vios add`" in skill
    assert "on Agent-backed and headless deployments" in skill
    assert (
        "Use `vss vios add` on both builds that reached Q3 and headless builds that skipped it"
        in skill
    )


def _generated_dataset(tmp_path: Path) -> Path:
    adapter = _load_adapter()
    spec = _search_spec()
    spec["_source_path"] = str(SPEC_PATH)
    skill = REPO_ROOT / "skills/operations/vss-search-archive"
    adapter.generate_task(
        "RTXPRO6000BW", "search", spec, tmp_path, skill, None, None, None
    )
    return tmp_path / "search/rtxpro6000bw"


def test_generated_instructions_do_not_supply_dispatcher_answers(
    tmp_path: Path,
) -> None:
    root = _generated_dataset(tmp_path)
    for number, expect in enumerate(_search_spec()["expects"], 1):
        if expect["role"] != "search":
            continue
        instruction = (root / f"step-{number}/instruction.md").read_text()
        for answer in (
            "run embed",
            "run fusion",
            "run attribute",
            "run object",
            "run tag",
            "--video-source",
            "--source-type",
            "family wildcard minus",
            "exits 4",
        ):
            assert answer not in instruction, (number, answer)
        assert expect["query"] in instruction


@pytest.mark.parametrize(
    ("scenario", "mode", "succeeds"),
    [
        ("ingest-search-fixtures", "success", True),
        ("ingest-search-fixtures", "converge", True),
        ("ingest-search-fixtures", "wrong-id", False),
        ("ingest-search-fixtures", "missing-id", False),
        ("ingest-search-fixtures", "nonconvergence", False),
        ("ingest-search-fixtures", "backend-error", False),
        ("ingest-search-fixtures", "list-error", False),
        ("ingest-search-fixtures", "missing-raw", False),
        ("delete-search-fixture", "success", True),
        ("delete-search-fixture", "converge", True),
        ("delete-search-fixture", "source-present", False),
        ("delete-search-fixture", "nonconvergence", False),
        ("delete-search-fixture", "backend-error", False),
        ("delete-search-fixture", "list-error", False),
    ],
)
def test_generated_fixture_scripts_gate_the_judge(
    tmp_path: Path,
    scenario: str,
    mode: str,
    succeeds: bool,
) -> None:
    """Execute the generated verifier, including source resolution and all tuples."""
    root = _generated_dataset(tmp_path / "dataset")
    number = 2 if scenario == "ingest-search-fixtures" else 11
    tests = root / f"step-{number}/tests"
    script = tests / "test.sh"
    state_root = tmp_path / "fixture-state"
    script.write_text(
        script.read_text()
        .replace("/tmp/skill-eval/fixture-state", str(state_root))
        .replace("SECONDS + 900", "SECONDS + 2")
        .replace("SECONDS + 600", "SECONDS + 2")
    )
    state_root.mkdir()
    # Compute the same persisted state key used by setup; cleanup must read it.
    import hashlib

    key = hashlib.sha256(b"https://vss.example\0test-run\0test-leg").hexdigest()
    (state_root / key).mkdir()
    (state_root / key / "ladder.uuid").write_text("ladder-uuid\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = """#!/usr/bin/python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
mode = os.environ['MODE']
scenario = os.environ['SCENARIO']
if Path(sys.argv[0]).name == 'vss':
    if args == ['configure', 'show']:
        indices = ['mdx-embed-filtered-2025-01-01', 'mdx-behavior-2025-01-01']
        if mode != 'missing-raw': indices += ['mdx-raw-2025-01-01']
        print(json.dumps({'base_url': 'https://vss.example', 'services': {'elasticsearch': {'url': 'https://es.example', 'indices': indices}}}))
    elif args[:2] == ['configure', '--base-url']:
        Path(os.environ['REFRESH_MARKER']).touch()
    elif args == ['vios', 'list']:
        if mode == 'list-error': sys.exit(3)
        sensors = []
        if scenario == 'ingest-search-fixtures' or mode == 'source-present':
            for name, uid in [('warehouse_sample', 'sample-uuid'), ('warehouse-ladder', 'ladder-uuid')]:
                sensor = {'name': name}
                if mode != 'missing-id': sensor['sensor_id'] = 'wrong-uuid' if mode == 'wrong-id' else uid
                sensors.append(sensor)
        print(json.dumps({'sensors': sensors}))
    else: sys.exit(2)
else:
    if mode == 'backend-error': sys.exit(7)
    url = next(a for a in args if a.startswith('https://'))
    term = json.loads(args[args.index('-d') + 1])['query']['term']
    index = url.split('/')[-2]
    field, value = next(iter(term.items()))
    valid = ((index.startswith('mdx-embed') and field == 'sensor.id.keyword' and value in ['sample-uuid', 'ladder-uuid'])
        or (index.startswith('mdx-behavior') and field == 'sensor.id.keyword' and value == 'warehouse-ladder')
        or (index.startswith('mdx-raw') and field == 'sensorId.keyword' and value == 'warehouse-ladder'))
    if not valid: sys.exit(22)
    counter = Path(os.environ['COUNT_FILE'])
    calls = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(calls + 1))
    ready = mode != 'nonconvergence' and (mode != 'converge' or calls >= 1)
    count = int(ready) if scenario == 'ingest-search-fixtures' else int(not ready)
    print(json.dumps({'count': count}))
"""
    for name in ("vss", "curl"):
        executable = bin_dir / name
        executable.write_text(stub)
        executable.chmod(0o755)
    real_python = sys.executable
    python = bin_dir / "python3"
    python.write_text(
        f'#!/bin/sh\nif [ "$1" = -m ]; then exit 0; fi\nexec "{real_python}" "$@"\n'
    )
    python.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\n/bin/sleep 0.2\n")
    sleep.chmod(0o755)
    marker = tmp_path / "judge-reached"
    refresh_marker = tmp_path / "configure-refreshed"
    (tests / "generic_judge.py").write_text(
        'import os\nfrom pathlib import Path\nPath(os.environ["JUDGE_MARKER"]).touch()\n'
    )
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "MODE": mode,
            "SCENARIO": scenario,
            "GITHUB_RUN_ID": "test-run",
            "EVAL_SLUG": "test-leg",
            "JUDGE_MARKER": str(marker),
            "COUNT_FILE": str(tmp_path / "counts"),
            "REFRESH_MARKER": str(refresh_marker),
        },
    )
    assert (result.returncode == 0) is succeeds, result.stderr
    assert marker.exists() is succeeds
    if scenario == "ingest-search-fixtures" and succeeds:
        assert refresh_marker.exists()
        assert (state_root / key / "ladder.uuid").read_text() == "ladder-uuid\n"

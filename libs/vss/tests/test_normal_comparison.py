# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Independent wire faults, schema attacks and real sibling-client HTTP checks."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.normal import readiness, summary
from benchmarks.normal_adapters import source_identity
from benchmarks.normal_fixture import wire_valid
from benchmarks.normal_results import NormalConfiguration, NormalResult, NormalTrial


def wire(records, *, model="fixture", done=True, asset="asset"):
    data = b"".join(
        b"data: "
        + json.dumps({"id": asset, "model": model, "chunk_responses": [p]}).encode()
        + b"\n\n"
        for p in records
    )
    return data + (b"data: [DONE]\n\n" if done else b"")


@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "missing_done",
        "duplicate",
        "wrong_model",
        "wrong_asset",
        "truncated",
        "after_done",
        "oversize",
    ],
)
def test_independent_wire_gate_rejects_fault_even_when_client_might_accept(fault):
    expected = (
        {"chunk_id": 0, "content": "first"},
        {"chunk_id": 1, "content": "second"},
    )
    data = wire(expected)
    if fault == "malformed":
        data = b"data: invalid json\n\n" + data
    elif fault == "missing_done":
        data = wire(expected, done=False)
    elif fault == "duplicate":
        data = wire((expected[0],) + expected)
    elif fault == "wrong_model":
        data = wire(expected, model="different")
    elif fault == "wrong_asset":
        data = wire(expected, asset="different")
    elif fault == "truncated":
        data = wire(expected[:1])
    elif fault == "after_done":
        data += wire(expected[:1])
    else:
        data = b"x" * 65537
    assert not wire_valid(data, expected, "fixture", "asset")[0]
    assert wire_valid(wire(expected), expected, "fixture", "asset") == (True, 2, True)


def test_empty_and_failed_run_has_no_survivor_statistics():
    result = NormalResult(
        status="incomplete", failure="timeout", configuration=NormalConfiguration()
    )
    assert summary(result)["statistics"] is None
    with pytest.raises(ValidationError):
        NormalResult.model_validate(
            result.model_dump() | {"status": "ok", "failure": None}
        )
    with pytest.raises(ValidationError):
        NormalResult.model_validate(result.model_dump() | {"acceptance_eligible": True})
    with pytest.raises(ValidationError):
        NormalResult.model_validate(result.model_dump() | {"coverage": ()})


def test_preflight_redacts_configured_endpoints(tmp_path, monkeypatch):
    secret = "https://secret-token@private-service.invalid"
    monkeypatch.setenv("M8_VSS_BASE_URL", secret)
    evidence = readiness(tmp_path)
    assert evidence["configured"]["M8_VSS_BASE_URL"]
    assert secret not in json.dumps(evidence)
    assert evidence["live_execution_qualified"] is False


def test_source_symlink_escape_rejected(tmp_path):
    root = tmp_path / "services/video-summarization"
    root.mkdir(parents=True)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    try:
        try:
            (root / "src").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("test symlinks unavailable")
        with pytest.raises(ValueError, match="root escapes"):
            source_identity(tmp_path)
    finally:
        outside.rmdir()


@pytest.mark.skipif(
    os.getenv("RUN_VSS_NORMAL_TESTS") != "1" or os.name != "posix",
    reason="opted-in native media/real upstream/loopback profile",
)
def test_actual_upstream_and_library_pairs_and_evidence_attacks(tmp_path):
    output = tmp_path / "normal.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.normal",
        "--repetitions",
        "2",
        "--warmups",
        "1",
        "--output",
        str(output),
    ]
    child = subprocess.run(command, capture_output=True, timeout=60)
    assert child.returncode == 0, (child.stdout, child.stderr)
    result = NormalResult.model_validate_json(output.read_bytes())
    assert len({t.process_id for t in result.trials}) == 2
    assert all(
        t.clients_closed and t.server_closed and t.upstream for t in result.trials
    )
    assert summary(result)["paired_relative_library_change"]["interval"] is None
    trial = result.trials[0]
    for change in [
        {"clients_closed": False},
        {"server_closed": False},
        {"periods": trial.periods[:1]},
        {"expected": ()},
        {"resources": trial.resources.model_dump() | {"complete": False}},
        {"media": trial.media.model_dump() | {"sha256": "0" * 64}},
    ]:
        with pytest.raises(ValidationError):
            NormalTrial.model_validate(trial.model_dump() | change)
    modified = result.model_dump()
    modified["configuration"]["seed"] += 1
    with pytest.raises(ValidationError):
        NormalResult.model_validate(modified)
    original = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 2
    assert output.read_bytes() == original

    # Preserve a valid earlier pair after the next worker times out. The fixed
    # sample must never shrink into statistics on the surviving pair.
    from unittest.mock import patch

    from benchmarks import normal

    partial = tmp_path / "partial.json"
    replies = [trial.model_dump_json().encode(), subprocess.TimeoutExpired("worker", 1)]
    with (
        patch.object(normal, "isolated_trial", side_effect=replies),
        patch.object(
            sys,
            "argv",
            [
                "normal",
                "--repetitions",
                "2",
                "--warmups",
                "1",
                "--output",
                str(partial),
            ],
        ),
    ):
        assert normal.main() == 3
    retained = NormalResult.model_validate_json(partial.read_bytes())
    assert retained.failure == "timeout" and retained.trials == (trial,)
    assert summary(retained)["statistics"] is None

    for period in trial.periods:
        for change in [
            {"valid": False},
            {"done": False},
            {"sent_complete": False},
            {"deleted": False},
            {"residual_uploads": 1},
            {"settings_sha256": "0" * 64},
            {"upload_sha256": "0" * 64},
            {"chunks": ()},
        ]:
            changed = period.model_dump() | {"wire": period.wire.model_dump() | change}
            data = trial.model_dump() | {
                "periods": [
                    changed if p.arm == period.arm else p.model_dump()
                    for p in trial.periods
                ]
            }
            with pytest.raises(ValidationError):
                NormalTrial.model_validate(data)

    # A successful fixed-N run cannot mix actual upstream, lock, dependency or
    # native-oracle versions. These changes leave each individual trial valid.
    for change in [
        {"upstream": trial.upstream.model_dump() | {"revision": "a" * 40}},
        {"lockfile_sha256": "a" * 64},
        {
            "environment": trial.environment.model_dump()
            | {"dependencies": {"httpx": "different"}}
        },
        {"native_builds": trial.native_builds | {"ffprobe_build_sha256": "a" * 64}},
    ]:
        modified = result.model_dump()
        modified["trials"][1].update(change)
        with pytest.raises(ValidationError, match="matched fixed pairs"):
            NormalResult.model_validate(modified)


@pytest.mark.skipif(
    os.getenv("RUN_VSS_NORMAL_TESTS") != "1" or os.name != "posix",
    reason="opted-in real upstream/HTTP protocol faults",
)
@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "missing_done",
        "duplicate",
        "wrong_model",
        "wrong_content",
        "delete_failure",
        "delete_conflict",
        "truncated",
    ],
)
def test_real_http_faults_cannot_produce_comparison_statistics(tmp_path, fault):
    output = tmp_path / (fault + ".json")
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.normal",
            "--repetitions",
            "1",
            "--warmups",
            "0",
            "--fault",
            fault,
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=60,
    )
    assert child.returncode == 3, (child.stdout, child.stderr)
    result = NormalResult.model_validate_json(output.read_bytes())
    assert result.status == "incomplete" and summary(result)["statistics"] is None
    assert len(result.trials[0].periods) == 2
    assert result.trials[0].clients_closed and result.trials[0].server_closed


@pytest.mark.skipif(
    os.getenv("RUN_VSS_NORMAL_TESTS") != "1",
    reason="real requests session/upstream modules",
)
def test_session_policy_applies_before_constructor_and_restores_on_failure(monkeypatch):
    import requests

    from benchmarks.normal_adapters import legacy_client

    factory = requests.Session
    old_env = os.environ.get("RTVI_VLM_URL")
    old_path = list(sys.path)
    calls = []

    def failed_get(session, *args, **kwargs):
        calls.append(session.trust_env)
        raise requests.ConnectionError("controlled failure")

    monkeypatch.setattr(requests.sessions.Session, "get", failed_get)
    checkout = Path(__file__).resolve().parents[2] / "video-search-and-summarization"
    with pytest.raises(ConnectionError):
        with legacy_client(checkout, "http://127.0.0.1:1"):
            pass
    assert calls == [False] and requests.Session is factory
    assert os.environ.get("RTVI_VLM_URL") == old_env and sys.path == old_path


@pytest.mark.parametrize("terminal", ["cancelled", "invalid_child"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX normal controller runtime")
def test_parent_failure_records_incomplete_without_contacting_service(
    tmp_path, monkeypatch, terminal
):
    from benchmarks import normal

    def fail(*args, **kwargs):
        if terminal == "cancelled":
            raise KeyboardInterrupt
        return b"malformed child evidence"

    output = tmp_path / (terminal + ".json")
    monkeypatch.setattr(normal, "isolated_trial", fail)
    monkeypatch.setattr(sys, "argv", ["normal", "--output", str(output)])
    assert normal.main() == 3
    result = NormalResult.model_validate_json(output.read_bytes())
    assert result.failure == terminal and not result.trials
    assert summary(result)["statistics"] is None


def test_configuration_and_source_inventory_cannot_be_weakened():
    from benchmarks.normal_results import SourceIdentity

    for change in [
        {"repetitions": 0},
        {"concurrency": 2},
        {"frames_per_chunk": 4},
        {"fixture_delay_ms": 101},
        {"timeout_s": float("nan")},
        {"transport_policy": "different.v2"},
    ]:
        with pytest.raises(ValidationError):
            NormalConfiguration(**change)
    with pytest.raises(ValidationError):
        SourceIdentity(revision="a" * 40, files={"rtvi_vlm_client.py": "b" * 64})


def test_wire_line_and_chunk_caps_independently_of_total_bytes():
    assert not wire_valid(b"data: " + b"x" * 8193 + b"\n\n", (), "fixture", "asset")[0]
    record = {"chunk_id": 0}
    assert len(wire((record,) * 65)) < 65536
    assert not wire_valid(wire((record,) * 65), (record,) * 65, "fixture", "asset")[0]


async def test_missing_sampler_dependency_is_typed_unsupported(monkeypatch):
    from benchmarks import normal
    from benchmarks.normal_adapters import SOURCE_FILES
    from benchmarks.normal_results import SourceIdentity

    original = normal.importlib.metadata.version

    def version(name):
        if name == "psutil":
            raise normal.importlib.metadata.PackageNotFoundError(name)
        if name in {"requests", "urllib3", "httpx-sse"}:
            return "fixture"
        return original(name)

    monkeypatch.setattr(normal.importlib.metadata, "version", version)
    original_spec = normal.importlib.util.find_spec
    monkeypatch.setattr(
        normal.importlib.util,
        "find_spec",
        lambda name: object() if name == "psutil" else original_spec(name),
    )
    monkeypatch.setattr(normal, "preflight", lambda: ({}, {}))
    monkeypatch.setattr(
        normal,
        "source_identity",
        lambda path: SourceIdentity(
            revision="a" * 40, files={name: "b" * 64 for name in SOURCE_FILES}
        ),
    )
    trial = await normal.worker(
        NormalConfiguration(), Path.cwd(), None, "disabled_first"
    )
    assert trial.status == "unsupported" and trial.failure == "prerequisite"


@pytest.mark.skipif(
    os.getenv("RUN_VSS_NORMAL_TESTS") != "1", reason="real loopback admission checks"
)
def test_http_rejects_wrong_media_and_settings_and_preserves_owned_cleanup(tmp_path):
    import httpx

    from benchmarks.media import generated, inspect
    from benchmarks.normal_fixture import Fixture, settings
    from benchmarks.processing import preflight

    tools, _ = preflight()
    source = generated(tmp_path, "tiny", tools["ffmpeg"])
    media, _ = inspect(source, tools)
    config = NormalConfiguration()
    fixture = Fixture(source, media, config)
    with (
        fixture,
        httpx.Client(base_url=fixture.url, trust_env=False, timeout=5) as client,
    ):
        wrong = client.post(
            "/v1/files",
            data={"purpose": "vision", "media_type": "video"},
            files={"file": ("input.mp4", b"wrong media", "video/mp4")},
        )
        assert wrong.status_code == 422 and not fixture.uploads
        good = client.post(
            "/v1/files",
            data={"purpose": "vision", "media_type": "video"},
            files={"file": ("input.mp4", source.read_bytes(), "video/mp4")},
        )
        asset = good.json()["id"]
        wrong = client.post(
            "/v1/generate_captions",
            headers={"x-stream-id": asset},
            json=settings(config) | {"id": asset, "chunk_duration": 15},
        )
        assert wrong.status_code == 422 and fixture.witness(asset) is None
        assert len(fixture.uploads) == 1
        assert client.delete("/v1/files/" + asset).status_code == 200
        assert not fixture.uploads
    assert fixture.closed


@pytest.mark.skipif(
    os.getenv("RUN_VSS_NORMAL_TESTS") != "1", reason="actual worker fault injection"
)
@pytest.mark.parametrize("fault", ["source_mutation", "resource_loss"])
async def test_worker_rejects_mutated_source_and_failed_sampler(
    tmp_path, monkeypatch, fault
):
    from benchmarks import normal
    from benchmarks.normal_adapters import SOURCE_FILES
    from benchmarks.resources import ResourceSampler

    checkout = Path(__file__).resolve().parents[2] / "video-search-and-summarization"
    if fault == "source_mutation":
        # Mutate an owned copy of the actual upstream source during a real paired
        # request. The sibling checkout is read-only throughout the test.
        root = tmp_path / "upstream"
        relative = Path("services/video-summarization/src")
        for name in SOURCE_FILES:
            target = root / relative / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkout / relative / name, target)
        for command in [
            ["git", "init", "-q"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=M8 fixture",
                "-c",
                "user.email=m8@example.invalid",
                "commit",
                "-qm",
                "Fixture source",
            ],
        ]:
            subprocess.run(
                command, cwd=root, capture_output=True, check=True, timeout=10
            )
        checkout = root
        original = normal.library_arm

        async def mutate(*args, **kwargs):
            period = await original(*args, **kwargs)
            with (root / relative / "rtvi_vlm_client.py").open("a") as out:
                out.write("\n# Controlled source mutation after measured request.\n")
            return period

        monkeypatch.setattr(normal, "library_arm", mutate)
    else:

        def failed_sample(sampler):
            sampler.complete = False

        monkeypatch.setattr(ResourceSampler, "_sample", failed_sample)
    trial = await normal.worker(
        NormalConfiguration(repetitions=1, warmups=0), checkout, None, "disabled_first"
    )
    assert trial.status == "error"
    assert trial.failure == (
        "source_mutation" if fault == "source_mutation" else "evidence"
    )
    assert trial.clients_closed and trial.server_closed and len(trial.periods) == 2
    if fault == "resource_loss":
        assert not trial.resources.complete and not trial.resources.samples

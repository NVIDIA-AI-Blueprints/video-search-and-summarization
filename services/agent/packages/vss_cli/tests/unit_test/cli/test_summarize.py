# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Contract tests for the focused ``vss summarize`` command group."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli.exits import Exit
from vss_cli.summarize_group import LvsSummaryRunner
from vss_cli.summarize_group import SummarizeGroup
from vss_cli.summarize_group import SummarizeInput
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.service import MemoryService

if TYPE_CHECKING:
    from vss_core.memory.models import UnifiedMemoryRecord

BASE_URL = "http://vss.example:7777"


class _TrackingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[str] = []

    def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        self.statuses.append(record.job.status)
        return super().upsert(record)


@pytest.fixture(autouse=True)
def cli_memory(monkeypatch: pytest.MonkeyPatch) -> memory_mod.Memory:
    memory = memory_mod.Memory(MemoryService(_TrackingStore()), index="test-memory")
    monkeypatch.setattr(memory_mod, "build", lambda *_args, **_kwargs: memory)
    return memory


class _FakeRunner:
    def __init__(self) -> None:
        self.requests: list[SummarizeInput] = []

    def summarize(self, request: SummarizeInput) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "id": "completion-1",
            "choices": [{"message": {"content": "A forklift crosses the aisle."}}],
        }


class _Response:
    def __init__(self, payload: Any = None, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload if payload is not None else {"id": "completion-1"}
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _deployment(*, models: list[str] | None = None) -> config_mod.Deployment:
    return config_mod.Deployment(
        base_url=BASE_URL,
        services={
            "agent": config_mod.Service(url=f"{BASE_URL}/api"),
            "rt_vlm": config_mod.Service(
                url=f"{BASE_URL}/rtvi-vlm",
                models=models if models is not None else ["cosmos-reason"],
            ),
        },
    )


def _group(fake: _FakeRunner | None = None) -> tuple[SummarizeGroup, _FakeRunner]:
    runner = fake or _FakeRunner()
    return SummarizeGroup(runner_factory=lambda _ctx: runner), runner


def _run(group: SummarizeGroup, *argv: str) -> Any:
    return CliRunner().invoke(group.cli(), ["run", *argv])


def test_group_exposes_fixed_job_verbs() -> None:
    group, _ = _group()
    assert {"run", "status", "get", "list"} <= set(group.cli().commands)


def test_run_help_uses_model_fields() -> None:
    group, _ = _group()
    result = CliRunner().invoke(group.cli(), ["run", "--help"])
    assert result.exit_code == 0
    assert {"--id", "--url", "--scenario", "--event", "--chunk-duration", "--prompt", "--model"} <= {
        option
        for parameter in group.cli().commands["run"].params
        for option in (*parameter.opts, *parameter.secondary_opts)
    }


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--scenario", "warehouse"], id="no-source"),
        pytest.param(
            ["--id", "video-1", "--url", "https://example.com/video.mp4", "--scenario", "warehouse"],
            id="two-sources",
        ),
    ],
)
def test_exactly_one_source_is_required(argv: list[str]) -> None:
    group, runner = _group()
    result = _run(group, *argv)
    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "exactly one of id or url" in result.output
    assert runner.requests == []


def test_scenario_is_required() -> None:
    group, runner = _group()
    result = _run(group, "--id", "video-1")
    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "scenario" in result.output
    assert runner.requests == []


def test_unknown_option_is_exit_two() -> None:
    group, runner = _group()
    result = _run(group, "--id", "video-1", "--scenario", "warehouse", "--scenerio", "typo")
    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "No such option" in result.output
    assert runner.requests == []


def test_validated_request_is_delegated_to_runner() -> None:
    group, runner = _group()
    result = _run(
        group,
        "--id",
        "video-1",
        "--scenario",
        "warehouse",
        "--event",
        "safety violation",
        "--event",
        "forklift stopped",
        "--prompt",
        "Summarize notable activity",
    )
    assert result.exit_code == 0, result.output
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.id == "video-1"
    assert request.events == ["safety violation", "forklift stopped"]
    assert request.chunk_duration == 10
    assert request.prompt == "Summarize notable activity"


def test_chunk_duration_can_be_overridden() -> None:
    group, runner = _group()
    result = _run(
        group,
        "--id",
        "video-1",
        "--scenario",
        "warehouse",
        "--chunk-duration",
        "30",
    )
    assert result.exit_code == 0, result.output
    assert runner.requests[0].chunk_duration == 30


def test_success_output_contains_job_id_and_summary() -> None:
    group, _ = _group()
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["job_id"].startswith("summarize-")
    assert body["summary"]["id"] == "completion-1"


def test_run_persists_running_then_completed_with_nested_summary(cli_memory: memory_mod.Memory) -> None:
    group, _ = _group()
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == 0, result.output

    job_id = json.loads(result.output)["job_id"]
    record = cli_memory.service.get(job_id, reconcile=False)
    store = cli_memory.service.store
    assert isinstance(store, _TrackingStore)
    assert store.statuses == ["running", "completed"]
    assert record.job.job_id == job_id
    assert record.job.group == "summary"
    assert record.job.status == "completed"
    assert record.output.answer == "A forklift crosses the aisle."
    assert record.output.ext["summary"] == {
        "schema": "nv.vss.summary/1.0",
        "summary_id": "completion-1",
        "events": [],
        "total_events": 0,
        "metadata": {"completion_id": "completion-1"},
    }


def test_structured_lvs_events_are_preserved_inside_summary_extension(cli_memory: memory_mod.Memory) -> None:
    event = {
        "id": 1,
        "start_time": 0.0,
        "end_time": 10.0,
        "type": "search",
        "description": "An officer searches a vehicle.",
    }

    class _StructuredRunner:
        def summarize(self, request: SummarizeInput) -> dict[str, Any]:
            return {
                "id": "completion-structured",
                "video_id": request.id,
                "model": "cosmos-reason",
                "usage": {"total_chunks_processed": 1},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "video_summary": "An officer searches a vehicle.",
                                    "events": [event],
                                    "total_events": 1,
                                }
                            )
                        }
                    }
                ],
            }

    group = SummarizeGroup(runner_factory=lambda _ctx: _StructuredRunner())
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    record = cli_memory.service.get(json.loads(result.output)["job_id"])
    summary = record.output.ext["summary"]
    assert summary["schema"] == "nv.vss.summary/1.0"
    assert summary["events"] == [event]
    assert summary["total_events"] == 1
    assert summary["metadata"]["usage"] == {"total_chunks_processed": 1}


def test_failed_runner_is_persisted_before_cli_error(cli_memory: memory_mod.Memory) -> None:
    class _FailingRunner:
        def summarize(self, request: SummarizeInput) -> dict[str, Any]:
            raise BackendUnreachableError("lvs", f"failed for {request.id}")

    group = SummarizeGroup(runner_factory=lambda _ctx: _FailingRunner())
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE)

    store = cli_memory.service.store
    assert isinstance(store, _TrackingStore)
    assert store.statuses == ["running", "failed"]
    job_id = store.upsert_ids[0]
    record = cli_memory.service.get(job_id, reconcile=False)
    assert record.job.status == "failed"
    assert record.error is not None
    assert record.error.code == "BackendUnreachableError"


def test_unavailable_memory_fails_before_lvs(monkeypatch: pytest.MonkeyPatch) -> None:
    group, runner = _group()

    def unavailable(*_args: Any, **_kwargs: Any) -> memory_mod.Memory:
        raise memory_mod.MemoryUnavailable("memory unavailable")

    monkeypatch.setattr(memory_mod, "build", unavailable)
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert runner.requests == []


def test_get_status_and_list_read_the_persisted_summary(cli_memory: memory_mod.Memory) -> None:
    group, _ = _group()
    run_result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    job_id = json.loads(run_result.output)["job_id"]

    get_result = CliRunner().invoke(group.cli(), ["get", "--job-id", job_id])
    status_result = CliRunner().invoke(group.cli(), ["status", "--job-id", job_id])
    list_result = CliRunner().invoke(group.cli(), ["list", "--sensor-id", "video-1"])

    assert get_result.exit_code == 0, get_result.output
    assert status_result.exit_code == 0, status_result.output
    assert list_result.exit_code == 0, list_result.output
    assert json.loads(get_result.output)["job"]["job_id"] == job_id
    assert json.loads(status_result.output)["job"]["status"] == "completed"
    listed = json.loads(list_result.output)
    assert [record["job"]["job_id"] for record in listed] == [job_id]
    assert cli_memory.service.get(job_id).output.ext["summary"]["schema"] == "nv.vss.summary/1.0"


def test_runner_uses_configured_route_model_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Response:
        seen.update(url=url, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    runner = LvsSummaryRunner(_deployment())
    completion = runner.summarize(
        SummarizeInput(
            id="video-1",
            scenario="warehouse",
            events=[],
            prompt="Summarize activity",
        )
    )
    assert completion == {"id": "completion-1"}
    assert seen["url"] == f"{BASE_URL}/api/v1/summarize"
    assert seen["json"] == {
        "id": "video-1",
        "scenario": "warehouse",
        "events": [],
        "chunk_duration": 10,
        "prompt": "Summarize activity",
        "model": "cosmos-reason",
    }


def test_explicit_model_overrides_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Response:
        seen["json"] = json
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    LvsSummaryRunner(_deployment()).summarize(SummarizeInput(id="video-1", scenario="warehouse", model="other-model"))
    assert seen["json"]["model"] == "other-model"


def test_http_client_never_runs_when_fake_runner_is_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_post(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected network call: {args!r} {kwargs!r}")

    monkeypatch.setattr(httpx, "post", unexpected_post)
    group, _ = _group()
    assert _run(group, "--id", "video-1", "--scenario", "warehouse").exit_code == 0


@pytest.mark.parametrize(
    ("response", "expected_exit"),
    [
        pytest.param(_Response(status_code=422, text="bad request"), Exit.INVALID_INPUT, id="rejected"),
        pytest.param(_Response(status_code=503), Exit.BACKEND_UNREACHABLE, id="server-error"),
        pytest.param(_Response(ValueError("not json")), Exit.BACKEND_UNREACHABLE, id="invalid-json"),
    ],
)
def test_runner_failures_use_cli_exit_semantics(
    monkeypatch: pytest.MonkeyPatch,
    response: _Response,
    expected_exit: Exit,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: response)
    group = SummarizeGroup(runner_factory=lambda _ctx: LvsSummaryRunner(_deployment()))
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == int(expected_exit), result.output


def test_transport_failure_is_backend_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fail)
    group = SummarizeGroup(runner_factory=lambda _ctx: LvsSummaryRunner(_deployment()))
    result = _run(group, "--id", "video-1", "--scenario", "warehouse")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE), result.output


def test_missing_default_model_is_configuration_error() -> None:
    runner = LvsSummaryRunner(_deployment(models=[]))
    with pytest.raises(config_mod.ConfigError, match="reports no RT-VLM model"):
        runner.summarize(SummarizeInput(id="video-1", scenario="warehouse"))


def test_input_rejects_unknown_programmatic_fields() -> None:
    with pytest.raises(ValueError, match="scenerio"):
        SummarizeInput(id="video-1", scenario="warehouse", scenerio="typo")

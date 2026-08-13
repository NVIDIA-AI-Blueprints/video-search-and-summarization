# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for ``vss summarize``.

The group is a thin client over the LVS REST API, so what is worth pinning is
not the summarization but the job shape around it: where the request is sent
(the recorded deployment, never a flag), what reaches the VLM, and how a
half-succeeded job reports itself.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli import summarize_group
from vss_cli.exits import Exit
from vss_cli.summarize_group import SUMMARIZE
from vss_cli.summarize_group import SummarizeInput
from vss_cli.summarize_group import SummarizeOptions
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory import InMemoryStore
from vss_core.memory import MemoryService

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://h:7777"


# --------------------------------------------------------------------------
# fixtures and doubles
# --------------------------------------------------------------------------


def _deployment(
    *,
    lvs_models: list[str] | None = None,
    vlm_models: list[str] | None = None,
    services: dict[str, Any] | None = None,
) -> config_mod.Deployment:
    """A deployment exposing everything summarize touches."""
    recorded = {
        "lvs": config_mod.Service(
            url=f"{BASE_URL}/lvs", models=lvs_models if lvs_models is not None else ["cosmos-reason"]
        ),
        "elasticsearch": config_mod.Service(url=f"{BASE_URL}/elasticsearch"),
        "rt_embed": config_mod.Service(url=f"{BASE_URL}/rtvi-embed", models=["bge-base-en-v1.5"]),
        "rt_vlm": config_mod.Service(
            url=f"{BASE_URL}/rtvi-vlm", models=vlm_models if vlm_models is not None else ["cosmos-reason"]
        ),
    }
    if services is not None:
        recorded = services
    return config_mod.Deployment(base_url=BASE_URL, services=recorded)


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config_mod.Deployment:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    deployment = _deployment()
    config_mod.save(deployment)
    return deployment


def _completion(content: Any = "a forklift crosses the aisle") -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": "cmpl-1",
        "created": 1_700_000_000,
        "model": "cosmos-reason",
        "choices": [{"message": {"content": text}}],
    }


class _Response:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str = "") -> None:
        self._payload = payload if payload is not None else _completion()
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return self._payload


def _capture_post(monkeypatch: pytest.MonkeyPatch, response: Any = None) -> dict[str, Any]:
    """Intercept the one HTTP call the group makes, recording what it sent."""
    seen: dict[str, Any] = {}

    def fake_post(url: str, json: Any = None, timeout: float | None = None) -> Any:
        seen.update(url=url, json=json, timeout=timeout)
        if isinstance(response, Exception):
            raise response
        return response if response is not None else _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


class _Store(InMemoryStore):
    """An in-process store that also remembers the lifecycle it was told.

    Which statuses were written, in order, is the part of persistence worth
    pinning: the record has to exist before the summarization does, or exit 7
    has nothing to resume from.
    """

    def __init__(self, *, fail_on: str | None = None) -> None:
        super().__init__()
        self.statuses: list[str] = []
        self._fail_on = fail_on

    def upsert(self, record: Any) -> Any:
        self.statuses.append(record.job.status)
        if record.job.status == self._fail_on:
            raise BackendUnreachableError("elasticsearch", "index is read-only")
        return super().upsert(record)


class _RefusingStore(InMemoryStore):
    """A store that rejects writes the way a read-only ingress does.

    Not a ``BackendUnreachableError``: Elasticsearch is reachable and answers,
    it just answers 405, which the client raises as ``ApiError``.
    """

    def upsert(self, record: Any) -> Any:
        from elasticsearch import ApiError

        raise ApiError("index is read-only", meta=SimpleNamespace(status=405), body=None)


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> memory_mod.Memory:
    """Run the real memory module against a store in this process.

    The adapters, the schema and the lifecycle are exercised for real -- only
    Elasticsearch is swapped out -- so a record that fails validation here
    would fail against a live index too.
    """
    return _memory(monkeypatch, _Store())


def _memory(monkeypatch: pytest.MonkeyPatch, store: InMemoryStore) -> memory_mod.Memory:
    built = memory_mod.Memory(MemoryService(store), index="vss-memory-test")
    monkeypatch.setattr(memory_mod, "build", lambda *_args, **_kwargs: built)
    return built


def _store(memory: memory_mod.Memory) -> _Store:
    store = memory.service.store
    assert isinstance(store, _Store)
    return store


def _persisted(memory: memory_mod.Memory) -> dict[str, Any]:
    """The one record the run wrote, as it would come back from `get`."""
    records = memory.service.list_jobs()
    assert len(records) == 1, records
    return records[0].model_dump_memory()


#: LVS requires model, scenario and events on every request. The model is
#: defaulted from the recorded deployment; the other two can only come from
#: the caller, so every invocation carries them. Tests that are not about
#: steering take these, and the ones that are pass their own.
_STEERING = ("--scenario", "warehouse monitoring", "--event", "forklift")


def _steered(argv: tuple[str, ...]) -> list[str]:
    return list(argv) if "--scenario" in argv else [*_STEERING, *argv]


def _run(*argv: str) -> Any:
    return CliRunner().invoke(SUMMARIZE.cli(), ["run", *_steered(argv)])


def _run_via_root(*argv: str) -> int:
    """Invoke through the root dispatcher.

    Two things only exist end to end: the ``vss.commands`` entry point that
    makes the group reachable at all, and the root's ConfigError -> exit 4
    mapping, which every group inherits rather than restating.
    """
    from vss_cli import main

    return main(["summarize", "run", *_steered(argv)])


# --------------------------------------------------------------------------
# surface: what the port moved off the command line
# --------------------------------------------------------------------------


def _run_flags() -> set[str]:
    """Every spelling `run` accepts, including the off half of a --x/--no-x pair."""
    params = SUMMARIZE.cli().commands["run"].params
    return {opt for param in params for opt in (*param.opts, *param.secondary_opts)}


def test_group_exposes_the_four_verbs() -> None:
    assert {"run", "status", "get", "list"} <= set(SUMMARIZE.cli().commands)


def test_there_is_no_recall_verb() -> None:
    """Fetching one record is `get`; querying recent ones is `list` (SDD 6.2).

    A separate `recall` would be a second spelling of both, reading the same
    memory index by a different name.
    """
    assert "recall" not in SUMMARIZE.cli().commands


def test_request_flags_are_derived_from_the_model() -> None:
    flags = _run_flags()
    assert {"--id", "--url", "--model", "--prompt", "--temperature", "--max-tokens"} <= flags
    assert "--enable-vlm-structured-output" in flags


def test_endpoint_flags_are_gone() -> None:
    """NFR-6: endpoints describe a deployment, not a request.

    These four named backends on every invocation. They are now read from
    ``~/.vss/config.json``, and their return would silently reintroduce the
    per-call deployment discovery the config layer replaced.
    """
    flags = _run_flags()
    for gone in ("--backend-url", "--es-endpoint", "--embedding-endpoint", "--embedding-model"):
        assert gone not in flags


def test_persistence_options_are_not_request_fields() -> None:
    """They configure the job, not the VLM call, so they must not reach the payload."""
    assert {"persist", "video_id", "media_source"} <= set(SummarizeOptions.model_fields)
    assert not {"persist", "video_id", "media_source"} & set(SummarizeInput.model_fields)
    assert {"--persist", "--no-persist", "--video-id"} <= _run_flags()


# --------------------------------------------------------------------------
# input validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="neither"),
        pytest.param(["--id", "v1", "--url", "http://x/v.mp4"], id="both"),
    ],
)
def test_exactly_one_source_is_required(configured: config_mod.Deployment, argv: list[str]) -> None:
    result = _run(*argv)
    assert result.exit_code == int(Exit.INVALID_INPUT), result.output


def test_scenario_and_events_are_required(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    """LVS answers 422 without them, so the CLI must not build the request.

    Both are named in one message rather than one per round trip, and the
    summarization is never attempted.
    """
    seen = _capture_post(monkeypatch)
    result = CliRunner().invoke(SUMMARIZE.cli(), ["run", "--id", "v1", "--no-persist"])
    assert result.exit_code == int(Exit.INVALID_INPUT), result.output
    assert "scenario" in result.output
    assert "events" in result.output
    assert seen == {}, "summarization must not be attempted"


def test_steering_reaches_the_request(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--event` and `--object-of-interest` are repeatable, plural in the body."""
    seen = _capture_post(monkeypatch)
    result = _run(
        "--id",
        "v1",
        "--no-persist",
        "--scenario",
        "retail",
        "--event",
        "theft",
        "--event",
        "spill",
        "--object-of-interest",
        "cart",
    )
    assert result.exit_code == 0, result.output
    assert seen["json"]["scenario"] == "retail"
    assert seen["json"]["events"] == ["theft", "spill"]
    assert seen["json"]["objects_of_interest"] == ["cart"]


def test_structured_output_is_on_by_default_and_can_be_turned_off(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative spelling has to survive `exclude_defaults`.

    LVS defaults this true. Were the field declared false, asking for prose
    would match the field default, be dropped from the payload, and LVS would
    apply its own true -- a flag that silently did nothing.
    """
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--no-persist").exit_code == 0
    assert "enable_vlm_structured_output" not in seen["json"], "default: let LVS apply its own"

    assert _run("--id", "v1", "--no-persist", "--no-enable-vlm-structured-output").exit_code == 0
    assert seen["json"]["enable_vlm_structured_output"] is False


def test_url_persist_without_video_id_fails_before_summarizing(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail fast: the check is free, the summarization it guards is not.

    A persisted record needs a video_id, which for a --url summary can only
    come from --video-id. Discovering that after an hour of VLM time would
    throw the result away.
    """
    seen = _capture_post(monkeypatch)
    result = _run("--url", "http://x/v.mp4")
    assert result.exit_code == int(Exit.INVALID_INPUT), result.output
    assert seen == {}, "summarization must not be attempted"
    assert "--video-id" in result.output


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


def test_run_posts_to_the_deployments_lvs_route(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LVS serves summarize itself; the agent has no such endpoint to proxy it."""
    seen = _capture_post(monkeypatch)
    result = _run("--id", "v1", "--no-persist")
    assert result.exit_code == 0, result.output
    assert seen["url"] == f"{BASE_URL}/lvs/v1/summarize"
    body = json.loads(result.output)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["job_id"].startswith("summarize-")


def test_model_defaults_to_what_lvs_reports(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment reported which VLM it serves; asking again is redundant."""
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--no-persist").exit_code == 0
    assert seen["json"]["model"] == "cosmos-reason"


def test_the_default_model_comes_from_lvs_not_rt_vlm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Where the two disagree, the request must carry what LVS serves.

    LVS is the backend answering this call, so its model is the one that has
    to be in the payload; RT-VLM's is what `vss vlm` would use.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(_deployment(lvs_models=["lvs-vlm"], vlm_models=["rtvi-vlm"]))
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--no-persist").exit_code == 0
    assert seen["json"]["model"] == "lvs-vlm"


def test_rt_vlm_answers_when_lvs_reported_no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment recorded before `vss configure` probed lvs still resolves."""
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(_deployment(lvs_models=[], vlm_models=["rtvi-vlm"]))
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--no-persist").exit_code == 0
    assert seen["json"]["model"] == "rtvi-vlm"


def test_explicit_model_wins_over_the_recorded_one(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--model", "other-vlm", "--no-persist").exit_code == 0
    assert seen["json"]["model"] == "other-vlm"


def test_no_recorded_model_and_no_flag_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(_deployment(lvs_models=[], vlm_models=[]))
    _capture_post(monkeypatch)
    assert _run_via_root("--id", "v1", "--no-persist") == int(Exit.CONFIGURATION)
    assert "vss configure" in capsys.readouterr().err


def test_unset_options_stay_out_of_the_request(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent, not null: an omitted flag must let the backend's default apply."""
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--temperature", "0.2", "--no-persist").exit_code == 0
    assert seen["json"]["temperature"] == 0.2
    assert "top_p" not in seen["json"]
    assert "enable_audio" not in seen["json"]


def test_missing_deployment_points_at_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    assert _run_via_root("--id", "v1", "--no-persist") == int(Exit.CONFIGURATION)
    assert "vss configure" in capsys.readouterr().err


# --------------------------------------------------------------------------
# backend failures
# --------------------------------------------------------------------------


def test_server_error_is_backend_unreachable(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_post(monkeypatch, _Response(status_code=503))
    assert _run("--id", "v1", "--no-persist").exit_code == int(Exit.BACKEND_UNREACHABLE)


def test_rejected_request_is_invalid_input(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(monkeypatch, _Response(status_code=422, text="bad model"))
    assert _run("--id", "v1", "--no-persist").exit_code == int(Exit.INVALID_INPUT)


def test_unreachable_backend_is_exit_three(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(monkeypatch, httpx.ConnectError("refused"))
    assert _run("--id", "v1", "--no-persist").exit_code == int(Exit.BACKEND_UNREACHABLE)


def test_timeout_exits_seven_and_names_the_job(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 7 means resume by handle, not re-run an hour of summarization."""
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    result = _run("--id", "v1", "--no-persist")
    assert result.exit_code == int(Exit.TIMEOUT)
    assert "summarize-" in result.output


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def test_no_persist_skips_the_memory_write(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    _capture_post(monkeypatch)
    result = _run("--id", "v1", "--no-persist")
    assert result.exit_code == 0
    assert _store(memory).statuses == []
    assert "persist" not in json.loads(result.output)


def test_persist_writes_one_unified_memory_record(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """The whole point of the group: a summary that outlives the process.

    The record is ``nv.vss.memory/1.0`` in the ``summary`` group, keyed by the
    job id the command reported, and it names the asset it describes -- which
    is what makes it findable by `get` and by `list --sensor-id`.
    """
    _capture_post(monkeypatch)
    result = _run("--id", "v1")
    assert result.exit_code == 0, result.output

    body = json.loads(result.output)
    record = _persisted(memory)
    assert record["schema"] == "nv.vss.memory/1.0"
    assert record["job"]["group"] == "summary"
    assert record["job"]["job_id"] == body["job_id"]
    assert record["job"]["status"] == "completed"
    assert record["input"]["sensors"][0]["id"] == "v1"
    assert record["output"]["answer"] == "a forklift crosses the aisle"
    assert body["persist"] == {"status": "complete", "index": memory.index, "group": "summary", "events": 0}


def test_the_record_exists_before_the_summary_does(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """Submitted first, terminal after -- one document, two writes.

    A job written only on success is invisible for exactly the hour in which
    something might want to ask about it.
    """
    _capture_post(monkeypatch)
    assert _run("--id", "v1").exit_code == 0
    assert _store(memory).statuses == ["submitted", "completed"]


def test_the_request_is_recorded_with_the_result(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """A persisted job describes the call that produced it, verbatim."""
    _capture_post(monkeypatch)
    assert _run("--id", "v1", "--prompt", "what happened?", "--temperature", "0.2").exit_code == 0

    record = _persisted(memory)
    assert record["input"]["query"] == "what happened?"
    assert record["input"]["params"]["temperature"] == 0.2
    assert record["input"]["params"]["model"] == "cosmos-reason"
    assert record["input"]["sensors"][0]["info"]["stream_id"] == "v1"
    assert record["output"]["ext"]["completion_id"] == "cmpl-1"


def test_structured_output_becomes_answer_and_events(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    # Shaped like LVS's event rows once --creation-time anchors them: the
    # adapter requires a parseable absolute instant on every row, so a bare
    # offset into the clip ("0.0") is refused. See the test below.
    event = {
        "start_time": "2025-01-01T00:00:00.000Z",
        "end_time": "2025-01-01T00:00:10.000Z",
        "type": "forklift",
        "description": "forklift enters",
    }
    structured = {"video_summary": "a forklift crosses", "events": [event]}
    _capture_post(monkeypatch, _Response(_completion(structured)))
    result = _run("--id", "v1")
    assert result.exit_code == 0, result.output

    record = _persisted(memory)
    assert record["output"]["answer"] == "a forklift crosses"
    # The adapter normalizes each row by promoting its time to `timestamp`,
    # which is the field windowed recall filters on.
    assert record["output"]["ext"]["events"] == [{**event, "timestamp": event["start_time"]}]
    assert json.loads(result.output)["persist"]["events"] == 1


def test_epoch_event_times_become_instants(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """LVS answers in epoch seconds once anchored; memory keys recall off instants.

    Verbatim from a live run: with --creation-time, LVS adds the anchor to each
    offset and returns a float. Memory refuses a float, so the group spells it.
    """
    structured = {
        "video_summary": "a forklift crosses",
        "events": [{"start_time": 1735689600.0, "end_time": 1735689720.0, "type": "forklift"}],
    }
    _capture_post(monkeypatch, _Response(_completion(structured)))
    result = _run("--id", "v1", "--creation-time", "2025-01-01T00:00:00Z")
    assert result.exit_code == 0, result.output

    (event,) = _persisted(memory)["output"]["ext"]["events"]
    assert event["start_time"] == "2025-01-01T00:00:00Z"
    assert event["end_time"] == "2025-01-01T00:02:00Z"
    assert event["timestamp"] == "2025-01-01T00:00:00Z"


def test_offsets_are_anchored_to_the_creation_time(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """A backend that ignores the anchor still yields absolute times.

    The anchor is known either way, so an offset is arithmetic rather than a
    reason to lose the events.
    """
    structured = {"video_summary": "a forklift crosses", "events": [{"start_time": 0.0, "end_time": 30.5}]}
    _capture_post(monkeypatch, _Response(_completion(structured)))
    result = _run("--id", "v1", "--creation-time", "2025-01-01T00:00:00Z")
    assert result.exit_code == 0, result.output

    (event,) = _persisted(memory)["output"]["ext"]["events"]
    assert event["start_time"] == "2025-01-01T00:00:00Z"
    assert event["end_time"] == "2025-01-01T00:00:30.500000Z"


def test_offsets_without_a_creation_time_name_the_flag_that_fixes_it(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """Unanchored offsets cannot become instants, and the error says so.

    Exit 6, not a crash: the summary is in hand and only the write is missing,
    and the caller can re-run the write once the media start is known.
    """
    structured = {"video_summary": "a forklift crosses", "events": [{"start_time": 0.0, "end_time": 30.0}]}
    _capture_post(monkeypatch, _Response(_completion(structured)))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.PARTIAL), result.output
    body = json.loads(result.stdout)
    assert body["summary"]["id"] == "cmpl-1"
    assert "--creation-time" in body["persist"]["error"]


def test_events_without_a_timestamp_cost_the_write_not_the_summary(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """The adapter rejects untimestamped event rows, and that lands as exit 6.

    A model that returns events with no time reference cannot support windowed
    recall, so unified memory refuses the record -- but the caller still paid
    for the summarization and still gets it.
    """
    structured = {"video_summary": "a forklift crosses", "events": [{"description": "no time reference"}]}
    _capture_post(monkeypatch, _Response(_completion(structured)))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.PARTIAL), result.output
    body = json.loads(result.stdout)
    assert body["summary"]["id"] == "cmpl-1"
    assert "timestamp" in body["persist"]["error"]


def test_prose_output_is_still_persistable(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """Unstructured VLM output must not block the write; it stores as a summary with no events."""
    _capture_post(monkeypatch, _Response(_completion("just prose")))
    assert _run("--id", "v1").exit_code == 0

    record = _persisted(memory)
    assert record["output"]["answer"] == "just prose"
    assert "events" not in record["output"]["ext"]


def test_failed_write_is_partial_and_keeps_the_summary(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 6 means retry the write, not the job.

    The caller has already paid for the summarization; discarding it would
    make a storage failure cost a second hour of VLM time.
    """
    _capture_post(monkeypatch)
    _memory(monkeypatch, _Store(fail_on="completed"))
    result = _run("--id", "v1")
    assert result.exit_code == int(Exit.PARTIAL), result.output

    body = json.loads(result.output)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"


def test_a_store_that_refuses_the_first_write_still_summarizes(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An index that will not take writes costs the record, not the answer.

    The submitted record goes in before the VLM call, so a store that refuses
    it aborted the run before any work happened at all. Persistence is
    optional; the summarization the caller asked for is not.
    """
    seen = _capture_post(monkeypatch)
    _memory(monkeypatch, _Store(fail_on="submitted"))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.PARTIAL), result.output
    assert seen, "the summarization must still have been requested"

    body = json.loads(result.stdout)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"


def test_a_status_rejection_is_a_persist_failure_not_a_crash(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only index answers 405, which is not a transport failure.

    The Elasticsearch store only translates connection and transport trouble
    into ``BackendUnreachableError``; a refused status arrives as the client's
    own ``ApiError``. Untranslated, it left the CLI as exit 1 and a traceback.
    """
    _capture_post(monkeypatch)
    _memory(monkeypatch, _RefusingStore())
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.PARTIAL), result.output
    assert "405" in json.loads(result.stdout)["persist"]["error"]


def test_unreachable_memory_fails_before_summarizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No index, no persisted job -- and no point spending VLM time first."""
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(_deployment(services={"rt_vlm": config_mod.Service(url=BASE_URL, models=["cosmos-reason"])}))
    seen = _capture_post(monkeypatch)
    assert _run_via_root("--id", "v1") == int(Exit.CONFIGURATION)
    assert seen == {}, "summarization must not be attempted"
    assert "memory" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------
# lifecycle: what a job that never finishes leaves behind
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "expected", "status"),
    [
        pytest.param(httpx.TimeoutException("slow"), Exit.TIMEOUT, "timeout", id="timeout"),
        pytest.param(httpx.ConnectError("refused"), Exit.BACKEND_UNREACHABLE, "failed", id="unreachable"),
        pytest.param(_Response(status_code=422, text="bad"), Exit.INVALID_INPUT, "failed", id="rejected"),
    ],
)
def test_a_job_that_fails_is_closed_out_not_left_pending(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    memory: memory_mod.Memory,
    failure: Any,
    expected: Exit,
    status: str,
) -> None:
    """Every exit path after the submitted write leaves a terminal record.

    Otherwise `status` reports a job as running forever, and the exit-7
    contract -- resume by job id -- resumes into nothing.
    """
    _capture_post(monkeypatch, failure)
    assert _run("--id", "v1").exit_code == int(expected)
    assert _store(memory).statuses == ["submitted", status]

    record = _persisted(memory)
    assert record["job"]["status"] == status
    assert record["error"]["code"] == status


def test_a_memory_write_that_fails_does_not_mask_the_real_error(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the record out is best-effort: the caller's diagnosis wins."""
    _capture_post(monkeypatch, httpx.ConnectError("refused"))
    _memory(monkeypatch, _Store(fail_on="failed"))
    result = _run("--id", "v1")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE), result.output
    assert "lvs" in result.output


def test_a_timeout_puts_its_handle_on_stdout(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """Exit 7 advertises "resume by job id", so the id has to be machine-readable.

    A harness reads stdout; leaving the only copy of the handle in a stderr
    sentence would make the contract depend on parsing prose.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    result = _run("--id", "v1")
    assert result.exit_code == int(Exit.TIMEOUT), result.output

    marker = json.loads(result.stdout)
    assert marker["status"] == "timeout"
    assert marker["record"] == "closed"
    assert marker["job_id"] == _store(memory).upsert_ids[0]


def test_a_record_that_could_not_be_closed_says_so(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale record is worse than none: `status` would report it submitted.

    The write is still best-effort -- the timeout is what the caller needs to
    know -- but silence would leave a job pending forever with nothing saying
    the handle is no longer trustworthy.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    _memory(monkeypatch, _Store(fail_on="timeout"))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert json.loads(result.stdout)["record"] == "stale"
    assert "still reports it submitted" in result.stderr


def test_a_timeout_without_persistence_claims_no_record(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-persist means there is nothing to reconcile against, and it says so."""
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    result = _run("--id", "v1", "--no-persist")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert json.loads(result.stdout)["record"] == "absent"


# --------------------------------------------------------------------------
# the read verbs, against what run persisted
# --------------------------------------------------------------------------


def _read(*argv: str) -> Any:
    return CliRunner().invoke(SUMMARIZE.cli(), list(argv))


def test_get_returns_the_record_run_persisted(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """run and get are two ends of one index -- the payoff of persisting."""
    _capture_post(monkeypatch)
    job_id = json.loads(_run("--id", "v1").output)["job_id"]

    result = _read("get", "--job-id", job_id)
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["job"]["job_id"] == job_id
    assert record["output"]["answer"] == "a forklift crosses the aisle"


def test_status_reports_the_lifecycle_state(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    assert _run("--id", "v1").exit_code == int(Exit.TIMEOUT)

    result = _read("status", "--job-id", _store(memory).upsert_ids[0])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["job"]["status"] == "timeout"


def test_list_filters_by_sensor(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    _capture_post(monkeypatch)
    assert _run("--id", "v1").exit_code == 0
    assert _run("--id", "v2").exit_code == 0

    assert len(json.loads(_read("list").output)) == 2
    only = json.loads(_read("list", "--sensor-id", "v2").output)
    assert [record["input"]["sensors"][0]["id"] for record in only] == ["v2"]


def test_an_unknown_job_is_not_found(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """Exit 5 means disambiguate the handle, not retry the backend."""
    result = _read("get", "--job-id", "summarize-NOPE")
    assert result.exit_code == int(Exit.NOT_FOUND), result.output


def test_another_groups_job_is_not_this_groups_to_return(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """`summarize get` must not hand back a search job that shares the index."""
    from vss_core.memory import SearchAdapter

    foreign = SearchAdapter().submitted_record(
        job_id="search-01",
        created_at="2026-01-01T00:00:00Z",
        input_data=SearchAdapter.build_input(query="forklift", sensors=None, window=None, params=None),
    )
    memory.service.upsert(foreign)

    result = _read("get", "--job-id", "search-01")
    assert result.exit_code == int(Exit.NOT_FOUND), result.output


# --------------------------------------------------------------------------
# job identity
# --------------------------------------------------------------------------


def test_job_ids_are_prefixed_and_sortable() -> None:
    """ULID ordering keeps job ids sortable by mint time without a separate key."""
    first = summarize_group._mint_job_id()
    second = summarize_group._mint_job_id()
    assert first.startswith("summarize-")
    assert len(first.split("-", 1)[1]) == 26
    assert first < second or first[:14] == second[:14]


def test_options_reject_unknown_keys() -> None:
    """extra=forbid guards the programmatic callers Click cannot."""
    with pytest.raises(ValueError, match="persistt"):
        SummarizeOptions(persistt=True)


def test_input_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="modl"):
        SummarizeInput(id="v1", modl="x")

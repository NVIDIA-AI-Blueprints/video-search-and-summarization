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
from typing import cast

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli.exits import Exit
from vss_cli.summarize import group as summarize_group
from vss_cli.summarize.group import SUMMARIZE
from vss_cli.summarize.group import SummarizeInput
from vss_cli.summarize.group import SummarizeOptions
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


class _Unparseable(_Response):
    """A 200 whose body is not JSON at all, as a misconfigured proxy returns."""

    def json(self) -> Any:
        raise ValueError("not json")


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

    def __init__(self, *, fail_on: str | tuple[str, ...] | None = None) -> None:
        super().__init__()
        self.statuses: list[str] = []
        # Several statuses, because an index that refuses the outcome refuses
        # the attempt to record the failure too -- which is the case that
        # decides whether a stale record is reported or swallowed.
        self._fail_on = (fail_on,) if isinstance(fail_on, str) else fail_on or ()

    def upsert(self, record: Any) -> Any:
        self.statuses.append(record.job.status)
        if record.job.status in self._fail_on:
            raise BackendUnreachableError("elasticsearch", "index is read-only")
        return super().upsert(record)


class _FlakyStore(_Store):
    """A store whose closing write fails a few times, then succeeds.

    The realistic shape of a lost close: Elasticsearch answered the submitted
    write moments ago, then blinked -- a rolling restart, a brief 503.
    """

    def __init__(self, *, fail_on: str, times: int) -> None:
        super().__init__()
        self._flaky_status = fail_on
        self._remaining = times
        self.attempts = 0

    def upsert(self, record: Any) -> Any:
        if record.job.status == self._flaky_status:
            self.attempts += 1
            if self._remaining > 0:
                self._remaining -= 1
                self.statuses.append(record.job.status)
                raise BackendUnreachableError("elasticsearch", "briefly unavailable")
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
    """The one parent record the run wrote, as it would come back from `get`."""
    records = memory.service.list_jobs()
    assert len(records) == 1, records
    return records[0].model_dump_memory()


def _persisted_event_children(memory: memory_mod.Memory) -> list[dict[str, Any]]:
    """The parent's ``event`` child records, oldest event first."""
    from vss_core.memory.store import MemoryQuery

    parent = _persisted(memory)
    children = memory.service.query(MemoryQuery(job_id=parent["job"]["job_id"], record_type="event", limit=100))
    ordered = sorted(
        children,
        key=lambda item: item.input.window.start.timestamp if item.input and item.input.window else item.job.created_at,
    )
    return [child.model_dump_memory() for child in ordered]


#: LVS requires model, scenario and events on every request. The model is
#: defaulted from the recorded deployment; the other two can only come from
#: the caller, so every invocation carries them. Tests that are not about
#: steering take these, and the ones that are pass their own.
_STEERING = ("--scenario", "warehouse monitoring", "--event", "forklift")


def _steered(argv: tuple[str, ...]) -> list[str]:
    return list(argv) if "--scenario" in argv else [*_STEERING, *argv]


def _run(*argv: str) -> Any:
    return CliRunner().invoke(SUMMARIZE.cli(), ["run", *_steered(argv)])


def _body(result: Any) -> dict[str, Any]:
    """The paid-for result is the first compact JSON line."""
    return cast("dict[str, Any]", json.loads(result.stdout.splitlines()[0]))


def _marker(result: Any) -> dict[str, Any]:
    """The SDD completion callback is always the final compact JSON line."""
    return cast("dict[str, Any]", json.loads(result.stdout.splitlines()[-1]))


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
    for gone in ("--backend-url", "--es-endpoint", "--embedding-endpoint", "--embedding-model", "--memory-index"):
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
    body = _body(result)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["job_id"].startswith("summarize-")


def test_pretty_output_still_ends_with_one_compact_marker(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_post(monkeypatch)
    result = _run("--id", "v1", "--no-persist", "--pretty")
    assert result.exit_code == 0, result.output
    marker = json.loads(result.stdout.splitlines()[-1])
    assert marker["event"] == "vss_job_completed"
    assert marker["group"] == "summary"
    assert marker["asset_id"] == "v1"
    assert marker["persisted"] is False


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
    assert "persist" not in _body(result)


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

    body = _body(result)
    record = _persisted(memory)
    assert record["schema"] == "nv.vss.memory/1.0"
    assert record["job"]["group"] == "summary"
    assert record["job"]["job_id"] == body["job_id"]
    assert record["job"]["status"] == "completed"
    assert record["input"]["sensors"][0]["id"] == "v1"
    assert record["output"]["answer"] == "a forklift crosses the aisle"
    assert body["persist"] == {
        "status": "complete",
        "index": memory.index,
        "group": "summary",
        "events": 0,
        "expected": 1,
        "written": 1,
    }


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
    # Events are child records now, never a nested collection on the parent.
    assert "events" not in record.get("output", {}).get("ext", {})
    assert record["output"]["ext"]["event_count"] == 1
    (child,) = _persisted_event_children(memory)
    assert child["job"]["record_type"] == "event"
    assert child["output"]["answer"] == "forklift enters"
    assert child["output"]["ext"]["event_type"] == "forklift"
    assert child["input"]["window"]["start"]["timestamp"] == "2025-01-01T00:00:00Z"
    assert child["input"]["window"]["end"]["timestamp"] == "2025-01-01T00:00:10Z"
    body = _body(result)
    assert body["persist"]["events"] == 1
    assert body["persist"]["written"] == 2  # parent + one child


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

    (child,) = _persisted_event_children(memory)
    assert child["input"]["window"]["start"]["timestamp"] == "2025-01-01T00:00:00Z"
    assert child["input"]["window"]["end"]["timestamp"] == "2025-01-01T00:02:00Z"


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

    (child,) = _persisted_event_children(memory)
    assert child["input"]["window"]["start"]["timestamp"] == "2025-01-01T00:00:00Z"
    assert child["input"]["window"]["end"]["timestamp"] == "2025-01-01T00:00:30.500000Z"


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
    body = _body(result)
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
    body = _body(result)
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

    body = _body(result)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"
    # The close landed, so the job reads `partial` rather than still running.
    assert body["record"] == "closed"


def test_a_partial_that_could_not_be_closed_says_so(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An index refusing the outcome refuses the record of the failure too.

    The summary survives either way, so the exit stays 6 -- but the record is
    still `submitted`, which is the one state `status` reports as running. Left
    out of the marker, a caller reading only stdout would trust a handle that
    answers with the wrong state, exactly as it would on the timeout path.
    """
    _capture_post(monkeypatch)
    _memory(monkeypatch, _Store(fail_on=("completed", "partial")))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.PARTIAL), result.output
    body = _body(result)
    marker = _marker(result)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"
    assert body["record"] == "stale"
    assert marker["persisted"] is False
    assert marker["exit_hint"] == int(Exit.PARTIAL)
    assert "still reports it submitted" in result.stderr


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

    body = _body(result)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"
    # Nothing was ever written, so there is no record to be stale about.
    assert body["record"] == "absent"


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
    assert "405" in _body(result)["persist"]["error"]


def test_unreachable_memory_fails_before_summarizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No index, no persisted job -- and no point spending VLM time first.

    The deployment routes LVS so that memory is the only thing missing:
    otherwise the framework's own requirement check answers first, which is a
    different diagnosis and the subject of the test below.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(
        _deployment(
            services={
                "lvs": config_mod.Service(url=f"{BASE_URL}/lvs", models=["cosmos-reason"]),
                "rt_vlm": config_mod.Service(url=BASE_URL, models=["cosmos-reason"]),
            }
        )
    )
    seen = _capture_post(monkeypatch)
    assert _run_via_root("--id", "v1") == int(Exit.CONFIGURATION)
    assert seen == {}, "summarization must not be attempted"
    assert "memory" in capsys.readouterr().err.lower()


def test_a_deployment_without_lvs_says_that_and_not_something_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The group declares `requires`, so the missing service is named as such.

    Without it the run reached model defaulting first and reported that `--model`
    could not be defaulted -- true, but it points at the flag rather than at the
    absent service, and it got there after opening Elasticsearch.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    config_mod.save(_deployment(services={"rt_vlm": config_mod.Service(url=BASE_URL, models=["cosmos-reason"])}))
    seen = _capture_post(monkeypatch)

    assert _run_via_root("--id", "v1") == int(Exit.CONFIGURATION)
    assert seen == {}, "summarization must not be attempted"
    stderr = capsys.readouterr().err
    assert "lvs" in stderr
    assert "--model" not in stderr


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

    Otherwise `status` reports a job as running forever, and the handle exit 7
    hands back names a job nothing can look up.
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
    """Exit 7 hands back a handle, so the id has to be machine-readable.

    A harness reads stdout; leaving the only copy of the handle in a stderr
    sentence would make the contract depend on parsing prose.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    result = _run("--id", "v1")
    assert result.exit_code == int(Exit.TIMEOUT), result.output

    body = _body(result)
    marker = _marker(result)
    assert marker["status"] == "timeout"
    assert marker["job_id"] == _store(memory).upsert_ids[0]
    assert marker["event"] == "vss_job_timeout"
    assert marker["persisted"] is True
    assert marker["exit_hint"] == int(Exit.TIMEOUT)
    assert body["record"] == "closed"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(httpx.ConnectError("refused"), Exit.BACKEND_UNREACHABLE, id="unreachable"),
        pytest.param(_Response(status_code=503, text="down"), Exit.BACKEND_UNREACHABLE, id="backend-error"),
        pytest.param(_Response(status_code=422, text="bad"), Exit.INVALID_INPUT, id="rejected"),
        pytest.param(_Unparseable(), Exit.BACKEND_UNREACHABLE, id="unparseable"),
        pytest.param(_Response(payload=["not", "an", "object"]), Exit.BACKEND_UNREACHABLE, id="not-an-object"),
    ],
)
def test_a_backend_failure_puts_its_handle_on_stdout(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    memory: memory_mod.Memory,
    failure: Any,
    expected: Exit,
) -> None:
    """Design §7.2: the marker is the final stdout line of *any* run.

    These paths write a terminal record before they give up, so the handle that
    names it has to reach stdout. Raising instead would spend the write and
    then hide what it produced, leaving a harness with a failed record it
    cannot address.
    """
    _capture_post(monkeypatch, failure)
    result = _run("--id", "v1")
    assert result.exit_code == int(expected), result.output

    body = _body(result)
    marker = _marker(result)
    assert marker["status"] == "failed"
    assert marker["job_id"] == _store(memory).upsert_ids[0]
    assert marker["event"] == "vss_job_failed"
    assert marker["persisted"] is True
    assert body["record"] == "closed"
    assert result.stderr.strip(), "a failure must still diagnose itself on stderr"


def test_a_rejected_request_is_diagnosed_in_the_frameworks_words(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    """One definition of the exit-2 prefix, so a harness sees one format.

    This path returns rather than raising, so it does not get the prefix from
    Click's rendering of `InvalidInput` -- but it is the same class of error and
    must read as one. Compared against the exception itself rather than a
    literal, which is what a second copy would drift from.
    """
    from vss_cli.group import InvalidInput

    _capture_post(monkeypatch, _Response(status_code=422, text="bad"))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.INVALID_INPUT), result.output
    prefix = InvalidInput("x").format_message().removesuffix("x")
    assert prefix in result.stderr, f"{prefix!r} not in {result.stderr!r}"


@pytest.fixture(autouse=True)
def _immediate_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry without waiting. How long the backoff sleeps is not the contract."""
    monkeypatch.setattr(summarize_group, "_TERMINAL_WRITE_BACKOFF_SECONDS", 0)


def test_a_close_that_loses_a_race_is_retried_before_it_is_given_up(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blink between the two writes must not strand the job at `submitted`.

    The submitted write already proved the store reachable, so a terminal write
    that fails once says little about the next attempt -- and the difference is
    a record `status` would report as running forever.

    The counts are literal on purpose. Deriving both the failures and the
    expectation from `_TERMINAL_WRITE_ATTEMPTS` makes the assertion hold at
    every value of it, including the 1 that deletes retrying altogether.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    store = _FlakyStore(fail_on="timeout", times=1)
    memory = _memory(monkeypatch, store)
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert _body(result)["record"] == "closed"
    assert store.attempts == 2, "one failure must not be final"
    assert _persisted(memory)["job"]["status"] == "timeout"


def test_the_retry_budget_is_bounded(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying is bounded: a store that never recovers still ends the run.

    Pairs with the test above -- that one proves the retry happens, this one
    proves it stops -- so neither the feature nor its bound can quietly go.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    store = _FlakyStore(fail_on="timeout", times=summarize_group._TERMINAL_WRITE_ATTEMPTS + 5)
    _memory(monkeypatch, store)
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert _body(result)["record"] == "stale"
    assert store.attempts == summarize_group._TERMINAL_WRITE_ATTEMPTS


def test_a_record_that_could_not_be_closed_says_so(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale record is worse than none: `status` would report it submitted.

    The write is retried first, but it stays best-effort -- the timeout is what
    the caller needs to know -- and silence would leave a job pending forever
    with nothing saying the handle is no longer trustworthy.
    """
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    _memory(monkeypatch, _Store(fail_on="timeout"))
    result = _run("--id", "v1")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert _body(result)["record"] == "stale"
    assert "still reports it submitted" in result.stderr


def test_a_timeout_without_persistence_claims_no_record(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-persist means there is nothing to reconcile against, and it says so."""
    _capture_post(monkeypatch, httpx.TimeoutException("slow"))
    result = _run("--id", "v1", "--no-persist")

    assert result.exit_code == int(Exit.TIMEOUT), result.output
    assert _body(result)["record"] == "absent"


@pytest.mark.parametrize(("argv", "worth"), [((), "closed"), (("--no-persist",), "absent")])
def test_success_says_what_the_handle_is_worth_too(
    configured: config_mod.Deployment,
    monkeypatch: pytest.MonkeyPatch,
    memory: memory_mod.Memory,
    argv: tuple[str, ...],
    worth: str,
) -> None:
    """`record` is on every marker, so it is one field to switch on, not two.

    Present only on the failures it would be a field whose absence means
    success -- an implicit contract, and implicit on the one path a caller
    reads most. Both values are known here without asking memory anything: the
    terminal write returned, or nothing was asked of it.
    """
    _capture_post(monkeypatch)
    result = _run("--id", "v1", *argv)

    assert result.exit_code == int(Exit.SUCCESS), result.output
    assert _body(result)["record"] == worth


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
    job_id = _body(_run("--id", "v1"))["job_id"]

    result = _read("get", "--job-id", job_id)
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["job"]["job_id"] == job_id
    assert record["output"]["answer"] == "a forklift crosses the aisle"
    assert record["children"] == []


def test_get_hydrates_event_children_in_time_order(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, memory: memory_mod.Memory
) -> None:
    structured = {
        "video_summary": "two events",
        "events": [
            {"start_time": 20, "end_time": 30, "description": "second"},
            {"start_time": 0, "end_time": 10, "description": "first"},
        ],
    }
    _capture_post(monkeypatch, _Response(_completion(structured)))
    job_id = _body(_run("--id", "v1", "--creation-time", "2025-01-01T00:00:00Z"))["job_id"]

    result = _read("get", "--job-id", job_id)
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert [child["output"]["answer"] for child in record["children"]] == ["first", "second"]
    assert all(child["job"]["record_type"] == "event" for child in record["children"])


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
    from vss_cli.search.memory_adapter import SearchAdapter

    foreign = SearchAdapter().submitted_record(
        job_id="search-01",
        created_at="2026-01-01T00:00:00Z",
        input_data=SearchAdapter.build_input(query="forklift", sensors=None, window=None, params=None),
    )
    memory.service.upsert(foreign)

    result = _read("get", "--job-id", "search-01")
    assert result.exit_code == int(Exit.NOT_FOUND), result.output


def test_the_docker_edge_admits_the_index_this_cli_writes() -> None:
    """The persisted write only lands if the edge permits that exact path.

    The store PUTs `/<index>/_doc/<job_id>`, which the edge's read-only method
    rule answers 405 unless the path is excepted. The exception names the
    unified-memory index rather than the `vss-` prefix, so a future index in
    that prefix does not inherit write access -- which means this CLI's default
    index and the edge's pattern have to be checked against each other.
    """
    from pathlib import Path
    import re

    for parent in Path(__file__).resolve().parents:
        template = parent / "deploy" / "docker" / "services" / "infra" / "haproxy" / "haproxy.cfg.template"
        if template.exists():
            break
    else:
        pytest.skip("docker edge is not in this checkout")

    pattern = re.search(r"acl es_memory_doc_path path_reg (\S+)", template.read_text())
    assert pattern, "the memory write exception is gone from the edge"
    excepted = re.compile(pattern.group(1))

    from vss_core.memory.backends.elasticsearch import DEFAULT_MEMORY_INDEX

    assert excepted.search(f"/elasticsearch/{DEFAULT_MEMORY_INDEX}/_doc/summarize-01ABC")
    assert not excepted.search("/elasticsearch/mdx-chunks/_doc/summarize-01ABC")
    assert not excepted.search("/elasticsearch/vss-search/_doc/summarize-01ABC")


@pytest.mark.parametrize("verb", [["list"], ["get", "--job-id", "summarize-01"]])
def test_a_refused_read_is_a_typed_exit_not_a_traceback(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, verb: list[str]
) -> None:
    """A 403 from a locked-down index is an answer, so it exits 3 with a sentence.

    Elasticsearch raises status rejections as `ApiError`, which is not a
    `TransportError`, so the store does not translate it and `_exit_for` has to
    name it -- otherwise the ordinary read verbs exit 1 with a traceback that no
    harness can branch on. `write_failures()` already says this for writes.
    """
    from elasticsearch import ApiError

    class _Refusing(InMemoryStore):
        def list_jobs(self, filters: Any) -> Any:
            raise ApiError("forbidden", meta=SimpleNamespace(status=403), body=None)

        def get(self, job_id: str) -> Any:
            raise ApiError("forbidden", meta=SimpleNamespace(status=403), body=None)

    _memory(monkeypatch, _Refusing())
    result = _read(*verb)
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE), result.output
    assert "Traceback" not in result.output


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


#: The one bound LVS states that this model deliberately does not: LVS pins
#: `creation_time` to exactly 24 characters, which a validator produces from any
#: ISO-8601 instant. Declared as a length it would refuse what it normalizes.
_UNMIRRORED = frozenset({"creation_time"})

#: What either side may say about a value. Ranges were the whole vocabulary
#: until a per-element `max_length` -- the kind written inside a list's
#: annotation rather than as a keyword on it -- went uncopied and unnoticed.
_LIMITS = ("ge", "le", "max_length")


def _lvs_query_bounds() -> dict[str, dict[str, float]]:
    """Every bound LVS puts on its own SummarizationQuery, by field.

    Read out of the service's source rather than imported: the summarization
    service is not a dependency of the CLI, and this only needs the numbers.
    A list is read twice over -- the keywords bound how many items it takes,
    the annotation bounds each item -- because only reading the keywords is
    what let the element caps drift.
    """
    import ast
    from pathlib import Path

    for parent in Path(__file__).resolve().parents:
        model = parent / "services" / "video-summarization" / "src" / "vss_api_models.py"
        if model.exists():
            break
    else:
        pytest.skip("summarization service is not in this checkout")

    module = ast.parse(model.read_text())
    # Module constants too: LVS spells one cap `max_length=MAX_PROMPT_LENGTH`,
    # and a name is not a number to compare against.
    constants = {
        target.id: statement.value.value
        for statement in module.body
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }
    query = next(
        node for node in ast.walk(module) if isinstance(node, ast.ClassDef) and node.name == "SummarizationQuery"
    )

    def limits(call: ast.Call, prefix: str = "") -> dict[str, float]:
        return {
            prefix + str(keyword.arg): eval(ast.unparse(keyword.value), {"__builtins__": {}}, constants)
            for keyword in call.keywords
            if keyword.arg in _LIMITS
        }

    bounds: dict[str, dict[str, float]] = {}
    for statement in query.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.value, ast.Call):
            continue
        if not isinstance(statement.target, ast.Name):
            continue
        found = limits(statement.value)
        for node in ast.walk(statement.annotation):
            if isinstance(node, ast.Call):
                found |= limits(node, prefix="item_")
        bounds[statement.target.id] = found
    return bounds


def _cli_bounds(field: str) -> dict[str, float]:
    """The same vocabulary, read off this model's own field."""
    from typing import Annotated
    from typing import get_args
    from typing import get_origin

    from annotated_types import Ge
    from annotated_types import Le
    from annotated_types import MaxLen

    def read(constraints: Any, prefix: str = "") -> dict[str, float]:
        found: dict[str, float] = {}
        for constraint in constraints:
            if isinstance(constraint, Ge):
                found[prefix + "ge"] = constraint.ge
            elif isinstance(constraint, Le):
                found[prefix + "le"] = constraint.le
            elif isinstance(constraint, MaxLen):
                found[prefix + "max_length"] = constraint.max_length
            else:
                # A Field() inside an Annotated arrives as a FieldInfo whose
                # own metadata holds the constraint.
                found |= read(getattr(constraint, "metadata", ()), prefix)
        return found

    info = SummarizeInput.model_fields[field]
    ours = read(info.metadata)
    for arg in get_args(info.annotation):
        if get_origin(arg) is Annotated:
            ours |= read(get_args(arg)[1:], prefix="item_")
    return ours


@pytest.mark.parametrize("field", sorted(set(SummarizeInput.model_fields) - _UNMIRRORED))
def test_request_bounds_match_the_service_they_are_sent_to(field: str) -> None:
    """The CLI's validation is only authoritative if it agrees with LVS.

    Looser here spends a round trip to be told 422 -- as a 300-character
    --object-of-interest did; stricter refuses values LVS documents, as
    `--chunk-duration 0` once did. Both are silent until someone uses the
    value, so the two tables are compared rather than maintained by hand.

    Every field is compared, not a list of the interesting ones: a field with
    no bound on either side passes trivially, and the next cap LVS adds is
    caught by the field being here rather than by someone adding it.
    """
    assert _cli_bounds(field) == _lvs_query_bounds().get(field, {}), field


def test_options_reject_unknown_keys() -> None:
    """extra=forbid guards the programmatic callers Click cannot."""
    with pytest.raises(ValueError, match="persistt"):
        SummarizeOptions(persistt=True)


def test_input_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="modl"):
        SummarizeInput(id="v1", modl="x")

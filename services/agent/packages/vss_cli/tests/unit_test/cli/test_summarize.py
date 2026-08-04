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
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import summarize_group
from vss_cli.exits import Exit
from vss_cli.summarize_group import SUMMARIZE
from vss_cli.summarize_group import SummarizeInput
from vss_cli.summarize_group import SummarizeOptions

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://h:7777"


# --------------------------------------------------------------------------
# fixtures and doubles
# --------------------------------------------------------------------------


def _deployment(
    *, vlm_models: list[str] | None = None, services: dict[str, Any] | None = None
) -> config_mod.Deployment:
    """A deployment exposing everything summarize touches."""
    recorded = {
        "agent": config_mod.Service(url=f"{BASE_URL}/api"),
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


def _memory_tool(tmp_path: Path) -> str:
    """A checkout layout the persist bridge will accept."""
    script = tmp_path / "memtool" / summarize_group._PERSIST_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return str(tmp_path / "memtool")


def _capture_persist(monkeypatch: pytest.MonkeyPatch, receipt: Any, returncode: int = 0) -> dict[str, Any]:
    """Intercept the memory-tool subprocess, recording payload and environment."""
    seen: dict[str, Any] = {}

    class _Completed:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = receipt if isinstance(receipt, str) else json.dumps(receipt)
            self.stderr = ""

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen.update(argv=argv, payload=json.loads(kwargs["input"]), env=kwargs["env"])
        return _Completed()

    monkeypatch.setattr(summarize_group.subprocess, "run", fake_run)
    return seen


def _run(*argv: str) -> Any:
    return CliRunner().invoke(SUMMARIZE.cli(), ["run", *argv])


def _run_via_root(*argv: str) -> int:
    """Invoke through the root dispatcher.

    Two things only exist end to end: the ``vss.commands`` entry point that
    makes the group reachable at all, and the root's ConfigError -> exit 4
    mapping, which every group inherits rather than restating.
    """
    from vss_cli import main

    return main(["summarize", "run", *argv])


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


def test_run_posts_to_the_deployments_agent_route(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture_post(monkeypatch)
    result = _run("--id", "v1", "--no-persist")
    assert result.exit_code == 0, result.output
    assert seen["url"] == f"{BASE_URL}/api/v1/summarize"
    body = json.loads(result.output)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["job_id"].startswith("summarize-")


def test_model_defaults_to_what_rt_vlm_reports(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployment reported which VLM it serves; asking again is redundant."""
    seen = _capture_post(monkeypatch)
    assert _run("--id", "v1", "--no-persist").exit_code == 0
    assert seen["json"]["model"] == "cosmos-reason"


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
    config_mod.save(_deployment(vlm_models=[]))
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


def test_no_persist_skips_the_memory_write(configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(monkeypatch)
    seen = _capture_persist(monkeypatch, {"status": "complete"})
    result = _run("--id", "v1", "--no-persist")
    assert result.exit_code == 0
    assert seen == {}
    assert "persist" not in json.loads(result.output)


def test_persist_is_configured_from_the_recorded_deployment(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The memory write inherits endpoints nobody typed.

    Elasticsearch, RT-Embed and the embedding model are what those services
    reported to ``vss configure``; only the tokenizer vocabulary, which no
    backend advertises, stays caller-supplied.
    """
    _capture_post(monkeypatch)
    seen = _capture_persist(monkeypatch, {"status": "complete", "summary_id": "summary:v1"})
    result = _run("--id", "v1", "--memory-tool-dir", _memory_tool(tmp_path))
    assert result.exit_code == 0, result.output

    env = seen["env"]
    assert env[summarize_group._ENV_ES_ENDPOINT] == f"{BASE_URL}/elasticsearch"
    assert env[summarize_group._ENV_EMBED_ENDPOINT] == f"{BASE_URL}/rtvi-embed"
    assert env[summarize_group._ENV_EMBED_MODEL] == "bge-base-en-v1.5"
    assert seen["payload"]["video_id"] == "v1"
    assert seen["payload"]["media_ref"]["stream_id"] == "v1"
    assert json.loads(result.output)["persist"]["summary_id"] == "summary:v1"


def test_structured_output_becomes_summary_and_events(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    structured = {"video_summary": "a forklift crosses", "events": [{"description": "forklift enters"}]}
    _capture_post(monkeypatch, _Response(_completion(structured)))
    seen = _capture_persist(monkeypatch, {"status": "complete"})
    assert _run("--id", "v1", "--memory-tool-dir", _memory_tool(tmp_path)).exit_code == 0
    content = seen["payload"]["content"]
    assert content["video_summary"] == "a forklift crosses"
    assert content["events"] == [{"description": "forklift enters"}]


def test_prose_output_is_still_persistable(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unstructured VLM output must not block the write; it stores as a summary with no events."""
    _capture_post(monkeypatch, _Response(_completion("just prose")))
    seen = _capture_persist(monkeypatch, {"status": "complete"})
    assert _run("--id", "v1", "--memory-tool-dir", _memory_tool(tmp_path)).exit_code == 0
    assert seen["payload"]["content"] == {"video_summary": "just prose", "events": []}


def test_failed_write_is_partial_and_keeps_the_summary(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 6 means retry the write, not the job.

    The caller has already paid for the summarization; discarding it would
    make a storage failure cost a second hour of VLM time.
    """
    _capture_post(monkeypatch)
    _capture_persist(monkeypatch, "not json at all")
    result = _run("--id", "v1", "--memory-tool-dir", _memory_tool(tmp_path))
    assert result.exit_code == int(Exit.PARTIAL), result.output
    body = json.loads(result.output)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["persist"]["status"] == "failed"


def test_incomplete_write_is_partial(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _capture_post(monkeypatch)
    _capture_persist(monkeypatch, {"status": "degraded"})
    result = _run("--id", "v1", "--memory-tool-dir", _memory_tool(tmp_path))
    assert result.exit_code == int(Exit.PARTIAL), result.output


def test_absent_memory_tool_is_partial_not_a_crash(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _capture_post(monkeypatch)
    result = _run("--id", "v1", "--memory-tool-dir", str(tmp_path / "nowhere"))
    assert result.exit_code == int(Exit.PARTIAL), result.output
    assert "persist" in json.loads(result.output)


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

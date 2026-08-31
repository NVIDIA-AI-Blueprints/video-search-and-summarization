# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic contract tests for the persisted ``vss vlm`` job."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from click.testing import CliRunner
from pydantic import ValidationError
import pytest

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli.group import Context
from vss_cli.vlm.group import VLM
from vss_cli.vlm.runner import IntrospectionVLMJobRunner
from vss_cli.vlm.runner import VLMJobError
from vss_cli.vlm.runner import VLMJobRequest
from vss_cli.vlm.runner import VLMJobResult
from vss_cli.vlm.runner import run_vlm_job
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory import InMemoryStore
from vss_core.memory import MemoryQuery
from vss_core.memory import MemoryService
from vss_core.vios import SensorRef
from vss_core.vios import VIOSInvalidInputError
from vss_core.vios import VIOSNotFoundError

START = "2026-08-13T20:00:00.000Z"
END = "2026-08-13T20:00:10.000Z"
SEGMENTS = [("2026-08-13T19:59:00.000Z", "2026-08-13T20:01:00.000Z")]


def _deployment(*, persist: bool = True) -> config_mod.Deployment:
    return config_mod.Deployment(
        base_url="http://vss.test",
        services={
            "vst": config_mod.Service(url="http://vss.test/vst"),
            "rt_vlm": config_mod.Service(url="http://vss.test/rtvi-vlm", models=["test-vlm"]),
            "elasticsearch": config_mod.Service(url="http://vss.test/elasticsearch"),
        },
        memory=config_mod.MemoryConfig(enabled=persist, persist_by_default=persist),
    )


def _memory() -> memory_mod.Memory:
    return memory_mod.Memory(MemoryService(InMemoryStore()), index="test-memory")


async def _sensor(*_args: Any, **_kwargs: Any) -> SensorRef:
    return SensorRef(
        name="warehouse",
        sensor_id="sensor-1",
        stream_id="stream-1",
        url="file:///warehouse.mp4",
        kind="video",
    )


async def _segments(*_args: Any, **_kwargs: Any) -> list[tuple[str, str]]:
    return SEGMENTS


class _Analyzer:
    def __init__(self, *, answer: str = "A forklift crosses the aisle.", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def analyze(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.answer


def _run(
    analyzer: _Analyzer,
    *,
    memory: memory_mod.Memory | None = None,
    timeout_seconds: int = 180,
    resolver: Any = _sensor,
    segments: Any = _segments,
) -> VLMJobResult:
    return asyncio.run(
        run_vlm_job(
            VLMJobRequest(sensor="warehouse", start_time=START, end_time=END, prompt="What happened?"),
            _deployment(),
            analyzer=analyzer,
            analyzer_model="test-vlm",
            memory=memory,
            timeout_seconds=timeout_seconds,
            resolve_sensor_fn=resolver,
            recorded_segments_fn=segments,
        )
    )


def test_runner_performs_one_inference_and_persists_parent_only() -> None:
    memory = _memory()
    analyzer = _Analyzer()

    result = _run(analyzer, memory=memory)

    assert result.status == "completed"
    assert result.job_id.startswith("vlm-") and len(result.job_id) == 30
    assert len(analyzer.calls) == 1
    records = memory.service.list_jobs()
    assert len(records) == 1
    record = records[0].model_dump_memory()
    assert record["job"]["group"] == "vlm"
    assert record["job"]["operation"] == "run"
    assert record["input"]["query"] == "What happened?"
    assert record["input"]["intent"] == "video-qa"
    assert record["input"]["sensors"][0]["id"] == "warehouse"
    assert record["input"]["window"]["start"]["timestamp"] == "2026-08-13T20:00:00Z"
    assert record["output"]["answer"] == analyzer.answer
    assert record["output"]["ext"]["model"] == "test-vlm"
    assert memory.service.query(MemoryQuery(job_id=result.job_id, limit=10)) == records


def test_runner_without_memory_never_persists() -> None:
    result = _run(_Analyzer(), memory=None)
    assert result.record == "absent"
    assert result.persisted is False


def test_introspection_adapter_runs_direct_job_with_normal_persistence() -> None:
    memory = _memory()
    analyzer = _Analyzer()
    runner = IntrospectionVLMJobRunner(
        _deployment(),
        memory=memory,
        analyzer=analyzer,
        analyzer_model="test-vlm",
        resolve_sensor_fn=_sensor,
        recorded_segments_fn=_segments,
    )

    evidence = asyncio.run(
        runner.run(
            sensor="warehouse",
            start_time=START,
            end_time=END,
            prompt="What happened?",
            intent="introspection",
        )
    )

    assert evidence.persisted is True
    assert evidence.answer == analyzer.answer
    assert memory.service.list_jobs()[0].input.intent == "introspection"
    assert runner.persistence_errors == []


def test_introspection_adapter_preserves_answer_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _memory()
    analyzer = _Analyzer()
    original_upsert = memory.service.upsert
    writes = 0

    def fail_completion(record: Any) -> Any:
        nonlocal writes
        writes += 1
        if writes > 1:
            raise BackendUnreachableError("memory", "offline")
        return original_upsert(record)

    monkeypatch.setattr(memory.service, "upsert", fail_completion)
    runner = IntrospectionVLMJobRunner(
        _deployment(),
        memory=memory,
        analyzer=analyzer,
        analyzer_model="test-vlm",
        resolve_sensor_fn=_sensor,
        recorded_segments_fn=_segments,
    )

    evidence = asyncio.run(
        runner.run(
            sensor="warehouse",
            start_time=START,
            end_time=END,
            prompt="What happened?",
            intent="introspection",
        )
    )

    assert evidence.answer == analyzer.answer
    assert evidence.persisted is False
    assert runner.persistence_errors


def test_introspection_adapter_with_disabled_persistence_writes_nothing() -> None:
    runner = IntrospectionVLMJobRunner(
        _deployment(persist=False),
        memory=None,
        analyzer=_Analyzer(),
        analyzer_model="test-vlm",
        resolve_sensor_fn=_sensor,
        recorded_segments_fn=_segments,
    )
    evidence = asyncio.run(
        runner.run(
            sensor="warehouse",
            start_time=START,
            end_time=END,
            prompt="What happened?",
            intent="introspection",
        )
    )
    assert evidence.persisted is False
    assert runner.persistence_errors == []


def test_timeout_is_terminal_and_closes_record() -> None:
    class _SlowAnalyzer(_Analyzer):
        async def analyze(self, **kwargs: Any) -> str:
            self.calls.append(kwargs)
            await asyncio.sleep(2)
            return self.answer

    memory = _memory()
    with pytest.raises(VLMJobError) as caught:
        _run(_SlowAnalyzer(), memory=memory, timeout_seconds=1)

    assert caught.value.result.status == "timeout"
    assert caught.value.result.record == "closed"
    assert memory.service.list_jobs()[0].job.status == "timeout"


def test_timeout_is_shared_across_validation_and_inference() -> None:
    class _SlowAnalyzer(_Analyzer):
        async def analyze(self, **kwargs: Any) -> str:
            self.calls.append(kwargs)
            await asyncio.sleep(0.8)
            return self.answer

    async def slow_sensor(*_args: Any, **_kwargs: Any) -> SensorRef:
        await asyncio.sleep(0.8)
        return await _sensor()

    analyzer = _SlowAnalyzer()
    started = time.monotonic()
    with pytest.raises(VLMJobError) as caught:
        _run(analyzer, resolver=slow_sensor, timeout_seconds=1)
    elapsed = time.monotonic() - started

    assert caught.value.result.status == "timeout"
    assert caught.value.result.record == "absent"
    assert elapsed < 1.5


def test_timeout_recovery_does_not_retry_persist_or_hang_on_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vss_cli.vlm.runner as runner_mod

    class _HangingCleanupAnalyzer(_Analyzer):
        async def analyze(self, **kwargs: Any) -> str:
            self.calls.append(kwargs)
            await asyncio.sleep(2)
            return self.answer

        async def aclose(self) -> None:
            await asyncio.sleep(10)

    class _FailingTerminalStore(InMemoryStore):
        def upsert(self, record: Any) -> Any:
            if record.job.status == "timeout":
                time.sleep(0.2)
                raise BackendUnreachableError("elasticsearch", "offline")
            return super().upsert(record)

    analyzer = _HangingCleanupAnalyzer()
    monkeypatch.setattr(runner_mod, "_production_analyzer", lambda *_args, **_kwargs: (analyzer, "test-vlm"))
    memory = memory_mod.Memory(MemoryService(_FailingTerminalStore()), index="test-memory")

    started = time.monotonic()
    with pytest.raises(VLMJobError) as caught:
        asyncio.run(
            run_vlm_job(
                VLMJobRequest(sensor="warehouse", start_time=START, end_time=END, prompt="What happened?"),
                _deployment(),
                memory=memory,
                timeout_seconds=1,
                resolve_sensor_fn=_sensor,
                recorded_segments_fn=_segments,
            )
        )
    elapsed = time.monotonic() - started

    assert caught.value.result.status == "timeout"
    assert caught.value.result.record == "stale"
    assert elapsed < 3


def test_later_phases_receive_remaining_timeout_not_a_fresh_budget() -> None:
    seen: dict[str, float] = {}

    async def tracking_sensor(*_args: Any, timeout_seconds: float, **_kwargs: Any) -> SensorRef:
        seen["sensor"] = timeout_seconds
        await asyncio.sleep(0.3)
        return await _sensor()

    async def tracking_segments(*_args: Any, timeout_seconds: float, **_kwargs: Any) -> list[tuple[str, str]]:
        seen["segments"] = timeout_seconds
        return SEGMENTS

    _run(_Analyzer(), timeout_seconds=1, resolver=tracking_sensor, segments=tracking_segments)

    assert seen["sensor"] <= 1
    assert seen["segments"] < seen["sensor"]
    assert seen["segments"] <= 0.8


def test_invalid_window_never_calls_analyzer() -> None:
    analyzer = _Analyzer()

    with pytest.raises(VIOSInvalidInputError, match="single recorded segment"):
        asyncio.run(
            run_vlm_job(
                VLMJobRequest(
                    sensor="warehouse",
                    start_time="2026-08-13T20:00:00.000Z",
                    end_time="2026-08-13T21:00:10.000Z",
                    prompt="What happened?",
                ),
                _deployment(),
                analyzer=analyzer,
                resolve_sensor_fn=_sensor,
                recorded_segments_fn=lambda *_args, **_kwargs: asyncio.sleep(
                    0,
                    result=[
                        ("2026-08-13T19:59:00.000Z", "2026-08-13T20:01:00.000Z"),
                        ("2026-08-13T21:00:00.000Z", "2026-08-13T21:01:00.000Z"),
                    ],
                ),
            )
        )
    assert analyzer.calls == []


def test_unrecorded_window_never_calls_analyzer() -> None:
    analyzer = _Analyzer()

    async def no_segments(*_args: Any, **_kwargs: Any) -> list[tuple[str, str]]:
        return []

    with pytest.raises(VIOSInvalidInputError, match="nothing is recorded"):
        _run(analyzer, segments=no_segments)
    assert analyzer.calls == []


def test_missing_sensor_never_calls_analyzer() -> None:
    analyzer = _Analyzer()

    async def missing(*_args: Any, **_kwargs: Any) -> SensorRef:
        raise VIOSNotFoundError("no VIOS sensor named 'missing'")

    with pytest.raises(VIOSNotFoundError):
        _run(analyzer, resolver=missing)
    assert analyzer.calls == []


def test_backend_failure_is_terminal_and_persisted() -> None:
    memory = _memory()
    analyzer = _Analyzer(error=BackendUnreachableError("vlm", "offline"))

    with pytest.raises(VLMJobError) as caught:
        _run(analyzer, memory=memory)

    assert caught.value.result.status == "failed"
    assert caught.value.result.record == "closed"
    assert memory.service.list_jobs()[0].job.status == "failed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sensor", " "),
        ("prompt", "\t"),
        ("start_time", "2026-08-13T20:00:00"),
        ("end_time", "2026-08-13T20:00:10-07:00"),
    ],
)
def test_input_requires_nonempty_fields_and_utc(field: str, value: str) -> None:
    payload = {"sensor": "warehouse", "start_time": START, "end_time": END, "prompt": "What happened?"}
    payload[field] = value
    with pytest.raises(ValidationError):
        VLMJobRequest(**payload)


def test_status_get_and_list_are_memory_reads() -> None:
    memory = _memory()
    result = _run(_Analyzer(), memory=memory)
    context = Context(deployment=_deployment(), memory=memory)

    assert VLM.status(result.job_id, context).body["job"]["status"] == "completed"
    assert VLM.get(result.job_id, context).body["children"] == []
    listed = VLM.list({"sensor_id": "warehouse"}, context).body
    assert listed[0]["job"]["job_id"] == result.job_id


def test_help_has_only_negative_persistence_override() -> None:
    result = CliRunner().invoke(VLM.cli(), ["run", "--help"])
    assert result.exit_code == 0
    assert "--no-persist" in result.output
    assert "--persist" not in result.output.replace("--no-persist", "")


def test_production_analyzer_passes_in_cluster_clip_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeVST:
        def __init__(self, **kwargs: Any) -> None:
            captured["vst"] = kwargs

    class _FakeAnalyzer:
        def __init__(self, **kwargs: Any) -> None:
            captured["analyzer"] = kwargs

    monkeypatch.setattr("vss_core.vios.VSTClient", _FakeVST)
    monkeypatch.setattr("vss_core.vlm.OpenAIVLMAnalyzer", _FakeAnalyzer)

    from vss_cli.vlm.runner import _production_analyzer

    analyzer, model = _production_analyzer(_deployment(), 30)

    assert model == "test-vlm"
    assert analyzer is not None
    assert captured["analyzer"]["video_url_scope"] == "internal"
    assert captured["analyzer"]["media_mode"] == "video_url"

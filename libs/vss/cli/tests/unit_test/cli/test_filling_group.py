# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""HTTP-fixture checks for source-bound filling CLI commands and deployment discovery."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import json

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod
from vss_cli.filling import group
from vss_cli.group import Context
from vss_core.filling import FillingClient
from vss_core.filling import FillingError

STREAM = "c96e2b29-2101-4f06-9f64-d76081147c34"
OTHER = "5815509a-7317-4e8d-907e-a9f3d1c508fa"
ORIGIN = "2025-01-01T00:00:00+00:00"
SOURCE = {
    "id": "vss:" + STREAM + ":abc",
    "sha256": "abc",
    "stream_id": STREAM,
    "sensor_id": "sensor-a",
    "actual_start_time": ORIGIN,
    "duration": 90,
    "origin": "vss-vios",
    "analysis_kind": "bottle-cycles",
    "profile_id": "fixture",
}
PROVENANCE = {"stream_id": STREAM, "actual_start_time": ORIGIN}
ANSWER = {
    "source_sha256": "abc",
    "provenance": PROVENANCE,
    "supported": True,
    "answer": "Two measured low fills.",
    "cycles": [{"id": "cycle-a"}, {"id": "cycle-b"}],
    "evidence": [
        {"bottle_id": "cycle-a", "t": 10, "label": "A", "evidence_start": 9, "evidence_end": 11},
        {"bottle_id": "cycle-b", "t": 30, "label": "B", "evidence_start": 29, "evidence_end": 31},
    ],
}


class Fixture:
    def __init__(self):
        self.source = dict(SOURCE)
        self.answer = json.loads(json.dumps(ANSWER))
        self.calls = []
        self.clip_available = True
        self.clip_stream = STREAM
        self.clip_clock_shift = 0
        self.change_after_query = False
        self.status = "complete"

    def handle(self, request):
        self.calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path == "/filling/api/source":
            body = self.source
        elif path == "/filling/api/vss/sources":
            body = {"available": True, "configured": True, "sources": [{"stream_id": STREAM, "name": "sample"}]}
        elif path == "/filling/api/vss/select":
            body = self.source
        elif path == "/filling/api/query":
            body = self.answer
            if self.change_after_query:
                self.source = {**self.source, "stream_id": OTHER}
        elif path == "/filling/api/analysis":
            body = {"status": self.status, "stream_id": STREAM, "source_id": SOURCE["id"]}
        elif path == "/filling/api/analysis/result":
            body = {**self.answer, "samples": [{"t": 10}, {"t": 30}]}
        elif path == "/filling/api/measurement-evidence":
            start, end = float(request.url.params["start"]), float(request.url.params["end"])
            clock = datetime.fromisoformat(ORIGIN)
            body = {
                "available": self.clip_available,
                "source_id": SOURCE["id"],
                "stream_id": self.clip_stream,
                "source_clock_origin": ORIGIN,
                "source_offsets": {"start": start, "end": end},
                "requested": {
                    "streamId": STREAM,
                    "startTime": (clock + timedelta(seconds=start + self.clip_clock_shift)).isoformat(),
                    "endTime": (clock + timedelta(seconds=end)).isoformat(),
                },
                "actual": {"streamId": STREAM, "startTime": (clock + timedelta(seconds=start - 1)).isoformat()},
                "public_url": f"https://public.test/vst/clip-{start}.mp4",
            }
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    def client(self):
        return FillingClient("https://vss.test/filling", transport=httpx.MockTransport(self.handle))


def test_query_returns_each_bottles_own_real_clip_and_preroll():
    fixture = Fixture()
    with fixture.client() as client:
        answer = client.query(STREAM, "underfills", reference_percent=75)
    clips = answer["video_evidences"]
    assert len(clips) == 2
    assert clips[0]["public_url"] != clips[1]["public_url"]
    assert answer["cycles"][1]["video_evidence"]["public_url"] == clips[1]["public_url"]
    assert clips[0]["actual"]["startTime"] == "2025-01-01T00:00:08+00:00"
    sent = json.loads(next(body for method, path, body in fixture.calls if path.endswith("/query")))
    assert sent["reference_level"] == 0.75


def test_query_rejects_recording_switch():
    fixture = Fixture()
    fixture.change_after_query = True
    with fixture.client() as client, pytest.raises(FillingError, match="selected source differs") as failure:
        client.query(STREAM, "underfills")
    assert failure.value.code == 4
    assert not any(path.endswith("measurement-evidence") for _, path, _ in fixture.calls)


@pytest.mark.parametrize(
    "field,value", [("source_sha256", "other"), ("provenance", {"stream_id": OTHER, "actual_start_time": ORIGIN})]
)
def test_query_rejects_wrong_measurement_provenance(field, value):
    fixture = Fixture()
    fixture.answer[field] = value
    with fixture.client() as client, pytest.raises(FillingError, match="Measurements do not match"):
        client.query(STREAM, "underfills")


@pytest.mark.parametrize("attribute,value", [("clip_stream", OTHER), ("clip_clock_shift", 1)])
def test_evidence_rejects_wrong_stream_or_clock(attribute, value):
    fixture = Fixture()
    setattr(fixture, attribute, value)
    with fixture.client() as client, pytest.raises(FillingError) as failure:
        client.evidence(STREAM, 9, 11)
    assert failure.value.code == 4


def test_missing_clip_never_replaced_with_other_bottle():
    fixture = Fixture()
    fixture.clip_available = False
    with fixture.client() as client:
        answer = client.query(STREAM, "underfills")
    assert all(not clip["available"] for clip in answer["video_evidences"])


def test_results_preserve_counts_without_sending_all_samples():
    fixture = Fixture()
    with fixture.client() as client:
        result = client.results(STREAM)
    assert result["sample_count"] == 2
    assert "samples" not in result
    assert result["source"]["stream_id"] == STREAM


@pytest.mark.parametrize("start,end", [(float("nan"), 2), (0, float("inf")), (2, 1), (0, 91)])
def test_bad_evidence_offsets_fail_before_evidence_request(start, end):
    fixture = Fixture()
    with fixture.client() as client, pytest.raises(FillingError) as failure:
        client.evidence(STREAM, start, end)
    assert failure.value.code == 2
    assert not any(path.endswith("measurement-evidence") for _, path, _ in fixture.calls)


def test_select_rejects_unregistered_uuid_without_mutation():
    fixture = Fixture()
    with fixture.client() as client, pytest.raises(FillingError) as failure:
        client.select(OTHER)
    assert failure.value.code == 5
    assert all(method == "GET" for method, _, _ in fixture.calls)


def test_analyze_waits_existing_server_state_without_fabricating_result():
    fixture = Fixture()
    with fixture.client() as client:
        state = client.analyze(STREAM)
    assert state["status"] == "complete"
    assert state["source"]["id"] == SOURCE["id"]
    assert len([1 for method, path, _ in fixture.calls if method == "POST" and path.endswith("/analysis")]) == 1


def test_analyze_timeout_does_not_restart_job():
    fixture = Fixture()
    fixture.status = "running"
    with fixture.client() as client, pytest.raises(FillingError) as failure:
        client.analyze(STREAM, timeout=0.001)
    assert failure.value.code == 7
    assert len([1 for method, path, _ in fixture.calls if method == "POST" and path.endswith("/analysis")]) == 1


def test_cli_commands_and_required_stream_binding(monkeypatch):
    fixture = Fixture()
    deployment = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    monkeypatch.setattr(group, "context_from", lambda values: Context(deployment, values.get("pretty")))
    monkeypatch.setattr(group, "FillingClient", lambda _endpoint: fixture.client())
    cli = group.FILLING.cli()
    assert set(cli.commands) == {
        "sources",
        "source",
        "select",
        "analyze",
        "status",
        "results",
        "query",
        "evidence",
        "segmentation",
        "live",
    }
    assert CliRunner().invoke(cli, ["query", "--question", "underfills"]).exit_code == 2
    result = CliRunner().invoke(
        cli, ["query", "--stream-id", STREAM, "--question", "underfills"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["video_evidences"]) == 2
    assert "--endpoint" not in CliRunner().invoke(cli, ["query", "--help"]).output


def test_configure_discovers_optional_filling_route(tmp_path, monkeypatch):
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(
        configure_mod, "_probe", lambda _base, path, _timeout: (path == "/filling/api/health", "HTTP 200")
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_kw: [])
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "https://vss.test"])
    assert result.exit_code == 0, result.output
    assert config_mod.load().endpoint("filling") == "https://vss.test/filling"


@pytest.mark.parametrize("available,code", [(False, 3), (True, None)])
def test_empty_source_list_is_distinct_from_unreachable_backend(available, code):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"configured": True, "available": available, "sources": []})
    )
    with FillingClient("https://vss.test/filling", transport=transport) as client:
        if code is not None:
            with pytest.raises(FillingError) as failure:
                client.sources()
            assert failure.value.code == code
        else:
            assert client.sources()["sources"] == []


def test_discovery_reports_filling_command_requirement():
    from vss_cli.configure import _command_availability

    configured = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    assert ("filling", True, "filling") in _command_availability(configured)
    unavailable = config_mod.Deployment(
        base_url="https://vss.test", services={"vst": config_mod.Service("https://vss.test/vst")}
    )
    assert ("filling", False, "needs filling") in _command_availability(unavailable)


class SegmentationFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.segmentation = {
            "source_id": SOURCE["id"],
            "source_sha256": SOURCE["sha256"],
            "stream_id": STREAM,
            "source_clock_origin": ORIGIN,
            "status": "complete",
            "sample_fps": 2,
            "models": {"bottle": {"checkpoint_sha256": "a" * 64}, "liquid": {"checkpoint_sha256": "b" * 64}},
            "provenance": {"stream_id": STREAM, "source_clock_origin": ORIGIN, "mode": "analyzed-replay"},
            "samples": [
                {"t": 10, "instances": [{"id": "frame-20-instance-0"}]},
                {"t": 10.5, "instances": [{"id": "frame-21-instance-0"}]},
            ],
        }
        self.change_clock_after_segmentation = False

    def handle(self, request):
        if "/api/segmentation" not in request.url.path:
            return super().handle(request)
        self.calls.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            assert request.url.params["source_id"] == SOURCE["id"]
        if self.change_clock_after_segmentation:
            self.source = {**self.source, "actual_start_time": "2025-01-01T00:00:01+00:00"}
        return httpx.Response(200, json=self.segmentation)


def test_segmentation_metadata_is_bounded_and_separate_from_measurements():
    fixture = SegmentationFixture()
    with fixture.client() as client:
        result = client.segmentation_get(STREAM)
    assert result["sample_count"] == 2
    assert "samples" not in result
    assert result["models"] == fixture.segmentation["models"]
    assert result["mode"] == "gpu-mask-segmentation"
    assert "not completed bottle-cycle counts" in result["interpretation"]
    assert all("/analysis" not in path and "/query" not in path for _, path, _ in fixture.calls)


def test_segmentation_at_preserves_actual_frame_clock_and_does_not_relabel_time():
    fixture = SegmentationFixture()
    with fixture.client() as client:
        result = client.segmentation_get(STREAM, at=10.24)
    assert result["requested_source_offset"] == 10.24
    assert result["actual_source_offset"] == 10
    assert result["actual_recorded_at"] == "2025-01-01T00:00:10+00:00"
    assert result["samples"] == fixture.segmentation["samples"][:1]
    assert result["samples_omitted"] == 1


def test_segmentation_full_samples_are_explicit():
    fixture = SegmentationFixture()
    with fixture.client() as client:
        result = client.segmentation_get(STREAM, include_samples=True)
    assert result["samples"] == fixture.segmentation["samples"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "different"),
        ("source_sha256", "different"),
        ("stream_id", OTHER),
        ("source_clock_origin", "2025-01-01T00:00:01+00:00"),
        ("provenance", {"stream_id": STREAM, "source_clock_origin": "2025-01-01T00:00:01+00:00"}),
    ],
)
def test_segmentation_rejects_mixed_source_or_clock(field, value):
    fixture = SegmentationFixture()
    fixture.segmentation[field] = value
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.segmentation_get(STREAM)
    assert error.value.code == 4


def test_segmentation_rechecks_clock_after_request():
    fixture = SegmentationFixture()
    fixture.change_clock_after_segmentation = True
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.segmentation_get(STREAM)
    assert error.value.code == 4


@pytest.mark.parametrize(
    "at,include",
    [
        (-1, False),
        (float("nan"), False),
        (float("inf"), False),
        (90, False),
        (10, True),
    ],
)
def test_segmentation_bad_offset_fails_before_result_fetch(at, include):
    fixture = SegmentationFixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.segmentation_get(STREAM, at=at, include_samples=include)
    assert error.value.code == 2
    assert not any(path.endswith("segmentation/result") for _, path, _ in fixture.calls)


def test_segmentation_run_reuses_backend_and_preserves_source_id():
    fixture = SegmentationFixture()
    with fixture.client() as client:
        result = client.segmentation_run(STREAM)
    assert result["status"] == "complete"
    posts = [(path, json.loads(body)) for method, path, body in fixture.calls if method == "POST"]
    assert posts == [("/filling/api/segmentation", {"source_id": SOURCE["id"], "force": False})]


def test_segmentation_timeout_never_restarts_backend():
    fixture = SegmentationFixture()
    fixture.segmentation["status"] = "running"
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.segmentation_run(STREAM, timeout=0.001)
    assert error.value.code == 7
    assert len([method for method, _, _ in fixture.calls if method == "POST"]) == 1


def test_segmentation_unavailable_is_not_empty_mask_success():
    fixture = SegmentationFixture()
    fixture.segmentation.update(status="unavailable", error="Worker not configured", models=None)
    with fixture.client() as client:
        assert client.segmentation_status(STREAM)["status"] == "unavailable"
        with pytest.raises(FillingError, match="Worker not configured"):
            client.segmentation_run(STREAM)


def test_segmentation_cli_grammar_and_source_binding(monkeypatch):
    fixture = SegmentationFixture()
    deployment = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    monkeypatch.setattr(group, "context_from", lambda values: Context(deployment, values.get("pretty")))
    monkeypatch.setattr(group, "FillingClient", lambda _endpoint: fixture.client())
    cli = group.FILLING.cli()
    assert set(cli.commands["segmentation"].commands) == {"run", "status", "get"}
    assert CliRunner().invoke(cli, ["segmentation", "get"]).exit_code == 2
    result = CliRunner().invoke(cli, ["segmentation", "get", "--stream-id", STREAM, "--at", "10.24"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["actual_source_offset"] == 10


NEURAL_CONTRACT = {
    "engine": "rfdetr",
    "algorithm": "rfdetr-mask-cycle-v2",
    "model_hashes": {"bottle": "a" * 64, "liquid": "b" * 64},
    "segmentation_pipeline_sha256": "c" * 64,
}


class NeuralFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.source["expected_measurement"] = json.loads(json.dumps(NEURAL_CONTRACT))
        self.answer.update(
            algorithm=NEURAL_CONTRACT["algorithm"],
            measurement=json.loads(json.dumps(NEURAL_CONTRACT)),
            models={k: {"checkpoint_sha256": v} for k, v in NEURAL_CONTRACT["model_hashes"].items()},
        )


def test_neural_query_and_each_clip_preserve_actual_measurement_identity():
    fixture = NeuralFixture()
    with fixture.client() as client:
        answer = client.query(STREAM, "which finished below reference?")
    assert answer["measurement"] == NEURAL_CONTRACT
    assert answer["video_evidence_source"]["measurement"] == NEURAL_CONTRACT
    assert all(clip["measurement"] == NEURAL_CONTRACT for clip in answer["video_evidences"])
    assert all(clip["source_sha256"] == SOURCE["sha256"] for clip in answer["video_evidences"])


@pytest.mark.parametrize("command", ["query", "results", "status", "analyze", "evidence"])
def test_old_same_source_cache_cannot_be_claimed_as_neural_measurements(command):
    fixture = NeuralFixture()
    fixture.answer.pop("measurement")
    with fixture.client() as client, pytest.raises(FillingError, match="identity is missing") as error:
        if command == "query":
            client.query(STREAM, "underfills")
        elif command == "evidence":
            client.evidence(STREAM, 9, 11)
        else:
            getattr(client, command)(STREAM)
    assert error.value.code == 4
    assert not any(path.endswith("measurement-evidence") for _, path, _ in fixture.calls)


@pytest.mark.parametrize(
    "key,value",
    [
        ("engine", "legacy-calibrated-pixels"),
        ("algorithm", "single-station-pixel-cycle-v1"),
        ("model_hashes", {"bottle": "a" * 64, "liquid": "d" * 64}),
        ("segmentation_pipeline_sha256", "e" * 64),
    ],
)
def test_neural_query_rejects_outdated_engine_models_or_sampling_pipeline(key, value):
    fixture = NeuralFixture()
    fixture.answer["measurement"][key] = value
    with fixture.client() as client, pytest.raises(FillingError, match="current source contract") as error:
        client.query(STREAM, "underfills", include_evidence=False)
    assert error.value.code == 4


def test_actual_model_identity_cannot_conflict_with_measurement_metadata():
    fixture = NeuralFixture()
    fixture.answer["models"]["liquid"]["checkpoint_sha256"] = "d" * 64
    with fixture.client() as client, pytest.raises(FillingError, match="checkpoint metadata"):
        client.results(STREAM)


def test_neural_complete_status_checks_the_actual_cached_result():
    fixture = NeuralFixture()
    with fixture.client() as client:
        state = client.status(STREAM)
    assert state["measurement"] == NEURAL_CONTRACT
    assert any(path.endswith("analysis/result") for _, path, _ in fixture.calls)


def test_contract_change_during_query_is_a_conflict_even_when_source_bytes_match():
    fixture = NeuralFixture()
    original = fixture.handle

    def change_contract(request):
        response = original(request)
        if request.url.path.endswith("/query"):
            fixture.source["expected_measurement"] = {**NEURAL_CONTRACT, "algorithm": "next-version"}
        return response

    with FillingClient("https://vss.test/filling", transport=httpx.MockTransport(change_contract)) as client:
        with pytest.raises(FillingError, match="changed during this command"):
            client.query(STREAM, "underfills")


def test_explicit_legacy_profile_stays_legacy_with_no_neural_hashes():
    fixture = Fixture()
    legacy = {"engine": "legacy-calibrated-pixels", "algorithm": "within-shot-pixels-v1", "model_hashes": {}}
    fixture.source.update(analysis_kind="within-shot-heights", expected_measurement=legacy)
    fixture.answer.update(algorithm=legacy["algorithm"], measurement=legacy)
    with fixture.client() as client:
        result = client.results(STREAM)
    assert result["measurement"] == legacy
    assert "models" not in result


def test_neural_units_preserve_fraction_values_and_do_not_divide_by_reference():
    fixture = NeuralFixture()
    fixture.answer["cycles"] = [
        {"id": "cycle-a", "final_level": 0.6, "reference_level": 0.75},
        {"id": "cycle-b", "final_level": None, "reference_level": 0.75},
    ]
    with fixture.client() as client:
        query = client.query(STREAM, "underfills", include_evidence=False)
        result = client.results(STREAM)
    for value in (query, result):
        assert value["cycles"][0]["final_level"] == 0.6
        assert value["cycles"][0]["reference_level"] == 0.75
        assert value["cycles"][1]["final_level"] is None
        assert value["measurement_units"]["final_level"] == "fraction of detected visible bottle height"
    legacy = Fixture()
    with legacy.client() as client:
        assert "measurement_units" not in client.results(STREAM)

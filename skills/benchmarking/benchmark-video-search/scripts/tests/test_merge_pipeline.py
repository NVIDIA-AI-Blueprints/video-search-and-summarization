# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration boundaries for staged ingestion after the develop merge."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows
import run_eval_flows as rf
from flows import pipeline
from flows.artifacts import RunConfig

ENDPOINT = "http://deployment:8000"
VST = "http://deployment:30888"


@pytest.fixture(autouse=True)
def no_unmocked_deployment_calls(monkeypatch):
    monkeypatch.setattr(
        pipeline.requests.sessions.Session,
        "request",
        Mock(side_effect=AssertionError("unexpected deployment request")),
    )


def config(tmp_path, *options):
    args = rf.parse_args(
        [
            "--endpoint",
            ENDPOINT,
            "--data-dir",
            str(tmp_path),
            "--dataset",
            "warehouse",
            "--no-decompose",
            *options,
        ]
    )
    return RunConfig.from_args(args, vst_url=VST)


def test_source_type_is_carried_from_parser_into_stage_config(tmp_path):
    cfg = config(tmp_path, "--source-type", "rtsp", "--skip-ingest")
    assert cfg.source_type == "rtsp"
    assert cfg.skip_ingest


@pytest.mark.parametrize("stage", ["run", "ingest"])
def test_rtsp_fresh_upload_is_rejected_before_any_backend_or_model_call(
    monkeypatch, stage
):
    argv = [
        "run_eval_flows.py",
        "--endpoint",
        ENDPOINT,
        "--stage",
        stage,
        "--source-type",
        "rtsp",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    for owner, name in [
        (rf, "build_ingest_backend"),
        (rf, "build_query_backend"),
        (flows, "LiveDecomposer"),
        (flows, "ensure_vss_configured"),
    ]:
        monkeypatch.setattr(
            owner, name, Mock(side_effect=AssertionError(f"called {name}"))
        )
    with pytest.raises(SystemExit, match="2|rtsp"):
        rf.main()


def test_retrieve_parser_to_stage_does_not_construct_ingest_backend(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval_flows.py",
            "--endpoint",
            ENDPOINT,
            "--stage",
            "retrieve",
            "--source-type",
            "rtsp",
            "--no-decompose",
            "--skip-download",
            "--data-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(
        rf, "build_ingest_backend", Mock(side_effect=AssertionError("ingest backend"))
    )
    query = object()
    monkeypatch.setattr(rf, "build_query_backend", lambda args: query)
    dispatch = Mock()
    monkeypatch.setattr(pipeline, "run_pipeline", dispatch)
    rf.main()
    cfg = dispatch.call_args.args[0]
    assert cfg.source_type == "rtsp" and cfg.stage == "retrieve"
    assert dispatch.call_args.kwargs["ingest_backend"] is None


def test_score_entrypoint_requires_no_deployment_or_model(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval_flows.py", "--stage", "score", "--retrieval-in", "saved.json"],
    )
    for owner, name in [
        (rf, "build_ingest_backend"),
        (rf, "build_query_backend"),
        (flows, "LiveDecomposer"),
        (flows, "ensure_vss_configured"),
    ]:
        monkeypatch.setattr(
            owner, name, Mock(side_effect=AssertionError(f"called {name}"))
        )
    dispatch = Mock()
    monkeypatch.setattr(pipeline, "run_pipeline", dispatch)
    rf.main()
    cfg, ingest, query, decomposer, fallback = dispatch.call_args.args
    assert cfg.stage == "score" and cfg.endpoint is None
    assert (ingest, query, decomposer, fallback) == (None, None, None, None)


@pytest.mark.parametrize(
    "problem", ["no-queries", "missing-media", "unreadable-dataset"]
)
def test_invalid_fresh_inputs_abort_before_cleanup(monkeypatch, tmp_path, problem):
    cfg = config(tmp_path, "--clear", "--confirm-delete", "--webhooks-confirmed")
    backend = SimpleNamespace(name="vst-direct", describe=dict)
    if problem == "unreadable-dataset":
        monkeypatch.setattr(
            flows, "load_dataset_file", Mock(side_effect=ValueError("bad JSON"))
        )
    else:
        monkeypatch.setattr(
            flows,
            "load_dataset_file",
            lambda *a: {
                "queries": {}
                if problem == "no-queries"
                else {"q": [{"video_name": "clip"}]}
            },
        )
    delete = Mock(side_effect=AssertionError("deleted before preflight"))
    monkeypatch.setattr(pipeline, "clear_all_videos", delete)
    monkeypatch.setattr(
        pipeline, "ingest_videos", Mock(side_effect=AssertionError("uploaded"))
    )
    with pytest.raises(SystemExit, match="before deleting"):
        pipeline.run_ingest(cfg, backend, None)
    delete.assert_not_called()


def test_reused_index_requires_all_annotation_sources_despite_partial_local_download(
    monkeypatch, tmp_path
):
    videos = tmp_path / "warehouse" / "videos"
    videos.mkdir(parents=True)
    (videos / "first.mp4").write_bytes(b"x")
    monkeypatch.setattr(
        flows,
        "load_dataset_file",
        lambda *a: {
            "queries": {
                "q": [{"video_name": "first.mp4"}, {"video_name": "missing.mp4"}]
            }
        },
    )
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {"file-id": {"name": "first", "url": "http://storage/first.mp4"}},
    )
    backend = SimpleNamespace(describe=dict)
    with pytest.raises(SystemExit, match="missing"):
        pipeline.run_ingest(config(tmp_path, "--skip-ingest"), backend, None)


def test_reused_index_records_extra_registered_sources_without_deletion(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        flows,
        "load_dataset_file",
        lambda *a: {"queries": {"q": [{"video_name": "first.mp4"}]}},
    )
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {
            "first-id": {"name": "first", "url": "http://storage/first.mp4"},
            "other-id": {"name": "other", "url": "http://storage/other.mp4"},
        },
    )
    monkeypatch.setattr(
        pipeline.requests, "delete", Mock(side_effect=AssertionError("deleted"))
    )
    manifest = pipeline.run_ingest(
        config(tmp_path, "--skip-ingest"), SimpleNamespace(describe=dict), None
    )
    assert manifest.readiness["reused_index"] == {
        "dataset_sources": 1,
        "other_sources": ["other"],
    }


@pytest.mark.parametrize("status", ["success", "partial", "failure"])
def test_cleanup_keeps_live_cameras_and_validates_response_body(monkeypatch, status):
    camera = {"camera-id": {"name": "live-camera", "url": "rtsp://camera"}}
    snapshots = iter(
        [
            {**camera, "file-id": {"name": "clip", "url": "http://storage/clip.mp4"}},
            camera,
        ]
    )
    monkeypatch.setattr(flows, "list_sensor_sources", lambda _: next(snapshots))
    response = SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": status}
    )
    delete = Mock(return_value=response)
    monkeypatch.setattr(pipeline.requests, "delete", delete)
    result = pipeline.clear_all_videos(ENDPOINT, VST)
    assert delete.call_count == 1
    assert delete.call_args.args[0].endswith("/file-id")
    assert result["kept_rtsp"] == ["live-camera"]
    assert result["deleted"] == (1 if status == "success" else 0)
    assert result["failed"] == ([] if status == "success" else ["clip"])


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--ingest-require", "embed"),
        ("--ingest-quiet-s", "15"),
        ("--ingest-absent-grace-s", "60"),
        ("--ingest-strict", None),
        ("--legacy-index-probe", None),
        ("--ingest-deadline-s", "600"),
        ("--max-decompose-failed-pct", "20"),
    ],
)
def test_deferred_upstream_controls_are_rejected_instead_of_ignored(flag, value):
    argv = ["--endpoint", ENDPOINT, flag]
    if value is not None:
        argv.append(value)
    with pytest.raises(SystemExit) as exc:
        rf.parse_args(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("scheme", ["rtsp", "RTSP", "rtsps", "RTSPS"])
def test_live_source_with_dataset_name_cannot_satisfy_file_reuse(
    monkeypatch, tmp_path, scheme
):
    monkeypatch.setattr(
        flows,
        "load_dataset_file",
        lambda *a: {"queries": {"q": [{"video_name": "clip.mp4"}]}},
    )
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {"camera-id": {"name": "clip", "url": f"{scheme}://camera/live"}},
    )
    with pytest.raises(SystemExit, match="clip"):
        pipeline.run_ingest(
            config(tmp_path, "--skip-ingest"), SimpleNamespace(describe=dict), None
        )


@pytest.mark.parametrize(
    "annotation,registered",
    [
        ("clip.v1.mp4", "clip.v1.mp4"),
        ("clip.v1.mp4", "clip.v1"),
        ("clip.v1", "clip.v1.mp4"),
        ("clip.v1", "clip.v1"),
    ],
)
def test_dotted_names_reuse_the_registered_file_without_double_stemming(
    monkeypatch, tmp_path, annotation, registered
):
    monkeypatch.setattr(
        flows,
        "load_dataset_file",
        lambda *a: {"queries": {"q": [{"video_name": annotation}]}},
    )
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {
            "file-id": {"name": registered, "url": "http://storage/clip.v1.mp4"}
        },
    )
    manifest = pipeline.run_ingest(
        config(tmp_path, "--skip-ingest"), SimpleNamespace(describe=dict), None
    )
    assert manifest.readiness["reused_index"]["dataset_sources"] == 1
    assert manifest.readiness["reused_index"]["other_sources"] == []


@pytest.mark.parametrize(
    "unknown", ["", "http://storage/unknown", "unknown://clip", None]
)
def test_cleanup_refuses_unknown_source_before_deleting_any_valid_files(
    monkeypatch, unknown
):
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {
            "file-id": {"name": "clip", "url": "http://storage/clip.mp4"},
            "unknown-id": {"name": "unknown", "url": unknown},
        },
    )
    delete = Mock(side_effect=AssertionError("deleted before classifying inventory"))
    monkeypatch.setattr(pipeline.requests, "delete", delete)
    with pytest.raises(SystemExit):
        pipeline.clear_all_videos(ENDPOINT, VST)
    delete.assert_not_called()


@pytest.mark.parametrize("scheme", ["RTSP", "rtsps", "RTSPS"])
def test_cleanup_preserves_case_insensitive_and_secure_rtsp(monkeypatch, scheme):
    monkeypatch.setattr(
        flows,
        "list_sensor_sources",
        lambda _: {
            "camera-id": {"name": "live-camera", "url": f"{scheme}://camera/live"}
        },
    )
    delete = Mock(side_effect=AssertionError("deleted live camera"))
    monkeypatch.setattr(pipeline.requests, "delete", delete)
    result = pipeline.clear_all_videos(ENDPOINT, VST)
    assert result["deleted"] == 0
    assert result["kept_rtsp"] == ["live-camera"]
    delete.assert_not_called()

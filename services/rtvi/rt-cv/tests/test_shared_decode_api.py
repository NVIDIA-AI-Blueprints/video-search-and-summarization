import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "src" / "rtvi_cv_shared_api.py"
SPEC = importlib.util.spec_from_file_location("rtvi_cv_shared_api", MODULE_PATH)
shared_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(shared_api)


def test_shared_uri_validates_and_maps_cuda_ipc_source(tmp_path, monkeypatch):
    socket_root = tmp_path / "broker"
    status_root = tmp_path / "status"
    socket_root.mkdir()
    status_root.mkdir()
    monkeypatch.setattr(shared_api, "_SOCKET_ROOT", socket_root.resolve())
    monkeypatch.setattr(shared_api, "_STATUS_DIR", status_root)

    uri = shared_api._shared_uri(
        "camera-1",
        {
            "type": "shared_decode",
            "protocol_version": "1.0",
            "control_socket": str(socket_root / "job.sock"),
            "session_token": "ephemeral",
            "consumer_id": "rtvi-cv",
            "transport": "cuda_ipc",
            "device_id": 0,
        },
    )

    assert uri.startswith("vssipc:///")
    assert "gpu_id=0" in uri
    assert (status_root / "camera-1.json").is_file()


@pytest.mark.parametrize(
    "override",
    [
        {"transport": "owned_cpu"},
        {"protocol_version": "2.0"},
        {"session_token": ""},
        {"device_id": -1},
        {"control_socket": "/tmp/outside.sock"},
    ],
)
def test_shared_uri_rejects_invalid_contract(tmp_path, monkeypatch, override):
    socket_root = tmp_path / "broker"
    status_root = tmp_path / "status"
    socket_root.mkdir()
    status_root.mkdir()
    monkeypatch.setattr(shared_api, "_SOCKET_ROOT", socket_root.resolve())
    monkeypatch.setattr(shared_api, "_STATUS_DIR", status_root)
    source = {
        "type": "shared_decode",
        "protocol_version": "1.0",
        "control_socket": str(socket_root / "job.sock"),
        "session_token": "ephemeral",
        "consumer_id": "rtvi-cv",
        "transport": "cuda_ipc",
        "device_id": 0,
    }
    source.update(override)

    with pytest.raises(ValueError):
        shared_api._shared_uri("camera-1", source)


def test_camera_url_rejects_direct_vssipc_uri():
    with pytest.raises(ValueError, match="reserved for frame_source"):
        shared_api._validate_camera_url(
            "vssipc:///run/vss/frame-broker/job.sock?status_file=/tmp/attacker-selected-file"
        )


@pytest.mark.parametrize(
    "camera_url",
    ["rtsp://example.test/live", "http://example.test/video.mp4"],
)
def test_camera_url_accepts_non_reserved_schemes(camera_url):
    shared_api._validate_camera_url(camera_url)

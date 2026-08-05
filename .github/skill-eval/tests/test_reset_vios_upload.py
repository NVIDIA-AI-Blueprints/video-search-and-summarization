import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE = REPO_ROOT / ".github/skill-eval/fixtures/reset_vios_upload.py"


def load_module():
    spec = importlib.util.spec_from_file_location("reset_vios_upload", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_remove_uploaded_file_uses_timeline(monkeypatch):
    module = load_module()
    calls = []

    def fake_request(_base, path, method="GET"):
        calls.append((method, path))
        if path == "sensor/sensor-1/streams":
            return [{
                "streamId": "stream-1",
                "isMain": True,
                "url": "/home/vst/video.mp4",
            }]
        if path == "storage/stream-1/timelines":
            return {"stream-1": [{
                "startTime": "2025-01-01T00:00:00.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }]}
        return {"spaceSaved": 1}

    monkeypatch.setattr(module, "request", fake_request)
    module.remove_sensor("http://vst", {"sensorId": "sensor-1"})

    method, path = calls[-1]
    assert method == "DELETE"
    assert path.startswith("storage/file/stream-1?")
    assert "startTime=2025-01-01T00%3A00%3A00.000Z" in path


def test_refuses_to_delete_rtsp_sensor(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "request", lambda *_args, **_kwargs: [{
        "streamId": "stream-1", "isMain": True,
        "url": "rtsp://camera/live",
    }])

    try:
        module.remove_sensor("http://vst", {"sensorId": "sensor-1"})
    except RuntimeError as exc:
        assert "RTSP" in str(exc)
    else:
        raise AssertionError("RTSP sensor deletion was not rejected")


def test_remove_uploaded_file_without_timeline_uses_file_id(monkeypatch):
    module = load_module()
    calls = []

    def fake_request(_base, path, method="GET"):
        calls.append((method, path))
        if path == "sensor/sensor-1/streams":
            return [{
                "streamId": "stream-1",
                "isMain": True,
                "url": "/home/vst/video.mp4",
            }]
        if path == "storage/stream-1/timelines":
            return {"stream-1": []}
        if path == "storage/file/sensor-1/list":
            return {"sensor-1": [{"metadata": {"id": "file-1"}}]}
        if path == "sensor/list":
            return [{"sensorId": "sensor-1"}]
        return {}

    monkeypatch.setattr(module, "request", fake_request)
    module.remove_sensor("http://vst", {"sensorId": "sensor-1"})

    assert ("DELETE", "storage/file?id=file-1") in calls
    assert ("DELETE", "sensor/sensor-1") in calls


def test_matching_storage_streams(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "request", lambda *_args: [
        {"stream-1": [{"name": "warehouse_safety_0001"}]},
        {"stream-2": [{"name": "other"}]},
    ])

    matches = module.matching_storage_streams(
        "http://vst", "warehouse_safety_0001"
    )

    assert matches == [{"name": "warehouse_safety_0001"}]


def test_restart_waits_for_all_services(monkeypatch):
    module = load_module()
    urls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda url, **_kwargs: urls.append(url) or Response(),
    )

    module.restart_services("http://localhost:30888/vst/api/v1")

    assert urls == [
        "http://localhost:30888/vst/api/v1/sensor/version",
        "http://localhost:8018/v1/models",
        "http://localhost:8000/docs",
    ]

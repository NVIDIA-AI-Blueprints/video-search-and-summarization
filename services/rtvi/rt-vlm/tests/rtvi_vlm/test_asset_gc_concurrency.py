# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import pickle
from multiprocessing.reduction import ForkingPickler
from threading import Event, Thread

import pytest

import utils.asset_manager as asset_manager_module
from common.service_exception import ServiceException
from utils.asset_manager import Asset, AssetManager


def _asset(tmp_path, asset_id):
    asset_dir = tmp_path / asset_id
    asset_dir.mkdir()
    path = asset_dir / "video.mp4"
    path.touch()
    return Asset(asset_id, str(path), "vision", "video", str(asset_dir))


def test_live_asset_command_payload_is_picklable(tmp_path):
    asset = Asset(
        "live",
        "rtsp://example.com/live",
        "vision",
        "video",
        str(tmp_path),
        username="user",
        password="password",
        sensor_name="sensor",
        camera_id="camera",
    )

    payload = pickle.loads(
        ForkingPickler.dumps({"command": "start-live-stream", "asset": asset})
    )
    restored = payload["asset"]

    assert restored.asset_id == asset.asset_id
    assert restored.path == asset.path
    assert restored.username == asset.username
    assert restored.password == asset.password
    assert restored.sensor_name == asset.sensor_name
    assert restored.camera_id == asset.camera_id
    restored.lock()
    assert restored.use_count == 1
    restored.unlock()
    assert restored.use_count == 0


def test_age_out_uses_stable_asset_snapshot(tmp_path, monkeypatch):
    manager = AssetManager(str(tmp_path))
    manager._max_storage_usage_gb = 1
    manager._publish_asset(_asset(tmp_path, "first"))
    manager._publish_asset(_asset(tmp_path, "second"))

    async def storage_usage(*_args, **_kwargs):
        return 1.0

    threshold_calls = 0

    async def above_threshold():
        nonlocal threshold_calls
        threshold_calls += 1
        return threshold_calls == 1

    entered_iteration = Event()
    resume_iteration = Event()
    original_exists = os.path.exists

    def blocking_exists(path):
        if not entered_iteration.is_set():
            entered_iteration.set()
            assert resume_iteration.wait(2)
        return original_exists(path)

    def register_asset():
        assert entered_iteration.wait(2)
        manager._publish_asset(_asset(tmp_path, "third"))
        resume_iteration.set()

    monkeypatch.setattr(manager, "_get_storage_usage", storage_usage)
    monkeypatch.setattr(manager, "_is_storage_above_threshold", above_threshold)
    monkeypatch.setattr(asset_manager_module.os.path, "exists", blocking_exists)

    register_thread = Thread(target=register_asset)
    register_thread.start()
    asyncio.run(manager._age_out_assets())
    register_thread.join(timeout=2)

    assert not register_thread.is_alive()
    assert manager.check_asset_exists("third")


def test_evicted_asset_reference_cannot_be_reacquired(tmp_path):
    manager = AssetManager(str(tmp_path))
    asset = _asset(tmp_path, "asset")
    manager._publish_asset(asset)
    stale_reference = manager.get_asset(asset.asset_id)

    manager.cleanup_asset(asset.asset_id)

    with pytest.raises(ServiceException) as exc_info:
        stale_reference.lock()

    assert exc_info.value.code == "DependencyError"
    assert exc_info.value.status_code == 503


def test_age_out_skips_asset_removed_after_snapshot(tmp_path, monkeypatch):
    manager = AssetManager(str(tmp_path))
    manager._max_storage_usage_gb = 1
    manager._publish_asset(_asset(tmp_path, "removed"))

    async def storage_usage(*_args, **_kwargs):
        return 1.0

    threshold_calls = 0

    async def above_threshold():
        nonlocal threshold_calls
        threshold_calls += 1
        return threshold_calls <= 2

    monkeypatch.setattr(manager, "_get_storage_usage", storage_usage)
    monkeypatch.setattr(manager, "_is_storage_above_threshold", above_threshold)

    def missing_asset(_asset_id):
        raise ServiceException("No such resource", "BadParameter", 400)

    monkeypatch.setattr(manager, "get_asset", missing_asset)

    asyncio.run(manager._age_out_assets())


def test_reused_asset_id_is_not_shadowed_by_age_out_history(tmp_path):
    manager = AssetManager(str(tmp_path))
    asset_id = "reused"
    old_asset = _asset(tmp_path, asset_id)
    manager._publish_asset(old_asset)
    manager.cleanup_asset(asset_id)
    manager._aged_out_assets.append(asset_id)

    replacement = Asset(asset_id, "/tmp/reused.mp4", "vision", "video", "")
    manager._publish_asset(replacement)

    assert manager.get_asset(asset_id) is replacement
    manager.cleanup_asset(asset_id)
    assert not manager.check_asset_exists(asset_id)

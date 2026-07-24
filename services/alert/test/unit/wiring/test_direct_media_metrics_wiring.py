# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metric callback wiring for the shared DirectMediaHandler."""

import importlib.util
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _load_handler_module():
    """Load the handler outside test modules that stub handlers.*."""
    alert_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    direct_media_root = os.path.join(
        alert_root, "src", "handlers", "direct_media"
    )
    package_name = "_direct_media_metrics_pkg"
    package = types.ModuleType(package_name)
    package.__path__ = [direct_media_root]
    sys.modules[package_name] = package

    for module_name in ("media_downloader", "direct_media_handler"):
        qualified_name = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified_name,
            os.path.join(direct_media_root, f"{module_name}.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)

    return sys.modules[f"{package_name}.direct_media_handler"]


handler_module = _load_handler_module()
DirectMediaHandler = handler_module.DirectMediaHandler


def _handler(observer=None):
    return DirectMediaHandler(
        vlm_client=MagicMock(),
        vlm_enhanced_event_sink=MagicMock(),
        config={
            "alert_agent": {
                "media_download": {
                    "enabled": True,
                    "use_verdict": False,
                }
            },
            "vlm": {"model": "test-model"},
        },
        vlm_duration_observer=observer,
    )


def _message():
    return {
        "id": "ondemand-test",
        "sensorId": "cam-1",
        "category": "collision",
        "info": {
            "media_urls": ["https://example.test/video.mp4"],
            "media_type": "video",
        },
    }


def test_successful_vlm_attempt_invokes_duration_observer():
    observer = MagicMock()
    handler = _handler(observer)
    message = _message()

    with patch.object(
        handler_module,
        "analyze_single_media",
        return_value=SimpleNamespace(content="clear"),
    ):
        handler.evaluate(
            worker_id=0,
            message=message,
            info_block=message["info"],
            user_prompt="prompt",
            system_prompt="system",
        )

    observer.assert_called_once()
    duration, sensor_id = observer.call_args.args
    assert duration >= 0
    assert sensor_id == "cam-1"


def test_failed_vlm_attempt_still_invokes_duration_observer():
    observer = MagicMock()
    handler = _handler(observer)
    message = _message()

    with patch.object(
        handler_module,
        "analyze_single_media",
        side_effect=RuntimeError("vlm unavailable"),
    ):
        handler.evaluate(
            worker_id=0,
            message=message,
            info_block=message["info"],
            user_prompt="prompt",
            system_prompt="system",
        )

    observer.assert_called_once()
    assert message["info"]["verdict"] == "verification-failed"


def test_kafka_default_has_no_duration_observer():
    handler = _handler()
    assert handler._vlm_duration_observer is None

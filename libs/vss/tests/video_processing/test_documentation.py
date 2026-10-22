# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Machine-readable public contracts and documented dependency recovery.

Inspect signatures/schema structure, not particular docstring wording. Native
dependency failures run in fresh interpreters so an earlier optional import
cannot accidentally satisfy the missing-dependency case.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize(
    "name,positional,keyword,awaitable",
    [
        ("probe_video", ("source",), ("source_id", "options"), True),
        ("probe_video_sync", ("source",), ("source_id", "options"), False),
        ("decode_frames", ("source", "plan"), ("source_id", "options"), False),
        ("extract_frames", ("source", "timestamps"), ("source_id", "options"), True),
        (
            "extract_frames_sync",
            ("source", "timestamps"),
            ("source_id", "options"),
            False,
        ),
        (
            "subsample_frames",
            ("source", "policy"),
            ("window", "source_id", "options"),
            False,
        ),
        ("materialize_clip", ("source", "start", "end"), ("source_id",), True),
    ],
)
def test_public_signatures_expose_typed_async_and_sync_contracts(
    processing: Any,
    name: str,
    positional: tuple[str, ...],
    keyword: tuple[str, ...],
    awaitable: bool,
) -> None:
    function = getattr(processing, name)
    signature = inspect.signature(function)
    assert inspect.iscoroutinefunction(function) is awaitable
    assert signature.return_annotation is not inspect.Signature.empty
    assert inspect.getdoc(function)
    for parameter in positional:
        field = signature.parameters[parameter]
        assert field.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert field.default is inspect.Parameter.empty
        assert field.annotation is not inspect.Parameter.empty
    for parameter in keyword:
        field = signature.parameters[parameter]
        assert field.kind is inspect.Parameter.KEYWORD_ONLY
        assert field.default is None
        assert field.annotation is not inspect.Parameter.empty


@pytest.mark.parametrize("method", ["to_base64", "to_encoded"])
def test_frame_transfer_signature_has_explicit_format_default(
    processing: Any, method: str
) -> None:
    function = getattr(processing.Frame, method)
    signature = inspect.signature(function)
    assert signature.parameters["format"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["format"].default == "jpeg"
    assert signature.return_annotation is not inspect.Signature.empty
    assert inspect.getdoc(function)


@pytest.mark.parametrize(
    "model",
    [
        "MediaFacts",
        "TimeWindow",
        "DecodePlan",
        "SamplingPolicy",
        "DecodeOptions",
        "FrameMetadata",
        "EncodedFrame",
    ],
)
def test_wire_schema_has_described_fields_version_and_closed_shape(
    processing: Any, model: str
) -> None:
    schema = getattr(processing, model).model_json_schema()
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert properties["schema_version"]["const"] == "1.0"
    assert properties["schema_version"]["default"] == "1.0"
    for name, field in properties.items():
        assert field.get("description", "").strip(), f"{model}.{name} lacks description"


def test_decode_options_schema_reports_actual_defaults(processing: Any) -> None:
    properties = processing.DecodeOptions.model_json_schema()["properties"]
    for name, value in {
        "decoder": "auto",
        "device_id": 0,
        "timestamp_mode": "best_effort",
        "batch_size": 8,
        "max_concurrency": 4,
    }.items():
        assert properties[name]["default"] == value


@pytest.mark.parametrize("operation", ["probe", "base64", "encoded"])
def test_missing_opencv_is_actionable_without_eager_import(
    video_path: Path, operation: str
) -> None:
    script = textwrap.dedent(
        """
        import asyncio
        import importlib.abc
        import sys
        import numpy as np

        class MissingOpenCV(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'cv2' or fullname.startswith('cv2.'):
                    raise ModuleNotFoundError('OpenCV intentionally absent', name='cv2')

        sys.meta_path.insert(0, MissingOpenCV())
        import vss.video_processing as processing
        operation, path = sys.argv[1:]
        try:
            if operation == 'probe':
                asyncio.run(processing.probe_video(path))
            else:
                frame = processing.Frame(
                    tensor=np.zeros((2, 3, 3), dtype=np.uint8),
                    source_id='video', timestamp_ns=0, index=0,
                    timestamp_quality='pts', decoder='memory', device='cpu',
                )
                if operation == 'base64':
                    frame.to_base64()
                else:
                    frame.to_encoded()
        except processing.DecoderUnavailableError as error:
            assert 'opencv' in str(error).lower() or 'cv2' in str(error).lower()
        else:
            raise AssertionError('missing optional OpenCV did not raise typed error')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, operation, str(video_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_native_image_encoding_failure_uses_decode_error() -> None:
    script = textwrap.dedent(
        """
        import sys
        import types
        import numpy as np
        cv2 = types.ModuleType('cv2')
        cv2.__version__ = '4.13.0'
        cv2.COLOR_RGB2BGR = 4
        cv2.IMWRITE_JPEG_QUALITY = 1
        cv2.error = RuntimeError
        cv2.cvtColor = lambda pixels, code: pixels[..., ::-1].copy()
        cv2.imencode = lambda *args, **kwargs: (False, None)
        sys.modules['cv2'] = cv2
        import vss.video_processing as processing
        frame = processing.Frame(
            tensor=np.zeros((2, 3, 3), dtype=np.uint8),
            source_id='video', timestamp_ns=0, index=0,
            timestamp_quality='pts', decoder='memory', device='cpu',
        )
        try:
            frame.to_base64()
        except processing.DecodeError:
            pass
        else:
            raise AssertionError('native encoder failure did not raise DecodeError')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr

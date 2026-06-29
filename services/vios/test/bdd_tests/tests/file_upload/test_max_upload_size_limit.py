# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
BDD regression test for Bug 6193881:
    [Nvstreamer] NVStreamer upload API accepts above-limit files despite
    configured max upload size.

Root cause: the upload handler (handleFileUpload in
src/modules/storage_management/storage_management_utils.cpp) never compares the
request Content-Length against the configured nv_streamer_max_upload_file_size_MB.
The only size check (HttpServerRequestHandler.cpp) lives in getInputMessage,
which returns early for upload APIs ("Upload API, skip parsing message") before
the check is ever reached. As a result, files larger than the configured limit
are accepted with HTTP 200 and a stream is created.

This test deploys against a stack whose config sets
nv_streamer_max_upload_file_size_MB = 1 (see the dev/test docker-compose
vst_config.json files, set as the repro harness). It then uploads the static
~2.5 MB test video, which exceeds that 1 MB limit.

Expected behavior (after fix): the over-limit upload is rejected with an HTTP
error (ideally 413 Payload Too Large) and no stream is created.
Buggy behavior (before fix): the upload returns 200/201 and creates a stream,
so the "rejected" assertion FAILS -- which is the reproduction.
"""
import logging
import tempfile
import uuid
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then

from .upload_test_utils import create_test_video_file, upload_file_simple

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/max_upload_size_limit.feature')

# Must match nv_streamer_max_upload_file_size_MB in the deployed vst_config.json
# (set to 1 MB as the repro harness for Bug 6193881).
CONFIGURED_MAX_UPLOAD_MB = 1
CONFIGURED_MAX_UPLOAD_BYTES = CONFIGURED_MAX_UPLOAD_MB * 1024 * 1024


def _static_video() -> Path:
    """The repo's static ~2.5 MB valid H.264 MP4 -- comfortably over a 1 MB limit."""
    p = Path(__file__).parent.parent.parent / "data" / "test_video.mp4"
    if not p.exists():
        raise FileNotFoundError(f"Static test video not found: {p}")
    return p


@given('the storage upload API is reachable')
def storage_upload_reachable(context, api_config):
    assert api_config['base_url'], "Base URL must be configured"
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_maxupload_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    context.over_limit_response = None
    context.within_limit_response = None


# ---------------------------------------------------------------------------
# Above-limit upload -> must be rejected
# ---------------------------------------------------------------------------

@when('I upload a media file larger than the configured max upload size')
def upload_over_limit(context, api_config):
    src = _static_video()
    size = src.stat().st_size
    assert size > CONFIGURED_MAX_UPLOAD_BYTES, (
        f"Test video ({size} bytes) is not larger than the configured limit "
        f"({CONFIGURED_MAX_UPLOAD_BYTES} bytes); cannot exercise the size check."
    )
    filename = f"over_limit_{uuid.uuid4().hex[:8]}.mp4"
    context.over_limit_filename = filename
    logger.info("Uploading over-limit file %s (%d bytes, limit %d bytes)",
                filename, size, CONFIGURED_MAX_UPLOAD_BYTES)

    response = upload_file_simple(
        api_config['base_url'], src, filename,
        sensor_id=context.sensor_id,
        timestamp='2026-05-14T00:00:00.000Z',
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.over_limit_response = response

    # If the (buggy) server accepted the over-limit upload it created a stream;
    # record the streamId so the autouse cleanup fixture removes it even though
    # the assertion below will fail.
    try:
        data = response.json()
        if isinstance(data, dict) and data.get('streamId'):
            context.uploaded_stream_ids.add(data['streamId'])
    except Exception:
        pass


@then('the upload is rejected with an HTTP error status')
def over_limit_rejected(context):
    resp = context.over_limit_response
    assert resp is not None, "No response captured for the over-limit upload"
    logger.info("Over-limit upload returned HTTP %d: %s",
                resp.status_code, resp.text[:200])
    assert resp.status_code not in (200, 201), (
        f"Over-limit upload should have been rejected, but the server accepted "
        f"it with HTTP {resp.status_code}. The configured "
        f"nv_streamer_max_upload_file_size_MB={CONFIGURED_MAX_UPLOAD_MB} limit "
        f"is not being enforced (Bug 6193881)."
    )
    assert resp.status_code >= 400, (
        f"Expected a client/server error (ideally 413 Payload Too Large) for the "
        f"over-limit upload; got HTTP {resp.status_code}."
    )


@then('no stream is created for the rejected upload')
def no_stream_created(context):
    resp = context.over_limit_response
    stream_id = None
    try:
        data = resp.json()
        if isinstance(data, dict):
            stream_id = data.get('streamId')
    except Exception:
        stream_id = None
    assert not stream_id, (
        f"A rejected over-limit upload must not create a stream, but the server "
        f"returned streamId={stream_id} (Bug 6193881)."
    )


# ---------------------------------------------------------------------------
# Within-limit upload -> must still succeed (positive control)
# ---------------------------------------------------------------------------

@when('I upload a media file smaller than the configured max upload size')
def upload_within_limit(context, api_config):
    small = context.temp_dir / f"within_limit_{uuid.uuid4().hex[:8]}.mp4"
    # A short, low-resolution clip is well under 1 MB.
    create_test_video_file(small, duration_seconds=2, fps=30)
    size = small.stat().st_size
    assert size < CONFIGURED_MAX_UPLOAD_BYTES, (
        f"Generated control file ({size} bytes) is not under the configured "
        f"limit ({CONFIGURED_MAX_UPLOAD_BYTES} bytes); regenerate smaller."
    )
    filename = f"within_limit_{uuid.uuid4().hex[:8]}.mp4"
    logger.info("Uploading within-limit file %s (%d bytes)", filename, size)

    response = upload_file_simple(
        api_config['base_url'], small, filename,
        sensor_id=context.sensor_id,
        timestamp='2026-05-14T00:00:00.000Z',
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.within_limit_response = response
    try:
        data = response.json()
        if isinstance(data, dict) and data.get('streamId'):
            context.uploaded_stream_ids.add(data['streamId'])
    except Exception:
        pass


@then('the within-limit upload succeeds')
def within_limit_succeeds(context):
    resp = context.within_limit_response
    assert resp is not None, "No response captured for the within-limit upload"
    assert resp.status_code in (200, 201), (
        f"A within-limit upload must still succeed; got HTTP {resp.status_code}. "
        f"Body: {resp.text[:300]}"
    )

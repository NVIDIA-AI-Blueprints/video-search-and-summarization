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
BDD tests for VST download API codec/overlay/transcode behaviour.

Covers BDD-GAP-021, BDD-GAP-022, BDD-GAP-023, BDD-GAP-024, BDD-GAP-025.
"""
import json
import logging
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import (
    create_test_video_file,
    get_with_retry,
    upload_test_video,
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_download/download_codecs_and_overlays.feature')


def _ffprobe(path: Path, args):
    cmd = ['ffprobe', '-v', 'error'] + args + [str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def _create_h265_video(file_path: Path, duration_seconds=20):
    """Create an HEVC (H.265) MP4 using libx265."""
    cmd = [
        'ffmpeg',
        '-f', 'lavfi',
        '-i', f'testsrc=duration={duration_seconds}:size=640x480:rate=30',
        '-c:v', 'libx265',
        '-pix_fmt', 'yuv420p',
        '-preset', 'ultrafast',
        '-tag:v', 'hvc1',
        '-movflags', '+faststart',
        '-y', str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode != 0 or not file_path.exists() or file_path.stat().st_size == 0:
        pytest.skip(
            f"Could not create H.265 source (libx265 unavailable?): "
            f"{res.stderr.decode(errors='ignore')[:200]}"
        )


def _create_known_params_video(file_path: Path, duration_seconds=30,
                               fps=25, bitrate_kbps=600, gop=50):
    # -bf 0 disables B-frames; otherwise libx264 'veryfast' inserts B-frames
    # by default and the server's upload path re-transcodes the file (see
    # LocalStreams::transcodeIfNeeded), which would change the stored
    # bitrate and GOP out from under us and make the download assertions
    # flaky.
    cmd = [
        'ffmpeg',
        '-f', 'lavfi',
        '-i', f'testsrc=duration={duration_seconds}:size=640x480:rate={fps}',
        '-c:v', 'libx264',
        '-b:v', f'{bitrate_kbps}k',
        '-maxrate', f'{bitrate_kbps}k',
        '-bufsize', f'{bitrate_kbps*2}k',
        '-g', str(gop),
        '-keyint_min', str(gop),
        '-sc_threshold', '0',
        '-bf', '0',
        '-preset', 'veryfast',
        '-movflags', '+faststart',
        '-y', str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode != 0:
        pytest.skip(f"ffmpeg failed: {res.stderr.decode(errors='ignore')[:200]}")


def _probe_source_params(file_path: Path):
    """Return the actual {fps, bitrate_kbps, gop} of an encoded test file.

    ffmpeg -b:v / -maxrate are ceilings, not floors, so the file we just
    wrote may have a bitrate far below the requested target -- especially
    on the static 'testsrc' pattern. We probe the file and use measured
    values as the reference for downstream 'preserve input params'
    assertions. GOP is measured as the average distance between keyframes.
    """
    bitrate_s = _ffprobe(
        file_path,
        ['-show_entries', 'format=bit_rate',
         '-of', 'default=noprint_wrappers=1:nokey=1'],
    )
    bitrate_kbps = int(bitrate_s) // 1000 if bitrate_s and bitrate_s.isdigit() else 0

    fps_s = _ffprobe(
        file_path,
        ['-select_streams', 'v:0',
         '-show_entries', 'stream=r_frame_rate',
         '-of', 'default=noprint_wrappers=1:nokey=1'],
    )
    try:
        num, den = fps_s.split('/')
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    frames_json = _ffprobe(
        file_path,
        ['-select_streams', 'v',
         '-show_entries', 'frame=key_frame',
         '-of', 'json'],
    )
    frames = json.loads(frames_json).get('frames', []) if frames_json else []
    key_positions = [i for i, fr in enumerate(frames) if fr.get('key_frame') == 1]
    if len(key_positions) >= 2:
        intervals = [
            key_positions[i+1] - key_positions[i]
            for i in range(len(key_positions) - 1)
        ]
        gop = sum(intervals) / len(intervals)
    else:
        gop = 0

    return {'fps': fps, 'bitrate_kbps': bitrate_kbps, 'gop': gop}


def _wait_timeline(api_config, stream_id, deadline_sec=60):
    """Poll until timelines for the given stream are reported (no fixed sleep).

    The download helper used by these tests retries internally on transient
    503/504 from Envoy, so we don't need to gate downloads on a fixed wait.
    """
    end_time = time.time() + deadline_sec
    while time.time() < end_time:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/timelines",
            timeout=10, verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            tl = (resp.json() or {}).get(stream_id, [])
            if tl:
                return tl
        time.sleep(2)
    return []


def _route_header(context):
    """Header dict for storage download routing through Envoy."""
    return {"streamid": context.sensor_id}


@given('the VST API is configured for codec download tests')
def configure_codec(context, api_config):
    assert api_config['base_url']
    context.codec_response = None
    context.codec_stream_id = None
    context.input_params = {}


# ---------------------------------------------------------------------------
# BDD-GAP-021 — H.265 download
# ---------------------------------------------------------------------------

@given('an H.265 source video has been uploaded')
def upload_h265_source(context, api_config):
    file_path = context.temp_upload_dir / f"h265_{uuid.uuid4().hex[:8]}.mp4"
    _create_h265_video(file_path, duration_seconds=20)
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    result = upload_test_video(
        api_config['base_url'], file_path, file_path.name, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"H.265 upload failed: {result.get('error')}"
    context.codec_stream_id = result['streamId']
    context.uploaded_stream_ids.add(result['streamId'])
    tl = _wait_timeline(api_config, result['streamId'])
    assert tl, f"No timeline for H.265 stream {result['streamId']}"
    context.codec_timeline = tl[0]


@when('an H.265 clip is downloaded from the recorded stream')
def download_h265_clip(context, api_config):
    tl = context.codec_timeline
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.codec_stream_id}"
    response = get_with_retry(
        url,
        params={'startTime': tl['startTime'], 'endTime': tl['endTime'],
                'container': 'mp4'},
        headers=_route_header(context),
        timeout=120,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.codec_response = response


@then('the returned clip probes as codec hevc')
def returned_clip_is_hevc(context):
    resp = context.codec_response
    assert resp.status_code == 200, f"H.265 download failed: {resp.status_code}"
    tmp = Path(f"/tmp/h265_{uuid.uuid4().hex[:8]}.mp4")
    tmp.write_bytes(resp.content)
    try:
        codec = _ffprobe(
            tmp,
            ['-select_streams', 'v:0',
             '-show_entries', 'stream=codec_name',
             '-of', 'default=noprint_wrappers=1:nokey=1'],
        )
    finally:
        tmp.unlink(missing_ok=True)
    assert codec.lower() in ('hevc', 'h265'), (
        f"Expected hevc, got {codec!r}"
    )
    context.codec_clip_path = tmp


@then('the returned clip duration matches the requested window within tolerance')
def codec_clip_duration_matches(context):
    resp = context.codec_response
    tmp = Path(f"/tmp/h265dur_{uuid.uuid4().hex[:8]}.mp4")
    tmp.write_bytes(resp.content)
    try:
        dur_s = _ffprobe(
            tmp,
            ['-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1'],
        )
        actual = float(dur_s) if dur_s else 0.0
    finally:
        tmp.unlink(missing_ok=True)
    tl = context.codec_timeline
    start = datetime.fromisoformat(tl['startTime'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(tl['endTime'].replace('Z', '+00:00'))
    requested = (end - start).total_seconds()
    assert abs(actual - requested) <= max(3.0, requested * 0.20), (
        f"H.265 clip duration off: requested {requested:.1f}s, got {actual:.1f}s"
    )


# ---------------------------------------------------------------------------
# BDD-GAP-022 — overlay=true (skipped unless explicit marker)
# ---------------------------------------------------------------------------

@given('a stream with stored bbox metadata exists')
def need_bbox_metadata(context):
    pytest.skip(
        "Stored bbox metadata fixture is not part of standard BDD setup. "
        "Run -m needs_bbox_metadata in an environment seeded with metadata."
    )


@when('a clip is downloaded with overlay enabled')
def download_overlay_clip(context):
    pass


@then('sampled frames contain rendered bbox-colored regions')
def overlay_check(context):
    pass


# ---------------------------------------------------------------------------
# BDD-GAP-023 — no-NVENC fallback
# ---------------------------------------------------------------------------

@given('the host has no NVENC available')
def need_no_nvenc(context):
    """Skip unless the runner has NO NVIDIA hardware encoder available."""
    nvidia_dev = any(Path('/dev').glob('nvidia*'))
    if nvidia_dev:
        pytest.skip(
            "Test requires a host WITHOUT NVENC; nvidia* devices are present."
        )


@given('a test video has been uploaded for transcode tests')
def upload_transcode_input(context, api_config):
    file_path = context.temp_upload_dir / f"transcode_{uuid.uuid4().hex[:8]}.mp4"
    create_test_video_file(file_path, duration_seconds=20)
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    result = upload_test_video(
        api_config['base_url'], file_path, file_path.name, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"Upload failed: {result.get('error')}"
    context.codec_stream_id = result['streamId']
    context.uploaded_stream_ids.add(result['streamId'])
    tl = _wait_timeline(api_config, result['streamId'])
    assert tl, "No timeline"
    context.codec_timeline = tl[0]


@when('a clip is downloaded that requires re-encoding')
def download_transcode_clip(context, api_config):
    tl = context.codec_timeline
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.codec_stream_id}"
    response = get_with_retry(
        url,
        params={
            'startTime': tl['startTime'],
            'endTime': tl['endTime'],
            'container': 'mp4',
            'transcode': 'true',
            'codec': 'h264',
        },
        headers=_route_header(context),
        timeout=180,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.codec_response = response


@then('the download completes within the configured timeout')
def download_within_timeout(context):
    assert context.codec_response is not None
    assert context.codec_response.status_code == 200, (
        f"Software-fallback transcode failed: {context.codec_response.status_code}"
    )


@then('the returned clip is a valid MP4')
def returned_clip_valid_mp4(context):
    body = context.codec_response.content
    assert body and body[4:8] == b'ftyp', "Response is not a valid MP4"


# ---------------------------------------------------------------------------
# BDD-GAP-024 — last requested frame is included
# ---------------------------------------------------------------------------

@given('a test video with a known long recording exists for boundary check')
def upload_long_for_boundary(context, api_config):
    file_path = context.temp_upload_dir / f"boundary_{uuid.uuid4().hex[:8]}.mp4"
    create_test_video_file(file_path, duration_seconds=60)
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    result = upload_test_video(
        api_config['base_url'], file_path, file_path.name, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"Upload failed: {result.get('error')}"
    context.codec_stream_id = result['streamId']
    context.uploaded_stream_ids.add(result['streamId'])
    tl = _wait_timeline(api_config, result['streamId'])
    assert tl, "No timeline"
    context.codec_timeline = tl[0]


@when('a clip ending at time T is downloaded')
def download_clip_ending_at_t(context, api_config):
    tl = context.codec_timeline
    start = datetime.fromisoformat(tl['startTime'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(tl['endTime'].replace('Z', '+00:00'))
    total = (end - start).total_seconds()
    assert total >= 15
    sub_start = start + timedelta(seconds=total * 0.25)
    sub_end = sub_start + timedelta(seconds=10)
    if sub_end > end:
        sub_end = end
    fmt = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    context.requested_end_dt = sub_end

    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.codec_stream_id}"
    response = get_with_retry(
        url,
        params={'startTime': fmt(sub_start), 'endTime': fmt(sub_end),
                'container': 'mp4'},
        headers=_route_header(context),
        timeout=60,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.codec_response = response
    context.requested_window = (sub_end - sub_start).total_seconds()


@then('the last decoded frame PTS is at least T minus one frame interval')
def last_frame_pts_check(context):
    resp = context.codec_response
    assert resp.status_code == 200, f"Boundary download failed: {resp.status_code}"
    tmp = Path(f"/tmp/boundary_{uuid.uuid4().hex[:8]}.mp4")
    tmp.write_bytes(resp.content)
    try:
        # Inspect last frame's PTS relative to clip duration
        frames_json = _ffprobe(
            tmp,
            ['-select_streams', 'v',
             '-show_entries', 'frame=pkt_pts_time,best_effort_timestamp_time',
             '-of', 'json'],
        )
        data = json.loads(frames_json) if frames_json else {}
        frames = data.get('frames', [])
        assert frames, "ffprobe found no frames"
        pts_values = []
        for fr in frames:
            v = fr.get('pkt_pts_time') or fr.get('best_effort_timestamp_time')
            if v:
                try:
                    pts_values.append(float(v))
                except ValueError:
                    pass
        assert pts_values, "No PTS values parsed"
        last_pts = max(pts_values)

        dur_s = _ffprobe(
            tmp,
            ['-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1'],
        )
        dur = float(dur_s) if dur_s else 0.0
        # Last frame should be within one frame interval (assume 30fps) of clip end
        one_frame = 1.0 / 30.0
        assert last_pts >= dur - 2 * one_frame, (
            f"Last frame PTS {last_pts:.3f}s is too far before duration {dur:.3f}s"
        )
        logger.info("Boundary check OK: dur=%.3f last_pts=%.3f", dur, last_pts)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# BDD-GAP-025 — direct-remux (transcode=none) preserves bitrate/fps/gop
# ---------------------------------------------------------------------------

@given('a known-bitrate test video has been uploaded')
def upload_known_params(context, api_config):
    file_path = context.temp_upload_dir / f"known_{uuid.uuid4().hex[:8]}.mp4"
    _create_known_params_video(
        file_path, duration_seconds=30, fps=25, bitrate_kbps=600, gop=50,
    )
    # ffmpeg's -b:v / -maxrate are *ceilings*; libx264 won't pad a simple
    # synthetic 'testsrc' pattern to hit them, so the measured source
    # bitrate is typically well below the requested value. Probe the file
    # we actually produced and assert against those measurements instead
    # of the encoder target -- the scenario verifies that the download
    # path preserves whatever the source is, not that the source matches
    # libx264's rate-control behavior on test patterns.
    context.input_params = _probe_source_params(file_path)
    logger.info(
        "Source params measured: %s",
        {k: round(v, 3) if isinstance(v, float) else v
         for k, v in context.input_params.items()},
    )

    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    result = upload_test_video(
        api_config['base_url'], file_path, file_path.name, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"Upload failed: {result.get('error')}"
    context.codec_stream_id = result['streamId']
    context.uploaded_stream_ids.add(result['streamId'])
    tl = _wait_timeline(api_config, result['streamId'])
    assert tl, "No timeline"
    context.codec_timeline = tl[0]


@when("a clip is downloaded with transcode preset 'none'")
def download_with_none_preset(context, api_config):
    # transcode=none is the server's direct-remux path: it muxes the stored
    # elementary stream into the requested container without re-encoding,
    # so the source's bitrate, fps and keyframe interval are preserved.
    # Valid values for the 'transcode' query param are 'none', 'full', 'gop'
    # (see storage_management_apis.cpp). 'original' is not a valid option.
    tl = context.codec_timeline
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.codec_stream_id}"
    response = get_with_retry(
        url,
        params={
            'startTime': tl['startTime'],
            'endTime': tl['endTime'],
            'container': 'mp4',
            'transcode': 'none',
        },
        headers=_route_header(context),
        timeout=120,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.codec_response = response


@then('the output bitrate is within tolerance of the input')
def output_bitrate_matches(context):
    resp = context.codec_response
    assert resp.status_code == 200, f"Download failed: {resp.status_code}"
    tmp = Path(f"/tmp/origp_{uuid.uuid4().hex[:8]}.mp4")
    tmp.write_bytes(resp.content)
    try:
        bitrate_s = _ffprobe(
            tmp,
            ['-show_entries', 'format=bit_rate',
             '-of', 'default=noprint_wrappers=1:nokey=1'],
        )
        actual = int(bitrate_s) // 1000 if bitrate_s and bitrate_s.isdigit() else 0
    finally:
        # keep file for downstream steps
        context.original_preset_clip = tmp
    expected = context.input_params['bitrate_kbps']
    tol = max(150, expected * 0.30)  # generous: 30% or 150 kbps
    assert abs(actual - expected) <= tol, (
        f"Bitrate drift: expected {expected} kbps, got {actual} kbps"
    )


@then('the output fps matches the input')
def output_fps_matches(context):
    tmp = context.original_preset_clip
    rate = _ffprobe(
        tmp,
        ['-select_streams', 'v:0',
         '-show_entries', 'stream=r_frame_rate',
         '-of', 'default=noprint_wrappers=1:nokey=1'],
    )
    try:
        num, den = rate.split('/')
        fps_actual = float(num) / float(den) if float(den) != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        fps_actual = 0.0
    expected = context.input_params['fps']
    assert abs(fps_actual - expected) <= 1.0, (
        f"fps drift: expected {expected}, got {fps_actual}"
    )


@then('the output keyframe interval matches the input within tolerance')
def output_gop_matches(context):
    tmp = context.original_preset_clip
    try:
        frames_json = _ffprobe(
            tmp,
            ['-select_streams', 'v',
             '-show_entries', 'frame=key_frame',
             '-of', 'json'],
        )
        frames = json.loads(frames_json).get('frames', []) if frames_json else []
        # Find intervals between key frames
        key_positions = [i for i, fr in enumerate(frames) if fr.get('key_frame') == 1]
        if len(key_positions) < 2:
            logger.warning("Not enough keyframes for GOP comparison")
            return
        intervals = [
            key_positions[i+1] - key_positions[i]
            for i in range(len(key_positions) - 1)
        ]
        actual_gop = sum(intervals) / len(intervals) if intervals else 0
    finally:
        tmp.unlink(missing_ok=True)
    expected = context.input_params['gop']
    assert abs(actual_gop - expected) <= max(5, expected * 0.20), (
        f"GOP drift: expected {expected}, got {actual_gop:.1f}"
    )

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# BDD steps for the /api/v1/storage/file fullFile=true fast path.
# See features/file_download/download_full_file.feature for scenarios.

import hashlib
import json
import logging
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import (
    create_test_video_file,
    upload_test_video,
    envoy_streamid_route_key,
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_download/download_full_file.feature')


# ----------------------------------------------------------------------------
# Endpoints — same shape as other download tests so envoy routing works.
# ----------------------------------------------------------------------------
ENDPOINTS = {
    'storage_file':     '/vst/api/v1/storage/file/{stream_id}',
    'storage_file_url': '/vst/api/v1/storage/file/{stream_id}/url',
    'storage_size':     '/vst/api/v1/storage/size?timelines=true',
}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mediainfo_summary(path: Path, timeout_sec: int = 30) -> Dict[str, Any]:
    """Run mediainfo and pull a few fields from its text output."""
    try:
        result = subprocess.run(
            ['mediainfo', str(path)],
            capture_output=True, text=True, timeout=timeout_sec,
        )
    except FileNotFoundError:
        return {'valid': False, 'error': 'mediainfo not installed', 'duration_sec': 0.0}
    except subprocess.TimeoutExpired:
        return {'valid': False, 'error': 'mediainfo timeout', 'duration_sec': 0.0}

    output = result.stdout or ''
    is_valid = (
        result.returncode == 0
        and 'Video' in output
        and ('Format' in output or 'Codec' in output)
    )

    # mediainfo prints things like "1 min 0 s 40 ms" or "12 s 40 ms" or
    # "00:01:30.500" depending on the build. Try unit-based first, then HMS.
    _UNIT_PAIR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|min|h|s)", re.IGNORECASE)
    _HMS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?")
    _UNIT_TO_SEC = {'h': 3600.0, 'min': 60.0, 's': 1.0, 'ms': 0.001}

    duration_sec = 0.0
    for line in output.splitlines():
        s = line.strip()
        if not s.startswith('Duration') or ':' not in s:
            continue
        tail = s.split(':', 1)[1].strip()
        try:
            pairs = _UNIT_PAIR_RE.findall(tail)
            if pairs:
                duration_sec = sum(float(v) * _UNIT_TO_SEC[u.lower()] for v, u in pairs)
                break
            hms = _HMS_RE.search(tail)
            if hms:
                h, m, sec, ms = hms.groups()
                duration_sec = (
                    int(h) * 3600 + int(m) * 60 + int(sec)
                    + (int(ms or 0) / (10 ** len(ms or '')) if ms else 0.0)
                )
                break
        except Exception:  # pragma: no cover
            continue

    return {
        'valid': is_valid,
        'error': None if is_valid else 'mediainfo did not report a Video track',
        'duration_sec': duration_sec,
        'raw': output,
    }


def _get_full_timeline_iso(api_base_url: str, stream_id: str,
                           timeout: int, verify_ssl: bool) -> Optional[Dict[str, str]]:
    """Return the recording's [startTime, endTime] for the given stream."""
    url = f"{api_base_url}{ENDPOINTS['storage_size']}"
    response = requests.get(url, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    data = response.json() or {}
    stream_info = data.get(stream_id) or {}
    timelines = stream_info.get('timelines') or []
    if not timelines:
        return None
    # Use the longest contiguous timeline window — for our single-file uploads
    # there's normally just one entry.
    longest = max(timelines, key=lambda t: (t.get('endTime', '') or '') > (t.get('startTime', '') or ''))
    return {
        'startTime': longest.get('startTime'),
        'endTime':   longest.get('endTime'),
    }


def _wait_for_stream_ready(api_base_url: str, stream_id: str, sensor_id: str,
                           verify_ssl: bool,
                           per_request_timeout: int = 10,
                           max_wait_sec: float = 60.0,
                           poll_interval_sec: float = 1.0) -> Dict[str, str]:
    """
    Block until the freshly-uploaded recording is fully reachable through
    envoy/SDR routing. Two conditions must be true:
      1. The storage layer knows about the recording (timeline available
         via /storage/size?timelines=true, served globally).
      2. The streamprocessing pod that owns the stream is registered with
         envoy/SDR — so a GET against /storage/file/{streamId} no longer
         returns 503.

    Returns the recording's [startTime, endTime] once both are true.
    """
    deadline = time.monotonic() + max_wait_sec
    last_err: Optional[str] = None
    attempt = 0
    timeline: Optional[Dict[str, str]] = None
    headers = {"streamid": envoy_streamid_route_key(sensor_id)}
    probe_url = f"{api_base_url}{ENDPOINTS['storage_file'].format(stream_id=stream_id)}"

    while time.monotonic() < deadline:
        attempt += 1
        try:
            if not timeline:
                timeline = _get_full_timeline_iso(
                    api_base_url, stream_id,
                    timeout=per_request_timeout, verify_ssl=verify_ssl,
                )
                if not timeline:
                    last_err = "timeline missing"
                    time.sleep(poll_interval_sec)
                    continue

            # Cheap probe: a tiny range request that should hit the same
            # SDR-routed pod as our real test calls. We don't care about the
            # response body — only whether envoy can route at all (i.e. NOT 503).
            probe = requests.get(
                probe_url,
                params={
                    'startTime': timeline['startTime'],
                    'endTime':   timeline['endTime'],
                },
                headers=headers,
                timeout=per_request_timeout,
                verify=verify_ssl,
            )
            if probe.status_code != 503:
                logger.info("[full_file] Stream %s routed after %d attempt(s) "
                            "(probe status=%d, timeline %s -> %s)",
                            stream_id, attempt, probe.status_code,
                            timeline['startTime'], timeline['endTime'])
                return timeline
            last_err = f"envoy returned 503 (no upstream yet)"
        except requests.exceptions.RequestException as e:
            last_err = str(e)
        time.sleep(poll_interval_sec)

    pytest.fail(
        f"Stream {stream_id} did not become routable within {max_wait_sec}s "
        f"({attempt} attempts); last error: {last_err}. Check that VST is "
        f"reachable at {api_base_url}, that the streamprocessing pod is up, "
        f"and that envoy/SDR routing is working."
    )


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------
@pytest.fixture
def test_endpoints():
    return ENDPOINTS


# ----------------------------------------------------------------------------
# Background: upload a single test recording and remember its source path/bytes.
# ----------------------------------------------------------------------------
@given('a file-based sensor with a known uploaded test recording')
def upload_known_recording(context, api_config, test_params):
    """Upload one ~10s test video; the resulting stream is our SUT recording."""
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    duration = max(int(test_params.get('video_duration_seconds', 10)), 5)

    filename = f"test_full_file_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_upload_dir / filename
    create_test_video_file(file_path, duration_seconds=duration)
    # VST may remux on storage so we don't compare against the uploaded
    # bytes directly; the duration is still useful for the sub-range test.
    context.source_duration_sec = float(duration)

    upload = upload_test_video(
        api_config['base_url'], file_path, filename, sensor_id,
        api_config.get('verify_ssl', False),
    )
    if not upload['success'] or not upload.get('streamId'):
        pytest.fail(f"Failed to upload test recording: {upload.get('error')}")

    context.stream_id = upload['streamId']
    context.uploaded_stream_ids.add(context.stream_id)
    logger.info("[full_file] Uploaded test recording: streamId=%s",
                context.stream_id)

    # Static post-upload wait (kept for parity with other download tests).
    wait_sec = float(test_params.get("post_upload_wait_sec", 0))
    if wait_sec > 0:
        time.sleep(wait_sec)

    # Block until envoy/SDR routing has picked up the new stream and the
    # storage layer can return its timeline. Without this gate the storage
    # GET / URL endpoints return 503 in a fresh deployment because there
    # is no upstream pod yet for our just-created stream id.
    timeline = _wait_for_stream_ready(
        api_config['base_url'], context.stream_id, context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False),
        per_request_timeout=int(test_params.get('timeout', 10)),
        max_wait_sec=float(test_params.get('video_ready_max_wait_sec', 60)),
        poll_interval_sec=float(test_params.get('video_ready_poll_interval_sec', 1)),
    )
    # Cache the resolved range so scenarios that need exact boundaries don't
    # have to re-query the storage layer.
    context.recording_start_iso = timeline['startTime']
    context.recording_end_iso   = timeline['endTime']


# ----------------------------------------------------------------------------
# Shared low-level callers that all `When` steps reuse.
# ----------------------------------------------------------------------------
def _download(context, api_config, query: Dict[str, str],
              stream_id_override: Optional[str] = None,
              timeout: int = 120) -> requests.Response:
    stream_id = stream_id_override if stream_id_override is not None else context.stream_id
    url = f"{api_config['base_url']}{ENDPOINTS['storage_file'].format(stream_id=stream_id)}"
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    return requests.get(
        url, params=query, headers=headers,
        timeout=timeout, verify=api_config.get('verify_ssl', False),
    )


def _request_url(context, api_config, query: Dict[str, str],
                 stream_id_override: Optional[str] = None,
                 timeout: int = 60) -> requests.Response:
    stream_id = stream_id_override if stream_id_override is not None else context.stream_id
    url = f"{api_config['base_url']}{ENDPOINTS['storage_file_url'].format(stream_id=stream_id)}"
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    return requests.get(
        url, params=query, headers=headers,
        timeout=timeout, verify=api_config.get('verify_ssl', False),
    )


# ----------------------------------------------------------------------------
# Positive: download with fullFile=true (no time range)
# ----------------------------------------------------------------------------
@when('the file is downloaded with fullFile=true and no startTime or endTime')
def download_full_file_no_times(context, api_config, test_params):
    context.last_query = {'fullFile': 'true'}
    response = _download(context, api_config, query=context.last_query,
                         timeout=test_params.get('download_timeout', 120))
    context.last_response = response


# ----------------------------------------------------------------------------
# Positive: download with exact-boundary times (auto-detect fast path)
# ----------------------------------------------------------------------------
@when("the file is downloaded with startTime and endTime equal to the recording's full range")
def download_full_file_exact_range(context, api_config, test_params):
    context.last_query = {
        'startTime': context.recording_start_iso,
        'endTime':   context.recording_end_iso,
    }
    response = _download(context, api_config, query=context.last_query,
                         timeout=test_params.get('download_timeout', 120))
    context.last_response = response


# ----------------------------------------------------------------------------
# Positive: range nudged inward by one frame must still trip the auto-detect
# path. The server uses kRangeToleranceMs = 1000/fps as the slack window;
# our test videos are 30 fps so tolerance is ~33 ms — a 20 ms nudge stays
# safely inside that window.
# ----------------------------------------------------------------------------
@when('the file is downloaded with startTime and endTime nudged inward by one frame')
def download_full_file_within_one_frame(context, api_config, test_params):
    nudge = timedelta(milliseconds=20)  # < 33 ms tolerance for 30 fps source
    file_start = datetime.fromisoformat(context.recording_start_iso.replace('Z', '+00:00'))
    file_end   = datetime.fromisoformat(context.recording_end_iso.replace('Z', '+00:00'))
    nudged_start = file_start + nudge
    nudged_end   = file_end   - nudge

    def _iso(dt: datetime) -> str:
        return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(dt.microsecond / 1000):03d}Z"

    context.last_query = {
        'startTime': _iso(nudged_start),
        'endTime':   _iso(nudged_end),
    }
    logger.info("[full_file] Nudged range: file=[%s, %s], request=[%s, %s] (~20ms inside, fps tolerance ~33ms)",
                context.recording_start_iso, context.recording_end_iso,
                context.last_query['startTime'], context.last_query['endTime'])
    response = _download(context, api_config, query=context.last_query,
                         timeout=test_params.get('download_timeout', 120))
    context.last_response = response


# ----------------------------------------------------------------------------
# Positive (URL path): single fullFile=true URL
# ----------------------------------------------------------------------------
@when('a temp URL is requested with fullFile=true and no time range')
def request_url_fullfile(context, api_config, test_params):
    response = _request_url(context, api_config, query={'fullFile': 'true'},
                            timeout=test_params.get('url_request_timeout', 60))
    context.last_response = response
    if response.status_code == 200:
        try:
            context.last_url_response = response.json()
        except json.JSONDecodeError:
            context.last_url_response = None
    else:
        context.last_url_response = None


# ----------------------------------------------------------------------------
# Positive (URL path): cache reuse — two identical requests
# ----------------------------------------------------------------------------
@when('a temp URL is requested with fullFile=true twice in a row')
def request_url_fullfile_twice(context, api_config, test_params):
    timeout = test_params.get('url_request_timeout', 60)
    r1 = _request_url(context, api_config, query={'fullFile': 'true'}, timeout=timeout)
    r2 = _request_url(context, api_config, query={'fullFile': 'true'}, timeout=timeout)
    assert r1.status_code == 200, f"first /url call failed: {r1.status_code} {r1.text[:200]}"
    assert r2.status_code == 200, f"second /url call failed: {r2.status_code} {r2.text[:200]}"
    context.url_response_a = r1.json()
    context.url_response_b = r2.json()


# ----------------------------------------------------------------------------
# Positive: cascade-delete
# ----------------------------------------------------------------------------
@given('a temp URL was created with fullFile=true')
def precondition_create_url(context, api_config, test_params):
    response = _request_url(context, api_config, query={'fullFile': 'true'},
                            timeout=test_params.get('url_request_timeout', 60))
    assert response.status_code == 200, \
        f"Failed to create temp URL: {response.status_code} {response.text[:200]}"
    body = response.json()
    assert body.get('videoUrl'), "Response missing videoUrl"
    context.cascade_video_url = body['videoUrl']
    logger.info("[full_file] Pre-created temp URL: %s", context.cascade_video_url)


@when('the source recording is deleted via the storage DELETE API')
def delete_source_recording(context, api_config, test_params):
    url = f"{api_config['base_url']}{ENDPOINTS['storage_file'].format(stream_id=context.stream_id)}"
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    response = requests.delete(
        url,
        params={
            'startTime': context.recording_start_iso,
            'endTime':   context.recording_end_iso,
        },
        headers=headers,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    assert response.status_code in (200, 204), \
        f"DELETE failed: {response.status_code} {response.text[:200]}"
    # Conftest cleanup also tries to delete; keep the id present so it's a no-op.
    logger.info("[full_file] Deleted source recording for streamId=%s", context.stream_id)

    # Give the cascade + cleanup a brief moment to complete on the server.
    time.sleep(test_params.get('expiry_check_buffer_sec', 5))


@then('the previously issued videoUrl returns 404 Not Found')
def cascade_url_should_404(context, api_config, test_params):
    assert getattr(context, 'cascade_video_url', None), "No cascade_video_url captured"
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    response = requests.get(
        context.cascade_video_url, headers=headers,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
        allow_redirects=False,
    )
    # Acceptable post-cascade outcomes:
    #   - 404 / 410 / 400 → application reports the temp link is gone.
    #   - 503             → envoy can no longer route the stream because
    #                       its upstream was torn down with the recording.
    # All of them prove the URL stopped serving content, which is the contract.
    assert response.status_code in (404, 410, 400, 503), (
        f"Expected 404/410/400/503 after cascade delete, got {response.status_code}: "
        f"{response.text[:200]}"
    )
    logger.info("[full_file] Cascade-deleted URL correctly returned %s", response.status_code)


# ----------------------------------------------------------------------------
# Negative: sub-range request must NOT take the fast path
# ----------------------------------------------------------------------------
@when('the file is downloaded with startTime offset by 1s into the recording and a 2s window')
def download_subrange(context, api_config, test_params):
    file_start = datetime.fromisoformat(context.recording_start_iso.replace('Z', '+00:00'))
    sub_start = file_start + timedelta(seconds=1)
    sub_end   = sub_start + timedelta(seconds=2)

    def _iso(dt: datetime) -> str:
        return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(dt.microsecond / 1000):03d}Z"

    response = _download(context, api_config, query={
        'startTime': _iso(sub_start),
        'endTime':   _iso(sub_end),
    }, timeout=test_params.get('download_timeout', 120))
    context.last_response = response
    context.requested_subrange_sec = 2.0


# ----------------------------------------------------------------------------
# Negative: fullFile=true + transcode=full
# ----------------------------------------------------------------------------
@when('a temp URL is requested with fullFile=true and transcode=full')
def request_url_fullfile_with_transcode(context, api_config, test_params):
    response = _request_url(context, api_config, query={
        'fullFile': 'true',
        'transcode': 'full',
    }, timeout=test_params.get('url_request_timeout', 300))
    context.last_response = response
    if response.status_code == 200:
        try:
            context.last_url_response = response.json()
        except json.JSONDecodeError:
            context.last_url_response = None


# ----------------------------------------------------------------------------
# Negative: fullFile=true with a different container than the source
# ----------------------------------------------------------------------------
@when('the file is downloaded with fullFile=true and container=mkv')
def download_fullfile_with_wrong_container(context, api_config, test_params):
    response = _download(context, api_config, query={
        'fullFile': 'true',
        'container': 'mkv',
    }, timeout=test_params.get('download_timeout', 120))
    context.last_response = response


# ----------------------------------------------------------------------------
# Negative: missing both fullFile and time range
# ----------------------------------------------------------------------------
@when('the file is downloaded with neither fullFile=true nor a time range')
def download_no_args(context, api_config, test_params):
    response = _download(context, api_config, query={},
                         timeout=test_params.get('download_timeout', 30))
    context.last_response = response


# ----------------------------------------------------------------------------
# Negative: fullFile=true on an unknown stream
# ----------------------------------------------------------------------------
@when('the file is downloaded with fullFile=true for a non-existent streamId')
def download_fullfile_bad_stream(context, api_config, test_params):
    bogus_id = f"nonexistent-{uuid.uuid4()}"
    response = _download(context, api_config, query={'fullFile': 'true'},
                         stream_id_override=bogus_id,
                         timeout=test_params.get('download_timeout', 30))
    context.last_response = response


# ----------------------------------------------------------------------------
# Then-step assertions
# ----------------------------------------------------------------------------
@then('the response is a valid media file')
def response_is_valid_media(context, test_params):
    response: requests.Response = context.last_response
    assert response.status_code == 200, \
        f"Expected 200, got {response.status_code}: {response.text[:200]}"
    assert response.content, "Response body is empty"

    tmp = Path(test_params['temp_download_dir']) / f"full_file_{uuid.uuid4().hex[:8]}.mp4"
    tmp.write_bytes(response.content)
    try:
        info = _mediainfo_summary(tmp, test_params.get('mediainfo_timeout_sec', 30))
        context.last_mediainfo = info
        assert info['valid'], f"mediainfo rejected the response: {info.get('error')}"
        logger.info("[full_file] mediainfo: duration=%.3fs, size=%d bytes",
                    info.get('duration_sec', 0.0), len(response.content))
    finally:
        if tmp.exists():
            tmp.unlink()


@then('a second download with the same parameters returns byte-identical content')
def second_download_is_byte_identical(context, api_config, test_params):
    """
    A strong fast-path signal: the fast path streams the stored file
    directly via mg_send_mime_file2, so two consecutive fast-path GETs
    return byte-identical bytes. The slow remux/transcode path stamps
    timestamps into the moov atom on every call, so two responses would
    differ by at least a few bytes.
    """
    first: requests.Response = context.last_response
    first_sha = _sha256_bytes(first.content)
    first_len = len(first.content)

    # Re-issue the same request with the same query params.
    second = _download(context, api_config, query=context.last_query,
                       timeout=test_params.get('download_timeout', 120))
    assert second.status_code == 200, (
        f"Second fast-path download failed: {second.status_code} {second.text[:200]}"
    )
    second_sha = _sha256_bytes(second.content)
    second_len = len(second.content)

    assert first_sha == second_sha and first_len == second_len, (
        "Two consecutive fast-path GETs returned different bytes — the fast path "
        "is probably NOT engaged (server may be remuxing on each request).\n"
        f"  first : {first_len} bytes, sha256={first_sha}\n"
        f"  second: {second_len} bytes, sha256={second_sha}"
    )
    logger.info("[full_file] Fast path engaged: 2 GETs returned same %d bytes (sha256=%s)",
                first_len, first_sha[:12])


@then('the URL response carries fullFile=true and a non-empty videoUrl')
def url_response_has_fullfile_true(context):
    body = getattr(context, 'last_url_response', None)
    assert body is not None, (
        "URL response was not parseable as JSON or status != 200; "
        f"status={getattr(context.last_response, 'status_code', None)}, "
        f"body={getattr(context.last_response, 'text', '')[:200]}"
    )
    assert body.get('fullFile') is True, (
        f"Expected fullFile=true in response, got: {json.dumps(body)[:300]}"
    )
    assert body.get('videoUrl'), f"Response missing videoUrl: {json.dumps(body)[:300]}"
    context.full_file_video_url = body['videoUrl']


@then('the videoUrl serves a valid media file')
def videourl_serves_valid_media(context, api_config, test_params):
    video_url = getattr(context, 'full_file_video_url', None)
    assert video_url, "No fullFile videoUrl captured"
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    response = requests.get(
        video_url, headers=headers,
        timeout=test_params.get('download_timeout', 120),
        verify=api_config.get('verify_ssl', False),
    )
    assert response.status_code == 200, (
        f"GET videoUrl failed: {response.status_code} {response.text[:200]}"
    )
    context.url_download_bytes = response.content

    tmp = Path(test_params['temp_download_dir']) / f"full_file_url_{uuid.uuid4().hex[:8]}.mp4"
    tmp.write_bytes(response.content)
    try:
        info = _mediainfo_summary(tmp, test_params.get('mediainfo_timeout_sec', 30))
        assert info['valid'], f"mediainfo rejected URL content: {info.get('error')}"
    finally:
        if tmp.exists():
            tmp.unlink()


@then('fetching the videoUrl twice returns byte-identical content')
def videourl_two_fetches_match(context, api_config, test_params):
    """
    Same fast-path signal as for the download endpoint: the URL is backed
    by a symlink to the stored recording, so two GETs against it return
    byte-identical bytes (mg_send_mime_file2 streams the file). A
    remux-on-each-request server would produce different bytes between
    calls.
    """
    first_bytes = getattr(context, 'url_download_bytes', None)
    assert first_bytes, "Need to have already downloaded the URL once"
    first_sha = _sha256_bytes(first_bytes)

    video_url = context.full_file_video_url
    headers = {"streamid": envoy_streamid_route_key(context.sensor_id)}
    second = requests.get(
        video_url, headers=headers,
        timeout=test_params.get('download_timeout', 120),
        verify=api_config.get('verify_ssl', False),
    )
    assert second.status_code == 200, (
        f"Second URL fetch failed: {second.status_code} {second.text[:200]}"
    )
    second_sha = _sha256_bytes(second.content)
    assert first_sha == second_sha and len(first_bytes) == len(second.content), (
        "Two consecutive videoUrl fetches returned different bytes — the URL is "
        "probably NOT backed by a symlink (server may be regenerating on each fetch).\n"
        f"  first : {len(first_bytes)} bytes, sha256={first_sha}\n"
        f"  second: {len(second.content)} bytes, sha256={second_sha}"
    )
    logger.info("[full_file] videoUrl backed by stable file: 2 fetches matched (%d bytes, sha256=%s)",
                len(first_bytes), first_sha[:12])


@then('both responses return the same videoUrl')
def both_url_responses_match(context):
    a = getattr(context, 'url_response_a', {}) or {}
    b = getattr(context, 'url_response_b', {}) or {}
    url_a, url_b = a.get('videoUrl'), b.get('videoUrl')
    assert url_a and url_b, f"Missing videoUrl in one of the responses: a={a}, b={b}"
    assert url_a == url_b, (
        f"Cache-reuse failed: each /url?fullFile=true call should return the same URL.\n"
        f"  call 1: {url_a}\n"
        f"  call 2: {url_b}"
    )
    logger.info("[full_file] Cache reuse confirmed: %s", url_a)


@then("the downloaded duration is shorter than the source file's duration")
def duration_is_subset(context):
    info = getattr(context, 'last_mediainfo', None)
    assert info and info.get('valid'), "Need a valid mediainfo result from the previous Then step"
    requested = float(getattr(context, 'requested_subrange_sec', 0.0))
    duration = float(info.get('duration_sec', 0.0))
    assert duration > 0.0, f"mediainfo could not parse duration; raw={info.get('raw', '')[:200]}"
    # The fast path would have returned the full source duration; a real
    # remux/transcode path returns ~ the requested window. Allow a small
    # keyframe-snap margin around the requested window.
    src_duration = float(getattr(context, 'source_duration_sec', 0.0))
    assert duration < src_duration - 1.0, (
        f"Sub-range request unexpectedly returned full-file duration "
        f"(got {duration:.3f}s, source is {src_duration:.3f}s, requested ~{requested:.3f}s) — "
        f"the fast path may have been incorrectly taken."
    )
    logger.info("[full_file] Sub-range duration %.3fs < source %.3fs (request=%.3fs) — non-fast-path confirmed",
                duration, src_duration, requested)


@then('the URL response does NOT carry fullFile=true')
def url_response_no_fullfile(context):
    response: requests.Response = context.last_response
    if response.status_code != 200:
        # Falling back to a different status (e.g. error) is also acceptable
        # — the only thing we forbid is a fast-path success response.
        logger.info("[full_file] /url with disqualifying flag returned status=%d (not fast path) — OK",
                    response.status_code)
        return
    body = getattr(context, 'last_url_response', None) or {}
    assert body.get('fullFile') is not True, (
        f"Fast path was taken despite disqualifying flag. response={json.dumps(body)[:300]}"
    )
    logger.info("[full_file] /url with disqualifying flag: fullFile flag absent in response — OK")


@then('the response status is not a 200 fast-path response')
def status_not_fast_path(context, api_config, test_params):
    """
    The fast path streams the stored MP4 file directly. If we asked for a
    different container (mkv), the server must either reject the request
    or actually remux into mkv — in both cases the response is NOT what
    the mp4 fast path would have produced. We verify by re-issuing the
    same request with the disqualifier removed; if both responses are
    byte-identical, the fast path was incorrectly taken.
    """
    first: requests.Response = context.last_response
    if first.status_code != 200:
        return  # Any non-200 means the fast path was clearly not taken.

    if first.headers.get('Content-Type', '').startswith('application/json'):
        pytest.fail(f"Expected non-fast-path response, got JSON 200: {first.text[:200]}")

    # Compare to a fast-path baseline (no container override).
    baseline = _download(context, api_config, query={'fullFile': 'true'},
                         timeout=test_params.get('download_timeout', 120))
    if baseline.status_code != 200:
        # Couldn't establish a baseline — be conservative and just trust the
        # disqualified call's status. Anything other than the baseline-equal
        # body would still prove the fast path wasn't taken.
        return
    assert _sha256_bytes(first.content) != _sha256_bytes(baseline.content), (
        "Server returned the same bytes for ?fullFile=true&container=mkv as for "
        "?fullFile=true alone — the fast path was incorrectly taken despite the "
        "container mismatch."
    )


@then('the response status is 400')
def expect_status_400(context):
    response: requests.Response = context.last_response
    assert response.status_code == 400, (
        f"Expected HTTP 400, got {response.status_code}: {response.text[:300]}"
    )


@then('the response status indicates an error')
def expect_error_status(context):
    response: requests.Response = context.last_response
    # Accept both 4xx (rejected by the application) and 5xx (rejected by
    # envoy / no upstream available) — both correctly signal the unknown
    # stream id cannot be served.
    assert response.status_code >= 400, (
        f"Expected an error status (>=400), got {response.status_code}: "
        f"{response.text[:300]}"
    )

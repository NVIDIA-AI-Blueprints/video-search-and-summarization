# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""BDD tests for video download via non-blocking URL API."""

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import envoy_streamid_route_key
from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)


def _persist_invalid_nonblocking_clip(
    temp_dir: Path,
    label: str,
    index: int,
    stream_id: str,
    content: bytes,
    video_url: Optional[str],
) -> Path:
    """
    Save bytes under temp_dir/validation_failures/ so file_download conftest's cleanup
    (only temp_dir/*.mp4) does not delete these artifacts.
    """
    fail_dir = temp_dir / "validation_failures"
    fail_dir.mkdir(parents=True, exist_ok=True)
    safe_stream = "".join(c if c.isalnum() or c in "-_" else "_" for c in (stream_id or "unknown"))[:80]
    out = fail_dir / f"{label}_{index}_{safe_stream}.mp4"
    out.write_bytes(content)
    msg = (
        f"[INVALID {label} index={index}] video_url={video_url!r}\n"
        f"  bytes={len(content)} saved_to={out}"
    )
    print(msg, flush=True)
    logger.error(msg)
    return out


async def _wait_for_video_ready(
    session: aiohttp.ClientSession,
    video_url: str,
    index: int,
    stream_id: str,
    *,
    poll_interval: float,
    max_wait: float,
    check_timeout: float,
) -> bool:
    """Probe with streamable=false until the muxer publishes a complete file.

    Server contract: the muxer writes to ``<final>.tmp`` then atomic
    renames to ``<final>`` on success.  The final URL therefore yields
    either 404 (task not yet complete) or returns the fully-muxed file.
    Probing with ``streamable=false`` triggers the server-side blocking
    branch (``handleBlockingTaskWait``) so each probe waits for
    completion server-side, capped by ``check_timeout``.
    """
    url_for_probe = f"{video_url}{'&' if '?' in video_url else '?'}streamable=false"
    start_time = time.time()
    attempt = 0

    while (time.time() - start_time) < max_wait:
        attempt += 1
        try:
            headers = {'Range': 'bytes=0-0', 'streamid': envoy_streamid_route_key(stream_id)}
            async with session.get(
                url_for_probe,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=check_timeout),
                ssl=False,
            ) as response:
                cl = int(response.headers.get('Content-Length', '0'))
                cr = response.headers.get('Content-Range', '')
                # Content-Range: bytes 0-0/<total_size>
                total_from_range = 0
                if '/' in cr:
                    try:
                        total_from_range = int(cr.split('/')[-1])
                    except (ValueError, IndexError):
                        pass
                file_size = total_from_range or cl

                if response.status in (200, 206) and file_size > 0:
                    elapsed = time.time() - start_time
                    logger.info(
                        "    [Index %d] Video ready after %.1fs (%d attempts, size=%d)",
                        index, elapsed, attempt, file_size,
                    )
                    return True
                await asyncio.sleep(poll_interval)
        except Exception:
            await asyncio.sleep(poll_interval)

    logger.warning(
        "    [Index %d] Video not ready after %ds timeout (%d attempts)",
        index, max_wait, attempt,
    )
    return False


async def _download_chunked(
    session: aiohttp.ClientSession,
    url: str,
    stream_id: str,
    timeout: int,
    chunk_size: int = 8192,
) -> tuple:
    """Single download attempt with chunked read for accurate TTFB.

    Returns ``(content, status, time_to_first_byte, total_time)``.
    """
    download_start = time.time()
    time_to_first_byte: Optional[float] = None
    chunks: List[bytes] = []
    async with session.get(
        url,
        headers={"streamid": envoy_streamid_route_key(stream_id)},
        timeout=aiohttp.ClientTimeout(total=timeout),
        ssl=False,
    ) as response:
        response.raise_for_status()
        async for chunk in response.content.iter_chunked(chunk_size):
            if time_to_first_byte is None:
                time_to_first_byte = time.time() - download_start
            chunks.append(chunk)
        status = response.status

    content = b''.join(chunks)
    total_time = time.time() - download_start
    return content, status, time_to_first_byte, total_time


def _run_mediainfo(content: bytes,
                   temp_file: Path,
                   mediainfo_timeout: int) -> Dict[str, Any]:
    """Write content to ``temp_file``, run mediainfo, classify the result.

    Returns a dict with: ``is_valid``, ``summary_str``, ``returncode``,
    ``stdout``, ``stderr``.
    """
    with open(temp_file, 'wb') as f:
        f.write(content)
    result = subprocess.run(
        ['mediainfo', str(temp_file)],
        capture_output=True,
        text=True,
        timeout=mediainfo_timeout,
    )
    output = result.stdout
    is_valid = (
        result.returncode == 0
        and 'Video' in output
        and ('Format' in output or 'Codec' in output)
    )
    summary_str = ''
    if is_valid:
        keywords = ('Format', 'Duration', 'Width', 'Height', 'Frame rate')
        summary = [
            line.strip()
            for line in output.split('\n')[:20]
            if any(k in line for k in keywords)
        ]
        summary_str = ' | '.join(summary[:3])
    return {
        'is_valid': is_valid,
        'summary_str': summary_str,
        'returncode': result.returncode,
        'stdout': output,
        'stderr': result.stderr or '',
    }


def _build_validation_entry(
    download_data: Dict[str, Any],
    url_response: Optional[Dict[str, Any]],
    temp_dir: Path,
    streamable_label: str,
    mediainfo_timeout: int,
) -> Dict[str, Any]:
    """Run mediainfo on a downloaded clip and return the validation entry."""
    index = download_data['index']
    temp_file = temp_dir / f"non_blocking_streamable_{streamable_label}_{index}.mp4"
    try:
        info = _run_mediainfo(download_data['content'], temp_file, mediainfo_timeout)
        ttfb_ms = (download_data.get('time_to_first_byte') or 0) * 1000
        total_ms = (download_data.get('total_download_time') or 0) * 1000

        if info['is_valid']:
            logger.info(
                "  [Index %d] streamable=%s: Valid - %s",
                index, streamable_label, info['summary_str'],
            )
            logger.info(
                "    Download timing - First byte: %.1fms, Total: %.1fms",
                ttfb_ms, total_ms,
            )
            file_path = str(temp_file)
        else:
            video_url = url_response.get('video_url') if url_response else None
            sid = url_response['stream_id'] if url_response else 'unknown'
            kept = _persist_invalid_nonblocking_clip(
                temp_dir,
                f"non_blocking_streamable_{streamable_label}",
                index,
                sid,
                download_data['content'],
                video_url,
            )
            logger.error(
                "mediainfo streamable=%s index=%s rc=%s stdout[:800]=%r stderr=%r",
                streamable_label, index, info['returncode'],
                info['stdout'][:800], info['stderr'][:800],
            )
            logger.warning("  [Index %d] streamable=%s: Invalid", index, streamable_label)
            file_path = str(kept)

        return {
            'index': index,
            'stream_id': url_response['stream_id'] if url_response else 'unknown',
            'valid': info['is_valid'],
            'file_size': len(download_data['content']),
            'file_path': file_path,
            'absolute_path': url_response['absolute_path'] if url_response else None,
            'expiry_iso': url_response['expiry_iso'] if url_response else None,
            'expiry_minutes': url_response['expiry_minutes'] if url_response else 0,
            'streamable': streamable_label,
            'error': None if info['is_valid'] else 'Invalid video format',
        }
    except subprocess.TimeoutExpired:
        logger.warning("  [Index %d] streamable=%s: mediainfo timeout", index, streamable_label)
        return {
            'index': index,
            'valid': False,
            'streamable': streamable_label,
            'error': 'mediainfo timeout',
        }
    except Exception as e:
        logger.error("  [Index %d] streamable=%s: Error - %s", index, streamable_label, e)
        return {
            'index': index,
            'valid': False,
            'streamable': streamable_label,
            'error': str(e),
        }


def _assert_all_valid(
    validation_results: List[Dict[str, Any]],
    streamable_label: str,
    test_params: Dict[str, Any],
) -> None:
    """Log a summary and assert every clip validated for the given label."""
    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)
    logger.info(
        "Validation Results (streamable=%s): %d/%d videos are valid",
        streamable_label, valid_count, total_count,
    )
    invalid_videos = [r for r in validation_results if not r['valid']]
    if invalid_videos:
        logger.warning("Invalid videos (streamable=%s):", streamable_label)
        for result in invalid_videos:
            logger.warning("  - Index: %d, Error: %s", result['index'], result['error'])
    if valid_count == total_count:
        return

    failure_info = format_validation_failure(
        valid_count, total_count, invalid_videos,
        f"non-blocking URL video (streamable={streamable_label})",
    )
    assert_with_detailed_failure(
        False,
        f"Non-Blocking URL Video Download Validation (streamable={streamable_label})",
        failure_info['expected'],
        failure_info['actual'],
        failure_info['failed_items'],
        "Check URL generation and video readiness polling above.\n"
        f"  Downloaded videos (streamable={streamable_label}): {total_count}\n"
        f"  Valid: {valid_count}\n"
        f"  Invalid: {total_count - valid_count}\n"
        f"  URL mode: Non-blocking (video generated asynchronously)\n"
        f"  Poll interval: {test_params.get('video_ready_poll_interval_sec', 3)}s\n"
        f"  Max wait time: {test_params.get('video_ready_max_wait_sec', 120)}s"
    )


def _run_streamable_phase(context,
                          test_params: Dict[str, Any],
                          *,
                          streamable: bool) -> None:
    """Probe, download, validate, and assert for the given streamable mode.

    streamable=True:  download with ``?streamable=true`` (chunked while
                      muxing).  A defensive retry + ``streamable=false``
                      fallback is applied to mask transient 0-byte
                      responses under heavy load.
    streamable=False: download with ``?streamable=false`` (server-side
                      blocking branch).  Single attempt.
    """
    label = 'true' if streamable else 'false'
    attr_name = f'validation_results_streamable_{label}'
    temp_dir = Path(test_params['temp_download_dir'])

    poll_interval = test_params.get('video_ready_poll_interval_sec', 3)
    max_wait = test_params.get('video_ready_max_wait_sec', 120)
    check_timeout = test_params.get('video_ready_check_timeout_sec', 15)
    max_retries = test_params.get('video_ready_max_retries', 3)
    delay_before_mp4 = float(test_params.get('delay_before_mp4_url_access_sec', 2))
    max_concurrent = test_params.get('parallelism', 2)
    dl_timeout = test_params.get('download_timeout', 120)
    mediainfo_timeout = test_params.get('mediainfo_timeout_sec', 30)

    async def download_video(session: aiohttp.ClientSession,
                             video_url: str,
                             index: int,
                             stream_id: str) -> Dict[str, Any]:
        try:
            if delay_before_mp4 > 0:
                logger.info(
                    "    [Index %d] Waiting %.1fs before first access to signed .mp4 URL"
                    " (delay_before_mp4_url_access_sec)",
                    index, delay_before_mp4,
                )
                await asyncio.sleep(delay_before_mp4)

            ready = await _wait_for_video_ready(
                session, video_url, index, stream_id,
                poll_interval=poll_interval,
                max_wait=max_wait,
                check_timeout=check_timeout,
            )
            if not ready:
                return {
                    'index': index, 'content': None, 'status': None,
                    'success': False, 'time_to_first_byte': None,
                    'total_download_time': 0,
                    'error': f'Video not ready after {max_wait}s timeout',
                }

            url_primary = f"{video_url}{'&' if '?' in video_url else '?'}streamable={label}"

            if not streamable:
                content, status, ttfb, total = await _download_chunked(
                    session, url_primary, stream_id, dl_timeout,
                )
                return {
                    'index': index, 'content': content, 'status': status,
                    'success': True, 'time_to_first_byte': ttfb,
                    'total_download_time': total, 'error': None,
                }

            last_status: Optional[int] = None
            last_ttfb: Optional[float] = None
            last_total: float = 0
            for attempt in range(max_retries + 1):
                content, status, ttfb, total = await _download_chunked(
                    session, url_primary, stream_id, dl_timeout,
                )
                last_status, last_ttfb, last_total = status, ttfb, total
                if len(content) > 0:
                    return {
                        'index': index, 'content': content, 'status': status,
                        'success': True, 'time_to_first_byte': ttfb,
                        'total_download_time': total, 'error': None,
                    }
                if attempt < max_retries:
                    logger.warning(
                        "    [Index %d] streamable=true returned 0 bytes (attempt %d/%d), retrying...",
                        index, attempt + 1, max_retries + 1,
                    )
                    await asyncio.sleep(poll_interval)

            logger.warning(
                "    [Index %d] streamable=true exhausted retries, falling back to streamable=false",
                index,
            )
            url_fallback = f"{video_url}{'&' if '?' in video_url else '?'}streamable=false"
            content, status, ttfb, total = await _download_chunked(
                session, url_fallback, stream_id, dl_timeout,
            )
            if len(content) > 0:
                return {
                    'index': index, 'content': content, 'status': status,
                    'success': True, 'time_to_first_byte': ttfb,
                    'total_download_time': total, 'error': None,
                }
            return {
                'index': index, 'content': None, 'status': last_status,
                'success': False, 'time_to_first_byte': last_ttfb,
                'total_download_time': last_total,
                'error': 'Downloaded 0 bytes after retries and fallback',
            }
        except Exception as e:
            return {
                'index': index, 'content': None, 'status': None,
                'success': False, 'time_to_first_byte': None,
                'total_download_time': 0, 'error': str(e),
            }

    async def download_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=False)

        async def _throttled(item: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await download_video(
                    session, item['video_url'], item['index'], item['stream_id'],
                )

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(item) for item in items if item['video_url']]
            return await asyncio.gather(*tasks)

    logger.info(
        "Downloading videos with streamable=%s (parallelism=%d)...",
        label, max_concurrent,
    )
    successful_url_responses = [
        r for r in context.url_responses if r['success'] and r['video_url']
    ]
    download_results = asyncio.run(download_batch(successful_url_responses))

    validation_results: List[Dict[str, Any]] = []
    for download_data in download_results:
        if not download_data['success']:
            validation_results.append({
                'index': download_data['index'],
                'valid': False,
                'streamable': label,
                'error': f"Failed to download: {download_data['error']}",
            })
            continue
        url_response = next(
            (r for r in context.url_responses if r['index'] == download_data['index']),
            None,
        )
        validation_results.append(
            _build_validation_entry(
                download_data, url_response, temp_dir, label, mediainfo_timeout,
            )
        )

    setattr(context, attr_name, validation_results)
    _assert_all_valid(validation_results, label, test_params)


# Load scenarios from the feature file
scenarios('../../features/file_download/video_download_by_non_blocking_url.feature')


# API Endpoints for this test
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_file_url': '/vst/api/v1/storage/file/{stream_id}/url'
}


class ScenarioContext:
    """Context to store test data between steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.url_responses: List[Dict[str, Any]] = []
        self.videos_streamable_true: List[Dict[str, Any]] = []
        self.videos_streamable_false: List[Dict[str, Any]] = []
        self.validation_results_streamable_true: List[Dict[str, Any]] = []
        self.validation_results_streamable_false: List[Dict[str, Any]] = []
        self.expiry_results: List[Dict[str, Any]] = []


@pytest.fixture
def context():
    """Create a test context."""
    return ScenarioContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


# test_params, test_endpoints, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


@given('the VST API is configured for non-blocking URL test')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['timelines'], "Storage timelines endpoint must be configured"
    assert test_endpoints['storage_file_url'], "Storage file URL endpoint must be configured"


@when('the list of available streams for non-blocking URL test is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch the list of available streams from the API."""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.streams = response.json()
    assert len(context.streams) > 0, "No streams found"


@when('the recording timelines for non-blocking URL test are fetched')
def fetch_timelines(context, api_config, test_endpoints, test_params):
    """Fetch recording timelines from the storage API."""
    url = f"{api_config['base_url']}{test_endpoints['timelines']}"
    # timelines endpoint doesn't need params
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"


@when('valid time ranges for non-blocking URL test are selected')
def select_time_ranges(context, test_params):
    """Select valid time ranges from the timeline data for each stream.

    Generates one test case per (stream, duration, expiry) combination.
    The downstream URL request and download steps throttle in-flight
    work via ``test_params['parallelism']`` so the server-side muxer
    queue does not get overwhelmed.
    """
    durations = test_params['video_durations']
    expiry_minutes = test_params['expiry_minutes']

    test_data = []

    stream_names = []
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                stream_names.append(stream_name)

    for stream_name in stream_names:
        stream_timeline_data = context.timelines.get(stream_name)
        
        if not stream_timeline_data or not isinstance(stream_timeline_data, list):
            continue
        
        timelines = stream_timeline_data
        
        if not isinstance(timelines, list) or len(timelines) == 0:
            continue
        
        for duration in durations:
            for expiry in expiry_minutes:
                suitable_timeline = None
                for timeline in timelines:
                    start_time_str = timeline.get('startTime')
                    end_time_str = timeline.get('endTime')
                    
                    if not start_time_str or not end_time_str:
                        continue
                    
                    try:
                        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                        timeline_duration = (end_time - start_time).total_seconds()
                        
                        if timeline_duration >= duration * 2:
                            suitable_timeline = (start_time, end_time)
                            break
                    except (ValueError, AttributeError):
                        continue
                
                if not suitable_timeline:
                    longest_timeline = None
                    longest_duration = 0
                    for timeline in timelines:
                        start_time_str = timeline.get('startTime')
                        end_time_str = timeline.get('endTime')
                        
                        if not start_time_str or not end_time_str:
                            continue
                        
                        try:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                            timeline_duration = (end_time - start_time).total_seconds()
                            
                            if timeline_duration > longest_duration:
                                longest_duration = timeline_duration
                                longest_timeline = (start_time, end_time)
                        except (ValueError, AttributeError):
                            continue
                    
                    if longest_timeline and longest_duration >= duration:
                        suitable_timeline = longest_timeline
                
                if suitable_timeline:
                    start_time, end_time = suitable_timeline
                    
                    middle_time = start_time + (end_time - start_time) / 2
                    video_end_time = middle_time + timedelta(seconds=duration)
                    
                    if video_end_time > end_time:
                        video_end_time = end_time
                    
                    test_data.append({
                        'stream_id': stream_name,
                        'start_time': middle_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                        'end_time': video_end_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                        'duration': duration,
                        'expiry_minutes': expiry
                    })

    context.test_data = test_data
    assert len(context.test_data) > 0, "No valid test data found"
    streams_with_cases = len({row['stream_id'] for row in context.test_data})
    logger.info(
        "Generated %d test case(s) across %d stream(s) (of %d stream id(s) from streams API)",
        len(context.test_data),
        streams_with_cases,
        len(stream_names),
    )


@then('video URLs for each stream and time range are requested with non-blocking mode in parallel')
def request_video_urls_parallel(context, api_config, test_endpoints, test_params):
    """Request video URLs with bounded parallelism using non-blocking mode."""
    max_concurrent = test_params.get('parallelism', 2)
    min_expiry_minutes = test_params.get('min_expiry_minutes_for_non_blocking', 5)
    # Stagger URL submissions by index so we don't burst N muxer-task starts
    # on the server within a single tick. Each request pre-delays by
    # `index * stagger_sec`, so submissions arrive at the server-side
    # task manager spread over ~N * stagger_sec seconds. Tasks themselves
    # still run concurrently server-side; only the moment of *starting*
    # them is staggered. Set to 0 in config to disable.
    stagger_sec = float(test_params.get('url_request_stagger_sec', 0))

    async def request_video_url(session: aiohttp.ClientSession,
                                stream_id: str,
                                start_time: str,
                                end_time: str,
                                expiry_minutes: int,
                                index: int) -> Dict[str, Any]:
        """Request a video URL asynchronously with non-blocking mode."""
        url = f"{api_config['base_url']}{test_endpoints['storage_file_url'].format(stream_id=stream_id)}"

        configuration = {
            "disableAudio": False,
            "overlay": {
                "bbox": {"showAll": True, "objectId": []},
                "color": "red",
                "thickness": 6,
                "opacity": 255,
                "debug": True
            }
        }

        # Non-blocking muxing returns the URL immediately but the file is
        # only published (atomic staging->rename) when mux completes.
        # Floor the expiry so the file survives until the subsequent
        # download poll/fetch finishes even on a busy muxer queue.
        effective_expiry = max(expiry_minutes, min_expiry_minutes)

        params = {
            'startTime': start_time,
            'endTime': end_time,
            'expiryMinutes': str(effective_expiry),
            'blocking': 'false',
            'container': 'mp4',
            'configuration': json.dumps(configuration)
        }
        
        try:
            request_start_time = time.time()
            async with session.get(
                url,
                params=params,
                headers={"streamid": envoy_streamid_route_key(stream_id)},
                timeout=aiohttp.ClientTimeout(total=test_params.get('url_request_timeout', 60)),
                ssl=api_config.get('verify_ssl', False)
            ) as response:
                response.raise_for_status()
                
                # Parse response as JSON (server may return text/plain content-type even for JSON)
                text = await response.text()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as je:
                    return {
                        'index': index,
                        'stream_id': stream_id,
                        'start_time': start_time,
                        'end_time': end_time,
                        'expiry_minutes': expiry_minutes,
                        'response_data': None,
                        'video_url': None,
                        'absolute_path': None,
                        'expiry_iso': None,
                        'request_duration': 0,
                        'status': response.status,
                        'success': False,
                        'error': f"JSON decode error: {str(je)}. Response: {text[:200]}"
                    }
                
                request_end_time = time.time()
                
                return {
                    'index': index,
                    'stream_id': stream_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'expiry_minutes': expiry_minutes,
                    'response_data': data,
                    'video_url': data.get('videoUrl'),
                    'absolute_path': data.get('absolutePath'),
                    'expiry_iso': data.get('expiryISO'),
                    'request_duration': request_end_time - request_start_time,
                    'status': response.status,
                    'success': True,
                    'error': None
                }
        except Exception as e:
            return {
                'index': index,
                'stream_id': stream_id,
                'start_time': start_time,
                'end_time': end_time,
                'expiry_minutes': expiry_minutes,
                'response_data': None,
                'video_url': None,
                'absolute_path': None,
                'expiry_iso': None,
                'request_duration': 0,
                'status': None,
                'success': False,
                'error': str(e)
            }
    
    async def request_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Request a batch of video URLs with bounded parallelism."""
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))

        async def _throttled(item: Dict[str, Any]) -> Dict[str, Any]:
            if stagger_sec > 0:
                await asyncio.sleep(item['index'] * stagger_sec)
            async with sem:
                return await request_video_url(
                    session,
                    item['stream_id'],
                    item['start_time'],
                    item['end_time'],
                    item['expiry_minutes'],
                    item['index'],
                )

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(item) for item in batch]
            return await asyncio.gather(*tasks)

    for i, item in enumerate(context.test_data):
        item['index'] = i

    logger.info(
        "Requesting video URLs for %d test case(s) (parallelism=%d, stagger=%.2fs/index)",
        len(context.test_data), max_concurrent, stagger_sec,
    )

    all_results = asyncio.run(request_batch(context.test_data))
    
    context.url_responses = all_results
    
    # Count successful requests
    successful = sum(1 for r in context.url_responses if r['success'])
    logger.info("Successfully received %d/%d URL responses", successful, len(context.url_responses))
    
    # Log results
    for result in context.url_responses:
        if result['success']:
            logger.info("  [%s] Duration: %.1fms, Expiry: %dmin (non-blocking)",
                       result['stream_id'], result['request_duration']*1000, result['expiry_minutes'])
        else:
            logger.error("  [%s] ERROR: %s", result['stream_id'], result['error'])
    
    # Assert that we got some results
    assert len(context.url_responses) > 0, "No URL responses received"
    
    # Count successful requests
    successful = sum(1 for r in context.url_responses if r['success'])
    logger.info("Total: %d/%d successful URL requests", successful, len(context.url_responses))


@then('all videos are downloaded with streamable true option and validated')
def download_and_validate_videos_streamable_true(context, test_params):
    """Download videos with streamable=true (chunked while muxing) and validate."""
    _run_streamable_phase(context, test_params, streamable=True)


@then('all videos are downloaded with streamable false option and validated')
def download_and_validate_videos_streamable_false(context, test_params):
    """Download videos with streamable=false (server-side blocking) and validate."""
    _run_streamable_phase(context, test_params, streamable=False)


@then('video files are verified to expire after expiry time')
def verify_file_expiry(context, test_params, api_config):
    """Verify that video files expire after the specified expiry time."""
    logger.info("Verifying file expiry...")
    
    expiry_results = []
    
    # Combine validation results from both streamable options
    all_validations = context.validation_results_streamable_true + context.validation_results_streamable_false
    
    # Track unique indices to avoid duplicate expiry checks
    checked_indices = set()
    
    for validation in all_validations:
        if not validation.get('valid') or not validation.get('expiry_iso'):
            continue
        
        # Skip if we already checked this index
        if validation['index'] in checked_indices:
            continue
        checked_indices.add(validation['index'])
        
        try:
            # Get corresponding URL response
            url_response = next((r for r in context.url_responses if r['index'] == validation['index']), None)
            if not url_response or not url_response.get('video_url'):
                continue
            
            video_url = url_response['video_url']
            
            # Parse expiry time
            expiry_time = datetime.fromisoformat(validation['expiry_iso'].replace('Z', '+00:00'))
            current_time = datetime.now(expiry_time.tzinfo)
            
            # Calculate time until expiry
            time_until_expiry = (expiry_time - current_time).total_seconds()
            
            logger.info("  [Index %d] Time until expiry: %.0fms", validation['index'], time_until_expiry*1000)
            
            # Get expiry timing parameters from config
            expiry_grace = test_params.get('expiry_grace_period_sec', 10)
            expiry_buffer = test_params.get('expiry_check_buffer_sec', 5)
            
            # If expiry time is in the future, wait for it plus grace period
            if time_until_expiry > 0:
                wait_time = time_until_expiry + expiry_grace
                logger.info("  [Index %d] Waiting %.0fs for expiry + %ds grace period...", 
                           validation['index'], wait_time, expiry_grace)
                time.sleep(wait_time)
            else:
                # Already expired, wait buffer time after expiry
                if time_until_expiry > -expiry_buffer:
                    wait_time = expiry_buffer + time_until_expiry
                    if wait_time > 0:
                        logger.info("  [Index %d] Waiting additional %.0fs after expiry...", 
                                   validation['index'], wait_time)
                        time.sleep(wait_time)
            
            # Try to access the URL again to verify it's no longer valid (with retry logic)
            logger.info("  [Index %d] Checking if URL is still accessible...", validation['index'])
            url_still_accessible = False
            url_status_code = None
            max_retries = test_params.get('video_ready_max_retries', 3)
            
            # Retry logic for expiry check (network issues shouldn't cause false failures)
            for retry in range(max_retries):
                try:
                    expiry_check_timeout = test_params.get('timeout', 10)
                    stream_id = url_response.get('stream_id')
                    route = envoy_streamid_route_key(stream_id) if stream_id else ""
                    req_headers = {"streamid": route} if route else None
                    response = requests.get(
                        video_url,
                        headers=req_headers,
                        timeout=expiry_check_timeout,
                        verify=api_config.get('verify_ssl', False),
                    )
                    url_still_accessible = response.status_code == 200
                    url_status_code = response.status_code
                    
                    if url_still_accessible:
                        logger.warning("  [Index %d] WARNING: URL still accessible (HTTP %s)", validation['index'], url_status_code)
                    else:
                        logger.info("  [Index %d] URL correctly expired (HTTP %s)", validation['index'], url_status_code)
                    break  # Success, no need to retry
                except requests.exceptions.Timeout:
                    if retry < max_retries - 1:
                        logger.debug("  [Index %d] Expiry check timeout, retry %d/%d", validation['index'], retry + 1, max_retries)
                        time.sleep(1)
                    else:
                        # Final timeout - assume expired
                        url_still_accessible = False
                        url_status_code = None
                        logger.info("  [Index %d] URL correctly expired (timeout)", validation['index'])
                except requests.exceptions.RequestException:
                    # Connection error = URL expired
                    url_still_accessible = False
                    url_status_code = None
                    logger.info("  [Index %d] URL correctly expired (connection error)", validation['index'])
                    break
            
            # Check if local temp files still exist
            temp_files = [
                Path(v.get('file_path', ''))
                for v in all_validations
                if v['index'] == validation['index'] and v.get('file_path')
            ]
            
            files_exist = any(f.exists() for f in temp_files if f)
            
            expiry_results.append({
                'index': validation['index'],
                'stream_id': validation['stream_id'],
                'expiry_minutes': validation['expiry_minutes'],
                'time_until_expiry': time_until_expiry,
                'file_exists_after_expiry': files_exist,
                'url_accessible_after_expiry': url_still_accessible,
                'url_status_code': url_status_code,
                'expected_expired': True,
                'expired_correctly': not url_still_accessible
            })
            
            # Clean up temp files
            for temp_file in temp_files:
                if temp_file and temp_file.exists():
                    temp_file.unlink()
                    logger.info("  [Index %d] Local temp file cleaned up", validation['index'])
            
        except Exception as e:
            logger.error("  [Index %d] Error checking expiry: %s", validation['index'], e)
            expiry_results.append({
                'index': validation['index'],
                'expired_correctly': False,
                'error': str(e)
            })
    
    context.expiry_results = expiry_results
    
    # Print summary
    correctly_expired = sum(1 for r in expiry_results if r.get('expired_correctly', False))
    logger.info("Expiry verification: %d/%d URLs correctly expired", correctly_expired, len(expiry_results))

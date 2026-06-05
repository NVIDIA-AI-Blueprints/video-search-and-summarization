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
API Latency Test - Comprehensive latency measurements for VST API.

Tests video downloads, picture API, and concurrent load scenarios
matching the original latency test script functionality.
"""
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote

import pytest
import requests

from .perf_test_utils import save_results_to_csv
from .vss_perf_vst_adaptor import (
    build_and_save,
    csv_rows_to_test_cases,
    discover_platform,
    upload_result_file,
)

logger = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    uuid: str
    stream_type: str  # "rtsp" or "file"
    timeline_start_ms: int
    timeline_end_ms: int


class LatencyTestContext:
    """Context for latency test data."""
    def __init__(self):
        self.streams: List[StreamInfo] = []
        self.csv_rows: List[dict] = []
        self.transcode_config = {
            "overlay": {
                "bbox": {"showAll": True, "objectId": []},
                "color": "red",
                "thickness": 6,
                "opacity": 255,
                "debug": True
            }
        }
        self.overlay_config = {
            "bbox": {"showAll": True},
            "tripwire": {"showAll": True},
            "roi": {"showAll": True},
            "color": "red",
            "thickness": 5,
            "debug": True,
            "opacity": 254
        }


@pytest.fixture(scope="function")
def latency_context():
    """Create latency test context."""
    return LatencyTestContext()


def iso_to_ms(iso_time: str) -> int:
    """Convert ISO timestamp to milliseconds since epoch."""
    try:
        iso_time = iso_time.rstrip('Z')
        if '.' in iso_time:
            dt = datetime.strptime(iso_time, "%Y-%m-%dT%H:%M:%S.%f")
        else:
            dt = datetime.strptime(iso_time, "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def ms_to_iso(ms: int) -> str:
    """Convert milliseconds since epoch to ISO timestamp."""
    sec = ms // 1000
    ms_only = ms % 1000
    dt = datetime.fromtimestamp(sec, timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms_only:03d}Z"


def now_ms() -> int:
    """Get current time in milliseconds since epoch."""
    return int(time.time() * 1000)


def calc_timestamps_rtsp(offset_ms: int, duration_ms: int) -> Tuple[str, str]:
    """Calculate timestamps for RTSP streams (wall clock based)."""
    current_ms = now_ms()
    end_ms = current_ms - offset_ms
    end_time = ms_to_iso(end_ms)
    
    if duration_ms > 0:
        start_ms = end_ms - duration_ms
        start_time = ms_to_iso(start_ms)
    else:
        start_time = ""
    
    return start_time, end_time


def calc_timestamps_timeline(timeline_start_ms: int, timeline_end_ms: int,
                             duration_ms: int, recency: str, offset_ms: int = 0) -> Tuple[str, str]:
    """Calculate timestamps for non-RTSP streams (timeline based)."""
    timeline_duration = timeline_end_ms - timeline_start_ms
    
    # If offset_ms is specified, use it regardless of recency type
    if offset_ms > 0:
        end_ms = timeline_end_ms - offset_ms
    elif recency == "very_recent":
        end_ms = timeline_end_ms - 1000
    else:
        end_ms = timeline_end_ms - (timeline_duration // 4)
    
    end_time = ms_to_iso(end_ms)
    
    if duration_ms > 0:
        start_ms = end_ms - duration_ms
        if start_ms < timeline_start_ms:
            start_ms = timeline_start_ms
        start_time = ms_to_iso(start_ms)
    else:
        start_time = ""
    
    return start_time, end_time


def get_video_duration_ms(filepath: str) -> Optional[int]:
    """Get video duration in milliseconds using mediainfo."""
    try:
        result = subprocess.run(
            ['mediainfo', '--Output=General;%Duration%', filepath],
            capture_output=True,
            text=True,
            timeout=30
        )
        duration_str = result.stdout.strip()
        if duration_str:
            return int(float(duration_str))
    except Exception:
        pass
    return None


def fetch_streams(context: LatencyTestContext, base_url: str, timeouts: dict, min_timeline_ms: int) -> bool:
    """Fetch available streams from the API."""
    fetch_timeout = timeouts.get('fetch_streams', 30)
    
    logger.info("Fetching streams from %s/vst/api/v1/sensor/streams", base_url)
    try:
        response = requests.get(f"{base_url}/vst/api/v1/sensor/streams", timeout=fetch_timeout)
        response.raise_for_status()
        streams_data = response.json()
    except Exception as e:
        logger.error("Failed to fetch streams: %s", e)
        return False

    if not streams_data:
        logger.error("No streams found")
        return False

    logger.info("Fetching timelines from %s/vst/api/v1/storage/timelines", base_url)
    try:
        response = requests.get(f"{base_url}/vst/api/v1/storage/timelines", timeout=timeouts.get('fetch_timelines', 30))
        response.raise_for_status()
        timelines_data = response.json()
    except Exception:
        logger.warning("Failed to fetch timelines")
        timelines_data = {}

    logger.info("Discovering streams...")
    
    for stream_obj in streams_data:
        for stream_uuid, stream_list in stream_obj.items():
            for stream_info in stream_list:
                if not stream_info.get('isMain', False):
                    continue
                
                url = stream_info.get('url', '')
                
                if 'rtsp' in url.lower():
                    context.streams.append(StreamInfo(
                        uuid=stream_uuid,
                        stream_type='rtsp',
                        timeline_start_ms=0,
                        timeline_end_ms=0
                    ))
                    logger.info("  Found: %s (rtsp)", stream_uuid)
                else:
                    # File-based stream - need timeline
                    if stream_uuid in timelines_data and timelines_data[stream_uuid]:
                        timeline = timelines_data[stream_uuid][-1]
                        tl_start_ms = iso_to_ms(timeline['startTime'])
                        tl_end_ms = iso_to_ms(timeline['endTime'])
                        timeline_duration = tl_end_ms - tl_start_ms
                        
                        if timeline_duration >= min_timeline_ms:
                            context.streams.append(StreamInfo(
                                uuid=stream_uuid,
                                stream_type='file',
                                timeline_start_ms=tl_start_ms,
                                timeline_end_ms=tl_end_ms
                            ))
                            logger.info("  Found: %s (file, timeline: %dms)", stream_uuid, timeline_duration)

    if not context.streams:
        logger.error("No valid streams found")
        return False

    logger.info("Total streams available: %d", len(context.streams))
    return True


def validate_against_limits(metrics: dict, limits: dict, test_type: str) -> Tuple[str, str]:
    """
    Validate metrics against configured limits.
    
    Returns:
        Tuple of (result, reason) where result is 'PASS'/'FAIL'/'PARTIAL' and reason describes any limit violations
    """
    violations = []
    
    # Check success rate
    total = metrics['pass_count'] + metrics['fail_count']
    success_rate = (metrics['pass_count'] / total * 100) if total > 0 else 0
    min_success_rate = limits.get('min_success_rate_per_scenario', 90.0)
    
    if success_rate < min_success_rate:
        violations.append(f"success_rate {success_rate:.1f}% < {min_success_rate}%")
    
    # Check P99 latency
    max_p99 = limits.get('max_p99_ms', float('inf'))
    if metrics['p99'] > max_p99:
        violations.append(f"P99 {metrics['p99']}ms > {max_p99}ms")
    
    # Check average latency
    max_avg = limits.get('max_avg_ms', float('inf'))
    if metrics['avg'] > max_avg:
        violations.append(f"avg {metrics['avg']}ms > {max_avg}ms")
    
    if violations:
        if metrics['result'] == 'FAIL':
            return 'FAIL', '; '.join(violations)
        else:
            return 'PARTIAL', '; '.join(violations)
    
    return metrics['result'], ''


def run_video_download_test(context: LatencyTestContext, base_url: str,
                            duration_sec: int, offset_ms: int, recency_label: str,
                            transcode: str, iterations: int, temp_dir: Path,
                            limits: dict = None, timeouts: dict = None):
    """Run video download test with iterations."""
    duration_ms = duration_sec * 1000
    latencies = []
    pass_count = 0
    fail_count = 0
    failed_clips = []
    MAX_FAILED_CLIPS_TO_KEEP = 3

    stream = random.choice(context.streams)
    
    for i in range(iterations):
        # Calculate timestamps
        if stream.stream_type == 'rtsp':
            start_time, end_time = calc_timestamps_rtsp(offset_ms, duration_ms)
        else:
            recency_type = "very_recent" if "Very recent" in recency_label else "recent"
            start_time, end_time = calc_timestamps_timeline(
                stream.timeline_start_ms, stream.timeline_end_ms, duration_ms, recency_type, offset_ms
            )

        # Build URL
        url_base = f"{base_url}/vst/api/v1/storage/file/{stream.uuid}"
        params = f"startTime={start_time}&endTime={end_time}"
        
        if transcode == "with-transcode":
            config_enc = quote(json.dumps(context.transcode_config))
            url = f"{url_base}?{params}&disableAudio=true&configuration={config_enc}"
        else:
            url = f"{url_base}?{params}"

        out_file = temp_dir / f"clip_{stream.uuid[:8]}_{duration_sec}s_{i}.mp4"
        start_ns = time.time_ns()
        iteration_failed = False
        
        try:
            response = requests.get(url, timeout=300)
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            latencies.append(latency_ms)

            if response.status_code != 200:
                fail_count += 1
                iteration_failed = True
            else:
                with open(out_file, 'wb') as f:
                    f.write(response.content)
                
                dur_ms = get_video_duration_ms(str(out_file))
                min_ok_ms = duration_ms - 1000
                
                if dur_ms is None or dur_ms < min_ok_ms:
                    fail_count += 1
                    iteration_failed = True
                else:
                    pass_count += 1
                    # Cleanup successful clips immediately
                    if out_file.exists():
                        out_file.unlink()
        except Exception:
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            latencies.append(latency_ms)
            fail_count += 1
            iteration_failed = True

        # Track failed clips for bounded retention (save to current directory for inspection)
        if iteration_failed and out_file.exists():
            if len(failed_clips) < MAX_FAILED_CLIPS_TO_KEEP:
                failed_clips.append(out_file)
            else:
                # Remove this failed clip since we've kept enough
                out_file.unlink()

    # Copy retained failed clips to current directory for inspection
    if failed_clips:
        logger.warning("Keeping %d failed clip(s) for inspection:", len(failed_clips))
        for clip_path in failed_clips:
            dest = Path.cwd() / clip_path.name
            try:
                shutil.copy2(clip_path, dest)
                logger.warning("  - %s", dest.name)
            except Exception as e:
                logger.error("  Failed to copy %s: %s", clip_path.name, e)

    # Calculate statistics using nearest-rank method (matching original script)
    latencies.sort()
    n = len(latencies)
    avg_latency = sum(latencies) // n
    p50 = latencies[int((n - 1) * 0.50)]
    p90 = latencies[int((n - 1) * 0.90)]
    p99 = latencies[int((n - 1) * 0.99)]
    max_latency = latencies[-1]
    
    result = "PASS" if fail_count == 0 else ("FAIL" if pass_count == 0 else "PARTIAL")

    # Validate against benchmark limits if provided
    metrics = {
        'avg': avg_latency,
        'p50': p50,
        'p90': p90,
        'p99': p99,
        'max': max_latency,
        'result': result,
        'pass_count': pass_count,
        'fail_count': fail_count
    }
    
    if limits:
        result, violation_reason = validate_against_limits(metrics, limits, 'video_download')
        if violation_reason:
            logger.warning("  LIMIT VIOLATION: %s", violation_reason)
    
    logger.info("%s %s %ds offset=%dms %s %s: avg=%dms p50=%dms p90=%dms p99=%dms max=%dms (%d/%d pass) [%s]",
               stream.stream_type, "video-download", duration_sec, offset_ms, recency_label,
               transcode, avg_latency, p50, p90, p99, max_latency, pass_count, iterations, result)

    context.csv_rows.append({
        'Stream UUID': stream.uuid,
        'Stream Type': stream.stream_type,
        'API': 'video-download',
        'Clip Duration': f"{duration_sec}s",
        'Offset(ms)': offset_ms,
        'Recency pattern': recency_label,
        'Transcode option': transcode,
        'Result': result,
        'Avg Latency(ms)': avg_latency,
        'P50(ms)': p50,
        'P90(ms)': p90,
        'P99(ms)': p99,
        'Max(ms)': max_latency,
        'Pass Count': pass_count,
        'Fail Count': fail_count
    })


def run_picture_api_test(context: LatencyTestContext, base_url: str,
                        offset_ms: int, recency_label: str, overlay: str, iterations: int,
                        limits: dict = None, delay_between_calls: float = 0.5, timeouts: dict = None):
    """Run picture API test with iterations."""
    latencies = []
    pass_count = 0
    fail_count = 0

    stream = random.choice(context.streams)

    for i in range(iterations):
        # Calculate timestamp
        if stream.stream_type == 'rtsp':
            _, end_time = calc_timestamps_rtsp(offset_ms, 0)
        else:
            recency_type = "very_recent" if "Very recent" in recency_label else "recent"
            _, end_time = calc_timestamps_timeline(
                stream.timeline_start_ms, stream.timeline_end_ms, 0, recency_type, offset_ms
            )

        # Build URL
        url_base = f"{base_url}/vst/api/v1/replay/stream/{stream.uuid}/picture"
        
        if overlay == "with-overlay":
            overlay_enc = quote(json.dumps(context.overlay_config))
            url = f"{url_base}?startTime={end_time}&overlay={overlay_enc}"
        else:
            url = f"{url_base}?startTime={end_time}"

        start_ns = time.time_ns()
        
        try:
            response = requests.get(url, timeout=60)
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            latencies.append(latency_ms)

            if response.status_code != 200 or len(response.content) == 0:
                fail_count += 1
            else:
                pass_count += 1
        except Exception:
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            latencies.append(latency_ms)
            fail_count += 1
        
        # Add small delay between iterations to prevent CUDA memory exhaustion
        if i < iterations - 1:  # Don't sleep after last iteration
            time.sleep(0.5)  # 500ms delay between picture API calls

    # Calculate statistics using nearest-rank method (matching original script)
    latencies.sort()
    n = len(latencies)
    avg_latency = sum(latencies) // n
    p50 = latencies[int((n - 1) * 0.50)]
    p90 = latencies[int((n - 1) * 0.90)]
    p99 = latencies[int((n - 1) * 0.99)]
    max_latency = latencies[-1]
    
    result = "PASS" if fail_count == 0 else ("FAIL" if pass_count == 0 else "PARTIAL")
    
    # Validate against benchmark limits if provided
    metrics = {
        'avg': avg_latency,
        'p50': p50,
        'p90': p90,
        'p99': p99,
        'max': max_latency,
        'result': result,
        'pass_count': pass_count,
        'fail_count': fail_count
    }
    
    if limits:
        result, violation_reason = validate_against_limits(metrics, limits, 'picture_api')
        if violation_reason:
            logger.warning("  LIMIT VIOLATION: %s", violation_reason)

    logger.info("%s picture-api offset=%dms %s %s: avg=%dms p50=%dms p90=%dms p99=%dms max=%dms (%d/%d pass) [%s]",
               stream.stream_type, offset_ms, recency_label, overlay,
               avg_latency, p50, p90, p99, max_latency, pass_count, iterations, result)

    context.csv_rows.append({
        'Stream UUID': stream.uuid,
        'Stream Type': stream.stream_type,
        'API': 'picture-api',
        'Clip Duration': 'NA',
        'Offset(ms)': offset_ms,
        'Recency pattern': recency_label,
        'Transcode option': overlay,
        'Result': result,
        'Avg Latency(ms)': avg_latency,
        'P50(ms)': p50,
        'P90(ms)': p90,
        'P99(ms)': p99,
        'Max(ms)': max_latency,
        'Pass Count': pass_count,
        'Fail Count': fail_count
    })


def run_concurrent_video_test(context: LatencyTestContext, base_url: str,
                              concurrent_count: int, transcode: str, temp_dir: Path,
                              limits: dict = None, timeouts: dict = None,
                              duration_sec: int = 15, offset_ms: int = 10000):
    """Run concurrent video download test."""
    duration_ms = duration_sec * 1000
    stream = random.choice(context.streams)

    # Calculate timestamps
    if stream.stream_type == 'rtsp':
        start_time, end_time = calc_timestamps_rtsp(offset_ms, duration_ms)
    else:
        start_time, end_time = calc_timestamps_timeline(
            stream.timeline_start_ms, stream.timeline_end_ms, duration_ms, "recent", offset_ms
        )

    def single_request(req_id):
        url_base = f"{base_url}/vst/api/v1/storage/file/{stream.uuid}"
        params = f"startTime={start_time}&endTime={end_time}"
        
        if transcode == "with-transcode":
            config_enc = quote(json.dumps(context.transcode_config))
            url = f"{url_base}?{params}&disableAudio=true&configuration={config_enc}"
        else:
            url = f"{url_base}?{params}"

        out_file = temp_dir / f"concurrent_{req_id}.mp4"
        start_ns = time.time_ns()
        status = "PASS"
        
        try:
            video_timeout = timeouts.get('video_download', 300) if timeouts else 300
            response = requests.get(url, timeout=video_timeout)
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000

            if response.status_code != 200:
                status = "FAIL"
            else:
                with open(out_file, 'wb') as f:
                    f.write(response.content)
                
                dur_ms = get_video_duration_ms(str(out_file))
                if dur_ms is None or dur_ms < (duration_ms - 1000):
                    status = "FAIL"
        except Exception:
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            status = "FAIL"
        finally:
            if out_file.exists():
                out_file.unlink()

        return latency_ms, status

    # Launch concurrent requests
    with ThreadPoolExecutor(max_workers=concurrent_count) as executor:
        futures = [executor.submit(single_request, i) for i in range(concurrent_count)]
        results = [future.result() for future in as_completed(futures)]

    # Calculate statistics using nearest-rank method (matching original script)
    latencies = sorted([r[0] for r in results])
    n = len(latencies)
    avg_latency = sum(latencies) // n
    p50 = latencies[int((n - 1) * 0.50)]
    p90 = latencies[int((n - 1) * 0.90)]
    p99 = latencies[int((n - 1) * 0.99)]
    max_latency = latencies[-1]
    
    pass_count = sum(1 for r in results if r[1] == "PASS")
    fail_count = sum(1 for r in results if r[1] == "FAIL")
    result = "PASS" if fail_count == 0 else ("FAIL" if pass_count == 0 else "PARTIAL")
    
    # Validate against benchmark limits if provided
    metrics = {
        'avg': avg_latency,
        'p50': p50,
        'p90': p90,
        'p99': p99,
        'max': max_latency,
        'result': result,
        'pass_count': pass_count,
        'fail_count': fail_count
    }
    
    if limits:
        result, violation_reason = validate_against_limits(metrics, limits, 'concurrent_video')
        if violation_reason:
            logger.warning("  LIMIT VIOLATION: %s", violation_reason)

    logger.info("%s video-download concurrent=%d %s: avg=%dms p50=%dms p90=%dms p99=%dms max=%dms (%d/%d pass) [%s]",
               stream.stream_type, concurrent_count, transcode,
               avg_latency, p50, p90, p99, max_latency, pass_count, concurrent_count, result)

    context.csv_rows.append({
        'Stream UUID': stream.uuid,
        'Stream Type': stream.stream_type,
        'API': 'video-download',
        'Clip Duration': f"{concurrent_count} concurrent",
        'Offset(ms)': 10000,
        'Recency pattern': '15s clip',
        'Transcode option': transcode,
        'Result': result,
        'Avg Latency(ms)': avg_latency,
        'P50(ms)': p50,
        'P90(ms)': p90,
        'P99(ms)': p99,
        'Max(ms)': max_latency,
        'Pass Count': pass_count,
        'Fail Count': fail_count
    })


def run_concurrent_picture_test(context: LatencyTestContext, base_url: str,
                                concurrent_count: int, overlay: str,
                                limits: dict = None, timeouts: dict = None,
                                offset_ms: int = 10000):
    """Run concurrent picture API test."""
    stream = random.choice(context.streams)

    # Calculate timestamp
    if stream.stream_type == 'rtsp':
        _, end_time = calc_timestamps_rtsp(offset_ms, 0)
    else:
        _, end_time = calc_timestamps_timeline(
            stream.timeline_start_ms, stream.timeline_end_ms, 0, "recent", offset_ms
        )

    def single_request(_):
        url_base = f"{base_url}/vst/api/v1/replay/stream/{stream.uuid}/picture"
        
        if overlay == "with-overlay":
            overlay_enc = quote(json.dumps(context.overlay_config))
            url = f"{url_base}?startTime={end_time}&overlay={overlay_enc}"
        else:
            url = f"{url_base}?startTime={end_time}"

        start_ns = time.time_ns()
        status = "PASS"
        
        try:
            picture_timeout = timeouts.get('picture_api', 60) if timeouts else 60
            response = requests.get(url, timeout=picture_timeout)
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000

            if response.status_code != 200 or len(response.content) == 0:
                status = "FAIL"
        except Exception:
            end_ns = time.time_ns()
            latency_ms = (end_ns - start_ns) // 1_000_000
            status = "FAIL"

        return latency_ms, status

    # Launch concurrent requests
    with ThreadPoolExecutor(max_workers=concurrent_count) as executor:
        futures = [executor.submit(single_request, i) for i in range(concurrent_count)]
        results = [future.result() for future in as_completed(futures)]

    # Calculate statistics
    latencies = sorted([r[0] for r in results])
    n = len(latencies)
    avg_latency = sum(latencies) // n
    p50 = latencies[int((n - 1) * 0.50)]
    p90 = latencies[int((n - 1) * 0.90)]
    p99 = latencies[int((n - 1) * 0.99)]
    max_latency = latencies[-1]
    
    pass_count = sum(1 for r in results if r[1] == "PASS")
    fail_count = sum(1 for r in results if r[1] == "FAIL")
    result = "PASS" if fail_count == 0 else ("FAIL" if pass_count == 0 else "PARTIAL")
    
    # Validate against benchmark limits if provided
    metrics = {
        'avg': avg_latency,
        'p50': p50,
        'p90': p90,
        'p99': p99,
        'max': max_latency,
        'result': result,
        'pass_count': pass_count,
        'fail_count': fail_count
    }
    
    if limits:
        result, violation_reason = validate_against_limits(metrics, limits, 'concurrent_picture')
        if violation_reason:
            logger.warning("  LIMIT VIOLATION: %s", violation_reason)

    logger.info("%s picture-api concurrent=%d %s: avg=%dms p50=%dms p90=%dms p99=%dms max=%dms (%d/%d pass) [%s]",
               stream.stream_type, concurrent_count, overlay,
               avg_latency, p50, p90, p99, max_latency, pass_count, concurrent_count, result)

    context.csv_rows.append({
        'Stream UUID': stream.uuid,
        'Stream Type': stream.stream_type,
        'API': 'picture-api',
        'Clip Duration': f"{concurrent_count} concurrent",
        'Offset(ms)': 10000,
        'Recency pattern': 'N/A',
        'Transcode option': overlay,
        'Result': result,
        'Avg Latency(ms)': avg_latency,
        'P50(ms)': p50,
        'P90(ms)': p90,
        'P99(ms)': p99,
        'Max(ms)': max_latency,
        'Pass Count': pass_count,
        'Fail Count': fail_count
    })


@pytest.mark.perf
@pytest.mark.slow
def test_api_latency(latency_context, api_config, perf_params, perf_iterations, request):
    """
    Comprehensive API latency test matching the original latency test script.
    
    Tests all combinations of:
    - Video downloads: 15s, 30s, 60s with various offsets
    - Picture API: with/without overlay
    - Concurrent loads: 5, 10, 20, 50 parallel requests
    - Transcode options: with/without
    
    Total: 40 internal test scenarios, each run with N iterations.
    
    Usage:
        # Default (10 iterations per scenario, from config.json)
        poetry run pytest tests/perf/test_latency.py -v
        
        # Custom iterations (20 iterations per scenario)
        poetry run pytest tests/perf/test_latency.py --perf-iterations 20 -v
        
        # With detailed logging
        poetry run pytest tests/perf/test_latency.py --perf-iterations 5 -v --log-cli-level=INFO
        
        # Run all performance tests
        poetry run pytest -m perf
        
    Note: Do NOT use --count flag with this test (it will generate duplicate CSVs).
          Use --perf-iterations to control iterations per scenario instead.
    """
    base_url = api_config['base_url']
    iterations = perf_iterations
    
    logger.info("=" * 60)
    logger.info("Starting API Latency Test Suite")
    logger.info("Endpoint: %s", base_url)
    logger.info("Iterations per test: %d", iterations)
    logger.info("=" * 60)
    
    suite_start_time = time.time()

    # Get configuration parameters
    timeouts = perf_params.get('timeouts', {})
    min_timeline_ms = perf_params.get('min_timeline_duration_ms', 70000)
    
    # Fetch streams
    assert fetch_streams(latency_context, base_url, timeouts, min_timeline_ms), "Failed to fetch streams"
    
    # Create temp directory for video downloads
    temp_dir = Path(perf_params['temp_perf_dir']) / 'latency'
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Get latency limits from config
    latency_limits = perf_params.get('latency_limits', {})
    video_limits = latency_limits.get('video_download', {})
    picture_limits = latency_limits.get('picture_api', {})
    concurrent_video_limits = latency_limits.get('concurrent_video', {})
    concurrent_picture_limits = latency_limits.get('concurrent_picture', {})
    
    # Get test scenario parameters
    video_offsets = perf_params.get('video_download_offsets_ms', {})
    picture_offsets = perf_params.get('picture_api_offsets_ms', [1000, 10000])
    video_concurrent_levels = perf_params.get('video_concurrent_levels', [5, 10, 20, 50])
    video_concurrent_duration = perf_params.get('video_concurrent_duration_sec', 15)
    video_concurrent_offset = perf_params.get('video_concurrent_offset_ms', 10000)
    
    try:
        # Video download without transcode
        logger.info("\n--- video-download without-transcode ---")
        for duration in [15, 30, 60]:
            offsets = video_offsets.get(f"{duration}s", [])
            for offset in offsets:
                run_video_download_test(latency_context, base_url, duration, offset, "Very recent" if offset < 5000 else "Recent", 
                                       "without-transcode", iterations, temp_dir, video_limits, timeouts)

        # Video download with transcode
        logger.info("\n--- video-download with-transcode ---")
        for offset in video_offsets.get("15s_with_transcode", []):
            run_video_download_test(latency_context, base_url, 15, offset, "Very recent" if offset < 5000 else "Recent",
                                   "with-transcode", iterations, temp_dir, video_limits, timeouts)
        for duration in [30, 60]:
            offsets = video_offsets.get(f"{duration}s", [])
            for offset in offsets:
                run_video_download_test(latency_context, base_url, duration, offset, "Very recent" if offset < 5000 else "Recent",
                                       "with-transcode", iterations, temp_dir, video_limits, timeouts)

        # Picture API - use configured iterations to avoid CUDA memory exhaustion
        logger.info("\n--- picture-api ---")
        picture_iterations = perf_params.get('picture_api_iterations', 5)
        picture_delay = perf_params.get('picture_api_delay_between_calls_sec', 0.5)
        logger.info("Using %d iterations with %.1fs delays for picture API tests (configured to avoid CUDA OOM)", 
                   picture_iterations, picture_delay)
        
        for offset in picture_offsets:
            recency = "Very recent" if offset < 5000 else "Recent"
            run_picture_api_test(latency_context, base_url, offset, recency, "without-overlay", 
                               picture_iterations, picture_limits, picture_delay, timeouts)
            run_picture_api_test(latency_context, base_url, offset, recency, "with-overlay", 
                               picture_iterations, picture_limits, picture_delay, timeouts)

        # Concurrent tests: video-download without-transcode
        logger.info("\n--- Concurrent tests: video-download without-transcode ---")
        logger.info("Using concurrency levels %s", video_concurrent_levels)
        for level in video_concurrent_levels:
            run_concurrent_video_test(latency_context, base_url, level, "without-transcode", temp_dir, 
                                     concurrent_video_limits, timeouts, video_concurrent_duration, video_concurrent_offset)

        # Concurrent tests: video-download with-transcode
        logger.info("\n--- Concurrent tests: video-download with-transcode ---")
        logger.info("Using concurrency levels %s", video_concurrent_levels)
        for level in video_concurrent_levels:
            run_concurrent_video_test(latency_context, base_url, level, "with-transcode", temp_dir, 
                                     concurrent_video_limits, timeouts, video_concurrent_duration, video_concurrent_offset)

        # Concurrent tests: picture-api without-overlay
        # Using configured concurrency levels to avoid CUDA memory exhaustion
        concurrent_levels = perf_params.get('picture_api_concurrent_levels', [2, 5, 10, 15])
        concurrent_delay = perf_params.get('picture_api_delay_between_concurrent_tests_sec', 2)
        
        logger.info("\n--- Concurrent tests: picture-api without-overlay ---")
        logger.info("Using concurrency levels %s with %ds delays to avoid CUDA OOM", 
                   concurrent_levels, concurrent_delay)
        
        for i, level in enumerate(concurrent_levels):
            run_concurrent_picture_test(latency_context, base_url, level, "without-overlay", 
                                       concurrent_picture_limits, timeouts, video_concurrent_offset)
            if i < len(concurrent_levels) - 1:  # Don't sleep after last test
                time.sleep(concurrent_delay)

        # Concurrent tests: picture-api with-overlay
        logger.info("\n--- Concurrent tests: picture-api with-overlay ---")
        logger.info("Using concurrency levels %s with %ds delays to avoid CUDA OOM", 
                   concurrent_levels, concurrent_delay)
        
        for i, level in enumerate(concurrent_levels):
            run_concurrent_picture_test(latency_context, base_url, level, "with-overlay", 
                                       concurrent_picture_limits, timeouts, video_concurrent_offset)
            if i < len(concurrent_levels) - 1:  # Don't sleep after last test
                time.sleep(concurrent_delay)

        # Save results to CSV
        fieldnames = ['Stream UUID', 'Stream Type', 'API', 'Clip Duration', 'Offset(ms)',
                     'Recency pattern', 'Transcode option', 'Result',
                     'Avg Latency(ms)', 'P50(ms)', 'P90(ms)', 'P99(ms)', 'Max(ms)',
                     'Pass Count', 'Fail Count']
        
        csv_file = save_results_to_csv('api_latency_comprehensive', latency_context.csv_rows, fieldnames, 'latency')
        logger.info("\nCSV results saved to: %s", csv_file)

        # VSS result JSON export (--perf-output-json)
        output_json = request.config.getoption("--perf-output-json", default=None)
        if output_json and latency_context.csv_rows:
            try:
                test_cases = csv_rows_to_test_cases(latency_context.csv_rows)
                config_id = request.config.getoption("--perf-config-id", default="h100-rtsp")
                release = request.config.getoption("--perf-release", default="")
                try:
                    platform = discover_platform(config_id=config_id)
                except Exception:
                    platform = {"gpu": {"model": "Unknown", "count": 0}}
                result_config = {
                    "config_id": config_id,
                    "base_url": base_url,
                    "iterations": iterations,
                }
                json_path = build_and_save(
                    output_json,
                    "Video-IO",
                    test_cases,
                    config_id=config_id,
                    platform=platform,
                    config=result_config,
                    benchmark_name="api_latency",
                    benchmark_mode="latency",
                    triggered_by="manual",
                    duration_seconds=time.time() - suite_start_time,
                    release=release,
                    passed=sum(1 for r in latency_context.csv_rows if r["Result"] == "PASS"),
                    failed=sum(1 for r in latency_context.csv_rows if r["Result"] != "PASS"),
                )
                logger.info("VSS result JSON: %s", json_path)
                if request.config.getoption("--perf-upload", default=False):
                    if upload_result_file(str(json_path), "Video-IO"):
                        logger.info("Uploaded to MinIO: Video-IO/%s", json_path.name)
                    else:
                        logger.warning("MinIO upload failed. Check MINIO_ENDPOINT and credentials.")
            except Exception as e:
                logger.warning("VSS JSON export failed: %s", e)

        # Summary
        total_tests = len(latency_context.csv_rows)
        passed = sum(1 for row in latency_context.csv_rows if row['Result'] == 'PASS')
        failed = sum(1 for row in latency_context.csv_rows if row['Result'] == 'FAIL')
        partial = sum(1 for row in latency_context.csv_rows if row['Result'] == 'PARTIAL')
        
        logger.info("\n" + "=" * 60)
        logger.info("Latency Test Summary:")
        logger.info("  Total: %d | Passed: %d | Failed: %d | Partial: %d", total_tests, passed, failed, partial)
        logger.info("  Pass Rate: %.1f%%", (passed / total_tests * 100))
        logger.info("=" * 60)
        
        # Collect detailed failure information
        failed_scenarios = []
        for row in latency_context.csv_rows:
            if row['Result'] in ['FAIL', 'PARTIAL']:
                scenario_desc = (
                    f"{row['API']} "
                    f"duration={row['Clip Duration']} "
                    f"offset={row['Offset(ms)']}ms "
                    f"{row['Transcode option']} "
                    f"[{row['Stream Type']}]: "
                    f"{row['Result']} "
                    f"(passed: {row['Pass Count']}/{row['Pass Count'] + row['Fail Count']}, "
                    f"avg={row['Avg Latency(ms)']}ms, "
                    f"p95={row['P90(ms)']}ms, "
                    f"p99={row['P99(ms)']}ms)"
                )
                failed_scenarios.append(scenario_desc)
                logger.error("  FAILED SCENARIO: %s", scenario_desc)
        
        # Log failures/partials but always pass (collect metrics, not enforce thresholds)
        if failed > 0 or partial > 0:
            logger.warning("\n" + "=" * 60)
            logger.warning("LATENCY TEST - SOME SCENARIOS DID NOT PASS")
            logger.warning("=" * 60)
            logger.warning("Failed/Partial Scenarios:")
            for scenario_desc in failed_scenarios:
                logger.warning("  - %s", scenario_desc)
            logger.warning("")
            logger.warning("Summary:")
            logger.warning("  Target: All %d scenarios PASS (100%% success rate)", total_tests)
            logger.warning("  Actual: %d PASS, %d FAIL, %d PARTIAL", passed, failed, partial)
            logger.warning("  Pass Rate: %.1f%%", (passed / total_tests * 100))
            logger.warning("")
            logger.warning("Check CSV for full details: reports/latency/api_latency_comprehensive_*.csv")
            logger.warning("=" * 60)
            logger.warning("\nNote: Test PASSED (latency tests collect metrics, not enforce pass/fail)")
        else:
            logger.info("\n✓ All %d scenarios PASSED", total_tests)
        
        # Always pass - we're measuring latency, not enforcing strict pass/fail
        logger.info("\n✓ Latency test completed - %d scenarios executed, metrics collected", total_tests)
        
    finally:
        # Cleanup temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

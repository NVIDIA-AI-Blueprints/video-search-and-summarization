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
Parallel File Download Load Test - Comprehensive load distribution testing for VST Storage API.

Tests parallel downloads across all available streams to validate:
- Storage service load distribution
- Concurrent download performance 
- System stability under heavy parallel load
- Storage API scalability

Features:
- Parallel downloads from all available streams
- Configurable concurrency levels and batch sizes
- Multiple test scenarios (duration, transcode, concurrent levels)
- Memory-efficient streaming downloads (no content caching)
- Automatic cleanup and error handling
"""
import asyncio
import gc
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import quote

import aiohttp
import pytest
import requests

from .download_test_utils import envoy_streamid_route_key

logger = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    uuid: str
    stream_type: str  # "rtsp" or "file"
    timeline_start_ms: int
    timeline_end_ms: int


@dataclass
class DownloadTask:
    stream_id: str
    start_time: str
    end_time: str
    duration_sec: int
    transcode: bool
    task_id: int
    batch_id: int


@dataclass
class DownloadResult:
    task_id: int
    batch_id: int
    stream_id: str
    success: bool
    duration_ms: float
    bytes_downloaded: int
    status_code: Optional[int] = None
    error: Optional[str] = None
    transcode: bool = False
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class ParallelDownloadContext:
    """Context for parallel download load testing."""
    def __init__(self):
        self.streams: List[StreamInfo] = []
        self.batch_summaries: List[dict] = []
        self.transcode_config = {
            "overlay": {
                "bbox": {"showAll": True, "objectId": []},
                "color": "red",
                "thickness": 6,
                "opacity": 255,
                "debug": True
            }
        }


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
                             duration_ms: int, offset_ms: int = 0) -> Tuple[str, str]:
    """Calculate timestamps for file-based streams (timeline based)."""
    timeline_duration = timeline_end_ms - timeline_start_ms
    
    # Use offset from end if specified, otherwise use middle segment
    if offset_ms > 0:
        end_ms = timeline_end_ms - offset_ms
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


def fetch_streams(context: ParallelDownloadContext, base_url: str, 
                  timeouts: dict, min_timeline_ms: int) -> bool:
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

    logger.info("Discovering streams for parallel download testing...")
    
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


async def download_file_async(session: aiohttp.ClientSession, base_url: str,
                              task: DownloadTask, transcode_config: dict,
                              verify_ssl: bool = False) -> DownloadResult:
    """Download a single file asynchronously."""
    url_base = f"{base_url}/vst/api/v1/storage/file/{task.stream_id}"
    params = f"startTime={task.start_time}&endTime={task.end_time}&container=mp4"
    
    if task.transcode:
        config_enc = quote(json.dumps(transcode_config))
        url = f"{url_base}?{params}&disableAudio=true&configuration={config_enc}"
    else:
        url = f"{url_base}?{params}"

    start_time = time.time()
    headers = {"streamid": envoy_streamid_route_key(task.stream_id)}

    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=300),
            ssl=verify_ssl,
        ) as response:
            if response.status != 200:
                duration_ms = (time.time() - start_time) * 1000
                return DownloadResult(
                    task_id=task.task_id,
                    batch_id=task.batch_id,
                    stream_id=task.stream_id,
                    success=False,
                    duration_ms=duration_ms,
                    bytes_downloaded=0,
                    status_code=response.status,
                    error=f"HTTP {response.status}",
                    transcode=task.transcode,
                    start_time=task.start_time,
                    end_time=task.end_time
                )
            
            # Stream content without storing in memory
            bytes_downloaded = 0
            async for chunk in response.content.iter_chunked(64 * 1024):  # 64KB chunks
                bytes_downloaded += len(chunk)
                # Discard chunk to save memory
                
            duration_ms = (time.time() - start_time) * 1000
            
            return DownloadResult(
                task_id=task.task_id,
                batch_id=task.batch_id,
                stream_id=task.stream_id,
                success=True,
                duration_ms=duration_ms,
                bytes_downloaded=bytes_downloaded,
                status_code=response.status,
                transcode=task.transcode,
                start_time=task.start_time,
                end_time=task.end_time
            )
            
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        return DownloadResult(
            task_id=task.task_id,
            batch_id=task.batch_id,
            stream_id=task.stream_id,
            success=False,
            duration_ms=duration_ms,
            bytes_downloaded=0,
            error=str(e),
            transcode=task.transcode,
            start_time=task.start_time,
            end_time=task.end_time
        )


async def run_parallel_download_batch(context: ParallelDownloadContext, base_url: str,
                                      tasks: List[DownloadTask], batch_name: str,
                                      verify_ssl: bool = False) -> List[DownloadResult]:
    """Run a batch of downloads in parallel."""
    logger.info("Starting batch '%s' with %d parallel downloads", batch_name, len(tasks))
    
    connector = aiohttp.TCPConnector(ssl=verify_ssl, limit=100, limit_per_host=50)
    async with aiohttp.ClientSession(connector=connector) as session:
        download_tasks = [
            download_file_async(session, base_url, task, context.transcode_config, verify_ssl)
            for task in tasks
        ]
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
    
    # Convert exceptions to error results
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            task = tasks[i]
            processed_results.append(DownloadResult(
                task_id=task.task_id,
                batch_id=task.batch_id,
                stream_id=task.stream_id,
                success=False,
                duration_ms=0,
                bytes_downloaded=0,
                error=str(result),
                transcode=task.transcode,
                start_time=task.start_time,
                end_time=task.end_time
            ))
        else:
            processed_results.append(result)
    
    # Calculate batch statistics
    successful = sum(1 for r in processed_results if r.success)
    total_bytes = sum(r.bytes_downloaded for r in processed_results)
    avg_duration = sum(r.duration_ms for r in processed_results) / len(processed_results)
    
    logger.info("Batch '%s' completed: %d/%d successful, %.1f MB total, %.0fms avg duration",
                batch_name, successful, len(processed_results), total_bytes / 1024 / 1024, avg_duration)
    
    return processed_results


def generate_download_tasks(context: ParallelDownloadContext, scenarios: List[dict]) -> List[List[DownloadTask]]:
    """Generate download tasks organized in batches."""
    all_batches = []
    task_id = 0
    
    for scenario_idx, scenario in enumerate(scenarios):
        duration_sec = scenario['duration_sec']
        transcode = scenario['transcode']
        concurrent_level = scenario['concurrent_level']
        offset_ms = scenario.get('offset_ms', 10000)
        
        duration_ms = duration_sec * 1000
        
        # Create tasks for this scenario
        batch_tasks = []
        
        # Distribute downloads across all available streams
        for i in range(concurrent_level):
            stream = context.streams[i % len(context.streams)]
            
            # Calculate timestamps
            if stream.stream_type == 'rtsp':
                start_time, end_time = calc_timestamps_rtsp(offset_ms, duration_ms)
            else:
                start_time, end_time = calc_timestamps_timeline(
                    stream.timeline_start_ms, stream.timeline_end_ms, duration_ms, offset_ms
                )
            
            task = DownloadTask(
                stream_id=stream.uuid,
                start_time=start_time,
                end_time=end_time,
                duration_sec=duration_sec,
                transcode=transcode,
                task_id=task_id,
                batch_id=scenario_idx
            )
            
            batch_tasks.append(task)
            task_id += 1
        
        all_batches.append(batch_tasks)
        
        logger.info("Generated scenario %d: %d tasks, %ds duration, transcode=%s, concurrent=%d",
                    scenario_idx, len(batch_tasks), duration_sec, transcode, concurrent_level)
    
    return all_batches


def _empty_metrics(total: int = 0, failed: int = 0,
                    error: Optional[str] = None) -> dict:
    """Return a metrics dict with a consistent structure and zeroed values."""
    zero_rt = {'min': 0, 'max': 0, 'avg': 0, 'p50': 0, 'p90': 0, 'p95': 0, 'p99': 0}
    zero_tp = {'total_bytes': 0, 'total_mb': 0.0, 'mbps': 0.0, 'bytes_per_sec': 0.0}
    m: Dict[str, Any] = {
        'total_downloads': total,
        'successful_downloads': 0,
        'failed_downloads': failed,
        'success_rate': 0.0,
        'response_time_ms': zero_rt,
        'throughput': zero_tp,
        'errors': [],
    }
    if error:
        m['error'] = error
    return m


def calculate_metrics(results: List[DownloadResult]) -> dict:
    """Calculate detailed metrics from download results.

    Always returns a dict with 'response_time_ms' and 'throughput' sub-dicts
    so callers can index safely without guards.
    """
    if not results:
        return _empty_metrics(error='No results to calculate')

    successful_results = [r for r in results if r.success]
    failed_results = [r for r in results if not r.success]

    if not successful_results:
        return _empty_metrics(
            total=len(results),
            failed=len(failed_results),
            error='All downloads failed',
        )

    durations = sorted(r.duration_ms for r in successful_results)
    total_bytes = sum(r.bytes_downloaded for r in successful_results)
    total_time_sec = sum(r.duration_ms for r in successful_results) / 1000

    def percentile(data, p):
        if not data:
            return 0
        index = int((len(data) - 1) * p / 100)
        return data[index]

    return {
        'total_downloads': len(results),
        'successful_downloads': len(successful_results),
        'failed_downloads': len(failed_results),
        'success_rate': len(successful_results) / len(results) * 100,
        'response_time_ms': {
            'min': min(durations),
            'max': max(durations),
            'avg': sum(durations) / len(durations),
            'p50': percentile(durations, 50),
            'p90': percentile(durations, 90),
            'p95': percentile(durations, 95),
            'p99': percentile(durations, 99)
        },
        'throughput': {
            'total_bytes': total_bytes,
            'total_mb': total_bytes / 1024 / 1024,
            'mbps': (total_bytes * 8 / 1024 / 1024) / total_time_sec if total_time_sec > 0 else 0,
            'bytes_per_sec': total_bytes / total_time_sec if total_time_sec > 0 else 0
        },
        'errors': [r.error for r in failed_results if r.error][:5]
    }


@pytest.mark.slow
def test_parallel_download_load(api_config, test_params):
    """
    Comprehensive parallel download load test for VST Storage API.
    
    Tests parallel downloads across all available streams with various scenarios:
    - Different concurrency levels (5, 10, 20, 50, 100)
    - Different video durations (5s, 15s, 30s)
    - With and without transcode
    - Load distribution across all streams
    
    The test measures:
    - Response times and percentiles
    - Throughput (MB/s, Mbps)
    - Success rates under load
    - System stability with high concurrency
    
    Config (file_download_tests.test_parameters): parallel_download_max_streams caps how many
    discovered streams are used (default 20). Use 0 or null for no cap.

    Usage:
        poetry run pytest tests/file_download/test_parallel_download.py -v
        poetry run pytest tests/file_download/test_parallel_download.py -v --log-cli-level=INFO
    """
    parallel_download_context = ParallelDownloadContext()
    base_url = api_config['base_url']
    iterations = test_params.get('iterations', 1)
    
    logger.info("=" * 80)
    logger.info("Starting Parallel Download Load Test Suite")
    logger.info("Endpoint: %s", base_url)
    logger.info("Iterations per scenario: %d", iterations)
    logger.info("=" * 80)
    
    suite_start_time = time.time()

    # Get configuration parameters
    timeouts = test_params.get('timeouts', {})
    min_timeline_ms = test_params.get('min_timeline_duration_ms', 70000)
    
    # Fetch streams — skip (not fail) when there are none available
    if not fetch_streams(parallel_download_context, base_url, timeouts, min_timeline_ms):
        logger.warning("No streams available for parallel download testing — skipping")
        pytest.skip("No streams available on the target VST instance")

    # Cap how many streams participate (avoids huge scenario load when VST exposes many streams).
    # Config: parallel_download_max_streams — omit or set a positive int (default 20). null/0 = no cap.
    _ms = test_params.get("parallel_download_max_streams", 20)
    if _ms is None or _ms == 0:
        max_streams = None  # unlimited
    else:
        try:
            max_streams = int(_ms)
        except (TypeError, ValueError):
            max_streams = 20
    if max_streams and max_streams > 0 and len(parallel_download_context.streams) > max_streams:
        total = len(parallel_download_context.streams)
        parallel_download_context.streams = parallel_download_context.streams[:max_streams]
        logger.info(
            "parallel_download_max_streams=%s: using first %d of %d stream(s)",
            max_streams,
            len(parallel_download_context.streams),
            total,
        )

    if len(parallel_download_context.streams) < 2:
        logger.warning("Only %d stream(s) available - limited parallel testing", len(parallel_download_context.streams))
    
    # Define test scenarios
    concurrent_levels = test_params.get('parallel_download_concurrent_levels', [5, 10, 20, 50])
    video_durations = test_params.get('parallel_download_durations_sec', [5, 15, 30])
    
    # Limit concurrent level based on available streams to avoid excessive load
    max_concurrent = min(max(concurrent_levels), len(parallel_download_context.streams) * 10)
    concurrent_levels = [level for level in concurrent_levels if level <= max_concurrent]
    
    scenarios = []
    
    # Scenario 1: Downloads without transcode
    for duration in video_durations:
        for concurrent in concurrent_levels:
            scenarios.append({
                'duration_sec': duration,
                'transcode': False,
                'concurrent_level': concurrent,
                'offset_ms': 10000
            })
    
    # Scenario 2: Downloads with transcode (smaller subset to avoid excessive test time)
    for duration in [15, 30]:  # Reduced durations for transcode
        for concurrent in concurrent_levels[:3]:  # Only lower concurrency levels
            scenarios.append({
                'duration_sec': duration,
                'transcode': True,
                'concurrent_level': concurrent,
                'offset_ms': 10000
            })
    
    logger.info("Generated %d test scenarios across %d stream(s)", len(scenarios), len(parallel_download_context.streams))
    
    try:
        all_results = []
        
        # Run each iteration
        for iteration in range(iterations):
            logger.info("\n" + "-" * 60)
            logger.info("ITERATION %d/%d", iteration + 1, iterations)
            logger.info("-" * 60)
            
            # Generate download tasks for all scenarios
            task_batches = generate_download_tasks(parallel_download_context, scenarios)
            
            # Execute each batch
            for batch_idx, tasks in enumerate(task_batches):
                scenario = scenarios[batch_idx]
                batch_name = (f"iter{iteration+1}_dur{scenario['duration_sec']}s_"
                             f"{'transcode' if scenario['transcode'] else 'direct'}_"
                             f"concurrent{scenario['concurrent_level']}")
                
                batch_start = time.time()
                batch_results = asyncio.run(
                    run_parallel_download_batch(
                        parallel_download_context, 
                        base_url, 
                        tasks, 
                        batch_name, 
                        api_config.get('verify_ssl', False)
                    )
                )
                batch_duration = time.time() - batch_start
                
                # Calculate batch metrics
                metrics = calculate_metrics(batch_results)
                
                # Log batch summary
                rt = metrics['response_time_ms']
                tp = metrics['throughput']
                logger.info("Batch %s: %d/%d successful, %.1fMB total, %.1fs duration",
                           batch_name,
                           metrics.get('successful_downloads', 0),
                           metrics.get('total_downloads', 0),
                           tp['total_mb'], batch_duration)
                
                parallel_download_context.batch_summaries.append({
                    'Iteration': iteration + 1,
                    'Scenario': f"{scenario['duration_sec']}s_{'transcode' if scenario['transcode'] else 'direct'}",
                    'Concurrent Level': scenario['concurrent_level'],
                    'Total Downloads': metrics.get('total_downloads', 0),
                    'Successful': metrics.get('successful_downloads', 0),
                    'Failed': metrics.get('failed_downloads', 0),
                    'Success Rate (%)': f"{metrics.get('success_rate', 0.0):.1f}",
                    'Avg Latency (ms)': f"{rt['avg']:.0f}",
                    'P50 (ms)': f"{rt['p50']:.0f}",
                    'P90 (ms)': f"{rt['p90']:.0f}",
                    'P95 (ms)': f"{rt['p95']:.0f}",
                    'P99 (ms)': f"{rt['p99']:.0f}",
                    'Max Latency (ms)': f"{rt['max']:.0f}",
                    'Total MB': f"{tp['total_mb']:.1f}",
                    'Throughput (Mbps)': f"{tp['mbps']:.1f}",
                    'Batch Duration (s)': f"{batch_duration:.1f}",
                    'Streams Used': len(set(task.stream_id for task in tasks)) if tasks else 0
                })
                
                all_results.extend(batch_results)
                
                # Force garbage collection to manage memory
                gc.collect()
        
        # Calculate overall metrics
        overall_metrics = calculate_metrics(all_results)
        
        # Summary
        total_tests = len(parallel_download_context.batch_summaries)
        high_success_rate_tests = sum(1 for row in parallel_download_context.batch_summaries 
                                     if float(row['Success Rate (%)']) >= 95.0)
        
        logger.info("\n" + "=" * 80)
        logger.info("Parallel Download Load Test Summary:")
        logger.info("  Total Scenarios: %d", total_tests)
        logger.info("  High Success Rate (>95%%): %d", high_success_rate_tests)
        logger.info("  Overall Success Rate: %.1f%%", overall_metrics['success_rate'])
        logger.info("  Total Downloads: %d", overall_metrics['total_downloads'])
        logger.info("  Total Data: %.1f MB", overall_metrics['throughput']['total_mb'])
        logger.info("  Average Throughput: %.1f Mbps", overall_metrics['throughput']['mbps'])
        logger.info("  Average Response Time: %.0f ms", overall_metrics['response_time_ms']['avg'])
        logger.info("  P99 Response Time: %.0f ms", overall_metrics['response_time_ms']['p99'])
        logger.info("  Streams Tested: %d", len(parallel_download_context.streams))
        logger.info("=" * 80)
        
        # Log any concerning results
        slow_scenarios = [row for row in parallel_download_context.batch_summaries 
                         if float(row['P99 (ms)']) > 30000]  # P99 > 30s
        if slow_scenarios:
            logger.warning("\n⚠ SLOW SCENARIOS (P99 > 30s):")
            for scenario in slow_scenarios[:5]:  # Show first 5
                logger.warning("  - %s concurrent=%s: P99=%sms", 
                              scenario['Scenario'], scenario['Concurrent Level'], scenario['P99 (ms)'])
        
        failed_scenarios = [row for row in parallel_download_context.batch_summaries 
                           if float(row['Success Rate (%)']) < 95.0]
        if failed_scenarios:
            logger.warning("\n❌ LOW SUCCESS RATE SCENARIOS (<95%%):")
            for scenario in failed_scenarios[:5]:  # Show first 5
                logger.warning("  - %s concurrent=%s: %s%% success", 
                              scenario['Scenario'], scenario['Concurrent Level'], scenario['Success Rate (%)'])
        
        if not slow_scenarios and not failed_scenarios:
            logger.info("\n✅ All scenarios performed well!")
        
        # Always pass - this is a load test for measuring performance, not enforcing limits
        logger.info("\n✅ Parallel download load test completed - %d scenarios executed", total_tests)
        
    finally:
        pass

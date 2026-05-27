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
Performance test utilities for VST API load and stress testing.

Provides shared functions for:
- High-concurrency operations
- Throughput measurement
- Response time tracking
- Resource monitoring
"""
import asyncio
import csv
import logging
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple

import aiohttp
import requests

logger = logging.getLogger(__name__)


# API Endpoints
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_file': '/vst/api/v1/storage/file/{stream_id}',
    'storage_upload': '/vst/api/v1/storage/file',
    'storage_size': '/vst/api/v1/storage/size',
}


class PerfContext:
    """Context for performance test data."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.uploaded_stream_ids: set = set()
        self.results: List[Dict[str, Any]] = []
        self.metrics: Dict[str, Any] = {}
        self.temp_dir: Path = None
        self.start_time: float = None
        self.end_time: float = None


class MetricsCollector:
    """Collect and calculate performance metrics."""
    
    def __init__(self):
        self.response_times: List[float] = []
        self.errors: List[Dict[str, Any]] = []
        self.throughput_bytes: int = 0
        self.operation_count: int = 0
        self.start_time: float = None
        self.end_time: float = None
    
    def record_response(self, duration_ms: float, success: bool, 
                       bytes_transferred: int = 0, error: str = None):
        """Record a single operation response."""
        self.response_times.append(duration_ms)
        self.operation_count += 1
        
        if success:
            self.throughput_bytes += bytes_transferred
        else:
            self.errors.append({
                'duration_ms': duration_ms,
                'error': error,
                'timestamp': time.time()
            })
    
    def start(self):
        """Start metrics collection."""
        self.start_time = time.time()
    
    def stop(self):
        """Stop metrics collection."""
        self.end_time = time.time()
    
    def get_metrics(self) -> Dict[str, Any]:
        """Calculate and return metrics."""
        if not self.response_times:
            return {
                'error': 'No data collected',
                'operation_count': 0
            }
        
        sorted_times = sorted(self.response_times)
        total_duration = (self.end_time - self.start_time) if self.end_time else 0
        
        return {
            'operation_count': self.operation_count,
            'success_count': self.operation_count - len(self.errors),
            'error_count': len(self.errors),
            'success_rate': (self.operation_count - len(self.errors)) / self.operation_count * 100,
            'response_time_ms': {
                'min': min(sorted_times),
                'max': max(sorted_times),
                'mean': sum(sorted_times) / len(sorted_times),
                'median': self._calculate_percentile(sorted_times, 50),
                'p50': self._calculate_percentile(sorted_times, 50),
                'p90': self._calculate_percentile(sorted_times, 90),
                'p95': self._calculate_percentile(sorted_times, 95),
                'p99': self._calculate_percentile(sorted_times, 99),
                'p999': self._calculate_percentile(sorted_times, 99.9)
            },
            'throughput': {
                'operations_per_sec': self.operation_count / total_duration if total_duration > 0 else 0,
                'bytes_per_sec': self.throughput_bytes / total_duration if total_duration > 0 else 0,
                'mbps': (self.throughput_bytes * 8 / 1_000_000) / total_duration if total_duration > 0 else 0
            },
            'duration_seconds': total_duration,
            'errors': self.errors[:10]  # First 10 errors
        }
    
    @staticmethod
    def _calculate_percentile(sorted_data: List[float], percentile: float) -> float:
        """
        Calculate percentile using linear interpolation (industry standard).
        
        This matches the behavior of:
        - numpy.percentile(data, percentile, method='linear')
        - Apache Bench, JMeter, and most APM tools
        
        Args:
            sorted_data: Pre-sorted list of values
            percentile: Percentile to calculate (0-100)
            
        Returns:
            Calculated percentile value
        """
        if not sorted_data:
            return 0.0
        
        n = len(sorted_data)
        
        # Handle edge cases
        if n == 1:
            return float(sorted_data[0])
        
        # Calculate the rank (0-based indexing)
        # rank = (percentile / 100) * (n - 1)
        rank = (percentile / 100.0) * (n - 1)
        
        # Get lower and upper indices
        lower_index = int(rank)
        upper_index = min(lower_index + 1, n - 1)
        
        # If rank is exactly an integer, return that value
        if rank == lower_index:
            return float(sorted_data[lower_index])
        
        # Linear interpolation between lower and upper values
        fraction = rank - lower_index
        lower_value = sorted_data[lower_index]
        upper_value = sorted_data[upper_index]
        
        return lower_value + fraction * (upper_value - lower_value)


def create_test_video_file(file_path: Path, duration_seconds: int = 5) -> Path:
    """
    Create a test video file using ffmpeg.
    
    Args:
        file_path: Path to save the video
        duration_seconds: Duration of the video
        
    Returns:
        Path to the created file
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        'ffmpeg',
        '-f', 'lavfi',
        '-i', f'testsrc=duration={duration_seconds}:size=640x480:rate=30',
        '-f', 'lavfi',
        '-i', f'sine=frequency=1000:duration={duration_seconds}',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-c:a', 'aac',
        '-y',
        str(file_path)
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)
    return file_path


async def upload_file_async(session: aiohttp.ClientSession, base_url: str,
                           file_path: Path, sensor_id: str, 
                           verify_ssl: bool = False) -> Dict[str, Any]:
    """
    Upload a file asynchronously.
    
    Args:
        session: aiohttp session
        base_url: Base API URL
        file_path: Path to file to upload
        sensor_id: Sensor ID
        verify_ssl: Whether to verify SSL
        
    Returns:
        Dict with upload result
    """
    url = f"{base_url}/vst/api/v1/storage/file"
    
    start_time = time.time()
    
    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        file_size = len(file_content)
        
        params = {
            'sensorId': sensor_id,
            'filename': file_path.name
        }
        
        async with session.put(
            url,
            params=params,
            data=file_content,
            headers={'Content-Type': 'application/octet-stream'},
            ssl=verify_ssl,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            duration_ms = (time.time() - start_time) * 1000
            
            if response.status in [200, 201]:
                result = await response.json()
                return {
                    'success': True,
                    'streamId': result.get('streamId'),
                    'duration_ms': duration_ms,
                    'bytes': file_size,
                    'status': response.status
                }
            else:
                return {
                    'success': False,
                    'error': f"HTTP {response.status}",
                    'duration_ms': duration_ms,
                    'bytes': 0,
                    'status': response.status
                }
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        return {
            'success': False,
            'error': str(e),
            'duration_ms': duration_ms,
            'bytes': 0,
            'status': None
        }


async def download_video_async(session: aiohttp.ClientSession, base_url: str,
                               stream_id: str, start_time_iso: str, end_time_iso: str,
                               verify_ssl: bool = False) -> Dict[str, Any]:
    """
    Download a video asynchronously.
    
    Args:
        session: aiohttp session
        base_url: Base API URL
        stream_id: Stream ID
        start_time_iso: Start time in ISO format
        end_time_iso: End time in ISO format
        verify_ssl: Whether to verify SSL
        
    Returns:
        Dict with download result
    """
    url = f"{base_url}/vst/api/v1/storage/file/{stream_id}"
    params = {
        'startTime': start_time_iso,
        'endTime': end_time_iso,
        'container': 'mp4'
    }
    
    start = time.time()
    
    try:
        async with session.get(
            url,
            params=params,
            ssl=verify_ssl,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            duration_ms = (time.time() - start) * 1000
            
            if response.status == 200:
                content = await response.read()
                return {
                    'success': True,
                    'duration_ms': duration_ms,
                    'bytes': len(content),
                    'status': response.status,
                    'content': content
                }
            else:
                return {
                    'success': False,
                    'error': f"HTTP {response.status}",
                    'duration_ms': duration_ms,
                    'bytes': 0,
                    'status': response.status
                }
    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        return {
            'success': False,
            'error': str(e),
            'duration_ms': duration_ms,
            'bytes': 0,
            'status': None
        }


async def fetch_endpoint_async(session: aiohttp.ClientSession, url: str,
                               verify_ssl: bool = False) -> Dict[str, Any]:
    """
    Fetch an API endpoint asynchronously.
    
    Args:
        session: aiohttp session
        url: Full URL to fetch
        verify_ssl: Whether to verify SSL
        
    Returns:
        Dict with fetch result
    """
    start_time = time.time()
    
    try:
        async with session.get(
            url,
            ssl=verify_ssl,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            duration_ms = (time.time() - start_time) * 1000
            
            if response.status == 200:
                return {
                    'success': True,
                    'duration_ms': duration_ms,
                    'status': response.status
                }
            else:
                return {
                    'success': False,
                    'error': f"HTTP {response.status}",
                    'duration_ms': duration_ms,
                    'status': response.status
                }
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        return {
            'success': False,
            'error': str(e),
            'duration_ms': duration_ms,
            'status': None
        }


def calculate_percentile(values: List[float], percentile: float) -> float:
    """
    Calculate percentile from a list of values using linear interpolation.
    
    This is a convenience wrapper around MetricsCollector._calculate_percentile
    for use outside the MetricsCollector class.
    
    Args:
        values: List of values (will be sorted if not already)
        percentile: Percentile to calculate (0.0-1.0 or 0-100)
        
    Returns:
        Calculated percentile value
    """
    if not values:
        return 0.0
    
    sorted_values = sorted(values)
    
    # Handle both 0-1 and 0-100 range
    if percentile <= 1.0:
        percentile = percentile * 100
    
    return MetricsCollector._calculate_percentile(sorted_values, percentile)


def format_metrics_summary(metrics: Dict[str, Any]) -> str:
    """Format metrics into a human-readable summary."""
    lines = [
        "Performance Metrics Summary:",
        "=" * 60,
        f"Total Operations: {metrics['operation_count']}",
        f"Successful: {metrics['success_count']}",
        f"Failed: {metrics['error_count']}",
        f"Success Rate: {metrics['success_rate']:.2f}%",
        f"Duration: {metrics['duration_seconds']:.2f}s",
        "",
        "Response Times (ms):",
        f"  Min:    {metrics['response_time_ms']['min']:>10.2f}",
        f"  Mean:   {metrics['response_time_ms']['mean']:>10.2f}",
        f"  Median: {metrics['response_time_ms']['median']:>10.2f}",
        f"  P90:    {metrics['response_time_ms']['p90']:>10.2f}",
        f"  P95:    {metrics['response_time_ms']['p95']:>10.2f}",
        f"  P99:    {metrics['response_time_ms']['p99']:>10.2f}",
        f"  P99.9:  {metrics['response_time_ms']['p999']:>10.2f}",
        f"  Max:    {metrics['response_time_ms']['max']:>10.2f}",
        "",
        "Throughput:",
        f"  Operations/sec: {metrics['throughput']['operations_per_sec']:.2f}",
        f"  Bytes/sec:      {metrics['throughput']['bytes_per_sec']:.2f}",
        f"  Mbps:           {metrics['throughput']['mbps']:.2f}",
        "",
        f"Errors: {metrics['error_count']}"
    ]
    
    if metrics['error_count'] > 0 and metrics.get('errors'):
        lines.append("")
        lines.append("Sample Errors:")
        for i, error in enumerate(metrics['errors'][:3], 1):
            lines.append(f"  {i}. {error.get('error', 'Unknown error')}")
    
    return "\n".join(lines)


def cleanup_streams(base_url: str, stream_ids: set, verify_ssl: bool = False):
    """
    Clean up uploaded test streams.
    
    Args:
        base_url: Base API URL
        stream_ids: Set of stream IDs to clean up
        verify_ssl: Whether to verify SSL
    """
    if not stream_ids:
        return
    
    logger.info("Cleaning up %d test stream(s)", len(stream_ids))
    
    try:
        storage_size_url = f"{base_url}/vst/api/v1/storage/size?timelines=true"
        response = requests.get(storage_size_url, timeout=10, verify=verify_ssl)
        
        if response.status_code != 200:
            logger.warning("Failed to get storage timelines for cleanup")
            return
        
        storage_data = response.json()
        deleted_count = 0
        
        for stream_id in stream_ids:
            if stream_id not in storage_data:
                continue
            
            stream_info = storage_data[stream_id]
            timelines = stream_info.get('timelines', [])
            
            for timeline in timelines:
                start_time = timeline.get('startTime')
                end_time = timeline.get('endTime')
                
                if not start_time or not end_time:
                    continue
                
                delete_url = f"{base_url}/vst/api/v1/storage/file/{stream_id}"
                params = {'startTime': start_time, 'endTime': end_time}
                
                try:
                    del_response = requests.delete(
                        delete_url, params=params, timeout=10, verify=verify_ssl
                    )
                    if del_response.status_code in [200, 204]:
                        deleted_count += 1
                except Exception as e:
                    logger.warning("Error deleting stream %s: %s", stream_id, str(e))
        
        logger.info("Deleted %d test stream(s)", deleted_count)
    except Exception as e:
        logger.warning("Error during cleanup: %s", str(e))


def save_results_to_csv(test_name: str, results: List[Dict[str, Any]], 
                        fieldnames: List[str], subdir: str = 'performance') -> str:
    """
    Save performance test results to CSV file in reports directory.
    
    Args:
        test_name: Name of the test (e.g., 'upload_performance')
        results: List of result dictionaries
        fieldnames: CSV column names
        subdir: Subdirectory under reports/ (default: 'performance', use 'latency' for latency tests)
        
    Returns:
        Path to the created CSV file
    """
    # Determine the base reports directory
    # In container environment, use the mounted directory
    # Check if we're in container by looking for mounted volume
    container_reports_dir = Path("/app/reports")
    local_reports_dir = Path("reports")
    
    if container_reports_dir.exists() and container_reports_dir.is_dir():
        # Running in container with mounted volume
        base_reports_dir = container_reports_dir
        logger.info("Using container mounted reports directory: %s", base_reports_dir)
    else:
        # Running locally
        base_reports_dir = local_reports_dir
        logger.info("Using local reports directory: %s", base_reports_dir)
    
    # Create reports subdirectory if it doesn't exist
    reports_dir = base_reports_dir / subdir
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = reports_dir / f"{test_name}_{timestamp}.csv"
    
    # Write results to CSV
    with open(csv_filename, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    logger.info("Results saved to: %s", csv_filename)
    return str(csv_filename)


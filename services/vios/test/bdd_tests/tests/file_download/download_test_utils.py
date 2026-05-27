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
Shared utilities for file download BDD tests.
"""
import asyncio
import logging
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

import aiohttp
import requests

logger = logging.getLogger(__name__)

# Envoy Lua routes by streamid header against WDM Redis keys (sensor id), not composite stream ids.
_ENVOY_SENSOR_PREFIX_RE = re.compile(
    r"^(test_upload_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def envoy_streamid_route_key(stream_id: Optional[str]) -> str:
    """
    Value for the streamid header (ENVOYROUTEHEADER). Redis stores workloads under the
    sensor id (test_upload_<uuid>); API streamId may append a suffix for extra uploads.
    """
    if not stream_id:
        return ""
    m = _ENVOY_SENSOR_PREFIX_RE.match(stream_id)
    if m:
        return m.group(1)
    return stream_id


def get_with_retry(url, *, params=None, headers=None, timeout=30,
                   verify_ssl=False, retries=3, retry_on=(503, 504)):
    """GET with bounded retry on transient Envoy/proxy errors.

    Used by negative/contract tests where a freshly registered sensor's
    Envoy/Redis route may not be ready when the request is issued. Retries
    use exponential backoff capped at 4 seconds.
    """
    import time as _time

    last = None
    delay = 1
    for attempt in range(retries + 1):
        try:
            r = requests.get(
                url, params=params, headers=headers,
                timeout=timeout, verify=verify_ssl,
            )
            if r.status_code not in retry_on:
                return r
            last = r
            logger.info(
                "Transient %d on %s (attempt %d/%d), retrying in %ds",
                r.status_code, url, attempt + 1, retries + 1, delay,
            )
        except requests.exceptions.RequestException as e:
            last = e
            logger.info(
                "Transient request error on %s: %s (attempt %d/%d)",
                url, e, attempt + 1, retries + 1,
            )
        if attempt < retries:
            _time.sleep(delay)
            delay = min(delay * 2, 4)
    if isinstance(last, requests.Response):
        return last
    raise last


# API Endpoints
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_file': '/vst/api/v1/storage/file/{stream_id}',
    'storage_file_url': '/vst/api/v1/storage/file/{stream_id}/url',
    'sensor_list': '/vst/api/v1/sensor/list',
    'sensor_file_list': '/vst/api/v1/storage/file/{sensor_id}/list',
}


# Recording filenames are epoch-millisecond timestamps of the first frame.
_RECORDING_FILE_RE = re.compile(r"/(\d{10,16})\.[A-Za-z0-9]+$")

# Download response Content-Disposition filename trailer:
#   ..._{startISO}_{endISO}_<numericId>.mp4
# ISO uses underscores in place of `:` and `.`, e.g. 2026-05-06T09_27_34_471Z.
_DOWNLOAD_FILENAME_TIMES_RE = re.compile(
    r"_(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}_\d{3}Z)"
    r"_(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}_\d{3}Z)"
    r"_\d+\.[A-Za-z0-9]+$"
)
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(r"filename\*?=([^;]+)", re.IGNORECASE)


class DownloadContext:
    """Base context to store test data between download test steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.videos: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.url_responses: List[Dict[str, Any]] = []
        self.expiry_results: List[Dict[str, Any]] = []
        self.uploaded_stream_ids: set = set()  # Track uploaded streams for cleanup
        # Sensors created out-of-band (or whose deletion is part of the test
        # itself). The autouse cleanup fixture deletes each to keep tests
        # self-healing regardless of pass/fail.
        self.created_sensor_ids: set = set()
        self.temp_upload_dir: Optional[Path] = None  # Temp dir for test video files


def fetch_streams(api_base_url: str, timeout: int, verify_ssl: bool) -> List[Dict[str, Any]]:
    """Fetch available streams from the API."""
    url = f"{api_base_url}{ENDPOINTS['streams']}"
    
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl
    )
    response.raise_for_status()
    
    streams = response.json()
    logger.info("Fetched %d stream(s)", len(streams))
    return streams


def fetch_timelines(api_base_url: str, timeout: int, verify_ssl: bool) -> Dict[str, Any]:
    """Fetch recording timelines from the storage API."""
    url = f"{api_base_url}{ENDPOINTS['timelines']}"
    
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl
    )
    response.raise_for_status()
    
    timelines = response.json()
    logger.info("Fetched timelines for %d stream(s)", len(timelines))
    return timelines


def select_time_ranges_from_timelines(streams: List[Dict[str, Any]], 
                                      timelines: Dict[str, Any],
                                      video_duration_seconds: int) -> List[Dict[str, Any]]:
    """
    Select valid time ranges from timeline data for testing.
    
    Returns a list of test data with stream_id, start_time, end_time.
    """
    test_data = []
    
    stream_names = []
    for stream_obj in streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                stream_names.append(stream_name)
    
    for stream_name in stream_names:
        timeline_list = timelines.get(stream_name)
        
        if not timeline_list or not isinstance(timeline_list, list) or len(timeline_list) == 0:
            continue
        
        suitable_timeline = None
        for timeline in timeline_list:
            start_time_str = timeline.get('startTime')
            end_time_str = timeline.get('endTime')
            
            if not start_time_str or not end_time_str:
                continue
            
            try:
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                timeline_duration = (end_time - start_time).total_seconds()
                
                if timeline_duration >= video_duration_seconds * 2:
                    suitable_timeline = (start_time, end_time)
                    break
            except (ValueError, AttributeError):
                continue
        
        if not suitable_timeline:
            longest_timeline = None
            longest_duration = 0
            for timeline in timeline_list:
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
            
            if longest_timeline and longest_duration >= video_duration_seconds:
                suitable_timeline = longest_timeline
        
        if suitable_timeline:
            start_time, end_time = suitable_timeline
            
            middle_time = start_time + (end_time - start_time) / 2
            video_end_time = middle_time + timedelta(seconds=video_duration_seconds)
            
            if video_end_time > end_time:
                video_end_time = end_time
            
            test_data.append({
                'stream_id': stream_name,
                'start_time': middle_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                'end_time': video_end_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                'duration': video_duration_seconds
            })
        
        if test_data:
            break
    
    logger.info("Generated %d test case(s)", len(test_data))
    return test_data


async def download_video_async(session: aiohttp.ClientSession, 
                               video_url: str, 
                               index: int,
                               timeout: int = 120,
                               verify_ssl: bool = False,
                               stream_id: Optional[str] = None) -> Dict[str, Any]:
    """Download a video from URL asynchronously. Optional stream_id adds streamid header (Envoy ENVOYROUTEHEADER)."""
    try:
        download_start = time.time()
        time_to_first_byte = None
        route = envoy_streamid_route_key(stream_id) if stream_id else ""
        headers = {"streamid": route} if route else None

        async with session.get(
            video_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=verify_ssl
        ) as response:
            response.raise_for_status()
            
            chunks = []
            async for chunk in response.content.iter_chunked(8192):
                if time_to_first_byte is None:
                    time_to_first_byte = time.time() - download_start
                chunks.append(chunk)
            
            content = b''.join(chunks)
            total_time = time.time() - download_start
            
            return {
                'index': index,
                'content': content,
                'status': response.status,
                'success': True,
                'time_to_first_byte': time_to_first_byte,
                'total_download_time': total_time,
                'error': None
            }
    except Exception as e:
        return {
            'index': index,
            'content': None,
            'status': None,
            'success': False,
            'time_to_first_byte': None,
            'total_download_time': 0,
            'error': str(e)
        }


def create_test_video_file(file_path: Path, duration_seconds: int = 60, fps: int = 30) -> None:
    """
    Create a valid MP4 test file using ffmpeg with H.264 codec.
    
    Args:
        file_path: Path where video file will be created
        duration_seconds: Duration of video (30-120 seconds recommended for download tests)
        fps: Frames per second
    """
    width, height = 640, 480
    
    try:
        ffmpeg_cmd = [
            'ffmpeg',
            '-f', 'lavfi',
            '-i', f'color=c=blue:s={width}x{height}:d={duration_seconds}:r={fps}',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-preset', 'ultrafast',
            '-movflags', '+faststart',
            '-y', str(file_path)
        ]
        
        subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            timeout=max(60, duration_seconds + 30),
            check=True
        )
        
        if not file_path.exists() or file_path.stat().st_size == 0:
            raise RuntimeError("Video file was not created or is empty")
            
        logger.info("Created test video: %s (%d bytes, %ds duration)", 
                   file_path.name, file_path.stat().st_size, duration_seconds)
            
    except FileNotFoundError:
        logger.error("ffmpeg not found - please install ffmpeg")
        raise RuntimeError("ffmpeg is required. Install with: apt-get install ffmpeg")
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg failed: %s", e.stderr.decode() if e.stderr else str(e))
        raise RuntimeError(f"ffmpeg failed to create video: {e}")
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out while creating video")
        raise RuntimeError("ffmpeg timed out")


def upload_test_video(api_base_url: str, file_path: Path, filename: str,
                      sensor_id: str, verify_ssl: bool) -> Dict[str, Any]:
    """
    Upload a test video using PUT API to create a stream for download testing.
    
    Returns upload result with streamId for cleanup.
    """
    url = f"{api_base_url}/vst/api/v1/storage/file/{filename}"
    params = {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'sensorId': sensor_id
    }
    
    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        logger.info("Uploading test video: %s (%d bytes)", filename, len(file_content))
        
        response = requests.put(
            url,
            params=params,
            data=file_content,
            headers={'Content-Type': 'application/octet-stream'},
            timeout=120,
            verify=verify_ssl
        )
        
        response_data = None
        try:
            response_data = response.json()
        except Exception as e:
            logger.warning("Failed to parse upload response as JSON: %s", e)
        
        result = {
            'filename': filename,
            'status_code': response.status_code,
            'success': response.status_code in [200, 201],
            'response_json': response_data,
            'streamId': response_data.get('streamId') if response_data else None,
            'sensorId': response_data.get('sensorId') if response_data else None,
            'error': None
        }
        
        if result['success']:
            logger.info("Upload success - streamId: %s", result['streamId'])
        else:
            logger.warning("Upload failed - status: %d", response.status_code)
        
        return result
        
    except Exception as e:
        logger.error("Upload failed with exception: %s", str(e))
        return {
            'filename': filename,
            'status_code': None,
            'success': False,
            'response_json': None,
            'streamId': None,
            'error': str(e)
        }


def validate_video_with_mediainfo(video_path: Path) -> Dict[str, Any]:
    """
    Validate a video file using mediainfo.
    
    Returns dict with 'valid', 'error', 'summary' keys.
    """
    try:
        result = subprocess.run(
            ['mediainfo', str(video_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        output = result.stdout
        is_valid = (
            result.returncode == 0 and
            'Video' in output and
            ('Format' in output or 'Codec' in output)
        )
        
        summary = []
        if is_valid:
            lines = output.split('\n')
            for line in lines[:20]:
                if any(keyword in line for keyword in ['Format', 'Duration', 'Width', 'Height', 'Frame rate']):
                    summary.append(line.strip())
        
        return {
            'valid': is_valid,
            'error': None if is_valid else 'Invalid video format',
            'summary': ' | '.join(summary[:3]) if summary else ''
        }
        
    except subprocess.TimeoutExpired:
        return {
            'valid': False,
            'error': 'mediainfo timeout',
            'summary': ''
        }
    except FileNotFoundError:
        return {
            'valid': False,
            'error': 'mediainfo not found - install with: apt-get install mediainfo',
            'summary': ''
        }
    except Exception as e:
        return {
            'valid': False,
            'error': str(e),
            'summary': ''
        }


def fetch_rtsp_sensors(api_base_url: str, timeout: int, verify_ssl: bool) -> List[Dict[str, Any]]:
    """Return the subset of sensors with type == 'sensor_rtsp'."""
    url = f"{api_base_url}{ENDPOINTS['sensor_list']}"
    response = requests.get(url, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    sensors = response.json() or []
    rtsp_sensors = [s for s in sensors if s.get('type') == 'sensor_rtsp']
    logger.info("Found %d RTSP sensor(s) out of %d total", len(rtsp_sensors), len(sensors))
    return rtsp_sensors


def fetch_sensor_file_starts_ms(
    api_base_url: str, sensor_id: str, timeout: int, verify_ssl: bool
) -> List[int]:
    """
    Fetch the recorded file list for a sensor and return the first-frame epoch-ms
    timestamps parsed from the filenames, sorted ascending.
    """
    url = f"{api_base_url}{ENDPOINTS['sensor_file_list'].format(sensor_id=sensor_id)}"
    response = requests.get(url, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    data = response.json() or {}

    files = data.get(sensor_id, []) if isinstance(data, dict) else []
    starts: List[int] = []
    for entry in files:
        media_path = entry.get('mediaFilePath') if isinstance(entry, dict) else None
        if not media_path:
            continue
        m = _RECORDING_FILE_RE.search(media_path)
        if m:
            starts.append(int(m.group(1)))
    starts.sort()
    logger.info("Sensor %s has %d recorded file(s)", sensor_id, len(starts))
    return starts


def epoch_ms_to_iso_z(epoch_ms: int) -> str:
    """Convert epoch milliseconds to an ISO-8601 UTC string with millisecond precision."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{epoch_ms % 1000:03d}Z"


def filename_iso_to_epoch_ms(filename_iso: str) -> int:
    """Convert a filename-style ISO timestamp (e.g. '2026-05-06T09_27_34_471Z') to epoch ms."""
    if not filename_iso.endswith('Z'):
        raise ValueError(f"Unexpected filename ISO timestamp: {filename_iso}")
    body = filename_iso[:-1]
    date_part, time_part = body.split('T', 1)
    h, m, s, ms = time_part.split('_')
    iso = f"{date_part}T{h}:{m}:{s}.{ms}+00:00"
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1000 + 0.5)


def parse_download_response_times_ms(content_disposition: str) -> Dict[str, int]:
    """
    Extract start/end epoch-ms from the Content-Disposition filename of a storage download.

    Expected trailer: ..._{startISO}_{endISO}_<id>.<ext>
    """
    if not content_disposition:
        raise ValueError("Missing Content-Disposition header")

    fname_match = _CONTENT_DISPOSITION_FILENAME_RE.search(content_disposition)
    if not fname_match:
        raise ValueError(f"Could not extract filename from: {content_disposition}")
    filename = fname_match.group(1).strip().strip('"')

    times = _DOWNLOAD_FILENAME_TIMES_RE.search(filename)
    if not times:
        raise ValueError(f"Could not parse start/end timestamps from filename: {filename}")

    return {
        'filename': filename,
        'start_ms': filename_iso_to_epoch_ms(times.group(1)),
        'end_ms': filename_iso_to_epoch_ms(times.group(2)),
    }

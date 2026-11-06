"""Stream tracking selected from SURF; see ../SOURCE.md for provenance."""
import asyncio
import json
import time
import logging
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
@dataclass
class TriggerEvent:
    trigger_name: str
    trigger_type: str
    timestamp: float
    data: dict
    metadata: dict

def http_get_sync(url: str, params: Optional[Dict[str, str]] = None, timeout: int = 30) -> Tuple[bool, Any, Optional[str]]:
    """
    Make a synchronous HTTP GET request using urllib.

    Args:
        url: The URL to request
        params: Optional query parameters
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success, response_data, error_message)
    """
    try:
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        req = urllib.request.Request(url, method='GET')
        req.add_header('Accept', 'application/json')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return False, None, f"HTTP {response.status}"

            data = json.loads(response.read().decode('utf-8'))
            return True, data, None

    except urllib.error.HTTPError as e:
        return False, None, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, None, f"URL Error: {str(e.reason)}"
    except json.JSONDecodeError as e:
        return False, None, f"JSON decode error: {str(e)}"
    except Exception as e:
        return False, None, f"Request failed: {str(e)}"


class StreamTracker:
    def __init__(self, vst_url, nvstreamer_url):
        self.name = "alerts-streams"
        self.logger = logging.getLogger(__name__)
        self.vst_url = vst_url.rstrip("/")
        self.nvstreamer_url = nvstreamer_url.rstrip("/")
        self.poll_interval = 5
        self.max_wait = 3600
        self.data_fetch_retry_interval = 30
        self.data_fetch_retry_limit = 120
        self.http_timeout = 30

    def _get_sensor_mapping(self) -> Tuple[bool, Dict[str, Dict[str, Any]], Optional[str]]:
        """Fetch sensor mapping from VST API."""
        url = f"{self.vst_url}/vst/api/v1/sensor/status"
        self.logger.info(f"Fetching sensor status from: {url}")

        success, sensors, error = http_get_sync(url, timeout=self.http_timeout)

        if not success:
            return False, {}, f"Failed to get sensor status: {error}"
        if sensors is None or not isinstance(sensors, dict):
            return False, {}, "Sensor status response was null or not a dict"
        self.logger.info(f"Found {len(sensors)} sensors")
        return True, sensors, None


    def _get_recording_timelines(self) -> Tuple[bool, Dict[str, List[Dict[str, Any]]], Optional[str]]:
        """Fetch recording timelines from VST API."""
        url = f"{self.vst_url}/vst/api/v1/storage/timelines"
        self.logger.info(f"Fetching recording timelines from: {url}")

        success, timelines, error = http_get_sync(url, timeout=self.http_timeout)

        if not success:
            return False, {}, f"Failed to get timelines: {error}"
        if timelines is None or not isinstance(timelines, dict):
            return False, {}, "Timelines response was null or not a dict"
        self.logger.info(f"Got timelines for {len(timelines)} sensors")
        return True, timelines, None


    def _get_video_media_info(self, sensor_name: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """Fetch video media info from nvstreamer API."""
        url = f"{self.nvstreamer_url}/api/v1/storage/file/mediainfo"

        success, media_info, error = http_get_sync(
            url,
            params={"sensorId": sensor_name},
            timeout=self.http_timeout,
        )

        if not success:
            return False, {}, f"Failed to get media info for {sensor_name}: {error}"
        if media_info is None or not isinstance(media_info, dict):
            return False, {}, f"Media info response for {sensor_name} was null or not a dict"
        return True, media_info, None


    def _parse_timestamp(self, timestamp_value: Any) -> Optional[float]:
        """
        Parse a timestamp value to epoch float.

        Handles:
        - Numeric epoch timestamps (int/float or string)
        - ISO 8601 datetime strings (e.g., "2026-01-20T07:30:28.640Z")

        Args:
            timestamp_value: The timestamp to parse

        Returns:
            Epoch timestamp as float, or None if parsing fails
        """
        if timestamp_value is None:
            return None

        # Try numeric conversion first (epoch timestamp)
        try:
            ts = float(timestamp_value)
            return ts
        except (ValueError, TypeError):
            pass

        # Try parsing as ISO 8601 datetime string
        if isinstance(timestamp_value, str):
            try:
                # Handle various ISO 8601 formats
                # Remove 'Z' suffix and replace with +00:00 for parsing
                ts_str = timestamp_value.replace('Z', '+00:00')

                # Try parsing with fromisoformat (Python 3.7+)
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(ts_str)

                # Convert to UTC if not already
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                # Return epoch timestamp in seconds
                return dt.timestamp()
            except (ValueError, TypeError):
                pass

            # Try alternative parsing for formats like "2026-01-20T07:30:28.640Z"
            try:
                from datetime import datetime, timezone
                # Remove milliseconds if present for strptime
                if '.' in timestamp_value:
                    # Parse with milliseconds
                    dt = datetime.strptime(timestamp_value, "%Y-%m-%dT%H:%M:%S.%fZ")
                else:
                    dt = datetime.strptime(timestamp_value, "%Y-%m-%dT%H:%M:%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except (ValueError, TypeError):
                pass

        return None


    def _fetch_all_sensor_data(self) -> Tuple[bool, Dict[str, Dict[str, Any]], Optional[str]]:
        """
        Fetch all sensor data from APIs and calculate end timestamps.

        Returns:
            Tuple of (success, sensor_info_dict, error_message)
        """
        # Step 1: Get sensor mapping
        success, sensors, error = self._get_sensor_mapping()
        if not success:
            return False, {}, error

        if not sensors:
            return False, {}, "No sensors found"

        # Filter to only online sensors
        online_sensors = {
            sid: data for sid, data in sensors.items()
            if data.get("state") == "online"
        }

        if not online_sensors:
            return False, {}, f"No online sensors found (total sensors: {len(sensors)})"

        self.logger.info(f"Found {len(online_sensors)} online sensors out of {len(sensors)} total")

        # Step 2: Get recording timelines
        success, timelines, error = self._get_recording_timelines()
        if not success:
            return False, {}, error

        # Step 3: Calculate end timestamps for each sensor
        sensor_info = {}
        errors = []

        for sensor_id, sensor_data in online_sensors.items():
            sensor_name = sensor_data.get("name", sensor_id)

            # Get timeline start timestamp
            sensor_timeline = timelines.get(sensor_id, [])
            if not sensor_timeline:
                self.logger.warning(f"No timeline found for sensor {sensor_name} ({sensor_id})")
                continue

            latest_timeline = sensor_timeline[-1] if sensor_timeline else None
            if not latest_timeline:
                self.logger.warning(f"Empty timeline for sensor {sensor_name}")
                continue

            start_timestamp_raw = latest_timeline.get("start") or latest_timeline.get("startTime")
            if start_timestamp_raw is None:
                self.logger.warning(f"No start timestamp in timeline for sensor {sensor_name}")
                continue

            # Convert start_timestamp to epoch float
            # Handle both epoch (numeric) and ISO 8601 datetime string formats
            start_timestamp = self._parse_timestamp(start_timestamp_raw)
            if start_timestamp is None:
                self.logger.warning(f"Invalid start timestamp format for sensor {sensor_name}: {start_timestamp_raw}")
                continue

            # Get video duration from nvstreamer (using sensor name)
            success, media_info, error = self._get_video_media_info(sensor_name)
            if not success:
                errors.append(error)
                self.logger.warning(f"Could not get media info for {sensor_name}: {error}")
                continue

            duration_raw = media_info.get("Duration") or media_info.get("duration", 0)
            # Convert duration to float (API may return string)
            try:
                duration = float(duration_raw)
            except (ValueError, TypeError):
                self.logger.warning(f"Invalid duration format for sensor {sensor_name}: {duration_raw}")
                continue

            if duration <= 0:
                self.logger.warning(f"Invalid duration for sensor {sensor_name}: {duration}")
                continue

            # Calculate end timestamp (handle both ms and seconds)
            if start_timestamp > 1e12:  # Likely milliseconds
                end_timestamp = start_timestamp + (duration * 1000)
            else:
                end_timestamp = start_timestamp + duration

            sensor_info[sensor_name] = {
                "sensor_id": sensor_id,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "duration": duration,
            }

            self.logger.info(
                f"Sensor {sensor_name}: start={start_timestamp}, "
                f"duration={duration}s, end={end_timestamp}"
            )

        if not sensor_info:
            return False, {}, f"No sensors with valid streaming data found. Errors: {errors}"

        return True, sensor_info, None


    async def _fetch_sensor_data_with_retries(self, start_time: float) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Fetch sensor data with retries if videos not ready.

        Returns:
            sensor_info dict if successful, None if failed or stopped
        """
        fetch_attempt = 0

        while self._running and not self._stop_requested:
            fetch_attempt += 1
            elapsed = time.time() - start_time

            # Check for overall timeout
            if elapsed > self.max_wait:
                self.logger.error(f"Timed out after {self.max_wait}s waiting for sensor data")
                return None

            self.logger.info(f"[Fetch attempt {fetch_attempt}] Fetching sensor data from APIs...")

            # Run sync HTTP calls in executor to avoid blocking
            loop = asyncio.get_event_loop()
            success, sensor_info, error = await loop.run_in_executor(
                None, self._fetch_all_sensor_data
            )

            if success:
                self.logger.info(f"Successfully fetched data for {len(sensor_info)} sensors")
                return sensor_info

            # Check retry limit (if set)
            if self.data_fetch_retry_limit is not None and fetch_attempt >= self.data_fetch_retry_limit:
                self.logger.error(
                    f"Data fetch retry limit reached ({self.data_fetch_retry_limit} attempts). Last error: {error}"
                )
                return None

            # Check if it's a retryable error
            if "No sensors with valid streaming data" in error or "HTTP 404" in error:
                remaining = self.max_wait - elapsed
                if remaining > self.data_fetch_retry_interval:
                    self.logger.warning(f"[Fetch attempt {fetch_attempt}] {error}")
                    self.logger.info(
                        f"[Fetch attempt {fetch_attempt}] Videos may not be ready yet. "
                        f"Retrying in {self.data_fetch_retry_interval}s... "
                        f"(elapsed: {elapsed:.0f}s, remaining: {remaining:.0f}s)"
                    )
                    await asyncio.sleep(self.data_fetch_retry_interval)
                    continue

            # Non-retryable error
            self.logger.error(f"Failed to fetch sensor data: {error}")
            return None

        return None


    async def run_once(self) -> Optional[TriggerEvent]:
        """
        Run one round of fetch + wait for streaming complete.
        Used by OnStartupTrigger when configured with nvstreamer_config so it
        can pass the same data (job_id, sensor_data, etc.) to actions.

        Returns:
            TriggerEvent with same data shape as when this trigger fires, or None on failure
        """
        self._running = True
        self._stop_requested = False
        start_time = time.time()
        sensor_info = await self._fetch_sensor_data_with_retries(start_time)
        if sensor_info is None:
            return None
        return await self._wait_for_streaming_complete(sensor_info, start_time)


    async def _wait_for_streaming_complete(
        self,
        sensor_info: Dict[str, Dict[str, Any]],
        start_time: float
    ) -> Optional[TriggerEvent]:
        """
        Wait for all sensors to complete streaming.

        Returns:
            TriggerEvent if all complete, None if timeout or stopped
        """
        pending_sensors = dict(sensor_info)
        completed_sensors = {}

        # Determine timestamp format
        first_sensor = next(iter(pending_sensors.values()))
        use_milliseconds = first_sensor["end_timestamp"] > 1e12

        self.logger.info("=" * 60)
        self.logger.info(f"Monitoring {len(pending_sensors)} sensors for streaming completion")
        self.logger.info(f"Timestamp format: {'milliseconds' if use_milliseconds else 'seconds'}")
        self.logger.info("=" * 60)

        iteration = 0
        while pending_sensors and self._running and not self._stop_requested:
            iteration += 1
            current_time = time.time()
            elapsed = current_time - start_time

            # Check for timeout
            if elapsed > self.max_wait:
                self.logger.error(f"Timed out after {self.max_wait}s waiting for streaming")
                return None

            # Get current timestamp
            current_ts = current_time * 1000 if use_milliseconds else current_time

            # Check each pending sensor
            sensors_completed = []
            for sensor_name, info in pending_sensors.items():
                if current_ts >= info["end_timestamp"]:
                    self.logger.info(f"[Iteration {iteration}] Sensor '{sensor_name}' streaming COMPLETE")
                    sensors_completed.append(sensor_name)
                    completed_sensors[sensor_name] = {
                        "sensor_id": info["sensor_id"],
                        "start_timestamp": info["start_timestamp"],
                        "end_timestamp": info["end_timestamp"],
                        "duration": info["duration"],
                        "completed_at": datetime.utcnow().isoformat() + "Z",
                    }

            # Remove completed sensors
            for sensor_name in sensors_completed:
                del pending_sensors[sensor_name]

            # Log progress and wait
            if pending_sensors:
                self.logger.info(
                    f"[Iteration {iteration}] Progress: {len(completed_sensors)}/{len(sensor_info)} complete, "
                    f"{len(pending_sensors)} pending, elapsed: {elapsed:.1f}s"
                )
                self.logger.debug(f"[Iteration {iteration}] Waiting sensors: {list(pending_sensors.keys())}")
                await asyncio.sleep(self.poll_interval)

        if not pending_sensors and completed_sensors:
            # All complete - create trigger event
            total_duration = time.time() - start_time

            return TriggerEvent(
                trigger_name=self.name,
                trigger_type='nvstreamer_one_round_streaming_complete',
                timestamp=time.time(),
                data={
                    "job_id": f"streaming_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "total_sensors": len(sensor_info),
                    "sensor_data": completed_sensors,
                    "total_wait_seconds": total_duration,
                },
                metadata={
                    "vst_url": self.vst_url,
                    "nvstreamer_url": self.nvstreamer_url,
                }
            )

        return None

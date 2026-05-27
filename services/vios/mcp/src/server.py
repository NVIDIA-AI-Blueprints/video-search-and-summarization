# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""MCP Server Gateway implementation."""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import settings
from .cpp_client import CppApiClient, CppApiError

logger = logging.getLogger(__name__)


def _parse_expiry_minutes(expiry_minutes: Optional[str]) -> int:
    """Parse and validate expiry_minutes parameter.
    
    Args:
        expiry_minutes: Optional expiration time in minutes as string containing positive number
        
    Returns:
        Validated expiry in minutes (default: 10080 - 7 days)
    """
    DEFAULT_EXPIRY = 10080  # 7 days (7 * 24 * 60 minutes)
    
    if expiry_minutes is None:
        return DEFAULT_EXPIRY
    
    try:
        expiry_int = int(expiry_minutes)
        return expiry_int if expiry_int > 0 else DEFAULT_EXPIRY
    except (ValueError, TypeError):
        return DEFAULT_EXPIRY


def _handle_api_error(e: Exception, operation: str) -> Dict[str, Any]:
    """Handle API errors and return a standardized error response.
    
    Args:
        e: The exception that occurred
        operation: Description of the operation that failed
        
    Returns:
        Standardized error response dictionary
    """
    if isinstance(e, CppApiError):
        logger.error(f"C++ API error during {operation}: [{e.error_code}] {e.error_message}")
        return {
            "error": f"Failed to {operation}",
            "error_code": e.error_code,
            "error_message": e.error_message,
            "status_code": e.status_code
        }
    else:
        logger.error(f"Unexpected error during {operation}: {e}")
        return {"error": f"Failed to {operation}: {str(e)}"}


class JSONRPCFormatter(logging.Formatter):
    """Custom formatter for JSON RPC messages to make them more readable."""
    
    def format(self, record):
        # Format the base message
        formatted = super().format(record)
        
        # Try to pretty-print JSON content in the message
        try:
            # Look for JSON-like content in the message
            if '{' in record.getMessage() and '}' in record.getMessage():
                # Try to extract and format JSON
                msg = record.getMessage()
                start = msg.find('{')
                end = msg.rfind('}') + 1
                if start >= 0 and end > start:
                    json_part = msg[start:end]
                    try:
                        parsed = json.loads(json_part)
                        pretty_json = json.dumps(parsed, indent=2)
                        formatted = formatted.replace(json_part, f"\n{pretty_json}")
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        
        return formatted

# Create the MCP server instance using FastMCP
transport_security: Optional[TransportSecuritySettings] = None
if settings.allow_all_hosts:
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if "MCP_GATEWAY_ALLOW_ALL_HOSTS" in os.environ:
        logger.warning(
            "MCP transport security disabled (allow_all_hosts=true). "
            "This is insecure; do not expose the service to untrusted networks."
        )

mcp = FastMCP(settings.server_name, transport_security=transport_security)


@mcp.prompt(name="sensors_count", title="Count Sensors", description="Count how many sensors are present")
async def prompt_sensors_count() -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Determine the total number of sensors.\n"
                "- Call the `sensor_list` tool to fetch all sensors.\n"
                "- Count the number of sensor objects in the returned object (use Object.keys().length or equivalent).\n"
                "- Reply with only the number (an integer)."
            ),
        }
    ]


@mcp.prompt(
    name="sensors_recording_status",
    title="Recording Status of Sensors",
    description="Summarize the current recording status across sensors",
)
async def prompt_sensors_recording_status() -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Provide a concise summary of recording status for sensors.\n"
                "Instructions:\n"
                "1) Call `sensor_list` to get sensors (returns object with sensorId as keys). If sensors include a stream identifier (e.g., `streamId`), use it.\n"
                "2) For each sensor with a stream identifier, call `record_stream_status` with that stream id.\n"
                "3) Produce a compact table-like summary: sensor name/id and recording status.\n"
                "4) If a sensor lacks a stream identifier or the call fails, mark its status as 'unknown'."
            ),
        }
    ]


@mcp.prompt(
    name="video_for_sensor",
    title="Get Video for Sensor",
    description="Get a temporary video URL for a sensor between two UTC ISO timestamps",
)
async def prompt_video_for_sensor() -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Get video for a sensor between two times.\n"
                "Parameters you must resolve first: sensor identifier (name or id), start_time (UTC ISO), end_time (UTC ISO).\n"
                "Steps:\n"
                "1) Call `sensor_list` (returns object with sensorId as keys) and resolve the sensor by exact id match or case-insensitive name match. If multiple match, ask to disambiguate.\n"
                "2) Use the resolved sensor's stream identifier (prefer `streamId`; if unavailable, try the sensor id if the backend treats it as stream id).\n"
                "3) Call `get_video_storage_url` with: stream_id, start_time, end_time. Optionally use blocking=false for instant URL return (default is blocking=true which waits for file to be written).\n"
                "4) Return the `videoUrl` from the response, and include filename and expiry info if present. Note: Users can manually add streamable=true or streamable=false to the returned URL to control streaming behavior.\n"
                "5) If the backend cannot find a stream for that sensor, say so clearly."
            ),
        }
    ]


@mcp.prompt(
    name="picture_for_camera",
    title="Get Picture for Camera",
    description="Get a camera picture; if a past time is requested and unsupported, return a live picture",
)
async def prompt_picture_for_camera() -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Get a picture from a camera.\n"
                "Parameters you must resolve: camera identifier (name or id), optional capture_time (UTC ISO).\n"
                "Steps:\n"
                "1) Call `sensor_list` (returns object with sensorId as keys) and resolve the camera by exact id match or case-insensitive name match.\n"
                "2) If a historical capture_time is provided but the backend lacks a time-based picture API, inform the user and proceed with a live snapshot.\n"
                "3) Choose between base64 image data or temporary URL:\n"
                "   - For base64 data: Call `get_live_picture_base64` or `get_replay_picture_base64` with the resolved stream id\n"
                "   - For temporary URL: Call `get_live_picture_url` or `get_replay_picture_url` with the stream id\n"
                "4) Return the image metadata and indicate whether this is a live snapshot or time-specific capture."
            ),
        }
    ]


@mcp.prompt(
    name="picture_url_for_camera",
    title="Get Picture URL for Camera",
    description="Get a temporary URL for accessing a camera picture",
)
async def prompt_picture_url_for_camera() -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Get a temporary URL for accessing a camera picture.\n"
                "Parameters you must resolve: camera identifier (name or id), optional capture_time (UTC ISO), optional expiry_minutes.\n"
                "Steps:\n"
                "1) Call `sensor_list` (returns object with sensorId as keys) and resolve the camera by exact id match or case-insensitive name match.\n"
                "2) Use the resolved sensor's stream identifier (prefer `streamId`; if unavailable, try the sensor id if the backend treats it as stream id).\n"
                "3) If capture_time is provided, call `get_replay_picture_url` with: stream_id, start_time, expiry_minutes.\n"
                "4) If no capture_time is provided, call `get_live_picture_url` with: stream_id, expiry_minutes.\n"
                "5) Return the `imageUrl` from the response, and include filename and expiry info."
            ),
        }
    ]


@mcp.tool()
async def sensor_list() -> Any:
    """Get a list of all sensors from the sensor management API.
    
    Returns detailed information about all sensors in the system including:
    - Sensor ID (UUID)
    - Name and manufacturer
    - Model information
    - IP address and location
    - Current status (online/offline)
        
    Returns:
        Object where each sensor is keyed by its sensorId, containing complete sensor metadata.
        Format: {"sensorId1": {sensor_data}, "sensorId2": {sensor_data}, ...}
    """
    async with CppApiClient() as client:
        try:
            result = await client.get("/api/v1/sensor/list", params={})
            return result
        except Exception as e:
            return _handle_api_error(e, "get sensor list")


@mcp.tool()
async def sensor_status() -> Any:
    """Get the current operational status of all sensors.
    
    Provides real-time status information including connectivity, health,
    and operational state for all sensors in the system. Does not support
    filtering by sensor ID; always returns the status of every sensor.
        
    Returns:
        List of status information for all sensors, including online/offline state,
        health metrics, and any error conditions.
    """
    async with CppApiClient() as client:
        try:
            result = await client.get("/api/v1/sensor/status", params={})
            return result
        except Exception as e:
            return _handle_api_error(e, "get sensor status")


@mcp.tool()
async def get_live_picture_base64(stream_id: str, overlay: str = "") -> Any:
    """Capture a live image from a camera stream.
    
    Retrieves a real-time picture from the specified camera stream.
    The image is returned as base64-encoded data with metadata.
    
    Args:
        stream_id: Required UUID of the camera stream to capture image from
        overlay: Optional JSON string for overlay configuration. Only include this parameter if user specifically requests overlay.
                If user requests overlay without specific settings, use default: '{"bbox":{"showAll":true},"color":"green","thickness":5,"debug":true,"opacity":254}'
                
                Example custom overlay:
                '{"bbox":{"showAll":true,"objectId":[1,2]},"color":"green","thickness":5,"debug":true,"opacity":254,"pose":true}'
                Properties:
                - bbox: Bounding box settings
                  - showAll: boolean - Show all detected objects
                  - objectId: array of integers - Specific object IDs to show
                - color: string - Overlay color (e.g., "green", "red", "blue")
                - thickness: integer - Line thickness for overlays (1-10)
                - debug: boolean - Enable debug mode for additional info
                - opacity: integer - Opacity level (0-255, where 255 is opaque)
                - pose: boolean - Enable pose detection overlays
        
    Returns:
        Dictionary containing:
        - image: Base64-encoded image data with data URI format
        - content_type: MIME type (e.g., 'image/jpeg')
        - size_bytes: Image file size in bytes
        - encoding: Always 'base64'
        - stream_id: The stream ID used for the capture
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            # Make request to the live stream picture endpoint
            endpoint = f"/api/v1/live/stream/{stream_id}/picture"
            params = {}
            if overlay:
                # Handle both string and dictionary inputs for overlay
                try:
                    # Try to parse as JSON first
                    overlay_dict = json.loads(overlay) if isinstance(overlay, str) else overlay
                    params["overlay"] = json.dumps(overlay_dict)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, use as string
                    params["overlay"] = overlay
            result = await client.get_binary(endpoint, params=params)
            
            return {
                "stream_id": stream_id,
                "image": result["data"],  # data:image/jpeg;base64,<base64_content>
                "content_type": result["content_type"],
                "size_bytes": result["size"],
                "encoding": "base64"
            }
        except Exception as e:
            return _handle_api_error(e, f"get live picture for stream {stream_id}")


@mcp.tool()
async def get_replay_picture_base64(stream_id: str, start_time: str, overlay: str = "") -> Any:
    """Capture a replay image from a camera stream at a specific time.
    
    Retrieves a picture from the specified camera stream at a specific timestamp
    from recorded video. The image is returned as base64-encoded data with metadata.
    
    Args:
        stream_id: Required UUID of the camera stream to capture image from
        start_time: Start time in ISO 8601 UTC format (e.g., "2025-08-25T03:05:55.752Z")
        overlay: Optional JSON string for overlay configuration. Only include this parameter if user specifically requests overlay.
                If user requests overlay without specific settings, use default: '{"bbox":{"showAll":true},"color":"green","thickness":5,"debug":true,"opacity":254}'
                
                Example custom overlay:
                '{"bbox":{"showAll":true,"objectId":[1,2]},"color":"green","thickness":5,"debug":true,"opacity":254,"pose":true}'
                Properties:
                - bbox: Bounding box settings
                  - showAll: boolean - Show all detected objects
                  - objectId: array of integers - Specific object IDs to show
                - color: string - Overlay color (e.g., "green", "red", "blue")
                - thickness: integer - Line thickness for overlays (1-10)
                - debug: boolean - Enable debug mode for additional info
                - opacity: integer - Opacity level (0-255, where 255 is opaque)
                - pose: boolean - Enable pose detection overlays
        
    Returns:
        Dictionary containing:
        - image: Base64-encoded image data with data URI format
        - content_type: MIME type (e.g., 'image/jpeg')
        - size_bytes: Image file size in bytes
        - encoding: Always 'base64'
        - stream_id: The stream ID used for the capture
        - start_time: The timestamp used for the capture
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            if not start_time:
                return {"error": "start_time is required"}
            
            # Make request to the replay stream picture endpoint
            endpoint = f"/api/v1/replay/stream/{stream_id}/picture"
            params = {"startTime": start_time}
            if overlay:
                # Handle both string and dictionary inputs for overlay
                try:
                    # Try to parse as JSON first
                    overlay_dict = json.loads(overlay) if isinstance(overlay, str) else overlay
                    params["overlay"] = json.dumps(overlay_dict)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, use as string
                    params["overlay"] = overlay
            result = await client.get_binary(endpoint, params=params)
            
            return {
                "stream_id": stream_id,
                "start_time": start_time,
                "image": result["data"],  # data:image/jpeg;base64,<base64_content>
                "content_type": result["content_type"],
                "size_bytes": result["size"],
                "encoding": "base64"
            }
        except Exception as e:
            return _handle_api_error(e, f"get replay picture for stream {stream_id} at {start_time}")


@mcp.tool()
async def get_live_picture_url(stream_id: str, expiry_minutes: str = "", overlay: str = "") -> Any:
    """Get temporary URL for accessing live picture from a camera stream.
    
    Retrieves a temporary URL that can be used to access a live picture
    from the specified camera stream. This does not download the picture,
    but provides a temporary URL that can be used to access the image file.
    
    Args:
        stream_id: UUID of the camera stream
        expiry_minutes: Optional expiration time in minutes as string containing positive number (default: 10080 - 7 days)
        overlay: Optional JSON string for overlay configuration. Only include this parameter if user specifically requests overlay.
                If user requests overlay without specific settings, use default: '{"bbox":{"showAll":true},"color":"green","thickness":5,"debug":true,"opacity":254}'
                
                Example custom overlay:
                '{"bbox":{"showAll":true,"objectId":[1,2]},"color":"green","thickness":5,"debug":true,"opacity":254,"pose":true}'
                Properties:
                - bbox: Bounding box settings
                  - showAll: boolean - Show all detected objects
                  - objectId: array of integers - Specific object IDs to show
                - color: string - Overlay color (e.g., "green", "red", "blue")
                - thickness: integer - Line thickness for overlays (1-10)
                - debug: boolean - Enable debug mode for additional info
                - opacity: integer - Opacity level (0-255, where 255 is opaque)
                - pose: boolean - Enable pose detection overlays
    
    Returns:
        Dictionary containing:
        - absolutePath: Full file path on server
        - imageUrl: Direct URL to access the image file
        - expiryISO: URL expiration time in ISO format
        - expiryMinutes: Expiry minutes as provided
        - streamId: Stream ID as provided
        - type: Always "live"
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            # Handle type conversion for expiry_minutes - treat empty string as None
            expiry_minutes_value = expiry_minutes if expiry_minutes else None
            expiry_minutes_parsed = _parse_expiry_minutes(expiry_minutes_value)
            
            params = {"expiryMinutes": expiry_minutes_parsed}
            if overlay:
                # Handle both string and dictionary inputs for overlay
                try:
                    # Try to parse as JSON first
                    overlay_dict = json.loads(overlay) if isinstance(overlay, str) else overlay
                    params["overlay"] = json.dumps(overlay_dict)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, use as string
                    params["overlay"] = overlay
            endpoint = f"/api/v1/live/stream/{stream_id}/picture/url"
            result = await client.get(endpoint, params=params)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get live picture URL for stream {stream_id}")


@mcp.tool()
async def get_replay_picture_url(stream_id: str, start_time: str, expiry_minutes: str = "", overlay: str = "") -> Any:
    """Get temporary URL for accessing replay picture from a camera stream at a specific time.
    
    Retrieves a temporary URL that can be used to access a picture from the specified
    camera stream at a specific timestamp from recorded video. This does not download
    the picture, but provides a temporary URL that can be used to access the image file.
    
    Args:
        stream_id: UUID of the camera stream
        start_time: Start time in ISO 8601 UTC format (e.g., "2025-09-15T12:45:52.685Z")
        expiry_minutes: Optional expiration time in minutes as string containing positive number (default: 10080 - 7 days)
        overlay: Optional JSON string for overlay configuration. Only include this parameter if user specifically requests overlay.
                If user requests overlay without specific settings, use default: '{"bbox":{"showAll":true},"color":"green","thickness":5,"debug":true,"opacity":254}'
                
                Example custom overlay:
                '{"bbox":{"showAll":true,"objectId":[1,2]},"color":"green","thickness":5,"debug":true,"opacity":254,"pose":true}'
                Properties:
                - bbox: Bounding box settings
                  - showAll: boolean - Show all detected objects
                  - objectId: array of integers - Specific object IDs to show
                - color: string - Overlay color (e.g., "green", "red", "blue")
                - thickness: integer - Line thickness for overlays (1-10)
                - debug: boolean - Enable debug mode for additional info
                - opacity: integer - Opacity level (0-255, where 255 is opaque)
                - pose: boolean - Enable pose detection overlays
    
    Returns:
        Dictionary containing:
        - absolutePath: Full file path on server
        - imageUrl: Direct URL to access the image file
        - expiryISO: URL expiration time in ISO format
        - expiryMinutes: Expiry minutes as provided
        - streamId: Stream ID as provided
        - type: Always "replay"
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            if not start_time:
                return {"error": "start_time is required"}
            
            # Handle type conversion for expiry_minutes - treat empty string as None
            expiry_minutes_value = expiry_minutes if expiry_minutes else None
            expiry_minutes_parsed = _parse_expiry_minutes(expiry_minutes_value)
            
            params = {
                "startTime": start_time,
                "expiryMinutes": expiry_minutes_parsed
            }
            if overlay:
                # Handle both string and dictionary inputs for overlay
                try:
                    # Try to parse as JSON first
                    overlay_dict = json.loads(overlay) if isinstance(overlay, str) else overlay
                    params["overlay"] = json.dumps(overlay_dict)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, use as string
                    params["overlay"] = overlay
            endpoint = f"/api/v1/replay/stream/{stream_id}/picture/url"
            result = await client.get(endpoint, params=params)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get replay picture URL for stream {stream_id} at {start_time}")


@mcp.tool()
async def sensor_health_check() -> Any:
    """Check the overall health of the sensor management system.
    
    Performs a system-wide health check to verify that the sensor management
    API and backend services are operational and responsive.
    
    Args:
        None
        
    Returns:
        Dictionary with status information or consistent error structure
    """
    async with CppApiClient() as client:
        try:
            is_healthy = await client.health_check()
            status = "healthy" if is_healthy else "unhealthy"
            return {"status": f"Sensor management API status: {status}"}
        except Exception as e:
            return _handle_api_error(e, "check health")


@mcp.tool()
async def record_stream_start(stream_id: str) -> Any:
    """Start recording for a given stream.
    
    Initiates recording for the specified stream ID using the VST Stream Recorder backend. Null response is considered success.
    
    Args:
        stream_id: UUID string of the stream to start recording
    
    Returns:
        Backend response indicating success or error details. Null response is considered success.
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            endpoint = f"/api/v1/record/{stream_id}/start"
            headers = {"streamId": stream_id, "Content-Type": "application/json"}
            result = await client.post(endpoint, data={}, headers=headers)
            return result
        except Exception as e:
            return _handle_api_error(e, f"start recording for stream {stream_id}")

@mcp.tool()
async def record_stream_stop(stream_id: str) -> Any:
    """Stop recording for a given stream.
    
    Stops recording for the specified stream ID using the VST Stream Recorder backend. Null response is considered success.
    
    Args:
        stream_id: UUID string of the stream to stop recording
    
    Returns:
        Backend response indicating success or error details. Null response is considered success.
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            endpoint = f"/api/v1/record/{stream_id}/stop"
            headers = {"streamId": stream_id, "Content-Type": "application/json"}
            result = await client.post(endpoint, data={}, headers=headers)
            return result
        except Exception as e:
            return _handle_api_error(e, f"stop recording for stream {stream_id}")

@mcp.tool()
async def record_stream_timelines(stream_id: str) -> Any:
    """Get recording timelines for a given stream.
    
    Retrieves the timeline of recorded video for the specified stream ID.
    
    Args:
        stream_id: UUID string of the stream to get timelines for
    
    Returns:
        Array of timeline objects or error details
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            endpoint = f"/api/v1/record/{stream_id}/timelines"
            headers = {"streamId": stream_id}
            result = await client.get(endpoint, headers=headers)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get timelines for stream {stream_id}")

@mcp.tool()
async def record_stream_status(stream_id: str) -> Any:
    """Get recording status for a given stream.
    
    Retrieves the current recording state for the specified stream ID.
    
    Args:
        stream_id: UUID string of the stream to get status for
    
    Returns:
        Recording status object or error details
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            
            endpoint = f"/api/v1/record/{stream_id}/status"
            headers = {"streamId": stream_id}
            result = await client.get(endpoint, headers=headers)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get status for stream {stream_id}")

@mcp.tool()
async def sensor_scan() -> Any:
    """Trigger a manual scan for sensors on the network.
    
    Initiates a network scan to discover available sensors. This is a POST operation.
    
    Returns:
        Backend response indicating scan results or error details
    """
    async with CppApiClient() as client:
        try:
            endpoint = "/api/v1/sensor/scan"
            result = await client.post(endpoint, data=None)
            return result
        except Exception as e:
            return _handle_api_error(e, "scan sensors")

@mcp.tool()
async def sensor_status_by_id(sensor_id: str) -> Any:
    """Get the status of a specific sensor by sensor ID.
    
    Retrieves the current status (online/offline, errors, etc.) for the specified sensor.
    
    Args:
        sensor_id: UUID string of the sensor
    
    Returns:
        Status object for the sensor or error details
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/sensor/{sensor_id}/status"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get status for sensor {sensor_id}")

@mcp.tool()
async def sensor_info_by_id(sensor_id: str) -> Any:
    """Get basic information for a specific sensor by sensor ID.
    
    Retrieves information such as IP, location, name, hardware ID, and position for the specified sensor.
    
    Args:
        sensor_id: UUID string of the sensor
    
    Returns:
        Sensor information object or error details
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/sensor/{sensor_id}/info"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get info for sensor {sensor_id}")

@mcp.tool()
async def sensor_network_by_id(sensor_id: str) -> Any:
    """Get network information for a specific sensor by sensor ID.
    
    Retrieves network-related information (DHCP, IP addresses, subnet masks, etc.) for the specified sensor.
    
    Args:
        sensor_id: UUID string of the sensor
    
    Returns:
        Network information object or error details
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/sensor/{sensor_id}/network"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get network info for sensor {sensor_id}")

@mcp.tool()
async def sensor_settings_by_id(sensor_id: str) -> Any:
    """Get image and encoding settings for a specific sensor by sensor ID.
    
    Retrieves image and encode-related settings for the specified sensor.
    
    Args:
        sensor_id: UUID string of the sensor
    
    Returns:
        Settings object (image and encode) or error details
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/sensor/{sensor_id}/settings"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get settings for sensor {sensor_id}")

@mcp.tool()
async def get_video_storage_url(stream_id: str, start_time: str, end_time: str, expiry_minutes: str = "", container: str = "mp4", configuration: str = "", blocking: str = "true") -> Any:
    """Get temporary URL for accessing stored video files.
    
    Retrieves a temporary URL that can be used to access stored video files
    for the specified stream and time range. This does not download the video,
    but provides a temporary URL that can be used to access the video file.
    
    Args:
        start_time: Start time in ISO 8601 UTC format (e.g., "2025-08-25T03:05:55.752Z")
        end_time: End time in ISO 8601 UTC format (e.g., "2025-08-25T03:06:15.752Z")
        expiry_minutes: Optional expiration time in minutes as string containing positive number (default: 10080 - 7 days)
        container: Video container format (default: "mp4")
        blocking: Optional boolean string (default: "true"). If "true", the API call blocks until the video file is completely written to disk before returning the URL. If "false", an async task is created in the backend and the video URL is returned instantly.
        configuration: Optional JSON string for advanced video and overlay configuration. Only include this parameter if user specifically requests configuration or overlay.
                      If user requests overlay without specific settings, use default overlay: '{"overlay":{"bbox":{"showAll":true},"color":"green","thickness":5,"debug":true,"opacity":254}}'
                      
                      Example custom configuration:
                      '{"container":"mp4","overlay":{"bbox":{"showAll":true,"objectId":[1,2]},"color":"green","thickness":5,"debug":true,"opacity":254,"pose":true}}'
                      Properties:
                      - container: Video container format override
                      - overlay: Overlay configuration
                        - bbox: Bounding box settings
                          - showAll: boolean - Show all detected objects
                          - objectId: array of integers - Specific object IDs to show
                        - color: string - Overlay color (e.g., "green", "red", "blue")
                        - thickness: integer - Line thickness for overlays (1-10)
                        - debug: boolean - Enable debug mode for additional info
                        - opacity: integer - Opacity level (0-255, where 255 is opaque)
                        - pose: boolean - Enable pose detection overlays
    
    Returns:
        Dictionary containing:
        - absolutePath: Full file path on server
        - videoUrl: Direct URL to access the video file. Users can manually add streamable=true or streamable=false query parameter to this URL to control streaming behavior. If streamable=true, the video can be streamed even if the file has not been completely written to disk. If streamable=false (default), the request will block until the file is written to disk. Note: streamable parameter is NOT included in the returned URL and must be added manually by users.
        - expiryISO: URL expiration time in ISO format
        - expiryMinutes: Expiry minutes as provided
        - streamId: Stream ID as provided
        - type: Always "replay"
    """
    async with CppApiClient() as client:
        try:
            if not stream_id:
                return {"error": "stream_id is required"}
            if not start_time:
                return {"error": "start_time is required"}
            if not end_time:
                return {"error": "end_time is required"}
            
            # Handle type conversion for expiry_minutes - treat empty string as None
            expiry_minutes_value = expiry_minutes if expiry_minutes else None
            expiry_minutes_parsed = _parse_expiry_minutes(expiry_minutes_value)
            
            params = {
                "startTime": start_time,
                "endTime": end_time,
                "expiryMinutes": expiry_minutes_parsed,
                "container": container,
                "blocking": blocking,
            }
            
            if configuration:
                # Handle both string and dictionary inputs for configuration
                try:
                    # Try to parse as JSON first
                    config_dict = json.loads(configuration) if isinstance(configuration, str) else configuration
                    params["configuration"] = json.dumps(config_dict)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, use as string
                    params["configuration"] = configuration
            
            endpoint = f"/api/v1/storage/file/{stream_id}/url"
            result = await client.get(endpoint, params=params)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get storage file URL for stream {stream_id}")


@mcp.tool()
async def storage_file_list() -> Any:
    """Get list of all media files.
    
    Returns a list of all media files linked to every sensor in the system.
    
    Returns:
        Dictionary containing sensor IDs mapped to their associated media files,
        including file paths, metadata, and file information.
    """
    async with CppApiClient() as client:
        try:
            endpoint = "/api/v1/storage/file/list"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, "get storage file list")


@mcp.tool()
async def storage_file_path(file_id: str, include_metadata: bool = False) -> Any:
    """Get media file path by unique ID.
    
    Retrieves the file path and optionally metadata for a specific file using its unique identifier.
    
    Args:
        file_id: Unique identifier for the stored file
        include_metadata: Whether to include metadata in response (default: False)
    
    Returns:
        Dictionary containing:
        - id: Unique identifier for the file
        - mediaFilePath: Full path to the media file
        - metadataFilePath: Path to the metadata file (if exists)
        - metadata: File metadata (if include_metadata is True)
    """
    async with CppApiClient() as client:
        try:
            if not file_id:
                return {"error": "file_id is required"}
            
            endpoint = "/api/v1/storage/file/path"
            params = {"id": file_id, "mediainfo": include_metadata}
            result = await client.get(endpoint, params=params)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get file path for {file_id}")


@mcp.tool()
async def storage_file_list_by_sensor(sensor_id: str) -> Any:
    """Get list of media files by sensor ID.
    
    Returns a list of all media files linked to the specified sensor.
    
    Args:
        sensor_id: Unique identifier for the sensor
    
    Returns:
        Dictionary containing the sensor ID mapped to its associated media files,
        including file paths, metadata, and file information.
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/storage/file/{sensor_id}/list"
            result = await client.get(endpoint)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get file list for sensor {sensor_id}")


@mcp.tool()
async def storage_file_path_by_sensor(sensor_id: str, start_time: str = "", end_time: str = "", include_metadata: bool = False) -> Any:
    """Get media file paths by sensor ID.
    
    Retrieves file paths for media files associated with a specific sensor,
    optionally filtered by time range and including metadata.
    
    Args:
        sensor_id: Unique identifier for the sensor
        start_time: Optional start time in ISO 8601 UTC format (e.g., "2025-07-09T00:00:00.000Z")
        end_time: Optional end time in ISO 8601 UTC format (e.g., "2025-07-09T23:59:59.999Z")
        include_metadata: Whether to include metadata in response (default: False)
    
    Returns:
        Array of file path objects containing:
        - id: Unique identifier for each file
        - mediaFilePath: Full path to the media file
        - metadataFilePath: Path to the metadata file
        - metadata: File metadata (if include_metadata is True)
    """
    async with CppApiClient() as client:
        try:
            if not sensor_id:
                return {"error": "sensor_id is required"}
            
            endpoint = f"/api/v1/storage/file/{sensor_id}/path"
            params = {"mediainfo": include_metadata}
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time
            
            result = await client.get(endpoint, params=params)
            return result
        except Exception as e:
            return _handle_api_error(e, f"get file paths for sensor {sensor_id}")


@mcp.tool()
async def storage_file_upload(media_file_path: str, metadata_file_path: str = "", event_info: str = "", timestamp: int = 0, stream_name: str = "", sensor_id: str = "", tag: str = "", checksum: str = "") -> Any:
    """Upload or register media file.
    
    Registers an existing media file on the server by providing file paths and metadata.
    This is the pass-through mode for file registration without chunked upload.
    
    Args:
        media_file_path: Full path to existing media file on server filesystem (required)
        metadata_file_path: Path to existing JSON metadata file on server filesystem (optional)
        event_info: Description of the event that triggered the recording (optional)
        timestamp: Unix timestamp in milliseconds when the event occurred (optional)
        stream_name: Name identifier for the stream (optional)
        sensor_id: Unique identifier of the sensor that captured the media (optional)
        tag: Tag or category for the media file (optional)
        checksum: Checksum or hash of the media file for integrity verification (optional)
    
    Returns:
        Dictionary containing:
        - id: Unique identifier for the uploaded or registered file
        - bytes: Size of the file in bytes
        - created_at: Timestamp when the file was registered
        - fileName: Name of the registered file
        - sensorId: Sensor ID that captured the media
        - filePath: Path where the file is stored
        - streamId: Stream identifier (for backward compatibility)
    """
    async with CppApiClient() as client:
        try:
            if not media_file_path:
                return {"error": "media_file_path is required"}
            
            endpoint = "/api/v1/storage/file"
            
            # Prepare form data
            form_data = {"mediaFilePath": media_file_path}
            
            if metadata_file_path:
                form_data["metadataFilePath"] = metadata_file_path
            
            # Build metadata object from individual parameters
            metadata = {}
            if event_info:
                metadata["eventInfo"] = event_info
            if timestamp > 0:
                metadata["timestamp"] = timestamp
            if stream_name:
                metadata["streamName"] = stream_name
            if sensor_id:
                metadata["sensorId"] = sensor_id
            if tag:
                metadata["tag"] = tag
            if checksum:
                metadata["checksum"] = checksum
            
            if metadata:
                form_data["metadata"] = json.dumps(metadata)
            
            # Use multipart/form-data for this endpoint
            headers = {"Content-Type": "multipart/form-data"}
            result = await client.post(endpoint, data=form_data, headers=headers)
            return result
        except Exception as e:
            return _handle_api_error(e, f"upload/register file {media_file_path}")


def setup_jsonrpc_logging():
    """Set up enhanced JSON RPC logging with custom formatting."""
    # Create a dedicated handler for JSON RPC messages
    jsonrpc_handler = logging.StreamHandler(sys.stderr)
    jsonrpc_handler.setLevel(logging.DEBUG)
    
    # Use custom formatter for better JSON readability
    jsonrpc_formatter = JSONRPCFormatter(
        "%(asctime)s - JSON RPC - %(name)s - %(levelname)s - %(message)s"
    )
    jsonrpc_handler.setFormatter(jsonrpc_formatter)
    
    # Add the handler to MCP-related loggers
    mcp_loggers = [
        "mcp",
        "mcp.server", 
        "mcp.server.fastmcp",
        "mcp.protocol",
        "mcp.transport",
        "mcp.client",
        "fastmcp"  # In case using different MCP library
    ]
    
    for logger_name in mcp_loggers:
        mcp_logger = logging.getLogger(logger_name)
        mcp_logger.addHandler(jsonrpc_handler)
        mcp_logger.setLevel(logging.DEBUG)
        # Prevent duplicate messages in root logger
        mcp_logger.propagate = False

def run_server():
    """Run the MCP server using stdio transport (for Cursor integration)."""
    # Configure logging
    log_level = getattr(logging, settings.log_level)
    
    # If JSON RPC logging is enabled, set to DEBUG for MCP-related loggers
    if settings.enable_jsonrpc_logging:
        log_level = logging.DEBUG
    
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Enable detailed logging for MCP-related modules when JSON RPC logging is enabled
    if settings.enable_jsonrpc_logging:
        setup_jsonrpc_logging()
        logger.info("JSON RPC message logging enabled - MCP protocol messages will be logged with detailed formatting")
    
    logger.info(f"Starting MCP Gateway Server '{settings.server_name}' v{settings.server_version}")
    logger.info(f"C++ API Base URL: {settings.cpp_api_base_url}")
    logger.info("Using stdio transport for Cursor integration")
    
    # Run the server using FastMCP (which handles stdio automatically)
    mcp.run()


def run_http_server(host: Optional[str] = None, port: Optional[int] = None):
    """Run the MCP server using HTTP transport (for remote connections).
    
    The MCP endpoint will be available at /mcp (without trailing slash).
    """
    # Use configuration defaults if not provided
    if host is None:
        host = settings.server_host
    if port is None:
        port = settings.server_port
        
    # Configure logging
    log_level = getattr(logging, settings.log_level)
    
    # If JSON RPC logging is enabled, set to DEBUG for MCP-related loggers
    if settings.enable_jsonrpc_logging:
        log_level = logging.DEBUG
    
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Enable detailed logging for MCP-related modules when JSON RPC logging is enabled
    if settings.enable_jsonrpc_logging:
        setup_jsonrpc_logging()
        logger.info("JSON RPC message logging enabled - MCP protocol messages will be logged with detailed formatting")
    
    logger.info(f"Starting MCP Gateway Server '{settings.server_name}' v{settings.server_version}")
    logger.info(f"C++ API Base URL: {settings.cpp_api_base_url}")
    # Apply host/port to FastMCP settings
    mcp.settings.host = host
    mcp.settings.port = port

    logger.info(f"Using HTTP transport on {host}:{port}")
    logger.info(f"MCP endpoint will be available at: http://{host}:{port}/mcp (no trailing slash)")
    
    # Run the server using FastMCP with Streamable HTTP transport
    mcp.run(transport="streamable-http") 
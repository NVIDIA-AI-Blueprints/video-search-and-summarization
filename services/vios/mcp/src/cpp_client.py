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

"""REST API client for communicating with the C++ application."""

import httpx
import asyncio
import base64
from typing import Dict, Any, Optional, List
import logging
from .config import settings

logger = logging.getLogger(__name__)


class CppApiError(Exception):
    """Custom exception for C++ API errors."""
    
    def __init__(self, error_code: str, error_message: str, status_code: Optional[int] = None):
        """Initialize the C++ API error.
        
        Args:
            error_code: The error code from the C++ server
            error_message: The error message from the C++ server
            status_code: HTTP status code if available
        """
        self.error_code = error_code
        self.error_message = error_message
        self.status_code = status_code
        super().__init__(f"[{error_code}] {error_message}")


class CppApiClient:
    """Async HTTP client for communicating with the C++ application."""
    
    def __init__(self):
        """Initialize the C++ API client."""
        self.base_url = settings.cpp_api_base_url
        self.timeout = settings.cpp_api_timeout
        self.client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()
    
    def _handle_error_response(self, response: httpx.Response) -> None:
        """Handle error responses from the C++ server.
        
        Args:
            response: The HTTP response object
            
        Raises:
            CppApiError: If the response contains a C++ server error
        """
        try:
            # Try to parse JSON response for C++ server error format
            if response.content.strip():
                error_data = response.json()
                if isinstance(error_data, dict) and "error_code" in error_data and "error_message" in error_data:
                    raise CppApiError(
                        error_code=error_data["error_code"],
                        error_message=error_data["error_message"],
                        status_code=response.status_code
                    )
        except (ValueError, TypeError):
            # If JSON parsing fails, fall back to raising the original HTTP error
            pass
        
        # If no structured error found, raise the original HTTP error
        response.raise_for_status()
    
    async def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a GET request to the C++ application."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        try:
            # Force refresh for sensor list endpoint (enabled by default)
            if settings.sensor_list_force_refresh and endpoint == "/api/v1/sensor/list":
                params = params.copy() if params else {}
                params["forceRefresh"] = "true"
            
            # Disable audio for video URL endpoint (enabled by default)
            if settings.video_url_disable_audio and endpoint.startswith("/api/v1/storage/file/") and endpoint.endswith("/url"):
                params = params.copy() if params else {}
                params["disableAudio"] = "true"
            
            logger.info(f"Making GET request to {endpoint} with params: {params} and headers: {headers}")
            if headers:
                response = await self.client.get(endpoint, params=params, headers=headers)
            else:
                response = await self.client.get(endpoint, params=params)
            
            # Handle error responses first
            if not response.is_success:
                self._handle_error_response(response)
            
            # Handle null content with 200 status
            if response.status_code == 200 and not response.content.strip():
                return {"status": "success"}
            
            result = response.json()
            
            # Transform array responses to object format for sensor list endpoint
            # to prevent FastMCP from treating them as display content
            logger.info(f"Response type: {type(result)}, endpoint: {endpoint}")
            if isinstance(result, list) and endpoint == "/api/v1/sensor/list":
                logger.info(f"Transforming sensor list array to object format, array length: {len(result)}")
                # Convert array of sensor objects to object of objects keyed by sensorId
                sensor_objects = {}
                for sensor in result:
                    if isinstance(sensor, dict) and "sensorId" in sensor:
                        sensor_id = sensor["sensorId"]
                        sensor_objects[sensor_id] = sensor
                        logger.info(f"Added sensor: {sensor_id}")
                logger.info(f"Final transformed object has {len(sensor_objects)} sensors")
                return sensor_objects
            
            return result
        except CppApiError:
            # Re-raise C++ API errors as-is
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            raise
    
    async def get_binary(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a GET request that returns binary data (e.g., images)."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        try:
            logger.info(f"Making GET request for binary data to {endpoint} with params: {params}")
            response = await self.client.get(endpoint, params=params)
            
            # Handle error responses first
            if not response.is_success:
                self._handle_error_response(response)
            
            # Get the content type from headers
            content_type = response.headers.get("content-type", "application/octet-stream")
            
            # Get binary content and encode as base64
            binary_content = response.content
            base64_content = base64.b64encode(binary_content).decode('utf-8')
            
            return {
                "data": f"data:{content_type};base64,{base64_content}",
                "content_type": content_type,
                "size": len(binary_content),
                "base64": base64_content
            }
        except CppApiError:
            # Re-raise C++ API errors as-is
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            raise

    async def post(self, endpoint: str, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a POST request to the C++ application."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        try:
            logger.info(f"Making POST request to {endpoint} with data: {data} and headers: {headers}")
            
            # Check if this is a multipart/form-data request
            if headers and headers.get("Content-Type") == "multipart/form-data":
                # Remove Content-Type header to let httpx set it automatically with boundary
                request_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
                response = await self.client.post(endpoint, data=data, headers=request_headers)
            elif headers:
                response = await self.client.post(endpoint, json=data, headers=headers)
            else:
                response = await self.client.post(endpoint, json=data)
            
            # Handle error responses first
            if not response.is_success:
                self._handle_error_response(response)
            
            # Handle null content with 200 status
            if response.status_code == 200 and not response.content.strip():
                return {"status": "success"}
            
            return response.json()
        except CppApiError:
            # Re-raise C++ API errors as-is
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            raise
    
    async def put(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a PUT request to the C++ application."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        try:
            logger.info(f"Making PUT request to {endpoint} with data: {data}")
            response = await self.client.put(endpoint, json=data)
            
            # Handle error responses first
            if not response.is_success:
                self._handle_error_response(response)
            
            # Handle null content with 200 status
            if response.status_code == 200 and not response.content.strip():
                return {"status": "success"}
            
            return response.json()
        except CppApiError:
            # Re-raise C++ API errors as-is
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            raise
    
    async def delete(self, endpoint: str) -> Dict[str, Any]:
        """Make a DELETE request to the C++ application."""
        if not self.client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        try:
            logger.info(f"Making DELETE request to {endpoint}")
            response = await self.client.delete(endpoint)
            
            # Handle error responses first
            if not response.is_success:
                self._handle_error_response(response)
            
            # Handle null content with 200 status
            if response.status_code == 200 and not response.content.strip():
                return {"status": "success"}
            
            return response.json()
        except CppApiError:
            # Re-raise C++ API errors as-is
            raise
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            raise
    
    async def health_check(self) -> bool:
        """Check if the C++ application is healthy."""
        try:
            response = await self.get("/api/v1/sensor/version")
            return response.get("status") == "healthy"
        except CppApiError as e:
            logger.warning(f"Health check failed with C++ API error: [{e.error_code}] {e.error_message}")
            return False
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False 
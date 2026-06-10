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

"""Configuration settings for the MCP Gateway."""

from pydantic import Field
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    # C++ Application API Configuration
    cpp_api_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL for the C++ application API"
    )
    cpp_api_timeout: int = Field(
        default=30,
        description="Timeout for C++ API calls in seconds"
    )
    
    # MCP Server Configuration
    server_name: str = Field(
        default="cpp-gateway",
        description="Name of the MCP server"
    )
    server_version: str = Field(
        default="1.0.0",
        description="Version of the MCP server"
    )
    server_host: str = Field(
        default="0.0.0.0",
        description="Host address for the MCP server"
    )
    server_port: int = Field(
        default=8000,
        description="Port for the MCP server"
    )

    allow_all_hosts: bool = Field(
        default=True,
        description="Disable MCP DNS rebinding/Host header protection (INSECURE)"
    )
    
    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )
    enable_jsonrpc_logging: bool = Field(
        default=False,
        description="Enable detailed JSON RPC message logging"
    )
    
    # API Behavior Configuration
    sensor_list_force_refresh: bool = Field(
        default=True,
        description="Always add forceRefresh=true to sensor list API calls"
    )
    video_url_disable_audio: bool = Field(
        default=True,
        description="Always add disableAudio=true to video URL API calls"
    )
    
    class Config:
        env_file = ".env"
        env_prefix = "MCP_GATEWAY_"


# Global settings instance
settings = Settings() 
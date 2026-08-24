# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict

from .schemas import HeartbeatRequest, HeartbeatConfig
from its_redis.redis_handler import RedisHandler  # Use absolute import
from ..core.dependencies import get_redis_handler

router = APIRouter(prefix="/sampling")

@router.post("/config")
async def create_sampling_config(
    request: HeartbeatRequest,
    redis_handler: RedisHandler = Depends(get_redis_handler)
):
    """Create or update a sampling configuration."""
    try:
        redis_handler.update_heartbeat_config(request.name, request.config.dict())
        return {"message": "Configuration updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/config")
async def get_sampling_configs(
    redis_handler: RedisHandler = Depends(get_redis_handler)
) -> Dict[str, HeartbeatConfig]:
    """Get all sampling configurations."""
    try:
        return redis_handler.get_heartbeat_configs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/config/{sensor_name}")
async def delete_sampling_config(
    sensor_name: str,
    redis_handler: RedisHandler = Depends(get_redis_handler)
):
    """Delete a sampling configuration."""
    try:
        if redis_handler.delete_heartbeat_config(sensor_name):
            return {"message": f"Configuration for sensor {sensor_name} deleted"}
        raise HTTPException(status_code=404, detail=f"No configuration found for sensor {sensor_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
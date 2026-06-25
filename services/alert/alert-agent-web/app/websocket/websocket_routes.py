#!/usr/bin/env python3
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

"""
WebSocket Routes for Alert Broadcasting

Handles WebSocket connections for real-time alert notifications.
"""

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from .connection_manager import connection_manager
from .websocket_service import websocket_service
import json
from datetime import datetime


# Create WebSocket router
router = APIRouter()

# Configure logging
logger = logging.getLogger(__name__)


@router.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time alert notifications.
    
    Clients connect to this endpoint to receive real-time alerts including:
    - Original alerts as they arrive
    - Enhanced/verified alerts after processing
    
    Args:
        websocket: WebSocket connection from client
    """
    # Check if WebSocket service is enabled before accepting connection
    service_status = websocket_service.get_status()
    if not service_status.get("enabled", True):
        # Accept connection to send error message, then close
        await websocket.accept()
        
        # Send error message explaining why service is unavailable
        error_message = {
            "type": "service_disabled",
            "error": "WebSocket service is currently disabled",
            "message": "Alert broadcasting is not available. Please check service configuration.",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        await websocket.send_text(json.dumps(error_message))
        
        # Close connection with appropriate code
        await websocket.close(code=1013, reason="WebSocket service disabled")
        logger.info("Rejected WebSocket connection - service disabled", extra={
            "client_info": f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        })
        return
    
    await connection_manager.connect(websocket)
    
    try:
        # Keep connection alive and handle client messages
        while True:
            try:
                # Wait for messages from client (e.g., ping, subscription preferences)
                data = await websocket.receive_text()
                
                # Parse client message
                try:
                    client_message = json.loads(data)
                    await handle_client_message(websocket, client_message)
                except json.JSONDecodeError:
                    # Handle non-JSON messages
                    logger.debug(f"Received non-JSON message from client: {data}")
                    
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                break
                
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
    finally:
        connection_manager.disconnect(websocket)


async def handle_client_message(websocket: WebSocket, message: dict):
    """
    Handle messages received from WebSocket clients.
    
    Args:
        websocket: WebSocket connection
        message: Parsed JSON message from client
    """
    message_type = message.get("type", "unknown")
    
    if message_type == "ping":
        # Respond to ping with pong
        pong_response = {
            "type": "pong",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        await websocket.send_text(json.dumps(pong_response))
        
    elif message_type == "get_status":
        # Send connection status
        status_response = {
            "type": "status",
            "total_connections": connection_manager.get_connection_count(),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        await websocket.send_text(json.dumps(status_response))
        
    else:
        logger.debug(f"Unknown message type from client: {message_type}")


# Health check endpoint for WebSocket status
@router.get("/ws/health")
async def websocket_health():
    """
    Health check endpoint for WebSocket service.
    
    Returns:
        WebSocket service health status and connection count
    """
    service_status = websocket_service.get_status()
    
    return {
        "status": "healthy" if service_status["is_running"] else "degraded",
        "service": "websocket",
        "active_connections": service_status["active_connections"],
        "redis_consumer": service_status["redis_consumer"],
        "timestamp": datetime.utcnow().isoformat() + "Z"
    } 
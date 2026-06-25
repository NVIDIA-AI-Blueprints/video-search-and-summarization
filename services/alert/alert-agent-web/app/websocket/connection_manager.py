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
WebSocket Connection Manager

Manages WebSocket client connections and broadcasting for Alert Agent.
"""

import logging
from typing import List, Set
from fastapi import WebSocket, WebSocketDisconnect
import json
import asyncio


class WebSocketConnectionManager:
    """
    Manages WebSocket connections and broadcasting for alert notifications.
    """
    
    def __init__(self):
        """Initialize the connection manager."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.active_connections: Set[WebSocket] = set()
        
    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection.
        
        Args:
            websocket: WebSocket connection to accept
        """
        await websocket.accept()
        self.active_connections.add(websocket)
        
        self.logger.info("New WebSocket connection established", extra={
            "total_connections": len(self.active_connections),
            "client_info": f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        })
        
    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection.
        
        Args:
            websocket: WebSocket connection to remove
        """
        self.active_connections.discard(websocket)
        
        self.logger.info("WebSocket connection closed", extra={
            "total_connections": len(self.active_connections),
            "client_info": f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        })
        
    async def broadcast_message(self, message: dict) -> None:
        """
        Broadcast a message to all connected clients.
        
        Args:
            message: Message dictionary to broadcast
        """
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return
            
        # Convert message to JSON string
        try:
            message_json = json.dumps(message)
        except (TypeError, ValueError) as e:
            self.logger.error(f"Failed to serialize message for broadcast: {e}")
            return
            
        # Track failed connections for cleanup
        failed_connections = set()
        
        # Broadcast to all connections
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(message_json)
            except Exception as e:
                self.logger.warning(f"Failed to send message to WebSocket client: {e}")
                failed_connections.add(connection)
                
        # Clean up failed connections
        for failed_connection in failed_connections:
            self.disconnect(failed_connection)
            
        self.logger.debug("Message broadcasted", extra={
            "successful_sends": len(self.active_connections) - len(failed_connections),
            "failed_sends": len(failed_connections),
            "total_connections": len(self.active_connections)
        })
        
    async def broadcast_text(self, text: str) -> None:
        """
        Broadcast a text message to all connected clients.
        
        Args:
            text: Text message to broadcast
        """
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return
            
        # Track failed connections for cleanup
        failed_connections = set()
        
        # Broadcast to all connections
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(text)
            except Exception as e:
                self.logger.warning(f"Failed to send text to WebSocket client: {e}")
                failed_connections.add(connection)
                
        # Clean up failed connections
        for failed_connection in failed_connections:
            self.disconnect(failed_connection)
            
        self.logger.debug("Text broadcasted", extra={
            "successful_sends": len(self.active_connections) - len(failed_connections),
            "failed_sends": len(failed_connections),
            "total_connections": len(self.active_connections)
        })
        
    def get_connection_count(self) -> int:
        """
        Get the number of active connections.
        
        Returns:
            Number of active WebSocket connections
        """
        return len(self.active_connections)
        
    async def send_ping_to_all(self) -> None:
        """
        Send ping to all connections to check health.
        """
        if not self.active_connections:
            return
            
        failed_connections = set()
        
        for connection in self.active_connections.copy():
            try:
                await connection.ping()
            except Exception as e:
                self.logger.warning(f"Failed to ping WebSocket client: {e}")
                failed_connections.add(connection)
                
        # Clean up failed connections
        for failed_connection in failed_connections:
            self.disconnect(failed_connection)


# Global connection manager instance
connection_manager = WebSocketConnectionManager() 
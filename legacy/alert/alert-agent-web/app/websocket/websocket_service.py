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
WebSocket Service

Main service that integrates Redis stream consumption with WebSocket broadcasting.
Runs as a background task in the FastAPI application.
"""

import asyncio
import logging
import yaml
from typing import Dict, Any, Optional
from .redis_consumer import RedisStreamConsumer
from .connection_manager import connection_manager


class WebSocketService:
    """
    Main WebSocket service that coordinates Redis stream consumption 
    and WebSocket broadcasting.
    """
    
    def __init__(self, config_file: str = "config.yaml"):
        """
        Initialize WebSocket service.
        
        Args:
            config_file: Path to configuration file
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_file = config_file
        
        # Load configuration
        self.config = self._load_config()
        
        # Initialize Redis consumer
        redis_config = self._get_redis_config()
        self.redis_consumer = RedisStreamConsumer(redis_config)
        
        # Service state
        self.is_running = False
        self._background_task: Optional[asyncio.Task] = None
        
        self.logger.info("WebSocket service initialized", extra={
            "config_file": config_file,
            "redis_host": redis_config.get('host'),
            "redis_port": redis_config.get('port')
        })
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Configuration dictionary
        """
        try:
            with open(self.config_file, 'r') as file:
                return yaml.safe_load(file)
        except Exception as e:
            self.logger.error(f"Failed to load config from {self.config_file}: {e}")
            # Return default config
            return {
                'event_bridge': {
                    'redis_source': {
                        'host': 'localhost',
                        'port': 6379,
                        'db': 0
                    }
                }
            }
    
    def _get_redis_config(self) -> Dict[str, Any]:
        """
        Extract Redis configuration for the consumer.
        
        Returns:
            Complete Redis configuration dictionary
        """
        event_bridge = self.config.get('event_bridge', {})
        redis_source = event_bridge.get('redis_source', {})
        redis_sink = event_bridge.get('redis_sink', {})
        
        # Get WebSocket-specific configuration
        websocket_config = self.config.get('websocket', {})
        websocket_redis_config = websocket_config.get('redis_consumer', {})
        
        # Merge configurations with WebSocket overrides
        redis_config = {
            'host': redis_source.get('host', 'localhost'),
            'port': redis_source.get('port', 6379),
            'db': redis_source.get('db', 0),
            'streams': redis_source.get('streams', {}),
            'sink_streams': redis_sink.get('streams', {}),
            'consumer_config': {
                'block_time': websocket_redis_config.get('block_time', 1000),
                'count': websocket_redis_config.get('count', 1),
                'reconnect_delay': websocket_redis_config.get('reconnect_delay', 5)
            },
            'consumer_group_prefix': websocket_config.get('consumer_group_prefix', 'websocket_instance')
        }
        
        return redis_config
    
    async def start(self) -> None:
        """
        Start the WebSocket service.
        
        This starts the Redis stream consumer as a background task.
        """
        # Check if WebSocket service is enabled in configuration
        websocket_config = self.config.get('websocket', {})
        if not websocket_config.get('enabled', True):
            self.logger.info("WebSocket service is disabled in configuration")
            return
        
        if self.is_running:
            self.logger.warning("WebSocket service is already running")
            return
        
        self.logger.info("Starting WebSocket service")
        
        try:
            # Start Redis consumer as background task
            self._background_task = asyncio.create_task(
                self._run_consumer()
            )
            
            self.is_running = True
            self.logger.info("WebSocket service started successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to start WebSocket service: {e}")
            raise
    
    async def stop(self) -> None:
        """
        Stop the WebSocket service.
        """
        if not self.is_running:
            return
        
        self.logger.info("Stopping WebSocket service")
        
        try:
            # Stop Redis consumer
            await self.redis_consumer.stop()
            
            # Cancel background task
            if self._background_task:
                self._background_task.cancel()
                try:
                    await self._background_task
                except asyncio.CancelledError:
                    pass
            
            self.is_running = False
            self.logger.info("WebSocket service stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping WebSocket service: {e}")
    
    async def _run_consumer(self) -> None:
        """
        Run the Redis consumer in the background.
        
        This is the main loop that consumes messages from Redis streams
        and broadcasts them to WebSocket clients.
        """
        try:
            self.logger.info("Starting Redis stream consumer")
            
            # Start consuming with our message handler
            await self.redis_consumer.start_consuming(
                message_callback=self._handle_alert_message
            )
            
        except asyncio.CancelledError:
            self.logger.info("Redis consumer cancelled")
            raise
        except Exception as e:
            self.logger.error(f"Error in Redis consumer: {e}")
            # Don't re-raise - let the service continue running
            # The consumer will handle reconnection internally
    
    async def _handle_alert_message(self, message: Dict[str, Any]) -> None:
        """
        Handle alert messages from Redis streams.
        
        This is the callback function that gets called for each message
        received from Redis streams. It broadcasts the message to all
        connected WebSocket clients.
        
        Args:
            message: Alert message from Redis stream
        """
        try:
            self.logger.debug("Received alert message for broadcasting", extra={
                "alert_type": message.get("alert_type"),
                "stream_name": message.get("stream_name"),
                "message_id": message.get("message_id")
            })
            
            # Broadcast message to all connected WebSocket clients
            await connection_manager.broadcast_message(message)
            
            self.logger.debug("Alert message broadcasted successfully", extra={
                "alert_type": message.get("alert_type"),
                "active_connections": connection_manager.get_connection_count()
            })
            
        except Exception as e:
            self.logger.error(f"Error handling alert message: {e}")
            # Don't re-raise - we don't want to break the consumer loop
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get service status information.
        
        Returns:
            Dictionary with service status
        """
        websocket_config = self.config.get('websocket', {})
        
        return {
            "is_running": self.is_running,
            "enabled": websocket_config.get('enabled', True),
            "active_connections": connection_manager.get_connection_count(),
            "redis_consumer": self.redis_consumer.get_consumer_info() if self.redis_consumer else None,
            "configuration": {
                "consumer_group_prefix": websocket_config.get('consumer_group_prefix', 'websocket_instance'),
                "redis_consumer": websocket_config.get('redis_consumer', {})
            }
        }


# Global WebSocket service instance
websocket_service = WebSocketService() 
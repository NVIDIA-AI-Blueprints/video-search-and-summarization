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
Base Message Consumer Abstract Class
Provides common functionality for all message broker consumers (Kafka, Redis, MQTT, etc.)
"""

import json
import logging
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from multiprocessing import Process

from google.protobuf.json_format import MessageToDict

import ext_pb2
import schema_pb2

# Default Constants
BATCH_SIZE = 100
DEFAULT_CONSUMER_GROUP = 'message-consumer'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BaseMessageConsumer(ABC):
    """Abstract base class for message consumers"""
    
    def __init__(self, args):
        """Initialize base consumer with command line arguments"""
        self.args = args
        self.processes = []
        
    @abstractmethod
    def get_consumer_type(self):
        """Return the type of consumer (e.g., 'Kafka', 'Redis', 'MQTT')"""
        pass
    
    @abstractmethod
    def get_source_names(self):
        """Get list of source names (topics/streams/channels) from args"""
        pass
    
    @abstractmethod
    def create_connection(self, source_name, consumer_group, **kwargs):
        """Create connection to the message broker
        
        Args:
            source_name: The topic/stream/channel name
            consumer_group: Consumer group name
            **kwargs: Additional connection parameters
            
        Returns:
            Connection object specific to the broker type
        """
        pass
    
    @abstractmethod
    def consume_messages(self, connection, source_name, consumer_group, **kwargs):
        """Consume messages from the broker
        
        Args:
            connection: The broker connection object
            source_name: The topic/stream/channel name
            consumer_group: Consumer group name
            **kwargs: Additional parameters specific to the broker
            
        Yields:
            tuple: (message_id, message_data) where message_data is bytes
        """
        pass
    
    @abstractmethod
    def acknowledge_messages(self, connection, source_name, consumer_group, message_ids):
        """Acknowledge processed messages (if applicable for the broker)
        
        Args:
            connection: The broker connection object
            source_name: The topic/stream/channel name
            consumer_group: Consumer group name
            message_ids: List of message IDs to acknowledge
        """
        pass
    
    @abstractmethod
    def close_connection(self, connection):
        """Close the connection to the broker
        
        Args:
            connection: The broker connection object
        """
        pass
    
    @abstractmethod
    def get_connection_info(self):
        """Get connection info string for logging"""
        pass
    
    @staticmethod
    def decode_protobuf_message(source_name, data):
        """Try to decode protobuf message to dict based on source name
        
        Args:
            source_name: The topic/stream/channel name
            data: Raw protobuf bytes
            
        Returns:
            dict: Decoded message or None if decoding fails
        """
        try:
            # Map source names to their expected protobuf message types
            if source_name in set(['mdx-raw', 'mdx-bev', 'mdx-frames']):
                message = schema_pb2.Frame()
                message.ParseFromString(data)
                return MessageToDict(message, preserving_proto_field_name=True)
            elif source_name in set(['mdx-behavior', 'mdx-events', 'mdx-alerts', 'mdx-behavior-plus']):
                message = ext_pb2.Behavior()
                message.ParseFromString(data)
                return MessageToDict(message, preserving_proto_field_name=True)
            elif source_name in set(['mdx-space-utilization']):
                message = ext_pb2.SpaceUtilization()
                message.ParseFromString(data)
                return MessageToDict(message, preserving_proto_field_name=True)
            elif source_name in set(['mdx-incidents']):
                message = ext_pb2.Incident()
                message.ParseFromString(data)
                return MessageToDict(message, preserving_proto_field_name=True)
        except Exception as e:
            # Don't log raw data - it could be huge and/or contain sensitive information
            logger.error(f"Failed to decode protobuf message for source {source_name}: {e}")
        
        logger.error(f"Source {source_name} must be one of the following: {['mdx-raw', 'mdx-bev', 'mdx-frames', 'mdx-behavior', 'mdx-events', 'mdx-alerts', 'mdx-behavior-plus', 'mdx-space-utilization', 'mdx-incidents']}")
        return None
    
    def process_source(self, source_name, output_file_path, consumer_group, **connection_params):
        """Process a message source and write to JSON lines file - runs in its own process
        
        Args:
            source_name: The topic/stream/channel name
            output_file_path: Path to the output file
            consumer_group: Consumer group name
            **connection_params: Additional connection parameters
        """
        # Ignore SIGINT in child processes - parent will handle Ctrl+C and send SIGTERM to children
        # This prevents child processes from receiving SIGINT directly when user presses Ctrl+C
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        
        # Set up logging for this process
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
        
        # Create connection for this process
        try:
            connection = self.create_connection(source_name, consumer_group=consumer_group, **connection_params)
        except Exception as e:
            logger.error(f"[{source_name}] Failed to create connection: {e}")
            return {'read': 0, 'written': 0, 'errors': 1}
        
        stats = {'read': 0, 'written': 0, 'errors': 0}
        
        # Flag for clean shutdown
        shutdown_requested = False
        
        def handle_term(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True
        
        # Handle SIGTERM for clean shutdown
        signal.signal(signal.SIGTERM, handle_term)
        
        # Open output file for this source
        with open(output_file_path, 'w', encoding='utf-8') as output_file:
            logger.info(f"[{source_name}] Starting processor - output: {output_file_path}")
            logger.info(f"[{source_name}] Connected to {self.get_consumer_type()}: {self.get_connection_info()}")
            first_batch = True
            index = 1
            
            while not shutdown_requested:
                try:
                    message_ids = []
                    messages_processed = False
                    
                    # Consume messages from the broker
                    for msg_id, msg_data in self.consume_messages(
                        connection, source_name, consumer_group, **connection_params
                    ):
                        # Check for special error markers (e.g., Kafka errors)
                        if msg_id == '__kafka_error__':
                            stats['errors'] += 1
                            messages_processed = True
                            continue
                        
                        # Log first batch to confirm processing started
                        if first_batch:
                            logger.info(f"[{source_name}] Started processing messages")
                            first_batch = False
                        
                        stats['read'] += 1
                        # Check if msg_id should be added (skip special markers like '__kafka_error__')
                        if msg_id:
                            # Handle both string and bytes message IDs
                            if isinstance(msg_id, bytes):
                                # Redis returns bytes, just add it
                                message_ids.append(msg_id)
                            elif isinstance(msg_id, str) and not msg_id.startswith('__'):
                                # String IDs - skip special markers
                                message_ids.append(msg_id)
                        
                        # Decode the protobuf message
                        if msg_data:
                            output_data = self.decode_protobuf_message(source_name, msg_data)
                            
                            # Write as JSON line (only the message data, no metadata)
                            if output_data is not None:
                                json.dump(output_data, output_file)
                                output_file.write('\n')
                                output_file.flush()
                                stats['written'] += 1
                        
                        messages_processed = True
                    
                    # Acknowledge messages if needed
                    if message_ids:
                        self.acknowledge_messages(connection, source_name, consumer_group, message_ids)
                    
                    # Log stats periodically every 1000 messages
                    if stats['read'] // 1000 == index:
                        logger.info(f"[{source_name}] Read {stats['read']}, Written {stats['written']}, Errors {stats['errors']}")
                        index += 1
                    
                    # If no messages were processed, sleep briefly to avoid busy-waiting
                    if not messages_processed:
                        time.sleep(0.1)
                        
                except (KeyboardInterrupt, SystemExit):
                    # Clean shutdown - no need to log error
                    break
                except Exception as e:
                    if shutdown_requested:
                        # Ignore errors during shutdown
                        break
                    # Only log real errors, not shutdown-related ones
                    logger.error(f"[{source_name}] Error processing: {e}")
                    stats['errors'] += 1
                    time.sleep(5)
        
        # Clean up
        try:
            self.close_connection(connection)
        except Exception as e:
            logger.error(f"[{source_name}] Error closing connection: {e}")
        
        # Always log final stats when exiting
        logger.info(f"[{source_name}] Final count - Read: {stats['read']}, Written: {stats['written']}, Errors: {stats['errors']}")
        logger.info(f"[{source_name}] Process stopped")
        return stats
    
    def run(self):
        """Main method to run the consumer"""
        # Parse source names
        source_names = self.get_source_names()
        
        # Create output directory if it doesn't exist
        os.makedirs(self.args.output_dir, exist_ok=True)
        
        # Startup logging
        consumer_type = self.get_consumer_type()
        logger.info(f"\n{'='*60}")
        logger.info(f"{consumer_type} to JSON Lines Dumper - Starting up")
        logger.info(f"{'='*60}")
        logger.info(f"Output directory: {self.args.output_dir}")
        logger.info(f"{consumer_type} connection: {self.get_connection_info()}")
        logger.info(f"Consumer group: {self.args.consumer_group}")
        logger.info(f"Sources to process: {', '.join(source_names)}")
        logger.info(f"{'='*60}\n")
        
        def signal_handler(signum, frame):
            signal_name = 'SIGINT (Ctrl+C)' if signum == signal.SIGINT else 'SIGTERM'
            logger.info(f"\n{'='*60}")
            logger.info(f"Received {signal_name} - shutting down...")
            
            # Send termination signal to all processes
            for p in self.processes:
                if p.is_alive():
                    with suppress(Exception):
                        p.terminate()
            
            # Wait briefly for processes to terminate
            for p in self.processes:
                with suppress(Exception):
                    p.join(timeout=2)
            
            logger.info("Shutdown complete")
            logger.info(f"{'='*60}\n")
            sys.exit(0)
        
        # Set up signal handlers for the main process
        # Child processes will ignore SIGINT (set in process_source)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            # Start a process for each source
            for source_name in source_names:
                process_args = self.get_process_args(source_name)
                
                logger.info(f"Starting process for source: {source_name} -> {process_args['output_file_path']}")
                
                process = Process(
                    target=self.process_source,
                    args=(source_name, process_args['output_file_path'], 
                          self.args.consumer_group),
                    kwargs=process_args.get('connection_params', {}),
                    name=f"process-{source_name}"
                )
                process.start()
                self.processes.append(process)
            
            logger.info(f"Started {len(self.processes)} process(es) for sources: {', '.join(source_names)}")
            logger.info("Press Ctrl+C to stop all processes gracefully")
            logger.info(f"{'='*60}\n")
            
            # Monitor processes
            monitor_count = 0
            while True:
                alive_count = 0
                dead_processes = []
                
                for p in self.processes:
                    if p.is_alive():
                        alive_count += 1
                    else:
                        dead_processes.append(p)
                
                # Report dead processes
                for p in dead_processes:
                    logger.warning(f"Process {p.name} is no longer running (exit code: {p.exitcode})")
                
                # Periodic status update every 30 seconds
                monitor_count += 1
                if monitor_count % 6 == 0:  # Every 30 seconds (6 * 5 second sleeps)
                    logger.info(f"Status: {alive_count}/{len(self.processes)} processes running")
                
                if alive_count == 0:
                    logger.error("All processes have died - exiting")
                    break
                
                time.sleep(5)
                
        except KeyboardInterrupt:
            pass  # Signal handler will take care of cleanup
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
        finally:
            # Ensure all processes are terminated
            for p in self.processes:
                if p.is_alive():
                    try:
                        p.terminate()
                        p.join(timeout=2)
                        if p.is_alive():
                            p.kill()
                            p.join()
                    except Exception as e:
                        logger.error(f"[{p.name}] Error terminating process: {e}")
            logger.info(f"{consumer_type} to JSON Lines Dumper shutdown complete")
            logger.info(f"{'='*60}\n")
    
    @abstractmethod
    def get_process_args(self, source_name):
        """Get process arguments for a specific source
        
        Args:
            source_name: The topic/stream/channel name
            
        Returns:
            dict: Contains 'output_file_path' and 'connection_params'
        """
        pass

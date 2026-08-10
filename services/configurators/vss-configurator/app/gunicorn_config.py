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
Gunicorn configuration and worker lifecycle hooks.

This module configures Gunicorn workers and ensures proper initialization
of logging and background threads in each worker process.
"""
import os


def get_env_bool(env_var, default):
    """Helper function to get boolean environment variables with proper defaults."""
    value = os.environ.get(env_var, "").strip().lower()
    if value == "true":
        return True
    elif value == "false":
        return False
    else:
        return default


def post_worker_init(worker):
    """
    Initialize worker with fresh environment variable reads and background threads.
    
    This hook is called after a worker has been forked and is ready to handle requests.
    It ensures each worker has its own properly configured logging and background threads.
    """
    # Import and refresh configuration to ensure fresh environment variable reads
    from sensor_config_manager import refresh_config, get_config, start_background_thread
    from utils.logger import setup_logging, get_logger
    
    # Setup logging in the worker process
    setup_logging(os.environ.get('LOG_LEVEL', 'INFO'))
    logger = get_logger(__name__)
    
    logger.debug(f"post_worker_init called for worker PID {worker.pid}")
    
    # Clear any cached configuration and re-read from environment
    logger.debug("Refreshing configuration for worker")
    refresh_config()
    config = get_config()
    
    # Log the configuration for debugging using both worker.log and standard logger
    worker.log.info(f"Worker {worker.pid} initializing with:")
    logger.info(f"Worker {worker.pid} configuration:")
    logger.debug(f"Full config keys: {list(config.keys())}")
    
    for key in ['CALIBRATION_MODE', 'CALIBRATION_API_ENDPOINT', 'ENABLE_CALIBRATION_PROCESS', 
                'SEND_CONFIG_TO_SDR', 'SENSOR_INFO_SOURCE', 'MESSAGE_BROKER_TYPE']:
        worker.log.info(f"  {key}: {config.get(key)}")
        logger.info(f"  {key}: {config.get(key)}")
        logger.debug(f"  Config {key}: {config.get(key)}")
    
    # Start background thread for sensor mapping
    logger.debug("Starting background thread for sensor data processing")
    start_background_thread()
    logger.info(f"Worker {worker.pid} initialization complete")


def worker_init(worker):
    """
    Initialize worker process - called before post_worker_init.
    
    This is called after the worker is forked but before post_worker_init.
    """
    worker.log.info(f"Worker {worker.pid} starting up...")
    worker.log.debug(f"worker_init called for PID {worker.pid}")

    
def pre_fork(server, worker):
    """
    Called just before a worker is forked.
    
    This hook allows for any necessary cleanup or preparation before forking.
    """
    server.log.info(f"About to fork worker {worker.pid}...")
    server.log.debug(f"pre_fork called for worker {worker.pid}")

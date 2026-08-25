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

import os
import json
import datetime
import copy
from flask import Flask, request, jsonify, send_file
from utils.kafka_producer import KfkProducer
from utils.recompute_bev_centers import recompute_bev_centers
from utils.message_broker_factory import MessageBrokerFactory
from utils.sensor_mapping import SensorMapping, Sensor
from utils.nvstreamer_upload import upload_videos, NVStreamerUploadError
from utils.vms_upload import upload_videos_to_vms, VMSUploadError
from typing import Dict, List, Optional
import requests
import time
import threading
import redis

# Initialize logging at application startup
from utils.logger import setup_logging, get_logger
setup_logging(os.environ.get('LOG_LEVEL', 'INFO'))  # Configure logging once at application entry point
logger = get_logger(__name__)

app = Flask(__name__)
sensor_mapping = None

# Video upload status tracking (for NVStreamer video upload feature)
# Status can be: "not_started", "in_progress", "completed", "failed", "disabled"
_video_upload_status = {
    "status": "not_started",
    "message": "Video upload has not been initiated",
    "uploaded_count": 0,
    "error": None
}
_video_upload_status_lock = threading.Lock()

def get_video_upload_status():
    """Thread-safe getter for video upload status."""
    with _video_upload_status_lock:
        return _video_upload_status.copy()

def set_video_upload_status(status: str, message: str, uploaded_count: int = 0, error: str = None):
    """Thread-safe setter for video upload status."""
    global _video_upload_status
    with _video_upload_status_lock:
        _video_upload_status = {
            "status": status,
            "message": message,
            "uploaded_count": uploaded_count,
            "error": error
        }
        logger.info(f"Video upload status updated: {_video_upload_status}")

# Configuration cache to avoid re-reading environment variables
_config_cache = {}

NVSTREAMER_STREAMS_POLL_INTERVAL_SEC = 5


def _parse_non_negative_int_env(env_var, default_value=0):
    """Parse a non-negative integer from the environment; invalid values fall back to default."""
    raw = os.environ.get(env_var)
    if raw is None or str(raw).strip() == "":
        return default_value
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(f"Invalid {env_var}={raw!r}; using {default_value}")
        return default_value
    if parsed < 0:
        logger.warning(f"{env_var}={parsed} is negative; using {default_value}")
        return default_value
    return parsed


def get_config():
    """Get configuration from environment variables with caching."""
    if not _config_cache:
        logger.debug("Initializing configuration cache from environment variables")
        
        # Thread configuration helpers
        def get_bool_env(env_var, default_value):
            if env_var in os.environ and os.environ[env_var].strip() != "":
                if os.environ[env_var].strip().lower() == "true":
                    return True
                elif os.environ[env_var].strip().lower() == "false":
                    return False
                else:
                    return default_value
            return default_value

        enable_calibration_process = get_bool_env("ENABLE_CALIBRATION_PROCESS", True)
        if enable_calibration_process:
            calibration_dir = os.environ.get("CALIBRATION_DIR_MOUNT_PATH", "/usr/src/app/calibration_store")
            calibration_file_name = os.environ.get("CALIBRATION_FILE_NAME", "calibration.json")
            logger.debug(f"Calibration directory: {calibration_dir}, Calibration file: {calibration_file_name}")
   
        else:
            calibration_dir = None
            calibration_file_name = None
        _config_cache.update({
            'SENSOR_MAPPING_FILE_PATH': os.path.join(calibration_dir, "sensor_mapping.json") if calibration_dir else "sensor_mapping.json",

            # Calibration configuration
            'ENABLE_CALIBRATION_PROCESS': enable_calibration_process,
            'CALIBRATION_FILE_NAME': calibration_file_name,
            'CALIBRATION_FILE_PATH': os.path.join(calibration_dir, calibration_file_name) if calibration_dir and calibration_file_name else None,
            'CALIBRATION_DIR': calibration_dir,
            'CALIBRATION_MODE': os.environ.get("CALIBRATION_MODE", "fetch").lower(),
            'CALIBRATION_API_ENDPOINT': os.environ.get("CALIBRATION_API_ENDPOINT", ""),
            'CALIBRATION_API_TIMEOUT': int(os.environ.get("CALIBRATION_API_TIMEOUT", "30")),
            'GET_CALIBRATION_DELAY': int(os.environ.get("GET_CALIBRATION_DELAY", "30")),
            
            # Service endpoints
            'SENSOR_BRIDGE_HTTP_ENDPOINT': os.environ.get("SENSOR_BRIDGE_HTTP_ENDPOINT", "http://localhost:8000/mtmc/urls"),
            'VST_CAMERA_ADD_ENDPOINT': os.environ.get("VST_CAMERA_ADD_ENDPOINT", "http://vms-vms-svc:30000/api/v1/sensor/add"),
            'NVSTREAMER_STREAMS_ENDPOINT': os.environ.get("NVSTREAMER_STREAMS_ENDPOINT", "http://localhost:30000/api/v1/live/streams"),
            'NVSTREAMER_SENSOR_STATUS_ENDPOINT': os.environ.get("NVSTREAMER_SENSOR_STATUS_ENDPOINT", "http://localhost:30000/api/v1/sensor/status"),
            
            # Naming suffixes
            'PN_SUFFIX': os.environ.get("PN_SUFFIX", ""),
            'CV_SUFFIX': os.environ.get("CV_SUFFIX", "-cv"),
            
            # Application configuration
            'PORT': os.environ.get("PORT", "5000"),
            
            # Thread configuration (will be fixed below)
            # 'ENABLE_SENSOR_MAPPING_THREAD': True,  # Temporary, fixed below
            # 'ENABLE_REDIS_DUPLICATOR_THREAD': get_bool_env("ENABLE_REDIS_DUPLICATOR_THREAD", False),
            'SENSOR_INFO_SOURCE': os.environ.get("SENSOR_INFO_SOURCE", "msb").lower(),  # 'msb', 'nvstreamer', or 'file'

            # Sensor file configuration (used when SENSOR_INFO_SOURCE='file')
            'SENSOR_FILE_PATH': os.environ.get("SENSOR_FILE_PATH", os.path.join(calibration_dir, "sensors.json") if calibration_dir else "sensors.json"),

            # Whether to send sensor config event to kafka
            'SEND_CONFIG_TO_SDR': get_bool_env("SEND_CONFIG_TO_SDR", True),

            # Whether to call sensor add API of VMS/RTSP Server
            'CALL_SENSOR_ADD_API': get_bool_env("CALL_SENSOR_ADD_API", True),
            
            # Message broker configuration
            'MESSAGE_BROKER_TYPE': os.environ.get("MESSAGE_BROKER_TYPE", "kafka").lower(),
            # 'MESSAGE_BROKER_TOPIC': os.environ.get("MESSAGE_BROKER_TOPIC", ""),
            
            # Redis configuration
            'WDM_REDIS_HOST': os.environ.get("WDM_REDIS_HOST", "localhost"),
            'WDM_REDIS_PORT': int(os.environ.get("WDM_REDIS_PORT", 6379)),
            # 'WDM_REDIS_DB': int(os.environ.get("WDM_REDIS_DB", 0)),
            'WDM_REDIS_STREAM_NAME': os.environ.get("WDM_REDIS_STREAM_NAME", "sensor"),
            'WDM_REDIS_MSG_KEY': os.environ.get('WDM_REDIS_MSG_KEY', 'sensor.id'),

            # Redis Duplicator configuration
            'ENABLE_REDIS_DUPLICATOR_THREAD': get_bool_env("ENABLE_REDIS_DUPLICATOR_THREAD", False),
            'REDIS_SOURCE_TOPIC': os.environ.get("REDIS_SOURCE_TOPIC", "vst.event"),
            'REDIS_TARGET_TOPIC_CV': os.environ.get("REDIS_TARGET_TOPIC_CV", "vst.event.cv"),
            'REDIS_TARGET_TOPIC_PN26': os.environ.get("REDIS_TARGET_TOPIC_PN26", "vst.event.pn26"),
            'REDIS_DB': int(os.environ.get("REDIS_DB", 0)),

            # Kafka configuration
            'WDM_KFK_BOOTSTRAP_URL': os.environ.get('WDM_KFK_BOOTSTRAP_URL', ''),
            'WDM_WL_ID_FIELD': os.environ.get('WDM_WL_ID_FIELD', 'camera_id'),
            'WDM_WL_EVENT_FIELD': os.environ.get('WDM_WL_EVENT_FIELD', 'event'),
            'WDM_KFK_TOPIC': os.environ.get('WDM_KFK_TOPIC', ''),
            'WDM_KFK_MSG_KEY': os.environ.get('WDM_KFK_MSG_KEY', 'sensor'),

            # Recompute BEV centers configs
            'RECOMPUTE_BEV_CENTERS_ENABLED': get_bool_env("RECOMPUTE_BEV_CENTERS_ENABLED", False),
 
            # Profile configurator configuration
            'ENABLE_PROFILE_CONFIGURATOR': True if os.environ.get("ENABLE_CALIBRATION_PROCESS", "true").lower() == "true" else False,
            
            # Deployment profile (2d or 3d) configuration
            'MODE': os.environ.get("MODE", "3d").lower(),

            # Expected camera count from NVStreamer (0 = unknown; use list stability instead)
            'NUM_STREAMS': _parse_non_negative_int_env("NUM_STREAMS", 0),

            # After NVStreamer returns a non-empty but incomplete list, wait this many seconds for more streams
            'NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT': int(os.environ.get("NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", "120")),
            'NVSTREAMER_STREAM_VALIDATION_MAX_RETRIES': int(os.environ.get("NVSTREAMER_STREAM_VALIDATION_MAX_RETRIES", "50")),
            'NVSTREAMER_STREAM_VALIDATION_RETRY_DELAY': int(os.environ.get("NVSTREAMER_STREAM_VALIDATION_RETRY_DELAY", "5")),

            # Video upload configuration (NVStreamer and/or VMS)
            'ENABLE_NVSTREAMER_VIDEO_UPLOAD': get_bool_env("ENABLE_NVSTREAMER_VIDEO_UPLOAD", False),
            'ENABLE_VMS_VIDEO_UPLOAD': get_bool_env("ENABLE_VMS_VIDEO_UPLOAD", False),
            'VIDEO_SOURCE_DIR': os.environ.get("VIDEO_SOURCE_DIR", ""),
            'VIDEO_UPLOAD_TIMEOUT': int(os.environ.get("VIDEO_UPLOAD_TIMEOUT", "300")),
            'VIDEO_UPLOAD_DELAY': int(os.environ.get("VIDEO_UPLOAD_DELAY", "0")),
            'NVSTREAMER_UPLOAD_BASE_URL': os.environ.get("NVSTREAMER_UPLOAD_BASE_URL", "http://localhost:30000"),
            'VMS_UPLOAD_BASE_URL': os.environ.get("VMS_UPLOAD_BASE_URL", "http://localhost:30888"),
        })
        
        # enable_sensor_mapping = True
        # if "ENABLE_SENSOR_MAPPING_THREAD" in os.environ and os.environ["ENABLE_SENSOR_MAPPING_THREAD"].strip() != "":
        #     enable_sensor_mapping = os.environ["ENABLE_SENSOR_MAPPING_THREAD"].strip().lower() != "false"
        # _config_cache['ENABLE_SENSOR_MAPPING_THREAD'] = enable_sensor_mapping
        
        # Create REDIS_TARGET_TOPICS list
        _config_cache['REDIS_TARGET_TOPICS'] = [
            _config_cache['REDIS_TARGET_TOPIC_CV'],
            _config_cache['REDIS_TARGET_TOPIC_PN26']
        ]
        
        logger.debug(f"Configuration cache initialized with MESSAGE_BROKER_TYPE={_config_cache['MESSAGE_BROKER_TYPE']}, "
                    f"SENSOR_INFO_SOURCE={_config_cache['SENSOR_INFO_SOURCE']}, "
                    f"CALIBRATION_MODE={_config_cache['CALIBRATION_MODE']}")
        
    return _config_cache

def refresh_config():
    """Clear configuration cache to force re-reading from environment variables."""
    logger.debug("Refreshing configuration cache")
    global _config_cache, CONFIG
    _config_cache.clear()
    CONFIG = get_config()  # Refresh the global config instance
    logger.debug("Configuration cache refreshed successfully")

# Create global configuration instance
CONFIG = get_config()

# Create calibration directory using config
if CONFIG['ENABLE_CALIBRATION_PROCESS'] and CONFIG['CALIBRATION_DIR']:
    os.makedirs(CONFIG['CALIBRATION_DIR'], exist_ok=True)

def initialize_sensor_mapping():
    global sensor_mapping
    logger.debug(f"Initializing sensor mapping from file: {CONFIG['SENSOR_MAPPING_FILE_PATH']}")
    try:
        sensor_mapping = SensorMapping.load_from_file(CONFIG['SENSOR_MAPPING_FILE_PATH'])
        if sensor_mapping:
            logger.info(f"Loaded existing sensor mapping from persistent storage at {CONFIG['SENSOR_MAPPING_FILE_PATH']}.")
            logger.debug(f"Loaded {len(sensor_mapping.sensors)} sensors from persistent storage")
        else:
            logger.info(f"No existing sensor mapping found at {CONFIG['SENSOR_MAPPING_FILE_PATH']}.")
    except Exception as e:
        logger.exception(f"Error loading sensor mapping.")

# Only initialize sensor mapping if calibration process is enabled
if CONFIG['ENABLE_CALIBRATION_PROCESS']:
    with app.app_context():
        initialize_sensor_mapping()
            
def fetch_sensor_data_from_msb(delay=60, timeout=5) -> Optional[List[Dict]]:
    logger.debug(f"Fetching sensor data from MSB endpoint: {CONFIG['SENSOR_BRIDGE_HTTP_ENDPOINT']}")
    while True:
        try:
            logger.debug(f"Making GET request to MSB endpoint with timeout={timeout}s")
            response = requests.get(CONFIG['SENSOR_BRIDGE_HTTP_ENDPOINT'], timeout=timeout)
            logger.debug(f"Received response from MSB with status code: {response.status_code}")
            if response.status_code == 200:
                logger.info("Successfully fetched sensor data.")
                data = response.json()
                logger.debug(f"Fetched {len(data)} groups from MSB")
                return data
            else:
                logger.warning(f"Error fetching sensor data. Status code: {response.status_code}")
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay) 
                continue
        except Exception as e:   
            logger.error(f"Error fetching sensor data: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay) 
            continue

def add_sensor(sensor_info: Sensor, delay=30):
    logger.debug(f"Adding sensor: {sensor_info.name} to VMS endpoint: {CONFIG['VST_CAMERA_ADD_ENDPOINT']}")
    headers = {"Content-Type": "application/json"}
    sensor_data = {
        "username": "",
        "password": "",
        "name": sensor_info.name,
        "sensorUrl": sensor_info.url
        }
    if sensor_info.region and sensor_info.group_id:
        sensor_data["tags"] = f"{sensor_info.region}|{sensor_info.group_id}"
        logger.debug(f"Sensor tags set: {sensor_data['tags']}")

    while True:
        try:
            logger.debug(f"Sending POST request to add sensor: {sensor_data['name']}")
            response = requests.post(CONFIG['VST_CAMERA_ADD_ENDPOINT'], json=sensor_data, headers=headers, timeout=5)
            if response and response.status_code == 200:
                logger.info(f"Successfully added sensor: {sensor_data['name']}")
                logger.debug(f"VMS response: {response.text}")
                return
            logger.warning(f"Error adding sensor {sensor_data['name']}. Received status code {response.status_code} from VMS. Retrying in {delay} seconds...")
            logger.debug(f"VMS error response: {response.text if response else 'No response'}")
        except Exception as e:
            logger.warning(
                f"VST sensor add API unreachable Retrying in {delay} seconds..."
            )
            logger.debug(f"Exception details: {repr(e)}")
            time.sleep(delay)
            continue

def fetch_calibration_from_api(delay):
    """Fetch calibration data from configured API endpoint."""
    calibration_api_endpoint = CONFIG['CALIBRATION_API_ENDPOINT']
    calibration_api_timeout = CONFIG['CALIBRATION_API_TIMEOUT']
    
    logger.debug(f"fetch_calibration_from_api called with delay={delay}, endpoint={calibration_api_endpoint}, timeout={calibration_api_timeout}")
    
    if not calibration_api_endpoint:
        logger.error("CALIBRATION_API_ENDPOINT not configured but CALIBRATION_MODE is set to 'fetch'")
        raise ValueError("CALIBRATION_API_ENDPOINT must be set when CALIBRATION_MODE is 'fetch'")
    
    while True:
        try:
            logger.info(f"Fetching calibration data from API: {calibration_api_endpoint}")
            logger.debug(f"Making GET request with timeout={calibration_api_timeout}s")
            response = requests.get(calibration_api_endpoint, timeout=calibration_api_timeout)
            logger.debug(f"Received response with status code: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    calibration_data = response.json()
                    sensors_data = calibration_data.get("sensors", [])
                    calibration_type = calibration_data.get("calibrationType", "")
                    logger.debug(f"Parsed calibration JSON: {len(sensors_data)} sensors, calibrationType={calibration_type}")
                    if not (calibration_data and sensors_data and calibration_type):
                        logger.warning(f"Unable to fetch calibration data from API. Retrying in {delay} seconds...")  
                        logger.debug(f"Calibration data validation failed: has_data={bool(calibration_data)}, has_sensors={bool(sensors_data)}, has_type={bool(calibration_type)}")
                        time.sleep(delay)
                        continue
                    logger.info("Successfully fetched calibration data from API")
                  
                    # Save the fetched data to local file for consistency
                    logger.debug(f"Saving calibration data to file: {CONFIG['CALIBRATION_FILE_PATH']}")
                    with open(CONFIG['CALIBRATION_FILE_PATH'], 'w') as file:
                        json.dump(calibration_data, file)
                    logger.info(f"Successfully saved calibration file to {CONFIG['CALIBRATION_FILE_PATH']}")
                    
                    return calibration_data
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON response from calibration API: {e}")
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                    continue
            else:
                logger.warning(f"Error fetching calibration data from API. Status code: {response.status_code}")
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error while fetching calibration data from API: {e}")
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            continue
        except Exception as e:
            logger.error(f"Unexpected error while fetching calibration data from API: {e}")
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            continue

def get_calibration_data():
    """Get calibration data based on configured mode (fetch or upload)."""
    calibration_mode = CONFIG['CALIBRATION_MODE']
    calibration_file_path = CONFIG['CALIBRATION_FILE_PATH']
    get_calibration_delay = CONFIG['GET_CALIBRATION_DELAY']
    
    if calibration_mode == "fetch":
        logger.info("Calibration mode is set to 'fetch' - fetching from API endpoint")
        return fetch_calibration_from_api(get_calibration_delay)
    elif calibration_mode == "upload":
        logger.info("Calibration mode is set to 'upload' - reading from uploaded file")
        while True:
            if os.path.exists(calibration_file_path):
                logger.info(f"Calibration file found at {calibration_file_path}")
                with open(calibration_file_path, 'r') as file:
                    calibration_data = json.load(file)
                return calibration_data
            else:
                logger.info(f"Calibration file not found at {calibration_file_path}. Retrying in {get_calibration_delay} seconds...")
                time.sleep(get_calibration_delay)
                continue
    elif calibration_mode == "mount":
        logger.info("Calibration mode is set to 'mount' - reading from mounted file")
        if os.path.exists(calibration_file_path):
            with open(calibration_file_path, 'r') as file:
                calibration_data = json.load(file)
            return calibration_data
        else:
            logger.error(f"Calibration file not found at {calibration_file_path}")
            raise ValueError(f"Calibration file not found at {calibration_file_path}")
    else:
        logger.error(f"Invalid CALIBRATION_MODE: '{calibration_mode}'. Must be 'fetch' or 'upload'")
        raise ValueError(f"Invalid CALIBRATION_MODE: '{calibration_mode}'. Must be 'fetch' or 'upload'")

def process_sensor_info_from_msb():
    logger.debug("Starting process_sensor_info_from_msb")
    sensor_bridge_output = fetch_sensor_data_from_msb()    
    logger.info(f"Sensor bridge output: {sensor_bridge_output}")
    global sensor_mapping
    if CONFIG['ENABLE_CALIBRATION_PROCESS']:
        logger.debug("Calibration process enabled, getting calibration data")
        calibration_data = get_calibration_data()
        logger.debug(f"Generating sensor mapping with calibration data")
        sensor_mapping = SensorMapping.generate(sensor_bridge_output, calibration_data, logger, info_source="msb")
    else:
        logger.debug("Calibration process disabled, generating sensor mapping without calibration data")
        sensor_mapping = SensorMapping.generate(sensor_bridge_output, None, logger, info_source="msb")
    logger.debug(f"Saving sensor mapping to {CONFIG['SENSOR_MAPPING_FILE_PATH']}")
    sensor_mapping.save_to_file(CONFIG['SENSOR_MAPPING_FILE_PATH']) 
    logger.info(f"Generated Sensor mapping: {sensor_mapping.sensors}")
    logger.debug(f"Generated {len(sensor_mapping.sensors)} sensors in mapping")
    logger.info(f"Sensor mapping saved to file at {CONFIG['SENSOR_MAPPING_FILE_PATH']}")

    if CONFIG['SEND_CONFIG_TO_SDR']:
        send_config_to_sdr(sensor_mapping)
        # try:
        #     # Determine topic based on message broker type
        #     topic = CONFIG['WDM_KFK_MSG_KEY'] if CONFIG['MESSAGE_BROKER_TYPE'] == 'redis' else CONFIG['WDM_KFK_TOPIC']
        #     msg_key=CONFIG['WDM_REDIS_MSG_KEY'] if CONFIG['MESSAGE_BROKER_TYPE'] == 'redis' else CONFIG['WDM_KFK_MSG_KEY']

        #     # Create message broker instance using factory
        #     message_broker = MessageBrokerFactory.create_message_broker(
        #         broker_type=CONFIG['MESSAGE_BROKER_TYPE'],
        #         config=CONFIG
        #     )
            
        #     # Send message using the configured broker
        #     message_broker.send_message(topic=topic, key=msg_key, sensor_mapping=sensor_mapping)
        #     logger.info(f"Message sent successfully to {CONFIG['MESSAGE_BROKER_TYPE']} topic/stream {topic}")
            
        #     # Close the connection
        #     message_broker.close()
        # except Exception as e:
        #     logger.error(f"Error sending message via {CONFIG['MESSAGE_BROKER_TYPE']}: {e}")
    if CONFIG['CALL_SENSOR_ADD_API']:
        logger.info("Calling sensor add API for VMS/RTSP Server")
        [add_sensor(sensor_info) for sensor_info in sensor_mapping.sensors.values()]
        logger.info(f"Successfully called sensor add API for {len(sensor_mapping.sensors)} sensors")
    else:
        logger.info("Skipping sensor add API call as CALL_SENSOR_ADD_API is disabled")


def fetch_sensor_data_from_file(delay=30) -> Optional[List[Dict]]:
    """
    Fetch sensor data from a JSON file.
    
    Expected file schema:
    {
        "sensors": [
            {
                "camera_name": "camera1",
                "rtsp_url": "rtsp://192.168.1.100:554/stream1",
                "group_id": "group1",  // optional
                "region": "building-A"  // optional
            },
            ...
        ]
    }
    
    Returns:
        List of sensor dictionaries with camera_name, rtsp_url, and optional group_id/region
    """
    sensor_file_path = CONFIG['SENSOR_FILE_PATH']
    logger.debug(f"Fetching sensor data from file: {sensor_file_path}")
    
    while True:
        try:
            if not os.path.exists(sensor_file_path):
                logger.warning(f"Sensor file not found at {sensor_file_path}. Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
            
            logger.debug(f"Reading sensor file: {sensor_file_path}")
            with open(sensor_file_path, 'r') as f:
                file_data = json.load(f)
            
            # Validate file structure
            if not isinstance(file_data, dict) or 'sensors' not in file_data:
                logger.error(f"Invalid sensor file format. Expected 'sensors' key in JSON. Got: {list(file_data.keys()) if isinstance(file_data, dict) else type(file_data)}")
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
            
            sensors_list = file_data['sensors']
            if not isinstance(sensors_list, list) or len(sensors_list) == 0:
                logger.warning(f"Sensor file contains empty or invalid sensors list. Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
            
            # Validate each sensor entry has required fields
            valid_sensors = []
            for idx, sensor in enumerate(sensors_list):
                if not isinstance(sensor, dict):
                    logger.warning(f"Skipping invalid sensor entry at index {idx}: not a dictionary")
                    continue
                if 'camera_name' not in sensor or 'rtsp_url' not in sensor:
                    logger.warning(f"Skipping sensor entry at index {idx}: missing 'camera_name' or 'rtsp_url'")
                    continue
                valid_sensors.append(sensor)
            
            if len(valid_sensors) == 0:
                logger.warning(f"No valid sensors found in file. Retrying in {delay} seconds...")
                time.sleep(delay)
                continue
            
            logger.info(f"Successfully loaded {len(valid_sensors)} sensors from file: {sensor_file_path}")
            logger.debug(f"Loaded sensors: {[s['camera_name'] for s in valid_sensors]}")
            return valid_sensors
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in sensor file {sensor_file_path}: {e}")
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            continue
        except Exception as e:
            logger.error(f"Error reading sensor file: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            continue


def process_sensor_info_from_file():
    """
    Process sensor information from a file and create sensor mapping.
    
    This function:
    1. Reads sensor data from the configured sensor file
    2. Optionally merges with calibration data if available
    3. Creates and saves the sensor mapping
    4. Sends config to SDR if enabled
    5. Calls sensor add API if enabled
    """
    logger.debug("Starting process_sensor_info_from_file")
    global sensor_mapping

    # Fetch sensor data from file
    file_sensors = fetch_sensor_data_from_file()
    logger.info(f"Loaded {len(file_sensors)} sensors from file")    

    if CONFIG['ENABLE_CALIBRATION_PROCESS']:
        logger.debug("Calibration process enabled, getting calibration data")
        calibration_data = get_calibration_data()
        logger.debug("Generating sensor mapping with calibration data")
        sensor_mapping = SensorMapping.generate(file_sensors, calibration_data, logger, info_source="file")
    else:
        logger.debug("Calibration process disabled, generating sensor mapping without calibration data")
        # When no calibration data, we can still use group_id and region from the file
        sensor_mapping = SensorMapping.generate(file_sensors, None, logger, info_source="file")

    logger.debug(f"Saving sensor mapping to {CONFIG['SENSOR_MAPPING_FILE_PATH']}")
    sensor_mapping.save_to_file(CONFIG['SENSOR_MAPPING_FILE_PATH'])
    logger.info(f"Generated Sensor mapping: {sensor_mapping.sensors}")
    logger.debug(f"Generated {len(sensor_mapping.sensors)} sensors in mapping")
    logger.info(f"Sensor mapping saved to file at {CONFIG['SENSOR_MAPPING_FILE_PATH']}")

    if CONFIG['RECOMPUTE_BEV_CENTERS_ENABLED'] and CONFIG['MODE'] == '3d': # Only recompute BEV centers for 3D mode
        logger.info("Recomputing BEV centers")
        sensor_names = [sensor.name for sensor in sensor_mapping.sensors.values()]
        n_sensor_groups = 1 # n_sensor_groups=1 for docker compose since we have only one group.
        max_sensors_per_group = len(sensor_names)
        calib_file_path = recompute_bev_centers(CONFIG['CALIBRATION_FILE_PATH'], sensor_names, n_sensor_groups, max_sensors_per_group)
        logger.info(f"BEV groups recomputed and saved to {calib_file_path}")
    else:
        logger.info("Skipping BEV centers recomputation as RECOMPUTE_BEV_CENTERS_ENABLED is disabled")

    if CONFIG['SEND_CONFIG_TO_SDR']:
        send_config_to_sdr(sensor_mapping)
    else:
        logger.info("Skipping config message sending as SEND_CONFIG_TO_SDR is disabled")
    
    if CONFIG['CALL_SENSOR_ADD_API']:
        logger.info("Calling sensor add API for VMS/RTSP Server")
        [add_sensor(sensor_info) for sensor_info in sensor_mapping.sensors.values()]
        logger.info(f"Successfully called sensor add API for {len(sensor_mapping.sensors)} sensors")
    else:
        logger.info("Skipping sensor add API call as CALL_SENSOR_ADD_API is disabled")
    
    # if CONFIG['ENABLE_REDIS_DUPLICATOR_THREAD']:
    #     send_nvstreamer_streams_to_redis(nvstreamer_streams)
    #     start_redis_duplicator_thread()

def start_sensor_data_processing():
    # Upload videos to NVStreamer and/or VMS if enabled
    if CONFIG['ENABLE_NVSTREAMER_VIDEO_UPLOAD'] or CONFIG['ENABLE_VMS_VIDEO_UPLOAD']:
        _run_video_uploads()
    else:
        logger.debug("Video upload is disabled (NVStreamer and VMS upload both disabled)")
        set_video_upload_status(
            status="disabled",
            message="Video upload is disabled (ENABLE_NVSTREAMER_VIDEO_UPLOAD and ENABLE_VMS_VIDEO_UPLOAD=false)",
            uploaded_count=0,
        )
    logger.debug(f"Starting sensor data processing with SENSOR_INFO_SOURCE={CONFIG['SENSOR_INFO_SOURCE']}")
    if CONFIG['SENSOR_INFO_SOURCE'] == 'msb':
        logger.debug("Processing sensor info from MSB")
        process_sensor_info_from_msb()
    
    elif CONFIG['SENSOR_INFO_SOURCE'] == 'nvstreamer':
        logger.debug("Processing sensor info from Nvstreamer")
        process_sensor_info_from_nvstreamer()
    elif CONFIG['SENSOR_INFO_SOURCE'] == 'file':
        logger.debug("Processing sensor info from file")
        process_sensor_info_from_file()
    elif CONFIG['SENSOR_INFO_SOURCE'] == 'not_required':
        logger.debug("Sensor info source is not required, skipping sensor data processing")
        return
    else:
        logger.error(f"Invalid SENSOR_INFO_SOURCE: {CONFIG['SENSOR_INFO_SOURCE']}. Valid options are: 'msb', 'nvstreamer', 'file'")
        raise ValueError(f"Invalid SENSOR_INFO_SOURCE: {CONFIG['SENSOR_INFO_SOURCE']}. Valid options are: 'msb', 'nvstreamer', 'file'")
def start_background_thread():
    logger.debug("Starting background thread for sensor data processing")
    thread = threading.Thread(target=start_sensor_data_processing)
    thread.daemon = True
    thread.start()
    logger.info("Background thread started successfully")


def nvstreamer_stream_is_valid(stream_name):
    """
    Validates if a Nvstreamer stream is online and has no errors.
    Retries validation based on configured retry settings.
    
    Args:
        stream_name: Name of the stream to validate
        
    Returns:
        bool: True if stream is valid (online and no errors), False otherwise
    """
    max_retries = CONFIG['NVSTREAMER_STREAM_VALIDATION_MAX_RETRIES']
    retry_delay = CONFIG['NVSTREAMER_STREAM_VALIDATION_RETRY_DELAY']
    
    logger.debug(f"Validating Nvstreamer stream: {stream_name} (max_retries={max_retries}, retry_delay={retry_delay})")
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"Validation attempt {attempt + 1}/{max_retries} for stream '{stream_name}'")
            # Make request to VST status endpoint
            resp = requests.get(CONFIG['NVSTREAMER_SENSOR_STATUS_ENDPOINT'], timeout=10)
            logger.debug(f"Received status response: {resp.status_code}")
            
            if not resp.status_code == 200:
                logger.info(f"Did not get return code 200 from Nvstreamer sensor status endpoint (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                return False

            try:
                json_vals = resp.json()
            except Exception as e:
                logger.info(f"Couldn't parse Nvstreamer sensor status endpoint response (attempt {attempt + 1}/{max_retries}). Exception: {repr(e)}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                return False
            
            # Search for the stream in the response
            stream_found = False
            for key, value in json_vals.items():
                if "name" not in value:
                    continue
                if value["name"] != stream_name:
                    continue
                
                stream_found = True
                is_valid = True
                
                # Check errorCode field
                if "errorCode" in value:
                    if value["errorCode"] != "NoError":
                        is_valid = False
                        logger.info(f"Stream '{stream_name}' has error code: {value['errorCode']} (attempt {attempt + 1}/{max_retries})")
                else:
                    is_valid = False
                    logger.info(f"Stream '{stream_name}' missing errorCode field (attempt {attempt + 1}/{max_retries})")
                
                # Check state field
                if "state" in value:
                    if value["state"] != "online":
                        is_valid = False
                        logger.info(f"Stream '{stream_name}' state is: {value['state']} (attempt {attempt + 1}/{max_retries})")
                else:
                    is_valid = False
                    logger.info(f"Stream '{stream_name}' missing state field (attempt {attempt + 1}/{max_retries})")
                
                # If stream is valid, return immediately
                if is_valid:
                    logger.info(f"Stream '{stream_name}' is valid (online with no errors)")
                    return True
                
                # Stream found but not valid, retry if attempts remaining
                if attempt < max_retries - 1:
                    logger.info(f"Stream '{stream_name}' not valid yet. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                break
            
            # If stream was not found in the response
            if not stream_found:
                logger.info(f"Stream '{stream_name}' not found in Nvstreamer sensor status response (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error while checking Nvstreamer stream status (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            return False
        except Exception as e:
            logger.error(f"Unexpected error while checking Nvstreamer stream status (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            return False
    
    # All retries exhausted
    logger.warning(f"Stream '{stream_name}' validation failed after {max_retries} attempts")
    return False

def _nvstreamer_stream_list_is_complete(current_count, last_count, expected_count):
    """Return True when the NVStreamer stream list is ready to commit.

    NVStreamer registers files serially, so a non-empty list is not complete.
    Prefer NUM_STREAMS when set; otherwise require the count to stay stable
    across two consecutive polls.
    """
    if current_count <= 0:
        return False
    if expected_count > 0:
        return current_count >= expected_count
    return last_count is not None and last_count == current_count


def fetch_all_streams_from_nvstreamer():
    api_up = False
    timeout = CONFIG['NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT']
    expected_count = CONFIG.get('NUM_STREAMS', 0) or 0
    last_count = None
    json_vals = []
    poll_interval = NVSTREAMER_STREAMS_POLL_INTERVAL_SEC
    partial_wait_started_at = None

    while not api_up:
        try:
            logger.info("Checking Nvstreamer streams endpoint to see if it's ready")
            resp = requests.get(CONFIG['NVSTREAMER_STREAMS_ENDPOINT'])
        except Exception as e:
            logger.warning(f"Error while checking Nvstreamer streams endpoint, retrying in {poll_interval} seconds")
            logger.debug(f"Exception details: {repr(e)}")
            time.sleep(poll_interval)
            continue

        if not resp.status_code == 200:
            logger.info(
                f"Getting status code {resp.status_code} from Nvstreamer streams endpoint "
                f"{CONFIG['NVSTREAMER_STREAMS_ENDPOINT']} - retrying in {poll_interval} seconds"
            )
            time.sleep(poll_interval)
            continue

        try:
            json_vals = resp.json()
            current_count = len(json_vals) if json_vals else 0
            if current_count == 0:
                logger.info(
                    f"Nvstreamer streams endpoint returned empty response - retrying in {poll_interval} seconds"
                )
                last_count = None
                partial_wait_started_at = None
                time.sleep(poll_interval)
                continue
        except Exception as e:
            logger.info(
                f"Failed to parse Nvstreamer response as JSON - retrying in {poll_interval} seconds. "
                f"Exception: {repr(e)}"
            )
            time.sleep(poll_interval)
            continue

        logger.info(
            f"Getting status code {resp.status_code} from Nvstreamer streams endpoint "
            f"{CONFIG['NVSTREAMER_STREAMS_ENDPOINT']} with valid response"
        )
        logger.info(f"Successfully parsed Nvstreamer streams endpoint response: {json_vals}")

        if _nvstreamer_stream_list_is_complete(current_count, last_count, expected_count):
            api_up = True
            break

        now = time.time()
        if partial_wait_started_at is None:
            partial_wait_started_at = now
        partial_elapsed = now - partial_wait_started_at
        if partial_elapsed >= timeout:
            expected_note = f", expected {expected_count}" if expected_count else ""
            logger.warning(
                f"Nvstreamer reported {current_count} stream(s) for {partial_elapsed:.1f}s "
                f"(NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT {timeout}s{expected_note}); "
                "proceeding with the list collected so far"
            )
            break
        retry_in = min(poll_interval, max(0.0, timeout - partial_elapsed))
        if expected_count > 0:
            logger.info(
                f"Nvstreamer reported {current_count} stream(s), waiting for expected {expected_count} "
                f"- retrying in {retry_in} seconds (timeout {timeout}s)"
            )
        else:
            logger.info(
                f"Nvstreamer reported {current_count} stream(s); waiting for the list to stabilize "
                f"- retrying in {retry_in} seconds (timeout {timeout}s)"
            )
        last_count = current_count
        time.sleep(retry_in)

    if expected_count > 0 and len(json_vals) < expected_count:
        logger.warning(
            f"Fetched {len(json_vals)} stream(s) from Nvstreamer but NUM_STREAMS={expected_count}; "
            "cameras may be missing from VST"
        )

    nvstreamer_streams = []
    for stream in json_vals:
        for key, value in stream.items():
            if len(value) < 1:
                continue
            curr_data = value[0]
            if curr_data["isMain"]:
                curr_dict = {}
                curr_dict["source"] = "preload"
                curr_dict["event"] = {}
                # curr_dict["event"]["camera_id"] = curr_data["streamId"]
                curr_dict["event"]["camera_id"] = curr_data["name"] # Quick fix till the time nvstreamer generates correct unique id
                curr_dict["event"]["camera_name"] = curr_data["name"]
                curr_dict["event"]["camera_url"] = curr_data["url"]
                curr_dict["event"]["change"] = "camera_streaming"
                curr_dict["event"]["metadata"] = curr_data["metadata"]
                
                if not nvstreamer_stream_is_valid(curr_data["name"]):
                    logger.info(f"Stream {curr_data['name']} is not online - skipping add")
                    continue
                else:
                    logger.info(f"Stream {curr_data['name']} is online - adding")
            
                nvstreamer_streams.append(curr_dict)

    return nvstreamer_streams

def get_sensor_mapping_from_nvstreamer():
    logger.info("Prefetching stream info from Nvstreamer")
    nvstreamer_streams = fetch_all_streams_from_nvstreamer()
    logger.info(f"Fetched {len(nvstreamer_streams)} streams from Nvstreamer")
    logger.info(nvstreamer_streams)
    if CONFIG['ENABLE_CALIBRATION_PROCESS']:
        calibration_data = get_calibration_data()
    else:
        calibration_data = None
        logger.debug("Calibration process disabled, generating sensor mapping without calibration data")
    sensor_mapping = SensorMapping.generate(nvstreamer_streams, calibration_data, logger, info_source="nvstreamer")

    return sensor_mapping, nvstreamer_streams

def send_config_to_sdr(sensor_mapping):
    logger.debug(f"Sending config to SDR via {CONFIG['MESSAGE_BROKER_TYPE']} message broker")
    try:
        # Determine topic based on message broker type
        topic = CONFIG['WDM_REDIS_STREAM_NAME'] if CONFIG['MESSAGE_BROKER_TYPE'] == 'redis' else CONFIG['WDM_KFK_TOPIC']
        msg_key=CONFIG['WDM_REDIS_MSG_KEY'] if CONFIG['MESSAGE_BROKER_TYPE'] == 'redis' else CONFIG['WDM_KFK_MSG_KEY']
        logger.debug(f"Message broker config: type={CONFIG['MESSAGE_BROKER_TYPE']}, topic={topic}, msg_key={msg_key}")
        
        # Create message broker instance using factory
        logger.debug("Creating message broker instance using factory")
        message_broker = MessageBrokerFactory.create_message_broker(
            broker_type=CONFIG['MESSAGE_BROKER_TYPE'],
            config=CONFIG
        )
        
        # Send message using the configured broker
        logger.debug(f"Sending {len(sensor_mapping.sensors)} sensor configs to topic/stream {topic}")
        message_broker.send_message(topic=topic, key=msg_key, sensor_mapping=sensor_mapping)
        logger.info(f"Config message sent successfully to {CONFIG['MESSAGE_BROKER_TYPE']} topic/stream {topic}")
        
        # Close the connection
        logger.debug("Closing message broker connection")
        message_broker.close()
    except Exception as e:
        logger.error(f"Error sending config message via {CONFIG['MESSAGE_BROKER_TYPE']}: {e}")
        logger.debug(f"Exception details: {repr(e)}")

def send_nvstreamer_streams_to_redis(nvstreamer_streams):
    # Connect to Redis to send the prefetched VST streams
    try:
        temp_redis_client = redis.StrictRedis(
            host=CONFIG['WDM_REDIS_HOST'],
            port=CONFIG['WDM_REDIS_PORT'],
            db=CONFIG['REDIS_DB'],
            decode_responses=False,
            socket_timeout=10,
            socket_connect_timeout=5,
            retry_on_timeout=True
        )
        
        # Send each VST stream to Redis target topics
        for stream in nvstreamer_streams:
            # Create alert data structure for Redis
            alert_data = {
                "alert_type": "camera_status_change",
                "created_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event": {
                    "camera_id": stream["event"]["camera_id"],
                    "camera_name": stream["event"]["camera_name"],
                    "camera_url": stream["event"]["camera_url"],
                    "change": stream["event"]["change"]
                },
                "source": "vst"
            }
            
            # For each target topic, modify the message and then add it to the stream
            for target_topic in CONFIG['REDIS_TARGET_TOPICS']:
                try:
                    # Make a copy of the alert data to avoid modifying the original
                    modified_alert = copy.deepcopy(alert_data)
                    
                    # Modify the camera_name based on the target topic
                    original_name = modified_alert["event"]["camera_name"]
                    
                    # Add suffix based on target topic
                    if target_topic.endswith('cv'):
                        modified_alert["event"]["camera_name"] = f"{original_name}{CONFIG['CV_SUFFIX']}"
                    elif target_topic.endswith('pn26'):
                        modified_alert["event"]["camera_name"] = f"{original_name}{CONFIG['PN_SUFFIX']}"
                    
                    # Convert to JSON string and then to bytes
                    alert_json_str = json.dumps(modified_alert)
                    
                    # Add the message to Redis with configurable field name as the key
                    redis_field_name = CONFIG['WDM_REDIS_MSG_KEY'].encode('utf-8')
                    temp_redis_client.xadd(target_topic, {redis_field_name: alert_json_str.encode('utf-8')})
                    logger.info(f"Added VST stream to Redis topic {target_topic}: {alert_json_str}")
                except Exception as e:
                    logger.error(f"Error adding VST stream to Redis topic {target_topic}: {e}")
        
        # Close the temporary Redis connection
        temp_redis_client.close()
    except Exception as e:
        logger.error(f"Error sending VST streams to Redis: {e}")

def _run_video_uploads():
    """
    Run NVStreamer and/or VMS video uploads if enabled. Uploads all videos
    in VIDEO_SOURCE_DIR. Sets global video upload status.
    """
    video_source_dir = CONFIG['VIDEO_SOURCE_DIR']
    timeout = CONFIG['VIDEO_UPLOAD_TIMEOUT']
    upload_delay = CONFIG['VIDEO_UPLOAD_DELAY']
    nv_enabled = CONFIG['ENABLE_NVSTREAMER_VIDEO_UPLOAD']
    vms_enabled = CONFIG['ENABLE_VMS_VIDEO_UPLOAD']

    if not nv_enabled and not vms_enabled:
        set_video_upload_status(
            status="disabled",
            message="Video upload is disabled (enable NVStreamer and/or VMS upload)",
            uploaded_count=0,
        )
        return

    if not video_source_dir or not os.path.isdir(video_source_dir):
        logger.warning(f"VIDEO_SOURCE_DIR '{video_source_dir}' is not a valid directory. Skipping video upload.")
        set_video_upload_status(
            status="failed",
            message=f"VIDEO_SOURCE_DIR '{video_source_dir}' is not a valid directory",
            uploaded_count=0,
            error="Invalid video source directory",
        )
        return

    parts = []
    if nv_enabled:
        parts.append("NVStreamer")
    if vms_enabled:
        parts.append("VMS")
    set_video_upload_status(
        status="in_progress",
        message=f"Uploading all videos to {' and '.join(parts)}",
        uploaded_count=0,
    )

    total_uploaded = 0
    all_errors = []

    if nv_enabled:
        try:
            nv_base = CONFIG['NVSTREAMER_UPLOAD_BASE_URL'].rstrip("/")
            upload_endpoint = f"{nv_base}/api/v1/storage/file"
            logger.info("Uploading all videos to NVStreamer base: %s", nv_base)
            nv_count, nv_errors = upload_videos(
                video_source_dir, upload_endpoint, count=None, timeout=timeout,
                upload_delay=upload_delay, logger=logger
            )
            total_uploaded += nv_count
            all_errors.extend(nv_errors)
            logger.info("NVStreamer video upload completed: %d uploaded", nv_count)
        except NVStreamerUploadError as e:
            logger.error("NVStreamer video upload failed: %s", e)
            set_video_upload_status(
                status="failed",
                message="Video upload to NVStreamer failed",
                uploaded_count=total_uploaded,
                error=str(e),
            )
            return
        except Exception as e:
            logger.exception("Unexpected error during NVStreamer video upload")
            set_video_upload_status(
                status="failed",
                message="Video upload to NVStreamer failed",
                uploaded_count=total_uploaded,
                error=str(e),
            )
            return

    if vms_enabled:
        try:
            vms_base = CONFIG['VMS_UPLOAD_BASE_URL']
            logger.info("Uploading all videos to VMS base: %s", vms_base)
            vms_count, vms_errors = upload_videos_to_vms(
                video_source_dir, vms_base, count=None, timeout=timeout,
                upload_delay=upload_delay, logger=logger
            )
            total_uploaded += vms_count
            all_errors.extend(vms_errors)
            logger.info("VMS video upload completed: %d uploaded", vms_count)
        except VMSUploadError as e:
            logger.error("VMS video upload failed: %s", e)
            set_video_upload_status(
                status="failed",
                message="Video upload to VMS failed",
                uploaded_count=total_uploaded,
                error=str(e),
            )
            return
        except Exception as e:
            logger.exception("Unexpected error during VMS video upload")
            set_video_upload_status(
                status="failed",
                message="Video upload to VMS failed",
                uploaded_count=total_uploaded,
                error=str(e),
            )
            return

    if all_errors:
        set_video_upload_status(
            status="completed",
            message=f"Video upload finished with {len(all_errors)} error(s); {total_uploaded} uploaded",
            uploaded_count=total_uploaded,
            error="; ".join(all_errors[:3]) + (" ..." if len(all_errors) > 3 else ""),
        )
    else:
        set_video_upload_status(
            status="completed",
            message=f"All videos uploaded successfully ({total_uploaded} total)",
            uploaded_count=total_uploaded,
        )


def process_sensor_info_from_nvstreamer():
    sensor_mapping, nvstreamer_streams = get_sensor_mapping_from_nvstreamer()

    bev_recompute_enabled = (
        CONFIG['RECOMPUTE_BEV_CENTERS_ENABLED']
        and CONFIG['MODE'] == '3d'
        and CONFIG['ENABLE_CALIBRATION_PROCESS']
        and os.path.exists(CONFIG['CALIBRATION_FILE_PATH'])
    )
    if bev_recompute_enabled:
        logger.info("Recomputing BEV centers")
        sensor_names = [sensor.name for sensor in sensor_mapping.sensors.values()]
        n_sensor_groups = 1 # n_sensor_groups=1 for docker compose since we have only one group.
        max_sensors_per_group = len(sensor_names)
        calib_file_path = recompute_bev_centers(CONFIG['CALIBRATION_FILE_PATH'], sensor_names, n_sensor_groups, max_sensors_per_group)
        logger.info(f"BEV groups recomputed and saved to {calib_file_path}")
    else:
        if CONFIG['RECOMPUTE_BEV_CENTERS_ENABLED'] and CONFIG['MODE'] == '3d':
            if not CONFIG['ENABLE_CALIBRATION_PROCESS']:
                logger.info("Skipping BEV centers recomputation: calibration process is disabled")
            elif not os.path.exists(CONFIG['CALIBRATION_FILE_PATH']):
                logger.info("Skipping BEV centers recomputation: calibration file not found at %s", CONFIG['CALIBRATION_FILE_PATH'])
        else:
            logger.info("Skipping BEV centers recomputation as RECOMPUTE_BEV_CENTERS_ENABLED is disabled")

    if CONFIG['SEND_CONFIG_TO_SDR']:
        send_config_to_sdr(sensor_mapping)
    else:
        logger.info("Skipping config message sending as SEND_CONFIG_TO_SDR is disabled")

    if CONFIG['CALL_SENSOR_ADD_API']:
        logger.info("Calling sensor add API for VMS/RTSP Server")
        [add_sensor(sensor_info) for sensor_info in sensor_mapping.sensors.values()]
        added_count = len(sensor_mapping.sensors)
        expected_count = CONFIG.get('NUM_STREAMS', 0) or 0
        if expected_count > 0 and added_count < expected_count:
            logger.warning(
                f"Called sensor add API for {added_count} sensors but NUM_STREAMS={expected_count}; "
                "cameras may be missing from VST"
            )
        else:
            logger.info(f"Successfully called sensor add API for {added_count} sensors")
    else:
        logger.info("Skipping sensor add API call as CALL_SENSOR_ADD_API is disabled")
    
    if CONFIG['ENABLE_REDIS_DUPLICATOR_THREAD']:
        send_nvstreamer_streams_to_redis(nvstreamer_streams)
        start_redis_duplicator_thread()

def start_redis_duplicator_thread():
    """
    Thread function that reads messages from vst.event Redis Stream and
    duplicates them to vst.event.cv and vst.event.pn26 topics.
    Maintains a consistent connection and only reconnects when needed.
    """
    # Connect to Redis once outside the loop
    redis_client = None
    connected = False
    last_id = '$'  # Start with the most recent message ($ means latest ID in the stream)
    
    # vst_preload()
    
    while True:  # Main loop to ensure the function runs forever
        # Only create a new connection if we're not already connected
        if not connected:
            try:
                # Connect to Redis
                redis_client = redis.StrictRedis(
                    host=CONFIG['WDM_REDIS_HOST'],
                    port=CONFIG['WDM_REDIS_PORT'],
                    db=CONFIG['REDIS_DB'],
                    decode_responses=False,  # Keep binary data as is
                    socket_timeout=10,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                    health_check_interval=30  # Periodically check if connection is alive
                )
                
                # Test the connection
                redis_client.ping()
                
                logger.info(f"Redis event duplicator started. Listening on {CONFIG['REDIS_SOURCE_TOPIC']} stream and duplicating to {', '.join(CONFIG['REDIS_TARGET_TOPICS'])}")
                
                # Mark as connected
                connected = True
            except redis.RedisError as e:
                logger.error(f"Redis connection error in event duplicator thread: {e}")
                # Clean up if connection attempt failed
                if redis_client:
                    try:
                        redis_client.close()
                    except:
                        pass
                redis_client = None
                connected = False
                # Sleep before attempting to reconnect
                logger.info("Redis event duplicator will attempt to reconnect in 5 seconds...")
                time.sleep(5)
                continue
            except Exception as e:
                logger.error(f"Unexpected error in Redis event duplicator thread: {e}")
                # Sleep before attempting to reconnect
                logger.info("Redis event duplicator will attempt to reconnect in 5 seconds...")
                time.sleep(5)
                continue
        
        # Process messages using the established connection
        try:
            # Read from the stream with a block of 1000ms (1 second)
            # Format: {stream_name: last_id}
            streams = {CONFIG['REDIS_SOURCE_TOPIC']: last_id}
            response = redis_client.xread(streams=streams, count=10, block=1000)
            
            # If no messages, continue the loop
            if not response:
                continue
            
            # Process each message from the stream
            for stream_name, messages in response:
                for message_id, message_data in messages:
                    try:
                        # Update last_id to the current message_id for next iteration
                        last_id = message_id
                        
                        logger.debug(f"Received message from stream {stream_name}: {message_id}, {message_data}")
                        logger.info(f"Received Message: {message_data}")
                        
                        # For each target topic, modify the message and then add it to the stream
                        for target_topic in CONFIG['REDIS_TARGET_TOPICS']:
                            try:
                                # Make a copy of the message data to avoid modifying the original
                                modified_data = message_data.copy()
                                
                                # The message has a different structure than expected
                                # Check for the configurable Redis message field key which contains the JSON data
                                redis_field_name = CONFIG['WDM_REDIS_MSG_KEY'].encode('utf-8')
                                if redis_field_name in modified_data and isinstance(modified_data[redis_field_name], bytes):
                                    # Decode the JSON string from bytes
                                    data_str = modified_data[redis_field_name].decode('utf-8')
                                    # Remove trailing newline if present
                                    data_str = data_str.strip()
                                    data_json = json.loads(data_str)
                                    
                                    # Modify the camera_name based on the target topic
                                    if 'event' in data_json and 'camera_name' in data_json['event']:
                                        original_name = data_json['event']['camera_name']
                                        
                                        # Add suffix based on target topic
                                        if target_topic.endswith('cv'):
                                            data_json['event']['camera_name'] = f"{original_name}{CONFIG['CV_SUFFIX']}"
                                        elif target_topic.endswith('pn26'):
                                            data_json['event']['camera_name'] = f"{original_name}{CONFIG['PN_SUFFIX']}"
                                    
                                    # Convert back to JSON string and then to bytes
                                    # Store with the configurable key
                                    modified_data[redis_field_name] = json.dumps(data_json).encode('utf-8')
                                else:
                                    # Handle case where data might be directly in the message (not in bytes)
                                    # This is a fallback, but the primary format should be bytes
                                    logger.warning(f"Unexpected message format: {modified_data}")
                                
                                # Add the modified message to the target stream
                                redis_client.xadd(target_topic, modified_data)
                            except json.JSONDecodeError as e:
                                logger.error(f"Error decoding JSON in message: {e}")
                                # If we can't decode JSON, still try to forward the original message
                                redis_client.xadd(target_topic, message_data)
                            except Exception as e:
                                logger.error(f"Error modifying message for {target_topic}: {e}")
                                # If modification fails, still try to forward the original message
                                redis_client.xadd(target_topic, message_data)
                        
                        logger.debug(f"Duplicated message from {CONFIG['REDIS_SOURCE_TOPIC']} to {', '.join(CONFIG['REDIS_TARGET_TOPICS'])}")
                    except Exception as e:
                        logger.error(f"Error processing Redis stream message: {e}")
                        # Continue processing other messages
                        continue
        except redis.RedisError as e:
            logger.error(f"Redis connection lost: {e}")
            # Mark as disconnected so we'll reconnect on the next iteration
            connected = False
            # Clean up the broken connection
            if redis_client:
                try:
                    redis_client.close()
                except:
                    pass
            redis_client = None
            # Sleep before attempting to reconnect
            logger.info("Redis event duplicator will attempt to reconnect in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error while processing messages: {e}")
            # Continue the loop, but don't disconnect unless it's a Redis error

# def start_redis_duplicator_thread():
#     """
#     Starts the Redis event duplicator thread.
#     """
#     thread = threading.Thread(target=redis_event_duplicator)
#     thread.daemon = True
#     thread.start()
#     logger.info("Started Redis event duplicator thread")

# NEW: Helper function to check if calibration is enabled
def calibration_enabled_required():
    if not CONFIG['ENABLE_CALIBRATION_PROCESS']:
        return jsonify({
            "error": "Calibration process is disabled",
            "message": "This endpoint is not available when ENABLE_CALIBRATION_PROCESS=false"
        }), 503
    return None

@app.route('/calibration', methods=['POST'])
def add_calibration_data():
    logger.info("Received POST request to /calibration endpoint")
    
    error_response = calibration_enabled_required()
    if error_response:
        logger.warning("Calibration process is disabled, cannot upload calibration file")
        return error_response
    
    calibration_mode = CONFIG['CALIBRATION_MODE']
    if calibration_mode == "fetch":
        logger.warning("Calibration upload endpoint called but CALIBRATION_MODE is set to 'fetch'. Calibration data will be fetched from API endpoint instead.")
        return jsonify({
            "status": "warning", 
            "message": f"Calibration mode is set to 'fetch'. Data will be fetched from {CONFIG['CALIBRATION_API_ENDPOINT']}. Upload ignored."
        }), 200
    
    calibration_data = request.json
    try:
        if calibration_data:
            with open(CONFIG['CALIBRATION_FILE_PATH'], 'w') as json_file:
                json.dump(calibration_data, json_file)
            logger.info(f"Successfully saved calibration file at {CONFIG['CALIBRATION_FILE_PATH']}")
            return jsonify({"status": "success", "message": "Calibration File added"}), 200
        else:
            logger.exception(f"No calibration data provided")
            return jsonify({"status": "error", "message": "No calibration data provided"}), 400
    except Exception as e:
        logger.exception(f"Error saving calibration file at {CONFIG['CALIBRATION_FILE_PATH']}")
        return jsonify({"status": "error", "message": "Unable to save calibration file"}), 400

@app.route('/download', methods=['GET'])
def download_calibration_file():
    logger.info("Received GET request to /download endpoint")
    # Check if calibration process is enabled
    error_response = calibration_enabled_required()
    if error_response:
        logger.warning("Calibration process is disabled, cannot download calibration file")
        return error_response
        
    if not os.path.exists(CONFIG['CALIBRATION_FILE_PATH']):
        logger.warning("Calibration file not found")
        return jsonify({"error": "No calibration file available"}), 404
    try:
        return send_file(
            CONFIG['CALIBRATION_FILE_PATH'],
            as_attachment=True,
            mimetype='application/json',
            download_name=CONFIG['CALIBRATION_FILE_NAME']
        )
    except Exception as e:
        logger.exception("Error sending calibration file")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/cameras', methods=['GET'])
def get_sensor_names():
    logger.info("Received GET request to /cameras endpoint")
    # # Check if calibration process is enabled
    # error_response = calibration_enabled_required()
    # if error_response:
    #     logger.warning("Calibration process is disabled, cannot retrieve sensor list")
    #     return error_response
        
    try:
        if sensor_mapping is None:
            logger.warning("Sensor mapping not initialized yet")
            return jsonify({"error": "Sensor mapping not created yet. Please wait till valid calibration file is added & successfully processed."}), 503
            
        sensor_list = sensor_mapping.get_sensor_names()
        return jsonify(sensor_list), 200
    except Exception as e:
        logger.exception(f"Error retrieving sensor list.")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/groups', methods=['GET'])
def get_group_names():
    logger.info("Received GET request to /groups endpoint")
    # Check if calibration process is enabled
    error_response = calibration_enabled_required()
    if error_response:
        logger.warning("Calibration process is disabled, cannot retrieve group info")
        return error_response
        
    try:
        if sensor_mapping is None:
            logger.warning("Sensor mapping not initialized yet")
            return jsonify({"error": "Sensor mapping not created yet. Please wait till valid calibration file is added & successfully processed."}), 503
            
        sensor_list = sensor_mapping.get_group_names()
        return jsonify(sensor_list), 200
    except Exception as e:
        logger.exception(f"Error retrieving sensor list.")
        return jsonify({"error": "Internal server error"}), 500

# Readiness marker file path - must match the path in profile_config_manager.py
PROFILE_CONFIG_READY_FILE = os.environ.get(
    'PROFILE_CONFIG_READY_FILE', 
    '/tmp/profile_config_ready'
)


def is_profile_config_ready() -> bool:
    """
    Check if profile configuration has completed successfully.
    
    Returns:
        True if profile configuration is complete, False otherwise
    """
    return os.path.exists(PROFILE_CONFIG_READY_FILE)


@app.route('/healthz', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200


@app.route('/readyz', methods=['GET'])
def readiness_check():
    """
    Readiness endpoint that returns success only when profile configuration is complete.
    
    Returns:
        HTTP 200 with {"status": "ready"} if profile configuration completed successfully
        HTTP 503 with {"status": "not_ready"} if profile configuration is pending or failed
    """
    logger.info("Received GET request to /readyz endpoint")
    
    # Check if profile configurator is enabled
    enable_profile_configurator = os.environ.get('ENABLE_PROFILE_CONFIGURATOR', 'false').lower() == 'true'
    
    if not enable_profile_configurator:
        # If profile configurator is disabled, always return ready
        logger.debug("Profile configurator is disabled, returning ready")
        return jsonify({
            "status": "ready",
            "message": "Profile configurator is disabled, service is ready"
        }), 200
    
    # Check for the readiness marker file
    if is_profile_config_ready():
        logger.info("Profile configuration is complete, returning ready")
        return jsonify({
            "status": "ready",
            "message": "Profile configuration completed successfully"
        }), 200
    else:
        logger.info("Profile configuration not yet complete, returning not ready")
        return jsonify({
            "status": "not_ready",
            "message": "Profile configuration is pending or failed"
        }), 503


@app.route('/video-upload-status', methods=['GET'])
def video_upload_status():
    """
    Video upload status endpoint that returns the current status of video upload
    (NVStreamer and/or VMS).
    
    This endpoint is designed to be polled by init containers to wait until all videos
    are uploaded successfully before proceeding.
    
    Returns:
        HTTP 200 with {"status": "completed", ...} if video upload completed successfully
        HTTP 503 with {"status": "in_progress|not_started", ...} if video upload is pending
        HTTP 503 with {"status": "failed", ...} if video upload failed
        HTTP 404 with {"status": "disabled", ...} if video upload feature is not enabled
    """
    logger.info("Received GET request to /video-upload-status endpoint")
    
    video_upload_enabled = CONFIG['ENABLE_NVSTREAMER_VIDEO_UPLOAD'] or CONFIG['ENABLE_VMS_VIDEO_UPLOAD']
    if not video_upload_enabled:
        logger.debug("Video upload is disabled, returning 404")
        return jsonify({
            "status": "disabled",
            "message": "Video upload is not enabled (ENABLE_NVSTREAMER_VIDEO_UPLOAD and ENABLE_VMS_VIDEO_UPLOAD=false)",
            "uploaded_count": 0,
            "ready": False
        }), 404
    
    # Get current video upload status
    status = get_video_upload_status()
    
    if status["status"] == "completed":
        logger.info("Video upload completed successfully, returning ready")
        return jsonify({
            "status": status["status"],
            "message": status["message"],
            "uploaded_count": status["uploaded_count"],
            "ready": True
        }), 200
    elif status["status"] == "failed":
        logger.warning(f"Video upload failed: {status['error']}")
        return jsonify({
            "status": status["status"],
            "message": status["message"],
            "uploaded_count": status["uploaded_count"],
            "error": status["error"],
            "ready": False
        }), 503
    else:
        # Status is "in_progress" or "not_started"
        logger.info(f"Video upload status: {status['status']}")
        return jsonify({
            "status": status["status"],
            "message": status["message"],
            "uploaded_count": status["uploaded_count"],
            "ready": False
        }), 503


# if __name__ == '__main__':
#     # Log calibration configuration
#     logger.info("=== VSS Configurator Starting ===")
#     logger.info(f"Calibration Mode: {CONFIG['CALIBRATION_MODE']}")
#     logger.info(f"Calibration File Path: {CONFIG['CALIBRATION_FILE_PATH']}")
    
#     if CONFIG['CALIBRATION_MODE'] == "fetch":
#         logger.info(f"Calibration will be fetched from API Endpoint: {CONFIG['CALIBRATION_API_ENDPOINT']}")
#         logger.info(f"Calibration API Timeout: {CONFIG['CALIBRATION_API_TIMEOUT']}s")
#         if not CONFIG['CALIBRATION_API_ENDPOINT']:
#             logger.error("WARNING: CALIBRATION_MODE is 'fetch' but CALIBRATION_API_ENDPOINT is not configured!")
#     elif CONFIG['CALIBRATION_MODE'] == "upload":
#         logger.info("Calibration will be accepted via POST /calibration endpoint")
#     else:
#         logger.error(f"Invalid CALIBRATION_MODE: '{CONFIG['CALIBRATION_MODE']}'. Must be 'fetch' or 'upload'")
    
#     if CONFIG['ENABLE_PROFILE_CONFIGURATOR']:
#         logger.info("Profile configurator is enabled")
#         from profile_configurator.profile_config_manager.py import main
#         main()
#     else:
#         logger.info("Profile configurator is disabled")

#     # Start the sensor mapping thread if enabled
#     print(f"ENABLE_SENSOR_MAPPING_THREAD: {CONFIG['ENABLE_SENSOR_MAPPING_THREAD']}, ENABLE_REDIS_DUPLICATOR_THREAD: {CONFIG['ENABLE_REDIS_DUPLICATOR_THREAD']}")
#     if CONFIG['ENABLE_SENSOR_MAPPING_THREAD']:
#         print("Starting sensor mapping thread (enabled by configuration)")
#         background_thread = threading.Thread(target=start_background_thread)
#         background_thread.daemon = True
#         background_thread.start()
#     else:
#         if not CONFIG['ENABLE_CALIBRATION_PROCESS']:
#             print("Sensor mapping thread is disabled because calibration process is disabled")
#         elif not CONFIG['ENABLE_SENSOR_MAPPING_THREAD']:
#             print("Sensor mapping thread is disabled by configuration")
    
#     # Start the Redis event duplicator thread if enabled
#     if CONFIG['ENABLE_REDIS_DUPLICATOR_THREAD']:
#         print("Starting Redis event duplicator thread (enabled by configuration)")
#         start_redis_duplicator_thread()
#     else:   
#         print("Redis event duplicator thread is disabled by configuration")
    
#     # Start the Flask application
#     app.run(host='0.0.0.0', port=CONFIG['PORT'])

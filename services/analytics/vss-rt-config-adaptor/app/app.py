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

# from datetime import datetime
from flask import Flask, request, after_this_request
from simple_settings import LazySettings
import logging
from logging.handlers import RotatingFileHandler
import sys
# import yaml
from ruamel.yaml import YAML
from typing import Dict, Any
import os
import threading
import time

settings = LazySettings("config")
app = Flask(__name__)
s = settings.Config()
app.config.from_object(s)


stdout_handler = logging.StreamHandler(stream=sys.stdout)

logging.basicConfig(
    format="%(asctime)s %(name)s - %(levelname)s - %(message)s",
    level="INFO",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        RotatingFileHandler(
            filename="/tmp/vss-rt-config-adaptor.log", maxBytes=200000, backupCount=2
        ),
        stdout_handler,
    ],
)

yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False
yaml.width = 4096  # Set a high line width to prevent line folding
yaml.boolean_representation = ['False', 'True']  # Preserve Python-style booleans

logger = logging.getLogger(__name__)


def read_yaml_file(file_path: str) -> Dict[str, Any]:
    """
    Read and parse a YAML file.

    Args:
        file_path: Path to the YAML file

    Returns:
        Dictionary containing the parsed YAML content

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the YAML is invalid
    """
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"YAML file not found: {file_path}")

        # with open(file_path, 'r', encoding='utf-8') as f:
        #     content = yaml.safe_load(f)

        with open(file_path, 'r') as f:
            content = yaml.load(f)

        logger.debug(f"Successfully read YAML file: {file_path}")
        return content

    except Exception as e:
        logger.error(f"Failed to read YAML file {file_path}: {e}")
        raise

def write_yaml_file(file_path: str, data: Dict[str, Any]) -> bool:
    """
    Write data to a YAML file.

    Args:
        file_path: Path to the YAML file
        data: Dictionary to write as YAML

    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)

        logger.info(f"Successfully wrote YAML file: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to write YAML file {file_path}: {e}")
        return False

@app.route("/config", methods=["POST"])
def vss_rt_config_adaptor():
    content_type = request.headers.get('Content-Type')
    if content_type != 'application/json':
        return "JSON input is required for this endpoint", 400

    # Schedule shutdown after response is sent
    @after_this_request
    def shutdown_after_response(response):
        def graceful_exit():
            time.sleep(0.5)  # Small delay to ensure response is fully flushed
            app.logger.info("Response sent. Shutting down vss-rt-config-adaptor application...")
            os._exit(0)  # Exit with success code 0

        shutdown_thread = threading.Thread(target=graceful_exit)
        shutdown_thread.daemon = True
        shutdown_thread.start()
        return response

    #data_path = "/tmp/data/vss-rt-config-adaptor/config.csv"
    data_path = app.config["DS_CONFIG_PATH"]
    config_yaml_source= app.config["DS_CONFIG_YAML_SOURCE_PATH"]
    config_yaml_target = app.config["DS_CONFIG_YAML_TARGET_PATH"]
    event_field = app.config["EVENT_OBJECT_FIELD"]
    metadata_field = app.config["METADATA_OBJECT_FIELD"]
    calib_file_path = app.config["CALIB_FILE_PATH"]

    try:
        data_json = request.get_json()
        event_data = data_json[event_field]
        if event_data is not None:
            metadata_data = event_data[metadata_field]
            if metadata_data is not None:
                region = metadata_data["region"]
                group = metadata_data["group"]
                topic_prefix = metadata_data["topic-prefix"]
                with open(data_path, "w") as f:
                    f.write("{0},{1},{2}".format (region, group, topic_prefix))
                app.logger.info("csv file generated")
    except Exception as e:
        app.logger.info(f"config file could not be written to volume mount: {e}")

    try:
        # with open(config_yaml_source, 'r') as file:
        #     data = yaml.safe_load(file)
        data = read_yaml_file(config_yaml_source)
        data['calib_file_path'] = calib_file_path
        data['bev_group_name'] = group
        logger.info(f"Updated calib_file_path: {data['calib_file_path']} and bev_group_name: {data['bev_group_name']} in config.yaml file")
        # with open(config_yaml_target, 'w') as file:
        #     yaml.dump(data, file, sort_keys=False)
        write_yaml_file(config_yaml_target, data)
        app.logger.info("config.yaml file updated")
    except Exception as e:
        app.logger.info(f"config.yaml could not be updated: {e}")
    return data_json


if __name__ == "__main__":
    app_port = app.config["PORT"]
    app.logger.info(f"application start on port {app_port}")
    app.run(host="0.0.0.0", port=app_port, use_reloader=False)

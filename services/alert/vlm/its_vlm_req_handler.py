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

import asyncio
import logging
import pandas as pd
import base64
import aiohttp
from models.sampling_entity import SamplingEntity
import requests
import json
import time  # Add this import at the top of your file


import sys  # Added for consistent error handling

# Configure centralized logging from config.yaml
from utils.logging_config import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)  # Ensure all debug logs are captured


class ITS_VLM_HANDLER:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.vlm_agent_base_url = config['vlm_agent']['base_url']
        self.vlm_agent_endpoint = config['vlm_agent']['endpoint']
        self.rate_limit = config.get('vlm_agent', {}).get(
            'rate_limit', 1)  # Default to sequential
        self.vss_handler = None  # Will be set after initialization

    def invoke_vlm_agent(self, entities_with_rtsp_urls):
        """
        Invokes the VLM agent for each entity with an RTSP URL.

        Args:
            entities_with_rtsp_urls (list): List of entities with constructed RTSP URLs.

        Returns:
            list: List of responses from the VLM agent.
        """

        temp = asyncio.run(
            self._invoke_vlm_agent_async(entities_with_rtsp_urls))
        return temp

    async def _invoke_vlm_agent_async(self, entities_with_rtsp_urls):
        """
        Async implementation to invoke the VLM agent for each entity with an RTSP URL.

        Args:
            entities_with_rtsp_urls (list): List of entities with constructed RTSP URLs.

        Returns:
            list: List of responses from the VLM agent.
        """
        tasks = []
        semaphore = asyncio.Semaphore(self.rate_limit)  # Rate control

        async with aiohttp.ClientSession() as session:
            for entity in entities_with_rtsp_urls:
                task = self._send_vlm_agent_request(entity, session, semaphore)
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.logger.info("All VLM Agent requests processed.")
            return results

    async def _send_vlm_agent_request(self, entity, session, semaphore):
        """
        Sends a single request to the VLM agent for the given entity.

        Args:
            entity (dict): An alert entity with RTSP URL.
            session (aiohttp.ClientSession): The HTTP client session.
            semaphore (asyncio.Semaphore): Semaphore for rate control.

        Returns:
            dict: Processed response or error message.
        """
        async with semaphore:
            try:
                if "rtsp_url" not in entity:
                    return entity

                # Log request details
                self.logger.info(f"VLM Request [ts={entity.get('timestamp', 'N/A')} sensor={entity.get('sensor_id', 'N/A')} vehicle={entity.get('vehicle_id', 'N/A')} anomaly={entity.get('anomaly_type', 'N/A')}]")

                payload = self._build_vlm_request_payload(entity)
                url = f"{self.vlm_agent_base_url}{self.vlm_agent_endpoint}"
               
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()
                    result = await response.json()
                    # Log response details
                    self.logger.info(f"VLM Response [ts={entity.get('timestamp', 'N/A')} sensor={entity.get('sensor_id', 'N/A')} vehicle={entity.get('vehicle_id', 'N/A')} anomaly={entity.get('anomaly_type', 'N/A')}]")
                    self._process_vlm_response(entity, result)
                    return entity
            except Exception as e:
                self.logger.error(f"VLM Error [ts={entity.get('timestamp', 'N/A')} sensor={entity.get('sensor_id', 'N/A')} vehicle={entity.get('vehicle_id', 'N/A')} anomaly={entity.get('anomaly_type', 'N/A')}] error={str(e)}")
                return entity

    def _build_vlm_request_payload(self, entity):
        """
        Builds the payload for the VLM agent HTTP request dynamically based on anomaly type.

        Args:
            entity (dict): An alert entity with RTSP URL.

        Returns:
            dict: JSON payload for the VLM agent.
        """
        anomaly_type_mapping = {
            "Movement Anomaly Module": "wrong_way_prompt",
            "Stop Anomaly Module": "stalled_vehicle_prompt",
            "Speed Anomaly Module": "speeding_vehicle_prompt"
        }

        anomaly_type = entity.get("anomaly_type")
        rtsp_url = entity.get("rtsp_url")

        # Get the corresponding prompt key from the mapping
        prompt_key = anomaly_type_mapping.get(anomaly_type, None)

        prompt = self.config['vlm_agent'].get(prompt_key, "Do you see any dangerous driving conditions on the road?.") if prompt_key else "Do you see any dangerous driving conditions on the road?."

        # Update payload to match the specified format
        payload = {
            "input_message": [
                {"type": "text/plain", "content": prompt},
                {"type": "video/rtsp", "content": rtsp_url}
            ]
        }

        return payload

    def _process_vlm_response(self, entity, response):
        """
        Processes the VLM response and updates the entity.

        Args:
            entity (dict): The original alert entity.
            response (dict): The response from the VLM agent.
        """
        entity['vlm_response'] = response
        self.logger.info(
            f"Processed VLM response for entity {entity['sensor_id']}: {response}")

    def invoke_vlm_agent_for_image_eval(self, entities_df: pd.DataFrame) -> pd.DataFrame:
        """
        Invokes VLM agent for each sampling entity.
        
        Args:
            entities_df: DataFrame containing sampling entities with images
            
        Returns:
            DataFrame with VLM responses added
        """
        try:
            # Create copy to avoid modifying original
            result_df = entities_df.copy()
            
            # Ensure 'vlmResponse' column exists
            if 'vlmResponse' not in result_df.columns:
                result_df['vlmResponse'] = None
         
            for idx, entity in result_df.iterrows():
                try:
                    # Skip if missing required data
                    if not entity.prompt or not entity.sampledImage:
                        self.logger.warning(
                            f"Missing prompt or image for sensor {entity.sensorName}"
                        )
                        continue
                        
                    # Build and send request
                    request_payload = self._build_vlm_request_payload_from_sampling(entity, entity.sampledImage)
                    vlm_response = self._send_vlm_request(request_payload)
                    
                    # Store response
                    result_df.at[idx, 'vlmResponse'] = vlm_response
                    self.logger.info(
                        f"Got VLM response for sensor {entity.sensorName}"
                    )
                    
                except Exception as e:
                    self.logger.error(
                        f"Error processing VLM request for sensor {entity.sensorName}: {e}",
                        exc_info=True
                    )
                
             
            return result_df
            
        except Exception as e:
            self.logger.error(f"Error in invoke_vlm_agent_for_image_eval: {e}", exc_info=True)
            return pd.DataFrame()

    def _build_vlm_request_payload_from_sampling(self, entity, image_data):
        """Build VLM request payload from sampling data"""
        try:
            
            # Convert image data to base64
            image_b64 = base64.b64encode(image_data).decode('utf-8')
            
            # Build payload with 'input_message' key
            payload = {
                "input_message": [
                    {
                        "type": "text/plain", 
                         "content": entity.prompt
                    },
                    {
                        "type": "image/jpeg",
                        "content": image_b64
                    }
                ]
            }
            
            self.logger.debug(f"Built VLM request payload for sensor {entity.sensorName}")
            return payload
        
        except Exception as e:
            self.logger.error(
                f"Error building VLM request payload for sensor {entity.sensorName}: {e}",
                exc_info=True
            )
            raise

    def _send_vlm_request(self, payload):
        """Send request to VLM API"""
        try:
            # Log the request payload
            self.logger.info("Sending VLM request with payload:")
            #self.logger.info(json.dumps(payload, indent=2))

            # Build request URL using correct config path
            base_url = self.config['vlm_agent']['base_url']
            endpoint = self.config['vlm_agent']['endpoint']
            url = f"{base_url.rstrip('/')}{endpoint}"

            # Add Content-Type header
            headers = {
                "Content-Type": "application/json"
            }

            # Make request with headers
            response = requests.post(url, json=payload, headers=headers)

            # Log the response
            self.logger.info(f"VLM Response status: {response.status_code}")
            self.logger.info(f"VLM Response body: {response.text}")

            response.raise_for_status()
            return response.json()

        except Exception as e:
            self.logger.error(f"Error sending VLM request: {e}")
            raise

    def set_vss_handler(self, vss_handler):
        """
        Set the VSS handler instance for processing objects.
        
        Args:
            vss_handler: An instance of ITS_VSS_HANDLER
        """
        self.vss_handler = vss_handler
        self.logger.info("VSS handler has been set.")
        
        # Initialize the VSS handler synchronously
        self.logger.info("Initializing VSS handler (will retry up to 3 times)...")
        self.vss_handler.initialize()
        self.logger.info("VSS handler initialization completed")

    def invoke_vss_agent(self, vss_input):
        """
        Invokes the VSS agent for each entity with an overlay image path.
        
        Args:
            vss_input (list): List of entities with overlay_image_path.
            
        Returns:
            list: List of responses from the VSS agent.
        """
        # Process entities one by one using synchronous VSS handler
        results = []
        for entity in vss_input:
            try:
                result = self._process_vss_entity(entity)
                if result:
                    results.append(result)
            except Exception as e:
                self.logger.error(f"Error processing VSS entity: {str(e)}")
                results.append(entity)
                
        self.logger.info("All VSS Agent requests processed.")
        return results
    
    def _filter_vss_response(self, entity, result):
        response = result.get('response')
        vehicle_id = entity.get('vehicle_id', 'N/A')
        sensor_id = entity.get('sensor_id', 'N/A')
        timestamp = entity.get('start', 'N/A')

        if response and 'Yes' in response:
            self.logger.info(f"Anomaly for Vehicle ID: {vehicle_id} | Sensor: {sensor_id} | Time: {timestamp}")
            self.logger.info(f"VSS Response: {response}")
            self.logger.info("Anomaly is dropped based on VSS analysis")
            entity['dropped'] = True
        else:
            self.logger.info(f"Anomaly for Vehicle ID: {vehicle_id} | Sensor: {sensor_id} | Time: {timestamp}")
            self.logger.info(f"VSS Response: {response}")
            self.logger.info("Anomaly is not dropped based on VSS analysis")
            entity['dropped'] = False
       
        return entity
    
    def _format_vss_response(self, entity):
        return {
            'sensor_id': entity['sensor_id'],
            'start': entity['start'],
            'end': entity['end'],
            'anomaly_type': entity['anomaly_type'],
            'vehicle_id': entity['object']['id'],
            'original_anomaly': entity['original_anomaly'],
            'overlay_image_path': entity['overlay_image_path'],
        }
    
    def _process_vss_entity(self, entity):
        """Process a single entity with the VSS handler."""
        try:
            overlay_image_path = entity.get('overlay_image_path', '')
            
            # Check if path exists
            if not overlay_image_path:
                self.logger.error(f"No overlay_image_path provided for entity {entity.get('sensor_id', 'unknown')}")
                return entity
            
            # Call VSS agent synchronously
            result = self.vss_handler.invoke_vss_agent(entity, overlay_image_path)
            self._process_vlm_response(entity, result)
            self._format_vss_response(entity)
            return self._filter_vss_response(entity, result)
        except Exception as e:
            self.logger.error(f"VSS Error [ts={entity.get('timestamp', 'N/A')} sensor={entity.get('sensor_id', 'N/A')} vehicle={entity.get('vehicle_id', 'N/A')} anomaly={entity.get('anomaly_type', 'N/A')}] error={str(e)}")
            return entity
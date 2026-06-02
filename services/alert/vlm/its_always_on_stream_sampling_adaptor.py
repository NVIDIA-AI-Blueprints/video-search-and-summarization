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

import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from itertools import groupby
from operator import attrgetter
import pandas as pd

from models.sampling_entity import SamplingEntity

class ITSAlwaysOnStreamSamplingAdaptor:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        # Get sampling interval from config, default to 5 minutes if not specified
        self.sampling_interval = timedelta(
            minutes=self.config.get('sampling_config', {}).get('sampling_interval_minutes', 5)
        )

    def filter_sampling_entities(self, entities: List[SamplingEntity]) -> List[SamplingEntity]:
        """
        Filters sampling entities based on multiple criteria.
        
        Args:
            entities: List of SamplingEntity instances
            
        Returns:
            Filtered list of SamplingEntity instances
        """
        try:
            if not entities:
                return []

            # Apply filters in sequence
            time_filtered = self._filter_by_timestamp(entities)
            if not time_filtered:
                return []

            latest_filtered = self._filter_latest_by_sensor(time_filtered)
            
            self.logger.info(
                f"Filtered {len(entities)} entities to {len(latest_filtered)} "
                f"(removed {len(entities) - len(latest_filtered)} duplicates/old samples)"
            )

            return latest_filtered

        except Exception as e:
            self.logger.error(f"Error filtering sampling entities: {e}", exc_info=True)
            return []

    def _filter_by_timestamp(self, entities: List[SamplingEntity]) -> List[SamplingEntity]:
        """
        Filters out entities older than the sampling interval.
        
        Args:
            entities: List of SamplingEntity instances
            
        Returns:
            List of entities within the sampling interval
        """
        current_time = datetime.utcnow()
        cutoff_time = current_time - self.sampling_interval

        filtered = [
            entity for entity in entities 
            if entity.timestamp >= cutoff_time
        ]

        if not filtered:
            self.logger.info("All entities filtered out due to age")
        else:
            self.logger.debug(f"Timestamp filter: {len(entities)} -> {len(filtered)} entities")

        return filtered

    def _filter_latest_by_sensor(self, entities: List[SamplingEntity]) -> List[SamplingEntity]:
        """
        Keeps only the latest entity for each sensor_name.
        
        Args:
            entities: List of SamplingEntity instances
            
        Returns:
            List of latest entities per sensor
        """
        # Sort by sensor_name and timestamp
        sorted_entities = sorted(
            entities,
            key=attrgetter('sensor_name', 'timestamp'),
            reverse=True  # Latest first
        )

        # Group by sensor_name and take the latest timestamp for each
        latest_by_sensor = {}
        for entity in sorted_entities:
            if entity.sensor_name not in latest_by_sensor:
                latest_by_sensor[entity.sensor_name] = entity

        filtered = list(latest_by_sensor.values())
        self.logger.debug(f"Latest by sensor filter: {len(entities)} -> {len(filtered)} entities")
        
        return filtered

    def extract_entities_from_samples(self, samples: List[tuple]) -> pd.DataFrame:
        """
        Extracts sampling entities from a list of sampling messages and converts to DataFrame.
        
        Args:
            samples: List of sampling message tuples (key, value)
                where key is sensorName and value is JSON message
            
        Returns:
            DataFrame containing SamplingEntity data
        """
        try:
            entities = []
            for key_bytes, value_bytes in samples:
                try:
                    # Decode and parse JSON from value
                    value_str = value_bytes.decode('utf-8')
                    sample_data = json.loads(value_str)
                    self.logger.debug(f"Processing sample data: {sample_data}")
                    
                    entity = SamplingEntity.from_dict(sample_data)
                    entities.append(entity)
                    self.logger.info(
                        f"Created entity -> Sensor: {entity.sensorName} ({entity.timeStamp})")
                except Exception as e:
                    self.logger.error(f"Error processing sample: {e}")
                    self.logger.debug(f"Failed sample value: {value_bytes}")
            
            if not entities:
                return pd.DataFrame()
            
            entities_df = pd.DataFrame([entity.__dict__ for entity in entities])
            self.logger.debug(f"Created DataFrame with {len(entities)} entities")
            return entities_df
        
        except Exception as e:
            self.logger.error(f"Error in extract_entities_from_samples: {e}", exc_info=True)
            return pd.DataFrame()

    def filter_scenarios_detected(self, entities_df: pd.DataFrame) -> pd.DataFrame:
        """
        Filters sampling entities where VLM detected a scenario.
        
        Args:
            entities_df: DataFrame containing SamplingEntity instances with VLM responses
            
        Returns:
            DataFrame containing only entities with positive VLM detections
        """
        try:
            # Create copy to avoid modifying original
            result_df = entities_df.copy()
            
            # Filter entities based on VLM response
            detected_scenarios = result_df[
                result_df.apply(lambda row: self._is_scenario_detected(row.vlmResponse), axis=1)
            ]
            
            # Log statistics
            self._log_vlm_response_statistics(entities_df, detected_scenarios)
            
            return detected_scenarios

        except Exception as e:
            self.logger.error(f"Error filtering scenarios detected: {e}", exc_info=True)
            return pd.DataFrame()

    def _is_scenario_detected(self, vlm_response: Dict[str, Any]) -> bool:
        """
        Determines if a scenario is detected from the VLM response.

        Args:
            vlm_response (dict): The response from the VLM agent.

        Returns:
            bool: True if a scenario is detected, False otherwise.
        """
        try:
            # Check if vlm_response is None
            if vlm_response is None:
                self.logger.error("VLM response is None.")
                return False

            # Navigate through the nested structure
            result = vlm_response.get('value', {}).get('result', {})
            response_list = result.get('response', [])

            if not response_list:
                self.logger.debug("VLM response is missing or does not contain 'response' key.")
                return False

            # Check each response for scenario detection
            for response in response_list:
                metadata = response.get('metadata', {})
                self.logger.debug(f"Metadata extracted: {metadata}")
                scenario_detected = metadata.get('scenario_detected', False)
                if scenario_detected:
                    return True

            return False

        except Exception as e:
            self.logger.error(f"Error processing VLM response: {e}", exc_info=True)
            return False

    def _get_response_details(self, vlm_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts relevant details from VLM response.
        
        Args:
            vlm_response: VLM response dictionary from SamplingEntity
            
        Returns:
            Dictionary containing confidence and description
        """
        try:
            if not vlm_response or 'response' not in vlm_response:
                return {}

            response = vlm_response['response'][0]
            metadata = response.get('metadata', {})
            
            return {
                'confidence': metadata.get('confidence', 'N/A'),
                'description': response.get('content', 'No description available'),
                'scenario_detected': metadata.get('scenario_detected', False)
            }

        except Exception as e:
            self.logger.error(f"Error extracting response details: {e}", exc_info=True)
            return {}

    def _log_vlm_response_statistics(self, all_entities_df: pd.DataFrame, detected_df: pd.DataFrame) -> None:
        """
        Logs statistics about VLM responses.
        """
        try:
            total = len(all_entities_df)
            detected = len(detected_df)
            detection_rate = (detected / total * 100) if total > 0 else 0
            
            self.logger.info(
                f"VLM Response Statistics:\n"
                f"Total entities processed: {total}\n"
                f"Scenarios detected: {detected}\n"
                f"Detection rate: {detection_rate:.2f}%"
            )
            
            # Log details for detected scenarios
            for _, entity in detected_df.iterrows():
                details = self._get_response_details(entity.vlmResponse)  # Changed from vlm_response
                self.logger.debug(f"Detected scenario details: {details}")
                
        except Exception as e:
            self.logger.error(f"Error logging VLM statistics: {e}")


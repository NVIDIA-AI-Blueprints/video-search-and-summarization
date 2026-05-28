# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import logging
import os
import requests
import json

from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.models import AmrState, RoiState
from mdx.analytics.core.utils.schema_util import group_frames_by_sensor_id
from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtEWithTripwire
from mdx.analytics.core.stream.state.amr.amr_state_management import AmrStateMgmt
from mdx.analytics.core.utils.processing_stats import BatchStats

logger = logging.getLogger(__name__)


class MuteUnmuteApp(BaseApp):

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:

        super().__init__(config, calibration_path)
        self.state_mgmt = StateMgmtEWithTripwire(self.config, self.calibration)
        self.amr_state_mgmt = AmrStateMgmt(self.config, self.calibration)  # type: ignore
        
        # Get API configuration from environment variable or config, with default fallback
        base_url = os.getenv("ROBOT_API_BASE_URL", 
                            self.config.get_app_config("robotApiBaseUrl", "grpc.nvcf.nvidia.com:443"))
        api_route_prefix = os.getenv("ROBOT_API_ROUTE_PREFIX", 
                            self.config.get_app_config("robotApiRoutePrefix", "/black-channel-server-api"))
        self.api_url = "https://" + base_url + api_route_prefix + "/robot/send_command"
        logger.info(f"Robot Speed Control API URL: {self.api_url}")
        self.api_timeout = int(self.config.get_app_config("robotApiTimeout", "5"))
        self.authorization_token = os.getenv("ROBOT_API_NVCF_AUTHORIZATION_TOKEN", self.config.get_app_config("robotApiNvcfAuthorizationToken", ""))
        self.function_id = os.getenv("ROBOT_API_FUNCTION_ID", self.config.get_app_config("robotApiFunctionId", ""))
        
        logger.info(f"Robot Speed Control API URL: {self.api_url} (from {'env' if 'ROBOT_API_BASE_URL' in os.environ else 'config/default'})")
        self.register_processor(self.read_raw, self.enhance_frames, int(self.config.get_app_config("numWorkersForFrameEnhancement")))

    def enhance_frames(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

        frames = self.calibration.filter_frames_by_sensor_id(frames)
        enhanced_frames = [ self.calibration.transform_frame(frame) for frame in frames ]
        self.write_frames(enhanced_frames)

        frames_map = group_frames_by_sensor_id(enhanced_frames)

        # 1 bev sensor with 1 roi and several AMRs from env.
        for sensor_id, sensor_frames in frames_map.items():
            changed_roi_states = self.amr_state_mgmt.update_roi_states(sensor_id, sensor_frames)
            self._process_roi_states(changed_roi_states)
            break
    
    def _process_amr_states(self, changed_amr_states: list[AmrState]) -> None:
        """
        Process changed AMR states and make API calls for mute/unmute actions.
        Groups states by action (mute/unmute) and makes batch API calls.
        
        :param changed_amr_states: List of AmrState objects with mute_state_changed=True
        """
        mute_states = [state for state in changed_amr_states if state.mute]
        unmute_states = [state for state in changed_amr_states if not state.mute]
        
        if mute_states:
            amr_ids = [state.id for state in mute_states]
            self._call_mute_unmute_api(amr_ids, True)
        
        if unmute_states:
            amr_ids = [state.id for state in unmute_states]
            self._call_mute_unmute_api(amr_ids, False)
    
    def _process_roi_states(self, changed_roi_states: list[RoiState]) -> None:
        """
        Process changed ROI states and make API calls for mute/unmute actions.
        Hard code to set all AMRs from environment variable to high speed for now.
        
        :param changed_roi_states: List of RoiState objects with mute_state_changed=True
        """
        mute_states = [state for state in changed_roi_states if state.mute]
        unmute_states = [state for state in changed_roi_states if not state.mute]
        amr_ids = []
        try:
            amr_ids_json = os.getenv("AMR_ID_MAP", self.config.get_app_config("amrIdMap", "{}"))
            amr_ids = list(json.loads(amr_ids_json).values())
        except json.JSONDecodeError:
            logger.error("Invalid AMR_ID_MAP format; expected JSON object string, got %r, using empty list as fallback", amr_ids_json)

        # There will be only one of mute_states or unmute_states since only one ROI for now.
        if mute_states and not unmute_states:
            self._call_mute_unmute_api(amr_ids, True)
        elif unmute_states and not mute_states:
            self._call_mute_unmute_api(amr_ids, False)

    def _call_mute_unmute_api(self, amr_ids: list[str], is_mute: bool) -> bool:
        """
        Call the mute/unmute API with the given object IDs and action.
        
        :param amr_ids: List of AMR IDs to mute/unmute
        :param is_mute: True if mute, False if unmute
        :return: True if API call was successful, False otherwise
        """
        mute_command = os.getenv("SPEED_RESET_COMMAND", self.config.get_app_config("speedResetCommand", "set_speed_100"))
        unmute_command = os.getenv("SPEED_REDUCE_COMMAND", self.config.get_app_config("speedReduceCommand", "set_speed_25"))
        command = mute_command if is_mute else unmute_command
        try:
            # Prepare API request
            payload = {
                "names": amr_ids,
                "command": command
            }
            
            # Prepare headers
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.authorization_token}",
                "function-id": self.function_id
            }
            
            # Log full request details
            logger.debug("API REQUEST DETAILS:")
            logger.debug(f"URL: {self.api_url}")
            logger.debug(f"Method: POST")
            logger.debug(f"Timeout: {self.api_timeout} seconds")
            logger.debug(f"Headers: {json.dumps(headers, indent=2)}")
            logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
            
            # Make the API call
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=self.api_timeout,
                headers=headers
            )

            # Log response body
            try:
                response_json = response.json()
                logger.debug(f"Response Body (JSON): {json.dumps(response_json, indent=2)}")
            except json.JSONDecodeError:
                logger.debug(f"Response Body (Text): {response.text}")
            logger.debug(f"Response Time: {response.elapsed.total_seconds():.3f} seconds")

            # Check response status
            if response.status_code == 200:
                logger.info(f"API call successful for {command} with {len(amr_ids)} AMRs")
                return True
            else:
                logger.error(f"API call failed with status {response.status_code}")
                return False

        except requests.exceptions.Timeout:
            logger.error(f"API call timed out for {command} command with {len(amr_ids)} AMRs")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API call failed for {command} command: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during API call for {command} command: {str(e)} with {len(amr_ids)} AMRs")
            return False


if __name__ == '__main__':

    from mdx.analytics.core.app.app_runner import run
    run(MuteUnmuteApp)

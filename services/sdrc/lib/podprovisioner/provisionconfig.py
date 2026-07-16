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

import requests
import logging
import json
import time
import datetime 

logger = logging.getLogger(__name__)


class provisionconfig:
    def __init__(self, app_config, redisMsging, cfg):
        self.app_config = app_config
        self.redisMsging = redisMsging
        self.cfg = cfg

    def add(self, podInfo=None, configData=None, ctx_header=None):
        logger.info("Starting add call")

        pod_IP = podInfo["podIp"]
        
        url = "http://{}:{}{}".format(
            pod_IP,
            podInfo["podPort"],
            self.app_config["WDM_WL_ADD_URL"],
        )
        if self.app_config["WDM_MAP_ADD_FIELD"] is not None \
            and self.app_config["WDM_MAP_ADD_FIELD"].strip() != "":
            map_add_field = json.loads(self.app_config["WDM_MAP_ADD_FIELD"])
            change_field = self.app_config["WDM_WL_CHANGE_FIELD"]
            mapped_data = map_add_field[
                configData[
                    self.app_config["WDM_EVENT_OBJECT_FIELD"]
                ]
                [change_field]
            ]
            configData[self.app_config["WDM_EVENT_OBJECT_FIELD"]] \
                [change_field] = mapped_data

        if self.app_config["WDM_REMAP_EVENT_OBJECT"] is not None and \
            self.app_config["WDM_REMAP_EVENT_OBJECT"].strip() != "":
            remap_event_obj = json.loads(
                self.app_config["WDM_REMAP_EVENT_OBJECT"]
            )
            event_obj_field = self.app_config["WDM_EVENT_OBJECT_FIELD"]
            remap_event_obj_field = remap_event_obj[event_obj_field]
            event_obj = configData[self.app_config["WDM_EVENT_OBJECT_FIELD"]]
            configData[remap_event_obj_field] = event_obj
            del configData[event_obj_field]

        event_obj_key = self.app_config["WDM_EVENT_OBJECT_FIELD"]
        if event_obj_key not in configData and self.app_config["WDM_REMAP_EVENT_OBJECT"]:
            remap = json.loads(self.app_config["WDM_REMAP_EVENT_OBJECT"])
            event_obj_key = remap[self.app_config["WDM_EVENT_OBJECT_FIELD"]]
        camera_id = configData.get(event_obj_key, {}).get(self.app_config["WDM_WL_ID_FIELD"], "?")
        logger.info("adding camera at {} (pod: {}, camera_id: {})".format(url, podInfo.get("podName", "?"), camera_id))
        logger.info("payload: {}".format(json.dumps(configData, indent=2)))
        response = None
        failed_to_add = True
        logger.info (f"Max retry attempt {self.app_config['WDM_ADD_REMOVE_RETRY_ATTEMPTS']}")
        failed_to_add_amnt = self.app_config["WDM_ADD_REMOVE_RETRY_ATTEMPTS"]
        
        if self.app_config["WDM_WL_ADD_URL"] is not None and self.app_config["WDM_WL_ADD_URL"] != "":
            while failed_to_add:
                try:
                    response = requests.post(json=configData, url=url, timeout=self.app_config["WDM_ADD_REMOVE_REQUEST_TIMEOUT"], headers=ctx_header)
                    logger.info(f"add operation Response Code: {response.status_code}")
                    if response.status_code == 200:
                        failed_to_add = False
                except Exception as e:
                    logger.info(f"error occurred in add call {e}. Will retry...")
                    print (f"{e}")
                    failed_to_add = True
                time.sleep(self.app_config["WDM_ADD_CALL_DELAY"])
                failed_to_add_amnt -= 1
                if failed_to_add_amnt == 0:
                    logger.info (f"Max retry attempt exhausted {self.app_config['WDM_ADD_REMOVE_RETRY_ATTEMPTS']}")
                    break
        self.redisMsging.publishMessage(self._generate_redis_msg(configData, podInfo, "Add event", self.app_config["WDM_WL_CHANGE_ID_ADD"]), self.app_config["WDM_AGENT_EVENT_BUS"])
        return response

    def delete(self, podInfo, configData=None):

        pod_IP = podInfo["podIp"]
        url = "http://{}:{}{}".format(
            pod_IP,
            podInfo["podPort"],
            self.app_config["WDM_WL_DELETE_URL"],
        )

        logger.info("deleting camera at {}".format(url))
        logger.info("payload: {}".format(json.dumps(configData, indent=2)))
        response = None
        failed_to_delete = True
        logger.info (f"Max retry attempt {self.app_config['WDM_ADD_REMOVE_RETRY_ATTEMPTS']}")
        failed_to_add_amnt = self.app_config["WDM_ADD_REMOVE_RETRY_ATTEMPTS"]
        if self.app_config["WDM_WL_DELETE_URL"] is not None and self.app_config["WDM_WL_DELETE_URL"] != "":
            while failed_to_delete:
                try:
                    if self.app_config["DELETE_API_METHOD"] == 'DELETE':
                        response = requests.delete(json=configData, url=url, timeout=self.app_config["WDM_ADD_REMOVE_REQUEST_TIMEOUT"])
                    else:
                        response = requests.post(json=configData, url=url, timeout=self.app_config["WDM_ADD_REMOVE_REQUEST_TIMEOUT"])
                    logger.info(f"delete operation Response Code: {response.status_code}")
                    try:
                        logger.info(f"delete operation text return: {response.text}")
                    except Exception as e:
                        logger.info("error while trying to print response.text - " + repr(e))
                    if response.status_code == 200:
                        failed_to_delete = False
                except Exception as e:
                    logger.info(f"error occurred in delete call {e}. Will retry...")
                    print (f"{e}")
                    failed_to_delete = True
                time.sleep(0.1)
                failed_to_add_amnt -= 1
                if failed_to_add_amnt == 0:
                    logger.info (f"Max retry attempt exhausted {self.app_config['WDM_ADD_REMOVE_RETRY_ATTEMPTS']}")
                    break
        self.redisMsging.publishMessage(self._generate_redis_msg(configData, podInfo, "Delete event", self.app_config["WDM_WL_CHANGE_ID_DEL"]), self.app_config["WDM_AGENT_EVENT_BUS"])
        return response
    
    def applyConfig(self, podInfo, configData):
        pod_IP = podInfo["podIp"]
        url = "http://{}:{}{}".format(
            pod_IP,
            self.app_config["WDM_CONFIG_PORT"],
            self.app_config["WDM_CONFIG_URL"],
        )

        logger.info("configuring at {}".format(url))
        logger.info("payload: {}".format(json.dumps(configData, indent=2)))
        response = None
        failed_to_configure = True
        failed_to_configure_amnt = self.app_config["WDM_ADD_REMOVE_RETRY_ATTEMPTS"]
        while failed_to_configure:
            try:
                response = requests.post(json=configData, url=url, timeout=self.app_config["WDM_ADD_REMOVE_REQUEST_TIMEOUT"])
                logger.info(f"configure operation Response Code: {response.status_code}")
                try:
                    logger.info(f"configure operation text return: {response.text}")
                except Exception as e:
                    logger.info("error while trying to print response.text - " + repr(e))
                failed_to_configure = False
            except Exception as e:
                logger.info(f"error occurred in configure call {e}. Will retry...")
                failed_to_configure = True
                time.sleep(0.1)
                failed_to_configure_amnt -= 1
                if failed_to_configure_amnt == 0:
                    logger.info (f"Max retry attempt exhausted {self.app_config['WDM_ADD_REMOVE_RETRY_ATTEMPTS']}")
                    break
        return response
    
    def _generate_redis_msg(self, configData, podInfo, event_info, event_type):
        output_data = {}
        output_data["stream_id"] = configData[self.app_config["WDM_EVENT_OBJECT_FIELD"]][self.app_config["WDM_WL_ID_FIELD"]]
        output_data["timestamp"] = datetime.datetime.utcnow().isoformat()[:-4] + "Z"
        output_data["type"] = "functional"
        output_data["source"] = str(podInfo["podName"]) + ":" + str(podInfo["podPort"])
        output_data[self.app_config["WDM_EVENT_OBJECT_FIELD"]] = event_info
        output_data["event_type"] = event_type
        output_data["config_data"] = configData
        
        _, cache_data = self.cfg.getCacheInfoForStreamId(configData[self.app_config["WDM_EVENT_OBJECT_FIELD"]][self.app_config["WDM_WL_ID_FIELD"]])
        if cache_data is None:
            cache_data = configData
        output_data["cache_data"] = cache_data
        
        return output_data

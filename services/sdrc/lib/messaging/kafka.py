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
from lib.parameters import configserver

import logging

logger = logging.getLogger(__name__)


class kafka:
    def __init__(self, config=None):
        self.config = config
        self.msg_key = config["WDM_KAFKA_MSG_KEY"]
        logger.info("Kafka: Message Key {}".format(self.msg_key))

    def getMessageValue(self, bus, msg):

        if msg.value is None:
            logger.info("Message value is None")
            return None

        if msg.key is None:
            logger.info("Message key is None")
            return None

        evobj_field = self.config["WDM_EVENT_OBJECT_FIELD"]
        v = msg.value.decode("utf-8")
        logger.info(f"value {v}")
        k = msg.key.decode("utf-8").strip('"')
        logger.info(k + " == '" + self.msg_key + "'")
        if k == self.msg_key:
            try:
                v_d = json.loads(v)
                # v_d[evobj_field]["wdm_id"] = str(uuid.uuid4())
                logger.info("Kafka: commit message queue")
                return v_d[evobj_field], v_d
            except Exception:
                logger.error(" Kafka: *** Exception occured ")
        return None

    # ? obsolete
    def saveMessage(self, pod, message):
        c = configserver()
        c.saveWorkLoadSpec(pod, spec_data=message)

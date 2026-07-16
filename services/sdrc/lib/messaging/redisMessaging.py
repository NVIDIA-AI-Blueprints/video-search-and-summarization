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

import datetime
import json
import logging
import os
import threading
import redis
from typing import List
import socket

logger = logging.getLogger(__name__)


class RedisStreamMsg:
    def __init__(self, msgid, content):
        self.msgid = msgid
        self.content = content

    def __str__(self):
        return f"id: {self.msgid}, content: {self.content}"

    def __repr__(self):
        return f"RedisStreamMsg(msgid={self.msgid}, content={self.content})"


class Consumer:
    def __init__(
            self,
            redis_conn,
            stream,
            consumer_group,
            batch_size=10,
            consumer_id=f"{os.getpid()}{threading.get_ident()}",
            max_wait_time_ms=300,
            cleanup_on_exit=True
    ) -> None:

        self.consumer_group = consumer_group
        self.stream = stream
        self.redis_conn = redis_conn
        self.batch_size = batch_size
        self.max_wait_time_ms = max_wait_time_ms
        self.consumer_id = consumer_id
        self.cleanup_on_exit = cleanup_on_exit
        self.prepare_redis_consumer()

    def _create_consumer_group(self) -> None:
        try:
            self.redis_conn.xgroup_create(
                name=self.stream,
                groupname=self.consumer_group,
                id="0-0",
                mkstream=True
            )
            logger.debug(
                f"{self.consumer_group} consumer group has been created"
            )
        except redis.ResponseError:
            logger.debug(
                f" {self.consumer_group} consumer group already exists"
            )

    def prepare_redis_consumer(self) -> None:
        self._create_consumer_group()

    def commit(self, item_id):
        self.redis_conn.xack(self.stream, self.consumer_group, item_id)

    def get_pending_items_of_consumer(
        self
    ) -> List[dict]:
        return self.redis_conn.xpending_range(
            name=self.stream,
            groupname=self.consumer_group,
            min="-",
            max="+",
            count=self.batch_size,
            consumername=self.consumer_id
        )

    def get_items(self):
        items = []
        delivered_msg = self.get_pending_items_of_consumer()
        sz = self.batch_size - len(delivered_msg) \
            if len(delivered_msg) > 0 else self.batch_size

        self._get_stream_messages(
            requested_messages=max(1, sz),
            do_never_delivered=True
        )

        items = self._get_stream_messages(
            requested_messages=None,
            do_never_delivered=False
        )

        return items

    def _get_stream_messages(
        self,
        requested_messages=None,
        wait_time=None,
        do_never_delivered=False
    ) -> List[RedisStreamMsg]:
        if requested_messages is None:
            requested_messages = self.batch_size
        try:
            items = self.redis_conn.xreadgroup(
                groupname=self.consumer_group,
                consumername=self.consumer_id,
                count=requested_messages,
                streams={self.stream: ">" if do_never_delivered else "0"},
                block=wait_time if wait_time else self.max_wait_time_ms,
                noack=False,
            )
            logger.debug(f"Got {items}")
            return self._transform_redis_resp_to_objects(items)
        except redis.ResponseError:
            logger.warning(
                f"Failed to get messages from {self.stream} from "
                f"{self.consumer_group} as ",
                exc_info=True,
            )
            return []

    def _transform_redis_resp_to_objects(self, items):
        msgs = []
        if isinstance(items, list) and len(items):
            try:
                if items[0][0] == self.stream:
                    items = items[0][1]
            except IndexError:
                logger.warning(
                    "Failed to process messages",
                    exc_info=True,
                )
        for item in items:
            msgs.append(RedisStreamMsg(msgid=item[0], content=item[1]))
        return msgs

    def remove_consumer(self, consumer_to_delete: str) -> int:
        return self.redis_conn.xgroup_delconsumer(
            name=self.stream,
            groupname=self.consumer_group,
            consumername=consumer_to_delete,
        )

    def __del__(self):
        if self.cleanup_on_exit:
            self.remove_consumer(self.consumer_id)


class redisMessaging:
    def __init__(self, config):
        self.config = config
        try:
            self.redis_connection = redis.StrictRedis(
                config["WDM_WL_REDIS_SERVER"],
                config["WDM_WL_REDIS_PORT"],
                encoding="utf-8",
                decode_responses=True,
                retry_on_timeout=True,
            )
            s = config["WDM_WL_REDIS_SERVER"]
            p = config["WDM_WL_REDIS_PORT"]
            logger.info (f"connected to redis {s}  port {p}")
        except Exception:
            logger.error("Unable to connect to redis")
            self.redis_connection = None
            return None

    def getRedisConnection(self):
        return self.redis_connection

    def getIdPodMapping(self, id):
        logger.info("getIdPodMapping: %s", id)
        return self.redis_connection.hget(
                self.config["WDM_WL_OBJECT_NAME"], id
            )

    def getIdPodPodDnsMapping(self, podname):
        return self.redis_connection.hget(
                        self.config["WDM_WL_OBJECT_NAME"]+"-pod",
                        podname
                    )

    def clearPodData(self, wl_pod):
        logger.info(
            "Clearing all redis data for %s" % (
                wl_pod
            )
        )

        podIdMaps = self.redis_connection.hgetall(
                        self.config["WDM_WL_OBJECT_NAME"]
                    )
        for id in podIdMaps:
            if podIdMaps[id] == wl_pod:
                self.redis_connection.hdel(
                    self.config["WDM_WL_OBJECT_NAME"],
                    id
                )

        podDnsMaps = self.redis_connection.hgetall(
            self.config["WDM_WL_OBJECT_NAME"] + "-pod"
        )

        for pod in podDnsMaps:
            if podDnsMaps[pod] == wl_pod:
                self.redis_connection.hdel(
                    self.config["WDM_WL_OBJECT_NAME"] + "-pod",
                    pod
                )

    def clearAllData(self):
        logger.info(
            "Clearing all redis data for %s" % (
                self.config["WDM_WL_OBJECT_NAME"]
            )
        )
        podIdMaps = self.redis_connection.hgetall(
                        self.config["WDM_WL_OBJECT_NAME"]
                    )
        for id in podIdMaps:
            self.redis_connection.hdel(
                self.config["WDM_WL_OBJECT_NAME"],
                id
            )

        podDnsMaps = self.redis_connection.hgetall(
            self.config["WDM_WL_OBJECT_NAME"] + "-pod"
        )
        for pod in podDnsMaps:
            self.redis_connection.hdel(
                self.config["WDM_WL_OBJECT_NAME"] + "-pod",
                pod
            )

    def getMessageValue(self, msg):
        evobj_field = self.config["WDM_EVENT_OBJECT_FIELD"]
        if evobj_field not in msg:
            logger.info("Value not found in the message")
            return None
        # msg[evobj_field]["wdm_id"] = str(uuid.uuid4())
        return msg[evobj_field]

    def getallIdsAsignedToPod(self, wlobj, podname):
        # dns = self.getIdPodPodDnsMapping (podname)
        logger.info(f"get all ids assigned to {podname} Wlobj {wlobj}")
        retIdList = []
        for id in self.redis_connection.hgetall(wlobj):
            m = self.getIdPodMapping(id)
            if m == podname:
                logger.info(f" {id} assigned to {m} ")
                retIdList.append(id)
            else:
                logger.info(f"{id} not assigned to {m}")

        return retIdList

    def message_up(self, **wlargs):
        wlargs["status"] = "up"
        logger.info("Sending UP message")
        self._error_event_message(**wlargs)

    def message_down(self, **wlargs):
        logger.info("Sending DOWN message")
        wlargs["status"] = "down"
        self._error_event_message(**wlargs)

    def message_err(self, **wlargs):
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        w = wlargs["wlobject"]
        ext_msg = self.config["WDM_EXT_ERROR_MSG"]
        message = {
                "wlobject": wlargs["wlobject"],
                "podname": wlargs["podname"],
                "timestamp": timestamp,
                "status": wlargs["status"],
                "ids": wlargs["id"],
                "type": wlargs["type"]
            }
        if self.config["WDM_ERROR_BUS_MSG_VERSION"].lower() == "v1":
            self.publishMessage(message)
        # podname = wlargs["podname"]
        status = wlargs["status"]
        id = wlargs["id"]
        source = socket.gethostname()
        messagev2 = {
                "streamid": id,
                "timestamp": timestamp,
                "type": wlargs["type"],
                "source": source,
                self.config["WDM_EVENT_OBJECT_FIELD"]: f"{w} {status}, {ext_msg}"
            }
        if self.config["WDM_ERROR_BUS_MSG_VERSION"].lower() == "v2":
            self.publishMessage(messagev2)

    def _error_event_message(self, **wlargs):
        wlobjname = wlargs["wlobject"]
        podname = wlargs["podname"]
        now = datetime.datetime.now()
        status = wlargs["status"]
        type = wlargs["type"]
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        ids = self.getallIdsAsignedToPod(wlobjname, podname)
        self.getIdPodPodDnsMapping(podname)

        if "payload" in wlargs and type == "reprovision":
            logger.info("Sending reprovision message with payload")
            message = {
                        "wlobject": wlobjname,
                        "podname": podname,
                        "timestamp": timestamp,
                        "status": status,
                        "ids": ids,
                        "type": type,
                        "event_field": self.config["WDM_EVENT_OBJECT_FIELD"],
                        "msg_key": self.config["WDM_REDIS_MSG_KEY"],
                        "redis_msg_field": self.config["WDM_WL_REDIS_MSG_FIELD"],
                        "payload": wlargs["payload"]
                    }
            logger.info("Sending reprovision message with payload: " + str(message))
            self.publishMessage(message)
            return

        logger.info("messaging version {}".format
                    (self.config["WDM_ERROR_BUS_MSG_VERSION"].lower()))
        if self.config["WDM_ERROR_BUS_MSG_VERSION"].lower() == "v1":
            message = {
                    "wlobject": wlobjname,
                    "podname": podname,
                    "timestamp": timestamp,
                    "status": status,
                    "ids": ids,
                    "type": type
                }
            self.publishMessage(message)
        source = socket.gethostname() 
        ext_msg = self.config["WDM_EXT_ERROR_MSG"] if type != "info" else "--"
        if self.config["WDM_ERROR_BUS_MSG_VERSION"].lower() == "v2":
            for id in ids:
                messagev2 = {
                    "streamid": id,
                    "timestamp": timestamp,
                    "type": type,
                    "source": source,
                    self.config["WDM_EVENT_OBJECT_FIELD"]: f"{wlobjname} {status}, {ext_msg}"
                }
                self.publishMessage(messagev2)

            if not ids:
                messagev2 = {
                    "streamid": "",
                    "timestamp": timestamp,
                    "type": type,
                    "source": source,
                    self.config["WDM_EVENT_OBJECT_FIELD"]: f"{wlobjname} {status}, {ext_msg}"
                }
                self.publishMessage(messagev2)



    def publishMessage(self, message, chnl=None):
        if chnl is None:
            chnl = "{}_{}".format(self.config["WDM_ERROR_EVENT_MSG_KEY"],
                              message["streamid"]) \
                if "streamid" in message and message["streamid"] is not None and \
                message["streamid"] != "" \
                else self.config["WDM_ERROR_EVENT_MSG_KEY"]
        logger.info(f"publish message to {chnl}")
        ret = self.redis_connection.publish(
                                        chnl,
                                        json.dumps(message, indent=4)
        )

    def getCurrentMapping(self):
        maps = self.redis_connection.hgetall(
            self.config["WDM_WL_OBJECT_NAME"])
        return maps


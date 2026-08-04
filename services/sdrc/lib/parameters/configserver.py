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

from io import StringIO
import sys
import json
from ruamel.yaml import YAML
import redis
import logging
from threading import Lock

logger = logging.getLogger(__name__)


class configserver:
    def __init__(self, wl_spec_file=None, app_config=None):
        self.config = app_config
        self.even_obj = app_config["WDM_EVENT_OBJECT_FIELD"]
        self.wl_spec_file = wl_spec_file
        self.wl_spec = None
        self.id_field = app_config["WDM_WL_ID_FIELD"]
        self.wl_objectName = app_config["WDM_WL_OBJECT_NAME"]
        self.y = YAML()
        self.y.indent(mapping=4, sequence=4, offset=2)
        self.y.preserve_quotes = True
        self.file_write_lock = Lock()
        # Serializes capacity-check + append so two placement decisions cannot both
        # reserve the last free slot on a pod. Distinct from file_write_lock, which is
        # taken and released inside each individual read/write helper.
        self.reserve_lock = Lock()
        self._loadWorkLoadSpec()

    def erasePodSpecContent(self, wl_pod):
        if self.wl_spec is not None and wl_pod in self.wl_spec:
            logger.debug (f"erase content for {wl_pod} from cache")
            self.deleteWLObj(wl_pod)


    def eraseSpecContent(self):
        try:
            try:
                self.file_write_lock.acquire()
                f = open(self.wl_spec_file, 'w')
            finally:
                f.close()
                self.file_write_lock.release()
            self._loadWorkLoadSpec()
        except Exception as e:
            logger.info(f"workload spec could not be loaded {e}")

    def getpods(self):
        retlist = list()
        if self.wl_spec is not None:
            for i in self.wl_spec.keys():
                retlist.append(i)
        return retlist

    def getpodsCount(self):
        return len(self.wl_spec.keys()) if self.wl_spec is not None else 0

    def getSpecCount(self, pod_name):
        if self.wl_spec is not None and pod_name in self.wl_spec.keys():
            spc = json.loads(self.wl_spec[pod_name])
            return len(spc)
        return 0

    def getAllStreams(self):
        pod_names = self.getpods()
        all_returns = {}
        for pod in pod_names:
            all_returns[pod] = json.loads(self.getworkLoadSpecs(pod))
        return all_returns
    
    def getCacheInfoForStreamId(self, stream_id):
        stream_cache_data = self.getAllStreams()
        for key, pipeline in stream_cache_data.items():
            streams = json.loads(pipeline)
            for stream in streams:
                if stream[self.config["WDM_EVENT_OBJECT_FIELD"]][self.config["WDM_WL_ID_FIELD"]] == stream_id:
                    return key, stream
        return None, None

    def _loadWorkLoadSpecRedis(self):
        self.redis_connection.llen(self.wl_objectName)
        redis.lrange(self.wl_objectName, 0, -1)[0].decode()

        return

    def _loadWorkLoadSpec(self):
        try:
            try:
                self.file_write_lock.acquire()
                with open(self.wl_spec_file, "r") as f:
                    d = f.read()
                    tmp_yml = self.y.load(d)
                    self.wl_spec = tmp_yml
            finally:
                self.file_write_lock.release()

        except FileNotFoundError:
            logger.info("filenot found")
            self.wl_spec = None
        except Exception as e:
            logger.info(self.wl_spec)
            logger.info("Some error while trying to load workload file, will delete and remake. NOTE: this may cause SDR and other services to go out of sync - " + repr(e))
#            self.eraseSpecContent()

    def getworkLoadSpecById(self, id):
        self._loadWorkLoadSpec()
        if self.wl_spec is None:
            return []
        retList = list()
        for itm in self.wl_spec.keys():
            for s in json.loads(self.wl_spec[itm]):
                if not isinstance(s, dict):
                    logger.info("Expected type read from wl_spec to be dict but it was not - value: " + str(s) + ". Skipping over this element")
                    continue
                if s[self.even_obj][self.id_field] == id:
                    if "pod_name" not in s:
                        s["pod_name"] = itm
                    retList.append(s)
        return retList if len(retList) > 0 else None

    def deleteFromWorkLoadSpecbyId(self, id):
        delList = self.getworkLoadSpecById(id)
        if delList is not None:
            for s in delList:
                if "pod_name" in s:
                    pn = s["pod_name"]
                    self.deleteFromWorkLoadSpec(id=id, pod_name=s["pod_name"])
                    p = len(json.loads(self.wl_spec[pn]))
                    if p == 0:
                        self.deleteWLObj(s["pod_name"])
        return delList

    def getworkLoadSpec(self, pod_name, id):
        self._loadWorkLoadSpec()
        if self.wl_spec is None or pod_name not in self.wl_spec:
            return None
        json.loads(self.wl_spec[pod_name] + "")
        o = list(
            filter(
                lambda itm: itm[self.even_obj][self.id_field] == id,
                json.loads(self.wl_spec[pod_name]),
            )
        )
        return o

    def getworkLoadSpecs(self, pod_name):
        return (
            None
            if self.wl_spec is None or pod_name not in self.wl_spec
            else json.dumps(self.wl_spec[pod_name])
        )

    def setworkLoadSpec(self, pod_name, spec_data):
        if self.getworkLoadSpec(pod_name) is None:
            self.wl_spec = dict()
        else:
            self.wl_spec[pod_name] = spec_data
        return

    def _initSpecData(self, pod_name, spec_data):
        inital = "{}: |-\n  [{}\n  ]".format(pod_name, spec_data)
        yinit = YAML()
        # yinit.indent()
        # yinit.preserve_quotes = True
        self.wl_spec = d = yinit.load(inital)
        try:
            self.file_write_lock.acquire()
            yinit.dump(self.wl_spec, sys.stdout)
        finally:
            self.file_write_lock.release()
        self._write_to_file(self.wl_spec)
        return d

    def deleteWLObj(self, pod_name):
        if self.wl_spec is None or pod_name not in self.wl_spec:
            logger.info("{} not found".format(pod_name))
            return

        del self.wl_spec[pod_name]
        try:
            self.file_write_lock.acquire()
            self.y.dump(self.wl_spec, sys.stdout)
        finally:
            self.file_write_lock.release()
        self._write_to_file(self.wl_spec)
        self._loadWorkLoadSpec()

    def updateWorkLoadSpec(self, pod_name, id, new_value):
        try:
            self.deleteFromWorkLoadSpec(pod_name, id)
            self.addWorkLoadSpec(pod_name, new_value, new_value)
        except Exception as e:
            logger.info("error in updateWorkLoadSpec: " + str(e))

    def deleteFromWorkLoadSpec(self, pod_name, id):
        logger.info("delete Workload Spec {} {}".format(pod_name, id))
        try:
            # Serialize with tryReserveWorkLoadSpec so a concurrent reserve cannot
            # be overwritten by a delete built from a stale in-memory snapshot.
            with self.reserve_lock:
                self._loadWorkLoadSpec()
                if self.wl_spec is None or pod_name not in self.wl_spec:
                    logger.info("{} not found".format(pod_name))
                    return
                logger.info("{} found id: {}".format(pod_name, id))
                d = json.loads(self.wl_spec[pod_name])
                o = [
                    itm
                    for itm in d
                    if not (
                        isinstance(itm, dict)
                        and itm.get(self.even_obj, {}).get(self.id_field) == id
                    )
                ]
                self.wl_spec[pod_name] = json.dumps(o, indent=4)
                if self.wl_spec_file is not None:  # this should never be the case
                    try:
                        self._write_to_file(self.wl_spec)
                    except Exception as e:
                        logger.info(
                            "error while writing to file in deleteFromWorkLoadSpec. "
                            "self.wl_spec: " + str(self.wl_spec)
                            + ", self.wl_spec_file: " + str(self.wl_spec_file)
                            + ", e: " + str(e)
                        )
                self._loadWorkLoadSpec()
        except Exception as e:
            logger.info("error in deleteFromWorkLoadSpec: " + str(e))

    def addWorkLoadSpec(self, pod_name, spec_data, originalData):
        logger.info("add Workload Spec {}".format(pod_name))
        # TODO: clean up the following
        spec_data = originalData
        spec_data = json.dumps(originalData)
        if self.wl_spec is None or self.wl_spec == {}:
            data = "{}: |-\n [{}\n ]".format(pod_name, spec_data)
            # y = YAML()
            try:
                self.file_write_lock.acquire()
                d = self.y.load(data)
            finally:
                self.file_write_lock.release()
            self._write_to_file(d)
            self._loadWorkLoadSpec()
        elif self.wl_spec is not None and pod_name not in self.wl_spec:
            data = "{}: |-\n [{}\n ]".format(pod_name, spec_data)
            # y = YAML()
            try:
                self.file_write_lock.acquire()
                d = self.y.load(data)
            finally:
                self.file_write_lock.release()

            string_stream = StringIO()
            try:
                self.file_write_lock.acquire()
                self.y.dump(d, string_stream)
            finally:
                self.file_write_lock.release()
            output_str = string_stream.getvalue()
            string_stream.close()
            try:
                self.file_write_lock.acquire()
                with open(self.wl_spec_file, "a") as f:
                    f.write(output_str)
            finally:
                self.file_write_lock.release()
            self._loadWorkLoadSpec()
            self._write_to_file(self.wl_spec)
        elif self.wl_spec is not None and pod_name in self.wl_spec:
            j = json.loads(spec_data)
            d = json.loads(self.wl_spec[pod_name])
            # y = YAML()
            d.append(j)
            self.wl_spec[pod_name] = json.dumps(d, indent=4)
            try:
                self.file_write_lock.acquire()
                self.y.dump(self.wl_spec, sys.stdout)
            finally:
                self.file_write_lock.release()
            self._write_to_file(self.wl_spec)
            self._loadWorkLoadSpec()
    
    def _stream_id_from_entry(self, entry):
        if not isinstance(entry, dict):
            return None
        event = entry.get(self.even_obj)
        if not isinstance(event, dict):
            return None
        return event.get(self.id_field)

    def tryReserveWorkLoadSpec(self, pod_name, originalData, threshold):
        """Append a stream to pod_name only if it currently holds fewer than threshold.

        The capacity check and the append share one hold of reserve_lock so a placement
        decision cannot interleave with another add for the same pod.

        Returns True (new reserve), False (pod full), or ``"already_held"`` when the
        stream id is already present on this pod.
        """
        with self.reserve_lock:
            self._loadWorkLoadSpec()
            stream_id = self._stream_id_from_entry(originalData)
            if stream_id is not None and self.wl_spec is not None and pod_name in self.wl_spec:
                try:
                    streams = json.loads(self.wl_spec[pod_name])
                except (TypeError, json.JSONDecodeError):
                    streams = []
                if isinstance(streams, list):
                    for existing in streams:
                        if self._stream_id_from_entry(existing) == stream_id:
                            logger.info(
                                "Reservation skipped: stream already present on pod %s",
                                pod_name,
                            )
                            return "already_held"
            if self.getSpecCount(pod_name) >= threshold:
                logger.info(
                    "Reservation refused: pod %s already holds %s stream(s)",
                    pod_name,
                    threshold,
                )
                return False
            self.addWorkLoadSpec(pod_name, originalData, originalData)
        return True

    def _write_to_file(self, file_contents, write_type="w"):
        try:
            self.file_write_lock.acquire()
            with open(self.wl_spec_file, write_type) as f:
                self.y.dump(file_contents, f)
        finally:
            self.file_write_lock.release()

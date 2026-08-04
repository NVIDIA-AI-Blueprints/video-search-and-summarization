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
import copy
import sys
import json
import threading
import time
from contextlib import contextmanager
from ruamel.yaml import YAML
import redis
import redis_lock
import logging

logger = logging.getLogger(__name__)


def clear_stale_redis_workload_spec_lock_keys(app_config, wl_spec_obj):
    """Remove python-redis-lock keys for the workload-spec cache so a prior crash cannot block startup."""
    lock_name = "redis_cache_lock:{}".format(
        wl_spec_obj or app_config["WDM_WL_OBJECT_NAME"] or "default"
    )
    lock_key = "lock:" + lock_name
    signal_key = "lock-signal:" + lock_name
    try:
        conn = redis.StrictRedis(
            host=app_config["WDM_WL_REDIS_SERVER"],
            port=app_config["WDM_WL_REDIS_PORT"],
            decode_responses=True,
        )
        deleted = conn.delete(lock_key, signal_key)
        if deleted:
            logger.info(
                "Removed %s stale redis lock key(s) for workload spec cache (%s)",
                deleted,
                lock_name,
            )
    except redis.RedisError as e:
        logger.warning(
            "Could not remove stale redis lock keys %s %s: %s",
            lock_key,
            signal_key,
            e,
        )


class redisconfig:
    def __init__(self, wl_spec_obj=None, app_config=None):
        self.config = app_config
        self.even_obj = app_config["WDM_EVENT_OBJECT_FIELD"]
        #self.wl_spec_obj = app_config["WL_SPEC_OBJECT"]
        self.wl_spec_obj = wl_spec_obj
        self.wl_spec = None
        self.id_field = app_config["WDM_WL_ID_FIELD"]
        self.wl_objectName = app_config["WDM_WL_OBJECT_NAME"]
        self.redis_host = app_config["WDM_WL_REDIS_SERVER"]
        self.redis_port = app_config["WDM_WL_REDIS_PORT"]
        self.redis_lock_timeout = app_config["WDM_REDIS_LOCK_TIMEOUT"]
        try:
            self._redis_lock_retry_sleep_sec = float(
                app_config.get("WDM_REDIS_LOCK_RETRY_SLEEP_SECONDS", 2.0)
            )
        except (TypeError, ValueError):
            self._redis_lock_retry_sleep_sec = 2.0
        self._redis_lock_retry_sleep_sec = max(0.05, self._redis_lock_retry_sleep_sec)
        self.redis_connection = redis.StrictRedis(host=self.redis_host, port=self.redis_port, decode_responses=True)
        # Per-cache-object lock so multiple workloads (different wl_spec_obj) do not block each other
        lock_name = "redis_cache_lock:{}".format(self.wl_spec_obj or self.wl_objectName or "default")
        self.lock = redis_lock.Lock(self.redis_connection, lock_name)
        # redis_lock.Lock is not thread-safe: if one thread holds the Redis key, another thread
        # calling acquire() on the same instance raises AlreadyAcquired (see self._held check).
        self._redis_lock_mutex = threading.Lock()
        self._loadWorkLoadSpec()

    def _provision_lease_key(self, stream_id):
        return "{}:provision-lease:{}".format(self.wl_spec_obj, stream_id)

    def tryAcquireProvisionLease(self, stream_id, owner, ttl_seconds):
        """Acquire a cross-replica lease for one stream provisioning attempt."""
        return bool(
            self.redis_connection.set(
                self._provision_lease_key(stream_id),
                owner,
                nx=True,
                ex=max(1, int(ttl_seconds)),
            )
        )

    def renewProvisionLease(self, stream_id, owner, ttl_seconds):
        """Extend a lease only when it is still owned by this attempt."""
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        return bool(
            self.redis_connection.eval(
                script,
                1,
                self._provision_lease_key(stream_id),
                owner,
                max(1, int(ttl_seconds)),
            )
        )

    def releaseProvisionLease(self, stream_id, owner):
        """Release a lease without deleting a newer attempt's lease."""
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        return bool(
            self.redis_connection.eval(
                script,
                1,
                self._provision_lease_key(stream_id),
                owner,
            )
        )

    @contextmanager
    def _workload_spec_lock_hold(self, retry_sleep_sec=None):
        """Acquire Redis workload-spec lock; on timeout wait retry_sleep_sec and retry (no limit)."""
        if retry_sleep_sec is None:
            retry_sleep_sec = self._redis_lock_retry_sleep_sec
        while True:
            with self._redis_lock_mutex:
                if self.lock.acquire(timeout=self.redis_lock_timeout):
                    try:
                        yield
                    finally:
                        try:
                            self.lock.release()
                        except redis_lock.NotAcquired:
                            pass
                    return
            logger.warning(
                "Redis workload-spec lock not acquired within %ss; retrying in %ss (wl_spec_obj=%s)",
                self.redis_lock_timeout,
                retry_sleep_sec,
                self.wl_spec_obj,
            )
            time.sleep(retry_sleep_sec)

    @contextmanager
    def _workload_spec_lock_try(self, max_attempts=3, retry_sleep_sec=None):
        """Best-effort acquire for reads; yields True if critical section ran, False if all attempts failed."""
        if retry_sleep_sec is None:
            retry_sleep_sec = self._redis_lock_retry_sleep_sec
        for attempt in range(max_attempts):
            with self._redis_lock_mutex:
                if self.lock.acquire(timeout=self.redis_lock_timeout):
                    try:
                        yield True
                    finally:
                        try:
                            self.lock.release()
                        except redis_lock.NotAcquired:
                            pass
                    return
            if attempt + 1 < max_attempts:
                logger.debug(
                    "Redis workload-spec load: lock busy, retry %s/%s in %ss (wl_spec_obj=%s)",
                    attempt + 1,
                    max_attempts,
                    retry_sleep_sec,
                    self.wl_spec_obj,
                )
                time.sleep(retry_sleep_sec)
        yield False

    def _decode_wl_spec_field(self, raw):
        """Parse Redis hash value as a JSON array of stream objects; empty or bad data → []."""
        if raw is None:
            return []
        s = raw.strip() if isinstance(raw, str) else str(raw).strip()
        if not s:
            return []
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in workload spec cache (wl_spec_obj=%s): %s", self.wl_spec_obj, e)
            return []
        if isinstance(data, list):
            return data
        logger.warning(
            "Expected JSON array in workload spec cache, got %s (wl_spec_obj=%s)",
            type(data).__name__,
            self.wl_spec_obj,
        )
        return []

    def erasePodSpecContent(self, wl_pod):
        self._loadWorkLoadSpec()
        if self.wl_spec is not None and wl_pod in self.wl_spec:
            logger.debug(f"erase content for {wl_pod} from cache")
            self.deleteWLObj(wl_pod)

    def eraseSpecContent(self):
        try:
            with self._workload_spec_lock_hold():
                self.redis_connection.delete(self.wl_spec_obj)
        except Exception as e:
            logger.info(f"workload spec could not be loaded {e}")
        try:
            self._loadWorkLoadSpec()
        except Exception as e:
            logger.info(f"workload spec could not be loaded {e}")

    def getpods(self):
        self._loadWorkLoadSpec()
        if self.wl_spec is None:
            return []
        return list(self.wl_spec.keys())

    def getpodsCount(self):
        return len(self.wl_spec.keys()) if self.wl_spec is not None else 0

    def getSpecCount(self, pod_name):
        if self.wl_spec is not None and pod_name in self.wl_spec.keys():
            return len(self._decode_wl_spec_field(self.wl_spec[pod_name]))
        return 0

    def getAllStreams(self):
        self._loadWorkLoadSpec()
        if self.wl_spec is None:
            return {}
        return {pod: self._decode_wl_spec_field(raw) for pod, raw in self.wl_spec.items()}

    def getCacheInfoForStreamId(self, stream_id):
        stream_cache_data = self.getAllStreams()
        for key, streams in stream_cache_data.items():
            for stream in streams:
                if stream[self.config["WDM_EVENT_OBJECT_FIELD"]][self.config["WDM_WL_ID_FIELD"]] == stream_id:
                    return key, stream
        return None, None

    def _loadWorkLoadSpec(self):
        try:
            with self._workload_spec_lock_try() as ok:
                if ok:
                    self.wl_spec = self.redis_connection.hgetall(self.wl_spec_obj)
                else:
                    logger.debug(
                        "Redis workload spec: could not acquire lock after retries (wl_spec_obj=%s)",
                        self.wl_spec_obj,
                    )
        except redis_lock.AlreadyAcquired as e:
            logger.warning("Redis workload spec: unexpected AlreadyAcquired: %s", e)
        except Exception as e:
            logger.info("Redis workload spec could not be loaded: %s", e)

    def getworkLoadSpecById(self, id):
        self._loadWorkLoadSpec()
        if self.wl_spec is None:
            return []
        retList = list()
        for itm in self.wl_spec.keys():
            for s in self._decode_wl_spec_field(self.wl_spec[itm]):
                if not isinstance(s, dict):
                    logger.info("Expected type read from wl_spec_obj to be dict but it was not - value: " + str(s) + ". Skipping over this element")
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
                    p = len(self._decode_wl_spec_field(self.wl_spec.get(pn)))
                    if p == 0:
                        self.deleteWLObj(s["pod_name"])
        return delList

    def getworkLoadSpec(self, pod_name, id):
        self._loadWorkLoadSpec()
        if self.wl_spec is None or pod_name not in self.wl_spec:
            return None
        o = list(
            filter(
                lambda itm: itm[self.even_obj][self.id_field] == id,
                self._decode_wl_spec_field(self.wl_spec[pod_name]),
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
        if self.wl_spec is None:
            self.wl_spec = dict()
        self.wl_spec[pod_name] = spec_data
        return

    def deleteWLObj(self, pod_name):
        if self.wl_spec is None or pod_name not in self.wl_spec:
            logger.info("{} not found".format(pod_name))
            return
        del self.wl_spec[pod_name]
        with self._workload_spec_lock_hold():
            self.redis_connection.hdel(self.wl_spec_obj, pod_name)
        self._loadWorkLoadSpec()

    def updateWorkLoadSpec(self, pod_name, id, new_value):
        try:
            self.deleteFromWorkLoadSpec(pod_name, id)
            self.addWorkLoadSpec(pod_name, new_value, new_value)
        except Exception as e:
            logger.info("error in updateWorkLoadSpec: " + str(e))

    def apply_cache_metadata_update(
        self,
        stream_id,
        additional_metadata,
        cache_key="external_metadata",
        overwrite=False,
    ):
        """Update metadata for one stream in one Redis lock + one hset.

        Avoids delete+add (two locks) and full-cache reload via getAllStreams before write,
        so updates commit as soon as Redis accepts the write and contend less with provision threads.
        """
        if not isinstance(additional_metadata, dict):
            logger.info("apply_cache_metadata_update: additional_metadata must be a dict")
            return False
        ev = self.even_obj
        idf = self.id_field
        try:
            with self._workload_spec_lock_hold():
                raw_hash = self.redis_connection.hgetall(self.wl_spec_obj)
                target_pod = None
                streams = None
                idx = None
                for pod_name, raw in raw_hash.items():
                    slist = self._decode_wl_spec_field(raw)
                    for i, stream in enumerate(slist):
                        if not isinstance(stream, dict):
                            continue
                        if stream.get(ev, {}).get(idf) == stream_id:
                            target_pod, idx, streams = pod_name, i, slist
                            break
                    if target_pod is not None:
                        break
                if target_pod is None:
                    return False
                new_stream = copy.deepcopy(streams[idx])
                new_dict_data = (
                    dict(new_stream[cache_key])
                    if cache_key in new_stream
                    and isinstance(new_stream[cache_key], dict)
                    else {}
                )
                if not overwrite and cache_key in new_stream:
                    new_dict_data.update(additional_metadata)
                else:
                    new_dict_data = dict(additional_metadata)
                if not new_dict_data:
                    new_stream.pop(cache_key, None)
                else:
                    new_stream[cache_key] = new_dict_data
                streams[idx] = new_stream
                self.redis_connection.hset(
                    self.wl_spec_obj,
                    target_pod,
                    json.dumps(streams, indent=4),
                )
            self._loadWorkLoadSpec()
            return True
        except Exception as e:
            logger.info("error in apply_cache_metadata_update: %s", e)
            return False

    def deleteFromWorkLoadSpec(self, pod_name, id):
        logger.info("delete Workload Spec {} {}".format(pod_name, id))
        try:
            with self._workload_spec_lock_hold():
                current = self.redis_connection.hget(self.wl_spec_obj, pod_name)
                if current is None:
                    logger.info("{} not found".format(pod_name))
                    return
                logger.info("{} found id: {}".format(pod_name, id))
                streams = self._decode_wl_spec_field(current)
                remaining = list(
                    filter(
                        lambda itm: itm[self.even_obj][self.id_field] != id,
                        streams,
                    )
                )
                self.redis_connection.hset(
                    self.wl_spec_obj, pod_name, json.dumps(remaining, indent=4)
                )
            self._loadWorkLoadSpec()
        except Exception as e:
            logger.info("error in deleteFromWorkLoadSpec: " + str(e))

    def tryReserveWorkLoadSpec(self, pod_name, originalData, threshold):
        """Atomically append a stream when the pod still has capacity."""
        stream_id = originalData[self.even_obj][self.id_field]
        with self._workload_spec_lock_hold():
            current = self.redis_connection.hget(self.wl_spec_obj, pod_name)
            streams = self._decode_wl_spec_field(current)
            if any(
                stream.get(self.even_obj, {}).get(self.id_field) == stream_id
                for stream in streams
                if isinstance(stream, dict)
            ):
                return "already_held"
            if len(streams) >= threshold:
                return False
            streams.append(copy.deepcopy(originalData))
            self.redis_connection.hset(
                self.wl_spec_obj, pod_name, json.dumps(streams, indent=4)
            )
        self._loadWorkLoadSpec()
        return True

    def addWorkLoadSpec(self, pod_name, spec_data, originalData):
        logger.info("add Workload Spec {}".format(pod_name))
        spec_data = json.dumps(originalData)
        # Compute new value in a local variable so another thread cannot replace
        # self.wl_spec (e.g. via _loadWorkLoadSpec) before we hset, which caused KeyError.
        current = (self.wl_spec or {}).get(pod_name)
        if current is None:
            new_value = json.dumps([json.loads(spec_data)], indent=4)
        else:
            d = self._decode_wl_spec_field(current)
            d.append(json.loads(spec_data))
            new_value = json.dumps(d, indent=4)

        logger.debug("addWorkLoadSpec %s: acquiring redis lock (timeout=%s)", pod_name, self.redis_lock_timeout)
        with self._workload_spec_lock_hold():
            self.redis_connection.hset(self.wl_spec_obj, pod_name, new_value)
        self._loadWorkLoadSpec()
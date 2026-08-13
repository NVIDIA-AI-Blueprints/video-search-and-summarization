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

import sys
sys.path.append('./../../..')
sys.path.append('.')
from lib.podprovisioner.kubernetes.k8sclient import k8sclient
import json
from pathlib import Path
import docker
import redis
import logging
from dotmap import DotMap
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class dockerclient(k8sclient):
    def __init__(self, app_config, **kvargs):
        self.downpodsArray = []
        self.health_watcher = None
        self.docker = docker.from_env()
        self.namespace = app_config["KUBERNETS_NAMESPACE"]
        self.kind = app_config["WDM_WL_KIND"]
        self.wlobjname = app_config["WDM_WL_OBJECT_NAME"]
        self.initiatorWLObjname = app_config["WDM_INITIATOR_WLOBJ_NAME"]
        self.timeout = app_config["WDM_TIMEOUT"]
        self.app_config = app_config
        self.redis_connection = redis.StrictRedis(
            self.app_config["WDM_WL_REDIS_SERVER"],
            self.app_config["WDM_WL_REDIS_PORT"],
            encoding="utf-8",
            decode_responses=True,
            retry_on_timeout=True,
        )
        self.ready_replicas = 0
        self.cluster_config_file = app_config["WDM_CLUSTER_CONFIG_FILE"]
        self.cluster_container_names = json.loads(app_config["WDM_CLUSTER_CONTAINER_NAMES"])
        self.cluster_container_names = [prefix.lower() for prefix in self.cluster_container_names]
        
        self.allocation_hash_name = app_config["WDM_POD_ALLOCATION_HASH_NAME"]
        self.allocation_regex_delimiter = app_config["WDM_POD_ALLOCATION_REGEX_DELIMITER"]

        self.max_replicas = app_config["WDM_MAX_REPLICAS"]
        self.pod_watch_cluster_delay = app_config["WDM_POD_WATCH_DOCKER_DELAY"]

        if Path(self.cluster_config_file).is_file():
            f = open(self.cluster_config_file)
            self.cluster_config_data = json.load(f)
            f.close()
            logger.info("Cluster config file (" + str(self.cluster_config_file) + ") was read")
        else:
            logger.error("Cluster config file (" + str(self.cluster_config_file) + ") does not exist")
            self.cluster_config_data = {}

    def get_current_allocation_configs(self):
        return super().get_current_allocation_configs()
    
    def get_pod_info_by_encoded_name(self, encoded_name):
        return super().get_pod_info_by_encoded_name(encoded_name)
    
    def get_pod_allocation_by_podName(self, podName):
        return super().get_pod_allocation_by_podName(podName)
    
    def get_current_allocation_pod_names(self):
        return super().get_current_allocation_pod_names()
    
    def find_unallocated_pod(self):
        return super().find_unallocated_pod()
    
    def update_current_allocation_configs(self, config_details):
        return super().update_current_allocation_configs(config_details)
    
    def delete_allocation_config(self, config_details):
        return super().delete_allocation_config(config_details)

    def getReadyReplicas (self):
        if self.health_watcher is not None:
            return self.health_watcher.healthy_count()
        return super().getReadyReplicas()
    
    def setReadyReplicas (self, readyReplicaCount=0):
        return super().setReadyReplicas(readyReplicaCount)

    def getClient(self):
         return self.docker

    def _phase_from_docker_container(self, container_name):
        """Resolve real container state for pod_list / replica counts (not hardcoded Running)."""
        try:
            container = self.docker.containers.get(container_name)
        except docker.errors.NotFound:
            logger.info(
                "Docker container %r not found; treating phase as Unknown",
                container_name,
            )
            return "Unknown"
        except Exception as e:
            logger.info(
                "Docker inspect failed for %r: %s; phase Unknown",
                container_name,
                e,
            )
            return "Unknown"
        try:
            container.reload()
        except Exception as e:
            logger.debug("container.reload failed for %r: %s", container_name, e)
        st = (container.status or "").lower()
        logger.info(f"container.status: {st}")
        if st == "running":
            return "Running"
        if st == "exited":
            return "Pending"
        if st == "paused":
            return "Unknown"
        if st == "restarting":
            return "Pending"
        if st == "dead":
            return "Failed"
        if st == "created":
            return "Pending"
        if st == "removing":
            return "Pending"
        return st.capitalize() if st else "Unknown"

    def getPodIps(self, WLObject):
        podIps = []
        for i in WLObject:
            logger.info(
                "->>> %s\t%s\t%s\t%s\t%s\t%s\t%s"
                % (
                    i.status.pod_ip,
                    i.status.port,
                    i.status.phase,
                    i.spec.hostname,
                    i.spec.subdomain,
                    i.metadata.namespace,
                    i.metadata.name,
                )
            )
            docker_service_address = self._docker_service_address(i)
            provisioning_address = None
            cfg_entry = self.cluster_config_data.get(i.metadata.name)
            if isinstance(cfg_entry, dict):
                provisioning_address = cfg_entry.get("provisioning_address")
            if not provisioning_address and i.status.pod_ip is not None:
                port = i.status.port
                if port is not None and str(port).strip() != "":
                    provisioning_address = f"{i.status.pod_ip}:{port}"
                else:
                    provisioning_address = str(i.status.pod_ip)
            ipobj = {
                "podName": i.metadata.name,
                "podIp": i.status.pod_ip,
                "namespace": i.metadata.namespace,
                "owner": i.metadata.owner_references,
                "phase": i.status.phase,
                "podPort": i.status.port,
                "poddns": docker_service_address,
                "provisioning_address": provisioning_address,
            }
            podIps.append(ipobj)
        logger.info("len(podsips): " + str(len(podIps)) + ", values: " + str(podIps))
        return podIps if len(podIps) > 0 else None

    def _docker_service_address(self, pod_obj):
        for candidate in (
            pod_obj.spec.hostname,
            pod_obj.status.pod_ip,
            pod_obj.metadata.name,
        ):
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        return None

    def _extractOwnerObject(self, wlObjName, selectPodIp):
        return super()._extractOwnerObject(wlObjName, selectPodIp)

    def getWorkloadObjects(self):
        return_list = []
        for key, value in self.cluster_config_data.items():
            # TODO: not sure if the values in here are correct
            provisioning_addr_base = value["provisioning_address"].split(":")[0]
            provisioning_addr_port = value["provisioning_address"].split(":")[1]
            if (
                self.health_watcher is not None
                and self.health_watcher.is_pod_known(key)
            ):
                phase = (
                    "Running"
                    if self.health_watcher.is_pod_healthy(key)
                    else "Pending"
                )
            else:
                phase = self._phase_from_docker_container(key)

            new_obj = {
                "status": {
                    "pod_ip": provisioning_addr_base,
                    "phase": phase,
                    "port": provisioning_addr_port
                },
                "spec": {
                    "hostname": provisioning_addr_base,
                    "subdomain": provisioning_addr_base
                },
                "metadata": {
                    "namespace": value["process_type"],
                    "name": key
                    # "owner_references":  # Not sure if this is needed and if it is, what it should contain
                }
            }
            return_list.append(DotMap(new_obj))
        return return_list

    def getDeployments(self):
        return None

    def getStatefulSets(self):
        return_list = []
        for key, value in self.cluster_config_data.items():
            # if self.wlobjname == key:
            # k8s version has additional items, but status.replicas is the only one used in app.py
            new_obj = {
                "api_version": 1.0,
                "kind": value["process_type"],
                "status": {
                    "replicas": self.max_replicas # not correct value, but should work
                }
            }
            return_list.append(new_obj)
        return DotMap(return_list[0]) if len(return_list) > 0 else None


    def scaleStatefulsetPods(self, name=None, replicas=None):
        return

    def watchAndUpdateActiveReplicaCount (self):
        return

    def waitForPodsToRunningState (self, podname):
        return None

    def ifPodDown(self, podname):
        # Prefer HTTP health watcher (application readiness) over Docker socket
        # container status. Unknown / not-yet-probed pods are treated as down.
        if self.health_watcher is not None:
            if self.health_watcher.is_pod_known(podname):
                return not self.health_watcher.is_pod_healthy(podname)
            return True
        if podname in self.downpodsArray:
            return True
        # Legacy fallback when no health watcher is attached.
        return self._phase_from_docker_container(podname) != "Running"

    def watchAllPodState(self):
        return None

    def watchPodState(self):
        """Watch pod readiness for PodErrorWatcher.

        When an HTTP health watcher is attached
        (``WDM_WL_HEALTH_CHECK_WAIT_ENABLED``), consume its transitions.
        Otherwise fall back to legacy Docker socket container status.
        """
        if self.health_watcher is not None:
            logger.info(
                "Docker pod watch using HTTP health checks at %s",
                self.app_config.get("WDM_WL_HEALTH_CHECK_URL"),
            )
            for is_down, podname, generate_name in self.health_watcher.iter_transitions():
                if is_down:
                    if podname not in self.downpodsArray:
                        self.downpodsArray.append(podname)
                    yield True, podname, generate_name
                else:
                    if podname in self.downpodsArray:
                        self.downpodsArray.remove(podname)
                    yield False, podname, generate_name
            return

        # Legacy Docker socket polling when HTTP health checks are disabled.
        logger.info("Docker pod watch using container state (HTTP health disabled)")
        error = False
        previous_ts_checked = datetime.utcnow()
        while True:
            containers = self.docker.containers.list()
            new_prev_ts = datetime.utcnow()
            for container in containers:
                # Check if the current container matches any of the possible substrings we need to watch
                container_name_matches = any(name == container.name.lower() for name in self.cluster_container_names)
                if container.name != self.wlobjname and not container_name_matches:
                    continue

                container.reload()
                curr_container_attrs = container.attrs

                finishedAt_ts = curr_container_attrs["State"]["FinishedAt"]
                startedAt_ts = curr_container_attrs["State"]["StartedAt"]
                container_finished_ts = datetime.fromisoformat(finishedAt_ts[:finishedAt_ts.rfind(".")])
                container_started_ts = datetime.fromisoformat(startedAt_ts[:startedAt_ts.rfind(".")])

                if finishedAt_ts.rfind(".") != -1:
                    container_finished_ts += timedelta(microseconds=int(finishedAt_ts[finishedAt_ts.rfind(".") + 1:finishedAt_ts.rfind(".") + 7]))
                if startedAt_ts.rfind(".") != -1:
                    container_started_ts += timedelta(microseconds=int(startedAt_ts[startedAt_ts.rfind(".") + 1:startedAt_ts.rfind(".") + 7]))

                if container_name_matches:
                    if container.status.lower() == "exited": # is exited equivalent to deleted on k8s? 
                        if container.name in self.downpodsArray:
                            self.downpodsArray.remove(container.name)
                            continue
                if container.name == self.wlobjname or container_name_matches:
                    if container_finished_ts >= previous_ts_checked or container_started_ts >= previous_ts_checked:
                        error = True

                    if container.name is not None:
                        if error:
                            if container.name not in self.downpodsArray:
                                self.downpodsArray.append(container.name)
                                yield error, container.name, container.name
                            error = False
                        else:
                            if container.name in self.downpodsArray:
                                self.downpodsArray.remove(container.name)
                                yield error, container.name, container.name
            previous_ts_checked = new_prev_ts
            time.sleep(self.pod_watch_cluster_delay)

    def waitForPendingPodToBecomeReady(self):
        return None

    def restartRouterDeployment(self):
        return None
    
    def _makeNginxProxyPass(self, id, hostname, port, proxyUrl):
        return None

    def _removeNginxProxyPass(self, configdata, id, hostname, port, proxyUrl):
        return None

    def _checkNginxProxyPassExists(
            self, configdata, id, hostname, port, proxyUrl
    ):
        return None

    def updateRouteMapping(
            self,
            k8sWLobjName,
            id,
            podInfoItm,
            operation="add"
    ):
        if operation == "add":
            pod_route_host = (
                podInfoItm.get("poddns")
                or podInfoItm.get("podIp")
                or podInfoItm["podName"]
            )
            self.redis_connection.hset(k8sWLobjName, id, podInfoItm["podName"])
            self.redis_connection.hset(
                k8sWLobjName + "-pod",
                podInfoItm["podName"],
                pod_route_host,
            )
        else:
            self.redis_connection.hdel(k8sWLobjName, id)

    def updateRouterConfigMap(self, id, podInfoItm, operation="add"):
        return None

    def get_podname_keys(self):
        return self.cluster_config_data.keys()

    def get_health_check_targets(self):
        """Return health-probe targets from ``docker_cluster_config.json``.

        Host/port come from each entry's ``provisioning_address``. Only the
        health path (``WDM_WL_HEALTH_CHECK_URL``) is supplied separately by the
        health watcher when building the full URL.
        """
        targets = []
        for pod_name, value in self.cluster_config_data.items():
            provisioning_address = value.get("provisioning_address")
            if not provisioning_address or not str(provisioning_address).strip():
                logger.warning(
                    "Skipping health target %s — missing provisioning_address "
                    "in docker cluster config",
                    pod_name,
                )
                continue
            provisioning_address = str(provisioning_address).strip()
            host = provisioning_address
            port = None
            if ":" in provisioning_address:
                host, port = provisioning_address.rsplit(":", 1)
            targets.append(
                {
                    "podName": pod_name,
                    "podIp": host,
                    "podPort": port,
                    "provisioning_address": provisioning_address,
                }
            )
        return targets

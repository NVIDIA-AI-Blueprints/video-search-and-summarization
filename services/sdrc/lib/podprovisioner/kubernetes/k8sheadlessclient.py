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
import redis
import logging
from dotmap import DotMap
import time
import socket
import re
import dns.reversename
import dns.resolver

logger = logging.getLogger(__name__)


class k8sheadlessclient(k8sclient):
    def __init__(self, app_config, **kvargs):
        super().__init__(app_config, **kvargs)
        self.downpodsArray = {}
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

        self.max_replicas = app_config["WDM_MAX_REPLICAS"]
        self.pod_watch_cluster_delay = app_config["WDM_POD_WATCH_DOCKER_DELAY"]
        self.cluster_type = app_config["WDM_CLUSTER_TYPE"]
        
        self.allocation_hash_name = app_config["WDM_POD_ALLOCATION_HASH_NAME"]
        self.allocation_regex_delimiter = app_config["WDM_POD_ALLOCATION_REGEX_DELIMITER"]

        self.base_domain = app_config["WDM_K8S_HEADLESS_BASE_DOMAIN"]
        self.default_port = app_config["WDM_K8S_HEADLESS_DEFAULT_POD_PORT"]
        self.cluster_config_data = {} # key=podIp
        
        initial_pods = self._query_dns()
        self._update_cluster_config_with_new_pods(initial_pods)

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
        return super().getReadyReplicas()
    
    def setReadyReplicas (self, readyReplicaCount=0):
        return super().setReadyReplicas(readyReplicaCount)

    def getClient(self):
         return None

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
            hostname = i.spec.hostname
            subdomain = i.spec.subdomain
            ipobj = {
                "podName": i.metadata.name,
                "podIp": i.status.pod_ip,
                "namespace": i.metadata.namespace,
                "owner": i.metadata.owner_references,
                "phase": i.status.phase,
                "podPort": i.status.port,
                "poddns": None
                if subdomain is None
                else hostname,
            }
            podIps.append(ipobj)
        logger.info("len(podsips): " + str(len(podIps)) + ", values: " + str(podIps))
        return podIps if len(podIps) > 0 else None

    def _extractOwnerObject(self, wlObjName, selectPodIp):
        return super()._extractOwnerObject(wlObjName, selectPodIp)

    def getWorkloadObjects(self):
        return_list = [DotMap(item) for key, item in self.cluster_config_data.items()]
        
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
                "kind": self.cluster_type,
                "status": {
                    "replicas": self.max_replicas # not correct value, but should work?
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

    def ifPodDown (self, podname):
        if self.health_watcher is not None:
            if self.health_watcher.is_pod_known(podname):
                return not self.health_watcher.is_pod_healthy(podname)
            return True
        return None

    def watchAllPodState(self):
        return None

    def _query_dns(self):
        ip_list = []
        ais = socket.getaddrinfo(self.base_domain,0,0,0,0)
        for result in ais:
            ip_list.append(result[-1][0])
            ip_list = list(set(ip_list))
        return list(ip_list)
    
    def _contains_valid_ip_addr(self, addr):
        ipv4_pattern = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')
        try:
            possible_ips = ipv4_pattern.findall(addr)
            for ip in possible_ips:
                try:
                    socket.inet_aton(addr)
                    return True
                except socket.error:
                    continue
        except Exception as e:
            return False
        return False
    
    def _reverse_name_lookup(self, ip):
        try:
            reverse_name = dns.reversename.from_address(ip)
            ptr_records = dns.resolver.resolve(reverse_name, 'PTR')
            ptr_strings = [ptr.to_text() for ptr in ptr_records]
                        
            for ptr in ptr_strings:
                # Find entry without IP addr in name
                if not self._contains_valid_ip_addr(ptr):
                    return ptr
            # Return first entry if no non-IP addr name found
            return ptr_strings[0] if ptr_strings else "NO_NAME"
        except Exception as e:
            logger.error(f"Error during reverse_name_lookup: {e}")
            return "NO_NAME"
    
    def _update_cluster_config_with_new_pods(self, dns_vals):
        for pod in dns_vals:
            pod_val_split = pod.split(":")
            pod_ip = pod_val_split[0] if len(pod_val_split) > 1 else pod
            port = pod_val_split[1] if len(pod_val_split) > 1 else self.default_port
            
            new_name = self._reverse_name_lookup(pod_ip)
            if new_name == "NO_NAME":
                logger.info("name is 'NO_NAME', setting name to pod ip")
                new_name = str(pod_ip)
                
            new_pod_dict = {
                "name": new_name,
                "status": {
                    "pod_ip": pod_ip,
                    "phase": "Running",
                    "default_port": port,
                    "port": port
                },
                "spec": {
                    "hostname": new_name,
                    "subdomain": self.base_domain
                },
                "metadata": {
                    "namespace": self.cluster_type,
                    "name": new_name,
                    "owner_references": {
                        "owner": {
                            "name": str("owner_") + str(new_name)
                        }
                    }
                }
            }
            
            self.cluster_config_data[pod] = new_pod_dict
        logger.info("cluster_config_data: " + str(self.cluster_config_data))
        return self.cluster_config_data

    def watchDnsStatus(self):
        # Check dns status of pods and update cluster_config_data
        curr_dns_vals = set(self._query_dns())
        no_longer_available = list(set(self.cluster_config_data.keys()) - curr_dns_vals)
        newly_available = list(curr_dns_vals - set(self.cluster_config_data.keys()))
        
        if len(no_longer_available) > 0:
            logger.info("pods no longer found: " + str(no_longer_available))
            
            for pod in no_longer_available:
                del self.cluster_config_data[pod]
            # yield False, no_longer_available, no_longer_available

        if len(newly_available) > 0:
            logger.info("new pods found: " + str(newly_available))
            self._update_cluster_config_with_new_pods(newly_available)
            # yield False, newly_available, newly_available

    def watchPodState(self):   
        # Watches the state of Kubernetes pods in the configured namespace, tracking their transitions between running and error states.
        # Also update cluster_config_data using watchDnsStatus()
        error = False
        podname = None
        while True:
            try:
                self.watchDnsStatus()
                pod_list = self.k8sclientCore.list_namespaced_pod(namespace=self.namespace)
                # Build a set of current pod names for quick lookup
                current_pods = {}
                for pod in pod_list.items:
                    name = pod.metadata.name
                    generate_name = pod.metadata.generate_name or ""
                    generate_name = generate_name[:-1] if generate_name else ""
                    phase = pod.status.phase or "Unknown"
                    pod_ip = pod.status.pod_ip
                    current_pods[name] = pod_ip

                    # Only consider pods matching workload or initiator
                    if generate_name == self.wlobjname or generate_name.startswith(self.initiatorWLObjname+"-"):
                        podname = name
                        error = False
                        if phase.lower() != "running":
                            logger.debug(f"pod {name} went down {phase}")
                            error = True
                        # Check container statuses
                        container_statuses = pod.status.container_statuses
                        if container_statuses is not None:
                            for cs in container_statuses:
                                n = cs.name
                                r = cs.ready
                                s = cs.started
                                logger.debug(f"- name - {n}-rdy-{r}-strtd-{s}-")
                                if not s or not r or cs.state.running is None:
                                    error = True
                                    break
                        else:
                            logger.debug(f"{podname} --- NULL")
                        f = list(filter(lambda x: x == podname, self.downpodsArray.keys()))
                        if error:
                            if not f:
                                logger.info(f"Pod {podname} add to Error State")
                                self.downpodsArray.update({podname: pod_ip})
                                logger.info(self.downpodsArray)
                                yield error, podname, generate_name, pod_ip, None
                        else:
                            if f:
                                logger.info(f"Pod {podname} remove from Error State")
                                pod_ip_new = pod_ip
                                pod_ip_old = self.downpodsArray.get(podname, None)
                                self.downpodsArray.pop(podname, None)
                                logger.info(self.downpodsArray)
                                # wait for pod dns is available 
                                while True:
                                    current_dns_ips = set(self._query_dns())
                                    logger.info("current_dns_ips: " + str(current_dns_ips))
                                    if pod_ip_new in current_dns_ips:
                                        self.watchDnsStatus()
                                        break
                                    time.sleep(0.1)

                                yield error, podname, generate_name, pod_ip_old, pod_ip_new
            except Exception as e:
                logger.error(f"Exception in watchPodState: {e}")
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
        return super().updateRouteMapping(k8sWLobjName, id, podInfoItm, operation)

    def updateRouterConfigMap(self, id, podInfoItm, operation="add"):
        return None

    def get_podname_keys(self):
        return self.cluster_config_data.keys()

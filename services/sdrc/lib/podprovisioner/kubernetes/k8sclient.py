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

import time
from kubernetes import client, watch
from kubernetes.client.rest import ApiException
from datetime import datetime
from string import Template
import logging
import re
import redis
import json

logger = logging.getLogger(__name__)


class k8sclient:
    def __init__(self, app_config, **kvargs):
        self.downpodsArray = []
        configuration = client.Configuration()
        logger.info(kvargs["kubernetes_url"])
        configuration.api_key["authorization"] = kvargs["bearer_token"]
        configuration.api_key_prefix["authorization"] = "Bearer"
        configuration.host = kvargs["kubernetes_url"].strip()
        configuration.ssl_ca_cert = kvargs["ssl_ca_cert"]
        self.k8sclient = client
        self.k8sclientCore = client.CoreV1Api(client.ApiClient(configuration))
        self.k8sAppclientV1 = client.AppsV1Api(client.ApiClient(configuration))
        self.namespace = app_config["KUBERNETS_NAMESPACE"]
        self.kind = app_config["WDM_WL_KIND"]
        self.wlobjname = app_config["WDM_WL_OBJECT_NAME"]
        self.initiatorWLObjname = app_config["WDM_INITIATOR_WLOBJ_NAME"]
        self.timeout = app_config["WDM_TIMEOUT"]
        self.allocation_hash_name = app_config["WDM_POD_ALLOCATION_HASH_NAME"]
        self.allocation_regex_delimiter = app_config["WDM_POD_ALLOCATION_REGEX_DELIMITER"]
        self.app_config = app_config
        self.redis_connection = redis.StrictRedis(
            self.app_config["WDM_WL_REDIS_SERVER"],
            self.app_config["WDM_WL_REDIS_PORT"],
            encoding="utf-8",
            decode_responses=True,
            retry_on_timeout=True,
        )
        self.ready_replicas = 0
        self.cluster_type = app_config["WDM_CLUSTER_TYPE"]
        self.default_port = app_config["WDM_WL_CONFIG_PORT"]
        # config.load_incluster_config (configuration)
        return None

    # Return all allocation configs that have been set
    def get_current_allocation_configs(self):
        # Get regex based allocation info/pod mapping
        vals = self.redis_connection.hgetall(self.allocation_hash_name)
        ret_dict = {}
        for key, value in vals.items():
            ret_dict[key] = json.loads(value)
        return ret_dict
    
    # Match encoded name to an allocated pod. Returns pod info if match found, otherwise None. Expects format to be "region|group|name" where "|name" is optional
    def get_pod_info_by_encoded_name(self, encoded_name):
        split_name = encoded_name.split(self.allocation_regex_delimiter)
        if len(split_name) != 2 and len(split_name) != 3:
            logger.error(f"encoded name does not have 2 or 3 elements. Format is region|group|name where the final pipe and name are optional. encoded_name: {str(encoded_name)}, split_name: {str(split_name)}")
            return {}
        match_name = split_name[0] + self.allocation_regex_delimiter + split_name[1]
        return json.loads(self.redis_connection.hget(self.allocation_hash_name, match_name))
    
    def get_pod_allocation_by_podName(self, podName):
        allocations = self.get_current_allocation_configs()
        for encoded_name, allocation in allocations.items():
            if allocation["podName"] == podName:
                return {encoded_name: allocation}
        return None
    
    # Return all podNames for configs that have been set
    def get_current_allocation_pod_names(self):
        allocations = self.get_current_allocation_configs()
        names = [allocation["podName"] for encoded_name, allocation in allocations.items()]
        return names
    
    # Find first unallocated pod for regex usecase
    def find_unallocated_pod(self):
        wlobjs = self.getWorkloadObjects()
        if wlobjs is not None:
            pods = self.getPodIps(wlobjs)
        curr_pod_names = self.get_current_allocation_pod_names()
        for pod in pods:
            if pod["podName"] not in curr_pod_names:
                return pod
        
        return None
    
    # Update pod allocation config info
    def update_current_allocation_configs(self, config_details):
        curr_allocations = self.get_current_allocation_configs()
        if config_details["encoded_matching_name"] in curr_allocations:
            logger.info("Provided name exists as key - will overwrite")
        if "owner" in config_details:
            del config_details["owner"]
        ret = self.redis_connection.hset(self.allocation_hash_name, config_details["encoded_matching_name"], json.dumps(config_details))
        
        return ret
    
    # Delete pod allocation config info
    def delete_allocation_config(self, config_details):
        curr_allocations = self.get_current_allocation_configs()
        if config_details["encoded_matching_name"] not in curr_allocations:
            logger.info("Provided name not in dict - will ignore request")
            return None
        
        ret = self.redis_connection.hdel(self.allocation_hash_name, config_details["encoded_matching_name"])
        
        return ret

    def getReadyReplicas (self):
        return self.ready_replicas
    
    def setReadyReplicas (self, readyReplicaCount=0):
        self.ready_replicas = readyReplicaCount

    def getClient(self):
        return self.k8sclientCore

    def getPodIps(self, WLObject):
        podIps = []
        for i in WLObject:
            logger.info(
                "->>> %s\t%s\t%s\t%s\t%s\t%s"
                % (
                    i.status.pod_ip,
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
                "podPort": self.default_port,
                "poddns": None
                if subdomain is None
                else hostname + "." + subdomain + "." + self.namespace,
            }
            podIps.append(ipobj)

        if self.wlobjname is not None:

            def filterName(itm):
                c = len(
                    list(
                        filter(
                            lambda i: i.name.lower() == self.wlobjname.lower()
                            and i.kind.lower() == self.kind.lower(),
                            itm["owner"],
                        )
                    )
                )
                return True if c > 0 else False

            selectPodIp = list(filter(filterName, podIps))
            return selectPodIp if len(selectPodIp) > 0 else None

        return podIps if len(podIps) > 0 else None

    def _extractOwnerObject(self, wlObjName, selectPodIp):
        owners = selectPodIp

        def filterWithOwners(itm):
            c = len(
                list(
                    filter(
                        lambda i: i.name.lower() == wlObjName.lower()
                        and i.kind.lower() == self.kind.lower(),
                        itm["owner"],
                    )
                )
            )
            return True if c > 0 else False

        o = list(filter(filterWithOwners, owners))

        return o if len(o) > 0 else None

    def getWorkloadObjects(self):
        ret = self.k8sclientCore.list_namespaced_pod(namespace=self.namespace)

        def checkOwnerKind(item):
            if (
                item is None
                or item.metadata is None
                or item.metadata.owner_references is None
            ):
                return False

            c = len(
                list(
                    filter(
                        lambda i: i.kind.lower() == self.kind.lower(),
                        item.metadata.owner_references,
                    )
                )
            )
            return True if c > 0 else False

        selectedItems = list(filter(checkOwnerKind, ret.items))

        logger.debug("selected items " + self.kind)
        logger.info("kind  count of selected %d " % len(selectedItems))
        return selectedItems

    def getDeployments(self):
        self.k8sAppclientV1.list_namespaced_deployment(
            namespace=self.namespace, async_req=False
        )

    def getStatefulSets(self):
        ret = self.k8sAppclientV1.list_namespaced_stateful_set(
            namespace=self.namespace, async_req=False
        )
        if self.wlobjname is not None:
            selectedItems = list(
                filter(lambda x: x.metadata.name == self.wlobjname, ret.items)
            )
            return selectedItems[0] if len(selectedItems) > 0 else None
        return None

    def scaleStatefulsetPods(self, name=None, replicas=None):
        err = False
        if name is None:
            logger.error("error name is required")
            err = True

        if replicas is None:
            logger.error("replica is None")
            err = True

        if err:
            return
        try:
            self.k8sAppclientV1.patch_namespaced_stateful_set_scale(
                namespace=self.namespace,
                body={"spec": {"replicas": replicas}},
                name=self.wlobjname,
                async_req=False,
            )
            self.waitForPendingPodToBecomeReady()
        except ApiException as e:
            logger.info(
                "Exception when calling AppsV1Api->patch_namespaced_stateful_set_scale: %s\n"
                % e
            )

    def watchAndUpdateActiveReplicaCount (self):
        try:
            w = watch.Watch () 
            for s in w.stream(
                self.k8sAppclientV1.list_namespaced_stateful_set,
                namespace=self.namespace,
                #timeout_seconds=100,
            ):
                if s["object"].metadata.name == self.wlobjname:
                    logger.info(s["object"].metadata.name)
                    logger.info(s["object"].status.ready_replicas)
                    logger.debug (s["object"].status)
                    if s['object'].status.ready_replicas > s['object'].status.current_replicas:
                        logger.info ("using current replica")
                        self.setReadyReplicas (s['object'].status.current_replicas)
                    elif s['object'].status.ready_replicas > s['object'].status.available_replicas:
                        logger.info ("using available replica")
                        self.setReadyReplicas (s['object'].status.available_replicas)
                    else:
                        self.setReadyReplicas (s['object'].status.ready_replicas)
                        logger.info ("using ready replica")
        except:
            logger.info ("an exception occured trying to recover")

    def waitForPodsToRunningState (self, podname):
        w = watch.Watch()
        running = False
        for s in w.stream(
            self.k8sclientCore.list_namespaced_pod,
            namespace=self.namespace,
            #timeout_seconds=100,
        ):
            name = s["object"].metadata.name
            if name == podname:
                phase = s["object"].status.phase
                if phase.lower() == "running":
                    if s['object'].status.container_statuses is not None:
                        for cs in s["object"].status.container_statuses:
                            n = cs.name
                            r = cs.ready
                            s = cs.started
                            print (f"---- name -- {n}--rdy--{r}--strtd--{s}--------")
                            print (cs.state.running)
                            logger.info (f"---- name -- {n}--rdy--{r}--strtd--{s}--------")
                            if  s and not r and cs.state.running is not None:
                                running = True
                else:
                    print ("not running wait")
        return running

    def ifPodDown (self, podname):
        return True if podname in self.downpodsArray \
                else False

    def watchAllPodState(self):
        t0 = time.time()
        error = False
        podname = None
        #downpodsArray = []
        while True:
            w = watch.Watch()
            for s in w.stream(
                self.k8sclientCore.list_namespaced_pod,
                namespace=self.namespace,
                #timeout_seconds=100,
            ):
                print ( s )
    

    def watchPodState(self):
        t0 = time.time()
        error = False
        podname = None
        while True:
            w = watch.Watch()
            for s in w.stream(
                self.k8sclientCore.list_namespaced_pod,
                namespace=self.namespace,
            ):
                generate_name = s["object"].metadata.generate_name
                generate_name = generate_name[:-1] if generate_name is not None else ""
                if  generate_name.startswith (
                            self.initiatorWLObjname+"-"
                        ):
                    f = list(filter(lambda x: x == s["object"].metadata.name, self.downpodsArray))
                    if len(f) > 0 and s["type"] == "ADDED":
                        de = s["object"].metadata.name
                        logger.info(f"added new Pod {de} ")
                        continue

                    if len(f) > 0 and s["type"] == "DELETED":
                        de = s["object"].metadata.name
                        logger.info(f"deleting old Pod {de} ")
                        self.downpodsArray.remove (s["object"].metadata.name)
                        continue

                if generate_name == self.wlobjname or \
                    generate_name.startswith(
                            self.initiatorWLObjname+"-"
                        ):
                    name = s["object"].metadata.name
                    #p = self.k8sclientCore.read_namespaced_pod_status(name=name, namespace=self.namespace)
                    phase = s["object"].status.phase
                    podname = name
                    logger.debug(f"-{name}--PHASE--{phase}----------")
                    if phase.lower() != "running":
                        logger.debug(f"pod {name} went down {phase}")
                        #print (f"-{name}--PHASE--{phase}----------")
                        error = True
                    if s['object'].status.container_statuses is not None:
                        for cs in s["object"].status.container_statuses:
                            n = cs.name
                            r = cs.ready
                            s = cs.started
                            logger.debug(f"- name - {n}-rdy-{r}-strtd-{s}-")
                            if not s or not r or cs.state.running is None:
                                error = True
                                break
                    else:
                        logger.debug(f"{podname} --- NULL")
                    if podname is not None:
                        # print (f"{podname} --- error {error}")
                        if error:
                            f = list(
                                filter(
                                    lambda x: x == podname, self.downpodsArray
                                )
                            )
                            if len(f) == 0:
                                logger.info(
                                    f"Pod {podname} add to Error State"
                                )
                                self.downpodsArray.append(podname)
                                yield error, podname, generate_name
                            error = False
                        else:
                            f = list(
                                filter(
                                    lambda x: x == podname, self.downpodsArray
                                )
                            )
                            if len(f) != 0:
                                logger.info(
                                    f"Pod {podname} remove from Error State"
                                )
                                self.downpodsArray.remove(podname)
                                yield error, podname, generate_name

    def waitForPendingPodToBecomeReady(self):
        logger.info ("Waiting for pending pod to become ready")
        t0 = time.time()
        exitWatch = False
        while True:
            logger.info( "Watch ... ")
            w = watch.Watch()
            for s in w.stream(
                self.k8sclientCore.list_namespaced_pod,
                namespace=self.namespace,
                timeout_seconds=60
            ):
                logger.info( "Waiting for Pending Pods %s" % (s["object"].metadata.name))

                WLObj = self.getWorkloadObjects()
                podsInfo = self.getPodIps(WLObj)
                f = list(filter(lambda x: x["phase"] != "Running", podsInfo))
                if f is not None and len(f) == 0:
                    logger.info ("No more pending pod to become ready")
                    exitWatch = True
                    break
            t1 = time.time()
            if (t1 - t0) > self.timeout:
                logger.error("Watch timed out ")
                break
            if exitWatch:
                break;

        return

    def restartRouterDeployment(self):
        now = datetime.now()
        self.k8sAppclientV1.patch_namespaced_deployment(
            namespace=self.namespace,
            body={
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            },
            name=self.app_config["WDM_WL_ROUTER"],
        )

    def _makeNginxProxyPass(self, id, hostname, port, proxyUrl):
        t = Template(
            """
                    location /$id {
                        proxy_pass http://$hostname:$port$proxyUrl;
                    }
        """
        )
        return t.substitute(
            id=id, hostname=hostname, port=port, proxyUrl=proxyUrl
        )

    def _removeNginxProxyPass(self, configdata, id, hostname, port, proxyUrl):
        regex = re.compile(
            pattern=r"(.+)location\s+/%s\s+{\n\s+? \
                proxy_pass\s+http://%s:%d%s;\n(.+)}"
            % (id, hostname, int(port), proxyUrl),
            flags=re.MULTILINE,
        )
        return regex.sub("#removed %s" % (id), configdata)

    def _checkNginxProxyPassExists(
            self, configdata, id, hostname, port, proxyUrl
    ):
        p = \
            r"(.+)location\s+/%s\s+{\n\s+? \
            proxy_pass\s+http://.+:%d%s;\n\s+}(.+)" \
            % (id, int(port), proxyUrl)
        if (
            re.match(
                pattern=p,
                string=configdata,
                flags=re.MULTILINE | re.DOTALL,
            )
            is not None
        ):
            return True
        else:
            return False

    def updateRouteMapping(
            self,
            k8sWLobjName,
            id,
            podInfoItm,
            operation="add"
    ):
        pipe = self.redis_connection.pipeline()
        if operation == "add":
            pipe.hset(k8sWLobjName, id, podInfoItm["podName"])
            pipe.hset(k8sWLobjName + "-pod", podInfoItm["podName"], podInfoItm["poddns"])
        else:
            pipe.hdel(k8sWLobjName, id)
        pipe.incr(k8sWLobjName + "-xds-version")
        pipe.execute()

    def updateRouterConfigMap(self, id, podInfoItm, operation="add"):
        replaceBody = False
        if podInfoItm is not None:
            response = self.k8sclientCore.read_namespaced_config_map(
                name=self.app_config["WDM_WL_ROUTER_CONFIG_MAP"],
                namespace=self.namespace,
            )

            body = response

            if operation == "add":
                if not self._checkNginxProxyPassExists(
                    body.data["default.conf"],
                    id,
                    podInfoItm["poddns"],
                    self.app_config["WDM_WL_CONFIG_PORT"],
                    self.app_config["WDM_WL_PROXY_URL"],
                ):

                    p = self._makeNginxProxyPass(
                        id,
                        podInfoItm["poddns"],
                        self.app_config["WDM_WL_CONFIG_PORT"],
                        self.app_config["WDM_WL_PROXY_URL"],
                    )
                    body.data["default.conf"] = \
                        response.data["default.conf"] + "\n" + p
                    replaceBody = True
            elif operation == "remove":
                if self._checkNginxProxyPassExists(
                    body.data["default.conf"],
                    id,
                    podInfoItm["poddns"],
                    self.app_config["WDM_WL_CONFIG_PORT"],
                    self.app_config["WDM_WL_PROXY_URL"],
                ):

                    body.data["default.conf"] = self._removeNginxProxyPass(
                        body.data["default.conf"],
                        id,
                        podInfoItm["poddns"],
                        self.app_config["WDM_WL_CONFIG_PORT"],
                        self.app_config["WDM_WL_PROXY_URL"],
                    )
                    replaceBody = True
            if replaceBody:
                self.k8sclientCore.replace_namespaced_config_map(
                    name=self.app_config["WDM_WL_ROUTER_CONFIG_MAP"],
                    namespace=self.namespace,
                    body=body,
                )

        return replaceBody
    
    def get_podname_keys(self):
        return []


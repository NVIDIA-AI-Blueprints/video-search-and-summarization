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
from lib.podprovisioner.kubernetes.dockerclient import dockerclient
from lib.podprovisioner.kubernetes.k8sheadlessclient import k8sheadlessclient
import logging

logger = logging.getLogger(__name__)

class cluster():
    def __init__(self, app_config, **kvargs):
        self.cluster_type = app_config["WDM_CLUSTER_TYPE"].lower()

        if self.cluster_type == "k8s":
            self.client = k8sclient(app_config, **kvargs)
        elif self.cluster_type == "k8s-headless":
            self.client = k8sheadlessclient(app_config, **kvargs)
        else:
            if self.cluster_type != "docker":
                logger.error("UNKNOWN CLUSTER TYPE - DEFAULTING TO DOCKER")
            
            self.client = dockerclient(app_config, **kvargs)
        
        return None
    
    def get_current_allocation_configs(self):
        return self.client.get_current_allocation_configs()
    
    def get_pod_info_by_encoded_name(self, encoded_name):
        return self.client.get_pod_info_by_encoded_name(encoded_name)
    
    def get_pod_allocation_by_podName(self, podName):
        return self.client.get_pod_allocation_by_podName(podName)
    
    def get_current_allocation_pod_names(self):
        return self.client.get_current_allocation_pod_names()
    
    def find_unallocated_pod(self):
        return self.client.find_unallocated_pod()
    
    def update_current_allocation_configs(self, config_details):
        return self.client.update_current_allocation_configs(config_details)
    
    def delete_allocation_config(self, config_details):
        return self.client.delete_allocation_config(config_details)

    def getReadyReplicas(self):
        return self.client.getReadyReplicas()
    
    def setReadyReplicas (self, readyReplicaCount=0):
        return self.client.setReadyReplicas(readyReplicaCount)

    def getClient(self):
        return self.client.getClient()

    def getPodIps(self, WLObject):
        return self.client.getPodIps(WLObject)

    def _extractOwnerObject(self, wlObjName, selectPodIp):
        return self.client._extractOwnerObject(wlObjName, selectPodIp)
    
    def getWorkloadObjects(self):
        return self.client.getWorkloadObjects()
    
    def getDeployments(self):
        return self.client.getDeployments()
        
    def getStatefulSets(self):
        return self.client.getStatefulSets()

    def scaleStatefulsetPods(self, name=None, replicas=None):
        return self.client.scaleStatefulsetPods(name, replicas)
    
    def watchAndUpdateActiveReplicaCount (self):
        return self.client.watchAndUpdateActiveReplicaCount()

    def waitForPodsToRunningState (self, podname):
        return self.client.waitForPodsToRunningState(podname)

    def ifPodDown (self, podname):
        return self.client.ifPodDown(podname)

    def watchAllPodState(self):
        return self.client.watchAllPodState()
    
    def watchPodState(self):
        logger.info ("watching pods in kubernetes")
        for result in self.client.watchPodState():
            if len(result) == 3:
                e, p, g = result
                old_ip, new_ip = None, None
            else:
                e, p, g, old_ip, new_ip = result
            yield e, p, g, old_ip, new_ip

    def waitForPendingPodToBecomeReady(self):
        return self.client.waitForPendingPodToBecomeReady()

    def restartRouterDeployment(self):
        return self.client.restartRouterDeployment()

    def _makeNginxProxyPass(self, id, hostname, port, proxyUrl):
        return self.client._makeNginxProxyPass(id, hostname, port, proxyUrl)

    def _removeNginxProxyPass(self, configdata, id, hostname, port, proxyUrl):
        return self.client._removeNginxProxyPass(configdata, id, hostname, port, proxyUrl)

    def _checkNginxProxyPassExists(
            self, configdata, id, hostname, port, proxyUrl
    ):
        return self.client._checkNginxProxyPassExists(configdata, id, hostname, port, proxyUrl)

    def updateRouteMapping(
            self,
            k8sWLobjName,
            id,
            podInfoItm,
            operation="add"
    ):
        return self.client.updateRouteMapping(k8sWLobjName, id, podInfoItm, operation)

    def updateRouterConfigMap(self, id, podInfoItm, operation="add"):
        return self.client.updateRouterConfigMap(id, podInfoItm, operation)
        
    def get_podname_keys(self):
        return self.client.get_podname_keys()

    def disaggregate_podInfo(self, pod_data):
        """Extract owner information to separate fields"""
        result = pod_data.copy()
        
        if 'owner' in result and result['owner']:
            # Get the first owner (assuming single owner)
            owner = result['owner'][0]
            
            # Add owner fields at top level
            if hasattr(owner, 'api_version'):  # It's a V1OwnerReference object
                result['owner_api_version'] = owner.api_version
                result['owner_block_owner_deletion'] = owner.block_owner_deletion
                result['owner_controller'] = owner.controller
                result['owner_kind'] = owner.kind
                result['owner_name'] = owner.name
                result['owner_uid'] = owner.uid
            else:  # It's a dictionary
                result['owner_api_version'] = owner.get('api_version', '')
                result['owner_block_owner_deletion'] = owner.get('block_owner_deletion', False)
                result['owner_controller'] = owner.get('controller', False)
                result['owner_kind'] = owner.get('kind', '')
                result['owner_name'] = owner.get('name', '')
                result['owner_uid'] = owner.get('uid', '')
            del result['owner']
        else:
            # Set default values if no owner
            result['owner_api_version'] = ''
            result['owner_block_owner_deletion'] = False
            result['owner_controller'] = False
            result['owner_kind'] = ''
            result['owner_name'] = ''
            result['owner_uid'] = ''
        return result

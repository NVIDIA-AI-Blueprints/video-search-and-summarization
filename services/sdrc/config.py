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
import os
import pathlib


def _bool_env(name, default=False):
    if name not in os.environ or os.environ[name].strip() == "":
        return default
    return os.environ[name].strip().lower() == "true"


class Config(object):
    DEBUG = False
    TESTING = False
    PORT = os.environ["PORT"] if "PORT" in os.environ else 4000

    KAFKA_URL = (
        os.environ["KAFKA_URL"]
        if "KAFKA_URL" in os.environ and os.environ["KAFKA_URL"].strip() != ""
        else None
    )
    token_path = pathlib.Path("/run/secrets/kubernetes.io/serviceaccount/token")
    if (
        "KUBERNETES_JWT_TOKEN" in os.environ
        and os.environ["KUBERNETES_JWT_TOKEN"].strip() != ""
    ):
        KUBERNETES_JWT_TOKEN = os.environ["KUBERNETES_JWT_TOKEN"]
    elif token_path.is_file():
        KUBERNETES_JWT_TOKEN = token_path.read_text()
    else:
        KUBERNETES_JWT_TOKEN = ""

    ns_file = "/run/secrets/kubernetes.io/serviceaccount/namespace"
    KUBERNETS_NAMESPACE = "default"
    if pathlib.Path(ns_file).is_file():
        with open(ns_file, "r") as _ns_f:
            KUBERNETS_NAMESPACE = (_ns_f.read() or "").strip() or "default"
    # Out-of-cluster / compose: env KUBERNETS_NAMESPACE (matches Config attribute name).
    # Legacy: env NAMESPACE is still accepted.
    _ns_override = os.environ.get("KUBERNETS_NAMESPACE", "").strip() or os.environ.get(
        "NAMESPACE", ""
    ).strip()
    if _ns_override:
        KUBERNETS_NAMESPACE = _ns_override
    defaultBrokers = "localhost:9092"
    KUBERNETES_URL = "https://localhost:6443"
    if pathlib.Path("/run/secrets/kubernetes.io").is_dir():
        KUBERNETES_URL = "https://kubernetes.default.svc"

    SSL_CERTS = "/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    ssl_path = pathlib.Path(SSL_CERTS)
    SSL_CERTS = SSL_CERTS if ssl_path.is_file() else "/etc/kubernetes/pki/ca.crt"

    KUBERNETES_URL = (
        "https://{}:{}".format(os.environ["KUBERNETES_HOST"],  os.environ["KUBERNETES_PORT"])
        if ("KUBERNETES_HOST" in os.environ and os.environ["KUBERNETES_HOST"].strip() != ""
            and "KUBERNETES_PORT" in os.environ and os.environ["KUBERNETES_PORT"].strip() != "")
        else "https://kubernetes.{}.svc".format (KUBERNETS_NAMESPACE)
    )

    WDM_MSG_BUS = (
        os.environ["WDM_MSG_BUS"]
        if "WDM_MSG_BUS" in os.environ and os.environ["WDM_MSG_BUS"].strip() != ""
        else "kafka"
    )
    WDM_KAFKA_MSG_KEY = (
        os.environ["WDM_KAFKA_MSG_KEY"]
        if "WDM_KAFKA_MSG_KEY" in os.environ and os.environ["WDM_KAFKA_MSG_KEY"].strip() != ""
        else "sensor"
    )
    WDM_REDIS_MSG_KEY = (
        os.environ["WDM_REDIS_MSG_KEY"]
        if "WDM_REDIS_MSG_KEY" in os.environ and os.environ["WDM_REDIS_MSG_KEY"].strip() != ""
        else "vst_events"
    )
    WDM_MSG_TOPIC = (
        os.environ["WDM_MSG_TOPIC"]
        if "WDM_MSG_TOPIC" in os.environ and os.environ["WDM_MSG_TOPIC"].strip() != ""
        else "mdx-notification"
    )
    WDM_WL_SPEC = (
        os.environ["WDM_WL_SPEC"]
        if "WDM_WL_SPEC" in os.environ and os.environ["WDM_WL_SPEC"].strip() != ""
        else "./tests/data_wl.yaml"
    )
    WDM_CONSUMER_GRP_ID = (
        os.environ["WDM_CONSUMER_GRP_ID"]
        if "WDM_CONSUMER_GRP_ID" in os.environ
        and os.environ["WDM_CONSUMER_GRP_ID"].strip() != ""
        else "consumer-grp-id-3"
    )
    WDM_KFK_ENABLE = (
        False
        if "WDM_KFK_ENABLE" in os.environ
        and os.environ["WDM_KFK_ENABLE"].strip() != ""
        and os.environ["WDM_KFK_ENABLE"].strip().lower() == "false"
        else True
    )
    WDM_KFK_BOOTSTRAP_URL = (
        os.environ["WDM_KFK_BOOTSTRAP_URL"]
        if "WDM_KFK_BOOTSTRAP_URL" in os.environ
        and os.environ["WDM_KFK_BOOTSTRAP_URL"].strip() != ""
        else defaultBrokers
    )
    WDM_KFK_SESSION_TIME_OUT = (
        int(os.environ["WDM_KFK_SESSION_TIME_OUT"])
        if "WDM_KFK_SESSION_TIME_OUT" in os.environ
        and os.environ["WDM_KFK_SESSION_TIME_OUT"].strip() != ""
        else int("30000")
    )
    WDM_MAX_PER_POD = (
        int(os.environ["WDM_MAX_PER_POD"])
        if "WDM_MAX_PER_POD" in os.environ
        and os.environ["WDM_MAX_PER_POD"].strip() != ""
        else int("0")
    )
    WDM_WL_OBJECT_NAME = (
        os.environ["WDM_WL_OBJECT_NAME"]
        if "WDM_WL_OBJECT_NAME" in os.environ
        and os.environ["WDM_WL_OBJECT_NAME"].strip() != ""
        else "sdrc-workload"
    )
    WDM_WL_ID_FIELD = (
        os.environ["WDM_WL_ID_FIELD"]
        if "WDM_WL_ID_FIELD" in os.environ
        and os.environ["WDM_WL_ID_FIELD"].strip() != ""
        else "camera_id"
    )
    WDM_EVENT_OBJECT_FIELD = (
        os.environ["WDM_EVENT_OBJECT_FIELD"]
        if "WDM_EVENT_OBJECT_FIELD" in os.environ
        and os.environ["WDM_EVENT_OBJECT_FIELD"].strip() != ""
        else "event"
    )
    WDM_WL_NAME_IGNORE_REGEX = (
        os.environ["WDM_WL_NAME_IGNORE_REGEX"]
        if "WDM_WL_NAME_IGNORE_REGEX" in os.environ
        and os.environ["WDM_WL_NAME_IGNORE_REGEX"].strip() != ""
        else ""
    )
    WDM_WL_THRESHOLD = (
        int(os.environ["WDM_WL_THRESHOLD"])
        if "WDM_WL_THRESHOLD" in os.environ
        and os.environ["WDM_WL_THRESHOLD"].strip() != ""
        else 8 # TODO: update based on config input
    )
    # Pod selection when adding streams: "lru_round_robin" (fewest streams,
    # ordinal/name tie-break) or "sequential" (first eligible from getPodIps).
    WDM_WL_ASSIGNING_METHOD = (
        os.environ["WDM_WL_ASSIGNING_METHOD"].strip().lower()
        if "WDM_WL_ASSIGNING_METHOD" in os.environ
        and os.environ["WDM_WL_ASSIGNING_METHOD"].strip() != ""
        else "sequential"
    )
    WDM_PROVISION_LEASE_SECONDS = (
        int(os.environ["WDM_PROVISION_LEASE_SECONDS"])
        if "WDM_PROVISION_LEASE_SECONDS" in os.environ
        and os.environ["WDM_PROVISION_LEASE_SECONDS"].strip() != ""
        else 120
    )

    WDM_CONFIG_URL = (
        os.environ["WDM_CONFIG_URL"]
        if "WDM_CONFIG_URL" in os.environ
        else "/config"
    )

    WDM_CONFIG_PORT = (
        os.environ["WDM_CONFIG_PORT"]
        if "WDM_CONFIG_PORT" in os.environ
        and os.environ["WDM_CONFIG_PORT"].strip() != ""
        else "9002"
    )

    WDM_WL_ADD_URL = (
        os.environ["WDM_WL_ADD_URL"].strip()
        if "WDM_WL_ADD_URL" in os.environ
        else "/api/v1/stream/add"
    )
    WDM_WL_HEALTH_CHECK_URL = (
        os.environ["WDM_WL_HEALTH_CHECK_URL"]
        if "WDM_WL_HEALTH_CHECK_URL" in os.environ and os.environ["WDM_WL_HEALTH_CHECK_URL"].strip() != ""
        else "/api/v1/stream/add"
    )
    WDM_WL_DELETE_URL = (
        os.environ["WDM_WL_DELETE_URL"].strip()
        if "WDM_WL_DELETE_URL" in os.environ
        else "/api/v1/stream/remove"
    )
    WDM_LIFECYCLE_INGRESS_MODE = (
        os.environ["WDM_LIFECYCLE_INGRESS_MODE"].strip()
        if "WDM_LIFECYCLE_INGRESS_MODE" in os.environ
        and os.environ["WDM_LIFECYCLE_INGRESS_MODE"].strip() != ""
        else "message-bus"
    )
    WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER"].strip()
        if "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER"].strip() != ""
        else (
            os.environ["ENVOY_ROUTE_HEADER"].strip()
            if "ENVOY_ROUTE_HEADER" in os.environ
            and os.environ["ENVOY_ROUTE_HEADER"].strip() != ""
            else (
                os.environ["ENVOYROUTEHEADER"].strip()
                if "ENVOYROUTEHEADER" in os.environ
                and os.environ["ENVOYROUTEHEADER"].strip() != ""
                else "id"
            )
        )
    )
    WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH"].strip()
        if "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH"].strip() != ""
        else "/sdrc/v1/streams"
    )
    WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD"].strip().upper()
        if "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD"].strip() != ""
        else "POST"
    )
    WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH"].strip()
        if "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH"].strip() != ""
        else "/sdrc/v1/streams"
    )
    WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD"].strip().upper()
        if "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD"].strip() != ""
        else "DELETE"
    )
    WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH"].strip()
        if "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH"].strip() != ""
        else "/sdrc/v1/streams/reprovision"
    )
    WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD = (
        os.environ["WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD"].strip().upper()
        if "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD" in os.environ
        and os.environ["WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD"].strip() != ""
        else "POST"
    )
    WDM_WL_CONFIG_PORT = (
        os.environ["WDM_WL_CONFIG_PORT"]
        if "WDM_WL_CONFIG_PORT" in os.environ
        and os.environ["WDM_WL_CONFIG_PORT"].strip() != ""
        else "5000"
    )
    WDM_TARGET_PORT_MAPPING = (
        json.loads(os.environ["WDM_TARGET_PORT_MAPPING"])
        if "WDM_TARGET_PORT_MAPPING" in os.environ
        and os.environ["WDM_TARGET_PORT_MAPPING"].strip() != ""
        else json.loads('{"default": 5000, "grpc": 50052}')
    )
    # CDS (clusterXDS): default pod DNS (STRICT_DNS); set WDM_XDS_USE_IP_ADDRESS for STATIC + pod IP.
    WDM_XDS_USE_IP_ADDRESS = (
        os.environ["WDM_XDS_USE_IP_ADDRESS"].strip().lower()
        not in ("0", "false", "no", "off", "")
        if "WDM_XDS_USE_IP_ADDRESS" in os.environ
        and os.environ["WDM_XDS_USE_IP_ADDRESS"].strip() != ""
        else False
    )
    WDM_XDS_USE_POD_DNS = (
        os.environ["WDM_XDS_USE_POD_DNS"].strip().lower()
        not in ("0", "false", "no", "off", "")
        if "WDM_XDS_USE_POD_DNS" in os.environ
        and os.environ["WDM_XDS_USE_POD_DNS"].strip() != ""
        else True
    )
    # gRPC ADS is opt-in. Default stays on the existing REST CDS/RDS xDS path.
    WDM_XDS_GRPC_ADS_ENABLED = (
        os.environ["WDM_XDS_GRPC_ADS_ENABLED"].strip().lower()
        in ("1", "true", "yes", "on")
        if "WDM_XDS_GRPC_ADS_ENABLED" in os.environ
        and os.environ["WDM_XDS_GRPC_ADS_ENABLED"].strip() != ""
        else False
    )
    GRPC_XDS_PORT = (
        int(os.environ["GRPC_XDS_PORT"])
        if "GRPC_XDS_PORT" in os.environ
        and os.environ["GRPC_XDS_PORT"].strip() != ""
        else 4001
    )
    WDM_WL_KIND = (
        os.environ["WDM_WL_KIND"]
        if "WDM_WL_KIND" in os.environ and os.environ["WDM_WL_KIND"].strip() != ""
        else "StatefulSet"
    )
    WDM_TIMEOUT = (
        int(os.environ["WDM_TIMEOUT"])
        if "WDM_TIMEOUT" in os.environ and os.environ["WDM_TIMEOUT"].strip() != ""
        else 300
    )
    WDM_WL_PROXY_URL = (
        os.environ["WDM_WL_PROXY_URL"]
        if "WDM_WL_PROXY_URL" in os.environ
        and os.environ["WDM_WL_PROXY_URL"].strip() != ""
        else "/hello"
    )
    WDM_WL_ROUTER = (
        os.environ["WDM_WL_ROUTER"]
        if "WDM_WL_ROUTER" in os.environ and os.environ["WDM_WL_ROUTER"].strip() != ""
        else "nginx-dep"
    )
    WDM_WL_ROUTER_CONFIG_MAP = (
        os.environ["WDM_WL_ROUTER_CONFIG_MAP"]
        if "WDM_WL_ROUTER_CONFIG_MAP" in os.environ
        and os.environ["WDM_WL_ROUTER_CONFIG_MAP"].strip() != ""
        else "nginx-cfgmap-def"
    )
    WDM_MIN_PODS = (
        int(os.environ["WDM_MIN_PODS"])
        if "WDM_MIN_PODS" in os.environ and os.environ["WDM_MIN_PODS"].strip() != ""
        else int("0")
    )
    WDM_WL_REDIS_SERVER = (
        os.environ["WDM_WL_REDIS_SERVER"]
        if "WDM_WL_REDIS_SERVER" in os.environ
        and os.environ["WDM_WL_REDIS_SERVER"].strip() != ""
        else "localhost"
    )
    WDM_WL_REDIS_PORT = (
        os.environ["WDM_WL_REDIS_PORT"]
        if "WDM_WL_REDIS_PORT" in os.environ
        and os.environ["WDM_WL_REDIS_PORT"].strip() != ""
        else 6379
    )
    WDM_WL_REDIS_MSG_FIELD = (
        os.environ["WDM_WL_REDIS_MSG_FIELD"]
        if "WDM_WL_REDIS_MSG_FIELD" in os.environ
        and os.environ["WDM_WL_REDIS_MSG_FIELD"].strip() != ""
        else "sensor.id"
    )
    ENVOYROUTEHEADER = (
        os.environ["ENVOYROUTEHEADER"]
        if "ENVOYROUTEHEADER" in os.environ
        and os.environ["ENVOYROUTEHEADER"].strip() != ""
        else "id"
    )
    ENVOY_ROUTE_URL_PREFIX_REWRITE = (
        os.environ["ENVOY_ROUTE_URL_PREFIX_REWRITE"]
        if "ENVOY_ROUTE_URL_PREFIX_REWRITE" in os.environ
        and os.environ["ENVOY_ROUTE_URL_PREFIX_REWRITE"].strip() != ""
        else "/hello"
    )
    ENVOY_ROUTE_URL_PREFIX = (
        os.environ["ENVOY_ROUTE_URL_PREFIX"]
        if "ENVOY_ROUTE_URL_PREFIX" in os.environ
        and os.environ["ENVOY_ROUTE_URL_PREFIX"].strip() != ""
        else "/"
    )

    WDM_FORWARD_MSG_TYPE = (
        os.environ["WDM_FORWARD_MSG_TYPE"]
        if "WDM_FORWARD_MSG_TYPE" in os.environ
        and os.environ["WDM_FORWARD_MSG_TYPE"].strip() != ""
        else "event_message"
    )

    WDM_EVENT_OBJECT_FIELD = (
        os.environ["WDM_EVENT_OBJECT_FIELD"]
        if "WDM_EVENT_OBJECT_FIELD" in os.environ
        and os.environ["WDM_EVENT_OBJECT_FIELD"].strip() != ""
        else "event"
    )

    WDM_MAX_REPLICAS = (
        os.environ["WDM_MAX_REPLICAS"]
        if "WDM_MAX_REPLICAS" in os.environ
        and os.environ["WDM_MAX_REPLICAS"].strip() != ""
        else "4"
    )

    WDM_PRELOAD_WORKLOAD = (
        os.environ["WDM_PRELOAD_WORKLOAD"]
        if "WDM_PRELOAD_WORKLOAD" in os.environ
        and os.environ["WDM_PRELOAD_WORKLOAD"].strip() != ""
        else "configs/event_pre-roll.json"
    )

    WDM_WL_CHANGE_FIELD = (
        os.environ["WDM_WL_CHANGE_FIELD"]
        if "WDM_WL_CHANGE_FIELD" in os.environ
        and os.environ["WDM_WL_CHANGE_FIELD"].strip() != ""
        else "change"
    )

    WDM_WL_CHANGE_ID_ADD = (
        os.environ["WDM_WL_CHANGE_ID_ADD"]
        if "WDM_WL_CHANGE_ID_ADD" in os.environ
        and os.environ["WDM_WL_CHANGE_ID_ADD"].strip() != ""
        else "camera_streaming"
    )

    WDM_WL_CHANGE_ID_REPROVISION = (
        os.environ["WDM_WL_CHANGE_ID_REPROVISION"]
        if "WDM_WL_CHANGE_ID_REPROVISION" in os.environ
        and os.environ["WDM_WL_CHANGE_ID_REPROVISION"].strip() != ""
        else "reprovision"
    )

    WDM_WL_CHANGE_ID_DEL = (
        os.environ["WDM_WL_CHANGE_ID_DEL"]
        if "WDM_WL_CHANGE_ID_DEL" in os.environ
        and os.environ["WDM_WL_CHANGE_ID_DEL"].strip() != ""
        else "camera_remove"
    )
    WDM_WL_CHANGE_ID_POD_CONFIGURE = (
        os.environ["WDM_WL_CHANGE_ID_POD_CONFIGURE"]
        if "WDM_WL_CHANGE_ID_POD_CONFIGURE" in os.environ
        and os.environ["WDM_WL_CHANGE_ID_POD_CONFIGURE"].strip() != ""
        else "config"
    )
    WDM_ERROR_EVENT_MSG_KEY = (
        os.environ["WDM_ERROR_EVENT_MSG_KEY"]
        if "WDM_ERROR_EVENT_MSG_KEY" in os.environ
        and os.environ["WDM_ERROR_EVENT_MSG_KEY"].strip() != ""
        else "wdm_error_events"
    )

    WDM_EVICT_QUEUE_ON_NO_CAPACITY = (
        os.environ["WDM_EVICT_QUEUE_ON_NO_CAPACITY"]
        if "WDM_EVICT_QUEUE_ON_NO_CAPACITY" in os.environ
        and os.environ["WDM_EVICT_QUEUE_ON_NO_CAPACITY"].strip() != ""
        else "True"
    )

    WDM_INITIATOR_WLOBJ_NAME = (
        os.environ["WDM_INITIATOR_WLOBJ_NAME"]
        if "WDM_INITIATOR_WLOBJ_NAME" in os.environ
        and os.environ["WDM_INITIATOR_WLOBJ_NAME"].strip() != ""
        else "vms-vms"
    )

    WDM_MAP_ADD_FIELD = (
        os.environ["WDM_MAP_ADD_FIELD"]
        if "WDM_MAP_ADD_FIELD" in os.environ
        and os.environ["WDM_MAP_ADD_FIELD"].strip() != ""
        else ''
#        else '{"camera_streaming": "camera_add"}'
    )

    WDM_REMAP_EVENT_OBJECT = (
        os.environ["WDM_REMAP_EVENT_OBJECT"]
        if "WDM_REMAP_EVENT_OBJECT" in os.environ
        and os.environ["WDM_REMAP_EVENT_OBJECT"].strip() != ""
        else ''
#        else '{"event": "values"}'
    )

    WDM_ENVOY_ADMIN_URL = (
        os.environ["WDM_ENVOY_ADMIN_URL"]
        if "WDM_ENVOY_ADMIN_URL" in os.environ
        and os.environ["WDM_ENVOY_ADMIN_URL"].strip() != ""
        else 'http://localhost:9901'
#        else '{"event": "values"}'
    )

    WDM_CHECK_STATUS = (
        True
        if "WDM_CHECK_STATUS" in os.environ
        and os.environ["WDM_CHECK_STATUS"].strip() != ""
        and os.environ["WDM_CHECK_STATUS"].strip() == "True"
        else False
#        else '{"event": "values"}'
    )

    WDM_ERROR_BUS_MSG_VERSION = (
        os.environ["WDM_ERROR_BUS_MSG_VERSION"].strip ()
        if "WDM_ERROR_BUS_MSG_VERSION" in os.environ
        and os.environ["WDM_ERROR_BUS_MSG_VERSION"].strip() != ""
        else "v2"
#        else '{"event": "values"}'
    )


    WDM_EXT_ERROR_MSG = (
        os.environ["WDM_EXT_ERROR_MSG"].strip ()
        if "WDM_EXT_ERROR_MSG" in os.environ
        and os.environ["WDM_EXT_ERROR_MSG"].strip() != ""
        else "please wait a few minutes and refresh the console"
    )
    WDM_CLUSTER_TYPE = (
        os.environ["WDM_CLUSTER_TYPE"].strip ()
        if "WDM_CLUSTER_TYPE" in os.environ
        and os.environ["WDM_CLUSTER_TYPE"].strip() != ""
        else "docker"
    )
    WDM_CLUSTER_CONFIG_FILE = (
        os.environ["WDM_CLUSTER_CONFIG_FILE"].strip ()
        if "WDM_CLUSTER_CONFIG_FILE" in os.environ
        and os.environ["WDM_CLUSTER_CONFIG_FILE"].strip() != ""
        else "docker_cluster_config.json"
    )
    WDM_CLUSTER_CONTAINER_NAMES = (
        os.environ["WDM_CLUSTER_CONTAINER_NAMES"].strip ()
        if "WDM_CLUSTER_CONTAINER_NAMES" in os.environ
        and os.environ["WDM_CLUSTER_CONTAINER_NAMES"].strip() != ""
        else "[\"sdr\", \"deepstream\", \"vst\"]"
    )
    WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES = (
        os.environ["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"].strip ()
        if "WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES" in os.environ
        and os.environ["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"].strip() != ""
        else "[\"deepstream\"]"
    )
    WDM_DOCKER_CLUSTER_POD_DOWN_NAMES = (
        os.environ["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"].strip ()
        if "WDM_DOCKER_CLUSTER_POD_DOWN_NAMES" in os.environ
        and os.environ["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"].strip() != ""
        else "[\"vst\"]"
    )
    VST_STREAMS_ENDPOINT = (
        os.environ["VST_STREAMS_ENDPOINT"].strip ()
        if "VST_STREAMS_ENDPOINT" in os.environ
        and os.environ["VST_STREAMS_ENDPOINT"].strip() != ""
        else "http://localhost:81/api/v1/live/streams"
    )
    VST_STATUS_ENDPOINT = (
        os.environ["VST_STATUS_ENDPOINT"].strip ()
        if "VST_STATUS_ENDPOINT" in os.environ
        and os.environ["VST_STATUS_ENDPOINT"].strip() != ""
        else "http://localhost:81/api/v1/sensor/status"
    )
    WDM_CHECK_VST_STREAM_IS_ONLINE = (
        True
        if "WDM_CHECK_VST_STREAM_IS_ONLINE" in os.environ
        and os.environ["WDM_CHECK_VST_STREAM_IS_ONLINE"].strip() != ""
        and os.environ["WDM_CHECK_VST_STREAM_IS_ONLINE"].strip().lower() == "true"
        else False
    )
    WDM_INITIALIZE_FROM_VST = (
        False
        if "WDM_INITIALIZE_FROM_VST" in os.environ
        and os.environ["WDM_INITIALIZE_FROM_VST"].strip() != ""
        and os.environ["WDM_INITIALIZE_FROM_VST"].strip().lower() == "false"
        else True
    )
    WDM_CLEAR_DATA_WL = (
        True
        if "WDM_CLEAR_DATA_WL" in os.environ
        and os.environ["WDM_CLEAR_DATA_WL"].strip() != ""
        and os.environ["WDM_CLEAR_DATA_WL"].strip().lower() == "true"
        else False
    )
    WDM_DS_SWAP_ID_NAME = (
        True
        if "WDM_DS_SWAP_ID_NAME" in os.environ
        and os.environ["WDM_DS_SWAP_ID_NAME"].strip() != ""
        and os.environ["WDM_DS_SWAP_ID_NAME"].strip().lower() == "true"
        else False
    )
    # Only needed if WDM_DS_SWAP_ID_NAME and/or WDM_WL_NAME_IGNORE_REGEX is true
    WDM_WL_SWAP_KEY_SECONDARY_FIELD = (
        os.environ["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]
        if "WDM_WL_SWAP_KEY_SECONDARY_FIELD" in os.environ
        and os.environ["WDM_WL_SWAP_KEY_SECONDARY_FIELD"].strip() != ""
        else "camera_name"
    )
    WDM_VALIDATE_BEFORE_ADD = (
        True
        if "WDM_VALIDATE_BEFORE_ADD" in os.environ
        and os.environ["WDM_VALIDATE_BEFORE_ADD"].strip() != ""
        and os.environ["WDM_VALIDATE_BEFORE_ADD"].strip().lower() == "true"
        else False
    )
    # Only needed if WDM_VALIDATE_BEFORE_ADD is true
    WDM_JSON_EXPECTED_KEYS = (
        os.environ["WDM_JSON_EXPECTED_KEYS"].strip ()
        if "WDM_JSON_EXPECTED_KEYS" in os.environ
        and os.environ["WDM_JSON_EXPECTED_KEYS"].strip() != ""
        else "[\"camera_url\", \"camera_name\", \"camera_id\"]"
    )
    WDM_PRELOAD_DELAY_FOR_DS_API = (
        True
        if "WDM_PRELOAD_DELAY_FOR_DS_API" in os.environ
        and os.environ["WDM_PRELOAD_DELAY_FOR_DS_API"].strip() != ""
        and os.environ["WDM_PRELOAD_DELAY_FOR_DS_API"].strip().lower() == "true"
        else False
    )
    WDM_PRELOAD_DELAY_FOR_REDIS = (
        True
        if "WDM_PRELOAD_DELAY_FOR_REDIS" in os.environ
        and os.environ["WDM_PRELOAD_DELAY_FOR_REDIS"].strip() != ""
        and os.environ["WDM_PRELOAD_DELAY_FOR_REDIS"].strip().lower() == "true"
        else False
    )
    WDM_API_WAIT_MAX_RETRIES_IN_SEC = (
        int(os.environ["WDM_API_WAIT_MAX_RETRIES_IN_SEC"].strip())
        if "WDM_API_WAIT_MAX_RETRIES_IN_SEC" in os.environ
        and os.environ["WDM_API_WAIT_MAX_RETRIES_IN_SEC"].strip() != ""
        else 30
    )
    WDM_ADD_REMOVE_RETRY_ATTEMPTS = (
        int(os.environ["WDM_ADD_REMOVE_RETRY_ATTEMPTS"].strip())
        if "WDM_ADD_REMOVE_RETRY_ATTEMPTS" in os.environ
        and os.environ["WDM_ADD_REMOVE_RETRY_ATTEMPTS"].strip() != ""
        else 2
    )
    WDM_CONFIG_RETRY_ATTEMPTS = (
        int(os.environ["WDM_CONFIG_RETRY_ATTEMPTS"].strip())
        if "WDM_CONFIG_RETRY_ATTEMPTS" in os.environ
        and os.environ["WDM_CONFIG_RETRY_ATTEMPTS"].strip() != ""
        else 3
    )
    WDM_POD_WATCH_DOCKER_DELAY = (
        float(os.environ["WDM_POD_WATCH_DOCKER_DELAY"].strip())
        if "WDM_POD_WATCH_DOCKER_DELAY" in os.environ
        and os.environ["WDM_POD_WATCH_DOCKER_DELAY"].strip() != ""
        else 0.05
    )
    WDM_ADD_REMOVE_RETRY_DELAY = (
        float(os.environ["WDM_ADD_REMOVE_RETRY_DELAY"].strip())
        if "WDM_ADD_REMOVE_RETRY_DELAY" in os.environ
        and os.environ["WDM_ADD_REMOVE_RETRY_DELAY"].strip() != ""
        else 0.5
    )
    WDM_CONFIG_RETRY_DELAY = (
        float(os.environ["WDM_CONFIG_RETRY_DELAY"].strip())
        if "WDM_CONFIG_RETRY_DELAY" in os.environ
        and os.environ["WDM_CONFIG_RETRY_DELAY"].strip() != ""
        else 0.5
    )
    WDM_ADD_REMOVE_REQUEST_TIMEOUT = (
        int(os.environ["WDM_ADD_REMOVE_REQUEST_TIMEOUT"].strip())
        if "WDM_ADD_REMOVE_REQUEST_TIMEOUT" in os.environ
        and os.environ["WDM_ADD_REMOVE_REQUEST_TIMEOUT"].strip() != ""
        else 2
    )
    WDM_CONFIG_DEFER_ON_FAILURE = _bool_env(
        "WDM_CONFIG_DEFER_ON_FAILURE", False
    )
    # Max times to re-process the same bus event on temporary failures before
    # giving up (ERROR log + commit). Applies to Redis and Kafka consumers.
    WDM_EVENT_RETRY_LIMIT = (
        int(os.environ["WDM_EVENT_RETRY_LIMIT"].strip())
        if "WDM_EVENT_RETRY_LIMIT" in os.environ
        and os.environ["WDM_EVENT_RETRY_LIMIT"].strip() != ""
        else 20
    )
    WDM_DS_STATUS_CHECK = (
        True
        if "WDM_DS_STATUS_CHECK" in os.environ
        and os.environ["WDM_DS_STATUS_CHECK"].strip() != ""
        and os.environ["WDM_DS_STATUS_CHECK"].strip().lower() == "true"
        else False
    )
    WDM_DISABLE_WERKZEUG_LOGGING = (
        True
        if "WDM_DISABLE_WERKZEUG_LOGGING" in os.environ
        and os.environ["WDM_DISABLE_WERKZEUG_LOGGING"].strip() != ""
        and os.environ["WDM_DISABLE_WERKZEUG_LOGGING"].strip().lower() == "true"
        else False
    )

    WDM_RESET_ON_WLOBJ_CRASH = (
        False
        if "WDM_RESET_ON_WLOBJ_CRASH" in os.environ
        and os.environ["WDM_RESET_ON_WLOBJ_CRASH"].strip() != ""
        and os.environ["WDM_RESET_ON_WLOBJ_CRASH"].strip().lower() == "false"
        else True
    )

    WDM_RESET_ON_INITIATOR_CRASH = (
        True
        if "WDM_RESET_ON_INITIATOR_CRASH" in os.environ
        and os.environ["WDM_RESET_ON_INITIATOR_CRASH"].strip() != ""
        and os.environ["WDM_RESET_ON_INITIATOR_CRASH"].strip().lower() == "true"
        else False
    )
    WDM_AGENT_EVENT_BUS = (
        os.environ["WDM_AGENT_EVENT_BUS"].strip ()
        if "WDM_AGENT_EVENT_BUS" in os.environ
        and os.environ["WDM_AGENT_EVENT_BUS"].strip() != ""
        else "sdr_agent_event"
    )
    WDM_RESET_PRELOAD_FILE = (
        True
        if "WDM_RESET_PRELOAD_FILE" in os.environ
        and os.environ["WDM_RESET_PRELOAD_FILE"].strip() != ""
        and os.environ["WDM_RESET_PRELOAD_FILE"].strip().lower() == "true"
        else False
    )
    WDM_CONTROLLER_SDR_AGENTS_PATH = (
        os.environ["WDM_CONTROLLER_SDR_AGENTS_PATH"]
        if "WDM_CONTROLLER_SDR_AGENTS_PATH" in os.environ
        and os.environ["WDM_CONTROLLER_SDR_AGENTS_PATH"].strip() != ""
        else '/sdrc/sdrc-agents/agents-data.yaml'
    )
    WDM_SDR_AGENT_PORT = (
        os.environ["WDM_SDR_AGENT_PORT"]
        if "WDM_SDR_AGENT_PORT" in os.environ
        and os.environ["WDM_SDR_AGENT_PORT"].strip() != ""
        else '4000'
    )
    CONTROLLER_SERVICE_URL = (
        os.environ["CONTROLLER_SERVICE_URL"]
        if "CONTROLLER_SERVICE_URL" in os.environ
        and os.environ["CONTROLLER_SERVICE_URL"].strip() != ""
        else "sdr-controller-service.default.svc.cluster.local:4001/report"
    )
    WDM_DISABLE_ALIVE_STATUS = (
        os.environ.get("WDM_DISABLE_ALIVE_STATUS", "true").strip().lower() == "true"
    )
    WDM_K8S_HEADLESS_BASE_DOMAIN = (
        os.environ["WDM_K8S_HEADLESS_BASE_DOMAIN"]
        if "WDM_K8S_HEADLESS_BASE_DOMAIN" in os.environ
        and os.environ["WDM_K8S_HEADLESS_BASE_DOMAIN"].strip() != ""
        else "sdrc-workload-svc"
    )
    WDM_K8S_HEADLESS_DEFAULT_POD_PORT = (
        os.environ["WDM_K8S_HEADLESS_DEFAULT_POD_PORT"]
        if "WDM_K8S_HEADLESS_DEFAULT_POD_PORT" in os.environ
        and os.environ["WDM_K8S_HEADLESS_DEFAULT_POD_PORT"].strip() != ""
        else "8000"
    )
    WDM_REAPPLY_ON_WL_RESTART = (
        True
        if "WDM_REAPPLY_ON_WL_RESTART" in os.environ
        and os.environ["WDM_REAPPLY_ON_WL_RESTART"].strip() != ""
        and os.environ["WDM_REAPPLY_ON_WL_RESTART"].strip().lower() == "true"
        else False
    )
    WDM_POD_ALLOCATION_HASH_NAME = (
        os.environ["WDM_POD_ALLOCATION_HASH_NAME"]
        if "WDM_POD_ALLOCATION_HASH_NAME" in os.environ
        and os.environ["WDM_POD_ALLOCATION_HASH_NAME"].strip() != ""
        else "regex-dns-pod-mapping"
    )
    WDM_POD_ALLOCATION_REGEX_DELIMITER = (
        os.environ["WDM_POD_ALLOCATION_REGEX_DELIMITER"]
        if "WDM_POD_ALLOCATION_REGEX_DELIMITER" in os.environ
        and os.environ["WDM_POD_ALLOCATION_REGEX_DELIMITER"].strip() != ""
        else "|"
    )
    WDM_POD_ALLOCATION_ENCODED_NAME_KEY = (
        os.environ["WDM_POD_ALLOCATION_ENCODED_NAME_KEY"]
        if "WDM_POD_ALLOCATION_ENCODED_NAME_KEY" in os.environ
        and os.environ["WDM_POD_ALLOCATION_ENCODED_NAME_KEY"].strip() != ""
        else "name"
    )
    WDM_STREAM_ADD_REGEX_INFO_KEY = (
        os.environ["WDM_STREAM_ADD_REGEX_INFO_KEY"]
        if "WDM_STREAM_ADD_REGEX_INFO_KEY" in os.environ
        and os.environ["WDM_STREAM_ADD_REGEX_INFO_KEY"].strip() != ""
        else "name"
    )
    WDM_ENABLE_REGEX_MAPPING = (
        True
        if "WDM_ENABLE_REGEX_MAPPING" in os.environ
        and os.environ["WDM_ENABLE_REGEX_MAPPING"].strip() != ""
        and os.environ["WDM_ENABLE_REGEX_MAPPING"].strip().lower() == "true"
        else False
    )
    # Opt-in: unset/empty defaults to false. Set true only for workloads that
    # must apply /config (e.g. warehouse 3D rtvi-cv).
    WDM_HANDLE_CONFIG_EVENTS = _bool_env("WDM_HANDLE_CONFIG_EVENTS", False)
    ENVOY_REQUEST_TIMEOUT = (
        int(os.environ["ENVOY_REQUEST_TIMEOUT"])
        if "ENVOY_REQUEST_TIMEOUT" in os.environ and os.environ["ENVOY_REQUEST_TIMEOUT"].strip() != ""
        else 5
    )
    OTEL_SERVICE_NAME = (
        os.environ["OTEL_SERVICE_NAME"]
        if "OTEL_SERVICE_NAME" in os.environ
        and os.environ["OTEL_SERVICE_NAME"].strip() != ""
        else "sdr-agent"
    )
    WDM_CACHE_METHOD = (
        os.environ["WDM_CACHE_METHOD"]
        if "WDM_CACHE_METHOD" in os.environ
        and os.environ["WDM_CACHE_METHOD"].strip() != ""
        else "redis"    #or 'file'
    )

    WDM_REDIS_CACHE_OBJECT = (
        os.environ["WDM_REDIS_CACHE_OBJECT"]
        if "WDM_REDIS_CACHE_OBJECT" in os.environ
        and os.environ["WDM_REDIS_CACHE_OBJECT"].strip() != ""
        else "testapp-data"
    )
    WDM_REDIS_LOCK_TIMEOUT = (
        int(os.environ["WDM_REDIS_LOCK_TIMEOUT"])
        if "WDM_REDIS_LOCK_TIMEOUT" in os.environ and os.environ["WDM_REDIS_LOCK_TIMEOUT"].strip() != ""
        else 2
    )
    # Seconds to sleep before retrying a failed Redis workload-spec lock (lower = snappier UI under contention).
    WDM_REDIS_LOCK_RETRY_SLEEP_SECONDS = (
        float(os.environ["WDM_REDIS_LOCK_RETRY_SLEEP_SECONDS"])
        if "WDM_REDIS_LOCK_RETRY_SLEEP_SECONDS" in os.environ
        and os.environ["WDM_REDIS_LOCK_RETRY_SLEEP_SECONDS"].strip() != ""
        else 1.0
    )
    # Deprovision/delete waits for in-flight async provision-add threads (see WDM_PROVISION_ASYNC) before updating Redis.
    WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT = (
        float(os.environ["WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT"])
        if "WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT" in os.environ
        and os.environ["WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT"].strip() != ""
        else 5.0
    )
    DELETE_API_METHOD = (
        os.environ["DELETE_API_METHOD"]
        if "DELETE_API_METHOD" in os.environ
        and os.environ["DELETE_API_METHOD"].strip() != ""
        else "POST"
    )
    WDM_CALL_WL_WEBHOOK = (
        True
        if "WDM_CALL_WL_WEBHOOK" in os.environ
        and os.environ["WDM_CALL_WL_WEBHOOK"].strip() != ""
        and os.environ["WDM_CALL_WL_WEBHOOK"].strip().lower() == "true"
        else False
    )
    WDM_PROVISION_ASYNC = (
        False
        if "WDM_PROVISION_ASYNC" in os.environ
        and os.environ["WDM_PROVISION_ASYNC"].strip() != ""
        and os.environ["WDM_PROVISION_ASYNC"].strip().lower() == "false"
        else True
    )
    WDM_WL_WEBHOOK_ENDPOINT = (
        os.environ["WDM_WL_WEBHOOK_ENDPOINT"]
        if "WDM_WL_WEBHOOK_ENDPOINT" in os.environ
        and os.environ["WDM_WL_WEBHOOK_ENDPOINT"].strip() != ""
        else "http://localhost:9001/add"
    )
    WDM_STANDBY_POD_COUNT = (
        int(os.environ["WDM_STANDBY_POD_COUNT"])
        if "WDM_STANDBY_POD_COUNT" in os.environ and os.environ["WDM_STANDBY_POD_COUNT"].strip() != ""
        else 2
    )
    WDM_CONTROLLER_REPROVISION = (
        False
        if "WDM_CONTROLLER_REPROVISION" in os.environ
        and os.environ["WDM_CONTROLLER_REPROVISION"].strip() != ""
        and os.environ["WDM_CONTROLLER_REPROVISION"].strip().lower() == "false"
        else True
    )
    # Background watchers (run_workloads.py imports lib.controller without controller-config swap)
    REPROVISION_ENABLED = (
        os.environ["REPROVISION_ENABLED"].strip().lower() == "true"
        if "REPROVISION_ENABLED" in os.environ
        and os.environ["REPROVISION_ENABLED"].strip() != ""
        else True
    )
    AGENT_CHECK_ENABLED = (
        os.environ["AGENT_CHECK_ENABLED"].strip().lower() == "true"
        if "AGENT_CHECK_ENABLED" in os.environ
        and os.environ["AGENT_CHECK_ENABLED"].strip() != ""
        else False
    )
    AUTOSCALE_ENABLED = (
        os.environ["AUTOSCALE_ENABLED"].strip().lower() == "true"
        if "AUTOSCALE_ENABLED" in os.environ
        and os.environ["AUTOSCALE_ENABLED"].strip() != ""
        else False
    )
    WDM_ADD_CALL_DELAY = (
        float(os.environ["WDM_ADD_CALL_DELAY"])
        if "WDM_ADD_CALL_DELAY" in os.environ
        and os.environ["WDM_ADD_CALL_DELAY"].strip() != ""
        else 0.1
    )
    # Flask template dir (lib.controller uses LazySettings("config"); Docker uses controller-config as config.py).
    TEMPLATE_FOLDER = (
        os.environ["TEMPLATE_FOLDER"]
        if "TEMPLATE_FOLDER" in os.environ
        and os.environ["TEMPLATE_FOLDER"].strip() != ""
        else str(pathlib.Path(__file__).resolve().parent / "templates")
    )


# Names not exported to workload env / startup logs (secrets, Flask flags).
_WDM_CONFIG_ENV_EXCLUDE = frozenset({"KUBERNETES_JWT_TOKEN", "DEBUG", "TESTING", "TEMPLATE_FOLDER"})


def wdm_config_env_keys():
    """Uppercase Config attributes suitable for os.environ (used by run_workloads / logging)."""
    keys = []
    for name in dir(Config):
        if name.startswith("_") or name in _WDM_CONFIG_ENV_EXCLUDE:
            continue
        if not name.isupper():
            continue
        val = getattr(Config, name, None)
        if callable(val):
            continue
        keys.append(name)
    return frozenset(keys)


def _serialize_config_value_for_env(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


def wdm_config_env_defaults():
    """String defaults from Config for seeding child app.py processes (same resolution as current os.environ)."""
    out = {}
    for name in wdm_config_env_keys():
        raw = getattr(Config, name)
        s = _serialize_config_value_for_env(raw)
        if s is not None:
            out[name] = s
    return out

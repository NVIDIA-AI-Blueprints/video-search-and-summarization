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
# from pprint import pprint
import envoy_data_plane.envoy.api.v2 as envoy

import stringcase
import redis
import logging
logger = logging.getLogger(__name__)

# RDS REST JSON shape (Envoy RouteConfiguration resources).
_ROUTE_CONFIGURATION_TYPE = "type.googleapis.com/envoy.config.route.v3.RouteConfiguration"
_GRPC_HTTP1_REVERSE_BRIDGE_PER_ROUTE_TYPE = (
    "type.googleapis.com/envoy.extensions.filters.http.grpc_http1_reverse_bridge.v3.FilterConfigPerRoute"
)
_HTTP_PROTOCOL_OPTIONS_TYPE = "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions"

# CDS cluster for proxying /dashboard to run_workloads (WDM router).
_WDM_DASHBOARD_ROUTER_CLUSTER = "wdm_dashboard_router"


class envoyxDS:
    def __init__(self, app_config):
        self.app_config = app_config
        self.redis_connection = redis.StrictRedis(
            self.app_config["WDM_WL_REDIS_SERVER"],
            self.app_config["WDM_WL_REDIS_PORT"],
            encoding="utf-8",
            decode_responses=True,
            retry_on_timeout=True,
        )
        logger.info("redis connected")
        self.cluster_name = app_config["WDM_WL_OBJECT_NAME"]
        # self.cluster_name = "testapp"
        return

    def getClusterPodsFromRedis(self):
        k = self.redis_connection.hgetall("{}-pod".format(self.cluster_name))
        return k

    def getClusterPodsIdMapFromRedis(self):
        k = self.redis_connection.hgetall("{}".format(self.cluster_name))
        return k

    def getClusterPodsIdMapFromRedisForCluster(self, cluster_name: str) -> dict:
        """Return stream id -> pod name map for a given cluster (wl_obj_name)."""
        return self.redis_connection.hgetall("{}".format(cluster_name)) or {}

    def _get_xds_version(self) -> str:
        v = self.redis_connection.get("{}-xds-version".format(self.cluster_name))
        return v if isinstance(v, str) else "1"

    def _rds_timeout_string(self) -> str:
        """Duration string for JSON RDS (e.g. 5.000s)."""
        try:
            sec = float(self.app_config["ENVOY_REQUEST_TIMEOUT"])
        except (TypeError, ValueError, KeyError):
            sec = 5.0
        return "%.3fs" % sec

    def _dashboard_proxy_enabled(self) -> bool:
        return bool(self.app_config.get("WDM_ENVOY_DASHBOARD_PROXY"))

    def _rds_dashboard_route_dict(self) -> dict:
        """Route /dashboard* to WDM router (Flask dashboard on ROUTER_PORT)."""
        return {
            "match": {"prefix": "/dashboard"},
            "route": {
                "cluster": _WDM_DASHBOARD_ROUTER_CLUSTER,
                "timeout": self._rds_timeout_string(),
            },
            "typed_per_filter_config": {
                "envoy.filters.http.grpc_http1_reverse_bridge": {
                    "@type": _GRPC_HTTP1_REVERSE_BRIDGE_PER_ROUTE_TYPE,
                    "disabled": True,
                }
            },
        }

    def _dashboard_cluster_dict(self) -> dict:
        host = str(self.app_config.get("WDM_CONTROLLER_HOST") or "127.0.0.1")
        try:
            port = int(self.app_config.get("WDM_CONTROLLER_PORT") or 5002)
        except (TypeError, ValueError):
            port = 5002
        return {
            "@type": "type.googleapis.com/envoy.config.cluster.v3.Cluster",
            "name": _WDM_DASHBOARD_ROUTER_CLUSTER,
            "type": "STRICT_DNS",
            "connect_timeout": "5s",
            "dns_lookup_family": "V4_ONLY",
            "load_assignment": {
                "cluster_name": _WDM_DASHBOARD_ROUTER_CLUSTER,
                "endpoints": [
                    {
                        "lb_endpoints": [
                            {
                                "endpoint": {
                                    "address": {
                                        "socket_address": {
                                            "address": host,
                                            "port_value": port,
                                        }
                                    }
                                }
                            }
                        ]
                    }
                ],
            },
            "typed_extension_protocol_options": {
                "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
                    "@type": _HTTP_PROTOCOL_OPTIONS_TYPE,
                    "explicit_http_config": {"http_protocol_options": {}},
                }
            },
        }

    def _append_dashboard_cluster_to_cds_response(self, resp: dict) -> None:
        if not self._dashboard_proxy_enabled():
            return
        resources = resp.get("resources")
        if resources is None or not isinstance(resources, list):
            resources = []
            resp["resources"] = resources
        for r in resources:
            if isinstance(r, dict) and r.get("name") == _WDM_DASHBOARD_ROUTER_CLUSTER:
                return
        resources.append(self._dashboard_cluster_dict())

    def _rds_catch_all_route_dict(self) -> dict:
        """Default route: prefix /, cluster_header upstream-cluster, grpc reverse bridge disabled (bool)."""
        return {
            "match": {"prefix": "/"},
            "route": {
                "cluster_header": "upstream-cluster",
                "timeout": self._rds_timeout_string(),
            },
            "typed_per_filter_config": {
                "envoy.filters.http.grpc_http1_reverse_bridge": {
                    "@type": _GRPC_HTTP1_REVERSE_BRIDGE_PER_ROUTE_TYPE,
                    "disabled": True,
                }
            },
        }

    def _rds_tail_routes_cluster_header_or_fallback(self) -> list:
        """
        When Lua/Redis set ``upstream-cluster``, use it (cluster_header route). If the header is
        absent (Redis down, stream id missing, etc.), Envoy would otherwise 503; optional
        ``WDM_ENVOY_FALLBACK_CLUSTER`` adds a final static cluster route for /hello and other paths.
        """
        base = self._rds_catch_all_route_dict()
        fb = (self.app_config.get("WDM_ENVOY_FALLBACK_CLUSTER") or "").strip()
        if not fb:
            return [base]
        tpc = base["typed_per_filter_config"]
        return [
            {
                "match": {
                    "prefix": "/",
                    "headers": [{"name": "upstream-cluster", "present_match": True}],
                },
                "route": base["route"],
                "typed_per_filter_config": tpc,
            },
            {
                "match": {"prefix": "/"},
                "route": {
                    "cluster": fb,
                    "timeout": self._rds_timeout_string(),
                },
                "typed_per_filter_config": tpc,
            },
        ]

    def _cluster_upstream_http_options(self, port_type: str) -> dict:
        """gRPC clusters use HTTP/2; WebSocket upgrades require HTTP/1.1 to the pod."""
        if port_type == "grpc":
            return {
                "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
                    "@type": _HTTP_PROTOCOL_OPTIONS_TYPE,
                    "explicit_http_config": {"http2_protocol_options": {}},
                }
            }
        if port_type == "websocket":
            return {
                "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
                    "@type": _HTTP_PROTOCOL_OPTIONS_TYPE,
                    "explicit_http_config": {"http_protocol_options": {}},
                }
            }
        return {}

    def _rds_stream_id_routes_dict(self, wl_obj_name: str) -> list:
        """Per stream-id -> cluster routes (prefix + cluster + prefix_rewrite), before catch-all."""
        prefix_base = self.app_config["ENVOY_ROUTE_URL_PREFIX"]
        rewrite = self.app_config["ENVOY_ROUTE_URL_PREFIX_REWRITE"]
        id_map = self.getClusterPodsIdMapFromRedisForCluster(wl_obj_name)
        routes = []
        for stream_id, cluster in id_map.items():
            routes.append(
                {
                    "match": {"prefix": "%s%s" % (prefix_base, stream_id)},
                    "route": {
                        "cluster": cluster,
                        "prefix_rewrite": rewrite,
                    },
                }
            )
        return routes

    def _rds_route_configuration_dict(self, wl_obj_name: str, route_config_name: str = None) -> dict:
        """
        One RouteConfiguration as plain dict: name e.g. {wl_obj_name}_route,
        virtual_hosts[*].name *_service, routes order: stream-specific then catch-all.
        """
        rc_name = route_config_name if route_config_name else "%s_route" % wl_obj_name
        routes = []
        if self._dashboard_proxy_enabled():
            routes.append(self._rds_dashboard_route_dict())
        routes.extend(self._rds_stream_id_routes_dict(wl_obj_name))
        routes.extend(self._rds_tail_routes_cluster_header_or_fallback())
        return {
            "@type": _ROUTE_CONFIGURATION_TYPE,
            "name": rc_name,
            "virtual_hosts": [
                {
                    "domains": ["*"],
                    "name": "%s_service" % wl_obj_name,
                    "routes": routes,
                }
            ],
        }

    def routeXDs(self, resource_names=None):
        """
        Return Envoy RDS as a plain dict: ``version_info`` + ``resources`` list.

        resource_names: if a non-empty list of strings, one RouteConfiguration per entry. Each
          entry is a workload ``wl_obj_name`` (Redis / stream-id routes use this string). Resource
          ``name`` is ``{wl_obj_name}_route``. A value already suffixed with ``_route`` is accepted
          and normalized to the wl_obj_name for Redis. If ``None`` (default), single-workload mode:
          one resource for ``WDM_WL_OBJECT_NAME`` with name ``{cluster}_route``. If an empty list,
          returns no resources.
        """
        cluster = self.app_config.get("WDM_WL_OBJECT_NAME") or self.cluster_name

        if resource_names is not None:
            logger.info("routeXDs: resource_names=%s", resource_names)
            resources = []
            for rname in resource_names:
                if not isinstance(rname, str):
                    logger.warning(
                        "routeXDs: resource_name=%s is not a string; skipping",
                        rname,
                    )
                    continue
                # rname is wl_obj_name for Redis / stream routes; RouteConfiguration.name must be
                # "{wl_obj_name}_route" to match listener rds.route_config_name (Envoy matches on name).
                wl_key = rname[:-6] if rname.endswith("_route") else rname
                resources.append(self._rds_route_configuration_dict(wl_key))
            return {"version_info": self._get_xds_version(), "resources": resources}

        resources = [self._rds_route_configuration_dict(cluster)]
        return {"version_info": self._get_xds_version(), "resources": resources}

    def getClusterPodsFromRedisForCluster(self, cluster_name: str) -> dict:
        """Return pod name -> address map for a given cluster (wl_obj_name)."""
        return self.redis_connection.hgetall("{}-pod".format(cluster_name)) or {}

    def getClusterPodsIpFromRedisForCluster(self, cluster_name: str) -> dict:
        """Return pod name -> pod IP map for a given cluster (wl_obj_name)."""
        return self.redis_connection.hgetall("{}-pod-ip".format(cluster_name)) or {}

    def _xds_use_pod_dns(self) -> bool:
        """When True, CDS endpoints use pod DNS (cluster_pod_list / Redis *-pod), not pod IP."""
        return bool(self.app_config.get("WDM_XDS_USE_POD_DNS", True))

    @staticmethod
    def _wdm_flag_value(value, default: bool) -> bool:
        """Parse YAML/env-style bool (true/false/1/0/yes/no). Empty uses default."""
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        s = str(value).strip()
        if not s:
            return default
        return s.lower() not in ("0", "false", "no", "off")

    def _entry_xds_use_pod_dns(self, workload_entry) -> bool:
        """Per-workload override from clusterXDs ``workload_config`` entry, else app_config."""
        if isinstance(workload_entry, dict) and "WDM_XDS_USE_POD_DNS" in workload_entry:
            return self._wdm_flag_value(workload_entry["WDM_XDS_USE_POD_DNS"], default=True)
        return self._xds_use_pod_dns()

    def _entry_xds_use_ip_address(self, workload_entry) -> bool:
        if isinstance(workload_entry, dict) and "WDM_XDS_USE_IP_ADDRESS" in workload_entry:
            return self._wdm_flag_value(workload_entry["WDM_XDS_USE_IP_ADDRESS"], default=False)
        return bool(self.app_config.get("WDM_XDS_USE_IP_ADDRESS", False))

    def clusterXDs(self, workload_config=None):
        """
        Return Envoy CDS response. If workload_config is given (dict of workload name -> config
        with wl_obj_name, enable), return clusters for all pods across all enabled workloads.
        Otherwise behave as single-workload: use self.cluster_name (current app's wl_obj_name).
        """
        if workload_config is None:
            ClusterPodList = self.getClusterPodsFromRedis()
            ClusterPodIpList = None
            if self.app_config.get("WDM_XDS_USE_IP_ADDRESS", False) and not self._xds_use_pod_dns():
                ClusterPodIpList = self.getClusterPodsIpFromRedisForCluster(self.cluster_name)
            resp = self._clusterXDsFromPodList(self.cluster_name, ClusterPodList, ClusterPodIpList)
            self._append_dashboard_cluster_to_cds_response(resp)
            return resp

        clusters = []
        default_port_mappings = self.app_config["WDM_TARGET_PORT_MAPPING"]
        for wl_name, entry in workload_config.items():
            if not entry.get("enable", True):
                continue
            wl_obj_name = entry.get("wl_obj_name")
            if not wl_obj_name:
                continue
            raw = entry.get("WDM_TARGET_PORT_MAPPING")
            if isinstance(raw, dict):
                portMappings = dict(raw)
            elif isinstance(raw, str) and raw.strip():
                try:
                    portMappings = json.loads(raw)
                    if not isinstance(portMappings, dict):
                        portMappings = dict(default_port_mappings)
                    else:
                        portMappings = dict(portMappings)
                except (json.JSONDecodeError, TypeError):
                    portMappings = dict(default_port_mappings)
            else:
                portMappings = dict(default_port_mappings)
            if "default" not in portMappings:
                portMappings["default"] = entry.get("port") if entry.get("port") is not None else default_port_mappings.get("default", 5000)
            ClusterPodList = entry.get("cluster_pod_list")
            if ClusterPodList is None:
                ClusterPodList = self.getClusterPodsFromRedisForCluster(wl_obj_name)
            ClusterPodIpList = entry.get("cluster_pod_ip_list")
            if (
                ClusterPodIpList is None
                and self._entry_xds_use_ip_address(entry)
                and not self._entry_xds_use_pod_dns(entry)
            ):
                ClusterPodIpList = self.getClusterPodsIpFromRedisForCluster(wl_obj_name)
            clusters.extend(
                self._clusterXDsClustersForPodList(
                    wl_obj_name, ClusterPodList, portMappings, ClusterPodIpList, workload_entry=entry
                )
            )

        response = envoy.DiscoveryResponse(
            version_info=self._get_xds_version(),
            resources=clusters
        )
        resp = response.to_dict(casing=stringcase.snakecase)
        for r in resp:
            if isinstance(resp[r], list):
                for i in range(len(resp[r])):
                    resp[r][i]["@type"] = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
        self._append_dashboard_cluster_to_cds_response(resp)
        return resp

    def _clusterXDsClustersForPodList(
        self,
        wl_obj_name: str,
        ClusterPodList: dict,
        portMappings: dict,
        ClusterPodIpList: dict = None,
        workload_entry: dict = None,
    ):
        """Build list of envoy.Cluster for a single workload's pod list (for multi-workload CDS).
        If ClusterPodIpList is provided, WDM_XDS_USE_IP_ADDRESS is true, and WDM_XDS_USE_POD_DNS is false,
        use pod IP as address and STATIC type; otherwise use pod DNS from ClusterPodList and STRICT_DNS.
        ``workload_entry`` (run_workloads / config.yml per-workload dict) can override global app_config flags."""
        use_ip_opt = bool(
            self._entry_xds_use_ip_address(workload_entry) and ClusterPodIpList
        ) and not self._entry_xds_use_pod_dns(workload_entry)
        clusters = []
        for portType in portMappings:
            for k in ClusterPodList:
                pod_ip = (ClusterPodIpList or {}).get(k)
                use_static = use_ip_opt and bool(pod_ip)
                address = pod_ip if use_static else ClusterPodList[k]
                ca = envoy.ClusterLoadAssignment()
                ca.cluster_name = k if portType == "default" else f"{k}-{portType}"
                LclLbEp = envoy.endpoint.LocalityLbEndpoints()
                lbe = envoy.endpoint.LbEndpoint()
                enp = envoy.endpoint.Endpoint()
                socAddre = envoy.core.SocketAddress(
                    address=address,
                    port_value=portMappings[portType]
                )
                addr = envoy.core.Address(socket_address=socAddre)
                enp.address = addr
                lbe.endpoint = enp
                LclLbEp.lb_endpoints = [lbe]
                ca.endpoints = [LclLbEp]

                cluster_type = envoy.ClusterDiscoveryType.STATIC if use_static else envoy.ClusterDiscoveryType.STRICT_DNS
                c = envoy.Cluster(
                    name=ca.cluster_name,
                    lb_policy=envoy.ClusterLbPolicy.ROUND_ROBIN,
                    load_assignment=ca,
                    type=cluster_type,
                    dns_lookup_family=envoy.ClusterDnsLookupFamily.V4_ONLY,
                    typed_extension_protocol_options=self._cluster_upstream_http_options(portType),
                )
                clusters.append(c)
        return clusters

    def _clusterXDsFromPodList(self, cluster_name: str, ClusterPodList: dict, ClusterPodIpList: dict = None):
        """Build full CDS response for a single workload (same endpoint rules as multi-workload CDS)."""
        portMappings = self.app_config["WDM_TARGET_PORT_MAPPING"]
        clusters = self._clusterXDsClustersForPodList(
            cluster_name, ClusterPodList, portMappings, ClusterPodIpList, workload_entry=None
        )
        response = envoy.DiscoveryResponse(
                version_info=self._get_xds_version(),
                resources=clusters
        )

        resp = response.to_dict(casing=stringcase.snakecase)
        for r in resp:
            if isinstance(resp[r], list):
                i = 0
                for r1 in resp[r]:
                    resp[r][i][
                        "@type"
                    ] = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
                    i = i + 1

        return resp

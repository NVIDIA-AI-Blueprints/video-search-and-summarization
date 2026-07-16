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
"""
gRPC Aggregated Discovery Service (ADS) server for WDM/Envoy integration.

Replaces the REST CDS/RDS polling (refresh_delay 5s/10s) with a push-based
gRPC stream. When updateRouteMapping() writes to Redis, notify_xds_update()
wakes all connected Envoy instances within ~50ms instead of waiting for the
next polling interval.

Envoy bootstrap change required (values.yaml for sdr-envoy-rtspserver and
sdr-envoy-recorder) — replace dynamic_resources and rds config_source with:

  dynamic_resources:
    ads_config:
      api_type: GRPC
      transport_api_version: V3
      grpc_services:
      - envoy_grpc:
          cluster_name: xds_cluster_grpc   # new static cluster below
    cds_config: {resource_api_version: V3, ads: {}}

  static_resources:
    clusters:
    # existing clusters ...
    - name: xds_cluster_grpc
      type: STRICT_DNS
      connect_timeout: 1s
      http2_protocol_options: {}
      load_assignment:
        cluster_name: xds_cluster_grpc
        endpoints:
        - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: <xdsClusterAddress>
                  port_value: 4001          # GRPC_XDS_PORT env var

  And inside the HTTP connection manager rds block:
    rds:
      route_config_name: rtspserver-deployment_route
      config_source:
        resource_api_version: V3
        ads: {}

Usage (app.py):
    from lib.xDS.grpc_xds_server import (
        is_grpc_xds_enabled,
        start_grpc_xds_server,
        notify_xds_update,
    )
    # if WDM_XDS_GRPC_ADS_ENABLED=true, start in a daemon thread before app.run()
    # call after every curr_cluster.updateRouteMapping(...)
    notify_xds_update()
"""

import asyncio
import base64
import contextlib
import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)

_CDS_TYPE = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
_RDS_TYPE = "type.googleapis.com/envoy.config.route.v3.RouteConfiguration"
_GRPC_HTTP1_REVERSE_BRIDGE_PER_ROUTE_TYPE = (
    "type.googleapis.com/envoy.extensions.filters.http.grpc_http1_reverse_bridge.v3.FilterConfigPerRoute"
)
_HTTP_PROTOCOL_OPTIONS_TYPE = "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions"

# ADS proto stubs are optional. If the v3 service stubs are absent the server
# degrades gracefully: REST polling continues to work (just slower). Use the
# already-required envoy-data-plane package: it exposes Envoy v3 service stubs
# through betterproto/grpclib, not canonical grpcio *_pb2_grpc modules.
try:
    from betterproto.lib.google.protobuf import Any
    from envoy_data_plane.envoy.config.cluster import v3 as cluster_pb
    from envoy_data_plane.envoy.config.route import v3 as route_pb
    from envoy_data_plane.envoy.extensions.filters.http.grpc_http1_reverse_bridge import (
        v3 as reverse_bridge_pb,
    )
    from envoy_data_plane.envoy.extensions.upstreams.http import v3 as http_options_pb
    from envoy_data_plane.envoy.service.discovery import v3 as discovery_pb
    import grpclib.server

    _GRPC_AVAILABLE = True
    _GRPC_IMPORT_ERROR = None
except ImportError as exc:
    Any = None
    cluster_pb = None
    route_pb = None
    reverse_bridge_pb = None
    http_options_pb = None
    discovery_pb = None
    grpclib = None
    _GRPC_AVAILABLE = False
    _GRPC_IMPORT_ERROR = exc
    logger.warning(
        "grpclib / envoy-data-plane ADS stubs not found; "
        "gRPC ADS listener cannot be started in this process"
    )

# Module-level reference so notify_xds_update() can reach the servicer
# without passing it through app.py globals.
_servicer = None

_AdsServicer = None

if _GRPC_AVAILABLE:
    _RESOURCE_TYPES = {
        _CDS_TYPE: cluster_pb.Cluster,
        _RDS_TYPE: route_pb.RouteConfiguration,
    }
    _TYPED_ANY_TYPES = {
        _GRPC_HTTP1_REVERSE_BRIDGE_PER_ROUTE_TYPE: reverse_bridge_pb.FilterConfigPerRoute,
        _HTTP_PROTOCOL_OPTIONS_TYPE: http_options_pb.HttpProtocolOptions,
    }


def is_grpc_xds_enabled(app_config: dict) -> bool:
    value = app_config.get("WDM_XDS_GRPC_ADS_ENABLED", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def can_start_grpc_xds_server(app_config: dict) -> bool:
    return (
        is_grpc_xds_enabled(app_config)
        and _GRPC_AVAILABLE
        and _AdsServicer is not None
    )


if _GRPC_AVAILABLE:
    def _put_update_token(update_q: "asyncio.Queue") -> None:
        try:
            update_q.put_nowait(True)
        except asyncio.QueueFull:
            pass


    def _pack_typed_any(type_url: str, payload: dict) -> dict:
        message_cls = _TYPED_ANY_TYPES.get(type_url)
        if message_cls is None:
            logger.warning("ADS: unsupported nested typed config %s", type_url)
            return {"type_url": type_url}
        message = message_cls().from_dict(_prepare_typed_any_fields(payload))
        return {
            "type_url": type_url,
            "value": base64.b64encode(bytes(message)).decode("ascii"),
        }


    def _prepare_typed_any_fields(value):
        if isinstance(value, list):
            return [_prepare_typed_any_fields(item) for item in value]
        if not isinstance(value, dict):
            return value

        type_url = value.get("@type")
        if isinstance(type_url, str):
            payload = {
                k: _prepare_typed_any_fields(v)
                for k, v in value.items()
                if k != "@type"
            }
            return _pack_typed_any(type_url, payload)

        return {
            k: _prepare_typed_any_fields(v)
            for k, v in value.items()
        }


    def _resource_to_any(resource: dict, default_type_url: str) -> "Any":
        if not isinstance(resource, dict):
            raise TypeError("ADS resource must be a dict, got %r" % type(resource).__name__)

        type_url = resource.get("@type") or default_type_url
        message_cls = _RESOURCE_TYPES.get(type_url)
        if message_cls is None:
            raise ValueError("ADS: unsupported resource type_url %s" % type_url)

        payload = {
            k: v
            for k, v in resource.items()
            if k != "@type"
        }
        message = message_cls().from_dict(_prepare_typed_any_fields(payload))
        return Any(type_url=type_url, value=bytes(message))


    class _AdsServicer(discovery_pb.AggregatedDiscoveryServiceBase):
        """Stateful ADS servicer.

        Each connected Envoy opens one stream_aggregated_resources call. The
        servicer keeps a per-stream notification queue; notify_xds_update() drops
        a token into every queue so the stream re-sends the current snapshot
        without waiting for the next client request.
        """

        def __init__(self, xds, poll_interval_seconds: float = 0.0):
            self._xds = xds
            self._lock = Lock()
            self._queues = []
            self._poll_interval_seconds = max(0.0, float(poll_interval_seconds or 0.0))

        def notify(self) -> None:
            with self._lock:
                queues = list(self._queues)
            for loop, update_q in queues:
                loop.call_soon_threadsafe(_put_update_token, update_q)

        async def stream_aggregated_resources(self, request_iterator):
            update_q = asyncio.Queue(maxsize=1)
            request_q = asyncio.Queue()
            loop = asyncio.get_running_loop()
            with self._lock:
                self._queues.append((loop, update_q))

            async def read_requests():
                try:
                    async for req in request_iterator:
                        await request_q.put(req)
                finally:
                    await request_q.put(None)

            requested_resources_by_type = {}
            last_version = self._current_version()
            reader_task = asyncio.create_task(read_requests())
            request_task = asyncio.create_task(request_q.get())
            update_task = asyncio.create_task(update_q.get())
            poll_task = None
            logger.info("Envoy ADS stream opened")
            try:
                while True:
                    wait_tasks = {request_task, update_task}
                    if (
                        self._poll_interval_seconds > 0
                        and requested_resources_by_type
                        and poll_task is None
                    ):
                        poll_task = asyncio.create_task(
                            asyncio.sleep(self._poll_interval_seconds)
                        )
                    if poll_task is not None:
                        wait_tasks.add(poll_task)
                    done, pending = await asyncio.wait(
                        wait_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if request_task in done:
                        req = request_task.result()
                        if req is None:
                            break
                        request_task = asyncio.create_task(request_q.get())
                        type_url = req.type_url
                        requested_resources_by_type[type_url] = list(req.resource_names)
                        response = self._build_response_for_request(req)
                        if response is not None:
                            last_version = self._response_version(response)
                            yield response

                    if update_task in done:
                        update_task.result()
                        update_task = asyncio.create_task(update_q.get())
                        logger.debug("ADS: state changed; pushing updated snapshots")
                        async for response in self._build_responses_for_requested(
                            requested_resources_by_type
                        ):
                            last_version = self._response_version(response)
                            yield response

                    if poll_task is not None and poll_task in done:
                        poll_task.result()
                        poll_task = None
                        current_version = self._current_version()
                        if current_version != last_version:
                            logger.debug(
                                "ADS: xDS version changed from %s to %s; pushing updated snapshots",
                                last_version,
                                current_version,
                            )
                            async for response in self._build_responses_for_requested(
                                requested_resources_by_type
                            ):
                                last_version = self._response_version(response)
                                yield response
            except Exception as exc:
                logger.warning("ADS stream closed: %s", exc)
            finally:
                for task in (reader_task, request_task, update_task, poll_task):
                    if task is None or task.done():
                        continue
                    task.cancel()
                for task in (reader_task, request_task, update_task, poll_task):
                    if task is None:
                        continue
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                with self._lock:
                    item = (loop, update_q)
                    if item in self._queues:
                        self._queues.remove(item)
                logger.info("ADS stream closed")

        def _build_response_for_request(self, req):
            if req.type_url == _CDS_TYPE:
                return self._build_response(self._xds.clusterXDs(), _CDS_TYPE)
            if req.type_url == _RDS_TYPE:
                return self._build_response(
                    self._xds.routeXDs(req.resource_names or None),
                    _RDS_TYPE,
                )
            logger.debug("ADS: unhandled type_url %s; skipping", req.type_url)
            return None

        def _build_response_for_type(self, type_url: str, resource_names=None):
            if type_url == _CDS_TYPE:
                return self._build_response(self._xds.clusterXDs(), _CDS_TYPE)
            if type_url == _RDS_TYPE:
                return self._build_response(
                    self._xds.routeXDs(resource_names),
                    _RDS_TYPE,
                )
            logger.debug("ADS: unhandled type_url %s; skipping update", type_url)
            return None

        async def _build_responses_for_requested(self, requested_resources_by_type):
            for type_url, resource_names in sorted(requested_resources_by_type.items()):
                response = self._build_response_for_type(
                    type_url,
                    resource_names or None,
                )
                if response is not None:
                    yield response

        def _current_version(self):
            getter = getattr(self._xds, "_get_xds_version", None)
            if callable(getter):
                try:
                    return str(getter())
                except Exception:
                    logger.debug("ADS: failed to read xDS version", exc_info=True)
            return None

        @staticmethod
        def _response_version(response):
            return str(response.version_info or "1")

        @staticmethod
        def _build_response(resp_dict: dict, type_url: str) -> "discovery_pb.DiscoveryResponse":
            return discovery_pb.DiscoveryResponse(
                version_info=str(resp_dict.get("version_info") or "1"),
                resources=[
                    _resource_to_any(resource, type_url)
                    for resource in resp_dict.get("resources", [])
                ],
                type_url=type_url,
                nonce="%s-%d" % (resp_dict.get("version_info") or "1", time.time_ns()),
            )


def start_grpc_xds_server(xds, app_config: dict) -> None:
    """Start the gRPC ADS server in the calling thread (run in a daemon Thread).

    Port defaults to 4001; override with GRPC_XDS_PORT in the WDM config/env.
    """
    if not is_grpc_xds_enabled(app_config):
        logger.info(
            "gRPC ADS listener disabled in this process; set "
            "WDM_XDS_GRPC_ADS_ENABLED=true to enable a local ADS listener."
        )
        return

    if not _GRPC_AVAILABLE or _AdsServicer is None:
        logger.warning(
            "gRPC ADS server not started — required packages missing: %s",
            _GRPC_IMPORT_ERROR,
        )
        return

    global _servicer
    try:
        poll_interval_seconds = float(app_config.get("GRPC_XDS_POLL_INTERVAL_SECONDS", 0.0))
    except (TypeError, ValueError):
        poll_interval_seconds = 0.0
    _servicer = _AdsServicer(xds, poll_interval_seconds=poll_interval_seconds)

    try:
        port = int(app_config.get("GRPC_XDS_PORT", 4001))
    except (TypeError, ValueError):
        logger.warning("Invalid GRPC_XDS_PORT value %r; defaulting to 4001", app_config.get("GRPC_XDS_PORT"))
        port = 4001

    async def serve() -> None:
        server = grpclib.server.Server([_servicer])
        await server.start("0.0.0.0", port)
        logger.info("gRPC ADS server listening on port %d", port)
        await server.wait_closed()

    try:
        asyncio.run(serve())
    except Exception:
        logger.exception("gRPC ADS server stopped unexpectedly")
        raise


def notify_xds_update() -> None:
    """Push a snapshot update to all connected Envoy instances.

    Call this immediately after every curr_cluster.updateRouteMapping() in
    app.py.  Safe to call even before the gRPC server has started or when no
    Envoy instances are connected — it is a no-op in both cases.
    """
    if _servicer is not None:
        _servicer.notify()

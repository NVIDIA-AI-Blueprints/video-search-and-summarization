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
import yaml
import time
from flask import Flask, Response, g, has_request_context, render_template, request, stream_with_context
from flask import jsonify
from simple_settings import LazySettings
from flask_kafka import FlaskKafka
from threading import Event
import signal
from threading import Thread, Lock
import logging
import sys
# from lib.podprovisioner.kubernetes.k8sclient import k8sclient
from lib.podprovisioner.kubernetes.cluster import cluster
from lib.parameters import configserver
from lib.parameters.redisconfig import clear_stale_redis_workload_spec_lock_keys, redisconfig
from lib.messaging import kafka
from lib.podprovisioner.provisionconfig import provisionconfig
from lib.podprovisioner.healthwatcher import (
    WorkloadHealthWatcher,
    WorkloadUnhealthyError,
)
from lib.messaging.redisMessaging import redisMessaging
from lib.messaging.redisMessaging import Consumer
from lib.xDS.envoyxDS import envoyxDS
from lib.xDS.grpc_xds_server import (
    can_start_grpc_xds_server,
    is_grpc_xds_enabled,
    start_grpc_xds_server,
    notify_xds_update,
)
from lib import tracing
from lib.bus_outcomes import (
    EVENT_NOOP,
    EVENT_OK,
    EVENT_RETRYABLE,
    EVENT_TERMINAL,
    classify_exception,
    decide_commit,
    kafka_message_key,
    kafka_park_offset_on_next_commit,
    kafka_rewind_to_message,
    log_terminal_failure,
    redis_message_key,
)
from lib.logging import configure_root_logging, log_rate_limited
from lib.wdm_swagger_ui import openapi_public_server_root, register_wdm_swagger_ui
from lib.lifecycle.http_header import (
    ACTION_ADD,
    ACTION_DELETE,
    ACTION_REPROVISION,
    MODE_HTTP_HEADER,
    build_http_lifecycle_event_payload,
    extract_header_value,
    is_message_bus_lifecycle_mode,
    lifecycle_header_name,
    match_http_lifecycle_action,
    normalize_lifecycle_ingress_mode,
)
import requests
import os
import os.path
import re
import datetime
import uuid
from prometheus_client import Gauge, generate_latest
import socket

class MaxReplicaException (Exception):

    def __init__(self, replica_count):
        super().__init__(f"Max replica count {replica_count} reached")


# Returned by provisionStreamRedis / reprovisionStreamRedis when placement is
# deferred because not all StatefulSet replicas are ready. Redis consumer must
# not ACK so the message stays pending until pods recover.
PROVISION_DEFERRED_UNREADY_PODS = object()

CONFIGURE_OK = "CONFIGURE_OK"
CONFIGURE_NOOP = "CONFIGURE_NOOP"
CONFIGURE_FAILED = "CONFIGURE_FAILED"
CONFIGURE_DEFERRED = "CONFIGURE_DEFERRED"


settings = LazySettings("config")
app = Flask(__name__)
s = settings.Config()
app.config.from_object(s)
if str(os.environ.get("WDM_TRUST_PROXY_HEADERS", "1")).strip().lower() not in (
    "0",
    "false",
    "no",
):
    from werkzeug.middleware.proxy_fix import ProxyFix

    _pfx = str(os.environ.get("WDM_TRUST_PROXY_PREFIX", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
    )
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=0,
        x_prefix=1 if _pfx else 0,
    )
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
wl_log_prefix = app.config.get("WDM_WL_OBJECT_NAME", "wdm")
# Prefer process env; if unset, seed from Config so config.yml/defaults apply.
for _log_key in ("WDM_LOG_LEVEL", "WDM_LOG_FORMAT", "WDM_LOG_TO_FILE"):
    if _log_key not in os.environ or not str(os.environ.get(_log_key, "")).strip():
        _val = app.config.get(_log_key)
        if _val is None:
            continue
        if isinstance(_val, bool):
            os.environ[_log_key] = "true" if _val else "false"
        else:
            os.environ[_log_key] = str(_val)
configure_root_logging(wl_log_prefix, REPO_ROOT, component="workload")
app.logger = logging.getLogger(__name__)


def _wdm_http_request_elapsed_s():
    """Seconds since start of this HTTP request (see before_request timer), or None."""
    if not has_request_context():
        return None
    t0 = getattr(g, "_wdm_request_t0", None)
    if t0 is None:
        return None
    return time.perf_counter() - t0


@app.before_request
def _wdm_request_timer_start():
    g._wdm_request_t0 = time.perf_counter()


@app.after_request
def _wdm_request_timer_log(response):
    t0 = getattr(g, "_wdm_request_t0", None)
    if t0 is not None:
        elapsed = time.perf_counter() - t0
        path = request.path or ""
        status = response.status_code
        # Steady-state dashboard / health polls are DEBUG; keep interesting traffic visible.
        quiet_prefixes = (
            "/pod_list",
            "/healthz",
            "/health",
            "/metrics",
            "/get_wl_replica_data",
            "/current_distributed_streams",
        )
        is_quiet = any(path == p or path.startswith(p + "/") for p in quiet_prefixes)
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif is_quiet:
            level = logging.DEBUG
        else:
            level = logging.INFO
        app.logger.log(
            level,
            "http_request %s %s status=%s elapsed_s=%.6f",
            request.method,
            path,
            status,
            elapsed,
        )
    return response


app.logger.info(
    "Kafka bootstrap url {}".
    format(app.config["WDM_KFK_BOOTSTRAP_URL"])
)
# swagger / OpenAPI 3.0.3 — custom UI so assets and spec URL work behind a path prefix (Envoy /sdrc/…/).
SWAGGER_URL = "/api/docs"
# From …/api/docs/ up two segments to app root, then openapi.json (../ would be …/api/openapi.json).
OPENAPI_SWAGGER_REL_URL = "../../openapi.json"


register_wdm_swagger_ui(
    app,
    SWAGGER_URL,
    OPENAPI_SWAGGER_REL_URL,
    "SDR Coordinator API",
    blueprint_name="swagger_ui_wdm_app",
)


def _app_openapi_document():
    """OpenAPI 3.0.3 description of app.py HTTP routes (single-workload SDR Coordinator)."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "SDR Coordinator API",
            "version": "1.0.0",
            "description": (
                "Single-workload SDR Coordinator (app.py): allocation, streams, Redis cache, "
                "Envoy xDS, and metrics. Interactive docs: Swagger UI at /api/docs/."
            ),
        },
        "servers": [{"url": "/", "description": "Workload HTTP port (PORT)"}],
        "tags": [
            {"name": "meta", "description": "Spec and landing"},
            {"name": "health", "description": "Liveness"},
            {"name": "config", "description": "Workload and cluster config"},
            {"name": "xds", "description": "Envoy discovery (CDS/RDS-style JSON)"},
            {"name": "streams", "description": "Streams, cache, and pod listings"},
            {"name": "admin", "description": "Provisioning and cache updates"},
            {"name": "metrics", "description": "Prometheus"},
        ],
        "paths": {
            "/openapi.json": {
                "get": {
                    "tags": ["meta"],
                    "summary": "OpenAPI document",
                    "operationId": "getOpenApi",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.0.3 document",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": True,
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/": {
                "get": {
                    "tags": ["meta"],
                    "summary": "Configuration landing page (HTML)",
                    "operationId": "getIndex",
                    "responses": {
                        "200": {
                            "description": "HTML table of non-sensitive app.config keys",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/healthz": {
                "get": {
                    "tags": ["health"],
                    "summary": "Health check",
                    "operationId": "getHealthz",
                    "responses": {
                        "200": {
                            "description": "Plain-text OK",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/reset": {
                "get": {
                    "tags": ["admin"],
                    "summary": "Reset caches and optional preload file",
                    "operationId": "getReset",
                    "responses": {
                        "200": {
                            "description": "Plain text ok",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/get_config": {
                "get": {
                    "tags": ["config"],
                    "summary": "Current allocation configs",
                    "operationId": "getConfig",
                    "responses": {
                        "200": {
                            "description": "JSON allocation config",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/replicas": {
                "get": {
                    "tags": ["config"],
                    "summary": "Replica counts for workload StatefulSet",
                    "operationId": "getReplicas",
                    "responses": {
                        "200": {
                            "description": "wl_object, replicas, wlobreplicas",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/getwl": {
                "get": {
                    "tags": ["config"],
                    "summary": "Workload spec by id",
                    "operationId": "getWl",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Spec JSON or empty list",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"type": "object"}},
                                }
                            },
                        }
                    },
                }
            },
            "/getpoddns": {
                "get": {
                    "tags": ["config"],
                    "summary": "Pod DNS mapping by stream id",
                    "operationId": "getPodDns",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "poddns, id, podname",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/v3/discovery:routes": {
                "post": {
                    "tags": ["xds"],
                    "summary": "Route discovery (RDS)",
                    "operationId": "postDiscoveryRoutes",
                    "responses": {
                        "200": {
                            "description": "Envoy-style route JSON",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/v3/discovery:clusters": {
                "post": {
                    "tags": ["xds"],
                    "summary": "Cluster discovery (CDS)",
                    "operationId": "postDiscoveryClusters",
                    "responses": {
                        "200": {
                            "description": "Envoy-style cluster JSON",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/stream": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Server-sent replica count stream",
                    "operationId": "getStream",
                    "responses": {
                        "200": {
                            "description": "Chunked text (replica count values)",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/current_distributed_streams_cache": {
                "get": {
                    "tags": ["streams"],
                    "summary": "All stream specs from cache (list)",
                    "operationId": "getCurrentDistributedStreamsCache",
                    "responses": {
                        "200": {
                            "description": "JSON array of stream objects",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"type": "object"}}
                                }
                            },
                        }
                    },
                }
            },
            "/current_distributed_streams_name_id_url": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Streams keyed by workload id field",
                    "operationId": "getCurrentDistributedStreamsNameIdUrl",
                    "responses": {
                        "200": {
                            "description": "Object map id -> stream event payload",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/current_streamid_address_mapping": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Stream id to address mapping (Redis)",
                    "operationId": "getCurrentStreamidAddressMapping",
                    "responses": {
                        "200": {
                            "description": "JSON mapping",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/redis_cache_data": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Full Redis cache object and pod stream data",
                    "operationId": "getRedisCacheData",
                    "responses": {
                        "200": {
                            "description": "cache_object and data",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RedisCacheDataResponse"}
                                }
                            },
                        },
                        "500": {
                            "description": "getAllStreams failed",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorJson"}
                                }
                            },
                        },
                    },
                }
            },
            "/cache_metadata_update": {
                "post": {
                    "tags": ["admin"],
                    "summary": "Merge metadata for a stream in cache",
                    "operationId": "postCacheMetadataUpdate",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CacheMetadataUpdateBody"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Plain text confirmation",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                        "400": {
                            "description": "Wrong Content-Type or missing fields / cache miss",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                    },
                }
            },
            "/metrics": {
                "get": {
                    "tags": ["metrics"],
                    "summary": "Prometheus metrics",
                    "operationId": "getMetrics",
                    "responses": {
                        "200": {
                            "description": "Prometheus exposition format",
                            "content": {
                                "text/plain": {
                                    "schema": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            },
            "/apply_metadata_payload": {
                "post": {
                    "tags": ["admin"],
                    "summary": "Provision / reprovision / deprovision / configure from event payload",
                    "operationId": "postApplyMetadataPayload",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "description": "Structure uses WDM_EVENT_OBJECT_FIELD and WDM_WL_ID_FIELD from config",
                                    "additionalProperties": True,
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Plain text status",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                        "400": {
                            "description": "Invalid Content-Type or missing stream id",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                        "500": {
                            "description": "Processing failed (e.g. max replicas)",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                    },
                }
            },
            "/remove_stream": {
                "post": {
                    "tags": ["admin"],
                    "summary": "Deprovision stream by stream_id",
                    "operationId": "postRemoveStream",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["stream_id"],
                                    "properties": {
                                        "stream_id": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Removed",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RemoveStreamOk"}
                                }
                            },
                        },
                        "400": {
                            "description": "Not JSON or missing stream_id",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                        "404": {
                            "description": "Stream not found",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorJson"}
                                }
                            },
                        },
                        "500": {
                            "description": "Deprovision error",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorJson"}
                                }
                            },
                        },
                    },
                }
            },
            "/get_wl_replica_data": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Replica and pod saturation stats",
                    "operationId": "getWlReplicaData",
                    "responses": {
                        "200": {
                            "description": "JSON replica summary",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/pod_list": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Pods with per-pod stream ids",
                    "operationId": "getPodList",
                    "responses": {
                        "200": {
                            "description": "{ pods: [...] }",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PodListResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/down_pods": {
                "get": {
                    "tags": ["streams"],
                    "summary": "Non-Running pods with stream ids",
                    "operationId": "getDownPods",
                    "responses": {
                        "200": {
                            "description": "{ pods: [...] }",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PodListResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/getpodInfo": {
                "get": {
                    "tags": ["config"],
                    "summary": "Disaggregated pod info by id",
                    "operationId": "getPodInfo",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Pod detail object or empty list",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "ErrorJson": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "stream_id": {"type": "string"},
                    },
                },
                "RemoveStreamOk": {
                    "type": "object",
                    "required": ["status", "stream_id"],
                    "properties": {
                        "status": {"type": "string", "enum": ["ok"]},
                        "stream_id": {"type": "string"},
                    },
                },
                "RedisCacheDataResponse": {
                    "type": "object",
                    "required": ["cache_object", "data"],
                    "properties": {
                        "cache_object": {"type": "string"},
                        "data": {"type": "object", "additionalProperties": True},
                    },
                },
                "CacheMetadataUpdateBody": {
                    "type": "object",
                    "required": ["stream_id", "additional_metadata"],
                    "properties": {
                        "stream_id": {"type": "string"},
                        "additional_metadata": {"type": "object", "additionalProperties": True},
                        "overwrite": {"type": "boolean"},
                        "cache_key": {"type": "string"},
                    },
                },
                "PodListResponse": {
                    "type": "object",
                    "required": ["pods"],
                    "properties": {
                        "pods": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PodListEntry"},
                        }
                    },
                },
                "PodListEntry": {
                    "type": "object",
                    "properties": {
                        "podName": {"type": "string"},
                        "podIp": {"type": "string"},
                        "podDns": {"type": "string"},
                        "phase": {"type": "string"},
                        "stream_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


@app.route("/openapi.json", methods=["GET"])
def openapi_spec():
    """Machine-readable OpenAPI 3.0.3 document for this app."""
    doc = dict(_app_openapi_document())
    try:
        root = openapi_public_server_root()
        if root:
            doc["servers"] = [{"url": root + "/", "description": "This deployment"}]
    except Exception:
        pass
    body = json.dumps(doc, indent=2)
    return Response(
        body,
        mimetype="application/vnd.oai.openapi+json;version=3.0",
    )


INTERRUPT_EVENT = Event()
bus = None
REDIS_IS_CONNECTED = False
REDIS_LISTENER_PAUSE = True
try:
    if app.config["WDM_KFK_ENABLE"]:
        bus = FlaskKafka(
            INTERRUPT_EVENT,
            bootstrap_servers=app.config["WDM_KFK_BOOTSTRAP_URL"],
            group_id=app.config["WDM_CONSUMER_GRP_ID"],
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            session_timeout_ms=app.config["WDM_KFK_SESSION_TIME_OUT"],
            max_poll_interval_ms=900000,  # ,
            reconnect_backoff_max_ms=10000,
            metadata_max_age_ms=4000,
            max_poll_records=1  # ,
            # value_deserializer=lambda m: json.loads(m.decode('utf-8'))
        )
    else:
        app.logger.info("Kafka disabled")
except Exception:
    app.logger.info("Kafka not configured")

evic_q_on_no_capacity = \
    True if app.config["WDM_EVICT_QUEUE_ON_NO_CAPACITY"].lower() == "true" \
    else False

wl_object_name = app.config["WDM_WL_OBJECT_NAME"]
topic = app.config["WDM_MSG_TOPIC"]
wdm_wl_spec = app.config["WDM_WL_SPEC"]
change_field = app.config["WDM_WL_CHANGE_FIELD"]
change_id_add = app.config["WDM_WL_CHANGE_ID_ADD"]
change_id_reprovision = app.config["WDM_WL_CHANGE_ID_REPROVISION"]
change_id_del = app.config["WDM_WL_CHANGE_ID_DEL"]
change_id_pod_configure = app.config["WDM_WL_CHANGE_ID_POD_CONFIGURE"]
lifecycle_ingress_mode = normalize_lifecycle_ingress_mode(
    app.config.get("WDM_LIFECYCLE_INGRESS_MODE")
)
app.config["WDM_LIFECYCLE_INGRESS_MODE"] = lifecycle_ingress_mode
cache_method = app.config["WDM_CACHE_METHOD"]
if cache_method == 'redis':
    wl_spec_obj = app.config["WDM_REDIS_CACHE_OBJECT"]
    clear_stale_redis_workload_spec_lock_keys(app.config, wl_spec_obj)
    cfg = redisconfig(wl_spec_obj=wl_spec_obj, app_config=app.config)
else:
    cfg = configserver(wl_spec_file=wdm_wl_spec, app_config=app.config)
curr_cluster = cluster(
    app.config,
    bearer_token=app.config["KUBERNETES_JWT_TOKEN"],
    kubernetes_url=app.config["KUBERNETES_URL"],
    ssl_ca_cert=app.config["SSL_CERTS"],
)
app.logger.info(
    "WDM_KAFKA_MSG_KEY=%s WDM_REDIS_MSG_KEY=%s"
    % (app.config["WDM_KAFKA_MSG_KEY"], app.config["WDM_REDIS_MSG_KEY"])
)
app.logger.info(app.config["WDM_WL_REDIS_SERVER"])

_VALID_ASSIGNING_METHODS = ("lru_round_robin", "sequential")
_assigning_method = app.config["WDM_WL_ASSIGNING_METHOD"]
if _assigning_method not in _VALID_ASSIGNING_METHODS:
    raise ValueError(
        f"WDM_WL_ASSIGNING_METHOD='{_assigning_method}' is not valid. "
        f"Choose one of: {_VALID_ASSIGNING_METHODS}"
    )
app.logger.info("WDM_WL_ASSIGNING_METHOD=%s", _assigning_method)

kfk = kafka(app.config)
lock = Lock()
envy = envoyxDS(app.config)
redisMsging = redisMessaging(app.config)
pc = provisionconfig(app.config, redisMsging, cfg)
initiatorWLObjname = app.config["WDM_INITIATOR_WLOBJ_NAME"]
reprovision_recent_removals = {}


def _resolve_workload_pods_for_health():
    """Return pod inventory for HTTP health polling.

    Docker mode reads host:port from each entry's ``provisioning_address`` in
    ``docker_cluster_config.json``. Only ``WDM_WL_HEALTH_CHECK_URL`` (path) is
    configurable when building probe URLs. K8s falls back to live pod IPs.
    """
    try:
        docker_targets = curr_cluster.get_health_check_targets()
        if docker_targets is not None:
            return docker_targets
        wl_objs = curr_cluster.getWorkloadObjects()
        if not wl_objs:
            return []
        return curr_cluster.getPodIps(wl_objs) or []
    except Exception:
        app.logger.exception("Failed resolving workload pods for health watcher")
        return []


def _config_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


health_watcher = None
if _config_bool(app.config.get("WDM_WL_HEALTH_CHECK_WAIT_ENABLED"), True):
    health_watcher = WorkloadHealthWatcher(
        app.config,
        resolve_pods=_resolve_workload_pods_for_health,
        logger_override=app.logger,
    )
    curr_cluster.set_health_watcher(health_watcher)
    pc.set_health_watcher(health_watcher)
else:
    app.logger.info(
        "WDM_WL_HEALTH_CHECK_WAIT_ENABLED=false; using legacy pod readiness "
        "(Docker container state for PodErrorWatcher; no HTTP health wait in add())"
    )


def should_handle_config_events():
    """Return True only when WDM_HANDLE_CONFIG_EVENTS is explicitly enabled.

    Default is false (opt-in): workloads without /config must not process
    configure events. Set WDM_HANDLE_CONFIG_EVENTS=true to enable.
    """
    return _config_bool(app.config.get("WDM_HANDLE_CONFIG_EVENTS"), False)


def _event_stream_meta(original_json, wl_d=None):
    """Best-effort stream id / change extraction for terminal failure logs."""
    stream_id = None
    change = None
    try:
        ev = None
        if isinstance(original_json, dict):
            ev = original_json.get(app.config["WDM_EVENT_OBJECT_FIELD"])
        if isinstance(ev, dict):
            stream_id = ev.get(app.config["WDM_WL_ID_FIELD"])
            change = ev.get(change_field)
        if isinstance(wl_d, dict):
            if change is None:
                change = wl_d.get(change_field)
            if stream_id is None:
                stream_id = wl_d.get(app.config["WDM_WL_ID_FIELD"])
    except Exception:
        pass
    return stream_id, change


def _apply_bus_commit_decision(
    *,
    bus_name,
    message_key,
    outcome,
    error=None,
    original_json=None,
    wl_d=None,
):
    """Apply safe bus policy: commit terminal/ok; retry retryable until limit."""
    retry_limit = int(app.config.get("WDM_EVENT_RETRY_LIMIT", 20) or 20)
    should_commit, final_outcome, attempt = decide_commit(
        outcome, message_key, retry_limit
    )
    if final_outcome == EVENT_TERMINAL:
        stream_id, change = _event_stream_meta(original_json, wl_d)
        reason = "terminal"
        if outcome == EVENT_RETRYABLE and attempt:
            reason = "retry_limit_exceeded"
        log_terminal_failure(
            app.logger,
            bus=bus_name,
            message_id=message_key,
            error=error if error is not None else final_outcome,
            change=change,
            stream_id=stream_id,
            workload=app.config.get("WDM_WL_OBJECT_NAME"),
            attempt=attempt or None,
            payload=original_json if original_json is not None else wl_d,
            reason=reason,
        )
    return should_commit, final_outcome, attempt


def _end_error_span_for_event(original_json):
    try:
        _cid = original_json[app.config["WDM_EVENT_OBJECT_FIELD"]][
            app.config["WDM_WL_ID_FIELD"]
        ]
    except (KeyError, TypeError):
        return
    _span = id_ctx_mapping.get(_cid, {}).get("span")
    if _span is not None:
        _span.set_status(tracing.StatusCode.ERROR)
        _span.end()


def _configure_failure_result():
    if _config_bool(app.config.get("WDM_CONFIG_DEFER_ON_FAILURE"), False):
        return CONFIGURE_DEFERRED
    return CONFIGURE_FAILED


def _config_endpoint_for_pod(pod_info):
    return "http://{}:{}{}".format(
        pod_info.get("podIp", "?"),
        app.config["WDM_CONFIG_PORT"],
        app.config["WDM_CONFIG_URL"],
    )


def _configure_failure_class(resp):
    if resp is None:
        return "no_response"
    return "http_{}".format(getattr(resp, "status_code", "unknown"))


# Track active provision-add threads; delete stream waits until this is empty
provision_add_threads = {}
provision_add_threads_lock = Lock()
global last_restart
last_restart = datetime.datetime.now(datetime.timezone.utc)

if app.config["WDM_DISABLE_WERKZEUG_LOGGING"]:
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.disabled = True



id_ctx_mapping = {}


def _pod_ordinal(pod_name):
    """Return a stable sort key for round-robin tie-breaking.

    Returns a ``(numeric_ordinal, pod_name)`` tuple so that the round-robin
    selection is deterministic regardless of pod naming convention:

    * **Indexed pods** (e.g. ``my-app-3``): ``(3, "my-app-3")``.
      The numeric ordinal is the primary discriminator, so the existing
      0→1→2→3→4→0 cycle is preserved unchanged.

    * **Non-indexed pods** (e.g. ``worker-alpha``): ``(0, "worker-alpha")``.
      All such pods share ordinal 0, so they are sorted lexicographically by
      name.  This produces an alphabetic round-robin
      (alpha→beta→gamma→alpha→…) without requiring any global state.

    Using only the integer (the old behaviour) caused all non-indexed pods to
    compare equal, making ``min()`` fall through to iteration order — correct
    only accidentally when the API returns pods in a stable sequence.
    """
    m = re.search(r"-(\d+)$", pod_name)
    return (int(m.group(1)) if m else 0, pod_name)


def _select_pod_from_candidates(candidates, assigning_method):
    """Select one eligible-pod tuple according to the configured policy."""
    if assigning_method == "sequential":
        return candidates[0]
    min_count = min(count for _, count, _ in candidates)
    return min(
        (entry for entry in candidates if entry[1] == min_count),
        key=lambda entry: _pod_ordinal(entry[0]["podName"]),
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return """
    OK
    """

def _is_hidden_config_key(key):
    """True if key should not be shown on the landing page (sensitive)."""
    if not isinstance(key, str) or key.startswith("_"):
        return True
    upper = key.upper()
    if "TOKEN" in upper or "SECRET" in upper or "PASSWORD" in upper or "BEARER" in upper:
        return True
    return False


@app.route("/", methods=["GET"])
def index():
    """Landing page showing current configuration (non-sensitive)."""
    items = []
    for key in sorted(app.config.keys()):
        if _is_hidden_config_key(key):
            continue
        try:
            val = app.config[key]
            items.append((key, val if val is not None else ""))
        except Exception:
            continue
    rows = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(
            key,
            json.dumps(val) if isinstance(val, (dict, list)) else str(val).replace("<", "&lt;").replace(">", "&gt;"),
        )
        for key, val in items
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>SDR Coordinator</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:1.5rem 2rem;background:#0f1419;color:#e6edf3;} "
        "h1{font-size:1.25rem;} table{border-collapse:collapse;} th,td{border:1px solid #2d3a4d;padding:0.5rem 0.75rem;text-align:left;} "
        "th{background:#1a2332;} td:first-child{font-weight:500;}</style></head><body>"
        "<h1>SDR Coordinator</h1><p>Current configuration</p><table><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody>"
        + rows +
        "</tbody></table></body></html>"
    )
    return Response(html, mimetype="text/html")


@app.route("/reset", methods=["GET"])
def reset():
    cfg.eraseSpecContent()

    if redisMsging is not None:
        redisMsging.clearAllData()
    if app.config["WDM_RESET_PRELOAD_FILE"]:
        try:
            preloadFile = app.config["WDM_PRELOAD_WORKLOAD"]
            if preloadFile is not None:
                try:
                    f = open(preloadFile, 'w')
                finally:
                    f.close()
        except Exception as e:
            app.logger.info(f"preload file could not be loaded {e}")
    return "ok"

@app.route("/get_config", methods=["GET"])
def config_endpoint():
    return jsonify(curr_cluster.get_current_allocation_configs())

def resetWorkLoadPod (wl_pod):
    app.logger.info (f"erase content for {wl_pod} from cache")
    if app.config["WDM_CLUSTER_TYPE"].lower() == "k8s":
        cfg.erasePodSpecContent(wl_pod)
    if redisMsging is not None:
        redisMsging.clearPodData(wl_pod)



@app.route("/replicas", methods=["GET"])
def getReplicas():
    readReplicas = curr_cluster.getReadyReplicas()
    Wlobj = curr_cluster.getStatefulSets()
    d = dict()
    d["wl_object"] = wl_object_name
    d["replicas"] = readReplicas
    d["wlobreplicas"] = Wlobj.status.replicas
    return jsonify(d)


@app.route("/getwl", methods=["GET"])
def getWl():
    args = request.args
    if args is None:
        return jsonify([])
    id = args.get("id")
    if id is None:
        return jsonify([])
    spec = cfg.getworkLoadSpecById(id)
    return (jsonify(spec if spec is not None else []))


def __getPodDns__(id):
    pn = redisMsging.getIdPodMapping(id)
    pm = redisMsging.getIdPodPodDnsMapping(podname=pn)
    r = dict()
    r["poddns"] = pm if pm is not None else ""
    r["id"] = id
    r["podname"] = pn if pn is not None else ""
    return(r)


@app.route("/getpoddns", methods=["GET"])
def getPodDns():
    try:
        if request.args is None:
            return jsonify([])
        args = request.args
        id = args.get("id")
        if id is None:
            return jsonify([])
        r = __getPodDns__(id)
        return (jsonify(r if r is not None else []))
    except Exception:
        return (jsonify([]))


@app.route('/v3/discovery:routes', methods=['POST'])
def XDSRouteConfiguration():
    return jsonify(envy.routeXDs())


@app.route("/v3/discovery:clusters", methods=["POST"])
def indexClusterXDS():
    return jsonify(envy.clusterXDs())


@app.route('/stream', methods=["GET"])
def streamed_response():
    @stream_with_context
    def generate():
        while True:
            localtime = time.localtime()
            result = time.strftime("%I:%M:%S %p", localtime)
            readReplicas = curr_cluster.getReadyReplicas()
            yield f"{readReplicas}"
    return Response(generate())

@app.route('/current_distributed_streams_cache', methods=["GET"])
def current_distributed_streams_cache():
    pod_info = cfg.getAllStreams()
    stream_list = [pod for key, pod in pod_info.items()]
    return jsonify(stream_list)


def _workload_specs_list_for_pod(pod_name):
    """Parse workload spec JSON for one pod into a list of stream dicts.

    getworkLoadSpecs returns json.dumps(redis_value). Redis may hold an empty
    string, a JSON array string, or legacy double-encoded JSON. Unconditional
    double json.loads in the route caused JSONDecodeError when the inner value
    was '' or already a list.
    """
    raw = cfg.getworkLoadSpecs(pod_name)
    if raw is None:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    s = raw.strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if isinstance(parsed, str):
        inner = parsed.strip()
        if not inner:
            return []
        try:
            parsed = json.loads(inner)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
    if not isinstance(parsed, list):
        return []
    return parsed


@app.route('/current_distributed_streams_name_id_url', methods=["GET"])
def current_distributed_streams_name_id_url():
    pod_names = cfg.getpods()
    all_returns = {}
    ev_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    id_field = app.config["WDM_WL_ID_FIELD"]
    try:
        for pod in pod_names:
            for stream in _workload_specs_list_for_pod(pod):
                if not isinstance(stream, dict) or ev_field not in stream:
                    continue
                curr_dict = stream[ev_field]
                if id_field not in curr_dict:
                    continue
                all_returns[curr_dict[id_field]] = curr_dict
    except Exception as e:
        app.logger.info("Error while getting all stream name/id/url: " + repr(e))
        all_returns = {}

    return jsonify(all_returns)

@app.route('/current_streamid_address_mapping', methods=["GET"])
def current_streamid_address_mapping():
    curr_mapping = redisMsging.getCurrentMapping()
    return jsonify(curr_mapping)


def _clean_json_for_display(obj):
    """Normalize structure for display: sort dict keys, parse double-encoded JSON strings."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): _clean_json_for_display(v) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))}
    if isinstance(obj, list):
        return [_clean_json_for_display(item) for item in obj]
    if isinstance(obj, str):
        s = obj.strip()
        if (s.startswith("{") or s.startswith("[")) and len(s) > 1:
            try:
                parsed = json.loads(obj)
                return _clean_json_for_display(parsed)
            except (TypeError, ValueError):
                pass
        return obj
    return obj


@app.route("/redis_cache_data", methods=["GET"])
def redis_cache_data():
    """Return WDM_REDIS_CACHE_OBJECT name and the full cache data (pod -> stream specs)."""
    try:
        data = cfg.getAllStreams()
    except Exception as e:
        app.logger.exception("redis_cache_data: getAllStreams failed")
        return jsonify({"error": str(e), "cache_object": app.config.get("WDM_REDIS_CACHE_OBJECT", "")}), 500
    cleaned = _clean_json_for_display(data)
    return Response(
        json.dumps({
            "cache_object": app.config.get("WDM_REDIS_CACHE_OBJECT", ""),
            "data": cleaned,
        }, indent=2, sort_keys=True, default=str),
        mimetype="application/json",
    )


@app.route("/cache_metadata_update", methods=["POST"])
def cache_metadata_update():
    content_type = request.headers.get('Content-Type')
    if content_type != 'application/json':
        return "JSON input is required for this endpoint", 400
    input_json = request.json
    if "stream_id" not in input_json or "additional_metadata" not in input_json:
        return "stream_id or additional_metadata is missing, will not process request", 400
    stream_id = input_json["stream_id"]
    additional_metadata = input_json["additional_metadata"]
    overwrite = False
    cache_key = "external_metadata"
    
    if "overwrite" in input_json and input_json["overwrite"] == True:
        overwrite = True
    if "cache_key" in input_json:
        cache_key = input_json["cache_key"]
        
    # Find stream id in cache
    pod_name, cache_info = cfg.getCacheInfoForStreamId(stream_id)
    if cache_info is None:
        return f"Cache info for stream_id {stream_id} not found. Will not modify cache", 400
    
    # Determine if new metadata should overwrite or if we should update existing data, create new dictionary object
    new_dict_data = cache_info[cache_key].copy() if cache_key in cache_info else {}
    if not overwrite and cache_key in cache_info:
        new_dict_data.update(additional_metadata)
    else:
        new_dict_data = additional_metadata
    cache_info[cache_key] = new_dict_data
    
    # Remove key if there is no data in the metadata dictionary
    if not new_dict_data:
        cache_info.pop(cache_key)
    
    # Save value to cache
    cfg.updateWorkLoadSpec(pod_name, stream_id, cache_info)
    
    return "Cache has been updated", 200   

stream_count = Gauge("stream_count", "number of streams for each pod", ["pod"])
CONTENT_TYPE_LATEST = str('text/plain; version=0.0.4; charset=utf-8')

@app.route('/metrics')
def metrics():
    WLObj = curr_cluster.getWorkloadObjects()
    if WLObj is not None:
        podsInfo = curr_cluster.getPodIps(WLObj)
        if podsInfo is not None:
            for podInfoItm in podsInfo:
                podName = podInfoItm["podName"]
                spec_count = cfg.getSpecCount(podName)
                stream_count.labels(podName).set(spec_count)
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def _try_http_provision(operation_name, runner):
    """Map health-check deferrals to the HTTP provisioning sentinel."""
    try:
        return runner()
    except WorkloadUnhealthyError as exc:
        app.logger.info(
            "HTTP %s deferred by health check: %s",
            operation_name,
            exc,
        )
        return PROVISION_DEFERRED_UNREADY_PODS


def _http_deferred_response(action, stream_id=None, source="apply_metadata_payload", **extra):
    body = {
        "status": "deferred",
        "reason": "waiting for healthy workload pod; retry later",
        "action": action,
        "source": source,
    }
    if stream_id is not None:
        body["stream_id"] = stream_id
    body.update(extra)
    return jsonify(body), 503


@app.route("/apply_metadata_payload", methods=["POST"])
def apply_metadata_payload():
    content_type = request.headers.get('Content-Type')
    if content_type != 'application/json':
        return "JSON input is required for this endpoint", 400
    jValue = request.json
    print(jValue)
    if app.config["WDM_WL_ID_FIELD"] not in jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]:
        return "stream_id is missing, will not process request", 400
    wl_d = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]
    print(wl_d)
    try:
        #tracing context for stream id
        global id_ctx_mapping
        if wl_d is not None:
            camera_id = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
            if camera_id not in id_ctx_mapping:
                otel_parent_span, parent_context = tracing.create_parent_span(camera_id, "apply_metadata_payload()", redisMsging)
                id_ctx_mapping[camera_id] = {
                    "context": parent_context,
                    "span": otel_parent_span
                }
            else:
                parent_context = id_ctx_mapping[camera_id]["context"]

        if (wl_d is not None) and (change_field in wl_d) and (
            wl_d[change_field].lower() == change_id_add
        ):
            app.logger.info("provision stream")
            response = _try_http_provision(
                "add",
                lambda: provisionStreamRedis(
                    app.config["WDM_WL_OBJECT_NAME"],
                    wl_d, jValue, parent_context,
                ),
            )
            if response is PROVISION_DEFERRED_UNREADY_PODS:
                return _http_deferred_response(
                    "add",
                    stream_id=wl_d.get(app.config["WDM_WL_ID_FIELD"]),
                )
            return "Provisioning process called"

        elif (wl_d is not None) and (change_field in wl_d) and (
            wl_d[change_field].lower() == change_id_reprovision
        ):
            app.logger.info("reprovision stream")
            response = _try_http_provision(
                "reprovision",
                lambda: reprovisionStreamRedis(
                    app.config["WDM_WL_OBJECT_NAME"],
                    wl_d, jValue, parent_context,
                ),
            )
            if response is PROVISION_DEFERRED_UNREADY_PODS:
                return _http_deferred_response(
                    "reprovision",
                    stream_id=wl_d.get(app.config["WDM_WL_ID_FIELD"]),
                )
            return "Reprovisioning process called"

        elif (wl_d is not None) and (change_field in wl_d) and (
            wl_d[change_field].lower() == change_id_del
        ):
            app.logger.info("deprovision stream")
            response = deprovisionStreamRedis(
                app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue, parent_context 
            )
            id_ctx_mapping[jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]]["span"].end()
            id_ctx_mapping.pop(jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]])   
            return "Deprovisioning process called"                            
        elif (wl_d is not None) and (change_field in wl_d) and (
            wl_d[change_field].lower() == change_id_pod_configure
        ):
            app.logger.info("configure stream")
            response = podConfigureRedis(
                app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue
            )
            if response == CONFIGURE_FAILED:
                return jsonify({
                    "error": "configuration failed",
                    "result": response,
                }), 502
            if response == CONFIGURE_DEFERRED:
                return jsonify({
                    "error": "configuration deferred",
                    "result": response,
                }), 503
            return "Configuration process called"                          
        else:
            app.logger.info("wl_d is None. wl_d: " + str(wl_d))
            return "wl_d is None."
    except MaxReplicaException as me:
        app.logger.error("Max replica exception %s", me)
        if wl_d is not None:
            cam_id = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
            if cam_id in id_ctx_mapping:
                id_ctx_mapping[cam_id]["span"].set_status(tracing.StatusCode.ERROR)
                id_ctx_mapping[cam_id]["span"].end()
                id_ctx_mapping.pop(cam_id)
        return "Failed to process the payload", 500


@app.route("/remove_stream", methods=["POST"])
def remove_stream():
    """Remove (deprovision) a stream by stream_id. Accepts JSON: {"stream_id": "<id>"}."""
    content_type = request.headers.get("Content-Type")
    if content_type != "application/json":
        return "JSON input is required for this endpoint", 400
    j = request.json
    if not j or "stream_id" not in j:
        return "stream_id is missing", 400
    stream_id = j["stream_id"]
    spec_list = cfg.getworkLoadSpecById(stream_id)
    if not spec_list:
        return jsonify({"error": "stream not found", "stream_id": stream_id}), 404
    spec = spec_list[0]
    event_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    wl_d = dict(spec[event_field])
    wl_d[change_field] = change_id_del
    jValue = {event_field: wl_d}
    try:
        deprovisionStreamRedis(
            app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue, None
        )
        return jsonify({"status": "ok", "stream_id": stream_id}), 200
    except MaxReplicaException as me:
        app.logger.error("Max replica exception %s", me)
        return jsonify({"error": str(me), "stream_id": stream_id}), 500
    except Exception as e:
        app.logger.exception("remove_stream failed for stream_id=%s", stream_id)
        return jsonify({"error": str(e), "stream_id": stream_id}), 500


def _http_header_lifecycle_json_body():
    raw_body = request.get_data(cache=True)
    if raw_body is None or raw_body.strip() == b"":
        return None, None

    body = request.get_json(silent=True)
    if body is None:
        return None, _http_header_lifecycle_error(
            "JSON lifecycle body is required when a body is present", 400
        )
    if not isinstance(body, dict):
        return None, _http_header_lifecycle_error(
            "JSON lifecycle body must be an object", 400
        )
    return body, None


def _http_header_lifecycle_error(message, status_code, **details):
    response = {"error": message}
    response.update(details)
    return jsonify(response), status_code


def _cached_stream_lifecycle_payload(stream_id):
    try:
        spec_list = cfg.getworkLoadSpecById(stream_id)
    except Exception:
        app.logger.exception("failed to load cached lifecycle state for %s", stream_id)
        return None

    if isinstance(spec_list, dict):
        return spec_list
    if spec_list and isinstance(spec_list, (list, tuple)):
        first_spec = spec_list[0]
        if isinstance(first_spec, dict):
            return first_spec
    return None


def _stream_known_for_lifecycle(stream_id, cached_payload):
    if cached_payload is not None:
        return True
    try:
        return redisMsging.getIdPodMapping(stream_id) is not None
    except Exception:
        app.logger.exception("failed to read route mapping for %s", stream_id)
        return False


def _ensure_lifecycle_trace_context(stream_id, span_name):
    global id_ctx_mapping
    if stream_id not in id_ctx_mapping:
        otel_parent_span, parent_context = tracing.create_parent_span(
            stream_id, span_name, redisMsging
        )
        id_ctx_mapping[stream_id] = {
            "context": parent_context,
            "span": otel_parent_span,
        }
        return parent_context
    return id_ctx_mapping[stream_id]["context"]


def _close_lifecycle_trace_context(stream_id):
    mapping = id_ctx_mapping.pop(stream_id, None)
    if mapping is None:
        return
    span = mapping.get("span")
    if span is not None:
        span.end()


@app.route(
    "/<path:lifecycle_path>",
    methods=["DELETE", "GET", "PATCH", "POST", "PUT"],
)
def http_header_lifecycle_ingress(lifecycle_path):
    has_body = bool((request.get_data(cache=True) or b"").strip())
    try:
        action = match_http_lifecycle_action(
            request.method, request.path, app.config, has_body=has_body
        )
    except ValueError as exc:
        app.logger.error("invalid HTTP lifecycle binding: %s", exc)
        return _http_header_lifecycle_error(str(exc), 500)
    if action is None:
        return jsonify({"error": "not found"}), 404

    try:
        mode = normalize_lifecycle_ingress_mode(
            app.config.get("WDM_LIFECYCLE_INGRESS_MODE")
        )
    except ValueError as exc:
        app.logger.error("invalid lifecycle ingress mode: %s", exc)
        return _http_header_lifecycle_error(str(exc), 500)

    if mode != MODE_HTTP_HEADER:
        return _http_header_lifecycle_error(
            "HTTP-header lifecycle ingress is not active",
            409,
            mode=mode,
            action=action,
        )

    header_name = lifecycle_header_name(app.config)
    stream_id = extract_header_value(request.headers, header_name)
    if stream_id is None:
        return _http_header_lifecycle_error(
            "missing stream id header: %s" % header_name,
            400,
            header=header_name,
        )

    body, error_response = _http_header_lifecycle_json_body()
    if error_response is not None:
        return error_response

    cached_payload = None
    if action in (ACTION_DELETE, ACTION_REPROVISION):
        cached_payload = _cached_stream_lifecycle_payload(stream_id)
        if action == ACTION_REPROVISION and cached_payload is None:
            return _http_header_lifecycle_error(
                "stream not found", 404, stream_id=stream_id
            )
        if action == ACTION_DELETE and not _stream_known_for_lifecycle(
            stream_id, cached_payload
        ):
            return _http_header_lifecycle_error(
                "stream not found", 404, stream_id=stream_id
            )

    payload_body = cached_payload if cached_payload is not None else body
    original_json = build_http_lifecycle_event_payload(
        app.config, action, stream_id, payload_body
    )
    wl_d = original_json[app.config["WDM_EVENT_OBJECT_FIELD"]]

    try:
        parent_context = _ensure_lifecycle_trace_context(
            stream_id, "http_header_lifecycle_ingress()"
        )
        if action == ACTION_ADD:
            response = _try_http_provision(
                "add",
                lambda: provisionStreamRedis(
                    app.config["WDM_WL_OBJECT_NAME"],
                    wl_d,
                    original_json,
                    parent_context,
                ),
            )
            if response is PROVISION_DEFERRED_UNREADY_PODS:
                return _http_deferred_response(
                    "add",
                    stream_id=stream_id,
                    source="http_header_lifecycle",
                    mode=MODE_HTTP_HEADER,
                )
        elif action == ACTION_DELETE:
            response = deprovisionStreamRedis(
                app.config["WDM_WL_OBJECT_NAME"], wl_d, original_json, parent_context
            )
            _close_lifecycle_trace_context(stream_id)
        elif action == ACTION_REPROVISION:
            response = _try_http_provision(
                "reprovision",
                lambda: reprovisionStreamRedis(
                    app.config["WDM_WL_OBJECT_NAME"],
                    wl_d,
                    original_json,
                    parent_context,
                ),
            )
            if response is PROVISION_DEFERRED_UNREADY_PODS:
                return _http_deferred_response(
                    "reprovision",
                    stream_id=stream_id,
                    source="http_header_lifecycle",
                    mode=MODE_HTTP_HEADER,
                )
        else:
            return _http_header_lifecycle_error(
                "unsupported lifecycle action", 500, action=action
            )

        return jsonify(
            {
                "status": "ok",
                "mode": MODE_HTTP_HEADER,
                "action": action,
                "stream_id": stream_id,
            }
        ), 200
    except MaxReplicaException as me:
        app.logger.error("Max replica exception %s", me)
        return _http_header_lifecycle_error(str(me), 500, stream_id=stream_id)
    except Exception as exc:
        app.logger.exception(
            "HTTP-header lifecycle failed action=%s stream_id=%s", action, stream_id
        )
        return _http_header_lifecycle_error(str(exc), 500, stream_id=stream_id)


@app.route("/get_wl_replica_data", methods=["GET"])
def get_wl_replica_data():

    engaged_pods_count = 0
    standby_pods_count = 0
    saturated_pods_count = 0
    pending_pods_count = 0
    replica_spec_data = {}
    replica_spec_data["wl_object"] = wl_object_name
    replica_spec_data["standby_pods_configured"] = app.config["WDM_STANDBY_POD_COUNT"]

    _sts_t0 = time.perf_counter()
    wlobj = curr_cluster.getStatefulSets()
    _req_elapsed = _wdm_http_request_elapsed_s()
    app.logger.debug(
        "get_wl_replica_data curr_cluster.getStatefulSets elapsed_s=%.6f wl=%s request_elapsed_s=%s",
        time.perf_counter() - _sts_t0,
        wl_object_name,
        "%.6f" % _req_elapsed if _req_elapsed is not None else "-",
    )
    if wlobj is not None and wlobj.status.replicas != 0:
        WLObj = curr_cluster.getWorkloadObjects()
        if WLObj is not None:
            podsInfo = curr_cluster.getPodIps(WLObj)
            replica_spec_data["total_replicas"] = len(podsInfo) if podsInfo else 0
            if podsInfo is not None:
                running_pods = list(filter(lambda x: x["phase"] == "Running", podsInfo))
                pending_pods = list(filter(lambda x: x["phase"] == "Pending", podsInfo))
                running_pods_count = len(running_pods)
                pending_pods_count = len(pending_pods)
                cfg._loadWorkLoadSpec()
                for podInfoItm in running_pods:
                    wl_spec_count = cfg.getSpecCount(
                            podInfoItm["podName"],
                        )
                    if wl_spec_count == app.config["WDM_WL_THRESHOLD"]:
                        saturated_pods_count += 1
                    elif wl_spec_count <  app.config["WDM_WL_THRESHOLD"] and wl_spec_count > 0:
                        engaged_pods_count += 1
                    elif wl_spec_count == 0:
                        standby_pods_count += 1
                
                replica_spec_data["running_pods"] = running_pods_count # pods in running state
                replica_spec_data["engaged_pods"] = engaged_pods_count # pods with workload < threshold
                replica_spec_data["standby_pods"] = standby_pods_count # pods with workload = 0
                replica_spec_data["saturated_pods"] = saturated_pods_count # pods with workload = threshold
                replica_spec_data["pending_pods"] = pending_pods_count # pods in pending state

    return jsonify(replica_spec_data)


@app.route("/pod_list", methods=["GET"])
def pod_list():
    """Return list of pods with stream IDs per pod: [{podName, phase, stream_ids: [...]}, ...]."""
    result = {"pods": []}
    event_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    id_field = app.config["WDM_WL_ID_FIELD"]
    _sts_t0 = time.perf_counter()
    app.logger.debug("pod_list start")
    wlobj = curr_cluster.getStatefulSets()
    _req_elapsed = _wdm_http_request_elapsed_s()
    app.logger.debug(
        "pod_list curr_cluster.getStatefulSets elapsed_s=%.6f wl=%s request_elapsed_s=%s",
        time.perf_counter() - _sts_t0,
        wl_object_name,
        "%.6f" % _req_elapsed if _req_elapsed is not None else "-",
    )
    if wlobj is not None:
        app.logger.debug("pod_list wlobj.status.replicas=%s", getattr(wlobj.status, "replicas", None))
    if wlobj is not None and wlobj.status.replicas != 0:
        app.logger.debug("pod_list wlobj is not None and wlobj.status.replicas != 0")
        WLObj = curr_cluster.getWorkloadObjects()
        app.logger.debug("pod_list WLObj: " + str(WLObj))
        if WLObj is not None:
            app.logger.debug("pod_list WLObj is not None")
            pods_info = curr_cluster.getPodIps(WLObj)
            if pods_info is not None:
                # One Redis read per request (not per pod) — avoids N× lock_try under write contention.
                app.logger.debug("pod_list cfg._loadWorkLoadSpec (once for all pods)")
                cfg._loadWorkLoadSpec()
                for p in pods_info:
                    pod_name = p.get("podName", "")
                    stream_ids = []
                    try:
                        app.logger.debug("pod_list try cfg.getworkLoadSpecs pod=%s", pod_name)
                        raw = cfg.getworkLoadSpecs(pod_name)
                        app.logger.debug("pod_list raw: " + str(raw))
                        if raw is not None and raw.strip():
                            curr_list = json.loads(raw)
                            if isinstance(curr_list, str):
                                curr_list = json.loads(curr_list)
                            if isinstance(curr_list, list):
                                for stream in curr_list:
                                    if isinstance(stream, dict) and event_field in stream:
                                        ev = stream[event_field]
                                        if isinstance(ev, dict) and id_field in ev:
                                            stream_ids.append(ev[id_field])
                                    elif isinstance(stream, dict) and id_field in stream:
                                        stream_ids.append(stream[id_field])
                    except (TypeError, ValueError, KeyError):
                        pass
                    result["pods"].append({
                        "podName": pod_name,
                        "podIp": p.get("podIp", ""),
                        "podDns": p.get("poddns", ""),
                        "phase": p.get("phase", "Unknown"),
                        "stream_ids": stream_ids,
                    })
            app.logger.debug(
                "pod_list completed pods=%s elapsed_s=%.6f",
                len(result.get("pods") or []),
                time.perf_counter() - _sts_t0,
            )
    return jsonify(result)


@app.route("/down_pods", methods=["GET"])
def down_pods():
    """Pods for the workload whose phase is not Running (e.g. Pending, Failed, Unknown)."""
    result = {"pods": []}
    event_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    id_field = app.config["WDM_WL_ID_FIELD"]
    wlobj = curr_cluster.getStatefulSets()
    if wlobj is not None and wlobj.status.replicas != 0:
        WLObj = curr_cluster.getWorkloadObjects()
        if WLObj is not None:
            pods_info = curr_cluster.getPodIps(WLObj)
            if pods_info is not None:
                cfg._loadWorkLoadSpec()
                for p in pods_info:
                    phase = (p.get("phase") or "Unknown").strip()
                    if phase.lower() == "running":
                        continue
                    pod_name = p.get("podName", "")
                    stream_ids = []
                    try:
                        raw = cfg.getworkLoadSpecs(pod_name)
                        if raw is not None and raw.strip():
                            curr_list = json.loads(raw)
                            if isinstance(curr_list, str):
                                curr_list = json.loads(curr_list)
                            if isinstance(curr_list, list):
                                for stream in curr_list:
                                    if isinstance(stream, dict) and event_field in stream:
                                        ev = stream[event_field]
                                        if isinstance(ev, dict) and id_field in ev:
                                            stream_ids.append(ev[id_field])
                                    elif isinstance(stream, dict) and id_field in stream:
                                        stream_ids.append(stream[id_field])
                    except (TypeError, ValueError, KeyError):
                        pass
                    result["pods"].append({
                        "podName": pod_name,
                        "podIp": p.get("podIp", ""),
                        "podDns": p.get("poddns", ""),
                        "phase": phase,
                        "stream_ids": stream_ids,
                    })
    return jsonify(result)


@app.route("/getpodInfo", methods=["GET"])
def getpodInfo():
    try:
        if request.args is None:
            return jsonify([])
        args = request.args
        id = args.get("id")
        if id is None:
            return jsonify([])
        r = __getPodDns__(id)
        app.logger.info("getpodInfo: " + str(r))
        Wlobj = curr_cluster.getStatefulSets()
        if Wlobj is not None and Wlobj.status.replicas != 0:
            WLObj = curr_cluster.getWorkloadObjects()
            if WLObj is not None:
                podsInfo = curr_cluster.getPodIps(WLObj)
                for podInfoItm in podsInfo:
                    if podInfoItm["podName"] == r["podname"] or podInfoItm["poddns"] == r["poddns"]:
                        podInfo = curr_cluster.disaggregate_podInfo(podInfoItm)
                        return jsonify(podInfo)
    except Exception as e:
        app.logger.error("Exception occured in getpodInfo: " + str(e))
        return jsonify([])
        
def listen_kill_server():
    if bus is not None:
        app.logger.debug("killed process")
        #signal.signal(signal.SIGTERM, bus.interrupted_process)
        #signal.signal(signal.SIGINT, bus.interrupted_process)
        #signal.signal(signal.SIGQUIT, bus.interrupted_process)
        #signal.signal(signal.SIGHUP, bus.interrupted_process)

def workload_spec_for_stream_id(stream_id):
    app.logger.info("originalJson[event][camera_id]: " + str(stream_id))
    workload_spec = cfg.getworkLoadSpecById(stream_id)
    app.logger.info("workload_spec: " + str(workload_spec))
    curr_spec = None
    if workload_spec is not None:
        for spec in workload_spec:
            if spec[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]] == stream_id:
                curr_spec = spec
                break
    return curr_spec

def remove_streams_with_same_id(k8swlob_name, data, originalJson, parent_context):
    app.logger.info("starting remove_streams_with_same_id")
    if app.config["WDM_FORWARD_MSG_TYPE"].lower() == "event_message":
        curr_spec = workload_spec_for_stream_id(originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]])
        if curr_spec is not None:
            if curr_spec[app.config["WDM_EVENT_OBJECT_FIELD"]] == originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]]:
                app.logger.info("camera is already in local cache and dict is same")
                return False
            app.logger.info("camera is already in local cache and url differs - first deleting, then readding")
            data_delete = data
            data_delete["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
            originalJson_delete = originalJson
            originalJson_delete[app.config["WDM_EVENT_OBJECT_FIELD"]]["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
            deprovisionStreamRedis(k8swlob_name, data_delete, originalJson_delete, parent_context)
            return True
    else:
        curr_spec = workload_spec_for_stream_id(data[app.config["WDM_WL_ID_FIELD"]])
        if curr_spec is not None:
            if curr_spec == originalJson:
                app.logger.info("camera is already in local cache and dict is same")
                return False
            app.logger.info("camera is already in local cache and url differs - first deleting, then readding")
            data_delete = data
            data_delete["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
            originalJson_delete = originalJson
            originalJson_delete[app.config["WDM_EVENT_OBJECT_FIELD"]]["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
            deprovisionStreamRedis(k8swlob_name, data_delete, originalJson_delete, parent_context)
            return True
    return True

def _merge_dicts(dict1, dict2, prefer_dict1=True):
    """Merge dictionaries with preference for values from a specific dict"""
    result = dict2.copy()
    for key, value in dict1.items():
        if key not in dict2 or prefer_dict1:
            result[key] = value
    return result

def reprovisionStreamRedis(k8swlob_name, data, originalJson, parent_context):
    # Clear old values in recent reprovisions
    keys_to_remove = []
    curr_time = datetime.datetime.now(datetime.timezone.utc)
    if len(reprovision_recent_removals) > 0:
        for key, value in reprovision_recent_removals.items():
            time_diff = curr_time - value
            if time_diff.total_seconds() > 10:
                    keys_to_remove.append(key)
        for key in keys_to_remove:
            reprovision_recent_removals.pop(key, None)
    # if the stream was recently reprovisioned, don't reprovisino again
    if originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]] in reprovision_recent_removals:
        app.logger.info("Recently reprovisioned stream " + str(originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]) + ". Will skip")
        return

    global last_restart
    time_diff = curr_time - last_restart
    if time_diff.total_seconds() < 10:
        app.logger.info("A container was recently restarted. Will skip reprovision")
        return

    # Check to make sure stream exists in local cache. If not, assume it was removed purposefully and that it should not be reprovisioned
    curr_spec = workload_spec_for_stream_id(originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]])
    if curr_spec is None:
        app.logger.info("Stream that is trying to be reprovisioned does not exist in local cache. Will assume it was previously removed on purpose or is otherwise invalid and will not reprovision.")
        return

    _wlobj_pre = curr_cluster.getStatefulSets()
    if (
        health_watcher is not None
        and _wlobj_pre is not None
        and _wlobj_pre.status.replicas != 0
        and health_watcher.healthy_count() == 0
    ):
        app.logger.info(
            "Reprovision deferred: no HTTP-healthy workload pod; will not "
            "deprovision until at least one pod is healthy (stream_id=%s)",
            originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][
                app.config["WDM_WL_ID_FIELD"]
            ],
        )
        return PROVISION_DEFERRED_UNREADY_PODS
    if _wlobj_pre is not None and _wlobj_pre.status.replicas != 0:
        _ready_pre = curr_cluster.getReadyReplicas()
        _desired_pre = int(_wlobj_pre.status.replicas)
        if _ready_pre < _desired_pre:
            app.logger.info(
                "Reprovision deferred: %d/%d replicas ready; will not deprovision "
                "until workload is healthy (stream_id=%s)",
                _ready_pre,
                _desired_pre,
                originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][
                    app.config["WDM_WL_ID_FIELD"]
                ],
            )
            return PROVISION_DEFERRED_UNREADY_PODS
    
    reprovision_recent_removals[originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]] = curr_time

    # Assume name/url is not included (or is wrong) in request, add it based on SDR local cache
    if curr_spec is not None:
        data = _merge_dicts(data, curr_spec[app.config["WDM_EVENT_OBJECT_FIELD"]])
        originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]] = _merge_dicts(originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]], curr_spec[app.config["WDM_EVENT_OBJECT_FIELD"]])
        
    app.logger.info(f"Reprovisioning stream for camera_id %s" % (data[app.config["WDM_WL_ID_FIELD"]]))

    # Deprovision stream
    data["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
    originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]]["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
    deprovisionStreamRedis(k8swlob_name, data, originalJson, parent_context)

    time.sleep(0.5) # TODO: wait until remove confirmed by DS?

    # Fetch info from VST for most up to date stream info - assume app.config["WDM_WL_ID_FIELD"] is the only correct value
    vst_streams = fetch_all_streams_from_vst()
    wlObj = app.config["WDM_WL_OBJECT_NAME"]
    evobj_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    
    # Provision stream (retry if placement is deferred while pods recover)
    stream_id = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][
        app.config["WDM_WL_ID_FIELD"]
    ]
    for origData in vst_streams:
        if origData[app.config["WDM_EVENT_OBJECT_FIELD"]][
            app.config["WDM_WL_ID_FIELD"]
        ] == stream_id:
            event_data = origData[evobj_field]
            max_wait_sec = int(
                app.config.get("WDM_API_WAIT_MAX_RETRIES_IN_SEC", 30)
            )
            deadline = time.time() + max_wait_sec
            while time.time() < deadline:
                result = provisionStreamRedis(
                    wlObj, event_data, origData, parent_context
                )
                if result is PROVISION_DEFERRED_UNREADY_PODS:
                    return PROVISION_DEFERRED_UNREADY_PODS
                if result is not False:
                    return
                app.logger.info(
                    "Reprovision: placement deferred (e.g. pods recovering); "
                    "retrying (stream_id=%s)",
                    stream_id,
                )
                time.sleep(1.0)
            app.logger.error(
                "Reprovision: provision still unsuccessful after %ss (stream_id=%s)",
                max_wait_sec,
                stream_id,
            )
            return

    return


def _renew_provision_lease_until_stopped(
    stream_id, lease_owner, lease_ttl, stop_event, lease_lost_event=None
):
    """Renew the provision lease until stopped, or signal abort if ownership is lost.

    ``lease_lost_event`` is set when renew is rejected (expired/stolen) or when
    renew keeps raising past the lease TTL. Without the TTL guard, Redis errors
    alone leave the worker unaware while another replica acquires the expired
    lease and places the same stream.
    """
    interval = max(1.0, float(lease_ttl) / 3.0)
    ttl = max(1.0, float(lease_ttl))
    # Acquired just before this heartbeat started; Redis TTL advances even when
    # renew calls raise and never reach EXPIRE.
    lease_deadline = time.monotonic() + ttl

    def _signal_lease_lost(reason):
        app.logger.error(
            "Lost provision lease while stream %s is still in flight (%s); "
            "signaling add-stream worker to abort",
            stream_id,
            reason,
        )
        if lease_lost_event is not None:
            lease_lost_event.set()

    while not stop_event.wait(interval):
        try:
            if not cfg.renewProvisionLease(stream_id, lease_owner, lease_ttl):
                _signal_lease_lost("renew rejected")
                return
            lease_deadline = time.monotonic() + ttl
        except Exception:
            app.logger.exception(
                "Failed to renew provision lease for stream %s", stream_id
            )
            if time.monotonic() >= lease_deadline:
                _signal_lease_lost("renew errors past lease TTL")
                return


def _finish_provision_lease(
    stream_id, lease_owner, stop_event, heartbeat_thread
):
    stop_event.set()
    heartbeat_thread.join(timeout=1.0)
    try:
        cfg.releaseProvisionLease(stream_id, lease_owner)
    except Exception:
        app.logger.exception(
            "Failed to release provision lease for stream %s", stream_id
        )


def _abort_provision_after_lease_loss(
    podInfoItm, config_data, wl_id,
    do_workload_spec_in_thread_first=False, rollback_workload_spec=False,
    added_on_pod=False,
):
    """Abort local commit after lease loss without clobbering a replacement attempt.

    Pod delete and workload-spec rollback are scoped only by pod + stream ID, so
    they must run only while this worker can re-acquire the provision lease. If a
    replacement replica already holds the lease, its reservation/add for the same
    stream must be left alone.
    """
    app.logger.error(
        "Aborting provision for stream %s on pod %s after losing provision lease "
        "(added_on_pod=%s)",
        wl_id,
        podInfoItm.get("podName"),
        added_on_pod,
    )
    cleanup_ttl = max(
        1, int(app.config.get("WDM_PROVISION_LEASE_SECONDS", 30))
    )
    cleanup_owner = str(uuid.uuid4())
    owns_cleanup = False
    try:
        owns_cleanup = cfg.tryAcquireProvisionLease(
            wl_id, cleanup_owner, cleanup_ttl
        )
    except Exception:
        app.logger.exception(
            "Failed to acquire cleanup lease for stream %s after lease loss",
            wl_id,
        )
    if not owns_cleanup:
        app.logger.warning(
            "Skipping pod/workload-spec rollback for stream %s after lease loss; "
            "another replica holds the provision lease",
            wl_id,
        )
        return
    try:
        if added_on_pod:
            try:
                pc.delete(podInfo=podInfoItm, configData=config_data)
            except Exception:
                app.logger.exception(
                    "Failed to undo pod add for stream %s after lease loss",
                    wl_id,
                )
        if do_workload_spec_in_thread_first or rollback_workload_spec:
            try:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            except Exception:
                app.logger.exception(
                    "Failed to rollback workload spec for stream %s after "
                    "lease loss",
                    wl_id,
                )
        try:
            redisMsging.message_err(
                wlobject=wl_object_name,
                podname=podInfoItm["podName"],
                id=wl_id,
                type="critical",
                status="add_stream_failed",
            )
        except Exception:
            app.logger.exception(
                "Failed to publish add_stream_failed after lease loss for "
                "stream %s",
                wl_id,
            )
    finally:
        try:
            cfg.releaseProvisionLease(wl_id, cleanup_owner)
        except Exception:
            app.logger.exception(
                "Failed to release cleanup lease for stream %s after lease loss",
                wl_id,
            )


def _run_provision_add_stream_to_pod_tracked(
    key_holder, podInfoItm, data, originalJson, config_data, wl_id, camera_id,
    otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False, workload_spec_reserved=False,
    rollback_workload_spec=False, lease_owner=None, lease_stop=None,
    lease_heartbeat=None, lease_lost_event=None,
):
    """Thread target that runs _run_provision_add_stream_to_pod and removes self from provision_add_threads."""
    try:
        _run_provision_add_stream_to_pod(
            podInfoItm, data, originalJson, config_data, wl_id, camera_id,
            otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
            workload_spec_reserved=workload_spec_reserved,
            rollback_workload_spec=rollback_workload_spec,
            lease_lost_event=lease_lost_event,
        )
    finally:
        with provision_add_threads_lock:
            provision_add_threads.pop(key_holder[0], None)
        _finish_provision_lease(
            wl_id, lease_owner, lease_stop, lease_heartbeat
        )


def _run_provision_add_stream_to_pod(
    podInfoItm, data, originalJson, config_data, wl_id, camera_id, otel_carrier,
    parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False, workload_spec_reserved=False,
    rollback_workload_spec=False, lease_lost_event=None,
):
    """Run _provision_add_stream_to_pod; used as thread target so exceptions are logged."""
    try:
        _provision_add_stream_to_pod(
            podInfoItm, data, originalJson, config_data, wl_id, camera_id,
            otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
            workload_spec_reserved=workload_spec_reserved,
            rollback_workload_spec=rollback_workload_spec,
            lease_lost_event=lease_lost_event,
        )
    except Exception:
        if do_workload_spec_in_thread_first or rollback_workload_spec:
            try:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            except Exception as e:
                app.logger.exception(
                    "Failed to rollback workload spec after async provision failure: %s", e
                )
        app.logger.exception(
            "Background provision add-stream failed for wl_id=%s pod=%s",
            wl_id, podInfoItm["podName"],
        )


def _wait_provision_add_threads_empty(timeout=60):
    """Block until no provision-add threads are active, or timeout (seconds).

    Deprovision/delete calls this so Redis workload-spec updates do not race with
    WDM_PROVISION_ASYNC background adds (same redis_lock). Long waits here delay
    cache/hash updates visible to the dashboard.
    """
    deadline = time.monotonic() + timeout
    start = time.monotonic()
    logged_wait = False
    last_progress_log = start
    while True:
        with provision_add_threads_lock:
            n = len(provision_add_threads)
            if n == 0:
                if logged_wait:
                    app.logger.info(
                        "Provision-add threads finished after %.1fs; proceeding with deprovision/cache update",
                        time.monotonic() - start,
                    )
                return
        if not logged_wait:
            app.logger.info(
                "Deprovision blocked: waiting for %s async provision-add thread(s) "
                "(WDM_PROVISION_ASYNC); timeout=%ss — cache/hash update runs after this (tune "
                "WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT or set WDM_PROVISION_ASYNC=false to avoid)",
                n,
                timeout,
            )
            logged_wait = True
        now = time.monotonic()
        if now - last_progress_log >= 5.0:
            app.logger.info(
                "Still waiting for provision-add threads: %s active, %.1fs elapsed (timeout in %.1fs)",
                n,
                now - start,
                max(0.0, deadline - now),
            )
            last_progress_log = now
        if now >= deadline:
            app.logger.warning(
                "Timeout waiting for provision-add threads (still %s active); "
                "proceeding with deprovision — Redis update may race with a slow add",
                n,
            )
            return
        time.sleep(0.2)


def _run_provision_add_stream(
    podInfoItm, data, originalJson, config_data, wl_id, camera_id, otel_carrier,
    parent_context, k8swlob_name, event_obj_field, span_data,
    workload_spec_reserved=False, rollback_workload_spec=False,
    lease_owner=None, lease_ttl=120,
):
    """Run add-stream provision synchronously or in a background thread per WDM_PROVISION_ASYNC."""
    lease_stop = Event()
    lease_lost = Event()
    lease_heartbeat = Thread(
        target=_renew_provision_lease_until_stopped,
        args=(wl_id, lease_owner, lease_ttl, lease_stop, lease_lost),
        daemon=True,
    )
    try:
        lease_heartbeat.start()
    except Exception:
        cfg.releaseProvisionLease(wl_id, lease_owner)
        if rollback_workload_spec:
            cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
        raise

    # Run the configurable health wait on this caller thread (bus/HTTP handler)
    # before spawning async work. Otherwise an unhealthy pod with
    # WDM_ADD_HEALTH_CHECK_TIMEOUT=-1 can park forever inside a worker thread
    # while the handler has already committed, or block a sync consumer.
    if (
        health_watcher is not None
        and _config_bool(app.config.get("WDM_WL_HEALTH_CHECK_WAIT_ENABLED"), True)
    ):
        try:
            wait_sec = float(app.config.get("WDM_ADD_HEALTH_CHECK_TIMEOUT", 60.0))
        except (TypeError, ValueError):
            wait_sec = 60.0
        wait_label = "forever" if wait_sec == -1 else f"{wait_sec}s"
        app.logger.info(
            "Pre-add health wait for pod %s (%s, timeout=%s)",
            podInfoItm.get("podName"),
            app.config.get("WDM_WL_HEALTH_CHECK_URL"),
            wait_label,
        )
        if not health_watcher.wait_until_healthy(podInfoItm, timeout_sec=wait_sec):
            _finish_provision_lease(
                wl_id, lease_owner, lease_stop, lease_heartbeat
            )
            if rollback_workload_spec:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            raise WorkloadUnhealthyError(
                "pod {} did not pass health check {} within {}s".format(
                    podInfoItm.get("podName"),
                    app.config.get("WDM_WL_HEALTH_CHECK_URL"),
                    wait_sec,
                )
            )

    if app.config.get("WDM_PROVISION_ASYNC"):
        # New placement calls reserve before reaching this function. Keep the
        # worker-side add for callers that have not already reserved a slot.
        key_holder = [None]
        try:
            t = Thread(
                target=_run_provision_add_stream_to_pod_tracked,
                args=(
                    key_holder,
                    podInfoItm, data, originalJson, config_data, wl_id, camera_id,
                    otel_carrier, parent_context, k8swlob_name,
                    event_obj_field, span_data, not workload_spec_reserved,
                    workload_spec_reserved, rollback_workload_spec, lease_owner,
                    lease_stop, lease_heartbeat, lease_lost,
                ),
                daemon=False,
            )
        except Exception:
            _finish_provision_lease(
                wl_id, lease_owner, lease_stop, lease_heartbeat
            )
            if rollback_workload_spec:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            raise
        with provision_add_threads_lock:
            key_holder[0] = id(t)
            provision_add_threads[key_holder[0]] = t
        try:
            t.start()
        except Exception:
            with provision_add_threads_lock:
                provision_add_threads.pop(key_holder[0], None)
            _finish_provision_lease(
                wl_id, lease_owner, lease_stop, lease_heartbeat
            )
            if rollback_workload_spec:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            raise
        app.logger.info(
            "Provision add-stream running in background for wl_id=%s camera_id=%s",
            wl_id, camera_id,
        )
    else:
        try:
            _provision_add_stream_to_pod(
                podInfoItm, data, originalJson, config_data, wl_id, camera_id,
                otel_carrier, parent_context, k8swlob_name, event_obj_field,
                span_data, do_workload_spec_in_thread_first=False,
                workload_spec_reserved=workload_spec_reserved,
                rollback_workload_spec=rollback_workload_spec,
                lease_lost_event=lease_lost,
            )
        except Exception:
            if rollback_workload_spec:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            raise
        finally:
            _finish_provision_lease(
                wl_id, lease_owner, lease_stop, lease_heartbeat
            )


def _provision_add_stream_to_pod(
    podInfoItm, data, originalJson, config_data, wl_id, camera_id, otel_carrier,
    parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False, workload_spec_reserved=False,
    rollback_workload_spec=False, lease_lost_event=None,
):
    """Perform add-stream RPC, update route mapping and workload spec, optionally call webhook."""
    # Legacy callers that did not reserve before dispatch still add from the worker.
    if do_workload_spec_in_thread_first:
        cfg.addWorkLoadSpec(podInfoItm["podName"], data, originalJson)
    if lease_lost_event is not None and lease_lost_event.is_set():
        _abort_provision_after_lease_loss(
            podInfoItm, config_data, wl_id,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
            rollback_workload_spec=rollback_workload_spec,
            added_on_pod=False,
        )
        return
    otel_span, current_ctx = tracing.create_child_span(
        "add", wl_id, podInfoItm, span_data, parent_context, app.config
    )
    try:
        resp = pc.add(
            podInfo=podInfoItm, configData=config_data, ctx_header=otel_carrier
        )
        tracing.propagate_context(
            camera_id, redisMsging, current_ctx, app.config["OTEL_SERVICE_NAME"]
        )
    except WorkloadUnhealthyError:
        app.logger.info(
            "Add deferred because pod %s failed health check before POST",
            podInfoItm.get("podName"),
        )
        otel_span.set_status(
            tracing.StatusCode.ERROR, description="Workload unhealthy"
        )
        if do_workload_spec_in_thread_first or rollback_workload_spec:
            try:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            except Exception as e:
                app.logger.exception(
                    "Failed to rollback workload spec after unhealthy add: %s", e
                )
        raise
    except Exception:
        app.logger.exception("Unexpected exception encountered while provisioning")
        otel_span.set_status(tracing.StatusCode.ERROR, description="Provisioning failed")
        raise
    finally:
        otel_span.end()

    if lease_lost_event is not None and lease_lost_event.is_set():
        # Add may have landed on the pod; undo before another replica re-places.
        _abort_provision_after_lease_loss(
            podInfoItm, config_data, wl_id,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
            rollback_workload_spec=rollback_workload_spec,
            added_on_pod=True,
        )
        return

    try:
        data["response"] = resp.json()
        originalJson[event_obj_field]["response"] = resp.json()
    except Exception:
        app.logger.info(
            "Failed to parse response as json - setting as empty string in cache"
        )
        data["response"] = ""
        originalJson[event_obj_field]["response"] = ""

    update_mapping = not (
        app.config["WDM_CHECK_STATUS"]
        and ((resp is not None and resp.status_code != 200) or resp is None)
    )
    if not update_mapping:
        if do_workload_spec_in_thread_first or rollback_workload_spec:
            try:
                cfg.deleteFromWorkLoadSpec(podInfoItm["podName"], wl_id)
            except Exception as e:
                app.logger.exception(
                    "Failed to rollback workload spec after add failed: %s", e
                )
        redisMsging.message_err(
            wlobject=wl_object_name,
            podname=podInfoItm["podName"],
            id=wl_id,
            type="critical",
            status="add_stream_failed",
        )
        app.logger.error(
            "add operation failed not updating the Route mapping"
        )
        return

    # Final lease check before committing route mapping / capacity accounting.
    if lease_lost_event is not None and lease_lost_event.is_set():
        _abort_provision_after_lease_loss(
            podInfoItm, config_data, wl_id,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
            rollback_workload_spec=rollback_workload_spec,
            added_on_pod=True,
        )
        return

    app.logger.info("add operation success updating the Route mapping")
    curr_cluster.updateRouteMapping(
        k8swlob_name, wl_id, podInfoItm, operation="add"
    )
    notify_xds_update()
    if not do_workload_spec_in_thread_first and not workload_spec_reserved:
        cfg.addWorkLoadSpec(podInfoItm["podName"], data, originalJson)
    if app.config["WDM_CALL_WL_WEBHOOK"]:
        try:
            app.logger.info(
                "calling webhook with payload: %s"
                % (config_data[event_obj_field],)
            )
            requests.post(
                app.config["WDM_WL_WEBHOOK_ENDPOINT"],
                json=config_data[event_obj_field],
            )
        except Exception:
            app.logger.exception(
                "Unexpected exception encountered while calling webhook"
            )


def provisionStreamRedis(k8swlob_name, data, originalJson, parent_context=None):
    cfg_key_id = app.config["WDM_WL_ID_FIELD"]
    cfg_ev_obj = app.config["WDM_EVENT_OBJECT_FIELD"]
    msg_type = app.config["WDM_FORWARD_MSG_TYPE"].lower()
    is_event = msg_type == "event_message"
    event_obj = originalJson[cfg_ev_obj] if is_event else data
    wl_id = data[cfg_key_id]
    lease_ttl = max(1, int(app.config["WDM_PROVISION_LEASE_SECONDS"]))

    app.logger.info("Provision Stream Redis")
    obj = redisMsging.getIdPodMapping(wl_id)
    wobj = cfg.getworkLoadSpecById(wl_id)
    if obj is not None and wobj is not None and len(wobj) > 0:
        app.logger.info("%s is already provisioned", wl_id)
        return
    if obj is None and wobj:
        cleanup_owner = str(uuid.uuid4())
        if not cfg.tryAcquireProvisionLease(
            wl_id, cleanup_owner, lease_ttl
        ):
            app.logger.info(
                "%s has a provision lease owned by another SDRC replica; "
                "deferring retry",
                wl_id,
            )
            return False
        try:
            app.logger.warning(
                "%s has workload-spec reservation(s) without a route mapping "
                "and no active provision lease; removing orphaned capacity "
                "before retry",
                wl_id,
            )
            for orphan in wobj:
                orphan_pod = orphan.get("pod_name")
                if orphan_pod is not None:
                    cfg.deleteFromWorkLoadSpec(orphan_pod, wl_id)
        finally:
            cfg.releaseProvisionLease(wl_id, cleanup_owner)
        wobj = None

    ignore_regex = app.config["WDM_WL_NAME_IGNORE_REGEX"]
    name_ignore_pattern = None
    if ignore_regex and ignore_regex.strip():
        try:
            name_ignore_pattern = re.compile(ignore_regex, re.IGNORECASE)
        except re.error:
            app.logger.error(
                "WDM_WL_NAME_IGNORE_REGEX set in config is not valid - will not filter any names"
            )
    if name_ignore_pattern is not None:
        swap_key = (
            cfg_key_id
            if app.config["WDM_DS_SWAP_ID_NAME"]
            else app.config["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]
        )
        curr_camera_name = (
            originalJson[cfg_ev_obj][swap_key] if is_event else data[swap_key]
        )
        if name_ignore_pattern.match(curr_camera_name):
            app.logger.info(
                "Camera name that was added matches WDM_WL_NAME_IGNORE_REGEX - will skip add"
            )
            return False

    if app.config["WDM_VALIDATE_BEFORE_ADD"]:
        required_fields = json.loads(app.config["WDM_JSON_EXPECTED_KEYS"])
        for field in required_fields:
            if field not in event_obj or (event_obj[field] or "").strip() == "":
                app.logger.info(
                    "%s in provided json is empty or missing - skipping add",
                    field,
                )
                return False

    if not remove_streams_with_same_id(k8swlob_name, data, originalJson, parent_context):
        app.logger.info(
            "Same stream id %s already in cache with different data - removing first, then adding (add may block while in-flight adds finish)",
            wl_id,
        )
        data["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
        originalJson[cfg_ev_obj]["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
        deprovisionStreamRedis(k8swlob_name, data, originalJson, parent_context, wait_add_threads_timeout=15)
        data["change"] = app.config["WDM_WL_CHANGE_ID_ADD"]
        originalJson[cfg_ev_obj]["change"] = app.config["WDM_WL_CHANGE_ID_ADD"]

    config_data = data if not is_event else originalJson
    camera_id = originalJson[cfg_ev_obj][cfg_key_id] if is_event else wl_id
    threshold = app.config["WDM_WL_THRESHOLD"]
    regex_info_key = app.config["WDM_STREAM_ADD_REGEX_INFO_KEY"]
    curr_allocations = (
        curr_cluster.get_current_allocation_pod_names()
        if app.config["WDM_ENABLE_REGEX_MAPPING"]
        else None
    )

    Wlobj = curr_cluster.getStatefulSets()
    if Wlobj is not None and Wlobj.status.replicas != 0:
        WLObj = curr_cluster.getWorkloadObjects()
        if WLObj is not None:
            podsInfo = curr_cluster.getPodIps(WLObj)
            if podsInfo is not None:
                provisionNewPod = True
                any_pod_down = False
                # Phase 1: collect eligible pods (not down, not full, regex match).
                # Orphan cleanup happens before this scan while holding the
                # distributed provision lease.
                eligible_pods = []  # (podInfoItm, spec_count, wl_spec)
                unhealthy_capacity_pods = []
                for podInfoItm in podsInfo:
                    pod_is_down = curr_cluster.ifPodDown(podInfoItm["podName"])
                    if pod_is_down:
                        any_pod_down = True
                        app.logger.info(
                            "Pod %s is down/unhealthy — preferring healthy peers",
                            podInfoItm["podName"],
                        )
                    if curr_allocations is not None:
                        if podInfoItm["podName"] not in curr_allocations:
                            continue
                        curr_encoded_name = (
                            originalJson[cfg_ev_obj][regex_info_key]
                            if is_event
                            else data[regex_info_key]
                        )
                        info_by_encoded_name = curr_cluster.get_pod_info_by_encoded_name(
                            curr_encoded_name
                        )["podName"]
                        if podInfoItm["podName"] not in info_by_encoded_name:
                            app.logger.info(
                                "podName (%s) not in encoded name list: %s",
                                podInfoItm["podName"],
                                info_by_encoded_name,
                            )
                            continue
                        app.logger.info(
                            "Regex matching enabled, found pod corresponding to podName"
                        )
                    wl_spec = cfg.getworkLoadSpec(
                        podInfoItm["podName"],
                        wl_id,
                    )
                    spec_count = cfg.getSpecCount(podInfoItm["podName"])
                    if spec_count >= threshold:
                        continue
                    entry = (podInfoItm, spec_count, wl_spec)
                    if pod_is_down:
                        unhealthy_capacity_pods.append(entry)
                    else:
                        eligible_pods.append(entry)

                # Prefer healthy pods. If none have capacity, use an unhealthy
                # pod with capacity; pc.add() waits for its health check before
                # sending the add request.
                if not eligible_pods and unhealthy_capacity_pods:
                    app.logger.info(
                        "No healthy workload pod for assignment; selecting "
                        "among %d capacity-eligible unhealthy pod(s); "
                        "add will wait for health (wl_id=%s)",
                        len(unhealthy_capacity_pods),
                        wl_id,
                    )
                    eligible_pods = unhealthy_capacity_pods

                # Phase 2: pod selection — strategy controlled by
                # WDM_WL_ASSIGNING_METHOD.
                #
                # "lru_round_robin" (default)
                #   Pick the pod with the fewest current streams; break ties
                #   with _pod_ordinal() → (ordinal, name) for a deterministic
                #   cycle.  Self-heals after uneven stream removal.
                #
                #   Trace — 5 indexed pods, threshold 4:
                #     s1  min=0 tied=[0,1,2,3,4] → pod-0  [1,0,0,0,0]
                #     s2  min=0 tied=[1,2,3,4]   → pod-1  [1,1,0,0,0]
                #     s3  min=0 tied=[2,3,4]     → pod-2  [1,1,1,0,0]
                #     ...  → 0→1→2→3→4→0→… (perfect round-robin)
                #
                # "sequential"
                #   Original first-fit: pick the first pod in the order
                #   returned by getPodIps() that is below threshold — no
                #   sorting, no load comparison.  Mirrors the pre-LRU inline
                #   loop+break.  Assignment order depends entirely on the API
                #   iteration order (typically StatefulSet ordinal order for
                #   K8s, insertion order for Docker).
                if eligible_pods:
                    assigning_method = app.config["WDM_WL_ASSIGNING_METHOD"]
                    candidates = list(eligible_pods)
                    selected_entry = None
                    lease_owner = str(uuid.uuid4())
                    if not cfg.tryAcquireProvisionLease(
                        wl_id, lease_owner, lease_ttl
                    ):
                        app.logger.info(
                            "%s provision lease is held by another SDRC "
                            "replica; deferring placement",
                            wl_id,
                        )
                        return False
                    try:
                        while candidates:
                            candidate = _select_pod_from_candidates(
                                candidates, assigning_method
                            )
                            candidate_pod, candidate_count, _ = candidate
                            reservation = cfg.tryReserveWorkLoadSpec(
                                candidate_pod["podName"], originalJson, threshold
                            )
                            if (
                                reservation is True
                                or reservation == "already_held"
                            ):
                                selected_entry = candidate
                                break
                            app.logger.info(
                                "Pod %s reached capacity after placement snapshot; "
                                "trying another candidate",
                                candidate_pod["podName"],
                            )
                            candidates.remove(candidate)
                    except Exception:
                        cfg.releaseProvisionLease(wl_id, lease_owner)
                        raise

                    if selected_entry is not None:
                        selected_pod, selected_count, selected_wl_spec = (
                            selected_entry
                        )
                        app.logger.info(
                            "stream_updates - %s adding_stream: %s "
                            "(%s selected pod=%s snapshot_streams=%d)",
                            wl_object_name, wl_id,
                            assigning_method, selected_pod["podName"],
                            selected_count,
                        )
                        otel_carrier = tracing.inject_context(parent_context)
                        if selected_wl_spec is not None:
                            app.logger.info(
                                "%s pod is already deployed",
                                selected_pod["podName"],
                            )
                        span_data = (
                            originalJson
                            if selected_wl_spec is None
                            else config_data
                        )
                        try:
                            _run_provision_add_stream(
                                selected_pod, data, originalJson, config_data,
                                wl_id, camera_id, otel_carrier, parent_context,
                                k8swlob_name, cfg_ev_obj, span_data,
                                workload_spec_reserved=True,
                                rollback_workload_spec=True,
                                lease_owner=lease_owner,
                                lease_ttl=lease_ttl,
                            )
                        except WorkloadUnhealthyError:
                            app.logger.info(
                                "Deferring add for wl_id=%s: selected pod %s "
                                "did not become healthy before /add within "
                                "WDM_ADD_HEALTH_CHECK_TIMEOUT",
                                wl_id,
                                selected_pod.get("podName"),
                            )
                            return PROVISION_DEFERRED_UNREADY_PODS
                        provisionNewPod = False
                    else:
                        cfg.releaseProvisionLease(wl_id, lease_owner)
                if provisionNewPod and app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                    if any_pod_down:
                        app.logger.info(
                            "Docker workload pod(s) not running; deferring add until "
                            "healthy (wl_id=%s) — message will not be committed yet",
                            wl_id,
                        )
                        return PROVISION_DEFERRED_UNREADY_PODS
                    app.logger.info("Max streams reached. New stream will not be provisioned.")
                    redisMsging.message_err(
                        wlobject=wl_object_name,
                        podname="None",
                        id=wl_id,
                        type="critical",
                        status="add_stream_failed"
                    )
                    # TODO: keep non provisioned streams in a separate list. If a provisioned stream is later removed, add one of the non provisioned streams to DS
                elif provisionNewPod and app.config["WDM_ENABLE_REGEX_MAPPING"]:
                    app.logger.info("No pod found for given stream matching regex and with available space. New stream will not be provisioned.")
                    redisMsging.message_err(
                        wlobject=wl_object_name,
                        podname="None",
                        id=wl_id,
                        type="critical",
                        status="add_stream_failed"
                    )
                elif provisionNewPod and (
                            (
                                int(app.config["WDM_MAX_REPLICAS"]) >
                                int(Wlobj.status.replicas)
                            )
                        ):
                    app.logger.info(
                        "Workload object replica {} {}".
                        format(Wlobj.status.replicas, provisionNewPod)
                    )
                    curr_cluster.scaleStatefulsetPods(
                        name=app.config["WDM_WL_OBJECT_NAME"],
                        replicas=Wlobj.status.replicas + 1,
                    )
                    _scaled = provisionStreamRedis(
                        k8swlob_name, data, originalJson, parent_context
                    )
                    if _scaled is PROVISION_DEFERRED_UNREADY_PODS:
                        return _scaled
                elif provisionNewPod:
                    app.logger.info(
                        "max replicas %d"
                        % (int(app.config["WDM_MAX_REPLICAS"]))
                    )
                    ready_replicas = curr_cluster.getReadyReplicas()
                    app.logger.info(
                        "ready replicas %d"
                        % (ready_replicas)
                    )
                    desired_replicas = int(Wlobj.status.replicas)
                    healthy_via_http = (
                        health_watcher.healthy_count()
                        if health_watcher is not None
                        else ready_replicas
                    )
                    if ready_replicas < desired_replicas or (
                        any_pod_down and healthy_via_http == 0
                    ):
                        app.logger.info(
                            "Replica count %d but only %d ready "
                            "(http-healthy=%d); deferring placement until "
                            "unhealthy pods recover (wl_id=%s)",
                            desired_replicas,
                            ready_replicas,
                            healthy_via_http,
                            wl_id,
                        )
                        return PROVISION_DEFERRED_UNREADY_PODS
                    app.logger.info(
                        f"no new pods to be provisioned \
                        {provisionNewPod} {Wlobj.status.replicas}"
                    )
                    app.logger.info(
                        "stream_updates - %s skipping_stream: %s",
                        wl_object_name, wl_id,
                    )
                    redisMsging.message_err(
                        wlobject=wl_object_name,
                        podname=podInfoItm["podName"],
                        id=wl_id,
                        type="critical",
                        status="add_stream_failed"
                    )
                    raise MaxReplicaException(
                        int(app.config["WDM_MAX_REPLICAS"])
                    )
                else:
                    app.logger.info("pod provisioned no new replica added")
    else:
        curr_cluster.scaleStatefulsetPods(
            name=app.config["WDM_WL_OBJECT_NAME"],
            replicas=1
        )
        _scaled = provisionStreamRedis(
            k8swlob_name, data, originalJson, parent_context
        )
        if _scaled is PROVISION_DEFERRED_UNREADY_PODS:
            return _scaled
    return True

def podConfigureRedis(k8swlob_name, data, originalJson):
    if app.config["WDM_FORWARD_MSG_TYPE"].lower() == \
            "event_message":
        config_event_json = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]]
    else:
        config_event_json = data

    if not should_handle_config_events():
        app.logger.info(
            "Skipping config event for workload %s because config handling is disabled",
            k8swlob_name,
        )
        return CONFIGURE_NOOP

    if not isinstance(config_event_json, dict):
        app.logger.error(
            "Config event payload must be a dictionary. Skipping allocation request: %s",
            config_event_json,
        )
        redisMsging.message_err(
            wlobject="system",
            podname="unknown",
            id="SDR",
            type="critical",
            status="Malformed config event payload",
        )
        return CONFIGURE_FAILED

    encoded_name_key = app.config["WDM_POD_ALLOCATION_ENCODED_NAME_KEY"]
    new_pod_encoded_name = config_event_json.get(encoded_name_key)
    if not new_pod_encoded_name:
        app.logger.error(
            "Config event missing required field %s. Skipping allocation request",
            encoded_name_key,
        )
        redisMsging.message_err(
            wlobject="system",
            podname="unknown",
            id="SDR",
            type="critical",
            status="Missing required config field " + encoded_name_key,
        )
        return CONFIGURE_FAILED

    new_pod_encoded_name_arr = new_pod_encoded_name.split(app.config["WDM_POD_ALLOCATION_REGEX_DELIMITER"])

    current_allocation_configs = curr_cluster.get_current_allocation_configs()
    if isinstance(current_allocation_configs, dict):
        pod_match_found = True if new_pod_encoded_name in current_allocation_configs else False
    else:
        app.logger.info("Did not get back dictionary of current allocations. Will assume none exist.")
        pod_match_found = False

    remove_config = _config_bool(config_event_json.get("remove_config"), False)
    if remove_config:
        if pod_match_found:
            app.logger.error("Removing given pod regex assignment. Assumed that all streams associated with this pod have already been accounted for elsewhere.")
            curr_cluster.delete_allocation_config({"encoded_matching_name": new_pod_encoded_name})
            return CONFIGURE_OK
        app.logger.info("No allocation config found for %s removal request; ignoring", new_pod_encoded_name)
        return CONFIGURE_NOOP

    # Make sure name is not already taken
    # TODO: override if it is?
    if pod_match_found:
        app.logger.error("Name being used for configuration already configured previously and deallocate not requested - ignoring new request")
        return CONFIGURE_NOOP

    # Find new pod with no association - if none found, return with error
    unallocated_pod_info = curr_cluster.find_unallocated_pod()
    if unallocated_pod_info is None:
        app.logger.error("No unallocated pods found to assign new configuration. Skipping allocation request")
        redisMsging.message_err(
            wlobject="system",
            podname=new_pod_encoded_name,
            id="SDR",
            type="critical",
            status="No unallocated pods found to assign new configuration"
        )
        return CONFIGURE_FAILED

    unallocated_pod_info["encoded_matching_name"] = new_pod_encoded_name
    unallocated_pod_info["encoded_matching_name_split"] = new_pod_encoded_name_arr

    app.logger.info("Sending config provision request")
    originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]] = config_event_json
    resp = pc.applyConfig(unallocated_pod_info, originalJson)

    if resp is None or resp.status_code != 200:
        failure_class = _configure_failure_class(resp)
        endpoint = _config_endpoint_for_pod(unallocated_pod_info)
        app.logger.error(
            "Configure request failed for workload=%s encoded_name=%s url=%s failure=%s response=%s",
            k8swlob_name,
            new_pod_encoded_name,
            endpoint,
            failure_class,
            resp,
        )
        redisMsging.message_err(
            wlobject="system",
            podname=new_pod_encoded_name,
            id="SDR",
            type="critical",
            status="Error while sending configure request to endpoint"
        )
        return _configure_failure_result()

    return_val = curr_cluster.update_current_allocation_configs(unallocated_pod_info)
    app.logger.info("allocated pod configuration: " + str(unallocated_pod_info))
    app.logger.info("allocated pod configuration write result: " + str(return_val))
    return CONFIGURE_OK

def deprovisionStreamRedis(k8swlob_name, data, originalJson, parent_context, wait_add_threads_timeout=None):
    if wait_add_threads_timeout is None:
        try:
            wait_add_threads_timeout = float(
                app.config.get("WDM_DEPROVISION_WAIT_ADD_THREADS_TIMEOUT", 60)
            )
        except (TypeError, ValueError):
            wait_add_threads_timeout = 60.0
    _wait_provision_add_threads_empty(timeout=wait_add_threads_timeout)
    Wlobj = curr_cluster.getStatefulSets()
    if Wlobj is not None and Wlobj.status.replicas != 0:
        WLObj = curr_cluster.getWorkloadObjects()
        if WLObj is not None:
            podsInfo = curr_cluster.getPodIps(WLObj)
            if podsInfo is not None:
                for podInfoItm in podsInfo:
                    podname = redisMsging.getIdPodMapping(
                            data[app.config["WDM_WL_ID_FIELD"]]
                        )
                    if podname is not None \
                            and podname == podInfoItm["podName"]:
                        wl_spec = cfg.getworkLoadSpec(
                            podInfoItm["podName"],
                            data[app.config["WDM_WL_ID_FIELD"]],
                        )
                        camera_id = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
                        if wl_spec is not None:
                            app.logger.info(
                                "stream_updates - %s removing_stream: %s" %
                                (
                                    wl_object_name,
                                    data[app.config["WDM_WL_ID_FIELD"]]
                                )
                            )

                            resp = None
                            if app.config["WDM_FORWARD_MSG_TYPE"].lower() \
                                    == "event_message":
                                video_name = data[app.config["WDM_WL_ID_FIELD"]]
                                otel_span, _ = tracing.create_child_span("remove", video_name, podInfoItm, originalJson, parent_context, app.config)
                                try:
                                    resp = pc.delete(
                                        podInfo=podInfoItm, configData=originalJson
                                    )
                                    tracing.delete_context_entry(camera_id, redisMsging, app.config["OTEL_SERVICE_NAME"])
                                except Exception as e:
                                    app.logger.exception("Unexpected exception encountered while deprovisioning")
                                    otel_span.set_status(tracing.StatusCode.ERROR, description="Deprovisioning failed")
                                    raise
                                finally:
                                    otel_span.end()
                                
                            else:
                                video_name = data[app.config["WDM_WL_ID_FIELD"]]
                                otel_span, _  = tracing.create_child_span("remove", video_name, podInfoItm, data, parent_context, app.config)
                                try:
                                    resp = pc.delete(
                                        podInfo=podInfoItm, configData=data
                                    )
                                    tracing.delete_context_entry(camera_id, redisMsging, app.config["OTEL_SERVICE_NAME"])
                                except Exception as e:
                                    app.logger.exception("Unexpected exception encountered while deprovisioning")
                                    otel_span.set_status(tracing.StatusCode.ERROR, description="Deprovisioning failed")
                                    raise
                                finally:
                                    otel_span.end()

                            updateMapping = True
                            if app.config["WDM_CHECK_STATUS"] and (
                                (
                                    resp is not None
                                    and resp.status_code != 200
                                )
                                or resp is None
                            ):
                                updateMapping = False
                                redisMsging.message_err(
                                    wlobject=wl_object_name,
                                    podname=podname,
                                    id=data[app.config["WDM_WL_ID_FIELD"]],
                                    type="agent",
                                    status="delete_stream_failed"
                                )
                                app.logger.error(
                                    """delete operation failed not
                                    updating the Route mapping"""
                                )

                            if updateMapping:
                                app.logger.info(
                                    """delete operation success
                                    updating the Route mapping"""
                                )
                                cfg.deleteFromWorkLoadSpec(
                                    podInfoItm["podName"],
                                    data[app.config["WDM_WL_ID_FIELD"]],
                                )
                                curr_cluster.updateRouteMapping(
                                    k8swlob_name,
                                    data[app.config["WDM_WL_ID_FIELD"]],
                                    podInfoItm,
                                    operation="delete",
                                )
                                notify_xds_update()
                        else:
                                app.logger.info("brute force delete !!! ")
                                video_name = data[app.config["WDM_WL_ID_FIELD"]]
                                if app.config["WDM_FORWARD_MSG_TYPE"].lower() \
                                    == "event_message":
                                    otel_span, _  = tracing.create_child_span("remove", video_name, podInfoItm, originalJson, parent_context, app.config)
                                    try:
                                        resp = pc.delete(
                                            podInfo=podInfoItm, configData=originalJson
                                        )
                                        tracing.delete_context_entry(camera_id, redisMsging, app.config["OTEL_SERVICE_NAME"])
                                    except Exception as e:
                                        app.logger.exception("Unexpected exception encountered while deprovisioning")
                                        otel_span.set_status(tracing.StatusCode.ERROR, description="Deprovisioning failed")
                                        raise
                                    finally:
                                        otel_span.end()

                                else:
                                    otel_span, _  = tracing.create_child_span("remove", video_name, podInfoItm, data, parent_context, app.config)
                                    try:
                                        resp = pc.delete(
                                            podInfo=podInfoItm, configData=data
                                        )
                                        tracing.delete_context_entry(camera_id, redisMsging, app.config["OTEL_SERVICE_NAME"])
                                    except Exception as e:
                                        app.logger.exception("Unexpected exception encountered while deprovisioning")
                                        otel_span.set_status(tracing.StatusCode.ERROR, description="Deprovisioning failed")
                                        raise
                                    finally:
                                        otel_span.end()
                                    
                                app.logger.info("Try force delete cache !!! ")
                                cfg.deleteFromWorkLoadSpec(
                                    podInfoItm["podName"],
                                    data[app.config["WDM_WL_ID_FIELD"]],
                                )
                                app.logger.info("remove from redis ")
                                curr_cluster.updateRouteMapping(
                                    k8swlob_name,
                                    data[app.config["WDM_WL_ID_FIELD"]],
                                    podInfoItm,
                                    operation="delete",
                                )
                                notify_xds_update()
    return

def readdStreams(podName, pod_spec):
    WLObj = curr_cluster.getWorkloadObjects()
    if WLObj is not None:
        podsInfo = curr_cluster.getPodIps(WLObj)
        if podsInfo is not None:
            json_spec = json.loads(json.loads(pod_spec))
            for podInfoItm in podsInfo:
                if podInfoItm['podName'] == podName:
                    for spec in json_spec:
                        data = redisMsging.getMessageValue(spec)
                        if data is None:
                            app.logger.error(
                                "readdStreams: cached spec on pod %s is missing the %s field; "
                                "skipping this stream, continuing with remaining streams",
                                podInfoItm.get("podName"),
                                app.config["WDM_EVENT_OBJECT_FIELD"],
                            )
                            continue
                        try:
                            resp = pc.add(
                                podInfo=podInfoItm, configData=spec
                                )
                        except Exception:
                            app.logger.exception(
                                "readd failed with an unexpected exception for stream %s on pod %s; "
                                "continuing with remaining streams",
                                data.get(app.config["WDM_WL_ID_FIELD"], "unknown"),
                                podInfoItm.get("podName"),
                            )
                            resp = None
                        app.logger.info(
                            "readd status %s",
                            resp.status_code if resp is not None else "no_response",
                        )
                        if app.config["WDM_CHECK_STATUS"] and (
                            resp is None
                            or resp.status_code != 200
                            ):
                            redisMsging.message_err(
                                wlobject=wl_object_name,
                                podname=podInfoItm["podName"],
                                id=data[app.config["WDM_WL_ID_FIELD"]],
                                type="critical",
                                status="reapply_stream_failed"
                            )
                            app.logger.error(
                                """add operation failed not updating
                                the Route mapping"""
                            )

                        else:
                            app.logger.info(
                                """add operation success updating
                                the Route mapping"""
                            )
                            curr_cluster.updateRouteMapping(
                                app.config["WDM_WL_OBJECT_NAME"],
                                data[app.config["WDM_WL_ID_FIELD"]],
                                podInfoItm,
                                operation="add",
                            )
                            notify_xds_update()

def __initPodState():

    Wlobj = curr_cluster.getStatefulSets()
    if Wlobj is None:
        app.logger.error(
            "Unable to locate a Statefulset %s" %
            (app.config["WDM_WL_OBJECT_NAME"])
        )

        cfg.eraseSpecContent()

        if redisMsging is not None:
            redisMsging.clearAllData()

        return False
    else:
        podCount = cfg.getpodsCount()
        if Wlobj.status.replicas != podCount:
            app.logger.warning("Replica count and cache pod spec out of sync ")
            # TO Do Replay data
            # curr_cluster.scaleStatefulsetPods(
            #    name=app.config["WDM_WL_OBJECT_NAME"],
            #    replicas=podCount
            # )
        else:
            app.logger.info(
                "pod counts %d match with replica count %d" %
                (Wlobj.status.replicas, podCount)
            )
    return True


def redisListener():
    if not is_message_bus_lifecycle_mode(app.config):
        app.logger.info(
            "Redis lifecycle listener disabled by WDM_LIFECYCLE_INGRESS_MODE=%s",
            app.config.get("WDM_LIFECYCLE_INGRESS_MODE"),
        )
        return True
    if __initPodState():
        app.logger.info ("Redis lifecycle listener starting")
        tr = Thread(target=redisGetStreamData)
        tr.start()
        return True
    return False


def statefulSetWatcher():
    if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
        return False
    
    tr = Thread(target=curr_cluster.watchAndUpdateActiveReplicaCount)
    tr.start()
    return True

def redisGetStreamData():
    global REDIS_IS_CONNECTED
    global REDIS_LISTENER_PAUSE
    # if app.config["WDM_MSG_BUS"].lower () != "redis":
    #    return
    while True:
        try:
            if redisMsging is None:
                return

            redis_connection = redisMsging.getRedisConnection()
            consumer = None

            try:
                consumer = Consumer(
                    redis_conn=redis_connection,
                    stream=app.config["WDM_REDIS_MSG_KEY"],
                    consumer_group=app.config["WDM_CONSUMER_GRP_ID"],
                    batch_size=10,
                    max_wait_time_ms=300,
                )
                REDIS_IS_CONNECTED = True
            except Exception as e:
                log_rate_limited(
                    app.logger,
                    logging.ERROR,
                    f"redis-consumer-connect:{type(e).__name__}",
                    "unexpected exception caught while processing Redis stream - %s",
                    repr(e),
                    interval_s=30.0,
                )
                REDIS_IS_CONNECTED = False
                continue

            while REDIS_LISTENER_PAUSE:
                time.sleep(0.05)

            start_ts = datetime.datetime.now(datetime.timezone.utc)

            app.logger.info(
                f"Waiting for Redis message %s %s"
                % (app.config["WDM_REDIS_MSG_KEY"], app.config["WDM_CONSUMER_GRP_ID"])
            )
            msg_field = app.config["WDM_WL_REDIS_MSG_FIELD"]
            while True:
                messages = consumer.get_items()
                for i, item in enumerate(messages):
                    message_key = redis_message_key(item.msgid)
                    outcome = EVENT_OK
                    err = None
                    jValue = None
                    wl_d = None
                    parent_context = None
                    try:
                        app.logger.info(item)
                        app.logger.info(item.content)
                        app.logger.info(item.content[msg_field])

                        sens = item.content[msg_field]
                        jValue = json.loads(sens)

                        # Swap camera_id and camera_name for the MMJ usecase
                        if app.config["WDM_DS_SWAP_ID_NAME"]:
                            if app.config["WDM_WL_ID_FIELD"] in jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]:
                                app.logger.info("swapping")
                                tmp_val = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
                                jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]] = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]]
                                jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]] = tmp_val
                            else:
                                app.logger.info("camera_id not found in event - not swapping")

                        wl_d = redisMsging.getMessageValue(jValue)
                        global id_ctx_mapping
                        if wl_d is not None:
                            if app.config["WDM_EVENT_OBJECT_FIELD"] in jValue and jValue[app.config["WDM_EVENT_OBJECT_FIELD"]] is not None and app.config["WDM_WL_ID_FIELD"] in jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]:
                                camera_id = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
                                if camera_id not in id_ctx_mapping:
                                    otel_parent_span, parent_context = tracing.create_parent_span(camera_id, "redisGetStreamData()", redisMsging)
                                    id_ctx_mapping[camera_id] = {
                                        "context": parent_context,
                                        "span": otel_parent_span
                                    }
                                else:
                                    parent_context = id_ctx_mapping[camera_id]["context"]
                                    otel_parent_span = id_ctx_mapping[camera_id]["span"]

                        if (wl_d is not None) and (change_field in wl_d) and (
                            wl_d[change_field].lower() == change_id_add
                        ):
                            app.logger.info("provision stream")
                            _prv = provisionStreamRedis(
                                app.config["WDM_WL_OBJECT_NAME"],
                                wl_d, jValue, parent_context
                            )
                            if _prv is PROVISION_DEFERRED_UNREADY_PODS:
                                outcome = EVENT_RETRYABLE
                        elif (wl_d is not None) and (change_field in wl_d) and (
                            wl_d[change_field].lower() == change_id_reprovision
                        ):
                            app.logger.info("reprovision stream")
                            _rpv = reprovisionStreamRedis(
                                app.config["WDM_WL_OBJECT_NAME"],
                                wl_d, jValue, parent_context
                            )
                            if _rpv is PROVISION_DEFERRED_UNREADY_PODS:
                                outcome = EVENT_RETRYABLE
                        elif (wl_d is not None) and (change_field in wl_d) and (
                            wl_d[change_field].lower() == change_id_del
                        ):
                            app.logger.info("deprovision stream")
                            deprovisionStreamRedis(
                                app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue, parent_context
                            )
                            id_ctx_mapping[jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]]["span"].end()
                            id_ctx_mapping.pop(jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]])
                        elif (wl_d is not None) and (change_field in wl_d) and (
                            wl_d[change_field].lower() == change_id_pod_configure
                        ):
                            app.logger.info("configure stream")
                            configure_result = podConfigureRedis(
                                app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue
                            )
                            if configure_result == CONFIGURE_DEFERRED:
                                outcome = EVENT_RETRYABLE
                            elif configure_result == CONFIGURE_FAILED:
                                outcome = EVENT_TERMINAL
                                err = configure_result
                            elif configure_result == CONFIGURE_NOOP:
                                outcome = EVENT_NOOP
                        else:
                            app.logger.info("wl_d is None. wl_d: " + str(wl_d))
                            outcome = EVENT_NOOP
                    except MaxReplicaException as me:
                        err = me
                        app.logger.error(f"Max replica exception {me}")
                        if jValue is not None:
                            _end_error_span_for_event(jValue)
                        # Preserve legacy eviction behavior: drop from queue when configured.
                        outcome = EVENT_TERMINAL if evic_q_on_no_capacity else EVENT_RETRYABLE
                    except Exception as exc:
                        err = exc
                        outcome = classify_exception(exc)
                        app.logger.exception(
                            "exception occurred while processing Redis stream event"
                        )
                        if jValue is not None:
                            _end_error_span_for_event(jValue)

                    should_commit, final_outcome, attempt = _apply_bus_commit_decision(
                        bus_name="redis",
                        message_key=message_key,
                        outcome=outcome,
                        error=err,
                        original_json=jValue,
                        wl_d=wl_d,
                    )
                    if should_commit:
                        app.logger.info("Committing message id %s", item.msgid)
                        try:
                            consumer.commit(item_id=item.msgid)
                        except Exception as commit_exc:
                            app.logger.error(
                                "Failed to commit Redis message id %s: %s",
                                item.msgid,
                                commit_exc,
                            )
                    else:
                        app.logger.info(
                            "Not committing message id %s (retryable attempt %s/%s)",
                            item.msgid,
                            attempt,
                            app.config.get("WDM_EVENT_RETRY_LIMIT", 20),
                        )
                        time.sleep(1.0)

                time.sleep(0.05)

        except Exception as e:
            log_rate_limited(
                app.logger,
                logging.ERROR,
                f"redis-consumer-loop:{type(e).__name__}",
                "unexpected exception caught while processing Redis stream - %s",
                repr(e),
                interval_s=30.0,
            )


if bus is not None:

    @bus.handle(topic)
    def kafka_topic_handler(msg):
        message_key = kafka_message_key(msg)
        outcome = EVENT_OK
        err = None
        wl_d = None
        originalJson = None
        parent_context = None
        try:
            message_value = kfk.getMessageValue(bus, msg)
            if message_value is None:
                app.logger.info("Kafka message ignored by key/value filter")
                outcome = EVENT_NOOP
            else:
                wl_d, originalJson = message_value
                try:
                    global id_ctx_mapping
                    if wl_d is not None:
                        camera_id = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
                        if camera_id not in id_ctx_mapping:
                            otel_parent_span, parent_context = tracing.create_parent_span(camera_id, "kafka_topic_handler()", redisMsging)
                            id_ctx_mapping[camera_id] = {
                                "context": parent_context,
                                "span": otel_parent_span
                            }
                        else:
                            parent_context = id_ctx_mapping[camera_id]["context"]
                            otel_parent_span = id_ctx_mapping[camera_id]["span"]
                    if wl_d is not None and (
                        wl_d[change_field].lower() == change_id_add
                    ):
                        app.logger.info("provision stream")
                        _prv = provisionStreamRedis(
                            app.config["WDM_WL_OBJECT_NAME"],
                            wl_d, originalJson, parent_context
                        )
                        if _prv is PROVISION_DEFERRED_UNREADY_PODS:
                            outcome = EVENT_RETRYABLE
                    elif wl_d is not None and change_field in wl_d and (
                        wl_d[change_field].lower() == change_id_del
                    ):
                        app.logger.info("deprovision stream")
                        deprovisionStreamRedis(
                            app.config["WDM_WL_OBJECT_NAME"],
                            wl_d, originalJson, parent_context
                        )
                        id_ctx_mapping[originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]]["span"].end()
                        id_ctx_mapping.pop(originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]])
                    elif (wl_d is not None) and (change_field in wl_d) and (
                        wl_d[change_field].lower() == change_id_pod_configure
                    ):
                        app.logger.info("configure stream")
                        configure_result = podConfigureRedis(
                            app.config["WDM_WL_OBJECT_NAME"], wl_d, originalJson
                        )
                        if configure_result == CONFIGURE_DEFERRED:
                            outcome = EVENT_RETRYABLE
                        elif configure_result == CONFIGURE_FAILED:
                            outcome = EVENT_TERMINAL
                            err = configure_result
                        elif configure_result == CONFIGURE_NOOP:
                            outcome = EVENT_NOOP
                    else:
                        app.logger.info("wl_d is None ")
                        outcome = EVENT_NOOP
                except MaxReplicaException as me:
                    err = me
                    app.logger.error(f"Max replica exception Kafka {me}")
                    if originalJson is not None:
                        _end_error_span_for_event(originalJson)
                    outcome = EVENT_TERMINAL if evic_q_on_no_capacity else EVENT_RETRYABLE
                except Exception as exc:
                    err = exc
                    outcome = classify_exception(exc)
                    app.logger.exception("exception occured")
                    if originalJson is not None:
                        _end_error_span_for_event(originalJson)
        except Exception as e:
            err = e
            outcome = classify_exception(e)
            app.logger.error(f"Exception occured: {e}")
            app.logger.error("An exception occured in the main loop")

        should_commit, final_outcome, attempt = _apply_bus_commit_decision(
            bus_name="kafka",
            message_key=message_key,
            outcome=outcome,
            error=err,
            original_json=originalJson,
            wl_d=wl_d,
        )
        try:
            if should_commit:
                app.logger.info("commiting consumer message")
                bus.consumer.commit()
            else:
                # flask-kafka commits after this handler returns. Seek back so
                # that commit (or a later success) cannot skip this offset.
                if kafka_rewind_to_message(bus.consumer, msg):
                    app.logger.info(
                        "rewound Kafka consumer after retryable failure "
                        "(attempt %s/%s, outcome=%s); deferring commit skip "
                        "via seek",
                        attempt,
                        app.config.get("WDM_EVENT_RETRY_LIMIT", 20),
                        final_outcome,
                    )
                elif kafka_park_offset_on_next_commit(bus.consumer, msg):
                    # seek failed: force flask-kafka's post-handler commit to
                    # park at this offset instead of acknowledging past it.
                    app.logger.error(
                        "Kafka seek failed after retryable failure; installed "
                        "one-shot park commit for offset "
                        "(attempt %s/%s, outcome=%s)",
                        attempt,
                        app.config.get("WDM_EVENT_RETRY_LIMIT", 20),
                        final_outcome,
                    )
                else:
                    # Last resort: abort before flask-kafka can commit past us.
                    app.logger.error(
                        "Kafka seek and park-commit install both failed after "
                        "retryable failure (attempt %s/%s, outcome=%s); "
                        "raising to prevent offset skip",
                        attempt,
                        app.config.get("WDM_EVENT_RETRY_LIMIT", 20),
                        final_outcome,
                    )
                    raise RuntimeError(
                        "kafka retryable failure: cannot rewind or park offset"
                    )
                time.sleep(1.0)
        except Exception as e:
            app.logger.error(f"Exception: {e}")
            # Do not swallow rewind/park failures: flask-kafka commits after return.
            if not should_commit:
                raise

        app.logger.info("waiting for next message")



def preloadData(originalJson):
    wlObj = app.config["WDM_WL_OBJECT_NAME"]
    evobj_field = app.config["WDM_EVENT_OBJECT_FIELD"]
    for origData in originalJson:
        event_data = origData[evobj_field]
        parent_span, parent_context = tracing.create_parent_span(origData[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]], "preloadData(originalJson)", redisMsging)
        provisionStreamRedis(wlObj, event_data, origData, parent_context)
        parent_span.end()
    return

def vst_stream_is_valid(stream_name):
    # state must be online and errorCode must be "NoError"
    vst_status_endpoint = app.config["VST_STATUS_ENDPOINT"]
    resp = requests.get(vst_status_endpoint)
    if not resp.status_code == 200:
        app.logger.info("Did not get return code 200 from VST sensor status endpoint - retrying")
        return False

    try:
        json_vals = resp.json()
    except Exception as e:
        app.logger.info("Couldn't parse VST endpoint response, will retry. Exception was - " + repr(e))
        return False
    
    for key, value in json_vals.items():
        if "name" not in value:
            continue
        if value["name"] != stream_name:
            continue
        
        is_valid = True
        if "errorCode" in value:
            if value["errorCode"] != "NoError":
                is_valid = False
        else:
            is_valid = False
            
        if "state" in value:
            if value["state"] != "online":
                is_valid = False
        else:
            is_valid = False
            
            
        return is_valid
    return False

def fetch_all_streams_from_vst():
    vst_streams_endpoint = app.config["VST_STREAMS_ENDPOINT"]

    api_up = False
    start_time = time.time()
    while not api_up:
        try:
            app.logger.info("testing VST streams endpoint to see if it's ready")
            resp = requests.get(vst_streams_endpoint)
            api_up = True
        except Exception as e:
            app.logger.debug(
                "VST streams endpoint not ready yet (expected during startup): %s",
                repr(e),
            )
            time.sleep(0.05)

        if int(time.time() - start_time)  > app.config["WDM_API_WAIT_MAX_RETRIES_IN_SEC"]:
            app.logger.error("VST endpoint took too long to respond - skipping VST preload")
            return []

    resp = requests.get(vst_streams_endpoint)

    if not resp.status_code == 200:
        app.logger.info("Did not get return code 200 from VST endpoint - retrying")
        return None

    try:
        json_vals = resp.json()
    except Exception as e:
        app.logger.info("Couldn't parse VST endpoint response, will retry. Exception was - " + repr(e))
        return None

    vst_streams = []
    for stream in json_vals:
        for key, value in stream.items():
            if len(value) < 1:
                continue
            curr_data = value[0]
            if curr_data["isMain"]:
                curr_dict = {}
                curr_dict["source"] = "preload"
                curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]] = {}

                if app.config["WDM_DS_SWAP_ID_NAME"]:
                    curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]] = curr_data["name"]
                    curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]] = curr_data["streamId"]
                else:
                    curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]] = curr_data["streamId"]
                    curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_SWAP_KEY_SECONDARY_FIELD"]] = curr_data["name"]
                
                curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]]["camera_url"] = curr_data["url"]
                curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]]["change"] = app.config["WDM_WL_CHANGE_ID_ADD"]
                curr_dict[app.config["WDM_EVENT_OBJECT_FIELD"]]["metadata"] = curr_data["metadata"]
                
                if app.config["WDM_CHECK_VST_STREAM_IS_ONLINE"]:
                    if not vst_stream_is_valid(curr_data["name"]):
                        app.logger.info(f"Stream {curr_data['name']} is not online - skipping add")
                        continue
                    else:
                        app.logger.info(f"Stream {curr_data['name']} is online - adding")
                
                vst_streams.append(curr_dict)

    return vst_streams


def preLoad():
    global REDIS_IS_CONNECTED
    global REDIS_LISTENER_PAUSE
    REDIS_LISTENER_PAUSE = True
    if app.config["WDM_PRELOAD_DELAY_FOR_REDIS"]:
        while not REDIS_IS_CONNECTED:
            app.logger.info("waiting for redis to connect before continuing...")
            time.sleep(0.05)

    if app.config["WDM_PRELOAD_DELAY_FOR_DS_API"]:
        api_up = False
        start_time = time.time()
        endpoint = ""
        endpoint_set = False
        while not api_up:
            try:
                if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                    app.logger.info("testing workload health check endpoint to see if it's ready")
                    if not endpoint_set:
                        with open(app.config["WDM_CLUSTER_CONFIG_FILE"]) as config_file:
                            config_data = json.load(config_file)
                            for key, value in config_data.items():
                                endpoint = "http://" + value["provisioning_address"] + app.config["WDM_WL_HEALTH_CHECK_URL"]
                                endpoint_set = True
                                break

                    r = requests.get(endpoint)
                    api_up = True
                # TODO: what if we're using k8s?
            except Exception as e:
                app.logger.debug(
                    "workload health check endpoint not ready yet (expected during startup): %s",
                    repr(e),
                )
                time.sleep(1)
                
            if int(time.time() - start_time)  > app.config["WDM_API_WAIT_MAX_RETRIES_IN_SEC"]:
                app.logger.error("DS endpoint took too long to respond")
                continue

    preloadFile = app.config["WDM_PRELOAD_WORKLOAD"]
    if not os.path.isfile(preloadFile):
        app.logger.info("preload file (" + str(preloadFile) + ") does not exist - skipping loading from it")
        preloadFile = None
    
    if preloadFile is not None:
        try:
            with open(preloadFile, "r") as f:
                jstr = f.read()
                try:
                    preloadData(json.loads(jstr))
                except Exception:
                    app.logger.exception(
                        f"Unable to load the pre load data {preloadFile}"
                    )
        except FileNotFoundError as fnf:
            app.logger.debug(
                fnf.strerror
            )

    if app.config["WDM_INITIALIZE_FROM_VST"]:
        vst_streams = None
        # TODO: check for VST up message on redis instead?
        while vst_streams == None:
            vst_streams = fetch_all_streams_from_vst()
        preloadData(vst_streams)
    REDIS_LISTENER_PAUSE = False

def removeAllStreams():
    global id_ctx_mapping
    pod_names = cfg.getpods()
    all_returns = []
    for pod in pod_names:
        try:
            pod_specs = cfg.getworkLoadSpecs(pod)
            if isinstance(pod_specs, str):
                pod_specs = json.loads(pod_specs)
            all_returns.append(pod_specs)
        except Exception:
            app.logger.exception("Unable to load cached streams for pod %s", pod)
    app.logger.info("all_cache_streams: " + str(all_returns))

    for pipeline in all_returns:
        try:
            curr_pipeline = json.loads(pipeline) if isinstance(pipeline, str) else pipeline
        except Exception:
            app.logger.exception("Unable to parse cached stream pipeline: %s", pipeline)
            continue
        if curr_pipeline is None:
            continue
        for curr_stream in curr_pipeline:
            try:
                app.logger.info("curr_stream being removed: " + str(curr_stream))
                event_obj = curr_stream[app.config["WDM_EVENT_OBJECT_FIELD"]]
                camera_id = event_obj[app.config["WDM_WL_ID_FIELD"]]
                parent_context = id_ctx_mapping.get(camera_id, {}).get("context")
                event_obj["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
                deprovisionStreamRedis(
                    app.config["WDM_WL_OBJECT_NAME"],
                    event_obj,
                    curr_stream,
                    parent_context,
                )
                time.sleep(0.1) # TODO: added since DS endpoints stop working/freezes when sending many remove requests at once. find a proper solution to this
            except Exception:
                app.logger.exception(
                    "Something went wrong while trying to remove stream: %s",
                    curr_stream,
                )

    return

def podWatch():
    global last_restart

    # For now assume if a pod goes down to remove all streams from all "pods" defined in docker_cluster_config.json if type==docker
    app.logger.info ("Starting Pod Watcher and send message if Initiator or certain pods go down")
    while True:
        try:
            for result in curr_cluster.watchPodState():
                if len(result) == 3:
                    e, p, g = result
                    old_ip, new_ip = None, None
                else:
                    e, p, g, old_ip, new_ip = result
                if e:
                    app.logger.info(f"Pod {p} is down wlobj name {g}")
                    if g.startswith(
                            initiatorWLObjname+"-"
                    ):
                        # TODO: using docker the current method may change the location of streams (ie. from pipeline 1 to 2) on a container restart - is this fine?
                        if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                            for key in curr_cluster.get_podname_keys():
                                redisMsging.message_down(
                                    wlobject=initiatorWLObjname,
                                    podname=key,
                                    type="critical"
                                )
                        else:
                            redisMsging.message_down(
                                wlobject=initiatorWLObjname,
                                podname=p,
                                type="critical"
                            )
                        app.logger.info(
                            f"Reset cache {initiatorWLObjname} went down"
                        )
                        
                        if app.config["WDM_RESET_ON_INITIATOR_CRASH"]:
                            reset()
                        last_restart = datetime.datetime.now(datetime.timezone.utc)
                    else:
                        if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                            if any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"]):
                                for key in curr_cluster.get_podname_keys():
                                    redisMsging.message_down(
                                        wlobject=wl_object_name,
                                        podname=key,
                                        type="critical"
                                    )
                                # send reprovision message to controller
                                streams_spec = cfg.getworkLoadSpecs(p)
                                if streams_spec and app.config["WDM_CONTROLLER_REPROVISION"]:
                                    reprovision_spec = json.loads(json.loads(streams_spec))
                                    app.logger.info("streams_spec: " + str(reprovision_spec))
                                    redisMsging.message_down(
                                        payload=reprovision_spec,
                                        wlobject=wl_object_name,
                                        podname=p,
                                        type="reprovision"
                                    )
                                    cfg.deleteWLObj(p)
                            elif any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"]):
                                redisMsging.message_down(
                                        wlobject=wl_object_name,
                                        podname=p,
                                        type="critical"
                                    )
                                # send reprovision message to controller
                                streams_spec = cfg.getworkLoadSpecs(p)
                                if streams_spec and app.config["WDM_CONTROLLER_REPROVISION"]:
                                    reprovision_spec = json.loads(json.loads(streams_spec))
                                    app.logger.info("streams_spec: " + str(reprovision_spec))
                                    print("streams_spec: " + str(reprovision_spec))
                                    redisMsging.message_down(
                                        payload=reprovision_spec,
                                        wlobject=wl_object_name,
                                        podname=p,
                                        type="reprovision"
                                    )
                                    cfg.deleteWLObj(p)
                            last_restart = datetime.datetime.now(datetime.timezone.utc)
                            if app.config["WDM_REAPPLY_ON_WL_RESTART"] == "false":
                                removeAllStreams()
                        else:
                            redisMsging.message_down(
                                wlobject=wl_object_name,
                                podname=p,
                                type="critical"
                            )
                            if app.config["WDM_RESET_ON_WLOBJ_CRASH"]: 
                                resetWorkLoadPod(p)
                            # send reprovision message to controller
                            streams_spec = cfg.getworkLoadSpecs(p)
                            print("streams_spec: " + str(streams_spec))
                            if streams_spec and app.config["WDM_CONTROLLER_REPROVISION"]:
                                reprovision_spec = json.loads(json.loads(streams_spec))
                                app.logger.info("streams_spec: " + str(reprovision_spec))
                                redisMsging.message_down(
                                    payload=reprovision_spec,
                                    wlobject=wl_object_name,
                                    podname=p,
                                    type="reprovision"
                                )
                                cfg.deleteWLObj(p)
                else:
                    app.logger.info(f"Pod {p} has recovered")
                    if g.startswith(
                            initiatorWLObjname+"-"
                    ):
                        if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                            if any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"]):
                                for key in curr_cluster.get_podname_keys():
                                    redisMsging.message_down(
                                        wlobject=wl_object_name,
                                        podname=key,
                                        type="critical"
                                    )
                            elif any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"]):
                                redisMsging.message_down(
                                        wlobject=wl_object_name,
                                        podname=p,
                                        type="critical"
                                    )
                        else:
                            if any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"]):
                                for key in curr_cluster.get_podname_keys():
                                    redisMsging.message_up(
                                        wlobject=initiatorWLObjname,
                                        podname=key,
                                        type="info"
                                    )
                            elif any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"]):
                                redisMsging.message_up(
                                    wlobject=initiatorWLObjname,
                                    podname=p,
                                    type="info"
                                )
                    else:
                        if app.config["WDM_REAPPLY_ON_WL_RESTART"]:
                            streams_spec = None
                            if app.config["WDM_CLUSTER_TYPE"].lower() == "k8s-headless":
                                old_ip = old_ip.replace('.', '-')
                                new_ip = new_ip.replace('.', '-')
                                streams_spec = cfg.getworkLoadSpecs(old_ip)
                                app.logger.info(f"old_ip: {old_ip}")
                                app.logger.info(f"new_ip: {new_ip}")
                                if streams_spec:
                                    app.logger.info("readding streams after recovered pod for k8s-headless")
                                    readdStreams(new_ip, streams_spec)
                            else:
                                streams_spec = cfg.getworkLoadSpecs(p)
                                if streams_spec:
                                    app.logger.info("readding streams after recovered pod for k8s")
                                    readdStreams(p, streams_spec)

                        if any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_KEY_DOWN_NAMES"]):
                            for key in curr_cluster.get_podname_keys():
                                redisMsging.message_up(
                                    wlobject=wl_object_name,
                                    podname=key,
                                    type="info"
                                )
                        elif any(pname.lower() in p.lower() for pname in app.config["WDM_DOCKER_CLUSTER_POD_DOWN_NAMES"]):
                            redisMsging.message_up(
                                wlobject=wl_object_name,
                                podname=p,
                                type="info"
                            )

                    if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                        preLoad()
                        last_restart = datetime.datetime.now(datetime.timezone.utc)
            
            if app.config["WDM_CLUSTER_TYPE"].lower() == "docker":
                time.sleep(app.config["WDM_POD_WATCH_DOCKER_DELAY"])
            
        except Exception:
            app.logger.exception(
                "pod watch exception trying to recover"
            )


def PodErrorWatcher():
    tr = Thread(target=podWatch)
    tr.start()
    return True


def WorkloadHealthCheckWatcher():
    """Start background HTTP health polling when health-check mode is enabled."""
    if health_watcher is None:
        app.logger.info(
            "WDM_WL_HEALTH_CHECK_WAIT_ENABLED=false; not starting HTTP health "
            "watcher (PodErrorWatcher uses legacy cluster/container state)"
        )
        return False
    app.logger.info(
        "Starting workload health check watcher (url=%s interval=%ss)",
        app.config.get("WDM_WL_HEALTH_CHECK_URL"),
        app.config.get("WDM_HEALTH_CHECK_INTERVAL"),
    )
    return health_watcher.start()


def send_alive_status():
    external_service_url =  app.config['CONTROLLER_SERVICE_URL']
    hostname = socket.gethostname()
    ip_address = socket.gethostbyname(hostname)
    status_sent = False
    app.logger.info(f"Sending alive status for pod {ip_address} to {external_service_url}")
    while not status_sent:
        try:
            response = requests.post(
                external_service_url,
                json={"status": "alive", "service": ip_address, "port": app.config["WDM_SDR_AGENT_PORT"]}
            )
            response.raise_for_status()
            app.logger.info(f"Successfully sent alive status for pod {ip_address}")
            status_sent = True
        except requests.RequestException as e:
            app.logger.error(f"Attempt failed: {e} {external_service_url}")
            time.sleep(5)

def SendAliveStatus():
    if app.config.get("WDM_DISABLE_ALIVE_STATUS"):
        app.logger.info("Alive-status report disabled by WDM_DISABLE_ALIVE_STATUS")
        return False
    url = app.config.get("CONTROLLER_SERVICE_URL")
    if url is None or (isinstance(url, str) and not url.strip()):
        app.logger.info("CONTROLLER_SERVICE_URL is empty or not set; skipping alive-status report thread")
        return False
    app.logger.info("Starting alive-status report thread to %s", url)
    tr = Thread(target=send_alive_status)
    tr.start()
    return True

if __name__ == "__main__":  # Script executed directly?
    
    try:
        if app.config["WDM_CLEAR_DATA_WL"]:
            # Remove all streams to start from blank slate
            removeAllStreams()

            app.logger.info("Clearing WDM_WL_SPEC file")
            cfg.eraseSpecContent()
    except Exception as e:
        app.logger.exception("Couldn't clear WL spec file")
    
    listners = False
    if bus is not None and is_message_bus_lifecycle_mode(app.config):
        listen_kill_server()
        bus.run()
        app.logger.info("Kafka Listerner started")
    elif bus is not None:
        listners = True
        app.logger.info(
            "Kafka lifecycle listener disabled by WDM_LIFECYCLE_INGRESS_MODE=%s; "
            "WDM_KFK_ENABLE remains available for internal broker use",
            app.config.get("WDM_LIFECYCLE_INGRESS_MODE"),
        )
    else:
        listners = True
        if app.config["WDM_KFK_ENABLE"]:
            app.logger.debug("Kafka Listerner could not be started")

    # TODO: disable redislistener while container restart is in progress to prevent exessive add/remove and reprovision events?
    # When reenabled, start timestamp for messages that should be read should start from the reenabled timestamp to prevent stale messages (ie. reprovision events) from being processed 
    if not redisListener():
        app.logger.debug("Redis Listerner could not be started")
    else:
        listners = True
        app.logger.info("Redis Listener started")

    if not listners:
        app.logger.error("No Listerner could be started Exiting")
        sys.exit(-1)
    else:
        app.logger.info("Listener(s) started")

    preLoad()

    statefulSetWatcher()
    # Health watcher must start before PodErrorWatcher so Docker mode can
    # consume HTTP health transitions instead of Docker socket status.
    WorkloadHealthCheckWatcher()
    PodErrorWatcher()
    SendAliveStatus()

    if is_grpc_xds_enabled(app.config):
        if can_start_grpc_xds_server(app.config):
            grpc_thread = Thread(
                target=start_grpc_xds_server,
                args=(envy, app.config),
                daemon=True,
            )
            grpc_thread.start()
            app.logger.info("gRPC ADS server thread starting")
        else:
            app.logger.warning(
                "gRPC ADS server enabled but unavailable in this process; "
                "REST CDS/RDS xDS endpoints remain registered for compatibility"
            )
    else:
        app.logger.info(
            "gRPC ADS listener disabled in this process; REST CDS/RDS xDS "
            "endpoints remain registered for compatibility"
        )
    app.logger.info("application start on port %s" % (app.config["PORT"]))
    app.run(host="0.0.0.0", port=app.config["PORT"], use_reloader=False)

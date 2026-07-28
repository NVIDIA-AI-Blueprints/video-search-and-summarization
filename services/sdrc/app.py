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
from lib.logging import configure_root_logging
from lib.wdm_swagger_ui import openapi_public_server_root, register_wdm_swagger_ui
import requests
import os
import os.path
import re
import datetime
from prometheus_client import Gauge, generate_latest
import socket

class MaxReplicaException (Exception):

    def __init__(self, replica_count):
        super().__init__(f"Max replica count {replica_count} reached")


# Returned by provisionStreamRedis / reprovisionStreamRedis when placement is
# deferred because not all StatefulSet replicas are ready. Redis consumer must
# not ACK so the message stays pending until pods recover.
PROVISION_DEFERRED_UNREADY_PODS = object()


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
configure_root_logging(wl_log_prefix, REPO_ROOT)
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
        app.logger.info(
            "http_request %s %s status=%s elapsed_s=%.6f",
            request.method,
            request.path,
            response.status_code,
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
            response = provisionStreamRedis(
                app.config["WDM_WL_OBJECT_NAME"],
                wl_d, jValue, parent_context 
            )
            
            return "Provisioning process called"

        elif (wl_d is not None) and (change_field in wl_d) and (
            wl_d[change_field].lower() == change_id_reprovision
        ):
            app.logger.info("reprovision stream")
            response = reprovisionStreamRedis(
                app.config["WDM_WL_OBJECT_NAME"],
                wl_d, jValue, parent_context 
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
    app.logger.info("pod_list start")
    wlobj = curr_cluster.getStatefulSets()
    _req_elapsed = _wdm_http_request_elapsed_s()
    app.logger.info(
        "pod_list curr_cluster.getStatefulSets elapsed_s=%.6f wl=%s request_elapsed_s=%s",
        time.perf_counter() - _sts_t0,
        wl_object_name,
        "%.6f" % _req_elapsed if _req_elapsed is not None else "-",
    )
    if wlobj is not None:
        app.logger.info("pod_list wlobj.status.replicas=%s", getattr(wlobj.status, "replicas", None))
    if wlobj is not None and wlobj.status.replicas != 0:
        app.logger.info("pod_list wlobj is not None and wlobj.status.replicas != 0")
        WLObj = curr_cluster.getWorkloadObjects()
        app.logger.debug("pod_list WLObj: " + str(WLObj))
        if WLObj is not None:
            app.logger.info("pod_list WLObj is not None")
            pods_info = curr_cluster.getPodIps(WLObj)
            if pods_info is not None:
                # One Redis read per request (not per pod) — avoids N× lock_try under write contention.
                app.logger.info("pod_list cfg._loadWorkLoadSpec (once for all pods)")
                cfg._loadWorkLoadSpec()
                for p in pods_info:
                    pod_name = p.get("podName", "")
                    stream_ids = []
                    try:
                        app.logger.info("pod_list try cfg.getworkLoadSpecs pod=%s", pod_name)
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


def _run_provision_add_stream_to_pod_tracked(
    key_holder, podInfoItm, data, originalJson, config_data, wl_id, camera_id,
    otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False,
):
    """Thread target that runs _run_provision_add_stream_to_pod and removes self from provision_add_threads."""
    try:
        _run_provision_add_stream_to_pod(
            podInfoItm, data, originalJson, config_data, wl_id, camera_id,
            otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
        )
    finally:
        with provision_add_threads_lock:
            provision_add_threads.pop(key_holder[0], None)


def _run_provision_add_stream_to_pod(
    podInfoItm, data, originalJson, config_data, wl_id, camera_id, otel_carrier,
    parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False,
):
    """Run _provision_add_stream_to_pod; used as thread target so exceptions are logged."""
    try:
        _provision_add_stream_to_pod(
            podInfoItm, data, originalJson, config_data, wl_id, camera_id,
            otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
            do_workload_spec_in_thread_first=do_workload_spec_in_thread_first,
        )
    except Exception:
        if do_workload_spec_in_thread_first:
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
    parent_context, k8swlob_name, event_obj_field, span_data
):
    """Run add-stream provision synchronously or in a background thread per WDM_PROVISION_ASYNC."""
    if app.config.get("WDM_PROVISION_ASYNC"):
        # Do not call cfg.addWorkLoadSpec in main thread: redis_lock is non-reentrant and
        # the main thread may already hold the lock from earlier cfg calls in provisionStreamRedis.
        # The background thread will update workload spec first (before add RPC) so spec_count
        # is updated as soon as the thread runs.
        key_holder = [None]
        t = Thread(
            target=_run_provision_add_stream_to_pod_tracked,
            args=(
                key_holder,
                podInfoItm, data, originalJson, config_data, wl_id, camera_id,
                otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
                True,  # do_workload_spec_in_thread_first (avoids main-thread lock)
            ),
            daemon=False,
        )
        with provision_add_threads_lock:
            key_holder[0] = id(t)
            provision_add_threads[key_holder[0]] = t
        t.start()
        app.logger.info(
            "Provision add-stream running in background for wl_id=%s camera_id=%s",
            wl_id, camera_id,
        )
    else:
        _provision_add_stream_to_pod(
            podInfoItm, data, originalJson, config_data, wl_id, camera_id,
            otel_carrier, parent_context, k8swlob_name, event_obj_field, span_data,
            do_workload_spec_in_thread_first=False,
        )


def _provision_add_stream_to_pod(
    podInfoItm, data, originalJson, config_data, wl_id, camera_id, otel_carrier,
    parent_context, k8swlob_name, event_obj_field, span_data,
    do_workload_spec_in_thread_first=False,
):
    """Perform add-stream RPC, update route mapping and workload spec, optionally call webhook."""
    # When async: update workload spec first in this thread (before add RPC) so spec_count
    # is correct and we avoid main-thread redis_lock re-acquire.
    if do_workload_spec_in_thread_first:
        cfg.addWorkLoadSpec(podInfoItm["podName"], data, originalJson)
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
    except Exception:
        app.logger.exception("Unexpected exception encountered while provisioning")
        otel_span.set_status(tracing.StatusCode.ERROR, description="Provisioning failed")
        raise
    finally:
        otel_span.end()

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
        if do_workload_spec_in_thread_first:
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

    app.logger.info("add operation success updating the Route mapping")
    curr_cluster.updateRouteMapping(
        k8swlob_name, wl_id, podInfoItm, operation="add"
    )
    notify_xds_update()
    if not do_workload_spec_in_thread_first:
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

    app.logger.info("Provision Stream Redis")
    obj = redisMsging.getIdPodMapping(wl_id)
    wobj = cfg.getworkLoadSpecById(wl_id)
    if obj is not None and wobj is not None and len(wobj) > 0:
        app.logger.info("%s is already provisioned", wl_id)
        return

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
                # Phase 1: collect eligible pods (not down, not full, regex match)
                # and run stale-data cleanup while iterating.
                eligible_pods = []  # (podInfoItm, spec_count, wl_spec)
                for podInfoItm in podsInfo:
                    if curr_cluster.ifPodDown(podInfoItm["podName"]):
                        any_pod_down = True
                        app.logger.info(
                            "Pod %s is down continue", podInfoItm["podName"]
                        )
                        continue
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
                    podMapping = redisMsging.getIdPodMapping(wl_id)
                    podDnsMapping = redisMsging.getIdPodPodDnsMapping(
                        podInfoItm["podName"]
                    )
                    if podDnsMapping is None or podMapping is None:
                        cfg.deleteFromWorkLoadSpec(
                            podInfoItm["podName"], wl_id
                        )
                    spec_count = cfg.getSpecCount(podInfoItm["podName"])
                    if spec_count >= threshold:
                        continue
                    eligible_pods.append((podInfoItm, spec_count, wl_spec))

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
                    if assigning_method == "sequential":
                        # Original first-fit: take the first pod in the order
                        # returned by getPodIps() that passed all filters.
                        # No sorting — mirrors the original inline loop+break.
                        _entry = eligible_pods[0]
                        selected_pod, _sel_count, selected_wl_spec = _entry
                        app.logger.info(
                            "stream_updates - %s adding_stream: %s "
                            "(sequential selected pod=%s streams=%d)",
                            wl_object_name, wl_id,
                            selected_pod["podName"], _sel_count,
                        )
                    else:
                        # lru_round_robin (default): fewest streams first,
                        # tie-broken by (ordinal, name).
                        min_count = min(count for _, count, _ in eligible_pods)
                        selected_pod, selected_wl_spec = min(
                            (
                                (pod, wl_spec)
                                for pod, count, wl_spec in eligible_pods
                                if count == min_count
                            ),
                            key=lambda x: _pod_ordinal(x[0]["podName"]),
                        )
                        app.logger.info(
                            "stream_updates - %s adding_stream: %s "
                            "(lru_round_robin selected pod=%s streams=%d)",
                            wl_object_name, wl_id,
                            selected_pod["podName"], min_count,
                        )
                    otel_carrier = tracing.inject_context(parent_context)
                    if selected_wl_spec is None:
                        _run_provision_add_stream(
                            selected_pod, data, originalJson, config_data,
                            wl_id, camera_id, otel_carrier, parent_context,
                            k8swlob_name, cfg_ev_obj, originalJson,
                        )
                    else:
                        app.logger.info(
                            "%s pod is already deployed", selected_pod["podName"]
                        )
                        _run_provision_add_stream(
                            selected_pod, data, originalJson, config_data,
                            wl_id, camera_id, otel_carrier, parent_context,
                            k8swlob_name, cfg_ev_obj, config_data,
                        )
                    provisionNewPod = False
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
                    if ready_replicas < desired_replicas:
                        app.logger.info(
                            "Replica count %d but only %d ready; deferring "
                            "placement until unhealthy pods recover (wl_id=%s)",
                            desired_replicas,
                            ready_replicas,
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
    
    new_pod_encoded_name = config_event_json[app.config["WDM_POD_ALLOCATION_ENCODED_NAME_KEY"]]
    new_pod_encoded_name_arr = new_pod_encoded_name.split(app.config["WDM_POD_ALLOCATION_REGEX_DELIMITER"])
    
    current_allocation_configs = curr_cluster.get_current_allocation_configs()
    if isinstance(current_allocation_configs, dict):
        pod_match_found = True if new_pod_encoded_name in current_allocation_configs else False
    else:
        app.logger.info("Did not get back dictionary of current allocations. Will assume none exist.")
        pod_match_found = False
    
    # Make sure name is not already taken
    # TODO: override if it is?
    if pod_match_found:
        if "remove_config" in config_event_json and config_event_json["remove_config"]:
            app.logger.error(f"Removing given pod regex assignment. Assumed that all streams associated with this pod have already been accounted for elsewhere.")
            curr_cluster.delete_allocation_config(current_allocation_configs["new_pod_encoded_name"])
        else:
            app.logger.error(f"Name being used for configuration already configured previously and deallocate not requested - ignoring new request")
        return None
    
    # Find new pod with no association - if none found, return with error
    unallocated_pod_info = curr_cluster.find_unallocated_pod()
    if unallocated_pod_info is None:
        app.logger.error(f"No unallocated pods found to assign new configuration. Skipping allocation request")
        redisMsging.message_err(
            wlobject="system",
            podname=new_pod_encoded_name,
            id="SDR",
            type="critical",
            status="No unallocated pods found to assign new configuration"
        )
        return None
    
    # Add hash map entry for new pod - mark as taken internally
    unallocated_pod_info["encoded_matching_name"] = new_pod_encoded_name
    unallocated_pod_info["encoded_matching_name_split"] = new_pod_encoded_name_arr
    return_val = curr_cluster.update_current_allocation_configs(unallocated_pod_info)
    app.logger.info("allocated pod configuration: " + str(unallocated_pod_info))
    
    app.logger.info("Sending config provision request")
    originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]] = config_event_json
    resp = pc.applyConfig(unallocated_pod_info, originalJson)
    
    if resp.status_code != 200:
        app.logger.error(f"Error while trying to send configure request to endpoint - {str(resp)}")
        redisMsging.message_err(
            wlobject="system",
            podname=new_pod_encoded_name,
            id="SDR",
            type="critical",
            status="Error while sending configure request to endpoint"
        )
    
    return return_val

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
                        resp = pc.add(
                            podInfo=podInfoItm, configData=spec
                            )
                        app.logger.info(f"readd status {resp.status_code}")
                        if app.config["WDM_CHECK_STATUS"] and (
                            (
                                resp is not None
                                and resp.status_code != 200
                            )
                            or resp is None
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
    if __initPodState():
        app.logger.info ("Redis listener starting")
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
                app.logger.exception(
                    "unexpected exception caught while processing Redis stream - " + repr(e)
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
                    try:
                        global id_ctx_mapping
                        redis_skip_commit = False
                        if wl_d is not None:
                            if app.config["WDM_EVENT_OBJECT_FIELD"] in jValue and jValue[app.config["WDM_EVENT_OBJECT_FIELD"]] is not None and app.config["WDM_WL_ID_FIELD"] in jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]:
                                camera_id =jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
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
                                redis_skip_commit = True
                        elif (wl_d is not None) and (change_field in wl_d) and (
                            wl_d[change_field].lower() == change_id_reprovision
                        ):
                            app.logger.info("reprovision stream")
                            _rpv = reprovisionStreamRedis(
                                app.config["WDM_WL_OBJECT_NAME"],
                                wl_d, jValue, parent_context 
                            )
                            if _rpv is PROVISION_DEFERRED_UNREADY_PODS:
                                redis_skip_commit = True
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
                            podConfigureRedis(
                                app.config["WDM_WL_OBJECT_NAME"], wl_d, jValue
                            )                        
                        else:
                            app.logger.info("wl_d is None. wl_d: " + str(wl_d))
                        if redis_skip_commit:
                            app.logger.info(
                                "Not committing message id %s — deferred until "
                                "all workload replicas are ready; message stays pending",
                                item.msgid,
                            )
                            time.sleep(1.0)
                        else:
                            app.logger.info(
                                "Commiting message id %s" % (item.msgid)
                            )
                            consumer.commit(item_id=item.msgid)
                    except MaxReplicaException as me:
                        app.logger.error(f"Max replica exception {me}")
                        err_span = None
                        if wl_d is not None and (
                            app.config["WDM_EVENT_OBJECT_FIELD"] in jValue
                            and jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]
                            is not None
                            and app.config["WDM_WL_ID_FIELD"]
                            in jValue[app.config["WDM_EVENT_OBJECT_FIELD"]]
                        ):
                            _cid = jValue[app.config["WDM_EVENT_OBJECT_FIELD"]][
                                app.config["WDM_WL_ID_FIELD"]
                            ]
                            err_span = id_ctx_mapping.get(_cid, {}).get("span")
                        if err_span is not None:
                            err_span.set_status(tracing.StatusCode.ERROR)
                            err_span.end()
                        if evic_q_on_no_capacity:
                            consumer.commit(item_id=item.msgid)

                time.sleep(0.05)

        except Exception:
            app.logger.exception(
                "unexpected exception caught while processing Redis stream"
            )


if bus is not None:

    @bus.handle(topic)
    def kafka_topic_handler(msg):
        try:
            error_occured = False
            wl_d, originalJson = kfk.getMessageValue(bus, msg)

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
                    provisionStreamRedis(
                        app.config["WDM_WL_OBJECT_NAME"],
                        wl_d, originalJson, parent_context
                    )
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
                    podConfigureRedis(
                        app.config["WDM_WL_OBJECT_NAME"], wl_d, originalJson
                    )  

                           
                else:
                    app.logger.info("wl_d is None ")
            except MaxReplicaException as me:
                app.logger.error(f"Max replica exception Kafka {me}")
                error_occured = True
                if wl_d is not None:
                    try:
                        _kcid = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][
                            app.config["WDM_WL_ID_FIELD"]
                        ]
                    except (KeyError, TypeError):
                        _kcid = None
                    if _kcid is not None:
                        _kspan = id_ctx_mapping.get(_kcid, {}).get("span")
                        if _kspan is not None:
                            _kspan.set_status(tracing.StatusCode.ERROR)
                            _kspan.end()
                raise MaxReplicaException("Max replica reached")
            except Exception:
                app.logger.exception("exception occured")
                if wl_d is not None:
                    try:
                        _kcid = originalJson[app.config["WDM_EVENT_OBJECT_FIELD"]][
                            app.config["WDM_WL_ID_FIELD"]
                        ]
                    except (KeyError, TypeError):
                        _kcid = None
                    if _kcid is not None:
                        _kspan = id_ctx_mapping.get(_kcid, {}).get("span")
                        if _kspan is not None:
                            _kspan.set_status(tracing.StatusCode.ERROR)
                            _kspan.end()
        except Exception as e:
            app.logger.error(f"Exception occured: {e}")
            app.logger.error("An exception occured in the main loop")
            # raise Exception ("upstream exception")
        try:
            app.logger.info("commiting consumer message")
            bus.consumer.commit()
        except Exception as e:
            app.logger.error(f"Exception: {e}")

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
            print("Some error (this is expected) - " + repr(e))
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
                print("Some error (this is expected) - " + repr(e))
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
    pod_names = cfg.getpods()
    all_returns = []
    for pod in pod_names:
        all_returns.append(json.loads(cfg.getworkLoadSpecs(pod)))
    app.logger.info("all_cache_streams: " + str(all_returns))

    try:
        for pipeline in all_returns:
            curr_pipeline = json.loads(pipeline)
            for curr_stream in curr_pipeline:
                app.logger.info("curr_stream being removed: " + str(curr_stream))
                global id_ctx_mapping
                camera_id = curr_stream[app.config["WDM_EVENT_OBJECT_FIELD"]][app.config["WDM_WL_ID_FIELD"]]
                parent_context = id_ctx_mapping[camera_id]
                curr_stream[app.config["WDM_EVENT_OBJECT_FIELD"]]["change"] = app.config["WDM_WL_CHANGE_ID_DEL"]
                deprovisionStreamRedis(app.config["WDM_WL_OBJECT_NAME"], curr_stream[app.config["WDM_EVENT_OBJECT_FIELD"]], curr_stream, parent_context)
                time.sleep(0.1) # TODO: added since DS endpoints stop working/freezes when sending many remove requests at once. find a proper solution to this
    except Exception as e:
        app.logger.info("Something went wrong while trying to remove a stream - " + repr(e))
    
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
    if bus is not None:
        listen_kill_server()
        bus.run()
        app.logger.info("Kafka Listerner started")
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

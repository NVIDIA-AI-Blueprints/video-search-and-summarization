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

"""OpenAPI 3.0.3 document for run_workloads multi-workload router (create_router_app)."""


def router_openapi_document():
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "SDR Controller API",
            "version": "1.0.0",
            "description": (
                "Multi-workload router (run_workloads.py): Envoy xDS, dashboard, "
                "and /sdrc proxy. Swagger UI at /api/docs/."
            ),
        },
        "servers": [{"url": "/", "description": "ROUTER_HOST:ROUTER_PORT"}],
        "tags": [
            {"name": "meta", "description": "Index and spec"},
            {"name": "xds", "description": "Envoy discovery JSON"},
            {"name": "dashboard", "description": "Dashboard APIs"},
            {"name": "proxy", "description": "Forward to workload coordinator"},
        ],
        "paths": {
            "/openapi.json": {
                "get": {
                    "tags": ["meta"],
                    "summary": "OpenAPI document",
                    "operationId": "routerGetOpenApi",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.0.3",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True},
                                }
                            },
                        }
                    },
                }
            },
            "/": {
                "get": {
                    "tags": ["meta"],
                    "summary": "List workloads and /sdrc paths",
                    "operationId": "routerGetIndex",
                    "responses": {
                        "200": {
                            "description": "JSON index",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RouterIndex"},
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
                    "operationId": "routerPostDiscoveryClusters",
                    "responses": {
                        "200": {
                            "description": "CDS JSON",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True},
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
                    "operationId": "routerPostDiscoveryRoutes",
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "resource_names": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "RDS JSON",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True},
                                }
                            },
                        },
                        "404": {
                            "description": "No routes for resource_names",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorMessage"},
                                }
                            },
                        },
                    },
                }
            },
            "/dashboard": {
                "get": {
                    "tags": ["dashboard"],
                    "summary": "Dashboard HTML",
                    "operationId": "routerGetDashboard",
                    "responses": {
                        "200": {
                            "description": "HTML",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/dashboard/health": {
                "get": {
                    "tags": ["dashboard"],
                    "summary": "Per-workload health",
                    "operationId": "routerGetDashboardHealth",
                    "responses": {
                        "200": {
                            "description": "Keyed by wl_obj_name",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": {
                                            "$ref": "#/components/schemas/DashboardHealthEntry",
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/dashboard/clusterxds": {
                "get": {
                    "tags": ["dashboard"],
                    "summary": "Cluster xDS JSON",
                    "operationId": "routerGetDashboardClusterxds",
                    "responses": {
                        "200": {
                            "description": "CDS JSON",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True},
                                }
                            },
                        }
                    },
                }
            },
            "/dashboard/config_yml": {
                "get": {
                    "tags": ["dashboard"],
                    "summary": "config.yml path and content",
                    "operationId": "routerGetDashboardConfigYml",
                    "responses": {
                        "200": {
                            "description": "path and content",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ConfigYmlResponse"},
                                }
                            },
                        },
                        "404": {
                            "description": "Missing file",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorMessage"},
                                }
                            },
                        },
                        "500": {
                            "description": "Read error",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorMessage"},
                                }
                            },
                        },
                    },
                }
            },
            "/dashboard/global_add": {
                "post": {
                    "tags": ["dashboard"],
                    "summary": "Redis XADD or Kafka produce",
                    "operationId": "routerPostDashboardGlobalAdd",
                    "parameters": [
                        {
                            "name": "transport",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "string",
                                "enum": ["redis", "kafka"],
                                "default": "redis",
                            },
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "additionalProperties": True},
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/GlobalAddOk"},
                                }
                            },
                        },
                        "400": {"description": "Bad request"},
                        "500": {"description": "Upstream error"},
                        "503": {"description": "Not configured"},
                    },
                }
            },
            "/sdrc/{wl_obj_name}": {
                "parameters": [
                    {
                        "name": "wl_obj_name",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "First segment under /sdrc/; extra path segments are forwarded.",
                    }
                ],
                "get": {
                    "tags": ["proxy"],
                    "summary": "Proxy GET to workload coordinator",
                    "operationId": "routerProxyGet",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "post": {
                    "tags": ["proxy"],
                    "summary": "Proxy POST",
                    "operationId": "routerProxyPost",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "put": {
                    "tags": ["proxy"],
                    "summary": "Proxy PUT",
                    "operationId": "routerProxyPut",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "patch": {
                    "tags": ["proxy"],
                    "summary": "Proxy PATCH",
                    "operationId": "routerProxyPatch",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "delete": {
                    "tags": ["proxy"],
                    "summary": "Proxy DELETE",
                    "operationId": "routerProxyDelete",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "head": {
                    "tags": ["proxy"],
                    "summary": "Proxy HEAD",
                    "operationId": "routerProxyHead",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
                "options": {
                    "tags": ["proxy"],
                    "summary": "Proxy OPTIONS",
                    "operationId": "routerProxyOptions",
                    "responses": {
                        "200": {"description": "Coordinator response"},
                        "404": {"description": "Unknown wl_obj_name"},
                        "502": {"description": "Proxy failure"},
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "RouterIndex": {
                    "type": "object",
                    "properties": {
                        "workloads": {"type": "array", "items": {"type": "string"}},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "openapi": {"type": "string"},
                        "api_docs": {"type": "string"},
                    },
                },
                "ErrorMessage": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "detail": {"type": "string"},
                        "wl_obj_name": {"type": "string"},
                        "resource_names": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "DashboardHealthEntry": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "status_code": {"type": "integer"},
                        "error": {"type": "string"},
                        "sensor_count": {"type": "integer", "nullable": True},
                        "pod_count": {"type": "integer", "nullable": True},
                    },
                },
                "ConfigYmlResponse": {
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "GlobalAddOk": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "transport": {"type": "string"},
                        "stream": {"type": "string"},
                        "msg_id": {"type": "string"},
                        "topic": {"type": "string"},
                        "partition": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                },
            }
        },
    }

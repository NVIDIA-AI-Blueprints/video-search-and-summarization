<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->
# VIOS Sensor Management Microservice (Python)

Python reimplementation of the VIOS sensor-management **control-plane** microservice
(originally `module=sensor` / `libnvsensormanagement.so`). Apache-2.0, dependency-light, no
proprietary native libraries — intended to be readable and modifiable by public developers.

Design and frozen contract: `../../../../docs/sensor-ms-python-migration/DESIGN.md`.
Authoritative REST contract: `../../../../doc/api/vst_sensor_management_ms/swagger.yaml`.
The C++ implementation of this module lives alongside, in `../cpp/`.

## Status

The framework (routing, auth, error envelope, response Content-Type, DB models, credential crypto,
adaptor ABCs, event publisher) is wired to the contract, and the full REST surface is implemented:

- Read/CRUD: list, streams, add, delete, status, info (get), credentials, configuration (get),
  version, help, qos, timelines, scan.
- Write/update: info (post: name/position/tags with uniqueness + truncation), configuration (post:
  discovery interfaces + NTP, restart discovery), replace.
- Device control via the ONVIF control adaptor: reboot, network (get/set), settings (get/set).
  These require a real ONVIF camera; the response<->model mapping is factored into unit-tested pure
  helpers, but the live-hardware matrix is the outstanding P3 validation gate.
- Debug/test hooks: debug/plug, debug/unplug, debug/status (simulate camera plug/unplug; discovery
  skips blocked IPs).

## Contract behaviors baked in (do not "fix" these — they are deliberate parity requirements)

- **All JSON responses use `Content-Type: text/plain`** (see `api/responses.py`). The body is
  JSON; the wire header is `text/plain` to match the C++ service exactly.
- **Auth is HTTP bearer** on mutating endpoints; GETs are open (`api/auth.py`).
- **Error envelope is snake_case** `{"error_code", "error_message"}` (`api/errors.py`).

## Run (dev)

    pip install -e ".[dev]"
    uvicorn sensor_ms.main:app --host 0.0.0.0 --port 30010

OpenAPI docs at `/docs`. Health at `/v1/live`, `/v1/ready`, `/v1/startup`.

## Logging

`sensor_ms.*` logs go to stdout (`docker logs sensor-py`) with a timestamped format. Lifecycle events
are logged at INFO (sensor discovered/added/credentialed/removed/replaced, reboot/network/settings
applied, config applied, sensor-count-limit reached) and every error response is logged with request
context (5xx -> ERROR, 4xx -> WARNING, uncredentialed-ONVIF 401 -> INFO). Credentials are never logged.

## Notifications (message broker)

Camera events (`camera_add`/`camera_proxy`/`camera_remove`) are published to the broker selected by
`use_message_broker` (from `vst_config.json` `notifications` section or env), matching the C++
NotificationFactory. All three backends are implemented:
- **redis** — `XADD <topic> * {<payload_key>: <json>}` (`redis_server_env_var`)
- **kafka** — `produce(topic=<topic>, value=<json>, key=<payload_key>)` (`kafka_server_address` / `KAFKA_BOOTSTRAP_URL`)
- **mqtt** — `publish(<topic>, <json>, qos=1, retain=true)` (`mqtt_broker_address` / `MQTT_BROKER_ADDRESS`)

Publish is best-effort: a broker error is logged, not raised, so a flaky bus never fails the sensor
operation. Config is read from the `vst_config.json` `notifications` section (env vars override).

## Logging env knobs (`logging_setup.py`):
- `LOG_LEVEL` = `DEBUG|INFO|WARNING|ERROR` (default `INFO`; `DEBUG` adds per-scan WS-Discovery detail).
- `LOG_HEALTH_ACCESS` = `1` to keep the `/v1/ready|live|startup` access-log lines (filtered out by default).

## Layout

    sensor_ms/
      main.py            FastAPI app, response class, exception handlers, router wiring
      config.py          config keys (vst_config.json + env), §6.4 of DESIGN.md
      api/               routes, schemas (from swagger), auth, errors, responses
      core/              SensorManagement / DeviceManager orchestration (stubs)
      db/                SQLAlchemy models (exact DDL) + AES-256-CBC credential crypto
      events/            notification publisher (camera_add/remove/streaming/proxy)
      adaptors/          adaptor ABCs + importlib loader; onvif/ uses onvif-zeep-async
    tests/               contract + crypto-parity tests

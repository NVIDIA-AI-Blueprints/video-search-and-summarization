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

Phase 1 scaffold. The framework (routing, auth, error envelope, response Content-Type, DB models,
credential crypto, adaptor ABCs, event publisher) is wired to the contract. Business logic in
`core/` and the ONVIF adaptor are stubs marked `TODO(Pn)` per the migration phases.

## Contract behaviors baked in (do not "fix" these — they are deliberate parity requirements)

- **All JSON responses use `Content-Type: text/plain`** (see `api/responses.py`). The body is
  JSON; the wire header is `text/plain` to match the C++ service exactly.
- **Auth is HTTP bearer** on mutating endpoints; GETs are open (`api/auth.py`).
- **Error envelope is snake_case** `{"error_code", "error_message"}` (`api/errors.py`).

## Run (dev)

    pip install -e ".[dev]"
    uvicorn sensor_ms.main:app --host 0.0.0.0 --port 30010

OpenAPI docs at `/docs`. Health at `/v1/live`, `/v1/ready`, `/v1/startup`.

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

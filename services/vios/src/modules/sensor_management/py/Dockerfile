# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
FROM nvcr.io/nvidia/cuda:13.3.0-base-ubuntu24.04 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HTTP_PORT=30010 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app

# Install the package and its dependencies (fastapi, uvicorn, sqlalchemy, pg8000, redis,
# cryptography, onvif-zeep-async) from pyproject into the venv.
COPY pyproject.toml README.md ./
COPY sensor_ms ./sensor_ms
RUN pip install . && pip list | grep -Ei "fastapi|uvicorn|sqlalchemy|pg8000|redis|onvif|cryptography"

RUN useradd --create-home --uid 10001 sensor && chown -R sensor:sensor /app
USER sensor

EXPOSE 30010

# Honour HTTP_PORT (the compose sets it from SENSOR_PY_HTTP_PORT).
ENTRYPOINT ["sh", "-c", "exec uvicorn sensor_ms.main:app --host 0.0.0.0 --port ${HTTP_PORT}"]

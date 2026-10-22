# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

ARG BASE_IMAGE_URL=nvcr.io/nvidia/base/ubuntu
ARG BASE_IMAGE_TAG=jammy-20250415.1

FROM ${BASE_IMAGE_URL}:${BASE_IMAGE_TAG} AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Install python 3.12
RUN apt update && \
    apt upgrade -y && \
    apt install -y curl software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update && apt install -y python3.12 && \
    apt-get clean

RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

RUN rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app

# Copy dependency files. NOTE: uv.lock is NOT committed — run `uv lock` locally
# to generate it, or let the `uv sync` below resolve and create it at build time.
COPY pyproject.toml ./

# Resolve + install dependencies (creates uv.lock if absent).
RUN uv sync --no-dev

# Copy service source
COPY src/ ./src/
COPY main.py ./

# Run as a non-root user for runtime isolation.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app

ENV PORT=8090
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8090

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD uv run python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/v1/health')"

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]

# syntax=docker/dockerfile:1
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

FROM ubuntu:24.04 AS runtime

# Add metadata labels
LABEL maintainer="VST Team" \
      version="0.1.1" \
      description="MCP server gateway for connecting to C++ applications via REST API"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/app/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python and system dependencies in a single layer
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update -y && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-full \
    ca-certificates

# Create virtual environment and upgrade pip in one step
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip==24.3.1

# Copy only dependency file first for better layer caching
COPY pyproject.toml /app/

# Install dependencies only (without the package itself)
# This layer will be cached unless pyproject.toml changes
RUN --mount=type=cache,target=/root/.cache/pip \
    /app/venv/bin/pip install --no-cache-dir \
    mcp==1.23.0 \
    httpx>=0.28.1 \
    pydantic>=2.11.7 \
    uvloop>=0.21.0 \
    "uvicorn>=0.35.0,<0.36.0" \
    starlette>=0.49.1 \
    pydantic-settings>=2.7.1

# Copy application source code (changes frequently, so keep it last)
COPY README.md /app/
COPY src /app/src

# Install the package in editable mode (or copy mode without reinstalling deps)
RUN /app/venv/bin/pip install --no-cache-dir --no-deps .

# Expose the HTTP port
EXPOSE 8000

# Use virtual environment python (PATH already includes venv/bin)
ENTRYPOINT ["python", "-m", "src"]
CMD ["--transport", "http"]

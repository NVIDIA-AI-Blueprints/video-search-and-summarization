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

# ARG declarations for the entire Dockerfile
ARG TARGETARCH
ARG TARGETPLATFORM
ARG PYTHON_VERSION=3.13
ARG TAG=v3.1.7
ARG IMG=nvcr.io/nvidian/distroless/python:${PYTHON_VERSION}

# Stage 1: Build Stage - Use a slim Python image to install dependencies
FROM python:${PYTHON_VERSION}-slim AS build
ARG TARGETARCH

# Set the working directory in the build stage
WORKDIR /app

# Install necessary system dependencies for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    librdkafka-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifest for install
COPY requirements.txt /app/requirements.txt

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Remove setuptools after installation (before copying)
RUN rm -rf /usr/local/lib/python3.13/site-packages/setuptools*

# Find the installed Python version dynamically
RUN ln -s /usr/local/lib/python3.13/site-packages /python-packages

# Stage 2: Production Stage - Use Distroless Python as the base image (per-platform)
FROM --platform=$TARGETPLATFORM nvcr.io/nvidian/distroless/python:${PYTHON_VERSION}-${TAG} AS prefinal
ARG TARGETARCH

# Set the working directory in the final stage
WORKDIR /app

# Copy Python runtime dependencies from the build stage
COPY --from=build /python-packages /usr/local/lib/python3.13/site-packages

FROM --platform=$TARGETPLATFORM busybox:1.36 AS strip
ARG TARGETARCH

# Copy the full root filesystem from the prefinal image and remove unwanted artifacts
# Remove sqlite and libexpat (CVE-2025-59375) - not used by application
COPY --from=prefinal / /rootfs
RUN rm -f \
    /rootfs/usr/lib/aarch64-linux-gnu/libsqlite3.so.0 \
    /rootfs/usr/lib/aarch64-linux-gnu/libsqlite3.so.0.8.6 \
    /rootfs/var/lib/dpkg/status.d/libsqlite3-0 \
    /rootfs/var/lib/dpkg/status.d/libsqlite3-0.md5sums \
    /rootfs/usr/lib/aarch64-linux-gnu/libexpat.so.* \
    /rootfs/usr/lib/x86_64-linux-gnu/libexpat.so.* \
    /rootfs/var/lib/dpkg/status.d/libexpat* || true

FROM scratch AS final

# Bring in the stripped root filesystem
COPY --from=strip /rootfs /

# Copy application files into the final image
COPY . /app

# Ship the NVIDIA Software License inside the container (governs the distributed
# image; must match the NGC catalog card). Source files remain Apache-2.0.
COPY NVIDIA-Software-License-Agreement.pdf /app/NVIDIA-Software-License-Agreement.pdf

# Minimal Python runtime settings and non-root execution
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532:65532

# Set the command to run the Python application with HTTP API (Alert Bridge)
CMD ["/usr/local/bin/python", "enhance_alert_with_vlm.py", "--config", "/app/config.yaml"]

# Expose the FastAPI port (documentation only with host networking)
EXPOSE 9080

# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

FROM nvcr.io/nvidia/distroless/python:3.13-v4.0.5 AS python-base

# Ubuntu builder: apt installs and Python package install via uv.
# (The prior pkg-installer stage was merged in here; uv must be present on PATH for
# the `uv pip install --target /tmp/packages` RUN below.)
FROM nvcr.io/nvidia/base/ubuntu:jammy-20250415.1 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN --mount=type=bind,source=docker/base/requirements_apt.txt,target=/tmp/requirements_apt.txt \
    apt-get update \
    && apt-get install -y --no-install-recommends $(cat /tmp/requirements_apt.txt) \
    && rm -rf /var/lib/apt/lists/*

COPY LICENSE.3rdparty /opt/nvidia/via/

RUN --mount=type=bind,source=docker/base/py_deps,target=/tmp/py_deps \
    cd /tmp/py_deps && uv export --frozen --no-dev --no-hashes --no-emit-project | \
    uv pip install --python-version 3.13 --target /tmp/packages -r -

# Final distroless production image
FROM python-base

COPY --from=builder /tmp/packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=builder /opt/nvidia/via /opt/nvidia/via

ENV HF_HOME=/tmp/huggingface

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

FROM python:3.12-slim

# websocket-client drives the OpenClaw gateway; uv runs the `vss` CLI that
# backs POST /v1/search. The repo itself is bind-mounted at runtime rather
# than copied, so the CLI always matches the deployment it is serving.
RUN pip install --no-cache-dir websocket-client==1.8.0 \
    && pip install --no-cache-dir uv==0.11.28

WORKDIR /app
COPY adapter.py /app/adapter.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Unprivileged: the adapter holds a gateway token and shells out to the CLI,
# so it has no reason to run as root.
RUN useradd --create-home --uid 10001 adapter

# uv needs somewhere writable for the CLI's virtualenv and cache, since the
# checkout is mounted read-only. Creating it here with the right ownership
# matters: docker seeds a fresh named volume from the image path, so the mount
# inherits this and the unprivileged user can write to it.
RUN mkdir -p /venv && chown adapter:adapter /venv

USER adapter

# SETUPTOOLS_SCM_PRETEND_VERSION: the checkout is mounted without git metadata
# the container user can read, so setuptools-scm cannot derive a version and the
# CLI build fails. The value itself is irrelevant; it only has to be set.
ENV ADAPTER_PORT=9098 \
    PYTHONUNBUFFERED=1 \
    UV_BIN=/usr/local/bin/uv \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

EXPOSE 9098

# No curl in the image on purpose; the healthcheck uses the interpreter that
# is already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os,urllib.request,sys; \
url=f\"http://127.0.0.1:{os.environ.get('ADAPTER_PORT','9098')}/health\"; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/entrypoint.sh"]

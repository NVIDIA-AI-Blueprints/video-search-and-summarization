# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

ARG PYTHON_VERSION=3.13
ARG TAG=v3.1.7

# Stage 1: build -- resolve wheels and collect the fonts the preview card needs.
FROM python:${PYTHON_VERSION}-slim AS build

WORKDIR /app

# fonts-dejavu-core is a runtime asset, not a build tool: the distroless base
# ships no fonts, and without one the OG preview card falls back to PIL's
# bitmap default (renders, but looks wrong at 1200x630).
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Drop setuptools from the shipped closure (matches services/alert).
RUN rm -rf /usr/local/lib/python${PYTHON_VERSION}/site-packages/setuptools* \
    && ln -s /usr/local/lib/python${PYTHON_VERSION}/site-packages /python-packages

# Stage 2: runtime -- distroless, non-root.
FROM nvcr.io/nvidia/distroless/python:${PYTHON_VERSION}-${TAG} AS final
ARG PYTHON_VERSION

WORKDIR /app

COPY --from=build /python-packages /usr/local/lib/python${PYTHON_VERSION}/site-packages
COPY --from=build /usr/share/fonts/truetype/dejavu /usr/share/fonts/truetype/dejavu

COPY vss_share /app/vss_share

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

USER 65532:65532

EXPOSE 9095

CMD ["/usr/local/bin/python", "-m", "uvicorn", "vss_share.app:app", "--host", "0.0.0.0", "--port", "9095"]

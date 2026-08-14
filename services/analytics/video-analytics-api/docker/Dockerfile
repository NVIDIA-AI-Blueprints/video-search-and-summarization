# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
FROM node:22.23.2-trixie AS builder

# Build web-api-core from source
COPY ./src/web-api-core /web-api-core

# Install dependencies in web-api-core
WORKDIR /web-api-core
RUN npm install

# Copy app and install with locally built web-api-core
RUN mkdir /web-api-app
COPY ./src/app /web-api-app
WORKDIR /web-api-app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl=8.14.1-2+deb13u4 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fL -o NVIDIA-Software-License-Agreement.pdf \
    https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/NVIDIA-Software-License-Agreement-2026.5.7.pdf

RUN mkdir -p /web-api-app/files
RUN npm install --save ../web-api-core
RUN npm install

###################################################
FROM nvcr.io/nvidia/distroless/node:22-v4.0.11 AS node-deploy

COPY --from=builder --chown=1000:1000 /web-api-core /web-api-core
COPY --from=builder --chown=1000:1000 /web-api-app /web-api-app
COPY --chown=1000:1000 ./configs /configs
COPY ./3rdParty_Licenses.md /web-api-app/3rdParty_Licenses.md
WORKDIR /web-api-app
USER 1000:1000
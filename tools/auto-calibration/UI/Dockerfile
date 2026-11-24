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

# Stage 1: Build the React UI
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files first for better caching
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Create directory for source packages
RUN mkdir -p /usr/src/packages

# Download source for all installed packages
RUN cd /usr/src/packages && \
    npm pack @emotion/react @emotion/styled @mui/icons-material @mui/material \
    axios konva react react-dom react-konva

# Copy source files
COPY . ./

# Build the application
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy source packages from builder stage
COPY --from=builder /usr/src/packages /usr/src/packages

# CVE Mitigation: Remove vulnerable libtiff. tiff is required by libgd which is
# required by nginx-module-image-filter.  so remove the image-filter module first, then libgd and tiff.
RUN apk del nginx-module-image-filter libgd tiff 2>/dev/null || true

# CVE Mitigation: Upgrade libpng to 1.6.55
RUN apk upgrade --no-cache libpng

# CVE Mitigation: Upgrade nghttp2-libs to 1.68.1+ (CVE-2026-27135) and curl to 8.19.0+ (CVE-2026-3805).
# curl fix is not yet in the standard Alpine repos for this base image, so pull both from Alpine edge/main.
RUN apk add --no-cache --upgrade \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    nghttp2-libs curl

# CVE-2026-14456: upgrade both OpenSSL runtime libraries past 3.5.7-r0.
# Keep the version check in the image build so an unavailable security update
# cannot silently produce a UI image that still carries the vulnerable APKs.
RUN apk add --no-cache --upgrade \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    'openssl>=3.5.8-r0' \
    'libcrypto3>=3.5.8-r0' \
    'libssl3>=3.5.8-r0' && \
    apk info -e 'libcrypto3>=3.5.8-r0' && \
    apk info -e 'libssl3>=3.5.8-r0'

# Copy custom nginx config for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy entrypoint script for runtime config
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose port 5000
EXPOSE 5000

# Use entrypoint to inject runtime config before starting nginx
ENTRYPOINT ["/docker-entrypoint.sh"]

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

# Dockerfile specifically for Redis health check
# Uses lightweight Alpine image

FROM alpine:3.23.2

# Install necessary tools for port checking
RUN apk add --no-cache \
    bash \
    netcat-openbsd

# Copy Redis health check script
COPY --chmod=755 ./broker-health-check/scripts/check-redis-health.sh /scripts/check-redis-health.sh

# Direct entrypoint to Redis health check script
ENTRYPOINT ["/scripts/check-redis-health.sh"]

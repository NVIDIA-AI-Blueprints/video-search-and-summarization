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

# Shared Kafka tooling image for health-check and topic-init.
# cp-kafka 8.3+ is based on ubi9-micro and no longer includes curl/wget,
# so jq is fetched in a separate Alpine stage and copied in.
#
# Build targets:
#   kafka-base       - cp-kafka + jq (shared; used by kafka-topic-init-container)
#   kafka-health-check - broker/topic readiness check (default)

FROM alpine:3.24.1 AS jq-fetch
# Fetch the upstream *static* jq: Alpine's own jq package is musl-linked and
# would not run once copied into the glibc-based cp-kafka stage below. busybox
# wget is already in the base image, so nothing needs installing -- an earlier
# `apk add curl=<version>` here broke when Alpine dropped that exact version
# from its index.
RUN ARCH=$(uname -m) \
    && if [ "$ARCH" = "x86_64" ]; then \
         JQ_URL="https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64"; \
       elif [ "$ARCH" = "aarch64" ]; then \
         JQ_URL="https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-arm64"; \
       else \
         echo "Unsupported architecture: $ARCH" && exit 1; \
       fi \
    && mkdir -p /jqbin \
    && wget -q -O /jqbin/jq "$JQ_URL" \
    && chmod +x /jqbin/jq

FROM confluentinc/cp-kafka:8.3.0 AS kafka-base

USER root
RUN mkdir -p /home/appuser/jqbin
COPY --from=jq-fetch /jqbin/jq /home/appuser/jqbin/jq
RUN chown -R appuser:appuser /home/appuser/jqbin
ENV PATH="/home/appuser/jqbin:${PATH}"
USER appuser

FROM kafka-base AS kafka-health-check

USER root
COPY --chmod=755 ./broker-health-check/scripts/check-kafka-health.sh /scripts/check-kafka-health.sh
USER appuser

ENTRYPOINT ["/scripts/check-kafka-health.sh"]

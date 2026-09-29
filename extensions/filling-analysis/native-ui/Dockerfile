# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Build the custom tab on the exact nightly UI source and dependency lock.
ARG BASE_UI_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-agent-ui@sha256:9f097898c769dd82ceacacd328566322f25417688c7ed009e0ea8cde370ea656
FROM node:22.23.2 AS builder
WORKDIR /repo/services/ui
COPY . ./
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_BUILD_TRACES=false
RUN --mount=type=cache,id=vss-ui-20260922-npm,target=/root/.npm npm ci --prefer-offline
RUN npx turbo run build bundle --filter=./apps/nv-metropolis-bp-vss-ui
RUN npx tsc --project apps/nv-metropolis-bp-vss-ui/tsconfig.filling.json --incremental false

FROM ${BASE_UI_IMAGE} AS runtime
COPY --from=builder --chown=65532:65532 --chmod=555 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/.next/standalone /repo/apps/nv-metropolis-bp-vss-ui/
COPY --from=builder --chown=65532:65532 --chmod=555 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/.next/static /repo/apps/nv-metropolis-bp-vss-ui/apps/nv-metropolis-bp-vss-ui/.next/static
COPY --from=builder --chown=65532:65532 --chmod=755 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/public /repo/apps/nv-metropolis-bp-vss-ui/apps/nv-metropolis-bp-vss-ui/public
LABEL org.opencontainers.image.title="VSS native filling analysis with OpenClaw chat" org.opencontainers.image.revision="1f1a0b904df824fbcdcd81287547881de47d4c31" com.nvidia.vss.extension="native-filling-analysis"

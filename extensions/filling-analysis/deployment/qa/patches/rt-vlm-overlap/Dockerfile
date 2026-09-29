# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Derived from the verified nightly-20260922 image; only two Python files change.
FROM ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm@sha256:bed1e5d8c088ade0dcdc75c8f828de6c9cf38331701301416cb0e14ce73c4301
COPY services/rtvi/rt-vlm/src/vlm_pipeline/vlm_pipeline.py /opt/nvidia/rtvi/rtvi/vlm_pipeline/vlm_pipeline.py
COPY services/rtvi/rt-vlm/src/server/rtvi_stream_handler.py /opt/nvidia/rtvi/rtvi/server/rtvi_stream_handler.py
LABEL org.opencontainers.image.revision="1f1a0b904df824fbcdcd81287547881de47d4c31" com.nvidia.vss.extension="live-overlap-forwarding"

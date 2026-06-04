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

# Export IS_SBSA=true on the host and pass `--build-arg IS_SBSA` to select the
# ARM64/SBSA base image; otherwise the default (x86_64) tag is used. Override
# BASE_IMAGE directly to point at a fully custom base.
ARG IS_SBSA=
ARG BASE_IMAGE="nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0${IS_SBSA:+-sbsa}"
FROM ${BASE_IMAGE}

# Overlay the open-source RTVI VLM Python sources on top of the base image.
# The base image already contains all third-party dependencies, the entrypoint
# (/opt/nvidia/rtvi/start_rtvi_vlm.sh), and a build-time copy of the sources;
# this layer replaces those sources with the Apache-2.0 licensed tree.
#
# start_rtvi_vlm.sh `cd`s into a subdirectory before running Python:
#   MODE=release      → /opt/nvidia/rtvi/rtvi/   (default)
#   MODE=development  → /opt/nvidia/rtvi/src/
# We overlay BOTH paths so either mode picks up the Apache source.
COPY --chown=1001:1001 src/ /opt/nvidia/rtvi/rtvi/
COPY --chown=1001:1001 src/ /opt/nvidia/rtvi/src/

USER vss:1001
WORKDIR /opt/nvidia/rtvi/

ENTRYPOINT ["/opt/nvidia/rtvi/start_rtvi_vlm.sh"]

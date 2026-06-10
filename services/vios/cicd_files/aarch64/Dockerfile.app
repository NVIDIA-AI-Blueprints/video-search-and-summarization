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

ARG BASE_IMAGE=vios/vst-base:2.1.0-runtime-26.04.1
FROM ${BASE_IMAGE}

ARG PKG_LOCATION

# Add the package to /home/vst
RUN mkdir -p /home/vst
ADD ${PKG_LOCATION}/vst_release.tbz2 /home/vst

# Ensure all users can read and execute files in /home/vst/vst_release
RUN chmod -R 755 /home/vst/vst_release

# Create directory if it doesn't exist
RUN mkdir -p /usr/lib/aarch64-linux-gnu/libv4l/plugins/nv

# Create proper links for v4l2
RUN ln -sf /home/vst/vst_release/prebuilts/aarch64/sbsa/libnvv4l2.so /usr/lib/aarch64-linux-gnu/libv4l2.so.0.0.999999
RUN ln -sf /usr/lib/aarch64-linux-gnu/libv4l2.so.0.0.999999 /usr/lib/aarch64-linux-gnu/libv4l2.so.0
RUN ln -sf /usr/lib/aarch64-linux-gnu/libv4l2.so.0 /usr/lib/aarch64-linux-gnu/libv4l2.so

# Plugins needed by v4l2
RUN ln -sf /home/vst/vst_release/prebuilts/aarch64/sbsa/libv4l2_nvcuvidvideocodec.so /usr/lib/aarch64-linux-gnu/libv4l/plugins/nv/libv4l2_nvcuvidvideocodec.so

RUN mkdir -p /usr/lib/aarch64-linux-gnu/nvidia
RUN ln -sf /home/vst/vst_release/prebuilts/aarch64/sbsa/libnvbufsurface.so.1.0.0 /usr/lib/aarch64-linux-gnu/nvidia/libnvbufsurface.so
RUN ln -sf /home/vst/vst_release/prebuilts/aarch64/sbsa/libnvbufsurftransform.so.1.0.0 /usr/lib/aarch64-linux-gnu/nvidia/libnvbufsurftransform.so
RUN ln -sf /home/vst/vst_release/prebuilts/aarch64/sbsa/libnvmm_jpeg.so /usr/lib/aarch64-linux-gnu/nvidia/libnvmm_jpeg.so

WORKDIR /home/vst/vst_release
ENV LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1:/lib/aarch64-linux-gnu/libGLdispatch.so.0
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/:/usr/lib/aarch64-linux-gnu/nvidia/:/home/vst/vst_release/prebuilts/aarch64/:/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/sbsa/
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/deepstream/gst-plugins/
ENV CUDA_CACHE_DISABLE=0

RUN ldconfig

ENTRYPOINT  ["/home/vst/vst_release/launch_vst"]

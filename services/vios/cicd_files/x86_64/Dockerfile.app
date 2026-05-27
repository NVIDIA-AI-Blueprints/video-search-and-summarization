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

ARG BASE_IMAGE=nvcr.io/rxczgrvsg8nx/vst-dev/vst-base:2.1.0-runtime-26.04.1
FROM ${BASE_IMAGE}

ARG PKG_LOCATION

# Create the user vst with home directory
RUN useradd -ms /bin/bash vst

# Add the vst package in home directory
ADD ${PKG_LOCATION}/vst_release.tbz2 /home/vst

# vst user owner & permissions
RUN chown -R vst:vst /home/vst

RUN ln -sf /home/vst/vst_release/prebuilts/x86_64/deepstream/libcuvidv4l2_plugin.so /usr/lib/x86_64-linux-gnu/libv4l/plugins/libcuvidv4l2_plugin.so
RUN ln -sf /home/vst/vst_release/prebuilts/x86_64/deepstream/libnvbufsurface.so /usr/lib/x86_64-linux-gnu/libnvbufsurface.so
RUN ln -sf /home/vst/vst_release/prebuilts/x86_64/deepstream/libnvbufsurftransform.so /usr/lib/x86_64-linux-gnu/libnvbufsurftransform.so
RUN ln -sf /home/vst/vst_release/prebuilts/x86_64/deepstream/libgstnvdsseimeta.so /usr/lib/x86_64-linux-gnu/libgstnvdsseimeta.so
RUN ln -sf /home/vst/vst_release/prebuilts/x86_64/deepstream/libnvbuf_fdmap.so /usr/lib/x86_64-linux-gnu/libnvbuf_fdmap.so

# Use v4l2 library from prebuilts instead of standard path
RUN rm -rf /usr/lib/x86_64-linux-gnu/libv4l2.so.0.0.0
RUN mv /home/vst/vst_release/prebuilts/x86_64/deepstream/libv4l2.so /usr/lib/x86_64-linux-gnu/libv4l2.so.0.0.0

# Switch to non-root user
WORKDIR /home/vst/vst_release
RUN chown -R 1000:1000 /home/vst
USER 1000:1000

ENV LD_LIBRARY_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/:/home/vst/vst_release/prebuilts/x86_64/gst-plugins/:/home/vst/vst_release/prebuilts/x86_64/:/home/vst/vst_release/prebuilts/x86_64/aws
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/gst-plugins:/home/vst/vst_release/prebuilts/x86_64/gst-plugins
ENV CUDA_CACHE_DISABLE=0

# vst process as starting point
ENTRYPOINT  ["/home/vst/vst_release/launch_vst"]

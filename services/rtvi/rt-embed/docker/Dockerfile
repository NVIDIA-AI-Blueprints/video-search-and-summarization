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

ARG ARM_PLATFORM="igpu"

######## Pytorch container
FROM nvcr.io/nvidia/pytorch:26.03-py3 AS pytorch_src-amd64
FROM nvcr.io/nvidia/pytorch:26.03-py3 AS pytorch_src-arm64

FROM pytorch_src-$TARGETARCH AS pytorch_src
##############################

######### Define base image per platform
FROM nvcr.io/nvidia/tritonserver:26.03-py3 AS base-amd64

RUN wget -O /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2 \
        https://github.com/NVIDIA/DeepStream/releases/download/v9.1.0/deepstream_sdk_v9.1.0_x86_64.tbz2 && \
    echo "fcafb7b5e4fbdf38b752eb35807011b69b14af826d6807180288d4d3d9b1ecbc  /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2" | sha256sum -c - && \
    tar xf /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2 -C / && \
    /opt/nvidia/deepstream/deepstream-9.1/install.sh && \
    rm /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2

FROM nvcr.io/nvidia/deepstream:9.1-triton-multiarch AS base-igpu
FROM nvcr.io/nvidia/deepstream:9.1-triton-sbsa-dgx-spark AS base-sbsa

FROM base-$ARM_PLATFORM AS base-arm64

# DeepStream publishes the patched RTSP plugins in /tmp99 of this image for
# both amd64 and arm64. Copying them avoids invoking update_rtpmanager.sh and
# retaining its source-build dependencies in the runtime image.
FROM nvcr.io/nvidia/deepstream:9.1-triton-multiarch AS rtpmanager-prebuilt
RUN test -s /tmp99/libgstrtpmanager.so \
    && test -s /tmp99/libgstrtsp.so \
    && test -s /tmp99/libgstvideoparsersbad.so
###############################

# Build a Thor-compatible PyDS wheel from the public DeepStream Python bindings.
# The upstream release arm64 wheel deliberately rejects get_nvds_buf_surface_gpu
# on non-SBSA arm64; the local Cosmos Embed1 raw-frame path requires it.
# PYDS_SOURCE_COMMIT is the verified source pin for the Git checkout below.
FROM base-arm64 AS pyds_arm64_builder
ARG TARGETARCH
ARG ARM_PLATFORM
ARG PYDS_SOURCE_COMMIT=6fdeefb7128435873f7794d2242ed48a1471ad7e
COPY docker/binaries/igpu/libnvbufsurface.so \
     docker/binaries/igpu/libnvbufsurftransform.so \
     /tmp/igpu-tegra/
RUN --mount=type=bind,source=docker/ubuntu.sources.${TARGETARCH},target=/etc/apt/sources.list.d/ubuntu.sources \
    if [ "$ARM_PLATFORM" = "igpu" ]; then \
        tegra_lib_dir=/usr/lib/aarch64-linux-gnu/tegra; \
        if [ -L "$tegra_lib_dir" ] && [ ! -e "$tegra_lib_dir" ]; then \
            mkdir -p "$(readlink -m "$tegra_lib_dir")"; \
        elif [ ! -e "$tegra_lib_dir" ]; then \
            mkdir -p "$tegra_lib_dir"; \
        fi && \
        test -d "$tegra_lib_dir" && \
        cp /tmp/igpu-tegra/libnvbufsurface.so /tmp/igpu-tegra/libnvbufsurftransform.so "$tegra_lib_dir/"; \
    fi && \
    rm -rf /tmp/igpu-tegra && \
    apt update && apt install -y --no-install-recommends \
    build-essential cmake git libglib2.0-dev libgstreamer1.0-dev \
    python3.12-dev python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/*
RUN git init /tmp/deepstream_python_apps \
    && git -C /tmp/deepstream_python_apps remote add origin https://github.com/NVIDIA-AI-IOT/deepstream_python_apps.git \
    && git -C /tmp/deepstream_python_apps fetch --depth=1 origin "${PYDS_SOURCE_COMMIT}" \
    && git -C /tmp/deepstream_python_apps checkout --detach --force FETCH_HEAD \
    && test "$(git -C /tmp/deepstream_python_apps rev-parse HEAD)" = "${PYDS_SOURCE_COMMIT}" \
    && git -C /tmp/deepstream_python_apps submodule update --init --recursive --depth 1 \
    && cd /tmp/deepstream_python_apps \
    && python3 bindings/3rdparty/git-partial-submodule/git-partial-submodule.py restore-sparse
COPY docker/patches/pyds-arm64.patch /tmp/pyds-arm64.patch
# Thor requires the IS_SBSA compile-time override to enable
# get_nvds_buf_surface_gpu on aarch64.
RUN cd /tmp/deepstream_python_apps \
    && git apply /tmp/pyds-arm64.patch \
    && cd bindings \
    && CMAKE_ARGS="-DIS_SBSA=on -DDS_PATH=/opt/nvidia/deepstream/deepstream-9.1" \
       CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)" python3 setup.py bdist_wheel \
    && python3 -c 'import pathlib, zipfile; wheel = next(pathlib.Path("dist").glob("pyds-*.whl")); assert b"get_nvds_buf_surface_gpu: Currently we only support x86" not in zipfile.ZipFile(wheel).read("pyds.so")'

# Only the selected target's PyDS stage is evaluated. amd64 deliberately
# provides an empty directory because it retains the verified release wheel.
FROM pyds_arm64_builder AS pyds-arm64
RUN mkdir -p /tmp/pyds-arm64 \
    && cp /tmp/deepstream_python_apps/bindings/dist/pyds-*.whl /tmp/pyds-arm64/
FROM base-amd64 AS pyds-amd64
RUN mkdir -p /tmp/pyds-arm64
FROM pyds-${TARGETARCH} AS pyds-wheel

######## Generate requirements.txt
FROM pytorch_src AS generate-requirements
ARG TARGETARCH
RUN --mount=type=bind,source=docker/ubuntu.sources.${TARGETARCH},target=/etc/apt/sources.list.d/ubuntu.sources \
    apt update && apt install python3.12-venv libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev pkg-config -y
ADD https://raw.githubusercontent.com/pdm-project/pdm/75156a09d7e710d8e10117c2c7c88e8ce5097e7d/install-pdm.py /tmp/python/
RUN echo "e1c7f6455fa7ffc50cbc13e4d49c06dfaaf8e9b74d0c9b46287bf767f6a4e4fc  /tmp/python/install-pdm.py" | sha256sum -c -
RUN python3 /tmp/python/install-pdm.py
COPY docker/py_deps/pdm.lock docker/py_deps/pyproject.toml /tmp/python/
RUN cd /tmp/python/ && /root/.local/bin/pdm export -G ${TARGETARCH} --no-hashes > requirements.txt

# Build GStreamer timestamp filter plugin
COPY src/gst-plugins /tmp/gst-plugins
RUN cd /tmp/gst-plugins && make

# Builder base selection: Use the selected DeepStream image for ARM (has libgstnvdsseimeta.so)
FROM base-${ARM_PLATFORM} AS builder-base-arm64
FROM base-amd64 AS builder-base-amd64

# Build stage for video_sei_metadata Python bindings
FROM builder-base-${TARGETARCH} AS video_sei_metadata_builder
ARG TARGETARCH

# Install build dependencies (header is bundled in source).
# Bind the public Ubuntu source configuration so this stage also works on
# GitHub-hosted CI runners without NVIDIA internal-network access.
RUN --mount=type=bind,source=docker/ubuntu.sources.${TARGETARCH},target=/etc/apt/sources.list.d/ubuntu.sources \
    apt update && apt install -y --no-install-recommends \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev pkg-config \
    python3.12-dev pybind11-dev wget && rm -rf /var/lib/apt/lists/*

# DeepStream is already available in the selected builder base. amd64 installs
# the public GitHub SDK in base-amd64; ARM uses the DeepStream base image.

# Install pybind11 Python module and ensure python3-config is available
RUN pip install --index-url https://pypi.org/simple pybind11 && \
    if [ ! -f /usr/bin/python3-config ] && [ -f /usr/bin/python3.12-config ]; then \
        ln -sf /usr/bin/python3.12-config /usr/bin/python3-config; \
    fi

# Copy source and Makefile
COPY src/cpp/video_sei_metadata /build/video_sei_metadata
COPY docker/binaries/igpu/libgstnvdsseimeta.so /tmp/libgstnvdsseimeta.so

RUN if [ "$TARGETARCH" = "arm64" ]; then \
        mkdir -p /opt/nvidia/deepstream/deepstream/lib && \
        cp -v /tmp/libgstnvdsseimeta.so /opt/nvidia/deepstream/deepstream/lib/ && \
        chmod 755 /opt/nvidia/deepstream/deepstream/lib/libgstnvdsseimeta.so; \
    fi && rm -f /tmp/libgstnvdsseimeta.so

# Build the module (pass TARGETARCH to Makefile for platform-specific libs)
RUN cd /build/video_sei_metadata && TARGETARCH=${TARGETARCH} make

# Final docker stage
ARG TARGETARCH
FROM base-${TARGETARCH}
ARG TARGETARCH
ARG ARM_PLATFORM
ARG FORCE_REBUILD=1
RUN echo "Force rebuild = $FORCE_REBUILD"

ENV NVIDIA_DRIVER_CAPABILITIES=$NVIDIA_DRIVER_CAPABILITIES,video \
    HF_HOME=/tmp/huggingface \
    TORCH_CUDA_ARCH_LIST="7.5 8.0 8.6 8.7 9.0 10.0 11.0 12.0+PTX" \
    CUDA_CACHE_DISABLE=0 \
    GST_PLUGIN_PATH=/opt/nvidia/rtvi/gst-plugins:/opt/nvidia/deepstream/deepstream/lib/gst-plugins

# Set LD_LIBRARY_PATH to include DeepStream and RT Embed runtime libraries.
# Tegra libraries are added by the startup script only on Thor/Jetson; exposing
# them globally makes SBSA nvidia-smi load an incompatible driver library.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/dist-packages/nvidia/nvshmem/lib:/opt/hpcx/ucx/lib:/opt/nvidia/deepstream/deepstream/lib:/opt/nvidia/deepstream/deepstream-9.1/lib:/opt/nvidia/rtvi/lib:/usr/local/nvidia/lib64:/usr/local/cuda/targets/x86_64-linux/lib:${LD_LIBRARY_PATH}

RUN mkdir -p /tmp/huggingface

# Install RT Embed runtime dependencies and remove disallowed libraries.
RUN --mount=type=bind,source=docker/requirements_apt.txt,target=/tmp/requirements_apt.txt \
    --mount=type=bind,source=docker/ubuntu.sources.${TARGETARCH},target=/etc/apt/sources.list.d/ubuntu.sources \
    apt update \
    && apt install -y --no-install-recommends $(cat /tmp/requirements_apt.txt) \
    && packagekit_packages="$(dpkg-query -W -f='${binary:Package}\n' packagekit packagekit-tools 2>/dev/null || true)" \
    && if [ -n "$packagekit_packages" ]; then apt-get purge -y --auto-remove $packagekit_packages; fi \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/lib/*-linux-gnu/gstreamer-1.0/libgstaudioparsers.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstfaad.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvoaacenc.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstx264.so \
        /usr/lib/*-linux-gnu/libavresample* /usr/lib/*-linux-gnu/libavutil* \
        /usr/lib/*-linux-gnu/libavcodec* /usr/lib/*-linux-gnu/libavformat* \
        /usr/lib/*-linux-gnu/libavfilter* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstde265.so* \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstx265.so* /usr/lib/*-linux-gnu/libde265.so* \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvpx.so* \
        /usr/lib/*-linux-gnu/libmpeg2.so.0* /usr/lib/*-linux-gnu/libmpeg2encpp-2.1.so* /usr/lib/*-linux-gnu/libmpg123.so* \
        /usr/lib/*-linux-gnu/libx265.so* /usr/lib/*-linux-gnu/libx264.so* /usr/lib/*-linux-gnu/libvpx.so* \
        /usr/lib/*-linux-gnu/libmpeg2convert.so* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstopenh264.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstnvcodec.so /usr/lib/*-linux-gnu/gio/modules/libgiolibproxy.so \
        /usr/lib/*-linux-gnu/libdca.so* /usr/lib/*-linux-gnu/libdvdnav.so* \
        /usr/lib/*-linux-gnu/libdvdread.so* /usr/lib/*-linux-gnu/libfaad* \
        /usr/lib/*-linux-gnu/libFLAC.so* /usr/lib/*-linux-gnu/libjbig.so* \
        /usr/lib/*-linux-gnu/libmjpegutils-2.1.so* /usr/lib/*-linux-gnu/libmpeg2encpp-2.1.so* \
        /usr/lib/*-linux-gnu/libmplex2-2.1.so* /usr/lib/*-linux-gnu/libslang.so* \
        /usr/lib/*-linux-gnu/libgpm.so* \
        /usr/lib/*-linux-gnu/libxvidcore.so* /usr/lib/*-linux-gnu/libzvbi* \
        /usr/lib/*-linux-gnu/libvo-aacenc.so* /usr/lib/*-linux-gnu/libjack* \
        /usr/lib/*-linux-gnu/libswresample.so.3* /usr/lib/*-linux-gnu/libopenh264.so.* \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpegpsmux.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstuvch264.so \
        /usr/lib/*-linux-gnu/libmp3lame.so.0.0.0 /usr/lib/*-linux-gnu/libmp3lame.so.0 \
        /usr/lib/*-linux-gnu/mfx/libmfx_h264la_hw64.so /usr/lib/*-linux-gnu/gstreamer-1.0/libgstflac.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2enc.so /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2dec.so \
        /opt/tritonserver/backends/dali/wheel/dali/nvidia/dali/.libs/libFLAC-69eaf1ee.so.12 \
        /usr/share/icons/Adwaita \
        /usr/bin/gtk-update-icon-cache \
        /usr/sbin/update-icon-caches \
        /usr/share/icons/hicolor \
        /usr/share/icons/Humanity* \
        /usr/lib/*-linux-gnu/directfb-1.7-7 \
        /usr/lib/*-linux-gnu/libdirect*.so* \
        /usr/lib/*-linux-gnu/libfusion* \
        /usr/lib/*-linux-gnu/liblrdf.so* \
        /usr/lib/*-linux-gnu/libneon.so* \
        /usr/lib/*-linux-gnu/libpipewire* \
        /usr/lib/*-linux-gnu/libraptor2* \
        /usr/lib/*-linux-gnu/spa-0.2 \
        /usr/lib/*-linux-gnu/libunibreak* \
        /usr/lib/*-linux-gnu/libZXing* \
        /usr/lib/*-linux-gnu/libgallium* \
        /usr/share/icons/ubuntu-mono* \
        /usr/share/icons/LoginIcons

# Replace the distribution plugins with the DeepStream prebuilt RTSP fixes.
COPY --from=rtpmanager-prebuilt \
     /tmp99/libgstrtpmanager.so \
     /tmp99/libgstrtsp.so \
     /tmp99/libgstvideoparsersbad.so \
     /tmp/rtpmanager-plugins/
RUN case "$(uname -m)" in \
        aarch64) plugin_dir=/usr/lib/aarch64-linux-gnu/gstreamer-1.0 ;; \
        x86_64) plugin_dir=/usr/lib/x86_64-linux-gnu/gstreamer-1.0 ;; \
        *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac \
    && test -d "$plugin_dir" \
    && install -m 0644 /tmp/rtpmanager-plugins/libgstrtpmanager.so \
        /tmp/rtpmanager-plugins/libgstrtsp.so \
        /tmp/rtpmanager-plugins/libgstvideoparsersbad.so "$plugin_dir/" \
    && rm -rf /tmp/rtpmanager-plugins

RUN apt remove libslurm40t64 libpmi2-0t64 -y --purge && apt autoremove --purge -y

# Register the x86 DeepStream libv4l CUVID plugin after the final apt layer has
# created the system libv4l plugin directory.
RUN if [ "$TARGETARCH" = "amd64" ]; then \
        mkdir -p /usr/lib/x86_64-linux-gnu/libv4l/plugins && \
        update-alternatives --install \
            /usr/lib/x86_64-linux-gnu/libv4l/plugins/libcuvidv4l2_plugin.so \
            deepstream-v4l2plugin \
            /opt/nvidia/deepstream/deepstream-9.1/lib/libv4l/plugins/libcuvidv4l2_plugin.so \
            91; \
    fi

RUN if [ "$TARGETARCH" = "amd64" ]; then \
        mkdir -p /opt/nvidia/rtvi/warmup_streams && \
        cp /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4 /opt/nvidia/rtvi/warmup_streams/ && \
        cp /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h265.mp4 /opt/nvidia/rtvi/warmup_streams/ && \
        rm -rf /opt/nvidia/deepstream/deepstream/samples/ && \
        rm -rf /opt/nvidia/deepstream/deepstream/sources/ && \
        rm -rd /opt/nvidia/deepstream/deepstream/bin && \
        rm -rf /opt/nvidia/deepstream/deepstream/service-maker/sources/; \
    else \
        mkdir -p /opt/nvidia/rtvi/warmup_streams && \
        cp /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4 /opt/nvidia/rtvi/warmup_streams/ && \
        cp /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h265.mp4 /opt/nvidia/rtvi/warmup_streams/; \
    fi

# Avoid warnings about missing plugins
RUN rm -f /usr/lib/x86_64-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_udp.so && \
    rm -f /usr/lib/x86_64-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_nvmultiurisrcbin.so && \
    rm -f /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstlame.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstsndfile.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstteletext.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstzxing.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstresindvd.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstassrender.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstdtsdec.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstladspa.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstmpg123.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstopenmpt.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstspandsp.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstpulseaudio.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstaasink.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstneonhttpsrc.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstfluidsynthmidi.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstcacasink.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstdirectfb.so \
    /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstmplex.so

RUN --mount=type=bind,source=docker/binaries/,target=/tmp/binaries \
    if [ "$TARGETARCH" = "arm64" ] && [ "$ARM_PLATFORM" = "igpu" ]; then \
        mkdir -p /opt/nvidia/rtvi/lib && \
        cp /tmp/binaries/igpu/libnvbufsurface.so /opt/nvidia/rtvi/lib/libnvbufsurface.so && \
        cp /tmp/binaries/igpu/libnvbufsurface.so /opt/nvidia/rtvi/lib/libnvbufsurface.so.1.0.0 && \
        cp /tmp/binaries/igpu/libnvbufsurftransform.so /opt/nvidia/rtvi/lib/libnvbufsurftransform.so && \
        cp /tmp/binaries/igpu/libnvbufsurftransform.so /opt/nvidia/rtvi/lib/libnvbufsurftransform.so.1.0.0; \
    fi

RUN --mount=type=bind,from=generate-requirements,source=/tmp/python/requirements.txt,target=/tmp/requirements.txt \
    --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://pypi.org/simple -r /tmp/requirements.txt --no-deps --ignore-installed \
    && rm -f /usr/local/lib/python3.12/dist-packages/decord.libs/libx264*

RUN --mount=type=bind,from=pytorch_src,source=/usr/local/lib/python3.12/dist-packages,target=/tmp/pytorch_src \
    /bin/bash -lc 'set -e; shopt -s nullglob; \
    rm -rf /usr/local/lib/python3.12/dist-packages/flash_attn* \
        /usr/local/lib/python3.12/dist-packages/triton \
        /usr/local/lib/python3.12/dist-packages/triton-*.dist-info; \
    cp -r /tmp/pytorch_src/{torch,functorch,torchgen,torch-*,torchvision*,flash_attn*,tensorrt*,triton,triton-*.dist-info,pytorch_triton-*} \
        /usr/local/lib/python3.12/dist-packages/; \
    if [ "$TARGETARCH" = "arm64" ]; then \
        sed -i "/self.capability = target.arch/a\\        if self.capability == 121: self.capability = 120" /usr/local/lib/python3.12/dist-packages/triton/backends/nvidia/compiler.py; \
    fi'

COPY --from=pytorch_src /usr/local/lib/libnvpl* /usr/local/lib/
COPY --from=pyds-wheel /tmp/pyds-arm64 /tmp/pyds-arm64

RUN if [ "$TARGETARCH" = "amd64" ]; then \
        pyds_url=https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.2.2/pyds-1.2.2-cp312-cp312-linux_x86_64.whl; \
        pyds_wheel_path=/tmp/pyds-1.2.2-cp312-cp312-linux_x86_64.whl; \
        pyds_sha256=74e13a6431cbef66b7a27da08658e0e30e7ca84bccb00a25a1a9c969608d3088; \
        curl --fail --location --output "$pyds_wheel_path" "$pyds_url" \
        && echo "$pyds_sha256  $pyds_wheel_path" | sha256sum -c - \
        && pip install "$pyds_wheel_path" --no-deps \
        && rm -f "$pyds_wheel_path"; \
    else \
        pyds_wheel_path="$(find /tmp/pyds-arm64 -maxdepth 1 -name 'pyds-*.whl' -print -quit)"; \
        test -n "$pyds_wheel_path" \
        && pip install "$pyds_wheel_path" --no-deps \
        && rm -rf /tmp/pyds-arm64; \
    fi

COPY LICENSE.3rdparty /opt/nvidia/rtvi/

RUN --mount=type=bind,src=src/cpp/nvdec_get_count,target=/tmp/nvdec_get_count \
    cp -r /tmp/nvdec_get_count /tmp/nvdec_get_count_rw && \
    make install -C /tmp/nvdec_get_count_rw && rm -rf /tmp/nvdec_get_count_rw

# Install GStreamer SEI metadata Python bindings (copy from build stage)
COPY --from=video_sei_metadata_builder /build/video_sei_metadata/gst_video_sei_meta*.so /usr/local/lib/python3.12/dist-packages/

# The ARM DeepStream base does not provide this library. It is required by the
# GStreamer SEI metadata Python binding built above.
RUN if [ "$TARGETARCH" = "arm64" ]; then mkdir -p /opt/nvidia/deepstream/deepstream/lib; fi
COPY docker/binaries/igpu/libgstnvdsseimeta.so /tmp/libgstnvdsseimeta.so
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        cp -v /tmp/libgstnvdsseimeta.so /opt/nvidia/deepstream/deepstream/lib/ && \
        chmod 755 /opt/nvidia/deepstream/deepstream/lib/libgstnvdsseimeta.so; \
    fi && rm -f /tmp/libgstnvdsseimeta.so

# Install GStreamer timestamp filter plugin
RUN --mount=type=bind,from=generate-requirements,source=/tmp/gst-plugins/libgsttimestampfilter.so,target=/tmp/libgsttimestampfilter.so \
    MULTIARCH=$([ "$TARGETARCH" = "amd64" ] && echo x86_64-linux-gnu || echo aarch64-linux-gnu) && \
    cp /tmp/libgsttimestampfilter.so /usr/lib/${MULTIARCH}/gstreamer-1.0/

RUN mkdir -p /home/triton-server /var/log/rtvi \
    && chmod -R +r /opt/nvidia/rtvi/warmup_streams /home/triton-server /var/log/rtvi

# Embed application layers, appended directly to the combined base stage.
ARG NSPECT_ID="NSPECT-KNMC-UF4J"
LABEL com.nvidia.mm.nspect="${NSPECT_ID}"

# Server containers do not need PackageKit's D-Bus package-management service.
# Remove it if an inherited base image includes it (CVE-2026-41651).
RUN packagekit_packages="$(dpkg-query -W -f='${binary:Package}\n' packagekit packagekit-tools 2>/dev/null || true)" \
    && if [ -n "$packagekit_packages" ]; then apt-get purge -y --auto-remove $packagekit_packages; fi \
    && rm -rf /var/lib/apt/lists/*

# Copy the complete open-source RTVI Embed tree for both runtime modes.
# MODE=release imports from /opt/nvidia/rtvi/rtvi; MODE=development imports
# from /opt/nvidia/rtvi/src.
COPY src/ /opt/nvidia/rtvi/rtvi/
COPY src/ /opt/nvidia/rtvi/src/
RUN mkdir -p /opt/mm/

# Install transformers==4.52.4 needed by Cosmos-Embed1-448p model
RUN pip install --index-url https://pypi.org/simple transformers==4.52.4 >/dev/null

# DLFW 26.03 carries Torch 2.11 while onnxscript 0.5.7 has shims up to Torch 2.9.
# Cosmos-Embed1 model repo generation imports the Torch-versioned onnxscript shim.
RUN python3 -c "from pathlib import Path; \
p=Path('/usr/local/lib/python3.12/dist-packages/onnxscript/_framework_apis'); \
src=p/'torch_2_9.py'; dst=p/'torch_2_11.py'; \
src.exists() or (_ for _ in ()).throw(FileNotFoundError(f'Missing onnxscript shim source: {src}')); \
dst.exists() or dst.write_text(src.read_text())"

COPY src/scripts/start_rtvi_embed.sh /opt/nvidia/rtvi/
COPY src/scripts/install_codecs_nonroot.sh /opt/nvidia/rtvi/

# Ship the NVIDIA Software License Agreement alongside the source tree (WORKDIR).
COPY docker/NVIDIA-Software-License-Agreement.pdf /opt/nvidia/rtvi/

# To allow helm chart start script to copy updated config:
ENV PATH=$PATH:/opt/nvidia/rtvi/
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/opt/tritonserver/lib/
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/lib/x86_64-linux-gnu/
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/dist-packages/nvidia/nvshmem/lib/:$LD_LIBRARY_PATH

# Embed does not use PyAV; remove FFmpeg LGPL runtime libraries if the base image ships them under /usr.
# Use rm -rf (not find -delete): arm64 base images also ship share/doc/libav* directories.
# This also clears the FFmpeg libs bundled inside Python wheels under
# /usr/local/lib/python3.12/dist-packages/ (opencv_python*.libs) -
# libavdevice/libswresample/libswscale in addition to libav{codec,format,filter,util}.
# libmfx* covers the Intel Media SDK libs (libmfx, libmfx-tracer, libmfxhw64) under
# /usr/lib, and libavif* covers the AV1 codec bundled in pillow.libs.
# libgstmsdk* drops the GStreamer Intel Media SDK (QSV) plugin, which is unused on
# NVIDIA hardware (decode goes through NVDEC) and otherwise logs a load warning once
# libmfx is removed.
# Also remove the FFmpeg libs bundled in the DALI wheel under /opt/tritonserver
# (e.g. .libs/libavcodec-<hash>.so.61).
RUN cd /usr && find . -depth \( -name 'libavcodec*' -o -name 'libavformat*' -o -name 'libavfilter*' -o -name 'libavutil*' -o -name 'libavdevice*' -o -name 'libswresample*' -o -name 'libswscale*' -o -name 'libmfx*' -o -name 'libavif*' -o -name 'libgstmsdk*' \) -exec rm -rf {} + \
    ; find /opt/tritonserver/backends/dali \( -name 'libavcodec*' -o -name 'libavformat*' -o -name 'libavfilter*' -o -name 'libavutil*' -o -name 'libavdevice*' -o -name 'libswresample*' -o -name 'libswscale*' \) -type f -delete 2>/dev/null || true

# Create user with UID/GID 1001 for non-root execution and set permissions.
# install_codecs_nonroot.sh runs apt-get update at startup using user-owned state dirs,
# so build-time apt lists are not needed and are cleaned up to keep the image smaller.
RUN groupadd --gid 1001 --non-unique vss && \
    useradd --create-home --shell /usr/sbin/nologin --uid 1001 --non-unique --gid 1001 vss && \
    chown -R 1001:1001 /opt/nvidia/rtvi/ && chmod -R 755 /opt/nvidia/rtvi/ && chmod -R 755 /opt/mm/ && \
    mkdir -p /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache /opt/nvidia/rtvi/codecs && \
    chown -R 1001:1001 /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache /opt/nvidia/rtvi/codecs && \
    chmod -R 755 /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache

USER vss:1001

WORKDIR /opt/nvidia/rtvi/

ENTRYPOINT ["/opt/nvidia/rtvi/start_rtvi_embed.sh"]

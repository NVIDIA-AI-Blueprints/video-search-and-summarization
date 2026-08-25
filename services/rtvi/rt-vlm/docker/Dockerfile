######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
######################################################################################################

# NVIDIA does not publish an installable DeepStream SDK artifact for SBSA.
# Keep its official container only as a source stage; the final runtime below
ARG TARGETARCH
ARG SBSA_MEDIA_IMAGE="nvcr.io/nvidia/deepstream:9.1-triton-sbsa-dgx-spark@sha256:6a25ce5964424a2fc18a67212b08fc3a844734e757e8cc2a989f033e3306b1af"
ARG DS_MEDIA_IMAGE="nvcr.io/nvidia/deepstream:9.1-triton-multiarch@sha256:fd31f5b44ababdbdee8cd397a375e888191b49e402ac237254a4cdc239130f5b"
ARG TRITON_CUDA_IMAGE="nvcr.io/nvidia/tritonserver:26.03-py3@sha256:72d1d796b49a17e6432d4908c95969be0dded2f4d06469fa0824a71ecf0b895b"
ARG VLLM_IMAGE="nvcr.io/nvidia/vllm:26.03-py3@sha256:13e327dad79e6e417f6687fec2ba76b0386d597082ec0ee003c1e964ec6ad0e7"
ARG PDM_EXPORT_IMAGE="python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a"
ARG MEDIA_PLATFORM="ds"
ARG DEEPSTREAM_X86_SHA256="fcafb7b5e4fbdf38b752eb35807011b69b14af826d6807180288d4d3d9b1ecbc"
ARG DEEPSTREAM_JETSON_SHA256="14813cb7da9eafdd01ceed52c1c8d85a3255612a9b1c2691f5ad3ad5c5d39c2e"
FROM ${PDM_EXPORT_IMAGE} AS pdm-export
ARG TARGETARCH
COPY docker/rtvi_vlm/py_deps/pdm-build-requirements.txt /tmp/pdm-build-requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/pdm-build-requirements.txt
WORKDIR /tmp/pdm
COPY docker/rtvi_vlm/py_deps/pyproject.toml docker/rtvi_vlm/py_deps/pdm.lock ./
RUN pdm export -G ${TARGETARCH} -o /tmp/rtvi-requirements.txt

# Media source stages use the build arguments declared before the first FROM.
FROM ${DS_MEDIA_IMAGE} AS media-runtime-ds
FROM ${DS_MEDIA_IMAGE} AS media-runtime-igpu
FROM ${SBSA_MEDIA_IMAGE} AS media-runtime-sbsa
FROM media-runtime-${MEDIA_PLATFORM} AS media-runtime
FROM ${SBSA_MEDIA_IMAGE} AS sbsa-media-runtime

# DeepStream 9.1's x86 plugins require CUDA 13 NPP/NVJPEG libraries. They are
# present in Triton 26.03 but intentionally absent from the lean vLLM image.
FROM ${TRITON_CUDA_IMAGE} AS triton-cuda-runtime

# The final foundation for every VLM target. NGC resolves the matching amd64 or
# arm64 manifest at build time.
FROM ${VLLM_IMAGE} AS rtvi-media

ARG TARGETARCH
ARG ARM_PLATFORM="sbsa"
ARG DEEPSTREAM_X86_SHA256
ARG DEEPSTREAM_JETSON_SHA256

LABEL com.nvidia.rtvi.base="vlm"

ENV NVIDIA_DRIVER_CAPABILITIES=$NVIDIA_DRIVER_CAPABILITIES,video \
    HF_HOME=/tmp/huggingface \
    TORCH_CUDA_ARCH_LIST="7.5 8.0 8.6 8.7 9.0 10.0 11.0 12.0+PTX" \
    CUDA_CACHE_DISABLE=0 \
    TRITON_CUDACRT_PATH=/usr/local/cuda/include \
    TRITON_CUDART_PATH=/usr/local/cuda/include \
    TRITON_CUOBJDUMP_PATH=/usr/local/cuda/bin/cuobjdump \
    TRITON_CUPTI_PATH=/usr/local/cuda/include \
    TRITON_NVDISASM_PATH=/usr/local/cuda/bin/nvdisasm \
    TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas \
    GST_PLUGIN_PATH=/opt/nvidia/rtvi/gst-plugins:/opt/nvidia/deepstream/deepstream/lib/gst-plugins \
    LD_LIBRARY_PATH=/opt/nvidia/rtvi/lib:/usr/local/lib/python3.12/dist-packages/nvidia/nvshmem/lib:/opt/hpcx/ucx/lib:/opt/nvidia/deepstream/deepstream/lib:/opt/nvidia/deepstream/deepstream-9.1/lib:/usr/local/nvidia/lib64:/usr/local/cuda/targets/x86_64-linux/lib:${LD_LIBRARY_PATH}

# Install only RTVI runtime dependencies directly on vLLM. Native build
# dependencies are confined to pyds-builder and native-builder below.
RUN --mount=type=bind,source=docker/base/requirements_apt.txt,target=/tmp/requirements_apt.txt \
    apt update \
    && apt install -y --no-install-recommends $(cat /tmp/requirements_apt.txt) \
    && rm -rf /var/lib/apt/lists/*

# x86 and Jetson use public DeepStream 9.1 GitHub release assets. SBSA has no
# package artifact, so its DeepStream payload is copied from the official SBSA
# image in the next layer.
RUN if [ "$TARGETARCH" = "amd64" ]; then \
        curl -fL --retry 3 -o /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2 \
            https://github.com/NVIDIA/DeepStream/releases/download/v9.1.0/deepstream_sdk_v9.1.0_x86_64.tbz2 \
        && echo "${DEEPSTREAM_X86_SHA256}  /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2" | sha256sum -c - \
        && tar xf /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2 -C / \
        && /opt/nvidia/deepstream/deepstream-9.1/install.sh \
        && rm /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2; \
    elif [ "$ARM_PLATFORM" = "igpu" ]; then \
        curl -fL --retry 3 -o /tmp/deepstream_sdk_v9.1.0_jetson.tbz2 \
            https://github.com/NVIDIA/DeepStream/releases/download/v9.1.0/deepstream_sdk_v9.1.0_jetson.tbz2 \
        && echo "${DEEPSTREAM_JETSON_SHA256}  /tmp/deepstream_sdk_v9.1.0_jetson.tbz2" | sha256sum -c - \
        && tar xf /tmp/deepstream_sdk_v9.1.0_jetson.tbz2 -C / \
        && /opt/nvidia/deepstream/deepstream-9.1/install.sh \
        && rm /tmp/deepstream_sdk_v9.1.0_jetson.tbz2; \
    fi

RUN --mount=from=sbsa-media-runtime,source=/,target=/tmp/sbsa-root,readonly \
    if [ "$TARGETARCH" = "arm64" ] && [ "$ARM_PLATFORM" = "sbsa" ]; then \
        # DeepStream SBSA libraries under /opt use absolute links into tegra.
        mkdir -p /opt/nvidia/deepstream /usr/lib/aarch64-linux-gnu/tegra \
        && cp -a /tmp/sbsa-root/opt/nvidia/deepstream/. /opt/nvidia/deepstream/ \
        && cp -a /tmp/sbsa-root/usr/lib/aarch64-linux-gnu/tegra/. /usr/lib/aarch64-linux-gnu/tegra/; \
    fi

# Apply the shared base image's GPL/media removal policy. DeepStream's required
# runtime remains under /opt/nvidia/deepstream and is not part of this cleanup.
RUN rm -rf /usr/lib/*-linux-gnu/gstreamer-1.0/libgstaudioparsers.so \
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
        /usr/lib/*-linux-gnu/libmjpegutils-2.1.so* /usr/lib/*-linux-gnu/libmpeg2encpp-2.1.so* /usr/lib/*-linux-gnu/libmplex2-2.1.so* /usr/lib/*-linux-gnu/libslang.so* \
        /usr/lib/*-linux-gnu/libxvidcore.so* /usr/lib/*-linux-gnu/libzvbi* \
        /usr/lib/*-linux-gnu/libvo-aacenc.so* /usr/lib/*-linux-gnu/libjack* \
        /usr/lib/*-linux-gnu/libswresample.so.3* /usr/lib/*-linux-gnu/libopenh264.so.* \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpegpsmux.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstuvch264.so \
        /usr/lib/*-linux-gnu/libmp3lame.so.0.0.0 /usr/lib/*-linux-gnu/libmp3lame.so.0 \
        /usr/lib/*-linux-gnu/mfx/libmfx_h264la_hw64.so /usr/lib/*-linux-gnu/gstreamer-1.0/libgstflac.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2enc.so /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2dec.so \
        /opt/tritonserver/backends/dali/wheel/dali/nvidia/dali/.libs/libFLAC-69eaf1ee.so.12 \
        /usr/share/icons/Adwaita /usr/bin/gtk-update-icon-cache /usr/sbin/update-icon-caches \
        /usr/share/icons/hicolor /usr/share/icons/Humanity* \
        /usr/lib/*-linux-gnu/directfb-1.7-7 /usr/lib/*-linux-gnu/libdirect*.so* \
        /usr/lib/*-linux-gnu/libfusion* /usr/lib/*-linux-gnu/liblrdf.so* \
        /usr/lib/*-linux-gnu/libneon.so* /usr/lib/*-linux-gnu/libpipewire* \
        /usr/lib/*-linux-gnu/libraptor2* /usr/lib/*-linux-gnu/spa-0.2 \
        /usr/lib/*-linux-gnu/libunibreak* /usr/lib/*-linux-gnu/libZXing* \
        /usr/lib/*-linux-gnu/libgallium* /usr/share/icons/ubuntu-mono* /usr/share/icons/LoginIcons \
    && packagekit_packages="$(dpkg-query -W -f='${binary:Package}\n' packagekit packagekit-tools 2>/dev/null || true)" \
    && if [ -n "$packagekit_packages" ]; then apt-get purge -y --auto-remove $packagekit_packages; fi \
    && slurm_packages="$(dpkg-query -W -f='${binary:Package}\n' libslurm40t64 libpmi2-0t64 2>/dev/null || true)" \
    && if [ -n "$slurm_packages" ]; then apt-get purge -y --auto-remove $slurm_packages; fi \
    && apt-get autoremove --purge -y \
    && rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_udp.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_nvmultiurisrcbin.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstlame.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstsndfile.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstteletext.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstzxing.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstresindvd.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstassrender.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstdtsdec.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstladspa.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpg123.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstopenmpt.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstspandsp.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstpulseaudio.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstaasink.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstneonhttpsrc.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstfluidsynthmidi.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstcacasink.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstdirectfb.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmplex.so

# The SBSA DeepStream image supplies its native hardware decoder as a system
# GStreamer plugin rather than under /opt. Preserve it after the shared media
# cleanup so H.264 decoding does not depend on the proprietary codec overlay.
RUN --mount=from=sbsa-media-runtime,source=/,target=/tmp/sbsa-root,readonly \
    if [ "$TARGETARCH" = "arm64" ] && [ "$ARM_PLATFORM" = "sbsa" ]; then \
        source_plugin=/tmp/sbsa-root/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstnvvideo4linux2.so; \
        test -s "$source_plugin"; \
        install -Dm755 "$source_plugin" /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstnvvideo4linux2.so; \
    fi

# The Jetson SDK archive omits the two NvBuf runtime libraries required by pyds.
# Use the portable iGPU copies rather than driver-mounted Tegra symlinks.
COPY docker/base/binaries/igpu/libnvbufsurface.so docker/base/binaries/igpu/libnvbufsurftransform.so /tmp/igpu-nvbuf/
RUN if [ "$TARGETARCH" = "arm64" ] && [ "$ARM_PLATFORM" = "igpu" ]; then \
        ds_path=/opt/nvidia/deepstream/deepstream-9.1/lib \
        && install -Dm755 /tmp/igpu-nvbuf/libnvbufsurface.so "$ds_path/libnvbufsurface.so" \
        && install -Dm755 /tmp/igpu-nvbuf/libnvbufsurftransform.so "$ds_path/libnvbufsurftransform.so"; \
    fi \
    && rm -rf /tmp/igpu-nvbuf
COPY --from=triton-cuda-runtime /usr/local/cuda/targets /tmp/triton-cuda-targets
RUN set -e; if [ "$TARGETARCH" = "amd64" ]; then \
        cp -a /tmp/triton-cuda-targets/x86_64-linux/lib/libnpp*.so* /usr/local/cuda/targets/x86_64-linux/lib/ \
        && cp -a /tmp/triton-cuda-targets/x86_64-linux/lib/libnvjpeg.so* /usr/local/cuda/targets/x86_64-linux/lib/; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
        for library in /tmp/triton-cuda-targets/aarch64-linux/lib/libnpp*.so* /tmp/triton-cuda-targets/aarch64-linux/lib/libnvjpeg.so*; do \
            if [ -e "$library" ]; then cp -a "$library" /usr/local/cuda/targets/aarch64-linux/lib/; fi; \
        done; \
    fi \
    && rm -rf /tmp/triton-cuda-targets \
    && ldconfig

FROM rtvi-media AS pyds-builder
ARG TARGETARCH
ARG ARM_PLATFORM
ARG PYDS_SOURCE_COMMIT="6fdeefb7128435873f7794d2242ed48a1471ad7e"
COPY docker/base/pyds.patch /tmp/pyds.patch
RUN apt update \
    && apt install -y --no-install-recommends \
        autoconf automake build-essential cmake g++ git libglib2.0-dev \
        libglib2.0-dev-bin libgirepository-2.0-dev libtool m4 meson ninja-build \
        python3-gi python3-gst-1.0 python-gi-dev python3-venv python3.12-dev \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev pkg-config pybind11-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git init /tmp/deepstream_python_apps \
    && cd /tmp/deepstream_python_apps \
    && git remote add origin https://github.com/NVIDIA-AI-IOT/deepstream_python_apps.git \
    && git fetch --depth 1 origin "${PYDS_SOURCE_COMMIT}" \
    && git checkout --detach --force FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${PYDS_SOURCE_COMMIT}" \
    && git submodule update --init \
    && python3 bindings/3rdparty/git-partial-submodule/git-partial-submodule.py restore-sparse \
    && git apply /tmp/pyds.patch
RUN pip install --no-cache-dir build==1.5.0 PyGObject==3.48.2 cuda-python==13.2.0 \
    && cd /tmp/deepstream_python_apps/bindings/3rdparty/gstreamer/subprojects/gst-python \
    && meson setup build \
    && ninja -C build \
    && ninja -C build install
RUN ds_path=/opt/nvidia/deepstream/deepstream-9.1; \
    if [ ! -d "$ds_path" ]; then ds_path=/opt/nvidia/deepstream/deepstream; fi; \
    cmake_args="-DDS_PATH=$ds_path"; \
    if [ "$TARGETARCH" = "arm64" ] && [ "$ARM_PLATFORM" = "sbsa" ]; then cmake_args="$cmake_args -DIS_SBSA=on"; fi; \
    cd /tmp/deepstream_python_apps/bindings \
    && CMAKE_ARGS="$cmake_args" CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)" python3 -m build \
    && mkdir -p /tmp/pyds-wheel \
    && cp dist/pyds-*.whl /tmp/pyds-wheel/

FROM pyds-builder AS native-builder
ARG TARGETARCH
COPY src/cpp/nvdec_get_count /build/nvdec_get_count
COPY src/gst-plugins /build/gst-plugins
COPY src/cpp/video_sei_metadata /build/video_sei_metadata
RUN pip install --no-cache-dir pybind11==3.1.0 \
    && if [ ! -f /usr/bin/python3-config ] && [ -f /usr/bin/python3.12-config ]; then ln -sf /usr/bin/python3.12-config /usr/bin/python3-config; fi \
    && make -C /build/nvdec_get_count \
    && make -C /build/gst-plugins \
    && make -C /build/video_sei_metadata TARGETARCH=${TARGETARCH} \
    && mkdir -p /opt/native-artifacts \
    && install -Dm755 /build/nvdec_get_count/nvdec_get_count /opt/native-artifacts/nvdec_get_count \
    && install -Dm755 /build/gst-plugins/libgsttimestampfilter.so /opt/native-artifacts/libgsttimestampfilter.so \
    && install -Dm755 /build/video_sei_metadata/gst_video_sei_meta*.so /opt/native-artifacts/

FROM rtvi-media AS runtime
ARG TARGETARCH
ARG ARM_PLATFORM
RUN --mount=from=pyds-builder,source=/tmp/pyds-wheel,target=/tmp/pyds-wheel,readonly \
    pip install --no-deps /tmp/pyds-wheel/pyds-*.whl

# Keep the manifest in the image filesystem so its contents are part of the
# Docker cache key. A BuildKit bind mount is intentionally excluded from that
# key, which previously allowed this layer to be reused after dependency
# changes and produced VLM images missing RTVI packages.
COPY --from=pdm-export /tmp/rtvi-requirements.txt /tmp/rtvi-requirements.txt

# Install RTVI's pinned dependencies. The manifest deliberately excludes the
# vLLM, xFormers, and FlashInfer packages owned by the NVIDIA base image.
# OpenCV cannot function without the intentionally excluded proprietary codecs.
# Remove the copies inherited from vLLM to prevent failed imports from polluting sys.path.
RUN --mount=type=cache,target=/root/.cache/pip \
    env -u PIP_CONSTRAINT -u PIP_BUILD_CONSTRAINT \
        pip install --no-deps --require-hashes -r /tmp/rtvi-requirements.txt \
    && pip uninstall -y opencv-python opencv-python-headless \
    && rm -f /usr/local/lib/python3.12/dist-packages/decord.libs/libx264* \
    && rm -f /tmp/rtvi-requirements.txt

# Install patched and RTVI-specific GStreamer plugins without retaining their
# source trees in the runtime image.
RUN --mount=from=media-runtime,source=/tmp99,target=/tmp/ds-gst,readonly \
    --mount=from=native-builder,source=/opt/native-artifacts/libgsttimestampfilter.so,target=/tmp/libgsttimestampfilter.so,readonly \
    multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH) \
    && install -Dm755 /tmp/ds-gst/libgstrtpmanager.so /usr/lib/${multiarch}/gstreamer-1.0/libgstrtpmanager.so \
    && install -Dm755 /tmp/ds-gst/libgstrtsp.so /usr/lib/${multiarch}/gstreamer-1.0/libgstrtsp.so \
    && install -Dm755 /tmp/ds-gst/libgstvideoparsersbad.so /usr/lib/${multiarch}/gstreamer-1.0/libgstvideoparsersbad.so \
    && install -Dm755 /tmp/libgsttimestampfilter.so /usr/lib/${multiarch}/gstreamer-1.0/libgsttimestampfilter.so

# Native extensions are built outside the runtime image.
COPY LICENSE.3rdparty /opt/nvidia/rtvi/
RUN --mount=from=native-builder,source=/opt/native-artifacts/nvdec_get_count,target=/tmp/nvdec_get_count,readonly \
    install -Dm755 /tmp/nvdec_get_count /usr/bin/nvdec_get_count

RUN --mount=from=native-builder,source=/opt/native-artifacts,target=/tmp/native-artifacts,readonly \
    install -Dm755 /tmp/native-artifacts/gst_video_sei_meta*.so /usr/local/lib/python3.12/dist-packages/

COPY --from=media-runtime /opt/nvidia/deepstream/deepstream-9.1/samples/streams/sample_1080p_h264.mp4 /opt/nvidia/rtvi/warmup_streams/its_264.mp4
COPY --from=media-runtime /opt/nvidia/deepstream/deepstream-9.1/samples/streams/sample_1080p_h265.mp4 /opt/nvidia/rtvi/warmup_streams/its_265.mp4

RUN mkdir -p /opt/nvidia/rtvi/warmup_streams /tmp/huggingface /home/triton-server /var/log/rtvi \
    && chmod -R +r /opt/nvidia/rtvi/warmup_streams /home/triton-server /var/log/rtvi \
    && pip install --no-cache-dir pytest==8.1.1 \
    && python3 -c 'import importlib.metadata as metadata; expected = {"vllm": "0.17.1+a03ca76a.nv26.3.46967107.cu132", "xformers": "0.0.33+nv26.3.46967107", "flashinfer-python": "0.6.7+a79fb63c.nv26.3.cu132.46951218", "flashinfer-cubin": "0.6.7+a79fb63c.nv26.3.cu132.46951218", "flashinfer-jit-cache": "0.6.7+a79fb63c.nv26.3.cu132.46951218"}; actual = {name: metadata.version(name) for name in expected}; assert actual == expected, actual'
ARG TARGETARCH
ARG NSPECT_ID="NSPECT-HTA3-YQL0"
LABEL com.nvidia.mm.nspect="${NSPECT_ID}"

# Server containers do not need PackageKit's D-Bus package-management service.
# Remove it if an inherited base image includes it (CVE-2026-41651).
RUN packagekit_packages="$(dpkg-query -W -f='${binary:Package}\n' packagekit packagekit-tools 2>/dev/null || true)" \
    && if [ -n "$packagekit_packages" ]; then \
        apt-get purge -y --auto-remove $packagekit_packages; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Copy only the runtime source files listed for this image.
RUN --mount=type=bind,source=src,target=/tmp/rtvi-src/src \
    --mount=type=bind,source=docker,target=/tmp/rtvi-src/docker \
    set -eu; \
    while IFS= read -r file; do \
        case "$file" in ''|'#'*) continue ;; esac; \
        destination="/opt/nvidia/rtvi/rtvi/$file"; \
        mkdir -p "$(dirname "$destination")"; \
        cp -v "/tmp/rtvi-src/src/$file" "$destination"; \
    done < /tmp/rtvi-src/docker/rtvi_vlm/package_file_list.txt; \
    cp -v /tmp/rtvi-src/src/cli/rtvi_client_cli.py /opt/nvidia/rtvi/rtvi_client_cli.py; \
    mkdir -p /opt/mm/

COPY start_rtvi_vlm.sh /opt/nvidia/rtvi/
COPY src/scripts/install_codecs_nonroot.sh /opt/nvidia/rtvi/

# Apply vLLM arch patch: registers NemotronH_Nano_Omni_Reasoning_V3 (GA checkpoint)
# in the installed vLLM model registry and config, mapping it to the same executor
# as NemotronH_Nano_VL_V2 (identical model structure, only the name changed EA→GA).
RUN --mount=type=bind,source=docker,target=/tmp/rtvi-src/docker \
    python3 /tmp/rtvi-src/docker/rtvi_vlm/patches/apply_vllm_arch_patch.py

# EVS++ is shipped as prebuilt architecture-specific extensions below. Do not
# copy its Python overlay into this public build; install the compiled modules
# directly and retain the independent tensor-IPC patch.
RUN --mount=type=bind,source=docker,target=/tmp/rtvi-src/docker \
    python3 /tmp/rtvi-src/docker/rtvi_vlm/patches/apply_vllm_tensor_ipc_patch.py

COPY artifacts/vss-rt-vlm-3.3.0-26.07.4-x86-evs-cython/ /tmp/evs-libs/x86/
COPY artifacts/vss-rt-vlm-3.3.0-26.07.4-arm-evs-cython/ /tmp/evs-libs/arm/
RUN set -eu; \
    case "$TARGETARCH" in amd64) evs_arch=x86 ;; arm64) evs_arch=arm ;; *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; esac; \
    source_root="/tmp/evs-libs/$evs_arch/vllm"; \
    vllm_root=/usr/local/lib/python3.12/dist-packages/vllm; \
    test "$(find "$source_root" -type f -name "*.so" | wc -l)" -eq 11; \
    case "$TARGETARCH" in amd64) elf_machine='Advanced Micro Devices X86-64' ;; arm64) elf_machine='AArch64' ;; esac; \
    find "$source_root" -type f -name "*.so" -print | while IFS= read -r library; do \
        readelf -h "$library" | grep -Fq 'Class:                             ELF64'; \
        readelf -h "$library" | grep -Fq "Machine:                           $elf_machine"; \
        relative="${library#$source_root/}"; \
        install -Dm755 "$library" "$vllm_root/$relative"; \
        rm -f "$vllm_root/${relative%%.cpython-312-*}.py"; \
    done; \
    rm -rf /tmp/evs-libs

# Remove unneeded media libraries shipped under /usr and DALI-bundled FFmpeg
# libraries under /opt/tritonserver (e.g. hashed shared objects inside the DALI wheel).
RUN cd /usr && find . \( -name 'libavcodec*' -o -name 'libavfilter*' -o -name 'libmfx*' \
        -o -name 'libgpm.so*' \
        -o -name 'libavif*' -o -name 'libgstmsdk*' \) -type f -delete \
    && find /opt/tritonserver/backends/dali \( -name 'libavcodec*' -o -name 'libavformat*' \
        -o -name 'libavfilter*' -o -name 'libavutil*' -o -name 'libavdevice*' \
        -o -name 'libswresample*' -o -name 'libswscale*' \) -type f -delete 2>/dev/null || true
# Create user with UID/GID 1001 for non-root execution and set permissions.
# install_codecs_nonroot.sh runs apt-get update at startup using user-owned state dirs,
# so build-time apt lists are not needed and are cleaned up to keep the image smaller.
RUN groupadd --gid 1001 --non-unique vss && \
    useradd --create-home --shell /bin/bash --uid 1001 --non-unique --gid 1001 vss && \
    chown -R 1001:1001 /opt/nvidia/rtvi/ && chmod -R 755 /opt/nvidia/rtvi/ && chmod -R 755 /opt/mm/ && \
    mkdir -p /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache /tmp/torch_cache \
             /opt/nvidia/rtvi/codecs && \
    chown -R 1001:1001 /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache \
                       /tmp/torch_cache /opt/nvidia/rtvi/codecs && \
    chmod -R 755 /tmp/huggingface /tmp/triton_model_repo /opt/nvidia/rtvi/.rtvi/ngc_model_cache /tmp/torch_cache


USER vss:1001

# Set PyTorch cache directory to avoid getuser() call that fails when user doesn't exist in /etc/passwd
ENV TORCHINDUCTOR_CACHE_DIR=/tmp/torch_cache
ENV TORCH_CACHE_DIR=/tmp/torch_cache

#To allow helm chart start script to copy updated config:
ENV PATH=$PATH:/opt/nvidia/rtvi/
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/opt/tritonserver/lib/
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/lib/x86_64-linux-gnu/
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/dist-packages/nvidia/nvshmem/lib/:$LD_LIBRARY_PATH

WORKDIR /opt/nvidia/rtvi/

ENTRYPOINT ["/opt/nvidia/rtvi/start_rtvi_vlm.sh"]

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

ARG PYTHON_VERSION=3.13
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.6
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python

###################################################
# OpenCV (headless) rebuilt WITHOUT FFmpeg/GStreamer.
#
# The PyPI opencv-python-headless wheel (pulled transitively via nuscenes-devkit)
# bundles FFmpeg shared libs (libavcodec/avformat/avutil/swscale, LGPLv2.1 +
# patent-encumbered codecs) hard-linked into cv2. We recompile OpenCV with
# WITH_FFMPEG=OFF to avoid shipping those codecs.
#
# NOTE: this DISABLES video read/write (cv2.VideoCapture / cv2.VideoWriter in
# spatialai_data_utils.visualization.video_utils). videoio is still compiled so
# the classes exist and fail gracefully (cannot_open / write_error); the image
# and drawing ops (imread/imwrite/resize/rectangle/putText/...) are unaffected.
#
# Built on manylinux (old glibc) + auditwheel so the wheel is self-contained for
# the distroless runtime. Tag 86 == opencv-python-headless 4.11.0.86, which
# matches Pipfile.lock and is numpy<2 compatible (the project pins numpy 1.26.4).
ARG MANYLINUX_IMG=quay.io/pypa/manylinux_2_28
ARG OPENCV_PYTHON_TAG=86

FROM ${MANYLINUX_IMG}_x86_64 AS ocv-amd64
FROM ${MANYLINUX_IMG}_aarch64 AS ocv-arm64
FROM ocv-${TARGETARCH} AS opencv-builder
ARG OPENCV_PYTHON_TAG
ENV PYBIN=/opt/python/cp313-cp313/bin \
    ENABLE_HEADLESS=1 \
    ENABLE_CONTRIB=0 \
    CMAKE_ARGS="-DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF -DWITH_V4L=OFF -DWITH_1394=OFF -DWITH_GPHOTO2=OFF -DWITH_ANDROID_MEDIANDK=OFF -DWITH_AVIF=OFF -DBUILD_LIST=python3,calib3d,imgcodecs,videoio"
RUN $PYBIN/pip install --no-cache-dir --upgrade pip auditwheel
RUN git clone --depth 1 --branch ${OPENCV_PYTHON_TAG} --recursive --shallow-submodules \
        https://github.com/opencv/opencv-python.git /src
RUN cd /src && $PYBIN/pip wheel . --no-deps --no-cache-dir -w /raw
RUN $PYBIN/auditwheel repair -w /opencv-wheels /raw/*.whl
# Fail the build if any FFmpeg/GStreamer lib slipped into the repaired wheel.
RUN $PYBIN/python - <<'PY'
import glob, zipfile
whl = glob.glob('/opencv-wheels/*.whl')[0]
bad = [n for n in zipfile.ZipFile(whl).namelist()
       if any(s in n.lower() for s in ('libav', 'swscale', 'swresample', 'libgst'))]
print('repaired wheel:', whl)
assert not bad, f'FFmpeg/GStreamer libs present in wheel: {bad}'
print('OK: no FFmpeg/GStreamer libs in wheel')
PY

###################################################
# Container used to build and download libraries
FROM python:${PYTHON_VERSION}-trixie AS builder

ARG APP_GID=1000
ARG APP_UID=1000
ARG PYTORCH3D_REF=33824be
ARG TARGETARCH
ARG TORCH_VERSION=2.10.0+cpu

ENV DEBIAN_FRONTEND=noninteractive \
    FORCE_CUDA=0 \
    MAX_JOBS=4 \
    PIP_NO_CACHE_DIR=1 \
    PIPENV_VENV_IN_PROJECT=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /spatialai_data_utils

COPY docker/deps.json deps.json

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++=4:14.2.0-1 \
        git=1:2.47.3-0+deb13u1 \
        jq=1.7.1-6+deb13u2 \
        libgl1=1.7.0-1+b2 \
        libglib2.0-0t64=2.84.4-3~deb13u3 \
        libgomp1=14.2.0-19 && \
    mkdir -p /var/lib/dpkg/status.d && \
    curl -o generate_metadata.sh https://urm.nvidia.com/artifactory/sw-gpu-ucs-hardened-debian/tools/generate_metadata_v0.3.sh && \
    chmod +x generate_metadata.sh && \
    ./generate_metadata.sh deps.json && \
    if [ "$TARGETARCH" = "arm64" ]; then \
        cp -L /usr/lib/aarch64-linux-gnu/libgomp.so.1 /lib/distroless/; \
        cp -L /usr/lib/aarch64-linux-gnu/libglib-2.0.so.0 /lib/distroless/; \
        cp -L /usr/lib/aarch64-linux-gnu/libgthread-2.0.so.0 /lib/distroless/; \
        cp -L /usr/lib/aarch64-linux-gnu/libGL.so.1 /lib/distroless/; \
    else \
        cp -L /usr/lib/x86_64-linux-gnu/libgomp.so.1 /lib/distroless/; \
        cp -L /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0 /lib/distroless/; \
        cp -L /usr/lib/x86_64-linux-gnu/libgthread-2.0.so.0 /lib/distroless/; \
        cp -L /usr/lib/x86_64-linux-gnu/libGL.so.1 /lib/distroless/; \
    fi && \
    rm -f /lib/distroless/libmbedcrypto.so.7 && \
    rm -f /var/lib/dpkg/status.d/libmbedcrypto7_* && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update && \
            apt-get install -y --no-install-recommends libgdal-dev=3.10.3+dfsg-1 && \
            apt-get clean && \
            rm -rf /var/lib/apt/lists/*; \
    fi

COPY Pipfile Pipfile.lock ./
COPY README.md LICENSE NOTICE 3rdParty_Licenses.md NVIDIA-Software-License-Agreement.pdf ./
COPY release ./release
COPY spatialai_data_utils ./spatialai_data_utils

# FFmpeg-free OpenCV wheel built in the opencv-builder stage above.
COPY --from=opencv-builder /opencv-wheels/ /tmp/opencv-wheels/

# Dependency install order (FFmpeg avoidance):
#   1. locked requirements (Pipfile.lock no longer contains nuscenes-devkit or
#      opencv-python-headless). The opencv grep filter is a defensive no-op kept
#      in case opencv is ever reintroduced into the lock.
#   2. our FFmpeg-free OpenCV wheel (opencv-python-headless 4.11.0.86).
RUN pip install --upgrade \
        "pip==26.1.2" \
        "pipenv==2026.6.2" \
        "setuptools==82.0.1" \
        "wheel==0.47.0" && \
    pip install \
        "meson-python==0.20.0" \
        "ninja==1.11.1.1" \
        "Cython==3.0.12" && \
    pipenv requirements --hash > requirements.full.txt && \
    grep -vi '^opencv-python-headless' requirements.full.txt > requirements.txt && \
    pip install --require-hashes --no-deps --no-cache-dir -r requirements.txt && \
    pip install --no-deps --no-cache-dir /tmp/opencv-wheels/*.whl && \
    rm -rf /tmp/opencv-wheels requirements.full.txt

RUN pip install --no-cache-dir "torch==${TORCH_VERSION}" \
    --extra-index-url https://download.pytorch.org/whl/cpu

RUN git clone https://github.com/facebookresearch/pytorch3d.git /tmp/pytorch3d && \
    cd /tmp/pytorch3d && \
    git checkout "${PYTORCH3D_REF}" && \
    pip install --no-build-isolation . && \
    rm -rf /tmp/pytorch3d

# Install the package + its `eval` extra (nuscenes-devkit + motmetrics). Base
# deps are already present from the hash-locked requirements above; the extra
# adds the eval-only packages.
RUN pip install --no-cache-dir ./release[eval] && \
    pip uninstall -y wheel pipenv pip

RUN mkdir -p /runtime/licenses /runtime/workspace/results && \
    cp 3rdParty_Licenses.md LICENSE NOTICE NVIDIA-Software-License-Agreement.pdf /runtime/licenses/ && \
    chown -R "${APP_UID}:${APP_GID}" /runtime && \
    chmod -R 775 /runtime/workspace/results

###################################################
# Container to deploy and run the python package
FROM $DISTROLESS_IMG:$DISTROLESS_TAG AS py-deploy

ARG APP_GID=1000
ARG APP_UID=1000
ARG PYTHON_VERSION=3.13

ENV LD_LIBRARY_PATH=/lib/distroless/ \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /usr/local/lib/python${PYTHON_VERSION}/site-packages /usr/local/lib/python${PYTHON_VERSION}/site-packages
COPY --from=builder /lib/distroless/ /lib/distroless/
COPY --from=builder /var/lib/dpkg/status.d /var/lib/dpkg/status.d
COPY --from=builder --chown=${APP_UID}:${APP_GID} /runtime/licenses /licenses
COPY --from=builder --chown=${APP_UID}:${APP_GID} /runtime/workspace /workspace

WORKDIR /workspace

USER ${APP_UID}:${APP_GID}

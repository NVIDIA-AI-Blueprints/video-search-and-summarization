# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# manylinux CPython tag for the OpenCV build; must track PYTHON_VERSION
# (e.g. 3.13 -> cp313-cp313, 3.14 -> cp314-cp314).
ARG PYBIN_TAG=cp313-cp313
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.8
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python

###################################################
# OpenCV (headless) rebuilt WITHOUT FFmpeg/GStreamer.
#
# The PyPI `opencv-python-headless` wheel bundles FFmpeg shared libraries
# (libavcodec/avformat/avutil/swscale, LGPLv2.1) hard-linked into cv2. This
# codebase only uses OpenCV for image + calibration ops (no video I/O), so we
# recompile OpenCV with WITH_FFMPEG=OFF to avoid shipping those codecs.
#
# Built on manylinux (old glibc) and repaired with auditwheel so the wheel is
# self-contained and installs cleanly into the distroless runtime stage. Image
# codecs (jpeg/png/webp/tiff/jp2) come from OpenCV's bundled static 3rdparty.
ARG MANYLINUX_IMG=quay.io/pypa/manylinux_2_28
# opencv-python release tag; "92" == 4.13.0.92 (must match Pipfile.lock).
ARG OPENCV_PYTHON_TAG=92

FROM ${MANYLINUX_IMG}_x86_64 AS ocv-amd64
FROM ${MANYLINUX_IMG}_aarch64 AS ocv-arm64
FROM ocv-${TARGETARCH} AS opencv-builder
ARG OPENCV_PYTHON_TAG
ARG PYBIN_TAG
ENV PYBIN=/opt/python/${PYBIN_TAG}/bin \
    ENABLE_HEADLESS=1 \
    ENABLE_CONTRIB=0 \
    CMAKE_ARGS="-DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF -DWITH_V4L=OFF -DWITH_1394=OFF -DWITH_GPHOTO2=OFF -DWITH_ANDROID_MEDIANDK=OFF -DVIDEOIO_ENABLE_PLUGINS=OFF -DWITH_AVIF=OFF -DBUILD_LIST=python3,calib3d,imgcodecs"
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

ARG TARGETARCH

WORKDIR /build

COPY docker/deps.json deps.json

# Install jq so generate_metadata.sh does not try to download it from GitHub (fails in CI)
# Ensure status.d exists for py-deploy COPY (python:3.13-trixie may not have it)
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /var/lib/dpkg/status.d

RUN curl -o generate_metadata.sh https://urm.nvidia.com/artifactory/sw-gpu-ucs-hardened-debian/tools/generate_metadata_v0.3.sh  && \
    chmod +x generate_metadata.sh && \
    ./generate_metadata.sh deps.json

# Download GCC source code
RUN echo "deb-src http://deb.debian.org/debian trixie main" >> /etc/apt/sources.list && \
    echo "deb-src http://deb.debian.org/debian trixie-updates main" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y dpkg-dev && \
    mkdir -p /usr/share/source && \
    cd /usr/share/source && \
    apt-get source gcc-14 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy python app here
WORKDIR /behavior-analytics


# Resolves issue with installing python package fiona on arch64
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update && \
            apt-get install -y libgdal-dev; \
    fi

# Install pipenv and other tools
RUN pip install --upgrade pip pipenv setuptools wheel

# FFmpeg-free OpenCV wheel built in the opencv-builder stage above.
COPY --from=opencv-builder /opencv-wheels/ /tmp/opencv-wheels/

# Install requirements and py analytics library itself.
# opencv-python-headless is filtered out of the locked requirements and installed
# from our FFmpeg-free wheel instead of the PyPI wheel (which bundles LGPL FFmpeg).
COPY . .
RUN pipenv requirements --hash > requirements.full.txt && \
    awk '/^opencv-python-headless/{skip=1;next} /^[^ \t]/{skip=0} !skip' requirements.full.txt > requirements.txt && \
    pip install --no-deps --no-cache-dir -r requirements.txt && \
    pip install --no-deps --no-cache-dir /tmp/opencv-wheels/*.whl && \
    pip install --no-deps --no-cache-dir . && \
    rm -rf /tmp/opencv-wheels requirements.full.txt && \
    pip uninstall -y wheel pipenv pip

# Prepare clean runtime directory with only required files
RUN mkdir -p /runtime && \
    cp -r apps /runtime/ && \
    cp -r src/mdx/analytics/core/tools /runtime/tools && \
    cp 3rdParty_Licenses.md /runtime/ && \
    chown -R 1000:1000 /runtime

# ###################################################
# Container to deploy and run the python app
FROM $DISTROLESS_IMG:$DISTROLESS_TAG AS py-deploy

ENV LD_LIBRARY_PATH=/lib/distroless/
# Copy installed python libraries
COPY --from=builder /usr/local/lib/python${PY_VERSION}/site-packages /usr/local/lib/python${PY_VERSION}/site-packages
# Copy necessary depedendencies
COPY --from=builder /lib/distroless/  /lib/distroless/
# Copy the status.d for scanning (directory created in builder if missing)
COPY --from=builder /var/lib/dpkg/status.d /var/lib/dpkg/status.d
# Copy GCC source code for license compliance
COPY --from=builder /usr/share/source /usr/share/source

# Copy only required runtime files (no source code, tests, or build artifacts)
# Single COPY ensures /behavior-analytics directory has correct 1000:1000 ownership
COPY --from=builder --chown=1000:1000 /runtime /behavior-analytics

WORKDIR /behavior-analytics

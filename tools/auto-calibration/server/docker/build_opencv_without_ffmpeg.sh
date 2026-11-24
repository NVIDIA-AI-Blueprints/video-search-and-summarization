#!/usr/bin/env bash
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

set -euo pipefail

readonly OPENCV_VERSION="${OPENCV_VERSION:-4.12.0}"
readonly OPENCV_SHA256="${OPENCV_SHA256:-44c106d5bb47efec04e531fd93008b3fcd1d27138985c5baf4eafac0e1ec9e9d}"
readonly SOURCE_DIR="/tmp/opencv-${OPENCV_VERSION}"
readonly BUILD_DIR="${SOURCE_DIR}/build"
readonly ARCHIVE="/tmp/opencv-${OPENCV_VERSION}.tar.gz"

python_bin=$(command -v python3.12 || command -v python3)
python_site=$(
    "$python_bin" -c 'import sysconfig; print(sysconfig.get_path("platlib"))'
)
python_include=$(
    "$python_bin" -c 'import sysconfig; print(sysconfig.get_path("include"))'
)
numpy_include=$(
    "$python_bin" -c 'import numpy; print(numpy.get_include())'
)

curl --fail --location --retry 5 \
    "https://github.com/opencv/opencv/archive/refs/tags/${OPENCV_VERSION}.tar.gz" \
    --output "$ARCHIVE"
printf '%s  %s\n' "$OPENCV_SHA256" "$ARCHIVE" | sha256sum --check
tar -xzf "$ARCHIVE" -C /tmp
cmake -S "$SOURCE_DIR" -B "$BUILD_DIR" \
    -D CMAKE_BUILD_TYPE=Release \
    -D CMAKE_INSTALL_PREFIX=/usr/local \
    -D BUILD_LIST=core,imgproc,imgcodecs,videoio,calib3d,features2d,flann,highgui,video,photo,python3 \
    -D BUILD_SHARED_LIBS=ON \
    -D BUILD_TESTS=OFF \
    -D BUILD_PERF_TESTS=OFF \
    -D BUILD_EXAMPLES=OFF \
    -D BUILD_opencv_apps=OFF \
    -D BUILD_opencv_python3=ON \
    -D OPENCV_PYTHON3_INSTALL_PATH="$python_site" \
    -D PYTHON3_EXECUTABLE="$python_bin" \
    -D PYTHON3_INCLUDE_DIR="$python_include" \
    -D PYTHON3_NUMPY_INCLUDE_DIRS="$numpy_include" \
    -D WITH_FFMPEG=OFF \
    -D WITH_GSTREAMER=ON \
    -D WITH_GTK=ON \
    -D WITH_TIFF=OFF \
    -D WITH_OPENEXR=OFF \
    -D WITH_CUDA=OFF \
    -D WITH_IPP=OFF \
    -D WITH_1394=OFF
cmake --build "$BUILD_DIR" --parallel "$(nproc)"
cmake --install "$BUILD_DIR"
ldconfig
rm -rf "$SOURCE_DIR" "$ARCHIVE"

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

# AMC video I/O uses an FFmpeg-free OpenCV build with GStreamer. Purge package
# records as well as payloads so container compliance inventories cannot report
# FFmpeg, Intel Media SDK/MFX, or non-free external codec implementations.
# NVIDIA's DeepStream codec plugins are outside this denylist and remain
# available.
mapfile -t codec_packages < <(
    dpkg-query -W -f='${binary:Package}\n' 2>/dev/null \
        | grep -E '^(ffmpeg|libmfx.*|libav(codec|device|filter|format|util)[0-9-].*|lib(postproc|swresample|swscale)[0-9-].*|lib(x26[45]|xvidcore|openh264|de265|fdk-aac|faad|vo-aacenc|vo-amrwbenc|a52|twolame|dv|mp3lame|mpg123|mpeg2)[0-9-].*|gstreamer1\.0-libav)' \
        || true
)

if ((${#codec_packages[@]})); then
    dpkg --purge --force-depends "${codec_packages[@]}"
fi

# Remove unowned/static copies, wheel payloads, GStreamer wrappers for the
# prohibited implementations, and stale linker metadata inherited from base
# image layers.
find /usr /opt -xdev \( -type f -o -type l \) 2>/dev/null \
    | grep -E '/(ffmpeg|ffprobe|libmfx|libav(codec|device|filter|format|util)|lib(postproc|swresample|swscale)|lib(x26[45]|xvidcore|openh264|de265|fdk[-_]aac|faad|vo-aacenc|mp3lame|mpg123|mpeg2))[^/]*$' \
    | xargs -r rm -f \
    || true

find /usr /opt -xdev -type f 2>/dev/null \
    | grep -E '/gstreamer-1\.0/libgst(libav|msdk|x264|x265|openh264|de265|fdkaac|faad|voaacenc|voamrwbenc|a52dec|twolame|dv|lame|mpg123|mpeg2(dec|enc)|vulkan|vaapi)\.so$' \
    | xargs -r rm -f \
    || true

# Remove unused codec implementations that are outside the AAC/H.264/H.265
# watchlist but are still distributed by the DeepStream base image.
find /usr /opt -xdev \( -type f -o -type l \) \
    \( -name 'liba52*.so*' -o -name 'libvo-amrwbenc*.so*' -o \
       -name 'libtwolame*.so*' -o -name 'libdv.so*' \) \
    -delete 2>/dev/null \
    || true

# Remove Intel Media SDK plugin configuration and any now-empty plugin
# directories. H.264/HEVC implementations also live in libmfxhw64, so removing
# only codec-named files under mfx/ is not sufficient.
find /usr/lib -mindepth 2 -maxdepth 2 -type d -name mfx \
    -exec rm -rf -- {} +
rm -rf /usr/share/mfx

rm -rf \
    /usr/include/libavcodec \
    /usr/include/libavdevice \
    /usr/include/libavfilter \
    /usr/include/libavformat \
    /usr/include/libavutil \
    /usr/include/libpostproc \
    /usr/include/libswresample \
    /usr/include/libswscale \
    /usr/include/x86_64-linux-gnu/libavcodec \
    /usr/include/x86_64-linux-gnu/libavdevice \
    /usr/include/x86_64-linux-gnu/libavfilter \
    /usr/include/x86_64-linux-gnu/libavformat \
    /usr/include/x86_64-linux-gnu/libavutil \
    /usr/include/x86_64-linux-gnu/libpostproc \
    /usr/include/x86_64-linux-gnu/libswresample \
    /usr/include/x86_64-linux-gnu/libswscale

# Fail closed: unlike the previous flag-based check, no FFmpeg runtime is
# allowed, including private copies bundled in Python wheels. NVIDIA codec
# libraries use nv*/nvidia* names and are intentionally unaffected.
if find /usr /opt -xdev \( -type f -o -type l \) \
    \( -name 'ffmpeg' -o -name 'ffprobe' -o \
       -name 'libavcodec*.so*' -o -name 'libavdevice*.so*' -o \
       -name 'libavfilter*.so*' -o -name 'libavformat*.so*' -o \
       -name 'libavutil*.so*' -o -name 'libpostproc*.so*' -o \
       -name 'libswresample*.so*' -o -name 'libswscale*.so*' -o \
       -name 'libx264*.so*' -o -name 'libx265*.so*' -o \
       -name 'libmfx*.so*' -o \
       -name 'libxvidcore*.so*' -o -name 'libopenh264*.so*' -o \
       -name 'libde265*.so*' -o -name 'libfdk*aac*.so*' -o \
       -name 'libfaad*.so*' -o -name 'libvo-aacenc*.so*' -o \
       -name 'liba52*.so*' -o -name 'libvo-amrwbenc*.so*' -o \
       -name 'libtwolame*.so*' -o -name 'libdv.so*' -o \
       -name 'libmp3lame*.so*' -o -name 'libmpg123*.so*' -o \
       -name 'libmpeg2*.so*' \) -print -quit 2>/dev/null | grep -q .; then
    echo 'Prohibited FFmpeg or non-free codec library remains in the image' >&2
    exit 1
fi

if find /usr /opt -xdev -type f \
    \( -name 'libgstmsdk.so' -o -name 'libgsta52dec.so' -o \
       -name 'libgstvoamrwbenc.so' -o \
       -name 'libgsttwolame.so' -o -name 'libgstdv.so' \) \
    -print -quit 2>/dev/null | grep -q .; then
    echo 'Prohibited non-NVIDIA GStreamer codec plugin remains in the image' >&2
    exit 1
fi

# Verify that AMC's OpenCV runtime is usable, FFmpeg is disabled, and the
# GStreamer backend needed for NVIDIA video handling is enabled.
python_bin=$(command -v python3.12 || command -v python3)
"$python_bin" - <<'PY'
import cv2

build_info = cv2.getBuildInformation()
assert "FFMPEG:                      YES" not in build_info, build_info
assert "GStreamer:                   YES" in build_info, build_info
print(f"Verified FFmpeg-free OpenCV {cv2.__version__} with GStreamer")
PY

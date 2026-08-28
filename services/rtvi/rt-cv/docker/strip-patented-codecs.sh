#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Remove patent-encumbered codecs and their GStreamer plugins from the image.
#
# Extracted verbatim from docker/Dockerfile, where it was a single 94-line RUN
# layer -- 30% of the file. Behaviour is unchanged: same package list, same file
# globs, same amd64-only Intel MFX branch. Kept as one script so it still runs
# as ONE layer, which matters: removing packages in a later layer would leave
# them in the earlier one and not shrink the image.
#
# TARGETARCH selects the apt architecture qualifier and gates the MFX removal.
set -eu
: "${TARGETARCH:?TARGETARCH must be set (buildx supplies it; declare ARG TARGETARCH)}"

apt-get update && \
    apt-get remove -y \
      ffmpeg \
      gstreamer1.0-libav:${TARGETARCH} \
        libde265-dev \
      libavcodec-dev:${TARGETARCH} \
      libavcodec60:${TARGETARCH} \
      libavdevice60:${TARGETARCH} \
      libavfilter-dev:${TARGETARCH} \
      libavfilter9:${TARGETARCH} \
      libavformat-dev:${TARGETARCH} \
      libavformat60:${TARGETARCH} \
      libavutil-dev:${TARGETARCH} \
      libavutil58:${TARGETARCH} \
      libopencv-calib3d-dev:${TARGETARCH} \
      libopencv-calib3d406t64:${TARGETARCH} \
      libopencv-contrib-dev:${TARGETARCH} \
      libopencv-contrib406t64:${TARGETARCH} \
      libopencv-core-dev:${TARGETARCH} \
      libopencv-core406t64:${TARGETARCH} \
      libopencv-dev \
      libopencv-dnn-dev:${TARGETARCH} \
      libopencv-dnn406t64:${TARGETARCH} \
      libopencv-features2d-dev:${TARGETARCH} \
      libopencv-features2d406t64:${TARGETARCH} \
      libopencv-flann-dev:${TARGETARCH} \
      libopencv-flann406t64:${TARGETARCH} \
      libopencv-highgui-dev:${TARGETARCH} \
      libopencv-highgui406t64:${TARGETARCH} \
      libopencv-imgcodecs-dev:${TARGETARCH} \
      libopencv-imgcodecs406t64:${TARGETARCH} \
      libopencv-imgproc-dev:${TARGETARCH} \
      libopencv-imgproc406t64:${TARGETARCH} \
      libopencv-java \
      libopencv-ml-dev:${TARGETARCH} \
      libopencv-ml406t64:${TARGETARCH} \
      libopencv-objdetect-dev:${TARGETARCH} \
      libopencv-objdetect406t64:${TARGETARCH} \
      libopencv-photo-dev:${TARGETARCH} \
      libopencv-photo406t64:${TARGETARCH} \
      libopencv-shape-dev:${TARGETARCH} \
      libopencv-shape406t64:${TARGETARCH} \
      libopencv-stitching-dev:${TARGETARCH} \
      libopencv-stitching406t64:${TARGETARCH} \
      libopencv-superres-dev:${TARGETARCH} \
      libopencv-superres406t64:${TARGETARCH} \
      libopencv-video-dev:${TARGETARCH} \
      libopencv-video406t64:${TARGETARCH} \
      libopencv-videoio-dev:${TARGETARCH} \
      libopencv-videoio406t64:${TARGETARCH} \
      libopencv-videostab-dev:${TARGETARCH} \
      libopencv-videostab406t64:${TARGETARCH} \
      libopencv-viz-dev:${TARGETARCH} \
      libopencv-viz406t64:${TARGETARCH} \
      libopencv406-jni \
      mencoder \
      mjpegtools \
      mplayer && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    (dpkg -l | grep "^rc" | awk '{print $2}' | xargs -r dpkg --purge || true) && \
    rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstaudioparsers.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstx264.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstfaad.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvoaacenc.so \
        /usr/lib/*-linux-gnu/libavresample* /usr/lib/*-linux-gnu/libavutil* \
        /usr/lib/*-linux-gnu/libavcodec* /usr/lib/*-linux-gnu/libavformat* \
        /usr/lib/*-linux-gnu/libavfilter* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstde265.so* \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstx265.so* /usr/lib/*-linux-gnu/libde265.so* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvpx.so* \
        /usr/lib/*-linux-gnu/libmpeg2.so* /usr/lib/*-linux-gnu/libmpeg2encpp-2.1.so* /usr/lib/*-linux-gnu/libmpg123.so* \
        /usr/lib/*-linux-gnu/libx265.so* /usr/lib/*-linux-gnu/libx264.so* /usr/lib/*-linux-gnu/libvpx.so* \
        /usr/lib/*-linux-gnu/libmpeg2convert.so* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstopenh264.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstnvcodec.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstuvch264.so /usr/lib/*-linux-gnu/libopenh264.so.2.2.0 /usr/lib/*-linux-gnu/libopenh264.so.6 \
        /usr/lib/*-linux-gnu/libvo-aacenc.so.0 /usr/lib/*-linux-gnu/libvo-aacenc.so.0.0.4 /usr/lib/*-linux-gnu/libmp3lame.so.0.0.0 \
        /usr/lib/*-linux-gnu/libmp3lame.so.0 /usr/lib/*-linux-gnu/libfaad* /usr/lib/*-linux-gnu/libFLAC.so* \
        /usr/lib/*-linux-gnu/libmjpegutils-2.1.so.0* /usr/lib/*-linux-gnu/libxvidcore.so* /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpegpsmux.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstflac.so \
        /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2enc.so /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2dec.so \
        /usr/lib/*-linux-gnu/mfx/libmfx_h264la_hw64.so /usr/lib/*-linux-gnu/libopenh264.so.7 /usr/lib/*-linux-gnu/libopenh264.so.2.4.1 \
        /usr/lib/*-linux-gnu/libdca.so* /usr/lib/*-linux-gnu/libdvdnav.so* /usr/lib/*-linux-gnu/libdvdread.so* \
        /usr/lib/*-linux-gnu/libmpeg2.so.0* /usr/lib/*-linux-gnu/libmpeg2encpp-2.1.so* /usr/lib/*-linux-gnu/libmpg123.so* && \
    if [ "$TARGETARCH" = "amd64" ]; then rm -rf /usr/lib/*-linux-gnu/mfx \
        /usr/lib/*-linux-gnu/libmfx.so.1 \
        /usr/lib/*-linux-gnu/libmfx-tracer.so.1.35 \
        /usr/lib/*-linux-gnu/libmfxhw64.so.1.35 \
        /usr/lib/*-linux-gnu/libmfxhw64.so.1 \
        /usr/lib/*-linux-gnu/libmfx.so.1.35 \
        /usr/lib/*-linux-gnu/libmfx-tracer.so.1; \
    fi
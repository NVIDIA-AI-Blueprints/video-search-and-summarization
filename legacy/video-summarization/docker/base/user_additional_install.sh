# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# additional components the user can self install
apt-get update
#apt-get install -y gstreamer1.0-libav
#backup librtsp to tmp
#cp /usr/lib/*-linux-gnu/gstreamer-1.0/libgstrtsp.so /tmp/
#cp /usr/lib/*-linux-gnu/libgstrtsp-1.0.so.0.2001.0 /tmp/

# ubuntu 24.04
apt-get install --reinstall -y gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly  libswresample-dev libavutil-dev libavutil58 \
    libavcodec-dev libavcodec60 libavformat-dev libavformat60 libavfilter9 \
    libde265-dev libde265-0 libx265-199 libx264-164 libvpx9 libmpeg2encpp-2.1-0t64 \
    libmpeg2-4 libmpg123-0t64 libswresample4 libzvbi0t64 libxvidcore4 libslang2 \
    libflac12t64 libzvbi-dev libmp3lame0 libmp3lame-dev \
    libxvidcore-dev libflac-dev libogg-dev libogg0 \
    libjack-jackd2-0 ffmpeg gstreamer1.0-libav libunibreak5 mesa-libgallium
ldconfig
#restore librtsp
#cp /tmp/libgstrtsp.so /usr/lib/*-linux-gnu/gstreamer-1.0/
#cp /tmp/libgstrtsp-1.0.so.0.2001.0 /usr/lib/*-linux-gnu/libgstrtsp-1.0.so.0.2001.0

mv /usr/bin/ffmpeg   /usr/bin/ffmpeg_for_overlay_video

# Remove Gstreamer plugin libraries with unmet dependencies
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstspandsp.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstopenh264.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvoaacenc.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstfaad.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstdtsdec.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstdvdread.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpeg2enc.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmplex.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstresindvd.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_eglglessink.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_udp.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/deepstream/libnvdsgst_nvmultiurisrcbin.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstladspa.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstzxing.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstneonhttpsrc.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstfluidsynthmidi.so
rm -f /usr/lib/*-linux-gnu/gstreamer-1.0/libgstdirectfb.so
rm /usr/lib/*-linux-gnu/gstreamer-1.0/libgstnvcodec.so

echo "Deleting GStreamer cache"
rm -rf ~/.cache/gstreamer-1.0/

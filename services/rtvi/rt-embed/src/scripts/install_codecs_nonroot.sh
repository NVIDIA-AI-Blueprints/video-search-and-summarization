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

# Install patent-encumbered multimedia codecs at container startup without root.
#
# Strategy:
#   1. curl the pinned .deb URLs directly from archive.ubuntu.com (Ubuntu 24.04 Noble).
#      No apt-get update or apt lists needed — URLs are hardcoded per arch.
#   2. dpkg -x (no root) to extract .deb contents.
#   3. Env vars (GST_PLUGIN_PATH, LD_LIBRARY_PATH, PATH) written to codec_env.sh.
#
# To update package versions: re-run apt-get --print-uris download <packages> inside
# a Noble container and replace the URLs below.
#
# Usage: bash install_codecs_nonroot.sh
# After running, source the generated env file:
#   source /opt/nvidia/rtvi/codecs/codec_env.sh

set -o pipefail

INSTALL_DIR=/opt/nvidia/rtvi/codecs
DEBS_DIR=/tmp/rtvi_codec_debs_$$

# Arch-aware lib dir
DEB_ARCH=$(dpkg --print-architecture)
MACHINE=$(uname -m)  # x86_64 on amd64, aarch64 on arm64
LIB_DIR="$INSTALL_DIR/usr/lib/${MACHINE}-linux-gnu"
GST_PLUGIN_DIR="$LIB_DIR/gstreamer-1.0"

# Skip if already installed
if [ -f "$INSTALL_DIR/.installed" ]; then
    echo "Proprietary codecs already installed at $INSTALL_DIR"
    exit 0
fi

if [ "$DEB_ARCH" != "amd64" ] && [ "$DEB_ARCH" != "arm64" ]; then
    echo "ERROR: Unsupported architecture: $DEB_ARCH (supported: amd64, arm64)" >&2
    exit 1
fi

echo "Installing proprietary codecs (no-root dpkg extraction, pinned Ubuntu 24.04 Noble URLs, arch=$DEB_ARCH)..."
mkdir -p "$DEBS_DIR" "$INSTALL_DIR"

# Pinned .deb URLs for Ubuntu 24.04 Noble / amd64.
# Source: archive.ubuntu.com — queried via apt-get --print-uris download (after apt-get update)
DEB_URLS_amd64=(
    # --- ffmpeg / libav ---
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/ffmpeg_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libavcodec60_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libavfilter9_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libavformat60_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libavutil58_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libpostproc57_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libswresample4_6.1.1-3ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/libswscale7_6.1.1-3ubuntu5_amd64.deb'
    # --- GStreamer plugins ---
    'http://archive.ubuntu.com/ubuntu/pool/main/g/gst-plugins-good1.0/gstreamer1.0-plugins-good_1.24.2-1ubuntu1.3_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/g/gst-plugins-bad1.0/gstreamer1.0-plugins-bad_1.24.2-1ubuntu4_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/g/gst-plugins-ugly1.0/gstreamer1.0-plugins-ugly_1.24.1-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/g/gst-libav1.0/gstreamer1.0-libav_1.24.1-1build1_amd64.deb'
    # --- Video codecs ---
    'http://archive.ubuntu.com/ubuntu/pool/main/libd/libde265/libde265-0_1.0.15-1build3_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/x/x265/libx265-199_3.5-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/x/x264/libx264-164_0.164.3108%2bgit31e19f9-1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/libv/libvpx/libvpx9_1.14.0-1ubuntu2.3_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/m/mpeg2dec/libmpeg2-4_0.5.1-9build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/x/xvidcore/libxvidcore4_1.3.7-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/d/dav1d/libdav1d7_1.4.1-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/r/rust-rav1e/librav1e0_0.7.1-2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libv/libvidstab/libvidstab1.1_1.1.0-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/o/onevpl/libvpl2_2023.3.0-1build1_amd64.deb'
    # --- Audio codecs ---
    'http://archive.ubuntu.com/ubuntu/pool/main/f/flac/libflac12t64_1.4.3%2bds-2.1ubuntu2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/l/lame/libmp3lame0_3.100-6build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/m/mpg123/libmpg123-0t64_1.32.5-1ubuntu1.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/libo/libogg/libogg0_1.3.5-3build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/z/zvbi/libzvbi0t64_0.2.42-2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/s/shine/libshine3_3.1.1-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/a/a52dec/liba52-0.7.4_0.7.4-20build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/a/aribb24/libaribb24-0t64_1.0.3-2.1build2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/p/pocketsphinx/libpocketsphinx3_0.8.0%2breal5prealpha%2b1-15ubuntu5_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/s/sphinxbase/libsphinxbase3t64_0.8%2b5prealpha%2b1-17build2_amd64.deb'
    # --- Image/container formats ---
    'http://archive.ubuntu.com/ubuntu/pool/universe/j/jpeg-xl/libjxl0.7_0.7.0-10.2ubuntu6.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libp/libplacebo/libplacebo338_6.338.2-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/z/zimg/libzimg2_3.0.5%2bds1-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libb/libbluray/libbluray2_1.3.4-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libu/libudfread/libudfread0_1.1.2-1build1_amd64.deb'
    # --- Networking/streaming ---
    'http://archive.ubuntu.com/ubuntu/pool/universe/z/zeromq3/libzmq5_4.3.5-1build2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libr/librist/librist4_0.2.10%2bdfsg-2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/libr/librabbitmq/librabbitmq4_0.11.0-1build2_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/libs/libssh/libssh-gcrypt-4_0.10.6-2ubuntu0.4_amd64.deb'
    # --- DSP / signal processing ---
    'http://archive.ubuntu.com/ubuntu/pool/main/f/fftw3/libfftw3-double3_3.3.10-1ubuntu3_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/r/rubberband/librubberband2_3.3.0%2bdfsg-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libs/libsoxr/libsoxr0_0.1.3-4build3_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libm/libmysofa/libmysofa1_1.3.2%2bdfsg-2ubuntu2_amd64.deb'
    # --- Math/BLAS ---
    'http://archive.ubuntu.com/ubuntu/pool/universe/o/openblas/libopenblas0-serial_0.3.26%2bds-1ubuntu0.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/l/lapack/libblas3_3.12.0-3build1.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/l/lapack/liblapack3_3.12.0-3build1.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/g/gcc-14/libgfortran5_14.2.0-4ubuntu2%7e24.04.1_amd64.deb'
    # --- GPU / compute ---
    'http://archive.ubuntu.com/ubuntu/pool/universe/o/ocl-icd/ocl-icd-libopencl1_2.3.2-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libv/libva/libva-x11-2_2.20.0-2ubuntu0.1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/main/libv/libvdpau/libvdpau1_1.5-2build1_amd64.deb'
    # --- Transitive deps ---
    'http://archive.ubuntu.com/ubuntu/pool/main/s/snappy/libsnappy1v5_1.1.10-1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/c/codec2/libcodec2-1.2_1.2.0-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libu/libunibreak/libunibreak5_5.1-2build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/libp/libpgm/libpgm-5.3-0t64_5.3.128%7edfsg-2.1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/n/norm/libnorm1t64_1.5.9%2bdfsg-3.1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/m/mbedtls/libmbedcrypto7t64_2.28.8-1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/h/highway/libhwy1t64_1.0.7-8.1build1_amd64.deb'
    'http://archive.ubuntu.com/ubuntu/pool/universe/c/cjson/libcjson1_1.7.17-1_amd64.deb'
)

# Pinned .deb URLs for Ubuntu 24.04 Noble / arm64.
# Source: ports.ubuntu.com — queried by parsing Packages index for noble + noble-updates + noble-security.
# Note: libvpl2 (Intel VPL) is x86-only and absent here.
DEB_URLS_arm64=(
    # --- ffmpeg / libav ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/ffmpeg_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libavcodec60_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libavfilter9_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libavformat60_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libavutil58_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libpostproc57_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libswresample4_6.1.1-3ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/f/ffmpeg/libswscale7_6.1.1-3ubuntu5_arm64.deb'
    # --- GStreamer plugins ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/g/gst-plugins-good1.0/gstreamer1.0-plugins-good_1.24.2-1ubuntu1.3_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/g/gst-plugins-bad1.0/gstreamer1.0-plugins-bad_1.24.2-1ubuntu4_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/g/gst-plugins-ugly1.0/gstreamer1.0-plugins-ugly_1.24.1-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/g/gst-libav1.0/gstreamer1.0-libav_1.24.1-1build1_arm64.deb'
    # --- Video codecs ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libd/libde265/libde265-0_1.0.15-1build3_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/x/x265/libx265-199_3.5-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/x/x264/libx264-164_0.164.3108+git31e19f9-1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libv/libvpx/libvpx9_1.14.0-1ubuntu2.3_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/m/mpeg2dec/libmpeg2-4_0.5.1-9build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/x/xvidcore/libxvidcore4_1.3.7-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/d/dav1d/libdav1d7_1.4.1-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/r/rust-rav1e/librav1e0_0.7.1-2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libv/libvidstab/libvidstab1.1_1.1.0-2build1_arm64.deb'
    # --- Audio codecs ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/f/flac/libflac12t64_1.4.3+ds-2.1ubuntu2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/l/lame/libmp3lame0_3.100-6build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/m/mpg123/libmpg123-0t64_1.32.5-1ubuntu1.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libo/libogg/libogg0_1.3.5-3build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/z/zvbi/libzvbi0t64_0.2.42-2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/s/shine/libshine3_3.1.1-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/a/a52dec/liba52-0.7.4_0.7.4-20build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/a/aribb24/libaribb24-0t64_1.0.3-2.1build2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/p/pocketsphinx/libpocketsphinx3_0.8.0+real5prealpha+1-15ubuntu5_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/s/sphinxbase/libsphinxbase3t64_0.8+5prealpha+1-17build2_arm64.deb'
    # --- Image/container formats ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/j/jpeg-xl/libjxl0.7_0.7.0-10.2ubuntu6.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libp/libplacebo/libplacebo338_6.338.2-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/z/zimg/libzimg2_3.0.5+ds1-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libb/libbluray/libbluray2_1.3.4-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libu/libudfread/libudfread0_1.1.2-1build1_arm64.deb'
    # --- Networking/streaming ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/z/zeromq3/libzmq5_4.3.5-1build2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libr/librist/librist4_0.2.10+dfsg-2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libr/librabbitmq/librabbitmq4_0.11.0-1build2_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libs/libssh/libssh-gcrypt-4_0.10.6-2ubuntu0.4_arm64.deb'
    # --- DSP / signal processing ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/f/fftw3/libfftw3-double3_3.3.10-1ubuntu3_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/r/rubberband/librubberband2_3.3.0+dfsg-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libs/libsoxr/libsoxr0_0.1.3-4build3_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libm/libmysofa/libmysofa1_1.3.2+dfsg-2ubuntu2_arm64.deb'
    # --- Math/BLAS ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/o/openblas/libopenblas0-serial_0.3.26+ds-1ubuntu0.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/l/lapack/libblas3_3.12.0-3build1.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/l/lapack/liblapack3_3.12.0-3build1.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/g/gcc-14/libgfortran5_14.2.0-4ubuntu2~24.04.1_arm64.deb'
    # --- GPU / compute ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/o/ocl-icd/ocl-icd-libopencl1_2.3.2-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libv/libva/libva-x11-2_2.20.0-2ubuntu0.1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/libv/libvdpau/libvdpau1_1.5-2build1_arm64.deb'
    # --- Transitive deps ---
    'http://ports.ubuntu.com/ubuntu-ports/pool/main/s/snappy/libsnappy1v5_1.1.10-1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/c/codec2/libcodec2-1.2_1.2.0-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libu/libunibreak/libunibreak5_5.1-2build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/libp/libpgm/libpgm-5.3-0t64_5.3.128~dfsg-2.1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/n/norm/libnorm1t64_1.5.9+dfsg-3.1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/m/mbedtls/libmbedcrypto7t64_2.28.8-1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/h/highway/libhwy1t64_1.0.7-8.1build1_arm64.deb'
    'http://ports.ubuntu.com/ubuntu-ports/pool/universe/c/cjson/libcjson1_1.7.17-1_arm64.deb'
)

# Select URL list for this arch
declare -n DEB_URLS="DEB_URLS_${DEB_ARCH//-/_}"

echo "Downloading ${#DEB_URLS[@]} packages to $DEBS_DIR ..."
FAILED=0
for url in "${DEB_URLS[@]}"; do
    filename=$(basename "$url")
    # curl decodes percent-encoded chars in the URL; use -o to name the output file
    if ! curl -fsSL "$url" -o "$DEBS_DIR/$filename"; then
        echo "ERROR: Failed to download $url" >&2
        FAILED=1
    fi
done

if [ "$FAILED" -ne 0 ]; then
    rm -rf "$DEBS_DIR"
    exit 1
fi

shopt -s nullglob
DEBS=("$DEBS_DIR"/*.deb)
shopt -u nullglob
if [ ${#DEBS[@]} -eq 0 ]; then
    echo "ERROR: No .deb files found after download." >&2
    rm -rf "$DEBS_DIR"
    exit 1
fi

echo "Extracting ${#DEBS[@]} packages..."
for deb in "${DEBS[@]}"; do
    dpkg -x "$deb" "$INSTALL_DIR/"
done

# blas/lapack live in subdirs — create top-level symlinks so linker finds them
[ -f "$LIB_DIR/blas/libblas.so.3" ]     && ln -sf "$LIB_DIR/blas/libblas.so.3"     "$LIB_DIR/libblas.so.3"
[ -f "$LIB_DIR/lapack/liblapack.so.3" ] && ln -sf "$LIB_DIR/lapack/liblapack.so.3" "$LIB_DIR/liblapack.so.3"

# Rename ffmpeg to avoid conflicting with any system ffmpeg
[ -f "$INSTALL_DIR/usr/bin/ffmpeg" ] && mv "$INSTALL_DIR/usr/bin/ffmpeg" "$INSTALL_DIR/usr/bin/ffmpeg_for_overlay_video"

# Remove GStreamer plugins with unresolvable dependencies
for plugin in libgstspandsp libgstopenh264 libgstvoaacenc libgstfaad libgstdtsdec \
              libgstdvdread libgstmpeg2enc libgstmplex libgstresindvd libgstladspa \
              libgstzxing libgstneonhttpsrc libgstfluidsynthmidi libgstdirectfb \
              libgstaasink libgstcacasink; do
    find "$GST_PLUGIN_DIR" -name "${plugin}.so" -delete 2>/dev/null || true
done

# Clear GStreamer plugin cache so new plugin path is rescanned on next start
rm -rf ~/.cache/gstreamer-1.0/

# Write env setup file to be sourced by start_rtvi_vlm.sh
cat > "$INSTALL_DIR/codec_env.sh" <<EOF
export GST_PLUGIN_PATH=${GST_PLUGIN_DIR}\${GST_PLUGIN_PATH:+:\$GST_PLUGIN_PATH}
export LD_LIBRARY_PATH=${LIB_DIR}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
export PATH=${INSTALL_DIR}/usr/bin\${PATH:+:\$PATH}
EOF

rm -rf "$DEBS_DIR"

touch "$INSTALL_DIR/.installed"
echo "Codec installation complete. Env written to $INSTALL_DIR/codec_env.sh"

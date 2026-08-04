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

# Install patent-encumbered multimedia codecs at runtime without root.
#
# Package versions are resolved from Ubuntu's signed Noble metadata instead of
# hard-coded pool URLs, which disappear as security updates supersede them.
# Downloads use HTTPS, apt's metadata hash validation, and retry/backoff.

set -o pipefail

INSTALL_DIR=${CODEC_INSTALL_DIR:-/opt/nvidia/rtvi/codecs}
DEBS_DIR=$(mktemp -d /tmp/rtvi_codec_debs_XXXXXX)
APT_DIR="$DEBS_DIR/apt"
SOURCES_LIST="$APT_DIR/sources.list"

cleanup() {
    rm -rf "$DEBS_DIR"
}
trap cleanup EXIT

HOST_DEB_ARCH=$(dpkg --print-architecture)
DEB_ARCH=${CODEC_DEB_ARCH:-$HOST_DEB_ARCH}
case "$DEB_ARCH" in
    amd64) MACHINE=x86_64 ;;
    arm64) MACHINE=aarch64 ;;
    *)
        echo "ERROR: Unsupported architecture: $DEB_ARCH (supported: amd64, arm64)" >&2
        exit 1
        ;;
esac
LIB_DIR="$INSTALL_DIR/usr/lib/${MACHINE}-linux-gnu"
GST_PLUGIN_DIR="$LIB_DIR/gstreamer-1.0"

if [ "$DEB_ARCH" != "$HOST_DEB_ARCH" ] && [ "${CODEC_VALIDATE_ONLY:-false}" != "true" ]; then
    echo "ERROR: Cross-architecture resolution is supported only with CODEC_VALIDATE_ONLY=true" >&2
    exit 1
fi

if [ "${CODEC_VALIDATE_ONLY:-false}" != "true" ] && [ -f "$INSTALL_DIR/.installed" ]; then
    echo "Proprietary codecs already installed at $INSTALL_DIR"
    exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "ERROR: apt-get is required to resolve Ubuntu codec packages" >&2
    exit 1
fi

KEYRING=/usr/share/keyrings/ubuntu-archive-keyring.gpg
if [ ! -r "$KEYRING" ]; then
    echo "ERROR: Ubuntu archive keyring is unavailable at $KEYRING" >&2
    exit 1
fi
if [ ! -r /etc/ssl/certs/ca-certificates.crt ]; then
    echo "ERROR: The ca-certificates package is required for HTTPS downloads" >&2
    exit 1
fi

PACKAGES=(
    # FFmpeg and GStreamer
    ffmpeg
    libavcodec60
    libavfilter9
    libavformat60
    libavutil58
    libpostproc57
    libswresample4
    libswscale7
    gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad
    gstreamer1.0-plugins-ugly
    gstreamer1.0-libav

    # Video and audio codecs
    libde265-0
    libx265-199
    libx264-164
    libvpx9
    libmpeg2-4
    libxvidcore4
    libdav1d7
    librav1e0
    libvidstab1.1
    libflac12t64
    libmp3lame0
    libmpg123-0t64
    libogg0
    libzvbi0t64
    libshine3
    liba52-0.7.4
    libaribb24-0t64
    libpocketsphinx3
    libsphinxbase3t64

    # Media formats, streaming, and signal processing
    libjxl0.7
    libplacebo338
    libzimg2
    libbluray2
    libudfread0
    libzmq5
    librist4
    librabbitmq4
    libssh-gcrypt-4
    libfftw3-double3
    librubberband2
    libsoxr0
    libmysofa1

    # Runtime dependencies
    libopenblas0-serial
    libblas3
    liblapack3
    libgfortran5
    ocl-icd-libopencl1
    libva-x11-2
    libvdpau1
    libsnappy1v5
    libcodec2-1.2
    libunibreak5
    libpgm-5.3-0t64
    libnorm1t64
    libmbedcrypto7t64
    libhwy1t64
    libcjson1
)

if [ "$DEB_ARCH" = "amd64" ]; then
    PACKAGES+=(libvpl2)
    ARCHIVE_MIRROR=${CODEC_UBUNTU_MIRROR:-https://archive.ubuntu.com/ubuntu}
    SECURITY_MIRROR=${CODEC_UBUNTU_SECURITY_MIRROR:-https://security.ubuntu.com/ubuntu}
else
    ARCHIVE_MIRROR=${CODEC_UBUNTU_MIRROR:-https://ports.ubuntu.com/ubuntu-ports}
    SECURITY_MIRROR=${CODEC_UBUNTU_SECURITY_MIRROR:-$ARCHIVE_MIRROR}
fi

mkdir -p \
    "$APT_DIR/lists/partial" \
    "$APT_DIR/cache"

cat > "$SOURCES_LIST" <<EOF
deb [arch=$DEB_ARCH signed-by=$KEYRING] $ARCHIVE_MIRROR noble main universe
deb [arch=$DEB_ARCH signed-by=$KEYRING] $ARCHIVE_MIRROR noble-updates main universe
deb [arch=$DEB_ARCH signed-by=$KEYRING] $SECURITY_MIRROR noble-security main universe
EOF

APT_OPTIONS=(
    -o "Dir::Etc::sourcelist=$SOURCES_LIST"
    -o "Dir::Etc::sourceparts=-"
    -o "Dir::State::lists=$APT_DIR/lists"
    -o "Dir::Cache=$APT_DIR/cache"
    -o "APT::Architecture=$DEB_ARCH"
    -o "APT::Update::Error-Mode=any"
    -o "Acquire::Retries=${CODEC_DOWNLOAD_RETRIES:-5}"
    -o "Acquire::https::Timeout=${CODEC_DOWNLOAD_TIMEOUT_SECONDS:-60}"
)

echo "Installing proprietary codecs from signed Ubuntu Noble repositories (arch=$DEB_ARCH)..."
echo "Refreshing signed package metadata over HTTPS..."
if ! apt-get "${APT_OPTIONS[@]}" update; then
    echo "ERROR: Failed to refresh signed Ubuntu package metadata" >&2
    exit 1
fi

if [ "${CODEC_VALIDATE_ONLY:-false}" = "true" ]; then
    echo "Resolving every codec artifact without downloading..."
    URI_OUTPUT=$(apt-get "${APT_OPTIONS[@]}" --print-uris download "${PACKAGES[@]}") || {
        echo "ERROR: Failed to resolve one or more codec packages" >&2
        exit 1
    }
    HTTPS_ARTIFACT_COUNT=$(grep -c "^'https://" <<< "$URI_OUTPUT" || true)
    if [ "$HTTPS_ARTIFACT_COUNT" -ne ${#PACKAGES[@]} ]; then
        echo "ERROR: Expected ${#PACKAGES[@]} HTTPS artifacts, resolved $HTTPS_ARTIFACT_COUNT" >&2
        printf '%s\n' "$URI_OUTPUT" >&2
        exit 1
    fi
    echo "Validated $HTTPS_ARTIFACT_COUNT signed HTTPS codec artifacts."
    exit 0
fi

mkdir -p "$INSTALL_DIR"

echo "Downloading ${#PACKAGES[@]} codec packages with apt retry and hash validation..."
if ! (
    cd "$DEBS_DIR" &&
        apt-get "${APT_OPTIONS[@]}" download "${PACKAGES[@]}"
); then
    echo "ERROR: Failed to download one or more codec packages" >&2
    exit 1
fi

shopt -s nullglob
DEBS=("$DEBS_DIR"/*.deb)
shopt -u nullglob
if [ ${#DEBS[@]} -ne ${#PACKAGES[@]} ]; then
    echo "ERROR: Expected ${#PACKAGES[@]} codec packages, downloaded ${#DEBS[@]}" >&2
    exit 1
fi

echo "Extracting ${#DEBS[@]} packages..."
for deb in "${DEBS[@]}"; do
    if ! dpkg -x "$deb" "$INSTALL_DIR/"; then
        echo "ERROR: Failed to extract $deb" >&2
        exit 1
    fi
done

[ -f "$LIB_DIR/blas/libblas.so.3" ] &&
    ln -sf "$LIB_DIR/blas/libblas.so.3" "$LIB_DIR/libblas.so.3"
[ -f "$LIB_DIR/lapack/liblapack.so.3" ] &&
    ln -sf "$LIB_DIR/lapack/liblapack.so.3" "$LIB_DIR/liblapack.so.3"

[ -f "$INSTALL_DIR/usr/bin/ffmpeg" ] &&
    mv "$INSTALL_DIR/usr/bin/ffmpeg" "$INSTALL_DIR/usr/bin/ffmpeg_for_overlay_video"

for plugin in libgstspandsp libgstopenh264 libgstvoaacenc libgstfaad libgstdtsdec \
    libgstdvdread libgstmpeg2enc libgstmplex libgstresindvd libgstladspa \
    libgstzxing libgstneonhttpsrc libgstfluidsynthmidi libgstdirectfb \
    libgstaasink libgstcacasink; do
    find "$GST_PLUGIN_DIR" -name "${plugin}.so" -delete 2>/dev/null || true
done

rm -rf ~/.cache/gstreamer-1.0/

cat > "$INSTALL_DIR/codec_env.sh" <<EOF
export GST_PLUGIN_PATH=${GST_PLUGIN_DIR}\${GST_PLUGIN_PATH:+:\$GST_PLUGIN_PATH}
export LD_LIBRARY_PATH=${LIB_DIR}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
export PATH=${INSTALL_DIR}/usr/bin\${PATH:+:\$PATH}
EOF

touch "$INSTALL_DIR/.installed"
echo "Codec installation complete. Env written to $INSTALL_DIR/codec_env.sh"

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

FROM nvcr.io/nvidia/deepstream:9.1-triton-multiarch AS base

# Use HTTPS for Ubuntu archives; plain HTTP is blocked on some build networks.
RUN find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) \
        -exec sed -i \
        -e 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' \
        -e 's|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' {} + && \
    apt-get update

# Temporarily enable source repositories for apt-get source commands
RUN sed -i 's/^# deb-src/deb-src/' /etc/apt/sources.list && \
    apt update -y

ENV TZ=America/Los_Angeles
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# DeepStream 9.1 is Ubuntu 24.04-based and supplies Python 3.12. Install only
# the development and virtual-environment packages needed to build dependencies.
RUN DEBIAN_FRONTEND=noninteractive apt install -y curl python3-pip python3.12-venv python3.12-dev

# Make Python 3.12 the default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --set python3 /usr/bin/python3.12 && \
    ln -sf /usr/bin/python3.12 /usr/bin/python

# Upgrade the Ubuntu-provided pip for Python 3.12. ensurepip is disabled for
# Debian/Ubuntu system Python, so use the distro python3-pip package above.
RUN rm -f /usr/lib/python3/dist-packages/distutils-precedence.pth && \
    python3.12 -m pip install --break-system-packages --default-timeout=100 --retries 10 --ignore-installed --upgrade pip setuptools

# Now install other dependencies (they will use Python 3.12). OpenCV is built
# separately without FFmpeg below; distro OpenCV would pull FFmpeg back in.
RUN DEBIAN_FRONTEND=noninteractive apt install -y \
    libssl-dev uuid-dev cython3 libblas-dev liblapack-dev cmake libsm6 bc \
    ca-certificates g++ make pkg-config libjpeg-dev libpng-dev libgtk-3-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# CVE Mitigation: Remove vulnerable libtiff packages
RUN DEBIAN_FRONTEND=noninteractive apt remove -y libtiffxx5 libtiff5 || true && \
    apt autoremove -y && \
    apt autoclean

# CVE Mitigation: Upgrade sudo to fix CVE-2026-35535
# (1.9.15p5-3ubuntu5.24.04.1 → 1.9.15p5-3ubuntu5.24.04.2).
# Must run BEFORE the gnupg-from-source step below purges gpgv — once
# gpgv is gone, apt cannot verify repo signatures and any later apt
# install fails with exit 100.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt install -y --only-upgrade sudo && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# CVE Mitigation: Upgrade PackageKit packages to fix CVE-2026-41651
# (1.2.8-2ubuntu1.2 → 1.2.8-2ubuntu1.5). Must also run before the
# gnupg-from-source step for the same reason as sudo above.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt install -y --only-upgrade \
    packagekit \
    libpackagekit-glib2-18 \
    gir1.2-packagekitglib-1.0 && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# CVE Mitigation: If Unbound is installed, upgrade it to fix CVE-2026-33278 in
# the DNSSEC validator. Ubuntu 24.04 fixes the issue in 1.19.2-1ubuntu3.8. Keep
# this before the GnuPG source build below purges gpgv and prevents later apt
# operations. An absent package is not vulnerable and does not fail the build.
RUN apt-get update && \
    set -eu; \
    if dpkg-query -W -f='${db:Status-Abbrev}' libunbound8 2>/dev/null | grep -q '^ii'; then \
        DEBIAN_FRONTEND=noninteractive apt install -y --only-upgrade libunbound8; \
        installed_version="$(dpkg-query -W -f='${Version}' libunbound8)"; \
        if ! dpkg --compare-versions "$installed_version" ge 1.19.2-1ubuntu3.8; then \
            echo "libunbound8 $installed_version is older than required version 1.19.2-1ubuntu3.8 (CVE-2026-33278 is not fixed)" >&2; \
            exit 1; \
        fi; \
    else \
        echo 'libunbound8 is not installed; CVE-2026-33278 is not applicable'; \
    fi; \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# CVE Mitigation: Build GnuPG 2.4.9 from source to fix CVE in armor_filter (g10/armor.c)
# Ubuntu repos only have 2.4.4, need 2.4.9+ for the fix
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    bzip2 libgnutls28-dev libsqlite3-dev libreadline-dev \
    libldap2-dev texinfo gettext libgpg-error-dev libgcrypt20-dev \
    libksba-dev libassuan-dev libnpth0-dev && \
    cd /tmp && \
    curl -LO https://gnupg.org/ftp/gcrypt/gnupg/gnupg-2.4.9.tar.bz2 && \
    tar xjf gnupg-2.4.9.tar.bz2 && \
    cd gnupg-2.4.9 && \
    ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var && \
    make -j$(nproc) && \
    make install && \
    cd / && rm -rf /tmp/gnupg-2.4.9 /tmp/gnupg-2.4.9.tar.bz2 && \
    # Remove old gnupg packages from dpkg database to clear CVE flags
    # Use dpkg --purge --force-depends to remove without breaking dependencies
    dpkg --purge --force-depends gnupg gnupg2 gnupg-l10n gnupg-utils gpg gpg-agent gpgconf gpgsm dirmngr keyboxd gpgv || true


# Remove old files from all Python site-packages locations
RUN rm -rf /usr/lib/python3/dist-packages/wheel* \
           /usr/lib/python3/dist-packages/jaraco* \
           /usr/lib/python3/dist-packages/starlette* \
           /usr/lib/python3.12/dist-packages/wheel* \
           /usr/lib/python3.12/dist-packages/jaraco* \
           /usr/lib/python3.12/dist-packages/starlette* \
           /usr/local/lib/python3.12/dist-packages/wheel* \
           /usr/local/lib/python3.12/dist-packages/jaraco* \
           /usr/local/lib/python3.12/dist-packages/setuptools* \
           /usr/local/lib/python3.12/dist-packages/starlette* \
           2>/dev/null || true

# Install fixed versions for inherited Python 3.12 packages. Do not mask an
# install failure: the image must not retain a vulnerable Starlette copy.
RUN python3.12 -m pip install --break-system-packages "setuptools>=75.0.0" "starlette==1.6.0" "jaraco-context>=6.1.0" "wheel>=0.46.1" && \
    python3.12 -c 'from importlib.metadata import version; assert tuple(map(int, version("starlette").split(".")[:3])) >= (1, 3, 1), version("starlette")' && \
    python3.12 -m pip check
RUN python3.12 -m pip install --break-system-packages --ignore-installed "setuptools>=75.0.0" "jaraco-context>=6.1.0" "wheel>=0.46.2"

# CVE Mitigation: Remove Nsight Systems CLI. AMC does not invoke nsys, but the
# bundled efa_metrics plugin ships a Go 1.23.4 binary (nic_sampler) that
# triggers dozens of Go-stdlib CVEs on every scan. Dropping the whole
# NsightSystems-cli-* directory removes the scanner hits and saves image size.
RUN rm -rf /usr/local/cuda*/NsightSystems-cli-* || true

# CVE Mitigation: Remove old vendored jaraco.context 5.3.0 and wheel 0.45.1
# metadata from python3.12's setuptools/_vendor/. setuptools 75.x still ships
# these older .dist-info entries, triggering GHSA-58pv-8j8x-9vj2 (jaraco.context)
# and GHSA-8rrh-rw8j-w5fx (wheel).
RUN rm -rf /usr/local/lib/python3.12/dist-packages/setuptools/_vendor/jaraco.context-5.3.0* \
           /usr/local/lib/python3.12/dist-packages/setuptools/_vendor/wheel-0.45.1*


# Install packages with preference for pre-built wheels (use --ignore-installed to bypass Debian conflicts)
COPY requirements.txt /tmp/
COPY server/requirements.txt /tmp/server_requirements.txt
COPY server/docker/build_opencv_without_ffmpeg.sh /usr/local/sbin/build-opencv-without-ffmpeg
COPY server/docker/remove_nonfree_codecs.sh /usr/local/sbin/remove-nonfree-codecs
# lu-vp-detect declares opencv-contrib-python, whose wheel bundles FFmpeg.
# Install it without dependencies because NumPy and the custom OpenCV build are
# supplied explicitly. Keep all installs and the guard in one layer so a wheel
# can never be committed to image history if dependency resolution changes.
RUN pip3 install --break-system-packages --default-timeout=100 --retries 10 --ignore-installed \
        -r /tmp/requirements.txt && \
    pip3 install --break-system-packages --default-timeout=100 --retries 10 --ignore-installed \
        -r /tmp/server_requirements.txt && \
    pip3 install --break-system-packages --default-timeout=100 --retries 10 --no-deps \
        lu-vp-detect==1.0.4 && \
    python3.12 -c 'from fastapi import FastAPI; from importlib.metadata import version; assert tuple(map(int, version("starlette").split(".")[:3])) >= (1, 3, 1), version("starlette"); FastAPI()' && \
    ! python3.12 -m pip list --format=freeze \
        | grep -Ei '^opencv(-contrib)?-python(-headless)?=='
RUN chmod 0755 /usr/local/sbin/build-opencv-without-ffmpeg && \
    /usr/local/sbin/build-opencv-without-ffmpeg

# ORSB mitigation: remove non-free codec payloads inherited from the base image
# or dependencies, including Intel Media SDK/MFX and unused AC-3, AMR-WB, MP2,
# and DV implementations, and fail if any prohibited runtime is introduced
# again. NVIDIA's own DeepStream codec plugins are intentionally retained.
RUN chmod 0755 /usr/local/sbin/remove-nonfree-codecs && \
    /usr/local/sbin/remove-nonfree-codecs

# No more packages are installed below. Do not refresh indexes after the GnuPG
# replacement removes distro gpgv; APT can no longer verify repository metadata.
RUN sed -i 's/^deb-src/# deb-src/' /etc/apt/sources.list && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN ln -sf /usr/bin/python3.12 /usr/bin/python3

# Create a local user for the container with GID 1000
RUN useradd -g 1000 -m auto-calibration-ms
ENV HOME=/home/auto-calibration-ms
RUN chown -R 1000:1000 $HOME

################################################################################
# RELEASE stage
################################################################################
FROM base AS release

COPY --chown=1000:1000 core $HOME/core

COPY --chown=1000:1000 server $HOME/server
COPY --chown=1000:1000 service_scripts $HOME/service_scripts
COPY --chown=1000:1000 models $HOME/models
COPY --chown=1000:1000 configs $HOME/configs
COPY --chown=1000:1000 submodules $HOME/submodules

USER 1000:1000

WORKDIR $HOME/server
ENV PYTHONPATH=$HOME
ENV API_VERSION_PREFIX=v1

LABEL com.nvidia.mm.nspect="NSPECT-P3RP-LLGW"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

################################################################################
# DEV stage
################################################################################
FROM base AS dev

COPY --chown=1000:1000 server $HOME/server
COPY --chown=1000:1000 core $HOME/core
COPY --chown=1000:1000 service_scripts $HOME/service_scripts
COPY --chown=1000:1000 models $HOME/models
COPY --chown=1000:1000 configs $HOME/configs
COPY --chown=1000:1000 submodules $HOME/submodules

USER 1000:1000

WORKDIR $HOME/server
ENV PYTHONPATH=$HOME
ENV API_VERSION_PREFIX=v1

LABEL com.nvidia.mm.nspect="NSPECT-P3RP-LLGW"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

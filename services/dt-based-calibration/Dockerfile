# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
ARG IMAGE_ARCH=amd64
ARG PYTHON_VERSION=3.12
ARG TAG=v3.5.2
ARG RUN_VERSION=3.14
ARG RUN_TAG=v4.0.9
ARG IMG=nvcr.io/nvidian/distroless/python:${PYTHON_VERSION}-${TAG}
############################################################################################
# Create base container
############################################################################################
FROM --platform=$IMAGE_ARCH $IMG AS base
FROM --platform=$IMAGE_ARCH nvcr.io/nvidian/distroless/python:${RUN_VERSION}-${RUN_TAG} AS base_runner
############################################################################################


############################################################################################
# Build your app and download uv
############################################################################################
FROM --platform=$IMAGE_ARCH python:${PYTHON_VERSION}-bookworm AS builder

# container used to and download poetry
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl zip wget && \
    # Create your required directories
    mkdir -p /usr/lib/aarch64-linux-gnu /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu /lib/aarch64-linux-gnu/ /lib64 && \
    # Cleanup to keep image size small
    apt-get clean && rm -rf /var/lib/apt/lists/*


############################
# build your app here
############################
WORKDIR /root/user

# 2. Leverage Docker Caching: Copy ONLY lock/config files first
# This ensures that if your code changes but dependencies don't, 
# this layer stays cached and doesn't re-download anything.
COPY pyproject.toml uv.lock ./

# This installs everything in your lockfile + PyInstaller 
# directly into the system environment

# 3. Sync dependencies into the system environment
# --frozen: ensures uv doesn't try to update the lockfile
# --no-dev: skips development-only tools
ENV UV_PROJECT_ENVIRONMENT="/usr/local"
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/dependency_bundle && mkdir -p /app/package_licenses && mkdir -p /app/source_code

# 4 Install the locked dependencies directly into that folder
RUN uv export --frozen --format requirements-txt > requirements-frozen.txt && \
    uv pip install \
    --target /app/dependency_bundle \
    -r requirements-frozen.txt

RUN apt-get update && apt-get install zip -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN uv pip install --system pyinstaller

# Create an isolated venv for supervisor so pip installs pkg_resources from scratch,
# bypassing any "already installed" skip behaviour for setuptools.
RUN python3 -m venv /opt/supervisor-venv && \
    /opt/supervisor-venv/bin/pip install --no-cache-dir "setuptools<71" && \
    /opt/supervisor-venv/bin/pip install --no-cache-dir supervisor==4.2.5

# Copy all application source code
COPY src/ ./

RUN ls -F utils/

# 5. Build the binaries
#RUN python -m PyInstaller -F /root/user/api.py --onefile --name api --paths . --hidden-import="utils" --hidden-import="utils.carla_utils"  --collect-all rasterio 
RUN PYTHONPATH=. python -m PyInstaller --onefile \
    --name api \
    --paths . \
    --collect-all rasterio \
    --collect-all cv2 \
    --add-data "utils:utils" \
    --hidden-import utils.carla_utils \
    --hidden-import utils.viz_utils \
    api.py

RUN PYTHONPATH=. python -m PyInstaller --onefile \
    --name accuracy \
    --paths . \
    --collect-all rasterio \
    --collect-all cv2 \
    accuracy.py

RUN PYTHONPATH=. python -m PyInstaller --onefile \
    --name file_upload_based_calibration \
    --paths . \
    --collect-all rasterio \
    --collect-all cv2 \
    --collect-data gradio \
    --collect-data gradio_client \
    --add-data "utils:utils" \
    --collect-all safehttpx \
    --collect-all groovy \
    --collect-all gradio \
    --hidden-import="autocalibration" \
    --hidden-import utils.carla_utils \
    --hidden-import utils.viz_utils \
    demo_web.py

RUN PYTHONPATH=. python -m PyInstaller --onefile \
    --name manual_assisted_based_calibration \
    --paths . \
    --collect-all rasterio \
    --collect-all cv2 \
    --collect-data gradio \
    --collect-data gradio_client \
    --add-data "utils:utils" \
    --collect-all safehttpx \
    --collect-all groovy \
    --collect-all gradio \
    --hidden-import="autocalibration" \
    --hidden-import utils.carla_utils \
    --hidden-import utils.viz_utils \
    manual_calibration_web.py

# copy Dependencies
# GeographicLib is a small C++ library for: geodesic and rhumb line calculations; conversions between geographic, UTM, UPS, MGRS, geocentric, and local cartesian coordinates; gravity (e.g., EGM2008) and geomagnetic field (e.g., WMM2020) calculations.
RUN wget 'https://master.dl.sourceforge.net/project/geographiclib/geoids-distrib/egm96-15.zip'
RUN unzip egm96-15.zip 

RUN mkdir -p /tmp/gradio/vibe_edit_history && chmod -R 777 /tmp/

# Copy Source Code
COPY pyproject.toml uv.lock src /app/source_code/

############################################################################################
# install python dependencies (supervisor)
############################################################################################
FROM base_runner AS py-builder

# 1. Grab the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY --from=builder /etc/passwd /etc/passwd
COPY --from=builder /etc/group /etc/group

# Copy required system libraries for foreign function interfaces
COPY --from=builder /usr/lib/x86_64-linux-gnu/libffi.so.* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/aarch64-linux-gnu/libffi.so.* /usr/lib/aarch64-linux-gnu/

USER root:root
WORKDIR /app
############################################################################################


############################################################################################
# Deploy the app
############################################################################################
FROM base_runner AS py-deploy

WORKDIR /opt
# Copy supervisor scripts and packages from the isolated venv where pip installs everything.
COPY --from=builder /opt/supervisor-venv/bin/supervisor* /usr/local/bin/
COPY --from=builder /opt/supervisor-venv/lib/python3.12/site-packages/supervisor /opt/supervisor-pkgs/supervisor
COPY --from=builder /opt/supervisor-venv/lib/python3.12/site-packages/pkg_resources /opt/supervisor-pkgs/pkg_resources
ENV PYTHONPATH="/opt/supervisor-pkgs"
# Copy geoid elements
COPY --from=builder /root/user/geoids /opt/geoids

COPY supervisord.conf /etc/supervisord.conf

#WORKDIR /opt

COPY src/index.html /opt/index.html
#COPY src/autocalibration.py /opt/autocalibration.py
COPY src/carla_camera_projection_demo.py /opt/carla_camera_projection_demo.py



####### Copy Cource Code/OSS/3rd_Party_Notice for users access since running codes are in binaries#######
# Copy License
COPY 3rd_Party_Notice /opt/3rd_Party_Notice
# # Copy Third Party OSS
# COPY --from=builder /app/dependency_bundle /opt/dependency_bundle
# Copy Source Code
COPY --from=builder /app/source_code /opt/source_code
#########################################################################################################


###### Copy Deployment binary apps to opt #################################################################
COPY --from=builder /root/user/dist/api /opt/api
COPY --from=builder /root/user/dist/accuracy /opt/accuracy
COPY --from=builder /root/user/dist/file_upload_based_calibration /opt/file_upload_based_calibration
COPY --from=builder /root/user/dist/manual_assisted_based_calibration /opt/manual_assisted_based_calibration
COPY --from=builder /tmp /tmp
############################################################################################################

# DO NOT USE ENTRYPOINT
CMD ["python3", "/usr/local/bin/supervisord"]
############################################################################################################

# Dockerfile for VSS Edge Node Services
# This Dockerfile is designed to be built on an NVIDIA Jetson device (ARM64 architecture)
# or a system with NVIDIA GPU support.

# --- Base Image ---
# Use an NVIDIA L4T (Linux for Tegra) base image for Jetson devices.
# Replace with the actual image tag for your Jetson OS version.
# FROM nvcr.io/nvidia/l4t-base:r32.7.1-runtime
# For x86_64 development/testing with CUDA:
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

# --- Metadata ---
LABEL maintainer="Manus AI"
LABEL description="Video Search & Summarization Edge Node Services"

# --- Environment Variables ---
ENV PYTHONUNBUFFERED 1
ENV VSS_CONFIG_PATH /etc/vss/config.yaml
ENV VSS_DB_PATH /var/lib/vss/vss_events.db
ENV VSS_CLIPS_PATH /var/lib/vss/clips
ENV VSS_MODELS_PATH /opt/vss/models

# --- System Dependencies ---
# Install system dependencies, including ffmpeg for video processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        python3-pip \
        python3-dev \
        build-essential \
        libgl1-mesa-glx \
        libsm6 \
        libxext6 \
        libxrender1 \
        git \
        tar \
        # Add any other necessary libraries for ONVIF/CV models
    && rm -rf /var/lib/apt/lists/*

# --- Python Dependencies ---
WORKDIR /app
COPY requirements.txt .

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# --- Application Code ---
# Copy the entire edge_node and config directories
COPY edge_node /app/edge_node
COPY config /app/config

# Create necessary runtime directories
RUN mkdir -p ${VSS_CLIPS_PATH} ${VSS_MODELS_PATH} /etc/vss && \
    # Set permissions for runtime directories
    chmod -R 777 ${VSS_CLIPS_PATH} ${VSS_MODELS_PATH}

# Copy the default config to the expected runtime location
COPY config/config.yaml ${VSS_CONFIG_PATH}

# --- Entrypoint (Example for vss_aggregator) ---
# The actual entrypoint will be defined in docker-compose.yaml for each service.
# CMD ["uvicorn", "edge_node.services.vss_aggregator:app", "--host", "0.0.0.0", "--port", "8002"]

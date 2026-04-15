#!/bin/bash
# Setup script — runs on the Brev instance to prepare for VSS deployment.
# Idempotent: safe to run multiple times.
set -euo pipefail

REPO_DIR=/home/ubuntu/video-search-and-summarization
REPO_URL=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
BRANCH=feat/skills

# Clone VSS repo if not present
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning VSS repo..."
    git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# Ensure Docker is installed
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
fi

# Ensure NVIDIA Container Toolkit
if ! docker info 2>/dev/null | grep -q nvidia; then
    echo "Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
fi

# Load GPU modules if needed
if ! nvidia-smi &>/dev/null; then
    sudo modprobe nvidia 2>/dev/null || true
    sudo modprobe nvidia_uvm 2>/dev/null || true
fi

# Kernel settings for Elasticsearch/Kafka
sudo sysctl -w vm.max_map_count=262144 2>/dev/null || true
sudo sysctl -w net.core.rmem_max=5242880 2>/dev/null || true
sudo sysctl -w net.core.wmem_max=5242880 2>/dev/null || true

# Create data directory
mkdir -p "$REPO_DIR/data"

echo "Setup complete."
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || echo "No GPU detected"

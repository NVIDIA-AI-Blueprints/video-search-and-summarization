#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# VST BDD Tests - Complete Setup Script
# This script checks and installs everything needed to run the BDD tests

# Color codes for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m' # No Color

# Script directory
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MIN_PYTHON_VERSION="3.8"

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Function to compare version numbers
version_ge() {
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# Check if running on supported OS
check_os() {
    if [[ ! -f /etc/os-release ]]; then
        print_error "Cannot detect OS. This script supports Ubuntu/Debian systems."
        return 1
    fi

    . /etc/os-release
    if [[ "${ID}" != "ubuntu" ]] && [[ "${ID}" != "debian" ]]; then
        print_warning "Detected OS: ${NAME}. This script is optimized for Ubuntu/Debian."
        print_info "Some features may not work as expected."
    else
        print_success "Detected OS: ${NAME} ${VERSION}"
    fi
}

# Check if Python is installed and meets version requirements
check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        print_error "Python 3 is not installed"
        return 1
    fi

    local python_version
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    
    if version_ge "${python_version}" "${MIN_PYTHON_VERSION}"; then
        print_success "Python ${python_version} is installed (>= ${MIN_PYTHON_VERSION})"
        return 0
    else
        print_error "Python ${python_version} is installed but version >= ${MIN_PYTHON_VERSION} is required"
        return 1
    fi
}

# Install Python if needed
install_python() {
    print_info "Installing Python 3..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-dev python3-venv
    
    if check_python; then
        print_success "Python installed successfully"
        return 0
    else
        print_error "Failed to install Python"
        return 1
    fi
}

# Check if Poetry is installed
check_poetry() {
    if command -v poetry >/dev/null 2>&1; then
        local poetry_version
        poetry_version=$(poetry --version 2>&1 | awk '{print $3}' | sed 's/)//')
        print_success "Poetry ${poetry_version} is installed"
        return 0
    else
        print_error "Poetry is not installed"
        return 1
    fi
}

# Install Poetry
install_poetry() {
    print_info "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    
    # Add Poetry to PATH for this session
    export PATH="${HOME}/.local/bin:${PATH}"
    
    if check_poetry; then
        print_success "Poetry installed successfully"
        print_warning "You may need to restart your shell or run: export PATH=\"\${HOME}/.local/bin:\${PATH}\""
        return 0
    else
        print_error "Failed to install Poetry"
        return 1
    fi
}

# Check if pkg-config is installed
check_pkg_config() {
    if command -v pkg-config >/dev/null 2>&1; then
        print_success "pkg-config is installed"
        return 0
    else
        print_error "pkg-config is not installed"
        return 1
    fi
}

# Install pkg-config
install_pkg_config() {
    print_info "Installing pkg-config..."
    sudo apt-get install -y pkg-config
    
    if check_pkg_config; then
        print_success "pkg-config installed successfully"
        return 0
    else
        print_error "Failed to install pkg-config"
        return 1
    fi
}

# Check system dependencies
check_system_dependencies() {
    local all_installed=0
    
    # Check jpeginfo
    if command -v jpeginfo >/dev/null 2>&1; then
        print_success "jpeginfo is installed"
    else
        print_error "jpeginfo is not installed"
        all_installed=1
    fi
    
    # Check mediainfo
    if command -v mediainfo >/dev/null 2>&1; then
        print_success "mediainfo is installed"
    else
        print_error "mediainfo is not installed"
        all_installed=1
    fi
    
    # Check ffmpeg
    if command -v ffmpeg >/dev/null 2>&1; then
        local ffmpeg_version
        ffmpeg_version=$(ffmpeg -version 2>&1 | head -n1 | awk '{print $3}')
        print_success "ffmpeg ${ffmpeg_version} is installed"
    else
        print_error "ffmpeg is not installed"
        all_installed=1
    fi
    
    # Check libav libraries
    if pkg-config --exists libavformat libavcodec libavutil 2>/dev/null; then
        print_success "libav libraries are installed"
    else
        print_error "libav libraries are not installed"
        all_installed=1
    fi
    
    return "${all_installed}"
}

# Install system dependencies
install_system_dependencies() {
    print_info "Installing system dependencies..."
    
    local packages=(
        jpeginfo
        mediainfo
        ffmpeg
        libavformat-dev
        libavcodec-dev
        libavdevice-dev
        libavutil-dev
        libswscale-dev
        libswresample-dev
        libavfilter-dev
        libopus-dev
        libvpx-dev
        pkg-config
    )
    
    sudo apt-get update
    sudo apt-get install -y "${packages[@]}"
    
    if check_system_dependencies; then
        print_success "All system dependencies installed successfully"
        return 0
    else
        print_warning "Some system dependencies may not have installed correctly"
        return 0
    fi
}

# Install Python dependencies using Poetry
install_python_dependencies() {
    print_info "Installing Python dependencies with Poetry..."
    
    cd "${SCRIPT_DIR}"
    
    # Configure Poetry to create virtualenv in project
    poetry config virtualenvs.in-project true
    
    # Install dependencies
    if poetry install --no-interaction; then
        print_success "Python dependencies installed successfully"
        return 0
    else
        print_error "Failed to install Python dependencies"
        return 1
    fi
}

# Create necessary directories
create_directories() {
    print_info "Creating necessary directories..."
    
    local dirs=(
        "${SCRIPT_DIR}/reports"
        "/tmp/vst_test_images"
        "/tmp/vst_test_downloads"
    )
    
    for dir in "${dirs[@]}"; do
        if [[ ! -d "${dir}" ]]; then
            mkdir -p "${dir}"
            print_success "Created directory: ${dir}"
        else
            print_success "Directory already exists: ${dir}"
        fi
    done
}

# Verify installation
verify_installation() {
    print_info "Verifying installation..."
    
    cd "${SCRIPT_DIR}"
    
    if poetry run python -c "import pytest, pytest_bdd, requests, aiohttp, aiortc, websockets; print('All Python modules imported successfully')"; then
        print_success "All Python dependencies are working correctly"
        return 0
    else
        print_error "Some Python dependencies are not working correctly"
        return 1
    fi
}

# Main function
main() {
    echo "=========================================="
    echo "VST BDD Tests - Setup Script"
    echo "=========================================="
    echo ""
    
    # Check OS
    echo "1. Checking operating system..."
    check_os
    echo ""
    
    # Check and install Python
    echo "2. Checking Python installation..."
    if ! check_python; then
        echo ""
        read -rp "Python ${MIN_PYTHON_VERSION}+ is required. Install it? [y/N]: " response
        if [[ "${response}" =~ ^[Yy]$ ]]; then
            install_python || exit 1
        else
            print_error "Python is required to continue"
            exit 1
        fi
    fi
    echo ""
    
    # Check and install pkg-config
    echo "3. Checking pkg-config..."
    if ! check_pkg_config; then
        echo ""
        read -rp "pkg-config is required. Install it? [y/N]: " response
        if [[ "${response}" =~ ^[Yy]$ ]]; then
            install_pkg_config || exit 1
        else
            print_error "pkg-config is required to continue"
            exit 1
        fi
    fi
    echo ""
    
    # Check and install Poetry
    echo "4. Checking Poetry installation..."
    if ! check_poetry; then
        echo ""
        read -rp "Poetry is required for dependency management. Install it? [y/N]: " response
        if [[ "${response}" =~ ^[Yy]$ ]]; then
            install_poetry || exit 1
            export PATH="${HOME}/.local/bin:${PATH}"
        else
            print_error "Poetry is required to continue"
            exit 1
        fi
    fi
    echo ""
    
    # Check and install system dependencies
    echo "5. Checking system dependencies..."
    if ! check_system_dependencies; then
        echo ""
        read -rp "System dependencies are missing. Install them? [y/N]: " response
        if [[ "${response}" =~ ^[Yy]$ ]]; then
            install_system_dependencies || exit 1
        else
            print_warning "Some tests may fail without system dependencies"
        fi
    fi
    echo ""
    
    # Install Python dependencies
    echo "6. Installing Python dependencies..."
    install_python_dependencies || exit 1
    echo ""
    
    # Create necessary directories
    echo "7. Creating necessary directories..."
    create_directories
    echo ""
    
    # Verify installation
    echo "8. Verifying installation..."
    verify_installation || exit 1
    echo ""
    
    echo "=========================================="
    print_success "Setup completed successfully!"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo ""
    echo "1. Configure your VST API endpoint:"
    echo "   Edit config.json and set 'api.base_url'"
    echo ""
    echo "2. Run tests:"
    echo "   cd ${SCRIPT_DIR}"
    echo "   poetry run pytest tests/"
    echo ""
    echo "Additional commands:"
    echo "   Run specific category:     poetry run pytest tests/file_upload/"
    echo "   Run with repetition:       poetry run pytest tests/ --count=3"
    echo "   Run in parallel:           poetry run pytest tests/ -n auto"
    echo "   View HTML report:          xdg-open reports/report.html"
    echo "   View container stats:      xdg-open reports/stats/container_stats_viewer.html"
    echo "   View latency results:      xdg-open reports/latency/latency_viewer.html"
    echo ""
}

# Run main function
main "$@"

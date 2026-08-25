######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

# Python extension module for GStreamer SEI metadata bindings
MODULE_NAME = gst_video_sei_meta
SOURCE = gst_video_sei_meta_binding.cpp

# Build variables
CXX = g++
CXXFLAGS = -O3 -Wall -shared -std=c++11 -fPIC

DEEPSTREAM_PATH ?= $(shell \
	for path in \
		/opt/nvidia/deepstream/deepstream \
		/opt/nvidia/deepstream/deepstream-9.1; do \
		if [ -f "$$path/sources/includes/gstnvdsseimeta.h" ]; then \
			echo "$$path"; \
			exit 0; \
		fi; \
	done; \
	for path in \
		/opt/nvidia/deepstream/deepstream \
		/opt/nvidia/deepstream/deepstream-9.1; do \
		if [ -d "$$path" ]; then \
			echo "$$path"; \
			exit 0; \
		fi; \
	done; \
	echo /opt/nvidia/deepstream/deepstream-9.1)

# Python configuration (can be overridden, e.g., PYTHON=python3.11)
PYTHON ?= python3
PYTHON_INCLUDES = $(shell $(PYTHON) -m pybind11 --includes)
PYTHON_EXT_SUFFIX = $(shell $(PYTHON)-config --extension-suffix)
SITE_PACKAGES = $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")

GSTREAMER_FLAGS = $(shell pkg-config --cflags --libs gstreamer-1.0)

# Include local directory first, then DeepStream path if available.
DEEPSTREAM_INCLUDE_DIRS ?= $(DEEPSTREAM_PATH)/sources/includes
DEEPSTREAM_INCLUDES = -I. $(addprefix -I,$(DEEPSTREAM_INCLUDE_DIRS))

# Detect architecture (can be overridden by TARGETARCH env var from Docker)
ARCH ?= $(shell uname -m)
TARGETARCH ?= $(ARCH)

# Platform-specific library paths.
DEEPSTREAM_LIB_DIRS ?= $(DEEPSTREAM_PATH)/lib
DEEPSTREAM_LIBS = $(addprefix -L,$(DEEPSTREAM_LIB_DIRS))

# Add Tegra library path for ARM platforms (Jetson)
ifeq ($(TARGETARCH),arm64)
    TEGRA_LIB_DIR = /usr/lib/aarch64-linux-gnu/tegra
    ifneq ($(wildcard $(TEGRA_LIB_DIR)),)
        DEEPSTREAM_LIBS += -L$(TEGRA_LIB_DIR)
    endif
else ifeq ($(TARGETARCH),aarch64)
    TEGRA_LIB_DIR = /usr/lib/aarch64-linux-gnu/tegra
    ifneq ($(wildcard $(TEGRA_LIB_DIR)),)
        DEEPSTREAM_LIBS += -L$(TEGRA_LIB_DIR)
    endif
endif

# Add the library itself
DEEPSTREAM_LIBS += -lgstnvdsseimeta

# Target output
OUTPUT = $(MODULE_NAME)$(PYTHON_EXT_SUFFIX)

all: $(OUTPUT)

$(OUTPUT): $(SOURCE)
	$(CXX) $(CXXFLAGS) $(DEEPSTREAM_INCLUDES) $(PYTHON_INCLUDES) \
		$(SOURCE) -o $(OUTPUT) \
		$(GSTREAMER_FLAGS) $(DEEPSTREAM_LIBS)

clean:
	rm -rf $(MODULE_NAME)*.so

install: $(OUTPUT)
	cp $(OUTPUT) $(SITE_PACKAGES)/

.PHONY: all clean install

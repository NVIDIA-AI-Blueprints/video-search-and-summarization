# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Makefile for GStreamer Timestamp Filter Plugin

# Plugin name
PLUGIN_NAME = gsttimestampfilter

# Source files
SOURCES = gsttimestampfilter.cpp
HEADERS = gsttimestampfilter.h

# Output
TARGET = lib$(PLUGIN_NAME).so

# Compiler and flags
CXX = g++
CXXFLAGS = -fPIC -Wall -O3 -std=c++11

# GStreamer packages
GST_PACKAGES = gstreamer-1.0 gstreamer-base-1.0

# Get GStreamer compile and link flags using pkg-config
GST_CFLAGS := $(shell pkg-config --cflags $(GST_PACKAGES))
GST_LIBS := $(shell pkg-config --libs $(GST_PACKAGES))

# Installation directory
GST_PLUGIN_DIR := $(shell pkg-config --variable=pluginsdir gstreamer-1.0)
INSTALL_DIR ?= $(GST_PLUGIN_DIR)

# If pkg-config fails, use local directory
ifeq ($(INSTALL_DIR),)
	INSTALL_DIR = /usr/lib/*-linux-gnu/gstreamer-1.0
endif

# Build rules
all: $(TARGET)

$(TARGET): $(SOURCES) $(HEADERS)
	@echo "Building GStreamer Timestamp Filter plugin..."
	$(CXX) $(CXXFLAGS) $(GST_CFLAGS) -shared -o $(TARGET) $(SOURCES) $(GST_LIBS) -Wl,--no-undefined
	@echo "✓ Build complete: $(TARGET)"

# Installation
install: $(TARGET)
	@echo "Installing plugin to $(INSTALL_DIR)..."
	@mkdir -p $(INSTALL_DIR)
	cp $(TARGET) $(INSTALL_DIR)/
	@echo "✓ Installed: $(INSTALL_DIR)/$(TARGET)"
	@echo ""
	@echo "Updating GStreamer plugin cache..."
	-gst-inspect-1.0 --gst-plugin-path=$(INSTALL_DIR) $(PLUGIN_NAME)
	@echo "✓ Plugin registered"

# Uninstall
uninstall:
	@echo "Removing plugin from $(INSTALL_DIR)..."
	rm -f $(INSTALL_DIR)/$(TARGET)
	@echo "✓ Uninstalled"

# Test if plugin loads
test: $(TARGET)
	@echo "Testing plugin..."
	GST_PLUGIN_PATH=.:$$GST_PLUGIN_PATH gst-inspect-1.0 timestampfilter

# Clean
clean:
	rm -f $(TARGET) *.o
	@echo "✓ Cleaned"

# Help
help:
	@echo "GStreamer Timestamp Filter Plugin Build"
	@echo ""
	@echo "Targets:"
	@echo "  make          - Build the plugin"
	@echo "  make install  - Install to GStreamer plugin directory"
	@echo "  make test     - Test if plugin loads correctly"
	@echo "  make clean    - Remove built files"
	@echo "  make uninstall- Remove installed plugin"
	@echo ""
	@echo "Variables:"
	@echo "  INSTALL_DIR   - Override installation directory"
	@echo "  CXX           - C++ compiler (default: g++)"
	@echo ""
	@echo "Current settings:"
	@echo "  Install dir: $(INSTALL_DIR)"
	@echo "  GST version: $(shell pkg-config --modversion gstreamer-1.0 2>/dev/null || echo 'NOT FOUND')"

.PHONY: all install uninstall test clean help

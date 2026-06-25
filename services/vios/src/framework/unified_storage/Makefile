# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

PREBUILT_DIR_MODULE=$(TOP)/$(PREBUILT_DIR)

# Target libraries
READER_TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragereader.so
WRITER_TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragewriter.so
MANAGER_TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragemanager.so

# Subdirectories
READER_DIR := reader
WRITER_DIR := writer
MANAGER_DIR := manager

# Default target - build all components for all platforms
all: reader writer manager

# Build cloud reader library
reader:
	@echo "Building Cloud Reader Library..."
	$(MAKE) -C $(READER_DIR)

# Build cloud writer library
writer:
	@echo "Building Cloud Writer Library..."
	$(MAKE) -C $(WRITER_DIR)

# Build cloud manager library
manager:
	@echo "Building Cloud Manager Library..."
	$(MAKE) -C $(MANAGER_DIR)

# Build only reader
reader-only:
	@echo "Building Cloud Reader Library only..."
	$(MAKE) -C $(READER_DIR)

# Build only writer
writer-only:
	@echo "Building Cloud Writer Library only..."
	$(MAKE) -C $(WRITER_DIR)

# Build only manager
manager-only:
	@echo "Building Cloud Manager Library only..."
	$(MAKE) -C $(MANAGER_DIR)

# Clean all components
clean: clean-reader clean-writer clean-manager

# Clean reader
clean-reader:
	@echo "Cleaning Cloud Reader..."
	$(MAKE) -C $(READER_DIR) clean

# Clean writer
clean-writer:
	@echo "Cleaning Cloud Writer..."
	$(MAKE) -C $(WRITER_DIR) clean

# Clean manager
clean-manager:
	@echo "Cleaning Cloud Manager..."
	$(MAKE) -C $(MANAGER_DIR) clean

# Install targets (optional)
install: all
	@echo "Installing cloud storage libraries..."
	@echo "Reader library: $(READER_TARGET)"
	@echo "Writer library: $(WRITER_TARGET)"
	@echo "Manager library: $(MANAGER_TARGET)"

# Check if libraries exist
check:
	@echo "Checking cloud storage libraries..."
	@if [ -f "$(READER_TARGET)" ]; then \
		echo "✓ Cloud Reader library exists: $(READER_TARGET)"; \
	else \
		echo "✗ Cloud Reader library missing: $(READER_TARGET)"; \
	fi
	@if [ -f "$(WRITER_TARGET)" ]; then \
		echo "✓ Cloud Writer library exists: $(WRITER_TARGET)"; \
	else \
		echo "✗ Cloud Writer library missing: $(WRITER_TARGET)"; \
	fi
	@if [ -f "$(MANAGER_TARGET)" ]; then \
		echo "✓ Cloud Manager library exists: $(MANAGER_TARGET)"; \
	else \
		echo "✗ Cloud Manager library missing: $(MANAGER_TARGET)"; \
	fi

# Help target
help:
	@echo "Cloud Storage Build System"
	@echo "=========================="
	@echo ""
	@echo "Available targets:"
	@echo "  all          - Build reader, writer, and manager libraries (default)"
	@echo "  reader       - Build cloud reader library only"
	@echo "  writer       - Build cloud writer library only"
	@echo "  manager      - Build cloud manager library only"
	@echo "  reader-only  - Alias for reader"
	@echo "  writer-only  - Alias for writer"
	@echo "  manager-only - Alias for manager"
	@echo "  clean        - Clean all components"
	@echo "  clean-reader - Clean reader components only"
	@echo "  clean-writer - Clean writer components only"
	@echo "  clean-manager- Clean manager components only"
	@echo "  install      - Install libraries (info only)"
	@echo "  check        - Check if libraries exist"
	@echo "  help         - Show this help message"
	@echo ""
	@echo "Libraries built:"
	@echo "  $(READER_TARGET)"
	@echo "  $(WRITER_TARGET)"
	@echo "  $(MANAGER_TARGET)"

.PHONY: all reader writer manager reader-only writer-only manager-only clean clean-reader clean-writer clean-manager install check help 

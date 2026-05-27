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

TOP ?= ../..

# Import common build variables from main Makefile
# We need CPPFLAGS, CXX, PREBUILT_DIR, etc.
PREBUILT_DIR ?= prebuilts/x86_64
SYSROOT ?=
arch ?= x86_64

# Compiler
CXX ?= g++
DEPFLAGS = -MT $@ -MMD -MP -MF $(@:.o=.d)

# Include paths (minimal set needed for tests)
CPPFLAGS += -std=c++17 -fPIC -pthread \
            -I$(TOP)/include \
            -I$(TOP)/src/framework \
            -I$(TOP)/src/framework/utilities \
            -I$(TOP)/include/3rdparty/Bosma/Scheduler \
            -I$(TOP)/src/modules/storage_management \
            -I$(TOP)/src/modules/sensor_management \
            -I$(TOP)/src/modules/stream_recorder \
            -I$(TOP)/src/app \
            -I$(SYSROOT)/usr/include/jsoncpp

# Test source files
TEST_SRCS := test_main.cpp \
             utils/mock_civetweb.cpp \
             utils/storage_test_utils.cpp \
             utils/sensor_test_utils.cpp \
             utils/recorder_utils.cpp \
             utils/db_test_utils.cpp \
             utils/rtspserver_utils.cpp \
             utils/system_stats_listener.cpp \
             streammonitor.cpp \
             storage.cpp \
             sensor.cpp \
             recorder.cpp

# Object files
TEST_OBJS := $(TEST_SRCS:.cpp=.o)

# GTest configuration
GOOGLE_TEST_INCLUDE := $(SYSROOT)/usr/local/include

# Compiler flags for tests
TEST_CPPFLAGS := $(CPPFLAGS) -fPIC -g -I$(GOOGLE_TEST_INCLUDE) -I.

.PHONY: all clean

# Default target - build all test object files
all: $(TEST_OBJS)
	@echo "============================================"
	@echo "✅ All test object files built successfully"
	@echo "============================================"
	@echo "Test objects created:"
	@for obj in $(TEST_OBJS); do echo "  - $$obj"; done
	@echo ""
	@echo "Main Makefile will link these into vst_test executable"
	@echo "============================================"

# Compile test source files
%.o: %.cpp
	@echo "Compiling test component: $<"
	$(CXX) $(DEPFLAGS) $(TEST_CPPFLAGS) -c $< -o $@

# Clean targets
clean:
	@echo "Cleaning test object files..."
	rm -f $(TEST_OBJS)
	rm -f *.d utils/*.d
	@echo "✓ Test artifacts cleaned"

# Include common dependency rules
include $(TOP)/Makefile.dep

# Include dependency files
-include $(TEST_SRCS:.cpp=.d)


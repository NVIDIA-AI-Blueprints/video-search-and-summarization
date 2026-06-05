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

PREBUILT=$(TOP)/$(PREBUILT_DIR)

# Create prebuilts directory if it doesn't exist
$(shell mkdir -p $(PREBUILT))

TARGET := $(PREBUILT)/libvideosegmentextractor.so
CPP = c++

SRCS := VideoSegmentExtractor.cpp

OBJS := $(SRCS:.cpp=.o)

# Compiler and linker flags - Dynamic loading instead of static linking
CXX ?= g++
CPPFLAGS += -std=c++17 -fPIC -Wall -Wextra -O2 -pthread

# Dynamic loading - no static libav linking needed
LDFLAGS := -rdynamic -shared -ldl

# Dependency generation flags
DEPFLAGS = -MMD -MP -MF $(@:.o=.d)

all: $(TARGET)

%.o: %.cpp
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

# Check dependencies - Runtime libraries will be loaded dynamically
check-deps:
	@echo "Checking runtime LibAV dependencies..."
	@echo "NOTE: LibAV libraries will be loaded dynamically at runtime."
	@echo "Ensure the following runtime libraries are installed:"
	@echo "  Ubuntu/Debian: sudo apt-get install libavformat58 libavcodec58 libavutil56"
	@echo "  CentOS/RHEL: sudo yum install ffmpeg-libs"
	@echo "Dependencies check completed (runtime libraries will be verified at program startup)."

install: $(TARGET)
	@echo "Installing VideoSegmentExtractor..."
	install -d $(DESTDIR)/usr/local/lib
	install -m 755 $(TARGET) $(DESTDIR)/usr/local/lib/
	install -d $(DESTDIR)/usr/local/include/nv_vms
	install -m 644 VideoSegmentExtractor.h $(DESTDIR)/usr/local/include/nv_vms/
	install -m 644 libav_wrapper.h $(DESTDIR)/usr/local/include/nv_vms/

clean:
	@echo "Cleaning..."
	rm -rf $(TARGET)
	rm -rf $(OBJS)
	rm -rf $(OBJS:.o=.d)
	rm -rf segment_*.mp4 test_output.mp4 test_output/ test_input_video.mp4
	rm -rf profiling_report.csv

# Include dependency files if they exist
-include $(OBJS:.o=.d)

.PHONY: all check-deps install clean 
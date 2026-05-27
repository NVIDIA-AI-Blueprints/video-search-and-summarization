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
TARGET := $(PREBUILT)libnvoverlays.so

arch ?= $(shell uname -p)
CPP = c++

# Overlay source files
SRCS := ll_overlay.cpp \
		overlay_internal.cpp \
		halo_safety.cpp

OBJS := $(SRCS:.cpp=.o)

# Include paths for overlays module
CPPFLAGS += -I $(TOP)/src/framework/media/overlays \
			-I $(TOP)/src/framework/media/media_utils \
			-I $(TOP)/src/framework/media/media_pipelines \
			-I $(TOP)/src/framework/stream_monitor \
			-I $(TOP)/src/framework/webrtc_streamer/inc \
			-I $(TOP)/src/framework/utilities \
			-I $(TOP)/src/framework/platform_specific \
			-I $(TOP)/src/framework \
			-I $(TOP)/include

# Link flags for overlay library
LDFLAGS := -L$(PREBUILT) -ljsoncpp -lmmutils -rdynamic -shared

ifeq ($(arch),x86_64)
	LDFLAGS += `pkg-config --libs opencv4`
else
	CPPFLAGS += -I $(SYSROOT)/usr/include/opencv4/
	LDFLAGS += -L $(SYSROOT)/usr/lib/aarch64-linux-gnu/ $(SYSROOT)/usr/lib/aarch64-linux-gnu/lapack/liblapack.so.3
	LDFLAGS += $(SYSROOT)/usr/lib/aarch64-linux-gnu/blas/libblas.so.3 -lopencv_core -lopencv_imgproc -lopencv_imgcodecs
ifneq ($(JETSON_PLATFORM),) # Jetson only
	LDFLAGS += -lnvdsgst_ipcmeta
endif
endif

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

-include $(TOP)/Makefile.dep

clean:
	$(AT)rm -rf $(TARGET) $(OBJS)
	$(AT)find . -name "*.d" -delete

.PHONY: all clean

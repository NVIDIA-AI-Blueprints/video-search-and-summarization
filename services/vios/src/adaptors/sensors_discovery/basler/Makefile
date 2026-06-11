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

# Basler pylon SDK location. Override with: make PYLON_ROOT=/path/to/pylon
PYLON_ROOT ?= /opt/pylon

# Verify pylon-config exists - hard fail with a helpful message if not.
PYLON_CONFIG := $(PYLON_ROOT)/bin/pylon-config
ifeq ($(wildcard $(PYLON_CONFIG)),)
$(error pylon SDK not found at $(PYLON_ROOT). Install pylon or set PYLON_ROOT)
endif

# Compile against pylon headers (build host still needs the SDK installed),
# but defer all pylon symbol resolution to runtime via dlopen. The adaptor
# .so therefore has no DT_NEEDED entries for pylon and loads cleanly on
# systems where pylon is absent; basler_discovery.cpp gates pylon calls on
# a runtime dlopen of libpylonbase.so.
#
# Convert pylon's -I include flags to -isystem so warnings inside pylon's
# own headers don't fail the build under -Wall -Werror (pylon has e.g.
# overloaded-virtual hits in pylon/ArrayParameter.h that we don't control).
PYLON_CFLAGS := $(patsubst -I%,-isystem %,$(shell $(PYLON_CONFIG) --cflags))

PREBUILT := $(TOP)/$(PREBUILT_DIR)
TARGET   := $(PREBUILT)libbasler_discovery.so
SRCS     := basler_discovery.cpp
OBJS     := $(SRCS:.cpp=.o)
# -shared already allows unresolved symbols at link time; pylon symbols stay
# unresolved here and get bound at runtime via the dlopen(RTLD_GLOBAL) call
# inside basler_discovery.cpp. VIOS framework symbols (Logger, SensorInfo,
# VmsConfigManager, ...) are resolved by the executable loading this .so,
# same as for onvif_discovery.so.
LDFLAGS  := -shared -rdynamic -ldl

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) $(CXXFLAGS) $(PYLON_CFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

include $(TOP)/Makefile.dep
clean:
	rm -rf *.o *.a *.so $(TARGET)

soclean:
	rm -rf *.o *.a *.so $(TARGET)

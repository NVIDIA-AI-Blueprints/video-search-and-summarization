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

# Compile against pylon headers (build host needs the SDK), but defer all pylon
# symbol resolution to runtime: the .so has no DT_NEEDED entry for pylon and is
# only dlopened by BaslerStreamMonitor when a basler stream is active, in a
# process that LD_PRELOADs pylon. Convert pylon's -I flags to -isystem so
# warnings inside pylon headers don't fail the build under -Wall -Werror.
PYLON_CFLAGS := $(patsubst -I%,-isystem %,$(shell $(PYLON_CONFIG) --cflags))

PREBUILT := $(TOP)/$(PREBUILT_DIR)
TARGET   := $(PREBUILT)libbasler_producer.so
SRCS     := basler_stream_producer.cpp
OBJS     := $(SRCS:.cpp=.o)
# -I for the IMediaDataProducer interface (media_pipelines) and the gstnvvstmeta.h
# it transitively pulls in (media_utils). The remaining headers (gstreamer, glib,
# stats.h, nvbufsurface.h) are already on the global CPPFLAGS include path.
BASLER_INC := -I $(TOP)/src/framework/media/media_pipelines \
              -I $(TOP)/src/framework/media/media_utils
# Link the GStreamer libs the encode pipeline uses (appsrc/appsink, buffers,
# caps). -shared still allows unresolved symbols, so pylon stays deferred (bound
# at runtime via the dlopen(RTLD_GLOBAL) in BaslerStreamMonitor) and VIOS
# framework symbols (Logger, ...) resolve against the executable loading this .so.
LDFLAGS  := -shared -rdynamic -ldl -lgstreamer-1.0 -lgstapp-1.0 -lgobject-2.0 -lglib-2.0

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) $(CXXFLAGS) $(BASLER_INC) $(PYLON_CFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

include $(TOP)/Makefile.dep
clean:
	rm -rf *.o *.a *.so *.d $(TARGET)

soclean:
	rm -rf *.o *.a *.so *.d $(TARGET)

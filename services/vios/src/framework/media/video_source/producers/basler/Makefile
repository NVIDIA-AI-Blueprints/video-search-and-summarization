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

PYLON_CONFIG := $(PYLON_ROOT)/bin/pylon-config
ifeq ($(wildcard $(PYLON_CONFIG)),)
$(error pylon SDK not found at $(PYLON_ROOT). Install pylon or set PYLON_ROOT)
endif

# Defer pylon symbol resolution to runtime (no DT_NEEDED for pylon libs).
# -isystem avoids -Wall -Werror failures from warnings inside pylon headers.
PYLON_CFLAGS := $(patsubst -I%,-isystem %,$(shell $(PYLON_CONFIG) --cflags))

PREBUILT := $(TOP)/$(PREBUILT_DIR)
TARGET   := $(PREBUILT)libbasler_producer.so
SRCS     := basler_stream_producer.cpp
OBJS     := $(SRCS:.cpp=.o)
# Extra include paths for IMediaDataProducer and its transitive headers.
BASLER_INC := -I $(TOP)/src/framework/media/media_pipelines \
              -I $(TOP)/src/framework/media/media_utils \
              -I $(TOP)/src/framework/media/video_source/producers
# Pylon symbols stay unresolved at link time, bound at runtime via dlopen(RTLD_GLOBAL).
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

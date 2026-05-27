# SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
TARGET := $(PREBUILT_DIR_MODULE)/libnvstreamrecorder.so

SRCS := streamrecorder.cpp \
		recorder_apis.cpp \
		$(TOP)/src/framework/media/media_pipelines/gstmux.cpp

CPP = c++
OBJS := $(SRCS:.cpp=.o)
CPPFLAGS += -I $(TOP)/src/framework/web/http_server
LDFLAGS := -L$(PREBUILT_DIR_MODULE) -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR_MODULE) -ljsoncpp -lnvfpsdisplay -lnvstoragewriter -lnvstatistics -rdynamic -shared

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

include $(TOP)/Makefile.dep

clean:
	$(AT)rm -rf $(TARGET) $(OBJS)

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

# Detect architecture
arch ?= $(shell uname -p)

PREBUILT=$(TOP)/$(PREBUILT_DIR)
TARGET := $(PREBUILT)vms_media.so
SRCS := vms_media.cpp milestone_data_streaming_client.cpp
CPP = c++
OBJS := $(SRCS:.cpp=.o)

# Protobuf sources
PROTO_SRCS = ds.proto
PROTO_OBJS = ds.pb.o ds.grpc.pb.o

# All objects for grpc_media including protobuf
ALL_OBJS_GRPC = $(OBJS) $(PROTO_OBJS)

# Protobuf tools
PROTOC = protoc
GRPC_CPP_PLUGIN = grpc_cpp_plugin
GRPC_CPP_PLUGIN_PATH ?= `which $(GRPC_CPP_PLUGIN)`

# Architecture-specific configuration
ifeq ($(arch),aarch64)
# aarch64 cross-compilation settings
PKG_CONFIG := $(SYSROOT)/usr/bin/pkg-config
GRPC_LIBS := $(shell qemu-aarch64-static -L $(SYSROOT) $(PKG_CONFIG) --libs grpc++ protobuf)

LDFLAGS := -shared -rdynamic -L$(SYSROOT)/usr/lib/aarch64-linux-gnu \
            -Wl,--no-as-needed $(GRPC_LIBS) -lgrpc++_reflection \
            -Wl,--as-needed -pthread -ldl \
            -Wl,-rpath,/usr/lib/aarch64-linux-gnu

# Custom compilation flags that exclude abseil-cpp include for aarch64
COMPILE_CPPFLAGS = $(subst -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/abseil-cpp,,$(CPPFLAGS))

CXX_SYSROOT = --sysroot=$(SYSROOT)

else
# x86 settings (default)
LDFLAGS := -shared -rdynamic -L$(TOP)/$(PREBUILT_DIR) -lgrpc -L/usr/local/lib `pkg-config --libs protobuf grpc++` -pthread \
            -Wl,--no-as-needed -lgrpc++_reflection -Wl,--as-needed -ldl

# Use CPPFLAGS directly for x86
COMPILE_CPPFLAGS = $(CPPFLAGS)

CXX_SYSROOT =

endif

all: $(PROTO_SRCS:.proto=.pb.cc) $(PROTO_SRCS:.proto=.grpc.pb.cc) $(TARGET)

# Protobuf compilation rules
.PRECIOUS: %.grpc.pb.cc
%.grpc.pb.cc: %.proto
	$(PROTOC) -I . --grpc_out=. --plugin=protoc-gen-grpc=$(GRPC_CPP_PLUGIN_PATH) $<

.PRECIOUS: %.pb.cc
%.pb.cc: %.proto
	$(PROTOC) -I . --cpp_out=. $<

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(COMPILE_CPPFLAGS) -c $< -o $@

%.o: %.cc
	@echo "Compiling: $<"
	$(CXX) $(COMPILE_CPPFLAGS) -c $< -o $@

# Make sure proto files are generated before compiling dependent sources
milestone_data_streaming_client.o: ds.pb.cc ds.grpc.pb.cc
ds.grpc.pb.o: ds.pb.cc

$(TARGET): $(ALL_OBJS_GRPC)
	@echo "Linking: $@"
	$(CXX) $(CXX_SYSROOT) -o $@ $(ALL_OBJS_GRPC) $(LDFLAGS)

include $(TOP)/Makefile.dep
clean:
	$(AT)rm -rf $(TARGET) $(ALL_OBJS_GRPC) *.pb.cc *.pb.h

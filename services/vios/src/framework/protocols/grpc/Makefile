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

PREBUILT=$(TOP)/$(PREBUILT_DIR)
TARGET := $(PREBUILT)libgrpc.so

# gRPC/protobuf must compile against the same /usr/local headers that produced
# the /usr/local libraries. The top-level CPPFLAGS includes WebRTC's bundled
# Abseil headers, which expose different absl symbol names and leave plain
# absl::Mutex references unresolved against the /usr/local Abseil shared libs.
WEBRTC_ABSL_INCLUDE := -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/abseil-cpp
override CPPFLAGS := -I /usr/local/include $(subst $(WEBRTC_ABSL_INCLUDE),,$(CPPFLAGS))

GRPC_PC_LIBS := $(shell pkg-config --libs protobuf grpc++ 2>/dev/null)

LDFLAGS2 += -L/usr/local/lib\
            -pthread\
            -Wl,--no-as-needed $(GRPC_PC_LIBS) -lgrpc++_reflection -Wl,--as-needed\
            -ldl -shared\
            -Wl,-rpath,/usr/local/lib,-rpath,/usr/local/lib/x86_64-linux-gnu
PROTOC = protoc
GRPC_CPP_PLUGIN = grpc_cpp_plugin
GRPC_CPP_PLUGIN_PATH ?= `which $(GRPC_CPP_PLUGIN)`
# protoc exec()s grpc_cpp_plugin; it must resolve libgrpc_plugin_support from the gRPC prefix.
GRPC_CPP_PLUGIN_LIBDIRS ?= /usr/local/lib:/usr/local/lib/x86_64-linux-gnu

PROTOS_PATH = ./

vpath %.proto $(PROTOS_PATH)

all: $(TARGET)

$(TARGET): vstserver.pb.o vstserver.grpc.pb.o nvgrpc.o
	$(CXX) $^ $(LDFLAGS2) -o $@

# protoc emits the .pb.h / .grpc.pb.h headers as side-effects of the .cc rules
# below, so make does not otherwise know which objects depend on them. Declare
# the cross-file header dependencies explicitly (via the generated .cc, which is
# a real target) so a parallel (-j) build generates the headers before compiling
# the objects that #include them. Without this, the build intermittently fails
# with "vstserver.pb.h" / "vstserver.grpc.pb.h: No such file or directory".
vstserver.grpc.pb.o: vstserver.pb.cc
nvgrpc.o: vstserver.pb.cc vstserver.grpc.pb.cc

.PRECIOUS: %.grpc.pb.cc
%.grpc.pb.cc: %.proto
	LD_LIBRARY_PATH="$(GRPC_CPP_PLUGIN_LIBDIRS):$$LD_LIBRARY_PATH" $(PROTOC) -I $(PROTOS_PATH) --grpc_out=. --plugin=protoc-gen-grpc=$(GRPC_CPP_PLUGIN_PATH) $<

.PRECIOUS: %.pb.cc
%.pb.cc: %.proto
	LD_LIBRARY_PATH="$(GRPC_CPP_PLUGIN_LIBDIRS):$$LD_LIBRARY_PATH" $(PROTOC) -I $(PROTOS_PATH) --cpp_out=. $<

clean:
	rm -f *.o *.pb.cc *.pb.h $(TARGET) nvgrpc

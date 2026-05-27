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

TOP := $(abspath $(lastword $(MAKEFILE_LIST))/../../../..)

# Protobuf compilation variables
PROTOC = protoc
PROTOS_PATH = ./
DS_SCHEMA_PROTO = $(PROTOS_PATH)ds_schema.proto
DS_SCHEMA_PB_CC = $(PROTOS_PATH)ds_schema.pb.cc
DS_SCHEMA_PB_H = $(PROTOS_PATH)ds_schema.pb.h
DS_SCHEMA_OBJ = $(PROTOS_PATH)ds_schema.pb.o

# 2D proto variables
DS_SCHEMA_2D_PROTO = $(PROTOS_PATH)ds_schema_2d.proto
DS_SCHEMA_2D_PB_CC = $(PROTOS_PATH)ds_schema_2d.pb.cc
DS_SCHEMA_2D_PB_H = $(PROTOS_PATH)ds_schema_2d.pb.h
DS_SCHEMA_2D_OBJ = $(PROTOS_PATH)ds_schema_2d.pb.o

# Shared library variables
arch ?= $(shell uname -p)
PREBUILT=$(TOP)/$(PREBUILT_DIR)
PREBUILT_ARCH=$(TOP)/prebuilts/$(arch)

# Include paths for protobuf compilation
PROTO_INCLUDES = -I $(PROTOS_PATH) \
                -I $(TOP)/include/ \
                -I $(TOP)/src/

# Compilation flags
CXXFLAGS = -std=c++17 -fPIC -pthread -O3 -Werror -Wall -Wno-error=varargs $(PROTO_INCLUDES)

# Prevent dependency generation for protobuf files
%.pb.cc.d: %.pb.cc
	@echo "Skipping dependency generation for protobuf file: $<"

%.pb.h.d: %.pb.h
	@echo "Skipping dependency generation for protobuf file: $<"

DS_SCHEMA_3D_C_API = $(PROTOS_PATH)ds_schema_3d_c_api.cpp
DS_SCHEMA_3D_C_API_OBJ = $(PROTOS_PATH)ds_schema_3d_c_api.o
DS_SCHEMA_2D_C_API = $(PROTOS_PATH)ds_schema_2d_c_api.cpp
DS_SCHEMA_2D_C_API_OBJ = $(PROTOS_PATH)ds_schema_2d_c_api.o

LIB_DS_SCHEMA_3D_SO = $(PREBUILT)/libnvds_schema_3d.so
LIB_DS_SCHEMA_2D_SO = $(PREBUILT)/libnvds_schema_2d.so

all: $(LIB_DS_SCHEMA_3D_SO) $(LIB_DS_SCHEMA_2D_SO)

.PRECIOUS: $(DS_SCHEMA_PB_CC) $(DS_SCHEMA_PB_H) $(DS_SCHEMA_2D_PB_CC) $(DS_SCHEMA_2D_PB_H)
$(DS_SCHEMA_PB_CC) $(DS_SCHEMA_PB_H): $(DS_SCHEMA_PROTO)
	@echo "Generating protobuf files from $(DS_SCHEMA_PROTO)"
	$(PROTOC) -I $(PROTOS_PATH) --cpp_out=$(PROTOS_PATH) $<

$(DS_SCHEMA_2D_PB_CC) $(DS_SCHEMA_2D_PB_H): $(DS_SCHEMA_2D_PROTO)
	@echo "Generating protobuf files from $(DS_SCHEMA_2D_PROTO)"
	$(PROTOC) -I $(PROTOS_PATH) --cpp_out=$(PROTOS_PATH) $<

$(DS_SCHEMA_OBJ): $(DS_SCHEMA_PB_CC) $(DS_SCHEMA_PB_H)
	@echo "Compiling $(DS_SCHEMA_PB_CC) to $(DS_SCHEMA_OBJ)"
	@echo "Using CXXFLAGS: $(CXXFLAGS)"
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(DS_SCHEMA_2D_OBJ): $(DS_SCHEMA_2D_PB_CC) $(DS_SCHEMA_2D_PB_H)
	@echo "Compiling $(DS_SCHEMA_2D_PB_CC) to $(DS_SCHEMA_2D_OBJ)"
	@echo "Using CXXFLAGS: $(CXXFLAGS)"
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(DS_SCHEMA_3D_C_API_OBJ): $(DS_SCHEMA_3D_C_API)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(DS_SCHEMA_2D_C_API_OBJ): $(DS_SCHEMA_2D_C_API)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(LIB_DS_SCHEMA_3D_SO): $(DS_SCHEMA_OBJ) $(DS_SCHEMA_3D_C_API_OBJ)
	@echo "Creating shared library $(LIB_DS_SCHEMA_3D_SO)"
	$(CXX) -shared -o $@ $^ -L$(PREBUILT_ARCH) -lprotobuf

$(LIB_DS_SCHEMA_2D_SO): $(DS_SCHEMA_2D_OBJ) $(DS_SCHEMA_2D_C_API_OBJ)
	@echo "Creating shared library $(LIB_DS_SCHEMA_2D_SO)"
	$(CXX) -shared -o $@ $^ -L$(PREBUILT_ARCH) -lprotobuf

# Phony target to explicitly generate protobuf files
generate_proto: $(DS_SCHEMA_PB_CC) $(DS_SCHEMA_PB_H) $(DS_SCHEMA_2D_PB_CC) $(DS_SCHEMA_2D_PB_H)

clean:
	@echo "Cleaning protobuf generated files"
	rm -f $(DS_SCHEMA_PB_CC) $(DS_SCHEMA_PB_H) $(DS_SCHEMA_OBJ) \
	      $(DS_SCHEMA_2D_PB_CC) $(DS_SCHEMA_2D_PB_H) $(DS_SCHEMA_2D_OBJ) \
	      $(DS_SCHEMA_3D_C_API_OBJ) $(DS_SCHEMA_2D_C_API_OBJ) \
	      $(LIB_DS_SCHEMA_3D_SO) $(LIB_DS_SCHEMA_2D_SO)

.PHONY: all clean generate_proto
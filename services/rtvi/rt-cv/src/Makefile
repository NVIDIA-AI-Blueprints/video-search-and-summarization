# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

CUDA_VER?=
ifeq ($(CUDA_VER),)
  $(error "CUDA_VER is not set")
endif
$(info "CUDA_VER is $(CUDA_VER)")
$(info "REGISTRY is $(REGISTRY)")
$(info "TAG is $(TAG)")

APP:= metropolis_perception_app

TARGET_DEVICE = $(shell gcc -dumpmachine | cut -f1 -d -)

NVDS_VERSION:=9.0

LIB_INSTALL_DIR?=/opt/nvidia/deepstream/deepstream-$(NVDS_VERSION)/lib/
APP_INSTALL_DIR?=/opt/nvidia/deepstream/deepstream-$(NVDS_VERSION)/bin/

ifeq ($(TARGET_DEVICE),aarch64)
  CFLAGS:= -DPLATFORM_TEGRA
endif

SRCS:= metropolis_perception_app.c perception_utc.c
SRCS+= ../deepstream-app/deepstream_app.c ../deepstream-app/deepstream_app_config_parser.c
SRCS+= ../deepstream-app/deepstream_app_config_parser_yaml.cpp
SRCS+= /opt/nvidia/deepstream/deepstream/sources/libs/nvds_msgapi_common_src/nvds_utils.cpp
SRCS+= $(wildcard ../../apps-common/src/*.c)
SRCS+= $(wildcard ../../apps-common/src/deepstream-yaml/*.cpp)

INCS:= $(wildcard *.h)
INCS+= -I/opt/nvidia/deepstream/deepstream/sources/includes/ 

PKGS:= gstreamer-1.0 gstreamer-video-1.0 x11 json-glib-1.0

OBJS:= $(SRCS:.c=.o)
OBJS:= $(OBJS:.cpp=.o)

CFLAGS+= -I../../apps-common/includes \
                 -I../../../includes \
                 -I../deepstream-app/ -DDS_VERSION_MINOR=0 -DDS_VERSION_MAJOR=5 \
                 -I /usr/local/cuda-$(CUDA_VER)/include

# Set default values if REGISTRY or TAG are not provided
IMAGE_PATH ?= $(REGISTRY)
IMAGE_TAG ?= $(TAG)
# Fallback defaults if still empty
ifeq ($(IMAGE_PATH),)
  IMAGE_PATH := "unknown-registry"
endif
ifeq ($(IMAGE_TAG),)
  IMAGE_TAG := "unknown-tag"
endif

CFLAGS+= -DIMAGE_PATH=\"$(IMAGE_PATH)\" -DIMAGE_TAG=\"$(IMAGE_TAG)\"
$(info "IMAGE_PATH is $(IMAGE_PATH)")
$(info "IMAGE_TAG is $(IMAGE_TAG)")

LIBS:= -L/usr/local/cuda-$(CUDA_VER)/lib64/ -lcudart -lnvds_utils -lnvds_msgbroker -lnvds_logger -lcrypto

LIBS+= -L$(LIB_INSTALL_DIR) -lnvdsgst_meta -lnvds_meta -lnvdsgst_helper -lnvdsgst_customhelper -lnvdsgst_smartrecord -lnvds_utils -lnvds_msgbroker -lm \
       -lyaml-cpp -lcuda -lgstrtspserver-1.0 -ldl -Wl,-rpath,$(LIB_INSTALL_DIR)

CFLAGS+= $(shell pkg-config --cflags $(PKGS))
CXXFLAGS+= $(CFLAGS)

LIBS+= $(shell pkg-config --libs $(PKGS))

all: $(APP)

%.o: %.c $(INCS) Makefile
	$(CC) -c -o $@ $(CFLAGS) $<

%.o: %.cpp $(INCS) Makefile
	$(CXX) -c -o $@ $(CFLAGS) $<

$(APP): $(OBJS) Makefile
	$(CXX) -o $(APP) $(OBJS) $(LIBS)

install: $(APP)
	cp -rv $(APP) $(APP_INSTALL_DIR)

clean:
	rm -rf $(OBJS) $(APP)


# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
TARGET := $(PREBUILT)libnvvideo_source.so

arch ?= $(shell uname -p)

# Core video source files
CORE_SRCS := core/CommonVideoSource.cpp \
			 core/PipelineConfiguration.cpp \
			 core/PipelineManager.cpp

# Builder files
BUILDER_SRCS := builders/PipelineBuilder.cpp \
				builders/SingleStreamPipelineBuilder.cpp \
				builders/CompositePipelineBuilder.cpp

# Processor files
PROCESSOR_SRCS := processors/transforms/ll_transform.cpp \
				  processors/compositors/nvcompositor.cpp

# Decoder files
DECODER_SRCS := decoders/gstnvvideodecoder.cpp

# Encoder files
ENCODER_SRCS := encoders/nvvideoencoder.cpp \
				encoders/libnv_encoder.cpp \
				encoders/image_encoder.cpp \
				encoders/nvjpegenc_loader.cpp

# Sender files
SENDER_SRCS := senders/videowebRTCsender.cpp \
			   senders/webrtc_sink_consumer.cpp

# Producer files
PRODUCER_SRCS := producers/nativestreamproducer.cpp \
				 producers/native_stream_monitor.cpp

# Platform-specific files (Jetson)
ifeq ($(arch),aarch64)
PRODUCER_SRCS += producers/gstnvipcproducer.cpp
endif

# Combine all sources
SRCS := $(CORE_SRCS) $(BUILDER_SRCS) $(PROCESSOR_SRCS) $(DECODER_SRCS) $(ENCODER_SRCS) $(SENDER_SRCS) $(PRODUCER_SRCS)

CPP = c++
OBJS := $(SRCS:.cpp=.o)

# Include paths for video_source module
CPPFLAGS += -I $(TOP)/src/framework/media/video_source/core \
			-I $(TOP)/src/framework/media/video_source/builders \
			-I $(TOP)/src/framework/media/video_source/processors/transforms \
			-I $(TOP)/src/framework/media/video_source/processors/compositors \
			-I $(TOP)/src/framework/media/video_source/decoders \
			-I $(TOP)/src/framework/media/video_source/encoders \
			-I $(TOP)/src/framework/media/video_source/senders \
			-I $(TOP)/src/framework/media/video_source/producers \
			-I $(TOP)/src/framework/media/media_pipelines \
			-I $(TOP)/src/framework/media/media_pipelines/cloud_stream \
			-I $(TOP)/src/framework/media/media_pipelines/local_stream \
			-I $(TOP)/src/modules/storage_management \
			-I $(TOP)/src/framework/media/overlays \
			-I $(TOP)/src/framework/stream_monitor \
			-I $(TOP)/src/framework/webrtc_streamer/inc \
			-I $(TOP)/src/framework/utilities \
			-I $(TOP)/src/framework/platform_specific \
			-I $(TOP)/src/framework/database/include \
			-I $(TOP)/src/framework/unified_storage/reader \
			-I $(TOP)/src/modules/webrtc_replay \
			-I $(TOP)/src/framework \
			-I $(TOP)/include

# TODO: REMOVE WAR LATER
LDFLAGS := -L$(PREBUILT) -ljsoncpp -lnvstoragereader -lnvstoragemanager -lnvoverlays -lclip_producer -ls3stream_producer -rdynamic -shared

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

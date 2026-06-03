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
TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragemanagement.so

SRCS := storage_management.cpp \
		storage_management_apis.cpp \
		storage_management_utils.cpp \
		video_download.cpp \
		VideoGeneratorTaskManager.cpp \
		AsyncVideoStreamHandler.cpp \
		../../framework/media/media_pipelines/transcode_writer_consumer.cpp \
		../../framework/media/media_pipelines/remux_writer_consumer.cpp

CPP = c++
OBJS := $(SRCS:.cpp=.o)

# Include cloud reader headers and VideoSegmentExtractor
CPPFLAGS += -I ./ \
			-I $(TOP)/src/framework/unified_storage/manager \
			-I $(TOP)/src/framework/unified_storage/reader \
			-I $(TOP)/include/3rdparty/Bosma/Scheduler \
			-I $(TOP)/src/framework/utilities \
			-I $(TOP)/src/framework/media/media_pipelines \
			-I $(TOP)/src/framework/media/media_pipelines/cloud_stream \
			-I $(TOP)/src/framework/media/media_pipelines/local_stream \
			-I $(TOP)/src/framework/media/video_segment_extractor \
			-I $(TOP)/src/framework/media/overlays \
			-I $(TOP)/src/framework/web/http_server \
			-I $(TOP)/include

ifeq ($(arch),x86_64)
# Add additional include paths for x86_64 builds
CPPFLAGS += -I/usr/include/jsoncpp
endif

# Link against cloud reader library, VideoSegmentExtractor LibAV dependencies, and other dependencies
ifeq ($(arch),x86_64)
# Define AWS SDK libraries if not already defined (for standalone builds)
# This prevents undefined-reference errors when this Makefile is used independently
ifndef AWS_SDK_LIBS
AWS_SDK_LIBS := -laws-cpp-sdk-core \
				-laws-crt-cpp \
				-laws-c-auth \
				-laws-c-common \
				-laws-c-event-stream \
				-laws-c-mqtt \
				-laws-c-io \
				-laws-c-s3 \
				-laws-c-http \
				-laws-c-cal \
				-laws-c-compression \
				-laws-c-sdkutils \
				-laws-checksums \
				-ls2n
endif

# Add AWS SDK dependencies only for x86_64 builds (removed LibAV and VideoSegmentExtractor - now dynamically loaded)
LDFLAGS := -L$(PREBUILT_DIR_MODULE) \
		   -L$(PREBUILT_DIR_MODULE)/aws \
		   -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR_MODULE),-rpath=$(PREBUILT_DIR_MODULE)/aws \
		   -ljsoncpp \
		   -lnvstoragereader \
		   -lnvstoragemanager \
		   -lnvoverlays \
		   -lnvstatistics \
		   -lclip_producer \
		   -ls3stream_producer \
		   $(AWS_SDK_LIBS) \
		   -ldl -lm -lrt -lz \
		   -rdynamic -shared
else
# For aarch64 (Jetson) and SBSA, exclude AWS SDK dependencies, LibAV, and VideoSegmentExtractor (now dynamically loaded)
LDFLAGS := -L$(PREBUILT_DIR_MODULE) \
		   -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR_MODULE) \
		   -ljsoncpp \
		   -lnvstoragereader \
		   -lnvstoragemanager \
		   -lnvoverlays \
		   -lnvstatistics \
		   -lclip_producer \
		   -ls3stream_producer \
		   -ldl -lm -lrt -lz \
		   -rdynamic -shared
endif


all: $(TARGET)

# Always build VideoSegmentExtractor - it will dynamically load LibAV at runtime
video_segment_extractor:
	@echo "Building VideoSegmentExtractor (will dynamically load LibAV at runtime)..."
	$(MAKE) -C $(TOP)/src/framework/media/video_segment_extractor/

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): video_segment_extractor $(OBJS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

include $(TOP)/Makefile.dep

clean: clean_video_segment_extractor
	rm -rf *.o *.a *.so $(TARGET) $(OBJS)
	$(AT)rm -f ../../framework/media/media_pipelines/clip_reader_producer.{o,d}
	$(AT)rm -f ../../framework/media/media_pipelines/transcode_writer_consumer.{o,d}
	$(AT)rm -f ../../framework/media/media_pipelines/remux_writer_consumer.{o,d}

clean_video_segment_extractor:
	$(MAKE) -C $(TOP)/src/framework/media/video_segment_extractor/ clean

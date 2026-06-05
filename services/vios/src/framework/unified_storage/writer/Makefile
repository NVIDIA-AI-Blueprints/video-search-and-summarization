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

PREBUILT_DIR_MODULE=$(TOP)/$(PREBUILT_DIR)
TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragewriter.so

# Start with core files that we'll implement first
SRCS := \

# Add other files as they are implemented
# Add the new separate files
ifneq ($(wildcard unified_local_storage_writer.cpp),)
SRCS += unified_local_storage_writer.cpp
endif

ifneq ($(wildcard unified_storage_writer_factory.cpp),)
SRCS += unified_storage_writer_factory.cpp
endif

ifneq ($(wildcard unified_storage_writer_factory.cpp),)
SRCS += unified_storage_writer.cpp
endif

# Define cloud storage writer files
UNIFIED_STORAGE_WRITERS := unified_cloud_storage_writer.cpp \
                         s3_storage_writer.cpp \
                         gcs_storage_writer.cpp \
                         azure_storage_writer.cpp \
                         minio_storage_writer.cpp \
						 cloud_storage_buffer.cpp \
						 cloud_storage_writer.cpp \
						 cloud_storage_writer_factory.cpp

# Add cloud storage writers
$(foreach writer,$(UNIFIED_STORAGE_WRITERS),$(if $(wildcard $(writer)),$(eval SRCS += $(writer))))

CPP = c++
OBJS := $(SRCS:.cpp=.o)

# Define MinIO libraries
MINIO_LIBS := -lminiocpp -lcurlpp -lpugixml -lINIReader -linih

# Define AWS SDK libraries
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

CLOUD_LIBS := $(MINIO_LIBS) $(AWS_SDK_LIBS)

LDFLAGS := -L$(PREBUILT_DIR_MODULE) \
		   -L$(PREBUILT_DIR_MODULE)/minio \
		   -L$(PREBUILT_DIR_MODULE)/aws \
		   -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR_MODULE),-rpath=$(PREBUILT_DIR_MODULE)/minio,-rpath=$(PREBUILT_DIR_MODULE)/aws \
		   -ljsoncpp -lcurl -lssl -lcrypto \
		   -lboost_system -lboost_filesystem -lboost_thread \
		   -pthread -rdynamic -shared $(CLOUD_LIBS) \
		   -Wl,-rpath=$(PREBUILT_DIR)prom_so \
		   -lglib-2.0 -lgstreamer-1.0 -lgstvideo-1.0 -lgstpbutils-1.0 -lgobject-2.0 -lgstapp-1.0 \
		   -ldl -lm -lrt -lz

# Add cloud storage specific include paths
UNIFIED_STORAGE_INCLUDES := -I $(TOP)/src/framework/unified_storage \
						  -I $(TOP)/src/framework/utilities \
						  -I $(TOP)/src/framework/media/media_pipelines \
						  -I $(TOP)/include/3rdparty/minio \
						  -I $(TOP)/include/3rdparty/aws \
						  -I $(TOP)/include/3rdparty/aws/core \
						  -I $(TOP)/include/3rdparty/aws/s3-encryption \
						  -I $(TOP)/include/3rdparty/smithy \
						  -I $(TOP)/include/3rdparty/aws/crt \
						  -I $(TOP)/include/3rdparty/aws/core/external

# AWS SDK specific compiler flags
AWS_SDK_CPPFLAGS := -DAWS_CUSTOM_MEMORY_MANAGEMENT \
					-DAWS_MEMORY_MANAGEMENT \
					-DENABLE_CURL_CLIENT \
					-DENABLE_OPENSSL_ENCRYPTION

# Override CPPFLAGS to add cloud storage specific flags
override CPPFLAGS += $(UNIFIED_STORAGE_INCLUDES) \
					 $(AWS_SDK_CPPFLAGS) \
					 -DUNIFIED_STORAGE_MODULE \
					 -std=c++17 -fPIC -pthread \
					 -O3 -Wall -Wno-error=varargs

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling Cloud Storage: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking Cloud Storage Library: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

# No stub file generation - only compile files that actually exist

include $(TOP)/Makefile.dep

clean:
	$(AT)rm -rf $(TARGET) $(OBJS)

.PHONY: all clean 
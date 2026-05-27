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
TARGET := $(PREBUILT_DIR_MODULE)/libnvstoragereader.so

# Cloud Reader Library source files
SRCS := \
		unified_storage_reader.cpp \
		unified_storage_reader_factory.cpp \
		unified_local_storage_reader.cpp \
		unified_storage_reader_utils.cpp \
		cloud_reader.cpp \
		s3_cloud_reader.cpp \
		minio_cloud_reader.cpp \
		cloud_reader_utils.cpp \
		cloud_reader_factory.cpp \
		unified_cloud_storage_reader.cpp \
		../common/aws_sdk_manager.cpp

CPP = c++
OBJS := $(SRCS:.cpp=.o)

# Define MinIO libraries
MINIO_LIBS := -lminiocpp -lcurlpp -lpugixml -lINIReader -linih

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

# Add AWS SDK dependencies and MinIO libraries
CLOUD_READER_LIBS := -ljsoncpp -lcurl -lssl -lcrypto -lnvlogger -lpthread $(AWS_SDK_LIBS) $(MINIO_LIBS)

LDFLAGS := -L$(PREBUILT_DIR_MODULE) -L$(PREBUILT_DIR_MODULE)/minio \
		   -L$(PREBUILT_DIR_MODULE)/aws \
		   -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR_MODULE),-rpath=$(PREBUILT_DIR_MODULE)/aws,-rpath=$(PREBUILT_DIR_MODULE)/minio \
		   $(CLOUD_READER_LIBS) \
		   -ldl -lm -lrt -lz \
		   -rdynamic -shared

# Cloud reader specific include paths
CLOUD_READER_INCLUDES := -I $(TOP)/src/framework/utilities \
						 -I $(TOP)/src/framework/unified_storage \
						 -I $(TOP)/include \
						 -I $(TOP)/include/3rdparty \
						 -I $(TOP)/include/3rdparty/aws \
						 -I $(TOP)/include/3rdparty/aws/core \
						 -I $(TOP)/include/3rdparty/aws/s3-encryption \
						 -I $(TOP)/include/3rdparty/smithy \
						 -I/usr/include/jsoncpp \
						 -I/usr/include/openssl \
						 -I/usr/include/curl \
						 -I $(TOP)/include/3rdparty/aws/crt \
						 -I $(TOP)/include/3rdparty/aws/core/external \
						 -I $(TOP)/include/3rdparty/minio

# AWS SDK specific compiler flags
AWS_SDK_CPPFLAGS := -DAWS_CUSTOM_MEMORY_MANAGEMENT \
					-DAWS_MEMORY_MANAGEMENT \
					-DENABLE_CURL_CLIENT \
					-DENABLE_OPENSSL_ENCRYPTION

# Override CPPFLAGS to add cloud reader specific flags
override CPPFLAGS += $(CLOUD_READER_INCLUDES) \
					 $(AWS_SDK_CPPFLAGS) \
					 -DCLOUD_READER_MODULE \
					 -std=c++17 -fPIC -pthread \
					 -O3 -Wall -Wno-error=varargs \
					 -Wno-error=deprecated-declarations \
					 -Wno-unused-parameter

all: $(TARGET)

%.o: %.cpp %.d
	@echo "Compiling Cloud Reader: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	@echo "Linking Cloud Reader Library: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)

# No stub file generation - only compile files that actually exist

include $(TOP)/Makefile.dep

clean:
	$(AT)rm -rf $(TARGET) $(OBJS)

# Debug and verification targets
check-aws-libs:
	@echo "=== AWS SDK Library Check ==="
	@echo "Looking for AWS libraries in: $(PREBUILT_DIR_MODULE)/aws/"
	@ls -la $(PREBUILT_DIR_MODULE)/aws/libaws-cpp-sdk-core.so* 2>/dev/null || echo "❌ libaws-cpp-sdk-core.so not found"
	@ls -la $(PREBUILT_DIR_MODULE)/aws/libaws-crt-cpp.so* 2>/dev/null || echo "❌ libaws-crt-cpp.so not found"
	@echo "================================="

check-aws-headers:
	@echo "=== AWS SDK Header Check ==="
	@echo "Looking for AWS headers in: $(TOP)/include/3rdparty/aws/"
	@ls -la $(TOP)/include/3rdparty/aws/core/Aws.h 2>/dev/null || echo "❌ core/Aws.h not found"
	@ls -la $(TOP)/include/3rdparty/aws/core/auth/ 2>/dev/null || echo "❌ core/auth/ directory not found"
	@ls -la $(TOP)/include/3rdparty/aws/core/http/ 2>/dev/null || echo "❌ core/http/ directory not found"
	@echo "=========================="

check-minio-libs:
	@echo "=== MinIO Library Check ==="
	@echo "Looking for MinIO libraries in: $(PREBUILT_DIR_MODULE)/minio/"
	@ls -la $(PREBUILT_DIR_MODULE)/minio/libminiocpp.so* 2>/dev/null || echo "❌ libminiocpp.so not found"
	@ls -la $(PREBUILT_DIR_MODULE)/minio/libcurlpp.so* 2>/dev/null || echo "❌ libcurlpp.so not found"
	@ls -la $(PREBUILT_DIR_MODULE)/minio/libpugixml.so* 2>/dev/null || echo "❌ libpugixml.so not found"
	@echo "================================="

check-minio-headers:
	@echo "=== MinIO Header Check ==="
	@echo "Looking for MinIO headers in: $(TOP)/include/3rdparty/minio/"
	@ls -la $(TOP)/include/3rdparty/minio/minio-cpp/client.h 2>/dev/null || echo "❌ minio-cpp/client.h not found"
	@ls -la $(TOP)/include/3rdparty/minio/minio-cpp/credentials.h 2>/dev/null || echo "❌ minio-cpp/credentials.h not found"
	@ls -la $(TOP)/include/3rdparty/minio/minio-cpp/args.h 2>/dev/null || echo "❌ minio-cpp/args.h not found"
	@echo "=========================="

debug-flags:
	@echo "=== Build Configuration Debug ==="
	@echo "CLOUD_READER_INCLUDES:"
	@echo "$(CLOUD_READER_INCLUDES)" | tr ' ' '\n'
	@echo ""
	@echo "AWS_SDK_LIBS:"
	@echo "$(AWS_SDK_LIBS)" | tr ' ' '\n'
	@echo ""
	@echo "MINIO_LIBS:"
	@echo "$(MINIO_LIBS)" | tr ' ' '\n'
	@echo ""
	@echo "CLOUD_READER_LIBS:"
	@echo "$(CLOUD_READER_LIBS)" | tr ' ' '\n'
	@echo ""
	@echo "CPPFLAGS:"
	@echo "$(CPPFLAGS)" | tr ' ' '\n'
	@echo "=========================="

verify-aws: check-aws-libs check-aws-headers
	@echo "✅ AWS SDK verification complete"

verify-minio: check-minio-libs check-minio-headers
	@echo "✅ MinIO SDK verification complete"

verify-all: verify-aws verify-minio
	@echo "✅ All SDK verifications complete"

.PHONY: all clean check-aws-libs check-aws-headers check-minio-libs check-minio-headers debug-flags verify-aws verify-minio verify-all

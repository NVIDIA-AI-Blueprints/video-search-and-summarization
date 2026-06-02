# SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

TOP := $(abspath $(lastword $(MAKEFILE_LIST))/..)

# Build-toolchain images used by the cc=1 / cc=0 containerised build wrappers.
# Build them locally from cicd_files/
# (see cicd_files/x86_64/devel and cicd_files/aarch64/devel), then either tag
# to the defaults below or override per build on the environment / command line:
#   make cc=0 X86_BUILD_IMAGE=<image> <target>
AARCH64_CC_IMAGE ?= vios-build:aarch64-cross-compiler
X86_BUILD_IMAGE  ?= vios-build:x86-24.04-cuda13.0.0

ifeq ($(cc),1)
all:
%:
	docker run --rm -v $(TOP):/root $(AARCH64_CC_IMAGE) bash -c "unset SBSA_PLATFORM && cd /root && PROJECT=$(PROJECT) MODULE=$(MODULE) make $(MAKECMDGOALS) -j12"
else ifeq ($(cc),0)
all:
%:
	docker run --rm -v $(TOP):/root $(X86_BUILD_IMAGE) bash -c "cd /root && PROJECT=$(PROJECT) MODULE=$(MODULE) make $(MAKECMDGOALS) -j12"
else
arch ?= $(shell uname -p)
ifneq ($(CROSS_COMPILE),)
arch = aarch64
SYSROOT = $(JETSON_ROOTFS)
endif
CXX :=$(CROSS_COMPILE)$(CXX)

# Enable OTEL support only for x86_64
ifeq ($(arch),x86_64)
USE_OTEL := 1
else
USE_OTEL := 0
endif
TOP := $(abspath $(lastword $(MAKEFILE_LIST))/..)
APP := launch_vst
TEST_TARGET := vst_test

# Version for projects
# Applicable for Rosie, VST, Tokkio
VST_VERSION = 2.1.0-26.05.2
MMS_VERSION = 2.1.0-26.05.2
STREAMER_VERSION = 2.1.0-26.05.2

SRCS := \
	src/app/Main.cpp \
	src/app/server.cpp \
	src/app/vstmodule.cpp \
	src/adaptors/adaptor_loader.cpp \
	src/adaptors/media_adaptor_loader.cpp \
	src/framework/platform_specific/cudaLoader.cpp \
	src/framework/platform_specific/nvlibs.cpp \
	src/framework/platform_specific/nvsurfacepool.cpp \
	src/framework/apis/websocket_apis.cpp \
	src/framework/apis/user_apis.cpp \
	src/framework/apis/common/vst_common.cpp \
	src/framework/apis/common/device_manager.cpp \
	src/framework/apis/common/sensor_info.cpp \
	src/framework/apis/common/modules_apis.cpp \
	src/framework/web/validators/SchemaValidator.cpp \
	src/framework/web/http_server/HttpServerRequestHandler.cpp \
	src/framework/web/http_server/OtelTracing.cpp \
	src/framework/web/http_server/UserAuthHandler.cpp \
	src/framework/web/http_server/health_probes.cpp \
	src/framework/web/webServer.cpp \
	src/framework/web/websocket_server/WebsocketServerRequestHandler.cpp \
	src/framework/web/websocket_server/Websocket.cpp \
	src/framework/web/websocket_client/WebsocketClient.cpp \
	src/framework/media/media_pipelines/media_consumer.cpp \
	src/framework/media/media_pipelines/VideoQueue.cpp \
	src/framework/live555/rtsp_client/rtspconnectionclient.cpp \
	src/framework/live555/rtsp_client/environment.cpp \
	src/framework/live555/rtsp_client/sdpclient.cpp \
	src/framework/live555/rtsp_client/SessionSink.cpp \
	src/framework/live555/rtsp_client/testRTSP.cpp \
	src/framework/notification/notification_manager.cpp \
	src/framework/notification/redis/redis_publisher.cpp \
	src/framework/notification/redis/redis_subscriber.cpp \
	src/framework/notification/kafka/kafka_producer.cpp \
	src/framework/notification/kafka/kafka_consumer.cpp \
	src/framework/notification/mqtt/mqtt_publisher.cpp \
	src/framework/notification/mqtt/mqtt_subscriber.cpp \
	src/framework/notification/ds_proto_parser.cpp \
	src/framework/protocols/prometheus_client/prometheus_client.cpp

MEDIA_PIPELINE_SRCS := \
	src/framework/media/media_pipelines/gstnvaudiodecoder.cpp \
	src/framework/media/media_pipelines/gstnvaudioencoder.cpp \
	src/framework/media/media_pipelines/gstnvvideoencoder.cpp \
	src/framework/media/media_pipelines/gstnvvideoudpclient.cpp \
	src/framework/media/media_pipelines/gstnvaudioudpclient.cpp \
	src/framework/media/media_pipelines/gstnvimageencode.cpp \
	src/framework/media/media_pipelines/gstnvvideoencodeout.cpp \
	src/framework/media/media_pipelines/gstnvdecodebin.cpp \
	src/framework/notification/messagebus/MessageBus.cpp

OVERLAY_SRCS := \
	src/framework/protocols/elastic_search/elasticSearch.cpp \
	src/framework/media/metadata/LiveMetadataStore.cpp \
	src/framework/media/metadata/ElasticMetadataStore.cpp

PREBUILT_DIR=prebuilts/$(arch)/
PREBUILT_ARCH=prebuilts/$(arch)/

CPPFLAGS := -std=c++17 -fPIC -pthread -DHAVE_JPEG -DWEBRTC_POSIX -DWEBRTC_USE_H264 -DHAVE_LIVE555 -DRTC_DCHECK_IS_ON=0 -Wno-attributes \
		-DVST_VERSION="\"$(VST_VERSION)\"" -DMMS_VERSION="\"$(MMS_VERSION)\"" -DSTREAMER_VERSION="\"$(STREAMER_VERSION)\"" \
		-DMODULE_ID="\"$(MODULE)\"" \
		-DUSE_CUOSD \
	    -I $(TOP)/include/ \
		-I $(TOP)/include/ds \
		-I $(TOP)/include/3rdparty/

ifeq ($(USE_OTEL),1)
CPPFLAGS += -DUSE_OTEL -I $(TOP)/include/opentelemetry
endif

CPPFLAGS += \
			-I $(TOP)/src/app/ \
			-I $(TOP)/src/framework/ \
			-I $(TOP)/src/framework/platform_specific/ \
			-I $(TOP)/src/framework/utilities/ \
			-I $(TOP)/src/framework/apis/common \
			-I $(TOP)/src/framework/apis \
			-I $(TOP)/src/modules/sensor_management/ \
			-I $(TOP)/src/modules/stream_recorder/ \
			-I $(TOP)/src/modules/storage_management/ \
			-I $(TOP)/src/modules/rtsp_server/ \
			-I $(TOP)/src/adaptors \
			-I $(TOP)/src/adaptors/onvif \
			-I ${SYSROOT}/usr/include/libxml2 \
			-I $(SYSROOT)/usr/include/gstreamer-1.0 \
			-I $(SYSROOT)/usr/include/glib-2.0/ \
			-I $(SYSROOT)/usr/include/glib-2.0/include \
			-I $(SYSROOT)/usr/lib/$(arch)-linux-gnu/glib-2.0/include \
			-I $(TOP)/src/framework/webrtc_streamer/ \
			-I $(TOP)/src/framework/webrtc_streamer/inc \
			-I $(TOP)/src/framework/webrtc_streamer/inc/nvmultimedia \
			-I $(TOP)/src/framework/webrtc_streamer/inc/linux \
			-I $(TOP)/src/framework/inc \
			-I $(TOP)/src/framework/web/api_spec \
			-I $(TOP)/src/framework/web/api_spec/services \
			-I $(TOP)/src/framework/web/validators \
			-I $(TOP)/src/framework/web/inc/civetweb \
			-I $(TOP)/src/framework/web/ \
			-I $(TOP)/src/framework/web/http_server \
			-I $(TOP)/src/framework/web/websocket_server \
			-I $(TOP)/src/framework/web/websocket_client \
			-I $(TOP)/src/framework/live555/inc/live \
			-I $(TOP)/src/framework/live555/inc/live555helper \
			-I $(TOP)/src/framework/live555/rtsp_client \
			-I $(TOP)/src/framework/live555/inc/live/BasicUsageEnvironment/include \
			-I $(TOP)/src/framework/live555/inc/live/UsageEnvironment/include \
			-I $(TOP)/src/framework/live555/inc/live/liveMedia/include \
			-I $(TOP)/src/framework/live555/inc/live/groupsock/include \
			-I $(TOP)/src/framework/live555/inc/live/mediaServer/ \
			-I $(TOP)/src/framework/media/media_pipelines \
			-I $(TOP)/src/framework/media/media_pipelines/cloud_stream \
			-I $(TOP)/src/framework/media/media_pipelines/local_stream \
			-I $(TOP)/src/framework/media/metadata \
			-I $(TOP)/src/framework/media/video_source/core \
			-I $(TOP)/src/framework/media/video_source/builders \
			-I $(TOP)/src/framework/media/video_source/processors/transforms \
			-I $(TOP)/src/framework/media/video_source/processors/compositors \
			-I $(TOP)/src/framework/media/video_source/decoders \
			-I $(TOP)/src/framework/media/video_source/encoders \
			-I $(TOP)/src/framework/media/video_source/senders \
			-I $(TOP)/src/framework/media/video_source/producers \
			-I $(TOP)/src/framework/media/media_utils \
			-I $(TOP)/src/framework/media/overlays \
			-I $(TOP)/src/framework/stream_monitor \
			-I $(TOP)/src/framework/database/ \
			-I $(TOP)/src/framework/database/include \
			-I $(TOP)/src/framework/database/sqlite \
			-I $(TOP)/src/framework/database/postgresql \
			-I $(TOP)/include/3rdparty/Bosma/Scheduler/ \
			-I $(TOP)/src/framework/notification/ \
			-I $(TOP)/src/framework/notification/messagebus \
			-I $(TOP)/src/framework/notification/kafka/ \
			-I $(TOP)/src/framework/protocols/ \
			-I $(TOP)/src/framework/protocols/soap/ \
			-I $(TOP)/src/framework/protocols/grpc \
			-I $(TOP)/src/framework/protocols/elastic_search \
			-I $(TOP)/src/framework/unified_storage \
			-I $(TOP)/src/framework/unified_storage/writer \
			-I $(TOP)/src/framework/unified_storage/reader \
			-I $(TOP)/src/framework/unified_storage/manager \
			-I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/ \
			-I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/jsoncpp/source/include \
			-I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/jsoncpp/generated \
			-I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/libyuv/include \
			-I $(TOP)/src/modules/webrtc_live \
			-I $(TOP)/src/modules/webrtc_replay \
			-I $(TOP)/src/modules/webrtc_stream_bridge \
			-O3 -Werror -Wall -Wno-error=varargs

ifeq ($(arch),aarch64)
CPPFLAGS += -Wno-error=overloaded-virtual -Wno-error=deprecated-declarations -DARCH="\"$(arch)\""
else
CPPFLAGS += -DARCH="\"$(arch)\""
endif

VST_SIGGRAPH=false
ifeq ($(arch),x86_64)
CPPFLAGS += -I $(HOME)/.local/include \
			-I /usr/local/cuda/include \
			-I /usr/local/cuda/ \
			-I ./src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/abseil-cpp \
			`pkg-config --cflags opencv4`
CPPFLAGS += -DGPU_ACCELERATE -Wno-error=deprecated-declarations
LDFLAGS += -lldap -llber
else # Jetson or sbsa
	CPPFLAGS +=	-I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/abseil-cpp
	CPPFLAGS += -I $(SYSROOT)/usr/include/opencv4/ \
				-I /usr/include/
	LDFLAGS += -L $(SYSROOT)/usr/lib/aarch64-linux-gnu/ $(SYSROOT)/usr/lib/aarch64-linux-gnu/lapack/liblapack.so.3
	LDFLAGS += $(SYSROOT)/usr/lib/aarch64-linux-gnu/blas/libblas.so.3 -lopencv_core -lopencv_imgproc -lopencv_imgcodecs
	CPPFLAGS += -DTEGRA_ACCELERATE
ifneq ($(JETSON_PLATFORM),) # Jetson only
	LDFLAGS += -lnvdsgst_ipcmeta
endif
endif

ifeq ($(arch),aarch64)
CPPFLAGS += -I $(SYSROOT)/.local/include \
			-I $(SYSROOT)/usr/local/cuda-12.9/include \
			-I $(SYSROOT)/usr/local/cuda-12.9/
endif

# Define targets & libraries for each module
MODULE_LIBS :=
VIDEO_SOURCE_TARGET = video_source
VIDEO_SOURCE_LIB = -lnvvideo_source
WEBRTC_STREAMER_TARGET = webrtc_streamer
WEBRTC_STREAMER_LIB = -lnvwebrtc_streamer
OVERLAY_TARGET = overlays
OVERLAY_LIB = -lnvoverlays
ADAPTOR_TARGET = adaptors
S3STREAM_PRODUCER_TARGET = s3stream_producer
S3STREAM_PRODUCER_LIB = -ls3stream_producer
CLIP_PRODUCER_TARGET = clip_producer
CLIP_PRODUCER_LIB = -lclip_producer
WEBRTC_SDK_LIB = -L$(PREBUILT_ARCH) -lwebrtc
GRPC_TARGET = grpc
GRPC_LIB = -lgrpc
SSL_LIBS = -lssl -lcrypto
# AWS SDK Libraries - using the available libraries
# Complete set of AWS SDK and CRT libraries for S3 functionality
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

# LibAV Libraries for VideoSegmentExtractor
# Unified Storage Libraries
# Individual library variables for specific use cases
UNIFIED_STORAGE_WRITER_LIB = -L$(PREBUILT_DIR)/aws -L$(PREBUILT_DIR)/minio -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR)/aws,-rpath=$(PREBUILT_DIR)/minio -lnvstoragewriter
UNIFIED_STORAGE_READER_LIB = -L$(PREBUILT_DIR)/aws -L$(PREBUILT_DIR)/minio -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR)/aws,-rpath=$(PREBUILT_DIR)/minio -lnvstoragereader
UNIFIED_STORAGE_MANAGER_LIB = -L$(PREBUILT_DIR)/aws -L$(PREBUILT_DIR)/minio -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR)/aws,-rpath=$(PREBUILT_DIR)/minio -lnvstoragemanager

# Common variable combining all storage libraries (reader + writer + manager)
UNIFIED_STORAGE_LIBS = $(UNIFIED_STORAGE_READER_LIB) $(UNIFIED_STORAGE_WRITER_LIB) $(UNIFIED_STORAGE_MANAGER_LIB)

ifeq ($(MODULE), )
# MODULE not set, that means buils all modules
SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)


ifeq ($(arch),x86_64)
    MODULE_TARGET := $(GRPC_TARGET)
endif

MODULE_TARGET += $(ADAPTOR_TARGET) $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(WEBRTC_STREAMER_TARGET) $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) sensor_management rtsp recorder storage_management unified_storage peerconnection_live peerconnection_replay stream_bridge
MODULE_LIBS = $(VIDEO_SOURCE_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) $(OVERLAY_LIB) $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) -lnvsensormanagement -lnvrtspserver -lnvstreamrecorder -lnvstoragemanagement -lnvpeerconnection_live -lnvpeerconnection_replay -lnvstreambridge $(UNIFIED_STORAGE_LIBS) $(AWS_SDK_LIBS) $(SSL_LIBS)

CPPFLAGS += -DSENSOR_MODULE -DRTSP_SERVER_MODULE -DRECORDER_MODULE -DSTORAGE_MODULE -DCLOUD_STORAGE_MODULE -DLIVE_STREAM_MODULE -DREPLAY_STREAM_MODULE -DSTREAMBRIDGE_MODULE -DMONOLITH_MODULE
CPPFLAGS += -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include

ifeq ($(arch),x86_64)
    MODULE_LIBS += $(GRPC_LIB)
    CPPFLAGS += -DUSE_GRPC_SERVER
    CPPFLAGS += -DUSE_GRPC_CLIENT
endif

else # Specific Module
# MODULE is set, so build specific module #
ifeq ($(MODULE), sensor)
    CPPFLAGS += -D SENSOR_MODULE
    MODULE_TARGET := $(ADAPTOR_TARGET) sensor_management
    MODULE_LIBS += -lnvsensormanagement $(SSL_LIBS)
endif # sensor

ifeq ($(MODULE), rtspserver)
    CPPFLAGS += -D RTSP_SERVER_MODULE -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include
    SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)

	MODULE_TARGET := unified_storage $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(WEBRTC_STREAMER_TARGET) rtsp
	MODULE_LIBS += $(UNIFIED_STORAGE_READER_LIB) $(UNIFIED_STORAGE_MANAGER_LIB) $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) $(VIDEO_SOURCE_LIB) $(OVERLAY_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) $(AWS_SDK_LIBS) -lnvrtspserver
endif # rtspserver

ifeq ($(MODULE), recorder)
    CPPFLAGS += -D RECORDER_MODULE -D CLOUD_STORAGE_MODULE
    MODULE_TARGET := unified_storage recorder
    MODULE_LIBS += -lnvstreamrecorder $(UNIFIED_STORAGE_WRITER_LIB) $(AWS_SDK_LIBS) $(SSL_LIBS)
endif # recorder

ifeq ($(MODULE), storage)
    SRCS += $(OVERLAY_SRCS)
    CPPFLAGS += -D STORAGE_MODULE
    MODULE_TARGET := $(ADAPTOR_TARGET) $(OVERLAY_TARGET) $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) unified_storage storage_management
    MODULE_LIBS += $(OVERLAY_LIB) $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) $(UNIFIED_STORAGE_READER_LIB) $(UNIFIED_STORAGE_MANAGER_LIB) $(AWS_SDK_LIBS) $(SSL_LIBS) -lnvstoragemanagement
endif # storage

ifeq ($(MODULE), livestream)
    SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)
    CPPFLAGS += -D LIVE_STREAM_MODULE \
                -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include

    MODULE_TARGET := $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(WEBRTC_STREAMER_TARGET) peerconnection_live

    MODULE_LIBS += $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) $(VIDEO_SOURCE_LIB) $(OVERLAY_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) $(UNIFIED_STORAGE_READER_LIB) $(UNIFIED_STORAGE_MANAGER_LIB) $(AWS_SDK_LIBS) -lnvpeerconnection_live $(SSL_LIBS)
endif # livestream

ifeq ($(MODULE), replaystream)
    SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)
    CPPFLAGS += -D REPLAY_STREAM_MODULE \
                -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include
    MODULE_TARGET := $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) $(WEBRTC_STREAMER_TARGET) peerconnection_replay

    MODULE_LIBS += $(VIDEO_SOURCE_LIB) $(OVERLAY_LIB) $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) $(UNIFIED_STORAGE_READER_LIB) $(UNIFIED_STORAGE_MANAGER_LIB) $(AWS_SDK_LIBS) -lnvpeerconnection_replay $(SSL_LIBS)
endif # replaystream

ifeq ($(MODULE), streambridge)
    SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)

ifeq ($(arch),x86_64)
    MODULE_TARGET := $(GRPC_TARGET)
    MODULE_LIBS += $(GRPC_LIB)
    CPPFLAGS += -D USE_GRPC_SERVER
    CPPFLAGS += -D USE_GRPC_CLIENT
endif
    CPPFLAGS += -DSTREAMBRIDGE_MODULE -DRTSP_SERVER_MODULE -DRECORDER_MODULE -DSTORAGE_MODULE -DCLOUD_STORAGE_MODULE \
                -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include
    MODULE_TARGET += $(ADAPTOR_TARGET) $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(WEBRTC_STREAMER_TARGET) sensor_management rtsp recorder storage_management unified_storage stream_bridge
    MODULE_LIBS += $(VIDEO_SOURCE_LIB) $(OVERLAY_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) -lnvsensormanagement -lnvrtspserver -lnvstreamrecorder -lnvstoragemanagement -lnvstreambridge $(UNIFIED_STORAGE_LIBS) $(AWS_SDK_LIBS)
ifeq ($(arch),x86_64)
    MODULE_LIBS += $(GRPC_LIB)
    CPPFLAGS += -D USE_GRPC_SERVER
    CPPFLAGS += -D USE_GRPC_CLIENT
endif

endif # streambridge

ifeq ($(MODULE), streamprocessing)
    SRCS += $(MEDIA_PIPELINE_SRCS) $(OVERLAY_SRCS)

ifeq ($(arch),x86_64)
    MODULE_TARGET := $(GRPC_TARGET)
endif

MODULE_TARGET += $(ADAPTOR_TARGET) $(VIDEO_SOURCE_TARGET) $(OVERLAY_TARGET) $(WEBRTC_STREAMER_TARGET) $(S3STREAM_PRODUCER_TARGET) $(CLIP_PRODUCER_TARGET) rtsp recorder storage_management unified_storage peerconnection_live peerconnection_replay
MODULE_LIBS = $(VIDEO_SOURCE_LIB) $(WEBRTC_STREAMER_LIB) $(WEBRTC_SDK_LIB) $(OVERLAY_LIB) $(S3STREAM_PRODUCER_LIB) $(CLIP_PRODUCER_LIB) -lnvrtspserver -lnvstreamrecorder -lnvstoragemanagement -lnvpeerconnection_live -lnvpeerconnection_replay  $(UNIFIED_STORAGE_LIBS) $(AWS_SDK_LIBS)

CPPFLAGS += -DRTSP_SERVER_MODULE -DRECORDER_MODULE -DSTORAGE_MODULE -DCLOUD_STORAGE_MODULE -DLIVE_STREAM_MODULE -DREPLAY_STREAM_MODULE
CPPFLAGS += -I $(TOP)/src/framework/webrtc_streamer/inc/webrtc_headers/src/third_party/boringssl/src/include

ifeq ($(arch),x86_64)
    MODULE_LIBS += $(GRPC_LIB)
    CPPFLAGS += -DUSE_GRPC_SERVER
    CPPFLAGS += -DUSE_GRPC_CLIENT
endif

endif # streamprocessing

endif

OBJS := $(SRCS:.cpp=.o)

# OTEL libraries - only for x86_64
ifeq ($(USE_OTEL),1)
OTEL_LIBS := -L$(PREBUILT_ARCH)opentelemetry \
             -lopentelemetry_exporter_otlp_http \
             -lopentelemetry_exporter_otlp_http_client \
             -lopentelemetry_otlp_recordable \
             -lopentelemetry_http_client_curl \
             -lopentelemetry_resources \
             -lopentelemetry_trace \
             -lopentelemetry_logs \
             -lopentelemetry_common \
             -lopentelemetry_proto \
             -lprotobuf
else
OTEL_LIBS :=
endif

LDFLAGS += -L$(PREBUILT_DIR) -ljsoncpp -lcurl -L$(PREBUILT_ARCH) -llivemedia -lnvcmdlineparser -lnvconfigmanager -lnveventloop -lnvfpsdisplay -lnvfsutils \
		   -lnvlogger -lnvnetworkutils -lnvutils -lnvsystemmonitoring -luuid -lnvdatabase -lmmutils -lxml2 -lnvsoap \
           -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR),-rpath=$(SYSROOT)/usr/lib/aarch64-linux-gnu,-rpath=$(SYSROOT)/lib \
           -L$(PREBUILT_ARCH) -lcivetweb -ldl -pthread -rdynamic -lsqlite3 -lpqxx -lpq \
		   -L$(PREBUILT_ARCH)prom_so -lprometheus-cpp-pull -lprometheus-cpp-core -lz -Wl,-rpath=$(PREBUILT_DIR)prom_so \
		   -lglib-2.0 -lgstreamer-1.0 -lgstvideo-1.0 -lgstpbutils-1.0 -lgobject-2.0 -lgstapp-1.0 -L$(PREBUILT_ARCH) -lasync++ -lpaho-mqttpp3 -lpaho-mqtt3as \
		   -lboost_system -lboost_filesystem -lboost_regex -lstream_monitor $(MODULE_LIBS) -lnvstatistics -lboost_thread $(OTEL_LIBS) -Wl,--disable-new-dtags

ifeq ($(arch),x86_64)
LDFLAGS += -lprotobuf
LDFLAGS += -lgrpc `pkg-config --libs opencv4`
endif

.EXPORT_ALL_VARIABLES:

video_container = mp4
ifeq ($(video_container),mkv)
	CPPFLAGS += -D MKV
else
	CPPFLAGS += -D MP4
endif

ifneq ($(CROSS_COMPILE),)
CPPFLAGS += --sysroot=$(JETSON_ROOTFS)
endif

ifeq ($(arch),aarch64)
CPPFLAGS += -D USE_NV_ENC -D USE_NV_DEC
ifneq ($(JETSON_PLATFORM),)
CPPFLAGS += -D JETSON_PLATFORM
else
CPPFLAGS += -D AARCH64_PLATFORM
endif

ifeq ($(BUILD),R32)
CPPFLAGS += -D JETSON_PLATFORM_R32
endif
endif

all: $(APP)

profiler: CPPFLAGS += -D FUNCTION_PROFILER

# "arm64" cross-compiles the sources
arm64: all

vms: $(APP)

debug: CPPFLAGS += -D DEBUG -g
debug: $(APP)

release: CPPFLAGS += -D RELEASE
release: $(APP)

release_package: release package
ifeq ($(MAKECMDGOALS), release_package)
ifeq ($(PROJECT), )
$(error "PROJECT is not set. Run 'PROJECT=<project> make release_package', exiting.")
endif
endif

utilities:
	@echo "Building utilities module....."
	$(MAKE) -C src/framework/utilities/

adaptors:
	@echo "Building adaptors module....."
	$(MAKE) -C src/adaptors/

soap:
	@echo "Building soap module....."
	$(MAKE) -C src/framework/protocols/soap/

grpc:
	@echo "Building grpc module....."
	$(MAKE) -C src/framework/protocols/grpc/

stream_monitor:
	@echo "Building stream monitor module....."
	$(MAKE) -C src/framework/stream_monitor/

s3stream_producer:
	@echo "Building s3 stream producer module....."
	$(MAKE) -C src/framework/media/media_pipelines/cloud_stream/

clip_producer: utilities
	@echo "Building clip producer module....."
	$(MAKE) -C src/framework/media/media_pipelines/local_stream/

sensor_management:
	@echo "Building sensor management module....."
	$(MAKE) -C src/modules/sensor_management/

rtsp: utilities stream_monitor unified_storage
	@echo "Building rtsp server module....."
	$(MAKE) -C src/modules/rtsp_server/

recorder: utilities stream_monitor unified_storage
	@echo "Building recorder module....."
	$(MAKE) -C src/modules/stream_recorder/

# TODO: REMOVE WAR LATER
storage_management: overlays utilities unified_storage s3stream_producer
	@echo "Building storage management module....."
	$(MAKE) -C src/modules/storage_management/

unified_storage: utilities
	@echo "Building unified storage module....."
	$(MAKE) -C src/framework/unified_storage/

video_source: utilities mm_utils unified_storage overlays s3stream_producer clip_producer
	@echo "Building video source module....."
	$(MAKE) -C src/framework/media/video_source/

overlays: mm_utils
	@echo "Building overlays module....."
	$(MAKE) -C src/framework/media/overlays/

webrtc_streamer: video_source overlays
	@echo "Building webrtc streamer module....."
	$(MAKE) -C src/framework/webrtc_streamer/

peerconnection_live: unified_storage
	@echo "Building peerconnection live module....."
	$(MAKE) -C src/modules/webrtc_live/

peerconnection_replay: unified_storage	
	@echo "Building peerconnection replay module....."
	$(MAKE) -C src/modules/webrtc_replay/

stream_bridge:
	@echo "Building stream bridge module....."
	$(MAKE) -C src/modules/webrtc_stream_bridge/

database:
	@echo "Building database module....."
	$(MAKE) -C src/framework/database/

mm_utils:
	@echo "Building media utils module....."
	$(MAKE) -C src/framework/media/media_utils/

notification_proto:
	@echo "Building notification proto module....."
	$(MAKE) -C src/framework/notification/ generate_proto
	$(MAKE) -C src/framework/notification/


%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

# Ensure kafka_consumer.o depends on notification_proto
src/framework/notification/kafka/kafka_consumer.o: notification_proto

ifeq ($(arch),x86_64)
$(APP): utilities stream_monitor mm_utils database soap video_source overlays $(MODULE_TARGET) $(OBJS)
else
$(APP): utilities stream_monitor mm_utils database soap notification_proto video_source overlays $(MODULE_TARGET) $(OBJS)
endif
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS) $(CPPFLAGS) $(LDFLAGS)



# Build unit tests
# Simple approach: compile all test sources directly into executable
# (GTest doesn't work well with tests in separate .so libraries)
#
# Propagate UNIT_TEST globally so sub-makes (libnvrtspserver.so etc.) also
# compile with -D UNIT_TEST.  Target-specific variables are NOT exported to
# recursive $(MAKE) invocations even with .EXPORT_ALL_VARIABLES, so we use
# ifeq(MAKECMDGOALS) which is evaluated at parse-time and affects the global
# CPPFLAGS that IS exported.
ifeq ($(MAKECMDGOALS),tests)
CPPFLAGS += -g -D UNIT_TEST
endif
tests: CPPFLAGS += -g -D UNIT_TEST
tests: $(TEST_TARGET)

# Test configuration
GOOGLE_TEST_LIB = gtest
GOOGLE_TEST_INCLUDE = $(SYSROOT)/usr/local/include

# Test source files (compiled directly, not as .so libraries)
TEST_SRCS := test/gtests/test_main.cpp \
             test/gtests/utils/mock_civetweb.cpp \
             test/gtests/utils/storage_test_utils.cpp \
             test/gtests/utils/sensor_test_utils.cpp \
             test/gtests/utils/recorder_utils.cpp \
             test/gtests/utils/db_test_utils.cpp \
             test/gtests/utils/rtspserver_utils.cpp \
             test/gtests/utils/system_stats_listener.cpp \
             test/gtests/streammonitor.cpp \
             test/gtests/mediapipelinetest.cpp \
             test/gtests/storage.cpp \
             test/gtests/sensor.cpp \
             test/gtests/recorder.cpp \
             test/gtests/rtspserver.cpp \
             test/gtests/replaystream.cpp \
             test/gtests/livestream.cpp

TEST_OBJS := $(TEST_SRCS:.cpp=.o)

# Framework sources for tests
# Exclude files with dependencies on modules/features we don't build:
# - Main.cpp: has main(), conflicts with test_main.cpp
# - server.cpp: depends on ALL modules (sensor, peerconnection, etc.)
# - websocket_apis.cpp: needs gRPC generated files
# - user_apis.cpp: has sensor management dependencies
# - remote_sensor_control_apis.cpp: needs gRPC generated files (vstserver.grpc.pb.h)
FRAMEWORK_SRCS := $(filter-out src/app/Main.cpp src/app/server.cpp src/framework/apis/websocket_apis.cpp src/framework/apis/user_apis.cpp src/modules/sensor_management/remote_sensor_control_apis.cpp,$(SRCS))
FRAMEWORK_OBJS := $(FRAMEWORK_SRCS:.cpp=.o)



# Test-specific module libraries (modules + dependencies for storage & recorder tests)
TEST_MODULE_LIBS := -lnvstoragemanagement \
                    -lnvstreamrecorder \
                    -lnvsensormanagement \
                    -lnvrtspserver \
                    -lnvpeerconnection_live \
                    -lnvpeerconnection_replay \
                    $(VIDEO_SOURCE_LIB) \
                    $(WEBRTC_STREAMER_LIB) \
                    $(WEBRTC_SDK_LIB) \
                    $(OVERLAY_LIB) \
                    $(S3STREAM_PRODUCER_LIB) \
                    $(CLIP_PRODUCER_LIB) \
                    $(UNIFIED_STORAGE_LIBS) \
                    $(AWS_SDK_LIBS) \
                    $(SSL_LIBS) \
					$(GRPC_LIB)

# Base libraries for tests (core libs WITHOUT MODULE_LIBS to avoid monolith modules)
TEST_BASE_LIBS := -lldap -llber -L$(PREBUILT_DIR) -ljsoncpp -lcurl -L$(PREBUILT_ARCH) -llivemedia \
                  -lnvutils -lnvcmdlineparser -lnvconfigmanager -lnveventloop -lnvfpsdisplay -lnvfsutils \
                  -lnvlogger -lnvnetworkutils -lnvsystemmonitoring -luuid -lnvdatabase -lmmutils -lxml2 -lnvsoap \
                  -Wl,--disable-new-dtags,-rpath=$(PREBUILT_DIR),-rpath=$(SYSROOT)/usr/lib/aarch64-linux-gnu,-rpath=$(SYSROOT)/lib \
                  -L$(PREBUILT_ARCH) -lcivetweb -ldl -pthread -rdynamic -lsqlite3 -lpqxx -lpq \
                  -L$(PREBUILT_ARCH)prom_so -lprometheus-cpp-pull -lprometheus-cpp-core -lz -Wl,-rpath=$(PREBUILT_DIR)prom_so \
                  -lglib-2.0 -lgstreamer-1.0 -lgstvideo-1.0 -lgstpbutils-1.0 -lgobject-2.0 -lgstapp-1.0 \
                  -L$(PREBUILT_ARCH) -lasync++ -lpaho-mqttpp3 -lpaho-mqtt3as \
                  -lboost_system -lboost_filesystem -lboost_regex -lstream_monitor -lnvstatistics \
                  $(OTEL_LIBS) -Wl,--disable-new-dtags

# Add arch-specific libs and boost_thread (needed by webrtc_streamer)
ifeq ($(arch),x86_64)
TEST_BASE_LIBS += -lprotobuf -lgrpc `pkg-config --libs opencv4` -lboost_thread
endif

# Complete linker flags for test executable
# Uses clean base libs + only test modules (no monolith modules)
#
# --allow-multiple-definition is required because mock_civetweb.cpp
# (test/gtests/utils/mock_civetweb.cpp) provides test-only overrides for
# these 6 CivetWeb symbols that are also defined in libcivetweb.a:
#   mg_get_request_info, mg_get_header, mg_read,
#   mg_handle_form_request, mg_send_mime_file2, mg_send_file
# The mock objects are linked before -lcivetweb so the linker picks
# the mock definitions first; the flag suppresses the duplicate error
# for the originals in the static archive.
TEST_LD_FLAGS = -Wl,--allow-multiple-definition \
                -Wl,--copy-dt-needed-entries \
                -L$(PREBUILT_DIR) \
                $(TEST_BASE_LIBS) \
                $(TEST_MODULE_LIBS) \
                -lboost_thread \
                -L /usr/local/lib -l$(GOOGLE_TEST_LIB)

# Compile test files (use -MF $(@:.o=.d) so .d lives next to .o; DEPFLAGS uses $*.d which is wrong for test/gtests/subdir/foo.o)
test/gtests/%.o: test/gtests/%.cpp
	@mkdir -p $(dir $@)
	@echo "Compiling test: $<"
	$(CXX) -MT $@ -MMD -MP -MF $(@:.o=.d) -I$(GOOGLE_TEST_INCLUDE) $(CPPFLAGS) -g -c $< -o $@

# Link final test executable (simple approach - all objects linked directly)
$(TEST_TARGET): adaptors utilities stream_monitor mm_utils database soap grpc $(WEBRTC_STREAMER_TARGET) video_source overlays sensor_management recorder storage_management unified_storage rtsp peerconnection_live peerconnection_replay $(TEST_OBJS) $(FRAMEWORK_OBJS)
	@echo "============================================"
	@echo "Linking test executable: $@"
	@echo "Test files:"
	@echo "  - test_main.cpp (GTest entry)"
	@echo "  - mock_civetweb.cpp (6 mocked functions)"
	@echo "  - storage_test_utils.cpp (8 utilities)"
	@echo "  - recorder_utils.cpp (timeline utilities)"
	@echo "  - db_test_utils.cpp (DB verification utilities)"
	@echo "  - streammonitor.cpp (StreamMonitor RTSP client basic tests)"
	@echo "  - storage.cpp (storage tests)"
	@echo "  - recorder.cpp (recorder tests)"
	@echo "  - rtspserver.cpp (RTSP proxy tests)"
	@echo "  - livestream.cpp (live streaming tests)"
	@echo "  - replaystream.cpp (replay streaming tests)"
	@echo "Module libraries:"
	@echo "  - libnvstoragemanagement.so"
	@echo "  - libnvstreamrecorder.so"
	@echo "  - libnvrtspserver.so"
	@echo "  - libnvpeerconnection_live.so"
	@echo "  - libnvpeerconnection_replay.so"
	@echo "  - libnvvideo_source.so + dependencies"
	@echo "============================================"
	$(CXX) -o $@ $(TEST_OBJS) $(FRAMEWORK_OBJS) $(TEST_LD_FLAGS)
	@echo "============================================"
	@echo "Test executable built successfully"
	@echo "Run with: ./vst_test --gtest_list_tests"
	@echo "============================================"

ifeq ($(PROJECT),vst)
RELEASE_VERSION = $(VST_VERSION)
endif

ifeq ($(arch),x86_64)
package : $(APP)
else
package : $(APP)
endif
	packaging/package_vms.sh

include $(TOP)/Makefile.dep

# Exclude protobuf files from being treated as dependencies
%.pb.cc.d: %.pb.cc
	@echo "Skipping dependency generation for protobuf file: $<"

%.pb.h.d: %.pb.h
	@echo "Skipping dependency generation for protobuf file: $<"

clean:
	$(AT)rm -rf $(APP) $(OBJS) $(TEST_TARGET) $(TEST_OBJS)
	$(MAKE) -C src/adaptors/onvif/ soclean
	$(MAKE) -C src/adaptors/rtsp_streams/ clean
	$(MAKE) -C src/adaptors/ clean
	$(MAKE) -C src/framework/protocols/soap/ clean
	$(MAKE) -C src/framework/stream_monitor clean
	$(MAKE) -C src/framework/media/media_pipelines/cloud_stream/ clean
	$(MAKE) -C src/framework/media/media_pipelines/local_stream/ clean
	$(MAKE) -C src/framework/protocols/grpc/ clean
	$(MAKE) -C src/framework/notification/ clean
	$(MAKE) -C src/framework/media/media_utils/ clean
	$(MAKE) -C src/framework/media/video_source/ clean
	$(MAKE) -C src/framework/media/overlays/ clean
	$(MAKE) -C src/framework/webrtc_streamer/ clean
	$(MAKE) -C src/framework/utilities/ clean
	$(MAKE) -C src/framework/database/ clean
	$(MAKE) -C src/framework/unified_storage/ clean
	$(MAKE) -C src/modules/sensor_management/ clean
	$(MAKE) -C src/modules/rtsp_server/ clean
	$(MAKE) -C src/modules/stream_recorder/ clean
	$(MAKE) -C src/modules/storage_management/ clean
	$(MAKE) -C src/modules/webrtc_live/ clean
	$(MAKE) -C src/modules/webrtc_replay/ clean
	$(MAKE) -C src/modules/webrtc_stream_bridge/ clean

.PHONY: all adaptor gstmuxdemux tests clean
endif

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

TARGET_CMDLINE_PARSER := $(PREBUILT)libnvcmdlineparser.so
SRCS_CMDLINE_PARSER := cmdline_parser.cpp
OBJS_CMDLINE_PARSER := $(SRCS_CMDLINE_PARSER:.cpp=.o)

TARGET_CONFIG_MANAGER := $(PREBUILT)libnvconfigmanager.so
SRCS_CONFIG_MANAGER := config.cpp
OBJS_CONFIG_MANAGER := $(SRCS_CONFIG_MANAGER:.cpp=.o)

TARGET_EVENT_LOOP := $(PREBUILT)libnveventloop.so
SRCS_EVENT_LOOP := event_loop.cpp
OBJS_EVENT_LOOP := $(SRCS_EVENT_LOOP:.cpp=.o)

TARGET_FPS_DISPLAY := $(PREBUILT)libnvfpsdisplay.so
SRCS_FPS_DISPLAY := fps_display.cpp
OBJS_FPS_DISPLAY := $(SRCS_FPS_DISPLAY:.cpp=.o)

TARGET_FS_UTILS := $(PREBUILT)libnvfsutils.so
SRCS_FS_UTILS := fs_utils.cpp \
                 TempFileScheduler.cpp
OBJS_FS_UTILS := $(SRCS_FS_UTILS:.cpp=.o)

TARGET_LOGGER := $(PREBUILT)libnvlogger.so
SRCS_LOGGER := logger.cpp
OBJS_LOGGER := $(SRCS_LOGGER:.cpp=.o)

TARGET_NW_UTILS := $(PREBUILT)libnvnetworkutils.so
SRCS_NW_UTILS := network_utils.cpp
OBJS_NW_UTILS := $(SRCS_NW_UTILS:.cpp=.o)

TARGET_STATS := $(PREBUILT)libnvstatistics.so
SRCS_STATS := stats.cpp
OBJS_STATS := $(SRCS_STATS:.cpp=.o)

TARGET_UTILS := $(PREBUILT)libnvutils.so
SRCS_UTILS := utils.cpp
OBJS_UTILS := $(SRCS_UTILS:.cpp=.o)

TARGET_SYSTEM_MONITORING := $(PREBUILT)libnvsystemmonitoring.so
SRCS_SYSTEM_MONITORING := SystemMonitoringTask.cpp
OBJS_SYSTEM_MONITORING := $(SRCS_SYSTEM_MONITORING:.cpp=.o)

CPPFLAGS += -I ./
LDFLAGS := -rdynamic --shared

all: $(TARGET_CMDLINE_PARSER) $(TARGET_CONFIG_MANAGER) $(TARGET_EVENT_LOOP) $(TARGET_FPS_DISPLAY) $(TARGET_FS_UTILS) $(TARGET_LOGGER) $(TARGET_NW_UTILS) $(TARGET_STATS) $(TARGET_UTILS) $(TARGET_SYSTEM_MONITORING)

%.o: %.cpp %.d
	@echo "Compiling: $<"
	$(CXX) $(DEPFLAGS) $(CPPFLAGS) -c $< -o $@

$(TARGET_CMDLINE_PARSER): $(OBJS_CMDLINE_PARSER)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_CMDLINE_PARSER) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_CONFIG_MANAGER): $(OBJS_CONFIG_MANAGER)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_CONFIG_MANAGER) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_EVENT_LOOP): $(OBJS_EVENT_LOOP)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_EVENT_LOOP) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_FPS_DISPLAY): $(OBJS_FPS_DISPLAY)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_FPS_DISPLAY) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_FS_UTILS): $(OBJS_FS_UTILS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_FS_UTILS) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_LOGGER): $(OBJS_LOGGER)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_LOGGER) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_NW_UTILS): $(OBJS_NW_UTILS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_NW_UTILS) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_STATS): $(OBJS_STATS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_STATS) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_UTILS): $(OBJS_UTILS)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_UTILS) $(CPPFLAGS) $(LDFLAGS)

$(TARGET_SYSTEM_MONITORING): $(OBJS_SYSTEM_MONITORING)
	@echo "Linking: $@"
	$(CXX) -o $@ $(OBJS_SYSTEM_MONITORING) $(CPPFLAGS) $(LDFLAGS)

include $(TOP)/Makefile.dep

clean:
	$(AT)rm -rf $(TARGET_CMDLINE_PARSER) $(OBJS_CMDLINE_PARSER)
	$(AT)rm -rf $(TARGET_CONFIG_MANAGER) $(OBJS_CONFIG_MANAGER)
	$(AT)rm -rf $(TARGET_EVENT_LOOP) $(OBJS_EVENT_LOOP)
	$(AT)rm -rf $(TARGET_FPS_DISPLAY) $(OBJS_FPS_DISPLAY)
	$(AT)rm -rf $(TARGET_FS_UTILS) $(OBJS_FS_UTILS)
	$(AT)rm -rf $(TARGET_LOGGER) $(OBJS_LOGGER)
	$(AT)rm -rf $(TARGET_NW_UTILS) $(OBJS_NW_UTILS)
	$(AT)rm -rf $(TARGET_STATS) $(OBJS_STATS)
	$(AT)rm -rf $(TARGET_UTILS) $(OBJS_UTILS)
	$(AT)rm -rf $(TARGET_SYSTEM_MONITORING) $(OBJS_SYSTEM_MONITORING)

.PHONY: all clean
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

all:
	$(MAKE) -C ./milestone/
	$(MAKE) -C ./onvif/
	$(MAKE) -C ./sensors_discovery/onvif
	$(MAKE) -C ./sensors_discovery/native_sensors
	$(MAKE) -C ./streamer/
	$(MAKE) -C ./rtsp_streams/
	$(MAKE) -C ./remote_device/
	$(MAKE) -C ./native_sensors/
	$(MAKE) -C ./vms_media/

%:
	$(MAKE) -C ./milestone/ $@
	$(MAKE) -C ./streamer/ clean
	$(MAKE) -C ./onvif/ soclean
	$(MAKE) -C ./sensors_discovery/onvif soclean
	$(MAKE) -C ./sensors_discovery/native_sensors soclean
	$(MAKE) -C ./rtsp_streams/ clean
	$(MAKE) -C ./remote_device/ clean
	$(MAKE) -C ./native_sensors/ clean
	$(MAKE) -C ./vms_media/ clean
/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "rtc_base/logging.h"
#include "logger.h"

#include "nvgstvideocapturer.h"

NvGstVideoCapturer::NvGstVideoCapturer(const std::string & uri, const std::map<std::string,std::string, std::less<>> & opts)
	: NvGstVideoSource(uri, opts)
{
	LOG(info) << __METHOD_NAME__ << uri << endl;
}

NvGstVideoCapturer::~NvGstVideoCapturer()
{
	LOG(info) << __METHOD_NAME__ << endl;
}

void NvGstVideoCapturer::getDecoderStats(LatencyStats& stats)
{
    getDecodeStats(stats);
    return;
}

VmsErrorCode NvGstVideoCapturer::controlStreamCapturer(const std::string& action, const std::string& seek_value)
{
	return this->controlStreamFileVideoSource(action, seek_value);
}

void NvGstVideoCapturer::startPlayback()
{
	LOG(info) << "startStream: " << endl;
	this->startStream();
	return;
}

void NvGstVideoCapturer::switchStreamCapturer(std::string url, const std::map<std::string, std::string, std::less<>> &opts)
{
	this->switchStreamVideoSource(url, opts);
	return;
}

gint64 NvGstVideoCapturer::getPositionCapturer()
{
	return this->getPositionFileVideoSource();
}

void NvGstVideoCapturer::streamSettingCapturer(const std::unordered_map<std::string, std::string> &opts)
{
	this->streamSettingVideoSource(opts);
	return;
}

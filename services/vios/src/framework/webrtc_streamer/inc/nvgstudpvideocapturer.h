/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#pragma once
#include "rtc_base/ref_counted_object.h"

#include "nvgstudpvideosource.h"

class NvGstUDPVideoCapturer : public NvGstUDPVideoSource
{
	public:
		NvGstUDPVideoCapturer(const std::string & uri, const std::map<std::string,std::string, std::less<>> & opts);
		virtual ~NvGstUDPVideoCapturer();
	
		static NvGstUDPVideoCapturer* Create(const std::string & url, const std::map<std::string, std::string, std::less<>> & opts)
		{
			return new NvGstUDPVideoCapturer(url, opts);
		}
		void controlStreamCapturer(const std::string&, const std::string&);
		void switchStreamCapturer(std::string url, const std::map<std::string, std::string, std::less<>> &opts);
		gint64 getPositionCapturer();
		void getDecoderStats(LatencyStats& stats);
		void startPlayback();
};



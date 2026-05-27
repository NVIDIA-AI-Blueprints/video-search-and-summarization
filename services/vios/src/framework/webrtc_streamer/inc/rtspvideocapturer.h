/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/* ---------------------------------------------------------------------------
** This software is in the public domain, furnished "as is", without technical
** support, and with no warranty, express or implied, as to its usefulness for
** any purpose.
**
** rtspvideocapturer.h
**
** -------------------------------------------------------------------------*/

#pragma once

#include "rtspconnectionclient.h"
#include "livevideosource.h"

class RTSPVideoCapturer : public LiveVideoSource<RTSPConnection>
{
	public:
		RTSPVideoCapturer(const std::string & uri, const std::map<std::string,std::string, std::less<>> & opts);
		virtual ~RTSPVideoCapturer();

		static RTSPVideoCapturer* Create(const std::string & url, const std::map<std::string, std::string, std::less<>> & opts)
		{
			return new RTSPVideoCapturer(url, opts);
		}
		
		void getDecoderStats(LatencyStats& stats);

		VmsErrorCode controlStreamCapturer(const std::string&, const std::string&);

		void startPlayback();

		std::pair <std::string, std::string> getUrlsPath();

		void setEOS();

		// overide RTSPConnection::Callback
		virtual void    onConnectionTimeout(RTSPConnection& connection) override {
				connection.start();
		}
		virtual void    onDataTimeout(RTSPConnection& connection) override {
				connection.start();
		}
		virtual void    onError(RTSPConnection& connection,const char* erro) override;

        virtual void onEOS(RTSPConnection& connection) override
        {
            setEOS();
        }
};



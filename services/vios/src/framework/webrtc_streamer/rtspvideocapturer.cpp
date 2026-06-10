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
** rtspvideocapturer.cpp
**
** -------------------------------------------------------------------------*/

#include "rtc_base/logging.h"

#include "rtspvideocapturer.h"
#include "logger.h"
#include "stats.h"

RTSPVideoCapturer::RTSPVideoCapturer(const std::string & uri, const std::map<std::string,std::string, std::less<>> & opts)
	: LiveVideoSource(uri, opts)
{
	LOG(verbose) << "RTSPVideoCapturer " << uri ;
}

RTSPVideoCapturer::~RTSPVideoCapturer()
{
}

void RTSPVideoCapturer::getDecoderStats(LatencyStats& stats)
{
	if(m_gstdecoder)
	{
        m_gstdecoder->getStats(m_peerid, stats);
	}
    return;
}

VmsErrorCode RTSPVideoCapturer::controlStreamCapturer(const std::string& action, const std::string& seek_value)	{
	this->controlStreamLiveVideoSource(action, seek_value);
	if(m_gstdecoder)
	{
		if (action == "pause")
		{
			m_gstdecoder->pause();
		}
		else if (action == "resume")
		{
			m_gstdecoder->play();
		}
		else
		{
			// Ignore the cmd as it is not supported in decoder.
		}
	}
	return VmsErrorCode::NoError;
}

void RTSPVideoCapturer::startPlayback()	{
	if(m_gstdecoder)
	{
		startStream();
	}
}

std::pair <std::string, std::string> RTSPVideoCapturer::getUrlsPath()
{
	std::pair <std::string, std::string> path;
	if(m_gstdecoder)
	{
        path = m_gstdecoder->getUrlPath();
	}
	return path;
}

void RTSPVideoCapturer::setEOS()
{
    if (m_gstdecoder)
    {
       return m_gstdecoder->setEOS();
    }
}

void RTSPVideoCapturer::onError(RTSPConnection& connection, const char* error) {
	LOG(error) << "RTSPVideoCapturer:onError url:" << secureUrlForLogging(m_liveclient.getUrl()) <<  " error:" << error;
	connection.start(1);
}


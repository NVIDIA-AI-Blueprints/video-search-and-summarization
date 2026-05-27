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
** SessionSink.h
** 
** -------------------------------------------------------------------------*/

#pragma once

#include <stdint.h>

#if defined(_MSC_VER)
#include <BaseTsd.h>
typedef SSIZE_T ssize_t;
#endif

#include "environment.h"
#include "liveMedia.hh"
#include <string>
#include "mm_utils.h"

/* ---------------------------------------------------------------------------
**  Media client callback interface
** -------------------------------------------------------------------------*/
class SessionCallback
{
		
	public:
		virtual bool    onNewSession(const char* id, const char* media, const char* codec, const char* sdp) { return true; }
		virtual bool    onData(const char* id, unsigned char* buffer, ssize_t size, struct timeval presentationTime) = 0;
		virtual ssize_t onNewBuffer(const char* id, const char* mime, unsigned char* buffer, ssize_t size)  { 
			ssize_t markerSize = 0;
			if ( (strcmp(mime, "video/H264") == 0) || (strcmp(mime, "video/H265") == 0) ) {
				vector<uint8_t> h26xMarker = getDefaultH26xMarker();
				memcpy( buffer, h26xMarker.data(), h26xMarker.size());
				markerSize = h26xMarker.size();
			}
			return 	markerSize;
		}
};

/* ---------------------------------------------------------------------------
**  Media client Sink
** -------------------------------------------------------------------------*/
class SessionSink: public MediaSink 
{
	public:
		static SessionSink* createNew(UsageEnvironment& env, SessionCallback* callback, bool qosmode = false) { return new SessionSink(env, callback, qosmode); }
		std::string sessionSinkTS;
		int gotTimeStamp;

	private:
		SessionSink(UsageEnvironment& env, SessionCallback* callback, bool qosmode);
		virtual ~SessionSink();

		void allocate(ssize_t bufferSize);

		static void afterGettingFrame(void* clientData, unsigned frameSize,
					unsigned numTruncatedBytes,
					struct timeval presentationTime,
					unsigned durationInMicroseconds)
		{
			static_cast<SessionSink*>(clientData)->afterGettingFrame(frameSize, numTruncatedBytes, presentationTime, durationInMicroseconds);
		}
		
		void afterGettingFrame(unsigned frameSize, unsigned numTruncatedBytes, struct timeval presentationTime, unsigned durationInMicroseconds);

		virtual Boolean continuePlaying();

	private:
		u_int8_t*              m_buffer;
		size_t                 m_bufferSize;
		SessionCallback*       m_callback; 	
		ssize_t                m_markerSize;
		bool                   m_isQosMode;
};

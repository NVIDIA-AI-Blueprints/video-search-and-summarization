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
** SessionSink.cpp
** 
** -------------------------------------------------------------------------*/


#include "SessionSink.h"
#include "logger.h"
#include "stats.h"

#include <utility>
#include <ctime>
#include <chrono>
#include <iostream>

#define MAX_PACKET_BUFFER_SIZE_IN_MB 1 * 1024 * 1024

SessionSink::SessionSink(UsageEnvironment& env, SessionCallback* callback, bool qosmode)
	: MediaSink(env)
	, gotTimeStamp(0)
	, m_buffer(nullptr)
	, m_bufferSize(0)
	, m_callback(callback) 
	, m_markerSize(0)
	, m_isQosMode(qosmode)
{
}

SessionSink::~SessionSink()
{
	delete [] m_buffer;
}

void SessionSink::allocate(ssize_t bufferSize)
{
	m_bufferSize = bufferSize;
	m_buffer = new u_int8_t[m_bufferSize];
	if (m_callback)
	{
		m_markerSize = m_callback->onNewBuffer(this->name(), this->source()->MIMEtype(), m_buffer, m_bufferSize);
		LOG(info) << "markerSize:" << (int)m_markerSize << "\n";
	}
}


void SessionSink::afterGettingFrame(unsigned frameSize, unsigned numTruncatedBytes, struct timeval presentationTime, unsigned durationInMicroseconds)
{
	int64_t recvFrameTS, user_given_time;

	// If QoS client then return immidiately.
	if (m_isQosMode && m_callback)
	{
		m_callback->onData(this->name(), m_buffer, frameSize+m_markerSize, presentationTime);
		goto _exit;
	}

	// Get the frame Timestamp from the FrameSource
	recvFrameTS = source()->fFrameTimestamp;

	// if the ONVIF Extn Header is missing and if MMS receives no TS
	// then forward the received stream as it is.
	if (recvFrameTS)
	{
		// Check if start time is specified by the user, if specified convert it
		// to time since epoch
		if (!sessionSinkTS.empty() && !gotTimeStamp)
		{
			std::tm tm = {};
			const char* snext = ::strptime(sessionSinkTS.c_str(), "%Y%m%dT%H%M%S", &tm);
			// Check if 'sessionSinkTS' contains garbage value then corresponding 'snext' value will be NULL
			if (snext != nullptr)
			{
				auto time_point = std::chrono::system_clock::from_time_t(std::mktime(&tm) - timezone);
				// Convert the user specified time to time since epoch in microseconds
				user_given_time = time_point.time_since_epoch() / std::chrono::microseconds(1) + std::atof(snext) * 1000000.0f;

				// Compare user specified time with the recorded timestamp of incoming frame
				// WAR: 1 ms is substracted from user requested timestamp to handle rounding off timestamp in ONVIF Header
				// Extension from Milestone VMS.
				if ((user_given_time - 1000) <= recvFrameTS && gotTimeStamp == 0)
				{
					Stats& pcStreamStats = Stats::getInstance();
					pcStreamStats.addQueueEntry(std::make_pair(user_given_time, recvFrameTS));
					gotTimeStamp = 1;
				}

				// Send PTS as zero till the time we dont get the requested timestamp, so that decoder will not broadcast 
				// the frames. Check DecodedImageCallback function in VideoDecoder.h
				if (!gotTimeStamp)
				{
					presentationTime = {0};
				}
			}
		}
	}

	if (gotTimeStamp)
	{
		presentationTime.tv_sec = recvFrameTS / 1000;
		presentationTime.tv_usec = (recvFrameTS % 1000) * 1000;
	}

	if (numTruncatedBytes != 0)
	{
		delete [] m_buffer;
		LOG(warning) << "buffer too small " << (int)m_bufferSize << " allocate bigger one\n";
		allocate(m_bufferSize*2);
	}
	else if (m_callback)
	{
		if (!m_callback->onData(this->name(), m_buffer, frameSize+m_markerSize, presentationTime))
		{
			LOG(error) << "NOTIFY failed\n";
		}
		m_callback->onData("record_time", (unsigned char*)(&recvFrameTS), 0, presentationTime);
	}
_exit:
	this->continuePlaying();
}

Boolean SessionSink::continuePlaying()
{
	if (m_buffer == nullptr) 
	{
		allocate(MAX_PACKET_BUFFER_SIZE_IN_MB);
	}
	Boolean ret = False;
	if (source() != nullptr)
	{
		source()->getNextFrame(m_buffer+m_markerSize, m_bufferSize-m_markerSize,
				afterGettingFrame, this,
				onSourceClosure, this);
		ret = True;
	}
	return ret;	
}

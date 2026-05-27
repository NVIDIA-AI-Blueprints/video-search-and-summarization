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
** mkvclient.cpp
** 
** Interface to an MKV client
** 
** -------------------------------------------------------------------------*/


#include <sstream>

#include "mkvclient.h"
#include "logger.h"

void MKVClient::onMatroskaFileCreation(MatroskaFile* newFile) {
	
	m_mkvfile = newFile;
	m_demux = m_mkvfile->newDemux();

	unsigned trackNumber = 0;
	FramedSource* trackSource = nullptr;
	while ( (trackSource = m_demux->newDemuxedTrack(trackNumber)) != nullptr) {
		LOG(info) << "track:" << trackNumber << "\n";
		
		
		MatroskaTrack* track = m_mkvfile->lookup(trackNumber);
		if (track) {
			std::istringstream is(track->mimeType);
			std::string media;
			getline(is, media, '/');
			std::string codec;
			getline(is, codec, '/');
			
			std::ostringstream os;
			struct sockaddr_storage destinationAddress;
			const Port rtpPort(0);
			Groupsock gs(m_env, destinationAddress, rtpPort, 0);

			RTPSink* rtpsink = m_mkvfile->createRTPSinkForTrackNumber(trackNumber, &gs, 96);
			os << rtpsink->rtpmapLine();
			const char* auxLine = rtpsink->auxSDPLine();
			if (auxLine) {
				os << auxLine;

			}
			Medium::close(rtpsink);
			std::string sdp(os.str());

			MediaSink* sink = SessionSink::createNew(m_env, m_callback);	 
			if (sink == nullptr) 
			{
				LOG(error) << "Failed to create sink for \"" << track->mimeType << "\" subsession error: " << m_env.getResultMsg() << "\n";
				m_callback->onError(*this, m_env.getResultMsg());			
			} 
			else if (m_callback->onNewSession(sink->name(), media.c_str(), codec.c_str(), sdp.c_str())) 
			{
				LOG(info) << "Start playing sink for \"" << track->mimeType << "\" sdp:" << sdp.c_str() << "\n";
				sink->startPlaying(*trackSource, onEndOfFile, this);	  
			} 
			else 
			{
				// MKV need to start all tracks to start to read the file
				Medium::close(sink);
				sink = SessionSink::createNew(m_env, nullptr);
				if (sink != nullptr)
				{
					sink->startPlaying(*trackSource, onEndOfFile, this);
				}
			}		
		}
	}
}

void MKVClient::onEndOfFile() {
	if (m_callback) {
		m_callback->onEndOfFile(*this);
	}
}
	

MKVClient::MKVClient(Environment& env, Callback* callback, const char* url, const std::map<std::string,std::string, std::less<>> & opts, int verbosityLevel) 
				: m_env(env)
				, m_callback(callback)
{
	const char* prefix = "file://";

	std::string fileurl(url);
	if (fileurl.find(prefix) == 0) {
		fileurl = fileurl.erase(0,strlen(prefix));
	}

	MatroskaFile::createNew(env, fileurl.c_str(), onMatroskaFileCreation, this);	
}

MKVClient::~MKVClient() noexcept
{
	try {
		if (m_demux != nullptr)  {
			Medium::close(m_demux);
		}
	} catch (const std::exception& e) {
		LOG(error) << "Exception closing m_demux: " << e.what() << "\n";
	} catch (...) {
	}

	try {
		if (m_mkvfile != nullptr)  {
			Medium::close(m_mkvfile);
		}
	} catch (const std::exception& e) {
		LOG(error) << "Exception closing m_mkvfile: " << e.what() << "\n";
	} catch (...) {
	}
}


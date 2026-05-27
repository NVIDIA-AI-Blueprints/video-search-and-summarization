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
** sdpclient.cpp
** 
** Interface to an SDP client
** 
** -------------------------------------------------------------------------*/


#include "sdpclient.h"
#include "logger.h"

SDPClient::SDPClient(Environment& env, Callback* callback, const char* sdp) 
				: m_env(env)
				, m_callback(callback)
{
	m_session = MediaSession::createNew(m_env, sdp);  	
	    
	if (m_session == nullptr) {
		LOG(error) << "Failed to create session from \"" << sdp << "\" error: " << m_env.getResultMsg() << "\n";
	} else {
		MediaSubsessionIterator iter(*m_session);
		MediaSubsession* subsession = nullptr;
		while ((subsession = iter.next()) != nullptr)  {		    
			if (!subsession->initiate()) {
				LOG(error) << "Failed to create sink for \"" << subsession->mediumName() << "/" << subsession->codecName() << "\" subsession error: " << m_env.getResultMsg() << "\n";
			} else {
				MediaSink* sink = SessionSink::createNew(m_env, m_callback);
				if (sink == nullptr) 
				{
					LOG(error) << "Failed to create sink for \"" << subsession->mediumName() << "/" << subsession->codecName() << "\" subsession error: " << m_env.getResultMsg() << "\n";
					m_callback->onError(*this, m_env.getResultMsg());			
				} 
				else if (m_callback->onNewSession(sink->name(), subsession->mediumName(), subsession->codecName(), subsession->savedSDPLines())) 
				{
					LOG(info) << "Start playing sink for \"" << subsession->mediumName() << "/" << subsession->codecName() << "\" subsession" << "\n";
					subsession->sink = sink;
					subsession->sink->startPlaying(*(subsession->readSource()), nullptr, nullptr);
				} 
				else 
				{
					Medium::close(sink);
				}	
			}		
		}
	}
}

SDPClient::~SDPClient()
{	
	// free subsession
	if (m_session != nullptr) 
	{
		MediaSubsessionIterator iter(*m_session);
		MediaSubsession* subsession;
		while ((subsession = iter.next()) != nullptr) 
		{
			if (subsession->sink) 
			{
				LOG(info) << "Close session: " << subsession->mediumName() << "/" << subsession->codecName() << "\n";
				Medium::close(subsession->sink);
				subsession->sink = nullptr;
			}
		}	
		Medium::close(m_session);
	}
}

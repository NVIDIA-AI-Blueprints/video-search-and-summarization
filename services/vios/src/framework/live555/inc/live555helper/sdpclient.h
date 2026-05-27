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
** sdpclient.h
** 
** Interface to an SDP client
** 
** -------------------------------------------------------------------------*/

#pragma once

#include "environment.h"
#include "SessionSink.h"
#include "liveMedia.hh"
#include <string>


/* ---------------------------------------------------------------------------
**  SDP client connection interface
** -------------------------------------------------------------------------*/
class SDPClient 
{
	public:
		/* ---------------------------------------------------------------------------
		**  SDP client callback interface
		** -------------------------------------------------------------------------*/
		class Callback : public SessionCallback
		{
			public:
				virtual void    onError(SDPClient&, const char*)  {}
		};
			
	public:
		SDPClient(Environment& env, Callback* callback, const char* SDP);
		virtual ~SDPClient();
	
	protected:
		Environment&             m_env;
		Callback*                m_callback; 	
		MediaSession*            m_session;                   	
};

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
** mkvclient.h
** 
** Interface to an MKV client
** 
** -------------------------------------------------------------------------*/

#pragma once

#include "environment.h"
#include "SessionSink.h"
#include "liveMedia.hh"
#include <string>
#include <map>


/* ---------------------------------------------------------------------------
**  MKV client connection interface
** -------------------------------------------------------------------------*/
class MKVClient 
{
	public:
		/* ---------------------------------------------------------------------------
		**  MKV client callback interface
		** -------------------------------------------------------------------------*/
		class Callback : public SessionCallback
		{
			public:
				virtual void    onError(MKVClient&, const char*)  {}
				virtual void    onEndOfFile(MKVClient& client)  { client.stop(); }
		};
			
	public:
		MKVClient(Environment& env, Callback* callback, const char* path, const std::map<std::string,std::string, std::less<>> & opts, int verbosityLevel=1);
		virtual ~MKVClient() noexcept;

		void stop() {
			m_env.stop();
		}
	
	private:
		void onMatroskaFileCreation(MatroskaFile* newFile);
		static void onMatroskaFileCreation(MatroskaFile* newFile, void* clientData) {
			((MKVClient*)(clientData))->onMatroskaFileCreation(newFile);
		}
		void onEndOfFile();
		static void onEndOfFile(void* clientData) {
			((MKVClient*)(clientData))->onEndOfFile();
		}
		
	protected:
		Environment&             m_env;
		Callback*                m_callback; 	
		MatroskaFile*            m_mkvfile; 
		MatroskaDemux*	         m_demux;
};

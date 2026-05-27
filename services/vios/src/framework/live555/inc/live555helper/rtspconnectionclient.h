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
** rtspconnectionclient.h
** 
** Interface to an RTSP client connection
** 
** -------------------------------------------------------------------------*/

#pragma once

#include "environment.h"
#include "SessionSink.h"
#include "liveMedia.hh"
#include <string>
#include <map>
#include <memory>
#include "utils.h"

#define DEFAULT_STREAM_FRAME_RATE 30

#define RTSP_CALLBACK(uri, resultCode, resultString) \
static void continueAfter ## uri(RTSPClient* rtspClient, int resultCode, char* resultString) { static_cast<RTSPConnection::RTSPClientConnection*>(rtspClient)->continueAfter ## uri(resultCode, resultString); } \
void continueAfter ## uri (int resultCode, char* resultString); \
/**/

#define TASK_CALLBACK(class,task) \
TaskToken m_ ## task ## Task; \
static void Task ## task(void* rtspClient) { static_cast<class*>(rtspClient)->Task ## task(); } \
void Task ## task (); \
/**/


#if LIVEMEDIA_LIBRARY_VERSION_INT > 1371168000 
	#define RTSPClientConstrutor(env, url, verbosity, appname, httpTunnelPort) RTSPClient(env, url, verbosity, appname, httpTunnelPort ,-1)
#else					
	#define RTSPClientConstrutor(env, url, verbosity, appname, httpTunnelPort) RTSPClient(env, url, verbosity, appname, httpTunnelPort)
#endif

/* ---------------------------------------------------------------------------
**  RTSP client connection interface
** -------------------------------------------------------------------------*/
class RTSPConnection 
{
	public:
		enum {
			RTPUDPUNICAST,
			RTPUDPMULTICAST,
			RTPOVERTCP,
			RTPOVERHTTP
		};
	
		static int decodeTimeoutOption(const std::map<std::string,std::string, std::less<>> & opts) {
			int timeout = 10;
			if (opts.find("timeout") != opts.end()) 
			{
				std::string timeoutString = opts.at("timeout");
				timeout = stringToInt(timeoutString, 10);
			}
			return timeout;
		}

		static double parseFrameRate(const std::map<std::string,std::string, std::less<>> & opts) {
			double framerate = 0;
			if (opts.find("framerate") != opts.end()) 
			{
				std::string fpsString = opts.at("framerate");
				framerate = stringToDouble(fpsString, 0);
				if (framerate <= 0)
				{
					framerate = DEFAULT_STREAM_FRAME_RATE;
				}
			}
			return framerate;
		}

		static bool parseQoSMode(const std::map<std::string,std::string, std::less<>> & opts)
		{
			if (opts.find("qosMode") != opts.end())
			{
				std::string qosMode = opts.at("qosMode");
				if (qosMode == "true")
					return true;
			}
			return false;
		}

		static int decodeRTPTransport(const std::map<std::string,std::string, std::less<>> & opts) 
		{
			int rtptransport = RTSPConnection::RTPUDPUNICAST;
			if (opts.find("rtptransport") != opts.end()) 
			{
				std::string rtpTransportString = opts.at("rtptransport");
				if (rtpTransportString == "tcp") {
					rtptransport = RTSPConnection::RTPOVERTCP;
				} else if (rtpTransportString == "http") {
					rtptransport = RTSPConnection::RTPOVERHTTP;
				} else if (rtpTransportString == "multicast") {
					rtptransport = RTSPConnection::RTPUDPMULTICAST;
				}
			}
			return rtptransport;
		}	

		void controlStreamRtspConnection(uint64_t* resume_time_in_epoch, const std::string& action, const std::string& seek_value)
		{
			if (m_rtspClient)
			{
				m_rtspClient->doPauseResume(resume_time_in_epoch, action, seek_value);
			}
		}

		string getPlaybackState()
		{
			if (m_rtspClient)
			{
				return m_rtspClient->getPlaybackState();
			}
			return "NOT_PLAYING";
		}

		/* ---------------------------------------------------------------------------
		**  RTSP client callback interface
		** -------------------------------------------------------------------------*/
		class Callback : public SessionCallback
		{
			public:
				virtual void    onError(RTSPConnection&, const char*)  {}
				virtual void    onConnectionTimeout(RTSPConnection&)   {}
				virtual void    onDataTimeout(RTSPConnection&)         {}
				virtual void    onPlaying(RTSPConnection &connection, MediaSession *m_session)  {}
				virtual void    onEOS(RTSPConnection&)         {}
		};
	
		/* ---------------------------------------------------------------------------
		**  RTSP client 
		** -------------------------------------------------------------------------*/
		class RTSPClientConnection : public RTSPClient
		{
			public:
				RTSPClientConnection(RTSPConnection& connection, Environment& env, Callback* callback, const char* rtspURL, int timeout, int rtptransport, double framerate, bool isQosMode, int verbosityLevel = 0, bool isfilesink = false);
				virtual ~RTSPClientConnection();

				void createOutPutFile();
				void doPauseResume(uint64_t*, const std::string&, const std::string&);
				string getPlaybackState() { return m_playbackState; }
				void sessionByeHandler(char const* reason);
			protected:
				void sendNextCommand();
						
				RTSP_CALLBACK(DESCRIBE,resultCode,resultString);
				RTSP_CALLBACK(SETUP,resultCode,resultString);
				RTSP_CALLBACK(PLAY,resultCode,resultString);
				RTSP_CALLBACK(PAUSE,resultCode,resultString);
			
				TASK_CALLBACK(RTSPConnection::RTSPClientConnection,ConnectionTimeout);
				TASK_CALLBACK(RTSPConnection::RTSPClientConnection,DataArrivalTimeout);
				TASK_CALLBACK(RTSPConnection::RTSPClientConnection,EOSArrivalTimeout);
				TASK_CALLBACK(RTSPConnection::RTSPClientConnection,HandleEOS);
			private:
				void startPlayingSession(MediaSession* session, double start, double end, float scale, RTSPClient::responseHandler* afterFunc);
				static void sessionAfterPlaying(void* client) { static_cast<RTSPConnection::RTSPClientConnection*>(client)->sessionAfterPlaying(); }
				void sessionAfterPlaying();
				static void periodicFileOutputTimerHandler(void *client) { static_cast<RTSPConnection::RTSPClientConnection*>(client)->periodicFileOutputTimerHandler(); }
				void periodicFileOutputTimerHandler();
			protected:
				RTSPConnection&          m_connection;
				int                      m_timeout;
				int                      m_rtptransport;
				double                   m_framerate;
				bool                     m_isQoSMode;
				MediaSession*            m_session;                   
				MediaSubsession*         m_subSession;
				MediaSubsessionIterator* m_subSessionIter;
				Callback*                m_callback; 	
				unsigned int             m_nbPacket;
				std::string              m_startTime;
				std::string              m_endTime;
				std::string              m_resumeTime;
				std::string              m_action;
				uint64_t*                m_resumeTimeEpoch;
				int64_t                  m_playback_speed;
				Authenticator* 		     m_authenticator;
			private:
                std::string              m_playbackState;
#ifdef QuickTimeFileSink
				QuickTimeFileSink* m_filesink;
				bool m_isFileSink;
				TaskToken m_periodicFileOutputTask;
#endif
		};
		
	public:
		RTSPConnection(Environment& env, Callback* callback, const char* rtspURL, const std::map<std::string,std::string, std::less<>> & opts, int verbosityLevel = 1);
		RTSPConnection(Environment& env, Callback* callback, const char* rtspURL, int timeout = 5, int rtptransport = RTPUDPUNICAST, int verbosityLevel = 1);
		virtual ~RTSPConnection();

		void        start(unsigned int delay = 0);
		std::string getUrl()          { return m_url; }
		int         getRtpTransport() { return m_rtptransport; }
		void closeRTSPClient()
		{
			if (m_rtspClient)
			{
				Medium::close(m_rtspClient);
				m_rtspClient = nullptr;
			}
		}

	protected:
		TASK_CALLBACK(RTSPConnection,startCallback);
	
	protected:
		Environment&             m_env;
		Callback*                m_callback; 	
		std::string              m_url;
		int                      m_timeout;
		int                      m_rtptransport;
		double                   m_framerate;
		int                      m_verbosity;
		bool m_isFileSink;
		RTSPClientConnection*    m_rtspClient;
		bool                     m_isQoSMode;
};
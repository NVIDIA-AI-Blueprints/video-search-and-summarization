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
** rtspconnectionclient.cpp
** 
** Interface to an RTSP client connection
** 
** -------------------------------------------------------------------------*/


#include "rtspconnectionclient.h"
#include <iostream>
#include "utils.h"
#include "logger.h"
#include "CivetServer.h"
#include "MultiFramedRTPSource.hh"
#include "modules_apis.h"
#include "GroupsockHelper.hh"
#include "vstmodule.h"

using namespace std;

constexpr int ADDON_FRAMES = 30;
constexpr int MAX_END_ADDON_TIME_SEC = 5;

unsigned fileSinkBufferSize = 400000;
unsigned int fileOutputInterval = 20;

RTSPConnection::RTSPConnection(Environment& env, Callback* callback, const char* rtspURL, int timeout, int rtptransport, int verbosityLevel) 
				: m_startCallbackTask(nullptr)
				, m_env(env)
				, m_callback(callback)
				, m_url(rtspURL)
				, m_timeout(timeout)
				, m_rtptransport(rtptransport)
				, m_verbosity(verbosityLevel)
				, m_rtspClient(nullptr)
				, m_isQoSMode(false)
{
	this->start();
}

RTSPConnection::RTSPConnection(Environment& env, Callback* callback, const char* rtspURL, const std::map<std::string,std::string, std::less<>> & opts, int verbosityLevel) 
				: m_startCallbackTask(nullptr)
				, m_env(env)
				, m_callback(callback)
				, m_url(rtspURL)
				, m_timeout(decodeTimeoutOption(opts))
				, m_rtptransport(decodeRTPTransport(opts))
				, m_framerate(parseFrameRate(opts))
				, m_verbosity(verbosityLevel)
				, m_rtspClient(nullptr)
				, m_isQoSMode(parseQoSMode(opts))
{
	LOG(verbose) << __func__ << endl;
	for (const auto& x : opts)
	{
		LOG(verbose) << x.first << ": " << x.second << endl;
    }
	// Setting RTP transport medium by default to UDP.
	m_rtptransport = RTSPConnection::RTPUDPUNICAST;

    /* If server domain name is defined, Then use original rtsp server
    ** address. This is because ip forwarding might not work */
	string token, mediaPath;
    if (GET_CONFIG().server_domain_name.empty() == false)
    {
		if (m_url.find(NV_STREAMER) != std::string::npos)
		{
			token = NV_STREAMER;
		}
		else if (m_url.find(VOD_STREAMER) != std::string::npos)
		{
			token = VOD_STREAMER;
		}
		else if (m_url.find(LIVE_STREAMER) != std::string::npos)
		{
			token = LIVE_STREAMER;
		}

		mediaPath = getFilePathFromUrl(m_url, token);
		if (!mediaPath.empty())
		{
			string token_1 = string("/") + token + string("/");
			string streamId = getStreamIdFromUrl(m_url, token_1);
			m_url = vst_rtsp::rtspOriginalUrlPrefix(streamId) + token + mediaPath;
		}
	}

	this->start();
}

void RTSPConnection::start(unsigned int delay)
{
	std::cout << m_startCallbackTask << std::endl;
	if (m_startCallbackTask) {
		m_env.taskScheduler().unscheduleDelayedTask(m_startCallbackTask);
	}
	m_startCallbackTask = m_env.taskScheduler().scheduleDelayedTask(delay*1000, TaskstartCallback, this);
}	

void RTSPConnection::TaskstartCallback() 
{
	if (m_rtspClient)
	{
		
		Medium::close(m_rtspClient);
		m_rtspClient = nullptr;
	}
	m_rtspClient = new RTSPClientConnection(*this, m_env, m_callback, m_url.c_str(), m_timeout, m_rtptransport, m_framerate, m_isQoSMode, m_verbosity);
}

RTSPConnection::~RTSPConnection()
{
	LOG(info) << __func__ << endl;
	m_env.taskScheduler().unscheduleDelayedTask(m_startCallbackTask);
	Medium::close(m_rtspClient);
	LOG(info) << "RTSPConnection destroyed: " << m_url << endl;
}

int getHttpTunnelPort(int  rtptransport, const char* rtspURL) 
{
	int httpTunnelPort = 0;
	if (rtptransport == RTSPConnection::RTPOVERHTTP) 
	{
		std::string url = rtspURL;
                const char * pattern = "://";
                std::size_t pos = url.find(pattern);
                if (pos != std::string::npos) {
                        url.erase(0,pos+strlen(pattern));
                }
                pos = url.find_first_of("/");
                if (pos != std::string::npos) {
                        url.erase(pos);
                }
                pos = url.find_first_of(":");
                if (pos != std::string::npos) {
                    url.erase(0,pos+1);
                    httpTunnelPort = stringToInt(url, 0);
                }
	}
	return httpTunnelPort;
}

RTSPConnection::RTSPClientConnection::RTSPClientConnection(RTSPConnection& connection, Environment& env, Callback* callback, const char* rtspURL, int timeout, int  rtptransport, double framerate, bool isQosMode, int verbosityLevel, bool isfilesink)
				: RTSPClientConstrutor(env, rtspURL, verbosityLevel, nullptr, getHttpTunnelPort(rtptransport, rtspURL))
				, m_connection(connection)
				, m_timeout(timeout)
				, m_rtptransport(rtptransport)
				, m_framerate(framerate)
				, m_isQoSMode(isQosMode)
				, m_session(nullptr)
				, m_subSessionIter(nullptr)
				, m_callback(callback)
				, m_nbPacket(0)
				, m_action("")
				, m_playback_speed(1)
				, m_authenticator(nullptr)
				, m_playbackState("NOT_PLAYING")
{
	if(GET_CONFIG().use_rtsp_authentication)
	{
		std::string passwordHash = getPasswordHash(DEFAULT_USERNAME);
		m_authenticator = new Authenticator(DEFAULT_USERNAME, passwordHash.c_str(), true);
	}
	MultiFramedRTPSource::maxReceiveBufferSize = 2 * 1024 * 1024; // 2MB of buffer in MultiFramedRTPSource Class
	// start tasks
	m_ConnectionTimeoutTask = envir().taskScheduler().scheduleDelayedTask(m_timeout*1000000, TaskConnectionTimeout, this);
	
	// parse URL for start and end time
	vector<string> values;
	vector<string> url_arr = splitString(rtspURL, "?");
	if (url_arr.size() > 1)
	{
		string params = url_arr[1];
		CivetServer::getParam(params, "startTime", m_startTime);
		CivetServer::getParam(params, "endTime",   m_endTime);
		m_resumeTime = m_startTime;
	}

	if (!m_startTime.empty())
	{
		eraseString(m_startTime, "-");
		eraseString(m_startTime, ":");
	}
	if (!m_endTime.empty())
	{
		eraseString(m_endTime, "-");
		eraseString(m_endTime, ":");
	}

	if(string(rtspURL).find(NV_STREAMER) != std::string::npos)
	{
		// In case of NvStreamer set full url which includes options seekable, loop etc.
		setBaseURL(rtspURL);
	}
	else
	{
		if (url_arr.size() > 0)
		{
			setBaseURL(url_arr[0].c_str());
		}
	}
	LOG(verbose) << "Timeout value: " << m_timeout << "\n";
	// initiate connection process
	this->sendNextCommand();
}

RTSPConnection::RTSPClientConnection::~RTSPClientConnection()
{
	LOG(info) << "Enter ~RTSPClientConnection::RTSPClientConnection : " << endl;
	envir().taskScheduler().unscheduleDelayedTask(m_ConnectionTimeoutTask);
	envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);
#ifdef QuickTimeFileSink
	if(m_periodicFileOutputTask)
	{
		envir().taskScheduler().unscheduleDelayedTask(m_periodicFileOutputTask);
	}
#endif
	delete m_subSessionIter;
	if(m_authenticator) 
	{
		delete m_authenticator;
	}
	// free subsession
	if (m_session != nullptr) 
	{
		bool someSubsessionsWereActive = false;
		MediaSubsessionIterator iter(*m_session);
		MediaSubsession* subsession;
		while ((subsession = iter.next()) != nullptr) 
		{
			if (subsession->sink) 
			{
				LOG(verbose) << "Close session: " << subsession->mediumName() << "/" << subsession->codecName() << "\n";
				Medium::close(subsession->sink);
				subsession->sink = nullptr;
			}
			someSubsessionsWereActive = true;
		}
		if (someSubsessionsWereActive)
		{
			// Send a RTSP "TEARDOWN" command, to tell the server to shutdown the stream.
			// Don't bother handling the response to the "TEARDOWN".
			this->sendTeardownCommand(*m_session, nullptr);
		}
#ifdef QuickTimeFileSink
		if (m_filesink)
		{
			Medium::close(m_filesink);
			m_filesink = nullptr;
		}
#endif
		if (m_session)
		{
			Medium::close(m_session);
			m_session = nullptr;
		}
	}
	LOG(info) << "Exit ~RTSPClientConnection::RTSPClientConnection : " << endl;
}

std::string getUpdatedTime(string time, int framerate)
{
	string updated_time;
	int addon_time;
	time_t epochTime = getEpocTimeInMS(time, false);
	/* Process updated end time */
	if (framerate)
	{
		addon_time = ADDON_FRAMES/framerate;
		if(addon_time > MAX_END_ADDON_TIME_SEC)
		{
			addon_time = MAX_END_ADDON_TIME_SEC;
		}
		epochTime = (epochTime + addon_time * 1000) * 1000;
	}
	updated_time = convertEpocToISO8601(epochTime);
	return updated_time;
}
void RTSPConnection::RTSPClientConnection::sendNextCommand() 
{
	if (m_subSessionIter == nullptr)
	{
		// no SDP, send DESCRIBE
		LOG(info) << "[CLIENT] Sending Describe command" << endl;
		this->sendDescribeCommand(continueAfterDESCRIBE, m_authenticator); 
	}
	else
	{
		m_subSession = m_subSessionIter->next();
		if (m_subSession != nullptr) 
		{
			// still subsession to SETUP
			if (!m_subSession->initiate()) 
			{
				LOG(error) << "Failed to initiate " << m_subSession->mediumName() << "/" << m_subSession->codecName() << " subsession: " << envir().getResultMsg() << "\n";
				this->sendNextCommand();
			} 
			else 
			{
				if (m_subSession->rtpSource() != nullptr)
				{
					int socketNum = m_subSession->rtpSource()->RTPgs()->socketNum();
					unsigned newBufferSize = GET_CONFIG().rx_socket_buffer_size;
					setReceiveBufferTo(envir(), socketNum, newBufferSize);
					LOG(info) << "Socket reciever buffer size = " << getReceiveBufferSize(envir(), socketNum) << endl;
				}
				if (fVerbosityLevel > 1) 
				{				
					envir() << "Initiated " << m_subSession->mediumName() << "/" << m_subSession->codecName() << " subsession" << "\n";
				}
				LOG(info) << "[CLIENT] Sending Setup command" << endl;
				this->sendSetupCommand(*m_subSession, continueAfterSETUP, false, (m_rtptransport == RTPOVERTCP), (m_rtptransport == RTPUDPMULTICAST), m_authenticator);
			}
		}
		else
		{
			std::string updated_endTime;
			char *absEndTime = nullptr;
			if (!m_endTime.empty())
			{
				m_endTime = getUpdatedTime (m_endTime, m_framerate);
				absEndTime = (char*)m_endTime.c_str();
				LOG(info) << "Updated End Time:" << updated_endTime << endl;
			}
			// no more subsession to SETUP, send PLAY
			LOG(info) << "[CLIENT] Sending Play command" << endl;
			if (m_session != nullptr)
			{
				if (!m_startTime.empty())
				{
					LOG(info) << "sendPlayCommand: m_startTime: " << m_startTime << ", m_endTime: " << m_endTime << endl;
					this->sendPlayCommand(*m_session, continueAfterPLAY, m_startTime.c_str(), absEndTime, 1.0, m_authenticator);
				}
				else
				{
					this->sendPlayCommand(*m_session, continueAfterPLAY);
				}
			}
		}
	}
}

void RTSPConnection::RTSPClientConnection::doPauseResume(uint64_t* resume_time_in_epoch, const std::string& action, const std::string& seek_value)
{
	if (m_session == nullptr)
	{
		LOG(error) << "m_session is null, ignoring the control command" << endl;
		return;
	}
	m_action = action;
	if (action == "pause")
	{
		this->sendPauseCommand(*m_session, continueAfterPAUSE);
		m_resumeTimeEpoch = resume_time_in_epoch;
	}
	else if (action == "resume")
	{
		if (m_playback_speed != 1)
		{
			this->sendPauseCommand(*m_session, continueAfterPAUSE, m_authenticator);
			m_resumeTimeEpoch = resume_time_in_epoch;
			m_playback_speed = 1;
		}
		else
		{
			this->sendPlayCommand(*m_session, continueAfterPLAY, -1, -1, 1.0, m_authenticator);
		}
	}
	else if (action == "seek_forward")
	{
		envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);
		m_resumeTimeEpoch = resume_time_in_epoch;
		m_resumeTime = convertEpocToISO8601(*m_resumeTimeEpoch + 10000000);
		LOG(info) << "Seek 10 secs forward to time: " << m_resumeTime << endl;
		this->sendPlayCommand(*m_session, continueAfterPLAY, m_resumeTime.c_str(), m_endTime.c_str(), m_playback_speed, m_authenticator);
	}
	else if (action == "seek_backward")
	{
		envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);
		m_resumeTimeEpoch = resume_time_in_epoch;
		m_resumeTime = convertEpocToISO8601(*m_resumeTimeEpoch - 10000000);
		LOG(info) << "Seek 10 secs backward to time: " << m_resumeTime << endl;
		this->sendPlayCommand(*m_session, continueAfterPLAY, m_resumeTime.c_str(), m_endTime.c_str(), m_playback_speed, m_authenticator);
	}
	else if (action == "rewind" || action == "fast_forward")
	{
		m_playback_speed = stringToInt(seek_value, 1);
		m_playback_speed = (action=="rewind") ? (-m_playback_speed) : m_playback_speed;
		this->sendPauseCommand(*m_session, continueAfterPAUSE, m_authenticator);
		m_resumeTimeEpoch = resume_time_in_epoch;
	}
	else
	{
		LOG(error) << "stream control " << action << " not supported\n";
	}
}

void RTSPConnection::RTSPClientConnection::continueAfterDESCRIBE(int resultCode, char* resultString)
{
	LOG(info) << "[CLIENT] Received DESCRIBE event, result:" << resultCode << endl;
	if (resultCode != 0) 
	{
		LOG(error) << "Failed to DESCRIBE: " << resultString << "\n";
		m_callback->onError(m_connection, resultString);
	}
	else
	{
		if (fVerbosityLevel > 1) 
		{
			LOG(verbose) << "Got SDP:\n" << resultString << "\n";
		}
		m_session = MediaSession::createNew(envir(), resultString);
		if (m_session)
		{
			m_subSessionIter = new MediaSubsessionIterator(*m_session);
			this->sendNextCommand();
		}
		else
		{
			if (fVerbosityLevel > 1)
			{
				LOG(verbose) << "MediaSession::createNew() failed! (result string: " << resultString << ")\n";
			}
			m_callback->onError(m_connection, "MediaSession::createNew() failed!");
		} 
	}
	delete[] resultString;
}

void subsessionByeHandlerCb(void* clientData, char const* reason)
{
	static_cast<RTSPConnection::RTSPClientConnection*>(clientData)->sessionByeHandler(reason);
}

void RTSPConnection::RTSPClientConnection::sessionByeHandler(char const* reason)
{
  LOG(info) << ":Received RTCP \"BYE\"" << endl;
  if (reason != nullptr) {
    LOG(info) << " (reason:\"" << reason << "\")";
    delete[] (char*)reason;
  }

  envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);
  m_playbackState = "NOT_PLAYING";
  m_callback->onEOS(m_connection);
}

void RTSPConnection::RTSPClientConnection::continueAfterSETUP(int resultCode, char* resultString)
{
	LOG(info) << "[CLIENT] Received SETUP event, result:" << resultCode << endl;
	if (resultCode != 0) 
	{
		LOG(error) << "Failed to SETUP: " << resultString << "\n";
		m_callback->onError(m_connection, resultString);
	}
	else
	{
		{
			MediaSink* sink = SessionSink::createNew(envir(), m_callback, m_isQoSMode);
			if (!m_startTime.empty())
			{
				static_cast<SessionSink*>(sink)->sessionSinkTS = m_startTime;
			}

			if (sink == nullptr)
			{
				LOG(error) << "Failed to create sink for \"" << m_subSession->mediumName() << "/" << m_subSession->codecName() << "\" subsession error: " << envir().getResultMsg() << "\n";
				m_callback->onError(m_connection, envir().getResultMsg());
			} 
			else if (m_callback->onNewSession(sink->name(), m_subSession->mediumName(), m_subSession->codecName(), m_subSession->savedSDPLines()))
			{
				LOG(info) << "Start playing sink for \"" << m_subSession->mediumName() << "/" << m_subSession->codecName() << "\" subsession" << "\n";
				m_subSession->sink = sink;
				m_subSession->sink->startPlaying(*(m_subSession->readSource()), nullptr, nullptr);

				// Set a handler to be called if a RTCP "BYE" arrives for this subsession:
				if (m_subSession->rtcpInstance() != nullptr) {
				    m_subSession->rtcpInstance()->setByeWithReasonHandler(subsessionByeHandlerCb, this);
				}
			}
			else 
			{
				Medium::close(sink);
			}
		}
	}
	delete[] resultString;
	this->sendNextCommand();  
}	

void RTSPConnection::RTSPClientConnection::continueAfterPLAY(int resultCode, char* resultString)
{
	LOG(info) << "[CLIENT] Received PLAY event, result:" << resultCode << endl;
	if (resultCode != 0) 
	{
		LOG(error) << "Failed to PLAY: " << resultString << "\n";
		m_callback->onError(m_connection, resultString);
	}
	else
	{
		m_playbackState = "PLAYING";
		if (fVerbosityLevel > 1) 
		{
			LOG(verbose) << "PLAY OK" << "\n";
		}
		if (m_isQoSMode && m_callback)
		{
			m_callback->onPlaying(m_connection, m_session);
		}
		LOG(verbose) << "StartTime: " << m_startTime << "\tEndTime: " << m_endTime <<  "\n";
		double duration = 0.0;
		if (m_playback_speed >= 1)
		{
			duration = getDuration(m_resumeTime, m_endTime);
		}
		else if (m_playback_speed < 0)		// Rewind
		{
			duration = getDuration(m_startTime, m_resumeTime);
		}
		double durationSlop = 0.0;
		int scale = m_playback_speed;
		double secondsToDelay = duration;
		if (duration > 0.0)
		{
		    double absScale = scale > 0 ? scale : -scale; // ASSERT: scale != 0
		    secondsToDelay = duration/absScale + durationSlop;

		    int64_t uSecsToDelay = (int64_t)(secondsToDelay*1000000.0);
			LOG(info) << "Playback duration: " << uSecsToDelay << "\n";
		}
		m_DataArrivalTimeoutTask = envir().taskScheduler().scheduleDelayedTask(m_timeout*1000000, TaskDataArrivalTimeout, this);
	}
	envir().taskScheduler().unscheduleDelayedTask(m_ConnectionTimeoutTask);
	delete[] resultString;
}

void RTSPConnection::RTSPClientConnection::continueAfterPAUSE(int resultCode, char* resultString)
{
	if (resultCode != 0)
	{
		LOG(error) << "Failed to PAUSE: " << resultCode << "\n";
		return;
	}
	envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);

	m_playbackState = "PAUSED";

	m_resumeTime = convertEpocToISO8601(*m_resumeTimeEpoch);

	if (m_session)
	{
		if (m_action == "rewind" || m_action == "fast_forward")
		{
			LOG(info) << m_action << " at speed " << m_playback_speed << endl;
			this->sendPlayCommand(*m_session, continueAfterPLAY, m_resumeTime.c_str(), m_endTime.c_str(), m_playback_speed, m_authenticator);
		}
		else if (m_action == "resume")
		{
			this->sendPlayCommand(*m_session, continueAfterPLAY, -1, -1, 1.0, m_authenticator);
		}
		else
		{
			LOG(info) << "Stream Paused" << endl;
		}
	}
}

void RTSPConnection::RTSPClientConnection::TaskConnectionTimeout()
{
	m_callback->onConnectionTimeout(m_connection);
}

void RTSPConnection::RTSPClientConnection::TaskDataArrivalTimeout()
{
	LOG(verbose2) << "TaskDataArrivalTimeout" << endl;
	unsigned int newTotNumPacketsReceived = 0;
	if (m_session)
	{
		MediaSubsessionIterator iter(*m_session);
		MediaSubsession* subsession;
		while ((subsession = iter.next()) != nullptr) 
		{
			RTPSource* src = subsession->rtpSource();
			if (src != nullptr) 
			{
				newTotNumPacketsReceived += src->receptionStatsDB().totNumPacketsReceived();
			}
		}
		
		if (newTotNumPacketsReceived == m_nbPacket) 
		{
			m_callback->onDataTimeout(m_connection);
		} 
		else 
		{
			m_nbPacket = newTotNumPacketsReceived;
			m_DataArrivalTimeoutTask = envir().taskScheduler().scheduleDelayedTask(m_timeout*1000000, TaskDataArrivalTimeout, this);
		}
	}
}

#ifdef QuickTimeFileSink
void RTSPConnection::RTSPClientConnection::periodicFileOutputTimerHandler()
{
   // First, close the existing output files:
   if (m_filesink)
   {
	    cout << getCurrentTime() << endl;
		Medium::close(m_filesink);
		m_filesink = nullptr;
   }
   if (m_session == nullptr)
   {return;
   }
   MediaSubsessionIterator iter(*m_session);
   MediaSubsession* subsession;
   while ((subsession = iter.next()) != nullptr)
   {
		Medium::close(subsession->sink);
		subsession->sink = nullptr;
   }

	// Then, create new output files:
	createOutPutFile();
}

void RTSPConnection::RTSPClientConnection::createOutPutFile()
{
	if (m_session == nullptr)
	{
		LOG(error) << "Media Session is null" << endl;
		return;
	}
	std::map<std::string,std::string, std::less<>> opts = m_connection.getOpts();
	string root_video = "./";
	if (opts.find("recorded_video_root") != opts.end()) 
	{
		root_video = opts.at("recorded_video_root");
	}
	string cameraId = "";
	if (opts.find("cameraid") != opts.end()) 
	{
		cameraId = opts.at("cameraid");
		if (createDir(root_video + "/" + cameraId) == -1)
		{
			return;
		}
	}
	string extension = "out";
	if (opts.find("container") != opts.end()) 
	{
		extension = opts.at("container");
	}
	string outFileName = root_video + "/" + cameraId + "/" + getCurrentTime() + "." + extension;
    m_filesink = QuickTimeFileSink::createNew(envir(), *m_session, outFileName.c_str(),
					   fileSinkBufferSize,
					   1920, 1080,
					   30,
					   false,
					   false,
					   false,
					   true);
    if (m_filesink == nullptr) 
    {
        LOG(error) << "FileSink creattion failed: " << envir().getResultMsg() << endl;
		return;
    }
       //shutdown();
    else
    {
        LOG(info) << "Outputting to the file: \"" << outFileName << "\"\n";
    }

    m_filesink->startPlaying(sessionAfterPlaying, this);

	// Schedule an event for writing the next output file:
  	m_periodicFileOutputTask = envir().taskScheduler().scheduleDelayedTask(fileOutputInterval*1000000,
					       (TaskFunc*)periodicFileOutputTimerHandler,
					       (void*)this);
}

void RTSPConnection::RTSPClientConnection::sessionAfterPlaying() {
     // We've been asked to play the stream(s) over again.
     // First, reset state from the current session:
     // Keep this running:      env->taskScheduler().unscheduleDelayedTask(periodicFileOutputTask);
     //envir().taskScheduler().unscheduleDelayedTask(m_ConnectionTimeoutTask);
	 //envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalTimeoutTask);
     startPlayingSession(m_session, 0, 10, 1, continueAfterPLAY);
}

void RTSPConnection::RTSPClientConnection::startPlayingSession(MediaSession* session, double start, double end, float scale, RTSPClient::responseHandler* afterFunc) {
  	this->sendPlayCommand(*session, afterFunc, start, end, scale);
}
#endif

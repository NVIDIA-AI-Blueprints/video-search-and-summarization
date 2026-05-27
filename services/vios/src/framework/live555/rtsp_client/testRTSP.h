/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#pragma once

#include "environment.h"
#include "liveMedia.hh"
#include "BasicUsageEnvironment.hh"

#include <atomic>

class videoClient;
class ourRTSPClient;
class DummySink: public MediaSink
{
public:
  static DummySink* createNew(UsageEnvironment& env,
			      MediaSubsession& subsession, // identifies the kind of data that's being received
                  ourRTSPClient* client,
			      char const* streamId = nullptr); // identifies the stream itself (optional)

private:
  DummySink(UsageEnvironment& env, MediaSubsession& subsession, ourRTSPClient* client, char const* streamId);
    // called only by "createNew()"
  virtual ~DummySink();

  static void afterGettingFrame(void* clientData, unsigned frameSize,
                                unsigned numTruncatedBytes,
				struct timeval presentationTime,
                                unsigned durationInMicroseconds);
  void afterGettingFrame(unsigned frameSize, unsigned numTruncatedBytes,
			 struct timeval presentationTime, unsigned durationInMicroseconds);

private:
  // redefined virtual functions:
  virtual Boolean continuePlaying();

private:
  u_int8_t* fReceiveBuffer;
  MediaSubsession& fSubsession;
  char* fStreamId;
  ourRTSPClient* m_client;
};

// Define a class to hold per-stream state that we maintain throughout each stream's lifetime:
class StreamClientState
{
public:
    StreamClientState();
    virtual ~StreamClientState();

public:
    MediaSubsessionIterator* iter;
    MediaSession* session;
    MediaSubsession* subsession;
    TaskToken streamTimerTask;
    double duration;
};

class ourRTSPClient: public RTSPClient
{
public:
    static ourRTSPClient* createNew(UsageEnvironment& env, char const* rtspURL,
				  int verbosityLevel = 0,
				  char const* applicationName = nullptr,
				  portNumBits tunnelOverHTTPPortNum = 0);

    static ourRTSPClient* createNew(Environment& env, char const* rtspURL);

    ourRTSPClient(UsageEnvironment& env, char const* rtspURL,
      int verbosityLevel, char const* applicationName, portNumBits tunnelOverHTTPPortNum);

    ourRTSPClient(Environment& env, char const* rtspURL);

      // called only by createNew();
    virtual ~ourRTSPClient();
    void shutDown();
    bool isDataArrived()
    {
        return m_dataArrived;
    }
  std::vector<std::string> getMediaFromSDP (char const* rtspURL);

    string dev_name;
    StreamClientState scs;
    unsigned m_describeState;
    std::atomic<bool> m_playState {false}, m_pauseState {false}, m_options {false};
    std::atomic<bool> m_dataArrived {false};
    std::atomic<bool> m_enableSR{false}, m_receivedSR {false};
    std::atomic<bool> m_timestampInfo {false}, m_fpsInfo {false};
    videoClient *m_client;
    struct timeval m_prevPtsTime = {};
    unsigned long m_frameCount, m_count_duplicate_ts, m_count_above_threshold;
    float m_framerate;
    std::string m_sdp;
    TaskToken m_qosPeriodicTask;
    uint64_t m_prev_frame_pts = 0;
    unsigned long m_sumDiff;
    std::mutex m_sdpMonitorMutex;
    std::condition_variable m_sdpMonitorCv;
};

class videoClient
{
public:
    static videoClient* createNew(char const* rtspURL)
    {
        return new videoClient(rtspURL);
    }
    void Start()
    {
        LOG(info) << "videoClient::start" << endl;
        m_capturethread = std::thread(&videoClient::CaptureThread, this);
    }

    void Stop()
    {
        m_env.stop();
        if (m_capturethread.joinable())
        {
            m_capturethread.join();
        }
        LOG(info) << "videoClient::stop : done" << endl;
    }

    void CaptureThread()
    {
        m_env.mainloop();
    }

    videoClient(char const* rtspURL)
      : m_env(m_stop)
    {
        LOG(info) << "videoClient" << endl;
        m_ourRtspClient = ourRTSPClient::createNew(m_env, rtspURL);
        m_ourRtspClient->m_client = this;
        this->Start();
    }

    ~videoClient()
    {
        try {
            LOG(info) << "~videoClient" << endl;
            this->Stop();
            if (m_ourRtspClient)
            {
                m_ourRtspClient->shutDown();
                m_ourRtspClient = nullptr;
            }
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~videoClient: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~videoClient" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    ourRTSPClient * getRtspClient() { return m_ourRtspClient; }
    void setRtspClient(ourRTSPClient *rtspClient) { m_ourRtspClient = rtspClient; }

private:
    std::thread m_capturethread;
    char m_stop = 0;
    Environment m_env;
    ourRTSPClient *m_ourRtspClient;
};

int testRtspUrl(const char* url, std::string& codec, std::string& frame_rate, std::string& width, std::string& height,
                const std::string& username = "", const std::string& password = "");
void parseSdpDescription(RTSPClient* rtspClient, char* resultString);
void continueAfterDESCRIBE(RTSPClient* rtspClient, int resultCode, char* resultString);
void continueAfterPLAY(RTSPClient* rtspClient, int resultCode, char* resultString);
void continueAfterPAUSE(RTSPClient* rtspClient, int resultCode, char* resultString);
void continueAfterOPTIONS(RTSPClient* rtspClient, int resultCode, char* resultString);
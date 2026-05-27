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

#include "logger.h"
#include "utils.h"
#include <mutex>
#include "testRTSP.h"
#include "network_utils.h"

#define MAX_OUT_PACKET_BUFFER_SIZE_IN_MB 2 * 1024 * 1024

// Forward function definitions:

// RTSP 'response handlers':
void continueAfterDESCRIBE(RTSPClient* rtspClient, int resultCode, char* resultString);
void continueAfterSETUP(RTSPClient* rtspClient, int resultCode, char* resultString);
void continueAfterPLAY(RTSPClient* rtspClient, int resultCode, char* resultString);

// Other event handler functions:
void subsessionAfterPlaying(void* clientData); // called when a stream's subsession (e.g., audio or video substream) ends
void subsessionByeHandler(void* clientData, char const* reason);
  // called when a RTCP "BYE" is received for a subsession
void subsessionSRHandler(void* clientData);
void streamTimerHandler(void* clientData);
  // called at the end of a stream's expected duration (if the stream has not already signaled its end using a RTCP "BYE")

// The main streaming routine (for each "rtsp://" URL):
void openURL(UsageEnvironment& env, char const* rtspURL);

// Used to iterate through each stream's 'subsessions', setting up each one:
void setupNextSubsession(RTSPClient* rtspClient);

// Used to shut down and close a stream (including its "RTSPClient" object):
void shutdownStream(RTSPClient* rtspClient, int exitCode = 1);

// A function that outputs a string that identifies each stream (for debugging output).  Modify this if you wish:
UsageEnvironment& operator<<(UsageEnvironment& env, const RTSPClient& rtspClient) {
  return env << "[URL:\"" << rtspClient.url() << "\"]: ";
}

// A function that outputs a string that identifies each subsession (for debugging output).  Modify this if you wish:
UsageEnvironment& operator<<(UsageEnvironment& env, const MediaSubsession& subsession) {
  return env << subsession.mediumName() << "/" << subsession.codecName();
}

void usage(UsageEnvironment& env, char const* progName) {
  env << "Usage: " << progName << " <rtsp-url-1> ... <rtsp-url-N>\n";
  env << "\t(where each <rtsp-url-i> is a \"rtsp://\" URL)\n";
}

std::mutex g_lock;
static int result = 0;
static std::string video_format = "";
static std::string video_framerate = "";
static std::string video_width = "";
static std::string video_height = "";
static unsigned payloadFormat;

int testRtspUrl(const char* url, std::string& codec, std::string& frame_rate, std::string& width, std::string& height,
                const std::string& username, const std::string& password)
{
    std::lock_guard<std::mutex> lck (g_lock);
    video_format = "";
    video_framerate = "";
    video_width = "";
    video_height = "";
    result = 0;

    if (isRtspServerReachable(string(url)) == false)
    {
        LOG(error) << "RTSP server not reachable" << endl;
        return -1;
    }
    videoClient *client = videoClient::createNew(url);
    if (client == nullptr) return false;

    ourRTSPClient *rtsp_client = client->getRtspClient();
    if (rtsp_client == nullptr) return false;

    // Build a live555 Authenticator when credentials are supplied. It must
    // outlive the DESCRIBE response, which is guaranteed because we wait
    // (with timeout) for m_describeState below before returning.
    std::unique_ptr<Authenticator> authenticator;
    if (username.empty() == false)
    {
        authenticator = std::make_unique<Authenticator>(username.c_str(), password.c_str(), False);
    }

    rtsp_client->m_describeState = false;
    rtsp_client->sendDescribeCommand(
        +[](RTSPClient* rtspClient, int resultCode, char* resultString)
    {
        LOG(error) << "Describe result:" << resultCode << endl;
        if (rtspClient == nullptr)
        {
            return;
        }
        if (resultCode == 0)
        {
            parseSdpDescription(rtspClient, resultString);
        }
        std::lock_guard<std::mutex> sdp_lock (((ourRTSPClient*)rtspClient)->m_sdpMonitorMutex);
        ((ourRTSPClient*)rtspClient)->m_describeState = true;
        ((ourRTSPClient*)rtspClient)->m_sdpMonitorCv.notify_all();
        result = resultCode;
    }, authenticator.get());

    /* Wait for describe response or till timeout */
    {
        std::unique_lock<std::mutex> sdp_lock(rtsp_client->m_sdpMonitorMutex);
        if (rtsp_client->m_describeState == false)
        {
            auto until = std::chrono::system_clock::now() + 2s;
            rtsp_client->m_sdpMonitorCv.wait_until(sdp_lock, until, [&]() { return rtsp_client->m_describeState; });
        }
    }
    delete client;

    codec = video_format;
    frame_rate = video_framerate;
    width = video_width;
    height = video_height;
    LOG(info) << "Result: " << result << endl;
    LOG(info) << "testRtspUrl video_format: " << video_format << " frame rate: " << video_framerate
              << " video size:  " << video_width << "x" << video_height << endl;
    return result;
}

// Define a data sink (a subclass of "MediaSink") to receive the data for each subsession (i.e., each audio or video 'substream').
// In practice, this might be a class (or a chain of classes) that decodes and then renders the incoming audio or video.
// Or it might be a "FileSink", for outputting the received data into a file (as is done by the "openRTSP" application).
// In this example code, however, we define a simple 'dummy' sink that receives incoming data, but does nothing with it.

#define RTSP_CLIENT_VERBOSITY_LEVEL 0 // by default, print verbose output from each "RTSPClient"

void openURL(UsageEnvironment& env, char const* rtspURL)
{
  // Begin by creating a "RTSPClient" object.  Note that there is a separate "RTSPClient" object for each stream that we wish
  // to receive (even if more than stream uses the same "rtsp://" URL).
  char const* progName = "vms";
  RTSPClient* rtspClient = ourRTSPClient::createNew(env, rtspURL, RTSP_CLIENT_VERBOSITY_LEVEL, progName);
  if (rtspClient == nullptr)
  {
    LOG(error) << "Failed to create a RTSP client for URL \"" << rtspURL << "\": " << env.getResultMsg() << "\n";
    return;
  }

  // Next, send a RTSP "DESCRIBE" command, to get a SDP description for the stream.
  // Note that this command - like all RTSP commands - is sent asynchronously; we do not block, waiting for a response.
  // Instead, the following function call returns immediately, and we handle the RTSP response later, from within the event loop:
  rtspClient->sendDescribeCommand(continueAfterDESCRIBE); 
}

// Implementation of the RTSP 'response handlers':

void parseSdpDescription(RTSPClient* rtspClient, char* resultString)
{
    ourRTSPClient *ourRtspClient = (ourRTSPClient*)rtspClient;
    vector<std::string> arr = splitString(std::string(resultString), "\n");
    for (unsigned int i = 0; i < arr.size(); i ++)
    {
      std::string line = arr[i];
      if (line.find("m=video") != std::string::npos)
      {
            char mediumName [32]; // ensures we have enough space
            unsigned short clientPortNum;
            payloadFormat = 0;

            if ((sscanf(line.c_str(), "m=%s %hu RTP/AVP %u",
                  mediumName, &clientPortNum, &payloadFormat) == 3 ||
                  sscanf(line.c_str(), "m=%s %hu/%*u RTP/AVP %u",
                  mediumName, &clientPortNum, &payloadFormat) == 3)
                  && payloadFormat <= 127)
              {
              }
              else if ((sscanf(line.c_str(), "m=%s %hu RTP/SAVP %u",
                      mediumName, &clientPortNum, &payloadFormat) == 3 ||
                      sscanf(line.c_str(), "m=%s %hu/%*u RTP/SAVP %u",
                      mediumName, &clientPortNum, &payloadFormat) == 3)
                      && payloadFormat <= 127)
              {
              }
              else if ((sscanf(line.c_str(), "m=%s %hu UDP %u",
                        mediumName, &clientPortNum, &payloadFormat) == 3 ||
                        sscanf(line.c_str(), "m=%s %hu udp %u",
                        mediumName, &clientPortNum, &payloadFormat) == 3 ||
                        sscanf(line.c_str(), "m=%s %hu RAW/RAW/UDP %u",
                        mediumName, &clientPortNum, &payloadFormat) == 3)
                        && payloadFormat <= 127)
              {
              }
        }
        else if (line.find("a=rtpmap") != std::string::npos)
        {
            unsigned rtpmapPayloadFormat;
            unsigned rtpTimestampFrequency = 0;
            unsigned numChannels = 1;
            char codecName[32] = "";
            if (sscanf(line.c_str(), "a=rtpmap: %u %[^/]/%u/%u",
                &rtpmapPayloadFormat, codecName, &rtpTimestampFrequency,
                &numChannels) == 4
                || sscanf(line.c_str(), "a=rtpmap: %u %[^/]/%u",
                &rtpmapPayloadFormat, codecName, &rtpTimestampFrequency) == 3
                || sscanf(line.c_str(), "a=rtpmap: %u %s",
            &rtpmapPayloadFormat, codecName) == 2)
            {
                if (payloadFormat == rtpmapPayloadFormat)
                {
                    video_format = codecName;
                }
            }
        }
        else if (line.find("framerate") != std::string::npos)
        {
            float frate;
            int rate;
            if (sscanf(line.c_str(), "a=framerate: %f", &frate) == 1 || sscanf(line.c_str(), "a=framerate:%f", &frate) == 1)
            {
              ourRtspClient->m_framerate = frate;
              video_framerate = std::to_string(frate);
            }
            else if (sscanf(line.c_str(), "a=x-framerate: %d", &rate) == 1)
            {
              ourRtspClient->m_framerate = rate;
              video_framerate = std::to_string(rate);
            }
        }
        else if (line.find("framesize") != std::string::npos)
        {
            unsigned rtpmapPayloadFormat;
            unsigned w, h;
            if (sscanf(line.c_str(), "a=framesize: %u %d-%d",&rtpmapPayloadFormat, &w, &h ) == 3)
            {
                video_width = std::to_string(w);
                video_height = std::to_string(h);
            }
        }
    }
    LOG(error) << " video_format: " << video_format << ", frame rate: " << video_framerate
              << ", video size:  " << video_width << "x" << video_height << endl;
}

void continueAfterOPTIONS(RTSPClient* rtspClient, int resultCode, char* resultString) {
  if (resultCode == 0) {
    LOG(error) << "Supported Options: " << resultString << endl;
    ((ourRTSPClient*)rtspClient)->m_options = true;
  }
  delete[] resultString;
}

void continueAfterDESCRIBE(RTSPClient* rtspClient, int resultCode, char* resultString) {
  do {
    UsageEnvironment& env = rtspClient->envir(); // alias
    StreamClientState& scs = ((ourRTSPClient*)rtspClient)->scs; // alias

    if (resultCode != 0) {
      env << *rtspClient << "Failed to get a SDP description: " << resultString << "\n";
      delete[] resultString;
      break;
    }

    ((ourRTSPClient*)rtspClient)->m_describeState = 1;
    parseSdpDescription(rtspClient, resultString);

    char* const sdpDescription = resultString;
    env << *rtspClient << "Got a SDP description:\n" << sdpDescription << "\n";

    // Create a media session object from this SDP description:
    scs.session = MediaSession::createNew(env, sdpDescription);
    delete[] sdpDescription; // because we don't need it anymore
    if (scs.session == nullptr) {
      env << *rtspClient << "Failed to create a MediaSession object from the SDP description: " << env.getResultMsg() << "\n";
      break;
    } else if (!scs.session->hasSubsessions()) {
      env << *rtspClient << "This session has no media subsessions (i.e., no \"m=\" lines)\n";
      break;
    }

    // Then, create and set up our data source objects for the session.  We do this by iterating over the session's 'subsessions',
    // calling "MediaSubsession::initiate()", and then sending a RTSP "SETUP" command, on each one.
    // (Each 'subsession' will have its own data source.)
    scs.iter = new MediaSubsessionIterator(*scs.session);
    setupNextSubsession(rtspClient);
    return;
  } while (0);

  // An unrecoverable error occurred with this stream.
  shutdownStream(rtspClient);
}

// By default, we request that the server stream its data using RTP/UDP.
// If, instead, you want to request that the server stream via RTP-over-TCP, change the following to True:
#define REQUEST_STREAMING_OVER_TCP False

void setupNextSubsession(RTSPClient* rtspClient) {
  UsageEnvironment& env = rtspClient->envir(); // alias
  StreamClientState& scs = ((ourRTSPClient*)rtspClient)->scs; // alias
  
  scs.subsession = scs.iter->next();
  if (scs.subsession != nullptr) {
    if (!scs.subsession->initiate()) {
      env << *rtspClient << "Failed to initiate the \"" << *scs.subsession << "\" subsession: " << env.getResultMsg() << "\n";
      setupNextSubsession(rtspClient); // give up on this subsession; go to the next one
    } else {
      env << *rtspClient << "Initiated the \"" << *scs.subsession << "\" subsession (";
      if (scs.subsession->rtcpIsMuxed()) {
	env << "client port " << scs.subsession->clientPortNum();
      } else {
	env << "client ports " << scs.subsession->clientPortNum() << "-" << scs.subsession->clientPortNum()+1;
      }
      env << ")\n";

      // Continue setting up this subsession, by sending a RTSP "SETUP" command:
      rtspClient->sendSetupCommand(*scs.subsession, continueAfterSETUP, False, REQUEST_STREAMING_OVER_TCP);
    }
    return;
  }

  // We've finished setting up all of the subsessions.  Now, send a RTSP "PLAY" command to start the streaming:
  if (scs.session->absStartTime() != nullptr) {
    // Special case: The stream is indexed by 'absolute' time, so send an appropriate "PLAY" command:
    rtspClient->sendPlayCommand(*scs.session, continueAfterPLAY, scs.session->absStartTime(), scs.session->absEndTime());
  } else {
    scs.duration = scs.session->playEndTime() - scs.session->playStartTime();
    rtspClient->sendPlayCommand(*scs.session, continueAfterPLAY);
  }
}

void continueAfterSETUP(RTSPClient* rtspClient, int resultCode, char* resultString) {
  do {
    UsageEnvironment& env = rtspClient->envir(); // alias
    StreamClientState& scs = ((ourRTSPClient*)rtspClient)->scs; // alias

    if (resultCode != 0) {
      env << *rtspClient << "Failed to set up the \"" << *scs.subsession << "\" subsession: " << resultString << "\n";
      break;
    }

    env << *rtspClient << "Set up the \"" << *scs.subsession << "\" subsession (";
    if (scs.subsession->rtcpIsMuxed()) {
      env << "client port " << scs.subsession->clientPortNum();
    } else {
      env << "client ports " << scs.subsession->clientPortNum() << "-" << scs.subsession->clientPortNum()+1;
    }
    env << ")\n";

    // Having successfully setup the subsession, create a data sink for it, and call "startPlaying()" on it.
    // (This will prepare the data sink to receive data; the actual flow of data from the client won't start happening until later,
    // after we've sent a RTSP "PLAY" command.)

    scs.subsession->sink = DummySink::createNew(env, *scs.subsession, ((ourRTSPClient*)rtspClient), rtspClient->url());
      // perhaps use your own custom "MediaSink" subclass instead
    if (scs.subsession->sink == nullptr) {
      env << *rtspClient << "Failed to create a data sink for the \"" << *scs.subsession
	  << "\" subsession: " << env.getResultMsg() << "\n";
      break;
    }

    env << *rtspClient << "Created a data sink for the \"" << *scs.subsession << "\" subsession\n";
    scs.subsession->miscPtr = rtspClient; // a hack to let subsession handler functions get the "RTSPClient" from the subsession 
    scs.subsession->sink->startPlaying(*(scs.subsession->readSource()),
				       subsessionAfterPlaying, scs.subsession);
    // Also set a handler to be called if a RTCP "BYE" arrives for this subsession:
    if (scs.subsession->rtcpInstance() != nullptr) {
      scs.subsession->rtcpInstance()->setByeWithReasonHandler(subsessionByeHandler, scs.subsession);
      if (((ourRTSPClient*)rtspClient)->m_enableSR == true) {
        scs.subsession->rtcpInstance()->setSRHandler(subsessionSRHandler, scs.subsession);
      }
    }
  } while (0);
  delete[] resultString;

  // Set up the next subsession, if any:
  setupNextSubsession(rtspClient);
}

void continueAfterPAUSE(RTSPClient* rtspClient, int resultCode, char* resultString) {
    do {
      UsageEnvironment& env = rtspClient->envir(); // alias
      // 551 - Option not supported
      if (resultCode != 0 && resultCode != 551) {
        env << *rtspClient << "Failed to pause session: " << resultString << "\n";
        break;
      }
      ((ourRTSPClient*)rtspClient)->m_pauseState = true;
    } while (0);

    delete[] resultString;
}

long timeDiff(struct timeval& starttime, struct timeval& endtime)
{
    long usec;
    if (starttime.tv_sec > endtime.tv_sec ||
           (starttime.tv_sec == endtime.tv_sec && starttime.tv_usec > endtime.tv_usec))
    {
        return 0;
    }
    usec = ((endtime.tv_sec * 1000000) + (endtime.tv_usec)) -
                ((starttime.tv_sec * 1000000) + (starttime.tv_usec));
        return usec;
}

void onFrame(ourRTSPClient *rtspClient, struct timeval& pts)
{
    if (!rtspClient)  return;

    uint64_t current_time = std::chrono::duration_cast<std::chrono::milliseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();
    uint64_t current_diff = (current_time - rtspClient->m_prev_frame_pts);
    rtspClient->m_sumDiff += current_diff;
    rtspClient->m_prev_frame_pts = current_time;
    rtspClient->m_frameCount++;
}

static void qosDataCollector(void* clientData)
{
    ourRTSPClient* rtspClient = (ourRTSPClient *)clientData;
    if (!rtspClient)  return;

    double avg_fps = 0;
    if (rtspClient->m_sumDiff && rtspClient->m_frameCount)
    {
        avg_fps  = (1000.00) / (rtspClient->m_sumDiff/rtspClient->m_frameCount);
    }
    if (avg_fps == 0)
    {
        LOG_COLOR(magenta, "%s \t ==*== Zero fps:%f,  frameCount:%ld", rtspClient->dev_name.c_str(), avg_fps, rtspClient->m_frameCount);
    }
    else
    {
        LOG_COLOR(yellow, "%s \tfps:%f,  frameCount:%ld", rtspClient->dev_name.c_str(), avg_fps, rtspClient->m_frameCount);
    }
    rtspClient->m_sumDiff = 0;
    rtspClient->m_frameCount = 0;
    rtspClient->m_qosPeriodicTask = rtspClient->envir().taskScheduler().scheduleDelayedTask(
                1 * 1000000, (TaskFunc*)qosDataCollector, rtspClient);

}

void continueAfterPLAY(RTSPClient* rtspClient, int resultCode, char* resultString) {
  Boolean success = False;

  do {
    UsageEnvironment& env = rtspClient->envir(); // alias
    StreamClientState& scs = ((ourRTSPClient*)rtspClient)->scs; // alias

    if (resultCode != 0) {
      env << *rtspClient << "Failed to start playing session: " << resultString << "\n";
      break;
    }
    ((ourRTSPClient*)rtspClient)->m_playState = true;

    // Set a timer to be handled at the end of the stream's expected duration (if the stream does not already signal its end
    // using a RTCP "BYE").  This is optional.  If, instead, you want to keep the stream active - e.g., so you can later
    // 'seek' back within it and do another RTSP "PLAY" - then you can omit this code.
    // (Alternatively, if you don't want to receive the entire stream, you could set this timer for some shorter value.)
    if (scs.duration > 0) {
      unsigned const delaySlop = 2; // number of seconds extra to delay, after the stream's expected duration.  (This is optional.)
      scs.duration += delaySlop;
      unsigned uSecsToDelay = (unsigned)(scs.duration*1000000);
      scs.streamTimerTask = env.taskScheduler().scheduleDelayedTask(uSecsToDelay, (TaskFunc*)streamTimerHandler, rtspClient);
    }

    if (((ourRTSPClient*)rtspClient)->m_fpsInfo == true)
    {
      ((ourRTSPClient*)rtspClient)->m_frameCount = 0;
      ((ourRTSPClient*)rtspClient)->m_qosPeriodicTask = env.taskScheduler().scheduleDelayedTask(
                  1 * 1000000, (TaskFunc*)qosDataCollector, rtspClient);
    }

    env << *rtspClient << "Started playing session";
    if (scs.duration > 0) {
      env << " (for up to " << scs.duration << " seconds)";
    }
    env << "...\n";

    success = True;
  } while (0);
  delete[] resultString;

  if (!success) {
    // An unrecoverable error occurred with this stream.
    shutdownStream(rtspClient);
  }
}


// Implementation of the other event handlers:

void subsessionAfterPlaying(void* clientData) {
  MediaSubsession* subsession = (MediaSubsession*)clientData;
  RTSPClient* rtspClient = (RTSPClient*)(subsession->miscPtr);

  // Begin by closing this subsession's stream:
  Medium::close(subsession->sink);
  subsession->sink = nullptr;

  // Next, check whether *all* subsessions' streams have now been closed:
  MediaSession& session = subsession->parentSession();
  MediaSubsessionIterator iter(session);
  while ((subsession = iter.next()) != nullptr) {
    if (subsession->sink != nullptr) return; // this subsession is still active
  }

  // All subsessions' streams have now been closed, so shutdown the client:
  shutdownStream(rtspClient);
}

void subsessionByeHandler(void* clientData, char const* reason) {
  MediaSubsession* subsession = (MediaSubsession*)clientData;
  RTSPClient* rtspClient = (RTSPClient*)subsession->miscPtr;
  UsageEnvironment& env = rtspClient->envir(); // alias

  env << *rtspClient << "Received RTCP \"BYE\"";
  if (reason != nullptr) {
    env << " (reason:\"" << reason << "\")";
    delete[] (char*)reason;
  }
  env << " on \"" << *subsession << "\" subsession\n";

  // Now act as if the subsession had closed:
  subsessionAfterPlaying(subsession);
}

void subsessionSRHandler(void* clientData) {
  MediaSubsession* subsession = (MediaSubsession*)clientData;
  RTSPClient* rtspClient = (RTSPClient*)subsession->miscPtr;
  UsageEnvironment& env = rtspClient->envir(); // alias

  env << *rtspClient << "Received RTCP SR\n";
  RTPReceptionStatsDB::Iterator statsIter(subsession->rtpSource()->receptionStatsDB());
  // Assume that there's only one SSRC source (usually the case):
  RTPReceptionStats* stats = statsIter.next(True);
    if (stats != nullptr) {
    struct timeval fSyncTime;
    fSyncTime.tv_sec = stats->lastReceivedSR_NTPmsw() - 0x83AA7E80; // 1/1/1900 -> 1/1/1970
    double microseconds = (stats->lastReceivedSR_NTPlsw()*15625.0)/0x04000000; // 10^6/2^32
    fSyncTime.tv_usec = (unsigned)(microseconds+0.5);

    LOG(info) << "=======================================================" << endl;
    LOG(info) << "NTP time = " << fSyncTime.tv_sec << "." << fSyncTime.tv_usec << endl;
    LOG(info) << "ReceivedSR_time = " << stats->lastReceivedSR_time().tv_sec << ":" << stats->lastReceivedSR_time().tv_usec << endl;
    LOG(info) << "=======================================================" << endl;
  }
  ((ourRTSPClient*)rtspClient)->m_receivedSR = true;
}

void streamTimerHandler(void* clientData) {
  ourRTSPClient* rtspClient = (ourRTSPClient*)clientData;
  StreamClientState& scs = rtspClient->scs; // alias

  scs.streamTimerTask = nullptr;

  // Shut down the stream:
  shutdownStream(rtspClient);
}

void shutdownStream(RTSPClient* rtspClient, int exitCode) {
    UsageEnvironment& env = rtspClient->envir(); // alias
    StreamClientState& scs = ((ourRTSPClient*)rtspClient)->scs; // alias

    // First, check whether any subsessions have still to be closed:
    if (scs.session != nullptr) { 
      Boolean someSubsessionsWereActive = False;
      MediaSubsessionIterator iter(*scs.session);
      MediaSubsession* subsession;

      while ((subsession = iter.next()) != nullptr) {
        if (subsession->sink != nullptr) {
    Medium::close(subsession->sink);
    subsession->sink = nullptr;

    if (subsession->rtcpInstance() != nullptr) {
      subsession->rtcpInstance()->setByeHandler(nullptr, nullptr); // in case the server sends a RTCP "BYE" while handling "TEARDOWN"
    }

    someSubsessionsWereActive = True;
        }
      }

      if (someSubsessionsWereActive) {
        // Send a RTSP "TEARDOWN" command, to tell the server to shutdown the stream.
        // Don't bother handling the response to the "TEARDOWN".
        rtspClient->sendTeardownCommand(*scs.session, nullptr);
      }
    }

    env << *rtspClient << "Closing the stream.\n";
    //((ourRTSPClient*)rtspClient)->m_playState = false;
    Medium::close(rtspClient);
      // Note that this will also cause this stream's "StreamClientState" structure to get reclaimed.
    LOG(verbose) << "Clsoing test RTSP connection" << endl;
}


// Implementation of "ourRTSPClient":

ourRTSPClient* ourRTSPClient::createNew(UsageEnvironment& env, char const* rtspURL,
					int verbosityLevel, char const* applicationName, portNumBits tunnelOverHTTPPortNum) {
  //return NULL;
  return new ourRTSPClient(env, rtspURL, verbosityLevel, applicationName, tunnelOverHTTPPortNum);
}

ourRTSPClient* ourRTSPClient::createNew(Environment& env, char const* rtspURL) {
  return new ourRTSPClient(env, rtspURL);
}

ourRTSPClient::ourRTSPClient(UsageEnvironment& env, char const* rtspURL,
			     int verbosityLevel, char const* applicationName, portNumBits tunnelOverHTTPPortNum)
  : RTSPClient(env,rtspURL, verbosityLevel, applicationName, tunnelOverHTTPPortNum, -1),
    m_describeState(false),
    m_playState(false),
    m_pauseState(false),
    m_client(nullptr),
    m_frameCount(0),
    m_count_duplicate_ts(0),
    m_count_above_threshold(0),
    m_framerate(30),
    m_qosPeriodicTask(nullptr),
    m_sumDiff(0)
{}

ourRTSPClient::ourRTSPClient(Environment& env, char const* rtspURL)
  : RTSPClient(env, rtspURL, 0, nullptr, 0, -1),
    m_describeState(false),
    m_playState(false),
    m_pauseState(false),
    m_client(nullptr),
    m_frameCount(0),
    m_count_duplicate_ts(0),
    m_count_above_threshold(0),
    m_framerate(30),
    m_qosPeriodicTask(nullptr),
    m_sumDiff(0)
{
  LOG(error) << "ourRTSPClient::ourRTSPClient" << endl;
  // Increase the maximum size of video frames that we can 'proxy' without truncation.
  // (Such frames are unreasonably large; the back-end servers should really not be sending frames this large!)
  OutPacketBuffer::maxSize = MAX_OUT_PACKET_BUFFER_SIZE_IN_MB; // M bytes
}

ourRTSPClient::~ourRTSPClient() {
  LOG(info) << "~ourRTSPClient" << endl;
}

void ourRTSPClient::shutDown() {
    shutdownStream(this);
}

std::vector<std::string> ourRTSPClient::getMediaFromSDP(char const* rtspURL) {

  std::vector<std::string> result;
  sendDescribeCommand(+[](RTSPClient* rtspClient, int resultCode, char* resultString)
  {
      if (resultCode == 0)
      {
          ((ourRTSPClient*)rtspClient)->m_describeState = true;
          ((ourRTSPClient*)rtspClient)->m_sdp           = resultString;
      }
  }, nullptr);

  int attempts = 50;
  while(true)
  {
      if (m_describeState == true)
      {
          size_t found = m_sdp.find("audio");
          if (found != string::npos)
          {
              result.push_back("audio");
          }
          found = m_sdp.find("video");
          if (found != string::npos)
          {
              result.push_back("video");
          }
          break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      if(--attempts == 0)
      {
          LOG(error) << "[Error] Describe event not received for url:" << url() << endl;
      }
  }
  return result;
}

// Implementation of "StreamClientState":

StreamClientState::StreamClientState()
  : iter(nullptr), session(nullptr), subsession(nullptr), streamTimerTask(nullptr), duration(0.0) {
}

StreamClientState::~StreamClientState() {
  delete iter;
  if (session != nullptr) {
    // We also need to delete "session", and unschedule "streamTimerTask" (if set)
    UsageEnvironment& env = session->envir(); // alias

    env.taskScheduler().unscheduleDelayedTask(streamTimerTask);
    Medium::close(session);
  }
}


// Implementation of "DummySink":

// Even though we're not going to be doing anything with the incoming data, we still need to receive it.
// Define the size of the buffer that we'll use:
#define DUMMY_SINK_RECEIVE_BUFFER_SIZE 1000000

DummySink* DummySink::createNew(UsageEnvironment& env, MediaSubsession& subsession, ourRTSPClient* dataArrival, char const* streamId) {
  return new DummySink(env, subsession, dataArrival, streamId);
}

DummySink::DummySink(UsageEnvironment& env, MediaSubsession& subsession, ourRTSPClient* client, char const* streamId)
  : MediaSink(env),
    fSubsession(subsession),
    m_client(client) {
  fStreamId = strDup(streamId);
  fReceiveBuffer = new u_int8_t[DUMMY_SINK_RECEIVE_BUFFER_SIZE];
}

DummySink::~DummySink() {
  delete[] fReceiveBuffer;
  delete[] fStreamId;
}

static long timediff(struct timeval& starttime, struct timeval& endtime)
{
    long usec;
    if (starttime.tv_sec > endtime.tv_sec ||
           (starttime.tv_sec == endtime.tv_sec && starttime.tv_usec > endtime.tv_usec))
    {
        return 0;
    }
    usec = ((endtime.tv_sec * 1000000) + (endtime.tv_usec)) -
                ((starttime.tv_sec * 1000000) + (starttime.tv_usec));
	return usec;
}

void DummySink::afterGettingFrame(void* clientData, unsigned frameSize, unsigned numTruncatedBytes,
				  struct timeval presentationTime, unsigned durationInMicroseconds) {
  DummySink* sink = (DummySink*)clientData;
  sink->afterGettingFrame(frameSize, numTruncatedBytes, presentationTime, durationInMicroseconds);
}

// If you don't want to see debugging output for each received frame, then comment out the following line:
#define DEBUG_PRINT_EACH_RECEIVED_FRAME 1

void DummySink::afterGettingFrame(unsigned frameSize, unsigned numTruncatedBytes,
				  struct timeval presentationTime, unsigned /*durationInMicroseconds*/) {
  // We've just received a frame of data.  (Optionally) print out information about it:
  if (m_client->m_timestampInfo)
  {
    unsigned char fCurPacketNALUnitType = (fReceiveBuffer[0]&0x1F);
    if (fCurPacketNALUnitType == 1 || fCurPacketNALUnitType == 5)
    {
      m_client->m_frameCount++;
      if (m_client->m_frameCount == 1)
      {
        goto skip;
      }

      long time_diff;
      time_diff = timediff(m_client->m_prevPtsTime, presentationTime);
      if (time_diff != 0) {
        // us to millisecond conversion
        time_diff = time_diff/1000;
      }
      LOG(info) << "url:" << m_client->url() << ", ts: " <<(int)presentationTime.tv_sec << "." << ((unsigned)presentationTime.tv_usec/1000) << ", PTS diff: " << time_diff << endl;
      if (time_diff == 0)
      {
        m_client->m_count_duplicate_ts++;
        LOG(info) << "////////------- Duplicate time found for pts:" <<
                  (int)presentationTime.tv_sec << "." << ((unsigned)presentationTime.tv_usec/1000) << endl;
      }
      else if (time_diff > static_cast<long>((1.25*(1000/m_client->m_framerate))))
      {
        LOG(info) << "////////------- Timestamp diff above 25% alert for pts:" <<
                 (int)presentationTime.tv_sec << "." << ((unsigned)presentationTime.tv_usec/1000) << endl;
        m_client->m_count_above_threshold++;
      }
skip:
      m_client->m_prevPtsTime = presentationTime;
    }
  }

  if (m_client->m_fpsInfo)
  {
    unsigned char fCurPacketNALUnitType = (fReceiveBuffer[0]&0x1F);
    if (fCurPacketNALUnitType == 1 || fCurPacketNALUnitType == 5)
    {
      onFrame(m_client, presentationTime);
    }
  }

#ifdef DEBUG_PRINT_EACH_RECEIVED_FRAME
    if (fStreamId != nullptr) LOG(verbose) << "Stream \"" << fStreamId << "\"; ";
    LOG(verbose) << fSubsession.mediumName() << "/" << fSubsession.codecName() << ":\tReceived " << frameSize << " bytes";
    if (numTruncatedBytes > 0) LOG(verbose) << " (with " << numTruncatedBytes << " bytes truncated)";
    char uSecsStr[6+1]; // used to output the 'microseconds' part of the presentation time
    sprintf(uSecsStr, "%06u", (unsigned)presentationTime.tv_usec);
    LOG(verbose) << ".\tPresentation time: " << (int)presentationTime.tv_sec << "." << uSecsStr;
    if (fSubsession.rtpSource() != nullptr && !fSubsession.rtpSource()->hasBeenSynchronizedUsingRTCP()) {
      LOG(verbose) << "!"; // mark the debugging output to indicate that this presentation time is not RTCP-synchronized
    }
#ifdef DEBUG_PRINT_NPT
    LOG(verbose) << "\tNPT: " << fSubsession.getNormalPlayTime(presentationTime);
#endif
    LOG(verbose) << "\n";
#endif
  m_client->m_dataArrived = true;
  // Then continue, to request the next frame of data:
  continuePlaying();
}

Boolean DummySink::continuePlaying() {
  if (fSource == nullptr) return False; // sanity check (should not happen)

  // Request the next frame of data from our input source.  "afterGettingFrame()" will get called later, when it arrives:
  fSource->getNextFrame(fReceiveBuffer, DUMMY_SINK_RECEIVE_BUFFER_SIZE,
                        afterGettingFrame, this,
                        onSourceClosure, this);
  return True;
}

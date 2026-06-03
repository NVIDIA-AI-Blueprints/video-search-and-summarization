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

#include "DynamicRTSPServer.hh"
#include <liveMedia.hh>
#include <string.h>
#include <iostream>
#include <string>
#include <vector>

#include "NvMediaServer.h"
#include "logger.h"
#include "utils.h"
#include "config.h"
#include "cmdline_parser.h"
#include "webrtcstreamproducer.h"
#include "gst_utils.h"
#include "RtspSyncPlayback.h"
#include "AvLoopSyncCoordinator.h"

#ifdef UNIT_TEST
#include <cstdlib>
#endif

DynamicRTSPServer*
DynamicRTSPServer::createNew(UsageEnvironment& env, Port ourPort,
			     UserAuthenticationDatabase* authDatabase,
			     unsigned reclamationTestSeconds) {
  int ourSocketIPv4 = setUpOurSocket(env, ourPort, AF_INET);
  int ourSocketIPv6 = setUpOurSocket(env, ourPort, AF_INET6);
  if (ourSocketIPv4 < 0 && ourSocketIPv6 < 0) return nullptr;

  return new DynamicRTSPServer(env, ourSocketIPv4, ourSocketIPv6, ourPort,
                               authDatabase, reclamationTestSeconds);
}

DynamicRTSPServer::DynamicRTSPServer(UsageEnvironment& env, int ourSocketIPv4, int ourSocketIPv6,
				     Port ourPort,
				     UserAuthenticationDatabase* authDatabase, unsigned reclamationTestSeconds)
  : RTSPServer(env, ourSocketIPv4, ourSocketIPv6, ourPort, authDatabase, reclamationTestSeconds) {
}

DynamicRTSPServer::~DynamicRTSPServer()
{
    LOG(info) << "~DynamicRTSPServer" << endl;
}

void DynamicRTSPServer::cleanup()
{
    LOG(info) << "DynamicRTSPServer::cleanup()" << endl;

    std::vector<std::string> streamNames;
    {
        GenericMediaServer::ServerMediaSessionIterator *iter =
                new GenericMediaServer::ServerMediaSessionIterator(*this);
        ServerMediaSession *sms = nullptr;
        while((sms = (ServerMediaSession *)iter->next()) != nullptr)
        {
            LOG(info) << "scheduling removal of sms:" << sms << ", stream:" << sms->streamName() << endl;
            streamNames.push_back(sms->streamName());
        }
        delete iter;
    }

    for (const auto& name : streamNames)
    {
        closeAllClientSessionsForServerMediaSession(name.c_str());
    }
    for (const auto& name : streamNames)
    {
        removeServerMediaSession(name.c_str());
    }
    LOG(info) << "DynamicRTSPServer::cleanup() - deleted streams" << endl;
    delete this;
}

vector<string> DynamicRTSPServer::getActiveStreams()
{
    vector<string> active_streams;
    GenericMediaServer::ServerMediaSessionIterator *iter =
            new GenericMediaServer::ServerMediaSessionIterator(*this);
    ServerMediaSession *sms = nullptr;
    while((sms = (ServerMediaSession *)iter->next()) != nullptr)
    {
        if (sms && sms->referenceCount() > 0)
        {
            active_streams.push_back(sms->streamName());
        }
    }
    delete iter;  // Add cleanup of iterator
    return active_streams;
}

ServerMediaSession* DynamicRTSPServer
::getServerMediaSessionForStream(char const* streamName)
{
    return getServerMediaSession(streamName);
}

void DynamicRTSPServer
::deleteServerMediaSessionForStream(char const* streamName)
{
    string smsAudioName = string(streamName) + "/audio";

    string nvstream_name;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager && deviceManager->getDeviceType() == TYPE_STREAMER)
    {
        shared_ptr<SensorInfo> sensor = deviceManager->searchSensor(streamName);
        if (sensor)
        {
            shared_ptr<StreamInfo> stream_info = sensor->getStream(streamName);
            if (stream_info)
            {
                /* There can be multiple sms with same name like filename.mp4, filename_1.mp4, filename_2.mp4
                * dot is added while finding streamName in sms, So that it will delete only intended sms */
                nvstream_name = stream_info->name + string(".");
            }
        }
    }
    GenericMediaServer::ServerMediaSessionIterator *iter =
            new GenericMediaServer::ServerMediaSessionIterator(*this);
    ServerMediaSession *sms = nullptr;
    while((sms = (ServerMediaSession *)iter->next()) != nullptr)
    {
        if (sms)
        {
            string sms_stream = sms->streamName();
            if( (strcmp(sms->streamName(), streamName) == 0)
                || (strcmp(sms->streamName(), smsAudioName.c_str()) == 0)
                || (!nvstream_name.empty() && sms_stream.find(nvstream_name) != string::npos) )
            {
                LOG(info) << "deleting sms:" << sms << ", stream:" << sms->streamName() << endl;
                deleteServerMediaSession(sms);
            }
        }
    }
    delete iter;
}

static ServerMediaSession* createNewSMS(UsageEnvironment& env,
					string streamName, eSourceType sourceType, string url_params); // forward

void DynamicRTSPServer
::lookupServerMediaSession(char const* streamName,
                           lookupServerMediaSessionCompletionFunc* completionFunc,
                           void* completionClientData,
                           Boolean isFirstLookupInSession)
{
  string stream_name(streamName);
  LOG(info) << "RTSP lookup: some Stream Name: " << stream_name << std::endl;

  // Next, check whether we already have a "ServerMediaSession" for this file:
  ServerMediaSession* sms = getServerMediaSession(streamName);
  Boolean smsExists = sms != nullptr;
  if (smsExists && isFirstLookupInSession == false)
  {
      LOG(info) << "Return existing session object" << streamName <<  endl;
      if (completionFunc != nullptr)
      {
          (*completionFunc)(completionClientData, sms);
      }
      return;
  }

  if(stream_name.find("live") != std::string::npos)
  {
      LOG(info) << "RTSP lookup: Live Stream Name: " << stream_name << std::endl;
  }
  if(stream_name.find("webrtc") != std::string::npos)
  {
      LOG(info) << "RTSP lookup: Live Stream Name: " << stream_name << std::endl;
      string token("webrtc/");
      string url = stream_name.substr(stream_name.find(token) + token.size());

      string peer_id, url_params;
      if (url.find("?") != string::npos)
      {
          peer_id = url.substr(0, url.find("?"));
          url_params = url.substr(url.find("?") + 1);
      }
      else
      {
          peer_id = url;
          url_params = "";
      }

      LOG(info) << "peer_id: " << peer_id << ", url_params:" << url_params << endl;
      sms = createNewSMS(envir(), peer_id, SourceTypeLive, url_params);
      addServerMediaSession(sms);
  }
  else if(stream_name.find("vod/") != std::string::npos)
  {
      LOG(info) << "RTSP lookup: Replay Stream Name: " << stream_name << std::endl;
      if (isVodServer() == false)
      {
        LOG(info) << "This is not Vod server, ignoring the request" << endl;
        /* Returning from here since this is not VoD server */
        if (completionFunc != nullptr)
        {
            (*completionFunc)(completionClientData, sms);
        }
        return;
      }

      string token("vod/");
      string url = stream_name.substr(stream_name.find(token) + token.size());
      string stream_id, url_params;
      if (url.find("?") != string::npos)
      {
          stream_id = url.substr(0, url.find("?"));
          url_params = url.substr(url.find("?") + 1);
          url_params.append("&vodStream=true");
      }
      else
      {
          stream_id = url;
          url_params = "?vodStream=true";
      }

      pair<int64_t, int64_t> epochTimeRange = getEpochTimeRangeFromIsoString(url_params);
      if (sms == nullptr && isRecordedFileExist(stream_id, epochTimeRange.first, epochTimeRange.second))
      {
          sms = createNewSMS(envir(), stream_id, SourceTypeFile, url_params);
          addServerMediaSession(sms);
      }
  }
  else if(stream_name.find(NV_STREAMER) != std::string::npos && stream_name.find(NV_STREAMER) == 0)
  {
    LOG(info) << "RTSP lookup: File Stream Name: " << stream_name << std::endl;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager && deviceManager->m_isRtspServerReady == false)
    {
        /* Returning from here since streams are not yet ready */
        if (completionFunc != nullptr)
        {
            (*completionFunc)(completionClientData, sms);
        }
        return;
    }

    if (GET_CONFIG().nv_streamer_sync_file_count > 0)
    {
        if (RtspSyncPlayback::getInstance()->getMediaSourceListSize() >= (size_t)GET_CONFIG().nv_streamer_sync_file_count)
        {
            LOG(info) << "RTSP lookup: Exceeded sync file count, ignoring the request" << std::endl;
            if (completionFunc != nullptr)
            {
                (*completionFunc)(completionClientData, sms);
            }
            return;
        }
    }

    // First, check whether the specified "streamName" exists as a local file:
    string token = string(NV_STREAMER);
    string url = stream_name.substr(stream_name.find(token) + token.size());

    string file_name, url_params;
    if (url.find("?") != string::npos)
    {
        file_name = url.substr(0, url.find("?"));
        url_params = url.substr(url.find("?") + 1);
    }
    else
    {
        file_name = url;
        url_params = "";
    }

    LOG(info) << "file Name: " << file_name << ", url_params:" << url_params << endl;
    Boolean fileExists = isFileExist(file_name);
    LOG(info) << "lookupServerMediaSession - fileExists: "<< static_cast<bool>(fileExists)
            << " smsExists:" << static_cast<bool>(smsExists) << endl;
    // Handle the four possibilities for "fileExists" and "smsExists":
    if (!fileExists)
    {
        if (smsExists)
        {
            // "sms" was created for a file that no longer exists. Remove it:
            removeServerMediaSession(sms);
            sms = nullptr;
        }
        LOG(error) << "NOT FOUND: Replay Stream Name: " << stream_name << std::endl;
        sms = nullptr;
    }
    else
    {
        if (sms == nullptr)
        {
            sms = createNewSMS(envir(), file_name, SourceTypeFile, url_params);
            addServerMediaSession(sms);
        }
    }
  }
#ifdef UNIT_TEST
  else if(stream_name.find("test/") != std::string::npos)
  {
    /* UNIT_TEST: serve video files from gtest video directory.
     * URL pattern: test/<filename> (e.g. test/sample_10sec_h264.mp4).
     * GTEST_VIDEO_DIR is set by RtspServerTestUtils::start(). */
    LOG(info) << "UNIT_TEST RTSP lookup: File Stream Name: " << stream_name << std::endl;

    string token("test/");
    string url = stream_name.substr(stream_name.find(token) + token.size());

    string file_name, url_params;
    if (url.find("?") != string::npos)
    {
        file_name = url.substr(0, url.find("?"));
        url_params = url.substr(url.find("?") + 1);
    }
    else
    {
        file_name = url;
        url_params = "";
    }

    /* Prepend gtest video directory to get the full path */
    const char* videoDir = std::getenv("GTEST_VIDEO_DIR");
    if (videoDir && videoDir[0] != '\0')
    {
        string dir(videoDir);
        if (dir.back() != '/')
            dir += '/';
        file_name = dir + file_name;
    }

    LOG(info) << "UNIT_TEST file Name: " << file_name << ", url_params:" << url_params << endl;
    Boolean fileExists = isFileExist(file_name);
    LOG(info) << "UNIT_TEST lookupServerMediaSession - fileExists: " << static_cast<bool>(fileExists)
              << " smsExists:" << static_cast<bool>(smsExists) << endl;

    if (!fileExists)
    {
        if (smsExists)
        {
            removeServerMediaSession(sms);
            sms = nullptr;
        }
        LOG(error) << "UNIT_TEST NOT FOUND: " << file_name << std::endl;
        sms = nullptr;
    }
    else
    {
        if (sms == nullptr)
        {
            sms = createNewSMS(envir(), file_name, SourceTypeFile, url_params);
            addServerMediaSession(sms);
        }
    }
  }
#endif /* UNIT_TEST */
  else if(stream_name.find(NV_CSI_SENSOR) != std::string::npos)
  {
      LOG(info) << "RTSP lookup: CSI Stream Name: " << stream_name << std::endl;
      string token(string(NV_CSI_SENSOR) + string("/"));
      string url = stream_name.substr(stream_name.find(token) + token.size());

      string sensorId, url_params;
      if (url.find("?") != string::npos)
      {
          sensorId = url.substr(0, url.find("?"));
          url_params = url.substr(url.find("?") + 1);
      }
      else
      {
          sensorId = url;
          url_params = "";
      }

      if (sensorId.empty())
      {
        if (completionFunc != nullptr)
        {
            (*completionFunc)(completionClientData, sms);
        }
        return;
      }

      LOG(info) << "sensorId: " << sensorId << ", url_params:" << url_params << endl;
      sms = createNewSMS(envir(), sensorId, SourceTypeNative, url_params);
      addServerMediaSession(sms);
  }
  if (completionFunc != nullptr)
  {
      (*completionFunc)(completionClientData, sms);
  }
}


#define NEW_SMS(description) do {\
char const* descStr = description\
    ", streamed by the LIVE555 Media Server";\
sms = ServerMediaSession::createNew(env, fileName, fileName, descStr);\
} while(0)

static ServerMediaSession* createNewSMS(UsageEnvironment& env,
					string streamName, eSourceType sourceType, string url_params)
{
    ServerMediaSession* sms = nullptr;
    bool video_session = false, audio_session = false;
    Boolean const reuseSource = False;
    char const* description= "NvStreamer Media";

    int64_t currentEpochTimeNs = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    string sms_name = streamName + string("_") + to_string(currentEpochTimeNs);

    LOG(info) << "Creating sms with name : " << sms_name << endl;
    sms = ServerMediaSession::createNew(env, sms_name.c_str(), sms_name.c_str(), description);
    if (url_params.find("videoOnly=true") != string::npos)
    {
        /* Create subsession for video only */
        video_session = true;
    }
    else if (url_params.find("audioOnly=true") != string::npos)
    {
        /* Create subsession for audio only */
        audio_session = true;
    }
    else if (url_params.find("includeAudio=true") != string::npos)
    {
        /* Create subsession for both video and audio */
        video_session = audio_session = true;
    }
    else
    {
        /* Default Create subsession for only video */
        video_session = true;
        if (sourceType == SourceTypeLive)
        {
            video_session = WebrtcStreamProducer::getInstance()->isVideoTrackEnabled(streamName);
            audio_session = WebrtcStreamProducer::getInstance()->isAudioTrackEnabled(streamName);
        }
    }

    /* If this session will host BOTH an audio and a video subsession for
     * the SAME file, create a per-SMS AV-loop-sync coordinator. Both
     * subsessions share this pointer; when loop playback is enabled,
     * the coordinator gates the EOS-driven seekToStart() so audio and
     * video restart in lockstep (no per-loop drift).
     *
     * Live (WebRTC) sources never loop, so a coordinator there would
     * never fire -- skip to keep the object graph minimal. */
    std::shared_ptr<AvLoopSyncCoordinator> avLoopSync;
    if (video_session && audio_session && sourceType == SourceTypeFile)
    {
        avLoopSync = std::make_shared<AvLoopSyncCoordinator>(sms_name);
        LOG(info) << "Created AvLoopSyncCoordinator for sms:" << sms_name << endl;
    }

    if (video_session == true)
    {
        NvFileServerMediaSubsession *NvVideoFileServerSms =
                NvFileServerMediaSubsession::createNew(env, streamName,
                MediaTypeVideo, sourceType, reuseSource, url_params);
        if (NvVideoFileServerSms) {
            if (avLoopSync) {
                NvVideoFileServerSms->setAvLoopSync(avLoopSync);
            }
            sms->addSubsession(NvVideoFileServerSms);
        }
    }
    if (audio_session == true)
    {
        NvFileServerMediaSubsession *NvAudioFileServerSms =
                NvFileServerMediaSubsession::createNew(env, streamName,
                MediaTypeAudio, sourceType, reuseSource, url_params);
        if (NvAudioFileServerSms) {
            if (avLoopSync) {
                NvAudioFileServerSms->setAvLoopSync(avLoopSync);
            }
            sms->addSubsession(NvAudioFileServerSms);
        }
    }
    return sms;
}

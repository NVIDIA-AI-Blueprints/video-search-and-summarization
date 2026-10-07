/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <thread>
#include <memory>
#include <iostream>
#include <sstream>
#include <map>
#include <queue>
#include <mutex>
#include <vector>
#include <condition_variable>
#include <future>
#include <functional>
#include <algorithm>
#include <cctype>

#include "logger.h"
#include "DynamicRTSPServer.hh"
#include "version.hh"
#include "liveMedia.hh"
#include "environment.h"
#include "syncobject.h"
#include "NotificationFactory.h"
#include "vst_common.h"
#include "vstmodule.h"
#include "modules_apis.h"
#include "stream_event_manager.h"

namespace nv_vms {

typedef struct _StreamDetails
{
    std::string id;
    std::string name;
    std::string sensorUrl;
    std::string sensorName;
    std::string proxyUrl;
    std::string vodUrl;
    std::string codec;
    std::string resolution;
    std::string framerate;
    std::string tags;
} StreamDetails;
class RtspServer
{
    public:
        explicit RtspServer(u_int16_t port);
        ~RtspServer();
        int16_t getPort() { return m_rtspServerPortNum; }
        std::string createProxy(const std::string& id, const std::string& name, const std::string& url);
        bool deleteProxy(const std::string& id);
        int addProxy(const std::string& id, const std::string& name, std::string& url);
        int removeProxy(const std::string& id);
        void addStream(const std::string& streamId, const std::string& url);
        std::string urlPrefix() { return m_urlPrefix; }
        std::string originalPrefix();
        unsigned int activeClientSessions();
        std::vector<std::string> getActiveStreams();
        ServerMediaSession* serverMediaSessionForStream(const std::string& id);
        int removeServerMediaSession(const std::string& id);
        UsageEnvironment& getEnv() { return m_env; }
        void updateUser(const char *username);
        void addUser(const char *username, const char *passwordHash);
        void removeUser(const char *username);
        std::vector<StreamDetails> streamList();
        bool findStreamId(const std::string& id);
        std::string getRtspServerDomainPrefix() { return m_rtspServerDomainPrefix; }
        bool isError() { return m_isError; }
        void setVodServer(bool isVodServer);
        std::map<std::string, StreamDetails, std::less<>> getStreamList() { return m_streamsList; }
        INotificationInterface* getNotifier() { return m_notifier; }
        void updateStreamMetadata(const std::string& id, const std::string& vodUrl,
                                  const std::string& codec, const std::string& resolution,
                                  const std::string& framerate, const std::string& tags);

        /* Register a stream asynchronously with the device manager and notify
         * downstream consumers. All optional/derived parameters are bundled
         * into a Json::Value `params` object so that the surface stays small
         * and new fields (audio info, future SDP-derived metadata, etc.) can
         * be added without churning every call site.
         *
         * Required keys in `params`:
         *   "vodUrl"           : string
         *   "codec"            : string  (initial/best-known video codec)
         *
         * Optional keys in `params`:
         *   "resolution"       : string
         *   "framerate"        : string
         *   "tags"             : string
         *   "sdpDetectedCodec" : string  (overrides "codec" when non-empty)
         *   "audio" : {
         *       "present"     : bool
         *       "codec"       : string  (raw SDP codec, e.g. "MPEG4-GENERIC")
         *       "encoding"    : string  (normalized for storage, e.g. "AAC")
         *       "sample_rate" : int
         *       "channels"    : int
         *   }
         */
        void registerStreamAsync(const std::string& id, const std::string& name,
                                 const std::string& proxyUrl,
                                 const Json::Value& params);

    private:
        int start();
        void startAsyncWorker();
        void stopAsyncWorker();
        void postAsyncTask(std::function<void()> task);
    private:
        RTSPServer* m_rtspServer = nullptr;
        portNumBits m_rtspServerPortNum;
        Environment m_env;
        TaskScheduler* m_scheduler = nullptr;
        bool m_threadRunning = false;
        std::unique_ptr<std::thread> m_thread = nullptr;
        std::map<std::string, std::string, std::less<>> m_liveCameraStreamList;
        std::mutex               m_streamLock;
        TaskToken m_eventAddStream;
        TaskToken m_eventRemoveStream;
        std::string m_urlPrefix;
        std::string m_preferredIface;
        ServerMediaSession* m_sms = nullptr;
        std::unique_ptr<UserAuthenticationDatabase> m_authDB;
        SyncObject m_sync;
        std::map<std::string, StreamDetails, std::less<>> m_streamsList;
        std::string m_rtspServerDomainPrefix;
        bool m_isError = false;
        INotificationInterface* m_notifier = nullptr;
        std::thread m_asyncWorker;
        std::queue<std::function<void()>> m_asyncTasks;
        std::mutex m_asyncTaskLock;
        std::condition_variable m_asyncTaskCv;
        bool m_asyncWorkerRunning = false;
    };

    class AppProxyServerMediaSession : public ProxyServerMediaSession {
    public:
        static AppProxyServerMediaSession* createNew(RtspServer *rtspServer,
                                                    UsageEnvironment& env,
                                                    GenericMediaServer* ourMediaServer,
                                                    char const* inputStreamURL,
                                                    char const* streamName = nullptr,
                                                    char const* username = nullptr,
                                                    char const* password = nullptr,
                                                    portNumBits tunnelOverHTTPPortNum = 0,
                                                    int verbosityLevel = 0,
                                                    portNumBits initialPortNum = 6970,
                                                    Boolean multiplexRTCPWithRTP = True,
                                                    int socketNumToServer = -1,
                                                    MediaTranscodingTable* transcodingTable = nullptr)
        {
            return std::make_unique<AppProxyServerMediaSession>(
                                                rtspServer, env, ourMediaServer, inputStreamURL,
                                                streamName, username, password,
                                                tunnelOverHTTPPortNum, verbosityLevel,
                                                initialPortNum, multiplexRTCPWithRTP,
                                                socketNumToServer, transcodingTable).release();
        }

        AppProxyServerMediaSession(RtspServer *rtspServer,
                                    UsageEnvironment& env,
                                    GenericMediaServer* ourMediaServer,
                                    char const* inputStreamURL,
                                    char const* streamName,
                                    char const* username,
                                    char const* password,
                                    portNumBits tunnelOverHTTPPortNum,
                                    int verbosityLevel,
                                    portNumBits initialPortNum,
                                    Boolean multiplexRTCPWithRTP,
                                    int socketNumToServer,
                                    MediaTranscodingTable* transcodingTable)
            : ProxyServerMediaSession(env, ourMediaServer, inputStreamURL, streamName,
                                    username, password, tunnelOverHTTPPortNum,
                                    verbosityLevel, socketNumToServer, transcodingTable,
                                    initialPortNum, multiplexRTCPWithRTP)
            , m_rtspServer(rtspServer)
            , m_savedUrl("")
        {
        }

        // Notify when sdp is ready for the stream
        void sdpReady()
        {
            // Save URL for later use and add null check
            const char* urlPtr = url();
            if (urlPtr != nullptr)
            {
                m_savedUrl = urlPtr;
                LOG(warning) << "SDP is ready url:" << secureUrlForLogging(urlPtr) << ", streamName:" << streamName() << std::endl;
            }
            else
            {
                LOG(warning) << "SDP is ready url: <null>, streamName:" << streamName() << std::endl;
            }

            // Access codec information through SDP description
            Json::Value detectedVideoCodecs(Json::arrayValue);
            Json::Value detectedAudioInfo;
            Json::Value videoParameterSets(Json::arrayValue);
            detectedAudioInfo["present"] = false;
            std::unique_ptr<char[]> sdpDesc(generateSDPDescription(AF_INET));
            if (sdpDesc)
            {
                Json::Value videoInfo = parseVideoInfoFromSDP(sdpDesc.get());
                detectedVideoCodecs = videoInfo["codecs"];
                videoParameterSets  = videoInfo["parameterSets"];
                for (const auto& codec : detectedVideoCodecs)
                {
                    LOG(warning) << "Detected video codec from SDP: " << codec.asString() << std::endl;
                }
                /* Also pull audio info from the same SDP buffer. This is the
                 * earliest hook we have for audio detection -- it runs at
                 * proxy DESCRIBE-response time, before any RTSP client
                 * connects and well before the recorder is started. */
                detectedAudioInfo = parseAudioInfoFromSDP(sdpDesc.get());
                if (detectedAudioInfo.get("present", false).asBool())
                {
                    LOG(warning) << "Detected audio from SDP: codec="
                                 << detectedAudioInfo.get("codec", "").asString()
                                 << ", sample_rate=" << detectedAudioInfo.get("sample_rate", 0).asInt()
                                 << ", channels=" << detectedAudioInfo.get("channels", 0).asInt()
                                 << std::endl;
                    /* Normalize codec name for storage. The download API and
                     * other consumers look for "AAC" specifically; the SDP
                     * RTP payload format is "MPEG4-GENERIC". Map it here in
                     * one place so all downstream consumers stay consistent. */
                    std::string sdpAudioCodec = detectedAudioInfo.get("codec", "").asString();
                    std::string normalizedCodec = sdpAudioCodec;
                    std::string codecLower      = sdpAudioCodec;
                    std::transform(codecLower.begin(), codecLower.end(),
                                   codecLower.begin(),
                                   [](unsigned char c) { return (char)std::tolower(c); });
                    if (codecLower == "mpeg4-generic")
                    {
                        normalizedCodec = "AAC";
                    }
                    detectedAudioInfo["encoding"] = normalizedCodec;
                }
            }


            std::string proxyStreamName = streamName();
            std::map<std::string, StreamDetails, std::less<>> streamsList = m_rtspServer->getStreamList();
            for (auto stream : streamsList)
            {
                StreamDetails streamInfo = stream.second;
                if (streamInfo.name == proxyStreamName)
                {
                    std::string live_proxy_url = vst_common::toDomainName(streamInfo.proxyUrl, streamInfo.id);
                    std::string vod_url = vst_rtsp::vodServerDomainPrefix(streamInfo.id) + std::string("vod/") + streamInfo.id;

                    std::string sdpDetectedCodec;
                    if (!detectedVideoCodecs.empty())
                    {
                        sdpDetectedCodec = detectedVideoCodecs[0].asString();
                    }
                    std::string asyncCodec = sdpDetectedCodec.empty() ? streamInfo.codec : sdpDetectedCodec;
                    std::string asyncVodUrl = streamInfo.vodUrl.empty() ? vod_url : streamInfo.vodUrl;

                    Json::Value params;
                    params["vodUrl"]           = asyncVodUrl;
                    params["codec"]            = asyncCodec;
                    params["resolution"]       = streamInfo.resolution;
                    params["framerate"]        = streamInfo.framerate;
                    params["tags"]             = streamInfo.tags;
                    params["sdpDetectedCodec"] = sdpDetectedCodec;
                    params["audio"]            = detectedAudioInfo;
                    params["parameterSets"]    = videoParameterSets;

                    m_rtspServer->registerStreamAsync(
                        streamInfo.id, streamInfo.sensorName, live_proxy_url, params);

                    break;
                }
            }
        }
        // Notify when sdp is reset for the stream
        void sdpReset()
        {
            // Use saved URL if available, otherwise try to get current URL
            if (!m_savedUrl.empty())
            {
                LOG(warning) << "SDP reset for url:" << secureUrlForLogging(m_savedUrl.c_str()) << std::endl;
            }
            else
            {
                const char* urlPtr = url();
                if (urlPtr != nullptr)
                {
                    LOG(warning) << "SDP reset for url:" << secureUrlForLogging(urlPtr) << std::endl;
                }
                else
                {
                    LOG(warning) << "SDP reset for url: <null>" << std::endl;
                }
            }
        }

    private:
        RtspServer *m_rtspServer;
        std::string m_savedUrl;  // Store URL for use in sdpReset when url() may be null

        /* Reads the video m-section in one pass (audio: parseAudioInfoFromSDP).
         * Returns "codecs" from a=rtpmap and "parameterSets" from a=fmtp, base64
         * and in bitstream order. Decoding them is left to the caller: this runs
         * on the live555 event-loop thread, which must not block. */
        Json::Value parseVideoInfoFromSDP(const char* sdp)
        {
            Json::Value videoInfo;
            videoInfo["codecs"] = Json::Value(Json::arrayValue);
            videoInfo["parameterSets"] = Json::Value(Json::arrayValue);
            if (!sdp) return videoInfo;

            // Value of <key> in an a=fmtp parameter list
            auto attributeValue = [](const std::string& line, const std::string& key) -> std::string {
                size_t keyPos = line.find(key);
                if (keyPos == std::string::npos) return "";
                std::string value = line.substr(keyPos + key.length());
                size_t endPos = value.find_first_of(" ;\r\n");
                if (endPos != std::string::npos)
                {
                    value.erase(endPos);
                }
                return value;
            };

            std::string sdpStr(sdp);
            std::istringstream stream(sdpStr);
            std::string line;
            bool inVideoSection = false;

            while (std::getline(stream, line))
            {
                // Track media sections: m=<media> <port> <proto> <payload_types>
                if (line.find("m=") == 0)
                {
                    inVideoSection = (line.find("m=video") == 0);
                }
                // Only process rtpmap lines when in video section
                else if (inVideoSection && line.find("a=rtpmap:") == 0)
                {
                    size_t spacePos = line.find(' ');
                    if (spacePos != std::string::npos)
                    {
                        std::string codecInfo = line.substr(spacePos + 1);
                        size_t slashPos = codecInfo.find('/');
                        if (slashPos != std::string::npos)
                        {
                            videoInfo["codecs"].append(codecInfo.substr(0, slashPos));
                        }
                    }
                }
                // Only process format parameters when in video section
                else if (inVideoSection && line.find("a=fmtp:") == 0 &&
                         videoInfo["parameterSets"].empty())
                {
                    // H.265 form first, H.264 packs all sets into one attribute
                    for (const char* const key : {"sprop-vps=", "sprop-sps=", "sprop-pps="})
                    {
                        std::string value = attributeValue(line, key);
                        if (!value.empty())
                        {
                            videoInfo["parameterSets"].append(value);
                        }
                    }
                    if (videoInfo["parameterSets"].empty() == false) continue;

                    for (const std::string& value : splitString(attributeValue(line, "sprop-parameter-sets="), ","))
                    {
                        if (!value.empty())
                        {
                            videoInfo["parameterSets"].append(value);
                        }
                    }
                }
            }

            return videoInfo;
        }

        /* Helper function to parse audio info from SDP description.
         *
         * Returns a JSON object with the fields:
         *   present       : bool   - whether m=audio section exists with rtpmap
         *   codec         : string - codec name from a=rtpmap (e.g. "MPEG4-GENERIC")
         *   sample_rate   : int    - clock rate from a=rtpmap (e.g. 44100, 48000)
         *   channels      : int    - channel count from a=rtpmap (default 1)
         *
         * Empty/missing fields when no audio section is present. The caller is
         * responsible for any codec-name normalization (e.g. mpeg4-generic -> AAC). */
        Json::Value parseAudioInfoFromSDP(const char* sdp)
        {
            Json::Value audio;
            audio["present"]     = false;
            audio["codec"]       = "";
            audio["sample_rate"] = 0;
            audio["channels"]    = 1;
            if (!sdp) return audio;

            std::string sdpStr(sdp);
            std::istringstream stream(sdpStr);
            std::string line;
            bool inAudioSection = false;

            while (std::getline(stream, line))
            {
                if (line.find("m=") == 0)
                {
                    inAudioSection = (line.find("m=audio") == 0);
                    continue;
                }
                if (!inAudioSection) continue;

                if (line.find("a=rtpmap:") == 0)
                {
                    /* Format: "a=rtpmap:<pt> <CODEC>/<rate>[/<channels>]"
                     *
                     * An audio m-section may advertise multiple payload
                     * types, e.g. "m=audio 0 RTP/AVP 96 97", followed by
                     * one a=rtpmap: per PT. We take the first rtpmap as
                     * the primary codec and stop -- continuing would
                     * non-deterministically overwrite the result with
                     * whichever line comes last. */
                    size_t spacePos = line.find(' ');
                    if (spacePos == std::string::npos) continue;
                    std::string rtpmapVal = line.substr(spacePos + 1);
                    size_t firstSlash = rtpmapVal.find('/');
                    if (firstSlash == std::string::npos) continue;

                    audio["present"] = true;
                    audio["codec"]   = rtpmapVal.substr(0, firstSlash);

                    std::string rest = rtpmapVal.substr(firstSlash + 1);
                    size_t secondSlash = rest.find('/');
                    std::string rateStr     = (secondSlash == std::string::npos) ? rest
                                                                       : rest.substr(0, secondSlash);
                    /* Trim trailing CR / whitespace introduced by SDP line endings. */
                    while (!rateStr.empty() && (rateStr.back() == '\r' || rateStr.back() == ' '))
                    {
                        rateStr.pop_back();
                    }
                    try
                    {
                        audio["sample_rate"] = std::stoi(rateStr);
                    }
                    catch (...)
                    {
                        audio["sample_rate"] = 0;
                    }
                    if (secondSlash != std::string::npos)
                    {
                        std::string chStr = rest.substr(secondSlash + 1);
                        while (!chStr.empty() && (chStr.back() == '\r' || chStr.back() == ' '))
                        {
                            chStr.pop_back();
                        }
                        try
                        {
                            audio["channels"] = std::stoi(chStr);
                        }
                        catch (...)
                        {
                            audio["channels"] = 1;
                        }
                    }
                    return audio;
                }
            }
            return audio;
        }
    };
} // nv_vms

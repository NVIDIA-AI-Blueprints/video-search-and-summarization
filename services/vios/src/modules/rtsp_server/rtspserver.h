/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

using namespace std;
namespace nv_vms {

typedef struct _StreamDetails
{
    string id;
    string name;
    string sensorUrl;
    string sensorName;
    string proxyUrl;
    string vodUrl;
    string codec;
    string resolution;
    string framerate;
    string tags;
} StreamDetails;
class RtspServer
{
    public:
        RtspServer(u_int16_t port);
        ~RtspServer();
        int16_t getPort() { return m_rtspServerPortNum; }
        std::string createProxy(const string& id, const string& name, const string& url);
        bool deleteProxy(const string& id);
        int addProxy(const string& id, const string& name, string& url);
        int removeProxy(const string& id);
        void addStream(const string& streamId, const string& url);
        std::string urlPrefix() { return m_urlPrefix; }
        string originalPrefix();
        unsigned int activeClientSessions();
        vector<string> getActiveStreams();
        ServerMediaSession* serverMediaSessionForStream(const string& id);
        int removeServerMediaSession(const string& id);
        UsageEnvironment& getEnv() { return m_env; }
        void updateUser(const char *username);
        void addUser(const char *username, const char *passwordHash);
        void removeUser(const char *username);
        vector<StreamDetails> streamList();
        bool findStreamId(const string& id);
        string getRtspServerDomainPrefix() { return m_rtspServerDomainPrefix; }
        bool isError() { return m_isError; }
        void setVodServer(bool isVodServer);
        map<string, StreamDetails, std::less<>> getStreamList() { return m_streamsList; }
        INotificationInterface* getNotifier() { return m_notifier; }
        void updateStreamMetadata(const string& id, const string& vodUrl,
                                  const string& codec, const string& resolution,
                                  const string& framerate, const string& tags);

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
        void registerStreamAsync(const string& id, const string& name,
                                 const string& proxyUrl,
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
        std::map<string, string, std::less<>> m_liveCameraStreamList;
        std::mutex               m_streamLock;
        TaskToken m_eventAddStream;
        TaskToken m_eventRemoveStream;
        std::string m_urlPrefix;
        ServerMediaSession* m_sms = nullptr;
        UserAuthenticationDatabase* m_authDB = nullptr;
        SyncObject m_sync;
        map<string, StreamDetails, std::less<>> m_streamsList;
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
            return new AppProxyServerMediaSession(rtspServer, env, ourMediaServer, inputStreamURL,
                                                streamName, username, password,
                                                tunnelOverHTTPPortNum, verbosityLevel,
                                                initialPortNum, multiplexRTCPWithRTP,
                                                socketNumToServer, transcodingTable);
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
                LOG(warning) << "SDP is ready url:" << secureUrlForLogging(urlPtr) << ", streamName:" << streamName() << endl;
            }
            else
            {
                LOG(warning) << "SDP is ready url: <null>, streamName:" << streamName() << endl;
            }

            // Access codec information through SDP description
            vector<string> detectedVideoCodecs;
            Json::Value detectedAudioInfo;
            Json::Value videoParameterSets(Json::arrayValue);
            detectedAudioInfo["present"] = false;
            char* sdpDesc = generateSDPDescription(AF_INET);
            if (sdpDesc)
            {
                // Parse SDP to extract video codec information
                detectedVideoCodecs = parseVideoCodecsFromSDP(sdpDesc);
                /* SPS/PPS carry the resolution and framerate of the stream;
                 * registerStreamAsync decodes them off this thread. */
                videoParameterSets = parseVideoParameterSetsFromSDP(sdpDesc);
                for (const auto& codec : detectedVideoCodecs)
                {
                    LOG(warning) << "Detected video codec from SDP: " << codec << endl;
                }
                /* Also pull audio info from the same SDP buffer. This is the
                 * earliest hook we have for audio detection -- it runs at
                 * proxy DESCRIBE-response time, before any RTSP client
                 * connects and well before the recorder is started. */
                detectedAudioInfo = parseAudioInfoFromSDP(sdpDesc);
                if (detectedAudioInfo.get("present", false).asBool())
                {
                    LOG(warning) << "Detected audio from SDP: codec="
                                 << detectedAudioInfo.get("codec", "").asString()
                                 << ", sample_rate=" << detectedAudioInfo.get("sample_rate", 0).asInt()
                                 << ", channels=" << detectedAudioInfo.get("channels", 0).asInt()
                                 << endl;
                    /* Normalize codec name for storage. The download API and
                     * other consumers look for "AAC" specifically; the SDP
                     * RTP payload format is "MPEG4-GENERIC". Map it here in
                     * one place so all downstream consumers stay consistent. */
                    string sdpAudioCodec = detectedAudioInfo.get("codec", "").asString();
                    string normalizedCodec = sdpAudioCodec;
                    string codecLower      = sdpAudioCodec;
                    std::transform(codecLower.begin(), codecLower.end(),
                                   codecLower.begin(),
                                   [](unsigned char c) { return (char)std::tolower(c); });
                    if (codecLower == "mpeg4-generic")
                    {
                        normalizedCodec = "AAC";
                    }
                    detectedAudioInfo["encoding"] = normalizedCodec;
                }
                delete[] sdpDesc; // Important: free the memory as per Live555 documentation
            }


            string proxyStreamName = streamName();
            map<string, StreamDetails, std::less<>> streamsList = m_rtspServer->getStreamList();
            for (auto stream : streamsList)
            {
                StreamDetails streamInfo = stream.second;
                if (streamInfo.name == proxyStreamName)
                {
                    string live_proxy_url = vst_common::toDomainName(streamInfo.proxyUrl, streamInfo.id);
                    string vod_url = vst_rtsp::vodServerDomainPrefix(streamInfo.id) + string("vod/") + streamInfo.id;

                    string sdpDetectedCodec;
                    if (!detectedVideoCodecs.empty())
                    {
                        sdpDetectedCodec = detectedVideoCodecs[0];
                    }
                    string asyncCodec = sdpDetectedCodec.empty() ? streamInfo.codec : sdpDetectedCodec;
                    string asyncVodUrl = streamInfo.vodUrl.empty() ? vod_url : streamInfo.vodUrl;

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
                LOG(warning) << "SDP reset for url:" << secureUrlForLogging(m_savedUrl.c_str()) << endl;
            }
            else
            {
                const char* urlPtr = url();
                if (urlPtr != nullptr)
                {
                    LOG(warning) << "SDP reset for url:" << secureUrlForLogging(urlPtr) << endl;
                }
                else
                {
                    LOG(warning) << "SDP reset for url: <null>" << endl;
                }
            }
        }

    private:
        RtspServer *m_rtspServer;
        std::string m_savedUrl;  // Store URL for use in sdpReset when url() may be null

        // Helper function to parse video codecs from SDP description (ignores audio)
        vector<string> parseVideoCodecsFromSDP(const char* sdp)
        {
            vector<string> videoCodecs;
            if (!sdp) return videoCodecs;

            string sdpStr(sdp);
            istringstream stream(sdpStr);
            string line;
            bool inVideoSection = false;

            while (getline(stream, line))
            {
                // Track media sections: m=<media> <port> <proto> <payload_types>
                if (line.find("m=") == 0)
                {
                    if (line.find("m=video") == 0)
                    {
                        inVideoSection = true;
                    }
                    else if (line.find("m=audio") == 0)
                    {
                        inVideoSection = false;
                    }
                    else
                    {
                        inVideoSection = false; // Reset for other media types
                    }
                }
                // Only process rtpmap lines when in video section
                else if (inVideoSection && line.find("a=rtpmap:") == 0)
                {
                    size_t spacePos = line.find(' ');
                    if (spacePos != string::npos)
                    {
                        string codecInfo = line.substr(spacePos + 1);
                        size_t slashPos = codecInfo.find('/');
                        if (slashPos != string::npos)
                        {
                            string codecName = codecInfo.substr(0, slashPos);
                            videoCodecs.push_back(codecName);
                        }
                    }
                }
                // Only process format parameters when in video section
                else if (inVideoSection && line.find("a=fmtp:") == 0)
                {
                    // Extract profile information for H.264/H.265
                    if (line.find("profile-level-id") != string::npos)
                    {
                        size_t profilePos = line.find("profile-level-id=");
                        if (profilePos != string::npos)
                        {
                            string profile = line.substr(profilePos + 17, 6); // Extract 6-char profile
                        }
                    }
                }
            }

            return videoCodecs;
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

            string sdpStr(sdp);
            istringstream stream(sdpStr);
            string line;
            bool inAudioSection = false;

            while (getline(stream, line))
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
                    if (spacePos == string::npos) continue;
                    string rtpmapVal = line.substr(spacePos + 1);
                    size_t firstSlash = rtpmapVal.find('/');
                    if (firstSlash == string::npos) continue;

                    audio["present"] = true;
                    audio["codec"]   = rtpmapVal.substr(0, firstSlash);

                    string rest = rtpmapVal.substr(firstSlash + 1);
                    size_t secondSlash = rest.find('/');
                    string rateStr     = (secondSlash == string::npos) ? rest
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
                    if (secondSlash != string::npos)
                    {
                        string chStr = rest.substr(secondSlash + 1);
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

        /* Helper function to pull the video parameter sets out of the SDP.
         *
         * They are returned base64-encoded exactly as they appear in the
         * a=fmtp line of the video m-section, in bitstream order: H.264 packs
         * them into a comma-separated "sprop-parameter-sets=<SPS>,<PPS>",
         * H.265 splits them across "sprop-vps=", "sprop-sps=", "sprop-pps=".
         *
         * Decoding them and querying the resulting caps is deliberately left
         * to the caller: this runs on the live555 event-loop thread, which
         * must not block. */
        Json::Value parseVideoParameterSetsFromSDP(const char* sdp)
        {
            Json::Value parameterSets(Json::arrayValue);
            if (!sdp) return parameterSets;

            /* Value of "<key>" within an a=fmtp parameter list, terminated by
             * the next parameter separator or end of line. */
            auto attributeValue = [](const string& line, const string& key) -> string {
                size_t keyPos = line.find(key);
                if (keyPos == string::npos) return "";
                string value = line.substr(keyPos + key.length());
                size_t endPos = value.find_first_of(" ;\r\n");
                if (endPos != string::npos)
                {
                    value.erase(endPos);
                }
                return value;
            };

            string sdpStr(sdp);
            istringstream stream(sdpStr);
            string line;
            bool inVideoSection = false;

            while (getline(stream, line))
            {
                if (line.find("m=") == 0)
                {
                    inVideoSection = (line.find("m=video") == 0);
                    continue;
                }
                if (!inVideoSection || line.find("a=fmtp:") != 0) continue;

                /* H.265 first: its keys are distinct substrings, so checking
                 * them ahead of the H.264 form avoids any ambiguity. */
                for (const char* const key : {"sprop-vps=", "sprop-sps=", "sprop-pps="})
                {
                    string value = attributeValue(line, key);
                    if (!value.empty())
                    {
                        parameterSets.append(value);
                    }
                }
                if (parameterSets.empty() == false) break;

                string spropSets = attributeValue(line, "sprop-parameter-sets=");
                if (spropSets.empty()) continue;
                for (const string& value : splitString(spropSets, ","))
                {
                    if (!value.empty())
                    {
                        parameterSets.append(value);
                    }
                }
                break;
            }

            return parameterSets;
        }
    };
} // nv_vms
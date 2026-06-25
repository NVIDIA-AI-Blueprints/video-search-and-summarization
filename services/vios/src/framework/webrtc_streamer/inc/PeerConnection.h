/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "PeerConnectionManager.h"
#include "rtc_base/thread.h"
#include "WebrtcCallbacks.h"
#include <jsoncpp/json/json.h>
#include "syncobject.h"
#include "WebrtcStreamStats.h"
#include "IWebrtcConnection.h"
#include "database.h"

#include <memory>

inline constexpr const char* DECODER_FACTORY_PASS_THROUGH = "decoder_factory_pass_through";

class PeerConnection : public IWebrtcConnection
{
public:
    PeerConnection(PeerConnectionManager* peerConnectionManager, const std::string& peerid,
                    const webrtc::PeerConnectionInterface::RTCConfiguration & config,
                    std::unordered_map<std::string, std::string>& opts);

    virtual ~PeerConnection();

    VmsErrorCode post(const string& task_name, const string& peerid,
                    Json::Value in, Json::Value req_info, Json::Value& response,
                    bool is_sync = true, uint32_t timeout = 0) override;

    string getPublicAddress() { return m_observer->getPublicAddress(); }
    void getIceCandidateList(Json::Value& out) { m_observer->getIceCandidateList(out); }
    void getStats(Json::Value& content);
    void isIceCandidateAdded();
    rtc::scoped_refptr<webrtc::PeerConnectionInterface> getRtcPeerConnection() { return m_pc; };
    bool isRpStunAvailable() { return m_peerConnectionManager->isRpStunAvailable(); }
    string getRpStunServer() { return m_peerConnectionManager->getRpStunServer(); }
    pair<string, int> getRpSeat() { return m_rpSeat; }
    void setRpSeat(pair<string, int> seat) { m_rpSeat = seat; }
    int getLocalPort() { return m_localPort; }
    void setLocalPort(const int& port) { m_localPort = port; }

    void closePeerConnection();

    void setDeviceId(string deviceId)
    {
        m_deviceId = deviceId;
        m_observer->setDeviceId(m_deviceId);
    }
    string getDeviceId()
    {
        return m_deviceId;
    }
    void isClientMode(bool is_client)
    {
        m_isClient = is_client;
    }
    int notify(const string& change, const string& deviceId,  int width = 0, int height = 0)
    {
        return m_peerConnectionManager->notify(change, deviceId, width, height);
    }
    string getPcType()
    {
        return m_peerConnectionManager->m_pcType;
    }
    string getWsConnectionId()
    {
        return m_wsConnectionId;
    }
    void setWsConnectionId(std::string wsConnectionId)
    {
        m_wsConnectionId = wsConnectionId;
    }

    void startPlayback(const std::string &peerid);
    void deleteSeatFromRP(const string& peerId);
    void setDeviceName(const string& deviceId);
protected:
    int CreateAndAddTrack(string video, std::map<string, string, std::less<>>& opts
                        , bool is_audio_required, string streamLabel
                        , shared_ptr<StreamInfo> stream_info, Json::Value& response);
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> CreateVideoSource(
        const std::string & videourl, const std::map<std::string,std::string, std::less<>> & opts);
    rtc::scoped_refptr<webrtc::AudioSourceInterface> CreateAudioSource(
        const std::string & audiourl, const std::map<std::string,std::string, std::less<>> & opts);
public:
    void removeTracks(const Json::Value &);
    VmsErrorCode controlStream(const std::string action, const std::string &peerid,
                                const Json::Value &, Json::Value&);
    VmsErrorCode getCurrentPosition(const std::string &peerId, Json::Value&, Json::Value&);

    friend void process_pc_message(std::shared_ptr<EventLoopData> data, void* parent);
    VmsErrorCode postToEventLoop(const string& task_name, const string& peerid,
                            Json::Value in, Json::Value req_info,
                            Json::Value& response, bool is_sync = true, uint32_t timeout = 0);
    std::string getNwInterface();
    std::string getClientPublicIpAddr() { return m_clientPublicIpAddr; }
    void setClientPublicIpAddr(const string& publicIp ) { m_clientPublicIpAddr = publicIp; }
    const pair<string, int> getAvailableSeatFromRP(const string& sessionId, const string& remote_ipAddr,
        const string& private_ip, const string& private_port);

private:
    VmsErrorCode call(const Json::Value& req_info, const Json::Value& jmessage, Json::Value&);
    VmsErrorCode startConnection(const Json::Value& req_info, const Json::Value& jmessage, Json::Value&);
    VmsErrorCode AddStreams(unordered_map<string, string> urlParameters,
                            std::map<std::string, std::string, std::less<>>& opts, Json::Value& offer);
    std::string addWebrtcBitrateToSDP(const Json::Value& in, const std::string& sdp);
    VmsErrorCode AddCompositorStreams(
                                    unordered_map<string, string> urlParameters,
                                    std::map<std::string, std::string, std::less<>>& opts,
                                    Json::Value& response, vector<string> &list_sensorids);
    VmsErrorCode toggleStream(const Json::Value& req_info, const Json::Value& in, Json::Value&);
    VmsErrorCode getQuery(const Json::Value& req_info, const Json::Value& in, Json::Value&);
    VmsErrorCode getMetadataLastFrame(const std::string &mediaSessionId, Json::Value&, const bool);
    VmsErrorCode getStartTime(const std::string &mediaSessionId, Json::Value& response);
    VmsErrorCode getDurationStream(const std::string &mediaSessionId, Json::Value& response);
    VmsErrorCode getStatus(const string peerId, const string mediaSessionId,
                            const string overlay, Json::Value& response);
    VmsErrorCode addIceCandidate(const std::string &peerid, const Json::Value& jmessage, Json::Value&);
    VmsErrorCode getPeerConnectionList(Json::Value&);
    VmsErrorCode getStreamList(Json::Value&);
    VmsErrorCode setAnswer(const Json::Value& in, Json::Value&);
    VmsErrorCode setOffer(const Json::Value& in, Json::Value&);
    VmsErrorCode getAnswer(const Json::Value& in, Json::Value&);
    void setAudioPlayout(bool value);
    bool removeAudioTrack(const std::string peerid);
    bool isRemoteDescriptionSet();
    void processRemoteCandidatesFromCache();
    VmsErrorCode addAudioTrack(rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource,
                rtc::scoped_refptr<webrtc::AudioSourceInterface> audioSource, std::string peerid,
                std::string video, std::map<std::string, std::string, std::less<>> opts, Json::Value&);
    VmsErrorCode addUdpTrack(const string sensorId, const string stream_id);
    VmsErrorCode createOffer(const Json::Value& in, Json::Value& offer);
    VmsErrorCode getAudioVideoPair(std::map<std::string, AudioVideoPair, std::less<>>::iterator& it
                                , const std::string &mediaSessionId, Json::Value& response);
    VmsErrorCode getMediaSources(const std::string &mediaSessionId,
                                rtc::scoped_refptr<webrtc::VideoTrackSourceInterface>& videoSource,
                                rtc::scoped_refptr<webrtc::AudioSourceInterface>& audioSource,
                                Json::Value& response);
    VmsErrorCode streamSettings(const std::string &peerId, const Json::Value &data,
                                Json::Value& response);
private:
    std::unique_ptr<rtc::Thread>                             m_signalingThread;
    std::unique_ptr<rtc::Thread>                             m_workerThread;
    std::unique_ptr<vst_webrtc::PeerConnectionObserver>      m_observer;
    PeerConnectionManager*                                   m_peerConnectionManager;
    const std::string                                        m_peerid;
    std::string                                              m_deviceId;
    std::string                                              m_thirdPartyPeerId;
    std::string                                              m_clientPublicIpAddr;
    rtc::scoped_refptr<webrtc::PeerConnectionInterface>      m_pc;
    rtc::scoped_refptr<vst_webrtc::PeerConnectionStatsCollectorCallback> m_statsCallback;
    std::unique_ptr<vst_webrtc::VideoSink>                   m_videosink;
    std::unique_ptr<vst_webrtc::AudioSink>                   m_audiosink;
    std::atomic<bool>                                        m_deleting{false};
    std::shared_ptr<WebrtcStream>                            m_producer;
    std::unique_ptr<Bosma::Scheduler>                        m_webrtcInputDataWatchDog;
    rtc::scoped_refptr<webrtc::PeerConnectionFactoryInterface> m_peer_connection_factory;
    const std::regex                                         m_publishFilter;
    rtc::scoped_refptr<webrtc::AudioDecoderFactory>          m_audioDecoderfactory;
    rtc::scoped_refptr<webrtc::AudioDeviceModule>            m_audioDeviceModule;
    std::map<std::string, AudioVideoPair, std::less<>>                    m_streamMap;
    EventLoop                                                m_eventLoop;
    std::shared_ptr<nv_vms::DeviceManager>                   m_deviceManager;
    std::atomic<uint64_t>                                    m_prevTimestamp;
    std::atomic<uint64_t>                                    m_prevBytesReceived;
    std::vector<std::unique_ptr<webrtc::IceCandidateInterface>> m_earlyRemoteCandidates;
    std::mutex                                               m_earlyRemoteCandidatesMutex;
    pair<string, int>                                        m_rpSeat;
    int                                                      m_localPort;
public:
    std::atomic<bool>                                        m_isIceConnected{false};
    std::atomic<bool>                                        m_isClient{false};
    WebrtcStreamStats                                        m_streamStats;
    std::string                                              m_wsConnectionId{""};
};
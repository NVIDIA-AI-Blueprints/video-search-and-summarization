/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string>
#include <mutex>
#include <regex>
#include <thread>
#include <future>
#include <chrono>

#include "api/peer_connection_interface.h"

#include "modules/audio_device/include/audio_device.h"
#ifndef HAVE_SOUND
#include "modules/audio_device/include/fake_audio_device.h"
#endif

#include "rtc_base/logging.h"
#include "rtc_base/strings/json.h"

#include "HttpServerRequestHandler.h"
#include "logger.h"
#include "event_loop.h"
#include "webrtcstreamproducer.h"
#include "stats.h"
#include "webrtcDataChannel.h"
#include "fps_display.h"
#include "stream_monitor.h"
#include "vstmodule.h"

// #define ASYNC_API
constexpr int PEER_CONNECTION_TIMEOUT_THREAD_COUNT = 1;
constexpr const char* WEBRTC_PREFIX = "webrtc_";
constexpr std::chrono::seconds WEBRTC_INPUT_DATA_WATCH_DOG_SCHEDULER_INTERVAL{12};
constexpr int WEBRTC_INPUT_FPS_CAPTURE_INTERVAL_SEC = 2;
constexpr int WEBRTC_INPUT_FPS_PUBLISH_INTERVAL_SEC = 20;
constexpr int STANDARD_BITRATE_720P_KBPS = 3000;
constexpr const char* PASS_THROUGH_QUALITY = "pass_through";

typedef std::pair< webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface>, webrtc::scoped_refptr<webrtc::AudioSourceInterface>> AudioVideoPair;

struct ClientInfo
{
    std::string m_ipAddress;
    std::string m_deviceId;
    std::string m_streamId;
};

class IWebrtcConnection;
class PeerConnection;
class PeerConnectionManager : public nv_vms::IStreamStatusEvent, public IVstModule
{
    public:
        IVstModule* createPeerConnectionManagerObject();
        void deletePeerConnectionManagerObject( IVstModule* object );

        PeerConnectionManager(std::string pcType, const webrtc::AudioDeviceModule::AudioLayer audioLayer,
            const std::string& publishFilter, std::shared_ptr<nv_vms::DeviceManager> deviceManager, bool monitor=false);
        virtual ~PeerConnectionManager();

        nv_vms::VmsErrorCode startStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
        nv_vms::VmsErrorCode stopStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
        nv_vms::VmsErrorCode switchStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response);

        virtual void onStreamStatusChange(const string &url, const nv_vms::StreamStatus newStatus, nv_vms::StreamEncParam& details);
        int notify(const string& change, const string& deviceId, int width = 0, int height = 0);
        void InitializePeerConnection();
        void DestroyPeerConnections();
        const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() { return m_func; };

        nv_vms::VmsErrorCode getIceCandidateList(const std::string &peerid, Json::Value&);
        nv_vms::VmsErrorCode addIceCandidate(const std::string &peerid, const Json::Value& in, Json::Value&);
        nv_vms::VmsErrorCode getVideoDeviceList(Json::Value&);
        nv_vms::VmsErrorCode getAudioDeviceList(Json::Value&);
        nv_vms::VmsErrorCode hangUp(const std::string peerid, const Json::Value &, Json::Value&);
        nv_vms::VmsErrorCode call(const Json::Value& req_info, const Json::Value& jmessage, Json::Value&);
        nv_vms::VmsErrorCode toggleStream(const Json::Value& req_info, const Json::Value& jmessage, Json::Value&);
        nv_vms::VmsErrorCode checkDeviceSanity (shared_ptr<nv_vms::SensorInfo> sensor, Json::Value&);
        nv_vms::VmsErrorCode checkStreamSanity (shared_ptr<nv_vms::StreamInfo> stream, std::string start_time, Json::Value&);
        nv_vms::VmsErrorCode getIceServers(const std::string& peerid, const std::string& clientIp, Json::Value&);
        nv_vms::VmsErrorCode getPeerConnectionList(Json::Value&);
        nv_vms::VmsErrorCode getStreamList(Json::Value&);
        nv_vms::VmsErrorCode createOffer(unordered_map<string, string> urlParameters, std::map<std::string, std::string, std::less<>>& opts, Json::Value&);
        nv_vms::VmsErrorCode setAnswer(const std::string &peerid, const Json::Value& in, Json::Value&);
        nv_vms::VmsErrorCode getStreamStats(std::string &peerid, Json::Value&, string deviceid = "");
        nv_vms::VmsErrorCode controlStream(const std::string action, const std::string &peerid, const Json::Value &, Json::Value&);
        nv_vms::VmsErrorCode getCurrentPosition(const std::string &peerId, const std::string &mediaSessionId, Json::Value& value);
        nv_vms::VmsErrorCode getStreamStatus(const std::string &peerId, const std::string &overlay, const Json::Value& req_info, const Json::Value& in, Json::Value& response);
        nv_vms::VmsErrorCode getStreamOverlayStatus(const std::string &peerId, Json::Value& response);
        const Json::Value getTimeStampMap();
        nv_vms::VmsErrorCode checkPeerConnectionErrors(const string& peerid, const Json::Value &in, Json::Value& response);
        nv_vms::VmsErrorCode AddTrackToExistingPeerConnection(const string peerid, const string stream_id);
        nv_vms::VmsErrorCode getQuery(const Json::Value& req_info, const Json::Value& in, Json::Value&);
        nv_vms::VmsErrorCode udpToWebrtc(const string sensorId, const string streamId);
        nv_vms::VmsErrorCode setWebrtcClientParams(const string streamId, const Json::Value& in);
        nv_vms::VmsErrorCode startUdpToWebrtcConnection(const Json::Value& req_info, const Json::Value& jmessage, Json::Value&);
        nv_vms::VmsErrorCode streamSettings(const Json::Value& req_info, const Json::Value &in, Json::Value &value);
        nv_vms::VmsErrorCode SendRemotePeerOffer(const string sensorId, const string streamId, const Json::Value& in);
        nv_vms::VmsErrorCode SendRemotePeerIceCandidate(const string sensorId, const string streamId, const Json::Value& in);
        void resolveAndValidateRP();
        string getMyPublicAddress(shared_ptr<PeerConnection>& peerConnection);
        std::shared_ptr<nv_vms::DeviceManager> getDeviceManager ()  { return m_deviceManager; }
        void checkAndAddSensorToRemote(const string& streamId);
        void parseRemoteAnswer(const Json::Value& in, Json::Value& response);
        nv_vms::VmsErrorCode onWsDisconnect(const string connectionId);

        /* RP-STUN is sommon and should be available to all live/replay/streambridge services */
        const pair<string, int> getAvailableSeatFromRP(const string& session_id,
                    const string& remote_ipAddr, const string& private_ip, const string& private_port);
        static void updateRpStunServer(const Json::Value& in);
        static bool isRpStunAvailable() { return !m_rpStunServer.empty(); }
        static std::string getRpStunServer();
        pair<string, int> getRpStunBinding(int& local_port);
        bool ClientSearch(const std::string& peerid, ClientInfo& client);
        void ClientInsert(const std::string& peerid, const ClientInfo& client);
        void ClientErase(const std::string& peerid);
        std::vector<std::pair<std::string, ClientInfo>> GetAllClients();

#ifdef ASYNC_API
        friend void process_peer_message(std::shared_ptr<EventLoopData> data, PeerConnectionManager* peer);
        nv_vms::VmsErrorCode postToEventLoop(const string& task_name, const string& peerid,
                                    Json::Value in, Json::Value req_info,
                                    Json::Value& response, bool is_async = true, uint32_t timeout = 0);
#endif
    protected:
        shared_ptr<IWebrtcConnection>                         CreatePeerConnection(std::unordered_map<std::string, std::string>& opts);
        bool                                                  streamStillUsed(const std::string & streamLabel);
        const std::list<std::string>                          getVideoCaptureDeviceList();
        webrtc::scoped_refptr<webrtc::PeerConnectionInterface>   getRtcPeerConnection(const std::string& peerid);
        const std::string                                     sanitizeLabel(const std::string &label);
        bool                                                  isWebrtcOutLimitCrossed();
        bool                                                  isWebrtcInLimitCrossed();
        bool                                                  isPeerIdUnique(const std::string peerid);
    private:
        nv_vms::VmsErrorCode getOverlayStatus(const string peerId, Json::Value& response);
        nv_vms::VmsErrorCode checkParamErrors(const Json::Value& req_info, const Json::Value& in, Json::Value& response);
        std::vector<string> getIceServersInternal(const std::string &peerId);
        std::vector<string> getIceServersForSpecificPeer(const std::string &peerId);
        std::string getAuthTokenForClient(const std::string& coturn_auth_secret, const std::string &peerId);
        nv_vms::VmsErrorCode createWebrtcDevice(const Json::Value &in, const string peerid, const string username, shared_ptr<nv_vms::SensorInfo> existingSensor);
        void addStreamToSensor(const Json::Value &in, shared_ptr<nv_vms::SensorInfo> sensor);
        int addUdpSubStreamToSensor(shared_ptr<nv_vms::SensorInfo> existed_sensor);
        nv_vms::VmsErrorCode addStreamToRecorder(shared_ptr<nv_vms::SensorInfo> sensor);
        shared_ptr<IWebrtcConnection> getPeerConnection(const std::string &peerid);
        shared_ptr<IWebrtcConnection> getOutPeerConnection(const std::string &peerid);
        shared_ptr<IWebrtcConnection> getInPeerConnection(const std::string &peerid);
        void insertPeerConnection(const std::string &peerid, shared_ptr<IWebrtcConnection> pc, bool webrtcIn = false);
        void erasePeerConnection(const std::string &peerid);
        int startPeerConnectionForRemoteDevice(shared_ptr<nv_vms::StreamInfo> stream_info);
        bool isStreamExists(const std::string &stream_id);
        void peerConnectionMonitorTask();
        bool checkIfRemoteReachable();
        int startDataChannel();
        void syncSensorStatus();
        Json::Value getLocalIceCandidates(shared_ptr<PeerConnection>& peerConnection,
                    const Json::Value& in, const Json::Value& req, bool expectRelayCandidates = false);
        void onWebrtcDataChannelConnection();
        void generateRpCandidate(const string& streamId, Json::Value iceCandidate);
    public:
        std::string                                                     m_pcType {""};
    private:
        std::unique_ptr<webrtc::TaskQueueFactory>                       m_taskQueueFactory;
#ifndef HAVE_SOUND
        std::unique_ptr<webrtc::FakeAudioDeviceModule>                  m_fakeAudioDeviceModule;
#endif
        webrtc::scoped_refptr<webrtc::AudioDeviceModule>                   m_audioDeviceModule;
#ifndef ASYNC_API
        std::mutex                                                      m_peerMapMutex;
        std::mutex                                                      m_peerInConnMapMutex;
        std::mutex                                                      m_streamMapMutex;
        std::mutex                                                      m_clientMutex;
#endif
        std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >           m_peerConnectionMap;
        std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<>>            m_peerInputConnections;
        std::map<std::string, AudioVideoPair, std::less<>>                           m_stream_map;
        const std::regex                                                m_publishFilter;
        std::map<std::string, std::vector<string>, std::less<>>                      m_iceServerPeerIdMap;
        std::mutex                                                      m_iceServerPeerIdMapLock;
        unsigned int                                                    m_turnserverIndex {0};
        static std::string                                              m_rpStunServer;
        static std::mutex                                               m_rpStunServerLock;
        Json::Value                                                     m_externalPeerInfo;
        std::shared_ptr<nv_vms::DeviceManager>                          m_deviceManager;
        std::thread                                                     m_peerConnMonitoringThread;
        std::atomic<bool>                                               m_exitPeerConnThread;
        std::mutex                                                      m_peerConnThreadMutex;
        std::condition_variable                                         m_cvPeerConnThread;
        std::mutex                                                      m_remoteRetryListLock;
        std::vector<std::pair<std::string, struct timeval>>             m_remoteRetryStreams;
        unsigned int                                                    m_minPort = 0;
        unsigned int                                                    m_maxPort = 0;
#ifdef ASYNC_API
        EventLoop                                                       m_eventLoop;
#endif
        std::map<std::string, ClientInfo, std::less<>>                   m_clientMap;
};

inline PeerConnectionManager* GET_PEERCONNECTION_MNGR()
{
    return static_cast<PeerConnectionManager*>(ModuleLoader::getInstance()->getPeerConnectionManager());
}

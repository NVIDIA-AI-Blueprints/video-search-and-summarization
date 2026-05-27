/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <chrono>
#include <iostream>
#include <fstream>
#include <utility>
#include <functional>
#include <math.h>

#include "rtc_base/ssl_adapter.h"
#include "rtc_base/thread.h"
#include "openssl/hmac.h"
#include "api/rtc_event_log/rtc_event_log_factory.h"
#include "api/task_queue/default_task_queue_factory.h"
#include "pc/session_description.h"
#include "p2p/base/transport_info.h"
#include "p2p/client/basic_port_allocator.h"
#include "rtc_base/socket_address.h"
#include "media/engine/webrtc_media_engine.h"
#include "modules/audio_device/include/fake_audio_device.h"
#include "stream_event_manager.h"

#include "rtspserver.h"

#include "PeerConnectionManager.h"
#include "V4l2AlsaMap.h"
#include "CapturerFactory.h"
#include "prometheus_client/prometheus_client.h"

#include "VideoScaler.h"
#include "VideoFilter.h"
#include "utils.h"
#include "network_utils.h"
#include "logger.h"
#include "device_manager.h"
#include "stats.h"
#include "error_code.h"
#include "config.h"
#include "profiler.h"
#include "NotificationFactory.h"
#include "Websocket.h"
#include "gst_utils.h"
#include "modules_apis.h"
#include "streamrecorder.h"
#include "PeerConnection.h"
#include "vst_common.h"
#include "udpclientpool.h"

#define TRY_CATCH_STOF(lval, rval, _str) try { lval[_str] = Json::Value(std::stof(rval)); } catch(const std::exception& e) {std::cerr << e.what() << '\n';}
#define TRY_CATCH_STOI(lval, rval, _str) try { lval[_str] = Json::Value(std::stoi(rval)); } catch(const std::exception& e) {std::cerr << e.what() << '\n';}

using namespace std;
using namespace nv_vms;

constexpr auto START_STREAMING_TIMEOUT = std::chrono::seconds{60};
constexpr const char* CAM_DEFAULT_PREFIX = "CAMERA_";

constexpr const char* WEBRTC_VENDOR_RAGNAROK = "ragnarok";
constexpr const char* WEBRTC_VENDOR_GOOGLE = "google";

constexpr const char* TURN_SERVER_SECRET_KEY = "rC6kx4q4I4Vc9ek4JO3yWI2DxKOvBo7ciGLmTIF7z4RvI9xPDDn8linXo6IVBGcw";
constexpr const char* TURN_SERVER_DEFAULT_USER_NAME = "vst_turn_user";
constexpr int AUTH_TOKEN_EXPIRATON_TIME_SEC = 5 * 60;
constexpr int REMOTE_PEER_CONNECTION_RETRY_INTERVAL = 10;

// Avatar stream related macros
constexpr const char* DEFAULT_UDP_DEVICE_NAME = "Tokkio_Avatar";


constexpr int DEFAULT_SAMPLE_RATE = 16000;
constexpr int DEFAULT_CHANNELS = 1;
constexpr const char* DEFAULT_AUDIO_CODEC_PCM = "pcm";
constexpr int DEFAULT_BITS_PER_SAMPLE = 32;

constexpr const char* DEFAULT_WIDTH = "1920";
constexpr const char* DEFAULT_HEIGHT = "1080";

#define CHECK_PEER_ERROR(obs , response)                                            \
    if (!obs.get())                                                                 \
    {                                                                               \
        string msg = "Peer not found";                                              \
        LOG(error) << msg << endl;                                                  \
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, msg.c_str())  \
        return VmsErrorCode::InvalidParameterError;                                 \
    }

// Names used for a IceCandidate JSON object.
const char kCandidateSdpMidName[] = "sdpMid";
const char kCandidateSdpMlineIndexName[] = "sdpMLineIndex";
const char kCandidateSdpName[] = "candidate";

// Names used for a SessionDescription JSON object.
const char kSessionDescriptionTypeName[] = "type";
const char kSessionDescriptionSdpName[] = "sdp";

std::string PeerConnectionManager::m_rpStunServer;
std::mutex PeerConnectionManager::m_rpStunServerLock;

extern "C" void* createPeerConnectionManagerObject()
{
    std::string publishFilter(".*");
    webrtc::AudioDeviceModule::AudioLayer audioLayer = webrtc::AudioDeviceModule::kPlatformDefaultAudio;
    return static_cast<void*>(static_cast<IVstModule*>(
        new PeerConnectionManager("", audioLayer, publishFilter, ModuleLoader::getInstance()->getDeviceManagerObject())));
}

extern "C" void deletePeerConnectionManagerObject(IVstModule* object)
{
    PeerConnectionManager* pcm = static_cast<PeerConnectionManager*>(object);
    delete pcm;
}

// character to remove from url to make webrtc label
bool ignoreInLabel(char c)
{
    return c == ' ' || c == ':' || c == '.' || c == '/' || c == '&';
}

/* ---------------------------------------------------------------------------
**  helpers that should be moved somewhere else
** -------------------------------------------------------------------------*/

#ifdef WIN32
std::string getServerIpFromClientIp(int clientip)
{
    return "127.0.0.1";
}
#else
#include <net/if.h>
#include <ifaddrs.h>
std::string getServerIpFromClientIp(int clientip)
{
    std::string serverAddress;
    char host[NI_MAXHOST];
    struct ifaddrs *ifaddr = nullptr;
    if (getifaddrs(&ifaddr) == 0)
    {
        for (struct ifaddrs *ifa = ifaddr; ifa != nullptr; ifa = ifa->ifa_next)
        {
            if ((ifa->ifa_netmask != nullptr) && (ifa->ifa_netmask->sa_family == AF_INET) && (ifa->ifa_addr != nullptr) && (ifa->ifa_addr->sa_family == AF_INET))
            {
                struct sockaddr_in *addr = (struct sockaddr_in *)ifa->ifa_addr;
                struct sockaddr_in *mask = (struct sockaddr_in *)ifa->ifa_netmask;
                if ((addr->sin_addr.s_addr & mask->sin_addr.s_addr) == (clientip & mask->sin_addr.s_addr))
                {
                    if (getnameinfo(ifa->ifa_addr, sizeof(struct sockaddr_in), host, sizeof(host), nullptr, 0, NI_NUMERICHOST) == 0)
                    {
                        serverAddress = host;
                        break;
                    }
                }
            }
        }
    }
    freeifaddrs(ifaddr);
    return serverAddress;
}
#endif

struct PeerData : public EventLoopData
{
    Json::Value m_queryParams;
    Json::Value m_dataParams;
};

struct PeerOutData : public EventLoopOutData
{
    Json::Value m_response;
    nv_vms::VmsErrorCode m_error;
};

struct IceServer
{
    std::string url;
    std::string user;
    std::string pass;
};

IceServer getIceServerFromUrl(const std::string &url, const std::string &clientIp = "")
{
    IceServer srv;
    srv.url = url;

    std::size_t pos = url.find_first_of(':');
    if (pos != std::string::npos)
    {
        std::string protocol = url.substr(0, pos);
        std::string uri = url.substr(pos + 1);
        std::string credentials;

        std::size_t pos = uri.rfind('@');
        if (pos != std::string::npos)
        {
            credentials = uri.substr(0, pos);
            uri = uri.substr(pos + 1);
        }

        if ((uri.find("0.0.0.0:") == 0) && (clientIp.empty() == false))
        {
            // answer with ip that is on same network as client
            std::string clienturl = getServerIpFromClientIp(inet_addr(clientIp.c_str()));
            clienturl += uri.substr(uri.find_first_of(':'));
            uri = clienturl;
        }
        srv.url = protocol + ":" + uri;

        if (!credentials.empty())
        {
            pos = credentials.find_last_of(':');
            if (pos == std::string::npos)
            {
                srv.user = credentials;
            }
            else
            {
                srv.user = credentials.substr(0, pos);
                srv.pass = credentials.substr(pos + 1);
            }
        }
    }

    return srv;
}

static bool isSensorPresentOnRemote(const std::string& sensorId)
{
    string url = GET_CONFIG().remote_vst_address;
    string api = "/api/v1/sensor/" + sensorId + "/info";
    string answer_str;
    if (!curlGetRequest(url + api, answer_str, VmsConfigManager::getInstance()->getNGCAuthHeaders()))
    {
        LOG(error) << "Failed to reach remote vst" << endl;
        return false;
    }
    if (!curlGetRequest(url + api, answer_str, VmsConfigManager::getInstance()->getNGCAuthHeaders()) || answer_str == "null" || answer_str.empty())
    {
        LOG(error) << "Failed to get sensor info from remote vst " << sensorId << endl;
        return false;
    }
    return true;
}

static std::string createUniqueStreamId(std::shared_ptr<SensorInfo> sensor)
{
    string stream_id = sensor->id;
    vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
    if (streams.size() == 0)
        return stream_id;

    int i = 0;
    bool streamNameExist = false;
    do
    {
        streamNameExist = false;
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream_id == stream->id)
            {
                streamNameExist = true;
                stream_id = sensor->id + string("_") + std::to_string(++i);
                break;
            }
        }
    } while (streamNameExist);
    return stream_id;
}

#ifdef ASYNC_API
void process_peer_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<PeerData> in_data = std::static_pointer_cast<PeerData>(data);
    shared_ptr<PeerOutData> out_data = std::static_pointer_cast<PeerOutData>(data->m_outResult);
    PeerConnectionManager* peer = static_cast <PeerConnectionManager*>(parent);
    Json::Value in = in_data->m_dataParams;
    Json::Value out;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    const string query_string = in_data->m_queryParams.get("query", EMPTY_STRING).asString();
    const string request_method = in_data->m_queryParams.get("method", UNKNOWN_STRING).asString();
    string peerid;
    if (in_data == nullptr || peer == nullptr)
    {
        LOG(error) << "Received null in data" << endl;
        return;
    }
    if(in_data->m_expectResult)
    {
        if(out_data.get() == nullptr)
        {
            LOG(error) << "Received null out data" << endl;
            return;
        }
    }

    if(in_data->m_taskName == "call")
    {
        error_code = peer->call(in_data->m_queryParams, in_data->m_dataParams, out);
    }
    else if(in_data->m_taskName == "hangUp")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->hangUp(peerid, out);
    }
    else if(in_data->m_taskName == "toggleStream")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->toggleStream(in_data->m_queryParams, in_data->m_dataParams, out);
    }
    else if(in_data->m_taskName == "pause")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->controlStream("pause", peerid, in, out);
    }
    else if(in_data->m_taskName == "resume")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->controlStream("resume", peerid, in, out);
    }
    else if(in_data->m_taskName == "seek")
    {
        if(iequals(request_method, "get"))
        {
            string mediaSessionId = "";
            CivetServer::getParam(query_string, "peerid", peerid);
            CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
            error_code = peer->getCurrentPosition(peerid, mediaSessionId, out);
        }
        else
        {
            peerid = in.get("peerid", EMPTY_STRING).asString();
            string action = in.get("action", EMPTY_STRING).asString();
            error_code = peer->controlStream(action, peerid, in, out);
        }
    }
    else if(in_data->m_taskName == "stats")
    {
        CivetServer::getParam(query_string, "peerid", peerid);
        string deviceid = "";
        if (peerid.empty())
        {
            CivetServer::getParam(query_string, "deviceid", deviceid);
        }
        string interfaces;
        CivetServer::getParam(query_string, "interfaces", interfaces);
        out["interfaces"] = interfaces;
        error_code = peer->getStreamStats(peerid, out, deviceid);
    }
    else if(in_data->m_taskName == "status")
    {
        string overlay;
        CivetServer::getParam(query_string, "peerid", peerid);
        if(!CivetServer::getParam(query_string, "overlay", overlay))
        {
            overlay = "false";
        }
        error_code = peer->getStreamStatus(peerid, overlay, in_data->m_queryParams, in, out);
    }
    else if(in_data->m_taskName == "query")
    {
        error_code = peer->getQuery(in_data->m_queryParams, in, out);
    }
    else if(in_data->m_taskName == "getIceCandidate")
    {
        CivetServer::getParam(query_string, "peerid", peerid);
        error_code = peer->getIceCandidateList(peerid, out);
    }
    else if(in_data->m_taskName == "addIceCandidate")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->addIceCandidate(peerid, in, out);
    }
    else if(in_data->m_taskName == "getPeerConnectionList")
    {
        error_code = peer->getPeerConnectionList(out);
    }
    else if(in_data->m_taskName == "getStreamList")
    {
        error_code = peer->getStreamList(out);
    }
    else if(in_data->m_taskName == "checkPeerError")
    {
        error_code = peer->checkPeerConnectionErrors(peerid, in, out);
    }
    else
    {
        LOG(warning) << "Invalid message received " << endl;
    }
    if(in_data->m_expectResult)
    {
        out_data->m_response = out;
        out_data->m_error = error_code;
    }
}

VmsErrorCode PeerConnectionManager::postToEventLoop(const string& task_name, const string& peerid,
                                    Json::Value in, Json::Value req_info,
                                    Json::Value& response, bool is_async, uint32_t timeout)
{
    std::shared_ptr<PeerData> in_data(new PeerData);
    in_data->m_taskName = task_name;
    in_data->m_msgId = peerid;
    in_data->m_queryParams = req_info;
    in_data->m_dataParams = in;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    std::shared_ptr<PeerOutData> out_data;
    if(is_async)
    {
        out_data.reset(new PeerOutData);
        if (timeout)
        {
            out_data->m_timeout = timeout;
        }
        in_data->m_outResult = out_data;
        in_data->m_expectResult = is_async;
    }
    bool ret = m_eventLoop.postMsg(in_data);
    if(is_async && ret)
    {
        if (out_data.get())
        {
            response = out_data->m_response;
            error_code =  out_data->m_error;
        }
    }
    else
    {
        if (ret)
        {
            response = true;
            error_code = VmsErrorCode::NoError;
        }
        else
        {
            response = false;
            error_code = VmsErrorCode::VMSInternalError;
        }
    }
    return error_code;
}
#endif

VmsErrorCode PeerConnectionManager::checkPeerConnectionErrors(const string& peerid,
    const Json::Value &in, Json::Value& response)
{
    bool is_client = in.get("isClient", false).asBool() || in.get("is_client", false).asBool();

    if (!is_client && isWebrtcOutLimitCrossed() == true)
    {
        LOG(warning) << "Webrtc connections limit reached" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Webrtc connections limit reached")
        return VmsErrorCode::VMSInternalError;
    }
    if (is_client == true)
    {
        if (isWebrtcInLimitCrossed())
        {
            LOG(warning) << "Webrtc-input connections limit reached" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Webrtc-input connections limit reached")
            return VmsErrorCode::VMSInternalError;
        }
    }
    if (isPeerIdUnique(peerid) == false)
    {
        LOG(warning) << "Peer ID is not unique" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Peer ID is not unique")
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnectionManager::checkParamErrors(const Json::Value& req_info, const Json::Value& in, Json::Value& response)
{
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    string peerId = in.get("peerId", EMPTY_STRING).asString();
    string streamId = in.get("streamId", EMPTY_STRING).asString();
    string sensorId;
    vector<string> list_sensorids;
    bool is_composite_stream = false;
    string peerid = in.get("peerid", EMPTY_STRING).asString();
    Json::Value jsensor = in.get("sensorId", EMPTY_STRING);
    std::map<std::string, std::string, std::less<>> options =  getStreamOptions(in);
    if (options.find("doComposite") != options.end())
    {
        if (options.find("streamIds") != options.end())
        {
            string objects = options.at("streamIds");
            list_sensorids = splitString(objects, ",");
            if (list_sensorids.size())
            {
                is_composite_stream = true;
            }
            else
            {
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Stream IDs not found")
                return VmsErrorCode::InvalidParameterError;
            }
        }
    }
    else if (jsensor.isString())
    {
        sensorId = jsensor.asString();
    }
    std::string startTime = in.get("startTime", EMPTY_STRING).asString();
    bool is_client = in.get("is_client", false).asBool();
    bool is_dataChannel = in.get("is_dataChannel", false).asBool();
    bool createStream = in.get("createStream", false).asBool();

    // backward compatibility
    if (peerId.empty())
    {
        peerId = in.get("peerid", EMPTY_STRING).asString();
        if (peerId.empty())
        {
            LOG(warning) << "peerId is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
            return VmsErrorCode::InvalidParameterError;
        }
    }
    if (!is_client)
    {
        is_client = in.get("isClient", false).asBool();
    }
    if (!is_dataChannel)
    {
        is_dataChannel = in.get("isDataChannel", false).asBool();
    }
    LOG(info) <<"stream start: streamId: " << streamId << " sensorId: " << sensorId << " peerId: " << peerId << endl;

    if (sensorId.empty() == true && is_client == false && is_dataChannel == false && is_composite_stream == false)
    {
        if (streamId.empty() && createStream)
        {
            LOG(warning) << "Skip sensor check for avatar stream" << endl;
            return VmsErrorCode::NoError;
        }
        if (!m_deviceManager->getSensorIdFromStreamId(streamId, sensorId))
        {
            LOG(warning) << "Stream Not Found" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Stream Not Found")
            return VmsErrorCode::InvalidParameterError;
        }
        list_sensorids.push_back(sensorId);
    }
    if (is_client || is_dataChannel)
    {
        return VmsErrorCode::NoError;
    }

    for (auto sensorid : list_sensorids)
    {
        /* Check the sensor sanity */
        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorid);
        VmsErrorCode device_error = checkDeviceSanity (sensor, response);
        if (device_error != VmsErrorCode::NoError)
        {
            if (is_composite_stream)
            {
                /* Ignore errors for composite streams */
                continue;
            }
            else
            {
                return device_error;
            }
        }
        if (startTime.empty() && sensor->type == SENSOR_TYPE_FILE)
        {
            LOG(error) << "File sensor does not support Live playback" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File sensor does not support Live playback")
            return VmsErrorCode::InvalidParameterError;
        }
        string user = req_info.get("username", EMPTY_STRING).asString();
        if(GET_CONFIG().use_multi_user && !(sensor->checkUser(user)))
        {
            string error_message = string("Unauthorized access to sensor");
            LOG(error) << error_message << " for " <<  sensorid << endl;
            if (is_composite_stream)
            {
                /* Ignore errors for composite streams */
                continue;
            }
            else
            {
                string error_message = string("Unauthorized access to sensor");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::CameraUnauthorizedError, response, error_message.c_str())
                return VmsErrorCode::CameraUnauthorizedError;
            }
        }

        std::map<std::string, std::string, std::less<>> opts =  getStreamOptions(in);
        /* if empty streamId, use sensorid as streamId */
        if (opts.find("streamId") != opts.end())
        {
            streamId = opts["streamId"];
            if(streamId == "")
            {
                streamId = sensorid;
            }
        }
        LOG(info) << "streamId: " << streamId << endl;
        /* Check Stream Sanity */
        shared_ptr<StreamInfo> stream_info = sensor->getStream(streamId);
        VmsErrorCode stream_error = checkStreamSanity (stream_info, startTime, response);
        if (stream_error != VmsErrorCode::NoError)
        {
            LOG(error) << "Stream Error for "<< sensorid << endl;
            if (is_composite_stream)
            {
                /* Ignore errors for composite streams */
                continue;
            }
            else
            {
                return stream_error;
            }
        }
    }
    return VmsErrorCode::NoError;
}


/* ---------------------------------------------------------------------------
**  Constructor
** -------------------------------------------------------------------------*/
PeerConnectionManager::PeerConnectionManager(std::string pcType,
                                            const webrtc::AudioDeviceModule::AudioLayer audioLayer,
                                            const std::string &publishFilter,
                                            std::shared_ptr<nv_vms::DeviceManager> deviceManager,
                                            bool monitor)
    : m_pcType(pcType), m_taskQueueFactory(webrtc::CreateDefaultTaskQueueFactory()),
#ifdef HAVE_SOUND
      m_audioDeviceModule(webrtc::AudioDeviceModule::Create(audioLayer, m_taskQueueFactory.get())),
#else
      m_audioDeviceModule(new webrtc::FakeAudioDeviceModule()),
#endif
      m_publishFilter(publishFilter), m_deviceManager(deviceManager)
#ifdef ASYNC_API
      , m_eventLoop("peer_event_loop", process_peer_message)
#endif
{
    InitializePeerConnection();
    if (monitor)
    {
        if (!GET_CONFIG().remote_vst_address.empty())
        {
            m_peerConnMonitoringThread = std::thread([this] { this->peerConnectionMonitorTask(); });
        }

        if (!GET_CONFIG().remote_vst_address.empty() && m_deviceManager && m_deviceManager->needStreamMonitoring)
        {
            StreamEventManager::getInstance().registerListener(this);
        }
    }
}

/* ---------------------------------------------------------------------------
**  Destructor
** -------------------------------------------------------------------------*/
PeerConnectionManager::~PeerConnectionManager()
{
    LOG(info) << __METHOD_NAME__ << endl;
    if (!GET_CONFIG().remote_vst_address.empty() && m_deviceManager && m_deviceManager->needStreamMonitoring)
    {
        StreamEventManager::getInstance().deregisterListener(this);
    }
    m_exitPeerConnThread = true;
    {
        std::lock_guard<std::mutex> lock(m_peerConnThreadMutex);
        m_cvPeerConnThread.notify_all();
    }
    if (m_peerConnMonitoringThread.joinable())
    {
        m_peerConnMonitoringThread.join();
    }
#ifdef HAVE_SOUND
    m_audioDeviceModule = nullptr;
#else
    auto* fakeAdm = static_cast<webrtc::FakeAudioDeviceModule*>(m_audioDeviceModule.get());
    m_audioDeviceModule = nullptr;
    delete fakeAdm;
#endif
    rtc::CleanupSSL();
}

VmsErrorCode PeerConnectionManager::startStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    VmsErrorCode error = checkParamErrors(req_info, in, response);
    if (error != VmsErrorCode::NoError)
    {
        return error;
    }
    string peerid = in.get("peerId", EMPTY_STRING).asString();
    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
#ifdef ASYNC_API
    error = postToEventLoop("checkPeerError", peerid, in, req_info, response, true);
    if (error != VmsErrorCode::NoError)
    {
        return error;
    }
    return postToEventLoop("call", peerid, in, req_info, response);
#else
    error = checkPeerConnectionErrors(peerid, in, response);
    if (error != VmsErrorCode::NoError)
    {
        return error;
    }
    bool createStream = in.get("createStream", false).asBool();
    if (createStream)
    {
        return this->startUdpToWebrtcConnection(req_info, in, response);
    }
    return this->call(req_info, in, response);
#endif
};

VmsErrorCode PeerConnectionManager::stopStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    string peerId = in.get("peerId", EMPTY_STRING).asString();
    // backward compatibility
    if (peerId.empty())
    {
        peerId = in.get("peerid", EMPTY_STRING).asString();
        if (peerId.empty())
        {
            LOG(warning) << "peerId is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
            return VmsErrorCode::InvalidParameterError;
        }
    }
#ifdef ASYNC_API
    VmsErrorCode code = postToEventLoop("hangUp", peerId, in, req_info, response, false);
    LOG(error) << "STOP: " << code << " response: " << response.toStyledString() << endl;
    return code;
#else
    return this->hangUp(peerId, in, response);
#endif
};

VmsErrorCode PeerConnectionManager::switchStream(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    string peerid = in.get("peerId", EMPTY_STRING).asString();
    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
    if (peerid.empty())
    {
        LOG(warning) << "peerid is empty";
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerid is empty")
        return VmsErrorCode::InvalidParameterError;
    }
#ifdef ASYNC_API
    VmsErrorCode code = postToEventLoop("toggleStream", peerid, in, req_info, response, true);
    return code;
#else
    return this->toggleStream(req_info, in, response);
#endif
};

/* ---------------------------------------------------------------------------
**  return video device List as JSON vector
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getVideoDeviceList(Json::Value& value)
{
    const std::list<std::string> videoCaptureDevice = CapturerFactory::GetVideoCaptureDeviceList(m_publishFilter);
    for (auto videoDevice : videoCaptureDevice)
    {
        value.append(videoDevice);
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  return audio device List as JSON vector
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getAudioDeviceList(Json::Value& value)
{
    if (std::regex_match("audiocap://", m_publishFilter))
    {
        int16_t num_audioDevices = m_audioDeviceModule->RecordingDevices();
        LOG(verbose) << "nb audio devices:" << num_audioDevices;

        for (int i = 0; i < num_audioDevices; ++i)
        {
            char name[webrtc::kAdmMaxDeviceNameSize] = {0};
            char id[webrtc::kAdmMaxGuidSize] = {0};
            if (m_audioDeviceModule->RecordingDeviceName(i, name, id) != -1)
            {
                LOG(verbose) << "audio device name:" << name << " id:" << id;
                value.append(name);
            }
        }
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  return iceServers as JSON vector
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getIceServers(const std::string &peerid, const std::string &clientIp, Json::Value& value)
{
    Json::Value urls;
    std::vector<string> iceServerList = getIceServersInternal(peerid);
    for (auto iceServer : iceServerList)
    {
        Json::Value server;
        IceServer srv = getIceServerFromUrl(iceServer, clientIp);
        LOG(verbose) << "ICE URL:" << srv.url;
        server["urls"] = srv.url;
        if (srv.user.length() > 0)
            server["username"] = srv.user;
        if (srv.pass.length() > 0)
            server["credential"] = srv.pass;
        urls.append(server);
    }
    value["iceServers"] = urls;
    {
        std::lock_guard<std::mutex> lock(m_iceServerPeerIdMapLock);
        m_iceServerPeerIdMap[peerid] = iceServerList;
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  get PeerConnection associated with peerid
** -------------------------------------------------------------------------*/
rtc::scoped_refptr<webrtc::PeerConnectionInterface> PeerConnectionManager::getRtcPeerConnection(const std::string &peerid)
{
    rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection;
    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(getPeerConnection(peerid));
    if (peerConnection.get())
    {
        rtcPeerConnection = peerConnection->getRtcPeerConnection();
    }
    return rtcPeerConnection;
}
/* ---------------------------------------------------------------------------
**  add ICE candidate to a PeerConnection
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::addIceCandidate(const std::string &peerid, const Json::Value &in, Json::Value& value)
{
    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerid);
    CHECK_PEER_ERROR(peerConnection, value)
    Json::Value req_info;
    return peerConnection->post("addIceCandidate", peerid, in, req_info, value);
}

/* ---------------------------------------------------------------------------
** create an offer for a call
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::createOffer(unordered_map<string, string> urlParameters,
                                                    std::map<std::string, std::string, std::less<>>& opts,
                                                    Json::Value& offer)
{
    LOG(verbose) << __FUNCTION__ << " peerid:" << urlParameters["peerid"]  << " sensorId:" << urlParameters["sensorId"]
    << " startTime:" << urlParameters["startTime"] << " endTIme:" << urlParameters["endTime"] << " audiourl:" << urlParameters["audiourl"]
    << endl;
    std::string peerid = urlParameters["peerid"];
#if 0
    VmsErrorCode ret = VmsErrorCode::NoError;
#endif
    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = peerid;
    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
    if (!peerConnection.get())
    {
        LOG(error) << "Failed to initialize PeerConnection";
    }
    else
    {
        rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = peerConnection->getRtcPeerConnection();
#if 0
        ret = this->AddStreams(peerConnection, urlParameters, opts, offer);
        if (ret != VmsErrorCode::NoError)
        {
            LOG(error) << "Can't add stream" << endl;
            return ret;
        }
#endif
        // register peerid
        {
            insertPeerConnection(peerid, peerConnection);
            GET_PROMETHEUS()->incrementWebrtcStreams();
        }

        // ask to create offer
        webrtc::PeerConnectionInterface::RTCOfferAnswerOptions rtcoptions;
        rtcoptions.offer_to_receive_video = 0;
        rtcoptions.offer_to_receive_audio = 0;
        std::promise<const webrtc::SessionDescriptionInterface *> promise;
        std::string sdp;
        rtcPeerConnection->CreateOffer(vst_webrtc::CreateSessionDescriptionObserver::Create(rtcPeerConnection.get(), promise, sdp), rtcoptions);

        // waiting for offer
        std::future<const webrtc::SessionDescriptionInterface *> future = promise.get_future();
        if (future.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
        {
            // answer with the created offer
            const webrtc::SessionDescriptionInterface *desc = future.get();
            if (desc)
            {
                offer[kSessionDescriptionTypeName] = desc->type();
                offer[kSessionDescriptionSdpName] = sdp;
            }
            else
            {
                LOG(error) << "Failed to create offer";
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, offer, "Failed to create offer")
                return VmsErrorCode::VMSInternalError;
            }
        }
        else
        {
            LOG(error) << "Failed to create offer";
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, offer, "Failed to create offer")
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
** set answer to a call initiated by createOffer
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::setAnswer(const std::string &peerid, const Json::Value &in, Json::Value& answer)
{
    shared_ptr<IWebrtcConnection> peerconnection = getPeerConnection(peerid);
    CHECK_PEER_ERROR(peerconnection, answer);

    Json::Value req_info;
    return peerconnection->post("setAnswer", peerid, in, req_info, answer);
}

VmsErrorCode PeerConnectionManager::checkDeviceSanity (shared_ptr<SensorInfo> sensor,  Json::Value &value)
{
    VmsErrorCode code = VmsErrorCode::NoError;
    if (sensor)
    {
        std::pair<int, string> err = sensor->getHttpErrorStatus();
        if (err.first != 200)
        {
            LOG(error) << "Camera communication error : "<< err.first << endl;
            code = translateCameraHttpErrorCodeToVmsErrorCode(err.first);
            SET_VMS_ERROR2(code, value, err.second)
        }
    }
    else
    {
        LOG(error) << "Device not found"<< endl;
        SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, value, "Device not found")
        code = VmsErrorCode::CameraNotFoundError;
    }
    return code;
}

VmsErrorCode PeerConnectionManager::checkStreamSanity (shared_ptr<StreamInfo> stream_info, std::string start_time, Json::Value &value)
{
    VmsErrorCode code = VmsErrorCode::NoError;
    if (stream_info.get() == nullptr)
    {
        SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, value, "Stream not found")
        code = VmsErrorCode::CameraNotFoundError;
    }
    else
    {
        /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
        if ((m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS) && m_deviceManager->needRtspServer == true)
        {
            if (start_time.empty() && stream_info->isMainStream
                && stream_info->getErrorStatus().first != STREAM_STATUS_STREAMING)
            {
                LOG(error) << "Streaming not yet started for: "<< stream_info->name << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, "Streaming not ready, try after some time");
                code = VmsErrorCode::VMSInternalError;
            }
        }
    }
    return code;
}

VmsErrorCode PeerConnectionManager::toggleStream(const Json::Value& req_info, const Json::Value &in, Json::Value &value)
{
    MEASURE_FUNCTION_EXECUTION_TIME

    /* Currently Toggle Stream API is applicable to VMS only */
    if(m_deviceManager->getDeviceType() != TYPE_VST)
    {
        LOG(error) << "Stream switching not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, value, "Stream switching not supported")
        return VmsErrorCode::VMSNotSupportedError;
    }

    /* Declare the data structure */
    std::string peerid;
    std::string streamId;
    std::string startTime;
    std::string endTime;
    std::string remote_addr;
    std::string video;

    /* Initialize the data structure */
    peerid      = in.get("peerId", EMPTY_STRING).asString();
    streamId    = in.get("streamId", EMPTY_STRING).asString();
    startTime   = in.get("startTime", EMPTY_STRING).asString();
    endTime     = in.get("endTime", EMPTY_STRING).asString();
    remote_addr = req_info.get("remoteAddr", EMPTY_STRING).asString();

    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
    if (remote_addr.empty())
    {
        remote_addr = req_info.get("remote_addr", EMPTY_STRING).asString();
    }

    /* Populate the opts map */
    std::map<std::string, std::string, std::less<>> opts;

    LOG(verbose) << __FUNCTION__ << " peerid:" << peerid  << " streamId:" << streamId
                << " startTime:" << startTime << " endTIme:" << endTime << endl;

    /* Check the sensor sanity */
    shared_ptr<SensorInfo> sensor = m_deviceManager->searchSensor(streamId);
    VmsErrorCode device_error = checkDeviceSanity (sensor, value);
    if (device_error != VmsErrorCode::NoError)
    {
        return device_error;
    }

    /* Check Stream Sanity */
    shared_ptr<StreamInfo> stream_info = sensor->getStream (streamId);
    VmsErrorCode stream_error = checkStreamSanity (stream_info, startTime, value);
    if (stream_error != VmsErrorCode::NoError)
    {
        LOG(error) << "Stream Error for "<< streamId << endl;
        return stream_error;
    }

    if (stream_info->stream_type == StreamType::Udp || sensor->type == SENSOR_TYPE_WEBRTC)
    {
        LOG(error) << "ToggleStream not supported for udp/webcam stream, Instead use stream/start"<< endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, value, "ToggleStream not supported for udp/webcam stream, Instead use stream/start")
        return VmsErrorCode::VMSNotSupportedError;
    }

    LOG(info) << "Stream status["<< translateStreamStatusToString(stream_info->getErrorStatus().first) << "]: "
                    << stream_info->getErrorStatus().second << endl;
    const string& format = stream_info->settings.encoderValues.encoding;

    if (m_deviceManager->needRtspServer == true && VmsConfigManager::getInstance()->isVideoFormatSupported(format) == false)
    {
        LOG(error) << "Video encode format not supported"<< endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, value, "Video encode format not supported")
        return VmsErrorCode::VMSNotSupportedError;
    }

    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerid);
    CHECK_PEER_ERROR(peerConnection, value);
    return peerConnection->post("toggleStream", peerid, in, req_info, value);
}

/* ---------------------------------------------------------------------------
**  auto-answer to a call
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::call(const Json::Value& req_info, const Json::Value &in, Json::Value &answer)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    std::string peerid;
    std::string username;
    bool is_client = false;
    bool is_dataChannel = false;
    std::string quality;
    VmsErrorCode callError = VmsErrorCode::NoError;
    std::string codec = "h264";

    peerid = in.get("peerId", EMPTY_STRING).asString();
    is_client = in.get("is_client", false).asBool();
    is_dataChannel = in.get("is_dataChannel", false).asBool();
    username = req_info.get("username", EMPTY_STRING).asString();

    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
    if (!is_client)
    {
        is_client = in.get("isClient", false).asBool();
    }
    if (!is_dataChannel)
    {
        is_dataChannel = in.get("isDataChannel", false).asBool();
    }

    LOG(info) << __FUNCTION__ << " peerid:" << peerid  << endl;
    LOG(info) << "is data channel connection: " << is_dataChannel << endl;
    LOG(info) << "is client: " << is_client << endl;
    std::map<std::string, std::string, std::less<>> opts = getStreamOptions(in);
    if ( opts.find("quality") != opts.end() )
    {
        quality = opts.at("quality");
    }
    std::string type;
    std::string sdp;
    Json::Value jmessage = in.get("sessionDescription", EMPTY_STRING);
    if (m_deviceManager->isDeviceRemote())
    {
        codec = in.get("codec", "h264").asString();
    }
    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = peerid;
    if (is_client && GET_CONFIG().webrtc_in_passthrough)
    {
        pc_options["decoder_factory"] = DECODER_FACTORY_PASS_THROUGH;
    }
    if (quality == PASS_THROUGH_QUALITY)
    {
        pc_options["quality"] = PASS_THROUGH_QUALITY;
    }
    std::string startTime = in.get("startTime", EMPTY_STRING).asString();
    if (startTime.empty() == false)
    {
        pc_options["recorded_playback"] = "true";
    }

    /* Need to get the codec for ORIN Nano use case where pass thru is unsupported for
    ** codecs other than H264, this codec info is used while creating factory dependencies */
    string streamId        = in.get("streamId", EMPTY_STRING).asString();
    if (streamId.empty() == false)
    {
        if (m_deviceManager.get() != nullptr)
        {
            shared_ptr<SensorInfo> sensor = m_deviceManager->searchSensor(streamId);
            if (sensor)
            {
                shared_ptr<StreamInfo> stream_info = sensor->getStream(streamId);
                if (stream_info)
                {
                    const string& format = stream_info->settings.encoderValues.encoding;
                    pc_options["codec"] = format;
                }
            }
        }
    }

    {
        shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
        if (!peerConnection.get())
        {
            LOG(error) << "Failed to initialize PeerConnection";
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to initialize PeerConnection")
            return VmsErrorCode::VMSInternalError;
        }
        else if (!peerConnection->getRtcPeerConnection().get())
        {
            LOG(error) << "Failed to initialize PeerConnection";
            peerConnection.reset();
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to initialize PeerConnection")
            return VmsErrorCode::VMSInternalError;
        }
        else
        {
            if (is_client)
            {
                peerConnection->setDeviceId(peerid);
                peerConnection->isClientMode(true);
            }
            Json::Value temp_in, dummy_response;
            temp_in["audioPlayout"] = false;
            peerConnection->post("setAudioPlayout", peerid, temp_in, req_info, dummy_response);

            // register peerid
            {
                insertPeerConnection(peerid, peerConnection, is_client);
                GET_PROMETHEUS()->incrementWebrtcStreams();
            }

            callError = peerConnection->post("call", peerid, in, req_info, answer);
        }
        // Create device after creating the peerConnection for is_client usecase
        if (callError == VmsErrorCode::NoError)
        {
            if (is_client)
            {
                /* create new webrtc device here because onAddStream needs it,
                *  reuse peerId as a unique sensorId for webrtc-in device */
                const string sensorId = peerid;
                // init as null deviceInfo
                shared_ptr<SensorInfo> sensor = nullptr;
                LOG(info) << "sensorId: " << sensorId << endl;
                if (m_deviceManager.get() != nullptr && m_deviceManager->sensorExists(sensorId) == true)
                {
                    sensor = m_deviceManager->searchSensor(sensorId);
                }
                if (sensor.get() && sensor->isRemoteSensor)
                {
                    VmsErrorCode ret = createWebrtcDevice(in, sensorId, username, sensor);
                    if (ret != VmsErrorCode::NoError)
                    {
                        LOG(error) << "Can't create webrtc-input device" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, answer, "Can't create webrtc-input device")
                        return ret;
                    }
                }
                answer["deviceId"] = sensorId;
                peerConnection->setDeviceName(sensorId);
            }
        }
        else
        {
            return callError;
        }
    }
    return VmsErrorCode::NoError;
}

bool PeerConnectionManager::streamStillUsed(const std::string &streamLabel)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    bool stillUsed = false;
    for (auto it : m_peerConnectionMap)
    {
        std::shared_ptr<PeerConnection> pc = std::dynamic_pointer_cast<PeerConnection>(it.second);
        if (pc == nullptr)
        {
            continue;
        }
        rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = pc->getRtcPeerConnection();
        std::vector<rtc::scoped_refptr<webrtc::RtpSenderInterface>> senders = rtcPeerConnection->GetSenders();
        for (auto stream : senders)
        {
            std::vector<std::string> streamVector = stream->stream_ids();
            if (streamVector.size() > 0) {
                if (streamVector[0] == streamLabel)
                {
                    stillUsed = true;
                    break;
                }
            }
        }
    }
    return stillUsed;
}

bool PeerConnectionManager::isWebrtcOutLimitCrossed()
{
    const uint32_t peerConnectionLimit  = GET_CONFIG().max_webrtc_out_connections;
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    if (m_peerConnectionMap.size() >= peerConnectionLimit)
    {
        return true;
    }
    return false;
}

bool PeerConnectionManager::isWebrtcInLimitCrossed()
{
    const uint32_t peerConnectionLimit  = GET_CONFIG().max_webrtc_in_connections;
    int32_t closingConnectionsCount = 0;

#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerInConnMapMutex);
#endif

    /* Check if any connections are in disconnected state, So that we can ignore from the map */
    auto it = m_peerInputConnections.begin();
    while (it != m_peerInputConnections.end())
    {
        auto peerConnection = std::dynamic_pointer_cast<PeerConnection>(it->second);
        if (peerConnection == nullptr)
        {
            continue;
        }
        rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = peerConnection->getRtcPeerConnection();
        if (rtcPeerConnection)
        {
            webrtc::PeerConnectionInterface::PeerConnectionState peerConnection_state
                = rtcPeerConnection->peer_connection_state();
            webrtc::PeerConnectionInterface::IceConnectionState ice_state
                = rtcPeerConnection->ice_connection_state();
            if (peerConnection_state >= webrtc::PeerConnectionInterface::PeerConnectionState::kDisconnected ||
                ice_state >= webrtc::PeerConnectionInterface::kIceConnectionDisconnected)
            {
                notify("camera_remove", it->first);
                closingConnectionsCount++;
            }
        }
        ++it;
    }
    if ((m_peerInputConnections.size() - closingConnectionsCount) >= peerConnectionLimit)
    {
        return true;
    }
    return false;
}

bool PeerConnectionManager::isPeerIdUnique(const std::string peerid)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    if (m_peerConnectionMap.find(peerid) == m_peerConnectionMap.end())
    {
        return true;
    }
    return false;
}
/* ---------------------------------------------------------------------------
**  hangup a call
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::hangUp(const std::string peerid,
                                            const Json::Value &in,
                                            Json::Value &answer)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    LOG(info) << __FUNCTION__ << " " << peerid << endl;
    /**
     * delete the web cam stream at start of hangup. Device is also deleted on webrtc
     * peer connection state callback.
     */
    shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(peerid);
    // Skip sensor deletion for Edge device
    if (sensor.get() && (sensor->type == SENSOR_TYPE_WEBRTC || sensor->type == SENSOR_TYPE_UDP))
    {
        LOG(warning) << "calling delete for " << peerid << endl;
        notify("camera_remove", peerid);
        vst_common::deleteWebrtcSensor(peerid, m_deviceManager);
    }
    {
        shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerid);
        if (peerConnection.get())
        {
            LOG(verbose) << "Remove PeerConnection peerid:" << peerid << endl;
            GET_PROMETHEUS()->decrementWebrtcStreams();
            Json::Value req_info;
            peerConnection->post("removeTracks", peerid, in, req_info, answer);
            erasePeerConnection(peerid);
        }
    }
    if (sensor.get() && sensor->type == SENSOR_TYPE_UDP)
    {
        LOG(warning) << "Stopping udp pipeline " << peerid << endl;
        GstDummyUdpPipeline::getInstance()->stopAllUdpPipelines();
    }
    if (sensor.get() && !GET_CONFIG().remote_vst_address.empty())
    {
        LOG(warning) << "Adding stream to remote retry list " << peerid << endl;
        struct timeval timeNow;
        gettimeofday(&timeNow, nullptr);
        std::lock_guard<std::mutex> lock(m_remoteRetryListLock);
        m_remoteRetryStreams.push_back(std::make_pair(peerid, timeNow));
    }

    /* Remove peerConnection from the input-map*/
    {
#ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerInConnMapMutex);
#endif
        auto it_input = m_peerInputConnections.find(peerid);
        if (it_input != m_peerInputConnections.end())
        {
            LOG(verbose) << "Remove PeerConnection peerid:" << peerid << endl;
            m_peerInputConnections.erase(it_input);
        }
    }

    /* Remove peerConnection from the client-map */
    ClientErase(peerid);

    {
        std::lock_guard<std::mutex> lock(m_iceServerPeerIdMapLock);
        m_iceServerPeerIdMap.erase(peerid);
    }
    LOG(info) << "Exiting from hangup " << peerid  << endl;
    answer = true;
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  get list ICE candidate associayed with a PeerConnection
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getIceCandidateList(const std::string &peerid, Json::Value& value)
{
    LOG(verbose) << __FUNCTION__;
    shared_ptr<IWebrtcConnection> pc = getPeerConnection(peerid);
    if (pc.get())
    {
        Json::Value in, req_info;
        pc->post("getIceCandidate", peerid, in, req_info, value);
    }
    else
    {
        LOG(error) << "No peerconnection for peer:" << peerid << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, "No peerconnection for peer")
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  get PeerConnection list
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getPeerConnectionList(Json::Value& value)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    for (auto it : m_peerConnectionMap)
    {
        Json::Value content, in, req_info;
        it.second->post("getPeerConnectionList", "1", in, req_info, content);

        // get Stats
        //      content["stats"] = it.second->getStats();

        Json::Value pc;
        pc[it.first] = content;
        value.append(pc);
    }
    return VmsErrorCode::NoError;
}

/* ---------------------------------------------------------------------------
**  get Stream stats
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getStreamStats(std::string &peerId, Json::Value& retJsonVal, string deviceId)
{
    Json::Value transcode_stats;
    Json::Value content;
    Json::Value streamProperties;
    Json::Value jsonObjDecoder;
    Json::Value jsonObjEncoder;
    Json::Value jsonObjFrameRate;
    Json::Value jsonInboundVideoStats;
    Json::Value jsonInboundAudioStats;

    uint32_t j = 0;
    LatencyStats decStats;
    double averageEncodeTime = 0;

    std::string toErase = "||tcp60";
    std::string streamLabel;
    std::string framesEncoded;
    std::string totalEncodeTime;
    std::string framesSent;
    std::string bytesSent;
    std::string qualityLimitationReason;
    std::string qualityLimitationResolutionChanges;

    Stats::StreamStats peerStatsObj;
    std::vector<std::string> contentMembers;
    std::map<std::string, AudioVideoPair, std::less<>>::iterator audio_video_pair_it;
    std::string transportID;
    std::string rtcInboundRTPVideoStream;
    std::string rtcInboundRTPAudioStream;
    std::string rtcOutboundRTPVideoStream;
    std::string rtcVideoSource;

    string nw_interfaces = "";
    retJsonVal = Json::nullValue;

    Stats& pcStreamStats = Stats::getInstance();

    /* PeerID and CameraID are not present */
    if (peerId.empty())
    {
        if (deviceId.empty())
        {
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, retJsonVal, "PeerID expected in request")
            return VmsErrorCode::InvalidParameterError;
        }
        else
        {
            bool found = false;
            auto clientList = GetAllClients();
            for(const auto& clientPair : clientList)
            {
                if (clientPair.second.m_deviceId == deviceId)
                {
                    peerId = clientPair.first;
                    found = true;
                    break;
                }
            }
            if (!found)
            {
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, retJsonVal, "No peer found for given sensor")
                return VmsErrorCode::InvalidParameterError;
            }
        }
    }

    string fps = "", width = "", height = "";
    string audioBitrate = EMPTY_STRING;
    Json::Value in, req_info;
    shared_ptr<IWebrtcConnection> pc;
    /* PeerID is present */
    if (!peerId.empty())
    {
#ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
        std::map<std::string, Stats::StreamStats, std::less<>>::iterator peer_stats_maps_it;
        std::map<std::string, Stats::StreamStats, std::less<>> peerStatsMap = pcStreamStats.getPeerStatsMap();
        peer_stats_maps_it = peerStatsMap.find(peerId);
        if (peer_stats_maps_it != peerStatsMap.end())
        {
            if (!pcStreamStats.isDataStale (peer_stats_maps_it->second.m_lastAccessTime))
            {
                transcode_stats = peer_stats_maps_it->second.m_transcodeStats;
                for (auto const& id : transcode_stats.getMemberNames()) {
                    streamLabel = id;
                    eraseString(streamLabel, toErase);
                }
                goto return_value;
            }
            fps = to_string(peer_stats_maps_it->second.m_avgFps);
            width = to_string(peer_stats_maps_it->second.m_width);
            height = to_string(peer_stats_maps_it->second.m_height);
        }
    }

    pc = getPeerConnection(peerId);
    if (!pc.get())
    {
        transcode_stats = "Incorrect PeerID";
        goto return_value;
    }
    /* Start of Encoder Stats */
    /* get complete webRTC stats using getStats() API */
    pc->post("stats", peerId, in, req_info, content);
    if (content.isBool())
    {
        LOG(error) << "Stats error" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, retJsonVal)
        return VmsErrorCode::VMSInternalError;
    }
    contentMembers = content["stats"].getMemberNames();
    if (content["stats"].get("transportId", "ERROR") != "ERROR")
    {
        transportID = content["stats"]["transportId"].asString();
    }
    rtcOutboundRTPVideoStream = "O" + transportID + "V";
    rtcInboundRTPVideoStream = "I" + transportID + "V";
    rtcInboundRTPAudioStream = "I" + transportID + "A";
    rtcVideoSource = "SV";

    /* iterate over complete stats size */
    for(j = 0; j < contentMembers.size(); j++)
    {
        /* check for "RTCOutboundRTPVideoStream" member as this contains the Encoder Stats */
        if (contentMembers.at(j).find(rtcOutboundRTPVideoStream) == 0)
        {
            /* populate the json object with encoder stats */
            framesEncoded =  content["stats"][contentMembers.at(j)].get("framesEncoded", "ERROR").asString();
            qualityLimitationReason =  content["stats"][contentMembers.at(j)].get("qualityLimitationReason", "ERROR").asString();
            qualityLimitationResolutionChanges =  content["stats"][contentMembers.at(j)].get("qualityLimitationResolutionChanges", "ERROR").asString();
            framesSent =  content["stats"][contentMembers.at(j)].get("framesSent", "ERROR").asString();
            totalEncodeTime =  content["stats"][contentMembers.at(j)].get("totalEncodeTime", "ERROR").asString();
            bytesSent = content["stats"][contentMembers.at(j)].get("bytesSent", "ERROR").asString();
            try
            {
                averageEncodeTime = (std::stof(totalEncodeTime) / std::stof(framesEncoded)) * 1000;
            }
            catch(const std::exception& e)
            {
                std::cerr << e.what() << '\n';
            }
            TRY_CATCH_STOF(jsonObjEncoder, totalEncodeTime, "totalEncodingTimeInSec");
            TRY_CATCH_STOI(jsonObjEncoder, framesEncoded, "framesEncoded");
            TRY_CATCH_STOI(jsonObjEncoder, framesSent, "framesSent");
            TRY_CATCH_STOI(jsonObjEncoder, bytesSent, "bytesSent");
            TRY_CATCH_STOI(jsonObjEncoder, qualityLimitationResolutionChanges, "qualityLimitationResolutionChanges");
            averageEncodeTime = (isnan(averageEncodeTime) ? 0 : averageEncodeTime);
            jsonObjEncoder["qualityLimitationReason"] = qualityLimitationReason;
            jsonObjEncoder["averageEncodingTimePerframeInMs"] = Json::Value(averageEncodeTime);
        }
        /* Check for "RTCVideoSource" stats */
        if (contentMembers.at(j).find(rtcVideoSource) == 0)
        {
            jsonObjFrameRate = Json::Value(content["stats"][contentMembers.at(j)].get("framesPerSecond", "ERROR").asString());
        }
        /* Check for "RTCInboundRTPVideoStream" stats */
        if (contentMembers.at(j).find(rtcInboundRTPVideoStream) == 0)
        {
            jsonInboundVideoStats = content["stats"][contentMembers.at(j)];
            jsonInboundVideoStats["frameRate"] = fps;
            jsonInboundVideoStats["frameWidth"] = width;
            jsonInboundVideoStats["frameHeight"] = height;
        }
        /* Check for "RTCInboundRTPAudioStream" stats */
        if (contentMembers.at(j).find(rtcInboundRTPAudioStream) == 0)
        {
            jsonInboundAudioStats = content["stats"][contentMembers.at(j)];
            jsonInboundAudioStats["encAudioBitrate"] = audioBitrate;
        }
    }
    /* End of Encoder Stats */

    /* Start of Decoder Stats */
    /* get PeerConnectionObserver instance from iterator */
    {
#ifndef ASYNC_API
        std::lock_guard<std::mutex> mlock(m_streamMapMutex);
#endif
        /* get the videoSource from AudioVideoPair */
        audio_video_pair_it = m_stream_map.find(peerId);
        if (audio_video_pair_it != m_stream_map.end())
        {
            AudioVideoPair pair = audio_video_pair_it->second;
            rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource(pair.first);
            if(m_deviceManager->getDeviceType() == TYPE_VST)
            {
#if 0 /* TODO Fix decoder stats */
                /* get decoder stats */
                rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource);
                TrackSource<NvGstVideoCapturer>* trackSource = static_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
                if (trackSource != nullptr)
                {
                    trackSource->getDecoderStatsTrackSource(decStats);
                    /* populate the json object with decoder stats */
                    jsonObjDecoder["framesDecoded"] = Json::Value(decStats.m_totalFrames);
                    jsonObjDecoder["totalDecodingTimeInsec]"] = Json::Value(decStats.m_totalFrames * decStats.m_totalLatency / 1000);
                    jsonObjDecoder["averageDecodingLatencyPerframeInMs"] = Json::Value(decStats.m_totalLatency);
                    jsonObjDecoder["maxDecodingLatencyPerFrameInMs"] = Json::Value(decStats.m_maxLatency);
                    jsonObjDecoder["minDecodingLatencyPerFrameinMs]"] = Json::Value(decStats.m_minLatency);
                }
#endif
            }
        }

        /* End of Decoder Stats */
        transcode_stats["encode"] = jsonObjEncoder;
        transcode_stats["decode"] = jsonObjDecoder;
        transcode_stats["currentFrameRate"] = jsonObjFrameRate;
        transcode_stats["inboundVideo"] = jsonInboundVideoStats;
        transcode_stats["inboundAudio"] = jsonInboundAudioStats;

        peerStatsObj.m_lastAccessTime = std::time(nullptr);
        peerStatsObj.m_transcodeStats = transcode_stats;
        pcStreamStats.setPeerStatsMap(std::make_pair(peerId, peerStatsObj));
    }

return_value:
    ClientInfo client;
    if (ClientSearch(peerId, client))
    {
        shared_ptr<StreamInfo> stream = m_deviceManager->getStream(client.m_deviceId, client.m_streamId);
        if (stream)
        {
            streamProperties["streamId"] = stream->id;
            streamProperties["framerate"] = stream->settings.encoderValues.frameRate;
            Json::Value resolution;
            resolution["width"] = stream->settings.encoderValues.resolution.width;
            resolution["height"] = stream->settings.encoderValues.resolution.height;
            streamProperties["resolution"] = resolution;
            streamProperties["encoding"] = stream->settings.encoderValues.encoding;
            streamProperties["encodingProfile"] = stream->settings.encoderValues.encodingProfile;
        }
    }

    if (auto google_pc = std::dynamic_pointer_cast<PeerConnection>(pc))
    {
        nw_interfaces = google_pc->getNwInterface();
    }
    retJsonVal["streamStats"] = transcode_stats;
    retJsonVal["frameRetrievalAccuracy"] = getTimeStampMap();
    retJsonVal["streamSettings"] = streamProperties;
    retJsonVal["networkBandwidth"] = getBandwidth(nw_interfaces);
    // timestamp will be null for stale data
    retJsonVal["timestamp"] = content["stats"]["timestamp"];

    return VmsErrorCode::NoError;
}

const Json::Value PeerConnectionManager::getTimeStampMap()
{
    Json::Value value;
    Stats& pcStreamStats = Stats::getInstance();
    std::queue<std::pair<uint64_t, uint64_t>> ts_queue = pcStreamStats.getQueue();
    while (!ts_queue.empty())
    {
        Json::Value pc;
        std::pair<uint64_t, uint64_t> q_element = ts_queue.front();
        ts_queue.pop();
        pc[convertEpocToISO8601(q_element.first)] = convertEpocToISO8601(q_element.second);
        int64_t diff = (q_element.second - q_element.first) / 1000;
        pc["Difference"] = Json::Value(diff);
        value.append(pc);
    }
    return value;
}

/* ---------------------------------------------------------------------------
**  get StreamList list
** -------------------------------------------------------------------------*/
VmsErrorCode PeerConnectionManager::getStreamList(Json::Value& value)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    for (auto it : m_peerConnectionMap)
    {
        Json::Value list, in, req_info;
        it.second->post("getStreamList", "2", in, req_info, list);
        if(list.isArray())
        {
            for(Json::Value::const_iterator itr = list.begin(); itr != list.end(); ++itr)
            {
                value.append(itr->asString());
            }
        }
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnectionManager::controlStream(const std::string action
                                                , const std::string &peerId
                                                , const Json::Value &data
                                                , Json::Value& value)
{
    std::string seek_value = data.get("seek_value", EMPTY_STRING).asString();
    LOG(verbose) << "ACTION: " << action << endl;
    LOG(verbose) << "SEEK VALUE: " << seek_value << endl;


    if ((action != "pause") && (action != "resume") && (action != "seek_forward")
        && (action != "seek_backward") && (action != "rewind") && (action != "fast_forward"))
    {
        string msg = string("Action: ") + action + string(" is not supported yet");
        LOG(error) << msg << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, msg.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    if (peerId.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, "PeerID expected in request")
        return VmsErrorCode::InvalidParameterError;
    }
    LOG(verbose) << "PEER ID: " << peerId << endl;

    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerId);
    CHECK_PEER_ERROR(peerConnection, value);

    string api = "";
    if (action == "pause" || action == "resume")
    {
        api = action;
    }
    else
    {
        api = "seek";
    }
    Json::Value req_info;
    return peerConnection->post(api, peerId, data, req_info, value);
}

VmsErrorCode PeerConnectionManager::getCurrentPosition(const std::string &peerId,
                                                        const std::string& mediaSessionId,
                                                        Json::Value& response)
{
    LOG(info) << "Seek GET position api" << endl;

    /* Currently GET seek API to get position is applicable to VMS only */
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() != TYPE_VST && m_deviceManager->getDeviceType() != TYPE_MMS)
    {
        LOG(error) << "Get Position API not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "GET protocol in seek not supported")
        return VmsErrorCode::VMSNotSupportedError;
    }

    if (peerId.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "PeerID expected in request")
        return VmsErrorCode::InvalidParameterError;
    }

    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerId);
    CHECK_PEER_ERROR(peerConnection, response);
    Json::Value in, req_info;
    in["mediaSessionId"] = mediaSessionId;
    return peerConnection->post("getPosition", peerId, in, req_info, response);
}

VmsErrorCode PeerConnectionManager::getStreamStatus(const std::string &peerId,
                                                    const std::string &overlay,
                                                    const Json::Value& req_info,
                                                    const Json::Value& in,
                                                    Json::Value& response)
{
    LOG(verbose) << "PEER ID: " << peerId << endl;
    if (peerId.empty() == false)
    {
        shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerId);
        CHECK_PEER_ERROR(peerConnection, response);

        return peerConnection->post("status", peerId, in, req_info, response);
    }
    else
    {
    #ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
    #endif
        for (auto it : m_peerConnectionMap)
        {
            Json::Value out;
            string peer_id = it.first;
            shared_ptr<IWebrtcConnection> peerConnection = it.second;
            peerConnection->post("status", peer_id, in, req_info, out);
            if (!out.isBool())
            {
                ClientInfo client;
                if (ClientSearch(peer_id, client))
                {
                    out["deviceId"] = client.m_deviceId;
                    out["streamId"] = client.m_streamId;
                    out["clientIpAddress"] = client.m_ipAddress;
                }
            }
            response.append(out);
        }
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnectionManager::getQuery(const Json::Value& req_info,
                                            const Json::Value& in,
                                            Json::Value& response)
{
    std::string peerid = "";
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (query_string.empty() == false)
    {
        CivetServer::getParam(query_string, "peerid", peerid);
    }

    if (peerid.empty() == false)
    {
        shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerid);
        CHECK_PEER_ERROR(peerConnection, response);

        return peerConnection->post("query", peerid, in, req_info, response);
    }
    else
    {
    #ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
    #endif
        for (const auto& it : m_peerConnectionMap)
        {
            string peer_id = it.first;
            shared_ptr<IWebrtcConnection> peerConnection = it.second;
            if (peerConnection.get() == nullptr)
            {
                continue;
            }
            Json::Value queryResponse;
            VmsErrorCode queryResult = peerConnection->post("query", peer_id, in, req_info, queryResponse);
            if (queryResult != VmsErrorCode::NoError)
            {
                LOG(error) << "query failed for peerId:" << peer_id << endl;
                continue;
            }
            Json::Value out;
            if (queryResponse.isMember("ts") && queryResponse["ts"].isUInt64())
            {
                out["frameTime"] = queryResponse["ts"];
            }
            out["peerId"] = peer_id;
            ClientInfo client;
            if (ClientSearch(peer_id, client))
            {
                out["sensorId"] = client.m_deviceId;
                out["streamId"] = client.m_streamId;
            }
            Json::Value startTimeResponse;
            VmsErrorCode startTimeResult = peerConnection->post("getStartTime", peer_id, in, req_info, startTimeResponse);
            if (startTimeResult == VmsErrorCode::NoError && startTimeResponse.isMember("startTime"))
            {
                out["startTime"] = startTimeResponse["startTime"];
            }
            Json::Value durationResponse;
            VmsErrorCode durationResult = peerConnection->post("getDurationStream", peer_id, in, req_info, durationResponse);
            if (durationResult == VmsErrorCode::NoError && durationResponse.isMember("duration"))
            {
                out["duration"] = durationResponse["duration"];
            }
            response.append(out);
        }
    }
    return VmsErrorCode::NoError;
}

void PeerConnectionManager::resolveAndValidateRP()
{
    vector<string> rp_ip_port_vector = splitString(GET_CONFIG().reverse_proxy_server_address, ":");
    if (rp_ip_port_vector.size() != 2)
    {
        LOG(error) << "Errorneous ReverseProxy endpoint provided: " << GET_CONFIG().reverse_proxy_server_address << endl;
        return;
    }

    string RP_ipAddr_string;
    if (rp_ip_port_vector[0] == "REVERSE_PROXY_SERVER_ADDRESS")
    {
        char *rp_ip_address = getenv(rp_ip_port_vector[0].c_str());
        if (rp_ip_address == nullptr)
        {
            LOG(error) << "Env variable 'REVERSE_PROXY_SERVER_ADDRESS' Not defined" << endl;
            return;
        }
        RP_ipAddr_string = rp_ip_address;
    }
    else
    {
        /* Direct ip_address is provided */
        RP_ipAddr_string = rp_ip_port_vector[0];
    }
    GET_CONFIG().reverse_proxy_server_address = RP_ipAddr_string + string(":") + rp_ip_port_vector[1];

    string RP_URL = GET_CONFIG().reverse_proxy_server_address + "/v1/status";
    long http_res = 0;
    bool curl_ret = curlGetRequest(RP_URL, http_res);
    if (curl_ret && http_res == 200)
    {
        LOG(info) << "RP is running & reachable" << endl;
    }
    else
    {
        LOG(info) << "RP is not running/reachable http_res:" << http_res << endl;
    }
}

/* ---------------------------------------------------------------------------
**  check if factory is initialized
** -------------------------------------------------------------------------*/
void PeerConnectionManager::InitializePeerConnection()
{
    int logLevel              = rtc::LS_ERROR;
    // To print webrtc latency stats
    if (GET_CONFIG().enable_latency_logging == true)
    {
        logLevel              = rtc::LS_WARNING;
    }
    rtc::LogMessage::LogToDebug((rtc::LoggingSeverity)logLevel);
    rtc::LogMessage::LogTimestamps();
    rtc::LogMessage::LogThreads();
    LOG(verbose) << "Logger level:" <<  rtc::LogMessage::GetLogToDebug() << std::endl;
#ifdef ASYNC_API
    m_eventLoop.setParent(this);
#endif
    rtc::InitializeSSL();
    if (GET_CONFIG().use_reverse_proxy == true)
    {
        resolveAndValidateRP();
    }

    /* Set the webrtc port range if specified */
    if (GET_CONFIG().webrtc_port_range != Json::nullValue)
    {
        m_minPort = GET_CONFIG().webrtc_port_range.get("min", 0).asUInt();
        m_maxPort = GET_CONFIG().webrtc_port_range.get("max", 0).asUInt();
        LOG(info) << "Webrtc port range min:"<< m_minPort <<", max:"<< m_maxPort << endl;
    }
}

void PeerConnectionManager::DestroyPeerConnections()
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >::iterator it;
    for (it = m_peerConnectionMap.begin(); it != m_peerConnectionMap.end(); )
    {
        shared_ptr<IWebrtcConnection> peerConnection = it->second;
        string peerid = it->first;
        Json::Value in, req_info, response;
        peerConnection->post("removeTracks", peerid, in, req_info, response);
        it = m_peerConnectionMap.erase(it);
        GET_PROMETHEUS()->decrementWebrtcStreams();
    }
}

std::string PeerConnectionManager::getAuthTokenForClient(
    const std::string& coturn_auth_secret, const std::string &peerId)
{
    string key = coturn_auth_secret;
    string userName = TURN_SERVER_DEFAULT_USER_NAME;
    unsigned char digest_final[1024];
    unsigned int digest_len;
    string final_token, str_signed;

    uint64_t curEpocTime = getEpocTimeInMS(getCurrentUtcTime(), true) / 1000;
    curEpocTime += AUTH_TOKEN_EXPIRATON_TIME_SEC;

    string userCombo = to_string(curEpocTime) + ":" + userName + "_" + peerId;

    HMAC(EVP_sha1(), key.data(), key.length(),
        (unsigned char*)userCombo.data(), userCombo.length(),
        digest_final, &digest_len);

    if (digest_len > 0)
    {
        str_signed = base64_encode(
                reinterpret_cast<char const*>(digest_final), digest_len);
    }

    final_token = userCombo + ":" + str_signed;
    LOG(verbose) << "Generate auth token: " << final_token << endl;
    return final_token;
}

std::vector<string> PeerConnectionManager::getIceServersInternal(const std::string &peerId)
{
    std::vector<string> iceServerList;
    std::vector<string> turnServerList;

    /* Get the all stun servers from the config file */
    for (auto stun_uri : GET_CONFIG().stunurl_list)
    {
        iceServerList.push_back(std::string("stun:")+stun_uri);
    }

    /* Return only stun list to client if RP is enabled */
    if (GET_CONFIG().use_reverse_proxy == true)
    {
        return iceServerList;
    }

    /* Get the turn-servers from either twilio, coturn or from config file */
    if (GET_CONFIG().use_twilio_stun_turn == true)
    {
        if (GET_CONFIG().twilio_account_sid.empty() || GET_CONFIG().twilio_auth_token.empty())
        {
            LOG(error) << "Error - Empty twilio account details provided" << endl;
            return iceServerList;
        }
        iceServerList.clear();
        string accound_sid, key_sid;
        vector<string> sid_list = splitString(GET_CONFIG().twilio_account_sid, ":");
        accound_sid = key_sid = sid_list[0];
        if (sid_list.size() > 1)
        {
            key_sid = sid_list[1];
        }

        string twilio_server = "https://accounts.twilio.com/v1/Credentials/PublicKeys";
        string twilio_rest_api = "https://api.twilio.com/2010-04-01/Accounts/" + accound_sid + "/Tokens.json";

        /* Get the token from twilio turn server */
        string ttl_value = to_string(AUTH_TOKEN_EXPIRATON_TIME_SEC);
        string post_params = "Ttl=" + ttl_value;
        string response;

        bool res = curlPostRequest(twilio_server,
            key_sid, GET_CONFIG().twilio_auth_token,
            twilio_rest_api, post_params, response);
        if (res == false)
        {
            LOG(error) << "HTTP tokens Rest-api failed for twllio server" << endl;
            return iceServerList;
        }

        std::stringstream response_stream(response);
        Json::Value json_obj;
        Json::CharReaderBuilder jsonReader;
        std::string errs;
        if (Json::parseFromStream(jsonReader, response_stream, &json_obj, &errs))
        {
            Json::Value ice_servers = json_obj.get("ice_servers", Json::nullValue);
            if(ice_servers.isArray())
            {
                for(Json::Value::const_iterator it = ice_servers.begin(); it != ice_servers.end(); ++it)
                {
                    string url, username, credential, finalIceServerUrl;
                    Json::Value elem = *it;
                    if (elem != Json::nullValue)
                    {
                        url = elem.get("url", EMPTY_STRING).asString();

                        /* Add stun url directly */
                        if (url.find("stun.") != string::npos)
                        {
                            iceServerList.push_back(url);
                            continue;
                        }
                        username = elem.get("username", EMPTY_STRING).asString();
                        credential = elem.get("credential", EMPTY_STRING).asString();

                        /* remove 'turn:' from the url, it will be added later */
                        string token("turn:");
                        string url_without_prefix = url.substr(url.find(token) + token.size());

                        /* Prepare ICE url in the form of turn:username:password@ip_address:port */
                        string tempCredentials = username + ":" + credential;
                        finalIceServerUrl = "turn:" + tempCredentials + "@" + url_without_prefix;
                        iceServerList.push_back(finalIceServerUrl);
                        /* Securely erase temporary credentials from memory */
                        std::fill(tempCredentials.begin(), tempCredentials.end(), '\0');
                        tempCredentials.clear();
                    }
                }
            }
        }
        else
        {
            LOG(error) << "Could not parse response data as JSON, err: " << errs << endl;
        }
    }
    else if (GET_CONFIG().use_coturn_auth_secret == true)
    {
        for (auto turn_uri : GET_CONFIG().coturn_turnurl_list_with_secret)
        {
            string ip_port, coturn_key_secret;
            int colon_count = std::count(turn_uri.begin(), turn_uri.end(), ':');
            if (colon_count != 2)
            {
                LOG(error) << "Ignoring wrong coturn turnurl:" << turn_uri << endl;
                continue;
            }
            std::size_t pos = turn_uri.find_last_of(':');
            if (pos != std::string::npos)
            {
                ip_port = turn_uri.substr(0, pos);
                coturn_key_secret = turn_uri.substr(pos + 1);

                string authToken = getAuthTokenForClient(coturn_key_secret, peerId);
                string finalTurnUrl = "turn:" + authToken + "@" + ip_port;
                turnServerList.push_back(finalTurnUrl);
            }
        }
    }

    for (auto turn_uri : GET_CONFIG().static_turnurl_list)
    {
        turnServerList.push_back(std::string("turn:") + turn_uri);
    }

    if (turnServerList.size() > 0)
    {
        /* This rotation is implemented for load balancing, since webrtc always selects
        first turnserver. So rotate the list for every peer */
        std::rotate(turnServerList.begin(), turnServerList.begin() + m_turnserverIndex, turnServerList.end());
        iceServerList.insert(iceServerList.end(), turnServerList.begin(), turnServerList.end());
        if (m_turnserverIndex < turnServerList.size() - 1)
        {
            m_turnserverIndex++;
        }
        else
        {
            m_turnserverIndex = 0;
        }
    }
    return iceServerList;
}

std::vector<string> PeerConnectionManager::getIceServersForSpecificPeer(const std::string &peerId)
{
    std::vector<string> iceServerList;

    /* Return iceServerList if it is already populated for this peerId */
    std::lock_guard<std::mutex> lock(m_iceServerPeerIdMapLock);
    std::map<std::string, std::vector<string>, std::less<>>::iterator it = m_iceServerPeerIdMap.find(peerId);
    if (it != m_iceServerPeerIdMap.end())
    {
        iceServerList = it->second;
    }
    return iceServerList;
}

void PeerConnectionManager::updateRpStunServer(const Json::Value& in)
{
    string stunIp = in.get("stunIp", EMPTY_STRING).asString();
    int stunPort = in.get("stunPort", 100).asInt();
    string username = in.get("username", EMPTY_STRING).asString();
    string password = in.get("password", EMPTY_STRING).asString();

    /* Return iceServerList if it is already populated for this peerId */
    std::lock_guard<std::mutex> lock(m_rpStunServerLock);
    /* Example format - admin:admin@10.0.0.1:3478 */
    m_rpStunServer = username + string(":") + password + string("@") + stunIp + string(":") + to_string(stunPort);
    LOG(info) << "Updated RP stun: " << m_rpStunServer << endl;
    return;
}

std::string PeerConnectionManager::getRpStunServer()
{
    std::lock_guard<std::mutex> lock(m_rpStunServerLock);
    return m_rpStunServer;
}

pair<string, int> PeerConnectionManager::getRpStunBinding(int& local_port)
{
    pair <string, int> rp_seat;

    vector<int> failed_ports;
    std::unordered_map<string, string> opts;

    /* m_rpStunServer = admin:admin@10.0.0.1:3478 */
    string natServer;
    vector<string> list_1 = splitString(getRpStunServer(), "@");
    if (list_1.size() > 1)
    {
        natServer = list_1[1];
    }

    vector<string> list_2;
    if (list_1.size())
    {
        list_2 = splitString(list_1[0], ":");
    }
    string password = "";
    if (list_2.size() > 1)
    {
        password = list_2[1];
    }

    /* retry_stun_binding */
    int stun_local_port = UdpClientPool::getInstance()->getWebrtcUdpPort();
    if (stun_local_port == -1)
    {
        LOG(error) << "Failed to get free webrtc port" << endl;
        return rp_seat;
    }

    string username = "";
    if (list_2.size())
    {
        username = list_2[0];
    }
    username = username + to_string(stun_local_port);

    LOG(info) << "Invoke GetPublicAddress with natServer:" << natServer << ", username:" << maskSensitiveData(username, MaskType::USERNAME)
            <<", password:"<< maskSensitiveData(password, MaskType::PASSWORD) <<", stun_local_port:" << stun_local_port << endl;
    opts["natServer"] = natServer;
    opts["username"] = username;
    opts["password"] = password;

    for (size_t i = 0; i < failed_ports.size(); i++)
    {
        UdpClientPool::getInstance()->freeWebrtcUdpPort(failed_ports[i]);
    }
    local_port = stun_local_port;
    return rp_seat;
}

/* ---------------------------------------------------------------------------
**  create a new PeerConnection
** -------------------------------------------------------------------------*/
shared_ptr<IWebrtcConnection> PeerConnectionManager::CreatePeerConnection(std::unordered_map<std::string, std::string>& opts)
{
    string peerid = "";
    if(opts.find("peerid") != opts.end())
    {
        peerid = opts.at("peerid");
    }

    webrtc::PeerConnectionInterface::RTCConfiguration config;
    std::vector<string> iceServerList = getIceServersForSpecificPeer(peerid);
    if (iceServerList.empty())
    {
        // User provided ICE serverList is empty, use default stun/turn url.
        iceServerList.push_back(std::string("stun:") + DEFAULT_STUN_URL);
    }
    if (GET_CONFIG().use_reverse_proxy == true)
    {
        /* In case of RP/Ice-lite, use empty iceservers */
        iceServerList.clear();
        /* Use RP Stun-server if available */
        /*if (isRpStunAvailable())
        {
            iceServerList.push_back(std::string("stun:") + getRpStunServer());
        }*/
    }
    for (auto iceServer : iceServerList)
    {
        webrtc::PeerConnectionInterface::IceServer server;
        IceServer srv = getIceServerFromUrl(iceServer);
        server.uri = srv.url;
        server.username = srv.user;
        server.password = srv.pass;
        config.servers.push_back(server);
    }
    config.sdp_semantics = webrtc::SdpSemantics::kUnifiedPlan;

    pair<string, int> rp_seat;
    int local_port = UdpClientPool::getInstance()->getWebrtcUdpPort();
    if (local_port != -1)
    {
        if (m_minPort != 0)
        {
            config.set_min_port(local_port);
        }
        if (m_maxPort != 0)
        {
            config.set_max_port(local_port);
        }
    }
    if (GET_CONFIG().use_reverse_proxy == true)
    {
        if (isRpStunAvailable())
        {
            rp_seat = getRpStunBinding(local_port);;
        }
    }

    LOG(info) << "Creating PeerConnection peerid:" << peerid << ", local_port:" << local_port << endl;
    shared_ptr<PeerConnection> pc;
    try
    {
        pc = std::make_shared<PeerConnection>(this, peerid, config, opts);
    }
    catch(const std::invalid_argument& e)
    {
        string err_msg = e.what();
        LOG(error) << err_msg << endl;
        return nullptr;
    }

    if (pc.get())
    {
        if (GET_CONFIG().use_reverse_proxy == true && isRpStunAvailable() && rp_seat.second != -1)
        {
            LOG(info) << "Setting RP seat for peerId:" << peerid << ", publicAddr:" << rp_seat.first << ", publicPort:" << rp_seat.second << endl;
            pc->setRpSeat(rp_seat);
        }
        pc->setLocalPort(local_port);
    }
    LOG(info) << "Created PeerConnection peerid:" << peerid << endl;
    return pc;
}

const std::string PeerConnectionManager::sanitizeLabel(const std::string &label)
{
    std::string out(label);

    // conceal labels that contain rtsp URL to prevent sensitive data leaks.
    if (label.find("rtsp:") != std::string::npos)
    {
        std::hash<std::string> hash_fn;
        size_t hash = hash_fn(out);
        return std::to_string(hash);
    }

    out.erase(std::remove_if(out.begin(), out.end(), ignoreInLabel), out.end());
    return out;
}

VmsErrorCode PeerConnectionManager::createWebrtcDevice(const Json::Value &in, const string deviceid,
    const string username, shared_ptr<SensorInfo> existingSensor)
{
    VmsErrorCode ret = VmsErrorCode::NoError;

    if (m_deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        ret = VmsErrorCode::VMSInternalError;
        return ret;
    }

    int current_sensor_count = m_deviceManager->getSensorsSize();
    if(current_sensor_count >= GET_CONFIG().max_sensors_supported)
    {
        LOG(error) << "Sensor count limit reached" << endl;
        ret = VmsErrorCode::VMSNotSupportedError;
    }
    else
    {
        // if deviceInfo is null then create sensor otherwise add stream only
        if (existingSensor)
        {
            LOG(info) << "existing sensor" << endl;
            existingSensor->printInfo();
            addStreamToSensor(in, existingSensor);
            ret = addStreamToRecorder(existingSensor);
             // Set NoError in case of remote restart
            existingSensor->updateSensorStatus(SensorStatusOnline);
            existingSensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
        }
        else
        {
            shared_ptr<SensorInfo> sensor (new SensorInfo);
            LOG(info) << "new sensor" << endl;
            shared_ptr<UserInfo> user (new UserInfo);
            user->username = username;
            sensor->id = sensor->sensorId = deviceid;
            sensor->name = DEFAULT_WEBCAM_NAME + deviceid;
            sensor->updateSensorStatus(SensorStatusOnline);
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            sensor->type = SENSOR_TYPE_WEBRTC;
            sensor->m_notify = false;
            sensor->addUser(user);
            addStreamToSensor(in, sensor);
            string response;
            vst_common::addSensorManually(sensor, response, m_deviceManager);
            LOG(info) << "Added sensor: " << response << endl;
            addStreamToRecorder(sensor);

            if(addUdpSubStreamToSensor(sensor) == -1)
            {
                ret = VmsErrorCode::VMSInternalError;
            }
            if (GET_CONFIG().use_external_peerconnection)
            {
                ret = VmsErrorCode::VMSNotSupportedError;
            }
            sensor->printInfo();
        }
    }
    return ret;
}

int PeerConnectionManager::addUdpSubStreamToSensor(shared_ptr<SensorInfo> existed_sensor)
{
    if (!existed_sensor.get())
    {
        return -1;
    }

    std::shared_ptr<StreamInfo> stream(new StreamInfo);
    stream->isMainStream = false;
    stream->sensorId = existed_sensor->id;
    stream->id = createUniqueStreamId(existed_sensor);

    stream->name = string(DEFAULT_UDP_DEVICE_NAME) + string("_") + stream->id;
    stream->updateStreamtype(StreamType::Udp);
    stream->direction = StreamDirectionOut;

    /* Obtain video & audio ports  */
    int video_port = 0, audio_port = 0;
    video_port = UdpClientPool::getInstance()->getUdpPort();
    audio_port = UdpClientPool::getInstance()->getUdpPort();

    if (video_port <= 0 || audio_port <= 0)
    {
        LOG(error) << "Port range is exhausted" << endl;
        return -1;
    }

    // Create live_url as => udp:<video_port>:<audio_port>
    string stream_url = "udp";
    stream->live_url = stream_url + ":" + to_string(video_port) + ":" + to_string(audio_port);
    LOG(info) << "stream name = " << stream->name << ", live_url = " << secureUrlForLogging(stream->live_url) << endl;
    {
        SensorAudioEncoderSettingsValues values;
        values.enable = true;
        values.encoding = DEFAULT_AUDIO_CODEC_PCM;
        values.sample_rate = to_string(DEFAULT_SAMPLE_RATE);
        values.bits_per_sample = to_string(DEFAULT_BITS_PER_SAMPLE);
        stream->updateAudioEncoderValues(values);
        LOG(info) << "audio_enable:" << values.enable << ", audio_codec = " << values.encoding << ", sample_rate = "
                << values.sample_rate << ", bps = " << values.bits_per_sample << endl;
    }
    stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
            translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));

    string width, height;
    if (width.empty() || height.empty())
    {
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty())
        {
            width = resolution.width;
            height = resolution.height;
        }
        else
        {
            width = DEFAULT_WIDTH;
            height = DEFAULT_HEIGHT;
        }
    }

    SensorVideoEncoderSettingsValues values;
    values.encoding = DEFAULT_VIDEO_CODEC;
    values.frameRate = to_string(DEFAULT_FRAMERATE);
    values.resolution.width = width;
    values.resolution.height = height;
    stream->updateVideoEncoderValues(values);

    /* Push newly created stream into the sensor */
    bool isStreamAdded = existed_sensor->addStreams(stream);
    if (isStreamAdded == false)
    {
        LOG(error) << "failed to add stream into sensor" << endl;
        return -1;
    }
    return 0;
}

void PeerConnectionManager::addStreamToSensor(const Json::Value &in, shared_ptr<SensorInfo> sensor)
{
    if (sensor)
    {
        LOG(info) << "Adding streams to sensor " << sensor->name << endl;
        shared_ptr<StreamInfo> stream(new StreamInfo);
        stream->isMainStream = true;
        stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
                        translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));
        stream->updateStreamtype(StreamType::Webrtc);
        stream->direction = StreamDirectionIn;
        stream->sensorId = stream->id = sensor->id;
        stream->name = sensor->name;
        stream->live_url = stream->replay_url = stream->live_proxy_url =
            vst_rtsp::rtspUrlPrefix(sensor->id) + string("webrtc/") + stream->sensorId;

        SensorVideoEncoderSettingsValues values;
        values.encoding = DEFAULT_VIDEO_CODEC;
        values.frameRate = to_string(DEFAULT_FRAMERATE);

        Resolution resolution;
        resolution = string(DEFAULT_RESOLUTION);
        if (!GET_CONFIG().webrtc_in_passthrough)
        {
            resolution = GET_CONFIG().webrtc_in_fixed_resolution;
        }
        values.resolution.width = resolution.width;
        values.resolution.height = resolution.height;

        /* Check if any change in video prop */
        bool isStreamUpdated = false;
        string codec = in.get("codec", DEFAULT_VIDEO_CODEC).asString();
        if (sensor && !iequals(codec, DEFAULT_VIDEO_CODEC))
        {
            if (iequals(codec, "h265"))
            {
                values.encoding = "h265";
                isStreamUpdated = true;
            }
        }
        stream->updateVideoEncoderValues(values);
        sensor->streams.push_back(stream);

        if (isStreamUpdated)
        {
            WebrtcStreamProducer::getInstance()->updateStreamProperties(sensor->id);
        }
    }
    else
    {
        LOG(error) << "Failed to add stream to sensor, sensor not found" << endl;
    }
    return;
}

VmsErrorCode PeerConnectionManager::addStreamToRecorder(shared_ptr<SensorInfo> sensor)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    if (sensor && sensor->streams.size() > 0)
    {
        LOG(info) << "Adding streams to recorder" << endl;
        shared_ptr<StreamInfo> stream = sensor->streams[0];
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder != nullptr)
        {
            int ret_val = vst_recorder::addStream(stream->id, stream->live_proxy_url);
            if (ret_val != 0)
            {
                ret = VmsErrorCode::VMSInternalError;
            }
        }
        else
        {
            LOG(error) << "Failed to get stream recorder" << endl;
            ret = VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        ret = VmsErrorCode::VMSInternalError;
    }
    return ret;
}

int PeerConnectionManager::notify(const string& change, const string& deviceId, int width, int height)
{
    Resolution resolution;
    bool isVideoTrackAvailable = true;
    if (width == 0 && height == 0)
    {
        /* Check if it is audio-only webrtc stream, Otherwise wait for video notification */
        isVideoTrackAvailable = WebrtcStreamProducer::getInstance()->isVideoTrackEnabled(deviceId);
        if (isVideoTrackAvailable == true && change == "camera_streaming")
        {
            /* Expecte & wait for camera_add notfiy from videosink */
            return 0;
        }
    }

    resolution = string(DEFAULT_RESOLUTION);
    if (!GET_CONFIG().webrtc_in_passthrough)
    {
        resolution = GET_CONFIG().webrtc_in_fixed_resolution;
    }
    shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(deviceId);
    if (sensor)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->streams;
        if (streams.size() > 0)
        {
            shared_ptr<StreamInfo> stream = streams[0];
            Json::Value payload, event, metadata;
            if (change == "camera_add")
            {
                SensorVideoEncoderSettingsValues values = stream->getvideoEncoderValues();
                metadata["resolution"] = resolution.width + "x" + resolution.height;
                metadata["codec"] = "h264";
                metadata["framerate"] = 30;
                if (!iequals(values.encoding, "h265"))
                {
                    metadata["codec"] = "h264";
                    values.encoding   = "h264";
                }
                else
                {
                    metadata["codec"] = "h265";
                }
                if (isVideoTrackAvailable == true)
                {
                    event["metadata"] = metadata;
                }

                values.frameRate  = "30";
                values.resolution.height = resolution.height;
                values.resolution.width  = resolution.width;
                stream->updateVideoEncoderValues(values);
            }
            event["camera_id"] = sensor->sensorId;
            event["camera_name"] = sensor->name;
            event["camera_url"] = stream->live_url; // Use original URL for payload
            event["change"] = change;
            payload["created_at"] = getCurrentTime();
            payload["source"] = "vst";
            payload["alert_type"] = "camera_status_change";
            payload["event"] = event;
            
            // Create a copy for logging with masked URL
            Json::Value logPayload = payload;
            logPayload["event"]["camera_url"] = secureUrlForLogging(stream->live_url);
            LOG(info) << logPayload.toStyledString() << endl;

            INotificationInterface* notifier = NotificationFactory::CreatePlatformNotification();
            if (notifier)
            {
                notifier->sendMessage(payload);

                // if (change == "camera_add")
                // {
                //     /* Send camera_streaming as well for webrtc-input device */
                //     event["change"] = "camera_streaming";
                //     payload["event"] = event;
                //     LOG(info) << payload.toStyledString() << endl;
                //     notifier->sendMessage(payload);
                // }
                return 0;
            }
            else
            {
                LOG(error) << "Notification Manager instance is not created" << endl;
            }
        }
        else
        {
            LOG(error) << "No streams present for sensor: " << sensor->sensorId << endl;
        }
    }
    else
    {
        LOG(error) << "No sensor present for: " << deviceId << endl;
        return -2;
    }
    return -1;
}

VmsErrorCode PeerConnectionManager::AddTrackToExistingPeerConnection(const string peerid, const string stream_id)
{
    LOG(info) << __METHOD_NAME__ << endl;
    if (peerid.empty() || stream_id.empty())
    {
        LOG(error) << "Empty arguments" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerid);
    if (!peerConnection.get())
    {
        LOG(error) << "Peer not found" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    VmsErrorCode ret = VmsErrorCode::NoError;
    Json::Value in, dummy_req, dummy_response;
    in["device_id"] = peerid;
    in["stream_id"] = stream_id;
    ret = peerConnection->post("addUdpTrack", peerid, in, dummy_req, dummy_response);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "addUdpTrack failed" << endl;
        return ret;
    }

    Json::Value offer;
    ret = peerConnection->post("createOffer", peerid, in, dummy_req, offer);

    if (ret == VmsErrorCode::NoError)
    {
        // websocket send offer
        string str_offer = jsonToString(offer);
        GET_WEBSOCKET_INSTANCE()->sendMessage(peerid, str_offer, MG_WEBSOCKET_OPCODE_TEXT);
    }
    return ret;
}

VmsErrorCode PeerConnectionManager::setWebrtcClientParams(const string sensorId, const Json::Value& in)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    LOG(info) << __METHOD_NAME__ << endl;
    if (sensorId.empty())
    {
        LOG(error) << "Empty arguments" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(getPeerConnection(sensorId));
    if (!peerConnection.get() && GET_CONFIG().use_external_peerconnection == false)
    {
        LOG(error) << "Peer not found" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    Json::Value json_value;
    string webrtc_vendor_type = in.get("webrtcVendor", WEBRTC_VENDOR_GOOGLE).asString();
    if (GET_CONFIG().use_reverse_proxy == true && webrtc_vendor_type.find(WEBRTC_VENDOR_RAGNAROK) != string::npos)
    {
        /* This is external webrtc connection, store it for RP ports cleanup purpose */
        string streamId = sensorId + "_1";
        string remote_client_addr = m_externalPeerInfo[streamId]["clientIpAddr"].asString();

        string ipAddr = in.get("ipAddress", "").asString();
        int signaling_port = in.get("signalingPort", 49100).asInt();
        int media_port = in.get("mediaPort", 49200).asInt();
        int width = in.get("width", 1280).asInt();
        int height = in.get("height", 720).asInt();
        int framerate = in.get("framerate", 30).asInt();

        /* Get the public-Ip & port from ReverseProxy */
        LOG(info) << "Streamsdk params IP:" << ipAddr << ", signaling_port:" << signaling_port
                << " media_port:" << media_port << " remote_client_addr:" << remote_client_addr << endl;
        pair<string, int> seat = getAvailableSeatFromRP(streamId, remote_client_addr, ipAddr, to_string(media_port));
        if (seat.first.empty() || seat.second == -1)
        {
            LOG(error) << "Failed to get the RP seat" << endl;
            return VmsErrorCode::InvalidParameterError;;
        }

        json_value["streamSettings"]["webrtcVendor"] = webrtc_vendor_type;
        json_value["streamSettings"]["signalingPort"] = signaling_port;
        json_value["streamSettings"]["mediaConnectionInfo"]["address"] = seat.first;
        json_value["streamSettings"]["mediaConnectionInfo"]["port"] = seat.second;
        json_value["streamSettings"]["resolution"]["width"] = width;
        json_value["streamSettings"]["resolution"]["height"] = height;
        json_value["streamSettings"]["fps"] = framerate;

        // websocket send message
        string str_msg = jsonToString(json_value);
        if (GET_WEBSOCKET_INSTANCE()->isConnected(sensorId))
        {
            LOG(info) << "Sending ragnarok streamStart message for connectionId:" << sensorId << endl;
            LOG(info) << "Message: " << json_value.toStyledString() << endl;
            GET_WEBSOCKET_INSTANCE()->sendMessage(sensorId, str_msg, MG_WEBSOCKET_OPCODE_TEXT);
        }
    }
    else
    {
        if (webrtc_vendor_type.find(WEBRTC_VENDOR_GOOGLE) != string::npos)
        {
            json_value["streamSettings"]["webrtcVendor"] = WEBRTC_VENDOR_GOOGLE;

            // websocket send message
            string str_msg = jsonToString(json_value);
            if (GET_WEBSOCKET_INSTANCE()->isConnected(sensorId))
            {
                LOG(info) << "Sending webrtc Vendor ws message for connectionId:" << sensorId << endl;
                LOG(info) << "Message: " << json_value.toStyledString() << endl;
                GET_WEBSOCKET_INSTANCE()->sendMessage(sensorId, str_msg, MG_WEBSOCKET_OPCODE_TEXT);
            }
        }
        else
        {
            LOG(error) << "Only RP is supported in ragnarok as of now" << endl;
            return VmsErrorCode::InvalidParameterError;
        }
    }
    return ret;
}

VmsErrorCode PeerConnectionManager::udpToWebrtc(const string sensorId, const string streamId)
{
    LOG(info) << __METHOD_NAME__ << endl;
    if (sensorId.empty() || streamId.empty())
    {
        LOG(error) << "Empty arguments" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = streamId;
    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
    if (!peerConnection.get())
    {
        LOG(error) << "Failed to initialize PeerConnection";
        return VmsErrorCode::VMSInternalError;
    }
    else if (!peerConnection->getRtcPeerConnection().get())
    {
        LOG(error) << "Failed to initialize PeerConnection";
        peerConnection.reset();
        return VmsErrorCode::VMSInternalError;
    }
    else
    {
        Json::Value temp_in, dummy_response;
        temp_in["audioPlayout"] = false;
        peerConnection->post("setAudioPlayout", streamId, temp_in, temp_in, dummy_response);

        insertPeerConnection(streamId, peerConnection);
        GET_PROMETHEUS()->incrementWebrtcStreams();
    }
    // Sensor Id needed to send iceCandidate on websocket for Avatar stream
    peerConnection->setDeviceId(sensorId);

    VmsErrorCode ret = VmsErrorCode::NoError;
    Json::Value in, dummy_req, dummy_response;
    in["device_id"] = sensorId;
    in["stream_id"] = streamId;
    ret = peerConnection->post("addUdpTrack", streamId, in, dummy_req, dummy_response);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "addUdpTrack failed" << endl;
        return ret;
    }

    /* Take publicIp of the client from webrtc-input stream if present */
    shared_ptr<PeerConnection> peerConnection_in = std::dynamic_pointer_cast<PeerConnection>(getInPeerConnection(sensorId));
    if (peerConnection_in.get())
    {
        peerConnection->setClientPublicIpAddr(peerConnection_in->getClientPublicIpAddr());
    }

    Json::Value offer;
    ret = peerConnection->post("createOffer", streamId, in, dummy_req, offer);
    offer["streamId"] = streamId;

    if (ret == VmsErrorCode::NoError)
    {
        Json::Value wsMsg;
        wsMsg["apiKey"] = "api/v1/stream/start";
        wsMsg["peerId"] = streamId;
        wsMsg["data"] = offer;
        offer = wsMsg;
        // websocket send offer
        string str_offer = jsonToString(offer);
        std::string connectionId = sensorId;
        if (!GET_WEBSOCKET_INSTANCE()->isConnected(connectionId))
        {
            // TODO: Workaround, get first available connection id.
            connectionId = GET_WEBSOCKET_INSTANCE()->getFirstConnectionId();
        }
        GET_WEBSOCKET_INSTANCE()->sendMessage(connectionId, str_offer, MG_WEBSOCKET_OPCODE_TEXT);
    }
    return ret;
}

VmsErrorCode PeerConnectionManager::SendRemotePeerOffer(const string sensorId, const string streamId, const Json::Value& in)
{
    LOG(info) << __METHOD_NAME__ << endl;

    Json::Value sdpOffer;
    sdpOffer["apiKey"] = "api/v1/streambridge/stream/start";
    sdpOffer["peerId"] = streamId;
    sdpOffer["data"] = in;
    if (GET_WEBSOCKET_INSTANCE()->isConnected(sensorId))
    {
        LOG(info) << "[UEDBG] Sending offer from UE: " << sdpOffer.toStyledString() << endl;
        GET_WEBSOCKET_INSTANCE()->sendMessage(sensorId, jsonToString(sdpOffer), MG_WEBSOCKET_OPCODE_TEXT);
    }

    return VmsErrorCode::NoError;
}

pair<string, string> parsePortFromCandidateString(const string& iceCandidate)
{
    string ipAddr, port;
    std::istringstream iss(iceCandidate);
    string token;

    // Skip the initial 'candidate' keyword and the following timestamp
    iss >> token; // Read 'candidate:'
    iss >> token; // Read timestamp (if needed)
    while (iss >> token)
    {
        if (token == "udp")
        {
            iss >> token; // Skip Priority value

            iss >> token; // Read Address value
            ipAddr = token;

            iss >> token; // Read port number
            port = token;
            break;
        }
    }
    LOG(info) << "Parsed port number: " << port << endl;
    return std::make_pair(ipAddr, port);
}

string replaceIPAddressAndPort(const string& candidate, const string& newIPAddress, int newPort)
{
    std::istringstream iss(candidate);
    std::ostringstream oss;
    string token;

    // Read and append the initial 'candidate' keyword and timestamp
    iss >> token; // Read 'candidate:timestamp'
    oss << token;

    // Read and append component-id, transport, priority
    for (int i = 0; i < 3; ++i)
    {
        iss >> token;
        oss << " " << token;
    }
    // Skip original ipAddress & port
    for (int i = 0; i < 2; ++i)
    {
        iss >> token;
    }
    // Insert new IP address and port with new ones
    oss << " " << newIPAddress << " " << newPort;

    // Read and append the remaining tokens
    while (iss >> token)
    {
        oss << " " << token;
    }
    return oss.str();
}

const pair<string, int> PeerConnectionManager::getAvailableSeatFromRP(const string& session_id,
        const string& remote_ipAddr, const string& private_ip, const string& private_port)
{
    pair<string, int> seat;
    vector<string> headers;
    string response;
    bool res = false;
    string RP_HTTP_URL;

    string sessionId = string("sessionId: ") + session_id;
    headers.push_back(sessionId);
    string sessionSourceAddress = string("sessionSourceAddress: ") + remote_ipAddr;
    headers.push_back(sessionSourceAddress);

    string node_ip;
    if (private_ip.empty() == false)
    {
        node_ip = private_ip;
    }
    else
    {
        char *node_ip_env = getenv("NODE_IP");
        if (node_ip_env != nullptr)
        {
            node_ip = string(node_ip_env);
        }
        else
        {
            node_ip = g_hostIp;
        }
    }

    LOG(info) << "Node IP:" << node_ip << endl;
    string sessionDestinationAddress = string("sessionDestinationAddress: ") + node_ip;
    headers.push_back(sessionDestinationAddress);

    string udp_port = private_port;
    string sessionRoutes = string("sessionRoutes: ") + "UDP:video:" + udp_port;
    headers.push_back(sessionRoutes);

    RP_HTTP_URL = "http://" + GET_CONFIG().reverse_proxy_server_address + "/v1/routes/seats";
    res = curlPostRequest_2(RP_HTTP_URL, headers, response);
    if (res == false)
    {
        LOG(error) << "Curl post request failed for RP" << endl;
        return seat;
    }
    /*
        RP response
        < HTTP/1.1 200 OK
        < nodeRemainingCapacity: 1998
        < sessionPublicAddress: 115.114.90.35
        < sessionPrivateAddress: 10.24.143.97
        < sessionRoutes: 15948:UDP:video:15010
    */

    LOG(info) << "RP response for sessionId: " << session_id << " - " << response << endl;
    std::istringstream f(response);
    std::string line;
    while (std::getline(f, line))
    {
        if (line.find("sessionPublicAddress") != string::npos)
        {
            vector<string> list = splitString(line, ": ");
            string public_ip = list[1];
            removeWhiteSpaces(public_ip);
            seat.first = public_ip;
        }
        else if (line.find("sessionRoutes") != string::npos)
        {
            vector<string> list = splitString(line, ": ");
            vector<string> list_1 = splitString(list[1], ":");
            string public_port = list_1[0];
            removeWhiteSpaces(public_port);
            seat.second = stringToInt(public_port, -1);
        }
    }
    LOG(info) << "RP seat=" << seat.first << ":" << seat.second << endl;
    return seat;
}

void PeerConnectionManager::generateRpCandidate(const string& streamId, Json::Value iceCandidates)
{
    string rp_candidate_str;
    Json::Value rp_candidate;
    string iceCandidate_str;
    string final_iceCandidate_str;
    unsigned int i = 0;
    string remote_client_addr = m_externalPeerInfo[streamId]["clientIpAddr"].asString();
    if (iceCandidates.isArray())
    {
        for (i = 0; i < iceCandidates.size(); ++i)
        {
            Json::Value temp = iceCandidates[i];
            string candidate = temp.get("candidate", "").asString();
            if (candidate.find("udp") != string::npos && candidate.find("host") != string::npos)
            {
                iceCandidate_str = candidate;
                break;
            }
        }
    }
    else
    {
        std::string candidate = iceCandidates.get("candidate", "").asString();
        if (candidate.find("udp") != string::npos && candidate.find("host") != string::npos)
        {
            iceCandidate_str = candidate;
        }
    }

    if (!iceCandidate_str.empty())
    {
        pair<string, string> ip_port = parsePortFromCandidateString(iceCandidate_str);
        if (!ip_port.first.empty() && !ip_port.second.empty())
        {
            LOG(info) << "RP input ipAddr:" << ip_port.first << ", port:" << ip_port.second << " remote_client_addr:" << remote_client_addr << endl;
            pair<string, int> seat = getAvailableSeatFromRP(streamId,
                                    remote_client_addr, ip_port.first, ip_port.second);
            if (seat.first.empty() || seat.second == -1)
            {
                LOG(error) << "Failed to get the RP seat" << endl;
                return;
            }
            rp_candidate_str = replaceIPAddressAndPort(iceCandidate_str, seat.first, seat.second);
            if (iceCandidates.isArray())
            {
                iceCandidates[i]["candidate"] = rp_candidate_str;
                rp_candidate.append(iceCandidates[i]);
            }
            else
            {
                iceCandidates["candidate"] = rp_candidate_str;
                rp_candidate.append(iceCandidates);
            }
            final_iceCandidate_str = jsonToString(rp_candidate);
            LOG(info) << "Websocket sending RP iceCandidate:" << final_iceCandidate_str << endl;
            if (!final_iceCandidate_str.empty())
            {
                string connectionId = GET_WEBSOCKET_INSTANCE()->getFirstConnectionId();
                GET_WEBSOCKET_INSTANCE()->sendMessage(connectionId, final_iceCandidate_str, MG_WEBSOCKET_OPCODE_TEXT);
            }
            m_externalPeerInfo[streamId]["rpCandidateGenerated"] = true;
        }
        else
        {
            LOG(error) << "Failed to parsing of ip & port from iceCandidate" << endl;
        }
    }
}

VmsErrorCode PeerConnectionManager::SendRemotePeerIceCandidate(const string sensorId, const string streamId, const Json::Value& in)
{
    LOG(info) << __METHOD_NAME__ << endl;

    Json::Value candidates = in.get("candidates", EMPTY_STRING);
    if (GET_CONFIG().use_reverse_proxy == true)
    {
        bool rpCandidateGenerated = m_externalPeerInfo[streamId]["rpCandidateGenerated"].asBool();
        if (rpCandidateGenerated == true)
        {
            // Ignore this, since RP candidate is already generated & sent
            return VmsErrorCode::NoError;
        }
        generateRpCandidate(streamId, candidates);
    }

    if (GET_WEBSOCKET_INSTANCE()->isConnected(sensorId))
    {
        Json::Value wsResponse;
        wsResponse["apiKey"] = "api/v1/streambridge/iceCandidate";
        wsResponse["peerId"] = streamId;
        wsResponse["data"] = candidates;
        LOG(info) << "[UEDBG] Sending iceCandidate for UE:" << wsResponse.toStyledString() << endl;
        GET_WEBSOCKET_INSTANCE()->sendMessage(sensorId, jsonToString(wsResponse), MG_WEBSOCKET_OPCODE_TEXT);
    }
    return VmsErrorCode::NoError;
}

shared_ptr<IWebrtcConnection> PeerConnectionManager::getPeerConnection(const std::string &peerid)
{
    shared_ptr<IWebrtcConnection> peerConnection = getOutPeerConnection(peerid);
    if (!peerConnection.get())
    {
        peerConnection = getInPeerConnection(peerid);
    }
    return peerConnection;
}

shared_ptr<IWebrtcConnection> PeerConnectionManager::getOutPeerConnection(const std::string &peerid)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    shared_ptr<IWebrtcConnection> peerConnection;
    std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >::iterator it = m_peerConnectionMap.find(peerid);
    if (it == m_peerConnectionMap.end())
    {
        LOG(verbose) << "Out Peer not found " << peerid << endl;
    }
    else
    {
        peerConnection = it->second;
    }
    return peerConnection;
}

shared_ptr<IWebrtcConnection> PeerConnectionManager::getInPeerConnection(const std::string &peerid)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerInConnMapMutex);
#endif
    shared_ptr<IWebrtcConnection> peerConnection;
    std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >::iterator it = m_peerInputConnections.find(peerid);
    if (it != m_peerInputConnections.end())
    {
        peerConnection = it->second;
    }
    else
    {
        LOG(verbose) << "In Peer not found " << peerid << endl;
    }
    return peerConnection;
}

void PeerConnectionManager::insertPeerConnection(const std::string &peerid,
            shared_ptr<IWebrtcConnection> peerConnection, bool webrtcIn /*false*/)
{
    if (!webrtcIn)
    {
#ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
        m_peerConnectionMap.insert(std::pair<std::string, shared_ptr<IWebrtcConnection> >(peerid, peerConnection));
    }
    else
    {
#ifndef ASYNC_API
        std::lock_guard<std::mutex> peerlock(m_peerInConnMapMutex);
#endif
        m_peerInputConnections.insert(std::pair<std::string, shared_ptr<IWebrtcConnection> >(peerid, peerConnection));
    }
}

void PeerConnectionManager::erasePeerConnection(const std::string &peerid)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >::iterator it = m_peerConnectionMap.find(peerid);
    if (it != m_peerConnectionMap.end())
    {
        m_peerConnectionMap.erase(it);
    }
}

bool PeerConnectionManager::isStreamExists(const std::string &stream_id)
{
#ifndef ASYNC_API
    std::lock_guard<std::mutex> peerlock(m_peerMapMutex);
#endif
    std::map<std::string, shared_ptr<IWebrtcConnection>, std::less<> >::iterator it;
    for (it = m_peerConnectionMap.begin(); it != m_peerConnectionMap.end(); ++it)
    {
        shared_ptr<PeerConnection> pc = std::dynamic_pointer_cast<PeerConnection>(it->second);
        if (pc.get())
        {
            string id = pc->getDeviceId();
            if (id == stream_id)
            {
                return true;
            }
        }
    }
    return false;
}

bool PeerConnectionManager::checkIfRemoteReachable()
{
    string url = GET_CONFIG().remote_vst_address;
    string api = "/api/v1/streambridge/version";
    string answer_str;
    if (!curlGetRequest(url + api, answer_str, VmsConfigManager::getInstance()->getNGCAuthHeaders()))
    {
        LOG(error) << "Failed to reach remote vst" << endl;
        return false;
    }
    return true;
}

void PeerConnectionManager::onWebrtcDataChannelConnection()
{
    LOG(verbose) << __PRETTY_FUNCTION__ << endl;
    string api = GET_CONFIG().remote_vst_address + string("/api/v1/sensor/list");
    string response;
    // get remote sensor list
    if (!curlGetRequest(api, response))
    {
        LOG(error) << "Failed to get sensor list from cloud" << endl;
        return;
    }
    // convert sensor list to JSON
    Json::Value jsonResponse = stringToJson(response);
    if (jsonResponse.isArray())
    {
        if (m_deviceManager.get() != nullptr)
        {
            vector<shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
            // iterate over local sensors
            for (uint32_t i = 0; i < sensors.size(); i++)
            {
                bool isSensorPresent = false;
                if (sensors[i].get())
                {
                    for (unsigned int j = 0; j < jsonResponse.size(); j++)
                    {
                        Json::Value sensorInfo = jsonResponse[j];
                        const string sensorId = sensorInfo.get("sensorId", EMPTY_STRING).asString();
                        LOG(verbose) << "remote sensor Id: " << sensorId << endl;
                        if (sensors[i]->id == sensorId)
                        {
                            // sensor already present on remote sensor
                            isSensorPresent = true;
                            break;
                        }
                    }
                    if (!isSensorPresent)
                    {
                        // sensor is not present on remote sensor, add it
                        LOG(info) << "sensor is not present on remote sensor, adding " << sensors[i]->id << endl;
                        vst_common::addSensorToRemoteDevice(sensors[i], m_deviceManager);
                        isSensorPresent = false;
                    }
                }
            }
        }
    }
}

void PeerConnectionManager::peerConnectionMonitorTask()
{
    LOG(info) << "Started the peer Connection Monitor task" << endl;
    m_exitPeerConnThread = false;

    try
    {
        while (m_exitPeerConnThread == false)
        {
            if (!GET_CONFIG().remote_vst_address.empty())
            {
                if((m_deviceManager != nullptr) && (!GET_DATA_CHANNEL()->isConnected(m_deviceManager->getDeviceId())) && (checkIfRemoteReachable()))
                {
                    startDataChannel();
                    onWebrtcDataChannelConnection();
                }
            }
            std::vector<std::pair<std::string, struct timeval>> retry_list;
            {
                std::lock_guard<std::mutex> lock(m_remoteRetryListLock);
                retry_list = m_remoteRetryStreams;
            }
            if (retry_list.size() && checkIfRemoteReachable())
            {
                std::vector<std::string> retry_success_list;
                for(auto stream_pair : retry_list)
                {
                    if (m_exitPeerConnThread)
                    {
                        break;
                    }
                    std::string stream_id = stream_pair.first;
                    if (isStreamExists(stream_id))
                    {
                        LOG(info) << "Already exists peerconnection " << stream_id << endl;
                        retry_success_list.push_back(stream_id);
                        continue;
                    }

                    struct timeval timeNow;
                    gettimeofday(&timeNow, nullptr);
                    unsigned int wait_period = timevaldiff(stream_pair.second, timeNow) / 1000000;
                    if (wait_period < REMOTE_PEER_CONNECTION_RETRY_INTERVAL)
                    {
                        continue;
                    }

                    shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(stream_id);
                    if (sensor.get() && sensor->getSensorStatus() == SensorStatusOnline)
                    {
                        shared_ptr<StreamInfo> stream_info = sensor->getStream (stream_id);
                        if (stream_info.get() && stream_info->getErrorStatus().first == STREAM_STATUS_STREAMING)
                        {
                            LOG(warning) << "Retry peerconnection for " << stream_id << endl;
                            checkAndAddSensorToRemote(stream_id);
                            int ret = startPeerConnectionForRemoteDevice(stream_info);
                            if (ret == 0)
                            {
                                LOG(info) << "Retry success for " << stream_id << endl;
                                retry_success_list.push_back(stream_id);
                            }
                        }
                    }
                }

                // Delete success retries from original list
                {
                    std::lock_guard<std::mutex> lock(m_remoteRetryListLock);
                    for (auto stream_id: retry_success_list)
                    {
                        for (auto it = m_remoteRetryStreams.begin(); it != m_remoteRetryStreams.end(); ++it)
                        {
                            if (it->first == stream_id)
                            {
                                m_remoteRetryStreams.erase(it);
                                break;
                            }
                        }
                    }
                }
            }

            // 10sec interval for monitoring peer connections.
            {
                std::unique_lock<std::mutex> lck(m_peerConnThreadMutex);
                if (m_exitPeerConnThread == false)
                {
                    m_cvPeerConnThread.wait_for(lck, std::chrono::seconds(REMOTE_PEER_CONNECTION_RETRY_INTERVAL));
                }
            }
        }
    }
    catch(const std::invalid_argument& e)
    {
        LOG(error) << "exception in peerConnectionMonitorTask, error: " << e.what() << endl;
        return;
    }

    //cleanup
    LOG(info) << "Exiting the peer Connection Monitor task" << endl;
}

Json::Value PeerConnectionManager::getLocalIceCandidates(shared_ptr<PeerConnection>& peerConnection,
    const Json::Value& in, const Json::Value& req_info, bool expectRelayCandidates)
{
    int waitCountForRelayCandidate = 3;
    string peer_id = in.get("peerid", EMPTY_STRING).asString();

retryForRelayCandidate:
    Json::Value local_iceCandidates;
    peerConnection->post("getIceCandidate", peer_id, in, req_info, local_iceCandidates);
    if (expectRelayCandidates == true)
    {
        bool isRelayCandidateFound = false;
        for (Json::Value::ArrayIndex i = 0; i != local_iceCandidates.size(); ++i)
        {
            Json::Value current_candidate = local_iceCandidates[i];
            if (current_candidate["candidate"].isString() == false)
            {
                continue;
            }
            string ice_candidate = current_candidate["candidate"].asString();
            if (ice_candidate.find("typ relay") != string::npos)
            {
                isRelayCandidateFound = true;
                break;
            }
        }
        if (isRelayCandidateFound == false && waitCountForRelayCandidate > 0)
        {
            waitCountForRelayCandidate--;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            goto retryForRelayCandidate;
        }
    }
    return local_iceCandidates;
}

string PeerConnectionManager::getMyPublicAddress(shared_ptr<PeerConnection>& peerConnection)
{
    string publicAddr;
    int retries = 0;
    const int maxRetries = 3;
    do
    {
        publicAddr = peerConnection->getPublicAddress();
        if (!publicAddr.empty())
        {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    } while (++retries < maxRetries);

    return publicAddr;
}

int PeerConnectionManager::startDataChannel()
{
    LOG(info) << __METHOD_NAME__ << endl;

    if (m_deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        return -1;
    }

    const string clientId = m_deviceManager->getDeviceId();
    bool isTurnServerConfigured = false;
    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = clientId;
    pc_options["isDataChannel"] = "true";

    string url = GET_CONFIG().remote_vst_address;

    /* Get the iceServers from the cloud/master vst */
    string api = "/api/v1/streambridge/iceServers";
    string answer_str;
    vector<string> iceServerList;
    auto ngcHeaders = VmsConfigManager::getInstance()->getNGCAuthHeaders();
    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to reach remote vst" << endl;
        return false;
    }
    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to get IceServers" << endl;
        Json::Value res;
        getIceServers(clientId, "", res);
    }
    Json::Value json_value = stringToJson(answer_str);
    Json::Value iceServers = json_value.get("iceServers", Json::Value::null);
    if (iceServers.isArray())
    {
        for (uint32_t i = 0; i < iceServers.size(); i++)
        {
            string iceserver_url = iceServers[i].get("urls", EMPTY_STRING).asString();
            if (iceserver_url.empty())
            {
                continue;
            }

            if (iceserver_url.find("turn:") != string::npos)
            {
                iceserver_url.erase(iceserver_url.find("turn:"), 5);
                string username = iceServers[i].get("username", EMPTY_STRING).asString();
                string credential = iceServers[i].get("credential", EMPTY_STRING).asString();
                iceserver_url = string("turn:") + username + string(":") + credential + string("@") + iceserver_url;
                isTurnServerConfigured = true;
            }
            iceServerList.push_back(iceserver_url);
        }
        {
            std::lock_guard<std::mutex> lock(m_iceServerPeerIdMapLock);
            m_iceServerPeerIdMap[clientId] = iceServerList;
        }
    }

    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
    if (!peerConnection.get())
    {
        LOG(error) << "Failed to initialize data channel " << clientId << endl;
        return -1;
    }

    insertPeerConnection(clientId, peerConnection);
    peerConnection->setDeviceId(clientId);

    Json::Value in, req_info, response;
    in["peerid"] = clientId;
    in["streamId"] = clientId;
    in["isDataChannel"] = true;
    VmsErrorCode ret = peerConnection->post("startConnection", clientId, in, req_info, response);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "start Peer Connection failed " << clientId << endl;
        return -1;
    }

    Json::Value offer;
    ret = peerConnection->post("createOffer", clientId, in, req_info, offer);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Create offer failed " << clientId << endl;
        return -1;
    }
    LOG(verbose) << "offer: " << offer.toStyledString() << endl;

    Json::Value payload, options;
    options["rtptransport"] = "udp";
    options["timeout"] = 60;
    payload["options"] = options;
    payload["peerId"] = clientId;
    payload["sessionDescription"] = offer;
    payload["isDataChannel"] = true;
    payload["clientIpAddr"] = getMyPublicAddress(peerConnection);

    api = "/api/v1/streambridge/stream/start";
    answer_str.clear();
    LOG(verbose) << "payload: " << payload << endl;
    if (!curlPostRequest(url + api, answer_str, payload, ngcHeaders))
    {
        LOG(error) << "Could not call /api/v1/streambridge/stream/start " << clientId << endl;
        Json::Value dummy_json;
        peerConnection->post("removeTracks", clientId, dummy_json, dummy_json, dummy_json);
        erasePeerConnection(clientId);
        return -1;
    }
    Json::Value answer = stringToJson(answer_str);
    LOG(verbose) << "result:\n" << answer.toStyledString() << endl;

    Json::Value dummy_response;
    in["sessionDescription"] = answer;
    peerConnection->post("setAnswer", clientId, in, req_info, dummy_response);

    api = "/api/v1/streambridge/iceCandidate";
    Json::Value local_iceCandidates = getLocalIceCandidates(peerConnection, in, req_info, isTurnServerConfigured);
    vector<async::task<void>> m_curlRequestsTasks;
    for (Json::Value::ArrayIndex i = 0; i != local_iceCandidates.size(); ++i)
    {
        m_curlRequestsTasks.push_back(async::spawn([=]() -> void
        {
            string answer_string;;
            Json::Value payload_local;
            payload_local["candidate"] = local_iceCandidates[i];
            payload_local["peerId"] = clientId;
            if (!curlPostRequest(url + api, answer_string, payload_local, ngcHeaders))
            {
                LOG(error) << "Could not call api/addIceCandidate " << clientId << endl;
            }
        }));
    }
    for (Json::Value::ArrayIndex i = 0; i != local_iceCandidates.size(); ++i)
    {
        m_curlRequestsTasks[i].get();
    }

    api = "/api/v1/streambridge/iceCandidate?peerId=" + clientId;
    Json::Value remote_iceCandidates;
    answer_str.clear();

    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to get IceCandidates " << clientId << endl;
        return -1;
    }
    remote_iceCandidates = stringToJson(answer_str);
    for (Json::Value::ArrayIndex i = 0; i != remote_iceCandidates.size(); ++i)
    {
        Json::Value current_candidate = remote_iceCandidates[i];
        string sdp_mid = current_candidate["sdpMid"].asString();
        int sdp_mlineindex = current_candidate["sdpMLineIndex"].asInt();
        string sdp_name = current_candidate["candidate"].asString();
        std::unique_ptr<webrtc::IceCandidateInterface> candidate(webrtc::CreateIceCandidate(sdp_mid, sdp_mlineindex, sdp_name, nullptr));
        rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = peerConnection->getRtcPeerConnection();
        rtcPeerConnection->AddIceCandidate(candidate.get());
    }
    return 0;
}

// Called by edge device, webrtc out connection.
int PeerConnectionManager::startPeerConnectionForRemoteDevice(shared_ptr<StreamInfo> stream_info)
{
    LOG(info) << __METHOD_NAME__ << endl;
    string stream_id    = stream_info->id;
    string stream_name  = stream_info->name;
    string stream_codec = stream_info->settings.encoderValues.encoding;
    string peer_id = stream_id;
    bool isTurnServerConfigured = false;
    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = peer_id;

    bool sensorPresent = isSensorPresentOnRemote(stream_id);
    if (!sensorPresent)
    {
        LOG(warning) << "Sensor not present on remote " << stream_id << " , add to retry list" << endl;
        return -1;
    }

    string url = GET_CONFIG().remote_vst_address;

    /* Get the iceServers from the cloud/master vst */
    string api = "/api/v1/streambridge/iceServers";
    string answer_str;
    vector<string> iceServerList;
    auto ngcHeaders = VmsConfigManager::getInstance()->getNGCAuthHeaders();
    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to reach remote vst" << endl;
        return false;
    }
    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to get IceServers" << endl;
        Json::Value res;
        getIceServers(peer_id, "", res);
    }
    Json::Value json_value = stringToJson(answer_str);
    Json::Value iceServers = json_value.get("iceServers", Json::Value::null);
    if (iceServers.isArray())
    {
        for (uint32_t i = 0; i < iceServers.size(); i++)
        {
            string iceserver_url = iceServers[i].get("urls", EMPTY_STRING).asString();
            if (iceserver_url.empty())
            {
                continue;
            }

            if (iceserver_url.find("turn:") != string::npos)
            {
                iceserver_url.erase(iceserver_url.find("turn:"), 5);
                string username = iceServers[i].get("username", EMPTY_STRING).asString();
                string credential = iceServers[i].get("credential", EMPTY_STRING).asString();
                iceserver_url = string("turn:") + username + string(":") + credential + string("@") + iceserver_url;
                isTurnServerConfigured = true;
            }
            iceServerList.push_back(iceserver_url);
        }
        {
            std::lock_guard<std::mutex> lock(m_iceServerPeerIdMapLock);
            m_iceServerPeerIdMap[peer_id] = iceServerList;
        }
    }

    /* Checking codec, if codec is H264 then only setting quality as pass_through */
    if(GET_CONFIG().webrtc_sender_quality == PASS_THROUGH_QUALITY && (iequals(stream_codec, "H264") || iequals(stream_codec, "H265")))
    {
        pc_options["quality"] = PASS_THROUGH_QUALITY;
    }

    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
    if (!peerConnection.get())
    {
        LOG(error) << "Failed to initialize PeerConnection " << stream_id << endl;
        return -1;
    }
    insertPeerConnection(peer_id, peerConnection);
    peerConnection->setDeviceId(stream_id);

    Json::Value in, req_info, response;
    in["peerid"] = peer_id;
    in["streamId"] = stream_id;
    VmsErrorCode ret = peerConnection->post("startConnection", peer_id, in, req_info, response);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "start Peer Connection failed " << stream_id << endl;
        return -1;
    }

    Json::Value offer;
    ret = peerConnection->post("createOffer", peer_id, in, req_info, offer);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Create offer failed " << stream_id << endl;
        return -1;
    }
    LOG(verbose) << "offer: " << offer.toStyledString() << endl;

    Json::Value payload, options;
    options["rtptransport"] = "udp";
    options["timeout"] = 60;
    payload["options"] = options;
    payload["peerId"] = peer_id;
    payload["sessionDescription"] = offer;
    payload["deviceName"] = stream_name;
    payload["isClient"] = true;
    payload["clientIpAddr"] = getMyPublicAddress(peerConnection);
    payload["codec"] = stream_codec;

    api = "/api/v1/streambridge/stream/start";
    answer_str.clear();
    LOG(verbose) << "payload: " << payload << endl;
    if (!curlPostRequest(url + api, answer_str, payload, ngcHeaders))
    {
        LOG(error) << "Could not call /api/v1/streambridge/stream/start " << stream_id << endl;
        Json::Value dummy_json;
        peerConnection->post("removeTracks", peer_id, dummy_json, dummy_json, dummy_json);
        erasePeerConnection(peer_id);
        return -1;
    }
    Json::Value answer = stringToJson(answer_str);
    LOG(verbose) << "result:\n" << answer.toStyledString() << endl;

    Json::Value dummy_response;
    in["sessionDescription"] = answer;
    peerConnection->post("setAnswer", peer_id, in, req_info, dummy_response);

    api = "/api/v1/streambridge/iceCandidate";
    Json::Value local_iceCandidates = getLocalIceCandidates(peerConnection, in, req_info, isTurnServerConfigured);
    vector<async::task<void>> m_curlRequestsTasks;
    for (Json::Value::ArrayIndex i = 0; i != local_iceCandidates.size(); ++i)
    {
        m_curlRequestsTasks.push_back(async::spawn([=]() -> void
        {
            string answer_string;;
            Json::Value payload_local;
            payload_local["candidate"] = local_iceCandidates[i];
            payload_local["peerId"] = peer_id;
            if (!curlPostRequest(url + api, answer_string, payload_local, ngcHeaders))
            {
                LOG(error) << "Could not call api/addIceCandidate " << stream_id << endl;
            }
        }));
    }
    for (Json::Value::ArrayIndex i = 0; i != local_iceCandidates.size(); ++i)
    {
        m_curlRequestsTasks[i].get();
    }

    api = "/api/v1/streambridge/iceCandidate?peerId=" + peer_id;
    Json::Value remote_iceCandidates;
    answer_str.clear();
    if (!curlGetRequest(url + api, answer_str, ngcHeaders))
    {
        LOG(error) << "Failed to get IceCandidates " << stream_id << endl;
        return -1;
    }
    remote_iceCandidates = stringToJson(answer_str);
    for (Json::Value::ArrayIndex i = 0; i != remote_iceCandidates.size(); ++i)
    {
        Json::Value current_candidate = remote_iceCandidates[i];
        string sdp_mid = current_candidate["sdpMid"].asString();
        int sdp_mlineindex = current_candidate["sdpMLineIndex"].asInt();
        string sdp_name = current_candidate["candidate"].asString();
        std::unique_ptr<webrtc::IceCandidateInterface> candidate(webrtc::CreateIceCandidate(sdp_mid, sdp_mlineindex, sdp_name, nullptr));
        rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = peerConnection->getRtcPeerConnection();
        rtcPeerConnection->AddIceCandidate(candidate.get());
    }
    return 0;
}

void PeerConnectionManager::onStreamStatusChange(const string &url
                                                , const StreamStatus newStatus
                                                , StreamEncParam& details)
{
    if (newStatus != StreamStatus::STREAM_STATUS_STREAMING)
    {
        LOG(warning) << "Stream status not streaming " << secureUrlForLogging(url) << endl;
        return;
    }

    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager == nullptr || (m_deviceManager->type != TYPE_VST && m_deviceManager->type != TYPE_MMS))
    {
        LOG(error) << "Skipping creating PeerConnection" << endl;
        return;
    }

    std::vector<shared_ptr<StreamInfo>> streamList = m_deviceManager->getStreamList();
    for (auto const& stream : streamList)
    {
        if (stream->live_proxy_url != url)
        {
            continue;
        }
        // Check if webrtc connection already exists for this stream
        if (isStreamExists(stream->id))
        {
            LOG(warning) << "PeerConnection already exists for stream " << stream->id
                        << " Skipping" << endl;
            return;
        }
        // Set the stream status to streaming
        stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING, "stream_streaming"));
        checkAndAddSensorToRemote(stream->id);
        int ret = startPeerConnectionForRemoteDevice(stream);
        if (ret != 0)
        {
            LOG(warning) << "Adding stream to remote retry list " << stream->id << endl;
            struct timeval timeNow;
            gettimeofday(&timeNow, nullptr);
            std::lock_guard<std::mutex> lock(m_remoteRetryListLock);
            m_remoteRetryStreams.push_back(std::make_pair(stream->id, timeNow));
        }
    }
}

void PeerConnectionManager::checkAndAddSensorToRemote(const string& streamId)
{
    bool sensorPresent = isSensorPresentOnRemote(streamId);
    if (!sensorPresent)
    {
        LOG(info) << "Trying to add sensor to remote " << streamId << endl;
        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(streamId);
        if (sensor.get())
        {
            vst_common::addSensorToRemoteDevice(sensor, m_deviceManager);
        }
    }
}

VmsErrorCode PeerConnectionManager::streamSettings( const Json::Value& req_info,
                                                    const Json::Value &in,
                                                    Json::Value &response)
{
    /* Currently Stream Settings API is applicable to VMS only */
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() != TYPE_VST && m_deviceManager->getDeviceType() != TYPE_MMS)
    {
        LOG(error) << "Stream settings not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Stream setting not supported")
        return VmsErrorCode::VMSNotSupportedError;
    }

    std::string peerId = in.get("peerId", EMPTY_STRING).asString();
    if (peerId.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "PeerID expected in request")
        return VmsErrorCode::InvalidParameterError;
    }
    LOG(verbose) << "PEER ID: " << peerId << endl;

    shared_ptr<IWebrtcConnection> peerConnection = getPeerConnection(peerId);
    CHECK_PEER_ERROR(peerConnection, response);
    return peerConnection->post("streamSettings", peerId, in, req_info, response);
}

void PeerConnectionManager::parseRemoteAnswer(const Json::Value& in, Json::Value& response)
{
    std::string type;
    std::string sdp;
    Json::Value jmessage = in.get("sessionDescription", EMPTY_STRING);
    if (!rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionTypeName, &type) || !rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionSdpName, &sdp))
    {
        LOG(error) << "Can't parse received message." << endl;
        return;
    }
    response["type"] = type;
    response["sdp"] = sdp;
}

VmsErrorCode PeerConnectionManager::startUdpToWebrtcConnection(const Json::Value& req_info, const Json::Value &in, Json::Value &answer)
{
    LOG(info) << __METHOD_NAME__ << endl;
    string peerId = in.get("peerId", EMPTY_STRING).asString();
    if (peerId.length() > 2 && peerId.substr(peerId.length() - 2) != "_1")
    {
        LOG(error) << "Error with avatar peerId " << peerId << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    string remote_addr = req_info.get("remote_addr", EMPTY_STRING).asString();
    string clientPublicIpAddr = in.get("clientIpAddr", EMPTY_STRING).asString();
    if (clientPublicIpAddr.empty() && !remote_addr.empty())
    {
        clientPublicIpAddr = remote_addr;
    }
    LOG(info) << "Client ip address clientPublicIpAddr:" << clientPublicIpAddr << ", remote_addr:" << remote_addr << endl;
    m_externalPeerInfo[peerId]["clientIpAddr"] = clientPublicIpAddr;

    string streamId = peerId;
    string sensorId = peerId.substr(0, peerId.length() - 2);
    string username = req_info.get("username", EMPTY_STRING).asString();
    VmsErrorCode ret = createWebrtcDevice(in, sensorId, username, nullptr);
    if (ret != VmsErrorCode::NoError)
    {
        if (ret == VmsErrorCode::VMSNotSupportedError)
        {
            /* Notify client that offer will be generated from vst */
            answer["apiKey"] = "api/v1/setAnswer";
            answer["peerId"] = streamId;
            answer["wait_for_offer"] = true;
            notify("camera_add", sensorId);
            LOG(warning) << "Informing client to wait for offer, Since vst will generate the offer ..." << endl;
        }
        LOG(error) << "Can't create webrtc-input device" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, answer, "Can't create webrtc-input device")
        return ret;
    }

    std::unordered_map<string, string> pc_options;
    pc_options["peerid"] = streamId;
    shared_ptr<PeerConnection> peerConnection = std::dynamic_pointer_cast<PeerConnection>(this->CreatePeerConnection(pc_options));
    if (!peerConnection.get())
    {
        LOG(error) << "Failed to initialize PeerConnection";
        return VmsErrorCode::VMSInternalError;
    }
    else if (!peerConnection->getRtcPeerConnection().get())
    {
        LOG(error) << "Failed to initialize PeerConnection";
        peerConnection.reset();
        return VmsErrorCode::VMSInternalError;
    }
    else
    {
        Json::Value temp_in, dummy_response;
        temp_in["audioPlayout"] = false;
        peerConnection->post("setAudioPlayout", streamId, temp_in, temp_in, dummy_response);

        insertPeerConnection(streamId, peerConnection);
        GET_PROMETHEUS()->incrementWebrtcStreams();
    }
    // Sensor Id needed to send iceCandidate on websocket for Avatar stream
    peerConnection->setDeviceId(sensorId);

    // Set Remote-Offer
    {
        Json::Value offerIn, dummy_response;
        offerIn = in;
        offerIn["streamId"] = streamId;
        offerIn["sensorId"] = sensorId;
        peerConnection->post("setOffer", streamId, offerIn, offerIn, dummy_response);
    }

    // Add Track
    {
        VmsErrorCode ret = VmsErrorCode::NoError;
        Json::Value in, dummy_req, dummy_response;
        in["device_id"] = sensorId;
        in["stream_id"] = streamId;
        ret = peerConnection->post("addUdpTrack", streamId, in, dummy_req, dummy_response);
        if (ret != VmsErrorCode::NoError)
        {
            LOG(error) << "addUdpTrack failed" << endl;
            return ret;
        }
    }

    // Get Answer
    {
        Json::Value offerIn;
        offerIn = in;
        offerIn["streamId"] = streamId;
        offerIn["sensorId"] = sensorId;
        peerConnection->post("getAnswer", streamId, offerIn, offerIn, answer);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnectionManager::onWsDisconnect(const string connectionId)
{
    auto it = m_peerInputConnections.find(connectionId);
    if (it == m_peerInputConnections.end())
    {
        LOG(error) << "No input peer found for " << connectionId << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    auto peerConnection = std::dynamic_pointer_cast<PeerConnection>(it->second);
    if (peerConnection == nullptr)
    {
        LOG(error) << "PeerConnection object not present for " << connectionId << endl;
        return VmsErrorCode::VMSInternalError;
    }

    rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = peerConnection->getRtcPeerConnection();
    if (rtcPeerConnection)
    {
        webrtc::PeerConnectionInterface::PeerConnectionState peerConnection_state
            = rtcPeerConnection->peer_connection_state();
        webrtc::PeerConnectionInterface::IceConnectionState ice_state
            = rtcPeerConnection->ice_connection_state();
        if (peerConnection_state >= webrtc::PeerConnectionInterface::PeerConnectionState::kDisconnected ||
            ice_state >= webrtc::PeerConnectionInterface::kIceConnectionDisconnected)
        {
            Json::Value temp_json;
            hangUp(connectionId, temp_json, temp_json);
        }
    }
    return VmsErrorCode::NoError;
}

// Client map management functions
bool PeerConnectionManager::ClientSearch(const std::string& peerid, ClientInfo& client)
{
    std::lock_guard<std::mutex> lock(m_clientMutex);
    auto it = m_clientMap.find(peerid);
    if (it != m_clientMap.end())
    {
        client = it->second;
        return true;
    }
    return false;
}

void PeerConnectionManager::ClientInsert(const std::string& peerid, const ClientInfo& client)
{
    std::lock_guard<std::mutex> lock(m_clientMutex);
    m_clientMap[peerid] = client;
}

void PeerConnectionManager::ClientErase(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_clientMutex);
    auto it = m_clientMap.find(peerid);
    if (it != m_clientMap.end())
    {
        m_clientMap.erase(it);
    }
}

std::vector<std::pair<std::string, ClientInfo>> PeerConnectionManager::GetAllClients()
{
    std::lock_guard<std::mutex> lock(m_clientMutex);
    std::vector<std::pair<std::string, ClientInfo>> result;
    for (const auto& pair : m_clientMap)
    {
        result.push_back(pair);
    }
    return result;
}

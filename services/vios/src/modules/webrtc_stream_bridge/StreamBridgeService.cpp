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

#include "StreamBridgeService.h"
#include <jsoncpp/json/json.h>
#include "error_code.h"
#include "config.h"
#include "vst_common.h"

#define STREAMBRIDGE_API "/api/v1/streambridge/stream/*"
#define EXECUTE_FUNC(func_key, arg1, arg2, arg3, agr4)  m_callbackMap[func_key](arg1, arg2, arg3, agr4);

static string streamBridgeApiList = R"([
        {"method": "GET - Get streams list", "endpoint": "api/v1/streambridge/streams"},
        {"method": "POST - Start streaming", "endpoint": "api/v1/streambridge/stream/start"},
        {"method": "POST - Stop streaming", "endpoint": "api/v1/streambridge/stream/stop"},
        {"method": "GET - Get stream status", "endpoint": "api/v1/streambridge/stream/status"},
        {"method": "POST - Share SDP with streambridge service", "endpoint": "api/v1/streambridge/setAnswer"},
        {"method": "GET - Get list of ICE candidates", "endpoint": "api/v1/streambridge/iceCandidate"},
        {"method": "POST - Post ICE candidate", "endpoint": "api/v1/streambridge/iceCandidate"},
        {"method": "GET - Get list of ICE servers", "endpoint": "api/v1/streambridge/iceServers"},
        {"method": "GET - Get streambridge service configuration", "endpoint": "api/v1/streambridge/configuration"},
        {"method": "POST - POST streambridge service configuration", "endpoint": "api/v1/streambridge/configuration"},
        {"method": "GET - Get version", "endpoint": "api/v1/streambridge/version"},
        {"method": "GET - Get API help", "endpoint": "api/v1/streambridge/help"}
])";

extern "C" void* createStreamBridgeObject()
{
    std::string publishFilter(".*");
    webrtc::AudioDeviceModule::AudioLayer audioLayer = webrtc::AudioDeviceModule::kPlatformDefaultAudio;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    std::shared_ptr<PeerConnectionManager> pcm = std::make_shared<PeerConnectionManager>("streambridge", audioLayer, publishFilter, deviceManager, true);

    return static_cast<void*>(static_cast<IVstModule*>(new StreamBridgeService(pcm, deviceManager)));
}

extern "C" void deleteStreamBridgeObject(IVstModule* object)
{
    StreamBridgeService* stream_bridge = static_cast<StreamBridgeService*>(object);
    delete stream_bridge;
}

StreamBridgeService::StreamBridgeService(std::shared_ptr<PeerConnectionManager> peerConnectionManager,
                                    std::shared_ptr<DeviceManager> deviceManager)
                                    : m_peerConnectionManager(peerConnectionManager), m_deviceManager(deviceManager)
{
    if (m_peerConnectionManager.get() == nullptr)
    {
        LOG(error) << "Cannot correctly initialize StreamBridge without PeerConnectionManager" << endl;
        return;
    }

    m_func["/api/v1/streambridge/stream/start"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        bool isDataChannel = in.get("isDataChannel", false).asBool();
        bool isClient = in.get("isClient", false).asBool();
        Json::Value data = in;
        if (!isClient && !isDataChannel)
        {
            data["createStream"] = true;
        }
        if (iequals(requestMethod, "post"))
        {
            VmsErrorCode ret = m_peerConnectionManager->startStream(req_info, data, response);
            return ret;
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/stream/stop"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            VmsErrorCode ret = m_peerConnectionManager->stopStream(req_info, in, response);
            return ret;
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/streams"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return vst_common::getSensorStreamListFromDB(m_deviceManager, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/stream/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            std::string peerId = "", overlay = "";
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
                if(!CivetServer::getParam(queryString, "overlay", overlay))
                {
                    overlay = "false";
                }
            }
            return m_peerConnectionManager->getStreamStatus(peerId, overlay, req_info, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/stream/stats"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        if (GET_CONFIG().enable_perf_logging == false)
        {
            LOG(error) << "Stream stats not enabled";
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Stream stats not enabled")
            return VmsErrorCode::MethodNotAllowedError;
        }
        std::string peerid = "";
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerId", peerid);
            // backward compatibility
            if (peerid.empty())
            {
                CivetServer::getParam(query_string, "peerid", peerid);
            }
        }
        string deviceid = "";
        if (peerid.empty())
        {
            CivetServer::getParam(query_string, "sensorId", deviceid);
            // backward compatibility
            if (deviceid.empty())
            {
                CivetServer::getParam(query_string, "deviceid", deviceid);
            }
        }
        return m_peerConnectionManager->getStreamStats(peerid, response, deviceid);
    };

    m_func["/api/v1/streambridge/setAnswer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(warning) << "streambridge/setAnswer" << endl;
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            std::string peerId;
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
            }
            if (m_remoteConnections.find(peerId) != m_remoteConnections.end())
            {
                Json::Value req_info;
                req_info["peerId"] = peerId;
                m_peerConnectionManager->parseRemoteAnswer(in, req_info);
                VmsErrorCode ret = EXECUTE_FUNC("/grpc/remotePeerAnswer", req_info, req_info, req_info, nullptr)
                return ret;
            }
            return m_peerConnectionManager->setAnswer(peerId, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/iceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (requestMethod == UNKNOWN_STRING)
        {
            LOG(error) << "Malformed HTTP request" << endl;
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        if (iequals(requestMethod, "get"))
        {
            std::string peerId;
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
            }
            return m_peerConnectionManager->getIceCandidateList(peerId, response);
        }
        else if (iequals(requestMethod, "post"))
        {
            std::string peerId;
            Json::Value candidate;
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
            peerId = in.get("peerId", EMPTY_STRING).asString();
            candidate = in.get("candidate", EMPTY_STRING);
            if (peerId.empty() == true)
            {
                LOG(warning) << "peerId is empty";
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
                return VmsErrorCode::InvalidParameterError;
            }
            if (candidate.empty() == true)
            {
                LOG(warning) << "candidate is empty";
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "candidate is empty")
                return VmsErrorCode::InvalidParameterError;
            }
            if (m_remoteConnections.find(peerId) != m_remoteConnections.end())
            {
                Json::Value req_info = in;
                VmsErrorCode ret = EXECUTE_FUNC("/grpc/remotePeerCandidate", req_info, req_info, req_info, nullptr)
                return ret;
            }
            return m_peerConnectionManager->addIceCandidate(peerId, in, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            return VmsErrorCode::MethodNotAllowedError;
        }
    };

    m_func["/api/v1/streambridge/iceServers"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            std::string peerId, remoteAddr;
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
            }
            remoteAddr = req_info.get("remote_addr", EMPTY_STRING).asString();
            return m_peerConnectionManager->getIceServers(peerId, remoteAddr, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/streambridge/configuration"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleStreambridgeConfiguration(req_info, in, response);
    };

    m_func["/api/v1/streambridge/version"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getVersion(req_info, in, out);
    };

    m_func["/api/v1/streambridge/help"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getStreambridgeHelp(req_info, in, out);
    };

    m_func["/grpc/addUdpTrack"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string peerid = in.get("peerid", EMPTY_STRING).asString();
        string stream_id = in.get("stream_id", EMPTY_STRING).asString();
        if (m_peerConnectionManager)
        {
            m_peerConnectionManager->AddTrackToExistingPeerConnection(peerid, stream_id);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/udpToWebrtc"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string sensorId = in.get("sensorId", EMPTY_STRING).asString();
        string streamId = in.get("streamId", EMPTY_STRING).asString();
        if (m_peerConnectionManager)
        {
            m_peerConnectionManager->udpToWebrtc(sensorId, streamId);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/setWebrtcClientParams"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string sensorId = in.get("sensorId", EMPTY_STRING).asString();
        if (m_peerConnectionManager)
        {
            m_peerConnectionManager->setWebrtcClientParams(sensorId, in);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/remotePeerOffer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        if (m_peerConnectionManager)
        {
            string sensorId = in.get("sensorId", EMPTY_STRING).asString();
            string streamId = in.get("streamId", EMPTY_STRING).asString();
            if (sensorId.empty() || streamId.empty() || m_remoteConnections.find(streamId) != m_remoteConnections.end())
            {
                LOG(error) << "Stream id not present or already used" << endl;
                return VmsErrorCode::InvalidParameterError;
            }
            m_remoteConnections.insert(streamId);
            m_peerConnectionManager->SendRemotePeerOffer(sensorId, streamId, in);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/remotePeerSendIceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        if (m_peerConnectionManager)
        {
            string sensorId = in.get("sensorId", EMPTY_STRING).asString();
            string streamId = in.get("streamId", EMPTY_STRING).asString();
            if (streamId.empty())
            {
                LOG(error) << "Candidate entry not present for stream id: " << streamId << endl;
                return VmsErrorCode::InvalidParameterError;
            }
            m_peerConnectionManager->SendRemotePeerIceCandidate(sensorId, streamId, in);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/remotePeerErase"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string streamId = in.get("streamId", EMPTY_STRING).asString();
        if (m_remoteConnections.find(streamId) != m_remoteConnections.end())
        {
            m_remoteConnections.erase(streamId);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/v1/streaming/creds"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        if (m_peerConnectionManager)
        {
            m_peerConnectionManager->updateRpStunServer(in);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/ws/streambridge/disconnect"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string connectionId = in.get("connectionId", EMPTY_STRING).asString();
        if (connectionId.empty())
        {
            LOG(error) << "WS connectionId missing" << endl;
            return VmsErrorCode::InvalidParameterError;
        }
        return handleWsDisconnect(connectionId);
    };

    m_func[STREAMBRIDGE_API] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleStreambridgeAPIrequest(req_info, in, out, conn);
    };

}

StreamBridgeService::~StreamBridgeService()
{
    if (m_peerConnectionManager)
    {
        m_peerConnectionManager->DestroyPeerConnections();
    }
}

VmsErrorCode StreamBridgeService::handleStreambridgeAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                                struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();

    string StreambridgeAPI(STREAMBRIDGE_API);
    string path = requestAPI.substr(StreambridgeAPI.size() - 1);
    LOG(info) << "Streambridge API path: " << path << std::endl;
    vector<string> pathArray = splitString(path, "/");
    string sensorId = EMPTY_STRING;
    string streamId;
    string action;
    string subAction;

    if (pathArray.size() > 0)
    {
        streamId = pathArray[0];
        action = pathArray.size() >= 2 ? pathArray[1] : "";
        subAction = pathArray.size() >= 3 ? pathArray[2] : "";
    }
    else
    {
        LOG(error) << "Requested API is not allowed" << std::endl;
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
        return VmsErrorCode::MethodNotAllowedError;
    }

    if(!m_deviceManager->getSensorIdFromStreamId(streamId, sensorId))
    {
        LOG(warning) << "Stream Not Found" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Stream Not Found")
        return VmsErrorCode::InvalidParameterError;
    }

    if (iequals(requestMethod, "get"))
    {
        if (iequals(action, "picture"))
        {
            ret = vst_common::getCameraPicture(m_deviceManager, sensorId, queryString, response);
        }
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode StreamBridgeService::handleStreambridgeConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    DeviceConfig config =  GET_CONFIG();
    if (iequals(requestMethod, "get"))
    {
    #ifndef RELEASE
        response["coturnTurnUrlListWithSecret"] = vectorToJson(config.coturn_turnurl_list_with_secret);
        response["twilioAuthToken"] = config.twilio_auth_token;
        response["twilioAccountSid"] = config.twilio_account_sid;
    #endif // !RELEASE
        response["grpcServerPort"] = config.grpc_server_port;
        response["maxDevicesSupported"] = config.max_sensors_supported;
        response["messageBrokerTopic"] = config.message_broker_topic;
        response["enableNotification"] = config.enable_notification;
        response["enableGrpc"] = config.enable_grpc;
        response["useMessageBroker"] = config.use_message_broker;
        response["redisServerEnvVar"] = config.redis_server_env_var;
        response["rtpUdpPortRange"] = config.rtp_udp_port_range;
        response["udpDropOnLatency"] = config.udp_drop_on_latency;
        response["udpLatencyMs"] = config.udp_latency_ms;
        response["webrtcInAudioSenderMaxBitrate"] = config.webrtc_in_audio_sender_max_bitrate;
        response["webrtcInFixedResolution"] = config.webrtc_in_fixed_resolution;
        response["webrtcInMaxFramerate"] = config.webrtc_in_max_framerate;
        response["webrtcInVideoBitrateThresoldPercentage"] = config.webrtc_in_video_bitrate_thresold_percentage;
        response["webrtcInVideoDegradationPreference"] = config.webrtc_in_video_degradation_preference;
        response["webrtcInVideoSenderMaxFramerate"] = config.webrtc_in_video_sender_max_framerate;
        response["httpPort"] = config.http_port;
        response["maxStreamsSupported"] = config.max_sensors_supported;
        response["maxWebrtcOutConnections"] = config.max_webrtc_out_connections;
        response["maxWebrtcInConnections"] = config.max_webrtc_in_connections;
        response["stunUrlList"] = vectorToJson(config.stunurl_list);
        response["useTwilioStunTurn"] = config.use_twilio_stun_turn;
        response["useReverseProxy"] = config.use_reverse_proxy;
        response["reverseProxyServerAddress"] = config.reverse_proxy_server_address;
        response["staticTurnUrlList"] = vectorToJson(config.static_turnurl_list);
        response["useCoturnAuthSecret"] = config.use_coturn_auth_secret;
        response["useHttpDigestAuthentication"] = config.use_http_digest_authentication;
        response["useHttps"] = config.use_https;
        response["enablePerfLogging"] = config.enable_perf_logging;
        response["useSoftwarePath"] = config.use_software_path;
        response["useWebrtcOutInbuiltEncoder"] = config.use_webrtc_inbuilt_encoder;
        response["vstDataPath"] = config.vst_data_path;
        response["webrtcLatencyMs"] = static_cast<Json::Value::UInt64>(config.webrtc_latency_ms);
        response["webrtcOutEnableInsertSpsPps"] = config.webrtc_out_enable_insert_sps_pps;
        response["webrtcOutSetIdrInterval"] = config.webrtc_out_set_idr_interval;
        response["webrtcOutMinDrcInterval"] = config.webrtc_out_min_drc_interval;
        response["webrtcOutSetIframeInterval"] = config.webrtc_out_set_iframe_interval;
        response["webrtcpeerConnTimeoutSec"] = config.webrtc_peer_conn_timeout_sec;
        response["webserviceAccessControlList"] = config.webservice_access_control_list;
        response["enableGstDebugProbes"] = config.enable_gst_debug_probes;
        response["enableUserCleanup"] = config.enable_user_cleanup;
        response["multiUserExtraOptions"] = vectorToString(config.multi_user_extra_options);
        response["vstIp"] = g_hostIp;
        response["useMultiUser"] = config.use_multi_user;
        response["enableNetworkBandwidthNotification"] = config.enable_network_bandwidth_notification;
        response["enableLatencyLogging"] = config.enable_latency_logging;
        response["webrtc_video_quality_tunning"] = config.webrtc_video_quality_tunning;
        response["useWebrtcHwDec"] = config.use_webrtc_hw_dec;
        response["WebrtcOutDefaultResolution"] = config.webrtc_out_default_resolution;
    }
    else if(iequals(requestMethod, "post"))
    {
        if (in.isNull() || !in.isObject())
        {
            LOG(error) << "Requested API is not allowed" << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
            return VmsErrorCode::MethodNotAllowedError;
        }

        Json::Value stun_urls = in.get("stunUrlList", Json::Value::null);
        if (stun_urls.isArray())
        {
            LOG(info) << "User provided stun urls:" << stun_urls << endl;
            vector<string> vec_stun = jsonToVector(stun_urls);
            if (vec_stun != GET_CONFIG().stunurl_list)
            {
                GET_CONFIG().stunurl_list.clear();
                for (auto stun_uri : vec_stun)
                {
                    if (stun_uri.empty() == false)
                    {
                        GET_CONFIG().stunurl_list.push_back(stun_uri);
                    }
                }
            }
        }

        Json::Value turn_urls = in.get("staticTurnUrlList", Json::Value::null);
        if (turn_urls.isArray())
        {
            LOG(info) << "User provided turn urls:" << turn_urls << endl;
            vector<string> vec_turn = jsonToVector(turn_urls);
            if (vec_turn != GET_CONFIG().static_turnurl_list)
            {
                GET_CONFIG().static_turnurl_list.clear();
                for (auto turn_uri : vec_turn)
                {
                    if (turn_uri.empty() == false)
                    {
                        GET_CONFIG().static_turnurl_list.push_back(turn_uri);
                    }
                }
            }
        }
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode StreamBridgeService::getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    // TODO: Get version from makefile when sensor managment is implemented as lib
    const string deviceType = m_deviceManager->getDeviceType();
    response["type"] = deviceType;
    if(deviceType == TYPE_VST)
    {
        response["version"] = VST_VERSION;
    }
    else if(deviceType == TYPE_MMS)
    {
        response["version"] = MMS_VERSION;
    }
    else if(deviceType == TYPE_STREAMER)
    {
        response["version"] = STREAMER_VERSION;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamBridgeService::getStreambridgeHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    Json::CharReaderBuilder builder;
    std::istringstream iss(streamBridgeApiList);

    std::string errs;
    if (!Json::parseFromStream(builder, iss, &response, &errs))
    {
        LOG(error) << "Failed to parse the API list JSON string: " << errs << endl;
    }
    return VmsErrorCode::NoError;
}

void StreamBridgeService::addRequestHandler(std::map<std::string, httpFunction, std::less<>>& func)
{
    string grpc_api = "/grpc/";
    // register handlers
    for (auto it : func)
    {
        if (it.first.find(grpc_api, 0) == 0)
        {
            m_callbackMap.insert({it.first, it.second});
        }
    }
}

VmsErrorCode StreamBridgeService::handleWsDisconnect(const string connectionId)
{
    if (!m_peerConnectionManager)
    {
        LOG(error) << "PeerConnection Manager instance not created" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    return m_peerConnectionManager->onWsDisconnect(connectionId);
}

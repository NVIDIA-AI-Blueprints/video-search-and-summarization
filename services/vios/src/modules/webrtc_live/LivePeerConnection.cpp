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

#include "LivePeerConnection.h"
#include <jsoncpp/json/json.h>
#include "error_code.h"
#include "config.h"
#include "vst_common.h"
#include "halo_safety.h"
#include "health_probes.h"
#include <filesystem>

#define LIVE_API "/api/v1/live/stream/*"

extern "C" void* createPeerConnectionLiveManagerObject()
{
    std::string publishFilter(".*");
    webrtc::AudioDeviceModule::AudioLayer audioLayer = webrtc::AudioDeviceModule::kPlatformDefaultAudio;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    std::shared_ptr<PeerConnectionManager> pcm = std::make_shared<PeerConnectionManager>("live", audioLayer, publishFilter, deviceManager);

    return static_cast<void*>(static_cast<IVstModule*>(new LivePeerConnection(pcm, deviceManager)));
}

extern "C" void deletePeerConnectionLiveManagerObject(IVstModule* object)
{
    LivePeerConnection* pcm_live = static_cast<LivePeerConnection*>(object);
    delete pcm_live;
}

LivePeerConnection::LivePeerConnection(std::shared_ptr<PeerConnectionManager> peerConnectionManager,
                                                    std::shared_ptr<DeviceManager> deviceManager)
                                                    : m_peerConnectionManager(peerConnectionManager), m_deviceManager(deviceManager)
{
    if (m_peerConnectionManager.get() == nullptr)
    {
        LOG(error) << "Cannot correctly initialize PeerConnection live apis without PeerConnectionManager" << endl;
        return;
    }

    // Overwrite config for livestream service
    if (m_deviceManager && m_deviceManager->needRtspServer == false)
    {
        GET_CONFIG().enable_qos_monitoring = false;
    }

    if (GET_CONFIG().halo_safety_udp_port != -1)
    {
        HaloSafetyCommandListener::getInstance()->start();
    }

    m_func["/api/v1/live/stream/start"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            VmsErrorCode ret = m_peerConnectionManager->startStream(req_info, in, response);
            return ret;
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/live/stream/stop"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/live/stream/query"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            std::string peerId = "";
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
            }
            return m_peerConnectionManager->getQuery(req_info, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

	m_func["/api/v1/live/stream/add"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string url = in.get("url", EMPTY_STRING).asString();
        string id = in.get("id", EMPTY_STRING).asString();

        if (in.isMember("event") && !in["event"].isNull())
        {
            Json::Value event = in["event"];
            id = event.get("camera_id", EMPTY_STRING).asString();
            url = event.get("camera_url", "").asString();
            std::string change = event.get("change", "").asString();

            string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusStreaming);

            if (change != changeLocal || id.empty() || url.empty())
            {
                string error_message = "Invalid parameter";
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
                return VmsErrorCode::InvalidParameterError;
            }
        }

        // Check if parameters are valid
        if (id.empty() || url.empty())
        {
            LOG(error) << "Invalid parameter" << endl;
            string error_message = "Invalid parameter";
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }

        // Validate URL scheme - only accept RTSP/RTSPS URLs for live streaming
        // Reject S3, HTTP, HTTPS, and other non-RTSP protocols
        if (url.compare(0, 7, "rtsp://") != 0 && url.compare(0, 8, "rtsps://") != 0)
        {
            string error_message = "Invalid URL scheme for live stream. Only RTSP/RTSPS URLs are supported. Received: " + url;
            LOG(info) << "Skipping live service for non-RTSP stream: " << id << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Live service only supports RTSP/RTSPS URLs")
            return VmsErrorCode::VMSNotSupportedError;
        }

        // Check and add sensor to cache
        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(id, true);
        if (sensor)
        {
            LOG(info) << "Sensor added in the cache" << endl;
        }
        else
        {
            LOG(error) << "Sensor not added in the cache" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Sensor not added in the cache")
            return VmsErrorCode::VMSInternalError;
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/live/streams"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return vst_common::getSensorStreamListFromDB(m_deviceManager, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/live/stream/pause"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            std::string peerId = "";
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
            peerId = in.get("peerId", EMPTY_STRING).asString();
            if (peerId.empty() == true)
            {
                LOG(warning) << "peerId is empty";
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
                return VmsErrorCode::InvalidParameterError;
            }
            return m_peerConnectionManager->controlStream("pause", peerId, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/live/stream/resume"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            std::string peerId = "";
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
            peerId = in.get("peerId", EMPTY_STRING).asString();
            if (peerId.empty() == true)
            {
                LOG(warning) << "peerId is empty";
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
                return VmsErrorCode::InvalidParameterError;
            }
            return m_peerConnectionManager->controlStream("resume", peerId, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/live/stream/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/live/setAnswer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            std::string peerId;
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            if (queryString.empty() == false)
            {
                CivetServer::getParam(queryString, "peerId", peerId);
            }
            return m_peerConnectionManager->setAnswer(peerId, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/live/iceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
            return m_peerConnectionManager->addIceCandidate(peerId, in, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            return VmsErrorCode::MethodNotAllowedError;
        }
    };
    m_func["/api/v1/live/iceServers"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
    m_func["/api/v1/live/stream/swap"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        VmsErrorCode ret;
        const string protocol = in.get("protocol", EMPTY_STRING).asString();
        const string startTime = in.get("startTime", EMPTY_STRING).asString();
        if (startTime.empty() == false)
        {
            LOG(error) << "Use replay stream API to switch to recorded stream" << endl;
            ret = VmsErrorCode::VMSNotSupportedError;
            return ret;
        }
        if(protocol == "hls")
        {
            LOG(error) << "Switch Stream not supported for HLS Protocol" << endl;
            ret = VmsErrorCode::VMSNotSupportedError;
            return ret;
        }
        ret = m_peerConnectionManager->switchStream(req_info, in, response);
        return ret;
    };
    m_func["/api/v1/live/stream/stats"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
    m_func["/api/v1/live/stream/settings"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "post"))
        {
            std::string peerId = "";
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
            peerId = in.get("peerId", EMPTY_STRING).asString();
            if (peerId.empty() == true)
            {
                LOG(warning) << "peerId is empty" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerId is empty")
                return VmsErrorCode::InvalidParameterError;
            }
            return m_peerConnectionManager->streamSettings(req_info, in, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };
    m_func["/api/v1/live/configuration"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleLiveConfiguration(req_info, in, response);
    };
    m_func["/api/v1/live/version"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getVersion(req_info, in, out);
    };
    m_func["/api/v1/live/help"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getLiveHelp(req_info, in, out);
    };
    m_func["/v1/live"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return VmsErrorCode::NoError;
    };
    m_func["/v1/ready"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkReadinessProbe(conn, out);
    };
    m_func["/v1/startup"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkCivetWebServerRunning(conn, out);
    };
    m_func[LIVE_API] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleLiveAPIrequest(req_info, in, out, conn);
    };

    m_imageCleanupScheduler = std::make_unique<TempFileScheduler>(
        nv_vms::TempFilesDBColumns::FILE_TYPE_IMAGE,
        [](const std::string& /*taskId*/, const std::string& filePath) {
            if (deleteFile(filePath))
            {
                LOG(info) << "Deleted expired image file: " << filePath << endl;
            }
            else
            {
                LOG(warning) << "Failed to delete expired image file: " << filePath << endl;
            }
            auto dbHelper = GET_DB_INSTANCE();
            if (dbHelper)
            {
                dbHelper->deleteTempFileRecord(filePath);
            }
        });
    m_imageCleanupScheduler->initializeFromDatabase();
}

LivePeerConnection::~LivePeerConnection()
{
    m_imageCleanupScheduler.reset();

    if (HaloSafetyCommandListener::getInstance()->isRunning())
    {
        HaloSafetyCommandListener::getInstance()->stop();
    }
    if (m_peerConnectionManager)
    {
        m_peerConnectionManager->DestroyPeerConnections();
    }
}

VmsErrorCode LivePeerConnection::handleLiveAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                                struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();

    string liveAPI(LIVE_API);
    string path = requestAPI.substr(liveAPI.size() - 1);
    LOG(info) << "LIVE API path: " << path << std::endl;
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
        if (in.isMember("event") && !in["event"].isNull())
        {
            Json::Value event = in["event"];
            streamId = event.get("camera_id", EMPTY_STRING).asString();
            std::string change = event.get("change", "").asString();

            string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusOffline);
            if (change != changeLocal || streamId.empty())
            {
                LOG(error) << "Requested API is not allowed" << std::endl;
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
                return VmsErrorCode::MethodNotAllowedError;
            }

            requestMethod = "delete"; // SDR sends only POST requests. So as of now we changed it to delete request. Once it is handled from the SDR we can remove this.
        }
        else
        {
            LOG(error) << "Requested API is not allowed" << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
            return VmsErrorCode::MethodNotAllowedError;
        }
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
            shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
            LOG(info) << "streamId: " << streamId << endl;
            if(!sensor)
            {
                LOG(error) << "Sensor Not Found for streamId: " << streamId << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Sensor Not Found")
                return VmsErrorCode::InvalidParameterError;
            }
            if(sensor->type == SENSOR_TYPE_FILE)
            {
                LOG(error) << "File sensor not supported for picture API" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File sensor not supported for picture API")
                return VmsErrorCode::InvalidParameterError;
            }

            bool isURLRequested = iequals(subAction, "url");

            shared_ptr<StreamInfo> stream_info = sensor->getStream (streamId);
            if (stream_info && m_deviceManager->needStreamMonitoring && m_deviceManager->needRtspServer == false)
            {
                StreamMonitor::getInstance()->addStream(stream_info);
            }
            ret = vst_common::getCameraPicture(m_deviceManager, sensorId, queryString, response, isURLRequested);

            if (isURLRequested && ret == VmsErrorCode::NoError)
            {
                string filePath = response.get("absolutePath", "").asString();
                if (!filePath.empty())
                {
                    int expiryMinutesInt = response.get("expiryMinutes", GET_CONFIG().default_file_expiry_minutes).asInt();
                    std::string filename = std::filesystem::path(filePath).filename().string();
                    size_t dotPos = filename.find_last_of('.');
                    std::string taskId = (dotPos != std::string::npos) ? filename.substr(0, dotPos) : filename;

                    string webRoot = VmsConfigManager::getInstance()->getWebRootPath();
                    string actualFilePath = webRoot + TEMP_STORAGE_DIR + "/" + filename;
                    int64_t durationMs = static_cast<int64_t>(expiryMinutesInt) * 60 * 1000;
                    m_imageCleanupScheduler->schedule(taskId, durationMs, actualFilePath);
                }
            }
        }
    }
    else if (iequals(requestMethod, "delete"))
    {
        if (m_deviceManager == nullptr)
        {
            LOG(error) << "Invalid deviceMngr object" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        if(streamId.empty())
        {
            LOG(error) << "Stream ID is empty" << endl;
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            return VmsErrorCode::MethodNotAllowedError;
        }

        if (m_deviceManager->needStreamMonitoring && m_deviceManager->needRtspServer == false)
        {
            StreamMonitor::getInstance()->removeStream(streamId);
        }

        // Remove stream or sensor
        m_deviceManager->removeStreamOrSensor(streamId);
        LOG(info) << "Processed stream/sensor removal for streamId: " << streamId << endl;
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode LivePeerConnection::handleLiveConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response)
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
        response["gpuIndices"] = vectorToString(config.gpu_indices);
        response["httpPort"] = config.http_port;
        response["maxStreamsSupported"] = config.max_sensors_supported;
        response["maxWebrtcOutConnections"] = config.max_webrtc_out_connections;
        response["maxWebrtcInConnections"] = config.max_webrtc_in_connections;
        response["enableFrameDrop"] = config.enable_frame_drop;
        response["prometheusPort"] = config.prometheus_port;
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
        response["useVideoMetadataProtobuf"] = config.use_video_metadata_protobuf;
        response["videoMetadataQueryBatchSizeNumFrames"] = config.video_metadata_query_batch_size_num_frames;
        response["videoMetadataServer"] = config.video_metadata_server;
        response["calibrationFilePath"] = config.calibration_file_path;
        response["calibrationMode"] = config.calibration_mode;
        response["useCameraGroups"] = config.use_camera_groups;
        response["enableRecentering"] = config.enable_recentering;
        response["floorMapFilePath"] = config.floor_map_file_path;
        response["3dOverlaySensorName"] = config.overlay_3d_sensor_name;
        response["overlayClassLabels"] = config.overlay_class_labels;
        response["overlayProximityLabels"] = config.overlay_proximity_labels;
        response["overlayColorCode"] = config.overlay_color_code;
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
        response["enableDecLowLatencyMode"] = config.enable_dec_low_latency_mode;
        response["analyticServerAddress"] = config.analytic_server_address;
        response["overlayTextFontType"] = config.overlay_text_font_type;
        response["WebrtcOutDefaultResolution"] = config.webrtc_out_default_resolution;
        response["webrtc_video_quality_tunning"] = config.webrtc_video_quality_tunning;
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

VmsErrorCode LivePeerConnection::getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
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

VmsErrorCode LivePeerConnection::getLiveHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    for (auto it : m_func)
    {
        response.append(it.first);
    }
    return VmsErrorCode::NoError;
}

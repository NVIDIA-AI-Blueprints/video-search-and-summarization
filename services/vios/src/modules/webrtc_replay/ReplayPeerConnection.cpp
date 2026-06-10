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

#include "ReplayPeerConnection.h"
#include <jsoncpp/json/json.h>
#include "error_code.h"
#include "config.h"
#include "vst_common.h"
#include "health_probes.h"
#include <filesystem>

#define REPLAY_API "/api/v1/replay/stream/*"

extern "C" void* createPeerConnectionReplayManagerObject()
{
    std::string publishFilter(".*");
    webrtc::AudioDeviceModule::AudioLayer audioLayer = webrtc::AudioDeviceModule::kPlatformDefaultAudio;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    std::shared_ptr<PeerConnectionManager> pcm = std::make_shared<PeerConnectionManager>("replay", audioLayer, publishFilter, deviceManager);

    return static_cast<void*>(static_cast<IVstModule*>(new ReplayPeerConnection(pcm, deviceManager)));
}

extern "C" void deletePeerConnectionReplayManagerObject(IVstModule* object)
{
    ReplayPeerConnection* pcm_live = static_cast<ReplayPeerConnection*>(object);
    delete pcm_live;
}

ReplayPeerConnection::ReplayPeerConnection(std::shared_ptr<PeerConnectionManager> peerConnectionManager,
                                                        std::shared_ptr<DeviceManager> deviceManager)
                                                        : m_peerConnectionManager(peerConnectionManager), 
                                                          m_deviceManager(deviceManager),
                                                          m_unifiedStorageReader(nullptr)
{
    if (m_peerConnectionManager.get() == nullptr)
    {
        LOG(error) << "Cannot correctly initialize PeerConnection replay apis without PeerConnectionManager" << endl;
        return;
    }

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

    if(GET_CONFIG().enable_cloud_storage)
    {
        initUnifiedStorageReader();
    }

    m_func["/api/v1/replay/stream/start"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/stream/stop"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/stream/query"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

	m_func["/api/v1/replay/stream/add"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        string url = in.get("url", EMPTY_STRING).asString();
        string id = in.get("id", EMPTY_STRING).asString();

        if (in.isMember("event") && !in["event"].isNull())
        {
            LOG(warning) << "Event is not null" << endl;
            Json::Value event = in["event"];
            id = event.get("camera_id", EMPTY_STRING).asString();
            url = event.get("camera_url", "").asString();
            std::string change = event.get("change", "").asString();
            LOG(warning) << "Event is " << event.toStyledString() << endl;
            string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusStreaming);
            LOG(warning) << "Change is " << change << endl;
            LOG(warning) << "Change local is " << changeLocal << endl;

            if (change != changeLocal || id.empty() || url.empty())
            {
                LOG(warning) << "Invalid parameter" << endl;
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

        // Check and add sensor to cache
        LOG(warning) << "Checking if sensor exists in cache" << endl;
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

    m_func["/api/v1/replay/streams"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return vst_common::getSensorStreamListFromDB(m_deviceManager, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/replay/stream/pause"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/stream/resume"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/stream/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/setAnswer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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

    m_func["/api/v1/replay/iceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
    m_func["/api/v1/replay/iceServers"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
    m_func["/api/v1/replay/stream/seek"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        std::string peerId = "";
        std::string action = "";
        std::string mediaSessionId = "";
        peerId = in.get("peerId", EMPTY_STRING).asString();
        action = in.get("action", EMPTY_STRING).asString();
        mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
        LOG(info) << "/api/v1/replay/stream/seek: " << peerId << " " << mediaSessionId << endl;
        const string requestMethod = req_info.get("method", EMPTY_STRING).asString();
        if(iequals(requestMethod, "get"))
        {
            const string queryString = req_info.get("query", EMPTY_STRING).asString();
            CivetServer::getParam(queryString, "peerId", peerId);
            CivetServer::getParam(queryString, "mediaSessionId", mediaSessionId);
            return m_peerConnectionManager->getCurrentPosition(peerId, mediaSessionId, response);
        }
        else if(iequals(requestMethod, "post"))
        {
            Json::Value requestBody = Json::nullValue;
            std::string seekValue = in.get("value", EMPTY_STRING).asString();
            std::string mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
            /**
             * backward compatibility, avoiding change of keys further in other classes.
             * When removed, the keys need to be updated in other classes.
             */
            if (action == "fastForward")
            {
                action = "fast_forward";
            }
            else if (action == "seekBackward")
            {
                action = "seek_backward";
            }
            else if (action == "seekForward")
            {
                action = "seek_forward";
            }
            else if (action != "rewind")
            {
                LOG(error) << "Seek action not supported" << endl;
                SET_VMS_ERROR(VmsErrorCode::VMSNotSupportedError, response)
                return VmsErrorCode::VMSNotSupportedError;
            }
            requestBody["seek_value"] = seekValue;
            requestBody["action"] = action;
            requestBody["peerid"] = peerId;
            requestBody["mediaSessionId"] = mediaSessionId;
            return m_peerConnectionManager->controlStream(action, peerId, requestBody, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            return VmsErrorCode::MethodNotAllowedError;
        }
    };
    m_func["/api/v1/replay/stream/swap"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        VmsErrorCode ret;
        const string protocol = in.get("protocol", EMPTY_STRING).asString();
        const string startTime = in.get("startTime", EMPTY_STRING).asString();
        if (startTime.empty())
        {
            LOG(error) << "Use live stream API to switch to live stream" << endl;
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
    m_func["/api/v1/replay/stream/stats"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
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
    m_func["/api/v1/replay/configuration"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleReplayConfiguration(req_info, in, response);
    };
    m_func["/api/v1/replay/version"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getVersion(req_info, in, out);
    };
    m_func["/api/v1/replay/help"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getReplayHelp(req_info, in, out);
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
    m_func[REPLAY_API] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleReplayAPIrequest(req_info, in, out, conn);
    };
}

ReplayPeerConnection::~ReplayPeerConnection()
{
    m_imageCleanupScheduler.reset();
    if (m_peerConnectionManager)
    {
        m_peerConnectionManager->DestroyPeerConnections();
    }
}

VmsErrorCode ReplayPeerConnection::handleReplayAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                                struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();

    string replayAPI(REPLAY_API);
    string path = requestAPI.substr(replayAPI.size() - 1);
    LOG(info) << "REPLAY API path: " << path << std::endl;
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
            if (!queryString.empty())
            {
                CivetServer::getParam(queryString, "streamId", streamId);
            }

            if (streamId.empty())
            {
                LOG(error) << "Requested API is not allowed" << std::endl;
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
                return VmsErrorCode::MethodNotAllowedError;
            }
        }
    }

    if (iequals(requestMethod, "get"))
    {

        if(!m_deviceManager->getSensorIdFromStreamId(streamId, sensorId))
        {
            if (!streamId.empty())
            {
                LOG(warning) << "Sensor not found, continuing with discontinued sensor flow for streamId: " << streamId << endl;
                sensorId = streamId;
            }
            else
            {
                LOG(warning) << "Stream Not Found" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Stream Not Found")
                return VmsErrorCode::InvalidParameterError;
            }
        }

        if (iequals(action, "picture"))
        {
            bool isURLRequested = iequals(subAction, "url");
            ret = vst_common::handlePictureAction(m_deviceManager, m_deviceManager->getDeviceId(),
                                                   sensorId, queryString, isURLRequested,
                                                   *m_imageCleanupScheduler, response);
        }
    }
    else if (iequals(requestMethod, "delete"))
    {
        //check device manager is loaded
        if (m_deviceManager == nullptr)
        {
            LOG(error) << "Device manager is not loaded" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Device manager is not loaded");
            return VmsErrorCode::VMSInternalError;
        }

        if(streamId.empty())
        {
            LOG(error) << "Stream ID is empty" << endl;
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            return VmsErrorCode::MethodNotAllowedError;
        }

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

VmsErrorCode ReplayPeerConnection::handleReplayConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response)
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

// Unified storage configuration and management methods
bool ReplayPeerConnection::initUnifiedStorageReader()
{
    try
    {
        // Get configuration from DeviceConfig
        const nv_vms::DeviceConfig& config = GET_CONFIG();

        // Use UnifiedStorageReaderUtils to create storage reader
        m_unifiedStorageReader = nv_vms::UnifiedStorageReaderUtils::createStorageReader(config);

        if (!m_unifiedStorageReader)
        {
            LOG(error) << "Failed to create unified storage reader: " << nv_vms::UnifiedStorageReaderUtils::getLastError() << endl;
            return false;
        }
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage initialization: " << e.what() << endl;
        return false;
    }
}

VmsErrorCode ReplayPeerConnection::getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
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

VmsErrorCode ReplayPeerConnection::getReplayHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    for (auto it : m_func)
    {
        response.append(it.first);
    }
    return VmsErrorCode::NoError;
}

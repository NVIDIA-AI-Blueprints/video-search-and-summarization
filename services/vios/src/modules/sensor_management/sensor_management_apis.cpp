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

#include "sensor_management_apis.h"
#include "sensor_management_utils.h"
#include "vst_common.h"
#include "gstnvvideodecoder.h"
#include "health_probes.h"

constexpr const char* SENSOR_API = "/api/v1/sensor/*";
constexpr const char* DEBUG_API = "/api/v1/sensor/debug/*";
constexpr const char* DEBUG_API_SUBSTR = "/api/v1/sensor/debug/";

using namespace std;

SensorManagementApis::SensorManagementApis(std::shared_ptr<SensorManagement> sensorMgmt, std::shared_ptr<DeviceManager> deviceMngr): m_sensorManagement(sensorMgmt), m_deviceManager(deviceMngr)
{
    m_func["/api/v1/sensor/list"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return getSensorInfoList(req_info, out);
        }
        return VmsErrorCode::NoError;
    };
    m_func["/api/v1/sensor/streams"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_common::getSensorStreamListFromDB(m_deviceManager, out, true);
    };
    m_func["/api/v1/sensor/scan"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        m_sensorManagement->scanCameras(true);
        return VmsErrorCode::NoError;
    };
    m_func["/api/v1/sensor/add"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return addSensor(m_sensorManagement, req_info, in, out);
    };
    m_func["/api/v1/sensor/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return getAllSensorStatus(m_deviceManager, out);
        }
        return VmsErrorCode::NoError;
    };
    m_func["/api/v1/sensor/configuration"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleSensorConfiguration(req_info, in, out);
    };
    m_func["/api/v1/sensor/version"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getVersion(req_info, in, out);
    };
    m_func["/api/v1/sensor/help"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getSensorHelp(req_info, in, out);
    };
    m_func["/api/v1/sensor/qos"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return getSensorQosInfo(req_info, out);
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
    m_func["/api/v1/sensor/timelines"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return getAllSensorTimelines(req_info, out);
        }
        return VmsErrorCode::NoError;
    };
    m_func[SENSOR_API] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleSensorAPIrequest(req_info, in, out, conn);
    };
}

VmsErrorCode SensorManagementApis::handleSensorConfiguration(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    DeviceConfig config =  GET_CONFIG();
    if (iequals(requestMethod, "get"))
    {
        response["deviceDiscoveryFrequencySeconds"] = config.sensor_discovery_freq_secs;
        response["deviceDiscoveryInterfaces"] = vectorToJson(config.sensor_discovery_interfaces);
        response["deviceDiscoveryTimeoutSeconds"] = config.sensor_discovery_timeout;
        response["httpPort"] = config.http_port;
        response["useMessageBroker"] = config.use_message_broker;
        response["kafkaServerAddress"] = config.kafka_server_address;
        response["mqttBrokerAddress"] = config.mqtt_broker_address;
        response["redisServerEnvironmentVariable"] = config.redis_server_env_var;
        response["messageBrokerTopic"] = config.message_broker_topic;
        response["message_broker_payload_key"] = config.message_broker_payload_key;
        response["messageBrokerMetadataTopic"] = config.message_broker_metadata_topic;
        response["maxSensorsSupported"] = config.max_sensors_supported;
        response["ntpServers"] = vectorToJson(config.ntpServers);
        response["use_sensor_ntp_time"] = config.use_sensor_ntp_time;
        response["onvifRequestTimeoutSeconds"] = config.onvif_request_timeout_secs;
        response["enablePrometheus"] = config.enable_prometheus;
        response["enableNotification"] = config.enable_notification;
        response["prometheusPort"] = config.prometheus_port;
        response["enableDebugApis"] = config.enable_debug_apis;
        response["supportedVideoCodecs"] = vectorToString(config.video_codecs);
        response["supportedAudioCodecs"] = vectorToString(config.audio_codecs);
        response["useHttpDigestAuthentication"] = config.use_http_digest_authentication;
        response["useHttps"] = config.use_https;
        response["vstDataPath"] = config.vst_data_path;
        response["webserviceAccessControlList"] = config.webservice_access_control_list;
        response["enableUserCleanup"] = config.enable_user_cleanup;
        response["multiUserExtraOptions"] = vectorToString(config.multi_user_extra_options);
        response["nvOrgId"] = config.nv_org_id;
        response["nvNgcKey"] = config.nv_ngc_key;
        response["useMultiUser"] = config.use_multi_user;
        response["vstIp"] = g_hostIp;
        response["remoteVstAddress"] = config.remote_vst_address;
        response["deviceName"] = config.device_name;
        response["deviceLocation"] = config.device_location;
        response["defaultProfile"] = config.default_profile;
        response["defaultQuality"] = config.default_quality;
        response["defaultEncodingInterval"] = config.default_encoding_interval;
        response["defaultGovLength"] = config.default_gov_length;
        response["defaultResolution"] = config.default_resolution;
        response["defaultFramerate"] = config.default_framerate;
        response["defaultBitrateKbps"] = config.default_bitrate;
    }
    else if(iequals(requestMethod, "post"))
    {
        if (in.isNull() || !in.isObject())
        {
            LOG(error) << "Requested API is not allowed" << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
            return VmsErrorCode::MethodNotAllowedError;
        }

        Json::Value interfaces = in.get("deviceDiscoveryInterfaces", Json::Value::null);
        if (interfaces.isArray())
        {
            LOG(info) << "User provided network interfaces:" << interfaces << endl;
            bool restart_discovery = false;
            vector<string> vec_interfaces = jsonToVector(interfaces);
            if (vec_interfaces != GET_CONFIG().sensor_discovery_interfaces)
            {
                GET_CONFIG().sensor_discovery_interfaces.clear();
                for (auto iface : vec_interfaces)
                {
                    if (iface.empty() == false)
                    {
                        restart_discovery = true;
                        GET_CONFIG().sensor_discovery_interfaces.push_back(iface);
                    }
                }

                if (restart_discovery && m_sensorManagement->rebootSensorDiscovery() != 0)
                {
                    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
                    return VmsErrorCode::VMSInternalError;
                }
            }
        }
        Json::Value ntp_servers = in.get("ntpServers", Json::Value::null);
        if (ntp_servers.isArray())
        {
            LOG(info) << "User provided ntp servers:" << ntp_servers << endl;
            vector<string> vec_ntp = jsonToVector(ntp_servers);
            if (vec_ntp != GET_CONFIG().ntpServers)
            {
                GET_CONFIG().ntpServers.clear();
                for (auto ntp_server : vec_ntp)
                {
                    if (ntp_server.empty() == false)
                    {
                        GET_CONFIG().ntpServers.push_back(ntp_server);
                    }
                }
            }
        }
    }
    return ret;
}

VmsErrorCode SensorManagementApis::getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    // TODO: Get version from makefile when sensor managment is implemented as lib
    const string vstType = m_deviceManager->getDeviceType();
    response["type"] = vstType;
    if(vstType == TYPE_VST)
    {
        response["version"] = VST_VERSION;
    }
    else if(vstType == TYPE_MMS)
    {
        response["version"] = MMS_VERSION;
    }
    else if(vstType == TYPE_STREAMER)
    {
        response["version"] = STREAMER_VERSION;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode SensorManagementApis::getSensorHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    for (auto it : m_func)
    {
        response.append(it.first);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode SensorManagementApis::handleSensorAPIrequest(const Json::Value& req_info, const Json::Value &in,
        Json::Value &response, struct mg_connection *conn)
{
#ifndef RELEASE
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    if (isSubstring(requestAPI, DEBUG_API_SUBSTR))
    {
        return handleSensorDebugAPI(req_info, in, response, conn);
    }
#endif
    return handleSensorAPI(req_info, in, response, conn);
}

bool isReqFromEdgeDevice(const struct mg_connection *conn)
{
    const char *isEdgeDevice = mg_get_header(conn, "isEdgeDevice");
    if (isEdgeDevice == nullptr)
    {
        LOG(info) << "Request is not getting from Edge device" << endl;
        return false;
    }

    if (strcmp(isEdgeDevice, "true") == 0)
    {
        LOG(info) << "Request is getting from Edge device" << endl;
        return true;
    }
    else
    {
        LOG(info) << "Request is not getting from Edge device" << endl;
        return false;
    }
}

VmsErrorCode SensorManagementApis::handleSensorAPI(const Json::Value& req_info, const Json::Value &in,
        Json::Value &response, struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();
    if (requestAPI.empty() || requestMethod == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }
    string sensorAPI(SENSOR_API);
    string path = requestAPI.substr(sensorAPI.size() - 1);
    string streamId;
    string action;
    string subAction;

    LOG(info) <<"Camera API path: " << path << std::endl;
    vector<string> pathArray = splitString(path, "/");
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

    if(GET_CONFIG().use_multi_user)
    {
        string user = req_info.get("username", EMPTY_STRING).asString();
        shared_ptr<SensorInfo> sensor = m_sensorManagement->getSensorInfo(streamId);
        if(sensor != nullptr && !(sensor->checkUser(user)))
        {
            string error_message = string("Unauthorized access to sensor");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::CameraUnauthorizedError, response, error_message.c_str())
            return VmsErrorCode::CameraUnauthorizedError;
        }
    }

    Json::Value value = Json::nullValue;
    getSensorStatus(m_sensorManagement, streamId, value);
    const string camera_error_code = value.get("errorCode", "").asString();
    if(camera_error_code == "CameraNotFoundError")
    {
        /* If error code is CameraNotFoundError, don't return error for deleting camera, camera video data
         * getting and setting recording status, schedule and getting camera info
         */
        ret = skipStatusError(camera_error_code, requestMethod, action, response);
        if(ret != NoError)
        {
            return ret;
        }
    }
    else if(camera_error_code != "NoError")
    {
        if((!iequals(action, "credentials")) && (!iequals(action, "info")) && (!iequals(requestMethod, "delete") && !iequals(action, "")))
        {
            /* If other than CameraNotFoundError error is present, return error EXCEPT when posting credentials of camera
             * and when deleting a unauthorized camera
             */
            VmsErrorCode code = getCameraErrorCode(camera_error_code);
            SET_VMS_ERROR(code, response);
            LOG(error) << "Error code: " << camera_error_code << ":" << response.get("errorMessage", "unknown error vms error").asString() << endl;
            return code;
        }
    }

    if(iequals(requestMethod, "delete"))
    {
        if(action.empty())
        {
            bool reqFromEdgeDevice = isReqFromEdgeDevice(conn);
            ret = deleteSensor(m_sensorManagement, streamId, response, false, reqFromEdgeDevice);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else if(iequals(requestMethod, "get"))
    {
        if(iequals(action, "streams"))
        {
            ret = vst_common::getSensorStreamList(m_deviceManager, streamId, "", response);
        }
        else if(iequals(action, "status"))
        {
            ret = getSensorStatus(m_sensorManagement, streamId, response);
        }
        else if(iequals(action, "info"))
        {
            ret = getSensorInfo(m_sensorManagement, streamId, response);
        }
        else if(iequals(action, "settings"))
        {
            ret = getSensorSettings(m_sensorManagement, streamId, "", response);
        }
        else if(iequals(action, "network"))
        {
            ret = getSensorNetworkInfo(m_sensorManagement, streamId, response);
        }
        else if(iequals(action, "timelines"))
        {
            ret = getRecordingTimelines(m_sensorManagement, m_deviceManager, streamId, req_info, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else if(iequals(requestMethod, "post"))
    {
        if(iequals(action, "replace"))
        {
            ret = replaceSensorId(m_sensorManagement, streamId, in, response);
        }
        else if(iequals(action, "info"))
        {
            bool reqFromEdgeDevice = isReqFromEdgeDevice(conn);
            ret = setSensorInfo(m_sensorManagement, streamId, in, response, false, reqFromEdgeDevice);
        }
        else if(iequals(action, "settings"))
        {
            ret = setSensorSettings(m_sensorManagement, streamId, in, response);
        }
        else if(iequals(action, "network"))
        {
            bool reqFromEdgeDevice = isReqFromEdgeDevice(conn);
            ret = setSensorNetworkInfo(m_sensorManagement, streamId, in, response, false, reqFromEdgeDevice);
        }
        else if(iequals(action, "credentials"))
        {
            shared_ptr<SensorInfo> sensor = m_sensorManagement->getSensorInfo(streamId);
            if(sensor.get() == nullptr)
            {
                string error_message = string("Invalid sensor ID " + streamId);
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, response, error_message.c_str());
                return VmsErrorCode::CameraNotFoundError;
            }
            ret = setSensorCredentials(m_sensorManagement, streamId, in, response);
        }
        else if(iequals(action, "reboot"))
        {
            ret = rebootSensor(m_sensorManagement, streamId, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode SensorManagementApis::handleSensorDebugAPI(const Json::Value& req_info, const Json::Value &in,
        Json::Value &response, struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();
    if (requestAPI.empty() || requestMethod == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }
    string sensorAPI(DEBUG_API);
    string path = requestAPI.substr(sensorAPI.size() - 1);

    LOG(verbose) <<"Sensor API path: " << path << std::endl;
    vector<string> pathArray = splitString(path, "/");
    string action;
    string subAction;
    if (pathArray.size() > 0)
    {
        action = pathArray[0];
        subAction = pathArray.size() >= 2 ? pathArray[1] : "";
    }
    else
    {
        LOG(error) << "Requested API is not allowed" << std::endl;
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
        return VmsErrorCode::MethodNotAllowedError;
    }

    string ipAddress;
    if(iequals(requestMethod, "get"))
    {
        if(action == "system" && subAction == "stats")
        {
            response = getSystemStats();
        }
        else if(action == "status")
        {
            CivetServer::getParam(queryString, "ip", ipAddress);
            bool status = blockSensor(ipAddress, "status");
            response["status"] = status ? "unplug" : "plug";
        }
        else if(action == "logging")
        {
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
            response["live_stream"] = GstNvVideoDecoder::m_debug_logging_live;
            response["vod_stream"] = GstNvVideoDecoder::m_debug_logging_vod;
#endif
        }
    }
    else if(iequals(requestMethod, "post"))
    {
        CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
        if(action == "plug" || action == "unplug")
        {
            string ipAddress = in.get("ip", "").asString();
            response = blockSensor(ipAddress, action);
        }
        else if(action == "logging")
        {
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
            if(action == "plug" || action == "unplug")
            {
                string ipAddress = in.get("ip", "").asString();
                response = blockSensor(ipAddress, action);
            }
            else if(action == "logging")
            {
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
                if ((in.isMember("live_stream") && in["live_stream"].isBool()) && (in.isMember("vod_stream") && in["vod_stream"].isBool()))
                {
                    bool enable_logging_live = in.get("live_stream", false).asBool();
                    bool enable_logging_vod = in.get("vod_stream", false).asBool();
                    GstNvVideoDecoder::m_debug_logging_live = enable_logging_live;
                    GstNvVideoDecoder::m_debug_logging_vod = enable_logging_vod;
                }
                else
                {
                    LOG(error) << "Invalid Parameter" << endl;
                    SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
                    return VmsErrorCode::InvalidParameterError;
                }
#endif
            }
        }
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode SensorManagementApis::getSensorQosInfo(const Json::Value& req_info, Json::Value &response)
{
    Json::Value rtsp_conn_info = Json::arrayValue;

    if (m_deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    if (m_deviceManager->getDeviceType() != TYPE_STREAMER)
    {
        StreamMonitor::getInstance()->getQosInfo(rtsp_conn_info);
    }
    response["stats"] = rtsp_conn_info;
    Json::Value jout = vst_rtsp::activeClientSessions();
    response["numActiveRtspConnections"] = jout.get("activeClientSessions", "0").asString();
    response["rtspServerTxBitrate"] = jout.get("rtspServerTxBitrate", "0").asString();
    return VmsErrorCode::NoError;
}

VmsErrorCode SensorManagementApis::getSensorInfoList(const Json::Value& req_info, Json::Value &response)
{

    if (m_deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    unordered_set<string> sensorIdsWithTimelines;
    VmsErrorCode errorCode = GET_DB_INSTANCE()->getSensorIdsWithRecordingTimelines(sensorIdsWithTimelines);
    if (errorCode != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to get sensor IDs that have timelines" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    // Check if sensor cache should be refreshed from DB
    vector<shared_ptr<SensorInfo>> currentSensors = m_deviceManager->getSensorList(false);
    bool shouldRefresh = vst_common::ShouldRefreshSensorCache(m_deviceManager->getDeviceId(), currentSensors.size());
    vector<shared_ptr<SensorInfo>> sensors = shouldRefresh ? m_deviceManager->getSensorList(true) : currentSensors;

    // Add connected sensors to list
    for (uint32_t i = 0; i < sensors.size(); i++ )
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        Json::Value info;
        Json::Value position;
        Json::Value origin;
        Json::Value geoLocation;
        Json::Value coordinates;
        info["sensorId"] = sensor->id;
        info["name"] = sensor->name;
        info["sensorIp"] = sensor->ip;
        info["hardware"] = sensor->hardware;
        info["manufacturer"] = sensor->manufacturer;
        info["firmwareVersion"] = sensor->firmware_version;
        info["serialNumber"] = sensor->serial_number;
        info["hardwareId"] = sensor->hardware_id;
        info["location"] = sensor->location;
        info["tags"] = sensor->tags;
        info["isRemoteSensor"] = sensor->isRemoteSensor;
        info["remoteDeviceId"] = sensor->remoteDeviceId;
        info["remoteDeviceName"] = sensor->remoteDeviceName;
        info["remoteDeviceLocation"] = sensor->remoteDeviceLocation;
        origin["latitude"] = sensor->position.origin.first;
        origin["longitude"] = sensor->position.origin.second;
        position["origin"] = origin;
        geoLocation["latitude"] = sensor->position.geoLocation.first;
        geoLocation["longitude"] = sensor->position.geoLocation.second;
        position["geoLocation"] = geoLocation;
        coordinates["x"] = sensor->position.coordinates.first;
        coordinates["y"] = sensor->position.coordinates.second;
        position["coordinates"] = coordinates;
        position["direction"] = sensor->position.direction;
        position["depth"] = sensor->position.depth;
        position["fieldOfView"] = sensor->position.fieldOfView;
        info["position"] = position;
        info["state"] = sensor->getHttpErrorStatus().first == CAMERA_NO_ERROR_CODE ? "online": "offline";
        info["isTimelinePresent"] = sensorIdsWithTimelines.count(sensor->id) > 0;
        info["type"] = sensor->type;
        response.append(info);
    }

    // Add disconnected sensors to list
    vector<VideoRecordDBColumns> removed_sensors = GET_DB_INSTANCE()->getAllDisconnectedSensorId();
    for (uint32_t i = 0; i < removed_sensors.size(); ++i )
    {
        Json::Value info;
        info["sensorId"] = removed_sensors[i].sensor_id_value;
        info["name"] = removed_sensors[i].sensor_name_value;
        info["state"] = "removed";
        response.append(info);
    }
    if (response == Json::nullValue)
    {
        response = Json::arrayValue;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode SensorManagementApis::getAllSensorTimelines(const Json::Value& req_info, Json::Value &response)
{
    if (m_sensorManagement == nullptr)
    {
        LOG(error) << "Invalid sensorManagement object" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }
    
    if (m_deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceManager object" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }
    
    // Check if adaptor type is MMS
    const string adaptorType = m_deviceManager->getDeviceType();
    
    if (adaptorType == TYPE_MMS)
    {
        // For MMS, use ONVIF APIs
        shared_ptr<SensorControl> sensorControl = m_sensorManagement->getSensorControl();
        if (!sensorControl)
        {
            LOG(error) << "SensorControl not found" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
        
        vector<shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
        
        if (sensors.empty())
        {
            LOG(info) << "No sensors found" << endl;
            response = Json::objectValue;  // Return empty object
            return VmsErrorCode::NoError;
        }
        
        response = Json::objectValue;
        
        // For each sensor, get its recording timelines from ONVIF
        int successCount = 0;
        int errorCount = 0;
        
        for (const auto& sensor : sensors)
        {
            if (!sensor)
            {
                continue;
            }
            
            const string& sensorId = sensor->id;
            
            // Call the recording timelines API for this sensor
            Json::Value sensorTimelinesJson;
            int ret = sensorControl->getRecordingTimelines(sensorId, sensorTimelinesJson);
            
            if (ret != 0)
            {
                LOG(error) << "Failed to get recording timelines for sensor " << sensorId << ", error: " << ret;
                errorCount++;
                // Continue to next sensor instead of failing entirely
                continue;
            }
            
            // Flatten all recording tokens into single timeline array per sensor
            Json::Value allTimelinesForSensor = Json::arrayValue;
            
            if (sensorTimelinesJson.isObject())
            {
                // Iterate through all recording tokens and collect their timelines
                for (const auto& recordingToken : sensorTimelinesJson.getMemberNames())
                {
                    const Json::Value& timelineArray = sensorTimelinesJson[recordingToken];
                    if (timelineArray.isArray())
                    {
                        // Append all timeline entries from this recording token
                        for (const auto& timeline : timelineArray)
                        {
                            allTimelinesForSensor.append(timeline);
                        }
                    }
                }
            }
            
            // Only add sensor to response if it has timelines
            if (!allTimelinesForSensor.empty())
            {
                response[sensorId] = allTimelinesForSensor;
                successCount++;
            }
        }
        
        if (errorCount > 0)
        {
            LOG(warning) << "Failed to get timelines for " << errorCount << " sensor(s)" << endl;
        }
        
        LOG(info) << "Retrieved timelines for " << successCount << " sensor(s)" << endl;
    }
    else
    {
        // For non-MMS adaptors (VST, STREAMER), get timelines from the database
        LOG(info) << "Using database for timelines (adaptor type: " << adaptorType << ")" << endl;
        return vst_common::GetAllRecordTimelines(req_info, response);
    }
    
    return VmsErrorCode::NoError;
}

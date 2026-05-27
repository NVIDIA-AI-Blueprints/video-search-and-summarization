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

#include "logger.h"
#include "rtspservermanager.h"
#include "error_code.h"
#include "network_utils.h"
#include "modules_apis.h"
#include "vst_common.h"
#include "stream_event_manager.h"
#include "stream_monitor.h"
#include "database.h"
#include "health_probes.h"
#include <string_view>
#include <mutex>
#include <set>
#include <limits>

using namespace nv_vms;

constexpr int DEFAULT_RTSP_PORT_NUMBER = 8554;
constexpr int DEFAULT_RTSP_SERVER_INSTANCE_COUNT = 1;

#define RTSP_SERVER_CHECK_ERROR(serverCount, ret_value) \
    if( serverCount == 0 ) \
    { \
        LOG(error) << "[Error] Rtsp-server is not yet ready/created" << endl; \
        return ret_value; \
    }

namespace
{
    static bool isProxyableStreamUrl(const string& url)
    {
        return (url.compare(0, 7, "rtsp://") == 0 ||
                url.compare(0, 8, "rtsps://") == 0);
    }

    VmsErrorCode RtspStreamStatusCallback(const string &url,
                                          const StreamStatus newStatus,
                                          StreamEncParam& details)
    {
        if (newStatus != StreamStatus::STREAM_STATUS_STREAMING
            && newStatus != StreamStatus::STREAM_STATUS_END_OF_STREAM
            && newStatus != StreamStatus::STREAM_STATUS_REMOVED)
        {
            return VmsErrorCode::NoError;
        }

        LOG(info) << "#### RtspStreamStatusListener: Stream status: " << translateStreamStatusToString(newStatus) << ", url: "
                  << secureUrlForLogging(url) << ", Codec: " << details.codec << endl;

        std::shared_ptr<DeviceManager> deviceManager =
            ModuleLoader::getInstance()->getDeviceManagerObject();
        if (!deviceManager ||
            (deviceManager->type != TYPE_VST && deviceManager->type != TYPE_MMS))
        {
            LOG(warning) << "RtspStreamStatusListener: Invalid device manager or device type" << endl;
            return VmsErrorCode::NoError;
        }

        std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();

        // Handle END_OF_STREAM and REMOVED status
        if (newStatus == StreamStatus::STREAM_STATUS_END_OF_STREAM
            || newStatus == StreamStatus::STREAM_STATUS_REMOVED)
        {
            string streamId;
            for (auto const& stream : streamList)
            {
                if (stream->live_proxy_url == url)
                {
                    streamId = stream->id;
                    LOG(info) << "RtspStreamStatusListener: Setting stream status to " << newStatus << " for: "
                            << stream->name << ", id: " << stream->id << endl;
                    stream->updateErrorStatus(std::make_pair(
                        newStatus,
                        translateStreamStatusToString(newStatus)));

                    RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
                    if (rtsp_mgmt != nullptr)
                    {
                        Json::Value response;
                        VmsErrorCode ret = rtsp_mgmt->removeStream(streamId, response);
                        if (ret != VmsErrorCode::NoError)
                        {
                            LOG(error) << "Failed to remove stream: " << streamId << endl;
                            return ret;
                        }
                    }
                    return VmsErrorCode::NoError;
                }
            }
        }

        // Update stream to STREAMING status
        for (auto const& stream : streamList)
        {
            if (stream->live_proxy_url == url)
            {
                if (!details.codec.empty())
                {
                    SensorVideoEncoderSettingsValues& enc_values = stream->getvideoEncoderValues();
                    enc_values.encoding = details.codec;
                    stream->updateVideoEncoderValues(enc_values, /*updateDB=*/false);
                    LOG(info) << "RtspStreamStatusListener: Updated codec for stream: "
                              << stream->name << ", id: " << stream->id
                              << ", codec: " << details.codec << endl;
                }

                if (stream->getErrorStatus().first == StreamStatus::STREAM_STATUS_STREAMING)
                {
                    LOG(info) << "RtspStreamStatusListener: stream "
                              << stream->name << " (" << stream->id
                              << ") already STREAMING; skipping duplicate event" << endl;
                    break;
                }

                LOG(info) << "RtspStreamStatusListener: Setting stream status to STREAMING for: "
                          << stream->name << ", id: " << stream->id << endl;

                stream->updateErrorStatus(std::make_pair(
                    StreamStatus::STREAM_STATUS_STREAMING,
                    translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)),
                    /*updateDB=*/false);

                stream->live_proxy_url = url;
                shared_ptr<SensorInfo> sensor = deviceManager->getSensor(stream->sensorId);
                if (sensor)
                {
                    vst_common::updateSensorDetailsToDB(
                        deviceManager->getDeviceId(), sensor, /*force=*/false);
                    LOG(info) << "RtspStreamStatusListener: Persisted full sensor+stream "
                                << stream->sensorId << "/" << stream->id
                                << " to DB" << endl;
                }
                break;
            }
        }

        // Remove invalid sub-streams
        for (auto const& stream : streamList)
        {
            SensorVideoEncoderSettingsValues& enc_values = stream->getvideoEncoderValues();
            if (enc_values.encoding.empty() && !stream->isMainStream)
            {
                LOG(info) << "RtspStreamStatusListener: Removing invalid sub-stream: "
                          << secureUrlForLogging(stream->live_url) << endl;
                GET_DB_INSTANCE()->deleteRowStream(stream->id);
                deviceManager->removeStream(stream->live_url);
            }
        }
        return VmsErrorCode::NoError;
    }
}

string getTotalTxBytes(vector<string> active_streams)
{
    unsigned long total_tx_bytes = 0;
    std::shared_ptr<DeviceManager> m_deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (m_deviceManager)
    {
        std::vector<shared_ptr<StreamInfo>> streamList;
        streamList = m_deviceManager->getStreamList();
        for (auto const& active_stream : active_streams)
        {
            for (auto const& stream : streamList)
            {
                string path = getFilePathFromUrl(stream->live_url, NV_STREAMER);
                if (active_stream.find(path) != string::npos)
                {
                    total_tx_bytes += stringToInt(stream->settings.encoderValues.bitrate, 0);
                    break;
                }
            }
        }
    }
    return to_string(total_tx_bytes);
}

string getVodRtspUrl(const string& serverUrl, const string& id)
{
    string vod_rtsp;
    if (!serverUrl.empty())
    {
        vod_rtsp = serverUrl + string("vod/") + id;
    }
    return vod_rtsp;
}

void RtspServerManager::handleRESTAPIs()
{
	m_func["/api/v1/proxy/stream/add"] = [this](const Json::Value& req_info, const Json::Value &in,
                                          Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "Called /api/v1/proxy/stream/add" << " in:" << in.toStyledString() << endl;
        string url = in.get("url", EMPTY_STRING).asString();
        string id = in.get("id", EMPTY_STRING).asString();
        string name = in.get("name", EMPTY_STRING).asString();
        string codec      = in.get("codec", EMPTY_STRING).asString();
        string resolution = in.get("resolution", EMPTY_STRING).asString();
        string framerate  = in.get("framerate", EMPTY_STRING).asString();
        string tags       = in.get("tags", EMPTY_STRING).asString();
        string sensor_type;
        string live_url = url;
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        if (id.empty() || url.empty() || name.empty())
        {
            if (in.isMember("event") && !in["event"].isNull())
            {
                Json::Value event = in["event"];
                id   = event.get("camera_id", EMPTY_STRING).asString();
                url  = event.get("camera_url", "").asString();
                live_url = url;
                name = event.get("camera_name", EMPTY_STRING).asString();
                tags = event.get("tags", EMPTY_STRING).asString();
                std::string change = event.get("change", "").asString();
                if(event.isMember("metadata") && !event["metadata"].isNull())
                {
                    Json::Value metadata = event["metadata"];
                    codec                = metadata.get("codec", EMPTY_STRING).asString();
                    resolution           = metadata.get("resolution", EMPTY_STRING).asString();
                    framerate            = metadata.get("framerate", EMPTY_STRING).asString();
                    sensor_type          = metadata.get("sensor_type", EMPTY_STRING).asString();
                }

                string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusProxy);

                if (change != changeLocal || id.empty() || url.empty() || name.empty())
                {
                    string error_message = "Invalid parameter";
                    LOG(error) << error_message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str())
                    return VmsErrorCode::InvalidParameterError;
                }

                // If url is not RTSP/RTSPS/S3, return success because it is file-based stream (no proxy)
                if (!isProxyableStreamUrl(url))
                {
                    LOG(info) << "Accepting camera_proxy for file-based stream, no RTSP proxy created: id=" << id << endl;
                    return VmsErrorCode::NoError;
                }
            }
        }

        LOG(info) << "Creating proxy stream for url:" << secureUrlForLogging(url) << " id:" << id << " name:" << name << endl;

        int ret = m_lb.addProxyStream(id, name, url);
        if (ret != 0)
        {
            string error_message = "Failed to create proxy rtsp url";
            LOG(error) << error_message << " for url:" << secureUrlForLogging(url) << " id:" << id << " name:" << name << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str())
            return VmsErrorCode::VMSInternalError;
        }
        url = vst_common::toDomainName(url, id);

        string vodUrl;
        if (sensor_type == SENSOR_TYPE_MMS_ONVIF)
        {
            /* find "/live/" in the live_url and replace it with "/vod/" */
            constexpr std::string_view livePrefix = "/live/";
            size_t live_pos = live_url.find(livePrefix);
            if (live_pos != string::npos)
            {
                vodUrl = live_url.substr(0, live_pos) + string("/vod/") + live_url.substr(live_pos + livePrefix.length());
            }
        }
        else
        {
            vodUrl = vst_rtsp::vodServerDomainPrefix(id) + string("vod/") + id;
        }
        out["url"] = url;
        out["vodUrl"] = vodUrl;

        // Store metadata for deferred registration in sdpReady()
        RtspServer* server = m_lb.rtspServer(id);
        if (server)
        {
            server->updateStreamMetadata(id, vodUrl, codec, resolution, framerate, tags);
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/urlPrefix"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/urlPrefix" << endl;
        const string id = in.get("id", EMPTY_STRING).asString();
        string url;
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        int ret = m_lb.addStream(id, url);
        if (ret != 0)
        {
            string error_message = "Failed to add sensor rtsp url";
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str())
            return VmsErrorCode::VMSInternalError;
        }
        out["urlPrefix"] = url;
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/streams"] = [this](const Json::Value& req_info, const Json::Value &in,
                                       Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/streams" << endl;
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)
        string sensorName, vodServerUrl;
        if (m_lb.getVodServer() != nullptr)
        {
            vodServerUrl = m_lb.getVodServer()->urlPrefix();
        }

        std::set<string> seenStreamIds;

        // 1. Streams from local RTSP server cache
        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            vector<StreamDetails> list = m_lb.rtspServer(i)->streamList();
            for (const auto& l : list)
            {
                Json::Value item;
                item["sensorId"] = l.id;
                sensorName = l.name.find("live/") != string::npos ?
                        l.name.substr(l.name.find("live/") + 5) : l.name;
                item["name"] = sensorName;
                item["proxyUrl"] = l.proxyUrl;
                item["vodUrl"] = getVodRtspUrl(vodServerUrl, l.id);
                out.append(item);
                seenStreamIds.insert(l.id);
            }
        }

        // 2. Merge streams from DB that are not already in the cache
        auto dbHelper = GET_DB_INSTANCE();
        std::vector<shared_ptr<StreamInfo>> dbStreamList;
        if (dbHelper && 0 == dbHelper->getAllStreams(dbStreamList, ModuleLoader::getInstance()->getDeviceId()))
        {
            for (const auto& stream : dbStreamList)
            {
                LOG(info) << "Adding stream to the list: " << stream->id << " " << stream->name << " " << secureUrlForLogging(stream->live_proxy_url) << " "
                << secureUrlForLogging(getVodRtspUrl(vodServerUrl, stream->id)) << " " << seenStreamIds.count(stream->id) << endl;
                if (!stream || stream->id.empty() || seenStreamIds.count(stream->id) > 0)
                {
                    continue;
                }
                Json::Value item;
                item["sensorId"] = stream->sensorId;
                item["name"] = stream->name;
                item["proxyUrl"] = stream->live_proxy_url;
                item["vodUrl"] = getVodRtspUrl(vodServerUrl, stream->id);
                out.append(item);
                seenStreamIds.insert(stream->id);
            }
        }

        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/user/add"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/user/add" << endl;
        const string username =  in.get("username", EMPTY_STRING).asString();
        const string password =  in.get("password", EMPTY_STRING).asString();
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            m_lb.rtspServer(i)->addUser(username.c_str(), password.c_str());
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/user/remove"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/user/remove" << endl;
        const string username =  in.get("username", EMPTY_STRING).asString();
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            m_lb.rtspServer(i)->removeUser(username.c_str());
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/user/update"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/user/update" << endl;
        const string username =  in.get("username", EMPTY_STRING).asString();
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            m_lb.rtspServer(i)->updateUser(username.c_str());
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/info"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/info" << endl;
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        vector<string> total_active_streams;
        unsigned int total_active_client_sessions = 0;
        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            Json::Value item;
            item["urlPrefix"] = m_lb.rtspServer(i)->urlPrefix();

            total_active_client_sessions += m_lb.rtspServer(i)->activeClientSessions();
            vector<string> active_streams = m_lb.rtspServer(i)->getActiveStreams();
            total_active_streams.insert(total_active_streams.end(), active_streams.begin(), active_streams.end());

            item["rtspServerDomainPrefix"] = m_lb.rtspServer(i)->getRtspServerDomainPrefix();
            string server_name = "server" + to_string(i);
            out[server_name.c_str()] = item;
        }
        Json::Value total;
        total["activeClientSessions"] = total_active_client_sessions;
        total["rtspServerTxBitrate"] = getTotalTxBytes(total_active_streams);
        out["stats"] = total;
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/activeClientSessions"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/activeClientSessions" << endl;
        unsigned int active_client_sessions = 0;
        vector<string> total_active_streams;
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        for (int i = 0; i < m_lb.getServersCount(); i++)
        {
            active_client_sessions += m_lb.rtspServer(i)->activeClientSessions();
            vector<string> active_streams = m_lb.rtspServer(i)->getActiveStreams();
            total_active_streams.insert(total_active_streams.end(), active_streams.begin(), active_streams.end());
        }
        out["activeClientSessions"] = active_client_sessions;
        out["rtspServerTxBitrate"] = getTotalTxBytes(total_active_streams);
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/orignalUrlPrefix"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(verbose) << "/api/v1/proxy/orignalUrlPrefix" << endl;
        const string id = in.get("id", EMPTY_STRING).asString();
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)

        out["orignalUrlPrefix"] = m_lb.rtspServer(id)->originalPrefix();
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/rtspServerDomainPrefix"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        LOG(info) << "/api/v1/proxy/rtspServerDomainPrefix" << endl;
        const string id = in.get("id", EMPTY_STRING).asString();
        RTSP_SERVER_CHECK_ERROR(m_lb.getServersCount(), VmsErrorCode::VMSInternalError)
        string serverPrefix = m_lb.rtspServer(id)->getRtspServerDomainPrefix();
        if (serverPrefix.empty())
        {
            serverPrefix = m_lb.rtspServer(id)->urlPrefix();
        }
        out["rtspServerDomainPrefix"] = serverPrefix;
        if (m_lb.getVodServer() != nullptr)
        {
            string vodUrl = m_lb.getVodServerDomainPrefix(serverPrefix);
            out["vodServerDomainPrefix"] = vodUrl;
        }
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/proxy/configuration"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleProxyConfiguration(req_info, in, response);
    };

    m_func["/v1/live"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return VmsErrorCode::NoError;
    };

    m_func["/v1/ready"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkReadinessProbe(conn, response);
    };

    m_func["/v1/startup"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkCivetWebServerRunning(conn, response);
    };

    m_func[PROXY_SESSION_API] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleSessionAPIrequest(req_info, in, out, conn);
    };

    m_func[PROXY_STREAM_API] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleStreamAPIrequest(req_info, in, out, conn);
    };

    m_func["/api/v1/proxy/debug/qos"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleProxyQos(out);
    };
}

RtspServerManager::RtspServerManager()
{
    uint16_t startPort = DEFAULT_RTSP_PORT_NUMBER;
    uint16_t instanceCount = DEFAULT_RTSP_SERVER_INSTANCE_COUNT;

    LOG(info) << "Creating RtspServerManager instance" << endl;
    if (GET_CONFIG().rtsp_server_port != -1)
    {
        startPort = GET_CONFIG().rtsp_server_port;
    }
#ifndef UNIT_TEST
    if (GET_CONFIG().rtsp_server_instances_count > 0)
    {
        instanceCount = GET_CONFIG().rtsp_server_instances_count;
    }
#endif
    m_lb.start(instanceCount, startPort);

    /* Start Vod server for vst type adaptor */
    std::shared_ptr<DeviceManager> m_deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (m_deviceManager)
    {
        if (m_deviceManager->getDeviceType() != TYPE_STREAMER)
        {
            m_lb.startVodServer(startPort);
        }
    }
    if (m_deviceManager && m_deviceManager->needStreamMonitoring)
    {
        static std::once_flag registerFlag;
        std::call_once(registerFlag, []() {
            StreamEventManager& eventManager = StreamEventManager::getInstance();
            eventManager.registerCallback(RtspStreamStatusCallback);

            LOG(info) << "Registered StreamEventManager and RTSP stream status callback" << endl;
        });
    }
    handleRESTAPIs();
}

void RtspServerManager::postInit()
{
    if (GET_CONFIG().restore_rtsp_streams_on_startup)
    {
        restoreRtspStreamsFromDB();
    }
    else
    {
        LOG(info) << "Not restoring RTSP streams from DB on startup" << endl;
    }
}

void RtspServerManager::restoreRtspStreamsFromDB()
{
    if (m_lb.getServersCount() == 0)
    {
        LOG(warning) << "RTSP server not ready, skipping proxy restoration from DB" << endl;
        return;
    }

    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        LOG(warning) << "DB instance not available, skipping proxy restoration" << endl;
        return;
    }

    vector<SensorStreamsDBColumns> dbStreams = dbHelper->readAllStreams();
    if (dbStreams.empty())
    {
        LOG(info) << "No streams found in DB to restore" << endl;
        return;
    }

    LOG(info) << "Restoring stream proxies from DB, total entries: " << dbStreams.size() << endl;

    int restoredCount = 0;
    for (const auto& row : dbStreams)
    {
        if (row.stream_id_value.empty() || row.live_url_value.empty())
        {
            continue;
        }

        if (!isProxyableStreamUrl(row.live_url_value))
        {
            continue;
        }

        string url = row.live_url_value;
        string vodUrl;
        int ret = vst_rtsp::addStream(row.stream_id_value, row.streamName_value, url, vodUrl);
        if (ret != 0)
        {
            LOG(error) << "Failed to restore proxy for stream id: " << row.stream_id_value
                       << ", url: " << secureUrlForLogging(row.live_url_value) << endl;
            continue;
        }

        restoredCount++;
        LOG(info) << "Restored proxy for stream id: " << row.stream_id_value
                  << ", proxyUrl: " << secureUrlForLogging(url)
                  << ", vodUrl: " << vodUrl << endl;
    }

    LOG(info) << "Proxy restoration complete: " << restoredCount << "/" << dbStreams.size()
              << " stream(s) restored" << endl;
}

RtspServerManager::~RtspServerManager()
{
    LOG(info) << "Deleting RtspServerManager instance" << endl;
    m_lb.stop();
}

VmsErrorCode RtspServerManager::handleProxyConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    DeviceConfig config =  GET_CONFIG();
    if (iequals(request_method, "get"))
    {
        response["enableRtspServerFrameIdSupport"] = config.enable_rtsp_server_sei_metadata;
        response["enableProxyServerFrameIdSupport"] = config.enable_proxy_server_sei_metadata;
        response["httpPort"] = config.http_port;
        response["maxStreamsSupported"] = config.max_sensors_supported;
        response["enableQosMonitoring"] = config.enable_qos_monitoring;
        response["qosLogfilePath"] = config.qos_logfile_path;
        response["qosDataCaptureIntervalSec"] = config.qos_data_capture_interval_sec;
        response["qosDataPublishIntervalSec"] = config.qos_data_publish_interval_sec;
        response["enablePrometheus"] = config.enable_prometheus;
        std::shared_ptr<DeviceManager> m_deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
        response["enableStreamMonitoring"] = m_deviceManager && m_deviceManager->needStreamMonitoring;
        response["prometheusPort"] = config.prometheus_port;
        response["rtspInBaseUdpPortNum"] = config.rtsp_in_base_udp_port_num;
        response["rtspOutBaseUdpPortNum"] = config.rtsp_out_base_udp_port_num;
        response["rtspPreferredNetworkIface"] = config.rtsp_preferred_network_iface;
        response["rtspServerPort"] = config.rtsp_server_port;
        response["rtsp_server_instances_count"] = config.rtsp_server_instances_count;
        response["rtspServerReclamationClientTimeoutSec"] = config.rtsp_server_reclamation_client_timeout_sec;
        response["rtspStreamingOverTcp"] = config.rtsp_streaming_over_tcp;
        response["use_sensor_ntp_time"] = config.use_sensor_ntp_time;
        response["serverDomainName"] = config.server_domain_name;
        response["sessionMaxAgeSec"] = config.session_max_age_sec;
        response["streamMonitorIntervalSecs"] = config.stream_monitor_interval_secs;
        response["useHttpDigestAuthentication"] = config.use_http_digest_authentication;
        response["useHttps"] = config.use_https;
        response["useRtspAuthentication"] = config.use_rtsp_authentication;
        response["vstDataPath"] = config.vst_data_path;
        response["webserviceAccessControlList"] = config.webservice_access_control_list;
        response["enableUserCleanup"] = config.enable_user_cleanup;
        response["multiUserExtraOptions"] = vectorToString(config.multi_user_extra_options);
        response["vstIp"] = g_hostIp;
        response["useMultiUser"] = config.use_multi_user;
    }
    else
    {
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Method is not allowed");
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode RtspServerManager::handleProxyQos(Json::Value &response)
{
    Json::Value qosStats = Json::arrayValue;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager && deviceManager->needStreamMonitoring)
    {
        StreamMonitor* streamMonitor = StreamMonitor::getInstance();
        if (streamMonitor)
        {
            streamMonitor->getQosInfo(qosStats);
        }
    }
    response["stats"] = qosStats;
    Json::Value jout = vst_rtsp::activeClientSessions();
    response["numActiveRtspConnections"] = jout.get("activeClientSessions", "0").asString();
    response["rtspServerTxBitrate"] = jout.get("rtspServerTxBitrate", "0").asString();
    return VmsErrorCode::NoError;
}

VmsErrorCode RtspServerManager::handleStreamDelete(const std::string &streamId, Json::Value &response)
{
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (!deviceManager)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Invalid deviceMngr object")
        return VmsErrorCode::VMSInternalError;
    }

    string streamUrl;
    string parentSensorId;
    string parentSensorType;
    {
        auto streamList = deviceManager->getStreamList();
        for (const auto& stream : streamList)
        {
            if (stream->id == streamId)
            {
                streamUrl = stream->live_proxy_url;
                parentSensorId = stream->sensorId;
                break;
            }
        }
    }
    if (!parentSensorId.empty())
    {
        if (auto sensor = deviceManager->getSensorInfo(parentSensorId))
        {
            parentSensorType = sensor->type;
        }
    }

    VmsErrorCode eventResult = VmsErrorCode::NoError;
    if (!streamUrl.empty() && parentSensorType != std::string(SENSOR_TYPE_FILE))
    {
        StreamEncParam details;
        eventResult = StreamEventManager::getInstance().sendEventBlocking(
            streamUrl, STREAM_STATUS_REMOVED, details);
    }

    // For SENSOR_TYPE_FILE, remove the uploaded file from disk before the
    // sensor is evicted from cache. sensor-ms publishes camera_remove on
    // DELETE /api/v1/sensor/{id}; SDR routes that here. Reuse the storage
    // module's own DELETE /api/v1/storage/file/{streamId}?startTime=*&endTime=*
    // path via vst_storage::deleteFilesByStream so the file unlink, the
    // VIDEO_RECORD row prune, and the sensor cascade all happen through
    // a single existing helper. Scoped to SENSOR_TYPE_FILE so RTSP
    // recordings are untouched.
    if (parentSensorType == std::string(SENSOR_TYPE_FILE))
    {
        if (vst_storage::deleteFilesByStream(streamId) != 0)
        {
            LOG(warning) << "deleteFilesByStream failed for file-sensor stream "
                         << streamId << endl;
        }
    }

    deviceManager->removeStreamOrSensor(streamId);

    LOG(info) << "Processed stream/sensor removal for streamId: " << streamId << endl;
    return eventResult;
}

VmsErrorCode RtspServerManager::removeStream(const std::string &streamId, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (!deviceManager)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Invalid deviceMngr object")
        return VmsErrorCode::VMSInternalError;
    }

    if (deviceManager->needStreamMonitoring && deviceManager->needRtspServer == true)
    {
        string sensor_id;
        if (!deviceManager->getSensorIdFromStreamId(streamId, sensor_id))
        {
            LOG(warning) << "Failed to get sensor ID from stream ID: " << streamId << endl;
            sensor_id = streamId;
        }
        std::shared_ptr<StreamInfo> streamInfo = deviceManager->getStream(sensor_id, streamId);
        StreamMonitor* streamMonitor = StreamMonitor::getInstance();
        if (streamMonitor && streamInfo)
        {
            streamMonitor->removeStream(streamInfo);
        }
    }

    int lbResult = m_lb.removeStream(streamId);
    if (lbResult != 0)
    {
        LOG(error) << "Failed to remove rtsp url" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to remove sensor rtsp url")
        ret = VmsErrorCode::VMSInternalError;
    }

    LOG(info) << "Processed stream/sensor removal for streamId: " << streamId << endl;
    return ret;
}

VmsErrorCode RtspServerManager::handleSessionAPIrequest(const Json::Value &req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    string deviceApi(PROXY_SESSION_API);
    string path = requestAPI.substr(deviceApi.size() - 1);
    LOG(info) <<"Proxy API path: " << path << std::endl;
    vector<string> pathArray = splitString(path, "/");

    if (pathArray.size() == 0)
    {
        string error_message = string("Failed to remove media session due to invalid parameters");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    string streamId = pathArray[0];
    string action = pathArray.size() >= 2 ? pathArray[1] : "";
    string subAction = pathArray.size() >= 3 ? pathArray[2] : "";
    if (iequals(requestMethod, "delete"))
    {
        if (action.empty())
        {
            ret = handleStreamDelete(streamId, response);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Method is not allowed");
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else
    {
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Method is not allowed");
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode RtspServerManager::handleStreamAPIrequest(const Json::Value &req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    string deviceApi(PROXY_STREAM_API);
    string path = requestAPI.substr(deviceApi.size() - 1);
    LOG(info) <<"Proxy API path: " << path << std::endl;
    vector<string> pathArray = splitString(path, "/");
    string streamId;
    string action;
    string subAction;

    if (pathArray.size() == 0)
    {
        if (in.isMember("event") && !in["event"].isNull())
        {
            Json::Value event = in["event"];
            streamId = event.get("camera_id", EMPTY_STRING).asString();
            std::string change = event.get("change", "").asString();

            string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusOffline);

            if (change != changeLocal || streamId.empty())
            {
                string error_message = "Invalid parameter";
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
                return VmsErrorCode::InvalidParameterError;
            }

            requestMethod = "delete"; // SDR sends only POST requests. So as of now we changed it to delete request. Once it is handled from the SDR we can remove this.
        }
        else
        {
            string error_message = string("Failed to remove rtsp url due to invalid parameters");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
    }
    else
    {
        streamId = pathArray[0];
        LOG(info) <<"Proxy API streamId: " << streamId << std::endl;
        action = pathArray.size() >= 2 ? pathArray[1] : "";
        subAction = pathArray.size() >= 3 ? pathArray[2] : "";
    }

    if (iequals(requestMethod, "delete"))
    {
        if (action.empty())
        {
            ret = handleStreamDelete(streamId, response);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Method is not allowed");
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else
    {
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Method is not allowed");
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

extern "C" void* createRtspServerManagerObject()
{
    return new RtspServerManager;
}

extern "C" void deleteRtspServerManagerObject(RtspServerManager* object)
{
    delete object;
}
/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "config.h"
#include "device_manager.h"
#include "logger.h"
#include "boost/regex.hpp"
#include "mm_utils.h"
#include "unified_storage_types.h"


using namespace nv_vms;

constexpr const char* WEBROOT_PATH = "./webroot";

constexpr const char* DEFAULT_ANALYTIC_PROTOCOL = "http";
constexpr const char* DEFAULT_ANALYTIC_PORT = "30080";
constexpr const char* DEFAULT_ANALYTIC_ENDPOINT = "emdx";

VmsConfigManager* VmsConfigManager::getInstance()
{
    static VmsConfigManager instance;
    return &instance;
}

/* Default values for webrtc video quality tunning */
std::string defaultWebrtcQualityTunning = R"(
    {
        "webrtc_video_quality_tunning" :
        {
            "resolution_2160":
            {
                "bitrate_start" : 20000, "bitrate_range" : [15000,25000],
                "qp_range_I" : [0,30], "qp_range_P" : [0,51]
            },
            "resolution_1440":
            {
                "bitrate_start" : 10000, "bitrate_range" : [8000,15000],
                "qp_range_I" : [10,30], "qp_range_P" : [10,30]
            },
            "resolution_1080":
            {
                "bitrate_start" : 5000, "bitrate_range" : [3000,8000],
                "qp_range_I" : [10,30], "qp_range_P" : [10,30]
            },
            "resolution_720":
            {
                "bitrate_start" : 3000, "bitrate_range" : [2000,5000],
                "qp_range_I" : [10,30], "qp_range_P" : [10,30]
            },
            "resolution_480":
            {
                "bitrate_start" : 1000, "bitrate_range" : [800,3000],
                "qp_range_I" : [10,30], "qp_range_P" : [10,30]
            }
        }
    }
)";

std::string VmsConfigManager::getContainerType(std::string container)
{
    if (iequals(container, "mp4"))
    {
        return "QuickTime";
    }
    else if (iequals(container, "mkv"))
    {
        return "Matroska";
    }
    else
    {
        return container;
    }
}
bool VmsConfigManager::isVideoFormatSupported(const std::string& format)
{
    for (unsigned int i = 0; i < m_vmsConfig.video_codecs.size(); i++)
    {
        if (iequals(m_vmsConfig.video_codecs[i], format))
        {
            return true;
        }
    }
    return false;
}
bool VmsConfigManager::isAudioFormatSupported(const std::string& format)
{
    for (unsigned int i = 0; i < m_vmsConfig.audio_codecs.size(); i++)
    {
        if (iequals(m_vmsConfig.audio_codecs[i], format))
        {
            return true;
        }
    }
    return false;
}

bool VmsConfigManager::validateVideoFileExtension(const std::vector<string>& containers, std::string filename)
{
    int ret = false;
    string regex_str = format_vector(containers);
    boost::regex pattern(regex_str, boost::regex::icase);
    try
    {
        // The regex pattern matches against the trailing container extension
        // (e.g. \.(mp4|mkv|ts)$). A space inside the filename does not affect
        // extension validity, so we no longer gate on whitespace here — the
        // upload-validation path already rejects control characters.
        if (boost::regex_search(filename, pattern))
        {
            LOG(info) << filename << std::endl;
            ret = true;
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "validateVideoFileExtension fail: " << e.what() << endl;
    }
    return ret;
}

bool VmsConfigManager::isVideoContainerSupported(const std::string& container, std::string& absoluteFilePath)
{
    DeviceConfig config =  GET_CONFIG();
    /* Validate video file extension */
    if (validateVideoFileExtension(config.media_containers, absoluteFilePath) == false)
    {
        return false;
    }

    /* Validate video container */
    for (unsigned int i = 0; i < m_vmsConfig.media_containers.size(); i++)
    {
        const std::string containerSynonym = getContainerType(m_vmsConfig.media_containers[i]);
        if (iequals(m_vmsConfig.media_containers[i], container))
        {
            return true;
        }
        if (iequals(containerSynonym, container))
        {
            return true;
        }
        if (container.find(containerSynonym) != std::string::npos)
        {
            return true;
        }
    }
    return false;
}
Json::Value VmsConfigManager::getWebrtcVideoQualityValues(const uint32_t& height)
{
    Json::Value webrtc_video_quality_tunning;
    std::string selected_resolution;
    if (height == HEIGHT_2160p)
    {
        selected_resolution = "resolution_2160";
    }
    else if (height == HEIGHT_1440p)
    {
        selected_resolution = "resolution_1440";
    }
    else if (height == HEIGHT_1080p)
    {
        selected_resolution = "resolution_1080";
    }
    else if (height == HEIGHT_720p)
    {
        selected_resolution = "resolution_720";
    }
    else if (height == HEIGHT_480p)
    {
        selected_resolution = "resolution_480";
    }
    else
    {
        /* Dafault value */
        selected_resolution = "resolution_1080";
    }

    if (GET_CONFIG().webrtc_video_quality_tunning != Json::nullValue)
    {
        webrtc_video_quality_tunning = GET_CONFIG().webrtc_video_quality_tunning[selected_resolution];
    }
    return webrtc_video_quality_tunning;
}

string VmsConfigManager::getWebServerUrl()
{
    string domain_name = m_vmsConfig.server_domain_name.empty() == false ?
                            m_vmsConfig.server_domain_name : g_hostIp;
    string url = string("http://") + domain_name + string(":") + m_vmsConfig.http_port + string("/");
    return url;
}
string VmsConfigManager::getDefaultCredentials()
{
    std::string credentials = DEFAULT_USERNAME + string(":") + AUTHENTICATION_DOMAIN +
                            string(":") + DEFAULT_PASSWORD_HASH + string("\n");
    return credentials;
}

DeviceConfig& VmsConfigManager::getVmsConfig()
{
    return m_vmsConfig;
}

vector<shared_ptr<SensorInfo>>& VmsConfigManager::getCameraBackList()
{
    return m_backlist;
}

VmsConfigManager::VmsConfigManager()
{
    Json::Value config = loadVmsConfig();
    Json::Value network = config.get("network", Json::nullValue);
    if (network != Json::nullValue)
    {
        m_vmsConfig.http_port = network.get("http_port", "81").asString();
        char *http_port_env = getenv("HTTP_PORT");
        if (http_port_env != nullptr)
        {
            m_vmsConfig.http_port = string(http_port_env);
        }
        Json::Value default_stun_url;
        default_stun_url.append(DEFAULT_STUN_URL);
        Json::Value stun_urls = network.get("stunurl_list", default_stun_url);
        if(stun_urls.isArray())
        {
            for(Json::Value::const_iterator it = stun_urls.begin(); it != stun_urls.end(); ++it)
            {
                m_vmsConfig.stunurl_list.push_back(it->asString());
            }
        }
        Json::Value turn_urls = network.get("static_turnurl_list", Json::nullValue);
        if(turn_urls.isArray())
        {
            for(Json::Value::const_iterator it = turn_urls.begin(); it != turn_urls.end(); ++it)
            {
                m_vmsConfig.static_turnurl_list.push_back(it->asString());
            }
        }
        m_vmsConfig.use_coturn_auth_secret = network.get("use_coturn_auth_secret", false).asBool();
        Json::Value coturn_turn_urls = network.get("coturn_turnurl_list_with_secret", Json::nullValue);
        if(coturn_turn_urls.isArray())
        {
            for(Json::Value::const_iterator it = coturn_turn_urls.begin(); it != coturn_turn_urls.end(); ++it)
            {
                m_vmsConfig.coturn_turnurl_list_with_secret.push_back(it->asString());
            }
        }
        m_vmsConfig.use_twilio_stun_turn = network.get("use_twilio_stun_turn", false).asBool();
        m_vmsConfig.twilio_account_sid = network.get("twilio_account_sid", "").asString();
        m_vmsConfig.use_reverse_proxy = network.get("use_reverse_proxy", false).asBool();
        m_vmsConfig.reverse_proxy_server_address = network.get("reverse_proxy_server_address", "REVERSE_PROXY_SERVER_ADDRESS:100").asString();
        m_vmsConfig.twilio_auth_token = network.get("twilio_auth_token", "").asString();
        Json::Value ntp_Servers = network.get("ntp_servers", Json::nullValue);
        if(ntp_Servers.isArray())
        {
            for(Json::Value::const_iterator it = ntp_Servers.begin(); it != ntp_Servers.end(); ++it)
            {
                m_vmsConfig.ntpServers.push_back(it->asString());
            }
        }
        m_vmsConfig.use_sensor_ntp_time = network.get("use_sensor_ntp_time", false).asBool();

        m_vmsConfig.max_webrtc_out_connections = network.get("max_webrtc_out_connections", 8).asInt();
        m_vmsConfig.max_webrtc_in_connections = network.get("max_webrtc_in_connections", 8).asInt();
        m_vmsConfig.webservice_access_control_list = network.get("webservice_access_control_list", "").asString();
        m_vmsConfig.rtsp_preferred_network_iface = network.get("rtsp_preferred_network_iface", "").asString();
        m_vmsConfig.rtsp_server_port = network.get("rtsp_server_port", -1).asInt();
        m_vmsConfig.rtsp_server_instances_count = network.get("rtsp_server_instances_count", 1).asInt();
        m_vmsConfig.rtsp_server_use_socket_poll = network.get("rtsp_server_use_socket_poll", true).asBool();
        m_vmsConfig.rtcp_rtp_port_multiplex = network.get("rtcp_rtp_port_multiplex", true).asBool();
        m_vmsConfig.rtsp_in_base_udp_port_num = network.get("rtsp_in_base_udp_port_num", -1).asInt();
        char *rtsp_server_port_env = getenv("RTSP_SERVER_PORT");
        if (rtsp_server_port_env != nullptr)
        {
            m_vmsConfig.rtsp_server_port = stringToInt(string(rtsp_server_port_env), -1);
        }
        m_vmsConfig.rtsp_out_base_udp_port_num = network.get("rtsp_out_base_udp_port_num", -1).asInt();
        m_vmsConfig.rtsp_streaming_over_tcp = network.get("rtsp_streaming_over_tcp", false).asBool();
        m_vmsConfig.rtsp_server_reclamation_client_timeout_sec = network.get("rtsp_server_reclamation_client_timeout_sec", -1).asInt();
        m_vmsConfig.server_domain_name = network.get("server_domain_name", "").asString();
        m_vmsConfig.rx_socket_buffer_size = network.get("rx_socket_buffer_size", DEFAULT_RX_SOCKET_BUFFER_SIZE).asUInt();
        m_vmsConfig.tx_socket_buffer_size = network.get("tx_socket_buffer_size", DEFAULT_TX_SOCKET_BUFFER_SIZE).asUInt();
        m_vmsConfig.tx_rtp_packet_size = network.get("tx_rtp_packet_size", DEFAULT_TX_RTP_PACKET_SIZE).asUInt();
        m_vmsConfig.enable_packet_pacing = network.get("enable_packet_pacing", false).asBool();
        m_vmsConfig.rtp_packet_pace_time_us = network.get("rtp_packet_pace_time_us", 1000).asUInt();
        m_vmsConfig.rtp_packet_batch_size = network.get("rtp_packet_batch_size", 5).asUInt();
        m_vmsConfig.proxyclient_jitter_buffer_size_ms = network.get("proxyclient_jitter_buffer_size_ms", DEFAULT_PROXYCLIENT_JITTER_BUFFER_SIZE_MS).asUInt();
        m_vmsConfig.stream_monitor_interval_secs = network.get("stream_monitor_interval_secs", 2).asInt();
        m_vmsConfig.rtp_udp_port_range = network.get("rtp_udp_port_range", "").asString();
        m_vmsConfig.udp_latency_ms = network.get("udp_latency_ms", 200).asInt();
        m_vmsConfig.udp_drop_on_latency = network.get("udp_drop_on_latency", false).asBool();
        m_vmsConfig.webrtc_peer_conn_timeout_sec = network.get("webrtc_peer_conn_timeout_sec", WEBRTC_PEER_CONN_TIMEOUT_SEC).asInt();
        m_vmsConfig.enable_grpc = network.get("enable_grpc", true).asBool();
        m_vmsConfig.enable_frame_drop = network.get("enable_frame_drop", true).asBool();
        m_vmsConfig.webrtc_latency_ms = network.get("webrtc_latency_ms", 500).asInt();
        m_vmsConfig.grpc_server_port = network.get("grpc_server_port", "50051").asString();
        m_vmsConfig.ai_bridge_endpoint = network.get("ai_bridge_endpoint", "").asString();
        m_vmsConfig.webrtc_in_audio_sender_max_bitrate = network.get("webrtc_in_audio_sender_max_bitrate", 128000).asInt();
        m_vmsConfig.webrtc_in_video_degradation_preference = network.get("webrtc_in_video_degradation_preference", "resolution").asString();
        m_vmsConfig.enable_websocket_pingpong = network.get("enable_websocket_pingpong", false).asBool();
        m_vmsConfig.websocket_keep_alive_ms = network.get("websocket_keep_alive_ms", 5000).asInt();
        m_vmsConfig.webrtc_in_video_sender_max_framerate = network.get("webrtc_in_video_sender_max_framerate", 30).asInt();
        m_vmsConfig.remote_vst_address = network.get("remote_vst_address", "").asString();
        char *remote_addr_env = getenv("REMOTE_ADDRESS_ENV");
        if (remote_addr_env != nullptr)
        {
            m_vmsConfig.remote_vst_address = string(remote_addr_env);
        }
        m_vmsConfig.webrtc_port_range = network.get("webrtc_port_range", Json::nullValue);
        m_vmsConfig.webrtc_video_quality_tunning = network.get("webrtc_video_quality_tunning", Json::nullValue);
        if (m_vmsConfig.webrtc_video_quality_tunning == Json::nullValue)
        {
            Json::Reader reader;
            Json::Value default_webrtc_quality_value;
            if (reader.parse(defaultWebrtcQualityTunning, default_webrtc_quality_value))
            {
                m_vmsConfig.webrtc_video_quality_tunning = default_webrtc_quality_value["webrtc_video_quality_tunning"];
            }
        }
        m_vmsConfig.tokkio_plugin_server_url = network.get("tokkio_plugin_server_url", "").asString();
        char *tokkio_plugin_server_url = getenv("TOKKIO_PLUGIN_SERVER_URL");
        if (tokkio_plugin_server_url != nullptr)
        {
            m_vmsConfig.tokkio_plugin_server_url = string(tokkio_plugin_server_url);
        }
    }
    else
    {
        m_vmsConfig.stunurl_list.push_back(DEFAULT_STUN_URL);
    }

    Json::Value onvif = config.get("onvif", Json::nullValue);
    if (onvif != Json::nullValue)
    {
        m_vmsConfig.sensor_discovery_timeout = onvif.get("device_discovery_timeout_secs", 10).asInt();
        m_vmsConfig.sensor_discovery_freq_secs = onvif.get("device_discovery_freq_secs", 15).asInt();
        m_vmsConfig.onvif_request_timeout_secs = onvif.get("onvif_request_timeout_secs", 10).asInt();
        m_vmsConfig.onvif_sensor_time_sync_interval_secs = onvif.get("onvif_sensor_time_sync_interval_secs", 60).asInt();
        m_vmsConfig.onvif_sensor_time_sync_compensation_ms = onvif.get("onvif_sensor_time_sync_compensation_ms", 20).asInt();
        Json::Value discovery_interfaces = onvif.get("device_discovery_interfaces", Json::nullValue);
        if(discovery_interfaces.isArray())
        {
            for(Json::Value::const_iterator it = discovery_interfaces.begin(); it != discovery_interfaces.end(); ++it)
            {
                m_vmsConfig.sensor_discovery_interfaces.push_back(it->asString());
            }
        }
        m_vmsConfig.max_sensors_supported = onvif.get("max_devices_supported", 8).asInt();
        m_vmsConfig.default_bitrate = onvif.get("default_bitrate_kbps", DEFAULT_BITRATE_KBPS).asInt();
        m_vmsConfig.default_framerate = onvif.get("default_framerate", DEFAULT_FRAMERATE).asDouble();
        m_vmsConfig.default_resolution = onvif.get("default_resolution", DEFAULT_RESOLUTION).asString();
        m_vmsConfig.default_gov_length = onvif.get("default_gov_length", DEFAULT_MAX_GOVLENGTH).asInt();
        m_vmsConfig.default_profile = onvif.get("default_profile", DEFAULT_PROFILE).asString();
        m_vmsConfig.default_quality = onvif.get("default_quality", DEFAULT_QUALITY).asDouble();
        m_vmsConfig.default_encoding_interval = onvif.get("default_encoding_interval", DEFAULT_ENCODING_INTERVAL).asInt();
    }

    Json::Value data = config.get("data", Json::nullValue);
    if (data != Json::nullValue)
    {
        m_vmsConfig.storage_config_file = data.get("storage_config_file", DEFAULT_STORAGE_CONFIG_FILE).asString();
        Json::Value storage_config = loadStorageConfig(m_vmsConfig.storage_config_file);
        string video_dir_path = DEFAULT_RECORDED_VIDEO_DIR;
        string vms_data_path = DEFAULT_VMS_DB_DIR;
        if (storage_config != Json::nullValue)
        {
            video_dir_path = storage_config.get("video_path", DEFAULT_RECORDED_VIDEO_DIR).asString();
            vms_data_path = storage_config.get("data_path", DEFAULT_VMS_DB_DIR).asString();
            m_vmsConfig.total_video_storage_size_MB = storage_config.get("total_video_storage_size_MB", DEFAULT_VIDEO_STORAGE_SIZE).asDouble();
        }
        const char* storageSizeEnv = getenv("VST_VIDEO_STORAGE_SIZE_MB");
        if (storageSizeEnv != nullptr && storageSizeEnv[0] != '\0')
        {
            char* endPtr = nullptr;
            double doubleVal = std::strtod(storageSizeEnv, &endPtr);
            if (endPtr == storageSizeEnv || *endPtr != '\0' || doubleVal <= 0)
            {
                std::cerr << "[WARNING] Invalid VST_VIDEO_STORAGE_SIZE_MB='" << storageSizeEnv << "', value must be a positive number. "
                     << "Using config value: " << m_vmsConfig.total_video_storage_size_MB << endl;
            }
            else
            {
                m_vmsConfig.total_video_storage_size_MB = static_cast<size_t>(doubleVal);
                std::cout << "Overriding total_video_storage_size_MB from env: " << m_vmsConfig.total_video_storage_size_MB << endl;
            }
        }
        if (isDirExist(video_dir_path) == false)
        {
            createDir(video_dir_path);
        }
        if (isDirExist(vms_data_path) == false)
        {
            createDir(vms_data_path);
        }

        /* Assert/terminate the service in case of permission issues */
        if (isDirExist(vms_data_path) == false)
        {
            std::cerr << "--#-- Terminating the service, Please check directory permissions --#--" << endl;
            assert(false);
        }
        m_vmsConfig.recorded_video_root = getAbsolutePath(video_dir_path);
        m_vmsConfig.vst_data_path = getAbsolutePath(vms_data_path);

        m_vmsConfig.storage_threshold_percentage = data.get("storage_threshold_percentage", 95).asDouble();
        m_vmsConfig.storage_monitoring_frequency_secs = data.get("storage_monitoring_frequency_secs", 2).asDouble();
        m_vmsConfig.enable_aging_policy = data.get("enable_aging_policy", true).asBool();
        const char* agingEnv = getenv("ENABLE_AGING_POLICY");
        if (agingEnv != nullptr)
        {
            std::string value = std::string(agingEnv);
            std::transform(value.begin(), value.end(), value.begin(), ::tolower);
            if (value == "true" || value == "1" || value == "yes" || value == "on")
            {
                m_vmsConfig.enable_aging_policy = true;
            }
            else if (value == "false" || value == "0" || value == "no" || value == "off")
            {
                m_vmsConfig.enable_aging_policy = false;
            }
        }
        m_vmsConfig.max_video_download_size_MB = data.get("max_video_download_size_MB", DEFAULT_VIDEO_DOWNLOAD_SIZE).asDouble();
        m_vmsConfig.always_recording = data.get("always_recording", false).asBool();
        m_vmsConfig.event_recording = data.get("event_recording", false).asBool();
        m_vmsConfig.event_record_length_secs = data.get("event_record_length_secs", 5).asInt();
        m_vmsConfig.record_buffer_length_secs = data.get("record_buffer_length_secs", 1).asInt();
        m_vmsConfig.use_software_path = data.get("use_software_path", false).asBool();
        m_vmsConfig.use_software_encoder = data.get("use_software_encoder", false).asBool();
        m_vmsConfig.use_webrtc_inbuilt_encoder = data.get("use_webrtc_inbuilt_encoder", "").asString();
        char *use_sofware_path_env = getenv("USE_SOFTWARE_PATH");
        if (use_sofware_path_env != nullptr)
        {
            string str_software_path_env = string(use_sofware_path_env);
            if (iequals(str_software_path_env, "true"))
            {
                m_vmsConfig.use_software_path = true;
            }
            else if (iequals(str_software_path_env, "false"))
            {
                m_vmsConfig.use_software_path = false;
            }
        }
        m_vmsConfig.device_location = data.get("device_location", "").asString();
        m_vmsConfig.device_name = data.get("device_name", "").asString();
        m_vmsConfig.webrtc_in_fixed_resolution = data.get("webrtc_in_fixed_resolution", "").asString();
        m_vmsConfig.webrtc_in_max_framerate = data.get("webrtc_in_max_framerate", 30).asInt();
        m_vmsConfig.webrtc_in_video_bitrate_thresold_percentage = data.get("webrtc_in_video_bitrate_thresold_percentage", 50).asInt();
        m_vmsConfig.webrtc_in_passthrough = data.get("webrtc_in_passthrough", false).asBool();
        m_vmsConfig.webrtc_sender_quality = data.get("webrtc_sender_quality", DEFAULT_WEBRTC_SENDER_QUALITY).asString();
        m_vmsConfig.webrtc_out_encode_fallback_option = data.get("webrtc_out_encode_fallback_option", WEBRTC_OUT_FALLBACK_SOFTWARE).asString();
        m_vmsConfig.enable_rtsp_server_sei_metadata = data.get("enable_rtsp_server_sei_metadata", false).asBool();
        m_vmsConfig.enable_proxy_server_sei_metadata = data.get("enable_proxy_server_sei_metadata", false).asBool();

        m_vmsConfig.webrtc_out_enable_insert_sps_pps = data.get("webrtc_out_enable_insert_sps_pps", false).asBool();
        m_vmsConfig.webrtc_out_set_iframe_interval = data.get("webrtc_out_set_iframe_interval", 0).asInt();
        m_vmsConfig.webrtc_out_set_idr_interval = data.get("webrtc_out_set_idr_interval", 0).asInt();
        m_vmsConfig.webrtc_out_min_drc_interval = data.get("webrtc_out_min_drc_interval", 5).asInt();
        m_vmsConfig.webrtc_out_enc_quality_tuning = data.get("webrtc_out_enc_quality_tuning", "ultra_low_latency").asString();
        m_vmsConfig.webrtc_out_enc_preset = data.get("webrtc_out_enc_preset", "ultra_fast").asString();
        m_vmsConfig.enable_dec_low_latency_mode = data.get("enable_dec_low_latency_mode", false).asBool();
        m_vmsConfig.enable_avsync_udp_input = data.get("enable_avsync_udp_input", false).asBool();
        m_vmsConfig.enable_drc = data.get("enable_drc", true).asBool();
        m_vmsConfig.use_standalone_udp_input = data.get("use_standalone_udp_input", false).asBool();
        m_vmsConfig.enable_silent_audio_in_udp_input = data.get("enable_silent_audio_in_udp_input", false).asBool();
        m_vmsConfig.enable_udp_input_dump = data.get("enable_udp_input_dump", false).asBool();
        m_vmsConfig.webrtc_out_default_resolution = data.get("webrtc_out_default_resolution", "").asString();
#ifdef JETSON_PLATFORM
        m_vmsConfig.enable_ipc_path = data.get("enable_ipc_path", false).asBool();
#else
        m_vmsConfig.enable_ipc_path = false;
#endif
        m_vmsConfig.ipc_src_buffer_timestamp_copy  = data.get("ipc_src_buffer_timestamp_copy", true).asBool();
        m_vmsConfig.ipc_src_connection_attempts    = data.get("ipc_src_connection_attempts", 5).asInt();
        m_vmsConfig.ipc_src_connection_interval_us = data.get("ipc_src_connection_interval_us", 1000000).asInt();
        m_vmsConfig.ipc_sink_buffer_timestamp_copy = data.get("ipc_sink_buffer_timestamp_copy", true).asBool();
        m_vmsConfig.ipc_sink_buffer_copy           = data.get("ipc_sink_buffer_copy", true).asBool();

        m_vmsConfig.use_external_peerconnection = data.get("use_external_peerconnection", false).asBool();
        m_vmsConfig.enable_mega_simulation = data.get("enable_mega_simulation", false).asBool();
        m_vmsConfig.mega_simulation_delay_min_ms    = data.get("mega_simulation_delay_min_ms", 0).asInt();
        m_vmsConfig.mega_simulation_delay_max_ms    = data.get("mega_simulation_delay_max_ms", 0).asInt();
        m_vmsConfig.mega_simulation_base_time    = data.get("mega_simulation_base_time", "").asString();
        m_vmsConfig.default_file_expiry_minutes = data.get("default_file_expiry_minutes", DEFAULT_FILE_EXPIRY_MINUTES).asInt();
        char *default_file_expiry_minutes_env = getenv("DEFAULT_FILE_EXPIRY_MINUTES");
        if (default_file_expiry_minutes_env != nullptr)
        {
            m_vmsConfig.default_file_expiry_minutes = stringToInt(string(default_file_expiry_minutes_env), DEFAULT_FILE_EXPIRY_MINUTES);
        }
        m_vmsConfig.ingress_endpoint = data.get("ingress_endpoint", "").asString();
        char *default_ingress_endpoint = getenv("VST_INGRESS_ENDPOINT");
        if (default_ingress_endpoint != nullptr)
        {
            m_vmsConfig.ingress_endpoint = string(default_ingress_endpoint);
        }
        else
        {
            // If ingress endpoint is not set, use the default ingress endpoint locahhost:30888/vst
            m_vmsConfig.ingress_endpoint =  string(g_hostIp) + ":" + string(DEFAULT_INGRESS_ENDPOINT);
        }

        m_vmsConfig.use_webrtc_hw_dec = data.get("use_webrtc_hw_dec", false).asBool();
        m_vmsConfig.recorder_enable_frame_drop = data.get("recorder_enable_frame_drop", true).asBool();
        m_vmsConfig.recorder_max_frame_queue_size_bytes = data.get("recorder_max_frame_queue_size_bytes", 16000000).asInt();
        m_vmsConfig.recorder_low_latency = data.get("recorder_low_latency", true).asBool();
        string nv_streamer_directory_path = data.get("nv_streamer_directory_path", getCurrentDirPath()).asString();
        nv_streamer_directory_path = nv_streamer_directory_path.empty() ? getCurrentDirPath() : nv_streamer_directory_path;
        if (isDirExist(nv_streamer_directory_path) == false)
        {
            createDir(nv_streamer_directory_path);
        }

        string ipc_socket_path = data.get("ipc_socket_path", DEFAULT_IPC_SOCKET_PATH).asString();
        ipc_socket_path = ipc_socket_path.empty() ? DEFAULT_IPC_SOCKET_PATH : ipc_socket_path;
        if (isDirExist(ipc_socket_path) == false)
        {
            createDir(ipc_socket_path);
        }

        m_vmsConfig.nv_streamer_directory_path = nv_streamer_directory_path;
        m_vmsConfig.ipc_socket_path = ipc_socket_path;
        m_vmsConfig.nv_streamer_loop_playback = data.get("nv_streamer_loop_playback", true).asBool();
        m_vmsConfig.nv_streamer_seekable = data.get("nv_streamer_seekable", false).asBool();
        m_vmsConfig.nv_streamer_sync_playback = data.get("nv_streamer_sync_playback", false).asBool();
        m_vmsConfig.nv_streamer_sync_file_count = data.get("nv_streamer_sync_file_count", -1).asInt();
        m_vmsConfig.nv_streamer_rtsp_server_output_buffer_size_kb = data.get("nv_streamer_rtsp_server_output_buffer_size_kb", 800).asInt();
        m_vmsConfig.nv_streamer_max_upload_file_size_MB = data.get("nv_streamer_max_upload_file_size_MB", 10000).asUInt();
        Json::Value default_containers;
        default_containers.append("mp4");
        default_containers.append("mkv");
        Json::Value containers = data.get("nv_streamer_media_container_supported", default_containers);
        if(containers.isArray())
        {
            for(Json::Value::const_iterator it = containers.begin(); it != containers.end(); ++it)
            {
                m_vmsConfig.media_containers.push_back(it->asString());
            }
        }
        Json::Value default_metadata_containers;
        default_containers.append(".json");
        Json::Value metadat_containers = data.get("nv_streamer_metadata_container_supported",  default_metadata_containers);
        if(metadat_containers.isArray())
        {
            for(Json::Value::const_iterator it = metadat_containers.begin(); it != metadat_containers.end(); ++it)
            {
                m_vmsConfig.metadata_containers.push_back(it->asString());
            }
        }
        Json::Value default_video_codec;
        default_video_codec.append(DEFAULT_VIDEO_CODEC);
        Json::Value codecs = data.get("supported_video_codecs", default_video_codec);
        if(codecs.isArray())
        {
            for(Json::Value::const_iterator it = codecs.begin(); it != codecs.end(); ++it)
            {
                m_vmsConfig.video_codecs.push_back(it->asString());
            }
        }
        Json::Value default_audio_codec;
        default_audio_codec.append(DEFAULT_AUDIO_CODEC);
        Json::Value audio_codecs = data.get("supported_audio_codecs", default_audio_codec);
        if(audio_codecs.isArray())
        {
            for(Json::Value::const_iterator it = audio_codecs.begin(); it != audio_codecs.end(); ++it)
            {
                m_vmsConfig.audio_codecs.push_back(it->asString());
            }
        }

        m_vmsConfig.centralize_db_name = data.get("centralize_db_name", "").asString();
        m_vmsConfig.centralize_db_username = data.get("centralize_db_username", "").asString();
        m_vmsConfig.centralize_remote_db_password = data.get("centralize_remote_db_password", "").asString();
        m_vmsConfig.centralize_remote_db_hostaddr = data.get("centralize_remote_db_hostaddr", "").asString();
        m_vmsConfig.centralize_remote_db_port = data.get("centralize_remote_db_port", "").asString();
        m_vmsConfig.use_centralize_local_db = data.get("use_centralize_local_db", false).asBool();

        char *centralize_db_name = getenv("CENTRALIZE_DB_NAME");
        if (centralize_db_name != nullptr)
        {
            m_vmsConfig.centralize_db_name = string(centralize_db_name);
        }
        char *centralize_db_username = getenv("CENTRALIZE_DB_USERNAME");
        if (centralize_db_username != nullptr)
        {
            m_vmsConfig.centralize_db_username = string(centralize_db_username);
        }
        char *centralize_remote_db_password = getenv("CENTRALIZE_DB_PASSWORD");
        if (centralize_remote_db_password != nullptr)
        {
            m_vmsConfig.centralize_remote_db_password = string(centralize_remote_db_password);
        }
        char *centralize_remote_db_hostaddr = getenv("CENTRALIZE_DB_HOSTADDR");
        if (centralize_remote_db_hostaddr != nullptr)
        {
            m_vmsConfig.centralize_remote_db_hostaddr = string(centralize_remote_db_hostaddr);
        }
        char *centralize_remote_db_port = getenv("CENTRALIZE_DB_PORT");
        if (centralize_remote_db_port != nullptr)
        {
            m_vmsConfig.centralize_remote_db_port = string(centralize_remote_db_port);
        }
        char *centralize_local_db = getenv("CENTRALIZE_DB_LOCAL");
        if (centralize_local_db != nullptr)
        {
            std::string value = string(centralize_local_db);
            // Convert to lowercase for case-insensitive comparison
            std::transform(value.begin(), value.end(), value.begin(), ::tolower);

            // Check for common "true" values
            if (value == "true" || value == "1" || value == "yes" || value == "on")
            {
                m_vmsConfig.use_centralize_local_db = true;
            }
        }

        if ((centralize_db_name != nullptr && centralize_db_username != nullptr && centralize_remote_db_password != nullptr
            && centralize_remote_db_hostaddr != nullptr && centralize_remote_db_port != nullptr) || (m_vmsConfig.use_centralize_local_db))
        {
            m_vmsConfig.use_centralize_db = true;
        }

        m_vmsConfig.max_centralize_db_conn = data.get("max_network_db_connections", 10).asInt();
        m_vmsConfig.restore_rtsp_streams_on_startup = data.get("restore_rtsp_streams_on_startup", false).asBool();

        m_vmsConfig.enable_cloud_storage = data.get("enable_cloud_storage", false).asBool();
        m_vmsConfig.cloud_storage_type = data.get("cloud_storage_type", StorageConstants::MINIO_TYPE).asString();
        m_vmsConfig.cloud_storage_endpoint = data.get("cloud_storage_endpoint", "http://127.0.0.1:9000").asString();
        m_vmsConfig.cloud_storage_access_key = data.get("cloud_storage_access_key", "admin").asString();
        m_vmsConfig.cloud_storage_secret_key = data.get("cloud_storage_secret_key", "nvidia123!").asString();
        m_vmsConfig.cloud_storage_bucket = data.get("cloud_storage_bucket", "videos").asString();
        m_vmsConfig.cloud_storage_region = data.get("cloud_storage_region", "").asString();
        m_vmsConfig.cloud_storage_use_ssl = data.get("cloud_storage_use_ssl", false).asBool();
        m_vmsConfig.download_files_timeout_secs = data.get("download_files_timeout_secs", DEFAULT_DOWNLOAD_FILES_TIMEOUT_SECS).asInt();
        m_vmsConfig.picture_api_timeout_secs = data.get("picture_api_timeout_secs", DEFAULT_PICTURE_API_TIMEOUT_SECS).asInt();
    }
    else
    {
        string relative_dir_path = DEFAULT_RECORDED_VIDEO_DIR;
        string relative_vms_data_path = DEFAULT_VMS_DB_DIR;
        if (isDirExist(relative_dir_path) == false)
        {
            createDir(relative_dir_path);
        }
        if (isDirExist(relative_vms_data_path) == false)
        {
            createDir(relative_vms_data_path);
        }
        string absolute_dir_path = getAbsolutePath(relative_dir_path);
        string absolute_vms_data_path = getAbsolutePath(relative_vms_data_path);
        m_vmsConfig.recorded_video_root = absolute_dir_path;
        m_vmsConfig.vst_data_path = absolute_vms_data_path;

        string nv_streamer_directory_path = getCurrentDirPath();
        if (isDirExist(nv_streamer_directory_path) == false)
        {
            createDir(nv_streamer_directory_path);
        }
        m_vmsConfig.nv_streamer_directory_path = nv_streamer_directory_path;

        m_vmsConfig.media_containers.push_back("mp4");
        m_vmsConfig.media_containers.push_back("mkv");
        m_vmsConfig.video_codecs.push_back(DEFAULT_VIDEO_CODEC);
        m_vmsConfig.audio_codecs.push_back(DEFAULT_AUDIO_CODEC);
    }

    Json::Value gpu_indices = data.get("gpu_indices", Json::nullValue);
    if(gpu_indices != Json::nullValue && gpu_indices.isArray())
    {
        for(Json::Value::const_iterator it = gpu_indices.begin(); it != gpu_indices.end(); ++it)
        {
            cout << "GPU INDEX: " << it->asInt() << endl;
            m_vmsConfig.gpu_indices.push_back(it->asInt());
        }
    }
    if (m_vmsConfig.gpu_indices.size() > 0)
    {
        g_gpuIndex = m_vmsConfig.gpu_indices[0];  // Currenlty only first gpu index is supported
    }

    Json::Value notifications = config.get("notifications", Json::nullValue);
    if (notifications != Json::nullValue)
    {
        m_vmsConfig.enable_notification = notifications.get("enable_notification", true).asBool();
        m_vmsConfig.enable_notification_consumer = notifications.get("enable_notification_consumer", true).asBool();
        m_vmsConfig.use_message_broker = notifications.get("use_message_broker", EMPTY_STRING).asString();
        m_vmsConfig.use_message_broker_consumer = notifications.get("use_message_broker_consumer", EMPTY_STRING).asString();
        m_vmsConfig.message_broker_topic = notifications.get("message_broker_topic", "vst.event").asString();
        m_vmsConfig.message_broker_topic_consumer = notifications.get("message_broker_topic_consumer", EMPTY_STRING).asString();
        m_vmsConfig.message_broker_payload_key = notifications.get("message_broker_payload_key", "sensor.id").asString();
        m_vmsConfig.message_broker_metadata_topic = notifications.get("message_broker_metadata_topic", "test").asString();
        m_vmsConfig.redis_server_env_var = notifications.get("redis_server_env_var", "ROSIE_REDIS_SVC_SERVICE_HOST:6379").asString();
        m_vmsConfig.kafka_server_address = notifications.get("kafka_server_address", "").asString();
        m_vmsConfig.mqtt_broker_address = notifications.get("mqtt_broker_address", "").asString();
    }

    Json::Value debug = config.get("debug", Json::nullValue);
    if (debug != Json::nullValue)
    {
        m_vmsConfig.enable_perf_logging = debug.get("enable_perf_logging", true).asBool();

        m_vmsConfig.enable_qos_monitoring = debug.get("enable_qos_monitoring", true).asBool();
        m_vmsConfig.qos_logfile_path = debug.get("qos_logfile_path", DEFAULT_QOS_LOG_PATH).asString();
        m_vmsConfig.qos_data_capture_interval_sec = debug.get("qos_data_capture_interval_sec", 1).asInt();
        m_vmsConfig.qos_data_publish_interval_sec = debug.get("qos_data_publish_interval_sec", 5).asInt();
        m_vmsConfig.enable_gst_debug_probes = debug.get("enable_gst_debug_probes", true).asBool();

        m_vmsConfig.enable_prometheus = debug.get("enable_prometheus", false).asBool();
        m_vmsConfig.prometheus_port = debug.get("prometheus_port", DEFAULT_PROMETHEUS_PORT).asString();
        m_vmsConfig.enable_system_metric = debug.get("enable_system_metric", false).asBool();
        m_vmsConfig.system_metric_interval_sec = debug.get("system_metric_interval_sec", 5).asInt();
        char *prometheus_port_env = getenv("PROMETHEUS_PORT");
        if (prometheus_port_env != nullptr)
        {
            m_vmsConfig.prometheus_port = string(prometheus_port_env);
        }
        m_vmsConfig.enable_highlighting_logs = debug.get("enable_highlighting_logs", true).asBool();
        m_vmsConfig.enable_debug_apis = debug.get("enable_debug_apis", true).asBool();
        m_vmsConfig.dump_webrtc_input_stats = debug.get("dump_webrtc_input_stats", true).asBool();
        m_vmsConfig.enable_frameid_in_webrtc_stream = debug.get("enable_frameid_in_webrtc_stream", true).asBool();
        m_vmsConfig.enable_network_bandwidth_notification = debug.get("enable_network_bandwidth_notification", false).asBool();
        m_vmsConfig.enable_latency_logging = debug.get("enable_latency_logging", false).asBool();
        m_vmsConfig.enable_loopback_multicast = debug.get("enable_loopback_multicast", false).asBool();
        m_vmsConfig.update_record_details_in_sec = debug.get("update_record_details_in_sec", 10).asInt();
        if (m_vmsConfig.qos_logfile_path.empty())
        {
            m_vmsConfig.qos_logfile_path = DEFAULT_QOS_LOG_PATH;
        }
        if (isDirExist(m_vmsConfig.qos_logfile_path) == false)
        {
            if (createDir(m_vmsConfig.qos_logfile_path) == false)
            {
                cout << "Failed to create qos log file path = " << m_vmsConfig.qos_logfile_path << ", using default VST data path for logging " << m_vmsConfig.vst_data_path << endl;
                m_vmsConfig.qos_logfile_path = m_vmsConfig.vst_data_path;
            }
        }
    }

    Json::Value overlay = config.get("overlay", Json::nullValue);
    if (overlay != Json::nullValue)
    {
        m_vmsConfig.video_metadata_server = overlay.get("video_metadata_server", "").asString();
        m_vmsConfig.video_metadata_query_batch_size_num_frames = overlay.get("video_metadata_query_batch_size_num_frames", 300).asInt();
        m_vmsConfig.use_video_metadata_protobuf = overlay.get("use_video_metadata_protobuf", true).asBool();
        m_vmsConfig.enable_gem_drawing = overlay.get("enable_gem_drawing", false).asBool();
        m_vmsConfig.analytic_server_address = overlay.get("analytic_server_address", "").asString();
        m_vmsConfig.calibration_file_path = overlay.get("calibration_file_path", "").asString();
        m_vmsConfig.floor_map_file_path = overlay.get("floor_map_file_path", "").asString();
        m_vmsConfig.overlay_3d_sensor_name = overlay.get("3d_overlay_sensor_name", "").asString();
        m_vmsConfig.calibration_mode = overlay.get("calibration_mode", "").asString();
        m_vmsConfig.use_camera_groups = overlay.get("use_camera_groups", false).asBool();
        m_vmsConfig.enable_recentering = overlay.get("enable_recentering", false).asBool();
        m_vmsConfig.overlay_text_font_type = overlay.get("overlay_text_font_type", DEFAULT_CUOSD_FONT_TYPE).asString();
        m_vmsConfig.bbox_tolerance_ms = overlay.get("bbox_tolerance_ms", 0).asInt();
        m_vmsConfig.enable_overlay_skip_frame = overlay.get("enable_overlay_skip_frame", false).asBool();
        m_vmsConfig.halo_safety_udp_port = overlay.get("halo_safety_udp_port", -1).asInt();
        m_vmsConfig.halo_safety_proximity_class = overlay.get("halo_safety_proximity_class", "Forklift").asString();
        m_vmsConfig.halo_safety_active_text = overlay.get("halo_safety_active_text", "").asString();
        m_vmsConfig.halo_safety_active_text_color = overlay.get("halo_safety_active_text_color", "").asString();
        m_vmsConfig.halo_safety_active_text_bg_color = overlay.get("halo_safety_active_text_bg_color", "").asString();
        m_vmsConfig.halo_safety_inactive_text = overlay.get("halo_safety_inactive_text", "").asString();
        m_vmsConfig.halo_safety_inactive_text_color = overlay.get("halo_safety_inactive_text_color", "").asString();
        m_vmsConfig.halo_safety_inactive_text_bg_color = overlay.get("halo_safety_inactive_text_bg_color", "").asString();
        m_vmsConfig.halo_safety_text_size = overlay.get("halo_safety_text_size", 0).asInt();

        parseOverlayConfigs(overlay);
    }
    if (m_vmsConfig.analytic_server_address.empty())
    {
        string protocol = DEFAULT_ANALYTIC_PROTOCOL;
        string hostIp = g_hostIp;
        string analyticsPort = DEFAULT_ANALYTIC_PORT;
        string analyticsEndPoint = DEFAULT_ANALYTIC_ENDPOINT;
        m_vmsConfig.analytic_server_address = protocol + "://" + hostIp + ":" + analyticsPort + "/" + analyticsEndPoint;
    }

    Json::Value security = config.get("security", Json::nullValue);
    if (security != Json::nullValue)
    {
        m_vmsConfig.use_http_digest_authentication = security.get("use_http_digest_authentication", true).asBool();
        m_vmsConfig.use_https = security.get("use_https", true).asBool();
        m_vmsConfig.use_rtsp_authentication = security.get("use_rtsp_authentication", false).asBool();
        m_vmsConfig.use_multi_user = security.get("use_multi_user", false).asBool();
        m_vmsConfig.enable_user_cleanup = security.get("enable_user_cleanup", false).asBool();
        m_vmsConfig.session_max_age_sec = security.get("session_max_age_sec", static_cast<int>(DAYS_IN_SECONDS.count())).asInt();
        Json::Value multi_user_options = security.get("multi_user_extra_options",  Json::Value::null);
        m_vmsConfig.nv_org_id = security.get("nv_org_id", "").asString();
        m_vmsConfig.nv_ngc_key = security.get("nv_ngc_key", "").asString();
        char *nv_org_id = getenv("NV_ORG_ID");
        if (nv_org_id != nullptr)
        {
            m_vmsConfig.nv_org_id = string(nv_org_id);
        }
        char *nv_ngc_key = getenv("NV_NGC_KEY");
        if (nv_ngc_key != nullptr)
        {
            m_vmsConfig.nv_ngc_key = string(nv_ngc_key);
        }
        m_vmsConfig.nv_org_id_key = "Nv-Org-Id";
        char *nv_org_id_key = getenv("NV_ORG_ID_KEY");
        if (nv_org_id_key != nullptr)
        {
            m_vmsConfig.nv_org_id_key = string(nv_org_id_key);
        }
        if(multi_user_options.isArray())
        {
            for(Json::Value::const_iterator it = multi_user_options.begin(); it != multi_user_options.end(); ++it)
            {
                m_vmsConfig.multi_user_extra_options.push_back(it->asString());
            }
        }
    }
    if (m_vmsConfig.use_http_digest_authentication || m_vmsConfig.use_rtsp_authentication)
    {
        std::string passwordFilePath = getFilePathWithName(m_vmsConfig.vst_data_path, PASSWORD_FILE);
        m_vmsConfig.password_file_path = passwordFilePath;
        if (!isFileExist(passwordFilePath))
        {
            std::string defaultCredentials = getDefaultCredentials();
            createFile(passwordFilePath, defaultCredentials);
        }
    }

    /* Module endpoints */
    char *rtsp_server_ep_env = getenv("RTSP_SERVER_MODULE_ENDPOINT");
    if (rtsp_server_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleRtspServer] = string(rtsp_server_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleRtspServer] = RTSP_SERVER_MODULE_DEFAULT_ENDPOINT;
    }

    char *recorder_ep_env = getenv("RECORDER_MODULE_ENDPOINT");
    if (recorder_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleStreamRecorder] = string(recorder_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleStreamRecorder] = RECORDER_MODULE_DEFAULT_ENDPOINT;
    }

    char *storage_ep_env = getenv("STORAGE_MODULE_ENDPOINT");
    if (storage_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleStorageManagement] = string(storage_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleStorageManagement] = STORAGE_MODULE_DEFAULT_ENDPOINT;
    }

    char *sensor_ep_env = getenv("SENSOR_MODULE_ENDPOINT");
    if (sensor_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleSensorManagement] = string(sensor_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleSensorManagement] = SENSOR_MANAGEMENT_MODULE_DEFAULT_ENDPOINT;
    }

    char *replay_stream_ep_env = getenv("REPLAYSTREAM_MODULE_ENDPOINT");
    if (replay_stream_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleReplayStream] = string(replay_stream_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleReplayStream] = REPLAY_STREAM_MODULE_DEFAULT_ENDPOINT;
    }

    char *live_stream_ep_env = getenv("LIVESTREAM_MODULE_ENDPOINT");
    if (live_stream_ep_env != nullptr)
    {
        m_vmsConfig.module_endpoints[ModuleLiveStream] = string(live_stream_ep_env);
    }
    else
    {
        m_vmsConfig.module_endpoints[ModuleLiveStream] = LIVE_STREAM_MODULE_DEFAULT_ENDPOINT;
    }

    // Observability configuration
    Json::Value observability = config.get("observability", Json::nullValue);
    if (observability != Json::nullValue)
    {
        m_vmsConfig.enable_telemetry = observability.get("enable_telemetry", false).asBool();
        m_vmsConfig.otlp_endpoint = observability.get("otlp_endpoint", "").asString();
    }
    
    // Environment variable override for telemetry (for containerized deployments)
    char *enable_telemetry_env = getenv("ENABLE_TELEMETRY");
    if (enable_telemetry_env != nullptr)
    {
        m_vmsConfig.enable_telemetry = (strcmp(enable_telemetry_env, "true") == 0 || strcmp(enable_telemetry_env, "1") == 0);
    }
    
    // Environment variable override for OTLP endpoint
    char *otlp_endpoint_env = getenv("OTLP_ENDPOINT");
    if (otlp_endpoint_env != nullptr)
    {
        m_vmsConfig.otlp_endpoint = string(otlp_endpoint_env);
    }

    m_vmsConfig.printInfo();

    Json::Value backlist = scanCameraBackList();
    Json::Value list = backlist["cameras"];
    for (uint32_t i = 0; i < list.size(); i++)
    {
        shared_ptr<SensorInfo> sensor (new SensorInfo);
        Json::Value camera = list[i];
        bool enabled = camera.get("enabled", false).asBool();
        if(enabled == false)
        {
            continue;
        }
        sensor->id = camera.get("id", "").asString();
        sensor->ip = camera.get("ip", "").asString();
        sensor->name = camera.get("name", "").asString();
        m_backlist.push_back(sensor);
    }
    /* Setting OS's internal receive buffer */
    setRecvMaxSocketBufferSize(m_vmsConfig.rx_socket_buffer_size);
    setSendMaxSocketBufferSize(m_vmsConfig.tx_socket_buffer_size);
}

string VmsConfigManager::getWebRootPath()
{
    return WEBROOT_PATH;
}

vector<string> VmsConfigManager::getNGCAuthHeaders()
{
    vector<string> customHeaders;
    const string nvOrgId = m_vmsConfig.nv_org_id;
    const string nvNgcKey = m_vmsConfig.nv_ngc_key;
    customHeaders.push_back(m_vmsConfig.nv_org_id_key + string(": ") + nvOrgId);
    customHeaders.push_back("Authorization" + string(": Bearer ") + nvNgcKey);
    return customHeaders;
}

vector<string> VmsConfigManager::getEdgeDeviceHeaders(bool isEdgeDevice)
{
    vector<string> customHeaders = getNGCAuthHeaders();
    const string edgedevice = isEdgeDevice ? "true" : "false";
    customHeaders.push_back("isEdgeDevice" + string(": ") + edgedevice);
    return customHeaders;
}

void VmsConfigManager::parseOverlayConfigs(const Json::Value& overlay)
{
    // Parse labels.txt and create overlay_class_labels
    std::vector<std::string> class_labels;
    std::ifstream labels_file(DEFAULT_LABELS_FILE_PATH);
    if (labels_file.is_open())
    {
        std::string line;
        while (std::getline(labels_file, line))
        {
            std::stringstream ss(line);
            std::string label;

            // labels.txt can be of format - "label1\nlabel2\n"
            if (!line.empty() && line.find(';') == std::string::npos)
            {
                class_labels.push_back(line);
            }
            else
            {
                // labels.txt can be of format - "label1:confidence;label2:confidence;\n"
                while (std::getline(ss, label, ';'))
                {
                    if (!label.empty())
                    {
                        // Extract only the label name before the colon
                        size_t colon_pos = label.find(':');
                        if (colon_pos != std::string::npos)
                        {
                            class_labels.push_back(label.substr(0, colon_pos));
                        }
                        else
                        {
                            // If no colon found, use the entire label
                            class_labels.push_back(label);
                        }
                    }
                }
            }
        }
        labels_file.close();
    }

    // Create JSON array and add to config
    Json::Value overlay_class_labels(Json::arrayValue);
    for (const auto& label : class_labels)
    {
        overlay_class_labels.append(label);
    }
    m_vmsConfig.overlay_class_labels = overlay_class_labels;

    /* Proximity labels */
    Json::Value overlay_proximity_labels(Json::arrayValue);
    overlay_proximity_labels.append("proximity_bubble");
    overlay_proximity_labels.append("proximity_bubble_inner");
    overlay_proximity_labels.append("proximity_bubble_outer");
    overlay_proximity_labels.append("proximity_bubble_border");
    overlay_proximity_labels.append("proximity_line");
    m_vmsConfig.overlay_proximity_labels = overlay_proximity_labels;

    m_vmsConfig.overlay_color_code = overlay.get("overlay_color_code", Json::nullValue);
    if (m_vmsConfig.overlay_color_code != Json::nullValue)
    {
        for (const auto& colorEntry : m_vmsConfig.overlay_color_code)
        {
            if (colorEntry.isObject())
            {
                for (const auto& key : colorEntry.getMemberNames())
                {
                    const Json::Value& colorArray = colorEntry[key];
                    if (colorArray.isArray() && colorArray.size() == 4)
                    {
                        std::vector<int> rgb =
                        {
                            colorArray[0].asInt(),
                            colorArray[1].asInt(),
                            colorArray[2].asInt(),
                            colorArray[3].asInt()
                        };
                        m_vmsConfig.color_map[key] = rgb;
                    }
                }
            }
        }
    }
}

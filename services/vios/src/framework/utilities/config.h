/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_info.h"
#include "device_manager.h"
#include "utils.h"
#include "fs_utils.h"

#include <string>
#include <array>
#include <chrono>
#include <jsoncpp/json/json.h>
#include <unordered_map>

inline constexpr const char* ADAPTOR_CONFIG_FILE = "configs/adaptor_config.json";
inline constexpr const char* VMS_CONFIG_FILE = "configs/vst_config.json";
inline constexpr const char* ONVIF_CAMERA_LIST_FILE = "configs/onvif_camera_list.json";
inline constexpr const char* DEVICE_DETAILS_FILE = "configs/device_details.json";
inline constexpr const char* CAMERA_BLACK_LIST_FILE = "configs/camera_blacklist.json";
inline constexpr const char* DEFAULT_STORAGE_CONFIG_FILE = "configs/vst_storage.json";
inline constexpr const char* DEFAULT_LABELS_FILE_PATH = "configs/labels.txt";
inline constexpr const char* DEFAULT_RECORDED_VIDEO_DIR = "./vst_video/";
inline constexpr const char* DEFAULT_VMS_DB_DIR = "./vst_data/";
inline constexpr const char* SELF_SIGNED_CERTIFICATE_FILE_NAME = "self_signed_certificate.pem";
inline constexpr const char* CA_CERTIFICATE_FILE_NAME = "ca_certificate.pem";
inline constexpr int DEFAULT_VIDEO_STORAGE_SIZE = 10000;
inline constexpr int DEFAULT_VIDEO_DOWNLOAD_SIZE = 1000;
inline constexpr const char* DEFAULT_QOS_LOG_PATH = "./vst_data/logs/";
inline constexpr const char* DEFAULT_NVSTREAMER_DIR_PATH = "./";
inline constexpr const char* RTSP_STREAMS_FILE = "configs/rtsp_streams.json";
inline constexpr const char* DEFAULT_STUN_URL = "stun.l.google.com:19302";
inline constexpr const char* DEFAULT_VIDEO_CODEC = "h264";
inline constexpr const char* DEFAULT_AUDIO_CODEC = "pcmu";
inline constexpr double DEFAULT_VIDEO_FRAME_RATE = 30.0;
inline constexpr int DEFAULT_NVSTREAMER_MAX_BITRATE = 30000000;
inline constexpr const char* DEFAULT_WEBCAM_NAME = "webcam_";
inline constexpr int DEFAULT_WEBRTC_MAX_BITRATE = 10000;
inline constexpr int DEFAULT_WEBRTC_MIN_BITRATE = 2000;
inline constexpr int DEFAULT_WEBRTC_START_BITRATE = 4000;
inline constexpr int WEBRTC_PEER_CONN_TIMEOUT_SEC = 10;
inline constexpr int DEFAULT_DOWNLOAD_FILES_TIMEOUT_SECS = 120;
inline constexpr int DEFAULT_PICTURE_API_TIMEOUT_SECS = 20;

inline constexpr const char* AUTHENTICATION_DOMAIN = "Nvidia VST";
inline constexpr const char* PASSWORD_FILE = ".htpasswd";
inline constexpr const char* DEFAULT_USERNAME = "admin";
inline constexpr const char* DEFAULT_PASSWORD_HASH = "f6e8d9d0a5be7df50ec30a55583657dc";
inline constexpr const char* DEFAULT_ADMIN_USERNAME = "admin";
inline constexpr const char* DEFAULT_ADMIN_PASSWORD = "admin";
inline constexpr std::chrono::seconds DAYS_IN_SECONDS{std::chrono::hours{24} * 30};

#define GET_CONFIG nv_vms::VmsConfigManager::getInstance()->getVmsConfig
inline constexpr const char* NV_STREAMER = "nvstream";
inline constexpr const char* LIVE_STREAMER = "live";
inline constexpr const char* VOD_STREAMER = "vod";

#define to_MB(bytes)  (bytes / (1024 * 1024))
#define to_bytes(MB)  (MB * (1024 * 1024))

inline constexpr const char* RTSP_SERVER_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";
inline constexpr const char* RECORDER_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";
inline constexpr const char* STORAGE_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";
inline constexpr const char* SENSOR_MANAGEMENT_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";
inline constexpr const char* REPLAY_STREAM_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";
inline constexpr const char* LIVE_STREAM_MODULE_DEFAULT_ENDPOINT = "http://localhost:30000";

inline constexpr const char* NV_CSI_SENSOR = "csi_sensor";

namespace nv_vms {

class VmsConfigManager
{
    public:
        static VmsConfigManager* getInstance();
        std::string getContainerType(std::string container);
        bool isVideoFormatSupported(const std::string& format);
        bool isAudioFormatSupported(const std::string& format);
        bool isVideoContainerSupported(const std::string& container, std::string& absoluteFilePath);
        Json::Value getWebrtcVideoQualityValues(const uint32_t& height);
        string getWebServerUrl();
        string getDefaultCredentials();
        DeviceConfig& getVmsConfig();
        vector<shared_ptr<SensorInfo>>& getCameraBackList();
        string getWebRootPath();
        vector<string> getNGCAuthHeaders();
        vector<string> getEdgeDeviceHeaders(bool isEdgeDevice);
        bool validateVideoFileExtension(const std::vector<string>& containers, std::string filename);
        void parseOverlayConfigs(const Json::Value& overlay);
    private:
        VmsConfigManager();
    private:
        DeviceConfig m_vmsConfig;
        vector<shared_ptr<SensorInfo>> m_backlist;
};

} //nv_vms

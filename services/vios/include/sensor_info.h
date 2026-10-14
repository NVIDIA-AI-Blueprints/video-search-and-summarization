/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <vector>
#include <array>
#include <memory>
#include <map>
#include <mutex>
#include <utility>
#include <condition_variable>
#include <atomic>
#include <set>
#include <jsoncpp/json/json.h>
#include <curl/curl.h>
#include "error_code.h"


inline constexpr const char* SENSOR_TYPE_ONVIF = "sensor_onvif";
inline constexpr const char* SENSOR_TYPE_MMS_ONVIF = "sensor_mms_onvif";
inline constexpr const char* SENSOR_TYPE_RTSP = "sensor_rtsp";
inline constexpr const char* SENSOR_TYPE_NVSTREAM = "sensor_nvstream";
inline constexpr const char* SENSOR_TYPE_UDP = "sensor_udp";
inline constexpr const char* SENSOR_TYPE_WEBRTC = "sensor_webrtc";
inline constexpr const char* SENSOR_TYPE_GENERIC = "sensor_generic";
inline constexpr const char* SENSOR_TYPE_REMOTE = "sensor_edge";
inline constexpr const char* SENSOR_TYPE_CSI = "sensor_csi";
inline constexpr const char* SENSOR_TYPE_FILE = "sensor_file";

// Record config values
inline constexpr const char* RECORD_CONFIG_CLOUD_SCANNED = "Cloud";  // For cloud-scanned/imported files

inline constexpr int MAX_SENSOR_NAME_LENGTH = 175;
inline constexpr int MAX_FILE_NAME_LENGTH = 140;
namespace nv_vms {

class SensorControl;
class NvSoap;

struct Range
{
    std::string min;
    std::string max;
};

struct Rect
{
    std::string bottom;
    std::string top;
    std::string right;
    std::string left;
};

struct MultiCast
{
    std::string AddressType;
    std::string IPAddress;
    std::string Port;
    std::string TTL;
    std::string AutoStart;
};

struct Token
{
    std::string profileName;
    std::string encoderToken;
    std::string sourceToken;
    std::string profileToken;
    std::string ptzToken;
    std::string ptzNodeToken;
};

struct Resolution
{
    std::string width;
    std::string height;

    void operator=(const std::string& value);
    bool operator==(const Resolution& res) const;
    bool empty() const;
    std::string getString() const;
    int getPixels() const;

};

struct VideoEncoderConfigurationsOptions
{
    Range EncodingIntervalRange;
    Range BitrateRange;
    Range GovLengthRange;
    std::string FrameRateSupported;
    std::vector <Resolution> ResolutionsAvailable;
    std::string encoding;
    Range qualityRange;
    std::vector <std::string> profilesSupported;
    bool isBframesPresent = false;
};

struct SensorEncoderSettingsOptions
{
    std::vector <VideoEncoderConfigurationsOptions> encoderSettingsOptions;
    std::vector <std::string> videoEncodingSupported;
};

enum AuthenticationMethods
{
    AUTH_METHOD_NONE            = 0,        // No flags set
    AUTH_METHOD_USERNAME_TOKEN  = 1 << 0,   // Bit 0
    AUTH_METHOD_DIGEST          = 1 << 1    // Bit 1
};

struct ServiceCapabilities
{
    AuthenticationMethods supportedAuthMethods = AUTH_METHOD_NONE;
    AuthenticationMethods securedAuthMethod = AUTH_METHOD_NONE;
    std::string supportedHashingAlgorithms;
};

struct HashingAlgorithmInfo
{
    std::string algorithm;  // e.g., "SHA-256"/"MD5,SHA-256"/"MD5"
};

// Opaque, typed stand-in for a libcurl easy handle (libcurl declares CURL as void)
struct CurlEasyHandle;

class ClientSession
{
    public:
        virtual ~ClientSession();
        ClientSession();

        ClientSession(const ClientSession&) = delete;
        ClientSession& operator=(const ClientSession&) = delete;
        ClientSession(ClientSession&&) = delete;
        ClientSession& operator=(ClientSession&&) = delete;

        CurlEasyHandle* getCurlClient();
        std::shared_ptr<NvSoap> getNvSoap();
    protected:
        CurlEasyHandle* m_curl;
        std::shared_ptr<NvSoap> m_nvsoap;
};

struct SensorVideoEncoderSettingsValues
{
    std::string container;
    std::string encoding;
    Resolution resolution;
    std::string frameRate;
    std::string bitrate;
    std::string encodingInterval;
    std::string encodingProfile;
    std::string quality;
    std::string govLength;
    std::string numFrames;
    bool isBframesPresent = false; // Indicates if video stream has B-frames
};

struct SensorAudioEncoderSettingsValues
{
    /* Sensors that never report audio settings leave this untouched, and an
     * indeterminate bool does not merely read as true: it carries a value
     * outside {0,1}, which turns a later 'enable ? a : b' into arithmetic on
     * that value and yields an enumerator that is neither a nor b. */
    bool   enable = false;
    std::string container;
    std::string encoding;
    std::string sample_rate;
    std::string bits_per_sample;
    std::string channels;
};

struct SensorNetworkInfo
{
    std::pair<std::string, bool> token;
    std::string interfaceName;

    bool enableIpv4 = false;
    std::string enableDhcp4;
    std::string IPAddr4;
    std::string prefixLen4;

    bool enableIpv6 = false;
    std::string enableDhcp6;
    std::string IPAddr6;
    std::string prefixLen6;
};

enum CamTNRMode
{
    GST_NVCAM_NR_OFF = 0, // NoiseReduction_Off
    GST_NVCAM_NR_FAST, // NoiseReduction_Fast (Default)
    GST_NVCAM_NR_HIGHQUALITY, // NoiseReduction_HighQuality
    GST_NVCAM_NR_MAX
};

enum CamwbMode
{
    GST_NVCAM_WB_MODE_OFF = 0, // off
    GST_NVCAM_WB_MODE_AUTO, // auto
    GST_NVCAM_WB_MODE_INCANDESCENT, // incandescent
    GST_NVCAM_WB_MODE_FLUORESCENT, // fluorescent
    GST_NVCAM_WB_MODE_WARM_FLUORESCENT, // warm-fluorescent
    GST_NVCAM_WB_MODE_DAYLIGHT, // daylight
    GST_NVCAM_WB_MODE_CLOUDY_DAYLIGHT, // cloudy-daylight
    GST_NVCAM_WB_MODE_TWILIGHT, // twilight
    GST_NVCAM_WB_MODE_SHADE, // shade
    GST_NVCAM_WB_MODE_MANUAL, // manual
    GST_NVCAM_WB_MODE_MAX
};

enum AeAntibandingMode
{
    GST_NVCAM_AEANTIBANDING_OFF = 0, // AeAntibandingMode_Off
    GST_NVCAM_AEANTIBANDING_AUTO, // AeAntibandingMode_Auto
    GST_NVCAM_AEANTIBANDING_50HZ, // AeAntibandingMode_50HZ
    GST_NVCAM_AEANTIBANDING_60HZ, // AeAntibandingMode_60HZ
    GST_NVCAM_AEANTIBANDING_MAX
};

enum EdgeEnhancementMode
{
    GST_NVCAM_EE_OFF = 0, // EdgeEnhancement_Off
    GST_NVCAM_EE_FAST, // EdgeEnhancement_Fast
    GST_NVCAM_EE_HIGHQUALITY, // EdgeEnhancement_HighQuality
    GST_NVCAM_EE_MAX
};

struct SensorImageSettingsOptions
{
    Range Brightness;
    Range ColorSaturation;
    Range Contrast;
    Range Sharpness;
    std::vector<std::string> BacklightCompensationModes; // ON/OFF
    Range BacklightCompensationLevel;
    std::vector<std::string> ExposureModes; // AUTO/MANUAL
    std::vector<std::string> ExposurePriorities; // LowNoise/FrameRate
    Range MinExposureTime;
    Range MaxExposureTime;
    Range ExposureMaxGain;
    Range ExposureTime;
    Range ExposureGain;
    std::vector<std::string> IrCutFilterModes; // OFF/ON/AUTO
    std::vector<std::string> WideDynamicRangeModes; // OFF/ON
    Range WideDynamicRangeLevel;
    std::vector<std::string> WhiteBalanceModes; // AUTO/MANUAL
    Range WhiteBalanceYrGain;
    Range WhiteBalanceYbGain;

    /* Native sensor settings */
    std::vector<std::string> TemporalNoiseReductionModes;
    std::vector<std::string> AeAntibandingModes;
    std::vector<std::string> EdgeEnhancementModes;
    Range EdgeEnhancementStrength;
    Range ExposureCompensation;
};

struct SensorImageSettingsValues
{
    std::string Brightness;
    std::string ColorSaturation;
    std::string Contrast;
    std::string Sharpness;
    std::string BacklightCompensationMode; // ON/OFF
    std::string BacklightCompensationLevel;
    std::string ExposureMode; // AUTO/MANUAL
    std::string ExposurePriority; // LowNoise/FrameRate
    Rect ExposureWindow;
    std::string MinExposureTime;
    std::string MaxExposureTime;
    std::string ExposureMaxGain;
    std::string ExposureTime;
    std::string ExposureGain;
    std::string IrCutFilterMode; // OFF/ON/AUTO
    std::string WideDynamicRangeMode; // OFF/ON
    std::string WideDynamicRangeLevel;
    std::string WhiteBalanceMode; // AUTO/MANUAL
    std::string WhiteBalanceYrGain;
    std::string WhiteBalanceYbGain;

    /* Native sensor Settings */
    std::string TemporalNoiseReductionMode;
    std::string AeAntibandingMode;
    std::string EdgeEnhancementMode;
    std::string EdgeEnhancementStrength;
    std::string ExposureCompensation;
};

struct SensorSettings
{
    SensorImageSettingsValues imageValues;
    SensorImageSettingsOptions imageOptions;
    SensorEncoderSettingsOptions encoderOptions;
    SensorVideoEncoderSettingsValues encoderValues;
    SensorAudioEncoderSettingsValues audioEncoderValues;
    MultiCast multiCast;
    Token token;
};

struct SensorStatus
{
    SensorStatusEvent event;
    std::string sensorId;
    std::string serverId;
    std::string timeStamp;
    std::string type;
    std::string sensorName;
    std::string tags;

    SensorStatus();
    static std::string getEventString(const SensorStatusEvent event);
};

struct SensorPosition
{
    std::pair<std::string, std::string> origin;
    std::pair<std::string, std::string> geoLocation;
    std::pair<std::string, std::string> coordinates;
    std::string direction;
    std::string fieldOfView;
    std::string depth;
    SensorPosition();
    void printInfo();
};

struct SensorMetadata
{
    std::map<std::string, std::string, std::less<>> data;
    public:
        void printInfo();
};

struct UserInfo
{
    std::string username;
    UserInfo();
};

enum StreamType
{
    Http,
    Hls,
    Rtsp,
    FileDownload,
    Udp,
    Webrtc,
    Native,
    NotSupported
};

enum StreamDirection
{
    StreamDirectionInvalid = -1,
    StreamDirectionIn,
    StreamDirectionOut,
    StreamDirectionBidirectional
};

struct ptzRange
{
    std::string x_min;
    std::string x_max;
    std::string y_min;
    std::string y_max;
};

enum PTZAction
{
    PanTilt = 0,
    Zoom,
    Unknown = 0xFFFF
};

enum StreamStorageType
{
    StreamStorageTypeLocal = 0,
    StreamStorageTypeCloud,
    StreamStorageTypeUnknown
};

inline std::string StreamStorageTypeToString(StreamStorageType storageType)
{
    switch (storageType)
    {
        case StreamStorageTypeLocal:   return "Local";
        case StreamStorageTypeCloud:   return "Cloud";
        case StreamStorageTypeUnknown: return "Unknown";
        default:                       return "Unknown";
    }
}

inline std::string PTZActionToString(PTZAction ptz)
{
    switch ((int)ptz)
    {
        case PTZAction::PanTilt :   return "PanTilt";
        case PTZAction::Zoom :   return "Zoom";
        default:      return "Unknown";
    }
}

inline PTZAction PTZStringtoOperation(std::string op)
{
    if (op == "PanTilt")
    {
        return PTZAction::PanTilt;
    }
    else if ( op == "Zoom")
    {
        return  PTZAction::Zoom;
    }
    else
    {
        return PTZAction::Unknown;
    }
}

struct StreamInfo
{
    std::string live_url;
    std::string replay_url;
    std::string live_proxy_url;
    std::string name;
    std::string socket_name;
    std::string id;
    std::string sensorId;
    bool isMainStream;
    StreamStorageType storageLocation;  // Storage location: Local, Cloud, or Unknown
    StreamType stream_type;
    StreamDirection direction;
    SensorSettings settings;
    std::mutex m_streamLock;
    int duration;
private:
    std::pair<StreamStatus, std::string> eStatusCode;
public:
    StreamInfo ();
    void printInfo();
    void updateErrorStatus(const std::pair<StreamStatus, std::string> error, bool updateDB = true);
    void updateVideoEncoderValues(const SensorVideoEncoderSettingsValues&, bool updateDB = true);
    SensorVideoEncoderSettingsValues& getvideoEncoderValues();
    void updateVideoEncoderOptions(const SensorEncoderSettingsOptions&);
    SensorEncoderSettingsOptions& getvideoEncoderOptions();
    void updateAudioEncoderValues(const SensorAudioEncoderSettingsValues&, bool updateDB = true);
    SensorAudioEncoderSettingsValues& getAudioEncoderValues();
    void updateImageValues(const SensorImageSettingsValues&);
    std::pair<StreamStatus, std::string> getErrorStatus();
    void updateStreamtype(const StreamType type);
    Json::Value toJson(bool isStreamerDevice = false);
};

struct OnvifServiceInfo
{
    std::string name_space;
    std::string url;
};

struct SensorInfo
{
    std::string id;
    std::string sensorId;
    std::string ip;
    std::string name;
    std::string url;
    std::map<std::string, OnvifServiceInfo> serviceUrls;
    std::string model;
    std::string hardware;
    std::string manufacturer;
    std::string firmware_version;
    std::string serial_number;
    std::string hardware_id;
    std::string location;
    std::string tags;
    std::string user;
    std::string password;
    std::vector<std::shared_ptr<StreamInfo>> streams;
    std::vector<std::shared_ptr<SensorMetadata>> metadata;
    std::map<PTZAction, ptzRange> ptzInfo;
    std::set<std::shared_ptr<UserInfo>> users;
    bool isAutoDiscovered;
    std::string type;
    SensorPosition position;
    std::mutex m_sensorLock;
    std::mutex m_streamLock;
    std::mutex m_userLock;
    bool m_notify;
    bool isRemoteSensor;
    std::string remoteDeviceId;
    std::string remoteDeviceName;
    std::string remoteDeviceLocation;
    ServiceCapabilities serviceCapabilities;
    std::shared_ptr<ClientSession> clientSession;
    SensorStatusEvent sensorStatus;
private:
    std::mutex sessionMutex;
public:
    std::pair<int, std::string> httpStatusCode;

    SensorInfo();
    SensorInfo (const SensorInfo& sensorInfo);
    ~SensorInfo();
    void operator=(const SensorInfo& sensorInfo);
    bool operator==(const std::string& id);
    bool operator==(const SensorInfo& sensorInfo);
    void updateStreams(std::vector<std::shared_ptr<StreamInfo>>& InStreams);
    bool addStreams(std::shared_ptr<StreamInfo>& in_stream);
    void clearStreams();
    std::vector<std::shared_ptr<StreamInfo>>& getStreams();
    std::vector<std::shared_ptr<SensorMetadata>> getMetadata();
    std::shared_ptr<StreamInfo> getStream(const std::string& id);
    void clearServiceUrls();
    void updateSensorStatus(const SensorStatusEvent status);
    SensorStatusEvent getSensorStatus();
    void updateHttpErrorStatus(const std::pair<int, std::string> http_error);
    std::pair<int, std::string> getHttpErrorStatus();
    void updateCredentials(const std::string& in_username, const std::string& in_password);
    std::pair<std::string, std::string> getCredentials();
    void printInfo();
    bool isPTZSuported();
    void addUser(std::shared_ptr<UserInfo> user);
    void removeUser(std::string username);
    bool checkUser(std::string username);
    std::string getUsersString();
    void addUsersFromString(std::string users);
    std::shared_ptr<ClientSession>& getClientSession();
    Json::Value getStreamsJson(bool isStreamerDevice = false);
};

}
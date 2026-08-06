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

#include "sensor_info.h"
#include "device_manager.h"
#include <iostream>
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <unistd.h>
#include <iostream>
#include <libxml/encoding.h>
#include <libxml/xmlwriter.h>

inline constexpr const char* ONVIF_MEDIA_SERVICE = "Media";
inline constexpr const char* ONVIF_MEDIA2_SERVICE = "Media2";
inline constexpr const char* ONVIF_PTZ_SERVICE = "PTZ";
inline constexpr const char* ONVIF_IMAGING_SERVICE = "Imaging";
inline constexpr const char* ONVIF_REPLAY_SERVICE = "Replay";
inline constexpr const char* ONVIF_SEARCH_SERVICE = "Search";
inline constexpr const char* ONVIF_PROBE_MATCH_NAME_PREFIX = "onvif://www.onvif.org/name/";
inline constexpr const char* ONVIF_PROBE_MATCH_NAME_PREFIX2 = "odm:name:";
inline constexpr const char* ONVIF_PROBE_MATCH_HARDWARE_PREFIX = "onvif://www.onvif.org/hardware/";
inline constexpr const char* ONVIF_PROBE_MATCH_LOCATION_PREFIX = "onvif://www.onvif.org/location/";
inline constexpr const char* ONVIF_PROBE_MATCH_TYPE_PREFIX = "onvif://www.onvif.org/type/";
xmlNodePtr findNode (xmlDocPtr doc, xmlNodePtr cur, const char *inKey);
std::string getNodeValue (xmlDocPtr doc, xmlNodePtr cur);
std::string parseattributes(const std::string& str, std::string prefix);
std::string parseLocation(const std::string& str);
namespace nv_vms
{

enum PTZSpaceTypes
{
    AbsolutePanTiltPositionSpace = 0,
    AbsoluteZoomPositionSpace,
    RelativePanTiltTranslationSpace,
    RelativeZoomTranslationSpace,
    ContinuousPanTiltVelocitySpace,
    ContinuousZoomVelocitySpace,
    PanTiltSpeedSpace,
    ZoomSpeedSpace,
    UnknowSapace = 0xFFF
};

struct PTZSpaces
{
    PTZSpaceTypes spaceType;
    std::string x_min_range;
    std::string x_max_range;
    std::string y_min_range;
    std::string y_max_range;

    PTZSpaces():spaceType (UnknowSapace)
               ,x_min_range("0")
               ,x_max_range("0")
               ,y_min_range("0")
               ,y_max_range("0")
               {}

    PTZSpaces(const PTZSpaces& p)
    {
        spaceType = p.spaceType;
        x_min_range = p.x_min_range;
        x_max_range = p.x_max_range;
        y_min_range = p.y_min_range;
        y_max_range = p.y_max_range;
    }
    void printInfo()
    {
        std::cout << "\tPTZ spaceType: "<< spaceType << std::endl;
        std::cout << "\tPTZ x_min_range: "<< x_min_range << std::endl;
        std::cout << "\tPTZ x_max_range: "<< x_max_range << std::endl;
        std::cout << "\tPTZ y_min_range: "<< y_min_range << std::endl;
        std::cout << "\tPTZ y_max_range: "<< y_max_range << std::endl;
        std::cout << "" << std::endl;
    }
};

struct Profile
{
    Profile(): token("")
              ,name("")
              ,encoderToken("")
              ,sourceToken("")
              ,ptzToken("")
              ,ptzNodeToken("")
              ,resolution("")
              ,encoding("")
              ,encodingProfile("")
              ,frameRate("")
              ,gov("")
    {
    }
    Profile(const Profile& p)
    {
        token = p.token;
        name = p.name;
        encoderToken = p.encoderToken;
        sourceToken = p.sourceToken;
        ptzToken = p.ptzToken;
        ptzNodeToken = p.ptzNodeToken;
        resolution = p.resolution;
        encoding = p.encoding;
        encodingProfile = p.encodingProfile;
        frameRate = p.frameRate;
        gov = p.gov;
    }
    std::string token;
    std::string name;
    std::string encoderToken;
    std::string sourceToken;
    std::string ptzToken;
    std::string ptzNodeToken;
    std::string resolution;
    std::string encoding;
    std::string encodingProfile;
    std::string frameRate;
    std::string gov;
};

struct DeviceTimeInfo
{
    bool enableNTP;
    bool dayLightSavings;
    // Hour, Min, Second
    std::tuple<std::string, std::string, std::string> utcTime;
    // Year, Month, day
    std::tuple<std::string, std::string, std::string> date;
};

struct DeviceNTPInfo
{
    bool fromDHCP;
    std::string type;
    std::string ipv4Addr;
    std::string ipv6Addr;
    std::string dnsName;
};

struct RecordingTrack
{
    std::string trackToken;
    std::string trackType;      // Video, Audio, Metadata
    std::string description;
    std::string dataFrom;       // ISO 8601 format
    std::string dataTo;         // ISO 8601 format
    
    RecordingTrack() 
        : trackToken("")
        , trackType("")
        , description("")
        , dataFrom("")
        , dataTo("")
    {}
};

struct RecordingSourceInfo
{
    std::string sourceId;
    std::string name;
    std::string location;
    std::string description;
    std::string address;
    
    RecordingSourceInfo()
        : sourceId("")
        , name("")
        , location("")
        , description("")
        , address("")
    {}
};

struct RecordingInformation
{
    std::string recordingToken;
    RecordingSourceInfo source;
    std::string earliestRecording; // ISO 8601 format
    std::string latestRecording;   // ISO 8601 format
    std::string content;
    std::vector<RecordingTrack> tracks;
    std::string recordingStatus;   // Recording, Stopped, etc.
    
    RecordingInformation()
        : recordingToken("")
        , earliestRecording("")
        , latestRecording("")
        , content("")
        , recordingStatus("")
    {}
};

struct RecordingSummary
{
    std::string dataFrom;        // ISO 8601 format
    std::string dataUntil;       // ISO 8601 format
    int numberRecordings;
    
    RecordingSummary()
        : dataFrom("")
        , dataUntil("")
        , numberRecordings(0)
    {}
};

struct RecordingSearchScope
{
    std::vector<std::string> includedSources;  // Source tokens to include in search
    std::string recordingInformationFilter; // XPath filter
    
    RecordingSearchScope()
        : recordingInformationFilter("")
    {}
};

struct RecordingSearchResults
{
    std::string searchState;     // Queued, Searching, Completed, Unknown
    std::vector<RecordingInformation> recordingList;
    
    RecordingSearchResults()
        : searchState("")
    {}
};

struct nvsoap_
{
    nvsoap_(): url("")
            , device_url("")
            , method("")
            , wsdl("")
            , wsdl2("")
            , tokenName("")
            , user("")
            , password("")
            , token("")
            , curl(nullptr)
            , authMethod(AUTH_METHOD_USERNAME_TOKEN)
            , status(0)
            , xmlData("")
            , userData2(nullptr)
            , timeout(-1)
            , jsonData("")
    {
        userData.clear();
    }
    std::string name_space;
    std::string url;
    std::string device_url;
    std::string method;
    std::string wsdl;
    std::string wsdl2;
    std::string tokenName;
    std::string user;
    std::string password;
    std::string token;
    CURL*  curl;
    AuthenticationMethods authMethod;
    int status;
    std::string xmlData;
    std::map<std::string, std::string> userData;
    void* userData2;
    int timeout;
    std::string jsonData;
};


class NvSoap
{
public:
    NvSoap() : m_httpErrorCode(-1)\
             , m_httpErrorString("")
             , m_membership(false)
    {
    }
    ~NvSoap() {}
    bool ping(SensorInfo& sensor);
    int sendProbeToDevice(SensorInfo& sensor, bool ping = false);
    int GetSystemDateAndTime(nvsoap_& soap, std::string& res);
    int GetNTP(nvsoap_& soap, std::string& res);
    int rebootDevice(nvsoap_& soap);
    int GetScopes(nvsoap_& soap, std::vector<std::string>& uris);
    int GetDiscoveryMode(nvsoap_& soap, std::string& discovery_mode);
    int GetDeviceInformation(nvsoap_& soap, std::map<std::string, std::string>& device_info);
    int GetCapabilities(nvsoap_& soap, std::map<std::string, OnvifServiceInfo>& caps);
    int GetProfile(nvsoap_& soap, SensorSettings& settings);
    int GetProfiles(nvsoap_& soap, std::vector<SensorSettings>& settings);
    int GetPTZProfiles(nvsoap_& soap, std::vector<Profile>& profiles);
    int GetMediaUri(nvsoap_& soap, std::string&);
    int GetReplayUri(nvsoap_& soap, std::string&);
    int GetConfiguration(nvsoap_& soap, std::string&);
    int GetPTZNode(nvsoap_& soap, std::vector<PTZSpaces>&);
    int ContinuousMove(nvsoap_& soap, PTZAction, std::string x, std::string y);
    int Stop(nvsoap_& soap, std::string oprtation);
    bool getProbeResponse(const std::string& xmlData, SensorInfo& sensor);
    int getDeviceImageSettings(nvsoap_& soap, SensorImageSettingsValues& settings);
    int getCameraImageOptions(nvsoap_& soap, SensorImageSettingsOptions& options);
    int setDeviceImageSettings(nvsoap_& soap, const SensorImageSettingsValues& settings);
    int setSystemDateAndTime(nvsoap_& soap, const DeviceTimeInfo& settings);
    int setNTP(nvsoap_& soap, const DeviceNTPInfo& ntpInfo);
    int getNetworkInterfaces(nvsoap_& soap, SensorNetworkInfo& networkInfo);
    int setNetworkInterfaces(nvsoap_& soap, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
    int getCameraEncoderOptions(nvsoap_& soap, SensorEncoderSettingsOptions& options);
    int getCameraEncoderConfiguration(nvsoap_& soap, SensorVideoEncoderSettingsValues& values);
    int setCameraEncoderSettings(nvsoap_& soap, const SensorVideoEncoderSettingsValues & settings);
    void getCameraPostionsValues(nvsoap_& soap, SensorPosition& position);
    int createAndSendRequest(nvsoap_& soap, std::string& outData);
    int GetServices(nvsoap_& soap, std::map<std::string, OnvifServiceInfo>& caps);
    int GetServiceCapabilities(nvsoap_& soap, ServiceCapabilities& serviceCapabilities);
    int setHashingAlgorithm(nvsoap_& soap, const HashingAlgorithmInfo& algorithm);
    std::pair<int, std::string> getHttpErrorCode() {
            std::lock_guard<std::mutex> req_lock(m_reqMutex);
            int code;
            std::string errorString;
            code = m_httpErrorCode;
            errorString = m_httpErrorString;
            m_httpErrorCode = 200;
            m_httpErrorString = "No Error";
            return std::make_pair(code, errorString);
    }
    int sendProbe(const std::string& ip = "");
    int getProbeMatch(SensorInfo& sensor);
    int synchronizeDeviceTime(nvsoap_& soap);
    int openProbe();
    void closeProbe()
    {
        for (auto pt : m_probePort)
        {
            close(pt);
        }
        m_probePort.clear();
    }
    int stopOnvifListenerThread();
    
    // Profile G - Recording Search APIs
    int GetRecordingSummary(nvsoap_& soap, RecordingSummary& summary);
    int FindRecordings(nvsoap_& soap, const RecordingSearchScope& scope, 
                      int maxMatches, const std::string& keepAliveTime, std::string& searchToken);
    int GetRecordingSearchResults(nvsoap_& soap, const std::string& searchToken,
                                  int minResults, int maxResults, 
                                  const std::string& waitTime, RecordingSearchResults& results);
    int EndSearch(nvsoap_& soap, const std::string& searchToken);
private:
    void getDeviceInformationResponse(const std::string& xmlData, std::map<std::string, std::string>& info);
    void getSystemDateAndTimeResponse(const std::string& xmlData, std::string& response);
    void getNTPResponse(const std::string& xmlData, std::string& response);
    int  getCapabilitiesResponse(const std::string& xmlData, std::map<std::string, OnvifServiceInfo>& caps);
    void getProfileResponse(const std::string& xmlData, SensorSettings& settings, const std::string nameSpace);
    void getProfilesResponse(const std::string& xmlData, std::vector<SensorSettings>& settings, const std::string nameSpace);
    void getPTZProfilesResponse(const std::string& xmlData, std::vector<Profile>& profiles);
    std::string getUriResponse(const std::string& xmlData);
    std::vector<PTZSpaces> getPTZNodeResponse(const std::string& xmlData);
    SensorImageSettingsValues getCameraGetImageSettingsResponse(const std::string& xmlData);
    SensorImageSettingsOptions getCameraGetImageOptionResponse(const std::string& xmlData);
    SensorNetworkInfo getCameraNetworkInterfacesResponse(const std::string& xmlData);
    bool setCameraNetworkInterfacesResponse(const std::string& xmlData);
    std::string rebootCameraResponse(const std::string& xmlData);
    std::string composeXml(nvsoap_& soap, void* methodxml);
    std::string composeXmlWithoutUsertoken(nvsoap_& soap, void* methodxml);
    std::string composeProbeXml();
    int sendProbe(std::map<std::string, SensorInfo>& deviceList);
    int receiveProbeMatch(std::string& outData);
    void getServicesResponse(const std::string& xmlData, std::map<std::string, OnvifServiceInfo>& caps);
    SensorEncoderSettingsOptions getVideoEncoderConfigurationOptionsMediaResponse(const std::string& xmlData);
    SensorEncoderSettingsOptions getVideoEncoderConfigurationOptionsMedia2Response(const std::string& xmlData);
    SensorVideoEncoderSettingsValues getVideoEncoderConfigurationMediaResponse(const std::string& xmlData);
    SensorVideoEncoderSettingsValues getVideoEncoderConfigurationsMedia2Response(const std::string& xmlData);
    ServiceCapabilities getServiceCapabilitiesResponse(const std::string& xmlData);
    int addUserToken(nvsoap_& soap, xmlTextWriterPtr& writer);
    
    // Profile G - Response parsing methods
    RecordingSummary getRecordingSummaryResponse(const std::string& xmlData);
    std::string getFindRecordingsResponse(const std::string& xmlData);
    RecordingSearchResults getRecordingSearchResultsResponse(const std::string& xmlData);

    int m_httpErrorCode;
    std::string m_httpErrorString;
    std::vector<int> m_probePort;
    bool m_membership;
    int fdCtrl[2];
    std::mutex m_reqMutex;
};


} //nv_vms
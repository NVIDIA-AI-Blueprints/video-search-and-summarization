/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

using namespace std;

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
string getNodeValue (xmlDocPtr doc, xmlNodePtr cur);
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
    string x_min_range;
    string x_max_range;
    string y_min_range;
    string y_max_range;

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
        cout << "\tPTZ spaceType: "<< spaceType << endl;
        cout << "\tPTZ x_min_range: "<< x_min_range << endl;
        cout << "\tPTZ x_max_range: "<< x_max_range << endl;
        cout << "\tPTZ y_min_range: "<< y_min_range << endl;
        cout << "\tPTZ y_max_range: "<< y_max_range << endl;
        cout << "" << endl;
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
    string token;
    string name;
    string encoderToken;
    string sourceToken;
    string ptzToken;
    string ptzNodeToken;
    string resolution;
    string encoding;
    string encodingProfile;
    string frameRate;
    string gov;
};

struct DeviceTimeInfo
{
    bool enableNTP;
    bool dayLightSavings;
    // Hour, Min, Second
    std::tuple<string, string, string> utcTime;
    // Year, Month, day
    std::tuple<string, string, string> date;
};

struct DeviceNTPInfo
{
    bool fromDHCP;
    string type;
    string ipv4Addr;
    string ipv6Addr;
    string dnsName;
};

struct RecordingTrack
{
    string trackToken;
    string trackType;      // Video, Audio, Metadata
    string description;
    string dataFrom;       // ISO 8601 format
    string dataTo;         // ISO 8601 format
    
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
    string sourceId;
    string name;
    string location;
    string description;
    string address;
    
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
    string recordingToken;
    RecordingSourceInfo source;
    string earliestRecording; // ISO 8601 format
    string latestRecording;   // ISO 8601 format
    string content;
    vector<RecordingTrack> tracks;
    string recordingStatus;   // Recording, Stopped, etc.
    
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
    string dataFrom;        // ISO 8601 format
    string dataUntil;       // ISO 8601 format
    int numberRecordings;
    
    RecordingSummary()
        : dataFrom("")
        , dataUntil("")
        , numberRecordings(0)
    {}
};

struct RecordingSearchScope
{
    vector<string> includedSources;  // Source tokens to include in search
    string recordingInformationFilter; // XPath filter
    
    RecordingSearchScope()
        : recordingInformationFilter("")
    {}
};

struct RecordingSearchResults
{
    string searchState;     // Queued, Searching, Completed, Unknown
    vector<RecordingInformation> recordingList;
    
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
    string name_space;
    string url;
    string device_url;
    string method;
    string wsdl;
    string wsdl2;
    string tokenName;
    string user;
    string password;
    string token;
    CURL*  curl;
    AuthenticationMethods authMethod;
    int status;
    string xmlData;
    map<string, string> userData;
    void* userData2;
    int timeout;
    string jsonData;
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
    int GetSystemDateAndTime(nvsoap_& soap, string& res);
    int GetNTP(nvsoap_& soap, string& res);
    int rebootDevice(nvsoap_& soap);
    int GetScopes(nvsoap_& soap, vector<string>& uris);
    int GetDiscoveryMode(nvsoap_& soap, string& discovery_mode);
    int GetDeviceInformation(nvsoap_& soap, map<string, string>& device_info);
    int GetCapabilities(nvsoap_& soap, map<string, OnvifServiceInfo>& caps);
    int GetProfile(nvsoap_& soap, SensorSettings& settings);
    int GetProfiles(nvsoap_& soap, vector<SensorSettings>& settings);
    int GetPTZProfiles(nvsoap_& soap, vector<Profile>& profiles);
    int GetMediaUri(nvsoap_& soap, string&);
    int GetReplayUri(nvsoap_& soap, string&);
    int GetConfiguration(nvsoap_& soap, string&);
    int GetPTZNode(nvsoap_& soap, vector<PTZSpaces>&);
    int ContinuousMove(nvsoap_& soap, PTZAction, string x, string y);
    int Stop(nvsoap_& soap, string oprtation);
    bool getProbeResponse(const string& xmlData, SensorInfo& sensor);
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
    int createAndSendRequest(nvsoap_& soap, string& outData);
    int GetServices(nvsoap_& soap, map<string, OnvifServiceInfo>& caps);
    int GetServiceCapabilities(nvsoap_& soap, ServiceCapabilities& serviceCapabilities);
    int setHashingAlgorithm(nvsoap_& soap, const HashingAlgorithmInfo& algorithm);
    std::pair<int, string> getHttpErrorCode() {
            std::lock_guard<std::mutex> req_lock(m_reqMutex);
            int code;
            string errorString;
            code = m_httpErrorCode;
            errorString = m_httpErrorString;
            m_httpErrorCode = 200;
            m_httpErrorString = "No Error";
            return std::make_pair(code, errorString);
    }
    int sendProbe(const string& ip = "");
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
                      int maxMatches, const string& keepAliveTime, string& searchToken);
    int GetRecordingSearchResults(nvsoap_& soap, const string& searchToken,
                                  int minResults, int maxResults, 
                                  const string& waitTime, RecordingSearchResults& results);
    int EndSearch(nvsoap_& soap, const string& searchToken);
private:
    void getDeviceInformationResponse(const string& xmlData, map<string, string>& info);
    void getSystemDateAndTimeResponse(const string& xmlData, string& response);
    void getNTPResponse(const string& xmlData, string& response);
    int  getCapabilitiesResponse(const string& xmlData, map<string, OnvifServiceInfo>& caps);
    void getProfileResponse(const string& xmlData, SensorSettings& settings, const string nameSpace);
    void getProfilesResponse(const string& xmlData, vector<SensorSettings>& settings, const string nameSpace);
    void getPTZProfilesResponse(const string& xmlData, vector<Profile>& profiles);
    string getUriResponse(const string& xmlData);
    vector<PTZSpaces> getPTZNodeResponse(const string& xmlData);
    SensorImageSettingsValues getCameraGetImageSettingsResponse(const string& xmlData);
    SensorImageSettingsOptions getCameraGetImageOptionResponse(const string& xmlData);
    SensorNetworkInfo getCameraNetworkInterfacesResponse(const string& xmlData);
    bool setCameraNetworkInterfacesResponse(const string& xmlData);
    string rebootCameraResponse(const string& xmlData);
    string composeXml(nvsoap_& soap, void* methodxml);
    string composeXmlWithoutUsertoken(nvsoap_& soap, void* methodxml);
    string composeProbeXml();
    int sendProbe(map<string, SensorInfo>& deviceList);
    int receiveProbeMatch(string& outData);
    void getServicesResponse(const string& xmlData, map<string, OnvifServiceInfo>& caps);
    SensorEncoderSettingsOptions getVideoEncoderConfigurationOptionsMediaResponse(const string& xmlData);
    SensorEncoderSettingsOptions getVideoEncoderConfigurationOptionsMedia2Response(const string& xmlData);
    SensorVideoEncoderSettingsValues getVideoEncoderConfigurationMediaResponse(const string& xmlData);
    SensorVideoEncoderSettingsValues getVideoEncoderConfigurationsMedia2Response(const string& xmlData);
    ServiceCapabilities getServiceCapabilitiesResponse(const string& xmlData);
    int addUserToken(nvsoap_& soap, xmlTextWriterPtr& writer);
    
    // Profile G - Response parsing methods
    RecordingSummary getRecordingSummaryResponse(const string& xmlData);
    string getFindRecordingsResponse(const string& xmlData);
    RecordingSearchResults getRecordingSearchResultsResponse(const string& xmlData);

    int m_httpErrorCode;
    string m_httpErrorString;
    vector<int> m_probePort;
    bool m_membership;
    int fdCtrl[2];
    std::mutex m_reqMutex;
};


} //nv_vms
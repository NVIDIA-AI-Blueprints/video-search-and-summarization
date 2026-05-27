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

#include "nvsoap.h"
#include "config.h"
#include "utils.h"
#include "logger.h"
#include "macros.h"
#include <openssl/sha.h>
#include <ctime>
#include <curl/curl.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <net/if.h>
#include <sys/time.h>
#include <sys/ioctl.h>
#include <algorithm>
#include <regex>

using namespace std;
using namespace nv_vms;

constexpr int PROBE_PORT = 3702;
constexpr const char* PROBE_IP = "239.255.255.250";
constexpr int PING_TIMEOUT = 5;
constexpr const char* ENCODING = "utf-8";
#define PRINT_XML_REQUEST_IF_ERROR(ret, xml)    if (ret != 0) { LOG(info) << "XML : request: " << xml << endl; }
constexpr const char* EXIT_MESSAGE = "exit_listener_thread";
constexpr const char* NTP_DEFAULT_SERVER = "time.google.com";
constexpr int DEVICE_AND_CLIENT_TIME_DIFFERENCE_SEC = 1;
constexpr const char* ONVIF_DEVICE_SERVICE_NAMESPACE = "http://www.onvif.org/ver10/device/wsdl";
constexpr const char* ONVIF_MEDIA2_SERVICE_NAMESPACE = "http://www.onvif.org/ver20/media/wsdl";
constexpr const char* ONVIF_MEDIA_SERVICE_NAMESPACE = "http://www.onvif.org/ver10/media/wsdl";
constexpr const char* ONVIF_PTZ_SERVICE_NAMESPACE = "http://www.onvif.org/ver20/ptz/wsdl";
constexpr const char* ONVIF_IMAGING_SERVICE_NAMESPACE = "http://www.onvif.org/ver20/imaging/wsdl";
constexpr const char* ONVIF_REPLAY_SERVICE_NAMESPACE = "http://www.onvif.org/ver10/replay/wsdl";
constexpr const char* ONVIF_SEARCH_SERVICE_NAMESPACE = "http://www.onvif.org/ver10/search/wsdl";

constexpr int REQUEST_RETRY = 1;

typedef unsigned char BYTE;

std::mutex g_probeMutex;
std::mutex g_probeMatchMutex;

typedef int (*composeMethodXml) (xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetUriXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetCapabilitiesMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetConfigurationMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetNodeMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composePTZMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composePTZStopMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetCameraImageSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetNetworkInterfacesXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetImageOptionsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetEncoderOptionsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeSetNetworkInterfacesXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeSetSystemDateAndTimeInfoXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeSetNTPInfoXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeSetCameraImageSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeSetEncoderSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static string composeCameraConfigurationAPIXML(const string& camera_id);
static int createAndSendCameraConfigurationAPIRequest(const string& url, const string& username,
                                                      const string& password, const string& inData, string& outData);
static void getCameraPositionResult(const string& xmlData, SensorPosition& position);
static int composeRebootCameraXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetServicesMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetProfileXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetRecordingSummaryXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeFindRecordingsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeGetRecordingSearchResultsXml(xmlTextWriterPtr& writer, nvsoap_& soap);
static int composeEndSearchXml(xmlTextWriterPtr& writer, nvsoap_& soap);

template <class Type>
bool isDuplicateEntry(vector<Type> v, Type& entry)
{
    auto it = find_if(v.begin(), v.end(), [&entry](Type& obj) { return obj == entry; } );
    return (it != v.end());
}

namespace
{
    size_t callback(
            const char* in,
            size_t size,
            size_t num,
            string* out)
    {
        const size_t totalBytes(size * num);
        out->append(in, totalBytes);
        return totalBytes;
    }
}

class AutoDestroyXml
{
public:
    AutoDestroyXml(xmlBufferPtr xml) :m_xml(xml) {}
    ~AutoDestroyXml() { xmlBufferFree(m_xml); }
private:
    xmlBufferPtr m_xml;
};

#ifdef DEBUG
static
int my_trace(CURL *handle, curl_infotype type,
             char *data, size_t size,
             void *userp)
{
    LOG(verbose) << string(data) << endl;
    return 0;
}
#endif

struct curlData {
  char trace_ascii; /* 1 or 0 */
};

int NvSoap::GetSystemDateAndTime(nvsoap_& soap, string& res)
{
    int ret = -1;
    if (soap.url.empty() == true)
    {
        return ret;
    }
    string out;
    soap.method = "GetSystemDateAndTime";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXmlWithoutUsertoken(soap, (void*)&composeGetMethodXml);
    ret = createAndSendRequest(soap, out);
    if (ret == 0)
    {
        getSystemDateAndTimeResponse(out, res);
    }
    return ret;
}

int NvSoap::GetNTP(nvsoap_& soap, string& res)
{
    int ret = -1;
    string out;
    soap.method = "GetNTP";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    ret = createAndSendRequest(soap, out);
    if (ret == 0)
    {
        getNTPResponse(out, res);
    }
    return ret;
}

int NvSoap::GetDeviceInformation(nvsoap_& soap, map<string, string>& device_info)
{
    int ret = -1;
    string out;
    soap.method = "GetDeviceInformation";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    ret = createAndSendRequest(soap, out);
    if (ret == 0 )
    {
        getDeviceInformationResponse(out, device_info);
        for(auto it: device_info)
        {
            LOG(verbose) << it.first << ": " << it.second << endl;
        }
    }
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    return ret;
}

int NvSoap::GetScopes(nvsoap_& soap, vector<string>& uris)
{
    int ret = -1;
    string out;
    soap.method = "GetScopes";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    LOG(verbose2) << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << out << endl;
    if (ret == 0 )
    {
        //getDeviceInformationResponse(out, host_name);
    }
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    return ret;
}

int NvSoap::GetDiscoveryMode(nvsoap_& soap, string& discovery_mode)
{
    int ret = -1;
    string out;
    soap.method = "GetDiscoveryMode";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    LOG(verbose2) << soap.xmlData << endl;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << out << endl;
    if (ret == 0 )
    {
        //getDeviceInformationResponse(out, host_name);
    }
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    return ret;
}

int NvSoap::GetCapabilities(nvsoap_& soap, map<string, OnvifServiceInfo>& caps)
{
    string out;
    soap.method = "GetCapabilities";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetCapabilitiesMethodXml);
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    ret = getCapabilitiesResponse(out, caps);
    return ret;
}

int NvSoap::GetProfile(nvsoap_& soap, SensorSettings& settings)
{
    string out;
    soap.wsdl = soap.name_space;
    if (soap.wsdl == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        soap.method = "GetProfile";
    }
    else if (soap.wsdl == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        soap.method = "GetProfiles";
    }
    else
    {
        LOG(error) << "Invalid namespace: " << soap.wsdl << endl;
        return -1;
    }
    soap.xmlData = composeXml(soap, (void*)&composeGetProfileXml);

    if (createAndSendRequest(soap, out) == 0)
    {
        LOG(verbose2) << "GetProfile/GetProfiles Result: " << out << endl;
        getProfileResponse(out, settings, soap.name_space);
        return 0;
    }

    return -1;
}

int NvSoap::GetProfiles(nvsoap_& soap, vector<SensorSettings>& settings)
{
    int ret = -1;
    string out;
    soap.method = "GetProfiles";
    soap.wsdl = soap.name_space;
    if (soap.wsdl == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    }
    else if (soap.wsdl == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        soap.xmlData = composeXml(soap, (void*)&composeGetProfileXml);
    }
    else
    {
        return ret;
    }
    ret = createAndSendRequest(soap, out);
    if (ret == 0)
    {
        LOG(verbose2) << "GetProfiles Result: " << out << endl;
        getProfilesResponse(out, settings, soap.name_space);
        return 0;
    }
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    return ret;
}

int NvSoap::GetPTZProfiles(nvsoap_& soap, vector<Profile>& profiles)
{
    string out;
    soap.method = "GetProfiles";
    soap.wsdl = ONVIF_MEDIA_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    getPTZProfilesResponse(out, profiles);
    return ret;
}

int NvSoap::GetMediaUri(nvsoap_& soap, string& uri)
{
    string out;
    soap.method = "GetStreamUri";
    soap.wsdl = soap.name_space;
    soap.tokenName = "ProfileToken";
    soap.xmlData = composeXml(soap, (void*)&composeGetUriXml);
    LOG(verbose2) << "GetMediaUri request: " << soap.xmlData << endl;
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    LOG(verbose2) << "GetMediaUri Result: " << out << endl;
    uri = getUriResponse(out);
    LOG(verbose) << "Media URI: " << uri << endl;
    return ret;
}

int NvSoap::GetReplayUri(nvsoap_& soap, string& uri)
{
    string out;
    soap.method = "GetReplayUri";
    soap.wsdl = ONVIF_REPLAY_SERVICE_NAMESPACE;
    soap.tokenName = "RecordingToken";
    soap.xmlData = composeXml(soap, (void*)&composeGetUriXml);
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    LOG(verbose2) << "GetReplayUri Result: " << out << endl;
    uri = getUriResponse(out);
    LOG(verbose) << "Replay URI: " << uri << endl;
    return ret;
}

int NvSoap::GetConfiguration(nvsoap_& soap, string& out)
{
    soap.method = "GetConfiguration";
    soap.wsdl = ONVIF_PTZ_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetConfigurationMethodXml);
    LOG(verbose2) << "GetConfiguration: " << soap.xmlData << endl;
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    LOG(verbose2) << "GetConfiguration: " << out << endl;
    return ret;
}

int NvSoap::GetPTZNode(nvsoap_& soap, vector<PTZSpaces>& spaces)
{
    string out;
    soap.method = "GetNode";
    soap.wsdl = ONVIF_PTZ_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetNodeMethodXml);
    LOG(verbose2) << "GetPTZNode: " << soap.xmlData << endl;
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    spaces = getPTZNodeResponse(out);
    return ret;
}

int NvSoap::ContinuousMove(nvsoap_& soap, PTZAction ptz, string x, string y)
{
    int ret = -1;
    string out;
    soap.method = "ContinuousMove";
    soap.wsdl = "http://www.onvif.org/ver10/schema";
    soap.wsdl2 = ONVIF_PTZ_SERVICE_NAMESPACE;
    soap.userData["x"] = x;
    soap.userData["y"] = y;
    soap.xmlData = composeXml(soap, (void*)&composePTZMethodXml);
    LOG(verbose2) << "ContinuousMove: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    if(ret != 0)
    {
      return ret;
    }
    soap.method = "Stop";
    soap.xmlData = composeXml(soap, (void*)&composePTZStopMethodXml);
    soap.userData["PanTilt"] = ptz == PTZAction::PanTilt ? "true" : "false";
    soap.userData["Zoom"] = ptz == PTZAction::Zoom ? "true" : "false";
    LOG(verbose2) << "Stop: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    return ret;
}

int NvSoap::Stop(nvsoap_& soap, string oprtation)
{
    int ret = -1;
    string out;
    soap.method = "Stop";
    soap.wsdl = "http://www.onvif.org/ver10/schema";
    soap.wsdl2 = ONVIF_PTZ_SERVICE_NAMESPACE;
    soap.userData["PanTilt"] = oprtation == "PanTilt" ? "true" : "false";
    soap.userData["Zoom"] = oprtation == "Zoom" ? "true" : "false";
    soap.xmlData = composeXml(soap, (void*)&composePTZStopMethodXml);
    LOG(verbose2) << "ContinuousMove: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << out << endl;
    return ret;
}

int NvSoap::getDeviceImageSettings(nvsoap_& soap, SensorImageSettingsValues& settings)
{
    string out;
    int ret = -1;
    soap.method = "GetImagingSettings";
    soap.wsdl = ONVIF_IMAGING_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetCameraImageSettingsXml);
    LOG(verbose2) << "GetImagingSettings: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    if (ret != 0)
    {
        LOG(verbose2) << "XML : request: " << soap.xmlData << endl;
    }
    LOG(verbose2) << "GetImagingSettings: " << out << endl;
    settings = getCameraGetImageSettingsResponse(out);
    return ret;
}

int NvSoap::getCameraImageOptions(nvsoap_& soap, SensorImageSettingsOptions& options)
{
    string out;
    int ret = -1;
    soap.method = "GetOptions";
    soap.wsdl = ONVIF_IMAGING_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetImageOptionsXml);
    LOG(verbose2) << "GetOptions: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "GetOptions: " << out << endl;
    options = getCameraGetImageOptionResponse(out);
    return ret;
}

int NvSoap::setDeviceImageSettings(nvsoap_& soap, const SensorImageSettingsValues& settings)
{
    string out;
    int ret = -1;
    soap.method = "SetImagingSettings";
    soap.wsdl = ONVIF_IMAGING_SERVICE_NAMESPACE;
    soap.userData2 = (void* )&settings;
    soap.xmlData = composeXml(soap, (void*)&composeSetCameraImageSettingsXml);
    LOG(verbose2) << "SetImagingSettings: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "SetImagingSettings: " << out << endl;
    return ret;
}

int NvSoap::setSystemDateAndTime(nvsoap_& soap, const DeviceTimeInfo& timeInfo)
{
    string out;
    int ret = -1;
    soap.method = "SetSystemDateAndTime";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.userData2 = (void* )&timeInfo;
    soap.xmlData = composeXml(soap, (void*)&composeSetSystemDateAndTimeInfoXml);
    LOG(verbose2) << "SetSystemDateAndTime: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "SetSystemDateAndTime out: " << out << endl;
    return ret;
}

int NvSoap::setNTP(nvsoap_& soap, const DeviceNTPInfo& ntpInfo)
{
    string out;
    int ret = -1;
    soap.method = "SetNTP";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.userData2 = (void* )&ntpInfo;
    soap.xmlData = composeXml(soap, (void*)&composeSetNTPInfoXml);
    LOG(verbose2) << "composeSetNTPInfoXml: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "composeSetNTPInfoXml out: " << out << endl;
    return ret;
}

int NvSoap::getNetworkInterfaces(nvsoap_& soap, SensorNetworkInfo& networkInfo)
{
    string out;
    int ret = -1;
    soap.method = "GetNetworkInterfaces";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetNetworkInterfacesXml);
    LOG(verbose2) << "xmData GetNetworkInterfaces: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "out GetNetworkInterfaces: " << out << endl;
    networkInfo = getCameraNetworkInterfacesResponse(out);
    LOG(verbose) << "========================================================================" << endl;
    LOG(verbose) << "url:" << soap.url << endl;
    LOG(verbose) << "token:" << networkInfo.token.first << " enabled:" << networkInfo.token.second << endl;
    LOG(verbose) << "InterfaceName:" << networkInfo.interfaceName << endl;
    LOG(verbose) << "enableDhcp4:" << networkInfo.enableDhcp4 << " Ipv4Addr:" << networkInfo.IPAddr4 << " prefixLen:" << networkInfo.prefixLen4 << endl;
    LOG(verbose) << "enableDhcp6:" << networkInfo.enableDhcp6 << " Ipv6Addr:" << networkInfo.IPAddr6 << " prefixLen:" << networkInfo.prefixLen6 << endl;
    LOG(verbose) << "========================================================================" << endl;

    return ret;
}

int NvSoap::setNetworkInterfaces(nvsoap_& soap, const SensorNetworkInfo& networkInfo, bool& rebootNeeded)
{
    string out;
    int ret = -1;
    soap.method = "SetNetworkInterfaces";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.userData2 = (void* )&networkInfo;
    soap.xmlData = composeXml(soap, (void*)&composeSetNetworkInterfacesXml);
    LOG(verbose2) << "xml SetNetworkInterfaces: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose2) << "out SetNetworkInterfaces: " << out << endl;
    rebootNeeded = setCameraNetworkInterfacesResponse(out);
    LOG(info) << "SetNetworkInterfaces => rebootNeeded:" << rebootNeeded << endl;
    return ret;
}

int NvSoap::getCameraEncoderOptions(nvsoap_& soap, SensorEncoderSettingsOptions& options)
{
    string out;
    int ret = -1;
    soap.method = "GetVideoEncoderConfigurationOptions";
    soap.wsdl = soap.name_space;
    soap.xmlData = composeXml(soap, (void*)&composeGetEncoderOptionsXml);
    LOG(verbose) << "GetVideoEncoderConfigurationOptions: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose) << "GetVideoEncoderConfigurationOptions: " << out << endl;
    if (soap.wsdl == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        options = getVideoEncoderConfigurationOptionsMediaResponse(out);
    }
    else if (soap.wsdl == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        options = getVideoEncoderConfigurationOptionsMedia2Response(out);
    }
    else
    {
        ret = -1;
    }
    return ret;
}

int NvSoap::getCameraEncoderConfiguration(nvsoap_& soap, SensorVideoEncoderSettingsValues& values)
{
    string out;
    int ret = -1;
    soap.wsdl = soap.name_space;
    if (soap.wsdl == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        soap.method = "GetVideoEncoderConfiguration";
    }
    else if (soap.wsdl == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        soap.method = "GetVideoEncoderConfigurations";
    }
    else
    {
        ret = -1;
        return ret;
    }
    soap.xmlData = composeXml(soap, (void*)&composeGetEncoderOptionsXml);

    LOG(verbose) << "GetVideoEncoderConfigurations: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose) << "GetVideoEncoderConfigurations: " << out << endl;

    if (soap.wsdl == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        values = getVideoEncoderConfigurationMediaResponse(out);
    }
    else if (soap.wsdl == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        values = getVideoEncoderConfigurationsMedia2Response(out);
    }
    return ret;
}

int NvSoap::setCameraEncoderSettings(nvsoap_& soap, const SensorVideoEncoderSettingsValues & settings)
{
    string out;
    int ret = -1;
    soap.method = "SetVideoEncoderConfiguration";
    soap.wsdl = soap.name_space;
    soap.userData2 = (void* )&settings;
    soap.xmlData = composeXml(soap, (void*)&composeSetEncoderSettingsXml);
    LOG(verbose) << "SetVideoEncoderConfiguration: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose) << "SetVideoEncoderConfiguration: " << out << endl;
    return ret;
}

void NvSoap::getCameraPostionsValues(nvsoap_& soap, SensorPosition& position)
{
    string config_xml = composeCameraConfigurationAPIXML(soap.token);
    const string & url = soap.url + string ("/ManagementServer/ConfigurationApiService.svc");
    string xmlData;
    if (createAndSendCameraConfigurationAPIRequest(url, soap.user, soap.password, config_xml, xmlData) == 0)
    {
        LOG(verbose2) << "Response: " << xmlData << endl;
        getCameraPositionResult(xmlData, position);
    }
}

int NvSoap::rebootDevice(nvsoap_& soap)
{
    string out;
    int ret = -1;
    soap.method = "SystemReboot";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeRebootCameraXml);
    LOG(info) << "xml SystemReboot: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(info) << "out SystemReboot: " << out << endl;
    string reboot_message = rebootCameraResponse(out);
    LOG(info) << "reboot_message:" << reboot_message << endl;
    return ret;
}


xmlNodePtr findNode (xmlDocPtr doc, xmlNodePtr cur, const char *inKey)
{
    xmlNodePtr outCur = nullptr;
    cur = cur->xmlChildrenNode;
    while (cur != nullptr)
    {
        if ((!xmlStrcmp(cur->name, (const xmlChar *)inKey)))
        {
            return cur;
        }
        else
        {
            outCur = findNode(doc, cur, inKey);
            if (outCur && (!xmlStrcmp(outCur->name, (const xmlChar *)inKey)))
            {
                break;
            }
        }
        cur = cur->next;
    }
    return outCur;
}

std::string parseattributes(const std::string& str, std::string prefix)
{
    // Check if the string starts with the expected prefix
    if (str.rfind(prefix, 0) != 0)
    {
        if (ONVIF_PROBE_MATCH_NAME_PREFIX == prefix)
        {
            prefix = ONVIF_PROBE_MATCH_NAME_PREFIX2;
        }
        else
        {
            return "";
        }
    }

    // Remove the prefix from the string
    std::string attributeData = str.substr(prefix.length());

    // Use std::istringstream to safely parse the location data
    std::istringstream stream(attributeData);
    std::string attribute;

    // Extract the first segment (mandatory)
    if (!std::getline(stream, attribute, '/'))
    {
        return "";
    }

    // Construct and return the result
    return attribute;
}

std::string parseLocation(const std::string& str)
{
    // Check if the string starts with the expected prefix
    std::string prefix = ONVIF_PROBE_MATCH_LOCATION_PREFIX;
    if (str.rfind(ONVIF_PROBE_MATCH_LOCATION_PREFIX, 0) != 0)
    {
        return "";
    }

    // Remove the prefix from the string
    std::string locationData = str.substr(prefix.length());

    // Use std::istringstream to safely parse the location data
    std::istringstream stream(locationData);
    std::string location1, location2;

    // Extract the first segment (mandatory)
    if (!std::getline(stream, location1, '/') || location1.empty())
    {
        return "";
    }

    // Extract the second segment (optional)
    if (!std::getline(stream, location2, '/'))
    {
        location2.clear(); // If no second segment exists, leave it empty
    }

    // Construct and return the result
    return location2.empty() ? location1 : (location1 + "-" + location2);
}

string getNodeValue (xmlDocPtr doc, xmlNodePtr cur)
{
    string outKey;
    xmlChar *key = xmlNodeListGetString(doc, cur->xmlChildrenNode, 1);
    if (key)
    {
        outKey = (char *)key;
        xmlFree(key);
    }
    return outKey;
}

bool NvSoap::getProbeResponse(const string& xmlData, SensorInfo& sensor)
{
    xmlDocPtr    doc;
    string value;
    bool matchFound = false;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return false;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "ProbeMatch");
        if (cur)
        {
            matchFound = true;
            xmlNodePtr cur_ = findNode(doc, cur, "XAddrs");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              vector<string> v = splitString(value, " ");
              if (v.size() > 0)
              {
                  sensor.url = v[0];
                  vector<string> str_arr = splitString(sensor.url, "/");
                  string ip = str_arr.size() > 2 ? str_arr[2] : "";
                  sensor.ip = ip;
              }
            }
            cur_ = findNode(doc, cur, "EndpointReference");
            if (cur_ && cur_->xmlChildrenNode)
            {
              value = getNodeValue(doc, cur_->xmlChildrenNode);
              int len = string("urn:uuid:").size();
              sensor.id = value.substr(value.find("urn:uuid:") + len);
            }
            cur_ = findNode(doc, cur, "Scopes");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              //cout << value << endl;
              vector<string> ss = splitString(value, " ");
              for(unsigned int i = 0; i < ss.size(); i++)
              {
                  string str = ss[i];
                  std::size_t found = str.find("name");
                  if (found != std::string::npos)
                  {
                      sensor.name = parseattributes(str, ONVIF_PROBE_MATCH_NAME_PREFIX);
                      continue;
                  }
                  found = str.find("type");
                  if (found != std::string::npos)
                  {
                      sensor.type = parseattributes(str, ONVIF_PROBE_MATCH_TYPE_PREFIX);
                      continue;
                  }
                  found = str.find("hardware");
                  if (found != std::string::npos)
                  {
                      sensor.hardware = parseattributes(str, ONVIF_PROBE_MATCH_HARDWARE_PREFIX);
                      continue;
                  }
                  found = str.find("location");
                  if (found != std::string::npos)
                  {
                      sensor.location = parseLocation(str);
                      continue;
                  }
              }
            }
        }
    }
    xmlFreeDoc(doc);
    return matchFound;
}

void NvSoap::getSystemDateAndTimeResponse(const string& xmlData, string& response)
{
    xmlDocPtr    doc;
    string value;
    string dateTimeType, daylightSavings;
    string hour, min, sec;
    string year, month, day;
    char timeString[256];

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if (cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "GetSystemDateAndTimeResponse");
        if (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "SystemDateAndTime");
            if (cur_)
            {
              xmlNodePtr cur__ = findNode(doc, cur_, "DateTimeType");
              if (cur__)
              {
                dateTimeType = getNodeValue(doc, cur__);
              }

              cur__ = findNode(doc, cur_, "DaylightSavings");
              if (cur__)
              {
                daylightSavings = getNodeValue(doc, cur__);
              }

              cur__ = findNode(doc, cur_, "UTCDateTime");
              if (cur__)
              {
                xmlNodePtr cur_time = findNode(doc, cur__, "Time");
                if (cur_time)
                {
                    xmlNodePtr cur_hour = findNode(doc, cur_time, "Hour");
                    if (cur_hour)
                    {
                        hour = getNodeValue(doc, cur_hour);
                    }
                    xmlNodePtr cur_min = findNode(doc, cur_time, "Minute");
                    if (cur_min)
                    {
                        min = getNodeValue(doc, cur_min);
                    }
                    xmlNodePtr cur_sec = findNode(doc, cur_time, "Second");
                    if (cur_sec)
                    {
                        sec = getNodeValue(doc, cur_sec);
                    }
                }

                xmlNodePtr cur_date = findNode(doc, cur__, "Date");
                if (cur_date)
                {
                    xmlNodePtr cur_year = findNode(doc, cur_date, "Year");
                    if (cur_year)
                    {
                        year = getNodeValue(doc, cur_year);
                    }
                    xmlNodePtr cur_month = findNode(doc, cur_date, "Month");
                    if (cur_month)
                    {
                        month = getNodeValue(doc, cur_month);
                    }
                    xmlNodePtr cur_day = findNode(doc, cur_date, "Day");
                    if (cur_day)
                    {
                        day = getNodeValue(doc, cur_day);
                    }
                }
              }
            }
        }
    }
    snprintf(timeString, sizeof(timeString), "%s-%s-%sT%s:%s:%sZ",
        year.c_str(), month.c_str(), day.c_str(), hour.c_str(), min.c_str(), sec.c_str());
    response = timeString;
    xmlFreeDoc(doc);
    return;
}

void NvSoap::getNTPResponse(const string& xmlData, string& response)
{
    xmlDocPtr    doc;
    string ntpserver_ipv4, ntpserver_name;
    string fromDhcp;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if (cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "GetNTPResponse");
        if (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "NTPInformation");
            if (cur_)
            {
                xmlNodePtr cur__ = findNode(doc, cur_, "FromDHCP");
                if (cur__)
                {
                    fromDhcp = getNodeValue(doc, cur__);
                    if (fromDhcp == "true")
                    {
                        cur__ = findNode(doc, cur_, "NTPFromDHCP");
                        if (cur__)
                        {
                            xmlNodePtr cur_type = findNode(doc, cur__, "Type");
                            if (cur_type)
                            {
                                string type = getNodeValue(doc, cur_type);
                            }
                            xmlNodePtr cur_ipv4 = findNode(doc, cur__, "IPv4Address");
                            if (cur_ipv4)
                            {
                                ntpserver_ipv4 = getNodeValue(doc, cur_ipv4);
                            }
                            xmlNodePtr cur_dnsName = findNode(doc, cur__, "DNSname");
                            if (cur_dnsName)
                            {
                                ntpserver_name = getNodeValue(doc, cur_dnsName);
                            }
                        }
                    }
                }

                cur__ = findNode(doc, cur_, "NTPManual");
                if (cur__)
                {
                    xmlNodePtr cur_type = findNode(doc, cur__, "Type");
                    if (cur_type)
                    {
                        string type = getNodeValue(doc, cur_type);
                    }
                    xmlNodePtr cur_ipv4 = findNode(doc, cur__, "IPv4Address");
                    if (cur_ipv4)
                    {
                        ntpserver_ipv4 = getNodeValue(doc, cur_ipv4);
                    }
                    xmlNodePtr cur_dnsName = findNode(doc, cur__, "DNSname");
                    if (cur_dnsName)
                    {
                        ntpserver_name = getNodeValue(doc, cur_dnsName);
                    }
                }
            }
        }
    }
    if (!ntpserver_ipv4.empty() && ntpserver_ipv4 != "0.0.0.0")
    {
        response = ntpserver_ipv4;
    }
    else if (!ntpserver_name.empty())
    {
        response = ntpserver_name;
    }
    else
    {
        response = "";
    }
    xmlFreeDoc(doc);
    return;
}

void NvSoap::getDeviceInformationResponse(const string& xmlData, map<string, string>& info)
{
    xmlDocPtr    doc;
    string value;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if (cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "GetDeviceInformationResponse");
        if (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "Model");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              info["Model"] = value;
            }

            cur_ = findNode(doc, cur, "Manufacturer");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              info["Manufacturer"] = value;
            }

            cur_ = findNode(doc, cur, "FirmwareVersion");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              info["FirmwareVersion"] = value;
            }

            cur_ = findNode(doc, cur, "SerialNumber");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              info["SerialNumber"] = value;
            }

            cur_ = findNode(doc, cur, "HardwareId");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              info["HardwareId"] = value;
            }
        }
    }
    xmlFreeDoc(doc);
    return;
}

int NvSoap::getCapabilitiesResponse(const string& xmlData, map<string, OnvifServiceInfo>& caps)
{
    xmlDocPtr    doc;
    OnvifServiceInfo value;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return -1;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    xmlNodePtr cur = findNode(doc, cursor, ONVIF_MEDIA_SERVICE);
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "XAddr");
        if (cur_)
        {
          value.url = getNodeValue(doc, cur_);
          value.name_space = ONVIF_MEDIA_SERVICE_NAMESPACE;
          caps[ONVIF_MEDIA_SERVICE] = value;
        }
    }
    cur = findNode(doc, cursor, ONVIF_PTZ_SERVICE);
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "XAddr");
        if (cur_)
        {
          value.url = getNodeValue(doc, cur_);
          value.name_space = ONVIF_PTZ_SERVICE_NAMESPACE;
          caps[ONVIF_PTZ_SERVICE] = value;
        }
    }
    cur = findNode(doc, cursor, ONVIF_REPLAY_SERVICE);
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "XAddr");
        if (cur_)
        {
          value.url = getNodeValue(doc, cur_);
          value.name_space = ONVIF_REPLAY_SERVICE_NAMESPACE;
          caps[ONVIF_REPLAY_SERVICE] = value;
        }
    }
    cur = findNode(doc, cursor, ONVIF_IMAGING_SERVICE);
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "XAddr");
        if (cur_)
        {
          value.url = getNodeValue(doc, cur_);
          value.name_space = ONVIF_IMAGING_SERVICE_NAMESPACE;
          caps[ONVIF_IMAGING_SERVICE] = value;
        }
    }
    cur = findNode(doc, cursor, ONVIF_SEARCH_SERVICE);
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "XAddr");
        if (cur_)
        {
          value.url = getNodeValue(doc, cur_);
          value.name_space = ONVIF_SEARCH_SERVICE_NAMESPACE;
          caps[ONVIF_SEARCH_SERVICE] = value;
        }
    }
    xmlFreeDoc(doc);
    return 0;
}

void NvSoap::getProfileResponse(const string& xmlData, SensorSettings& setting, const string nameSpace)
{
    xmlDocPtr    doc;
    string value;

    if (xmlData.empty() || nameSpace.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    Token& token = setting.token;
    SensorVideoEncoderSettingsValues& encoderValues = setting.encoderValues;
    MultiCast& multiCast = setting.multiCast;

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "Profile");
        if (cur)
        {
            xmlAttr* attribute = cur->properties;
            {
                while (attribute && attribute->name && attribute->children)
                {
                    if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                    {
                      xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                      token.profileToken = (char*)value;
                      xmlFree(value);
                    }
                    attribute = attribute->next;
                }
            }
            xmlNodePtr cur_ = findNode(doc, cur, "Name");
            if (cur_)
            {
                token.profileName = getNodeValue(doc, cur_);
            }

            cur_ = findNode(doc, cur, "SourceToken");
            if (cur_)
            {
                token.sourceToken = getNodeValue(doc, cur_);
            }

            if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
            {
                cur_ = findNode(doc, cur, "VideoEncoderConfiguration");
            }
            else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
            {
                cur_ = findNode(doc, cur, "VideoEncoder");
            }
            else
            {
                LOG(error) << "Invalid namespace:" << nameSpace << endl;
                xmlFreeDoc(doc);
                return;
            }
            if (cur_)
            {
                xmlAttr* attribute = cur_->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                            token.encoderToken = (char*)value;
                            xmlFree(value);
                        }
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "encoding"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                            encoderValues.encoding = (char*)value;
                            xmlFree(value);
                        }

                        if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                        {
                            if (!xmlStrcmp(attribute->name, (const xmlChar *) "GovLength"))
                            {
                                xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                encoderValues.govLength = (char*)value;
                                xmlFree(value);
                            }
                            if (!xmlStrcmp(attribute->name, (const xmlChar *) "Profile"))
                            {
                                xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                encoderValues.encodingProfile = (char*)value;
                                xmlFree(value);
                            }
                        }
                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur__;
                if(encoderValues.encoding.empty())
                {
                    cur__ = findNode(doc, cur_, "Encoding");
                    if (cur__)
                    {
                        encoderValues.encoding = getNodeValue(doc, cur__);
                    }
                }
                cur__ = findNode(doc, cur_, "Resolution");
                if (cur__)
                {
                    string w;
                    xmlNodePtr cur___ = findNode(doc, cur__, "Width");
                    if (cur___)
                    {
                        encoderValues.resolution.width = getNodeValue(doc, cur___);
                    }
                    string h;
                    cur___ = findNode(doc, cur__, "Height");
                    if (cur___)
                    {
                        encoderValues.resolution.height = getNodeValue(doc, cur___);
                    }
                }
                cur__ = findNode(doc, cur_, "Quality");
                if (cur__)
                {
                    encoderValues.quality = getNodeValue(doc, cur__);
                }
                cur__ = findNode(doc, cur_, "RateControl");
                if (cur__)
                {
                    xmlNodePtr cur___ = findNode(doc, cur__, "FrameRateLimit");
                    if (cur___)
                    {
                        encoderValues.frameRate = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "EncodingInterval");
                    if (cur___)
                    {
                        encoderValues.encodingInterval = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "BitrateLimit");
                    if (cur___)
                    {
                        encoderValues.bitrate = getNodeValue(doc, cur___);
                    }
                }
                if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
                {
                    cur__ = findNode(doc, cur_, "H264");
                    if (cur__)
                    {
                        encoderValues.encoding = "H264";
                        xmlNodePtr cur___ = findNode(doc, cur__, "GovLength");
                        if (cur___)
                        {
                            encoderValues.govLength = getNodeValue(doc, cur___);
                        }
                        cur___ = findNode(doc, cur__, "H264Profile");
                        if (cur___)
                        {
                            encoderValues.encodingProfile = getNodeValue(doc, cur___);
                        }
                    }
                    cur__ = findNode(doc, cur_, "H265");
                    if (cur__)
                    {
                        encoderValues.encoding = "H265";
                        xmlNodePtr cur___ = findNode(doc, cur__, "GovLength");
                        if (cur___)
                        {
                            encoderValues.govLength = getNodeValue(doc, cur___);
                        }
                        cur___ = findNode(doc, cur__, "H265Profile");
                        if (cur___)
                        {
                            encoderValues.encodingProfile = getNodeValue(doc, cur___);
                        }
                    }
                }
                else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                {
                    if (encoderValues.govLength.empty())
                    {
                        cur__ = findNode(doc, cur_, "GovLength");
                        if (cur__)
                        {
                            encoderValues.govLength = getNodeValue(doc, cur__);
                        }
                    }
                    if (encoderValues.encodingProfile.empty())
                    {
                        cur__ = findNode(doc, cur_, "Profile");
                        if (cur__)
                        {
                            encoderValues.encodingProfile = getNodeValue(doc, cur__);
                        }
                    }
                }
                cur__ = findNode(doc, cur_, "Multicast");
                if (cur__)
                {
                    xmlNodePtr cur___ = findNode(doc, cur__, "Address");
                    if (cur___)
                    {
                        xmlNodePtr cur____ = findNode(doc, cur___, "Type");
                        if (cur____)
                        {
                            multiCast.AddressType = getNodeValue(doc, cur____);
                        }
                        cur____ = findNode(doc, cur___, "IPv4Address");
                        if (cur____)
                        {
                            multiCast.IPAddress = getNodeValue(doc, cur____);
                        }
                    }
                    cur___ = findNode(doc, cur__, "Port");
                    if (cur___)
                    {
                        multiCast.Port = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "TTL");
                    if (cur___)
                    {
                        multiCast.TTL = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "AutoStart");
                    if (cur___)
                    {
                        multiCast.AutoStart = getNodeValue(doc, cur___);
                    }
                }
            }

            if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
            {
                cur_ = findNode(doc, cur, "PTZConfiguration");
            }
            else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
            {
                cur_ = findNode(doc, cur, ONVIF_PTZ_SERVICE);
            }
            else
            {
                LOG(error) << "Invalid namespace:" << nameSpace << endl;
                xmlFreeDoc(doc);
                return;
            }
            if (cur_)
            {
                xmlAttr* attribute = cur_->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          token.ptzToken = (char*)value;
                          xmlFree(value);
                        }
                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur__ = findNode(doc, cur_, "NodeToken");
                if (cur__)
                {
                    value = getNodeValue(doc, cur__);
                    token.ptzNodeToken = value;
                }
            }
        }
    }
    xmlFreeDoc(doc);
    return;
}


void NvSoap::getProfilesResponse(const string& xmlData, vector<SensorSettings>& settings, const string nameSpace)
{
    xmlDocPtr    doc;
    string value;

    if (xmlData.empty() || nameSpace.empty())
    {
        LOG(error) << "xml data/namespace is empty" << endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "Profiles");
        while (cur)
        {
            if (!xmlStrcmp(cur->name, (const xmlChar *)"Profiles"))
            {
                SensorSettings setting;
                Token& token = setting.token;
                SensorVideoEncoderSettingsValues& encoderValues = setting.encoderValues;
                MultiCast& multiCast = setting.multiCast;
                xmlAttr* attribute = cur->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                            token.profileToken = (char*)value;
                            xmlFree(value);
                        }
                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur_ = findNode(doc, cur, "Name");
                if (cur_)
                {
                    token.profileName = getNodeValue(doc, cur_);
                }

                cur_ = findNode(doc, cur, "SourceToken");
                if (cur_)
                {
                    token.sourceToken = getNodeValue(doc, cur_);
                }

                if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
                {
                    cur_ = findNode(doc, cur, "VideoEncoderConfiguration");
                }
                else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                {
                    cur_ = findNode(doc, cur, "VideoEncoder");
                }
                else
                {
                    LOG(error) << "Invalid namespace:" << nameSpace << endl;
                    xmlFreeDoc(doc);
                    return;
                }
                if (cur_)
                {
                    xmlAttr* attribute = cur_->properties;
                    {
                        while (attribute && attribute->name && attribute->children)
                        {
                            if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                            {
                                xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                token.encoderToken = (char*)value;
                                xmlFree(value);
                            }
                            if (!xmlStrcmp(attribute->name, (const xmlChar *) "encoding"))
                            {
                                xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                encoderValues.encoding = (char*)value;
                                xmlFree(value);
                            }

                            if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                            {
                                if (!xmlStrcmp(attribute->name, (const xmlChar *) "GovLength"))
                                {
                                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                    encoderValues.govLength = (char*)value;
                                    xmlFree(value);
                                }
                                if (!xmlStrcmp(attribute->name, (const xmlChar *) "Profile"))
                                {
                                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                                    encoderValues.encodingProfile = (char*)value;
                                    xmlFree(value);
                                }
                            }
                            attribute = attribute->next;
                        }
                    }
                    xmlNodePtr cur__;
                    if (encoderValues.encoding.empty())
                    {
                        cur__ = findNode(doc, cur_, "Encoding");
                        if (cur__)
                        {
                            encoderValues.encoding = getNodeValue(doc, cur__);
                        }
                    }
                    cur__ = findNode(doc, cur_, "Resolution");
                    if (cur__)
                    {
                        string w;
                        xmlNodePtr cur___ = findNode(doc, cur__, "Width");
                        if (cur___)
                        {
                            encoderValues.resolution.width = getNodeValue(doc, cur___);
                        }
                        string h;
                        cur___ = findNode(doc, cur__, "Height");
                        if (cur___)
                        {
                            encoderValues.resolution.height = getNodeValue(doc, cur___);
                        }
                    }
                    cur__ = findNode(doc, cur_, "RateControl");
                    if (cur__)
                    {
                        xmlNodePtr cur___ = findNode(doc, cur__, "FrameRateLimit");
                        if (cur___)
                        {
                            encoderValues.frameRate = getNodeValue(doc, cur___);
                        }
                    }
                    if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
                    {
                        cur__ = findNode(doc, cur_, "H264"); // For Media service only
                        if (cur__)
                        {
                            xmlNodePtr cur___ = findNode(doc, cur__, "GovLength");
                            if (cur___)
                            {
                                encoderValues.govLength = getNodeValue(doc, cur___);
                            }
                            cur___ = findNode(doc, cur__, "H264Profile");
                            if (cur___)
                            {
                                encoderValues.encodingProfile = getNodeValue(doc, cur___);
                            }
                        }

                        cur__ = findNode(doc, cur_, "H265"); // For Media service only
                        if (cur__)
                        {
                            encoderValues.encoding = "H265";
                            xmlNodePtr cur___ = findNode(doc, cur__, "GovLength");
                            if (cur___)
                            {
                                encoderValues.govLength = getNodeValue(doc, cur___);
                            }
                            cur___ = findNode(doc, cur__, "H265Profile");
                            if (cur___)
                            {
                                encoderValues.encodingProfile = getNodeValue(doc, cur___);
                            }
                        }
                    }
                    else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                    {
                        if (encoderValues.govLength.empty())
                        {
                            cur__ = findNode(doc, cur_, "GovLength");
                            if (cur__)
                            {
                                encoderValues.govLength = getNodeValue(doc, cur__);
                            }
                        }
                        if (encoderValues.encodingProfile.empty())
                        {
                            cur__ = findNode(doc, cur_, "Profile");
                            if (cur__)
                            {
                                encoderValues.encodingProfile = getNodeValue(doc, cur__);
                            }
                        }
                    }
                    cur__ = findNode(doc, cur_, "Multicast");
                    if (cur__)
                    {
                        xmlNodePtr cur___ = findNode(doc, cur__, "Address");
                        if (cur___)
                        {
                            xmlNodePtr cur____ = findNode(doc, cur___, "Type");
                            if (cur____)
                            {
                                multiCast.AddressType = getNodeValue(doc, cur____);
                            }
                            cur____ = findNode(doc, cur___, "IPv4Address");
                            if (cur____)
                            {
                                multiCast.IPAddress = getNodeValue(doc, cur____);
                            }
                        }
                        cur___ = findNode(doc, cur__, "Port");
                        if (cur___)
                        {
                            multiCast.Port = getNodeValue(doc, cur___);
                        }
                        cur___ = findNode(doc, cur__, "TTL");
                        if (cur___)
                        {
                            multiCast.TTL = getNodeValue(doc, cur___);
                        }
                        cur___ = findNode(doc, cur__, "AutoStart");
                        if (cur___)
                        {
                            multiCast.AutoStart = getNodeValue(doc, cur___);
                        }
                    }
                }

                if (nameSpace == ONVIF_MEDIA_SERVICE_NAMESPACE)
                {
                    cur_ = findNode(doc, cur, "PTZConfiguration");
                }
                else if (nameSpace == ONVIF_MEDIA2_SERVICE_NAMESPACE)
                {
                    cur_ = findNode(doc, cur, ONVIF_PTZ_SERVICE);
                }
                else
                {
                    LOG(error) << "Invalid namespace:" << nameSpace << endl;
                    xmlFreeDoc(doc);
                    return;
                }
                if (cur_)
                {
                    xmlAttr* attribute = cur_->properties;
                    {
                        while (attribute && attribute->name && attribute->children)
                        {
                            if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                            {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                            token.ptzToken = (char*)value;
                            xmlFree(value);
                            }
                            attribute = attribute->next;
                        }
                    }
                    xmlNodePtr cur__ = findNode(doc, cur_, "NodeToken");
                    if (cur__)
                    {
                        value = getNodeValue(doc, cur__);
                        token.ptzNodeToken = value;
                    }
                }
                settings.push_back(setting);
            }
            cur = cur->next;
        }
    }
    xmlFreeDoc(doc);
    return;
}

void NvSoap::getPTZProfilesResponse(const string& xmlData, vector<Profile>& profiles)
{
    xmlDocPtr    doc;
    string value;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    Profile profile;

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "Profiles");
        while (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "PTZConfiguration");
            if (cur_)
            {
                xmlAttr* attribute = cur_->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          profile.ptzToken = (char*)value;
                          xmlFree(value);
                        }
                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur__ = findNode(doc, cur_, "Name");
                if (cur__)
                {
                    value = getNodeValue(doc, cur__);
                    profile.name = value;
                }
                cur__ = findNode(doc, cur_, "NodeToken");
                if (cur__)
                {
                    value = getNodeValue(doc, cur__);
                    profile.ptzNodeToken = value;
                }
            }
            profiles.push_back(profile);
            cur = cur->next;
        }
    }
    xmlFreeDoc(doc);
    return;
}


string NvSoap::getUriResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return value;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "Uri");
    if (!cur)
    {
        LOG(error) << "xml data does not contain camera info" <<endl;
        return value;
    }
    value = getNodeValue(doc, cur);
    xmlFreeDoc(doc);
    return value;
}

static void getPTZSpaceValues(xmlDocPtr doc, xmlNodePtr cur_, PTZSpaces& space)
{
    if (cur_ == nullptr)
    {
        return;
    }
    xmlNodePtr cur__ = findNode(doc, cur_, "XRange");
    if (cur__)
    {
        xmlNodePtr cur___ = findNode(doc, cur__, "Min");
        if (cur___)
        {
            space.x_min_range = getNodeValue(doc, cur___);

        }
        cur___ = findNode(doc, cur__, "Max");
        if (cur___)
        {
            space.x_max_range = getNodeValue(doc, cur___);
        }
    }
    cur__ = findNode(doc, cur_, "YRange");
    if (cur__)
    {
        xmlNodePtr cur___ = findNode(doc, cur__, "Min");
        if (cur___)
        {
            space.y_min_range = getNodeValue(doc, cur___);

        }
        cur___ = findNode(doc, cur__, "Max");
        if (cur___)
        {
            space.y_max_range = getNodeValue(doc, cur___);
        }
    }
}

vector<PTZSpaces> NvSoap::getPTZNodeResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    string value;
    vector<PTZSpaces> spaces;
    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return spaces;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "SupportedPTZSpaces");
        while (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "AbsolutePanTiltPositionSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::AbsolutePanTiltPositionSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "AbsoluteZoomPositionSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::AbsoluteZoomPositionSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "RelativePanTiltTranslationSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::RelativePanTiltTranslationSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "RelativeZoomTranslationSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::RelativeZoomTranslationSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "ContinuousPanTiltVelocitySpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::ContinuousPanTiltVelocitySpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "ContinuousZoomVelocitySpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::ContinuousZoomVelocitySpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "PanTiltSpeedSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::PanTiltSpeedSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur_ = findNode(doc, cur, "ZoomSpeedSpace");
            if (cur_)
            {
                 PTZSpaces space;
                 space.spaceType = PTZSpaceTypes::ZoomSpeedSpace;
                 getPTZSpaceValues(doc, cur_, space);
                 spaces.push_back(space);
            }
            cur = cur->next;
        }
    }
    xmlFreeDoc(doc);
    return spaces;
}

SensorImageSettingsValues NvSoap::getCameraGetImageSettingsResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorImageSettingsValues values;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return values;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "ImagingSettings");
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "BacklightCompensation");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            if (cur__)
            {
                values.BacklightCompensationMode = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Level");
            if (cur__)
            {
                values.BacklightCompensationLevel = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "Brightness");
        if (cur_)
        {
            values.Brightness = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "ColorSaturation");
        if (cur_)
        {
            values.ColorSaturation = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "Contrast");
        if (cur_)
        {
            values.Contrast = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "Exposure");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            if (cur__)
            {
                values.ExposureMode = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Priority");
            if (cur__)
            {
                values.ExposurePriority = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Window");
            if (cur__)
            {
                Rect& rect = values.ExposureWindow;
                xmlAttr* attribute = cur__->properties;
                {
                    if (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "bottom"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          rect.bottom = (char*)value;
                          xmlFree(value);
                        }
                        attribute = attribute->next;
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "top"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          rect.top = (char*)value;
                          xmlFree(value);
                        }
                        attribute = attribute->next;
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "right"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          rect.right = (char*)value;
                          xmlFree(value);
                        }
                        attribute = attribute->next;
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "left"))
                        {
                          xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                          rect.left = (char*)value;
                          xmlFree(value);
                        }
                    }
                }
            }
            cur__ = findNode(doc, cur_, "MinExposureTime");
            if (cur__)
            {
                values.MinExposureTime = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "MaxExposureTime");
            if (cur__)
            {
                values.MaxExposureTime = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "MaxGain");
            if (cur__)
            {
                values.ExposureMaxGain = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "ExposureTime");
            if (cur__)
            {
                values.ExposureTime = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Gain");
            if (cur__)
            {
                values.ExposureGain = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "IrCutFilter");
        if (cur_)
        {
            values.IrCutFilterMode = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "Sharpness");
        if (cur_)
        {
            values.Sharpness = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "WideDynamicRange");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            if (cur__)
            {
                values.WideDynamicRangeMode = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Level");
            if (cur__)
            {
                values.WideDynamicRangeLevel = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "WhiteBalance");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            if (cur__)
            {
                values.WhiteBalanceMode = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "CrGain");
            if (cur__)
            {
                values.WhiteBalanceYrGain = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "CbGain");
            if (cur__)
            {
                values.WhiteBalanceYbGain = getNodeValue(doc, cur__);
            }
        }
    }
    xmlFreeDoc(doc);
    return values;
}

SensorImageSettingsOptions NvSoap::getCameraGetImageOptionResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorImageSettingsOptions options;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return options;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "ImagingOptions");
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "BacklightCompensation");
        vector<string>& modes = options.BacklightCompensationModes;
        modes.clear();
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            while (cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"Mode")))
                {
                    modes.push_back(getNodeValue(doc, cur__));
                }
                cur__ = cur__->next;
            }
            cur__ = findNode(doc, cur_, "Level");
            if (cur__)
            {
                xmlNodePtr cur____ = findNode(doc, cur__, "Min");
                if (cur____)
                {
                   options.BacklightCompensationLevel.min = getNodeValue(doc, cur____);
                }
                cur____ = findNode(doc, cur__, "Max");
                if (cur____)
                {
                    options.BacklightCompensationLevel.max = getNodeValue(doc, cur____);
                }
            }
        }
        cur_ = findNode(doc, cur, "Brightness");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Min");
            if (cur__)
            {
                options.Brightness.min = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Max");
            if (cur__)
            {
                options.Brightness.max = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "ColorSaturation");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Min");
            if (cur__)
            {
                options.ColorSaturation.min = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Max");
            if (cur__)
            {
                options.ColorSaturation.max = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "Contrast");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Min");
            if (cur__)
            {
                options.Contrast.min = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Max");
            if (cur__)
            {
                options.Contrast.max = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "Exposure");
        vector<string>& expose_modes = options.ExposureModes;
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            while(cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"Mode")))
                {
                    expose_modes.push_back(getNodeValue(doc, cur__));
                }
                cur__ = cur__->next;
            }
            cur__ = findNode(doc, cur_, "Priority");
            vector<string>& priorities_modes = options.ExposurePriorities;
            while(cur__)
            {
                if (cur__)
                {
                    if ((!xmlStrcmp(cur__->name, (const xmlChar *)"Priority")))
                    {
                       priorities_modes.push_back(getNodeValue(doc, cur__));
                    }
                }
                cur__ = cur__->next;
            }
            cur__ = findNode(doc, cur_, "MinExposureTime");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.MinExposureTime.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.MinExposureTime.max = getNodeValue(doc, cur___);
                }
            }
            cur__ = findNode(doc, cur_, "MaxExposureTime");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.MaxExposureTime.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.MaxExposureTime.max = getNodeValue(doc, cur___);
                }
            }
            cur__ = findNode(doc, cur_, "MaxGain");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.ExposureMaxGain.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.ExposureMaxGain.max = getNodeValue(doc, cur___);
                }
            }
            cur__ = findNode(doc, cur_, "ExposureTime");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.ExposureTime.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.ExposureTime.max = getNodeValue(doc, cur___);
                }
            }
            cur__ = findNode(doc, cur_, "Gain");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.ExposureGain.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.ExposureGain.max = getNodeValue(doc, cur___);
                }
            }
        }
        cur_ = findNode(doc, cur, "IrCutFilterModes");
        vector<string>& irCutFilter_modes = options.IrCutFilterModes;
        while(cur_)
        {
            if ((!xmlStrcmp(cur_->name, (const xmlChar *)"IrCutFilterModes")))
            {
                irCutFilter_modes.push_back(getNodeValue(doc, cur_));
            }
            cur_ = cur_->next;
        }
        cur_ = findNode(doc, cur, "Sharpness");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Min");
            if (cur__)
            {
                options.Sharpness.min = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Max");
            if (cur__)
            {
                options.Sharpness.max = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "WideDynamicRange");
        vector<string>& wideDynamicRange_modes = options.WideDynamicRangeModes;
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            while (cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"Mode")))
                {
                    wideDynamicRange_modes.push_back(getNodeValue(doc, cur__));
                }
                cur__ = cur__->next;
            }
            cur__ = findNode(doc, cur_, "Level");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.WideDynamicRangeLevel.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.WideDynamicRangeLevel.max = getNodeValue(doc, cur___);
                }
            }
        }
        cur_ = findNode(doc, cur, "WhiteBalance");
        vector<string>& whiteBalanceModes_modes = options.WhiteBalanceModes;
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Mode");
            while (cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"Mode")))
                {
                    whiteBalanceModes_modes.push_back(getNodeValue(doc, cur__));
                }
                cur__ = cur__->next;
            }
            cur__ = findNode(doc, cur_, "YrGain");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.WhiteBalanceYrGain.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.WhiteBalanceYrGain.max = getNodeValue(doc, cur___);
                }
            }
            cur__ = findNode(doc, cur_, "YbGain");
            if (cur__)
            {
                xmlNodePtr cur___ = findNode(doc, cur__, "Min");
                if (cur___)
                {
                    options.WhiteBalanceYbGain.min = getNodeValue(doc, cur___);
                }
                cur___ = findNode(doc, cur__, "Max");
                if (cur___)
                {
                    options.WhiteBalanceYbGain.max = getNodeValue(doc, cur___);
                }
            }
        }
    }
    else
    {
        LOG(error) << "xml data does not contain camera image options" <<endl;
        return options;
    }
    xmlFreeDoc(doc);
    return options;
}

bool NvSoap::setCameraNetworkInterfacesResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return false;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "RebootNeeded");
    if (cur)
    {
        string rebootNeeded = getNodeValue(doc, cur);
        if (rebootNeeded == "true")
            return true;
    }
    return false;
}

SensorNetworkInfo NvSoap::getCameraNetworkInterfacesResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorNetworkInfo networkInfo;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return networkInfo;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "NetworkInterfaces");
    if (cur)
    {
        while (cur)
        {
            xmlNodePtr cur_iface = findNode(doc, cur, "Info");
            if (cur_iface)
            {
                cur_iface = findNode(doc, cur_iface, "Name");
                if (cur_iface)
                {
                    if (getNodeValue(doc, cur_iface) != "eth0")
                    {
                        // Look for only 'eth0' interface.
                        cur = cur->next;
                        continue;
                    }
                    networkInfo.interfaceName = getNodeValue(doc, cur_iface);
                }
            }

            if (networkInfo.interfaceName == "eth0" || networkInfo.interfaceName.empty())
            {
                xmlAttr* attribute = cur->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                            networkInfo.token.first = (char*)value;
                            xmlFree(value);
                        }
                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur_token = findNode(doc, cur, "Enabled");
                if (cur_token)
                {
                    string isTokenEnabled = getNodeValue(doc, cur_token);
                    networkInfo.token.second = (isTokenEnabled == "true") ? true : false;
                }

                // Ipv4 information
                xmlNodePtr cur_ipv4 = findNode(doc, cur, "IPv4");
                if (cur_ipv4)
                {
                    xmlNodePtr cur_ipv4_enabled = findNode(doc, cur_ipv4, "Enabled");
                    if (cur_ipv4_enabled)
                    {
                        string isIpv4Enabled = getNodeValue(doc, cur_ipv4_enabled);
                        networkInfo.enableIpv4 = (isIpv4Enabled == "true") ? true : false;
                    }
                    if (networkInfo.enableIpv4 == true)
                    {
                        xmlNodePtr cur_config = findNode(doc, cur_ipv4, "Config");
                        if (cur_config)
                        {
                            xmlNodePtr cur_dhcp = findNode(doc, cur_config, "DHCP");
                            if (cur_dhcp)
                            {
                                networkInfo.enableDhcp4 = getNodeValue(doc, cur_dhcp);
                            }
                            if (networkInfo.enableDhcp4 == "true")
                            {
                                xmlNodePtr cur_FromDHCP = findNode(doc, cur_config, "FromDHCP");
                                if (cur_FromDHCP)
                                {
                                    xmlNodePtr cur_address = findNode(doc, cur_FromDHCP, "Address");
                                    if (cur_address)
                                    {
                                        networkInfo.IPAddr4 = getNodeValue(doc, cur_address);
                                    }

                                    xmlNodePtr cur_prefixLen = findNode(doc, cur_FromDHCP, "PrefixLength");
                                    if (cur_prefixLen)
                                    {
                                        networkInfo.prefixLen4 = getNodeValue(doc, cur_prefixLen);
                                    }
                                }
                            }
                            else    // Static configuration
                            {
                                xmlNodePtr cur_manual = findNode(doc, cur_config, "Manual");
                                if (cur_manual)
                                {
                                    //while (cur_manual)
                                    {
                                        xmlNodePtr cur_address = findNode(doc, cur_manual, "Address");
                                        if (cur_address)
                                        {
                                            networkInfo.IPAddr4 = getNodeValue(doc, cur_address);
                                        }

                                        xmlNodePtr cur_prefixLen = findNode(doc, cur_manual, "PrefixLength");
                                        if (cur_prefixLen)
                                        {
                                            networkInfo.prefixLen4 = getNodeValue(doc, cur_prefixLen);
                                        }
                                        //cur_manual = cur_manual->next;
                                    }
                                } // cur_manual
                            }
                        }  //Config
                    }
                }  //cur_ipv4

                // Ipv6 information
                xmlNodePtr cur_ipv6 = findNode(doc, cur, "IPv6");
                if (cur_ipv6)
                {
                    xmlNodePtr cur_ipv6_enabled  = findNode(doc, cur_ipv6, "Enabled");
                    if (cur_ipv6_enabled)
                    {
                        string isIpv6Enabled = getNodeValue(doc, cur_ipv6_enabled);
                        networkInfo.enableIpv6 = (isIpv6Enabled == "true") ? true : false;
                    }
                    if (networkInfo.enableIpv6 == true)
                    {
                        xmlNodePtr cur_config = findNode(doc, cur_ipv6, "Config");
                        if (cur_config)
                        {
                            xmlNodePtr cur_dhcp = findNode(doc, cur_config, "DHCP");
                            if (cur_dhcp)
                            {
                                networkInfo.enableDhcp6 = getNodeValue(doc, cur_dhcp);
                            }

                            if (networkInfo.enableDhcp6 != "Off")   // { 'Auto', 'Stateful', 'Stateless', 'Off' }
                            {
                                xmlNodePtr cur_FromDHCP = findNode(doc, cur_config, "FromDHCP");
                                if (cur_FromDHCP)
                                {
                                    xmlNodePtr cur_address = findNode(doc, cur_FromDHCP, "Address");
                                    if (cur_address)
                                    {
                                        networkInfo.IPAddr6 = getNodeValue(doc, cur_address);
                                    }

                                    xmlNodePtr cur_prefixLen = findNode(doc, cur_FromDHCP, "PrefixLength");
                                    if (cur_prefixLen)
                                    {
                                        networkInfo.prefixLen6 = getNodeValue(doc, cur_prefixLen);
                                    }
                                }
                            }
                            else    // Static configuration
                            {
                                xmlNodePtr cur_manual = findNode(doc, cur_config, "Manual");
                                if (cur_manual)
                                {
                                    while (cur_manual)
                                    {
                                        xmlNodePtr cur_address = findNode(doc, cur_manual, "Address");
                                        if (cur_address)
                                        {
                                            networkInfo.IPAddr6 = getNodeValue(doc, cur_address);
                                        }

                                        xmlNodePtr cur_prefixLen = findNode(doc, cur_manual, "PrefixLength");
                                        if (cur_prefixLen)
                                        {
                                            networkInfo.prefixLen6 = getNodeValue(doc, cur_prefixLen);
                                        }
                                        cur_manual = cur_manual->next;
                                    }
                                } // cur_manual
                            }
                        }  //Config
                    }
                }  //cur_ipv6
            }
            cur = cur->next;
        }
    }
    else
    {
        LOG(error) << "xml data does not contain camera network info" <<endl;
        return networkInfo;
    }
    xmlFreeDoc(doc);
    return networkInfo;
}

string NvSoap::rebootCameraResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string rebootMessage;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return rebootMessage;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "Message");
    if (cur)
    {
        rebootMessage = getNodeValue(doc, cur);
    }
    return rebootMessage;
}

static void parseEncoderSettings(xmlDocPtr doc, xmlNodePtr cur_, VideoEncoderConfigurationsOptions& options)
{
    if (cur_)
    {
        xmlNodePtr cur__ = findNode(doc, cur_, "ResolutionsAvailable");
        if (cur__)
        {
            vector <Resolution>& resolution = options.ResolutionsAvailable;
            while(cur__)
            {
                Resolution res;
                xmlNodePtr cur___ = findNode(doc, cur__, "Width");
                if (cur___)
                {
                    res.width = getNodeValue(doc, cur___);

                }
                cur___ = findNode(doc, cur__, "Height");
                if (cur___)
                {
                    res.height = getNodeValue(doc, cur___);
                }
                if (isDuplicateEntry(resolution, res) == false && (res.width.empty() == false && res.height.empty() == false))
                {
                    resolution.push_back(res);
                }
                cur__ = cur__->next;
            }
        }
        cur__ = findNode(doc, cur_, "GovLengthRange");
        if (cur__)
        {
            xmlNodePtr cur___ = findNode(doc, cur__, "Min");
            if (cur___)
            {
                options.GovLengthRange.min = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur__, "Max");
            if (cur___)
            {
                options.GovLengthRange.max = getNodeValue(doc, cur___);
            }
        }
        cur__ = findNode(doc, cur_, "FrameRateRange");
        if (cur__)
        {
            string minFrameRate, maxFrameRate;
            xmlNodePtr cur___ = findNode(doc, cur__, "Min");
            if (cur___)
            {
                minFrameRate = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur__, "Max");
            if (cur___)
            {
                maxFrameRate = getNodeValue(doc, cur___);
            }

            int min = std::stoi(minFrameRate);
            int max = std::stoi(maxFrameRate);
            std::ostringstream resultStream;

            for (int cnt = min; cnt <= max; ++cnt)
            {
                resultStream << cnt;
                if (cnt != max)
                {
                    resultStream << " ";
                }
            }
            options.FrameRateSupported = resultStream.str();
        }
        cur__ = findNode(doc, cur_, "EncodingIntervalRange");
        if (cur__)
        {
            xmlNodePtr cur___ = findNode(doc, cur__, "Min");
            if (cur___)
            {
                options.EncodingIntervalRange.min = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur__, "Max");
            if (cur___)
            {
                options.EncodingIntervalRange.max = getNodeValue(doc, cur___);
            }
        }
        cur__ = findNode(doc, cur_, "BitrateRange");
        if (cur__)
        {
            xmlNodePtr cur___ = findNode(doc, cur__, "Min");
            if (cur___)
            {
                options.BitrateRange.min = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur__, "Max");
            if (cur___)
            {
                options.BitrateRange.max = getNodeValue(doc, cur___);
            }
        }
    }
}

SensorEncoderSettingsOptions NvSoap::getVideoEncoderConfigurationOptionsMediaResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorEncoderSettingsOptions options;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return options;
    }

    VideoEncoderConfigurationsOptions option;

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "Options");
    if (cur)
    {
        xmlNodePtr cur_ = findNode(doc, cur, "QualityRange");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "Min");
            if (cur__)
            {
                option.qualityRange.min = getNodeValue(doc, cur__);
            }
            cur__ = findNode(doc, cur_, "Max");
            if (cur__)
            {
                option.qualityRange.max = getNodeValue(doc, cur__);
            }
        }
        cur_ = findNode(doc, cur, "H264");
        if (cur_)
        {
            option.encoding = "H264";
            options.videoEncodingSupported.push_back("H264");
            parseEncoderSettings(doc, cur, option);

            xmlNodePtr cur__ = findNode(doc, cur_, "H264ProfilesSupported");
            vector<string>& H264ProfilesSupported = option.profilesSupported;
            while (cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"H264ProfilesSupported")))
                {
                    string p = getNodeValue(doc, cur__);
                    if (isDuplicateEntry(H264ProfilesSupported, p) == false)
                    {
                        H264ProfilesSupported.push_back(p);
                    }
                }
                cur__ = cur__->next;
            }
        }

        cur_ = findNode(doc, cur, "Extension");
        if (cur_)
        {
            xmlNodePtr cur__ = findNode(doc, cur_, "H264");
            if (cur__)

            {
                parseEncoderSettings(doc, cur__, option);
            }

            cur__ = findNode(doc, cur_, "H264ProfilesSupported");
            vector<string>& H264ProfilesSupported = option.profilesSupported;
            while (cur__)
            {
                if ((!xmlStrcmp(cur__->name, (const xmlChar *)"H264ProfilesSupported")))
                {
                    string p = getNodeValue(doc, cur__);
                    if (isDuplicateEntry(H264ProfilesSupported, p) == false)
                    {
                        H264ProfilesSupported.push_back(p);
                    }
                }
                cur__ = cur__->next;
            }
        }

        options.encoderSettingsOptions.push_back(option);

        LOG(verbose) << "--------------------------------------------------------" << endl;
        for (auto encodings: options.videoEncodingSupported)
        {
            LOG(verbose) << "videoEncodingSupported:" << encodings << endl;
        }
        for (auto videoEncoderConfigurationsOptions : options.encoderSettingsOptions)
        {
            LOG(verbose) << "encoding:" << videoEncoderConfigurationsOptions.encoding << endl;
            LOG(verbose) << "FrameRateSupported:" << videoEncoderConfigurationsOptions.FrameRateSupported << endl;
            for (auto Resolution: videoEncoderConfigurationsOptions.ResolutionsAvailable)
            {
                LOG(verbose) << "Resolution width:" << Resolution.width << " height:" << Resolution.height << endl;
            }
            for (auto profilesSupported : videoEncoderConfigurationsOptions.profilesSupported)
            {
                LOG(verbose) << "profilesSupported:" << profilesSupported << endl;
            }
            LOG(verbose) << "qualityRange min:" << videoEncoderConfigurationsOptions.qualityRange.min << " max:" << videoEncoderConfigurationsOptions.qualityRange.max << endl;
            LOG(verbose) << "bitrateRange min:" << videoEncoderConfigurationsOptions.BitrateRange.min << " max:" << videoEncoderConfigurationsOptions.BitrateRange.max << endl;
            LOG(verbose) << "EncodingIntervalRange: min:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.min << " max:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.max << endl;
            LOG(verbose) << "GovLengthRange min:" << videoEncoderConfigurationsOptions.GovLengthRange.min << " max:" << videoEncoderConfigurationsOptions.GovLengthRange.max << endl;
        }
        LOG(verbose) << "-----------------------------------------------" << endl;
    }
    xmlFreeDoc(doc);
    return options;
}

SensorVideoEncoderSettingsValues NvSoap::getVideoEncoderConfigurationMediaResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorVideoEncoderSettingsValues values;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return values;
    }

    Token token;
    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "Configuration");
    if (cur)
    {
        xmlAttr* attribute = cur->properties;
        {
            while (attribute && attribute->name && attribute->children)
            {
                if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                {
                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                    token.profileToken = (char*)value;
                    xmlFree(value);
                }
                attribute = attribute->next;
            }
        }
        xmlNodePtr cur_ = findNode(doc, cur, "Name");
        if (cur_)
        {
            token.profileName = getNodeValue(doc, cur_);
        }

        cur_ = findNode(doc, cur, "Encoding");
        if (cur_)
        {
            values.encoding = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "Resolution");
        if (cur_)
        {
            string w;
            xmlNodePtr cur___ = findNode(doc, cur_, "Width");
            if (cur___)
            {
                values.resolution.width = getNodeValue(doc, cur___);
            }
            string h;
            cur___ = findNode(doc, cur_, "Height");
            if (cur___)
            {
                values.resolution.height = getNodeValue(doc, cur___);
            }
        }
        cur_ = findNode(doc, cur, "Quality");
        if (cur_)
        {
            values.quality = getNodeValue(doc, cur_);
        }
        cur_ = findNode(doc, cur, "RateControl");
        if (cur_)
        {
            xmlNodePtr cur___ = findNode(doc, cur_, "FrameRateLimit");
            if (cur___)
            {
                values.frameRate = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "EncodingInterval");
            if (cur___)
            {
                values.encodingInterval = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "BitrateLimit");
            if (cur___)
            {
                values.bitrate = getNodeValue(doc, cur___);
            }
        }
        cur_ = findNode(doc, cur, "H264");
        values.encoding = "";
        if (cur_)
        {
            values.encoding = "H264";
            xmlNodePtr cur___ = findNode(doc, cur_, "GovLength");
            if (cur___)
            {
                values.govLength = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "H264Profile");
            if (cur___)
            {
                values.encodingProfile = getNodeValue(doc, cur___);
            }
        }
        cur_ = findNode(doc, cur, "H265");
        if (cur_)
        {
            values.encoding = "H265";
            xmlNodePtr cur___ = findNode(doc, cur_, "GovLength");
            if (cur___)
            {
                values.govLength = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "H264Profile");
            if (cur___)
            {
                values.encodingProfile = getNodeValue(doc, cur___);
            }
        }
    }

    LOG(verbose) << "-----------------------------------------------" << endl;
    LOG(verbose) << "encoding: " << values.encoding << endl;
    LOG(verbose) << "resolution width:" << values.resolution.width << " height:" << values.resolution.height << endl;
    LOG(verbose) << "frameRate: " << values.frameRate << endl;
    LOG(verbose) << "bitrate: " << values.bitrate << endl;
    LOG(verbose) << "encodingInterval: " << values.encodingInterval << endl;
    LOG(verbose) << "encodingProfile: " << values.encodingProfile << endl;
    LOG(verbose) << "quality: " << values.quality << endl;
    LOG(verbose) << "govLength: " << values.govLength << endl;
    LOG(verbose) << "-----------------------------------------------" << endl;

    xmlFreeDoc(doc);
    return values;
}

static void getCameraPositionResult(const string& xmlData, SensorPosition& position)
{
    xmlDocPtr    doc;
    xmlNodePtr   cursor;
    string result;

    if (xmlData.empty())
    {
        cout << "xml data is empty" <<endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cursor = xmlDocGetRootElement(doc);
    if (cursor == nullptr)
    {
        cout << "xml data does not contain camera info" <<endl;
        return ;
    }

    xmlNodePtr prop_cur = findNode(doc, cursor, "Properties");
    if (prop_cur)
    {
        xmlNodePtr cur = findNode(doc, prop_cur, "Property");
        while (cur)
        {
            string prop_name;
            xmlNodePtr cur_ = findNode(doc, cur, "DisplayName");
            if (cur_)
            {
                prop_name = getNodeValue(doc, cur_);
            }
            if (prop_name == "Device position")
            {
                cur_ = findNode(doc, cur, "Value");
                if (cur_)
                {
                    string v = getNodeValue(doc, cur_);
                    if (v != "POINT EMPTY")
                    {
                        // Define a regex pattern to match the format "POINT (x y)"
                        std::regex pattern(R"(POINT \(([^\s]+)\s+([^\s]+)\))");
                        std::smatch matches;

                        // Match the input string against the regex pattern
                        if (std::regex_match(v, matches, pattern))
                        {
                            // Extract the coordinates and assign them to geoLocation
                            position.geoLocation.first = matches[1].str();
                            position.geoLocation.second = matches[2].str();
                        }
                    }
                }
            }
            if (prop_name == "Direction")
            {
                cur_ = findNode(doc, cur, "Value");
                if (cur_)
                {
                    position.direction = getNodeValue(doc, cur_);
                }
            }
            if (prop_name == "Depth")
            {
                cur_ = findNode(doc, cur, "Value");
                if (cur_)
                {
                    position.depth = getNodeValue(doc, cur_);
                }
            }
            if (prop_name == "Field of view")
            {
                cur_ = findNode(doc, cur, "Value");
                if (cur_)
                {
                    position.fieldOfView = getNodeValue(doc, cur_);
                }
            }
            cur = cur->next;
        }
    }
    return;
}

int NvSoap::addUserToken(nvsoap_& soap, xmlTextWriterPtr& writer)
{
      int rc;
      string nounce = generate_uuid();
      string nounce64 = base64_encode(nounce.c_str(), nounce.size());
      string utcTime;

      nvsoap_ soap_time;
      soap_time.url = soap.device_url;
      soap_time.timeout = 5;   // 5sec
      soap_time.curl = soap.curl;
      soap_time.authMethod = soap.authMethod;
      if (GetSystemDateAndTime(soap_time, utcTime) != 0)
      {
          // Use current system utc time if get device time fails.
          utcTime = getCurrentUtcTime();
      }
      LOG(verbose2) << "utcTime: "<<utcTime << endl;

      unsigned char hash[SHA_DIGEST_LENGTH]; // == 20
      string str = nounce + utcTime + soap.password;

      SHA1((const unsigned char*)str.c_str(), str.size(), hash);
      string diagest = base64_encode((const char*)hash, SHA_DIGEST_LENGTH);

      //cout << "Diagest: "<<diagest << endl;
      _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Header");
      _xmlTextWriterStartElement_(writer, BAD_CAST "Security");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "soap:mustUnderstand", BAD_CAST "1");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        goto cleanup;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        goto cleanup;
      }

      _xmlTextWriterStartElement_(writer, BAD_CAST "UsernameToken");
      _xmlTextWriterWriteElement_(writer, BAD_CAST "Username", BAD_CAST soap.user.c_str());
      _xmlTextWriterStartElement_(writer, BAD_CAST "Password");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "Type", BAD_CAST "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        goto cleanup;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST diagest.c_str());
      _xmlTextWriterEndElement_(writer); // Password

      _xmlTextWriterStartElement_(writer, BAD_CAST "Nonce");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "EncodingType", BAD_CAST "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        goto cleanup;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST nounce64.c_str());
      _xmlTextWriterEndElement_(writer); // Nonce

      _xmlTextWriterStartElement_(writer, BAD_CAST "Created");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        goto cleanup;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST utcTime.c_str());
      _xmlTextWriterEndElement_(writer); // Created

      _xmlTextWriterEndElement_(writer); // UsernameToken
      _xmlTextWriterEndElement_(writer); // Security
      _xmlTextWriterEndElement_(writer); // Header

      return 0;
      cleanup:
        return -1;
}

static int composeGetMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
      }
      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composeGetCapabilitiesMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
      }
      {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Category");
        _xmlTextWriterWriteString_(writer, BAD_CAST "All");
        _xmlTextWriterEndElement_(writer);
      }

      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composeGetProfileXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
      }
      {
        if (!soap.token.empty())
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "ProfileToken");
            _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        else if (soap.name_space == ONVIF_MEDIA2_SERVICE_NAMESPACE)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Type");
            _xmlTextWriterWriteString_(writer, BAD_CAST "All");
            _xmlTextWriterEndElement_(writer);
        }
      }

      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composeGetConfigurationMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
      }
      if (soap.token.empty() == false)
      {
          _xmlTextWriterStartElement_(writer, BAD_CAST "ProfileToken");
          _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
          _xmlTextWriterEndElement_(writer);
      }
      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composeGetNodeMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
      }
      if (soap.token.empty() == false)
      {
          _xmlTextWriterStartElement_(writer, BAD_CAST "NodeToken");
          _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
          _xmlTextWriterEndElement_(writer);
      }
      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composePTZMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      string x = soap.userData["x"];
      string y = soap.userData["y"];
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST ONVIF_PTZ_SERVICE_NAMESPACE);
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
      }
      if (soap.token.empty() == false)
      {
          _xmlTextWriterStartElement_(writer, BAD_CAST "ProfileToken");
          _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
          _xmlTextWriterEndElement_(writer);
          _xmlTextWriterStartElement_(writer, BAD_CAST "Velocity");
          _xmlTextWriterStartElement_(writer, BAD_CAST "PanTilt");
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "x", BAD_CAST x.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
           rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "y", BAD_CAST y.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
          _xmlTextWriterEndElement_(writer);
          _xmlTextWriterEndElement_(writer);
      }
      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composePTZStopMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      string pan = soap.userData["PanTilt"];
      string zoom = soap.userData["Zoom"];
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      if (soap.wsdl.empty() == false)
      {
          rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST ONVIF_PTZ_SERVICE_NAMESPACE);
          if (rc < 0)
          {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
          }
      }
      if (soap.token.empty() == false)
      {
          _xmlTextWriterStartElement_(writer, BAD_CAST "ProfileToken");
          _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
          _xmlTextWriterEndElement_(writer);
          _xmlTextWriterWriteElement_(writer, BAD_CAST "PanTilt", BAD_CAST pan.c_str());
          _xmlTextWriterWriteElement_(writer, BAD_CAST "Zoom", BAD_CAST zoom.c_str());
      }
      _xmlTextWriterEndElement_(writer);
      return 0;
}

static int composeGetCameraImageSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
        }
    }
    if (soap.token.empty() == false)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "VideoSourceToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
        _xmlTextWriterEndElement_(writer);
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}

static int composeGetNetworkInterfacesXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}

static int composeSetSystemDateAndTimeInfoXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const DeviceTimeInfo& values = *((DeviceTimeInfo *)soap.userData2);
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    string enableNTP = values.enableNTP ? "NTP" : "Manual";
    _xmlTextWriterStartElement_(writer, BAD_CAST "DateTimeType");
    _xmlTextWriterWriteString_(writer, BAD_CAST enableNTP.c_str());
    _xmlTextWriterEndElement_(writer);

    string dayLightSavings = values.dayLightSavings ? "true" : "false";
    _xmlTextWriterStartElement_(writer, BAD_CAST "DaylightSavings");
    _xmlTextWriterWriteString_(writer, BAD_CAST dayLightSavings.c_str());
    _xmlTextWriterEndElement_(writer);

    if (enableNTP == "Manual")
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "UTCDateTime");
        _xmlTextWriterStartElement_(writer, BAD_CAST "Time");
        {
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            _xmlTextWriterStartElement_(writer, BAD_CAST "Hour");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<0>(values.utcTime).c_str());
            _xmlTextWriterEndElement_(writer);

            _xmlTextWriterStartElement_(writer, BAD_CAST "Minute");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<1>(values.utcTime).c_str());
            _xmlTextWriterEndElement_(writer);

            _xmlTextWriterStartElement_(writer, BAD_CAST "Second");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<2>(values.utcTime).c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer);  // Time

        _xmlTextWriterStartElement_(writer, BAD_CAST "Date");
        {
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            _xmlTextWriterStartElement_(writer, BAD_CAST "Year");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<2>(values.date).c_str());
            _xmlTextWriterEndElement_(writer);

            _xmlTextWriterStartElement_(writer, BAD_CAST "Month");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<1>(values.date).c_str());
            _xmlTextWriterEndElement_(writer);

            _xmlTextWriterStartElement_(writer, BAD_CAST "Day");
            _xmlTextWriterWriteString_(writer, BAD_CAST get<0>(values.date).c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer);  // Date
        _xmlTextWriterEndElement_(writer);  // UTCDateTime
    }
    _xmlTextWriterEndElement_(writer);

    return 0;
}

static int composeSetNTPInfoXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const DeviceNTPInfo& values = *((DeviceNTPInfo *)soap.userData2);
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    string fromDHCP = values.fromDHCP ? "true" : "false";
    _xmlTextWriterStartElement_(writer, BAD_CAST "FromDHCP");
    _xmlTextWriterWriteString_(writer, BAD_CAST fromDHCP.c_str());
    _xmlTextWriterEndElement_(writer);

    if (fromDHCP == "false")
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "NTPManual");
        {
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            _xmlTextWriterStartElement_(writer, BAD_CAST "Type");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.type.c_str());
            _xmlTextWriterEndElement_(writer);

            if (values.type == "IPv4")
            {
                _xmlTextWriterStartElement_(writer, BAD_CAST "IPv4Address");
                _xmlTextWriterWriteString_(writer, BAD_CAST values.ipv4Addr.c_str());
                _xmlTextWriterEndElement_(writer);
            }
            else if (values.type == "IPv6")
            {
                _xmlTextWriterStartElement_(writer, BAD_CAST "IPv6Address");
                _xmlTextWriterWriteString_(writer, BAD_CAST values.ipv6Addr.c_str());
                _xmlTextWriterEndElement_(writer);
            }
            else if (values.type == "DNS")
            {
                _xmlTextWriterStartElement_(writer, BAD_CAST "DNSname");
                _xmlTextWriterWriteString_(writer, BAD_CAST values.dnsName.c_str());
                _xmlTextWriterEndElement_(writer);
            }
        }
        _xmlTextWriterEndElement_(writer);  // NTPManual
    }
    _xmlTextWriterEndElement_(writer);

    return 0;
}

static int composeSetNetworkInterfacesXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const SensorNetworkInfo& values = *((SensorNetworkInfo *)soap.userData2);
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    _xmlTextWriterStartElement_(writer, BAD_CAST "InterfaceToken");
    _xmlTextWriterWriteString_(writer, BAD_CAST values.token.first.c_str());
    _xmlTextWriterEndElement_(writer);

    _xmlTextWriterStartElement_(writer, BAD_CAST "NetworkInterface");
    _xmlTextWriterWriteString_(writer, BAD_CAST values.interfaceName.c_str());

    _xmlTextWriterStartElement_(writer, BAD_CAST "IPv4");
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }

        string enableIpv4 = values.enableIpv4 ? "true" : "false";
        _xmlTextWriterStartElement_(writer, BAD_CAST "Enabled");
        _xmlTextWriterWriteString_(writer, BAD_CAST enableIpv4.c_str());
        _xmlTextWriterEndElement_(writer);

        // Ipv4 configuration
        if (values.enableIpv4)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "DHCP");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.enableDhcp4.c_str());
            _xmlTextWriterEndElement_(writer);

            if (!values.IPAddr4.empty() && !values.prefixLen4.empty() && values.enableDhcp4 == "false")
            {
                _xmlTextWriterStartElement_(writer, BAD_CAST "Manual");
                {
                    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
                    if (rc < 0)
                    {
                        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                        return -1;
                    }

                    _xmlTextWriterStartElement_(writer, BAD_CAST "Address");
                    _xmlTextWriterWriteString_(writer, BAD_CAST values.IPAddr4.c_str());
                    _xmlTextWriterEndElement_(writer);

                    _xmlTextWriterStartElement_(writer, BAD_CAST "PrefixLength");
                    _xmlTextWriterWriteString_(writer, BAD_CAST values.prefixLen4.c_str());
                    _xmlTextWriterEndElement_(writer);
                }
                _xmlTextWriterEndElement_(writer);  // Manual
            }

        }
    }
    _xmlTextWriterEndElement_(writer);  // IPv4


    _xmlTextWriterStartElement_(writer, BAD_CAST "IPv6");
    {
        // Ipv6 configuration
        string enableIpv6 = values.enableIpv6 ? "true" : "false";
        _xmlTextWriterStartElement_(writer, BAD_CAST "Enabled");
        _xmlTextWriterWriteString_(writer, BAD_CAST enableIpv6.c_str());
        _xmlTextWriterEndElement_(writer);

        if (values.enableIpv6)
        {
            if (!values.enableDhcp6.empty())    // enum { 'Auto', 'Stateful', 'Stateless', 'Off' }
            {
                _xmlTextWriterStartElement_(writer, BAD_CAST "DHCP");
                _xmlTextWriterWriteString_(writer, BAD_CAST values.enableDhcp6.c_str());
                _xmlTextWriterEndElement_(writer);
            }

            if (values.enableDhcp6 == "Off")
            {
                if (!values.IPAddr6.empty() && !values.prefixLen6.empty())
                {
                    _xmlTextWriterStartElement_(writer, BAD_CAST "Manual");
                    {
                        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
                        if (rc < 0)
                        {
                            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                            return -1;
                        }

                        _xmlTextWriterStartElement_(writer, BAD_CAST "Address");
                        _xmlTextWriterWriteString_(writer, BAD_CAST values.IPAddr6.c_str());
                        _xmlTextWriterEndElement_(writer);

                        _xmlTextWriterStartElement_(writer, BAD_CAST "PrefixLength");
                        _xmlTextWriterWriteString_(writer, BAD_CAST values.prefixLen6.c_str());
                        _xmlTextWriterEndElement_(writer);
                    }
                    _xmlTextWriterEndElement_(writer);  // Manual
                }
            }
        }
    }
    _xmlTextWriterEndElement_(writer);  // IPv6

    _xmlTextWriterEndElement_(writer); // NetworkInterface
    _xmlTextWriterEndElement_(writer);
    return 0;
}

static int composeGetImageOptionsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
        }
    }
    if (soap.token.empty() == false)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "VideoSourceToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
        _xmlTextWriterEndElement_(writer);
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}

static int composeGetEncoderOptionsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
        }
    }
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "ConfigurationToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST soap.userData["encoderToken"].c_str());
        _xmlTextWriterEndElement_(writer);
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}


static int composeSetEncoderSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const SensorVideoEncoderSettingsValues& values = *((SensorVideoEncoderSettingsValues *)soap.userData2);
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    _xmlTextWriterStartElement_(writer, BAD_CAST "Configuration");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "token", BAD_CAST soap.token.c_str());
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }

    if (soap.name_space == ONVIF_MEDIA2_SERVICE_NAMESPACE)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "GovLength", BAD_CAST values.govLength.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "Profile", BAD_CAST values.encodingProfile.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    _xmlTextWriterStartElement_(writer, BAD_CAST "Name");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
    _xmlTextWriterEndElement_(writer); // Name

    _xmlTextWriterStartElement_(writer, BAD_CAST "UseCount");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteString_(writer, BAD_CAST "1");
    _xmlTextWriterEndElement_(writer); // UseCount

    _xmlTextWriterStartElement_(writer, BAD_CAST "Encoding");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteString_(writer, BAD_CAST values.encoding.c_str());
    _xmlTextWriterEndElement_(writer); // Encoding

    _xmlTextWriterStartElement_(writer, BAD_CAST "Resolution");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "Width", BAD_CAST values.resolution.width.c_str());
    _xmlTextWriterWriteElement_(writer, BAD_CAST "Height", BAD_CAST values.resolution.height.c_str());
    _xmlTextWriterEndElement_(writer); // Resolution

    _xmlTextWriterStartElement_(writer, BAD_CAST "Quality");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteString_(writer, BAD_CAST values.quality.c_str());
    _xmlTextWriterEndElement_(writer); // Quality

    _xmlTextWriterStartElement_(writer, BAD_CAST "RateControl");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "FrameRateLimit", BAD_CAST values.frameRate.c_str());
    if (soap.name_space == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        _xmlTextWriterWriteElement_(writer, BAD_CAST "EncodingInterval", BAD_CAST values.encodingInterval.c_str());
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "BitrateLimit", BAD_CAST values.bitrate.c_str());
    _xmlTextWriterEndElement_(writer); // RateControl

    if (soap.name_space == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "H264");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteElement_(writer, BAD_CAST "GovLength", BAD_CAST values.govLength.c_str());
        _xmlTextWriterWriteElement_(writer, BAD_CAST "H264Profile", BAD_CAST values.encodingProfile.c_str());
        _xmlTextWriterEndElement_(writer); // H264
    }

    map<string, string>multicast = soap.userData;
    _xmlTextWriterStartElement_(writer, BAD_CAST "Multicast");
    _xmlTextWriterStartElement_(writer, BAD_CAST "Address");
    map<string, string>::iterator it = multicast.find("Type");
    if (it != multicast.end())
    {
        string value = it->second;
        _xmlTextWriterWriteElement_(writer, BAD_CAST "Type", BAD_CAST value.c_str());
    }
    it = multicast.find("IPv4Address");
    if (it != multicast.end())
    {
        string value = it->second;
        _xmlTextWriterWriteElement_(writer, BAD_CAST "IPv4Address", BAD_CAST value.c_str());
    }
    _xmlTextWriterEndElement_(writer); // Address
    it = multicast.find("Port");
    if (it != multicast.end())
    {
        string value = it->second;
        _xmlTextWriterWriteElement_(writer, BAD_CAST "Port", BAD_CAST value.c_str());
    }
    it = multicast.find("TTL");
    if (it != multicast.end())
    {
        string value = it->second;
        _xmlTextWriterWriteElement_(writer, BAD_CAST "TTL", BAD_CAST value.c_str());
    }
    it = multicast.find("AutoStart");
    if (it != multicast.end())
    {
        string value = it->second;
        _xmlTextWriterWriteElement_(writer, BAD_CAST "AutoStart", BAD_CAST value.c_str());
    }
    _xmlTextWriterEndElement_(writer); // Multicast

    if (soap.name_space == ONVIF_MEDIA_SERVICE_NAMESPACE)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "SessionTimeout");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST "PT1M");
        _xmlTextWriterEndElement_(writer); // SessionTimeout

        _xmlTextWriterEndElement_(writer); // Configuration
        _xmlTextWriterStartElement_(writer, BAD_CAST "ForcePersistence");
        _xmlTextWriterWriteString_(writer, BAD_CAST "true");
        _xmlTextWriterEndElement_(writer);
    }
    _xmlTextWriterEndElement_(writer); // SetVideoEncoderConfiguration

    return 0;
}

static int composeSetHashingAlgorithmXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const HashingAlgorithmInfo& values = *((HashingAlgorithmInfo*)soap.userData2);

    // Start the method element
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());

    // Add the WSDL namespace if provided
    if (!soap.wsdl.empty())
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0) {
            LOG(error) << "Error at xmlTextWriterWriteAttribute" << std::endl;
            return -1;
        }
    }

    // Write the Algorithm element with the provided value
    _xmlTextWriterStartElement_(writer, BAD_CAST "tds:Algorithm");
    _xmlTextWriterWriteString_(writer, BAD_CAST values.algorithm.c_str());
    _xmlTextWriterEndElement_(writer);  // End the Algorithm element

    _xmlTextWriterEndElement_(writer);  // End the method element

    return 0;
}

static int composeSetCameraImageSettingsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    const SensorImageSettingsValues& values = *((SensorImageSettingsValues *)soap.userData2);
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }
    if (soap.token.empty() == false)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "VideoSourceToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST soap.token.c_str());
        _xmlTextWriterEndElement_(writer);
    }
    _xmlTextWriterStartElement_(writer, BAD_CAST "ImagingSettings");

    if(values.BacklightCompensationMode.size() != 0 && values.BacklightCompensationLevel.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "BacklightCompensation");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        if(values.BacklightCompensationMode.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Mode");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.BacklightCompensationMode.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.BacklightCompensationLevel.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Level");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.BacklightCompensationLevel.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer); // BacklightCompensation
    }
    if(values.Brightness.c_str() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Brightness");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST values.Brightness.c_str());
        _xmlTextWriterEndElement_(writer); // Brightness
    }
    if(values.ColorSaturation.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "ColorSaturation");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST values.ColorSaturation.c_str());
        _xmlTextWriterEndElement_(writer); // ColorSaturation
    }
    if(values.Contrast.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Contrast");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST values.Contrast.c_str());
        _xmlTextWriterEndElement_(writer); // Contrast
    }
    if(values.ExposureMode.size() != 0 || values.ExposurePriority.size() != 0 || (values.ExposureWindow.bottom.size() != 0 &&
    values.ExposureWindow.top.size() != 0 && values.ExposureWindow.right.size() != 0 && values.ExposureWindow.left.size() != 0))
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Exposure");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        if(values.ExposureMode.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Mode");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.ExposureMode.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.ExposurePriority.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Priority");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.ExposurePriority.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.ExposureWindow.bottom.size() != 0 && values.ExposureWindow.top.size() != 0 && values.ExposureWindow.right.size() != 0
        && values.ExposureWindow.left.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Window");
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "bottom", BAD_CAST values.ExposureWindow.bottom.c_str());
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "top", BAD_CAST values.ExposureWindow.top.c_str());
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "right", BAD_CAST values.ExposureWindow.right.c_str());
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "left", BAD_CAST values.ExposureWindow.left.c_str());
            if (rc < 0)
            {
                LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
                return -1;
            }
            _xmlTextWriterEndElement_(writer); // Window
        }
        if(values.MinExposureTime.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "MinExposureTime");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.MinExposureTime.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.MaxExposureTime.size()!= 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "MaxExposureTime");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.MaxExposureTime.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.ExposureMaxGain.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "MaxGain");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.ExposureMaxGain.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.ExposureTime.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "ExposureTime");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.ExposureTime.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.ExposureGain.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Gain");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.ExposureGain.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer); // Exposure
    }
    if(values.IrCutFilterMode.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "IrCutFilter");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST values.IrCutFilterMode.c_str());
        _xmlTextWriterEndElement_(writer); // IrCutFilter
    }
    if(values.Sharpness.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Sharpness");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST values.Sharpness.c_str());
        _xmlTextWriterEndElement_(writer); // Sharpness
    }
    if(values.WideDynamicRangeMode.size() != 0 || values.WideDynamicRangeLevel.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "WideDynamicRange");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        if(values.WideDynamicRangeMode.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Mode");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.WideDynamicRangeMode.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.WideDynamicRangeLevel.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Level");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.WideDynamicRangeLevel.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer); // WideDynamicRange
    }
    if(values.WhiteBalanceMode.size() != 0 || values.WhiteBalanceYrGain.size() != 0 || values.WhiteBalanceYbGain.size() != 0)
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "WhiteBalance");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        if(values.WhiteBalanceMode.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "Mode");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.WhiteBalanceMode.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.WhiteBalanceYrGain.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "CrGain");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.WhiteBalanceYrGain.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        if(values.WhiteBalanceYbGain.size() != 0)
        {
            _xmlTextWriterStartElement_(writer, BAD_CAST "CbGain");
            _xmlTextWriterWriteString_(writer, BAD_CAST values.WhiteBalanceYbGain.c_str());
            _xmlTextWriterEndElement_(writer);
        }
        _xmlTextWriterEndElement_(writer); // WhiteBalance
    }
    _xmlTextWriterEndElement_(writer); // ImagingSettings
    _xmlTextWriterStartElement_(writer, BAD_CAST "ForcePersistence");
    _xmlTextWriterWriteString_(writer, BAD_CAST "true");
    _xmlTextWriterEndElement_(writer);
    _xmlTextWriterEndElement_(writer); // SetImagingSettings
    return 0;
}

static int composeGetUriXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
      int rc;
      _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return -1;
      }
      if (soap.name_space == ONVIF_MEDIA_SERVICE_NAMESPACE ||
        soap.name_space == ONVIF_REPLAY_SERVICE_NAMESPACE)
      {
        _xmlTextWriterStartElement_(writer, BAD_CAST "StreamSetup");
        _xmlTextWriterStartElement_(writer, BAD_CAST "Stream");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST "RTP-Unicast");
        _xmlTextWriterEndElement_(writer); // Stream
        _xmlTextWriterStartElement_(writer, BAD_CAST "Transport");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }

        _xmlTextWriterStartElement_(writer, BAD_CAST "Protocol");
        _xmlTextWriterWriteString_(writer, BAD_CAST "RTSP");
        _xmlTextWriterEndElement_(writer); // Protocol
        _xmlTextWriterEndElement_(writer); // Transport
        _xmlTextWriterEndElement_(writer); // StreamSetup
      }
      else if (soap.name_space == ONVIF_MEDIA2_SERVICE_NAMESPACE)
      {
        _xmlTextWriterStartElement_(writer, BAD_CAST "Protocol");
        _xmlTextWriterWriteString_(writer, BAD_CAST "RtspUnicast");
        _xmlTextWriterEndElement_(writer); // Protocol

      }
      else
      {
        LOG(error) << "Invalid namespace:" << soap.name_space << endl;
        return -1;
      }

      _xmlTextWriterWriteElement_(writer, BAD_CAST soap.tokenName.c_str(), BAD_CAST soap.token.c_str());
      _xmlTextWriterEndElement_(writer); // GetStreamUri
      return 0;
}

static int composeRebootCameraXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}

// ===== Profile G - Recording Search XML Composition Functions =====

static int composeGetRecordingSummaryXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }
    _xmlTextWriterEndElement_(writer);
    return 0;
}

static int composeFindRecordingsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    // Add Scope element
    _xmlTextWriterStartElement_(writer, BAD_CAST "Scope");

    // Add IncludedSources with proper namespace
    int sourceIndex = 0;
    while (true)
    {
        string key = "IncludedSource_" + to_string(sourceIndex);
        auto it = soap.userData.find(key);
        if (it == soap.userData.end())
            break;

        _xmlTextWriterStartElement_(writer, BAD_CAST "IncludedSources");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute for IncludedSources" << endl;
            return -1;
        }
        _xmlTextWriterStartElement_(writer, BAD_CAST "Token");
        _xmlTextWriterWriteString_(writer, BAD_CAST it->second.c_str());
        _xmlTextWriterEndElement_(writer); // Token
        _xmlTextWriterEndElement_(writer); // IncludedSources
        sourceIndex++;
    }

    // Add RecordingInformationFilter if present with proper namespace
    auto filterIt = soap.userData.find("RecordingInformationFilter");
    if (filterIt != soap.userData.end() && !filterIt->second.empty())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "RecordingInformationFilter");
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://www.onvif.org/ver10/schema");
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute for RecordingInformationFilter" << endl;
            return -1;
        }
        _xmlTextWriterWriteString_(writer, BAD_CAST filterIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // RecordingInformationFilter
    }

    _xmlTextWriterEndElement_(writer); // Scope

    // Add MaxMatches
    auto maxMatchesIt = soap.userData.find("MaxMatches");
    if (maxMatchesIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "MaxMatches");
        _xmlTextWriterWriteString_(writer, BAD_CAST maxMatchesIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // MaxMatches
    }

    // Add KeepAliveTime
    auto keepAliveIt = soap.userData.find("KeepAliveTime");
    if (keepAliveIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "KeepAliveTime");
        _xmlTextWriterWriteString_(writer, BAD_CAST keepAliveIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // KeepAliveTime
    }

    _xmlTextWriterEndElement_(writer); // FindRecordings
    return 0;
}

static int composeGetRecordingSearchResultsXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    // Add SearchToken
    auto searchTokenIt = soap.userData.find("SearchToken");
    if (searchTokenIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "SearchToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST searchTokenIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // SearchToken
    }

    // Add MinResults
    auto minResultsIt = soap.userData.find("MinResults");
    if (minResultsIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "MinResults");
        _xmlTextWriterWriteString_(writer, BAD_CAST minResultsIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // MinResults
    }

    // Add MaxResults
    auto maxResultsIt = soap.userData.find("MaxResults");
    if (maxResultsIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "MaxResults");
        _xmlTextWriterWriteString_(writer, BAD_CAST maxResultsIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // MaxResults
    }

    // Add WaitTime
    auto waitTimeIt = soap.userData.find("WaitTime");
    if (waitTimeIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "WaitTime");
        _xmlTextWriterWriteString_(writer, BAD_CAST waitTimeIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // WaitTime
    }

    _xmlTextWriterEndElement_(writer); // GetRecordingSearchResults
    return 0;
}

static int composeEndSearchXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) << "Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }

    // Add SearchToken
    auto searchTokenIt = soap.userData.find("SearchToken");
    if (searchTokenIt != soap.userData.end())
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "SearchToken");
        _xmlTextWriterWriteString_(writer, BAD_CAST searchTokenIt->second.c_str());
        _xmlTextWriterEndElement_(writer); // SearchToken
    }

    _xmlTextWriterEndElement_(writer); // EndSearch
    return 0;
}

string NvSoap::composeXml(nvsoap_& soap, void* methodxml)
{
      int rc;
      xmlTextWriterPtr writer;
      xmlBufferPtr xmlBuf;
      string retString;

      xmlBuf = xmlBufferCreate();
      if (xmlBuf == nullptr)
      {
        LOG(error) << "testXmlwriterMemory: Error creating the xml buffer" << endl;
        return retString;
      }
      AutoDestroyXml xml(xmlBuf);

      writer = xmlNewTextWriterMemory(xmlBuf, 0);
      if (writer == nullptr)
      {
          LOG(error) << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
          return retString;
      }

      rc = _xmlTextWriterStartDocument_(writer, nullptr, ENCODING, nullptr);
      _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      if(!soap.user.empty() && soap.authMethod == AUTH_METHOD_USERNAME_TOKEN)
      {
          if (addUserToken(soap, writer) < 0)
          {
              return retString;
          }
      }

      _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return retString;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return retString;
      }

      composeMethodXml func = (composeMethodXml)methodxml;
      if (func(writer, soap) < 0)
      {
          return retString;
      }

      _xmlTextWriterEndElement_(writer); // "soap:Body"
      _xmlTextWriterEndDocument_(writer); // "Envelope"
      xmlFreeTextWriter(writer);


      retString = ((char *)xmlBuf->content);

      //cout << retString << endl;
      return retString;
}

string NvSoap::composeXmlWithoutUsertoken(nvsoap_& soap, void* methodxml)
{
      int rc;
      xmlTextWriterPtr writer;
      xmlBufferPtr xmlBuf;
      string retString;

      xmlBuf = xmlBufferCreate();
      if (xmlBuf == nullptr)
      {
        LOG(error) << "testXmlwriterMemory: Error creating the xml buffer" << endl;
        return retString;
      }
      AutoDestroyXml xml(xmlBuf);

      writer = xmlNewTextWriterMemory(xmlBuf, 0);
      if (writer == nullptr)
      {
          LOG(error) << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
          return retString;
      }

      rc = _xmlTextWriterStartDocument_(writer, nullptr, ENCODING, nullptr);
      _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return retString;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
        return retString;
      }

      composeMethodXml func = (composeMethodXml)methodxml;
      if (func(writer, soap) < 0)
      {
          return retString;
      }

      _xmlTextWriterEndElement_(writer); // "soap:Body"
      _xmlTextWriterEndDocument_(writer); // "Envelope"
      xmlFreeTextWriter(writer);


      retString = ((char *)xmlBuf->content);

      return retString;
}

string NvSoap::composeProbeXml()
{
      int rc;
      xmlTextWriterPtr writer;
      xmlBufferPtr xmlBuf;
      string retString;

      xmlBuf = xmlBufferCreate();
      if (xmlBuf == nullptr)
      {
        LOG(error) << "testXmlwriterMemory: Error creating the xml buffer" << endl;
        return retString;
      }
      AutoDestroyXml xml(xmlBuf);

      writer = xmlNewTextWriterMemory(xmlBuf, 0);
      if (writer == nullptr)
      {
          LOG(error) << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
          return retString;
      }

      rc = _xmlTextWriterStartDocument_(writer, nullptr, ENCODING, nullptr);
      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Envelope");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:s", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:a", BAD_CAST "http://schemas.xmlsoap.org/ws/2004/08/addressing");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
/*
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:d", BAD_CAST "http://docs.oasis-open.org/ws-dd/ns/discovery/2009/01");
      if (rc < 0)
      {
        cerr <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:i", BAD_CAST "http://printer.example.org/2003/imaging");
      if (rc < 0)
      {
        cerr <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }*/

      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Header");

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:Action");

      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "s:mustUnderstand", BAD_CAST "1");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      _xmlTextWriterWriteString_(writer, BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe");

      _xmlTextWriterEndElement_(writer); // Action

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:MessageID");
      string msgId = string("urn:uuid:") + generate_uuid();
      _xmlTextWriterWriteString_(writer, BAD_CAST msgId.c_str() );//"urn:uuid:0a6dc791-2be6-4991-9af1-454778a1917a");
      _xmlTextWriterEndElement_(writer); // MessageID

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:ReplyTo");
      _xmlTextWriterWriteElement_(writer, BAD_CAST "a:Address", BAD_CAST "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous");
      _xmlTextWriterEndElement_(writer); // ReplyTo

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:To");

      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "s:mustUnderstand", BAD_CAST "1");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST "urn:schemas-xmlsoap-org:ws:2005:04:discovery");
      _xmlTextWriterEndElement_(writer); // To

      _xmlTextWriterEndElement_(writer); // Header


      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Body");

      _xmlTextWriterStartElement_(writer, BAD_CAST "Probe");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      _xmlTextWriterStartElement_(writer, BAD_CAST "d:Types");
      //_xmlTextWriterWriteString_(writer, BAD_CAST "dn:NetworkVideoTransmitter");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:d", BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:dp0", BAD_CAST "http://www.onvif.org/ver10/network/wsdl");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST "dp0:NetworkVideoTransmitter");
      _xmlTextWriterEndElement_(writer); // Types

      //_xmlTextWriterStartElement_(writer, BAD_CAST "d:Scopes");
      //_xmlTextWriterEndElement_(writer); // Scopes

      _xmlTextWriterEndElement_(writer); // Probe
      _xmlTextWriterEndElement_(writer); // "soap:Body"
      _xmlTextWriterEndDocument_(writer); // "Envelope"
      xmlFreeTextWriter(writer);


      retString = ((char *)xmlBuf->content);

      //cout << retString << endl;
      return retString;
}

static string composeCameraConfigurationAPIXML(const string& camera_id)
{
    int rc;
    xmlTextWriterPtr writer;
    xmlBufferPtr xmlBuf;
    string retString;

    xmlBuf = xmlBufferCreate();
    if (xmlBuf == nullptr)
    {
        LOG(error) << "testXmlwriterMemory: Error creating the xml buffer" << endl;
        return retString;
    }
    AutoDestroyXml xml(xmlBuf);

    writer = xmlNewTextWriterMemory(xmlBuf, 0);
    if (writer == nullptr)
    {
        LOG(error) << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
        return retString;
    }

    rc = _xmlTextWriterStartDocument_(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://schemas.xmlsoap.org/soap/envelope/");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

   _xmlTextWriterStartElement_(writer, BAD_CAST "GetItem");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://VideoOS.Net/ConfigurationService");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    string path_value = string("Camera[") + camera_id + string("]");
    _xmlTextWriterWriteElement_(writer, BAD_CAST "path", BAD_CAST path_value.c_str());

    _xmlTextWriterEndElement_(writer); // "StartStatusSession"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);
    return retString;
}

int NvSoap::createAndSendRequest(nvsoap_& soap, string& outData)
{
    std::lock_guard<std::mutex> req_lock(m_reqMutex);
    CURLcode errCode = CURLE_OK;
    CURL* curl = soap.curl;
    int ret = 0;
    if(!curl)
    {
        LOG(error) << "Curl initialzation failed method:" << soap.method << endl;
        return -1;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, soap.url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    if (!soap.jsonData.empty())
    {
        errCode = curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, soap.method.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    struct curl_slist *headers = nullptr;
    if (!soap.xmlData.empty())
    {
        headers = curl_slist_append(headers, "Expect:");
        headers = curl_slist_append(headers, "Content-Type: application/soap+xml");
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, soap.xmlData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, soap.xmlData.size());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
    else if (!soap.jsonData.empty())
    {
        headers = curl_slist_append(headers, "Content-Type: application/json");
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, soap.jsonData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, -1L);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        // Don't bother trying IPv6, which would increase DNS resolution time.
        errCode = curl_easy_setopt(curl, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        // Follow HTTP redirects if necessary.
        errCode = curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    /*Set curl request timeout 20secs*/
    if(soap.timeout == -1)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, GET_CONFIG().onvif_request_timeout_secs);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
    else
    {
        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, soap.timeout);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
#ifdef DEBUG
    struct curlData config;
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGFUNCTION, my_trace);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGDATA, &config);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    errCode = curl_easy_setopt(curl, CURLOPT_VERBOSE, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
#endif

    // Response information.
    long httpCode(0);
    CURLcode code;
    unique_ptr<string> httpData(new string());

    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // errCode = curl_easy_setopt(curl, CURLOPT_VERBOSE, 1L);
    // CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    int retry = 0;
    std::string credentials;

    if (curl != nullptr && soap.authMethod == AUTH_METHOD_DIGEST)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, CURLAUTH_DIGEST);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    while (retry <= REQUEST_RETRY)
    {
        /* Retrying in below cases for the digest authentication supported cameras:
           1. If we received 401 from the camera and username/password is already setted for the camera
           2. If previous soap status due to previous onvif request is already 401 during authentication, we don't need to wait for the 401.
           We can send direct request with authentication header as libcurl already have the already nonce for the digest.
        */
        if ((soap.authMethod == AUTH_METHOD_DIGEST) && ((retry >= REQUEST_RETRY && httpCode == 401 && !soap.user.empty()) || (soap.status == 401 && !soap.user.empty())))
        {
            credentials = soap.user + ":" + soap.password;
            errCode = curl_easy_setopt(curl, CURLOPT_USERPWD, credentials.c_str());
            CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
        }

        // Run our HTTP GET command, capture the HTTP response code, and clean up.
        code = curl_easy_perform(curl);
        if(code == CURLE_OK)
        {
            curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
            m_httpErrorCode = (int)httpCode;
            m_httpErrorString = "No error";
            if (httpCode == 200)
            {
                ret = 0;
                outData = *httpData.get();
                break;
            }
            else
            {
                LOG(error) << "Couldn't GET from " << soap.url << ", method:" << soap.method << ", Http errorCode: "<< httpCode << endl;
                if (httpCode == 400 || httpCode == 401)
                {
                    m_httpErrorString = "Camera has not authorized, please set credentials";
                }
                else
                {
                    m_httpErrorString = "Camera communication failed";
                }
                ret = -1;
            }
        }
        else
        {
            if (!soap.jsonData.empty())
            {
                LOG(error) << "Error in json curl" << endl;
                LOG(error) << "CURL Request failed: " << code << " : " << curl_easy_strerror(code) << " httpCode: " << httpCode << std::endl;
                ret = -1;
                break;
            }

            httpCode = CAMERA_CAMERA_REQUEST_TIMEOUT;
            LOG(error) << "CURL Request failed: " << code << " : " << curl_easy_strerror(code) << " httpCode: " << httpCode << ", url:" << soap.url << ", method:" << soap.method << endl;
            m_httpErrorCode = httpCode; // Assume that device is offline
            m_httpErrorString = CAMERA_CAMERA_REQUEST_TIMEOUT_MSG;
            ret = -1;
            break;
        }

        retry++;
        if (httpCode == 401 && soap.user.empty() == false && retry <= REQUEST_RETRY && soap.authMethod == AUTH_METHOD_DIGEST)
        {
            ret = 0;
            httpData->clear();

            LOG(info) << "CURL request retry:" << retry << " " << soap.url.c_str() << " soap.authMethod:" << soap.authMethod << " method:" << soap.method << " username: " << maskSensitiveData(soap.user, MaskType::USERNAME) << " httpCode:" << httpCode << " curl:" << curl << endl;
        }
        else
        {
            break;
        }
    }

    if(headers)
    {
        curl_slist_free_all(headers);
        headers = nullptr;
    }

    return ret;
}

static int createAndSendCameraConfigurationAPIRequest(const string& url, const string& username,
                                                      const string& password, const string& inData, string& outData)
{
    CURLcode errCode = CURLE_OK;
    CURL* curl = curl_easy_init();
    if(!curl)
    {
        cout << "Curl initialzation failed" <<endl;
        return -1;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    struct curl_slist *headers = nullptr;
    headers = curl_slist_append(headers, "Expect:");
    headers = curl_slist_append(headers, "Content-Type: text/xml; charset=utf-8");
    //headers = curl_slist_append(headers, "Content-Type: application/soap+xml; charset=utf-8");
    headers = curl_slist_append(headers, "SOAPAction:  \"http://VideoOS.Net/ConfigurationService/IConfigurationService/GetItem\"");
    headers = curl_slist_append(headers, "Accept: text/plain"); // Example output easier to read as plain text.
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    if (!inData.empty())
    {
        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, inData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, inData.size());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
        //cout << "Posting XML: " << inData << endl;
    }

    errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, (long)CURLAUTH_BASIC);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_USERNAME, username.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_PASSWORD, password.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

#ifdef DEBUG
    struct curlData config;
    curl_easy_setopt(curl, CURLOPT_DEBUGFUNCTION, my_trace);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    curl_easy_setopt(curl, CURLOPT_DEBUGDATA, &config);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    curl_easy_setopt(curl, CURLOPT_VERBOSE, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
#endif

    // Response information.
    long httpCode(0);
    unique_ptr<string> httpData(new string());

    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    CURL_CHECK_ERROR(curl_easy_perform, errCode, -1)

    errCode = curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
    CURL_CHECK_ERROR(curl_easy_getinfo, errCode, -1)

    curl_easy_cleanup(curl);

    if (httpCode == 200)
    {
        //cout << "\nGot successful response from " << url << endl << httpCode << *httpData.get() << endl;
        outData = *httpData.get();
    }
    else
    {
        cout << "Couldn't GET from " << url << " - exiting " << "Error code: "<< httpCode << endl;
        return -1;
    }

    return 0;
}

int NvSoap::sendProbe(map<string, SensorInfo>& deviceList)
{
    struct sockaddr_in groupSock;
    int sd = -1, max_sd;
    int port = PROBE_PORT;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    fd_set readfds;
    struct timeval      timeout;
    DeviceConfig& config = GET_CONFIG();
    timeout.tv_sec  = config.sensor_discovery_timeout;
    timeout.tv_usec = 0;
    vector<string> net_interfaces = config.sensor_discovery_interfaces;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    if (net_interfaces.empty())
    {
        net_interfaces.push_back("INADDR_ANY");
    }
    for (unsigned pt = 0; pt < m_probePort.size(); pt++)
    {
        sd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, IPPROTO_UDP);
        if(sd < 0)
        {
            LOG(error) << "Opening datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Opening the datagram socket...OK." << endl;

        /*
        * Disable loopback so you do not receive your own datagrams.
        */
        char loopch=0;
        if (setsockopt(sd, IPPROTO_IP, IP_MULTICAST_LOOP, (char *)&loopch, sizeof(loopch)) < 0)
        {
            LOG(error) << "setting IP_MULTICAST_LOOP:" << endl;
            ret = -1;
            goto cleanup;
        }

        if (net_interfaces[pt] != "INADDR_ANY")
        {
            struct ifreq ifr;
            memset(&ifr, 0, sizeof(ifr));
            memcpy(ifr.ifr_name, net_interfaces[pt].c_str(), sizeof(ifr.ifr_name));
            if ((setsockopt(sd, SOL_SOCKET, SO_BINDTODEVICE, (void *)&ifr, sizeof(ifr))) < 0)
            {
                LOG(error) << "Server-setsockopt() error for SO_BINDTODEVICE: "<< strerror(errno) << endl;
                ret = -1;
                goto cleanup;
            }
        }

        struct sockaddr_in localSock;
        memset((char *) &localSock, 0, sizeof(localSock));
        localSock.sin_family = AF_INET;
        localSock.sin_port = htons(port);
        localSock.sin_addr.s_addr = INADDR_ANY;

        if(bind(sd, (struct sockaddr*)&localSock, sizeof(localSock)))
        {
            LOG(error) << "Binding datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Binding datagram socket...OK." << endl;

        {
            int reuse = 1;
            if(setsockopt(sd, SOL_SOCKET, SO_REUSEADDR, (char *)&reuse, sizeof(reuse)) < 0)
            {
                LOG(error) << "Setting SO_REUSEADDR error" << endl;
                goto cleanup;
            }
            else
            LOG(verbose) << "Setting SO_REUSEADDR...OK." << endl;
        }

        memset((char *) &groupSock, 0, sizeof(groupSock));
        groupSock.sin_family = AF_INET;
        groupSock.sin_addr.s_addr = inet_addr(mcast_ip.c_str());
        groupSock.sin_port = htons(port);

        xmlout = composeProbeXml();
        LOG(verbose2) << xmlout << endl;

        struct ip_mreq group;
        group.imr_multiaddr.s_addr = inet_addr(mcast_ip.c_str());
        group.imr_interface.s_addr = htonl( INADDR_ANY );;
        if(setsockopt(sd, IPPROTO_IP, IP_ADD_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
        {
            LOG(error) << "Adding multicast group error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Adding multicast group...OK." << endl;

        if(sendto(sd, xmlout.c_str(), xmlout.size(), 0, (struct sockaddr*)&groupSock, sizeof(groupSock)) < 0)
        {
            LOG(error) << "Sending datagram message error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        LOG(verbose) << "Sending datagram message...OK" << endl;

        max_sd = sd;
        FD_ZERO(&readfds);
        FD_SET(max_sd, &readfds);
        while(true)
        {
            struct sockaddr_in cliaddr;
            memset(&cliaddr, 0, sizeof(cliaddr));
            int n;
            socklen_t length;
            length = sizeof(cliaddr);
            int buffer_len = 1024 * 10;
            char buffer[buffer_len];
            LOG(verbose) << "Checking data from socket (select)..." << endl;
            int rc = select(max_sd + 1, &readfds, nullptr, nullptr, &timeout);
            if (rc < 0 )
            {
                LOG(error) << "Select on get probeMatch failed" << endl;
                break;
            }
            if (rc == 0 )
            {
                LOG(error) << "Select on get probeMatch timeout" << endl;
                break;
            }
            LOG(verbose) << "Reading data from socket (recvfrom)..." << endl;
            n = recvfrom(sd, (char *)buffer, buffer_len,
                    MSG_WAITALL, ( struct sockaddr *) &cliaddr,
                    &length);
            if (n < 0)
            {
                if (errno != EINTR)
                {
                    perror("recvfrom");
                }
                goto cleanup;
            }
            buffer[n] = '\0';
            string match (buffer);
            LOG(verbose) << "probe Match: " << getCurrentTime() << endl << match << endl;

            g_probeMatchMutex.lock();
            SensorInfo sensor;
            if (getProbeResponse(match, sensor))
            {
                deviceList[sensor.id] = sensor;
            }
            else
            {
                g_probeMatchMutex.unlock();
                break;
            }
            g_probeMatchMutex.unlock();
        }
    }
cleanup:
    if (sd != -1)
    {
        close(sd);
        sd = -1;
    }
    return ret;
}

bool NvSoap::ping(SensorInfo& sensor)
{
    return sendProbeToDevice(sensor, true) == 0;
}

int NvSoap::sendProbeToDevice(SensorInfo& sensor, bool ping)
{
    int ret = -1;
    if (sensor.ip.empty())
    {
        LOG(error) << "Device IP is empty" << endl;
        return -1;
    }
    LOG(info) << "Device IP: " << sensor.ip << endl;

    if (openProbe() != 0)
    {
        return -1;
    }

    if (sendProbe(sensor.ip) == 0)
    {
        ret = getProbeMatch(sensor);
    }

    closeProbe();

    return ret;
}

int NvSoap::sendProbe(const string& inIpAddress)
{
    int sd;
    struct sockaddr_in groupSock;
    string xmlout;
    int ret = 0;
    const char* ip = inIpAddress.empty() ? PROBE_IP : inIpAddress.c_str();
    int port = PROBE_PORT;

    std::lock_guard<std::mutex> lock(g_probeMutex);

    if (m_probePort.empty())
    {
        LOG(error) << "Probe port is still not opened" << endl;
        return -1;
    }
    for (unsigned pt = 0; pt < m_probePort.size(); pt++)
    {
        sd = m_probePort[pt];
        LOG(verbose) << "PROBE PORT FD : " << sd << " ip: "<< ip << endl;
        if (!inIpAddress.empty())
        {
            struct ip_mreq group;
            if(setsockopt(sd, IPPROTO_IP, IP_DROP_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
            {
                LOG(error) << "Error in Dropping multicast group " << endl;
            }
            else
            {
                LOG(verbose) << "Dropping multicast group...OK." << endl;
            }
        }

        memset((char *) &groupSock, 0, sizeof(groupSock));
        groupSock.sin_family = AF_INET;
        groupSock.sin_addr.s_addr = inet_addr(ip);
        groupSock.sin_port = htons(port);

        xmlout = composeProbeXml();
        LOG(verbose2) << xmlout << endl;

        if(sendto(sd, xmlout.c_str(), xmlout.size(), 0, (struct sockaddr*)&groupSock, sizeof(groupSock)) < 0)
        {
            LOG(error) << "Sending datagram message error" << endl;
            ret = -1;
        }
        else
        {
            LOG(verbose) << "Sending datagram message...OK" << endl;
        }
    }

    return ret;
}

static bool checkIfValidInterface(vector<string> user_list)
{
    bool validIface = false;
    vector<string> nw_list = getNwInterfaceList();
    for (auto iface : user_list)
    {
        vector<string>::iterator it;
        it = std::find (nw_list.begin(), nw_list.end(), iface);
        if (it != nw_list.end())
        {
            validIface = true;
        }
    }
    return validIface;
}

int NvSoap::openProbe()
{
    int sd = -1;
    int port = PROBE_PORT;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    ifconf ifc;
    ifreq* item;
    char buf[1024];
    string iface_ipAddr = to_string(INADDR_ANY);
    bool iface_found = false;

    DeviceConfig& config = GET_CONFIG();
    vector<string> net_interfaces = config.sensor_discovery_interfaces;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    if (net_interfaces.empty() || !checkIfValidInterface(net_interfaces))
    {
        LOG(error) << "Either interface list is empty or wrong interfaces provided, use INADDR_ANY" << endl;
        net_interfaces.clear();
        net_interfaces.push_back("INADDR_ANY");
        iface_found = true;
    }
    for (unsigned pt = 0; pt < net_interfaces.size(); pt++)
    {
        sd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, IPPROTO_UDP);
        if(sd < 0)
        {
            LOG(error) << "Opening datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Opening the datagram socket...OK." << endl;
        }

        // Get the ip address of network interface.
        ifc.ifc_len = sizeof(buf);
        ifc.ifc_buf = buf;
        if(ioctl(sd, SIOCGIFCONF, &ifc) < 0)
        {
            LOG(error) << "cannot get interface list" << endl;
        }
        else
        {
            for(unsigned int i = 0; i < ifc.ifc_len / sizeof(ifreq); i++)
            {
                item = &ifc.ifc_req[i];
                if (item && item->ifr_name == net_interfaces[pt])
                {
                    iface_ipAddr = inet_ntoa(((sockaddr_in*)&item->ifr_addr)->sin_addr);
                    iface_found = true;
                    break;
                }
            }
        }

        if (iface_found == false)
        {
            LOG(error) << "Wrong network interface provided:" << net_interfaces[pt] << endl;
            continue;
        }
        iface_found = false;

        LOG(info) << "PROBE PORT FD : " << sd << ", interface:" << net_interfaces[pt] << endl;
        m_probePort.push_back(sd);

        /*
        * Disable loopback so you do not receive your own datagrams.
        */
        char loopch=0;
        if (setsockopt(sd, IPPROTO_IP, IP_MULTICAST_LOOP, (char *)&loopch, sizeof(loopch)) < 0)
        {
            LOG(error) << "setting IP_MULTICAST_LOOP:" << endl;
            ret = -1;
            goto cleanup;
        }

        if (net_interfaces[pt] != "INADDR_ANY")
        {
            struct ifreq ifr;
            memset(&ifr, 0, sizeof(ifr));
            memcpy(ifr.ifr_name, net_interfaces[pt].c_str(), sizeof(ifr.ifr_name));
            if ((setsockopt(sd, SOL_SOCKET, SO_BINDTODEVICE, (void *)&ifr, sizeof(ifr))) < 0)
            {
                LOG(error) << "Server-setsockopt() error for SO_BINDTODEVICE: "<< strerror(errno) << endl;
                ret = -1;
                goto cleanup;
            }
        }

        struct sockaddr_in localSock;
        memset((char *) &localSock, 0, sizeof(localSock));
        localSock.sin_family = AF_INET;
        localSock.sin_port = htons(port);
        localSock.sin_addr.s_addr = INADDR_ANY;

        if(bind(sd, (struct sockaddr*)&localSock, sizeof(localSock)))
        {
            LOG(error) << "Binding datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Binding datagram socket...OK." << endl;
        }

        int reuse = 1;
        if(setsockopt(sd, SOL_SOCKET, SO_REUSEADDR, (char *)&reuse, sizeof(reuse)) < 0)
        {
            LOG(error) << "Setting SO_REUSEADDR error" << endl;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Setting SO_REUSEADDR...OK." << endl;
        }

        struct ip_mreq group;
        group.imr_multiaddr.s_addr = inet_addr(PROBE_IP);
        group.imr_interface.s_addr = inet_addr(iface_ipAddr.c_str());
        if(setsockopt(sd, IPPROTO_IP, IP_ADD_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
        {
            LOG(error) << "Error in Adding multicast group " << endl;
        }
        else
        {
            LOG(verbose) << "Adding multicast group ...OK. for interface:" << net_interfaces[pt] << endl;
        }
    }
    // Dummy pipe desciptor to terminate the listener thread.
    if (pipe(fdCtrl) < 0)
    {
        perror("pipe error");
        LOG(error) << "Failed to create pipe fdCtrl" << endl;
    }
    return ret;
cleanup:
    if (sd != -1)
    {
        close(sd);
        sd = -1;
    }
    return ret;
}

int NvSoap::getProbeMatch(SensorInfo& sensor)
{
    int max_sd;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    fd_set readfds;
    struct timeval      timeout;
    DeviceConfig& config = GET_CONFIG();
    timeout.tv_sec  = config.sensor_discovery_timeout;
    timeout.tv_usec = 0;

    if (m_probePort.empty())
    {
        LOG(error) << "Probe port is still not opened" << endl;
        return -1;
    }

    FD_ZERO(&readfds);
    max_sd = fdCtrl[0];
    for (auto sd : m_probePort)
    {
        FD_SET(sd, &readfds);
        if (sd > max_sd)
            max_sd = sd;
    }
    FD_SET(fdCtrl[0], &readfds);
    {
        struct sockaddr_in cliaddr;
        memset(&cliaddr, 0, sizeof(cliaddr));
        int n;
        socklen_t length;
        length = sizeof(cliaddr);
        int buffer_len = 1024 * 10;
        char buffer[buffer_len];
        LOG(verbose) << "Checking data from socket (select)..." << endl;
        int rc = select(max_sd + 1, &readfds, nullptr, nullptr, &timeout);
        if (rc < 0 )
        {
            LOG(error) << "Select on get probeMatch failed" << endl;
            ret = -1;
            goto cleanup;
        }
        if (rc == 0 )
        {
            LOG(error) << "Select on get probeMatch timeout: " << endl;
            goto cleanup;
        }

        // Check if exit message is received from pipe.
        if (FD_ISSET(fdCtrl[0], &readfds))
        {
            int msg_len = 1024;
            char message[msg_len];
            if (read(fdCtrl[0], message, msg_len) < 0)
            {
                LOG(error) << "read from pipe failed" << endl;
                return -1;
            }
            else
            {
                string objMsg (message);
                if (objMsg == EXIT_MESSAGE)
                {
                    LOG(info) << "Received exit message, terminate the thread" << endl;
                    close(fdCtrl[0]);
                    close(fdCtrl[1]);
                    return -1;
                }
            }
        }

        int sd_set = -1;
        for (auto sd : m_probePort)
        {
            if (FD_ISSET(sd, &readfds))
            {
                sd_set = sd;
                break;
            }
        }

        LOG(verbose) << "Reading data from socket (recvfrom)..." << endl;
        n = recvfrom(sd_set, (char *)buffer, buffer_len,
                MSG_WAITALL, ( struct sockaddr *) &cliaddr,
                &length);
        if (n < 0)
        {
			if (errno != EINTR)
            {
				perror("recvfrom");
            }
            goto cleanup;
        }
        buffer[n] = '\0';
        string match (buffer);
        LOG(verbose) << "probe Match: " << getCurrentTime() << endl << match << endl;

        if (getProbeResponse(match, sensor) == false)
        {
            ret = -1;
        }
    }
cleanup:
    return ret;
}

int NvSoap::synchronizeDeviceTime(nvsoap_& soap)
{
    int response = 0;
    string device_time;

    if (soap.device_url.empty() || soap.user.empty() || soap.password.empty())
    {
        // Setting Api's need security header.
        return response;
    }
    response = GetSystemDateAndTime(soap, device_time);
    if (response == 0)
    {
        string currentTimeSec = getCurrentUtcTime();
        uint32_t timediff_sec = std::abs((getEpocTimeInMS(currentTimeSec) / 1000) - (getEpocTimeInMS(device_time) / 1000));
        if (timediff_sec > DEVICE_AND_CLIENT_TIME_DIFFERENCE_SEC)
        {
            LOG(info) << "Time difference is observed, synchronizing it..." << endl;
            LOG(info) << "Device:" << soap.device_url << ", device_time:" << device_time << ", system_time:" << currentTimeSec << endl;

            DeviceTimeInfo timeInfo;
            timeInfo.enableNTP = false;
            timeInfo.dayLightSavings = false;
            timeInfo.utcTime = getCurrentTimeInHHMMSS();
            timeInfo.date = getCurrentDateInDDMMYYYY();
            if (setSystemDateAndTime(soap, timeInfo))
            {
                LOG(info) << "setSystemDateAndTime failed for sensor:" << soap.device_url << endl;
                return -1;
            }

#if 0
            string ntpServer;
            // Set the NTP server if it is not set
            if (GetNTP(soap, ntpServer) == 0)
            {
                LOG(info) << "GetNTP response ntpServer:" << ntpServer << endl;
                if (ntpServer.empty() || pingHostname(ntpServer) == false)
                {
                    string setNtpServer;
                    DeviceNTPInfo ntpInfo;
                    for (auto ntp : GET_CONFIG().ntpServers)
                    {
                        if (pingHostname(ntp))
                        {
                            setNtpServer = ntp;
                            break;
                        }
                    }

                    // Set default public ntp-server in case not provided by user.
                    if (setNtpServer.empty())
                    {
                        setNtpServer = NTP_DEFAULT_SERVER;
                    }

                    // Check if ntp-server is ip_address or dnsName.
                    if (validateIpAddress(setNtpServer) == true)
                    {
                        ntpInfo.type = "IPv4";
                        ntpInfo.ipv4Addr = setNtpServer;
                    }
                    else
                    {
                        ntpInfo.type = "DNS";
                        ntpInfo.dnsName = setNtpServer;
                    }

                    LOG(info) << "Current NTP-server '" << ntpServer << "' is empty or not reachable, Setting NTP-server:" << setNtpServer << endl;
                    ntpInfo.fromDHCP = false;
                    setNTP(soap, ntpInfo);
                }
            }
#endif
        }
    }
    return response;
}

// ===== Profile G - Recording Search APIs Implementation =====

int NvSoap::GetRecordingSummary(nvsoap_& soap, RecordingSummary& summary)
{
    int ret = -1;
    string out;

    soap.method = "GetRecordingSummary";
    soap.wsdl = ONVIF_SEARCH_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetRecordingSummaryXml);

    LOG(verbose2) << "GetRecordingSummary request: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)

    if (ret == 0)
    {
        LOG(verbose2) << "GetRecordingSummary Result: " << out << endl;
        summary = getRecordingSummaryResponse(out);
    }

    return ret;
}

int NvSoap::FindRecordings(nvsoap_& soap, const RecordingSearchScope& scope,
                          int maxMatches, const string& keepAliveTime, string& searchToken)
{
    int ret = -1;
    string out;

    soap.method = "FindRecordings";
    soap.wsdl = ONVIF_SEARCH_SERVICE_NAMESPACE;

    // Store search parameters in userData for XML composition
    soap.userData.clear();
    for (size_t i = 0; i < scope.includedSources.size(); i++)
    {
        soap.userData["IncludedSource_" + to_string(i)] = scope.includedSources[i];
    }
    soap.userData["RecordingInformationFilter"] = scope.recordingInformationFilter;

    // Only add MaxMatches if it's > 0 (optional parameter)
    if (maxMatches > 0)
    {
        soap.userData["MaxMatches"] = to_string(maxMatches);
    }

    soap.userData["KeepAliveTime"] = keepAliveTime;

    soap.xmlData = composeXml(soap, (void*)&composeFindRecordingsXml);

    LOG(verbose2) << "FindRecordings request: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)

    if (ret == 0)
    {
        LOG(verbose2) << "FindRecordings Result: " << out << endl;
        searchToken = getFindRecordingsResponse(out);
    }

    soap.userData.clear();
    return ret;
}

int NvSoap::GetRecordingSearchResults(nvsoap_& soap, const string& searchToken,
                                     int minResults, int maxResults,
                                     const string& waitTime, RecordingSearchResults& results)
{
    int ret = -1;
    string out;

    soap.method = "GetRecordingSearchResults";
    soap.wsdl = ONVIF_SEARCH_SERVICE_NAMESPACE;

    // Store search parameters in userData for XML composition
    soap.userData.clear();
    soap.userData["SearchToken"] = searchToken;
    soap.userData["MinResults"] = to_string(minResults);
    soap.userData["MaxResults"] = to_string(maxResults);
    soap.userData["WaitTime"] = waitTime;

    soap.xmlData = composeXml(soap, (void*)&composeGetRecordingSearchResultsXml);

    LOG(verbose2) << "GetRecordingSearchResults request: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)

    if (ret == 0)
    {
        LOG(verbose2) << "GetRecordingSearchResults Result: " << out << endl;
        results = getRecordingSearchResultsResponse(out);
    }

    soap.userData.clear();
    return ret;
}

int NvSoap::EndSearch(nvsoap_& soap, const string& searchToken)
{
    int ret = -1;
    string out;

    soap.method = "EndSearch";
    soap.wsdl = ONVIF_SEARCH_SERVICE_NAMESPACE;

    // Store search token in userData for XML composition
    soap.userData.clear();
    soap.userData["SearchToken"] = searchToken;

    soap.xmlData = composeXml(soap, (void*)&composeEndSearchXml);

    LOG(verbose2) << "EndSearch request: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)

    if (ret == 0)
    {
        LOG(verbose2) << "EndSearch Result: " << out << endl;
    }

    soap.userData.clear();
    return ret;
}

int NvSoap::stopOnvifListenerThread()
{
    int result = 0;
    string bye_message = EXIT_MESSAGE;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    result = write (fdCtrl[1], bye_message.c_str(), sizeof(bye_message));
    if (result < 0)
    {
        LOG(error) << ("writting by-message failed") << endl;
        return -1;
    }
    return 0;
}

static int composeGetServicesMethodXml(xmlTextWriterPtr& writer, nvsoap_& soap)
{
    int rc;
    _xmlTextWriterStartElement_(writer, BAD_CAST soap.method.c_str());
    if (soap.wsdl.empty() == false)
    {
        rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST soap.wsdl.c_str());
        if (rc < 0)
        {
            LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
            return -1;
        }
    }
    {
        _xmlTextWriterStartElement_(writer, BAD_CAST "IncludeCapability");
        _xmlTextWriterWriteString_(writer, BAD_CAST "true");
        _xmlTextWriterEndElement_(writer);
    }

    _xmlTextWriterEndElement_(writer);
    return 0;
}

void NvSoap::getServicesResponse(const string& xmlData, map<string, OnvifServiceInfo>& caps)
{
    xmlDocPtr   doc;
    OnvifServiceInfo serviceInfo;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "Service");
        while (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "Namespace");
            if (cur_)
            {
                serviceInfo.name_space = getNodeValue(doc, cur_);
            }

            cur_ = findNode(doc, cur, "XAddr");
            if (cur_)
            {
                serviceInfo.url = getNodeValue(doc, cur_);
            }

            if (serviceInfo.name_space.empty() || serviceInfo.url.empty())
            {
                serviceInfo.name_space.clear();
                serviceInfo.url.clear();
                cur = cur->next;
                continue;
            }

            LOG(info) << "Supported Namespace:" << serviceInfo.name_space << " xAddr:" << serviceInfo.url << endl;

            if (serviceInfo.name_space == ONVIF_DEVICE_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps["Device"] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_MEDIA2_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_MEDIA2_SERVICE] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_MEDIA_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_MEDIA_SERVICE] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_PTZ_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_PTZ_SERVICE] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_IMAGING_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_IMAGING_SERVICE] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_REPLAY_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_REPLAY_SERVICE] = serviceInfo;
                }
            }
            else if (serviceInfo.name_space == ONVIF_SEARCH_SERVICE_NAMESPACE)
            {
                if (!serviceInfo.url.empty())
                {
                    caps[ONVIF_SEARCH_SERVICE] = serviceInfo;
                }
            }

            serviceInfo.name_space.clear();
            serviceInfo.url.clear();

            cur = cur->next;
        }
    }
    xmlFreeDoc(doc);
    return;
}

int NvSoap::GetServices(nvsoap_& soap, map<string, OnvifServiceInfo>& caps)
{
    string out;
    soap.method = "GetServices";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetServicesMethodXml);
    int ret = createAndSendRequest(soap, out);
    PRINT_XML_REQUEST_IF_ERROR(ret, soap.xmlData)
    getServicesResponse(out, caps);
    return ret;
}

SensorEncoderSettingsOptions NvSoap::getVideoEncoderConfigurationOptionsMedia2Response(const string& xmlData)
{
    xmlDocPtr    doc;
    string value;
    SensorEncoderSettingsOptions options;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return options;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "Options");
        while (cur)
        {
            if (!xmlStrcmp(cur->name, (const xmlChar *)"Options"))
            {
                VideoEncoderConfigurationsOptions option;
                xmlAttr* attribute = cur->properties;
                {
                    while (attribute && attribute->name && attribute->children)
                    {
                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "GovLengthRange"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);

                            std::string govRange = (char*)value;
                            std::istringstream iss(govRange);
                            iss >> option.GovLengthRange.min;
                            iss >> option.GovLengthRange.max;
                            xmlFree(value);
                        }

                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "FrameRatesSupported"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);

                            option.FrameRateSupported = (char*)value;
                            xmlFree(value);
                        }

                        if (!xmlStrcmp(attribute->name, (const xmlChar *) "ProfilesSupported"))
                        {
                            xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);

                            std::string profilesSupported = (char*)value;
                            std::istringstream iss(profilesSupported);

                            std::string profile;
                            while (iss >> profile)
                            {
                                option.profilesSupported.push_back(profile);
                            }
                            xmlFree(value);
                        }

                        attribute = attribute->next;
                    }
                }
                xmlNodePtr cur_ = findNode(doc, cur, "Encoding");
                if (cur_)
                {
                    option.encoding = getNodeValue(doc, cur_);
                }

                cur_ = findNode(doc, cur, "QualityRange");
                if (cur_)
                {
                    xmlNodePtr cur__ = findNode(doc, cur_, "Min");
                    if (cur__)
                    {
                        option.qualityRange.min = getNodeValue(doc, cur__);
                    }
                    cur__ = findNode(doc, cur_, "Max");
                    if (cur__)
                    {
                        option.qualityRange.max = getNodeValue(doc, cur__);
                    }
                }

                cur_ = findNode(doc, cur, "ResolutionsAvailable");
                if (cur_)
                {
                    vector <Resolution>& resolution = option.ResolutionsAvailable;
                    while(cur_)
                    {
                        Resolution res;
                        xmlNodePtr cur__ = findNode(doc, cur_, "Width");
                        if (cur__)
                        {
                            res.width = getNodeValue(doc, cur__);

                        }
                        cur__ = findNode(doc, cur_, "Height");
                        if (cur__)
                        {
                            res.height = getNodeValue(doc, cur__);
                        }
                        if (isDuplicateEntry(resolution, res) == false && (res.width.empty() == false && res.height.empty() == false))
                        {
                            resolution.push_back(res);
                        }
                        cur_ = cur_->next;
                    }
                }

                cur_ = findNode(doc, cur, "BitrateRange");
                if (cur_)
                {
                    xmlNodePtr cur__ = findNode(doc, cur_, "Min");
                    if (cur__)
                    {
                        option.BitrateRange.min = getNodeValue(doc, cur__);
                    }
                    cur__ = findNode(doc, cur_, "Max");
                    if (cur__)
                    {
                        option.BitrateRange.max = getNodeValue(doc, cur__);
                    }
                }

                if(option.GovLengthRange.min.empty() || option.GovLengthRange.max.empty())
                {
                    cur_ = findNode(doc, cur, "GovLengthRange");
                    if (cur_)
                    {
                        std::string govRange = getNodeValue(doc, cur_);
                        std::istringstream iss(govRange);
                        iss >> option.GovLengthRange.min;
                        iss >> option.GovLengthRange.max;
                    }
                }

                if(option.FrameRateSupported.empty())
                {
                    cur_ = findNode(doc, cur, "FrameRatesSupported");
                    if (cur_)
                    {
                        option.FrameRateSupported = getNodeValue(doc, cur_);
                    }
                }

                if(option.profilesSupported.empty())
                {
                    cur_ = findNode(doc, cur, "ProfilesSupported");
                    if (cur_)
                    {
                        std::string profilesSupported = getNodeValue(doc, cur_);
                        std::istringstream iss(profilesSupported);

                        std::string profile;
                        while (iss >> profile)
                        {
                            option.profilesSupported.push_back(profile);
                        }
                    }
                }

                options.encoderSettingsOptions.push_back(option);
                options.videoEncodingSupported.push_back(option.encoding);
            }

            cur = cur->next;
        }

        LOG(verbose) << "--------------------------------------------------------" << endl;
        for (auto encodings: options.videoEncodingSupported)
        {
            LOG(verbose) << "videoEncodingSupported:" << encodings << endl;
        }
        for (auto videoEncoderConfigurationsOptions : options.encoderSettingsOptions)
        {
            LOG(verbose) << "encoding:" << videoEncoderConfigurationsOptions.encoding << endl;
            LOG(verbose) << "FrameRateSupported:" << videoEncoderConfigurationsOptions.FrameRateSupported << endl;
            for (auto Resolution: videoEncoderConfigurationsOptions.ResolutionsAvailable)
            {
                LOG(verbose) << "Resolution width:" << Resolution.width << " height:" << Resolution.height << endl;
            }
            for (auto profilesSupported : videoEncoderConfigurationsOptions.profilesSupported)
            {
                LOG(verbose) << "profilesSupported:" << profilesSupported << endl;
            }
            LOG(verbose) << "qualityRange min:" << videoEncoderConfigurationsOptions.qualityRange.min << " max:" << videoEncoderConfigurationsOptions.qualityRange.max << endl;
            LOG(verbose) << "bitrateRange min:" << videoEncoderConfigurationsOptions.BitrateRange.min << " max:" << videoEncoderConfigurationsOptions.BitrateRange.max << endl;
            LOG(verbose) << "EncodingIntervalRange: min:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.min << " max:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.max << endl;
            LOG(verbose) << "GovLengthRange min:" << videoEncoderConfigurationsOptions.GovLengthRange.min << " max:" << videoEncoderConfigurationsOptions.GovLengthRange.max << endl;
        }
        LOG(verbose) << "-----------------------------------------------" << endl;
    }

    xmlFreeDoc(doc);
    return options;
}

SensorVideoEncoderSettingsValues NvSoap::getVideoEncoderConfigurationsMedia2Response(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    string value;
    SensorVideoEncoderSettingsValues values;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return values;
    }

    Token token;
    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "Configurations");
    if (cur)
    {
        xmlAttr* attribute = cur->properties;
        {
            while (attribute && attribute->name && attribute->children)
            {
                if (!xmlStrcmp(attribute->name, (const xmlChar *) "token"))
                {
                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                    token.profileToken = (char*)value;
                    xmlFree(value);
                }
                else if (!xmlStrcmp(attribute->name, (const xmlChar *) "GovLength"))
                {
                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                    values.govLength = (char*)value;
                    xmlFree(value);
                }
                else if (!xmlStrcmp(attribute->name, (const xmlChar *) "Profile"))
                {
                    xmlChar* value = xmlNodeListGetString(doc, attribute->children, 1);
                    values.encodingProfile = (char*)value;
                    xmlFree(value);
                }
                attribute = attribute->next;
            }
        }

        xmlNodePtr cur_ = findNode(doc, cur, "Name");
        if (cur_)
        {
            token.profileName = getNodeValue(doc, cur_);
        }

        cur_ = findNode(doc, cur, "Encoding");
        if (cur_)
        {
            values.encoding = getNodeValue(doc, cur_);
        }

        cur_ = findNode(doc, cur, "Resolution");
        if (cur_)
        {
            string w;
            xmlNodePtr cur___ = findNode(doc, cur_, "Width");
            if (cur___)
            {
                values.resolution.width = getNodeValue(doc, cur___);
            }
            string h;
            cur___ = findNode(doc, cur_, "Height");
            if (cur___)
            {
                values.resolution.height = getNodeValue(doc, cur___);
            }
        }
        cur_ = findNode(doc, cur, "RateControl");
        if (cur_)
        {
            xmlNodePtr cur___ = findNode(doc, cur_, "FrameRateLimit");
            if (cur___)
            {
                values.frameRate = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "EncodingInterval");
            if (cur___)
            {
                values.encodingInterval = getNodeValue(doc, cur___);
            }
            cur___ = findNode(doc, cur_, "BitrateLimit");
            if (cur___)
            {
                values.bitrate = getNodeValue(doc, cur___);
            }
        }
        cur_ = findNode(doc, cur, "Quality");
        if (cur_)
        {
            values.quality = getNodeValue(doc, cur_);
        }

        if (values.govLength.empty())
        {
            cur_ = findNode(doc, cur, "GovLength");
            if (cur_)
            {
                values.govLength = getNodeValue(doc, cur_);
            }
        }

        if (values.encodingProfile.empty())
        {
            cur_ = findNode(doc, cur, "Profile");
            if (cur_)
            {
                values.encodingProfile = getNodeValue(doc, cur_);
            }
        }
    }

    LOG(verbose) << "--------------------------------------------------------" << endl;
    LOG(verbose) << "encoding: " << values.encoding << endl;
    LOG(verbose) << "resolution width:" << values.resolution.width << " height:" << values.resolution.height << endl;
    LOG(verbose) << "frameRate: " << values.frameRate << endl;
    LOG(verbose) << "bitrate: " << values.bitrate << endl;
    LOG(verbose) << "encodingInterval: " << values.encodingInterval << endl;
    LOG(verbose) << "encodingProfile: " << values.encodingProfile << endl;
    LOG(verbose) << "quality: " << values.quality << endl;
    LOG(verbose) << "govLength: " << values.govLength << endl;
    LOG(verbose) << "--------------------------------------------------------" << endl;

    xmlFreeDoc(doc);
    return values;
}

ServiceCapabilities NvSoap::getServiceCapabilitiesResponse(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;
    ServiceCapabilities capabilities;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return capabilities;
    }

    Token token;
    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);

    // Find the Security node
    xmlNodePtr securityNode = findNode(doc, cur, (const char *)"Security");
    if (securityNode != nullptr)
    {
        xmlChar *value = xmlGetProp(securityNode, (const xmlChar *)"UsernameToken");
        if (value && strcmp((char *)value, "true") == 0)
        {
            capabilities.supportedAuthMethods = static_cast<AuthenticationMethods>(static_cast<int>(capabilities.supportedAuthMethods)
            | static_cast<int>(AuthenticationMethods::AUTH_METHOD_USERNAME_TOKEN));
            xmlFree(value);
        }
        value = xmlGetProp(securityNode, (const xmlChar *)"HttpDigest");
        if (value && strcmp((char *)value, "true") == 0)
        {
            capabilities.supportedAuthMethods = static_cast<AuthenticationMethods>(static_cast<int>(capabilities.supportedAuthMethods)
            | static_cast<int>(AuthenticationMethods::AUTH_METHOD_DIGEST));
            xmlFree(value);
        }
        value = xmlGetProp(securityNode, (const xmlChar *)"HashingAlgorithms");
        if (value)
        {
            capabilities.supportedHashingAlgorithms = std::string(reinterpret_cast<const char*>(value));
            xmlFree(value);
        }

        LOG(info) << "---------------------Security Service capabilities---------------------"<< endl;
        LOG(info) << "supportedAuthMethods:" << capabilities.supportedAuthMethods << endl;
        LOG(info) << "supportedHashingAlgorithms:" << capabilities.supportedHashingAlgorithms << endl;
        LOG(info) << "---------------------Security Service capabilities---------------------"<< endl;
    }
    else
    {
        LOG(error) << "Security node not found" << endl;
    }

    // Free the document
    xmlFreeDoc(doc);

    return capabilities;
}

// ===== Profile G - Recording Search Response Parsing Methods =====

RecordingSummary NvSoap::getRecordingSummaryResponse(const string& xmlData)
{
    xmlDocPtr doc;
    xmlNodePtr cur;
    RecordingSummary summary;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" << endl;
        return summary;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);

    // Find GetRecordingSummaryResponse node
    xmlNodePtr responseNode = findNode(doc, cur, "GetRecordingSummaryResponse");
    if (responseNode)
    {
        // Find Summary node
        xmlNodePtr summaryNode = findNode(doc, responseNode, "Summary");
        if (summaryNode)
        {
            // Extract DataFrom
            xmlNodePtr dataFromNode = findNode(doc, summaryNode, "DataFrom");
            if (dataFromNode)
            {
                summary.dataFrom = getNodeValue(doc, dataFromNode);
            }

            // Extract DataUntil
            xmlNodePtr dataUntilNode = findNode(doc, summaryNode, "DataUntil");
            if (dataUntilNode)
            {
                summary.dataUntil = getNodeValue(doc, dataUntilNode);
            }

            // Extract NumberRecordings
            xmlNodePtr numRecordingsNode = findNode(doc, summaryNode, "NumberRecordings");
            if (numRecordingsNode)
            {
                string numStr = getNodeValue(doc, numRecordingsNode);
                summary.numberRecordings = std::stoi(numStr);
            }
        }
    }

    xmlFreeDoc(doc);
    return summary;
}

string NvSoap::getFindRecordingsResponse(const string& xmlData)
{
    xmlDocPtr doc;
    xmlNodePtr cur;
    string searchToken;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" << endl;
        return searchToken;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);

    // Find FindRecordingsResponse node
    xmlNodePtr responseNode = findNode(doc, cur, "FindRecordingsResponse");
    if (responseNode)
    {
        // Extract SearchToken
        xmlNodePtr tokenNode = findNode(doc, responseNode, "SearchToken");
        if (tokenNode)
        {
            searchToken = getNodeValue(doc, tokenNode);
        }
    }

    xmlFreeDoc(doc);
    return searchToken;
}

RecordingSearchResults NvSoap::getRecordingSearchResultsResponse(const string& xmlData)
{
    xmlDocPtr doc;
    xmlNodePtr cur;
    RecordingSearchResults results;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" << endl;
        return results;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);

    // Find GetRecordingSearchResultsResponse node
    xmlNodePtr responseNode = findNode(doc, cur, "GetRecordingSearchResultsResponse");
    if (responseNode)
    {
        // Find ResultList node
        xmlNodePtr resultListNode = findNode(doc, responseNode, "ResultList");
        if (resultListNode)
        {
            // Extract SearchState
            xmlNodePtr searchStateNode = findNode(doc, resultListNode, "SearchState");
            if (searchStateNode)
            {
                results.searchState = getNodeValue(doc, searchStateNode);
            }

            // Iterate through RecordingInformation nodes
            xmlNodePtr node = resultListNode->children;
            while (node != nullptr)
            {
                if (node->type == XML_ELEMENT_NODE &&
                    xmlStrcmp(node->name, BAD_CAST "RecordingInformation") == 0)
                {
                    RecordingInformation recInfo;

                    // Extract RecordingToken
                    xmlNodePtr tokenNode = findNode(doc, node, "RecordingToken");
                    if (tokenNode)
                    {
                        recInfo.recordingToken = getNodeValue(doc, tokenNode);
                    }

                    // Extract Source information
                    xmlNodePtr sourceNode = findNode(doc, node, "Source");
                    if (sourceNode)
                    {
                        xmlNodePtr sourceIdNode = findNode(doc, sourceNode, "SourceId");
                        if (sourceIdNode)
                        {
                            recInfo.source.sourceId = getNodeValue(doc, sourceIdNode);
                        }

                        xmlNodePtr nameNode = findNode(doc, sourceNode, "Name");
                        if (nameNode)
                        {
                            recInfo.source.name = getNodeValue(doc, nameNode);
                        }

                        xmlNodePtr locationNode = findNode(doc, sourceNode, "Location");
                        if (locationNode)
                        {
                            recInfo.source.location = getNodeValue(doc, locationNode);
                        }

                        xmlNodePtr descNode = findNode(doc, sourceNode, "Description");
                        if (descNode)
                        {
                            recInfo.source.description = getNodeValue(doc, descNode);
                        }

                        xmlNodePtr addressNode = findNode(doc, sourceNode, "Address");
                        if (addressNode)
                        {
                            recInfo.source.address = getNodeValue(doc, addressNode);
                        }
                    }

                    // Extract EarliestRecording
                    xmlNodePtr earliestNode = findNode(doc, node, "EarliestRecording");
                    if (earliestNode)
                    {
                        recInfo.earliestRecording = getNodeValue(doc, earliestNode);
                    }

                    // Extract LatestRecording
                    xmlNodePtr latestNode = findNode(doc, node, "LatestRecording");
                    if (latestNode)
                    {
                        recInfo.latestRecording = getNodeValue(doc, latestNode);
                    }

                    // Extract Content
                    xmlNodePtr contentNode = findNode(doc, node, "Content");
                    if (contentNode)
                    {
                        recInfo.content = getNodeValue(doc, contentNode);
                    }

                    // Extract Track information
                    xmlNodePtr trackSearchNode = node->children;
                    while (trackSearchNode != nullptr)
                    {
                        if (trackSearchNode->type == XML_ELEMENT_NODE &&
                            xmlStrcmp(trackSearchNode->name, BAD_CAST "Track") == 0)
                        {
                            RecordingTrack track;

                            xmlNodePtr trackTokenNode = findNode(doc, trackSearchNode, "TrackToken");
                            if (trackTokenNode)
                            {
                                track.trackToken = getNodeValue(doc, trackTokenNode);
                            }

                            xmlNodePtr trackTypeNode = findNode(doc, trackSearchNode, "TrackType");
                            if (trackTypeNode)
                            {
                                track.trackType = getNodeValue(doc, trackTypeNode);
                            }

                            xmlNodePtr trackDescNode = findNode(doc, trackSearchNode, "Description");
                            if (trackDescNode)
                            {
                                track.description = getNodeValue(doc, trackDescNode);
                            }

                            xmlNodePtr dataFromNode = findNode(doc, trackSearchNode, "DataFrom");
                            if (dataFromNode)
                            {
                                track.dataFrom = getNodeValue(doc, dataFromNode);
                            }

                            xmlNodePtr dataToNode = findNode(doc, trackSearchNode, "DataTo");
                            if (dataToNode)
                            {
                                track.dataTo = getNodeValue(doc, dataToNode);
                            }

                            LOG(verbose) << "    Track parsed - Token: " << track.trackToken
                                      << ", Type: " << track.trackType
                                      << ", DataFrom: " << track.dataFrom
                                      << ", DataTo: " << track.dataTo << endl;

                            recInfo.tracks.push_back(track);
                        }
                        trackSearchNode = trackSearchNode->next;
                    }

                    // Extract RecordingStatus
                    xmlNodePtr statusNode = findNode(doc, node, "RecordingStatus");
                    if (statusNode)
                    {
                        recInfo.recordingStatus = getNodeValue(doc, statusNode);
                    }

                    results.recordingList.push_back(recInfo);
                }
                node = node->next;
            }
        }
    }

    xmlFreeDoc(doc);
    return results;
}

int NvSoap::GetServiceCapabilities(nvsoap_& soap, ServiceCapabilities& serviceCapabilities)
{
    int ret = -1;
    string out;
    soap.method = "GetServiceCapabilities";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.xmlData = composeXml(soap, (void*)&composeGetMethodXml);
    ret = createAndSendRequest(soap, out);
    LOG(verbose) << "GetServiceCapabilities: " << out << endl;
    if (ret == 0)
    {
        serviceCapabilities = getServiceCapabilitiesResponse(out);
    }
    return ret;
}

int NvSoap::setHashingAlgorithm(nvsoap_& soap, const HashingAlgorithmInfo& algorithm)
{
    string out;
    int ret = -1;
    soap.method = "SetHashingAlgorithm";
    soap.wsdl = ONVIF_DEVICE_SERVICE_NAMESPACE;
    soap.userData2 = (void* )&algorithm;
    soap.xmlData = composeXml(soap, (void*)&composeSetHashingAlgorithmXml);
    LOG(verbose) << "SetHashingAlgorithm: " << soap.xmlData << endl;
    ret = createAndSendRequest(soap, out);
    LOG(verbose) << "SetHashingAlgorithm: " << out << endl;
    return ret;
}
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

#include "onvif_client.h"
#include "utils.h"
#include "logger.h"
#include "error_code.h"
#include "profiler.h"
#include "config.h"
#include "network_utils.h"

#include <curl/curl.h>
#include <sstream>
#include <iomanip>
#include <string.h>
#include <ctime>
#include <openssl/sha.h>
#include <algorithm>

using namespace std;
using namespace nv_vms;

constexpr const char* DEFAULT_CAMERA_NAME = "Camera";

constexpr const char* HASH_ALGORITHM_SHA_256 = "SHA-256";

// Default recording duration: approximately 5 years in milliseconds
constexpr long long DEFAULT_RECORDING_DURATION_MS = 157680000000LL;

extern "C" ISensorControlInterface* createObject()
{
    return new OnvifClient;
}

extern "C" void destroyObject( OnvifClient* object )
{
    delete object;
}

static bool isDuplicateUrl(vector<shared_ptr<StreamInfo>>streams, string url)
{
    for(uint32_t i = 0; i < streams.size(); i++)
    {
        shared_ptr<StreamInfo> stream = streams[i];
        if(stream->live_url == url)
        {
            return true;
        }
    }
    return false;
}

OnvifClient::OnvifClient()
{
}

OnvifClient::~OnvifClient()
{
    LOG(info) << "Destroying onvif client" << endl;
}

static int setHashingAlgorithmSHA256(shared_ptr<SensorInfo> sensor, nvsoap_& soap)
{
    int ret = -1;
    if (sensor.get() == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    // Check if "SHA-256" is present in the comma-separated algorithms string
    std::stringstream ss(sensor->serviceCapabilities.supportedHashingAlgorithms);
    std::string item;
    bool sha256_found = false;
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    soap.curl = clientSession->getCurlClient();

    while (std::getline(ss, item, ','))
    {
        if (item == HASH_ALGORITHM_SHA_256)
        {
            sha256_found = true;
            break;
        }
    }
    if (sha256_found)
    {
        HashingAlgorithmInfo algorithmInfo;
        algorithmInfo.algorithm = HASH_ALGORITHM_SHA_256;
        ret = clientSession->getNvSoap()->setHashingAlgorithm(soap, algorithmInfo);
        if (ret != 0)
        {
            LOG(error) << "Failed to set SHA-256 hashing algorithm for sensor:" << sensor->id << " url:" << secureUrlForLogging(sensor->url) << endl;
        }
        else
        {
            LOG(info) << "Set SHA-256 hashing algorithm successfully for sensor:" << sensor->id << " url:" << secureUrlForLogging(sensor->url) << endl;
        }
    }
    else
    {
        LOG(info) << "SHA-256 hashing algorithm is not supported for sensor:" << sensor->id << " url:" << secureUrlForLogging(sensor->url) << endl;
        ret = 0;
    }

    return ret;
}

static int getDeviceCapabilities(shared_ptr<SensorInfo> sensor)
{
    LOG(verbose) << __FUNCTION__<<endl;
    int ret = -1;
    int retGetServices = -1;
    int retGetCapabilities = -1;
    int retServiceCapabilities = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.url = soap.device_url = sensor->url;
    soap.curl = clientSession->getCurlClient();

    // Use existing authMethod if available, otherwise default to UsernameToken (1)
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    if (soap.authMethod == 0 || soap.authMethod == AUTH_METHOD_NONE)
    {
        soap.authMethod = AUTH_METHOD_USERNAME_TOKEN; // Default to UsernameToken authentication
        LOG(info) << "Using default authentication method: UsernameToken" << endl;
    }

    map<string, OnvifServiceInfo> caps;
    LOG(info) << "GetCapabilities & GetServices: " << secureUrlForLogging(sensor->url)
              << ", authMethod: " << soap.authMethod << endl;
    retGetCapabilities = clientSession->getNvSoap()->GetCapabilities(soap, caps);
    retGetServices = clientSession->getNvSoap()->GetServices(soap, caps);
    retServiceCapabilities = clientSession->getNvSoap()->GetServiceCapabilities(soap, sensor->serviceCapabilities);
    if ((retGetCapabilities == 0 || retGetServices == 0) && (retServiceCapabilities == 0))
    {
        for (auto cap : caps)
        {
            LOG(info) << cap.first << " : " << cap.second.name_space << " : " << secureUrlForLogging(cap.second.url) << endl;
        }
        sensor->serviceUrls = caps;

        sensor->serviceCapabilities.securedAuthMethod = getSecuredAuthMethod(sensor->serviceCapabilities.supportedAuthMethods);
        ret = 0;
    }
    else
    {
        LOG(error) << "(GetCapabilities & GetServices) | GetServiceCapabilities failed for sensor:" << secureUrlForLogging(sensor->url) << endl;
        sensor->updateSensorStatus(SensorStatusOffline);
        sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
    }
    sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());
    return ret;
}

static int GetSensorInfo(shared_ptr<SensorInfo>& sensor)
{
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.url = soap.device_url = sensor->url;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    map<string, string> devInfo;
    ret = clientSession->getNvSoap()->GetDeviceInformation(soap, devInfo);
    if (ret == 0)
    {
        sensor->manufacturer = devInfo["Manufacturer"];
        sensor->serial_number = devInfo["SerialNumber"];
        sensor->firmware_version = devInfo["FirmwareVersion"];
        sensor->hardware_id = devInfo["HardwareId"];
    }
    sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());
    return ret;
}

bool OnvifClient::isSensorExists(const string& id)
{
    for (auto sensor : m_cacheSensorList)
    {
        if(sensor->id == id)
        {
            return true;
        }
    }
    return false;
}

std::shared_ptr<SensorInfo> OnvifClient::getSensor(const string& id)
{
    for (auto sensor : m_cacheSensorList)
    {
        if(sensor->id == id)
        {
            return sensor;
        }
    }
    return nullptr;
}

int OnvifClient::fetchSensorStreamInfo(shared_ptr<SensorInfo> sensor)
{
    LOG(verbose) << __FUNCTION__<<endl;
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();
    sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode()); // reset code value
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    vector<shared_ptr<StreamInfo>> streams;
    vector<SensorSettings> settings;
    map<string, OnvifServiceInfo> caps = sensor->serviceUrls;
    OnvifServiceInfo serviceInfo;

    auto it = caps.find(ONVIF_MEDIA2_SERVICE);
    if (it == caps.end())
    {
        it = caps.find(ONVIF_MEDIA_SERVICE);
    }

    if (it != caps.end())
    {
        serviceInfo = it->second;
    }
    else
    {
        LOG(error) << "fetchSensorStreamInfo failed due to ONVIF service is not supported" << endl;
        return ret;
    }

    soap.url = serviceInfo.url;
    soap.name_space = serviceInfo.name_space;
    soap.device_url = sensor->url;
    StreamInfo stream_info;
    ret = clientSession->getNvSoap()->GetProfiles(soap, settings);
    if ( ret != 0)
    {
        LOG(error) << "GetProfiles failed" << endl;
        sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());
        return ret;
    }
    LOG(verbose) << "profiles size: " << settings.size() << endl;
    for (SensorSettings s : settings)
    {
        shared_ptr<StreamInfo> stream(new StreamInfo);
        stream->name = s.token.profileName;
        stream->id = s.token.profileToken;
        stream->settings = s;
        soap.token = s.token.profileToken;
        soap.url = serviceInfo.url;
        soap.name_space = serviceInfo.name_space;
        clientSession->getNvSoap()->GetMediaUri(soap, stream->live_url);
        sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());
        /* Skip push_back of camera with missing live URL */
        if(stream->live_url == "")
        {
            continue;
        }

        #ifndef RELEASE
        LOG(info) << "live_url: " << secureUrlForLogging(stream->live_url) << endl;
        #endif
        stream->isMainStream = streams.size() == 0 ? true : false; // Treat 1st stream as Main stream

        if (isDuplicateUrl(streams, stream->live_url) )
        {
            stream.reset();
            continue;
        }
        if (stream->isMainStream)
        {
            stream->id =  sensor->id;
            stream->name = sensor->name.empty() ? "CAMERA" : sensor->name;
        }
        else
        {
            stream->id = sensor->id + "-" + stream->id;
            stream->name = sensor->name.empty() ? "CAMERA-" + stream->name : sensor->name + "-" + stream->name;
        }
        stream->sensorId = sensor->id;
        streams.push_back(move(stream));
    }
    sensor->updateStreams(streams);
    ret = 0;
    return ret;
}

int OnvifClient::fetchSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    LOG(verbose) << __FUNCTION__<<endl;
    int ret = -1;
    int retGetServices = -1;
    int retGetCapabilities = -1;
    nvsoap_ soap;
    std::shared_ptr<ClientSession> clientSession = std::make_shared<ClientSession>();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }

    soap.user = m_adaptorInfo.m_user;
    soap.password = m_adaptorInfo.m_password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = AUTH_METHOD_USERNAME_TOKEN;
    clientSession->getNvSoap()->getHttpErrorCode(); // reset code value
    vector<SensorSettings> settings;
    soap.url = m_adaptorInfo.m_url +  string(":") + m_adaptorInfo.m_port + string("/onvif/device_service");
    soap.device_url = soap.url;
    map<string, OnvifServiceInfo> caps;
    OnvifServiceInfo serviceInfo;

    retGetCapabilities = clientSession->getNvSoap()->GetCapabilities(soap, caps);
    retGetServices = clientSession->getNvSoap()->GetServices(soap, caps);
    if (retGetCapabilities != 0 && retGetServices != 0)
    {
        LOG(error) << "GetCapabilities failed" << endl;
        return ret;
    }

    // Get service capabilities for MMS device (will be shared across all sensors)
    ServiceCapabilities mmsServiceCapabilities;
    int retServiceCapabilities = clientSession->getNvSoap()->GetServiceCapabilities(soap, mmsServiceCapabilities);
    if (retServiceCapabilities == 0)
    {
        mmsServiceCapabilities.securedAuthMethod = getSecuredAuthMethod(mmsServiceCapabilities.supportedAuthMethods);
        // If MMS device reports no supported auth methods (returns 0), default to USERNAME_TOKEN
        if (mmsServiceCapabilities.securedAuthMethod == AUTH_METHOD_NONE)
        {
            LOG(info) << "MMS device reported no supported auth methods, defaulting to USERNAME_TOKEN" << endl;
            mmsServiceCapabilities.securedAuthMethod = AUTH_METHOD_USERNAME_TOKEN;
        }
        LOG(info) << "MMS Service Capabilities retrieved successfully, authMethod: " << mmsServiceCapabilities.securedAuthMethod << endl;
    }
    else
    {
        LOG(info) << "GetServiceCapabilities failed for MMS device, using default auth method" << endl;
        mmsServiceCapabilities.securedAuthMethod = AUTH_METHOD_USERNAME_TOKEN;
    }

    auto it = caps.find(ONVIF_MEDIA2_SERVICE);
    if (it == caps.end())
    {
        it = caps.find(ONVIF_MEDIA_SERVICE);
    }

    if (it != caps.end())
    {
        serviceInfo = it->second;
    }
    else
    {
        LOG(error) << "fetchSensorStreamInfo failed due to ONVIF service is not supported" << endl;
        return ret;
    }

    soap.url = serviceInfo.url;
    soap.name_space = serviceInfo.name_space;

    if (clientSession->getNvSoap()->GetProfiles(soap, settings) != 0)
    {
        LOG(error) << "GetProfiles failed" << endl;
        return ret;
    }
    LOG(verbose) << "profiles size: " << settings.size() << endl;

    for (SensorSettings s : settings)
    {
        shared_ptr<SensorInfo> sensor(new SensorInfo);
        shared_ptr<StreamInfo> stream(new StreamInfo);
        // Check if sensor already exists
        if (isSensorExists(s.token.profileToken))
        {
            sensor = getSensor(s.token.profileToken);
            sensor->streams.clear();
        }
        sensor->user =  m_adaptorInfo.m_user;
        sensor->password = m_adaptorInfo.m_password;
        sensor->id = sensor->sensorId = stream->id = s.token.profileToken;
        // Set sensor URL to MMS device URL (needed for MMS mode)
        sensor->url = soap.device_url;
        // Extract and set IP from MMS device URL (needed for duplicate detection in database layer)
        sensor->ip = m_adaptorInfo.m_ipaddress;
        // Set sensor type to MMS_ONVIF for MMS sensors, For onvif sensors, type is set in discovery_adaptor
        sensor->type = SENSOR_TYPE_MMS_ONVIF;
        // Populate serviceUrls with MMS device capabilities (avoid redundant getDeviceCapabilities calls)
        sensor->serviceUrls = caps;
        // Populate serviceCapabilities with MMS device service capabilities
        sensor->serviceCapabilities = mmsServiceCapabilities;
        // Will not update name if sensor exists
        if (isSensorExists(sensor->id) == false)
        {
            sensor->name =
                s.token.profileName.empty() ? DEFAULT_CAMERA_NAME : s.token.profileName;
        }
        sensor->updateSensorStatus(SensorStatusOnline);
        stream->settings = s;
        if (s.encoderValues.encoding == "H264" || s.encoderValues.encoding == "H265")
        {
            stream->isMainStream = true;
        }
        else
        {
            continue; // skip non h264/h265 streams
        }

        if (stream->isMainStream)
        {
            stream->id = sensor->id;
            stream->name = sensor->name.empty() ? "CAMERA" : sensor->name;
        }
        else
        {
            stream->id = sensor->id + "-" + stream->id;
            stream->name = sensor->name.empty() ? "CAMERA-" + stream->name : sensor->name + "-" + stream->name;
        }
        LOG(verbose) << "Profile token: " << s.token.profileToken << endl;
        soap.token = s.token.profileToken;
        clientSession->getNvSoap()->GetMediaUri(soap, stream->live_url);
        sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());

        /* Skip push_back of camera with missing live URL */
        if(stream->live_url == "")
        {
            continue;
        }
        LOG(info) << "live_url: " << secureUrlForLogging(stream->live_url) << endl;
        map<string, OnvifServiceInfo>::iterator it = caps.find(ONVIF_REPLAY_SERVICE);
        if (it != caps.end())
        {
            // Save original media service URL and namespace
            string original_url = soap.url;
            string original_namespace = soap.name_space;

            soap.url = it->second.url;
            soap.name_space = it->second.name_space;
            soap.token = s.token.profileToken;
            clientSession->getNvSoap()->GetReplayUri(soap, stream->replay_url);
            sensor->updateHttpErrorStatus(clientSession->getNvSoap()->getHttpErrorCode());

            // Restore original media service URL and namespace for next iteration
            soap.url = original_url;
            soap.name_space = original_namespace;
        }
        if(sensor->user.empty() == false && sensor->password.empty() == false)
        {
            string token("//");
            const string subString = sensor->user + string(":") + sensor->password + string("@");
            insertString( stream->replay_url, token, subString);
            insertString( stream->live_url, token, subString);
        }
        {
            nvsoap_ in;
            in.url = HTTPS + string("://") + m_adaptorInfo.m_ipaddress;
            in.token = stream->id;
            in.user = m_adaptorInfo.m_user;
            in.password = m_adaptorInfo.m_password;
            in.curl = clientSession->getCurlClient();
            clientSession->getNvSoap()->getCameraPostionsValues(in, sensor->position);
        }
        stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_ONLINE,
                        translateStreamStatusToString(StreamStatus::STREAM_STATUS_ONLINE)));
        sensor->streams.push_back(move(stream));
        sensor->printInfo();

        // Will not push if sensor already exists.
        vector<shared_ptr<SensorInfo> >::iterator sensors_it = find_if(
            sensors.begin(), sensors.end(), [=](shared_ptr<SensorInfo>& element) {
                return element->id == sensor->id;
            });
        if (sensors_it == sensors.end())
        {
            sensors.push_back(sensor);
        }
    }
    ret = 0;
    return ret;
}

int OnvifClient::connect()
{
    LOG(verbose) << __FUNCTION__<<endl;
    int result = -1;
    string url;
    int online_sensors = 0;
    if (m_adaptorInfo.m_type == TYPE_VST)
    {
        int max_sensors = GET_CONFIG().max_sensors_supported;
        std::vector<shared_ptr<SensorInfo>>::iterator it;
        for (it = m_cacheSensorList.begin(); it != m_cacheSensorList.end();)
        {
            auto sensor =  *it;
            bool is_sensor_offline = false;
            if (sensor->type == SENSOR_TYPE_ONVIF)
            {
                if (sensor->serviceUrls.size() == 0) // in case camera connected after vms boots
                {
                    getDeviceCapabilities(sensor);
                }
                is_sensor_offline = isCameraOnline(*sensor) == false;
            }
            else if (sensor->type == SENSOR_TYPE_RTSP)
            {
                vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                if (streams.size() > 0 && streams[0]->live_url.empty() == false)
                {
                    is_sensor_offline = isRtspServerReachable(streams[0]->live_url) == false;
                }
                else
                {
                    is_sensor_offline = isRtspServerReachable(sensor->ip, false) == false;
                }
            }
            else
            {
                ++it;
                continue;
            }

            if (is_sensor_offline)
            {
                LOG(error) << "Camera is Not online: " << secureUrlForLogging(sensor->url) << endl;
                sensor->updateSensorStatus(SensorStatusOffline);
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
                ++it;
                continue;
            }
            if(online_sensors >= max_sensors)
            {
                LOG(warning) << "Max sensor limit is reached, so deleting extra sensor: " << sensor->name << endl;
                it = m_cacheSensorList.erase(it);
                continue;
            }
            else
            {
                ++it;
            }
            if (validateCredentials(sensor, sensor->user, sensor->password) || sensor->type == SENSOR_TYPE_RTSP)
            {
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            }
            else
            {
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraUnauthorizedError));
            }
            sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
            ++online_sensors;
        }
        result = 0;
    }
    else
    {
        shared_ptr<SensorInfo> temp_sensor(new SensorInfo);
        std::shared_ptr<ClientSession> clientSession = std::make_shared<ClientSession>();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return result;
        }
        /* TBD, about ONVIF endpoints, getting input from vms_config.json file */
        url = m_adaptorInfo.m_url +  string(":") + m_adaptorInfo.m_port + string("/onvif/device_service");
        nvsoap_ soap;
        soap.user = m_adaptorInfo.m_user;
        soap.password = m_adaptorInfo.m_password;
        soap.url = soap.device_url = url;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = AUTH_METHOD_USERNAME_TOKEN;
        map<string, string> devInfo;
        result = clientSession->getNvSoap()->GetDeviceInformation(soap, devInfo);
        std::pair<int, string> httpResponse = clientSession->getNvSoap()->getHttpErrorCode();
        if(httpResponse.first == 200)
        {
            for(auto it: devInfo)
            {
                LOG(verbose) << it.first << ": " << it.second << endl;
            }
        }
    }
    return result;
}

int OnvifClient::synchronizeSensorTime(shared_ptr<SensorInfo>& sensor)
{
    int response = 0;

    if (sensor.get() == nullptr || sensor->getSensorStatus() == SensorStatusOffline)
    {
        LOG(error) << "Sensor is null or not connected" << endl;
        return -1;
    }

    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return -1;
    }

    nvsoap_ soap;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.token = sensor->url;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;

    response = clientSession->getNvSoap()->synchronizeDeviceTime(soap);
    if (response != 0)
    {
        LOG(error) << "Time synchronozation failed for sensor:" << sensor->name << endl;
        return -1;
    }
    return response;
}

int OnvifClient::getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    LOG(verbose) << __FUNCTION__<<endl;
    int response = 0;
    std::shared_ptr<NvSoap> nvsoap;
    nvsoap_ soap;
    std::shared_ptr<ClientSession> clientSession;
    if (m_adaptorInfo.m_type == TYPE_VST)
    {
        for (uint32_t i = 0; i < sensors.size(); i++)
        {
            shared_ptr<SensorInfo> sensor = sensors[i];
            if (sensor->type == SENSOR_TYPE_RTSP || sensor->type == SENSOR_TYPE_CSI ||
            sensor->getSensorStatus() == SensorStatusOffline)
            {
                continue;
            }
            if (sensor->url.empty())
            {
                goto sensor_offline;
            }

            clientSession = sensor->getClientSession();
            if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
            {
                LOG(error) << "Failed to get client session" << endl;
                continue;
            }

            nvsoap = sensor->getClientSession()->getNvSoap();
            if (nullptr == nvsoap)
            {
                LOG(error) << "Failed to get NvSoap client Session for sensor: " << sensor->id << " url:" << secureUrlForLogging(sensor->url) << endl;
                continue;
            }

            soap.user = sensor->user;
            soap.password = sensor->password;
            soap.token = sensor->url;
            soap.curl = clientSession->getCurlClient();
            soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;

            response = clientSession->getNvSoap()->synchronizeDeviceTime(soap);
            if (response != 0)
            {
                LOG(error) << "Time synchronozation failed for sensor:" << sensor->name << endl;
            }
            response = getDeviceCapabilities(sensor);
            if (response == 0)
            {
                response |= getSensorStreamInfo(sensor);
                continue;
            }
            else
            {
                goto sensor_offline;
            }
            sensor_offline:
            {
                sensor->updateSensorStatus(SensorStatusOffline);
                sensor->updateHttpErrorStatus(std::make_pair(CAMERA_CAMERA_NOT_FOUND_CODE, CAMERA_CAMERA_NOT_FOUND_MSG));
            }
        }
    }
    else if (m_adaptorInfo.m_type == TYPE_MMS)
    {
        response = fetchSensorStreamInfo(sensors);
    }
    return response;
}

int OnvifClient::getSensorStreamInfo(shared_ptr<SensorInfo>& sensor)
{
    LOG(verbose) << __FUNCTION__<<endl;
    int response = 0;
    if (sensor.get() == nullptr ||
        sensor->getSensorStatus() == SensorStatusOffline)
    {
        LOG(error) << "Sensor is null or not connected" << endl;
        return -1;
    }
    vector<shared_ptr<StreamInfo>> streams;
    if (m_adaptorInfo.m_type == TYPE_VST)
    {
        if (sensor->url.empty())
        {
            LOG(error) << "Invalid sensor/sensor url is null" << endl;
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
            response = -1;
            goto finalize;
        }
        GetSensorInfo(sensor);
        if (sensor->serviceUrls.size() == 0) // in case camera connected after vms boots
        {
            response = getDeviceCapabilities(sensor);
        }
        if (response == 0)
        {
            response = fetchSensorStreamInfo(sensor);
            sensor->ptzInfo = getPTZ(sensor);
            if (response == 0)
            {
                for (uint stream = 0; stream < sensor->streams.size(); stream++)
                {
                    response = getStreamSettings(sensor, sensor->streams[stream]->id);
                }
            }
        }
        else
        {
            goto finalize;
        }
    }
    finalize:
        if (response != 0 && sensor->getHttpErrorStatus().first == CAMERA_CAMERA_NOT_FOUND_CODE)
        {
            LOG(error) << "Error fetching camera info" << endl;
            sensor->updateSensorStatus(SensorStatusOffline);
            sensor->updateHttpErrorStatus(std::make_pair(CAMERA_CAMERA_NOT_FOUND_CODE, CAMERA_CAMERA_NOT_FOUND_MSG));
        }
        sensor->printInfo();
    return response;
}

bool OnvifClient::isServerOnline(const string & url)
{
    return true;
}

 map<PTZAction, ptzRange> OnvifClient::getPTZ(shared_ptr<SensorInfo> sensor)
 {
    map<PTZAction, ptzRange> ptz;
    if (sensor != nullptr && sensor->streams.size() > 0)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ptz;
        }

        std::shared_ptr<NvSoap> nvsoap = clientSession->getNvSoap();
        if (nullptr == nvsoap)
        {
            LOG(error) << "Failed to get NvSoap client Session for sensor: " << sensor->id << " url:" << secureUrlForLogging(sensor->url) << endl;
            return ptz;
        }

        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = sensor->streams[0]->settings.token.ptzNodeToken;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
        map<string, OnvifServiceInfo>::iterator it = sensor->serviceUrls.find(ONVIF_PTZ_SERVICE);
        if(it != sensor->serviceUrls.end())
        {
            OnvifServiceInfo serviceInfo;
            serviceInfo = it->second;
            soap.url = serviceInfo.url;
            soap.name_space = serviceInfo.name_space;
            soap.device_url = sensor->url;
            vector<PTZSpaces> res;
            clientSession->getNvSoap()->GetPTZNode(soap, res);
            for (uint32_t i = 0; i < res.size(); i++)
            {
                PTZSpaces s = res[i];
                ptzRange range;
                range.x_max = s.x_max_range;
                range.x_min = s.x_min_range;
                range.y_max = s.y_max_range;
                range.y_min = s.y_min_range;
                if (s.spaceType == ContinuousPanTiltVelocitySpace)
                    /*s.spaceType == AbsolutePanTiltPositionSpace ||
                    s.spaceType == RelativePanTiltTranslationSpace ||
                    s.spaceType == ContinuousPanTiltVelocitySpace ||
                    s.spaceType == PanTiltSpeedSpace)*/
                {
                    ptz[PTZAction::PanTilt] = range;
                }
                else if (s.spaceType == ContinuousZoomVelocitySpace)
                    /*s.spaceType == AbsoluteZoomPositionSpace ||
                    s.spaceType == RelativeZoomTranslationSpace ||
                    s.spaceType == ContinuousZoomVelocitySpace ||
                    s.spaceType == ZoomSpeedSpace)*/
                {
                    ptz[PTZAction::Zoom] = range;
                }
            }
        }
    }
    return ptz;
 }

int OnvifClient::setPTZ(shared_ptr<SensorInfo>& sensor, PTZAction ptz, string x, string y)
{
    int ret = -1;
    if(ptz == PTZAction::Unknown)
    {
        LOG(error) << "Invalid PTZ operation" << endl;
        return ret;
    }
    if (sensor != nullptr && sensor->streams.size() > 0)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ret;
        }
        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = sensor->streams[0]->settings.token.profileToken;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
        map<string, OnvifServiceInfo>::iterator it = sensor->serviceUrls.find(ONVIF_PTZ_SERVICE);
        if(it != sensor->serviceUrls.end())
        {
            OnvifServiceInfo serviceInfo;
            serviceInfo = it->second;
            soap.url = serviceInfo.url;
            soap.name_space = serviceInfo.name_space;
            soap.device_url = sensor->url;
            ret = clientSession->getNvSoap()->ContinuousMove(soap, ptz, x, y);
            LOG(verbose) << "OnvifClient::setPTZ: " << PTZActionToString(ptz) << " X: " << x << " Y: "<< y << endl;
        }
    }
    return ret;
}

bool OnvifClient::validateCredentials(shared_ptr<SensorInfo>& sensor, const string username, const string password)
{
    LOG(verbose) << "OnvifClient::validateCredentials" << endl;
    if (sensor != nullptr)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return false;
        }
        nvsoap_ soap;
        soap.user = username;
        soap.password = password;
        soap.url = soap.device_url = sensor->url;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
        soap.status = sensor->getHttpErrorStatus().first;
        string host;
        string discovery_mode;
        if (clientSession->getNvSoap()->GetDiscoveryMode(soap, discovery_mode) == 0)
        {
            /* Set hashing algorithm(SHA-256) is sensor supports */
            setHashingAlgorithmSHA256(sensor, soap);

            if (clientSession->getNvSoap()->synchronizeDeviceTime(soap) != 0)
            {
                LOG(error) << "Time synchronozation failed for sensor:" << sensor->name << endl;
            }
            return true;
        }
    }
    LOG(error) << "validateCredentials failed for " << sensor->name << endl;
    return false;
}

int OnvifClient::getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo)
{
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.url = soap.device_url = sensor->url;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    ret = clientSession->getNvSoap()->getNetworkInterfaces(soap, networkInfo);
    if (ret != 0)
    {
        LOG(error) << "getNetworkInterfaces request failed with ret:" << ret << endl;
    }
    return ret;
}

int OnvifClient::setNetworkInfo(shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded)
{
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.url = soap.device_url = sensor->url;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    ret = clientSession->getNvSoap()->setNetworkInterfaces(soap, networkInfo, rebootNeeded);
    if (ret != 0)
    {
        LOG(error) << "setNetworkInterfaces request failed with ret:" << ret << endl;
    }
    return ret;
}

int OnvifClient::getSensorImageSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings)
{
    LOG(verbose2) << "getSensorImageSettings" <<endl;
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    shared_ptr<StreamInfo> stream = sensor->getStream(stream_id);
    if (stream != nullptr)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ret;
        }
        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = stream->settings.token.sourceToken;
        map<string, OnvifServiceInfo>::iterator it = sensor->serviceUrls.find(ONVIF_IMAGING_SERVICE);
        if(it != sensor->serviceUrls.end())
        {
            OnvifServiceInfo serviceInfo;
            serviceInfo = it->second;
            soap.url = serviceInfo.url;
            soap.name_space = serviceInfo.name_space;
            soap.device_url = sensor->url;
            soap.curl = clientSession->getCurlClient();
            soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
            ret = clientSession->getNvSoap()->getDeviceImageSettings(soap, settings.imageValues);
            if (ret == 0)
            {
                ret = clientSession->getNvSoap()->getCameraImageOptions(soap, settings.imageOptions);
            }
            if (ret == 0 && sensor->type == SENSOR_TYPE_ONVIF)
            {
                stream->settings.imageValues = settings.imageValues;
                stream->settings.imageOptions = settings.imageOptions;
            }
        }
        else
        {
            LOG(error) << "Sensor service URL not found" << endl;
        }
    }
    else
    {
        LOG(error) << "Sensor not found or sensor stream info not present" << endl;
    }
    return ret;
}

int OnvifClient::setSensorImageSettings(shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings)
{
    int ret = -1;
    if (sensor != nullptr && sensor->streams.size() > 0)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ret;
        }
        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = sensor->streams[0]->settings.token.sourceToken;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
        map<string, OnvifServiceInfo>::iterator it = sensor->serviceUrls.find(ONVIF_IMAGING_SERVICE);
        if(it != sensor->serviceUrls.end())
        {
            OnvifServiceInfo serviceInfo;
            serviceInfo = it->second;
            soap.url = serviceInfo.url;
            soap.name_space = serviceInfo.name_space;
            soap.device_url = sensor->url;
            ret = clientSession->getNvSoap()->setDeviceImageSettings(soap, settings);
        }
    }
    else
    {
        LOG(error) << "Sensor not found or sensor stream info not present" << endl;
    }
    return ret;
}

int OnvifClient::getSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings)
{
    LOG(verbose) << "getSensorEncodeSettings" <<endl;
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "Sensor not found " << endl;
        return ret;
    }
    shared_ptr<StreamInfo> stream = sensor->getStream(stream_id);
    if (stream != nullptr)
    {
        OnvifServiceInfo serviceInfo;
        map<string, OnvifServiceInfo> caps = sensor->serviceUrls;
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ret;
        }
        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = stream->settings.token.profileToken;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;

        auto it = caps.find(ONVIF_MEDIA2_SERVICE);
        if (it == caps.end())
        {
            it = caps.find(ONVIF_MEDIA_SERVICE);
        }

        if (it != caps.end())
        {
            serviceInfo = it->second;
        }
        else
        {
            LOG(error) << "getSensorEncodeSettings failed due to onvif service is not supported" << endl;
            return ret;
        }

        soap.url = serviceInfo.url;
        soap.name_space = serviceInfo.name_space;
        soap.device_url = sensor->url;
        soap.userData["encoderToken"] = stream->settings.token.encoderToken;
        ret = clientSession->getNvSoap()->getCameraEncoderOptions(soap, settings.encoderOptions);
        if (ret == 0)
        {
            ret = clientSession->getNvSoap()->GetProfile(soap, settings);
        }
        ret = clientSession->getNvSoap()->getCameraEncoderConfiguration(soap, settings.encoderValues);
    }
    else
    {
        LOG(error) << "Sensor stream info not present" << endl;
    }
    return ret;
}

int OnvifClient::setSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings)
{
    int ret = -1;
    if (sensor != nullptr && sensor->streams.size() > 0)
    {
        std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
        if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
        {
            LOG(error) << "Failed to get client session" << endl;
            return ret;
        }
        nvsoap_ soap;
        soap.user = sensor->user;
        soap.password = sensor->password;
        soap.token = sensor->streams[0]->settings.token.encoderToken;
        map<string, string> multicast;
        MultiCast& m = sensor->streams[0]->settings.multiCast;
        multicast["Type"] = m.AddressType;
        multicast["IPv4Address"] = m.IPAddress;
        multicast["Port"] = m.Port;
        multicast["TTL"] = m.TTL;
        multicast["AutoStart"] = m.AutoStart;
        soap.userData = multicast;
        OnvifServiceInfo serviceInfo;

        map<string, OnvifServiceInfo> caps = sensor->serviceUrls;

        auto it = caps.find(ONVIF_MEDIA2_SERVICE);
        if (it == caps.end())
        {
            it = caps.find(ONVIF_MEDIA_SERVICE);
        }

        if (it != caps.end())
        {
            serviceInfo = it->second;
        }
        else
        {
            LOG(error) << "setSensorEncodeSettings failed" << endl;
            return ret;
        }

        soap.url = serviceInfo.url;
        soap.name_space = serviceInfo.name_space;
        soap.device_url = sensor->url;
        soap.curl = clientSession->getCurlClient();
        soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
        ret = clientSession->getNvSoap()->setCameraEncoderSettings(soap, settings);
        if (ret == -1)
        {
            LOG(error) << "Failed to set sensor encode settings" << sensor->sensorId << endl;
        }
    }
    else
    {
        LOG(error) << "Sensor not found or sensor stream info not present" << endl;
    }

    return ret;
}

int OnvifClient::rebootSensor(shared_ptr<SensorInfo>& sensor)
{
    int ret = -1;
    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }
    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }
    nvsoap_ soap;
    soap.url = soap.device_url = sensor->url;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    ret = clientSession->getNvSoap()->rebootDevice(soap);
    if (ret != 0)
    {
        LOG(error) << "rebootDevice request failed with ret:" << ret << endl;
    }
    return ret;
}

int OnvifClient::getStreamSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id)
{
    SensorSettings settings;
    getSensorImageSettings(sensor, stream_id, settings);
    getSensorEncodeSettings(sensor, stream_id, settings);
    return 0;
}

int OnvifClient::restoreSensorCapabilitiesFromCache(shared_ptr<SensorInfo>& sensor)
{
    if (!sensor->serviceUrls.empty())
    {
        return 0; // Already populated
    }

    shared_ptr<SensorInfo> cachedSensor = getSensor(sensor->id);
    if (cachedSensor != nullptr && !cachedSensor->serviceUrls.empty())
    {
        sensor->serviceUrls = cachedSensor->serviceUrls;
        sensor->serviceCapabilities = cachedSensor->serviceCapabilities;
        sensor->url = cachedSensor->url;
        sensor->ip = cachedSensor->ip;
        sensor->user = cachedSensor->user;
        sensor->password = cachedSensor->password;

        // If cached authMethod is NONE, set default
        if (sensor->serviceCapabilities.securedAuthMethod == 0 ||
            sensor->serviceCapabilities.securedAuthMethod == AUTH_METHOD_NONE)
        {
            sensor->serviceCapabilities.securedAuthMethod = AUTH_METHOD_USERNAME_TOKEN;
        }
        return 0;
    }

    // Cache miss - need to refresh capabilities
    if (sensor->user.empty() || sensor->password.empty())
    {
        LOG(error) << "Sensor credentials not set for sensor: " << sensor->id << endl;
        return CameraUnauthorizedError;
    }

    int refreshRet = getDeviceCapabilities(sensor);
    if (refreshRet != 0)
    {
        auto httpError = sensor->getHttpErrorStatus();
        LOG(error) << "Failed to refresh device capabilities (HTTP: "
                  << httpError.first << ")" << endl;

        // If we got a 401, it's an authentication issue
        if (httpError.first == 401)
        {
            return CameraUnauthorizedError;
        }
        return refreshRet;
    }

    if (sensor->serviceCapabilities.securedAuthMethod == 0 ||
        sensor->serviceCapabilities.securedAuthMethod == AUTH_METHOD_NONE)
    {
        sensor->serviceCapabilities.securedAuthMethod = AUTH_METHOD_USERNAME_TOKEN;
    }

    shared_ptr<SensorInfo> cachedSensorUpdate = getSensor(sensor->id);
    if (cachedSensorUpdate != nullptr)
    {
        cachedSensorUpdate->serviceUrls = sensor->serviceUrls;
        cachedSensorUpdate->serviceCapabilities = sensor->serviceCapabilities;
    }

    return 0;
}

// Profile G - Recording Timeline Implementation
int OnvifClient::getRecordingTimelines(shared_ptr<SensorInfo>& sensor, Json::Value& timelinesJson)
{
    int ret = -1;

    if (sensor == nullptr)
    {
        LOG(error) << "sensor is null" << endl;
        return ret;
    }

    std::shared_ptr<ClientSession> clientSession = sensor->getClientSession();
    if (clientSession == nullptr || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client session" << endl;
        return ret;
    }

    // Restore service URLs from cache if empty (for MMS sensors)
    ret = restoreSensorCapabilitiesFromCache(sensor);
    if (ret != 0)
    {
        return ret;
    }

    // Check if Search service is available
    auto it = sensor->serviceUrls.find(ONVIF_SEARCH_SERVICE);
    if (it == sensor->serviceUrls.end())
    {
        LOG(warning) << "ONVIF Profile G not supported by sensor: " << sensor->id << endl;
        timelinesJson = Json::Value(Json::objectValue);
        return 0;
    }

    // Prepare SOAP structure
    nvsoap_ soap;
    soap.user = sensor->user;
    soap.password = sensor->password;
    soap.curl = clientSession->getCurlClient();

    // Use existing authMethod if available, otherwise default to UsernameToken (1)
    soap.authMethod = sensor->serviceCapabilities.securedAuthMethod;
    if (soap.authMethod == 0 || soap.authMethod == AUTH_METHOD_NONE)
    {
        soap.authMethod = AUTH_METHOD_USERNAME_TOKEN;
    }

    soap.url = it->second.url;
    soap.name_space = it->second.name_space;
    soap.device_url = sensor->url;

    // Get Recording Summary
    RecordingSummary summary;

    ret = clientSession->getNvSoap()->GetRecordingSummary(soap, summary);
    if (ret != 0)
    {
        LOG(error) << "GetRecordingSummary failed" << endl;
        return ret;
    }

    string summaryStartTime = summary.dataFrom;
    string summaryEndTime = summary.dataUntil;

    // Calculate duration from summary times
    long long durationMs = DEFAULT_RECORDING_DURATION_MS;

    if (!summaryStartTime.empty() && !summaryEndTime.empty())
    {
        // Parse ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
        struct tm tmStart = {0}, tmEnd = {0};

        // Parse start time
        if (strptime(summaryStartTime.c_str(), "%Y-%m-%dT%H:%M:%SZ", &tmStart) != nullptr)
        {
            // Parse end time
            if (strptime(summaryEndTime.c_str(), "%Y-%m-%dT%H:%M:%SZ", &tmEnd) != nullptr)
            {
                time_t startEpoch = timegm(&tmStart);
                time_t endEpoch = timegm(&tmEnd);

                // Calculate duration in milliseconds
                long long durationSeconds = (long long)(endEpoch - startEpoch);
                durationMs = durationSeconds * 1000LL; // Convert to milliseconds
            }
        }
    }

    // Find Recordings
    RecordingSearchScope scope;
    // Use sensor ID as recording source
    scope.includedSources.push_back(sensor->id);

    // Create filter: boolean(//tt:Track),starttime,maxduration,maxmatches,startindex,endindex
    // Use summaryStartTime and calculated duration to get all recordings in the time range
    string filter = "boolean(//tt:Track)," + summaryEndTime + "," +
                    to_string(durationMs) + ",10000,0,0";
    scope.recordingInformationFilter = filter;

    int maxMatches = 10000;
    string keepAliveTime = "PT60S";
    string searchToken;

    ret = clientSession->getNvSoap()->FindRecordings(soap, scope, maxMatches, keepAliveTime, searchToken);
    if (ret != 0 || searchToken.empty())
    {
        LOG(error) << "FindRecordings failed for sensor: " << sensor->id << endl;
        return ret;
    }

    // Get Recording Search Results - Loop until SearchState is "Completed"
    int minResults = 1;  // Minimum results to wait for
    int maxResults = 1000;  // Request up to 1000 results per iteration
    string waitTime = "PT10S";  // Wait time for each request

    vector<RecordingInformation> allRecordings;  // Accumulate all recordings across iterations
    string searchState;
    int iteration = 0;
    const int maxIterations = 100;  // Safety limit to prevent infinite loops

    do
    {
        iteration++;

        RecordingSearchResults results;

        ret = clientSession->getNvSoap()->GetRecordingSearchResults(soap, searchToken, minResults,
                                                                   maxResults, waitTime, results);
        if (ret != 0)
        {
            LOG(error) << "GetRecordingSearchResults failed on iteration " << iteration << endl;
            clientSession->getNvSoap()->EndSearch(soap, searchToken);
            return ret;
        }

        searchState = results.searchState;

        // Accumulate results from this iteration
        if (!results.recordingList.empty())
        {
            allRecordings.insert(allRecordings.end(),
                               results.recordingList.begin(),
                               results.recordingList.end());
        }

        // Check if we've reached the safety limit
        if (iteration >= maxIterations)
        {
            LOG(warning) << "Reached max iterations (" << maxIterations << ")" << endl;
            break;
        }

        // If search is not completed, add a small delay before next iteration
        // to avoid hammering the server in case it doesn't respect WaitTime properly
        if (searchState != "Completed")
        {
            LOG(verbose) << "Search not completed, waiting 100ms before next iteration..." << endl;
            usleep(100000); // 100ms delay
        }
    } while (searchState != "Completed");

    // End search session
    int endSearchRet = clientSession->getNvSoap()->EndSearch(soap, searchToken);
    if (endSearchRet != 0)
    {
        LOG(warning) << "EndSearch failed (code: " << endSearchRet << ")" << endl;
    }

    // Helper lambda to ensure timestamp has milliseconds (.000Z format)
    // If ONVIF server provides milliseconds, use as-is; otherwise append .000 before Z
    auto formatTimeWithMs = [](const string& timestamp) -> string {
        if (timestamp.empty())
        {
            return timestamp;
        }

        // Check if timestamp ends with 'Z'
        if (timestamp.back() != 'Z')
        {
            return timestamp; // Non-standard format, return as-is
        }

        // Check if milliseconds already present (look for '.' in the last part)
        // Format with ms: 2025-12-18T12:22:51.141Z
        // Format without ms: 2025-12-18T12:22:51Z
        size_t lastDot = timestamp.rfind('.');
        size_t lastColon = timestamp.rfind(':');

        // If there's a dot after the last colon, milliseconds are present
        if (lastDot != string::npos && lastColon != string::npos && lastDot > lastColon)
        {
            return timestamp; // Already has milliseconds
        }

        // Insert .000 before the trailing Z
        return timestamp.substr(0, timestamp.length() - 1) + ".000Z";
    };

    // Format results: { "recording_token": [ { "startTime": "...", "endTime": "..." } ] }
    int totalTimelineEntries = 0;
    for (const auto& recording : allRecordings)
    {
        if (recording.tracks.empty())
        {
            continue;
        }

        for (const auto& track : recording.tracks)
        {
            if (track.trackType != "Video")
            {
                continue;
            }

            if (track.dataFrom.empty() || track.dataTo.empty())
            {
                continue;
            }

            // Create timeline entry with milliseconds format
            Json::Value timelineEntry;
            timelineEntry["startTime"] = formatTimeWithMs(track.dataFrom);
            timelineEntry["endTime"] = formatTimeWithMs(track.dataTo);

            // Check if this recording token already exists in the result
            // If yes, append to existing array (handles recording gaps)
            // If no, create new array
            if (!timelinesJson.isMember(recording.recordingToken))
            {
                timelinesJson[recording.recordingToken] = Json::Value(Json::arrayValue);
            }

            timelinesJson[recording.recordingToken].append(timelineEntry);
            totalTimelineEntries++;
        }
    }

    LOG(info) << "Found " << timelinesJson.size() << " unique recording token(s) with "
              << totalTimelineEntries << " timeline segment(s) for sensor: " << sensor->id << endl;

    return 0;
}
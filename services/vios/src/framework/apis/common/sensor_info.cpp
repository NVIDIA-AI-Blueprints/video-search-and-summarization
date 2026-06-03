/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include "sensor_info.h"
#include "config.h"
#include "logger.h"
#include "database.h"
#include "sensor_control.h"
#include "nvsoap.h"

SensorInfo::SensorInfo() :
        id("")
        ,sensorId("")
        ,ip("")
        ,name("")
        ,url("")
        ,hardware("")
        ,manufacturer("")
        ,firmware_version("")
        ,serial_number("")
        ,hardware_id("")
        ,location("")
        ,tags("")
        ,user("")
        ,password("")
        ,isAutoDiscovered(false)
        ,type("")
        ,m_notify(true)
        ,isRemoteSensor(false)
        ,remoteDeviceId("")
        ,remoteDeviceName("")
        ,remoteDeviceLocation("")
        ,clientSession(nullptr)
        ,sensorStatus(SensorStatusUnknown)
        ,httpStatusCode(-1, "")
{
    serviceUrls.clear();
    streams.clear();
    metadata.clear();
}

SensorInfo::SensorInfo (const SensorInfo& sensorInfo)
{
    this->id = sensorInfo.id;
    this->sensorId = sensorInfo.sensorId;
    this->name = sensorInfo.name;
    this->ip = sensorInfo.ip;
    this->url = sensorInfo.url;
    this->model = sensorInfo.model;
    this->hardware = sensorInfo.hardware;
    this->manufacturer = sensorInfo.manufacturer;
    this->firmware_version = sensorInfo.firmware_version;
    this->serial_number = sensorInfo.serial_number;
    this->hardware_id = sensorInfo.hardware_id;
    this->location = sensorInfo.location;
    this->tags = sensorInfo.tags;
    this->user = sensorInfo.user;
    this->password = sensorInfo.password;
    this->httpStatusCode = sensorInfo.httpStatusCode;
    this->sensorStatus = sensorInfo.sensorStatus;
    this->isAutoDiscovered = sensorInfo.isAutoDiscovered;
    this->serviceUrls = sensorInfo.serviceUrls;
    this->streams = sensorInfo.streams;
    this->metadata = sensorInfo.metadata;
    this->ptzInfo = sensorInfo.ptzInfo;
    this->users = sensorInfo.users;
    this->type = sensorInfo.type;
    this->position = sensorInfo.position;
    this->m_notify = sensorInfo.m_notify;
    this->isRemoteSensor = sensorInfo.isRemoteSensor;
    this->remoteDeviceId = sensorInfo.remoteDeviceId;
    this->remoteDeviceName = sensorInfo.remoteDeviceName;
    this->remoteDeviceLocation = sensorInfo.remoteDeviceLocation;
    this->serviceCapabilities = sensorInfo.serviceCapabilities;
    this->clientSession = sensorInfo.clientSession;
}

SensorInfo::~SensorInfo()
{
    streams.clear();
    metadata.clear();
}

void SensorInfo::operator=(const SensorInfo& sensorInfo)
{
    this->id = sensorInfo.id;
    this->sensorId = sensorInfo.sensorId;
    this->name = sensorInfo.name;
    this->ip = sensorInfo.ip;
    this->url = sensorInfo.url;
    this->model = sensorInfo.model;
    this->hardware = sensorInfo.hardware;
    this->manufacturer = sensorInfo.manufacturer;
    this->firmware_version = sensorInfo.firmware_version;
    this->serial_number = sensorInfo.serial_number;
    this->hardware_id = sensorInfo.hardware_id;
    this->location = sensorInfo.location;
    this->tags = sensorInfo.tags;
    this->user = sensorInfo.user;
    this->password = sensorInfo.password;
    this->httpStatusCode = sensorInfo.httpStatusCode;
    this->sensorStatus = sensorInfo.sensorStatus;
    this->isAutoDiscovered = sensorInfo.isAutoDiscovered;
    this->serviceUrls = sensorInfo.serviceUrls;
    this->streams = sensorInfo.streams;
    this->metadata = sensorInfo.metadata;
    this->ptzInfo = sensorInfo.ptzInfo;
    this->users = sensorInfo.users;
    this->type = sensorInfo.type;
    this->position = sensorInfo.position;
    this->m_notify = sensorInfo.m_notify;
    this->isRemoteSensor = sensorInfo.isRemoteSensor;
    this->remoteDeviceId = sensorInfo.remoteDeviceId;
    this->remoteDeviceName = sensorInfo.remoteDeviceName;
    this->remoteDeviceLocation = sensorInfo.remoteDeviceLocation;
    this->serviceCapabilities = sensorInfo.serviceCapabilities;
    this->clientSession = sensorInfo.clientSession;
}

bool SensorInfo::operator==(const string& id)
{
    return (this->id == id);
}

bool SensorInfo::operator==(const SensorInfo& sensorInfo)
{
    return (this->id == sensorInfo.id) || (this->sensorId == sensorInfo.sensorId && this->ip == sensorInfo.ip);
}

void SensorInfo::updateStreams(vector<shared_ptr<StreamInfo>>& InStreams)
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    for(shared_ptr<StreamInfo> streamLocal: streams)
    {
        for(shared_ptr<StreamInfo> stream: InStreams)
        {
            if (stream->id == streamLocal->id)
            {
                stream->replay_url = streamLocal->replay_url;
                stream->live_proxy_url = streamLocal->live_proxy_url;
                break;
            }
        }
    }
    streams = InStreams;
}

bool SensorInfo::addStreams(shared_ptr<StreamInfo>& in_stream)
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    /* Check if stream is already present in the list */
    for(shared_ptr<StreamInfo> stream: streams)
    {
        if (in_stream->id == stream->id)
        {
            LOG(warning) << "Stream is already present with id: " << in_stream->id  << endl;
            return false;
        }
    }
    streams.push_back(in_stream);
    return true;
}

void SensorInfo::clearStreams()
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    streams.clear();
}

vector<shared_ptr<StreamInfo>>& SensorInfo::getStreams()
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    return streams;
}

vector<shared_ptr<SensorMetadata>> SensorInfo::getMetadata()
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    return metadata;
}

shared_ptr<StreamInfo> SensorInfo::getStream(const string& id)
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    shared_ptr<StreamInfo> stream;
    
    for (uint32_t i = 0; i < streams.size(); i++)
    {
        if(streams[i]->id == id)
        {
            stream = streams[i];
            break;
        }
    }
    
    if (stream == nullptr)
    {
        LOG(error) << "Stream ID '" << id << "' not found in sensor '" << this->id << "'" << endl;
        LOG(error) << "Available stream IDs: ";
        for (uint32_t i = 0; i < streams.size(); i++)
        {
            LOG(error) << "'" << streams[i]->id << "'";
            if (i < streams.size() - 1) LOG(error) << ", ";
        }
        LOG(error) << endl;
    }
    
    return stream;
}

void SensorInfo::clearServiceUrls()
{
    std::lock_guard<std::mutex> guard(m_streamLock);
    serviceUrls.clear();
}

void SensorInfo::updateSensorStatus(const SensorStatusEvent status)
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    sensorStatus = status;

    SensorDetailsDBColumns row =  GET_DB_INSTANCE()->readSensorDetails("", id);
    if (!row.sensor_id_value.empty())
    {
        row.sensorStatus_value = sensorStatus;
        GET_DB_INSTANCE()->insertRowSensorDetails(row);
    }
}

SensorStatusEvent SensorInfo::getSensorStatus()
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    return sensorStatus;
}

void SensorInfo::updateHttpErrorStatus(const std::pair<int, string> http_error)
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    httpStatusCode = http_error;

    SensorDetailsDBColumns row =  GET_DB_INSTANCE()->readSensorDetails("", id);
    if (!row.sensor_id_value.empty())
    {
        row.httpStatus_value = httpStatusCode.first;
        GET_DB_INSTANCE()->insertRowSensorDetails(row);
    }
}

std::pair<int, string> SensorInfo::getHttpErrorStatus()
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    return httpStatusCode;
}

void SensorInfo::updateCredentials(const string& in_username, const string& in_password)
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    user = in_username;
    password = in_password;
}

std::pair<string, string> SensorInfo::getCredentials()
{
    std::lock_guard<std::mutex> guard(m_sensorLock);
    return std::make_pair(user, password);
}

void SensorInfo::printInfo()
{
    LOG2(info) << "\tUnique ID: " << id << endl;
    LOG2(info) << "\tSensor ID: " << sensorId << endl;
    LOG2(info) << "\tSensor name: " << name << endl;
    LOG2(info) << "\tSensor ip: " << ip << endl;
    LOG2(info) << "\tSensor url: " << secureUrlForLogging(url) << endl;
    LOG2(info) << "\tSensor model: " << hardware << endl;
    LOG2(info) << "\tSensor manufacturer: " << manufacturer << endl;
    LOG2(info) << "\tSensor firmware_version: " << firmware_version << endl;
    LOG2(info) << "\tSensor serial_number: " << serial_number << endl;
    LOG2(info) << "\tSensor hardware_id: " << hardware_id << endl;
    LOG2(info) << "\tSensor location: " << location << endl;
    LOG2(info) << "\tSensor tags: " << tags << endl;
    LOG2(info) << "\tSensor user: "<< maskSensitiveData(user, MaskType::USERNAME) << endl;
#ifndef RELEASE
    LOG2(info) << "\tSensor password: "<< maskSensitiveData(password, MaskType::PASSWORD) << endl;
#endif
    LOG2(info) << "\tSensor Status: " << SensorStatus::getEventString(sensorStatus) << endl;
    LOG2(info) << "\tSensor Error code: " << getHttpErrorStatus().first << endl;
    LOG2(info) << "\tSensor Error message: " << getHttpErrorStatus().second << endl;
    LOG2(info) << "\tSensor auto discovered: "<< isAutoDiscovered << endl;
    LOG2(info) << "\tSensor type: "<< type << endl;
    LOG2(info) << "\tNotify: "<< m_notify << endl;
    LOG2(info) << "\tIs Remote Sensor: "<< isRemoteSensor << endl;
    LOG2(info) << "\tRemote device ID: "<< remoteDeviceId << endl;
    LOG2(info) << "\tRemote device Name: "<< remoteDeviceName << endl;
    LOG2(info) << "\tRemote device Location: "<< remoteDeviceLocation << endl;
    for (auto it_servUrl : serviceUrls)
    {
        LOG2(info) << "\t" << it_servUrl.first << ":" << it_servUrl.second.url << " :" << it_servUrl.second.name_space << endl;
    }
    for (auto it_users : users)
    {
        LOG2(info) << "\t" << it_users << endl;
    }
    vector<shared_ptr<StreamInfo>>::iterator it_stream;
    for (it_stream = streams.begin(); it_stream != streams.end(); it_stream++)
    {
        (*it_stream)->printInfo();
    }
    LOG2(info) << "\tSensor Metatadata: " << endl;
    vector<shared_ptr<SensorMetadata>>::iterator it_meta;
    for (it_meta = metadata.begin(); it_meta != metadata.end(); it_meta++)
    {
        (*it_meta)->printInfo();
    }
    position.printInfo();
    LOG2(info) << "" << endl;
}

bool SensorInfo::isPTZSuported()
{
    return ptzInfo.size() > 0;
}

void SensorInfo::addUser(shared_ptr<UserInfo> user)
{
    std::lock_guard<std::mutex> guard(m_userLock);
    for (auto itr: users)
    {
        if (itr->username == user->username)
        {
            LOG(error) << "username already exists" << endl;
            return;
        }
    }
    users.insert(user);
}

void SensorInfo::removeUser(string username)
{
    std::lock_guard<std::mutex> guard(m_userLock);
    for (auto itr: users)
    {
        if (itr->username == username)
        {
            users.erase(itr);
            return;
        }
    }
}

string SensorInfo::getUsersString()
{
    std::lock_guard<std::mutex> guard(m_userLock);
    std::string oss;
    for (auto itr: users)
    {
        oss += itr->username + ",";
    }
    if(oss.empty() == false)
    {
        oss.pop_back();
    }
    return oss;
}

void SensorInfo::addUsersFromString(string users)
{
    stringstream ss(users);
    while (ss.good())
    {
        string substr;
        getline(ss, substr, ',');
        if(substr != EMPTY_STRING)
        {
            shared_ptr<UserInfo> user (new UserInfo);
            user->username = substr;
            addUser(user);
        }
    }
    return;
}

/**
 * return true if username is found in set, username is admin or empty,
 * or users list is empty
 */
bool SensorInfo::checkUser(string username)
{
    std::lock_guard<std::mutex> guard(m_userLock);
    if (username == DEFAULT_ADMIN_USERNAME || username == EMPTY_STRING || users.size() == 0)
    {
        return true;
    }
    for (auto itr: users)
    {
        if (itr->username == username)
        {
            return true;
        }
    }
    return false;
}

ClientSession::~ClientSession()
{
    if (m_curl)
    {
        curl_easy_cleanup(m_curl);
    }
}

ClientSession::ClientSession()
{
    m_curl = curl_easy_init();
    m_nvsoap = std::make_shared<NvSoap>();

    if (!m_curl || !m_nvsoap)
    {
        LOG(error) << "Failed to initialize client session" << endl;
    }
}

CURL* ClientSession::getCurlClient()
{
    if (m_curl != nullptr)
    {
        return m_curl;
    }

    return nullptr;
}

std::shared_ptr<NvSoap> ClientSession::getNvSoap()
{
    if (m_nvsoap != nullptr)
    {
        return m_nvsoap;
    }

    return nullptr;
}

std::shared_ptr<ClientSession>& SensorInfo::getClientSession()
{
    std::lock_guard<std::mutex> lock(sessionMutex);
    if (clientSession == nullptr)
    {
        clientSession = std::make_shared<ClientSession>();
    }
    return clientSession;
}

Json::Value SensorInfo::getStreamsJson(bool isStreamerDevice)
{
    Json::Value response(Json::arrayValue);
    for (const auto& stream : streams)
    {
        /* For file sensor i.e. external file upload, use stream id as name only for sub streams
         * if the stream name is not already set (e.g., user-provided streamName in metadata).
         * for MainStreams name will be : uploaded_file_name or user-provided streamName
         * for Sub streams name will be : UUID_uploaded_file_name or user-provided streamName */
        if (SENSOR_TYPE_FILE == this->type && stream->isMainStream == false && stream->name.empty())
        {
            stream->name = stream->id;
        }
        response.append(stream->toJson(isStreamerDevice));
    }
    return response;
}
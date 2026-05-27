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

#include "remote_device.h"
#include "cmdline_parser.h"
#include "Websocket.h"
#include "utils.h"

#define SENSOR_MONITOR_THREAD_COUNT 1
#define SENSOR_MONITOR_INTERVAL 20
#define DATA_CHANNEL_WAIT_TIME 10000
#define CHECK_VALUE_IF_ERROR_RETURN(v, l, u)                                          \
    if (!(v.empty() || l.empty() || u.empty()) && valueWithinRange(v, l, u) == false) \
    {                                                                                 \
        LOG(error) << "Camera parameter Value : " << v << " is wrong" << endl;        \
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);                 \
        return VmsErrorCode::InvalidParameterError;                                   \
    }
#define CONTAINS_VALUE_IF_ERROR_RETURN(v, vec)                           \
    if (!v.empty() && std::find(vec.begin(), vec.end(), v) == vec.end()) \
    {                                                                    \
        LOG(error) << "Camera parameter Value is wrong" << endl;         \
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);    \
        return VmsErrorCode::InvalidParameterError;                      \
    }

extern "C" ISensorControlInterface *createObject()
{
    return new RemoteDevice;
}

extern "C" void destroyObject(RemoteDevice *object)
{
    delete object;
}

static void fillEncoderSettingsOptions(const Json::Value &jsettings, VideoEncoderConfigurationsOptions& settings);
static void fillSensorEncoderSettingsOptions(const Json::Value &jsettings, SensorEncoderSettingsOptions &settings);
static void fillSensorVideoEncoderSettingsValues(const Json::Value &jsettings, SensorVideoEncoderSettingsValues &settings);
static void fillSensorImageSettingsValues(const Json::Value &jsettings, SensorImageSettingsValues &settings);
static void fillSensorImageSettingsOptions(const Json::Value &jsettings, SensorImageSettingsOptions &settings);
static void sensorImageSettingsValuesToJson(const SensorImageSettingsValues &settings, Json::Value &jsettings);
static void sensorVideoEncoderSettingsValuesToJson(const SensorVideoEncoderSettingsValues &settings, Json::Value &jsettings);
static void fillSensorNetworkInfo(const Json::Value &jsettings, SensorNetworkInfo& networkInfo);
static void sensorNetworkInfoValuesToJson(const SensorNetworkInfo &netInfo, Json::Value &jsettings);
static void sensorInfoValuesToJson(const SensorInfo &sensorInfo, Json::Value &jsettings);


int RemoteDevice::connect()
{
    LOG(verbose) << __PRETTY_FUNCTION__ << endl;
    std::chrono::seconds monitorInterval(SENSOR_MONITOR_INTERVAL);
    m_sensorStatusMonitoring = make_unique<Bosma::Scheduler>(SENSOR_MONITOR_THREAD_COUNT);
    m_sensorStatusMonitoring->interval(monitorInterval, [=]() {
        syncSensorStatus();
    });
    return 0;
}

void RemoteDevice::syncSensorStatus()
{
    std::vector<shared_ptr<SensorInfo>>::iterator it;
    set<pair<string, string>> remoteIds;
    for (auto sensor : m_cacheSensorList)
    {
        if (sensor.get() && sensor->isRemoteSensor)
        {
            remoteIds.insert(make_pair(sensor->id, sensor->remoteDeviceId));
        }
    }
    for (auto &asyncTasks: m_dataChannelTasks)
    {
        asyncTasks.get();
    }
    m_dataChannelTasks.clear();
    for (const auto& remoteId : remoteIds)
    {
        m_dataChannelTasks.push_back(async::spawn([=]() -> void
        {
            syncSensorStatus(remoteId);
        }));
    }
}

int RemoteDevice::getSensorStreamInfo(vector<shared_ptr<SensorInfo>> &sensors)
{
    return 0;
}
int RemoteDevice::getSensorStreamInfo(shared_ptr<SensorInfo> &sensor)
{
    return 0;
}

bool RemoteDevice::validateCredentials(shared_ptr<SensorInfo>& sensor, const string username, const string password)
{
    VmsErrorCode err = VmsErrorCode::NoError;
    Json::Value credentials = Json::nullValue;
    Json::Value response = Json::nullValue;
    credentials["username"] = username;
    credentials["password"] = password;
    err = validateCredentials(sensor, credentials, response);
    if (err != VmsErrorCode::NoError)
    {
        return false;
    }
    return true;
}

void RemoteDevice::syncSensorStatus(pair<string, string> sensorInfo)
{
    std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
    Json::Value msg = Json::objectValue;
    Json::Value data = Json::objectValue;
    Json::Value apiPayload = Json::nullValue;
    Json::Value response = Json::nullValue;
    msg["sensorId"] = sensorInfo.first;
    msg["apiKey"] = "api/v1/sensor/status";
    msg["data"] = apiPayload;
    msg["requestMethod"] = string("get");
    msg["clientId"] = sensorInfo.second;
    object->m_responseId = generate_uuid();
    msg["requestId"] = object->m_responseId;
    bool isSendSuccess = m_messageBus->sendMessage(sensorInfo.second, jsonToString(msg), object);
    if (!isSendSuccess)
    {
        LOG(error) << "Failed to send WS message" << endl;
        return;
    }
    LOG(verbose2) << "Waiting for response with ID: " << object->m_responseId << endl;
    if (object->m_isResponseReceived == false)
    {
        object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
    }
    LOG(verbose2) << "Response received for ID " << object->m_responseId << endl;
    LOG(verbose2) << "Response: " << object->m_response.toStyledString() << endl;
    response = object->m_response;
    if (response.isObject())
    {
        for (auto sensor : m_cacheSensorList)
        {
            if (sensor.get() && sensor->id == sensorInfo.first)
            {
                if (response.isMember("errorCode"))
                {
                    const string errorString = response.get("errorCode", EMPTY_STRING).asString();
                    sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(getCameraErrorCode(errorString)));
                }
                if (response.isMember("state"))
                {
                    const string statusString = response.get("state", EMPTY_STRING).asString();
                    SensorStatusEvent status = statusString == "offline" ? SensorStatusEvent::SensorStatusOffline : SensorStatusEvent::SensorStatusOnline;
                    sensor->updateSensorStatus(status);
                }
                break;
            }
        }
    }
    else
    {
        LOG(error) << "Failed to sync sensor status,  sensor ID: " << sensorInfo.first << endl;
    }
}

int RemoteDevice::getSensorImageSettings(shared_ptr<SensorInfo> &sensor, const string &stream_id, SensorSettings &settings)
{
    LOG(verbose) << __PRETTY_FUNCTION__ << endl;
    Json::Value imageSettings = Json::nullValue;
    VmsErrorCode err = getSensorSettings(sensor, "Image", imageSettings);
    if (err != VmsErrorCode::NoError)
    {
        return -1;
    }
    // Fill the SensorImageSettingsOptions struct
    SensorImageSettingsOptions imageSettingsOptions;
    fillSensorImageSettingsOptions(imageSettings, imageSettingsOptions);
    settings.imageOptions = imageSettingsOptions;

    // Fill the SensorImageSettingsValues struct
    SensorImageSettingsValues imageSettingsValues;
    fillSensorImageSettingsValues(imageSettings, imageSettingsValues);
    settings.imageValues = imageSettingsValues;

    return 0;
}

int RemoteDevice::setSensorImageSettings(shared_ptr<SensorInfo> &sensor, const SensorImageSettingsValues &settings)
{
    Json::Value jsettings;
    Json::Value response;
    Json::Value data;
    VmsErrorCode err = VmsErrorCode::NoError;
    sensorImageSettingsValuesToJson(settings, jsettings);
    data["Image"] = jsettings;
    err = setSensorSettings(sensor, data, response);
    if (err != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to set image settings" << endl;
        return -1;
    }
    return 0;
}

int RemoteDevice::getSensorEncodeSettings(shared_ptr<SensorInfo> &sensor, const string &stream_id, SensorSettings &settings)
{
    LOG(verbose) << __PRETTY_FUNCTION__ << endl;
    Json::Value jencodeSettings = Json::nullValue;
    VmsErrorCode err = getSensorSettings(sensor, "Encode", jencodeSettings);
    if (err != VmsErrorCode::NoError)
    {
        return -1;
    }

    // Fill the SensorEncoderSettingsOptions struct
    SensorEncoderSettingsOptions encodeSettingsOptions;
    fillSensorEncoderSettingsOptions(jencodeSettings, encodeSettingsOptions);
    settings.encoderOptions = encodeSettingsOptions;

    // Fill the SensorImageSettingsValues struct
    SensorVideoEncoderSettingsValues encodeSettingsValues;
    fillSensorVideoEncoderSettingsValues(jencodeSettings, encodeSettingsValues);
    settings.encoderValues = encodeSettingsValues;

    return 0;
}

int RemoteDevice::setSensorEncodeSettings(shared_ptr<SensorInfo> &sensor, const SensorVideoEncoderSettingsValues &settings)
{
    Json::Value jsettings;
    Json::Value response;
    Json::Value data;
    VmsErrorCode err = VmsErrorCode::NoError;
    sensorVideoEncoderSettingsValuesToJson(settings, jsettings);
    data["Encode"] = jsettings;
    err = setSensorSettings(sensor, data, response);
    if (err != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to set encode settings" << endl;
        return -1;
    }
    return 0;
}

VmsErrorCode RemoteDevice::addSensor(const Json::Value& sensorInfo)
{
    Json::Value response = Json::nullValue;
    /* Edge to cloud use case. Forward the request to edge device via datachannel and wait for its response */
    if (!sensorInfo.empty())
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["apiKey"] = "api/v1/sensor/add";
        msg["data"] = sensorInfo;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        string remoteDeviceId = sensorInfo.get("remoteDeviceId", EMPTY_STRING).asString();
        msg["clientId"] = remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response =  object->m_response;
        if(response.isBool() && response.asBool())
        {
            LOG(info) << "sensor added successfully" << endl;
            return VmsErrorCode::NoError;
        }
        else if(response.isObject() && response.isMember("sensorId"))
        {
            LOG(info) << "sensor added successfully" << endl;
            return VmsErrorCode::NoError;
        }
        else if(response.isObject() && response.isMember("error_code"))
        {
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::CameraNotFoundError;
}

bool RemoteDevice::deleteSensor(shared_ptr<SensorInfo>& sensor)
{
    Json::Value response = Json::nullValue;
    VmsErrorCode err = VmsErrorCode::NoError;
    err = deleteSensor(sensor, response);
    if (err != VmsErrorCode::NoError)
    {
        return false;
    }
    return true;
}

VmsErrorCode RemoteDevice::deleteSensor(shared_ptr<SensorInfo>& sensor, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/remove";
        msg["data"] = Json::nullValue;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("delete");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response =  object->m_response;
        if(response.isBool() && response.asBool())
        {
            LOG(info) << "sensor removed successfully, deleting local sensor" << endl;
            return VmsErrorCode::NoError;
        }
        else if(response.isObject() && response.isMember("error_code"))
        {
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::CameraNotFoundError;
}

VmsErrorCode RemoteDevice::validateCredentials(shared_ptr<SensorInfo>& sensor, Json::Value &credentials, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/credentials";
        msg["data"] = credentials;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response =  object->m_response;
        if(response.isBool() && response.asBool())
        {
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            return VmsErrorCode::NoError;
        }
        else if(response.isObject() && response.isMember("error_code"))
        {
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::CameraNotFoundError;
}

VmsErrorCode RemoteDevice::getSensorSettings(shared_ptr<SensorInfo> &sensor, const string &type, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/settings";
        msg["data"] = Json::nullValue;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("get");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(verbose2) << "Response: \n" << object->m_response.toStyledString() << endl;
        response = object->m_response;
        if (response.isObject() && response.isMember("error_code"))
        {
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else if (response.isObject() && response.isMember(sensor->id))
        {
            Json::Value sensorSettings = response.get(sensor->id, Json::nullValue);
            if (type == "Image")
            {
                if (sensorSettings.isMember("Image"))
                {
                    response = sensorSettings.get("Image", Json::nullValue);
                }
            }
            else if (type == "Encode")
            {
                if (sensorSettings.isMember("Encode"))
                {
                    response = sensorSettings.get("Encode", Json::nullValue);
                }
            }
            else
            {
                response = sensorSettings;
            }
            LOG(info) << "Successfully got sensor settings" << endl;
            return VmsErrorCode::NoError;
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::CameraNotFoundError;
}

VmsErrorCode RemoteDevice::setSensorSettings(shared_ptr<SensorInfo> &sensor, const Json::Value &settings, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/settings";
        msg["data"] = settings;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("post");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response = object->m_response;
        if (response.isBool() && response.asBool())
        {
            LOG(info) << "Successfully set sensor settings" << endl;
            return VmsErrorCode::NoError;
        }
        else if (response.isObject() && response.isMember("error_code"))
        {
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::CameraNotFoundError;
}

static void fillEncoderSettingsOptions(const Json::Value &jsettings, VideoEncoderConfigurationsOptions& settings)
{
    // FrameRateRange
    if (jsettings.isMember("FrameRate") && jsettings["FrameRate"].isMember("AllowedValues"))
    {
        std::string resultFrameRates;
        const Json::Value &framerates = jsettings["FrameRate"]["AllowedValues"];
        for (unsigned int i = 0; i < framerates.size(); ++i)
        {
            resultFrameRates += framerates[i].asString();
            if (i != framerates.size()-1)
            {
                resultFrameRates += " ";
            }
        }

        if (!resultFrameRates.empty())
        {
            settings.FrameRateSupported = resultFrameRates;
        }
    }

    // EncodingIntervalRange
    if (jsettings.isMember("EncodingInterval"))
    {
        if (jsettings["EncodingInterval"].isMember("Min"))
        {
            settings.EncodingIntervalRange.min = jsettings["EncodingInterval"]["Min"].asString();
        }
        if (jsettings["EncodingInterval"].isMember("Max"))
        {
            settings.EncodingIntervalRange.max = jsettings["EncodingInterval"]["Max"].asString();
        }
    }

    // BitrateRange
    if (jsettings.isMember("Bitrate"))
    {
        if (jsettings["Bitrate"].isMember("Min"))
        {
            settings.BitrateRange.min = jsettings["Bitrate"]["Min"].asString();
        }
        if (jsettings["Bitrate"].isMember("Max"))
        {
            settings.BitrateRange.max = jsettings["Bitrate"]["Max"].asString();
        }
    }

    // GovLengthRange
    if (jsettings.isMember("GovLength"))
    {
        if (jsettings["GovLength"].isMember("Min"))
        {
            settings.GovLengthRange.min = jsettings["GovLength"]["Min"].asString();
        }
        if (jsettings["GovLength"].isMember("Max"))
        {
            settings.GovLengthRange.max = jsettings["GovLength"]["Max"].asString();
        }
    }

    // ResolutionsAvailable
    if (jsettings.isMember("Resolution") && jsettings["Resolution"].isMember("AllowedValues"))
    {
        const Json::Value &resolutions = jsettings["Resolution"]["AllowedValues"];
        for (unsigned int i = 0; i < resolutions.size(); ++i)
        {
            if (resolutions[i].isMember("Width") && resolutions[i].isMember("Height"))
            {
                Resolution resolution;
                resolution.width = resolutions[i]["Width"].asString();
                resolution.height = resolutions[i]["Height"].asString();
                settings.ResolutionsAvailable.push_back(resolution);
            }
        }
    }
}

static void parseEncodingOptions(const Json::Value& jsettings, VideoEncoderConfigurationsOptions& settings)
{
    // QualityRange
    if (jsettings.isMember("Quality"))
    {
        if (jsettings["Quality"].isMember("Min"))
        {
            settings.qualityRange.min = jsettings["Quality"]["Min"].asString();
        }
        if (jsettings["Quality"].isMember("Max"))
        {
            settings.qualityRange.max = jsettings["Quality"]["Max"].asString();
        }
    }

    // ProfilesSupported
    if (jsettings.isMember("Profiles") && jsettings["Profiles"].isMember("AllowedValues"))
    {
        const Json::Value &profiles = jsettings["Profiles"]["AllowedValues"];
        for (unsigned int i = 0; i < profiles.size(); ++i)
        {
            settings.profilesSupported.push_back(profiles[i].asString());
        }
    }

    // Fill the other encode settings
    fillEncoderSettingsOptions(jsettings, settings);
}

static void fillSensorEncoderSettingsOptions(const Json::Value &jsettings, SensorEncoderSettingsOptions &settings)
{
    for (const auto& val : jsettings["Encoding"]["AllowedValues"])
    {
        settings.videoEncodingSupported.push_back(val.asString());
    }

    for (const auto& val : jsettings["Encoding"]["AllowedValues"])
    {
        VideoEncoderConfigurationsOptions encoderOption;
        // Iterate through allowed values and parse options for each encoding type
        encoderOption.encoding = val.asString();
        const auto& options = jsettings["Options"];
        for (const auto& option : options)
        {
            if (option.isMember(encoderOption.encoding))
            {
                const auto& encodingOption = option[encoderOption.encoding];
                parseEncodingOptions(encodingOption, encoderOption);
            }
        }
        settings.encoderSettingsOptions.push_back(encoderOption);
    }
}

static void fillSensorVideoEncoderSettingsValues(const Json::Value &jsettings, SensorVideoEncoderSettingsValues &settings)
{
    std::string encoding;
    // Encoding
    if (jsettings.isMember("Encoding") && jsettings["Encoding"].isMember("Value"))
    {
        encoding = jsettings["Encoding"]["Value"].asString();
    }

    if (encoding.empty())
    {
        LOG(error) << "Encoding value is not present" << endl;
        return;
    }

    const auto& options = jsettings["Options"];
    for (const auto& option : options)
    {
        if (option.isMember(encoding))
        {
            const auto& encodingSet = option[encoding];
            // Encoding
            settings.encoding = encoding;

            // Resolution
            if (encodingSet.isMember("Resolution") && encodingSet["Resolution"].isMember("Value"))
            {
                const Json::Value &resolution = encodingSet["Resolution"]["Value"];
                if (resolution.isMember("Width") && resolution.isMember("Height"))
                {
                    settings.resolution.width = resolution["Width"].asString();
                    settings.resolution.height = resolution["Height"].asString();
                }
            }

            // FrameRate
            if (encodingSet.isMember("FrameRate") && encodingSet["FrameRate"].isMember("Value"))
            {
                settings.frameRate = encodingSet["FrameRate"]["Value"].asString();
            }

            // Bitrate
            if (encodingSet.isMember("Bitrate") && encodingSet["Bitrate"].isMember("Value"))
            {
                settings.bitrate = encodingSet["Bitrate"]["Value"].asString();
            }

            // EncodingInterval
            if (encodingSet.isMember("EncodingInterval") && encodingSet["EncodingInterval"].isMember("Value"))
            {
                settings.encodingInterval = encodingSet["EncodingInterval"]["Value"].asString();
            }

            // Quality
            if (encodingSet.isMember("Quality") && encodingSet["Quality"].isMember("Value"))
            {
                settings.quality = encodingSet["Quality"]["Value"].asString();
            }

            // GovLength
            if (encodingSet.isMember("GovLength") && encodingSet["GovLength"].isMember("Value"))
            {
                settings.govLength = encodingSet["GovLength"]["Value"].asString();
            }

            // Profiles
            if (encodingSet.isMember("Profiles") && encodingSet["Profiles"].isMember("Value"))
            {
                settings.encodingProfile = encodingSet["Profiles"]["Value"].asString();
            }

            break;
        }
    }
}

static void fillSensorImageSettingsValues(const Json::Value &jsettings, SensorImageSettingsValues &settings)
{
    // Brightness
    if (jsettings.isMember("Brightness") && jsettings["Brightness"].isMember("Value"))
    {
        settings.Brightness = jsettings["Brightness"]["Value"].asString();
    }

    // ColorSaturation
    if (jsettings.isMember("ColorSaturation") && jsettings["ColorSaturation"].isMember("Value"))
    {
        settings.ColorSaturation = jsettings["ColorSaturation"]["Value"].asString();
    }

    // Contrast
    if (jsettings.isMember("Contrast") && jsettings["Contrast"].isMember("Value"))
    {
        settings.Contrast = jsettings["Contrast"]["Value"].asString();
    }

    // Sharpness
    if (jsettings.isMember("Sharpness") && jsettings["Sharpness"].isMember("Value"))
    {
        settings.Sharpness = jsettings["Sharpness"]["Value"].asString();
    }

    // BacklightCompensationMode
    if (jsettings.isMember("BacklightCompensationMode") && jsettings["BacklightCompensationMode"].isMember("Value"))
    {
        settings.BacklightCompensationMode = jsettings["BacklightCompensationMode"]["Value"].asString();
    }

    // BacklightCompensationLevel
    if (jsettings.isMember("BacklightCompensationLevel") && jsettings["BacklightCompensationLevel"].isMember("Value"))
    {
        settings.BacklightCompensationLevel = jsettings["BacklightCompensationLevel"]["Value"].asString();
    }

    // ExposureMode
    if (jsettings.isMember("ExposureMode") && jsettings["ExposureMode"].isMember("Value"))
    {
        settings.ExposureMode = jsettings["ExposureMode"]["Value"].asString();
    }

    // ExposurePriority
    if (jsettings.isMember("ExposurePriority") && jsettings["ExposurePriority"].isMember("Value"))
    {
        settings.ExposurePriority = jsettings["ExposurePriority"]["Value"].asString();
    }

    // ExposureWindow
    if (jsettings.isMember("ExposureWindow"))
    {
        const Json::Value &exposureWindow = jsettings["ExposureWindow"];
        if (jsettings["ExposureWindow"].isMember("bottom"))
        {
            settings.ExposureWindow.bottom = exposureWindow["bottom"].asString();
        }
        if (jsettings["ExposureWindow"].isMember("left"))
        {
            settings.ExposureWindow.left = exposureWindow["left"].asString();
        }
        if (jsettings["ExposureWindow"].isMember("right"))
        {
            settings.ExposureWindow.right = exposureWindow["right"].asString();
        }
        if (jsettings["ExposureWindow"].isMember("top"))
        {
            settings.ExposureWindow.top = exposureWindow["top"].asString();
        }
    }

    // MinExposureTime
    if (jsettings.isMember("MinExposureTime") && jsettings["MinExposureTime"].isMember("Value"))
    {
        settings.MinExposureTime = jsettings["MinExposureTime"]["Value"].asString();
    }

    // MaxExposureTime
    if (jsettings.isMember("MaxExposureTime") && jsettings["MaxExposureTime"].isMember("Value"))
    {
        settings.MaxExposureTime = jsettings["MaxExposureTime"]["Value"].asString();
    }

    // ExposureMaxGain
    if (jsettings.isMember("ExposureMaxGain") && jsettings["ExposureMaxGain"].isMember("Value"))
    {
        settings.ExposureMaxGain = jsettings["ExposureMaxGain"]["Value"].asString();
    }

    // ExposureTime
    if (jsettings.isMember("ExposureTime") && jsettings["ExposureTime"].isMember("Value"))
    {
        settings.ExposureTime = jsettings["ExposureTime"]["Value"].asString();
    }

    // ExposureGain
    if (jsettings.isMember("ExposureGain") && jsettings["ExposureGain"].isMember("Value"))
    {
        settings.ExposureGain = jsettings["ExposureGain"]["Value"].asString();
    }

    // IrCutFilterMode
    if (jsettings.isMember("IrCutFilterMode") && jsettings["IrCutFilterMode"].isMember("Value"))
    {
        settings.IrCutFilterMode = jsettings["IrCutFilterMode"]["Value"].asString();
    }

    // WideDynamicRangeMode
    if (jsettings.isMember("WideDynamicRangeMode") && jsettings["WideDynamicRangeMode"].isMember("Value"))
    {
        settings.WideDynamicRangeMode = jsettings["WideDynamicRangeMode"]["Value"].asString();
    }

    // WideDynamicRangeLevel
    if (jsettings.isMember("WideDynamicRangeLevel") && jsettings["WideDynamicRangeLevel"].isMember("Value"))
    {
        settings.WideDynamicRangeLevel = jsettings["WideDynamicRangeLevel"]["Value"].asString();
    }

    // WhiteBalanceMode
    if (jsettings.isMember("WhiteBalanceMode") && jsettings["WhiteBalanceMode"].isMember("Value"))
    {
        settings.WhiteBalanceMode = jsettings["WhiteBalanceMode"]["Value"].asString();
    }

    // WhiteBalanceYrGain
    if (jsettings.isMember("WhiteBalanceYrGain") && jsettings["WhiteBalanceYrGain"].isMember("Value"))
    {
        settings.WhiteBalanceYrGain = jsettings["WhiteBalanceYrGain"]["Value"].asString();
    }

    // WhiteBalanceYbGain
    if (jsettings.isMember("WhiteBalanceYbGain") && jsettings["WhiteBalanceYbGain"].isMember("Value"))
    {
        settings.WhiteBalanceYbGain = jsettings["WhiteBalanceYbGain"]["Value"].asString();
    }
}

static void fillSensorImageSettingsOptions(const Json::Value &jsettings, SensorImageSettingsOptions &settings)
{
    // Brightness
    if (jsettings.isMember("Brightness"))
    {
        if (jsettings["Brightness"].isMember("Min"))
        {
            settings.Brightness.min = jsettings["Brightness"]["Min"].asString();
        }
        if (jsettings["Brightness"].isMember("Max"))
        {
            settings.Brightness.max = jsettings["Brightness"]["Max"].asString();
        }
    }

    // ColorSaturation
    if (jsettings.isMember("ColorSaturation"))
    {
        if (jsettings["ColorSaturation"].isMember("Min"))
        {
            settings.ColorSaturation.min = jsettings["ColorSaturation"]["Min"].asString();
        }
        if (jsettings["ColorSaturation"].isMember("Max"))
        {
            settings.ColorSaturation.max = jsettings["ColorSaturation"]["Max"].asString();
        }
    }

    // Contrast
    if (jsettings.isMember("Contrast"))
    {
        if (jsettings["Contrast"].isMember("Min"))
        {
            settings.Contrast.min = jsettings["Contrast"]["Min"].asString();
        }
        if (jsettings["Contrast"].isMember("Max"))
        {
            settings.Contrast.max = jsettings["Contrast"]["Max"].asString();
        }
    }

    // Sharpness
    if (jsettings.isMember("Sharpness"))
    {
        if (jsettings["Sharpness"].isMember("Min"))
        {
            settings.Sharpness.min = jsettings["Sharpness"]["Min"].asString();
        }
        if (jsettings["Sharpness"].isMember("Max"))
        {
            settings.Sharpness.max = jsettings["Sharpness"]["Max"].asString();
        }
    }

    // BacklightCompensationModes
    if (jsettings.isMember("BacklightCompensationMode") &&
        jsettings["BacklightCompensationMode"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["BacklightCompensationMode"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.BacklightCompensationModes.push_back(allowedValues[i].asString());
        }
    }

    // BacklightCompensationLevel
    if (jsettings.isMember("BacklightCompensationLevel"))
    {
        if (jsettings["BacklightCompensationLevel"].isMember("Min"))
        {
            settings.BacklightCompensationLevel.min = jsettings["BacklightCompensationLevel"]["Min"].asString();
        }
        if (jsettings["BacklightCompensationLevel"].isMember("Max"))
        {
            settings.BacklightCompensationLevel.max = jsettings["BacklightCompensationLevel"]["Max"].asString();
        }
    }

    // ExposureModes
    if (jsettings.isMember("ExposureMode") &&
        jsettings["ExposureMode"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["ExposureMode"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.ExposureModes.push_back(allowedValues[i].asString());
        }
    }

    // ExposurePriorities
    if (jsettings.isMember("ExposurePriority") &&
        jsettings["ExposurePriority"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["ExposurePriority"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.ExposurePriorities.push_back(allowedValues[i].asString());
        }
    }

    // MinExposureTime
    if (jsettings.isMember("MinExposureTime"))
    {
        if (jsettings["MinExposureTime"].isMember("Min"))
        {
            settings.MinExposureTime.min = jsettings["MinExposureTime"]["Min"].asString();
        }
        if (jsettings["MinExposureTime"].isMember("Max"))
        {
            settings.MinExposureTime.max = jsettings["MinExposureTime"]["Max"].asString();
        }
    }

    // MaxExposureTime
    if (jsettings.isMember("MaxExposureTime"))
    {
        if (jsettings["MaxExposureTime"].isMember("Min"))
        {
            settings.MaxExposureTime.min = jsettings["MaxExposureTime"]["Min"].asString();
        }
        if (jsettings["MaxExposureTime"].isMember("Max"))
        {
            settings.MaxExposureTime.max = jsettings["MaxExposureTime"]["Max"].asString();
        }
    }

    // ExposureMaxGain
    if (jsettings.isMember("ExposureMaxGain"))
    {
        if (jsettings["ExposureMaxGain"].isMember("Min"))
        {
            settings.ExposureMaxGain.min = jsettings["ExposureMaxGain"]["Min"].asString();
        }
        if (jsettings["ExposureMaxGain"].isMember("Max"))
        {
            settings.ExposureMaxGain.max = jsettings["ExposureMaxGain"]["Max"].asString();
        }
    }

    // ExposureTime
    if (jsettings.isMember("ExposureTime"))
    {
        if (jsettings["ExposureTime"].isMember("Min"))
        {
            settings.ExposureTime.min = jsettings["ExposureTime"]["Min"].asString();
        }
        if (jsettings["ExposureTime"].isMember("Max"))
        {
            settings.ExposureTime.max = jsettings["ExposureTime"]["Max"].asString();
        }
    }

    // ExposureGain
    if (jsettings.isMember("ExposureGain"))
    {
        if (jsettings["ExposureGain"].isMember("Min"))
        {
            settings.ExposureGain.min = jsettings["ExposureGain"]["Min"].asString();
        }
        if (jsettings["ExposureGain"].isMember("Max"))
        {
            settings.ExposureGain.max = jsettings["ExposureGain"]["Max"].asString();
        }
    }

    // IrCutFilterModes
    if (jsettings.isMember("IrCutFilterMode") &&
        jsettings["IrCutFilterMode"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["IrCutFilterMode"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.IrCutFilterModes.push_back(allowedValues[i].asString());
        }
    }

    // WideDynamicRangeModes
    if (jsettings.isMember("WideDynamicRangeMode") &&
        jsettings["WideDynamicRangeMode"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["WideDynamicRangeMode"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.WideDynamicRangeModes.push_back(allowedValues[i].asString());
        }
    }

    // WideDynamicRangeLevel
    if (jsettings.isMember("WideDynamicRangeLevel"))
    {
        if (jsettings["WideDynamicRangeLevel"].isMember("Min"))
        {
            settings.WideDynamicRangeLevel.min = jsettings["WideDynamicRangeLevel"]["Min"].asString();
        }
        if (jsettings["WideDynamicRangeLevel"].isMember("Max"))
        {
            settings.WideDynamicRangeLevel.max = jsettings["WideDynamicRangeLevel"]["Max"].asString();
        }
    }

    // WhiteBalanceModes
    if (jsettings.isMember("WhiteBalanceMode") &&
        jsettings["WhiteBalanceMode"].isMember("AllowedValues"))
    {
        const Json::Value &allowedValues = jsettings["WhiteBalanceMode"]["AllowedValues"];
        for (unsigned int i = 0; i < allowedValues.size(); ++i)
        {
            settings.WhiteBalanceModes.push_back(allowedValues[i].asString());
        }
    }

    // WhiteBalanceYrGain
    if (jsettings.isMember("WhiteBalanceYrGain"))
    {
        if (jsettings["WhiteBalanceYrGain"].isMember("Min"))
        {
            settings.WhiteBalanceYrGain.min = jsettings["WhiteBalanceYrGain"]["Min"].asString();
        }
        if (jsettings["WhiteBalanceYrGain"].isMember("Max"))
        {
            settings.WhiteBalanceYrGain.max = jsettings["WhiteBalanceYrGain"]["Max"].asString();
        }
    }

    // WhiteBalanceYbGain
    if (jsettings.isMember("WhiteBalanceYbGain"))
    {
        if (jsettings["WhiteBalanceYbGain"].isMember("Min"))
        {
            settings.WhiteBalanceYbGain.min = jsettings["WhiteBalanceYbGain"]["Min"].asString();
        }
        if (jsettings["WhiteBalanceYbGain"].isMember("Max"))
        {
            settings.WhiteBalanceYbGain.max = jsettings["WhiteBalanceYbGain"]["Max"].asString();
        }
    }
}

static void sensorImageSettingsValuesToJson(const SensorImageSettingsValues &settings, Json::Value &jsettings)
{
    jsettings["BacklightCompensationLevel"] = settings.BacklightCompensationLevel;
    jsettings["BacklightCompensationMode"] = settings.BacklightCompensationMode;
    jsettings["Brightness"] = settings.Brightness;
    jsettings["ColorSaturation"] = settings.ColorSaturation;
    jsettings["Contrast"] = settings.Contrast;
    jsettings["ExposureGain"] = settings.ExposureGain;
    jsettings["ExposureMaxGain"] = settings.ExposureMaxGain;
    jsettings["ExposureMode"] = settings.ExposureMode;
    jsettings["ExposurePriority"] = settings.ExposurePriority;
    jsettings["ExposureTime"] = settings.ExposureTime;
    jsettings["IrCutFilterMode"] = settings.IrCutFilterMode;
    jsettings["MaxExposureTime"] = settings.MaxExposureTime;
    jsettings["MinExposureTime"] = settings.MinExposureTime;
    jsettings["Sharpness"] = settings.Sharpness;
    jsettings["WhiteBalanceMode"] = settings.WhiteBalanceMode;
    jsettings["WhiteBalanceYbGain"] = settings.WhiteBalanceYbGain;
    jsettings["WhiteBalanceYrGain"] = settings.WhiteBalanceYrGain;
    jsettings["WideDynamicRangeLevel"] = settings.WideDynamicRangeLevel;
    jsettings["WideDynamicRangeMode"] = settings.WideDynamicRangeMode;
}

static void sensorVideoEncoderSettingsValuesToJson(const SensorVideoEncoderSettingsValues &settings, Json::Value &jsettings)
{
    jsettings["Bitrate"] = settings.bitrate;
    jsettings["Encoding"] = settings.encoding;
    jsettings["EncodingInterval"] = settings.encodingInterval;
    jsettings["FrameRate"] = settings.frameRate;
    jsettings["GovLength"] = settings.govLength;
    jsettings["Profiles"] = settings.encodingProfile;
    jsettings["Quality"] = settings.quality;

    Json::Value resolution;
    resolution["Height"] = settings.resolution.height;
    resolution["Width"] = settings.resolution.width;
    jsettings["Resolution"] = resolution;
}

static void sensorInfoValuesToJson(const SensorInfo &sensorInfo, Json::Value &jsettings)
{
    jsettings["sensorId"] = sensorInfo.id;
    jsettings["name"] = sensorInfo.name;
    jsettings["hardware"] = sensorInfo.hardware;
    jsettings["manufacturer"] = sensorInfo.manufacturer;
    jsettings["serialNumber"] = sensorInfo.serial_number;
    jsettings["firmwareVersion"] = sensorInfo.firmware_version;
    jsettings["hardwareId"] = sensorInfo.hardware_id;
    jsettings["location"] = sensorInfo.location;
    jsettings["position"]["tags"] = sensorInfo.tags;
    jsettings["position"]["depth"] = sensorInfo.position.depth;
    jsettings["position"]["fieldOfView"] = sensorInfo.position.fieldOfView;
    jsettings["position"]["direction"] = sensorInfo.position.direction;
    jsettings["position"]["origin"]["latitude"] = sensorInfo.position.origin.first;
    jsettings["position"]["origin"]["longitude"] = sensorInfo.position.origin.second;
    jsettings["position"]["coordinates"]["x"] = sensorInfo.position.coordinates.first;
    jsettings["position"]["coordinates"]["y"] = sensorInfo.position.coordinates.second;
    jsettings["position"]["geoLocation"]["latitude"] = sensorInfo.position.geoLocation.first;
    jsettings["position"]["geoLocation"]["longitude"] = sensorInfo.position.geoLocation.second;
}

static void sensorNetworkInfoValuesToJson(const SensorNetworkInfo &netInfo, Json::Value &jsettings)
{
    jsettings["isIpv4Enabled"] = netInfo.enableIpv4;
    jsettings["dhcpV4"] = netInfo.enableDhcp4;
    jsettings["ipAddressV4"] = netInfo.IPAddr4;
    jsettings["subnetMaskV4"] = netInfo.prefixLen4;

    jsettings["isIpv6Enabled"] = netInfo.enableIpv6;
    jsettings["dhcpV6"] = netInfo.enableDhcp6;
    jsettings["ipAddressV6"] = netInfo.IPAddr6;
    jsettings["subnetMaskV6"] = netInfo.prefixLen6;
}

static void fillSensorNetworkInfo(const Json::Value &jsettings, SensorNetworkInfo& networkInfo)
{
    if (jsettings.isMember("isIpv4Enabled"))
    {
        networkInfo.enableIpv4 = jsettings["isIpv4Enabled"].asBool();
    }

    if (jsettings.isMember("dhcpV4"))
    {
        networkInfo.enableDhcp4 = jsettings["dhcpV4"].asString();
    }

    if (jsettings.isMember("ipAddressV4"))
    {
       networkInfo.IPAddr4 = jsettings["ipAddressV4"].asString();
    }

    if (jsettings.isMember("subnetMaskV4"))
    {
        networkInfo.prefixLen4 = to_string(getPrefixLength(jsettings["subnetMaskV4"].asString()));
    }

    if (jsettings.isMember("isIpv6Enabled"))
    {
        networkInfo.enableIpv6 = jsettings["isIpv6Enabled"].asBool();
    }

    if (jsettings.isMember("dhcpV6"))
    {
        networkInfo.enableDhcp6 = jsettings["dhcpV6"].asString();
    }

    if (jsettings.isMember("ipAddressV6"))
    {
        networkInfo.IPAddr6 = jsettings["ipAddressV6"].asString();
    }

    if (jsettings.isMember("subnetMaskV6"))
    {
        networkInfo.prefixLen6 = jsettings["subnetMaskV6"].asString();
    }
}

int RemoteDevice::getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo)
{
    LOG(info) << __PRETTY_FUNCTION__ << endl;

    Json::Value jNetworkSettings = Json::nullValue;
    VmsErrorCode err = getSensorNetworkSettings(sensor, jNetworkSettings);
    if (err != VmsErrorCode::NoError)
    {
        return -1;
    }

    fillSensorNetworkInfo(jNetworkSettings, networkInfo);

    return 0;
}

VmsErrorCode RemoteDevice::getSensorNetworkSettings(shared_ptr<SensorInfo> &sensor, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/netsettings";
        msg["data"] = Json::nullValue;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("get");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: \n" << object->m_response.toStyledString() << endl;
        response = object->m_response;
        if (response.isObject() && response.isMember("error_code"))
        {
            LOG(error) << "Failed to get sensor network settings" << endl;
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else if (response.isObject())
        {
            LOG(info) << "Successfully got sensor network settings" << endl;
            return VmsErrorCode::NoError;
        }
        else
        {
            LOG(error) << "Failed to get sensor network settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }

    return VmsErrorCode::CameraNotFoundError;
}

int RemoteDevice::setNetworkInfo(shared_ptr<SensorInfo> &sensor, const SensorNetworkInfo &settings, bool& rebootNeeded)
{
    Json::Value jsettings;
    Json::Value response;
    VmsErrorCode err = VmsErrorCode::NoError;
    sensorNetworkInfoValuesToJson(settings, jsettings);
    err = setSensorNetworkSettings(sensor, jsettings, response);
    if (err != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to set sensor network settings" << endl;
        return -1;
    }
    return 0;
}

VmsErrorCode RemoteDevice::setSensorNetworkSettings(shared_ptr<SensorInfo> &sensor, const Json::Value &settings, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/netsettings";
        msg["data"] = settings;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("post");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response = object->m_response;
        if (response.isObject() && response.isMember("error_code"))
        {
            LOG(error) << "Failed to set sensor network settings" << endl;
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else if (response.isObject())
        {
            LOG(info) << "Successfully set sensor network settings" << endl;
            return VmsErrorCode::NoError;
        }
        else
        {
            LOG(error) << "Failed to set sensor network settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }

    return VmsErrorCode::CameraNotFoundError;
}

VmsErrorCode RemoteDevice::setSensorInfoSettings(shared_ptr<SensorInfo> &sensor, const Json::Value &settings, Json::Value &response)
{
    // Edge to cloud use case. Forward the request to edge device via websocket and wait for its response. Notify the caller after response.
    if (sensor)
    {
        std::shared_ptr<MessageObject> object = std::make_shared<MessageObject>();
        Json::Value msg = Json::objectValue;
        Json::Value data = Json::objectValue;

        msg["sensorId"] = sensor->id;
        msg["apiKey"] = "api/v1/sensor/info";
        msg["data"] = settings;
        object->m_responseId = generate_uuid();
        msg["requestId"] = object->m_responseId;
        msg["requestMethod"] = string("post");
        msg["clientId"] = sensor->remoteDeviceId;
        bool isSendSuccess = m_messageBus->sendMessage(sensor->remoteDeviceId, jsonToString(msg), object);
        if (!isSendSuccess)
        {
            LOG(error) << "Failed to send WS message" << endl;
            return VmsErrorCode::VMSInternalError;
        }
        LOG(info) << "Waiting for response with ID: " << object->m_responseId << endl;
        if (object->m_isResponseReceived == false)
        {
            object->m_sync.wait(DATA_CHANNEL_WAIT_TIME);
        }
        LOG(info) << "Response received for ID " << object->m_responseId << endl;
        LOG(info) << "Response: " << object->m_response.toStyledString() << endl;
        response = object->m_response;

        if (response.isBool() && response.asBool())
        {
            LOG(info) << "Successfully set sensor info settings" << endl;
            return VmsErrorCode::NoError;
        }
        else if (response.isObject() && response.isMember("error_code"))
        {
            LOG(error) << "Failed to set sensor info settings" << endl;
            const string defaultErrorCode = getCameraErrorCodeString(VmsErrorCode::VMSInternalError).first;
            const string errorCode = response.get("error_code", defaultErrorCode).asString();
            return getCameraErrorCode(errorCode);
        }
        else
        {
            LOG(error) << "Failed to set sensor info settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }

    return VmsErrorCode::CameraNotFoundError;
}

int RemoteDevice::setSensorInfo(shared_ptr<SensorInfo> &sensor)
{
    Json::Value jsettings;
    Json::Value response;
    VmsErrorCode err = VmsErrorCode::NoError;
    sensorInfoValuesToJson(*sensor, jsettings);
    err = setSensorInfoSettings(sensor, jsettings, response);
    if (err != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to set sensor info settings" << endl;
        return -1;
    }

    return 0;
}

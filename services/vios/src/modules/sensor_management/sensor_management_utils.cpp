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

#include <chrono>
#include <boost/algorithm/clamp.hpp>
#include "sensor_management_utils.h"
#include "MessageObject.h"
#include "Websocket.h"
#include "testRTSP.h"
#include "streamrecorder.h"
#include "network_utils.h"
#include "vst_common.h"
#include "nvsoap.h"
#include "database.h"

#define SENSOR_DEFAULT_PREFIX_NAME "SENSOR"

#define CHECK_VALUE_IF_ERROR_RETURN(para, val, min, max)                                                                                    \
    if (!(val.empty() || min.empty() || max.empty()) && !(min == "0" && max == "0") && valueWithinRange(val, min, max) == false)            \
    {                                                                                                                                       \
        LOG(error) << para << " parameter Value : " << val << " is wron, It should be in between min:" << min << " max:" << max << endl;    \
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);                                                                       \
        return VmsErrorCode::InvalidParameterError;                                                                                         \
    }                                                                                                                                       \
    if (min == "0" && max == "0")                                                                                                           \
    {                                                                                                                                       \
        val.clear();                                                                                                                        \
    }
#define CONTAINS_VALUE_IF_ERROR_RETURN(para, val, vec)                          \
    if (!val.empty() && std::find(vec.begin(), vec.end(), val) == vec.end())    \
    {                                                                           \
        LOG(error) << para << " parameter Value is wrong" << endl;              \
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);           \
        return VmsErrorCode::InvalidParameterError;                             \
    }

#define SET_SLIDER_IF_VALID(para, json, val, min, max)                                                                          \
    if (!(val.empty() || min.empty() || max.empty()) && !(min == "0" && max == "0") && valueWithinRange(val, min, max) == true) \
    {                                                                                                                           \
        Json::Value range;                                                                                                      \
        range["Min"]  = min;                                                                                                    \
        range["Max"]  = max;                                                                                                    \
        range["Value"]  = val;                                                                                                  \
        json[para] = range;                                                                                                     \
    }

#define SET_DROP_DOWN_IF_VALID(para, json, vec, val)                                \
    {                                                                               \
        Json::Value obj;                                                            \
        Json::Value array;                                                          \
        for (uint32_t i = 0; i < vec.size(); i++)                                   \
        {                                                                           \
            array.append(vec[i]);                                                   \
        }                                                                           \
        if (array.size() && std::find(vec.begin(), vec.end(), val) != vec.end())    \
        {                                                                           \
            obj["AllowedValues"] = array;                                           \
            obj["Value"] = val;                                                     \
            json[para] = obj;                                                       \
        }                                                                           \
    }

#define WEBSOCKET_WAIT_TIME 10000

#define CHECK_DEVICE_MANAGER(obj) {\
                                    if (obj == nullptr) { \
                                        LOG(error) << "Device Manager object is NULL" << endl; \
                                        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response) \
                                        return VmsErrorCode::InvalidParameterError; } }

#define CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(obj) {\
                                if (obj == nullptr) { \
                                    LOG(error) << "Sensor Management object is NULL" << endl; \
                                    SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response) \
                                    return VmsErrorCode::InvalidParameterError; } }

#define CHECK_SENSOR_MNGT_AND_RETURN(obj) {\
                                if (obj == nullptr) { \
                                    LOG(error) << "Sensor Management object is NULL" << endl; \
                                    return -1; \
                                    } }

using namespace nv_vms;
using namespace std;

VmsErrorCode getSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string& type, Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    shared_ptr<SensorInfo> sensor = deviceMngr->getSensorInfo(sensor_id);
    if (sensor == nullptr)
    {
        string error_message = string("Invalid camera id");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    for (uint32_t i = 0; i < sensor->streams.size(); i++)
    {
        Json::Value values;
        Json::Value array;
        SensorSettings settings;
        int ret = getSensorSettings(sensorMgmt, sensor_id, sensor->streams[i]->id, settings, type);
        if (ret == 0)
        {
            if (type.empty() || type == "Image")
            {
                if (isCameraImageSupported(sensorMgmt, sensor_id))
                {
                    Json::Value imageValues;
                    const SensorImageSettingsOptions& opt = settings.imageOptions;
                    const SensorImageSettingsValues& val = settings.imageValues;
                    SET_SLIDER_IF_VALID("Brightness", imageValues, val.Brightness, opt.Brightness.min, opt.Brightness.max)
                    SET_SLIDER_IF_VALID("ColorSaturation", imageValues, val.ColorSaturation, opt.ColorSaturation.min, opt.ColorSaturation.max)
                    SET_SLIDER_IF_VALID("Contrast", imageValues, val.Contrast, opt.Contrast.min, opt.Contrast.max)
                    SET_SLIDER_IF_VALID("Sharpness", imageValues, val.Sharpness, opt.Sharpness.min, opt.Sharpness.max)
                    SET_SLIDER_IF_VALID("BacklightCompensationLevel", imageValues, val.BacklightCompensationLevel, opt.BacklightCompensationLevel.min, opt.BacklightCompensationLevel.max)

                    SET_DROP_DOWN_IF_VALID("BacklightCompensationMode", imageValues, opt.BacklightCompensationModes, val.BacklightCompensationMode)
                    SET_DROP_DOWN_IF_VALID("ExposureMode", imageValues, opt.ExposureModes, val.ExposureMode)
                    SET_DROP_DOWN_IF_VALID("ExposurePriority", imageValues, opt.ExposurePriorities, val.ExposurePriority)
                    SET_DROP_DOWN_IF_VALID("IrCutFilterMode", imageValues, opt.IrCutFilterModes, val.IrCutFilterMode)
                    SET_DROP_DOWN_IF_VALID("WideDynamicRangeMode", imageValues, opt.WideDynamicRangeModes, val.WideDynamicRangeMode)
                    SET_DROP_DOWN_IF_VALID("WhiteBalanceMode", imageValues, opt.WhiteBalanceModes, val.WhiteBalanceMode)
                    SET_DROP_DOWN_IF_VALID("TemporalNoiseReductionModes", imageValues, opt.TemporalNoiseReductionModes, val.TemporalNoiseReductionMode)
                    SET_DROP_DOWN_IF_VALID("AutoExposureAntibandingMode", imageValues, opt.AeAntibandingModes, val.AeAntibandingMode)
                    SET_DROP_DOWN_IF_VALID("EdgeEnhancementMode", imageValues, opt.EdgeEnhancementModes, val.EdgeEnhancementMode)

                    SET_SLIDER_IF_VALID("EdgeEnhancementStrength", imageValues, val.EdgeEnhancementStrength, opt.EdgeEnhancementStrength.min, opt.EdgeEnhancementStrength.max)
                    SET_SLIDER_IF_VALID("ExposureCompensation", imageValues, val.ExposureCompensation, opt.ExposureCompensation.min, opt.ExposureCompensation.max)
                    SET_SLIDER_IF_VALID("MinExposureTime", imageValues, val.MinExposureTime, opt.MinExposureTime.min, opt.MinExposureTime.max)
                    SET_SLIDER_IF_VALID("ExposureMaxGain", imageValues, val.ExposureMaxGain, opt.ExposureMaxGain.min, opt.ExposureMaxGain.max)
                    SET_SLIDER_IF_VALID("ExposureTime", imageValues, val.ExposureTime, opt.ExposureTime.min, opt.ExposureTime.max)
                    SET_SLIDER_IF_VALID("ExposureGain", imageValues, val.ExposureGain, opt.ExposureGain.min, opt.ExposureGain.max)

                    if (!val.ExposureWindow.bottom.empty() && !val.ExposureWindow.top.empty() && !val.ExposureWindow.right.empty() && !val.ExposureWindow.left.empty())
                    {
                        Json::Value rect;
                        rect["bottom"] = val.ExposureWindow.bottom;
                        rect["top"] = val.ExposureWindow.top;
                        rect["right"] = val.ExposureWindow.right;
                        rect["left"] = val.ExposureWindow.left;
                        imageValues["ExposureWindow"] = rect;
                    }

                    SET_SLIDER_IF_VALID("WideDynamicRangeLevel", imageValues, val.WideDynamicRangeLevel, opt.WideDynamicRangeLevel.min, opt.WideDynamicRangeLevel.max)
                    SET_SLIDER_IF_VALID("WhiteBalanceYrGain", imageValues, val.WhiteBalanceYrGain, opt.WhiteBalanceYrGain.min, opt.WhiteBalanceYrGain.max)
                    SET_SLIDER_IF_VALID("WhiteBalanceYbGain", imageValues, val.WhiteBalanceYbGain, opt.WhiteBalanceYbGain.min, opt.WhiteBalanceYbGain.max)

                    values["Image"] = imageValues;
                }
                else
                {
                    LOG(error) << "Camera Image service not supported" << endl;
                }
            }

            if (type.empty() || type == "Encode")
            {
                Json::Value encodeValues;
                SensorEncoderSettingsOptions& encOpts = settings.encoderOptions;
                SensorVideoEncoderSettingsValues& encValues = settings.encoderValues;

                {
                    Json::Value obj;
                    const vector<string>& vec = encOpts.videoEncodingSupported;
                    for (uint32_t i = 0; i < vec.size(); i++)
                    {
                        string enc = vec[i];
                        if (enc != "H264" && enc != "H265") // Only supporting H264, H265
                        {
                            continue;
                        }
                        array.append(enc);
                    }
                    if (array.size())
                    {
                        obj["AllowedValues"] = array;
                        array.clear();
                        obj["Value"] = encValues.encoding;
                        encodeValues["Encoding"] = obj;
                    }
                }

                Json::Value element;
                Json::Value elementObj;
                array.clear();
                for (auto encoderSettingsOptions: encOpts.encoderSettingsOptions)
                {
                    if (encoderSettingsOptions.encoding != "H264" && encoderSettingsOptions.encoding != "H265") // Only supporting H264, H265
                    {
                        LOG(warning) << "Encoding: " << encoderSettingsOptions.encoding << " is not supported yet in the VST" << endl;
                        continue;
                    }

                    SET_SLIDER_IF_VALID("Quality", element, encValues.quality, encoderSettingsOptions.qualityRange.min, encoderSettingsOptions.qualityRange.max)
                    SET_SLIDER_IF_VALID("Bitrate", element, encValues.bitrate, encoderSettingsOptions.BitrateRange.min, encoderSettingsOptions.BitrateRange.max)

                    SET_SLIDER_IF_VALID("EncodingInterval", element, encValues.encodingInterval, encoderSettingsOptions.EncodingIntervalRange.min, encoderSettingsOptions.EncodingIntervalRange.max)

                    if (!encoderSettingsOptions.FrameRateSupported.empty())
                    {
                        std::vector<std::string> frameRates;
                        std::istringstream supprotedFramerates(encoderSettingsOptions.FrameRateSupported);

                        std::string token;
                        while (supprotedFramerates >> token)
                        {
                            frameRates.push_back(token);
                        }

                        Json::Value obj;
                        for (uint32_t i = 0; i < frameRates.size(); i++)
                        {
                            array.append(frameRates[i]);
                        }
                        if (array.size())
                        {
                            obj["AllowedValues"] = array;
                            array.clear();
                            obj["Value"] = encValues.frameRate;
                            element["FrameRate"] = obj;
                        }
                    }

                    SET_SLIDER_IF_VALID("GovLength", element, encValues.govLength, encoderSettingsOptions.GovLengthRange.min, encoderSettingsOptions.GovLengthRange.max)

                    {
                        Json::Value obj;
                        const vector<Resolution>& vec = encoderSettingsOptions.ResolutionsAvailable;
                        for (uint32_t i = 0; i < vec.size(); i++)
                        {
                            Resolution res = vec[i];
                            Json::Value v;
                            v["Width"] = res.width;
                            v["Height"] =res.height;
                            array.append(v);
                        }
                        if (array.size())
                        {
                            obj["AllowedValues"] = array;
                            array.clear();
                            Json::Value v;
                            v["Width"] = encValues.resolution.width;
                            v["Height"] =encValues.resolution.height;
                            obj["Value"] = v;
                            element["Resolution"] = obj;
                        }
                    }

                    SET_DROP_DOWN_IF_VALID("Profiles", element, encoderSettingsOptions.profilesSupported, encValues.encodingProfile)

                    elementObj[encoderSettingsOptions.encoding] = element;
                    encodeValues["Options"].append(elementObj);

                    element.clear();
                    elementObj.clear();
                }

                values["Encode"] = encodeValues;
            }
            response[sensor->streams[i]->id] = values;
        }
        else
        {
            LOG(error) << "Get Encode settings failed for stream id:" << sensor->streams[i]->id << endl;
        }
    }
    LOG(verbose) << "getSensorSettings: " << response.toStyledString() << endl;
    return VmsErrorCode::NoError;
}

VmsErrorCode setSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value& settings, Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    /*Set camera settings like brigtness, saturation, contrast, framerate, etc*/
    int ret = -1;
    shared_ptr<SensorInfo> sensor = deviceMngr->getSensorInfo(sensor_id);
    if (sensor->type != SENSOR_TYPE_ONVIF && !sensor->isRemoteSensor)
    {
        LOG(error) << "SetSensorSettings is not supported for non-onvif sensor" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSNotSupportedError, response)
        return VmsErrorCode::VMSNotSupportedError;
    }
    LOG(verbose) << "setSensorSettings: " << settings.toStyledString() << endl;
    Json::Value imageValues = settings.get("Image", false);
    if (imageValues != false && isCameraImageSupported(sensorMgmt, sensor_id))
    {
        SensorSettings current_settings;
        ret = getSensorSettings(sensorMgmt, sensor_id, sensor->streams[0]->id, current_settings, "Image");
        if (ret != 0)
        {
            LOG(error) << "Error getting camera settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
        const SensorImageSettingsOptions& options = current_settings.imageOptions;
        const SensorImageSettingsValues& current_values = current_settings.imageValues;
        SensorImageSettingsValues values;
        values.Brightness = imageValues.get("Brightness", current_values.Brightness).asString();
        CHECK_VALUE_IF_ERROR_RETURN("Brightness", values.Brightness, options.Brightness.min, options.Brightness.max)

        values.ColorSaturation = imageValues.get("ColorSaturation", current_values.ColorSaturation).asString();
        CHECK_VALUE_IF_ERROR_RETURN("ColorSaturation", values.ColorSaturation, options.ColorSaturation.min, options.ColorSaturation.max)

        values.Contrast = imageValues.get("Contrast", current_values.Contrast).asString();
        CHECK_VALUE_IF_ERROR_RETURN("Contrast", values.Contrast, options.Contrast.min, options.Contrast.max)

        values.Sharpness = imageValues.get("Sharpness", current_values.Sharpness).asString();
        CHECK_VALUE_IF_ERROR_RETURN("Sharpness", values.Sharpness, options.Sharpness.min, options.Sharpness.max)

        values.BacklightCompensationLevel = imageValues.get("BacklightCompensationLevel", current_values.BacklightCompensationLevel).asString();
        CHECK_VALUE_IF_ERROR_RETURN("BacklightCompensationLevel", values.BacklightCompensationLevel, options.BacklightCompensationLevel.min, options.BacklightCompensationLevel.max)

        values.MinExposureTime = imageValues.get("MinExposureTime", current_values.MinExposureTime).asString();
        CHECK_VALUE_IF_ERROR_RETURN("MinExposureTime", values.MinExposureTime, options.MinExposureTime.min, options.MinExposureTime.max)

        values.MaxExposureTime = imageValues.get("MaxExposureTime", current_values.MaxExposureTime).asString();
        CHECK_VALUE_IF_ERROR_RETURN("MaxExposureTime", values.MaxExposureTime, options.MaxExposureTime.min, options.MaxExposureTime.max)

        values.ExposureMaxGain = imageValues.get("ExposureMaxGain", current_values.ExposureMaxGain).asString();
        CHECK_VALUE_IF_ERROR_RETURN("ExposureMaxGain", values.ExposureMaxGain, options.ExposureMaxGain.min, options.ExposureMaxGain.max)

        values.ExposureTime = imageValues.get("ExposureTime", current_values.ExposureTime).asString();
        CHECK_VALUE_IF_ERROR_RETURN("ExposureTime", values.ExposureTime, options.ExposureTime.min, options.ExposureTime.max)

        values.ExposureGain = imageValues.get("ExposureGain", current_values.ExposureGain).asString();
        CHECK_VALUE_IF_ERROR_RETURN("ExposureGain", values.ExposureGain, options.ExposureGain.min, options.ExposureGain.max)

        values.ExposureWindow.bottom = current_values.ExposureWindow.bottom;
        values.ExposureWindow.top = current_values.ExposureWindow.top;
        values.ExposureWindow.right = current_values.ExposureWindow.right;
        values.ExposureWindow.left = current_values.ExposureWindow.left;

        values.WideDynamicRangeLevel = imageValues.get("WideDynamicRangeLevel", current_values.WideDynamicRangeLevel).asString();
        CHECK_VALUE_IF_ERROR_RETURN("WideDynamicRangeLevel", values.WideDynamicRangeLevel, options.WideDynamicRangeLevel.min, options.WideDynamicRangeLevel.max)

        values.WhiteBalanceYrGain = imageValues.get("WhiteBalanceYrGain", current_values.WhiteBalanceYrGain).asString();
        CHECK_VALUE_IF_ERROR_RETURN("WhiteBalanceYrGain", values.WhiteBalanceYrGain, options.WhiteBalanceYrGain.min, options.WhiteBalanceYrGain.max)

        values.WhiteBalanceYbGain = imageValues.get("WhiteBalanceYbGain", current_values.WhiteBalanceYbGain).asString();
        CHECK_VALUE_IF_ERROR_RETURN("WhiteBalanceYbGain", values.WhiteBalanceYbGain, options.WhiteBalanceYbGain.min, options.WhiteBalanceYbGain.max)

        values.BacklightCompensationMode = imageValues.get("BacklightCompensationMode", current_values.BacklightCompensationMode).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("BacklightCompensationMode", values.BacklightCompensationMode, options.BacklightCompensationModes)

        values.ExposureMode = imageValues.get("ExposureMode", current_values.ExposureMode).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("ExposureMode", values.ExposureMode, options.ExposureModes)

        values.ExposurePriority = imageValues.get("ExposurePriority", current_values.ExposurePriority).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("ExposurePriority", values.ExposurePriority, options.ExposurePriorities)

        values.IrCutFilterMode = imageValues.get("IrCutFilterMode", current_values.IrCutFilterMode).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("IrCutFilterMode", values.IrCutFilterMode, options.IrCutFilterModes)

        values.WideDynamicRangeMode = imageValues.get("WideDynamicRangeMode", current_values.WideDynamicRangeMode).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("WideDynamicRangeMode", values.WideDynamicRangeMode, options.WideDynamicRangeModes)

        values.WhiteBalanceMode = imageValues.get("WhiteBalanceMode", current_values.WhiteBalanceMode).asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("WhiteBalanceMode", values.WhiteBalanceMode, options.WhiteBalanceModes)

        ret = setSensorImageSettings(sensorMgmt, sensor_id, values);
        if (ret != 0)
        {
            LOG(error) << "Error setting camera image settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        shared_ptr<StreamInfo> stream = deviceMngr->getStream(sensor_id, sensor->streams[0]->id);
        stream->updateImageValues(values);
    }
    Json::Value encodeValues = settings.get("Encode", false);
    if (encodeValues != false )
    {
        SensorSettings current_settings;
        ret = getSensorSettings(sensorMgmt, sensor_id, sensor->streams[0]->id, current_settings, "Encode");
        if (ret != 0)
        {
            LOG(error) << "Error getting camera settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::CommunicationError, response)
            return VmsErrorCode::CommunicationError;
        }
        SensorVideoEncoderSettingsValues values;
        VideoEncoderConfigurationsOptions options;
        LOG(verbose) << encodeValues.toStyledString() << endl;

        values.encoding = encodeValues.get("Encoding", "").asString();

        bool foundOption = false;
        for (auto encoderSettingsOptions : current_settings.encoderOptions.encoderSettingsOptions)
        {
            if (encoderSettingsOptions.encoding == values.encoding)
            {
                options = encoderSettingsOptions;
                foundOption = true;
            }
        }

        if (foundOption == false)
        {
            LOG(error) << "Error in finding options" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        values.quality = encodeValues.get("Quality", "").asString();
        CHECK_VALUE_IF_ERROR_RETURN("Quality", values.quality, options.qualityRange.min, options.qualityRange.max)
        values.bitrate = encodeValues.get("Bitrate", "").asString();
        CHECK_VALUE_IF_ERROR_RETURN("Bitrate", values.bitrate, options.BitrateRange.min, options.BitrateRange.max)
        if (!options.EncodingIntervalRange.min.empty() && !options.EncodingIntervalRange.max.empty())
        {
            values.encodingInterval = encodeValues.get("EncodingInterval", "").asString();
            CHECK_VALUE_IF_ERROR_RETURN("EncodingInterval", values.encodingInterval, options.EncodingIntervalRange.min, options.EncodingIntervalRange.max)
        }

        if (!options.FrameRateSupported.empty())
        {
            std::istringstream supprotedFramerates(options.FrameRateSupported);
            std::vector<double> frameRates;

            double rate;
            while (supprotedFramerates >> rate)
            {
                frameRates.push_back(rate);
            }

            values.frameRate = encodeValues.get("FrameRate", "").asString();
            double framerate = stringToDouble(values.frameRate, MAX_FRAMERATE);
            framerate = findNearestValue(frameRates, framerate);

            string min = to_string(*std::min_element(frameRates.begin(), frameRates.end()));
            string max = to_string(*std::max_element(frameRates.begin(), frameRates.end()));
            CHECK_VALUE_IF_ERROR_RETURN("FrameRate", values.frameRate, min, max)

            values.frameRate = removeDecimals(framerate);
        }
        else
        {
            LOG(error) << "Invalid framerate supproted received in encode options" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        values.govLength = encodeValues.get("GovLength", "").asString();
        CHECK_VALUE_IF_ERROR_RETURN("GovLength", values.govLength, options.GovLengthRange.min, options.GovLengthRange.max)

        Json::Value res = encodeValues.get("Resolution", Json::nullValue);
        if (!res.isNull() && res.empty() == false)
        {
            values.resolution.width = res.get("Width", "").asString();
            values.resolution.height = res.get("Height", "").asString();
        }
        CONTAINS_VALUE_IF_ERROR_RETURN("Resolution", values.resolution, options.ResolutionsAvailable)

        values.encodingProfile = encodeValues.get("Profiles", "").asString();
        bool found = false;
        for (auto profilesSupported : options.profilesSupported)
        {
            if (profilesSupported == values.encodingProfile)
            {
                found = true;
                break;
            }
        }

        if ((!found) && (options.profilesSupported.size() != 0))
        {
            LOG (warning) << "Given profile: " << values.encodingProfile << " is not supported, So set from allowed profiles:" << options.profilesSupported[0] << endl;
            values.encodingProfile = options.profilesSupported[0];
        }
        CONTAINS_VALUE_IF_ERROR_RETURN("Profiles", values.encodingProfile, options.profilesSupported)

        values.encoding = encodeValues.get("Encoding", "").asString();
        CONTAINS_VALUE_IF_ERROR_RETURN("Encoding", values.encoding, current_settings.encoderOptions.videoEncodingSupported)

        ret = setSensorEncodeSettings(sensorMgmt, sensor_id, values);
        if (ret != 0)
        {
            LOG(error) << "Error setting sensor encode settings" << endl;
            SET_VMS_ERROR(VmsErrorCode::CommunicationError, response)
            return VmsErrorCode::CommunicationError;
        }

        shared_ptr<StreamInfo> stream = deviceMngr->getStream(sensor_id, sensor->streams[0]->id);
        stream->updateVideoEncoderValues(values);
    }
    response = true;
    return VmsErrorCode::NoError;
}

VmsErrorCode replaceSensorId(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string old_sensor_id, const Json::Value& in, Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    string new_sensor_id = in.get("sensorId", EMPTY_STRING).asString();
    // backward compatibility
    if (new_sensor_id == EMPTY_STRING)
    {
        new_sensor_id = in.get("deviceid", EMPTY_STRING).asString();
    }
    if (new_sensor_id == EMPTY_STRING)
    {
        LOG(error) << "Sensor ID is empty" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    shared_ptr<DeviceManager> deviceManager = sensorMgmt->getDeviceManagerObject();

    shared_ptr<SensorInfo> old_sensor =  deviceManager->getSensor(old_sensor_id);
    shared_ptr<SensorInfo> new_sensor =  deviceManager->getSensor(new_sensor_id);
    if (old_sensor == nullptr)
    {
        string error_message = string("Old Sensor does not exists, cannot replace");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }
    if (new_sensor == nullptr)
    {
        string error_message = string("New Sensor does not exists, cannot replace");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    if (old_sensor->type == SENSOR_TYPE_CSI || new_sensor->type == SENSOR_TYPE_CSI)
    {
        string error_message = string("Old/new sensor is a CSI sensor, cannot replace");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    if (old_sensor->getSensorStatus() == SensorStatusOnline||
        old_sensor->getSensorStatus() == SensorStatusStreaming)
    {
        string error_message = string("Old Sensor still active, cannot replace");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    sensorMgmt->replaceSensor(old_sensor_id, new_sensor_id);
    return VmsErrorCode::NoError;
}

VmsErrorCode addSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const Json::Value& req_info, const Json::Value &data, Json::Value &response)
{
    const auto addSensorStartTime = std::chrono::steady_clock::now();
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(data)
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    string vstUser = req_info.get("username", EMPTY_STRING).asString();
    DeviceConfig config =  GET_CONFIG();
    bool isSensorFromEdge = data.get("isRemoteSensor", false).asBool();
    const string remoteDeviceId = data.get("remoteDeviceId", EMPTY_STRING).asString();
    if (!remoteDeviceId.empty() && deviceMngr->isDeviceRemote() && isSensorFromEdge == false)
    {
        /* This request is intended for edge device */
        Json::Value sensor_info;
        string sensor_ip = data.get("sensorIp", "").asString();
        string rtsp_url = data.get("sensorUrl", "").asString();
        if (sensor_ip.empty() == false)
        {
            sensor_info["sensorIp"] = sensor_ip;
        }
        else if (rtsp_url.find("rtsp") != std::string::npos)
        {
            sensor_info["sensorUrl"] = rtsp_url;
        }
        else
        {
            string error_message = string("Invalid Parameters");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
        sensor_info["username"] = data.get("username", EMPTY_STRING).asString();
        sensor_info["password"] = data.get("password", EMPTY_STRING).asString();
        sensor_info["name"] = data.get("name", EMPTY_STRING).asString();
        sensor_info["location"] = data.get("location", EMPTY_STRING).asString();
        sensor_info["remoteDeviceId"] = remoteDeviceId;

        return sensorMgmt->addSensorToEdgeVst(sensor_info);
    }
    if (deviceMngr->getDeviceType() != TYPE_STREAMER)
    {
         if (deviceMngr->isSpaceForNewSensor() == false)
        {
           LOG(error) << "Sensors count limit reached" << endl;
           SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Sensors count limit reached")
           return VmsErrorCode::VMSNotSupportedError;
        }
    }
    #ifndef RELEASE
        // Create a safe copy of data for logging (mask sensitive fields)
        Json::Value safeData = data;
        if (safeData.isMember("username"))
        {
            safeData["username"] = maskSensitiveData(safeData["username"].asString(), MaskType::USERNAME);
        }
        if (safeData.isMember("password"))
        {
            safeData["password"] = maskSensitiveData(safeData["password"].asString(), MaskType::PASSWORD);
        }
        LOG(info) << "Parameters: " << safeData.toStyledString() << endl;
    #endif // !RELEASE
    shared_ptr<SensorInfo> sensor (new SensorInfo);
    shared_ptr<UserInfo> user (new UserInfo);
    user->username = vstUser;
    string rtsp_url = data.get("sensorUrl", "").asString();
    string sensor_ip = data.get("sensorIp", "").asString();
    std::string codec;
    std::string frame_rate;
    std::string width;
    std::string height;

    sensor->user = data.get("username", EMPTY_STRING).asString();
    sensor->password = data.get("password", EMPTY_STRING).asString();
    sensor->name = data.get("name", EMPTY_STRING).asString();
    sensor->location = data.get("location", UNKNOWN_STRING).asString();
    sensor->tags = data.get("tags", EMPTY_STRING).asString();
    sensor->isRemoteSensor = data.get("isRemoteSensor", false).asBool();
    sensor->id = generate_uuid();
    sensor->addUser(user);
    if (rtsp_url.find("rtsp") != std::string::npos )
    {
        if ((validateAndStripRtspUrl(rtsp_url, sensor->ip, sensor->user, sensor->password) == false) &&
            (deviceMngr->getDeviceType() != TYPE_STREAMER))
        {
            string error_message = string("Invalid RTSP URL");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
        // Opt-in RTSP DESCRIBE probe before persisting. Off by default to
        // preserve existing client behaviour; clients that want strict
        // reachability checking pass {"verifyRtsp": true} in the request.
        const bool verifyRtsp = data.get("verifyRtsp", false).asBool();
        if (verifyRtsp && deviceMngr->getDeviceType() != TYPE_STREAMER)
        {
            int describe_result = testRtspUrl(rtsp_url.c_str(), codec, frame_rate, width, height,
                                              sensor->user, sensor->password);
            if (describe_result != 0)
            {
                string error_message = string("RTSP DESCRIBE failed for the supplied sensorUrl");
                LOG(error) << error_message << " (result=" << describe_result << ")" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
                return VmsErrorCode::InvalidParameterError;
            }
        }
        if (codec.empty() == false && VmsConfigManager::getInstance()->isVideoFormatSupported(codec) == false)
        {
            string error_message = string("Video encode format not supported: ") + codec;
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, error_message.c_str())
            return VmsErrorCode::VMSNotSupportedError;
        }
        sensor->sensorId = sensor->id;
        sensor->name = data.get("name", EMPTY_STRING).asString();
        sensor->name = truncateString(sensor->name, MAX_SENSOR_NAME_LENGTH);
        sensor->hardware = data.get("hardware", UNKNOWN_STRING).asString();
        sensor->manufacturer = data.get("manufacturer", UNKNOWN_STRING).asString();
        sensor->serial_number = data.get("serialNumber", UNKNOWN_STRING).asString();
        sensor->firmware_version = data.get("firmwareVersion", UNKNOWN_STRING).asString();
        sensor->hardware_id = data.get("hardwareId", UNKNOWN_STRING).asString();
        sensor->updateSensorStatus(SensorStatusOnline);
        sensor->updateHttpErrorStatus(std::make_pair(200, "No Error"));
        if (deviceMngr->getDeviceType() == TYPE_STREAMER)
        {
            sensor->type = SENSOR_TYPE_NVSTREAM;
        }
        else
        {
            sensor->type = SENSOR_TYPE_RTSP;
        }

        if (sensor->name.empty())
        {
            sensor->name = deviceMngr->createUniqueName(SENSOR_DEFAULT_PREFIX_NAME);
        }
        shared_ptr<StreamInfo> stream(new StreamInfo);
        stream->isMainStream = true;
        stream->sensorId = stream->id = sensor->id;
        stream->name = sensor->name;
        stream->live_url = rtsp_url;
        if (deviceMngr->getDeviceType() == TYPE_STREAMER)
        {
            stream->live_proxy_url = stream->replay_url = stream->live_url;
        }
        stream->settings.encoderValues.frameRate = frame_rate.empty() ? data.get("framerate", "").asString() : frame_rate;
        stream->settings.encoderValues.resolution.width = width.empty() ? data.get("width", "").asString() : width;
        stream->settings.encoderValues.resolution.height = height.empty() ?  data.get("height", "").asString() : height;
        if (codec.empty() == false)
        {
            toLowerCase(codec);
        }
        stream->settings.encoderValues.encoding = codec.empty() ?  data.get("encoding", "h264").asString() : codec;
        stream->settings.encoderValues.container = data.get("container", "").asString();
        sensor->streams.push_back(stream);
    }
    // handle camera add by edge VST device
    else if (sensor->isRemoteSensor)
    {
        LOG(warning) << "Add camera from edge device" << endl;
        sensor->remoteDeviceId = data.get("remoteDeviceId", EMPTY_STRING).asString();
        sensor->remoteDeviceName = data.get("remoteDeviceName", EMPTY_STRING).asString();
        sensor->remoteDeviceLocation = data.get("remoteDeviceLocation", EMPTY_STRING).asString();
        std::string receivedSensorId = data.get("sensorId", EMPTY_STRING).asString();
        const int sensorStatus = data.get("sensorStatus", 501).asInt();
        if (receivedSensorId.empty())
        {
            string error_message = string("Received empty sensor ID");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }

        sensor->id = receivedSensorId;
        sensor->sensorId = sensor->id;
        sensor->type = SENSOR_TYPE_REMOTE;
        sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
        sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(translateCameraHttpErrorCodeToVmsErrorCode(sensorStatus)));
    }
    else if (sensor_ip.empty() == false)
    {
        if(validateIpAddress(sensor_ip) == false)
        {
            string error_message = string("Invalid IP address");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
        sensor->ip = sensor_ip;
        sensor->type = SENSOR_TYPE_ONVIF;
    }
    else
    {
        string error_message = string("Invalid Parameters");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }
    auto existingSensor = GET_DB_INSTANCE()->findExistingSensor(sensor, deviceMngr->getDeviceId());
    if (existingSensor)
    {
        string error_message = "Sensor exists already, sensorId: " + existingSensor->id + ", sensorName: " + existingSensor->name;
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }
    if (deviceMngr->getDeviceType() != TYPE_STREAMER)
    {
        shared_ptr<SensorInfo> nameConflictSensor;
        if (sensor->name.empty() == false && validateSensorName(deviceMngr, sensor->name, &nameConflictSensor) == false)
        {
            string error_message = string("User given name is invalid or already exists");
            if (nameConflictSensor)
            {
                error_message += ", sensorId: " + nameConflictSensor->id + ", sensorName: " + nameConflictSensor->name;
            }
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }
    }
    string sensor_id = "";
    string result;
    sensor->printInfo();
    if (sensorMgmt->addSensorManually(sensor, result) == 0)
    {
        response["sensorId"] = sensor->id;
        const auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - addSensorStartTime).count();
        LOG(info) << "addSensor completed: " << sensor->name << " : total time : " << elapsedMs << " ms" << endl;
    }
    else
    {
        const auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - addSensorStartTime).count();
        LOG(error) << "addSensor failed: " << sensor->name << " : total time : " << elapsedMs << " ms" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, result.c_str())
        LOG(error) << result << endl;
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode deleteSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response, bool isReqFromRemote, bool isReqFromEdgeDevice)
{
    bool result = false;
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    LOG(info) << "deleteSensor id: " << sensor_id << " reqFromEdge:" << isReqFromEdgeDevice << " reqFromRemote:" << isReqFromRemote << endl;
    const std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    if (deviceMngr->getSensorInfo(sensor_id) == nullptr)
    {
        const string error_message = "Invalid sensor ID " + sensor_id;
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, response, error_message.c_str());
        return VmsErrorCode::CameraNotFoundError;
    }

    if (sensorMgmt->deleteSensor(sensor_id, isReqFromRemote, isReqFromEdgeDevice) != 0)
    {
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }
    else
    {
        result = true;
    }
    response = result;
    return VmsErrorCode::NoError;
}

VmsErrorCode skipStatusError(const string camera_error_code, const string request_method,
    const string action, Json::Value &response)
{
    VmsErrorCode code = VmsErrorCode::NoError;
    if(iequals(request_method, "delete"))
    {
        if(!iequals(action, "") && !iequals(action, "record"))
        {
            goto return_error;
        }
    }
    else if(iequals(request_method, "get"))
    {
        if(!iequals(action, "record") && !iequals(action, "status") && !iequals(action, "info") && !iequals(action, "download"))
        {
            goto return_error;
        }
    }
    else if(iequals(request_method, "post"))
    {
        if(!iequals(action, "record") && !iequals(action, "replace"))
        {
            goto return_error;
        }
    }
    return code;

    return_error:
        code = getCameraErrorCode(camera_error_code);
        SET_VMS_ERROR(code, response);
        LOG(error) << "Error code: " << camera_error_code << ":" << response.get("errorMessage", "unknown error sensor management error").asString() << endl;
        return code;
}

VmsErrorCode getAllSensorStatus(std::shared_ptr<DeviceManager> deviceMngr, Json::Value &response)
{
    vector<shared_ptr<SensorInfo>> sensors = deviceMngr->getSensorList();
    std::for_each(sensors.begin(), sensors.end(), [ &response](shared_ptr<SensorInfo> &sensor)
    {
        Json::Value resp;
        VmsErrorCode code = translateCameraHttpErrorCodeToVmsErrorCode(sensor->getHttpErrorStatus().first);
        std::pair<string, string> code_pair = getCameraErrorCodeString(code);
        resp["name"] = sensor->name;
        resp["errorCode"] = code_pair.first;
        resp["errorMessage"] = code_pair.second;
        resp["state"] = sensor->getSensorStatus() == SensorStatusOffline ? CAMERA_STATE_OFFLINE : CAMERA_STATE_ONLINE;
        response[sensor->id] = resp;
    });
    return VmsErrorCode::NoError;
}

VmsErrorCode getSensorInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    shared_ptr<SensorInfo> sensor = sensorMgmt->getSensorInfo(sensor_id);
    if (sensor)
    {
        response["sensorId"] = sensor->id;
        response["name"] = sensor->name;
        response["sensorIp"] = sensor->ip;
        response["hardware"] = sensor->hardware;
        response["manufacturer"] = sensor->manufacturer;
        response["serialNumber"] = sensor->serial_number;
        response["firmwareVersion"] = sensor->firmware_version;
        response["hardwareId"] = sensor->hardware_id;
        response["location"] = sensor->location;
        response["tags"] = sensor->tags;
        response["isRemoteSensor"] = sensor->isRemoteSensor;
        response["remoteDeviceId"] = sensor->remoteDeviceId;
        response["remoteDeviceName"] = sensor->remoteDeviceName;
        response["remoteDeviceLocation"] = sensor->remoteDeviceLocation;
        Json::Value position;
        Json::Value origin;
        origin["latitude"] = sensor->position.origin.first;
        origin["longitude"] = sensor->position.origin.second;
        position["origin"] = origin;
        Json::Value geoLocation;
        geoLocation["latitude"] = sensor->position.geoLocation.first;
        geoLocation["longitude"] = sensor->position.geoLocation.second;
        position["geoLocation"] = geoLocation;
        Json::Value coordinates;
        coordinates["x"] = sensor->position.coordinates.first;
        coordinates["y"] = sensor->position.coordinates.second;
        position["coordinates"] = coordinates;
        position["direction"] = sensor->position.direction;
        position["depth"] = sensor->position.depth;
        position["fieldOfView"] = sensor->position.fieldOfView;
        response["position"] = position;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode setSensorInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id,
    const Json::Value &in, Json::Value &response, bool isReqFromCloudDevice, bool isReqFromEdgeDevice)
{
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    if (in.empty())
    {
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        LOG(error) << "setSensorInfo: invalid parameters" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    shared_ptr<SensorInfo> existingSensorInfo = sensorMgmt->getSensorInfo(sensor_id);
    if (existingSensorInfo.get() == nullptr)
    {
        LOG(error) << "Failed to get sensor info:" << sensor_id << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    SensorInfo sensor;
    Json::Value position;
    Json::Value origin;
    Json::Value geoLocation;
    Json::Value coordinates;
    sensor.id = sensor_id;
    sensor.name = in.get("name", existingSensorInfo->name).asString();
    sensor.hardware = in.get("hardware", EMPTY_STRING).asString();
    sensor.manufacturer = in.get("manufacturer", EMPTY_STRING).asString();
    sensor.serial_number = in.get("serialNumber", EMPTY_STRING).asString();
    sensor.firmware_version = in.get("firmwareVersion", EMPTY_STRING).asString();
    sensor.hardware_id = in.get("hardwareId", EMPTY_STRING).asString();
    sensor.location = in.get("location", EMPTY_STRING).asString();
    sensor.tags = in.get("tags", EMPTY_STRING).asString();
    position = in.get("position", Json::nullValue);
    sensor.position.depth = position.get("depth", EMPTY_STRING).asString();
    sensor.position.fieldOfView = position.get("fieldOfView", EMPTY_STRING).asString();
    sensor.position.direction = position.get("direction", EMPTY_STRING).asString();
    origin = position.get("origin", Json::nullValue);
    coordinates = position.get("coordinates", Json::nullValue);
    geoLocation = position.get("geoLocation", Json::nullValue);

    if (origin != Json::nullValue)
    {
        sensor.position.origin.first = origin.get("latitude", EMPTY_STRING).asString();
        sensor.position.origin.second = origin.get("longitude", EMPTY_STRING).asString();
    }
    if (geoLocation != Json::nullValue)
    {
        sensor.position.geoLocation.first = geoLocation.get("latitude", EMPTY_STRING).asString();
        sensor.position.geoLocation.second = geoLocation.get("longitude", EMPTY_STRING).asString();
    }
    if (coordinates != Json::nullValue)
    {
        sensor.position.coordinates.first = coordinates.get("x", EMPTY_STRING).asString();
        sensor.position.coordinates.second = coordinates.get("y", EMPTY_STRING).asString();
    }

    std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    /**
     * if adaptor is not streamer and sensor name is new and that new name already
     * exists or new name is empty then return error
     */
    shared_ptr<SensorInfo> nameConflictSensor;
    if (sensor.name != existingSensorInfo->name && validateSensorName(deviceMngr, sensor.name, &nameConflictSensor) == false)
    {
        string error_message = string("User given name is invalid or already exists");
        LOG(error) << error_message << endl;
        if (nameConflictSensor)
        {
            response["existingSensorId"] = nameConflictSensor->id;
            response["existingSensorName"] = nameConflictSensor->name;
            error_message += ", sensorId: " + nameConflictSensor->id + ", sensorName: " + nameConflictSensor->name;
        }
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    if (deviceMngr->updateSensorInfo(sensor) != 0)
    {
        LOG(error) << "updateSensorInfo: failed due to internal error" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    if ((isReqFromCloudDevice == false) && (!GET_CONFIG().remote_vst_address.empty()))
    {
        vst_common::updateSensorInfoToRemoteVst(sensor);
    }
    else if ((isReqFromEdgeDevice == false) && (GET_CONFIG().remote_vst_address.empty()) && (sensorMgmt->setSensorInfo(sensor_id) != 0))
    {
        LOG(error) << "Failed to set sensor info for " << sensor_id << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    LOG(info) << "Set sensor info successfully for sensorId:" << sensor_id << endl;

    response = true;
    return VmsErrorCode::NoError;
}

bool validateSensorName(std::shared_ptr<DeviceManager> deviceMngr, const string sensor_name,
                        std::shared_ptr<SensorInfo>* existingSensor)
{
    if (existingSensor)
    {
        existingSensor->reset();
    }
    /* check for whitespace in name for nvstreamer */
    if (deviceMngr->getDeviceType() == TYPE_STREAMER && checkWhiteSpace(sensor_name))
    {
        return false;
    }
    /* check if unique name */
    vector<shared_ptr<SensorInfo>> sensors = deviceMngr->getSensorList();
    for (uint32_t i = 0; i < sensors.size(); i++ )
    {
        shared_ptr<SensorInfo> existing_sensor = sensors[i];
        if (sensor_name.compare(existing_sensor->name) == 0)
        {
            LOG(warning) << "found existing sensor with same name " << existing_sensor->name << endl;
            if (existingSensor)
            {
                *existingSensor = existing_sensor;
            }
            return false;
        }
    }
    return true;
}

VmsErrorCode getSensorStatus(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id,
    Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    shared_ptr<SensorInfo> sensor = sensorMgmt->getSensorInfo(sensor_id);
    if (sensor)
    {
        VmsErrorCode code = translateCameraHttpErrorCodeToVmsErrorCode(sensor->getHttpErrorStatus().first);
        std::pair<string, string> code_pair = getCameraErrorCodeString(code);
        response["name"] = sensor->name;
        response["errorCode"] = code_pair.first;
        response["errorMessage"] = code_pair.second;
        response["state"] = sensor->getSensorStatus() == SensorStatusOffline ? CAMERA_STATE_OFFLINE : CAMERA_STATE_ONLINE;
    }
    else
    {
        response["state"] = CAMERA_STATE_OFFLINE;
        std::pair<string, string> code_pair = getCameraErrorCodeString(VmsErrorCode::CameraNotFoundError);
        response["errorCode"] = code_pair.first;
        response["errorMessage"] = code_pair.second;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode getSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response)
{
    int ret = -1;
    SensorNetworkInfo netInfo;
    ret = getSensorNetworkInfo(sensorMgmt, sensor_id, netInfo);
    if (ret != 0)
    {
        LOG(error) << "Error getting camera network information" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    response["isIpv4Enabled"] = netInfo.enableIpv4;
    response["dhcpV4"] = netInfo.enableDhcp4;
    response["ipAddressV4"] = netInfo.IPAddr4;
    response["subnetMaskV4"] = getNetmaskFromPrefixLen(stringToInt(netInfo.prefixLen4, 0));

    response["isIpv6Enabled"] = netInfo.enableIpv6;
    response["dhcpV6"] = netInfo.enableDhcp6;
    response["ipAddressV6"] = netInfo.IPAddr6;
    response["subnetMaskV6"] = netInfo.prefixLen6;

    return VmsErrorCode::NoError;
}

VmsErrorCode setSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value &in,
Json::Value &response, bool isReqFromCloudDevice, bool isReqFromEdgeDevice)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    SensorNetworkInfo netInfo;
    bool rebootNeeded = false;
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    if (in.empty())
    {
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        LOG(error) << "setSensorNetworkInfo: invalid parameters" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    int ret = getSensorNetworkInfo(sensorMgmt, sensor_id, netInfo);
    if (ret != 0)
    {
        LOG(error) << "Error getting camera network information" << endl;
    }

    netInfo.enableIpv4 = in.get("isIpv4Enabled", false).asBool();
    netInfo.enableDhcp4 = in.get("dhcpV4", EMPTY_STRING).asString();
    netInfo.IPAddr4 = in.get("ipAddressV4", EMPTY_STRING).asString();
    if (!validateIpAddress(netInfo.IPAddr4))
    {
        LOG(error) << "Invalid ipv4 address" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    string subnet_mask_v4 = in.get("subnetMaskV4", EMPTY_STRING).asString();
    netInfo.prefixLen4 = to_string(getPrefixLength(subnet_mask_v4));

    netInfo.enableIpv6 = in.get("isIpv6Enabled", false).asBool();
    netInfo.enableDhcp6 = in.get("dhcpV6", EMPTY_STRING).asString();
    netInfo.IPAddr6 = in.get("ipAddressV6", EMPTY_STRING).asString();
    netInfo.prefixLen6 = in.get("subnetMaskV6", EMPTY_STRING).asString();

    LOG(info) << "Setting below network info for sensor_id:" << sensor_id << endl;
    LOG(info) << "dhcp:" << netInfo.enableDhcp4 << ", ip_address:" << netInfo.IPAddr4 << ", prefixLen4:" << netInfo.prefixLen4 << endl;
    LOG(info) << "dhcp6:" << netInfo.enableDhcp6 << ", ip_address6:" << netInfo.IPAddr6 << ", prefixLen6:" << netInfo.prefixLen6 << endl;

    if((isReqFromEdgeDevice == false) && (setSensorNetworkInfo(sensorMgmt, sensor_id, netInfo, rebootNeeded) != 0))
    {
        LOG(error) << "setSensorNetworkInfo: set info faied due to internal error" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    if ((isReqFromCloudDevice == false) && (!GET_CONFIG().remote_vst_address.empty()))
    {
        vst_common::updateSensorNetworkInfoToRemoteVst(netInfo, sensor_id);
    }

    LOG(info) << "is reboot needed:" << rebootNeeded << endl;
    response["rebootNeeded"] = rebootNeeded;

    return VmsErrorCode::NoError;
}

VmsErrorCode setSensorCredentials(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value& in, Json::Value &response)
{
    CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    std::shared_ptr<DeviceManager> deviceMngr = sensorMgmt->getDeviceManagerObject();
    CHECK_DEVICE_MANAGER(deviceMngr)
    SensorDetailsDBColumns dbRow;
    dbRow.device_id_value = deviceMngr->getDeviceId();
    dbRow.sensor_id_value = sensor_id;
    dbRow.username_value = in.get("username", EMPTY_STRING).asString();
    dbRow.password_value = in.get("password", EMPTY_STRING).asString();
    dbRow.httpStatus_value = translateVmsErrorCodeToCameraHttpErrorCode(NoError).first;

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    shared_ptr<SensorInfo> sensor = sensorControl->getSensor(sensor_id);
    if(sensor.get() == nullptr)
    {
        string error_message = string("Invalid Sensor ID " + sensor_id);
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, response, error_message.c_str());
        return VmsErrorCode::CameraNotFoundError;
    }
    if (sensor->user == dbRow.username_value && sensor->password == dbRow.password_value)
    {
        response = true;
        return VmsErrorCode::NoError;
    }

    if (validateCredentials(sensorMgmt, sensor_id, dbRow.username_value, dbRow.password_value) == false)
    {
        string err_msg("setSensorCredentials: invalid username or password");
        LOG(error) << err_msg << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, err_msg.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    if(GET_DB_INSTANCE()->insertRowSensorDetails(dbRow) != 0)
    {
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }
    /* also update sensor after db update  */
    sensor->updateCredentials(dbRow.username_value,  dbRow.password_value);
    sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
    /* Fetch camera details */
    if (sensorMgmt->getSensorInfo(sensor_id, true) == nullptr)
    {
        /* Notify external modules that camera is ready to stream */
        LOG(error) << "getSensorInfo() failed, sensor is not yet ready" << endl;
    }

    if (!GET_CONFIG().remote_vst_address.empty())
    {
        vst_common::updateSensorInfoToRemoteVst(*sensor);
    }

    response = true;
    return VmsErrorCode::NoError;
}

VmsErrorCode rebootSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response)
{
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)
    LOG(info) << "Reboot Sensor id: " << sensor_id << endl;
    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    if (sensorControl->rebootSensor(sensor_id) != 0)
    {
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

int setPTZ(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, PTZAction ptz, string x, string y)
{
    int ret = -1;
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return ret;
    }

    ret = sensorControl->setPTZ(sensor_id, ptz, x, y);
    return ret;
}

map<PTZAction, ptzRange> getPTZ(std::shared_ptr<DeviceManager> deviceMngr, const string sensor_id)
{
    map<string, std::shared_ptr<DeviceManager>>::iterator it;
    map<PTZAction, ptzRange> ptz;

    if (deviceMngr == nullptr)
    {
        LOG(error) << "Invalid DeviceMngr obj: " << sensor_id << endl;
        return ptz;
    }

    ptz = deviceMngr->getSensorPTZInfo(sensor_id);
    return ptz;
}

bool isCameraImageSupported(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id)
{
    bool ret = false;
    if (sensorMgmt == nullptr)
    {
        LOG(error) << "Invalid SensorMgmt obj: " << sensor_id << endl;
        return ret;
    }

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return ret;
    }

    shared_ptr<SensorInfo> sensor = sensorControl->getSensor(sensor_id);

    if (sensor->type == SENSOR_TYPE_CSI)
    {
        return true;
    }

    if (sensor.get())
    {
        ret = ((sensor->serviceUrls.find(ONVIF_IMAGING_SERVICE) != sensor->serviceUrls.end()) || sensor->isRemoteSensor);
    }
    return ret;
}

int synchronizeSensorTime(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->synchronizeSensorTime(sensor_id);
}

int getSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, SensorNetworkInfo& networkInfo)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->getSensorNetworkInfo(sensor_id, networkInfo);
}

int setSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorNetworkInfo& networkInfo, bool& rebootNeeded)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->setSensorNetworkInfo(sensor_id, networkInfo, rebootNeeded);
}

int getSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string stream_id, SensorSettings& settings, const string& type)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->getSensorSettings(sensor_id, stream_id, settings, type);
}

int setSensorImageSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorImageSettingsValues& settings)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->setSensorImageSettings(sensor_id, settings);
}

int setSensorEncodeSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorVideoEncoderSettingsValues& settings)
{
    CHECK_SENSOR_MNGT_AND_RETURN(sensorMgmt)

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return -1;
    }

    return sensorControl->setSensorEncodeSettings(sensor_id, settings);
}

bool validateCredentials(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string username, const string password)
{
    if (sensorMgmt == nullptr)
    {
        LOG(error) << "Invalid SensorMgmt obj: " << sensor_id << endl;
        return false;
    }

    std::shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
    if (sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensor_id << endl;
        return false;
    }

    return sensorControl->validateCredentials(sensor_id, username, password);
}

void setStreamDefaultSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string& sensor_id, shared_ptr<StreamInfo>& stream)
{
    if (sensorMgmt == nullptr)
    {
        LOG(error) << "Invalid SensorMgmt obj: " << sensor_id << endl;
        return;
    }
    if (stream->stream_type == StreamType::Native)
    {
        LOG(info) << "No need to set default settings for native stream type sensroId:" << sensor_id << endl;
        return;
    }

    int bitrate = GET_CONFIG().default_bitrate;
    double framerate = GET_CONFIG().default_framerate;
    int gov_length = GET_CONFIG().default_gov_length;
    string bitrate_str = to_string(bitrate);
    string framerate_str = to_string(framerate);
    string resolution_str = GET_CONFIG().default_resolution;

    SensorSettings& current_settings = stream->settings;
    SensorVideoEncoderSettingsValues new_settings = current_settings.encoderValues;

    for (auto encoderSettingsOptions : current_settings.encoderOptions.encoderSettingsOptions)
    {
        if (current_settings.encoderValues.encoding != encoderSettingsOptions.encoding)
        {
            continue;
        }

        if (current_settings.encoderValues.bitrate != bitrate_str)
        {
            if (bitrate)
            {
                int max_bitrate = stringToInt(encoderSettingsOptions.BitrateRange.max, MAX_BITRATE);
                int min_bitrate = stringToInt(encoderSettingsOptions.BitrateRange.min, MIN_BITRATE);
                if (bitrate <= max_bitrate && bitrate >= min_bitrate)
                {
                    new_settings.bitrate = bitrate_str;
                }
                else if (bitrate > max_bitrate)
                {
                    new_settings.bitrate = encoderSettingsOptions.BitrateRange.max;
                }
                else
                {
                    new_settings.bitrate = encoderSettingsOptions.BitrateRange.min;
                }
            }
        }

        if (current_settings.encoderValues.frameRate != framerate_str)
        {
            if (framerate)
            {
                if (!encoderSettingsOptions.FrameRateSupported.empty())
                {
                    std::istringstream supprotedFramerates(encoderSettingsOptions.FrameRateSupported);
                    std::vector<double> frameRates;

                    double rate;
                    while (supprotedFramerates >> rate)
                    {
                        frameRates.push_back(rate);
                    }

                    framerate = findNearestValue(frameRates, framerate);

                    new_settings.frameRate = removeDecimals(framerate);
                }
            }
        }

        if (gov_length != 0)
        {
            int max_govLength = stringToInt(encoderSettingsOptions.GovLengthRange.max, MAX_GOVLENGTH);
            int min_govLength = stringToInt(encoderSettingsOptions.GovLengthRange.min, MIN_GOVLENGTH);
            int new_govLength = boost::algorithm::clamp (gov_length, min_govLength, max_govLength);
            new_settings.govLength = to_string(new_govLength);
        }

        if (current_settings.encoderValues.resolution.getString() != resolution_str)
        {
            Resolution new_res;
            new_res = resolution_str;
            const vector<Resolution>& resolutions_available = encoderSettingsOptions.ResolutionsAvailable;
            bool resolution_supported = false;

            // Check if resolution requested is supported.
            for (uint32_t i = 0; i < resolutions_available.size(); i++)
            {
                Resolution res = resolutions_available[i];
                if (res.getString() == resolution_str)
                {
                    resolution_supported = true;
                    break;
                }
            }

            // Find the nearest resolution to the one requested.
            if (!resolution_supported && resolutions_available.size() > 1)
            {
                size_t res_index = 0;
                bool resolutions_increasing = false;
                // Check if resolutions available are in incerasing or decreasing order.
                if (resolutions_available[0].getPixels() < resolutions_available[1].getPixels())
                {
                    resolutions_increasing = true;
                }

                while(true)
                {
                    int target_pixels = new_res.getPixels();
                    res_index = std::min(res_index, resolutions_available.size() - 1 );
                    Resolution current_res = resolutions_available[res_index];
                    if (res_index == (resolutions_available.size() - 1 ) ||
                        (!resolutions_increasing && current_res.getPixels() <= target_pixels) ||
                        (resolutions_increasing && current_res.getPixels() >= target_pixels) )
                    {
                        new_res = current_res;
                        break;
                    }
                    ++res_index;
                }
            }
            new_settings.resolution = new_res;
        }
        break;
    }

    LOG(info) << "New sensor settings as per default config:"
                << "\n bitrate: " << new_settings.bitrate
                << "\n frameRate: " << new_settings.frameRate
                << "\n govLength: " << new_settings.govLength
                << "\n resolution: " << new_settings.resolution.getString() << endl;

    if (setSensorEncodeSettings(sensorMgmt, sensor_id, new_settings))
    {
        LOG(error) << "Error setting camera default encode settings for " << sensor_id << endl;
    }
    else
    {
        LOG(info) << "Successfully set default settings" << endl;
    }
}

VmsErrorCode getRecordingTimelines(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, std::shared_ptr<nv_vms::DeviceManager> deviceMgr, const string sensor_id, const Json::Value& req_info, Json::Value &response)
{
    LOG(verbose) << "getRecordingTimelines for sensor: " << sensor_id << endl;
    CHECK_SENSOR_MNGT_AND_RETURN_FAIL_RESP(sensorMgmt)

    if (deviceMgr == nullptr)
    {
        LOG(error) << "Invalid deviceManager object" << endl;
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    shared_ptr<SensorInfo> sensor = sensorMgmt->getSensorInfo(sensor_id);
    if (!sensor)
    {
        LOG(error) << "Sensor not found: " << sensor_id << endl;
        SET_VMS_ERROR(VmsErrorCode::CameraNotFoundError, response)
        return VmsErrorCode::CameraNotFoundError;
    }

    // Check if adaptor type is MMS
    const string adaptorType = deviceMgr->getDeviceType();
    
    if (adaptorType == TYPE_MMS)
    {
        // For MMS, use ONVIF APIs
        shared_ptr<SensorControl> sensorControl = sensorMgmt->getSensorControl();
        if (!sensorControl)
        {
            LOG(error) << "SensorControl not found" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        // Call the recording timelines API from ONVIF
        Json::Value timelinesJson;
        int ret = sensorControl->getRecordingTimelines(sensor_id, timelinesJson);

        if (ret != 0)
        {
            LOG(error) << "Failed to get recording timelines for sensor: " << sensor_id << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        // Flatten all recording tokens into a single timeline array
        // ONVIF returns {recordingToken: [timelines]}, but we want just [timelines]
        response = Json::arrayValue;
        
        if (timelinesJson.isObject())
        {
            // Iterate through all recording tokens and collect their timelines
            for (const auto& recordingToken : timelinesJson.getMemberNames())
            {
                const Json::Value& timelineArray = timelinesJson[recordingToken];
                if (timelineArray.isArray())
                {
                    // Append all timeline entries from this recording token
                    for (const auto& timeline : timelineArray)
                    {
                        response.append(timeline);
                    }
                }
            }
        }
    }
    else
    {
        // For non-MMS adaptors (VST, STREAMER), use database like record/{streamId}/timelines
        LOG(info) << "Using database for timelines (adaptor type: " << adaptorType << ")" << endl;
        
        // Parse startTime and endTime from query parameters (same as record API)
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        string startTime;
        string endTime;
        CivetServer::getParam(query_string, "startTime", startTime);
        CivetServer::getParam(query_string, "endTime", endTime);
        
        if (vst_common::getRecordTimelines(sensor_id, startTime, endTime, response))
        {
            LOG(error) << "Failed to get recording timelines for sensor: " << sensor_id << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
    }

    return VmsErrorCode::NoError;
}
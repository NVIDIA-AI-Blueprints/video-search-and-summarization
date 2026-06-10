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

#pragma once

#include "CivetServer.h"
#include "error_code.h"
#include "sensor_management.h"
#include "sqlite_helper.h"

using namespace std;
using namespace nv_vms;

inline constexpr int MAX_FRAMERATE = 60;
inline constexpr int MIN_BITRATE = 64;
inline constexpr int MAX_BITRATE = 20480;
inline constexpr int MIN_GOVLENGTH = 2;
inline constexpr int MAX_GOVLENGTH = 255;
inline constexpr int MAX_QUALITY = 6;
inline constexpr int MIN_QUALITY = 0;
inline constexpr int MAX_ENCODING_INTERVAL = 240;
inline constexpr int MIN_ENCODING_INTERVAL = 1;

VmsErrorCode getSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string& type, Json::Value &response);
VmsErrorCode setSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value& settings, Json::Value &response);
VmsErrorCode addSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const Json::Value& req_info, const Json::Value &data, Json::Value &response);
VmsErrorCode deleteSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response, bool isReqFromRemote = false, bool isReqFromEdgeDevice = false);
VmsErrorCode replaceSensorId(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string old_sensor_id, const Json::Value& in, Json::Value &response);
VmsErrorCode skipStatusError(const string camera_error_code, const string request_method, const string action, Json::Value &response);
VmsErrorCode getAllSensorStatus(std::shared_ptr<DeviceManager> deviceMngr, Json::Value &response);
VmsErrorCode getSensorInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response);
VmsErrorCode setSensorInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value &in, Json::Value &response,
bool isReqFromCloudDevice = false, bool isReqFromEdgeDevice = false);
bool validateSensorName(std::shared_ptr<DeviceManager> deviceMngr, const string sensor_name,
                        std::shared_ptr<SensorInfo>* existingSensor = nullptr);
VmsErrorCode getSensorStatus(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response);
VmsErrorCode getSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response);
VmsErrorCode setSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value &in, Json::Value &response,
bool isReqFromCloudDevice = false, bool isReqFromEdgeDevice = false);
VmsErrorCode setSensorCredentials(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const Json::Value& in, Json::Value &response);
VmsErrorCode rebootSensor(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, Json::Value &response);
int setPTZ(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, PTZAction ptz, string x, string y);
map<PTZAction, ptzRange> getPTZ(std::shared_ptr<DeviceManager> deviceMngr, const string sensor_id);
bool isCameraImageSupported(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id);
int synchronizeSensorTime(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id);
int getSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, SensorNetworkInfo& networkInfo);
int setSensorNetworkInfo(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
int getSensorSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string stream_id, SensorSettings& settings, const string& type);
int setSensorImageSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorImageSettingsValues& settings);
int setSensorEncodeSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const SensorVideoEncoderSettingsValues& settings);
bool validateCredentials(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string sensor_id, const string username, const string password);
void setStreamDefaultSettings(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, const string& sensor_id, shared_ptr<StreamInfo>& stream);
VmsErrorCode getRecordingTimelines(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, std::shared_ptr<nv_vms::DeviceManager> deviceMgr, const string sensor_id, const Json::Value& req_info, Json::Value &response);
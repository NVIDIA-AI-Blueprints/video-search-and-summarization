/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "stream_monitor.h"

#include "database.h"
#include <openssl/pem.h>
#include <openssl/x509.h>

class TempFileScheduler;

// Temp image storage path defines
inline constexpr const char* TEMP_STORAGE_PATH = "/storage/temp_files";
inline constexpr const char* TEMP_STORAGE_DIR = "/temp_files";

using namespace std;
using namespace nv_vms;

namespace vst_common
{
    string sensorStatusEventToString(SensorStatusEvent event);
    VmsErrorCode getSensorStreamList(shared_ptr<DeviceManager> deviceManager, const string sensor_id, const string& query_string, Json::Value &response);
    VmsErrorCode getSensorStreamList(shared_ptr<DeviceManager> deviceManager, const Json::Value& req_info, Json::Value &response);
    VmsErrorCode getSensorStreamListFromDB(shared_ptr<DeviceManager> deviceManager, Json::Value &response, bool fetchFromDB = false);
    void updateSensorDetailsToDB(const string deviceId, shared_ptr<SensorInfo> sensor, bool force=false);
    std::string getSslCertificate();
    EVP_PKEY *generate_key();
    X509 *generate_x509(EVP_PKEY *pkey);
    bool write_cert_to_disk(std::string certFile, EVP_PKEY * pkey, X509 * x509);
    bool encrypt_data(const std::string &input, std::string &output, string &iv_);
    bool decrypt_data(const std::string &input, std::string &output, string &iv_);
    void evp_cleanup(EVP_CIPHER_CTX *ctx_);
    bool evp_final(std::string &out, EVP_CIPHER_CTX *ctx_, int dir_);
    bool evp_update(const std::string &in, std::string &out, EVP_CIPHER_CTX *ctx_, int dir_);
    void evp_setup(int dir, EVP_CIPHER_CTX *ctx_, const EVP_CIPHER *cryptoAlgorithm_, string &key_, string &iv_);
    int evp_blocksize(EVP_CIPHER_CTX *ctx_);
    bool getSha256(const string &str, string &hashedStr);
    string toDomainName(const string& url, const string& id);
    void addSensorToRemoteDevice(shared_ptr<SensorInfo>& sensor, std::shared_ptr<DeviceManager> deviceManager);
    void removeSensorFromRemoteDevice(const string& sensor_id);
    void notifySensorStatusEvent(SensorStatusEvent statusEvent, shared_ptr<SensorInfo> sensor);
    void notifyStreamStatusEvent(SensorStatusEvent statusEvent, shared_ptr<StreamInfo> stream);
    void notifyEvent(const SensorStatus& status, const string& sensor_url, const SensorVideoEncoderSettingsValues* encoder_values = nullptr);
    int addSensorManually(shared_ptr<SensorInfo>& sensor, string& response, std::shared_ptr<DeviceManager> deviceManager);
    VmsErrorCode getCameraPicture(shared_ptr<DeviceManager> deviceManager, const string sensor_id, const string& query_string, Json::Value &response, bool isURLRequested = false);
    std::vector <VideoFileInfo> getStreamerFileName(std::string url);
    void deleteWebrtcSensorDetails(shared_ptr<SensorInfo> sensor);
    int deleteWebrtcSensor(const string sensor_id, std::shared_ptr<DeviceManager> deviceManager);
    void updateSensorInfoToRemoteVst(SensorInfo& sensorInfo);
    void updateSensorNetworkInfoToRemoteVst(const SensorNetworkInfo &netInfo, const string& sensor_id);
    VmsErrorCode getRecordTimelines(const string stream_id, const string start_time, const string end_time, Json::Value &response);
    VmsErrorCode GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out);
    string parseMetadataObject(map<string, float, std::less<>>& coordinates, string &obj_type,
                            double &confidence, Json::Value& curr_object, int index = 0);
    void saveTempFileToDatabase(const string& deviceId, const string& filePath, const string& streamId, size_t fileSize, int64_t expiryTimestamp, int64_t createdTimestamp, int64_t startTimeMs = 0, const string& fileType = "");
    void generateUrlResponse(Json::Value& response, const string& baseUrl, const string& tempFileName, const string& streamId, bool isReplay, const string& startTime,
                            int expiryMinutesInt, int64_t expiryTimestamp, const string& expiryISO);
    string calculateExpiryTime(int expiryMinutesInt, int64_t& expiryTimestamp, int64_t& currentTimestamp);
    bool ShouldRefreshSensorCache(const string& deviceId, size_t currentSensorCount);
    bool tryReuseCachedPictureUrl(const string& deviceId, const string& sensorId, const string& startTime,
                                  const string& expiryMinutesStr, TempFileScheduler& scheduler, Json::Value& response);
    VmsErrorCode handlePictureAction(shared_ptr<DeviceManager> deviceManager, const string& deviceId,
                                     const string& sensorId, const string& queryString, bool isURLRequested,
                                     TempFileScheduler& scheduler, Json::Value& response);
}

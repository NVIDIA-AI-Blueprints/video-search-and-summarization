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

#pragma once
#include <string>
#include "logger.h"

using namespace std;

namespace vst_rtsp
{
    int addStream(const string& id, const string& name, string& url, string& vodUrl);
    int removeStream(const string& id);
    Json::Value activeClientSessions();
    string rtspUrlPrefix(const string& id = "");
    string rtspOriginalUrlPrefix(const string& id = "");
    int removeServerMediaSession(const string& id);
    int updateUser(const string& username);
    int addUser(const string& username, const string& passwordHash);
    int removeUser(const string& username);
    string rtspServerDomainPrefix(const string& id = "");
    string vodServerDomainPrefix(const string& id = "");
}

namespace vst_recorder
{
    int addStream(const string& id, const string& url);
    int removeStream(const string& id);
}

namespace vst_storage
{
    int addOrRemoveFileInProtectList(const string& filePath, const bool& addOrRemove);
    int addOrRemoveFilesInProtectList(const std::vector<string>& filePaths, const bool& addOrRemove);
    int updateStorageSize(const size_t& size, const bool& addOrRemove);
    int doAging(const size_t& bytesToReserve);
    int deleteMediaFile(const string& filePath);
    // Delete every file backing the given stream (regardless of time range) and
    // cascade the sensor-side cleanup through StorageManagement::deleteSensorDetails.
    // Used by the proxy/delete handler so file-type sensor delete clears the upload
    // from disk. Returns 0 on success.
    int deleteFilesByStream(const string& streamId);
    bool checkStorageCapacity(const size_t& size);
}

namespace vst_replaystream
{
    int addStream(const string& id, const string& url);
    int removeStream(const string& id);
    int removeSensor(const string& sensorId);
}

namespace vst_sensor
{
    int deleteSensor(const string& sensorId);
    int deleteStream(const string& streamId);
}
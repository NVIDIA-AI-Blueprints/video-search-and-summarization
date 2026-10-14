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


namespace vst_rtsp
{
    int addStream(const std::string& id, const std::string& name, std::string& url, std::string& vodUrl);
    int removeStream(const std::string& id);
    Json::Value activeClientSessions();
    std::string rtspUrlPrefix(const std::string& id = "");
    std::string rtspOriginalUrlPrefix(const std::string& id = "");
    int removeServerMediaSession(const std::string& id);
    int updateUser(const std::string& username);
    int addUser(const std::string& username, const std::string& passwordHash);
    int removeUser(const std::string& username);
    std::string rtspServerDomainPrefix(const std::string& id = "");
    std::string vodServerDomainPrefix(const std::string& id = "");
}

namespace vst_recorder
{
    int addStream(const std::string& id, const std::string& url);
    int removeStream(const std::string& id);
}

namespace vst_storage
{
    int addOrRemoveFileInProtectList(const std::string& filePath, const bool& addOrRemove);
    int addOrRemoveFilesInProtectList(const std::vector<std::string>& filePaths, const bool& addOrRemove);
    int updateStorageSize(const size_t& size, const bool& addOrRemove);
    int doAging(const size_t& bytesToReserve);
    int deleteMediaFile(const std::string& filePath);
    // Delete every file backing the given stream (regardless of time range) and
    // cascade the sensor-side cleanup through StorageManagement::deleteSensorDetails.
    // Used by the proxy/delete handler so file-type sensor delete clears the upload
    // from disk. Returns 0 on success.
    int deleteFilesByStream(const std::string& streamId);
    bool checkStorageCapacity(const size_t& size);
}

namespace vst_replaystream
{
    int addStream(const std::string& id, const std::string& url);
    int removeStream(const std::string& id);
    int removeSensor(const std::string& sensorId);
}

namespace vst_sensor
{
    int deleteSensor(const std::string& sensorId);
    int deleteStream(const std::string& streamId);
}
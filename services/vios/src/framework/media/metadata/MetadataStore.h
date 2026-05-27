/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <jsoncpp/json/json.h>
#include <queue>
#include <mutex>
#include <string>

typedef struct _MetadataParams
{
    _MetadataParams() : m_startTime("")
                      , m_endTime ("")
                      , m_sensorName("")
    { }
    std::string m_startTime;
    std::string m_endTime;
    std::string m_sensorName;
    bool m_isLive = false;
} MetadataParams;
class IMetadataStore
{
public:
    virtual ~IMetadataStore() = default;

    virtual void addMetadata(const Json::Value& metadata) = 0;
    virtual Json::Value getMetadata(const int64_t frameTS) = 0;
    virtual void reFillMetadata(std::queue<Json::Value>& qToFill, std::mutex& qToFillMutex) = 0;
    virtual size_t getMetadataSize()
    {
        std::lock_guard<std::mutex> guard(m_metadataQueueMutex);
        return m_metadataQueue.size();
    }
    virtual bool isMetadataQueueEmpty()
    {
        std::lock_guard<std::mutex> guard(m_metadataQueueMutex);
        return m_metadataQueue.empty();
    }

protected:
    std::queue<Json::Value> m_metadataQueue;
    std::mutex              m_metadataQueueMutex;
};
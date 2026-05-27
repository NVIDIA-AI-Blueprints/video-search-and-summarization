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

#include "unified_storage_writer.h"
#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <string>

namespace nv_vms
{

/**
 * @brief Cloud storage implementation using appsink
 */
class UnifiedCloudStorageWriter : public UnifiedStorageWriter
{
public:
    UnifiedCloudStorageWriter();
    virtual ~UnifiedCloudStorageWriter();

    // Override for optimized pipeline reuse
    std::string startWrite(const std::string& remote_path, const std::string& stream_id,
                           size_t estimated_size = 0) override;

    // Implement pure virtual functions
    StorageResult uploadFile(const std::string& local_path, const std::string& remote_path) override;
    bool deleteFile(const std::string& remote_path) override;
    bool fileExists(const std::string& remote_path) const override;
    size_t getFileSize(const std::string& remote_path) const override;

protected:
    // UnifiedStorageWriter interface
    bool initializeStorage() override;
    GstElement* createSinkElement() override;
    bool configureSinkElement(GstElement* sink, const std::string& remote_path, const std::string& session_id) override;
    StorageResult finalizeSession(const std::string& session_id, const std::string& stream_id) override;
    bool cleanupSession(const std::string& session_id) override;

    // Cloud storage specific callbacks
    static GstFlowReturn onNewSampleCloud(GstAppSink* appsink, gpointer user_data);
    static void onEOSCloud(GstAppSink* appsink, gpointer user_data);

    // Session management helper
    void cleanupSessionTracking(const std::string& session_id);

    // Cloud writer integration
    std::shared_ptr<StorageWriter> m_cloud_writer;
    mutable std::mutex m_cloud_writer_mutex;

    // Session tracking
    std::map<std::string, std::string, std::less<>> m_active_sessions;      // session_id -> stream_id
    std::map<std::string, std::string, std::less<>> m_session_remote_paths; // session_id -> remote_path
    std::mutex m_session_mutex;
};

} // namespace nv_vms
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

#include "../unified_storage_types.h"
#include "unified_storage_writer.h"
#include <gst/gst.h>

namespace nv_vms
{

/**
 * @brief Local storage implementation using filesink
 */
class UnifiedLocalStorageWriter : public UnifiedStorageWriter
{
public:
    UnifiedLocalStorageWriter();
    virtual ~UnifiedLocalStorageWriter();

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
};

} // namespace nv_vms
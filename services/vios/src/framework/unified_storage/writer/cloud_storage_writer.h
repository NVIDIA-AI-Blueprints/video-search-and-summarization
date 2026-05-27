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
#include <chrono>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace nv_vms
{

// Forward declaration
class CloudStorageBuffer;

// Make common types available globally in the namespace
using StorageResult = nv_vms::StorageResult;
using StorageConfig = nv_vms::StorageConfig;
using StorageStats = nv_vms::StorageStats;

/**
 * Abstract base class for all storage writers
 * Cloud storage types will automatically handle buffering internally
 */
class StorageWriter
{
public:
    virtual ~StorageWriter() = default;

    // Core interface - buffering handled internally for cloud storage
    virtual bool isAvailable() const = 0;
    virtual std::string getStorageTypeName() const = 0;

    // Session management
    virtual std::string startSession(const std::string& remote_path, const std::string& stream_id,
                                     size_t estimated_size = 0) = 0;

    // Write operations - buffering handled internally for cloud storage
    virtual bool writeData(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                           const std::string& media_type = "video") = 0;

    virtual StorageResult completeSession(const std::string& session_id, const std::string& stream_id) = 0;

    virtual bool cancelSession(const std::string& session_id, const std::string& stream_id) = 0;

    // File operations
    virtual StorageResult uploadFile(const std::string& local_path, const std::string& remote_path) = 0;

    virtual bool deleteFile(const std::string& remote_path) = 0;
    virtual bool fileExists(const std::string& remote_path) const = 0;
    virtual size_t getFileSize(const std::string& remote_path) const = 0;

    // Statistics and monitoring
    virtual StorageStats getStats() const = 0;
    virtual void resetStats() = 0;

    // Configuration
    virtual bool configure(const StorageConfig& config) = 0;
    virtual StorageConfig getConfiguration() const = 0;

    // Health and diagnostics
    virtual bool performHealthCheck() = 0;
    virtual std::string getLastError() const = 0;

protected:
    // Helper for storage types to determine if they should use buffering
    virtual bool shouldUseBuffering() const
    {
        return false;
    } // Default: no buffering (local storage)
};

/**
 * Base class for cloud storage that includes buffering
 * This class handles all buffering logic internally, providing a clean interface to GstMux
 */
class CloudStorageWriter : public StorageWriter
{
public:
    CloudStorageWriter();
    virtual ~CloudStorageWriter();

    // Override session management to handle stream_id storage
    std::string startSession(const std::string& remote_path, const std::string& stream_id,
                             size_t estimated_size = 0) override;

    // Override write operations to use buffering
    bool writeData(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                   const std::string& media_type = "video") override final;

    StorageResult completeSession(const std::string& session_id, const std::string& stream_id) override final;

    bool cancelSession(const std::string& session_id, const std::string& stream_id) override final;

    // Configure buffering
    bool configure(const StorageConfig& config) override;

    // Get buffering statistics
    StorageStats getStats() const override;
    void resetStats() override;

    // Buffering control
    void flushBuffers();
    void clearBuffers();

protected:
    // Should use buffering for cloud storage
    bool shouldUseBuffering() const override
    {
        return true;
    }

    // Abstract methods for cloud storage implementations
    virtual bool doWriteData(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                             const std::string& media_type = "video") = 0;

    virtual StorageResult doCompleteSession(const std::string& session_id, const std::string& stream_id) = 0;

    virtual bool doCancelSession(const std::string& session_id, const std::string& stream_id) = 0;

private:
    std::unique_ptr<CloudStorageBuffer> m_buffer;
    StorageConfig m_config;
    mutable std::mutex m_stats_mutex;
    StorageStats m_stats;

    // Session management for buffering
    std::map<std::string, std::string, std::less<>> m_active_sessions; // session_id -> stream_id
    std::mutex m_session_mutex;

    // Internal methods
    void initializeBuffering(const std::string& stream_id, const std::string& session_id);
    void stopBuffering();
    bool handleBufferedWrite(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                             const std::string& media_type = "video");
    void updateBufferingStats();
};

} // namespace nv_vms
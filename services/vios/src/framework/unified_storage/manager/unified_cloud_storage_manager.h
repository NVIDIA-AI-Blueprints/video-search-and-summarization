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

#include "unified_storage_manager.h"
#include "cloud_manager_factory.h"
#include <filesystem>
#include <memory>
#include <string>
#include <vector>
#include <mutex>

namespace nv_vms
{

// Forward declarations
class CloudManager;

/**
 * @brief Concrete implementation of unified storage manager for cloud storage
 */
class UnifiedCloudStorageManager : public UnifiedStorageManager
{
public:
    UnifiedCloudStorageManager();
    virtual ~UnifiedCloudStorageManager();

    // Core interface
    bool isAvailable() const override;
    std::string getStorageMode() const override;

    // Configuration
    bool configureStorage(const StorageConfig& config) override;
    StorageConfig getStorageConfiguration() const override;

    // File operations (unified interface)
    DeleteResult deleteFile(const std::string& path) override;
    DeleteResult deleteDirectory(const std::string& path, bool recursive = false) override;
    
    // File existence checking (unified interface)
    bool isFileExist(const std::string& path) const override;
    
    // Multi-file delete operations
    MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths) override;
    MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path, 
                                            const std::string& pattern = "",
                                            bool recursive = false) override;
    
    // Bucket operations (cloud storage)
    BucketResult createBucket(const std::string& bucket_name) override;
    BucketResult deleteBucket(const std::string& bucket_name, bool force = false) override;
    BucketResult checkBucketExists(const std::string& bucket_name) override;
    std::vector<BucketInfo> listBuckets() override;
    
    // Statistics and monitoring
    StorageStats getManagerStats() const override;
    void resetManagerStats() override;

    // Health and diagnostics
    bool performHealthCheck() override;
    std::string getLastError() const override;

protected:
    // UnifiedStorageManager interface
    bool initializeStorage() override;
    bool cleanupStorage() override;

    // Cloud storage specific methods
    std::pair<std::string, std::string> parseCloudPath(const std::string& path) const;
    std::string formatCloudPath(const std::string& bucket, const std::string& object_key) const;
    bool validateBucketName(const std::string& bucket_name) const;
    bool validateObjectKey(const std::string& object_key) const;
    std::vector<std::string> listObjectsInDirectory(const std::string& bucket, 
                                                    const std::string& prefix,
                                                    const std::string& pattern = "") const;

    // Local file operations (for hybrid cloud/local support)
    bool isLocalFilePath(const std::string& path) const;
    DeleteResult deleteLocalFile(const std::string& path);

    // Member variables
    // Mutex to protect m_cloud_manager from concurrent access and use-after-free issues
    // All methods accessing m_cloud_manager must acquire this lock before dereferencing
    mutable std::mutex m_cloud_manager_mutex;
    std::unique_ptr<CloudManager> m_cloud_manager;
    std::string m_bucket_name;
    std::string m_endpoint;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_region;
    bool m_use_ssl;
    uint32_t m_timeout_seconds;
    uint32_t m_max_retries;
};

} // namespace nv_vms 
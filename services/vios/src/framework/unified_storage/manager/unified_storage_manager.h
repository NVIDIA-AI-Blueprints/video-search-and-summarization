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
#include <memory>
#include <string>
#include <vector>
#include <mutex>
#include <map>
#include <cstdint>
#include <chrono>
#include <atomic>

namespace nv_vms
{

// Forward declarations
class CloudReader;

/**
 * @brief Result structure for bucket operations
 */
struct BucketResult
{
    bool success;
    std::string message;
    std::string errorCode;
    std::string bucketName;
    std::chrono::milliseconds duration;
    
    BucketResult() : success(false), duration(0) {}
    BucketResult(bool success, const std::string& message = "") 
        : success(success), message(message), duration(0) {}
};

/**
 * @brief Result structure for delete operations
 */
struct DeleteResult
{
    bool success;
    std::string message;
    std::string errorCode;
    std::string deletedPath;
    uint64_t deletedSize;
    std::chrono::milliseconds duration;
    
    DeleteResult() : success(false), deletedSize(0), duration(0) {}
    DeleteResult(bool success, const std::string& message = "") 
        : success(success), message(message), deletedSize(0), duration(0) {}
};

/**
 * @brief Result structure for multi-delete operations
 */
struct MultiDeleteResult
{
    bool overall_success;
    std::string error_message;
    std::string error_code;
    std::vector<DeleteResult> delete_results;
    size_t total_files;
    size_t successful_deletes;
    size_t failed_deletes;
    uint64_t total_bytes_freed;
    std::chrono::milliseconds total_duration;
    
    MultiDeleteResult() : overall_success(false), total_files(0), successful_deletes(0), 
                         failed_deletes(0), total_bytes_freed(0), total_duration(0) {}
    
    void addResult(const DeleteResult& result) {
        delete_results.push_back(result);
        total_duration += result.duration;
        if (result.success) {
            successful_deletes++;
            total_bytes_freed += result.deletedSize;
        } else {
            failed_deletes++;
        }
        
        // Update overall_success: true if no failures, false if any failures
        overall_success = (failed_deletes == 0);
    }
};

/**
 * @brief Unified storage manager that handles administrative operations for both local and cloud storage
 */
class UnifiedStorageManager
{
public:
    UnifiedStorageManager(StorageType type);
    virtual ~UnifiedStorageManager();

    // Core interface
    virtual bool isAvailable() const;
    virtual std::string getStorageMode() const;
    StorageType getStorageType() const;

    // Configuration
    virtual bool configureStorage(const StorageConfig& config);
    virtual StorageConfig getStorageConfiguration() const;

    // File operations (unified interface)
    virtual DeleteResult deleteFile(const std::string& path);
    virtual DeleteResult deleteDirectory(const std::string& path, bool recursive = false);
    
    // File existence checking (unified interface)
    virtual bool isFileExist(const std::string& path) const;
    
    // Multi-file delete operations
    virtual MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths);
    virtual MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path, 
                                                    const std::string& pattern = "",
                                                    bool recursive = false);
    
    // Bucket operations (cloud storage)
    virtual BucketResult createBucket(const std::string& bucket_name);
    virtual BucketResult deleteBucket(const std::string& bucket_name, bool force = false);
    virtual BucketResult checkBucketExists(const std::string& bucket_name);
    virtual std::vector<BucketInfo> listBuckets();
    
    // Directory operations (local storage)
    virtual bool createDirectory(const std::string& path, bool create_parents = true);
    virtual bool directoryExists(const std::string& path) const;
    
    // Statistics and monitoring
    virtual StorageStats getManagerStats() const;
    virtual void resetManagerStats();

    // Health and diagnostics
    virtual bool performHealthCheck();
    virtual std::string getLastError() const;

protected:
    // Abstract methods for derived classes
    virtual bool initializeStorage() = 0;
    virtual bool cleanupStorage() = 0;

    // Helper methods
    /**
     * @brief Records an operation result in the statistics
     * @param success Whether the operation was successful
     * @param duration Duration of the operation
     * @param errorCode Error code if operation failed
     * @note This function is thread-safe and handles its own mutex locking
     */
    void recordOperation(bool success, std::chrono::milliseconds duration, 
                        const std::string& errorCode = "");
    
    /**
     * @brief Formats a path by removing leading/trailing slashes
     * @param path The path to format
     * @return Formatted path
     * @note This function is thread-safe as it only operates on the input parameter
     */
    std::string formatPath(const std::string& path) const;
    
    /**
     * @brief Validates a path for invalid characters
     * @param path The path to validate
     * @return true if path is valid, false otherwise
     * @note This function is thread-safe as it only operates on the input parameter
     */
    bool validatePath(const std::string& path) const;

    // Member variables
    StorageType m_storage_type;
    StorageConfig m_config;
    StorageStats m_stats;
    std::string m_last_error;
    std::atomic<bool> m_initialized;
    mutable std::mutex m_mutex;
};

} // namespace nv_vms 
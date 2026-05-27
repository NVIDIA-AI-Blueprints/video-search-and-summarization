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

#include <string>
#include <memory>
#include <vector>
#include <functional>
#include <chrono>
#include <map>
#include <mutex>
#include <cstdint>
#include <thread>
#include <queue>
#include <atomic>
#include <condition_variable>
#include <jsoncpp/json/json.h>
#include "../unified_storage_types.h"

namespace nv_vms {

/**
 * @brief Configuration for cloud manager
 */
struct CloudManagerConfig
{
    CloudStorageType storageType = CloudStorageType::UNKNOWN;
    std::string endpoint;
    std::string region;
    std::string accessKeyId;
    std::string secretAccessKey;
    std::string sessionToken;           // For temporary credentials
    bool useSSL = true;
    uint32_t timeoutSeconds = 30;
    uint32_t maxRetries = 3;
    
    // Authentication settings
    struct AuthConfig
    {
        bool useDefaultCredentials = false;
        bool useInstanceProfile = false;
        std::string credentialsFile;
        std::string profileName = "default";
    } auth;
    
    // Request settings
    struct RequestConfig
    {
        bool enableCache = true;        // Enable response caching
        uint32_t cacheTimeoutSec = 300; // Cache timeout in seconds
    } request;
    
    CloudManagerConfig() = default;
    CloudManagerConfig(CloudStorageType type) : storageType(type)
    {
    }
};

/**
 * @brief Statistics for cloud manager operations
 */
struct CloudManagerStats
{
    uint64_t totalRequests = 0;
    uint64_t successfulRequests = 0;
    uint64_t failedRequests = 0;
    uint64_t objectsDeleted = 0;
    uint64_t bucketsCreated = 0;
    uint64_t bucketsDeleted = 0;
    std::chrono::milliseconds totalLatency{0};
    std::chrono::milliseconds averageLatency{0};
    std::chrono::system_clock::time_point lastRequestTime;
    
    // Error tracking
    std::map<std::string, uint32_t, std::less<>> errorCounts;
    
    void recordRequest(bool success, std::chrono::milliseconds latency, 
                      const std::string& errorCode = "")
    {
        totalRequests++;
        totalLatency += latency;
        lastRequestTime = std::chrono::system_clock::now();
        
        if (success)
        {
            successfulRequests++;
        }
        else
        {
            failedRequests++;
            if (!errorCode.empty())
            {
                errorCounts[errorCode]++;
            }
        }
        
        if (totalRequests > 0)
        {
            averageLatency = totalLatency / totalRequests;
        }
    }
    
    void reset()
    {
        totalRequests = 0;
        successfulRequests = 0;
        failedRequests = 0;
        objectsDeleted = 0;
        bucketsCreated = 0;
        bucketsDeleted = 0;
        totalLatency = std::chrono::milliseconds{0};
        averageLatency = std::chrono::milliseconds{0};
        errorCounts.clear();
    }
};

/**
 * @brief Abstract base class for cloud storage management operations
 * 
 * This class provides a unified interface for managing cloud storage operations
 * across different cloud providers (AWS S3, MinIO, Google Cloud Storage, Azure Blob Storage).
 * 
 * Key features:
 * - Object deletion operations
 * - Bucket creation and deletion
 * - Bucket listing and existence checks
 * - Comprehensive error handling and retry logic
 * - Performance monitoring and statistics
 * - Thread-safe operations
 * - Configuration management
 */
class CloudManager
{
public:
    CloudManager();
    virtual ~CloudManager();
    
    // Core interface
    virtual bool isAvailable() const = 0;
    virtual std::string getStorageTypeName() const = 0;
    virtual CloudStorageType getStorageType() const = 0;
    
    // Configuration
    virtual bool configure(const CloudManagerConfig& config);
    virtual CloudManagerConfig getConfiguration() const = 0;
    
    // Object operations
    virtual CloudResult deleteObject(const std::string& bucket,
                                   const std::string& objectKey) = 0;
    
    virtual CloudResult deleteMultipleObjects(const std::string& bucket,
                                            const std::vector<std::string>& objectKeys) = 0;
    
    virtual CloudResult deleteObjectsWithPrefix(const std::string& bucket,
                                              const std::string& prefix) = 0;
    
    // Object listing (for directory operations)
    virtual CloudResult listObjects(const std::string& bucket,
                                  const std::string& prefix,
                                  std::vector<std::string>& objectKeys,
                                  uint32_t maxKeys = 1000) = 0;
    
    // Object existence checking
    virtual CloudResult checkObjectExists(const std::string& bucket,
                                        const std::string& objectKey) = 0;
    
    // Object information
    virtual CloudResult getObjectInfo(const std::string& bucket,
                                    const std::string& objectKey,
                                    CloudObject& objectInfo) = 0;
    
    // Bucket operations
    virtual CloudResult createBucket(const std::string& bucketName,
                                   const std::string& region = "") = 0;
    
    virtual CloudResult deleteBucket(const std::string& bucketName,
                                   bool force = false) = 0;
    
    virtual CloudResult checkBucketExists(const std::string& bucketName) = 0;
    
    virtual CloudResult listBuckets(std::vector<std::string>& buckets) = 0;
    
    // Bucket information
    virtual CloudResult getBucketInfo(const std::string& bucketName,
                                    nv_vms::BucketInfo& bucketInfo) = 0;
    
    // Health and diagnostics
    virtual CloudResult performHealthCheck() = 0;
    virtual std::string getLastError() const = 0;
    
    // Statistics and monitoring
    virtual CloudManagerStats getStats() const = 0;
    virtual void resetStats() = 0;
    
    // Utility methods
    virtual bool validateBucketName(const std::string& bucketName) const = 0;
    virtual bool validateObjectKey(const std::string& objectKey) const = 0;
    virtual std::string sanitizeObjectName(const std::string& objectName) const = 0;
    
    // JSON utilities
    virtual Json::Value bucketInfoToJson(const BucketInfo& bucketInfo) const;
    virtual Json::Value statsToJson(const CloudManagerStats& stats) const;
    
protected:
    // Common utility methods
    virtual void updateStats(bool success, std::chrono::milliseconds latency, 
                    const std::string& errorCode = "");
    virtual void setLastError(const std::string& error);
    std::string formatTimestamp(const std::string& timestamp) const;
    bool validateConfiguration(const CloudManagerConfig& config) const;
    bool validateEndpointUrl(const std::string& endpointUrl) const;
    
    // Member variables
    CloudManagerConfig m_config;
    CloudManagerStats m_stats;
    std::string m_last_error;
    bool m_initialized;
    mutable std::mutex m_mutex;
    mutable std::mutex m_stats_mutex;
};

} // namespace nv_vms 
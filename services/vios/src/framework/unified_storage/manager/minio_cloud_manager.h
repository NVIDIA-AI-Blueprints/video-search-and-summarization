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

#include "cloud_manager.h"
#include <string>
#include <memory>
#include <vector>
#include <chrono>
#include <mutex>

// MinIO SDK includes
#include "minio-cpp/client.h"
#include "minio-cpp/credentials.h"

namespace nv_vms {

/**
 * @brief MinIO implementation of CloudManager using MinIO C++ SDK
 * 
 * Features:
 * - MinIO C++ SDK integration for S3-compatible operations
 * - Native authentication and request signing
 * - Built-in HTTP client from MinIO SDK
 * - Comprehensive error handling and retry logic
 * - Performance monitoring and statistics
 * - Support for MinIO-specific features
 */
class MinioCloudManager : public CloudManager {
public:
    MinioCloudManager();
    virtual ~MinioCloudManager();
    
    // Delete copy and move operations to prevent invalid states
    MinioCloudManager(const MinioCloudManager&) = delete;
    MinioCloudManager& operator=(const MinioCloudManager&) = delete;
    MinioCloudManager(MinioCloudManager&&) = delete;
    MinioCloudManager& operator=(MinioCloudManager&&) = delete;
    
    // Core interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override { return "MinIO"; }
    CloudStorageType getStorageType() const override { return CloudStorageType::MINIO; }
    
    // Configuration
    bool configure(const CloudManagerConfig& config) override;
    CloudManagerConfig getConfiguration() const override;
    
    // Object deletion operations
    CloudResult deleteObject(const std::string& bucket,
                           const std::string& objectKey) override;
    
    CloudResult deleteMultipleObjects(const std::string& bucket,
                                    const std::vector<std::string>& objectKeys) override;
    
    CloudResult deleteObjectsWithPrefix(const std::string& bucket,
                                      const std::string& prefix) override;
    
    // Object listing (for directory operations)
    CloudResult listObjects(const std::string& bucket,
                          const std::string& prefix,
                          std::vector<std::string>& objectKeys,
                          uint32_t maxKeys = 1000) override;
    
    // Object existence checking
    CloudResult checkObjectExists(const std::string& bucket,
                                const std::string& objectKey) override;
    
    CloudResult getObjectInfo(const std::string& bucket,
                            const std::string& objectKey,
                            CloudObject& objectInfo) override;
    
    // Bucket operations
    CloudResult createBucket(const std::string& bucketName,
                           const std::string& region = "") override;
    
    CloudResult deleteBucket(const std::string& bucketName,
                           bool force = false) override;
    
    CloudResult checkBucketExists(const std::string& bucketName) override;
    
    CloudResult listBuckets(std::vector<std::string>& buckets) override;
    
    // Bucket information
    CloudResult getBucketInfo(const std::string& bucketName,
                            BucketInfo& bucketInfo) override;
    
    // Health and diagnostics
    CloudResult performHealthCheck() override;
    std::string getLastError() const override;
    
    // Statistics and monitoring
    CloudManagerStats getStats() const override;
    void resetStats() override;
    
    // Utility methods
    bool validateBucketName(const std::string& bucketName) const override;
    bool validateObjectKey(const std::string& objectKey) const override;
    std::string sanitizeObjectName(const std::string& objectName) const override;
    
private:
    // MinIO client management
    bool initializeMinioClient();
    void shutdownMinioClient();
    bool ensureClientInitialized();
    
    // MinIO SDK operations
    CloudResult deleteObjectWithMinioClient(const std::string& bucket,
                                          const std::string& objectKey);
    
    CloudResult deleteMultipleObjectsWithMinioClient(const std::string& bucket,
                                                   const std::vector<std::string>& objectKeys);
    
    CloudResult createBucketWithMinioClient(const std::string& bucketName,
                                          const std::string& region);
    
    CloudResult deleteBucketWithMinioClient(const std::string& bucketName);
    
    CloudResult checkBucketExistsWithMinioClient(const std::string& bucketName);
    
    CloudResult getObjectInfoWithMinioClient(const std::string& bucket,
                                           const std::string& objectKey,
                                           CloudObject& objectInfo);
    
    CloudResult listBucketsWithMinioClient(std::vector<std::string>& buckets);
    
    CloudResult getBucketInfoWithMinioClient(const std::string& bucketName,
                                           BucketInfo& bucketInfo);
    
    // Helper methods
    std::string getRequiredConfigParameter(const std::string& key) const;
    CloudResult handleMinioError(const minio::error::Error& error, const std::string& operation) const;
    std::string extractRegionFromEndpoint(const std::string& endpoint) const;
    
protected:
    // Override base class protected methods
    void setLastError(const std::string& error) override;
    void updateStats(bool success, std::chrono::milliseconds duration, const std::string& errorCode = "") override;
    
    // Member variables
    std::unique_ptr<minio::s3::Client> m_minio_client;
    std::unique_ptr<minio::creds::StaticProvider> m_credentials;
    std::string m_endpoint;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_region;
    bool m_use_ssl;
    uint32_t m_timeout_seconds;
    uint32_t m_max_retries;
    mutable std::mutex m_client_mutex;
};

} // namespace nv_vms 
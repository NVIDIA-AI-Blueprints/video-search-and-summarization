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

#include "cloud_reader.h"
#include <string>
#include <memory>
#include <vector>
#include <chrono>

// MinIO SDK includes
#include "minio-cpp/client.h"
#include "minio-cpp/credentials.h"

namespace nv_vms {

/**
 * @brief MinIO implementation of CloudReader using MinIO C++ SDK
 *
 * Features:
 * - MinIO C++ SDK integration for S3-compatible operations
 * - Native authentication and request signing
 * - Built-in HTTP client from MinIO SDK
 * - Comprehensive error handling and retry logic
 * - Performance monitoring and statistics
 * - Support for MinIO-specific features
 */
class MinioCloudReader : public CloudReader {
public:
    MinioCloudReader();
    virtual ~MinioCloudReader();
    
    // Delete copy and move operations to prevent unsafe MinIO SDK object lifecycle issues
    MinioCloudReader(const MinioCloudReader&) = delete;
    MinioCloudReader& operator=(const MinioCloudReader&) = delete;
    MinioCloudReader(MinioCloudReader&&) = delete;
    MinioCloudReader& operator=(MinioCloudReader&&) = delete;

    // Core interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override { return "MinIO"; }
    CloudStorageType getStorageType() const override { return CloudStorageType::MINIO; }

    // Configuration
    bool configure(const CloudReaderConfig& config) override;
    CloudReaderConfig getConfiguration() const override;

    // Object listing operations
    CloudListResult listObjects(const std::string& bucket,
                               const std::string& prefix = "",
                               uint32_t maxKeys = 1000) override;

    CloudListResult listObjectsPaginated(const std::string& bucket,
                                        const std::string& prefix = "",
                                        const std::string& marker = "",
                                        uint32_t maxKeys = 1000) override;

    CloudListResult listAllObjects(const std::string& bucket,
                                  const std::string& prefix = "") override;

    // Object operations
    CloudResult downloadObject(const std::string& bucket,
                              const std::string& objectKey,
                              const std::string& localPath) override;

    CloudResult getObjectInfo(const std::string& bucket,
                             const std::string& objectKey,
                             CloudObject& objectInfo) override;

    CloudResult checkObjectExists(const std::string& bucket,
                                 const std::string& objectKey) override;

    // Override deleteObject from base class with MinIO-specific implementation
    CloudResult deleteObject(const std::string& bucket,
                            const std::string& objectKey) override;

    // Bucket operations
    CloudResult listBuckets(std::vector<std::string>& buckets) override;
    CloudResult checkBucketExists(const std::string& bucket) override;

    // URL generation
    CloudResult generatePresignedUrl(const std::string& bucket,
                                   const std::string& objectKey,
                                   uint32_t expirationSeconds,
                                   std::string& url) override;

    // Statistics and monitoring
    CloudReaderStats getStats() const override;
    void resetStats() override;

    // Health and diagnostics
    CloudResult performHealthCheck() override;
    std::string getLastError() const override;

private:
    // MinIO client management
    bool initializeMinioClient();
    void shutdownMinioClient();
    bool ensureClientInitialized();

    // MinIO SDK operations
    CloudResult getObjectWithMinioClient(const std::string& bucket,
                                        const std::string& objectKey,
                                        const std::string& localPath);

    CloudResult statObjectWithMinioClient(const std::string& bucket,
                                         const std::string& objectKey,
                                         CloudObject& objectInfo);

    CloudResult listObjectsWithMinioClient(const std::string& bucket,
                                          const std::string& prefix,
                                          const std::string& marker,
                                          uint32_t maxKeys,
                                          CloudListResult& result);

    CloudResult listBucketsWithMinioClient(std::vector<std::string>& buckets);

    CloudResult checkObjectExistsWithMinioClient(const std::string& bucket,
                                                const std::string& objectKey);

    CloudResult checkBucketExistsWithMinioClient(const std::string& bucket);

    CloudResult deleteObjectWithMinioClient(const std::string& bucket,
                                           const std::string& objectKey);

    CloudResult generatePresignedUrlWithMinioClient(const std::string& bucket,
                                                   const std::string& objectKey,
                                                   uint32_t expirationSeconds,
                                                   std::string& url);

    // Utility methods
    std::string buildMinioEndpoint() const;
    std::string getRequiredConfigParameter(const std::string& key) const;

    // MinIO client and configuration
    std::unique_ptr<minio::s3::Client> m_minio_client;
    std::unique_ptr<minio::creds::StaticProvider> m_credentials;
    mutable std::mutex m_client_mutex;
    bool m_client_initialized;

    // Configuration
    std::string m_endpoint_url;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_session_token;
    std::string m_region;
    bool m_use_ssl;
    uint32_t m_timeout_seconds;
    uint32_t m_max_retries;
};

} // namespace nv_vms
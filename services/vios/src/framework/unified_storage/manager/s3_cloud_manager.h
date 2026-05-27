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
#include <cstdint>

// AWS SDK includes
#include <aws/core/Aws.h>
#include <aws/core/auth/AWSCredentials.h>
#include <aws/core/auth/AWSCredentialsProvider.h>
#include <aws/core/auth/signer/AWSAuthV4Signer.h>
#include <aws/core/client/ClientConfiguration.h>
#include <aws/core/http/HttpRequest.h>
#include <aws/core/http/HttpResponse.h>
#include <aws/core/http/HttpClient.h>
#include <aws/core/http/HttpClientFactory.h>
#include <aws/core/utils/xml/XmlSerializer.h>
#include <aws/core/utils/memory/stl/AWSString.h>

namespace nv_vms {

/**
 * @brief AWS S3 implementation of CloudManager
 * 
 * Features:
 * - AWS S3 SDK integration for S3 operations
 * - Native AWS authentication and request signing
 * - Comprehensive error handling and retry logic
 * - Performance monitoring and statistics
 * - Support for AWS S3-specific features
 * 
 * Note: This is a stub implementation. Full implementation would require AWS SDK for C++
 */
class S3CloudManager : public CloudManager {
public:
    S3CloudManager();
    virtual ~S3CloudManager();
    
    // Delete copy and move operations to prevent unsafe AWS SDK object lifecycle issues
    S3CloudManager(const S3CloudManager&) = delete;
    S3CloudManager& operator=(const S3CloudManager&) = delete;
    S3CloudManager(S3CloudManager&&) = delete;
    S3CloudManager& operator=(S3CloudManager&&) = delete;
    
    // Core interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override { return "AWS S3"; }
    CloudStorageType getStorageType() const override { return CloudStorageType::AWS_S3; }
    
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
    
    // Bucket operations
    CloudResult createBucket(const std::string& bucketName,
                           const std::string& region = "") override;
    
    CloudResult deleteBucket(const std::string& bucketName,
                           bool force = false) override;
    
    CloudResult checkBucketExists(const std::string& bucketName) override;
    
    CloudResult listBuckets(std::vector<std::string>& buckets) override;
    
    // Object existence checking
    CloudResult checkObjectExists(const std::string& bucket,
                                 const std::string& objectKey) override;
    
    CloudResult getObjectInfo(const std::string& bucket,
                            const std::string& objectKey,
                            CloudObject& objectInfo) override;
    
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
    // AWS S3 client management
    bool initializeS3Client();
    void shutdownS3Client();
    bool ensureClientInitialized();
    
    // AWS S3 SDK operations (stub implementations)
    CloudResult deleteObjectWithS3Client(const std::string& bucket,
                                       const std::string& objectKey);
    
    CloudResult deleteMultipleObjectsWithS3Client(const std::string& bucket,
                                                const std::vector<std::string>& objectKeys);
    
    CloudResult createBucketWithS3Client(const std::string& bucketName,
                                       const std::string& region);
    
    CloudResult deleteBucketWithS3Client(const std::string& bucketName);
    
    CloudResult checkBucketExistsWithS3Client(const std::string& bucketName);
    
    CloudResult listBucketsWithS3Client(std::vector<std::string>& buckets);
    
    CloudResult getBucketInfoWithS3Client(const std::string& bucketName,
                                        BucketInfo& bucketInfo);
    
    // HTTP request operations using AWS SDK
    CloudResult makeS3Request(const std::string& method,
                             const std::string& bucket,
                             const std::string& objectKey,
                             const std::string& queryParams,
                             std::shared_ptr<Aws::Http::HttpResponse>& response);
    
    CloudResult makeS3DeleteRequest(const std::string& bucket,
                                   const std::string& objectKey,
                                   std::shared_ptr<Aws::Http::HttpResponse>& response);
    
    CloudResult makeS3DeleteMultipleRequest(const std::string& bucket,
                                           const std::vector<std::string>& objectKeys,
                                           std::shared_ptr<Aws::Http::HttpResponse>& response);
    
    // XML parsing using AWS SDK
    CloudResult parseS3ListResponseWithAwsXml(const std::string& xmlContent,
                                             std::vector<std::string>& objectKeys);
    
    // Utility methods
    std::string buildS3Endpoint(const std::string& bucket) const;
    Aws::String convertToAwsString(const std::string& str) const;
    std::string convertFromAwsString(const Aws::String& awsStr) const;
    std::string createDeleteMultipleObjectsXml(const std::vector<std::string>& objectKeys) const;
    
    // Helper methods
    std::string getRequiredConfigParameter(const std::string& key) const;
    CloudResult handleS3Error(const std::string& error, const std::string& operation) const;
    CloudResult handleAwsHttpError(const std::shared_ptr<Aws::Http::HttpResponse>& response);
    
    // Member variables
    std::string m_endpoint;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_region;
    bool m_use_ssl;
    uint32_t m_timeout_seconds;
    uint32_t m_max_retries;
    mutable std::mutex m_client_mutex;
    
    // AWS SDK components
    bool m_awsSdkInitialized;
    std::shared_ptr<Aws::Auth::AWSCredentialsProvider> m_credentialsProvider;
    std::shared_ptr<Aws::Client::AWSAuthV4Signer> m_signer;
    std::shared_ptr<Aws::Http::HttpClient> m_httpClient;
    std::shared_ptr<Aws::Client::ClientConfiguration> m_clientConfig;
    
    // HTTP status codes
    static const int HTTP_OK = 200;
    static const int HTTP_NO_CONTENT = 204;
    static const int HTTP_NOT_FOUND = 404;
    static const int HTTP_FORBIDDEN = 403;
};

} // namespace nv_vms 
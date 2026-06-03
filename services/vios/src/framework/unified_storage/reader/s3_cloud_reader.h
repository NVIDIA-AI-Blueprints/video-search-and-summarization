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
#include <map>
#include <chrono>

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
#include <aws/core/utils/HashingUtils.h>
#include <aws/core/utils/StringUtils.h>

namespace nv_vms {

/**
 * @brief AWS S3 implementation of CloudReader using AWS C++ SDK
 *
 * Features:
 * - AWS C++ SDK integration for authentication and signing
 * - Built-in HTTP client from AWS SDK
 * - Native XML parsing capabilities
 * - Comprehensive error handling and retry logic
 * - Performance monitoring and statistics
 */
class S3CloudReader : public CloudReader {
public:
    S3CloudReader();
    virtual ~S3CloudReader();
    
    // Delete copy and move operations to prevent unsafe AWS SDK object lifecycle issues
    S3CloudReader(const S3CloudReader&) = delete;
    S3CloudReader& operator=(const S3CloudReader&) = delete;
    S3CloudReader(S3CloudReader&&) = delete;
    S3CloudReader& operator=(S3CloudReader&&) = delete;

    // Core interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override { return "Amazon S3"; }
    CloudStorageType getStorageType() const override { return CloudStorageType::AWS_S3; }

    // Configuration
    bool configure(const CloudReaderConfig& config) override;
    CloudReaderConfig getConfiguration() const override;

    // Object listing operations
    CloudListResult listObjects(const std::string& bucket,
                               const std::string& prefix = "",
                               uint32_t maxKeys = 1000) override;

    CloudListResult listAllObjects(const std::string& bucket,
                                   const std::string& prefix = "") override;

    CloudListResult listObjectsPaginated(const std::string& bucket,
                                        const std::string& prefix = "",
                                        const std::string& marker = "",
                                        uint32_t maxKeys = 1000) override;

    // Object operations
    CloudResult downloadObject(const std::string& bucket,
                              const std::string& objectKey,
                              const std::string& localPath) override;

    CloudResult getObjectInfo(const std::string& bucket,
                             const std::string& objectKey,
                             CloudObject& objectInfo) override;

    CloudResult checkObjectExists(const std::string& bucket,
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
    // AWS SDK initialization and cleanup
    bool initializeAwsSDK();
    void shutdownAwsSDK();

    // HTTP request operations using AWS SDK
    CloudResult makeS3RequestWithParams(const std::string& method,
                                       const std::string& bucket,
                                       const std::string& objectKey,
                                       const std::map<std::string, std::string, std::less<>>& queryParamsMap,
                                       std::shared_ptr<Aws::Http::HttpResponse>& response);

    CloudResult makeS3Request(const std::string& method,
                             const std::string& bucket,
                             const std::string& objectKey,
                             const std::string& queryParams,
                             std::shared_ptr<Aws::Http::HttpResponse>& response);

    CloudResult downloadToFileWithAwsClient(const std::string& bucket,
                                           const std::string& objectKey,
                                           const std::string& localPath);

    // XML parsing using AWS SDK
    CloudResult parseS3ListResponseWithAwsXml(const std::string& xmlContent,
                                             CloudListResult& result);

    CloudResult parseS3ObjectInfoWithAwsXml(const std::string& xmlContent,
                                           CloudObject& objectInfo);

    // Utility methods
    std::string buildS3Endpoint(const std::string& bucket) const;
    Aws::String convertToAwsString(const std::string& str) const;
    std::string convertFromAwsString(const Aws::String& awsStr) const;

    // Error handling
    CloudResult handleAwsHttpError(const std::shared_ptr<Aws::Http::HttpResponse>& response);
    std::string getAwsErrorMessage(const std::shared_ptr<Aws::Http::HttpResponse>& response);

    // Configuration validation
    bool validateConfig() const;
    std::string getRequiredConfigParameter(const std::string& key) const;

    // Internal state
    bool m_initialized = false;
    bool m_awsSdkInitialized = false;
    std::string m_endpoint;
    mutable std::mutex m_config_mutex;

    // AWS SDK components
    std::shared_ptr<Aws::Auth::AWSCredentialsProvider> m_credentialsProvider;
    std::shared_ptr<Aws::Client::AWSAuthV4Signer> m_signer;
    std::shared_ptr<Aws::Http::HttpClient> m_httpClient;
    std::shared_ptr<Aws::Client::ClientConfiguration> m_clientConfig;

    // Default S3 endpoints by region
    static const std::map<std::string, std::string, std::less<>> DEFAULT_ENDPOINTS;

    // HTTP status codes
    static const int HTTP_OK = 200;
    static const int HTTP_NOT_FOUND = 404;
    static const int HTTP_FORBIDDEN = 403;
    static const int HTTP_TOO_MANY_REQUESTS = 429;
    static const int HTTP_INTERNAL_ERROR = 500;
    static const int HTTP_BAD_GATEWAY = 502;
    static const int HTTP_SERVICE_UNAVAILABLE = 503;
    static const int HTTP_GATEWAY_TIMEOUT = 504;
};

} // namespace nv_vms
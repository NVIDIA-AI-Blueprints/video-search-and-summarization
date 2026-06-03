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

#include <aws/core/Aws.h>
#include <aws/core/auth/AWSCredentialsProvider.h>
#include <aws/core/client/ClientConfiguration.h>
#include <aws/core/http/HttpClient.h>
#include <aws/core/auth/signer/AWSAuthV4Signer.h>
#include <memory>
#include <mutex>
#include <string>

namespace nv_vms
{

/**
 * @brief Thread-safe singleton manager for AWS SDK initialization and component creation
 * 
 * This class ensures AWS SDK is initialized exactly once across all modules using it,
 * with proper reference counting for shutdown. It also provides thread-safe factory
 * methods for creating AWS SDK components.
 */
class AwsSdkManager
{
public:
    /**
     * @brief Get singleton instance
     */
    static AwsSdkManager& getInstance();
    
    /**
     * @brief Initialize AWS SDK (reference counted)
     * @return true if successfully initialized or already initialized
     */
    bool initializeAwsSDK();
    
    /**
     * @brief Shutdown AWS SDK (decrements reference count, shuts down when count reaches 0)
     */
    void shutdownAwsSDK();
    
    /**
     * @brief Check if AWS SDK is initialized
     */
    bool isInitialized() const;
    
    /**
     * @brief Create AWS credentials provider
     * @param accessKeyId AWS access key ID
     * @param secretAccessKey AWS secret access key
     * @return Shared pointer to credentials provider
     */
    std::shared_ptr<Aws::Auth::AWSCredentialsProvider> createCredentialsProvider(
        const std::string& accessKeyId,
        const std::string& secretAccessKey);
    
    /**
     * @brief Create AWS client configuration
     * @param region AWS region
     * @param useSsl Whether to use SSL/TLS
     * @param timeoutSeconds Request timeout in seconds
     * @param endpoint Custom endpoint URL (for S3-compatible services)
     * @return Shared pointer to client configuration
     */
    std::shared_ptr<Aws::Client::ClientConfiguration> createClientConfiguration(
        const std::string& region,
        bool useSsl,
        uint32_t timeoutSeconds,
        const std::string& endpoint = "");
    
    /**
     * @brief Create HTTP client
     * @param config Client configuration
     * @return Shared pointer to HTTP client
     */
    std::shared_ptr<Aws::Http::HttpClient> createHttpClient(
        const std::shared_ptr<Aws::Client::ClientConfiguration>& config);
    
    /**
     * @brief Create AWS V4 signer
     * @param credentialsProvider Credentials provider
     * @param serviceName AWS service name (e.g., "s3")
     * @param region AWS region
     * @param payloadSigningPolicy Payload signing policy
     * @return Shared pointer to V4 signer
     */
    std::shared_ptr<Aws::Client::AWSAuthV4Signer> createV4Signer(
        const std::shared_ptr<Aws::Auth::AWSCredentialsProvider>& credentialsProvider,
        const std::string& serviceName,
        const std::string& region,
        Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy payloadSigningPolicy);

    // Delete copy/move constructors and assignment operators
    AwsSdkManager(const AwsSdkManager&) = delete;
    AwsSdkManager& operator=(const AwsSdkManager&) = delete;
    AwsSdkManager(AwsSdkManager&&) = delete;
    AwsSdkManager& operator=(AwsSdkManager&&) = delete;

private:
    AwsSdkManager();
    ~AwsSdkManager();
    
    mutable std::mutex m_initMutex;
    bool m_initialized = false;
    int m_refCount = 0;
    Aws::SDKOptions m_sdkOptions;  // Shared options for InitAPI/ShutdownAPI consistency
    static std::once_flag m_setenvOnceFlag;  // For thread-safe setenv
};

} // namespace nv_vms


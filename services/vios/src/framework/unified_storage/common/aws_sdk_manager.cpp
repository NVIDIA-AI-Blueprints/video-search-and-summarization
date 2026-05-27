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

#include "aws_sdk_manager.h"
#include <aws/core/auth/AWSCredentialsProvider.h>
#include <aws/core/http/HttpClientFactory.h>
#include "logger.h"
#include <cstdlib>

namespace nv_vms
{

// Static member initialization
std::once_flag AwsSdkManager::m_setenvOnceFlag;

AwsSdkManager::AwsSdkManager()
{
    // Configure AWS SDK options - same instance used for both InitAPI and ShutdownAPI
    m_sdkOptions.loggingOptions.logLevel = Aws::Utils::Logging::LogLevel::Info;
    m_sdkOptions.httpOptions.installSigPipeHandler = true;
}

AwsSdkManager& AwsSdkManager::getInstance()
{
    static AwsSdkManager instance;
    return instance;
}

bool AwsSdkManager::initializeAwsSDK()
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    if (m_initialized)
    {
        m_refCount++;
        return true;
    }
    
    try
    {
        // Thread-safe setenv - only call once globally
        // AWS SDK metadata service defaults can cause hangs on non-EC2 environments
        std::call_once(m_setenvOnceFlag, []() {
            setenv("AWS_EC2_METADATA_DISABLED", "true", 1);
        });
        
        // Initialize AWS SDK with member options (same instance used for shutdown)
        Aws::InitAPI(m_sdkOptions);
        
        m_initialized = true;
        m_refCount = 1;
        LOG(info) << "AWS SDK initialized successfully (ref count: " << m_refCount << ")";
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to initialize AWS SDK: " << e.what();
        return false;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during AWS SDK initialization";
        return false;
    }
}

void AwsSdkManager::shutdownAwsSDK()
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    if (!m_initialized)
    {
        return;
    }
    
    m_refCount--;
    LOG(info) << "AWS SDK shutdown requested (ref count: " << m_refCount << ")";
    
    if (m_refCount <= 0)
    {
        try
        {
            // Use same options instance that was used for InitAPI
            Aws::ShutdownAPI(m_sdkOptions);
            m_initialized = false;
            m_refCount = 0;
            LOG(info) << "AWS SDK fully shut down";
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Error during AWS SDK shutdown: " << e.what();
        }
        catch (...)
        {
            LOG(error) << "Unknown error during AWS SDK shutdown";
        }
    }
}

bool AwsSdkManager::isInitialized() const
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    return m_initialized;
}

std::shared_ptr<Aws::Auth::AWSCredentialsProvider> AwsSdkManager::createCredentialsProvider(
    const std::string& accessKeyId,
    const std::string& secretAccessKey)
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    try
    {
        auto provider = std::make_shared<Aws::Auth::SimpleAWSCredentialsProvider>(
            Aws::String(accessKeyId.c_str(), accessKeyId.length()),
            Aws::String(secretAccessKey.c_str(), secretAccessKey.length()));
        return provider;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to create credentials provider: " << e.what();
        return nullptr;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception in createCredentialsProvider";
        return nullptr;
    }
}

std::shared_ptr<Aws::Client::ClientConfiguration> AwsSdkManager::createClientConfiguration(
    const std::string& region,
    bool useSsl,
    uint32_t timeoutSeconds,
    const std::string& endpoint)
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    try
    {
        auto config = std::make_shared<Aws::Client::ClientConfiguration>();
        config->region = Aws::String(region.c_str(), region.length());
        config->scheme = useSsl ? Aws::Http::Scheme::HTTPS : Aws::Http::Scheme::HTTP;
        config->connectTimeoutMs = 10000;
        config->requestTimeoutMs = timeoutSeconds * 1000;
        
        // Disable features that might trigger EC2 metadata service calls
        config->enableEndpointDiscovery = false;
        config->useDualStack = false;
        config->maxConnections = 25;
        
        // Set endpoint override for MinIO/custom S3 compatibility
        // When endpointOverride is set, AWS SDK automatically uses path-style addressing
        if (!endpoint.empty())
        {
            // Extract host from endpoint (remove protocol if present)
            std::string host = endpoint;
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
            config->endpointOverride = Aws::String(host.c_str(), host.length());
        }
        
        return config;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to create client configuration: " << e.what();
        return nullptr;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception in createClientConfiguration";
        return nullptr;
    }
}

std::shared_ptr<Aws::Http::HttpClient> AwsSdkManager::createHttpClient(
    const std::shared_ptr<Aws::Client::ClientConfiguration>& config)
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    if (!config)
    {
        LOG(error) << "Cannot create HTTP client: config is null";
        return nullptr;
    }
    
    try
    {
        auto client = Aws::Http::CreateHttpClient(*config);
        return client;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to create HTTP client: " << e.what();
        return nullptr;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception in createHttpClient";
        return nullptr;
    }
}

std::shared_ptr<Aws::Client::AWSAuthV4Signer> AwsSdkManager::createV4Signer(
    const std::shared_ptr<Aws::Auth::AWSCredentialsProvider>& credentialsProvider,
    const std::string& serviceName,
    const std::string& region,
    Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy payloadSigningPolicy)
{
    std::lock_guard<std::mutex> lock(m_initMutex);
    
    if (!credentialsProvider)
    {
        LOG(error) << "Cannot create V4 signer: credentials provider is null";
        return nullptr;
    }
    
    try
    {
        auto signer = std::make_shared<Aws::Client::AWSAuthV4Signer>(
            credentialsProvider,
            serviceName.c_str(),
            Aws::String(region.c_str(), region.length()),
            payloadSigningPolicy);
        return signer;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to create V4 signer: " << e.what();
        return nullptr;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception in createV4Signer";
        return nullptr;
    }
}

AwsSdkManager::~AwsSdkManager()
{
    if (m_initialized)
    {
        try
        {
            LOG(info) << "AwsSdkManager destructor called, shutting down AWS SDK";
            // Use same options instance that was used for InitAPI
            Aws::ShutdownAPI(m_sdkOptions);
        }
        catch (...)
        {
            // Ignore shutdown errors during destruction
        }
    }
}

} // namespace nv_vms


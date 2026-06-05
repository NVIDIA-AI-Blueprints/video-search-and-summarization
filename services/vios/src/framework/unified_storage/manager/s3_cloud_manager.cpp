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

#include "s3_cloud_manager.h"
#include "common/aws_sdk_manager.h"
#include "logger.h"
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <sstream>
#include <iomanip>

// AWS SDK includes
#include <aws/core/Aws.h>
#include <aws/core/http/URI.h>
#include <aws/core/platform/Environment.h>
#include <aws/core/utils/Outcome.h>
#include <aws/core/utils/stream/ResponseStream.h>

namespace nv_vms {

S3CloudManager::S3CloudManager()
    : m_use_ssl(true)
    , m_timeout_seconds(30)
    , m_max_retries(3)
    , m_awsSdkInitialized(false)
{
}

S3CloudManager::~S3CloudManager()
{
    shutdownS3Client();
}

bool S3CloudManager::isAvailable() const
{
    return m_initialized;
}

bool S3CloudManager::configure(const CloudManagerConfig& config)
{
    // Call base class configure method
    if (!CloudManager::configure(config))
    {
        return false;
    }
    
    // Validate required configuration parameters
    if (config.endpoint.empty())
    {
        m_last_error = "Endpoint is required but not provided";
        return false;
    }
    
    if (config.accessKeyId.empty())
    {
        m_last_error = "Access key ID is required but not provided";
        return false;
    }
    
    if (config.secretAccessKey.empty())
    {
        m_last_error = "Secret access key is required but not provided";
        return false;
    }
    
    // Store configuration
    m_config = config;
    m_endpoint = config.endpoint;
    m_access_key = config.accessKeyId;
    m_secret_key = config.secretAccessKey;
    m_region = config.region;
    m_use_ssl = config.useSSL;
    m_timeout_seconds = config.timeoutSeconds;
    m_max_retries = config.maxRetries;
    
    // Attempt to initialize the S3 client
    if (!initializeS3Client())
    {
        m_last_error = "Failed to initialize S3 client";
        m_initialized = false;
        return false;
    }
    
    // Only set initialized to true if client initialization succeeds
    m_initialized = true;
    return true;
}

CloudManagerConfig S3CloudManager::getConfiguration() const
{
    return m_config;
}

CloudResult S3CloudManager::deleteObject(const std::string& bucket, const std::string& objectKey)
{
    if (!m_initialized)
    {
        CloudResult result(false);
        result.message = "S3 cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        return result;
    }
    
    return deleteObjectWithS3Client(bucket, objectKey);
}

CloudResult S3CloudManager::deleteMultipleObjects(const std::string& bucket, const std::vector<std::string>& objectKeys)
{
    if (!m_initialized)
    {
        CloudResult result(false);
        result.message = "S3 cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        return result;
    }
    
    return deleteMultipleObjectsWithS3Client(bucket, objectKeys);
}

CloudResult S3CloudManager::deleteObjectsWithPrefix(const std::string& bucket, const std::string& prefix)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::listObjects(const std::string& bucket, const std::string& prefix, std::vector<std::string>& objectKeys, uint32_t maxKeys)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::checkObjectExists(const std::string& bucket, const std::string& objectKey)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::getObjectInfo(const std::string& bucket, const std::string& objectKey, CloudObject& objectInfo)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::createBucket(const std::string& bucketName, const std::string& region)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::deleteBucket(const std::string& bucketName, bool force)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::checkBucketExists(const std::string& bucketName)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::listBuckets(std::vector<std::string>& buckets)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::getBucketInfo(const std::string& bucketName, BucketInfo& bucketInfo)
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

CloudResult S3CloudManager::performHealthCheck()
{
    CloudResult result;
    result.success = false;
    result.message = "S3 cloud manager not yet implemented";
    return result;
}

std::string S3CloudManager::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    return m_last_error;
}

CloudManagerStats S3CloudManager::getStats() const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    return m_stats;
}

void S3CloudManager::resetStats()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_stats.reset();
}

bool S3CloudManager::validateBucketName(const std::string& bucketName) const
{
    return !bucketName.empty() && bucketName.length() >= 3 && bucketName.length() <= 63;
}

bool S3CloudManager::validateObjectKey(const std::string& objectKey) const
{
    return !objectKey.empty();
}

std::string S3CloudManager::sanitizeObjectName(const std::string& objectName) const
{
    return objectName;
}

// Private methods - AWS SDK implementation
bool S3CloudManager::initializeS3Client()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    
    // Check if already initialized (idempotent)
    if (m_httpClient && m_signer && m_credentialsProvider)
    {
        LOG(info) << "S3 client already initialized, skipping re-initialization";
        return true;
    }
    
    try
    {
        // Initialize AWS SDK using the singleton manager
        AwsSdkManager& sdkManager = AwsSdkManager::getInstance();
        
        if (!sdkManager.initializeAwsSDK())
        {
            m_last_error = "Failed to initialize AWS SDK";
            LOG(error) << m_last_error;
            return false;
        }
        m_awsSdkInitialized = true;
        
        // Create credentials provider
        m_credentialsProvider = sdkManager.createCredentialsProvider(m_access_key, m_secret_key);
        if (!m_credentialsProvider)
        {
            m_last_error = "Failed to create AWS credentials provider";
            LOG(error) << m_last_error;
            return false;
        }
        
        // Create V4 signer for request signing
        m_signer = sdkManager.createV4Signer(
            m_credentialsProvider,
            "s3",
            m_region,
            Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy::RequestDependent);
        if (!m_signer)
        {
            m_last_error = "Failed to create AWS V4 signer";
            LOG(error) << m_last_error;
            return false;
        }
        
        // Create client configuration
        // Pass endpoint for MinIO/custom S3 compatibility
        m_clientConfig = sdkManager.createClientConfiguration(m_region, m_use_ssl, m_timeout_seconds, m_endpoint);
        if (!m_clientConfig)
        {
            m_last_error = "Failed to create AWS client configuration";
            LOG(error) << m_last_error;
            return false;
        }
        
        // Create HTTP client
        m_httpClient = sdkManager.createHttpClient(m_clientConfig);
        if (!m_httpClient)
        {
            m_last_error = "Failed to create AWS HTTP client";
            LOG(error) << m_last_error;
            return false;
        }
        
        LOG(info) << "Successfully initialized S3 client for AWS S3 cloud manager - endpoint: " << m_endpoint 
                  << ", region: " << m_region;
        return true;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Exception during S3 client initialization: " + std::string(e.what());
        LOG(error) << m_last_error;
        return false;
    }
}

void S3CloudManager::shutdownS3Client()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    
    try
    {
        m_httpClient.reset();
        m_clientConfig.reset();
        m_signer.reset();
        m_credentialsProvider.reset();
        
        if (m_awsSdkInitialized)
        {
            AwsSdkManager::getInstance().shutdownAwsSDK();
            m_awsSdkInitialized = false;
        }
    }
    catch (...)
    {
        // Ignore errors during shutdown
    }
}

bool S3CloudManager::ensureClientInitialized()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    
    if (!m_httpClient || !m_signer || !m_credentialsProvider)
    {
        m_last_error = "S3 client not initialized";
        return false;
    }
    
    return true;
}

CloudResult S3CloudManager::deleteObjectWithS3Client(const std::string& bucket, const std::string& objectKey)
{
    CloudResult result(false);
    
    if (!ensureClientInitialized())
    {
        result.message = m_last_error;
        result.errorCode = "CLIENT_NOT_INITIALIZED";
        return result;
    }
    
    try
    {
        auto startTime = std::chrono::steady_clock::now();
        
        // Make DELETE request to S3
        std::shared_ptr<Aws::Http::HttpResponse> response;
        result = makeS3DeleteRequest(bucket, objectKey, response);
        
        auto endTime = std::chrono::steady_clock::now();
        auto latency = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
        
        // Update stats
        {
            std::lock_guard<std::mutex> lock(m_client_mutex);
            m_stats.recordRequest(result.success, latency, result.errorCode);
            if (result.success)
            {
                m_stats.objectsDeleted++;
            }
        }
        
        return result;
    }
    catch (const std::exception& e)
    {
        result.message = "Exception in deleteObject: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
        LOG(error) << result.message;
        return result;
    }
}

CloudResult S3CloudManager::deleteMultipleObjectsWithS3Client(const std::string& bucket, const std::vector<std::string>& objectKeys)
{
    CloudResult result(false);
    
    if (!ensureClientInitialized())
    {
        result.message = m_last_error;
        result.errorCode = "CLIENT_NOT_INITIALIZED";
        return result;
    }
    
    if (objectKeys.empty())
    {
        result.success = true;
        result.message = "No objects to delete";
        return result;
    }
    
    try
    {
        auto startTime = std::chrono::steady_clock::now();
        
        // Make DELETE request to S3 with multiple objects
        std::shared_ptr<Aws::Http::HttpResponse> response;
        result = makeS3DeleteMultipleRequest(bucket, objectKeys, response);
        
        auto endTime = std::chrono::steady_clock::now();
        auto latency = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
        
        // Update stats
        {
            std::lock_guard<std::mutex> lock(m_client_mutex);
            m_stats.recordRequest(result.success, latency, result.errorCode);
            if (result.success)
            {
                m_stats.objectsDeleted += objectKeys.size();
            }
        }
        
        return result;
    }
    catch (const std::exception& e)
    {
        result.message = "Exception in deleteMultipleObjects: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
        LOG(error) << result.message;
        return result;
    }
}

CloudResult S3CloudManager::createBucketWithS3Client(const std::string& bucketName, const std::string& region)
{
    CloudResult result;
    result.success = false;
    result.message = "Not implemented";
    return result;
}

CloudResult S3CloudManager::deleteBucketWithS3Client(const std::string& bucketName)
{
    CloudResult result;
    result.success = false;
    result.message = "Not implemented";
    return result;
}

CloudResult S3CloudManager::checkBucketExistsWithS3Client(const std::string& bucketName)
{
    CloudResult result;
    result.success = false;
    result.message = "Not implemented";
    return result;
}

CloudResult S3CloudManager::listBucketsWithS3Client(std::vector<std::string>& buckets)
{
    CloudResult result;
    result.success = false;
    result.message = "Not implemented";
    return result;
}

CloudResult S3CloudManager::getBucketInfoWithS3Client(const std::string& bucketName, BucketInfo& bucketInfo)
{
    CloudResult result;
    result.success = false;
    result.message = "Not implemented";
    return result;
}

std::string S3CloudManager::getRequiredConfigParameter(const std::string& key) const
{
    // This method can be used to retrieve additional configuration parameters if needed
    // For now, the main configuration is stored in member variables
    return "";
}

CloudResult S3CloudManager::handleS3Error(const std::string& error, const std::string& operation) const
{
    CloudResult result(false);
    result.message = error;
    result.errorCode = operation;
    return result;
}

CloudResult S3CloudManager::handleAwsHttpError(const std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);
    
    if (!response)
    {
        result.message = "No response from AWS S3";
        result.errorCode = "NO_RESPONSE";
        return result;
    }
    
    int statusCode = static_cast<int>(response->GetResponseCode());
    std::string errorMessage = "HTTP " + std::to_string(statusCode);
    
    // Try to read error response body
    try
    {
        auto& responseBody = response->GetResponseBody();
        std::stringstream ss;
        ss << responseBody.rdbuf();
        std::string body = ss.str();
        
        if (!body.empty())
        {
            errorMessage += ": " + body;
        }
    }
    catch (...)
    {
        // Ignore errors reading response body
    }
    
    result.message = errorMessage;
    result.errorCode = "HTTP_" + std::to_string(statusCode);
    
    LOG(error) << "AWS S3 HTTP error: " << errorMessage;
    
    return result;
}

CloudResult S3CloudManager::makeS3Request(const std::string& method,
                                         const std::string& bucket,
                                         const std::string& objectKey,
                                         const std::string& queryParams,
                                         std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);
    
    try
    {
        // Build S3 endpoint - use custom endpoint if provided (for MinIO compatibility)
        std::string host;
        if (!m_endpoint.empty())
        {
            // Extract host from custom endpoint
            host = m_endpoint;
            // Remove protocol if present
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
        }
        else
        {
            // Default to AWS S3 endpoint
            host = "s3." + m_region + ".amazonaws.com";
        }
        Aws::Http::Scheme awsScheme = m_use_ssl ? Aws::Http::Scheme::HTTPS : Aws::Http::Scheme::HTTP;
        
        // Build path
        std::string path = "/" + bucket;
        if (!objectKey.empty())
        {
            path += "/" + objectKey;
        }
        
        // Create URI
        Aws::Http::URI uri;
        uri.SetScheme(awsScheme);
        uri.SetAuthority(convertToAwsString(host));
        uri.SetPath(convertToAwsString(path));
        
        if (!queryParams.empty())
        {
            uri.SetQueryString(convertToAwsString(queryParams));
        }
        
        // Determine HTTP method
        Aws::Http::HttpMethod httpMethod = Aws::Http::HttpMethod::HTTP_GET;
        if (method == "DELETE")
        {
            httpMethod = Aws::Http::HttpMethod::HTTP_DELETE;
        }
        else if (method == "POST")
        {
            httpMethod = Aws::Http::HttpMethod::HTTP_POST;
        }
        else if (method == "PUT")
        {
            httpMethod = Aws::Http::HttpMethod::HTTP_PUT;
        }
        else if (method == "HEAD")
        {
            httpMethod = Aws::Http::HttpMethod::HTTP_HEAD;
        }
        
        // Create HTTP request
        auto request = Aws::Http::CreateHttpRequest(
            uri, httpMethod, Aws::Utils::Stream::DefaultResponseStreamFactoryMethod);
        
        if (!request)
        {
            result.message = "Failed to create HTTP request";
            result.errorCode = "REQUEST_ERROR";
            return result;
        }
        
        // Sign the request
        if (!m_signer->SignRequest(*request))
        {
            result.message = "Failed to sign AWS request";
            result.errorCode = "SIGN_ERROR";
            return result;
        }
        
        // Make the HTTP request
        response = m_httpClient->MakeRequest(request);
        if (!response)
        {
            result.message = "HTTP request failed - no response";
            result.errorCode = "HTTP_ERROR";
            return result;
        }
        
        // Check response code
        int responseCode = static_cast<int>(response->GetResponseCode());
        if (responseCode != S3CloudManager::HTTP_OK && responseCode != S3CloudManager::HTTP_NO_CONTENT)
        {
            return handleAwsHttpError(response);
        }
        
        result.success = true;
        result.message = "S3 request successful";
    }
    catch (const std::exception& e)
    {
        result.message = "Exception making S3 request: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
        LOG(error) << result.message;
    }
    
    return result;
}

CloudResult S3CloudManager::makeS3DeleteRequest(const std::string& bucket,
                                               const std::string& objectKey,
                                               std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    return makeS3Request("DELETE", bucket, objectKey, "", response);
}

CloudResult S3CloudManager::makeS3DeleteMultipleRequest(const std::string& bucket,
                                                       const std::vector<std::string>& objectKeys,
                                                       std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);
    
    try
    {
        // Build S3 endpoint - use custom endpoint if provided (for MinIO compatibility)
        std::string host;
        if (!m_endpoint.empty())
        {
            // Extract host from custom endpoint (e.g., "http://10.24.218.240:9000")
            host = m_endpoint;
            // Remove protocol if present
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
        }
        else
        {
            // Default to AWS S3 endpoint
            host = "s3." + m_region + ".amazonaws.com";
        }
        Aws::Http::Scheme awsScheme = m_use_ssl ? Aws::Http::Scheme::HTTPS : Aws::Http::Scheme::HTTP;
        
        // Build path with delete query parameter
        std::string path = "/" + bucket;
        
        // Create URI
        Aws::Http::URI uri;
        uri.SetScheme(awsScheme);
        uri.SetAuthority(convertToAwsString(host));
        uri.SetPath(convertToAwsString(path));
        uri.SetQueryString(convertToAwsString("delete"));
        
        // Create HTTP POST request (DELETE uses POST with ?delete)
        auto request = Aws::Http::CreateHttpRequest(
            uri, Aws::Http::HttpMethod::HTTP_POST, Aws::Utils::Stream::DefaultResponseStreamFactoryMethod);
        
        if (!request)
        {
            result.message = "Failed to create HTTP request";
            result.errorCode = "REQUEST_ERROR";
            return result;
        }
        
        // Create XML body for multiple delete
        std::string xmlBody = createDeleteMultipleObjectsXml(objectKeys);
        
        // Set content type and body
        request->SetHeaderValue("Content-Type", "application/xml");
        auto bodyStream = std::make_shared<std::stringstream>(xmlBody);
        request->AddContentBody(bodyStream);
        
        // Sign the request
        if (!m_signer->SignRequest(*request))
        {
            result.message = "Failed to sign AWS request";
            result.errorCode = "SIGN_ERROR";
            return result;
        }
        
        // Make the HTTP request
        response = m_httpClient->MakeRequest(request);
        if (!response)
        {
            result.message = "HTTP request failed - no response";
            result.errorCode = "HTTP_ERROR";
            return result;
        }
        
        // Check response code
        int responseCode = static_cast<int>(response->GetResponseCode());
        if (responseCode != S3CloudManager::HTTP_OK && responseCode != S3CloudManager::HTTP_NO_CONTENT)  // 200 OK, 204 No Content
        {
            return handleAwsHttpError(response);
        }
        
        result.success = true;
        result.message = "S3 multiple delete successful";
    }
    catch (const std::exception& e)
    {
        result.message = "Exception making S3 delete multiple request: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
        LOG(error) << result.message;
    }
    
    return result;
}

std::string S3CloudManager::buildS3Endpoint(const std::string& bucket) const
{
    std::string scheme = m_use_ssl ? "https" : "http";
    // Use custom endpoint if provided (for MinIO compatibility)
    if (!m_endpoint.empty())
    {
        // Extract host from endpoint if it has protocol
        std::string host = m_endpoint;
        if (host.find("://") != std::string::npos)
        {
            host = host.substr(host.find("://") + 3);
        }
        return scheme + "://" + host + "/" + bucket;
    }
    // Default to AWS S3 endpoint
    return scheme + "://s3." + m_region + ".amazonaws.com/" + bucket;
}

Aws::String S3CloudManager::convertToAwsString(const std::string& str) const
{
    return Aws::String(str.c_str(), str.length());
}

std::string S3CloudManager::convertFromAwsString(const Aws::String& awsStr) const
{
    return std::string(awsStr.c_str(), awsStr.length());
}

// Helper function to escape XML special characters
static std::string escapeXml(const std::string& str)
{
    std::string escaped;
    escaped.reserve(str.size());
    for (char c : str)
    {
        switch (c)
        {
            case '&':  escaped += "&amp;";  break;
            case '<':  escaped += "&lt;";   break;
            case '>':  escaped += "&gt;";   break;
            case '"':  escaped += "&quot;"; break;
            case '\'': escaped += "&apos;"; break;
            default:   escaped += c;        break;
        }
    }
    return escaped;
}

std::string S3CloudManager::createDeleteMultipleObjectsXml(const std::vector<std::string>& objectKeys) const
{
    std::ostringstream xml;
    xml << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>";
    xml << "<Delete>";
    xml << "<Quiet>true</Quiet>";
    
    for (const auto& key : objectKeys)
    {
        xml << "<Object>";
        xml << "<Key>" << escapeXml(key) << "</Key>";
        xml << "</Object>";
    }
    
    xml << "</Delete>";
    return xml.str();
}

CloudResult S3CloudManager::parseS3ListResponseWithAwsXml(const std::string& xmlContent,
                                                         std::vector<std::string>& objectKeys)
{
    CloudResult result(false);
    result.message = "XML parsing not yet implemented";
    result.errorCode = "NOT_IMPLEMENTED";
    return result;
}

} // namespace nv_vms 
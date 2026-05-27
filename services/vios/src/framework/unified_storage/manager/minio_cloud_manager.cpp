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

#include "minio_cloud_manager.h"
#include "logger.h"
#include <iostream>
#include <regex> // Added for bucket name validation
#include <algorithm> // Added for std::replace
#include <mutex> // Added for std::lock_guard and std::mutex
#include "cloud_manager.h"
#include "unified_storage_manager.h"

namespace nv_vms {

MinioCloudManager::MinioCloudManager()
    : m_use_ssl(true)
    , m_timeout_seconds(30)
    , m_max_retries(3)
{
}

MinioCloudManager::~MinioCloudManager()
{
}

bool MinioCloudManager::isAvailable() const
{
    return m_initialized;
}

bool MinioCloudManager::configure(const CloudManagerConfig& config)
{
    LOG(info) << "MinioCloudManager::configure called with endpoint: " << config.endpoint << std::endl;
    
    // Store the configuration for later access
    m_config = config;
    
    // Call base class configure method
    if (!CloudManager::configure(config))
    {
        LOG(error) << "MinioCloudManager::configure - Base class configure failed" << std::endl;
        return false;
    }
    
    m_endpoint = config.endpoint;
    m_access_key = config.accessKeyId;
    m_secret_key = config.secretAccessKey;
    m_region = config.region;
    m_use_ssl = config.useSSL;
    m_timeout_seconds = config.timeoutSeconds;
    m_max_retries = config.maxRetries;
    
    // Initialize the MinIO client
    m_initialized = initializeMinioClient();
    
    LOG(info) << "MinioCloudManager::configure - MinIO client initialization result: " << (m_initialized ? "SUCCESS" : "FAILED") << std::endl;
    
    return m_initialized;
}

CloudManagerConfig MinioCloudManager::getConfiguration() const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    return m_config;
}

CloudResult MinioCloudManager::deleteObject(const std::string& bucket, const std::string& objectKey)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;
    
    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    if (!validateBucketName(bucket) || !validateObjectKey(objectKey))
    {
        result.success = false;
        result.message = "Invalid bucket name or object key";
        result.errorCode = "INVALID_PARAMETERS";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    result = deleteObjectWithMinioClient(bucket, objectKey);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    
    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);
    
    return result;
}

CloudResult MinioCloudManager::deleteMultipleObjects(const std::string& bucket, const std::vector<std::string>& objectKeys)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucket))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucket;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    for (const auto& objectKey : objectKeys)
    {
        if (!validateObjectKey(objectKey))
        {
            result.success = false;
            result.message = "Invalid object key: " + objectKey;
            result.errorCode = "INVALID_OBJECT_KEY";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }
    }

    result = deleteMultipleObjectsWithMinioClient(bucket, objectKeys);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::deleteObjectsWithPrefix(const std::string& bucket, const std::string& prefix)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucket))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucket;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    // First, list objects with the prefix
    std::vector<std::string> objectKeys;
    CloudResult listResult = listObjects(bucket, prefix, objectKeys, 1000);
    
    if (!listResult.success)
    {
        result.success = false;
        result.message = "Failed to list objects with prefix: " + listResult.message;
        result.errorCode = listResult.errorCode;
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (objectKeys.empty())
    {
        result.success = true;
        result.message = "No objects found with prefix: " + prefix;
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    // Delete the objects
    result = deleteMultipleObjectsWithMinioClient(bucket, objectKeys);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::listObjects(const std::string& bucket, const std::string& prefix, std::vector<std::string>& objectKeys, uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucket))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucket;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }

        minio::s3::ListObjectsArgs args;
        args.bucket = bucket;
        if (!prefix.empty())
        {
            args.prefix = prefix;
        }
        args.max_keys = maxKeys;

        auto response = m_minio_client->ListObjects(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to list objects from MinIO";
            result.errorCode = "LIST_OBJECTS_ERROR";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }

        // Extract object keys from the response
        if (response)
        {
            // The response object itself can be used as an iterator
            while (response)
            {
                objectKeys.push_back((*response).name);
                ++response;
            }
        }

        result.success = true;
        result.message = "Objects listed successfully. Found " + std::to_string(objectKeys.size()) + " objects";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list objects: ") + e.what();
        result.errorCode = "EXCEPTION";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::createBucket(const std::string& bucketName, const std::string& region)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;
    
    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    if (!validateBucketName(bucketName))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucketName;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    result = createBucketWithMinioClient(bucketName, region);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    
    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);
    
    return result;
}

CloudResult MinioCloudManager::deleteBucket(const std::string& bucketName, bool force)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucketName))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucketName;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    // If force is true, delete all objects first
    if (force)
    {
        std::vector<std::string> objectKeys;
        CloudResult listResult = listObjects(bucketName, "", objectKeys, 1000);
        
        if (listResult.success && !objectKeys.empty())
        {
            CloudResult deleteResult = deleteMultipleObjectsWithMinioClient(bucketName, objectKeys);
            if (!deleteResult.success)
            {
                result.success = false;
                result.message = "Failed to delete objects in bucket: " + deleteResult.message;
                result.errorCode = deleteResult.errorCode;
                result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time);
                return result;
            }
        }
    }

    result = deleteBucketWithMinioClient(bucketName);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::checkBucketExists(const std::string& bucketName)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucketName))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucketName;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    result = checkBucketExistsWithMinioClient(bucketName);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::listBuckets(std::vector<std::string>& buckets)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    result = listBucketsWithMinioClient(buckets);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::getObjectInfo(const std::string& bucket, const std::string& objectKey, CloudObject& objectInfo)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;
    
    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    if (!validateBucketName(bucket) || !validateObjectKey(objectKey))
    {
        result.success = false;
        result.message = "Invalid bucket name or object key";
        result.errorCode = "INVALID_PARAMETERS";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    result = getObjectInfoWithMinioClient(bucket, objectKey, objectInfo);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    
    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);
    
    return result;
}

CloudResult MinioCloudManager::getBucketInfo(const std::string& bucketName, BucketInfo& bucketInfo)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    if (!validateBucketName(bucketName))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucketName;
        result.errorCode = "INVALID_BUCKET_NAME";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    result = getBucketInfoWithMinioClient(bucketName, bucketInfo);
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

CloudResult MinioCloudManager::performHealthCheck()
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }

        // Test connection by listing buckets
        auto response = m_minio_client->ListBuckets();
        if (!response)
        {
            result.success = false;
            result.message = "Health check failed: " + response.Error().String();
            result.errorCode = "HEALTH_CHECK_FAILED";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }

        result.success = true;
        result.message = "Health check passed - MinIO connection is healthy";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Health check exception: ") + e.what();
        result.errorCode = "EXCEPTION";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }

    // Update statistics
    updateStats(result.success, result.duration, result.errorCode);

    return result;
}

std::string MinioCloudManager::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    return m_last_error;
}

CloudManagerStats MinioCloudManager::getStats() const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    return m_stats;
}

void MinioCloudManager::resetStats()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_stats = CloudManagerStats();
}

bool MinioCloudManager::validateBucketName(const std::string& bucketName) const
{
    if (bucketName.empty() || bucketName.length() < 3 || bucketName.length() > 63)
    {
        return false;
    }
    
    // Check for valid characters (lowercase letters, numbers, hyphens, dots)
    std::regex bucket_pattern("^[a-z0-9][a-z0-9.-]*[a-z0-9]$");
    return std::regex_match(bucketName, bucket_pattern);
}

bool MinioCloudManager::validateObjectKey(const std::string& objectKey) const
{
    if (objectKey.empty())
    {
        return false;
    }
    
    // Check for invalid characters
    std::string invalid_chars = "<>:\"|?*";
    for (char c : invalid_chars)
    {
        if (objectKey.find(c) != std::string::npos)
        {
            return false;
        }
    }
    
    return true;
}

std::string MinioCloudManager::sanitizeObjectName(const std::string& objectName) const
{
    std::string sanitized = objectName;
    
    // Replace invalid characters with underscores
    std::string invalid_chars = "<>:\"|?*";
    for (char c : invalid_chars)
    {
        std::replace(sanitized.begin(), sanitized.end(), c, '_');
    }
    
    // Remove leading/trailing spaces and dots
    sanitized.erase(0, sanitized.find_first_not_of(" ."));
    sanitized.erase(sanitized.find_last_not_of(" .") + 1);
    
    return sanitized;
}

// Private methods - stub implementations
bool MinioCloudManager::initializeMinioClient()
{
    try
    {
        if (m_endpoint.empty() || m_access_key.empty() || m_secret_key.empty())
        {
            setLastError("Missing required configuration: endpoint, access_key, or secret_key");
            LOG(error) << "Missing required configuration - endpoint: " << (m_endpoint.empty() ? "EMPTY" : "SET") 
                       << ", access_key: " << (m_access_key.empty() ? "EMPTY" : "SET") 
                       << ", secret_key: " << (m_secret_key.empty() ? "EMPTY" : "SET") << std::endl;
            return false;
        }
        
        // Parse endpoint to extract host and port
        std::string host;
        int port = 9000; // Default MinIO port
        bool use_ssl = m_use_ssl;
        
        if (m_endpoint.find("://") != std::string::npos)
        {
            // Full URL with protocol
            size_t protocol_end = m_endpoint.find("://");
            std::string protocol = m_endpoint.substr(0, protocol_end);
            std::string rest = m_endpoint.substr(protocol_end + 3);
            
            use_ssl = (protocol == "https");
            
            size_t colon_pos = rest.find(':');
            if (colon_pos != std::string::npos)
            {
                host = rest.substr(0, colon_pos);
                port = std::stoi(rest.substr(colon_pos + 1));
            }
            else
            {
                host = rest;
            }
        }
        else
        {
            // Direct hostname without protocol
            host = m_endpoint;
        }
        
        // Create MinIO client using the correct constructor
        minio::s3::BaseUrl base_url;
        base_url.host = host;
        base_url.https = use_ssl;
        base_url.port = port;
        
        // Create credential provider
        m_credentials = std::make_unique<minio::creds::StaticProvider>(
            m_access_key, m_secret_key, "");
        
        // Create MinIO client
        m_minio_client = std::make_unique<minio::s3::Client>(base_url, m_credentials.get());
        
        LOG(info) << "MinIO client created, testing connection..." << std::endl;
        
        // Test connection by listing buckets
        auto response = m_minio_client->ListBuckets();
        if (!response)
        {
            std::string error_msg = "Failed to connect to MinIO server: " + response.Error().String();
            setLastError(error_msg);
            LOG(error) << error_msg << std::endl;
            return false;
        }
        
        LOG(info) << "MinIO client initialized successfully" << std::endl;
        return true;
    }
    catch (const std::exception& e)
    {
        std::string error_msg = std::string("Failed to initialize MinIO client: ") + e.what();
        setLastError(error_msg);
        LOG(error) << error_msg << std::endl;
        return false;
    }
}

void MinioCloudManager::shutdownMinioClient()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_minio_client.reset();
    m_credentials.reset();
    m_initialized = false;
}

bool MinioCloudManager::ensureClientInitialized()
{
    if (m_minio_client && m_initialized)
    {
        return true;
    }
    
    if (!m_initialized)
    {
        return initializeMinioClient();
    }
    
    return false;
}

CloudResult MinioCloudManager::deleteObjectWithMinioClient(const std::string& bucket, const std::string& objectKey)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        minio::s3::RemoveObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;
        
        auto response = m_minio_client->RemoveObject(args);
        if (!response)
        {
            result = handleMinioError(response.Error(), "delete object");
            return result;
        }
        
        result.success = true;
        result.message = "Object deleted successfully: " + objectKey;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during delete object: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::deleteMultipleObjectsWithMinioClient(const std::string& bucket, const std::vector<std::string>& objectKeys)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        if (objectKeys.empty())
        {
            result.success = true;
            result.message = "No objects to delete";
            return result;
        }
        
        // MinIO doesn't have a bulk delete operation like S3, so we'll delete objects one by one
        std::vector<std::string> failedObjects;
        std::vector<std::string> successfulObjects;
        
        for (const auto& objectKey : objectKeys)
        {
            minio::s3::RemoveObjectArgs args;
            args.bucket = bucket;
            args.object = objectKey;
            
            auto response = m_minio_client->RemoveObject(args);
            if (!response)
            {
                failedObjects.push_back(objectKey);
            }
            else
            {
                successfulObjects.push_back(objectKey);
            }
        }
        
        if (failedObjects.empty())
        {
            result.success = true;
            result.message = "All " + std::to_string(successfulObjects.size()) + " objects deleted successfully";
        }
        else
        {
            result.success = false;
            result.message = "Failed to delete " + std::to_string(failedObjects.size()) + " out of " + 
                           std::to_string(objectKeys.size()) + " objects";
            result.errorCode = "PARTIAL_DELETE_FAILED";
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during delete multiple objects: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::createBucketWithMinioClient(const std::string& bucketName, const std::string& region)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        minio::s3::MakeBucketArgs args;
        args.bucket = bucketName;
        
        auto response = m_minio_client->MakeBucket(args);
        if (!response)
        {
            result = handleMinioError(response.Error(), "create bucket");
            return result;
        }
        
        result.success = true;
        result.message = "Bucket created successfully: " + bucketName;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during create bucket: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::deleteBucketWithMinioClient(const std::string& bucketName)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        minio::s3::RemoveBucketArgs args;
        args.bucket = bucketName;
        
        auto response = m_minio_client->RemoveBucket(args);
        if (!response)
        {
            result = handleMinioError(response.Error(), "delete bucket");
            return result;
        }
        
        result.success = true;
        result.message = "Bucket deleted successfully: " + bucketName;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during delete bucket: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::checkBucketExistsWithMinioClient(const std::string& bucketName)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        // Use BucketExists to properly check if bucket exists
        minio::s3::BucketExistsArgs args;
        args.bucket = bucketName;
        
        auto response = m_minio_client->BucketExists(args);
        if (!response)
        {
            result = handleMinioError(response.Error(), "check bucket exists");
            return result;
        }
        
        if (response.exist)
        {
            result.success = true;
            result.message = "Bucket exists: " + bucketName;
        }
        else
        {
            result.success = false;
            result.message = "Bucket does not exist: " + bucketName;
            result.errorCode = "BUCKET_NOT_FOUND";
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during check bucket exists: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::listBucketsWithMinioClient(std::vector<std::string>& buckets)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        auto response = m_minio_client->ListBuckets();
        if (!response)
        {
            result = handleMinioError(response.Error(), "list buckets");
            return result;
        }
        
        buckets.clear();
        
        // Extract bucket names from the response
        for (const auto& bucket : response.buckets)
        {
            buckets.push_back(bucket.name);
        }
        
        result.success = true;
        result.message = "Listed " + std::to_string(buckets.size()) + " buckets successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list buckets: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::getObjectInfoWithMinioClient(const std::string& bucket, const std::string& objectKey, CloudObject& objectInfo)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        // Use MinIO statObject to get object information
        minio::s3::StatObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;
        
        auto response = m_minio_client->StatObject(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to get object info: " + response.Error().String();
            result.errorCode = "STAT_OBJECT_ERROR";
            return result;
        }
        
        // Populate object info
        objectInfo.key = objectKey;
        objectInfo.size = response.size;
        objectInfo.lastModified = response.last_modified.ToISO8601UTC();
        objectInfo.etag = response.etag;
        
        // Try to get content type from headers
        auto headers = response.headers;
        if (headers.Contains("content-type"))
        {
            auto content_types = headers.Get("content-type");
            if (!content_types.empty())
            {
                objectInfo.metadata["content-type"] = content_types.front();
            }
        }
        else if (headers.Contains("Content-Type"))
        {
            auto content_types = headers.Get("Content-Type");
            if (!content_types.empty())
            {
                objectInfo.metadata["content-type"] = content_types.front();
            }
        }
        
        result.success = true;
        result.message = "Object info retrieved successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during get object info: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

CloudResult MinioCloudManager::getBucketInfoWithMinioClient(const std::string& bucketName, BucketInfo& bucketInfo)
{
    CloudResult result;
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_NOT_INITIALIZED";
            return result;
        }
        
        // For MinIO, we'll use a simpler approach to check bucket existence
        // Try to list objects with a limit of 1 to check if bucket exists
        minio::s3::ListObjectsArgs listArgs;
        listArgs.bucket = bucketName;
        listArgs.max_keys = 1;
        
        auto listResponse = m_minio_client->ListObjects(listArgs);
        if (!listResponse)
        {
            result.success = false;
            result.message = "Bucket does not exist: " + bucketName;
            result.errorCode = "BUCKET_NOT_FOUND";
            return result;
        }
        
        // Populate bucket info (MinIO doesn't provide detailed bucket info)
        bucketInfo.name = bucketName;
        bucketInfo.region = "default"; // MinIO doesn't have regions like AWS
        bucketInfo.creationDate = ""; // MinIO doesn't provide creation date
        bucketInfo.objectCount = 0; // We don't count objects here
        bucketInfo.totalSize = 0; // We don't calculate total size here
        
        result.success = true;
        result.message = "Bucket info retrieved successfully: " + bucketName;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during get bucket info: ") + e.what();
        result.errorCode = "EXCEPTION";
    }
    
    return result;
}

std::string MinioCloudManager::getRequiredConfigParameter(const std::string& key) const
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    
    if (key == "endpoint")
    {
        return m_endpoint;
    }
    else if (key == "access_key")
    {
        return m_access_key;
    }
    else if (key == "secret_key")
    {
        return m_secret_key;
    }
    else if (key == "region")
    {
        return m_region;
    }
    else if (key == "use_ssl")
    {
        return m_use_ssl ? "true" : "false";
    }
    else if (key == "timeout_seconds")
    {
        return std::to_string(m_timeout_seconds);
    }
    else if (key == "max_retries")
    {
        return std::to_string(m_max_retries);
    }
    
    return "";
}

CloudResult MinioCloudManager::handleMinioError(const minio::error::Error& error, const std::string& operation) const
{
    CloudResult result;
    result.success = false;
    
    // Extract error code from the error message or derive from operation context
    std::string errorCode = "MINIO_ERROR";
    std::string errorMessage = error.String();
    
    // Try to extract HTTP status code or error type from the error message
    if (errorMessage.find("404") != std::string::npos || errorMessage.find("Not Found") != std::string::npos) {
        errorCode = "OBJECT_NOT_FOUND";
    } else if (errorMessage.find("403") != std::string::npos || errorMessage.find("Forbidden") != std::string::npos) {
        errorCode = "ACCESS_DENIED";
    } else if (errorMessage.find("401") != std::string::npos || errorMessage.find("Unauthorized") != std::string::npos) {
        errorCode = "AUTHENTICATION_FAILED";
    } else if (errorMessage.find("500") != std::string::npos || errorMessage.find("Internal Server Error") != std::string::npos) {
        errorCode = "SERVER_ERROR";
    } else if (errorMessage.find("timeout") != std::string::npos || errorMessage.find("Timeout") != std::string::npos) {
        errorCode = "TIMEOUT";
    } else if (errorMessage.find("network") != std::string::npos || errorMessage.find("connection") != std::string::npos) {
        errorCode = "NETWORK_ERROR";
    } else if (errorMessage.find("bucket") != std::string::npos && errorMessage.find("not found") != std::string::npos) {
        errorCode = "BUCKET_NOT_FOUND";
    }
    
    // Prefix error message with operation name for better context
    result.message = "[" + operation + "] " + errorMessage;
    result.errorCode = errorCode;
    
    return result;
}

std::string MinioCloudManager::extractRegionFromEndpoint(const std::string& endpoint) const
{
    return "us-east-1";
}

void MinioCloudManager::setLastError(const std::string& error)
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_last_error = error;
}

void MinioCloudManager::updateStats(bool success, std::chrono::milliseconds duration, const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_stats.recordRequest(success, duration, errorCode);
}

CloudResult MinioCloudManager::checkObjectExists(const std::string& bucket, const std::string& objectKey)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;
    
    if (!isAvailable())
    {
        result.success = false;
        result.message = "MinIO cloud manager not initialized";
        result.errorCode = "NOT_INITIALIZED";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    if (!validateBucketName(bucket) || !validateObjectKey(objectKey))
    {
        result.success = false;
        result.message = "Invalid bucket name or object key";
        result.errorCode = "INVALID_PARAMETERS";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "Failed to initialize MinIO client";
            result.errorCode = "CLIENT_INIT_FAILED";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }
        
        // Use MinIO statObject to check if object exists
        // This is more efficient than listObjects for existence checking
        minio::s3::StatObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;
        minio::s3::StatObjectResponse stat_response = m_minio_client->StatObject(args);
        
        if (stat_response)
        {
            result.success = true;
            result.message = "Object exists";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
        }
        else
        {
            result.success = false;
            result.message = "Object does not exist";
            result.errorCode = "OBJECT_NOT_FOUND";
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
        }
    }
    catch (const minio::error::Error& error)
    {
        result = handleMinioError(error, "checkObjectExists");
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = "Exception during object existence check: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
        result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    
    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

} // namespace nv_vms 
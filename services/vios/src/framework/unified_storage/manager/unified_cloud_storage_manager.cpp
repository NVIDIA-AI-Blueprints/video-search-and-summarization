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

#include "unified_cloud_storage_manager.h"
#include "../unified_storage_types.h"
#include "cloud_manager_factory.h"
#include "logger.h"
#include <chrono>
#include <filesystem>
#include <iostream>
#include <regex>
#include <sstream>

namespace nv_vms
{

UnifiedCloudStorageManager::UnifiedCloudStorageManager()
    : UnifiedStorageManager(StorageType::CLOUD)
    , m_use_ssl(true)
    , m_timeout_seconds(30)
    , m_max_retries(3)
{
}

UnifiedCloudStorageManager::~UnifiedCloudStorageManager()
{
}

bool UnifiedCloudStorageManager::isAvailable() const
{
    return UnifiedStorageManager::isAvailable();
}

std::string UnifiedCloudStorageManager::getStorageMode() const
{
    return "cloud";
}

bool UnifiedCloudStorageManager::configureStorage(const StorageConfig& config)
{
    return UnifiedStorageManager::configureStorage(config);
}

StorageConfig UnifiedCloudStorageManager::getStorageConfiguration() const
{
    return UnifiedStorageManager::getStorageConfiguration();
}

DeleteResult UnifiedCloudStorageManager::deleteFile(const std::string& path)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Cloud storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    try
    {
        // Check if this is a local file path (starts with / or contains no bucket separator)
        if (isLocalFilePath(path))
        {
            // Handle local file deletion
            return deleteLocalFile(path);
        }
        else
        {
            // Handle cloud object deletion
            auto path_parts = parseCloudPath(path);
            std::string bucket = path_parts.first;
            std::string object_key = path_parts.second;
            
            if (!validateBucketName(bucket))
            {
                result.errorCode = ErrorCodes::INVALID_BUCKET_NAME;
                result.message = "Invalid bucket name: " + bucket;
                recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time), result.errorCode);
                return result;
            }
            
            if (!validateObjectKey(object_key))
            {
                result.errorCode = ErrorCodes::INVALID_OBJECT_KEY;
                result.message = "Invalid object key: " + object_key;
                recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time), result.errorCode);
                return result;
            }
            
            // Get object size before deletion
            CloudObject object_info;
            CloudResult info_result;
            CloudResult delete_result;
            {
                std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
                if (!m_cloud_manager)
                {
                    result.errorCode = ErrorCodes::NOT_INITIALIZED;
                    result.message = "Cloud manager not initialized";
                    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - start_time), result.errorCode);
                    return result;
                }
                info_result = m_cloud_manager->getObjectInfo(bucket, object_key, object_info);
            }
            size_t object_size = 0;
            if (info_result.success)
            {
                object_size = object_info.size;
            }
            
            // Delete the object using cloud manager
            {
                std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
                if (!m_cloud_manager)
                {
                    result.errorCode = ErrorCodes::NOT_INITIALIZED;
                    result.message = "Cloud manager not initialized";
                    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - start_time), result.errorCode);
                    return result;
                }
                delete_result = m_cloud_manager->deleteObject(bucket, object_key);
            }
            
            if (!delete_result.success)
            {
                result.errorCode = ErrorCodes::DELETE_FAILED;
                result.message = "Failed to delete object: " + delete_result.message;
                recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time), result.errorCode);
                return result;
            }
            
            result.success = true;
            result.message = "Object deleted successfully";
            result.deletedPath = path;
            result.deletedSize = object_size;
            result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            recordOperation(true, result.duration);
        }
    }
    catch (const std::exception& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    
    return result;
}

DeleteResult UnifiedCloudStorageManager::deleteDirectory(const std::string& path, bool recursive)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Cloud storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    try
    {
        auto [bucket, prefix] = parseCloudPath(path);
        
        if (!validateBucketName(bucket))
        {
            result.errorCode = ErrorCodes::INVALID_BUCKET_NAME;
            result.message = "Invalid bucket name: " + bucket;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        // List all objects with the prefix
        std::vector<std::string> objects_to_delete = listObjectsInDirectory(bucket, prefix);
        
        if (objects_to_delete.empty())
        {
            result.errorCode = ErrorCodes::FILE_NOT_FOUND;
            result.message = "No objects found with prefix: " + path;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        // Delete all objects using cloud manager
        CloudResult delete_result;
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            if (!m_cloud_manager) {
                result.errorCode = ErrorCodes::NOT_INITIALIZED;
                result.message = "Cloud manager not initialized";
                recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time), result.errorCode);
                return result;
            }
            delete_result = m_cloud_manager->deleteMultipleObjects(bucket, objects_to_delete);
        }
        
        if (!delete_result.success)
        {
            result.errorCode = ErrorCodes::DELETE_FAILED;
            result.message = "Failed to delete directory: " + delete_result.message;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        result.success = true;
        result.message = "Directory deleted successfully";
        result.deletedSize = objects_to_delete.size(); // Approximate size
        recordOperation(true, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    
    return result;
}

MultiDeleteResult UnifiedCloudStorageManager::deleteMultipleFiles(const std::vector<std::string>& file_paths)
{
    return UnifiedStorageManager::deleteMultipleFiles(file_paths);
}

MultiDeleteResult UnifiedCloudStorageManager::deleteFilesInDirectory(const std::string& directory_path, 
                                                                     const std::string& pattern, 
                                                                     bool recursive)
{
    MultiDeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.error_code = ErrorCodes::NOT_INITIALIZED;
        result.error_message = "Cloud storage manager not initialized";
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    try
    {
        auto [bucket, prefix] = parseCloudPath(directory_path);
        
        if (!validateBucketName(bucket))
        {
            result.error_code = ErrorCodes::INVALID_BUCKET_NAME;
            result.error_message = "Invalid bucket name: " + bucket;
            result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            return result;
        }
        
        // List objects matching the pattern
        std::vector<std::string> objects_to_delete = listObjectsInDirectory(bucket, prefix, pattern);
        result.total_files = objects_to_delete.size();
        
        // Delete each object
        for (const auto& object_key : objects_to_delete)
        {
            std::string cloud_path = formatCloudPath(bucket, object_key);
            DeleteResult delete_result = deleteFile(cloud_path);
            result.addResult(delete_result);
        }
        
        result.overall_success = (result.failed_deletes == 0);
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    catch (const std::exception& e)
    {
        result.error_code = ErrorCodes::EXCEPTION;
        result.error_message = "Exception: " + std::string(e.what());
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
    }
    
    return result;
}

BucketResult UnifiedCloudStorageManager::createBucket(const std::string& bucket_name)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Cloud storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    if (!validateBucketName(bucket_name))
    {
        result.errorCode = ErrorCodes::INVALID_BUCKET_NAME;
        result.message = "Invalid bucket name: " + bucket_name;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Check if bucket already exists
    BucketResult exists_result = checkBucketExists(bucket_name);
    if (exists_result.success)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Bucket already exists: " + bucket_name;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Create bucket (this would need to be implemented in the CloudReader)
    // For now, we'll return a not implemented error
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "Bucket creation not yet implemented in CloudReader";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    
    return result;
}

BucketResult UnifiedCloudStorageManager::deleteBucket(const std::string& bucket_name, bool force)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Cloud storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    if (!validateBucketName(bucket_name))
    {
        result.errorCode = ErrorCodes::INVALID_BUCKET_NAME;
        result.message = "Invalid bucket name: " + bucket_name;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Check if bucket exists
    BucketResult exists_result = checkBucketExists(bucket_name);
    if (!exists_result.success)
    {
        result.errorCode = ErrorCodes::FILE_NOT_FOUND;
        result.message = "Bucket not found: " + bucket_name;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // If not forcing, check if bucket is empty
    if (!force)
    {
        std::vector<std::string> objects = listObjectsInDirectory(bucket_name, "");
        if (!objects.empty())
        {
            result.errorCode = ErrorCodes::EXCEPTION;
            result.message = "Bucket is not empty: " + bucket_name;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
    }
    
    // Delete bucket (this would need to be implemented in the CloudReader)
    // For now, we'll return a not implemented error
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "Bucket deletion not yet implemented in CloudReader";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    
    return result;
}

BucketResult UnifiedCloudStorageManager::checkBucketExists(const std::string& bucket_name)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Cloud storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    if (!validateBucketName(bucket_name))
    {
        result.errorCode = ErrorCodes::INVALID_BUCKET_NAME;
        result.message = "Invalid bucket name: " + bucket_name;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    try
    {
        // Check if bucket exists using cloud manager
        CloudResult check_result;
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            if (!m_cloud_manager)
            {
                result.errorCode = ErrorCodes::NOT_INITIALIZED;
                result.message = "Cloud manager not initialized";
                recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - start_time), result.errorCode);
                return result;
            }
            check_result = m_cloud_manager->checkBucketExists(bucket_name);
        }
        
        if (check_result.success)
        {
            result.success = true;
            result.message = "Bucket exists: " + bucket_name;
            result.bucketName = bucket_name;
        }
        else
        {
            result.errorCode = ErrorCodes::FILE_NOT_FOUND;
            result.message = "Bucket not found: " + bucket_name;
        }
        
        recordOperation(result.success, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    catch (const std::exception& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    
    return result;
}

std::vector<BucketInfo> UnifiedCloudStorageManager::listBuckets()
{
    std::vector<BucketInfo> buckets;
    
    if (!isAvailable())
    {
        return buckets;
    }
    
    try
    {
        // Implement bucket listing using the cloud manager
        if (m_cloud_manager)
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            std::vector<std::string> bucket_names;
            CloudResult result = m_cloud_manager->listBuckets(bucket_names);
            
            if (result.success)
            {
                // Convert bucket names to BucketInfo objects
                for (const auto& bucket_name : bucket_names)
                {
                    BucketInfo bucket_info;
                    bucket_info.name = bucket_name;
                    bucket_info.creationDate = ""; // Could be populated if available
                    bucket_info.totalSize = 0; // Could be calculated if needed
                    buckets.push_back(bucket_info);
                }
                LOG(info) << "Successfully listed buckets from cloud storage" << std::endl;
            }
            else
            {
                LOG(warning) << "Failed to list buckets: " << result.message << std::endl;
            }
        }
        else
        {
            LOG(warning) << "Cloud manager not available for bucket listing" << std::endl;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during bucket listing: " << e.what() << std::endl;
    }
    
    return buckets;
}

StorageStats UnifiedCloudStorageManager::getManagerStats() const
{
    return UnifiedStorageManager::getManagerStats();
}

void UnifiedCloudStorageManager::resetManagerStats()
{
    UnifiedStorageManager::resetManagerStats();
}

bool UnifiedCloudStorageManager::performHealthCheck()
{
    if (!isAvailable())
    {
        m_last_error = "Cloud storage manager not initialized";
        return false;
    }
    
    try
    {
        // Check if we can access the configured bucket
        if (!m_bucket_name.empty())
        {
            CloudResult result;
            {
                std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
                if (!m_cloud_manager)
                {
                    m_last_error = "Cloud manager not initialized";
                    return false;
                }
                result = m_cloud_manager->checkBucketExists(m_bucket_name);
            }
            if (!result.success)
            {
                m_last_error = "Cannot access bucket: " + m_bucket_name;
                return false;
            }
        }
        
        return true;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Health check failed: " + std::string(e.what());
        return false;
    }
}

std::string UnifiedCloudStorageManager::getLastError() const
{
    return UnifiedStorageManager::getLastError();
}

bool UnifiedCloudStorageManager::initializeStorage()
{
    try
    {
        // Get configuration parameters
        std::string cloud_type = m_config.getParameter(StorageConstants::CLOUD_TYPE_KEY, StorageConstants::MINIO_TYPE);
        m_bucket_name = m_config.getParameter(StorageConstants::BUCKET_NAME_KEY, "");
        m_endpoint = m_config.getParameter(StorageConstants::ENDPOINT_KEY, "");
        m_access_key = m_config.getParameter(StorageConstants::ACCESS_KEY_KEY, "");
        m_secret_key = m_config.getParameter(StorageConstants::SECRET_KEY_KEY, "");
        m_region = m_config.getParameter(StorageConstants::REGION_KEY, "");
        m_use_ssl = m_config.getParameter(StorageConstants::USE_SSL_KEY, "true") == "true";
        m_timeout_seconds = std::stoul(m_config.getParameter(StorageConstants::TIMEOUT_SECONDS_KEY, "30"));
        m_max_retries = std::stoul(m_config.getParameter(StorageConstants::MAX_RETRIES_KEY, "3"));
        
        // Create cloud manager configuration
        CloudManagerConfig cloud_config;
        cloud_config.storageType = CloudManagerFactory::stringToStorageType(cloud_type);
        cloud_config.endpoint = m_endpoint;
        cloud_config.accessKeyId = m_access_key;
        cloud_config.secretAccessKey = m_secret_key;
        cloud_config.region = m_region;
        cloud_config.useSSL = m_use_ssl;
        cloud_config.timeoutSeconds = m_timeout_seconds;
        cloud_config.maxRetries = m_max_retries;
        
        // Create cloud manager
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            m_cloud_manager = CloudManagerFactory::createManager(cloud_type, cloud_config);
        }
        if (!m_cloud_manager)
        {
            m_last_error = "Failed to create cloud manager for type: " + cloud_type;
            return false;
        }
        
        return true;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Failed to initialize cloud storage: " + std::string(e.what());
        return false;
    }
}

bool UnifiedCloudStorageManager::cleanupStorage()
{
    std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
    m_cloud_manager.reset();
    return true;
}

std::pair<std::string, std::string> UnifiedCloudStorageManager::parseCloudPath(const std::string& path) const
{
    std::string bucket = m_bucket_name;
    std::string object_key = path;
    
    // If path contains bucket prefix (e.g., "bucket/key"), extract bucket
    size_t slash_pos = path.find('/');
    if (slash_pos != std::string::npos)
    {
        std::string potential_bucket = path.substr(0, slash_pos);
        if (validateBucketName(potential_bucket))
        {
            bucket = potential_bucket;
            object_key = path.substr(slash_pos + 1);
        }
    }
    
    return {bucket, object_key};
}

std::string UnifiedCloudStorageManager::formatCloudPath(const std::string& bucket, const std::string& object_key) const
{
    if (object_key.empty())
    {
        return bucket;
    }
    return bucket + "/" + object_key;
}

bool UnifiedCloudStorageManager::validateBucketName(const std::string& bucket_name) const
{
    if (bucket_name.empty() || bucket_name.length() < 3 || bucket_name.length() > 63)
    {
        return false;
    }
    
    // Check for valid characters (lowercase letters, numbers, hyphens, dots)
    std::regex bucket_pattern("^[a-z0-9][a-z0-9.-]*[a-z0-9]$");
    return std::regex_match(bucket_name, bucket_pattern);
}

bool UnifiedCloudStorageManager::validateObjectKey(const std::string& object_key) const
{
    if (object_key.empty())
    {
        return false;
    }
    
    // Check for invalid characters
    std::string invalid_chars = "<>:\"|?*";
    for (char c : invalid_chars)
    {
        if (object_key.find(c) != std::string::npos)
        {
            return false;
        }
    }
    
    return true;
}

std::vector<std::string> UnifiedCloudStorageManager::listObjectsInDirectory(const std::string& bucket, 
                                                                             const std::string& prefix,
                                                                             const std::string& pattern) const
{
    std::vector<std::string> objects;
    
    try
    {
        std::vector<std::string> objectKeys;
        CloudResult result;
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            if (!m_cloud_manager)
            {
                return objects;
            }
            result = m_cloud_manager->listObjects(bucket, prefix, objectKeys, 1000);
        }
        
        if (!result.success)
        {
            return objects;
        }
        
        std::regex file_pattern;
        if (!pattern.empty())
        {
            file_pattern = std::regex(pattern);
        }
        
        for (const auto& objectKey : objectKeys)
        {
            std::string object_name = objectKey;
            
            // Extract filename from object key
            size_t last_slash = object_name.find_last_of('/');
            if (last_slash != std::string::npos)
            {
                object_name = object_name.substr(last_slash + 1);
            }
            
            // If no pattern specified, include all objects
            if (pattern.empty() || std::regex_match(object_name, file_pattern))
            {
                objects.push_back(objectKey);
            }
        }
    }
    catch (const std::exception& e)
    {
        // Return empty vector on error
    }
    
    return objects;
}

bool UnifiedCloudStorageManager::isLocalFilePath(const std::string& path) const
{
    // In cloud storage mode, detect local file paths vs cloud object keys
    if (path.empty())
    {
        return false;
    }
    
    // Absolute paths starting with / are local files
    if (path[0] == '/')
    {
        return true;
    }
    
    // Relative paths starting with ./ or ../ are local files
    if (path.rfind("./", 0) == 0 || path.rfind("../", 0) == 0)
    {
        return true;
    }
    
    // Check if path contains filesystem directory separators that indicate it's a local path
    // Local paths typically have patterns like "webroot/", "temp_files/", etc.
    // Cloud object keys usually don't start with common local directory names
    if (path.find("webroot/") == 0 || 
        path.find("temp_files/") == 0 ||
        path.find("streamer_videos/") == 0)
    {
        return true;
    }
    
    // Default: treat as cloud object key
    return false;
}

DeleteResult UnifiedCloudStorageManager::deleteLocalFile(const std::string& path)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    try
    {
        std::filesystem::path file_path(path);
        
        if (!std::filesystem::exists(file_path))
        {
            result.errorCode = ErrorCodes::FILE_NOT_FOUND;
            result.message = "Local file not found: " + path;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        if (!std::filesystem::is_regular_file(file_path))
        {
            result.errorCode = ErrorCodes::PERMISSION_DENIED;
            result.message = "Path is not a regular file: " + path;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        // Get file size before deletion
        result.deletedSize = std::filesystem::file_size(file_path);
        
        // Delete the file
        std::filesystem::remove(file_path);
        
        result.success = true;
        result.message = "Local file deleted successfully: " + path;
        result.deletedPath = path;
        
        recordOperation(true, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::filesystem::filesystem_error& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Filesystem error: " + std::string(e.what());
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    catch (const std::exception& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
    }
    
    return result;
}

bool UnifiedCloudStorageManager::isFileExist(const std::string& path) const
{
    if (!isAvailable())
    {
        return false;
    }
    
    try
    {
        // Check if this is a local file path
        if (isLocalFilePath(path))
        {
            // Handle as local file
            std::filesystem::path file_path(path);
            return std::filesystem::exists(file_path) && std::filesystem::is_regular_file(file_path);
        }
        
        // Handle as cloud object
        auto [bucket, object_key] = parseCloudPath(path);
        
        if (bucket.empty() || object_key.empty())
        {
            return false;
        }
        
        // Use checkObjectExists for efficient object existence checking
        // This is much more efficient than listObjects for checking if a specific object exists
        CloudResult exists_result;
        {
            std::lock_guard<std::mutex> lock(m_cloud_manager_mutex);
            if (!m_cloud_manager)
            {
                return false;
            }
            exists_result = m_cloud_manager->checkObjectExists(bucket, object_key);
        }
        
        return exists_result.success;
    }
    catch (const std::exception& e)
    {
        // Return false on any exception
        return false;
    }
}

} // namespace nv_vms 
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

#include "unified_storage_manager.h"
#include "../unified_storage_types.h"
#include <filesystem>
#include <iostream>
#include <regex>
#include <sstream>
#include <mutex>

namespace nv_vms
{

UnifiedStorageManager::UnifiedStorageManager(StorageType type)
    : m_storage_type(type)
    , m_initialized(false)
{
}

UnifiedStorageManager::~UnifiedStorageManager()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    // Derived classes should handle cleanup in their own destructors
}

bool UnifiedStorageManager::isAvailable() const
{
    return m_initialized.load();
}

std::string UnifiedStorageManager::getStorageMode() const
{
    switch (m_storage_type)
    {
        case StorageType::LOCAL:
            return "local";
        case StorageType::CLOUD:
            return "cloud";
        default:
            return "unknown";
    }
}

StorageType UnifiedStorageManager::getStorageType() const
{
    return m_storage_type;
}

bool UnifiedStorageManager::configureStorage(const StorageConfig& config)
{
    bool needs_cleanup = false;
    
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        
        m_config = config;
        needs_cleanup = m_initialized.load();
    }
    
    // Release lock before calling virtual methods to avoid potential deadlock
    if (needs_cleanup)
    {
        cleanupStorage();
    }
    
    bool init_result = initializeStorage();
    m_initialized.store(init_result);
    
    if (!init_result)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_last_error = "Failed to initialize storage";
    }
    
    return init_result;
}

StorageConfig UnifiedStorageManager::getStorageConfiguration() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_config;
}

DeleteResult UnifiedStorageManager::deleteFile(const std::string& path)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    if (!validatePath(path))
    {
        result.errorCode = ErrorCodes::INVALID_OBJECT_KEY;
        result.message = "Invalid path: " + path;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "deleteFile not implemented for this storage type";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    return result;
}

DeleteResult UnifiedStorageManager::deleteDirectory(const std::string& path, bool recursive)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    if (!validatePath(path))
    {
        result.errorCode = ErrorCodes::INVALID_OBJECT_KEY;
        result.message = "Invalid path: " + path;
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "deleteDirectory not implemented for this storage type";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    return result;
}

MultiDeleteResult UnifiedStorageManager::deleteMultipleFiles(const std::vector<std::string>& file_paths)
{
    MultiDeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.error_code = ErrorCodes::NOT_INITIALIZED;
        result.error_message = "Storage manager not initialized";
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    result.total_files = file_paths.size();
    
    for (const auto& path : file_paths)
    {
        DeleteResult delete_result = deleteFile(path);
        result.addResult(delete_result);
    }
    
    result.overall_success = (result.failed_deletes == 0);
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    
    return result;
}

MultiDeleteResult UnifiedStorageManager::deleteFilesInDirectory(const std::string& directory_path, 
                                                                const std::string& pattern, 
                                                                bool recursive)
{
    MultiDeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.error_code = ErrorCodes::NOT_INITIALIZED;
        result.error_message = "Storage manager not initialized";
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.error_code = ErrorCodes::NOT_IMPLEMENTED;
    result.error_message = "deleteFilesInDirectory not implemented for this storage type";
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    return result;
}

BucketResult UnifiedStorageManager::createBucket(const std::string& bucket_name)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "createBucket not implemented for this storage type";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    return result;
}

BucketResult UnifiedStorageManager::deleteBucket(const std::string& bucket_name, bool force)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "deleteBucket not implemented for this storage type";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    return result;
}

BucketResult UnifiedStorageManager::checkBucketExists(const std::string& bucket_name)
{
    BucketResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    // Default implementation - derived classes should override
    result.errorCode = ErrorCodes::NOT_IMPLEMENTED;
    result.message = "checkBucketExists not implemented for this storage type";
    recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time), result.errorCode);
    return result;
}

std::vector<BucketInfo> UnifiedStorageManager::listBuckets()
{
    // Default implementation - derived classes should override
    return std::vector<BucketInfo>();
}

bool UnifiedStorageManager::createDirectory(const std::string& path, bool create_parents)
{
    if (!isAvailable())
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_last_error = "Storage manager not initialized";
        return false;
    }
    
    // Default implementation - derived classes should override
    std::lock_guard<std::mutex> lock(m_mutex);
    m_last_error = "createDirectory not implemented for this storage type";
    return false;
}

bool UnifiedStorageManager::directoryExists(const std::string& path) const
{
    // Default implementation - derived classes should override
    return false;
}

StorageStats UnifiedStorageManager::getManagerStats() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_stats;
}

void UnifiedStorageManager::resetManagerStats()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_stats = StorageStats();
}

bool UnifiedStorageManager::performHealthCheck()
{
    if (!isAvailable())
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_last_error = "Storage manager not initialized";
        return false;
    }
    
    // Default implementation - derived classes should override
    std::lock_guard<std::mutex> lock(m_mutex);
    m_last_error = "performHealthCheck not implemented for this storage type";
    return false;
}

bool UnifiedStorageManager::isFileExist(const std::string& path) const
{
    // Default implementation - derived classes should override
    return false;
}

std::string UnifiedStorageManager::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_last_error;
}

void UnifiedStorageManager::recordOperation(bool success, std::chrono::milliseconds duration, 
                                           const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_stats.recordRequest(success, duration, errorCode);
}

std::string UnifiedStorageManager::formatPath(const std::string& path) const
{
    // Remove leading/trailing slashes and normalize
    std::string formatted = path;
    
    // Remove leading slash
    if (!formatted.empty() && formatted[0] == '/')
    {
        formatted = formatted.substr(1);
    }
    
    // Remove trailing slash
    if (!formatted.empty() && formatted[formatted.length() - 1] == '/')
    {
        formatted = formatted.substr(0, formatted.length() - 1);
    }
    
    return formatted;
}

bool UnifiedStorageManager::validatePath(const std::string& path) const
{
    if (path.empty())
    {
        return false;
    }
    
    // Check for invalid characters
    std::string invalid_chars = "<>:\"|?*";
    for (char c : invalid_chars)
    {
        if (path.find(c) != std::string::npos)
        {
            return false;
        }
    }
    
    return true;
}

} // namespace nv_vms 
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

#include "unified_local_storage_manager.h"
#include "../unified_storage_types.h"
#include "logger.h"
#include <filesystem>
#include <iostream>
#include <regex>
#include <sstream>
#include <fstream>
#include <chrono>

namespace nv_vms
{

UnifiedLocalStorageManager::UnifiedLocalStorageManager()
    : UnifiedStorageManager(StorageType::LOCAL)
    , m_create_directories(true)
    , m_recursive_listing(false)
    , m_max_depth(10)
    , m_include_hidden(false)
{
}

UnifiedLocalStorageManager::~UnifiedLocalStorageManager()
{
}

bool UnifiedLocalStorageManager::isAvailable() const
{
    return UnifiedStorageManager::isAvailable();
}

std::string UnifiedLocalStorageManager::getStorageMode() const
{
    return "local";
}

bool UnifiedLocalStorageManager::configureStorage(const StorageConfig& config)
{
    return UnifiedStorageManager::configureStorage(config);
}

StorageConfig UnifiedLocalStorageManager::getStorageConfiguration() const
{
    return UnifiedStorageManager::getStorageConfiguration();
}

DeleteResult UnifiedLocalStorageManager::deleteFile(const std::string& path)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Local storage manager not initialized";
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        recordOperation(false, duration, result.errorCode);
        return result;
    }
    
    std::string full_path = path;
    
    try
    {
        std::filesystem::path file_path(full_path);
        
        if (!std::filesystem::exists(file_path))
        {
            result.errorCode = ErrorCodes::FILE_NOT_FOUND;
            result.message = "File not found: " + path;
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            result.duration = duration;
            recordOperation(false, duration, result.errorCode);
            return result;
        }
        
        if (!std::filesystem::is_regular_file(file_path))
        {
            result.errorCode = ErrorCodes::PERMISSION_DENIED;
            result.message = "Path is not a regular file: " + path;
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time);
            result.duration = duration;
            recordOperation(false, duration, result.errorCode);
            return result;
        }
        
        // Get file size before deletion
        try
        {
            result.deletedSize = std::filesystem::file_size(file_path);
        }
        catch (const std::filesystem::filesystem_error& e)
        {
            // If we can't get the file size, set it to 0 but continue with deletion
            result.deletedSize = 0;
            LOG(warning) << "Could not get file size for " << path << ": " << e.what() << std::endl;
        }
        
        // Delete the file
        std::filesystem::remove(file_path);
        
        result.success = true;
        result.message = "File deleted successfully: " + path;
        result.deletedPath = path;
        
        auto end_time = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        result.duration = duration;
        recordOperation(true, duration);
        
        LOG(info) << "Successfully deleted file: " << path << " (" << result.deletedSize << " bytes) in " 
                  << duration.count() << "ms" << std::endl;
    }
    catch (const std::filesystem::filesystem_error& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Filesystem error: " + std::string(e.what());
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        recordOperation(false, duration, result.errorCode);
    }
    catch (const std::exception& e)
    {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        recordOperation(false, duration, result.errorCode);
    }
    
    return result;
}

bool UnifiedLocalStorageManager::isFileExist(const std::string& path) const
{
    if (!isAvailable())
    {
        return false;
    }
    
    try
    {
        std::string full_path = path;
        std::filesystem::path file_path(full_path);
        
        // Check if the file exists and is a regular file
        return std::filesystem::exists(file_path) && std::filesystem::is_regular_file(file_path);
    }
    catch (const std::exception& e)
    {
        // Return false on any exception
        return false;
    }
}

DeleteResult UnifiedLocalStorageManager::deleteDirectory(const std::string& path, bool recursive)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Local storage manager not initialized";
        recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), result.errorCode);
        return result;
    }
    
    std::string full_path = path;
    
    try
    {
        std::filesystem::path dir_path(full_path);
        
        if (!std::filesystem::exists(dir_path))
        {
            result.errorCode = ErrorCodes::FILE_NOT_FOUND;
            result.message = "Directory not found: " + path;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        if (!std::filesystem::is_directory(dir_path))
        {
            result.errorCode = ErrorCodes::PERMISSION_DENIED;
            result.message = "Path is not a directory: " + path;
            recordOperation(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), result.errorCode);
            return result;
        }
        
        // Calculate directory size and file count before deletion
        result.deletedSize = calculateDirectorySize(full_path);
        
        // Delete the directory
        if (recursive)
        {
            std::filesystem::remove_all(dir_path);
        }
        else
        {
            std::filesystem::remove(dir_path);
        }
        
        result.success = true;
        result.message = "Directory deleted successfully: " + path;
        result.deletedPath = path;
        
        auto end_time = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        recordOperation(true, duration);
        
        LOG(info) << "Successfully deleted directory: " << path << " (" << result.deletedSize << " bytes) in " 
                  << duration.count() << "ms" << std::endl;
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

MultiDeleteResult UnifiedLocalStorageManager::deleteMultipleFiles(const std::vector<std::string>& file_paths)
{
    return UnifiedStorageManager::deleteMultipleFiles(file_paths);
}

MultiDeleteResult UnifiedLocalStorageManager::deleteFilesInDirectory(const std::string& directory_path, 
                                                                     const std::string& pattern, 
                                                                     bool recursive)
{
    MultiDeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    if (!isAvailable())
    {
        result.error_code = ErrorCodes::NOT_INITIALIZED;
        result.error_message = "Local storage manager not initialized";
        result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        return result;
    }
    
    try
    {
        std::string full_dir_path = directory_path;
        
        // Find files matching the pattern
        std::vector<std::string> files_to_delete = findFilesMatchingPattern(full_dir_path, pattern);
        result.total_files = files_to_delete.size();
        
        // Delete each file
        for (const auto& file_path : files_to_delete)
        {
            // Pass the original absolute file_path directly to deleteFile()
            // which expects absolute paths for its safety checks
            DeleteResult delete_result = deleteFile(file_path);
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

bool UnifiedLocalStorageManager::createDirectory(const std::string& path, bool create_parents)
{
    if (!isAvailable())
    {
        m_last_error = "Local storage manager not initialized";
        return false;
    }
    
    try
    {
        std::string full_path = path;
        
        std::filesystem::path dir_path(full_path);
        
        if (create_parents)
        {
            std::filesystem::create_directories(dir_path);
        }
        else
        {
            std::filesystem::create_directory(dir_path);
        }
        
        return true;
    }
    catch (const std::filesystem::filesystem_error& e)
    {
        m_last_error = "Filesystem error: " + std::string(e.what());
        return false;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Exception: " + std::string(e.what());
        return false;
    }
}

bool UnifiedLocalStorageManager::directoryExists(const std::string& path) const
{
    if (!isAvailable())
    {
        return false;
    }
    
    try
    {
        std::string full_path = path;
        std::filesystem::path dir_path(full_path);
        return std::filesystem::exists(dir_path) && std::filesystem::is_directory(dir_path);
    }
    catch (const std::exception& e)
    {
        return false;
    }
}

StorageStats UnifiedLocalStorageManager::getManagerStats() const
{
    return UnifiedStorageManager::getManagerStats();
}

void UnifiedLocalStorageManager::resetManagerStats()
{
    UnifiedStorageManager::resetManagerStats();
}

bool UnifiedLocalStorageManager::performHealthCheck()
{
    if (!isAvailable())
    {
        m_last_error = "Local storage manager not initialized";
        return false;
    }
    
    try
    {
        // Check if base directory exists and is accessible
        std::filesystem::path base_path(m_base_path);
        if (!std::filesystem::exists(base_path))
        {
            m_last_error = "Base directory does not exist: " + m_base_path;
            return false;
        }
        
        if (!std::filesystem::is_directory(base_path))
        {
            m_last_error = "Base path is not a directory: " + m_base_path;
            return false;
        }
        
        // Try to create a test file to check write permissions
        std::filesystem::path test_file = base_path / ".health_check_test";
        std::ofstream test_stream(test_file);
        if (!test_stream.is_open())
        {
            m_last_error = "Cannot write to base directory: " + m_base_path;
            return false;
        }
        test_stream.close();
        std::filesystem::remove(test_file);
        
        return true;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Health check failed: " + std::string(e.what());
        return false;
    }
}

std::string UnifiedLocalStorageManager::getLastError() const
{
    return UnifiedStorageManager::getLastError();
}

bool UnifiedLocalStorageManager::initializeStorage()
{
    try
    {
        // Get configuration parameters
        m_base_path = m_config.getParameter(StorageConstants::BASE_PATH_KEY, "/tmp/vms_storage");
        m_create_directories = m_config.getParameter(StorageConstants::CREATE_DIRECTORIES_KEY, "true") == "true";
        m_recursive_listing = m_config.getParameter(StorageConstants::RECURSIVE_LISTING_KEY, "false") == "true";
        m_max_depth = std::stoul(m_config.getParameter(StorageConstants::MAX_DEPTH_KEY, "10"));
        m_include_hidden = m_config.getParameter(StorageConstants::INCLUDE_HIDDEN_KEY, "false") == "true";
        
        // Create base directory if it doesn't exist and create_directories is enabled
        if (m_create_directories)
        {
            std::filesystem::path base_path(m_base_path);
            if (!std::filesystem::exists(base_path))
            {
                std::filesystem::create_directories(base_path);
            }
        }
        
        return true;
    }
    catch (const std::exception& e)
    {
        m_last_error = "Failed to initialize local storage: " + std::string(e.what());
        return false;
    }
}

bool UnifiedLocalStorageManager::cleanupStorage()
{
    // No cleanup needed for local storage
    return true;
}

std::string UnifiedLocalStorageManager::getFullPath(const std::string& relative_path) const
{
    if (relative_path.empty())
    {
        return m_base_path;
    }
    
    std::filesystem::path base_path(m_base_path);
    std::filesystem::path rel_path(relative_path);
    
    // Resolve the full path
    std::filesystem::path full_path = base_path / rel_path;
    return full_path.string();
}

bool UnifiedLocalStorageManager::isPathWithinBase(const std::string& path) const
{
    try
    {
        std::filesystem::path base_path(m_base_path);
        std::filesystem::path check_path(path);
        
        // Normalize paths - use weakly_canonical to avoid exceptions for non-existent paths
        base_path = std::filesystem::weakly_canonical(base_path);
        check_path = std::filesystem::weakly_canonical(check_path);
        
        // Check if check_path is within base_path
        auto base_iter = base_path.begin();
        auto check_iter = check_path.begin();
        
        while (base_iter != base_path.end() && check_iter != check_path.end())
        {
            if (*base_iter != *check_iter)
            {
                return false;
            }
            ++base_iter;
            ++check_iter;
        }
        
        return base_iter == base_path.end();
    }
    catch (const std::exception& e)
    {
        LOG(warning) << "Exception in isPathWithinBase for path '" << path << "': " << e.what() << std::endl;
        return false;
    }
}

std::vector<std::string> UnifiedLocalStorageManager::findFilesMatchingPattern(const std::string& directory_path, 
                                                                              const std::string& pattern) const
{
    std::vector<std::string> matching_files;
    
    try
    {
        std::filesystem::path dir_path(directory_path);
        
        if (!std::filesystem::exists(dir_path) || !std::filesystem::is_directory(dir_path))
        {
            return matching_files;
        }
        
        std::regex file_pattern;
        if (!pattern.empty())
        {
            file_pattern = std::regex(pattern);
        }
        
        if (m_recursive_listing)
        {
            // Use recursive iterator with depth limit
            std::filesystem::recursive_directory_iterator dir_iter(dir_path);
            std::filesystem::recursive_directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                const std::filesystem::path& file_path = dir_iter->path();
                
                // Check depth limit
                if (m_max_depth > 0)
                {
                    size_t depth = std::distance(dir_path.begin(), file_path.begin());
                    if (depth > m_max_depth)
                    {
                        dir_iter.disable_recursion_pending();
                        continue;
                    }
                }
                
                // Skip hidden files if not included
                if (!m_include_hidden && file_path.filename().string()[0] == '.')
                {
                    continue;
                }
                
                // Check if it's a regular file
                if (std::filesystem::is_regular_file(file_path))
                {
                    std::string filename = file_path.filename().string();
                    
                    // If no pattern specified, include all files
                    if (pattern.empty() || std::regex_match(filename, file_pattern))
                    {
                        matching_files.push_back(file_path.string());
                    }
                }
            }
        }
        else
        {
            // Use non-recursive iterator
            std::filesystem::directory_iterator dir_iter(dir_path);
            std::filesystem::directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                const std::filesystem::path& file_path = dir_iter->path();
                
                // Skip hidden files if not included
                if (!m_include_hidden && file_path.filename().string()[0] == '.')
                {
                    continue;
                }
                
                // Check if it's a regular file
                if (std::filesystem::is_regular_file(file_path))
                {
                    std::string filename = file_path.filename().string();
                    
                    // If no pattern specified, include all files
                    if (pattern.empty() || std::regex_match(filename, file_pattern))
                    {
                        matching_files.push_back(file_path.string());
                    }
                }
            }
        }
    }
    catch (const std::exception& e)
    {
        // Return empty vector on error
    }
    
    return matching_files;
}

uint64_t UnifiedLocalStorageManager::calculateDirectorySize(const std::string& path) const
{
    uint64_t total_size = 0;
    
    try
    {
        std::filesystem::path dir_path(path);
        
        if (!std::filesystem::exists(dir_path) || !std::filesystem::is_directory(dir_path))
        {
            return 0;
        }
        
        if (m_recursive_listing)
        {
            // Use recursive iterator with depth limit
            std::filesystem::recursive_directory_iterator dir_iter(dir_path);
            std::filesystem::recursive_directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                const std::filesystem::path& file_path = dir_iter->path();
                
                // Check depth limit
                if (m_max_depth > 0)
                {
                    size_t depth = std::distance(dir_path.begin(), file_path.begin());
                    if (depth > m_max_depth)
                    {
                        dir_iter.disable_recursion_pending();
                        continue;
                    }
                }
                
                if (std::filesystem::is_regular_file(file_path))
                {
                    total_size += std::filesystem::file_size(file_path);
                }
            }
        }
        else
        {
            // Use non-recursive iterator
            std::filesystem::directory_iterator dir_iter(dir_path);
            std::filesystem::directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                const std::filesystem::path& file_path = dir_iter->path();
                
                if (std::filesystem::is_regular_file(file_path))
                {
                    total_size += std::filesystem::file_size(file_path);
                }
            }
        }
    }
    catch (const std::exception& e)
    {
        // Return 0 on error
    }
    
    return total_size;
}

size_t UnifiedLocalStorageManager::countFilesInDirectory(const std::string& path, bool recursive) const
{
    size_t file_count = 0;
    
    try
    {
        std::filesystem::path dir_path(path);
        
        if (!std::filesystem::exists(dir_path) || !std::filesystem::is_directory(dir_path))
        {
            return 0;
        }
        
        if (recursive)
        {
            std::filesystem::recursive_directory_iterator dir_iter(dir_path);
            std::filesystem::recursive_directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                const std::filesystem::path& file_path = dir_iter->path();
                
                // Check depth limit
                if (m_max_depth > 0)
                {
                    size_t depth = std::distance(dir_path.begin(), file_path.begin());
                    if (depth > m_max_depth)
                    {
                        dir_iter.disable_recursion_pending();
                        continue;
                    }
                }
                
                if (std::filesystem::is_regular_file(file_path))
                {
                    file_count++;
                }
            }
        }
        else
        {
            std::filesystem::directory_iterator dir_iter(dir_path);
            std::filesystem::directory_iterator end_iter;
            
            for (; dir_iter != end_iter; ++dir_iter)
            {
                if (std::filesystem::is_regular_file(dir_iter->path()))
                {
                    file_count++;
                }
            }
        }
    }
    catch (const std::exception& e)
    {
        // Return 0 on error
    }
    
    return file_count;
}

} // namespace nv_vms 
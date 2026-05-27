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

#include "unified_local_storage_reader.h"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <regex>
#include <sstream>
#include <string>

namespace nv_vms
{

// MIME type mapping
const std::map<std::string, std::string, std::less<>> UnifiedLocalStorageReader::s_mimeTypes = {
    {".txt", "text/plain"},
    {".log", "text/plain"},
    {".csv", "text/csv"},
    {".json", "application/json"},
    {".xml", "application/xml"},
    {".html", "text/html"},
    {".htm", "text/html"},
    {".css", "text/css"},
    {".js", "application/javascript"},
    {".pdf", "application/pdf"},
    {".jpg", "image/jpeg"},
    {".jpeg", "image/jpeg"},
    {".png", "image/png"},
    {".gif", "image/gif"},
    {".bmp", "image/bmp"},
    {".svg", "image/svg+xml"},
    {".mp4", "video/mp4"},
    {".avi", "video/x-msvideo"},
    {".mkv", "video/x-matroska"},
    {".mp3", "audio/mpeg"},
    {".wav", "audio/wav"},
    {".zip", "application/zip"},
    {".tar", "application/x-tar"},
    {".gz", "application/gzip"},
    {".bz2", "application/x-bzip2"},
    {".7z", "application/x-7z-compressed"}};

UnifiedLocalStorageReader::UnifiedLocalStorageReader()
    : UnifiedStorageReader(StorageType::LOCAL), m_localConfig(), m_basePath()
{
}

UnifiedLocalStorageReader::~UnifiedLocalStorageReader() {}

bool UnifiedLocalStorageReader::isAvailable() const
{
    return std::filesystem::exists(m_basePath) && std::filesystem::is_directory(m_basePath);
}

std::string UnifiedLocalStorageReader::getStorageMode() const
{
    return StorageConstants::LOCAL_STORAGE;
}

bool UnifiedLocalStorageReader::configureStorage(const StorageConfig& config)
{
    // Extract local-specific configuration first
    m_localConfig.basePath = config.getParameter(StorageConstants::BASE_PATH_KEY, "/tmp");
    m_localConfig.recursiveListing = config.getParameter(StorageConstants::RECURSIVE_LISTING_KEY, "false") == "true";
    m_localConfig.maxDepth = std::stoul(config.getParameter(StorageConstants::MAX_DEPTH_KEY, "10"));
    m_localConfig.includeHidden = config.getParameter(StorageConstants::INCLUDE_HIDDEN_KEY, "false") == "true";
    m_localConfig.timeoutSeconds = std::stoul(config.getParameter(StorageConstants::TIMEOUT_SECONDS_KEY, "30"));

    // Set base path
    m_basePath = std::filesystem::path(m_localConfig.basePath);

    // Validate configuration before calling base class
    if (!validateConfiguration(config))
    {
        setLastError("Invalid configuration provided");
        return false;
    }

    // Call base class configureStorage (which will call initializeStorage)
    if (!UnifiedStorageReader::configureStorage(config))
    {
        return false;
    }

    // Create base directory if it doesn't exist
    if (!std::filesystem::exists(m_basePath))
    {
        try
        {
            std::filesystem::create_directories(m_basePath);
        }
        catch (const std::exception& e)
        {
            setLastError(std::string("Failed to create base directory: ") + e.what());
            return false;
        }
    }

    return true;
}

StorageConfig UnifiedLocalStorageReader::getStorageConfiguration() const
{
    StorageConfig config = UnifiedStorageReader::getStorageConfiguration();
    config.setParameter(StorageConstants::BASE_PATH_KEY, m_localConfig.basePath);
    config.setParameter(StorageConstants::RECURSIVE_LISTING_KEY, m_localConfig.recursiveListing ? "true" : "false");
    config.setParameter(StorageConstants::MAX_DEPTH_KEY, std::to_string(m_localConfig.maxDepth));
    config.setParameter(StorageConstants::INCLUDE_HIDDEN_KEY, m_localConfig.includeHidden ? "true" : "false");
    config.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, std::to_string(m_localConfig.timeoutSeconds));
    return config;
}

FileResult UnifiedLocalStorageReader::downloadFile(const std::string& remote_path, const std::string& local_path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        std::filesystem::path sourcePath = normalizePath(remote_path);
        std::filesystem::path destPath = std::filesystem::path(local_path);

        // Check if source exists
        if (!std::filesystem::exists(sourcePath))
        {
            result.success = false;
            result.message = "Source file does not exist: " + remote_path;
            result.errorCode = "FILE_NOT_FOUND";
        }
        else if (!std::filesystem::is_regular_file(sourcePath))
        {
            result.success = false;
            result.message = "Source is not a regular file: " + remote_path;
            result.errorCode = "NOT_A_FILE";
        }
        else
        {
            // Create destination directory if it doesn't exist
            std::filesystem::create_directories(destPath.parent_path());

            // Copy file
            std::filesystem::copy_file(sourcePath, destPath, std::filesystem::copy_options::overwrite_existing);

            result.success = true;
            result.message = "File downloaded successfully";
            result.bytes_read = std::filesystem::file_size(sourcePath);
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during download: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedLocalStorageReader::getFileInfo(const std::string& path, FileInfo& fileInfo)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!std::filesystem::exists(path))
        {
            result.success = false;
            result.message = "File does not exist: " + path;
            result.errorCode = "FILE_NOT_FOUND";
        }
        else
        {
            fileInfo = createFileInfoFromPath(path);
            result.success = true;
            result.message = "File info retrieved successfully";
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during getFileInfo: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedLocalStorageReader::checkFileExists(const std::string& path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (std::filesystem::exists(path))
        {
            result.success = true;
            result.message = "File exists";
        }
        else
        {
            result.success = false;
            result.message = "File does not exist: " + path;
            result.errorCode = "FILE_NOT_FOUND";
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during checkFileExists: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileListResult UnifiedLocalStorageReader::listFiles(const std::string& path, const std::string& prefix)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        std::filesystem::path dirPath = normalizePath(path);

        if (!std::filesystem::exists(dirPath))
        {
            result.success = false;
            result.message = "Directory does not exist: " + path;
            result.errorCode = "DIRECTORY_NOT_FOUND";
        }
        else if (!std::filesystem::is_directory(dirPath))
        {
            result.success = false;
            result.message = "Path is not a directory: " + path;
            result.errorCode = "NOT_A_DIRECTORY";
        }
        else
        {
            result.path = path;
            result.files.clear();

            for (const auto& entry : std::filesystem::directory_iterator(dirPath))
            {
                if (shouldIncludeFile(entry.path()))
                {
                    FileInfo fileInfo = createFileInfoFromPath(entry.path());

                    // Apply prefix filter
                    if (prefix.empty() || fileInfo.name.find(prefix) == 0)
                    {
                        result.files.push_back(fileInfo);
                        result.totalSize += fileInfo.size;
                    }
                }
            }

            result.count = static_cast<uint32_t>(result.files.size());
            result.success = true;
            result.message = "Files listed successfully";
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listFiles: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileListResult UnifiedLocalStorageReader::listFilesPaginated(const std::string& path, const std::string& prefix,
                                                             const std::string& marker, uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        // Get all files first
        FileListResult allFiles = listFiles(path, prefix);
        if (!allFiles.success)
        {
            return allFiles;
        }

        result.path = path;
        result.success = true;
        result.message = "Files listed successfully";

        // Apply pagination
        size_t startIndex = 0;
        if (!marker.empty())
        {
            auto it = std::find_if(allFiles.files.begin(), allFiles.files.end(),
                                   [&marker](const FileInfo& file) { return file.name == marker; });
            if (it != allFiles.files.end())
            {
                startIndex = std::distance(allFiles.files.begin(), it) + 1;
            }
        }

        size_t endIndex = std::min(startIndex + maxKeys, allFiles.files.size());

        // Copy files for current page
        result.files.assign(allFiles.files.begin() + startIndex, allFiles.files.begin() + endIndex);
        result.count = static_cast<uint32_t>(result.files.size());
        result.isTruncated = (endIndex < allFiles.files.size());

        if (result.isTruncated && !result.files.empty())
        {
            result.nextMarker = result.files.back().name;
        }

        // Calculate total size for this page
        for (const auto& file : result.files)
        {
            result.totalSize += file.size;
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listFilesPaginated: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedLocalStorageReader::generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                                           std::string& url)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // Local storage doesn't support pre-signed URLs
        result.success = false;
        result.message = "Pre-signed URLs are not supported for local storage";
        result.errorCode = "NOT_SUPPORTED";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during generatePresignedUrl: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedLocalStorageReader::listBuckets(std::vector<std::string>& buckets)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // Local storage doesn't have buckets concept
        result.success = false;
        result.message = "Buckets are not supported for local storage";
        result.errorCode = "NOT_SUPPORTED";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listBuckets: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedLocalStorageReader::checkBucketExists(const std::string& bucket)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // Local storage doesn't have buckets concept
        result.success = false;
        result.message = "Buckets are not supported for local storage";
        result.errorCode = "NOT_SUPPORTED";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during checkBucketExists: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

ReaderStats UnifiedLocalStorageReader::getReaderStats() const
{
    return UnifiedStorageReader::getReaderStats();
}

void UnifiedLocalStorageReader::resetStats()
{
    UnifiedStorageReader::resetStats();
}

std::string UnifiedLocalStorageReader::getLastError() const
{
    return UnifiedStorageReader::getLastError();
}

FileResult UnifiedLocalStorageReader::performHealthCheck()
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!std::filesystem::exists(m_basePath))
        {
            result.success = false;
            result.message = "Base path does not exist: " + m_localConfig.basePath;
            result.errorCode = "BASE_PATH_NOT_FOUND";
        }
        else if (!std::filesystem::is_directory(m_basePath))
        {
            result.success = false;
            result.message = "Base path is not a directory: " + m_localConfig.basePath;
            result.errorCode = "BASE_PATH_NOT_DIRECTORY";
        }
        else
        {
            // Try to create a test file to check write permissions
            std::filesystem::path testFile = m_basePath / ".health_check_test";
            std::ofstream testStream(testFile);
            if (testStream.is_open())
            {
                testStream << "health_check" << std::endl;
                testStream.close();
                std::filesystem::remove(testFile);
                result.success = true;
                result.message = "Local storage is healthy";
            }
            else
            {
                result.success = false;
                result.message = "Cannot write to base path: " + m_localConfig.basePath;
                result.errorCode = "WRITE_PERMISSION_DENIED";
            }
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during health check: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

bool UnifiedLocalStorageReader::initializeStorage()
{
    try
    {
        if (!std::filesystem::exists(m_basePath))
        {
            std::filesystem::create_directories(m_basePath);
        }
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError(std::string("Failed to initialize storage: ") + e.what());
        return false;
    }
}

bool UnifiedLocalStorageReader::validateConfiguration(const StorageConfig& config)
{
    std::string basePath = config.getParameter(StorageConstants::BASE_PATH_KEY);
    if (basePath.empty())
    {
        setLastError("base_path parameter is required for local storage");
        return false;
    }

    return true;
}

FileInfo UnifiedLocalStorageReader::createFileInfoFromPath(const std::filesystem::path& path) const
{
    FileInfo fileInfo;
    fileInfo.path = path.string();
    fileInfo.name = path.filename().string();
    fileInfo.size = std::filesystem::is_regular_file(path) ? std::filesystem::file_size(path) : 0;
    fileInfo.isDirectory = std::filesystem::is_directory(path);
    fileInfo.contentType = getMimeType(path.string());
    fileInfo.lastModified = formatFileTime(std::filesystem::last_write_time(path));

    return fileInfo;
}

std::string UnifiedLocalStorageReader::getMimeType(const std::string& filePath) const
{
    std::filesystem::path path(filePath);
    std::string extension = path.extension().string();

    auto it = s_mimeTypes.find(extension);
    if (it != s_mimeTypes.end())
    {
        return it->second;
    }

    return "application/octet-stream"; // Default MIME type
}

std::string UnifiedLocalStorageReader::formatFileTime(const std::filesystem::file_time_type& time) const
{
    // Convert file_time_type to time_t using duration_cast
    auto duration = time.time_since_epoch();
    auto system_duration = std::chrono::duration_cast<std::chrono::system_clock::duration>(duration);
    auto system_time = std::chrono::system_clock::time_point(system_duration);
    auto time_t = std::chrono::system_clock::to_time_t(system_time);

    std::tm tm_buf{};
    gmtime_r(&time_t, &tm_buf);
    std::stringstream ss;
    ss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%SZ");
    return ss.str();
}

bool UnifiedLocalStorageReader::shouldIncludeFile(const std::filesystem::path& path) const
{
    std::string fileName = path.filename().string();

    // Skip hidden files unless explicitly included
    if (!m_localConfig.includeHidden && fileName[0] == '.')
    {
        return false;
    }

    // Skip if not readable
    if (!std::filesystem::exists(path) || !std::filesystem::is_regular_file(path))
    {
        return false;
    }

    return true;
}

std::filesystem::path UnifiedLocalStorageReader::normalizePath(const std::string& path) const
{
    std::filesystem::path filePath(path);

    // If path is relative, make it relative to base path
    if (filePath.is_relative())
    {
        return m_basePath / filePath;
    }

    return filePath;
}

} // namespace nv_vms
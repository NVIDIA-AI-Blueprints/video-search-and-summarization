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

#include "unified_storage_reader.h"
#include "logger.h"
#include <algorithm>
#include <chrono>
#include <exception>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <sstream>

namespace nv_vms
{

UnifiedStorageReader::UnifiedStorageReader(StorageType type)
    : m_storageMode(type), m_config(), m_stats(), m_last_error()
{
}

UnifiedStorageReader::~UnifiedStorageReader()
{
    try
    {
        LOG(info) << "UnifiedStorageReader destructor called - cleaning up storage reader" << std::endl;

        // Cancel all async downloads if this is a cloud reader
        if (m_storageMode == StorageType::CLOUD && m_cloudReader)
        {
            m_cloudReader->cancelAllAsyncDownloads();
        }

        // Reset the cloud reader
        m_cloudReader.reset();

        LOG(info) << "UnifiedStorageReader destructor completed" << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during UnifiedStorageReader destruction: " << e.what() << std::endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during UnifiedStorageReader destruction" << std::endl;
    }
}

bool UnifiedStorageReader::isAvailable() const
{
    return true; // Base implementation - derived classes should override
}

std::string UnifiedStorageReader::getStorageMode() const
{
    switch (m_storageMode)
    {
        case StorageType::LOCAL:
            return StorageConstants::LOCAL_STORAGE;
        case StorageType::CLOUD:
            return StorageConstants::CLOUD_STORAGE;
        default:
            return "unknown";
    }
}

StorageType UnifiedStorageReader::getStorageType() const
{
    return m_storageMode;
}

bool UnifiedStorageReader::configureStorage(const StorageConfig& config)
{
    std::lock_guard<std::mutex> lock(m_config_mutex);

    if (!validateConfiguration(config))
    {
        LOG(error) << "Invalid configuration provided" << std::endl;
        setLastError("Invalid configuration provided");
        return false;
    }

    m_config = config;

    if (!initializeStorage())
    {
        LOG(error) << "Failed to initialize storage" << std::endl;
        setLastError("Failed to initialize storage");
        return false;
    }

    return true;
}

StorageConfig UnifiedStorageReader::getStorageConfiguration() const
{
    std::lock_guard<std::mutex> lock(m_config_mutex);
    return m_config;
}

FileResult UnifiedStorageReader::downloadFile(const std::string& remote_path, const std::string& local_path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "downloadFile not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileResult UnifiedStorageReader::getFileInfo(const std::string& path, FileInfo& fileInfo)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "getFileInfo not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileResult UnifiedStorageReader::checkFileExists(const std::string& path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "checkFileExists not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileListResult UnifiedStorageReader::listFiles(const std::string& path, const std::string& prefix)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "listFiles not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileListResult UnifiedStorageReader::listFilesPaginated(const std::string& path, const std::string& prefix,
                                                        const std::string& marker, uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "listFilesPaginated not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileResult UnifiedStorageReader::generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                                      std::string& url)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "generatePresignedUrl not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileResult UnifiedStorageReader::getPresignedUrl(const std::string& path, std::string& url)
{
    // Default implementation: forward to generatePresignedUrl with 12-hour expiration
    // Subclasses (like UnifiedCloudStorageReader) can override with caching logic
    return generatePresignedUrl(path, 12 * 60 * 60, url);
}

FileResult UnifiedStorageReader::listBuckets(std::vector<std::string>& buckets)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "listBuckets not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

FileResult UnifiedStorageReader::checkBucketExists(const std::string& bucket)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "checkBucketExists not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

CloudListResult UnifiedStorageReader::listCloudObjects(const std::string& bucketName,
                                                      const std::string& prefix,
                                                      uint32_t maxKeys)
{
    CloudListResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "listCloudObjects not implemented for this storage type (local storage)";
        result.errorCode = "NOT_IMPLEMENTED";
        result.bucket = bucketName;
        result.prefix = prefix;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listCloudObjects: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudListResult UnifiedStorageReader::listAllCloudObjects(const std::string& bucketName,
                                                         const std::string& prefix)
{
    CloudListResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "listAllCloudObjects not implemented for this storage type (local storage)";
        result.errorCode = "NOT_IMPLEMENTED";
        result.bucket = bucketName;
        result.prefix = prefix;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listAllCloudObjects: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

std::string UnifiedStorageReader::getBucketName() const
{
    std::lock_guard<std::mutex> lock(m_config_mutex);
    return m_config.getParameter(StorageConstants::BUCKET_NAME_KEY);
}

// Async download operations - base implementations
std::string UnifiedStorageReader::downloadFilesWithPathsAsync(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                                             DownloadCompletionCallback completionCallback,
                                                             DownloadProgressCallback progressCallback)
{
    // Base implementation - derived classes should override
    setLastError("downloadFilesWithPathsAsync not implemented for this storage type");
    return "";
}

std::string UnifiedStorageReader::downloadMultipleFilesAsync(const std::string& remote_directory,
                                                            const std::vector<std::string>& file_paths,
                                                            const std::string& local_directory,
                                                            DownloadCompletionCallback completionCallback,
                                                            DownloadProgressCallback progressCallback)
{
    // Base implementation - derived classes should override
    setLastError("downloadMultipleFilesAsync not implemented for this storage type");
    return "";
}

bool UnifiedStorageReader::cancelAsyncDownload(const std::string& sessionId)
{
    // Base implementation - derived classes should override
    setLastError("cancelAsyncDownload not implemented for this storage type");
    return false;
}

bool UnifiedStorageReader::cancelAllAsyncDownloads()
{
    // Base implementation - derived classes should override
    setLastError("cancelAllAsyncDownloads not implemented for this storage type");
    return false;
}

std::vector<std::string> UnifiedStorageReader::getActiveAsyncDownloads() const
{
    // Base implementation - derived classes should override
    return {};
}

bool UnifiedStorageReader::isAsyncDownloadCompleted(const std::string& sessionId) const
{
    // Base implementation - derived classes should override
    return false;
}

DownloadResult UnifiedStorageReader::getAsyncDownloadResult(const std::string& sessionId) const
{
    // Base implementation - derived classes should override
    DownloadResult result;
    result.overall_success = false;
    result.error_message = "getAsyncDownloadResult not implemented for this storage type";
    return result;
}

ReaderStats UnifiedStorageReader::getReaderStats() const
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    return m_stats;
}

void UnifiedStorageReader::resetStats()
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats = ReaderStats();
}

std::string UnifiedStorageReader::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    return m_last_error;
}

FileResult UnifiedStorageReader::performHealthCheck()
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        // This is a base implementation - derived classes should override
        result.success = false;
        result.message = "performHealthCheck not implemented for this storage type";
        result.errorCode = "NOT_IMPLEMENTED";
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

void UnifiedStorageReader::setLastError(const std::string& error)
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error = error;
}

void UnifiedStorageReader::clearLastError()
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error.clear();
}

void UnifiedStorageReader::updateStats(bool success, std::chrono::milliseconds latency, const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats.recordRequest(success, latency, errorCode);
}

// Multi-file download operations implementation
DownloadResult UnifiedStorageReader::downloadMultipleFiles(const std::string& remote_directory,
                                                          const std::vector<std::string>& file_paths,
                                                          const std::string& local_directory,
                                                          DownloadProgressCallback progressCallback)
{
    DownloadResult result;
    auto startTime = std::chrono::steady_clock::now();

    // Validate inputs
    if (file_paths.empty())
    {
        result.overall_success = false;
        result.error_message = "No files provided for download";
        result.error_code = "NO_FILES";
        return result;
    }

    if (local_directory.empty())
    {
        result.overall_success = false;
        result.error_message = "Local directory path is required";
        result.error_code = "INVALID_LOCAL_DIR";
        return result;
    }

    // Create directory if it doesn't exist
    try
    {
        std::filesystem::create_directories(local_directory);
    }
    catch (const std::exception& e)
    {
        result.overall_success = false;
        result.error_message = "Failed to create local directory: " + std::string(e.what());
        result.error_code = "DIRECTORY_CREATION_FAILED";
        return result;
    }

    // Create remote-local path pairs
    std::vector<std::pair<std::string, std::string>> remoteLocalPairs;
    for (const auto& file_path : file_paths)
    {
        std::string remote_path = remote_directory + "/" + file_path;
        std::string local_path = local_directory + "/" + file_path;

        // Ensure local directory structure exists
        std::filesystem::path localPathObj(local_path);
        try
        {
            std::filesystem::create_directories(localPathObj.parent_path());
        }
        catch (const std::exception& e)
        {
            std::cout << "Warning: Failed to create directory for: " << local_path << std::endl;
            continue;
        }

        remoteLocalPairs.emplace_back(remote_path, local_path);
    }

    // Perform the downloads
    result = downloadFilesWithPaths(remoteLocalPairs, progressCallback);

    auto endTime = std::chrono::steady_clock::now();
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);

    return result;
}

DownloadResult UnifiedStorageReader::downloadFilesWithPaths(
    const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs, DownloadProgressCallback progressCallback)
{
    DownloadResult result;
    auto startTime = std::chrono::steady_clock::now();

    if (remoteLocalPairs.empty())
    {
        result.overall_success = false;
        result.error_message = "No download tasks provided";
        result.error_code = "NO_TASKS";
        return result;
    }

    // Check if we have a cloud reader for cloud storage
    if (m_storageMode == StorageType::CLOUD && m_cloudReader)
    {
        // For cloud storage, we need to extract bucket and object keys from paths
        // The remote paths can be in format "bucket/object-key" or just "object-key"
        std::string defaultBucket = m_config.getParameter(StorageConstants::BUCKET_NAME_KEY, "default-bucket");
        std::vector<std::pair<std::string, std::string>> objectKeyPathPairs;
        std::string bucket = defaultBucket; // Use default bucket for all downloads

        for (const auto& pair : remoteLocalPairs)
        {
            const std::string& remote_path = pair.first;
            const std::string& local_path = pair.second;

            // Extract object key from remote path
            std::string objectKey = remote_path;

            objectKeyPathPairs.emplace_back(objectKey, local_path);
        }

        // Use cloud reader's multi-download functionality
        MultiDownloadResult cloudResult = m_cloudReader->downloadObjectsWithPaths(
            bucket, objectKeyPathPairs,
            [progressCallback](const std::string& objectKey, uint64_t bytesDownloaded, uint64_t totalBytes,
                               double speed)
            {
                if (progressCallback)
                {
                    progressCallback(objectKey, bytesDownloaded, totalBytes, speed);
                }
            });

        // Convert cloud result to unified result
        result.overall_success = cloudResult.overall_success;
        result.error_message = cloudResult.error_message;
        result.error_code = cloudResult.error_code;
        result.total_bytes_downloaded = cloudResult.total_bytes_downloaded;
        result.successful_downloads = cloudResult.successful_downloads;
        result.failed_downloads = cloudResult.failed_downloads;
        result.average_speed = cloudResult.average_speed;

        // Convert individual results
        for (const auto& cloudDownloadResult : cloudResult.file_results)
        {
            FileDownloadResult downloadResult;
            downloadResult.remote_path = cloudDownloadResult.remote_path;
            downloadResult.local_path = cloudDownloadResult.local_path;
            downloadResult.success = cloudDownloadResult.success;
            downloadResult.error_message = cloudDownloadResult.error_message;
            downloadResult.error_code = cloudDownloadResult.error_code;
            downloadResult.bytes_downloaded = cloudDownloadResult.bytes_downloaded;
            downloadResult.duration = cloudDownloadResult.duration;
            result.file_results.push_back(downloadResult);
        }
    }
    else
    {
        // For local storage or when cloud reader is not available, use sequential downloads
        for (const auto& pair : remoteLocalPairs)
        {
            const std::string& remote_path = pair.first;
            const std::string& local_path = pair.second;

            auto fileStartTime = std::chrono::steady_clock::now();

            FileResult fileResult = downloadFile(remote_path, local_path);

            auto fileEndTime = std::chrono::steady_clock::now();
            fileResult.duration = std::chrono::duration_cast<std::chrono::milliseconds>(fileEndTime - fileStartTime);

            // Convert FileResult to FileDownloadResult
            FileDownloadResult downloadResult;
            downloadResult.remote_path = remote_path;
            downloadResult.local_path = local_path;
            downloadResult.success = fileResult.success;
            downloadResult.error_message = fileResult.message;
            downloadResult.error_code = fileResult.errorCode;
            downloadResult.bytes_downloaded = fileResult.bytes_read;
            downloadResult.duration = fileResult.duration;
            result.file_results.push_back(downloadResult);

            if (fileResult.success)
            {
                result.successful_downloads++;
                result.total_bytes_downloaded += fileResult.bytes_read;

                // Calculate download speed for progress callback
                double downloadSpeed = 0.0;
                if (fileResult.duration.count() > 0)
                {
                    downloadSpeed = (fileResult.bytes_read / (1024.0 * 1024.0)) / (fileResult.duration.count() / 1000.0); // MB/s
                }

                // Call progress callback
                if (progressCallback)
                {
                    progressCallback(remote_path, fileResult.bytes_read, fileResult.bytes_read, downloadSpeed);
                }
            }
            else
            {
                result.failed_downloads++;
            }
        }

        result.overall_success = (result.failed_downloads == 0);
        result.error_message = "Downloaded " + std::to_string(result.successful_downloads) + " files, " +
                               std::to_string(result.failed_downloads) + " failed";
    }

    auto endTime = std::chrono::steady_clock::now();
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);

    // Calculate average speed
    if (result.total_duration.count() > 0)
    {
        double durationSeconds = result.total_duration.count() / 1000.0;
        result.average_speed = (result.total_bytes_downloaded / (1024.0 * 1024.0)) / durationSeconds; // MB/s
    }

    return result;
}

bool UnifiedStorageReader::cancelDownload(const std::string& filePath)
{
    if (m_storageMode == StorageType::CLOUD && m_cloudReader)
    {
        // Extract object key from file path
        std::string objectKey = filePath;
        std::string defaultBucket = m_config.getParameter(StorageConstants::BUCKET_NAME_KEY, "default-bucket");

        return m_cloudReader->cancelDownload(objectKey);
    }

    // For local storage, cancellation is not supported in this implementation
    return false;
}

bool UnifiedStorageReader::cancelAllDownloads()
{
    if (m_storageMode == StorageType::CLOUD && m_cloudReader)
    {
        return m_cloudReader->cancelAllDownloads();
    }

    // For local storage, cancellation is not supported in this implementation
    return false;
}

std::vector<std::string> UnifiedStorageReader::getActiveDownloads() const
{
    if (m_storageMode == StorageType::CLOUD && m_cloudReader)
    {
        std::vector<std::string> activeDownloads;
        std::vector<std::string> cloudActiveDownloads = m_cloudReader->getActiveDownloads();

        // Convert object keys back to full paths
        std::string bucket = m_config.getParameter(StorageConstants::BUCKET_NAME_KEY, "default-bucket");
        for (const auto& objectKey : cloudActiveDownloads)
        {
            activeDownloads.push_back(bucket + "/" + objectKey);
        }

        return activeDownloads;
    }

    // For local storage, no active downloads tracking in this implementation
    return std::vector<std::string>();
}

} // namespace nv_vms
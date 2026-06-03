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
#include "../unified_storage_types.h"
#include <atomic>
#include <memory>
#include <mutex>

namespace nv_vms
{

// Forward declarations
class CloudReader;

/**
 * @brief Unified storage reader that handles both local and cloud storage
 */
class UnifiedStorageReader
{
public:
    UnifiedStorageReader(StorageType type);
    virtual ~UnifiedStorageReader();

    // Core interface
    virtual bool isAvailable() const;
    virtual std::string getStorageMode() const;
    StorageType getStorageType() const;

    // Configuration
    virtual bool configureStorage(const StorageConfig& config);
    virtual StorageConfig getStorageConfiguration() const;

    // File operations (unified interface)
    virtual FileResult downloadFile(const std::string& remote_path, const std::string& local_path);
    virtual FileResult getFileInfo(const std::string& path, FileInfo& fileInfo);
    virtual FileResult checkFileExists(const std::string& path);

    // Multi-file download operations
    virtual DownloadResult downloadMultipleFiles(const std::string& remote_directory,
                                                const std::vector<std::string>& file_paths,
                                                const std::string& local_directory,
                                                DownloadProgressCallback progressCallback = nullptr);

    virtual DownloadResult downloadFilesWithPaths(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                                 DownloadProgressCallback progressCallback = nullptr);

    // Async download operations
    virtual std::string downloadFilesWithPathsAsync(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                                   DownloadCompletionCallback completionCallback = nullptr,
                                                   DownloadProgressCallback progressCallback = nullptr);

    virtual std::string downloadMultipleFilesAsync(const std::string& remote_directory,
                                                  const std::vector<std::string>& file_paths,
                                                  const std::string& local_directory,
                                                  DownloadCompletionCallback completionCallback = nullptr,
                                                  DownloadProgressCallback progressCallback = nullptr);

    virtual bool cancelAsyncDownload(const std::string& sessionId);
    virtual bool cancelAllAsyncDownloads();
    virtual std::vector<std::string> getActiveAsyncDownloads() const;
    virtual bool isAsyncDownloadCompleted(const std::string& sessionId) const;
    virtual DownloadResult getAsyncDownloadResult(const std::string& sessionId) const;

    virtual bool cancelDownload(const std::string& filePath);
    virtual bool cancelAllDownloads();
    virtual std::vector<std::string> getActiveDownloads() const;

    // Directory operations
    virtual FileListResult listFiles(const std::string& path, const std::string& prefix = "");
    virtual FileListResult listFilesPaginated(const std::string& path, const std::string& prefix = "",
                                     const std::string& marker = "", uint32_t maxKeys = 1000);

    // Cloud-specific operations (only available for cloud storage)
    virtual FileResult generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                   std::string& url);

    /**
     * @brief Get presigned URL with caching support (cloud storage only)
     * @param path Object path
     * @param url Output presigned URL
     * @return FileResult indicating success/failure
     */
    virtual FileResult getPresignedUrl(const std::string& path, std::string& url);

    virtual FileResult listBuckets(std::vector<std::string>& buckets);
    virtual FileResult checkBucketExists(const std::string& bucket);

    /**
     * @brief List objects from cloud storage (cloud-specific API)
     * @param bucketName The S3 bucket name
     * @param prefix Optional prefix to filter objects
     * @param maxKeys Maximum number of objects to return (default: 1000)
     * @return CloudListResult containing list of objects and metadata
     *
     * Note: This is a cloud-specific method. For local storage, it returns an error result.
     */
    virtual CloudListResult listCloudObjects(const std::string& bucketName,
                                            const std::string& prefix = "",
                                            uint32_t maxKeys = 1000);

    /**
     * @brief List all objects from cloud storage (handles pagination automatically)
     * @param bucketName The S3 bucket name
     * @param prefix Optional prefix to filter objects
     * @return CloudListResult containing all objects and metadata
     *
     * Note: This is a cloud-specific method. For local storage, it returns an error result.
     */
    virtual CloudListResult listAllCloudObjects(const std::string& bucketName,
                                               const std::string& prefix = "");

    // Get configured bucket name (for cloud storage)
    virtual std::string getBucketName() const;

    // Statistics and monitoring
    virtual ReaderStats getReaderStats() const;
    virtual void resetStats();

    // Error handling
    virtual std::string getLastError() const;

    // Health and diagnostics
    virtual FileResult performHealthCheck();

protected:
    // Storage mode
    StorageType m_storageMode;

    // Configuration
    StorageConfig m_config;
    mutable std::mutex m_config_mutex;

    // Statistics
    mutable std::mutex m_stats_mutex;
    ReaderStats m_stats;

    // Error handling
    mutable std::mutex m_error_mutex;
    std::string m_last_error;

    // Storage readers
    std::shared_ptr<CloudReader> m_cloudReader = nullptr;

    // Helper methods
    void setLastError(const std::string& error);
    void clearLastError();
    void updateStats(bool success, std::chrono::milliseconds latency,
                    const std::string& errorCode = "");

    // Abstract methods for specific storage implementations
    virtual bool initializeStorage() = 0;
    virtual bool validateConfiguration(const StorageConfig& config) = 0;
};

} // namespace nv_vms
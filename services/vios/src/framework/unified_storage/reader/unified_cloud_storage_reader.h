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
#include "unified_storage_reader.h"
#include <filesystem>
#include <memory>
#include <unordered_map>
#include <chrono>
#include <mutex>

namespace nv_vms
{

/**
 * @brief Concrete implementation of unified storage reader for cloud storage
 */
class UnifiedCloudStorageReader : public UnifiedStorageReader
{
public:
    UnifiedCloudStorageReader();
    virtual ~UnifiedCloudStorageReader();

    // Core interface
    bool isAvailable() const override;
    std::string getStorageMode() const override;

    // Configuration
    bool configureStorage(const StorageConfig& config) override;
    StorageConfig getStorageConfiguration() const override;

    // File operations (unified interface)
    FileResult downloadFile(const std::string& remote_path, const std::string& local_path) override;
    FileResult getFileInfo(const std::string& path, FileInfo& fileInfo) override;
    FileResult checkFileExists(const std::string& path) override;

    // Directory operations
    FileListResult listFiles(const std::string& path, const std::string& prefix = "") override;
    FileListResult listFilesPaginated(const std::string& path, const std::string& prefix = "",
                                     const std::string& marker = "", uint32_t maxKeys = 1000) override;

    // Cloud-specific operations
    FileResult generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                   std::string& url) override;

    /**
     * @brief Get presigned URL with caching support
     *
     * First checks if a valid cached URL exists for the object.
     * If found and not expired, returns the cached URL.
     * Otherwise, generates a new presigned URL with 12-hour expiration and caches it.
     *
     * @param path Object path
     * @param url Output presigned URL
     * @return FileResult indicating success/failure
     */
    FileResult getPresignedUrl(const std::string& path, std::string& url);

    FileResult listBuckets(std::vector<std::string>& buckets) override;
    FileResult checkBucketExists(const std::string& bucket) override;

    /**
     * @brief List objects from cloud storage
     * @param bucketName The S3 bucket name
     * @param prefix Optional prefix to filter objects
     * @param maxKeys Maximum number of objects to return (default: 1000)
     * @return CloudListResult containing list of objects and metadata
     */
    CloudListResult listCloudObjects(const std::string& bucketName,
                                    const std::string& prefix = "",
                                    uint32_t maxKeys = 1000) override;

    /**
     * @brief List all objects from cloud storage (handles pagination automatically)
     * @param bucketName The S3 bucket name
     * @param prefix Optional prefix to filter objects
     * @return CloudListResult containing all objects and metadata
     */
    CloudListResult listAllCloudObjects(const std::string& bucketName,
                                       const std::string& prefix = "");

    // Statistics and monitoring
    ReaderStats getReaderStats() const override;
    void resetStats() override;

    // Error handling
    std::string getLastError() const override;

    // Health and diagnostics
    FileResult performHealthCheck() override;

    // Direct access to underlying cloud reader
    std::shared_ptr<CloudReader> getCloudReader() const;

    // Async download operations implementation
    std::string downloadFilesWithPathsAsync(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                           DownloadCompletionCallback completionCallback = nullptr,
                                           DownloadProgressCallback progressCallback = nullptr) override;

    std::string downloadMultipleFilesAsync(const std::string& remote_directory,
                                          const std::vector<std::string>& file_paths,
                                          const std::string& local_directory,
                                          DownloadCompletionCallback completionCallback = nullptr,
                                          DownloadProgressCallback progressCallback = nullptr) override;

    bool cancelAsyncDownload(const std::string& sessionId) override;
    bool cancelAllAsyncDownloads() override;
    std::vector<std::string> getActiveAsyncDownloads() const override;
    bool isAsyncDownloadCompleted(const std::string& sessionId) const override;
    DownloadResult getAsyncDownloadResult(const std::string& sessionId) const override;

protected:
    // Abstract methods implementation
    bool initializeStorage() override;
    bool validateConfiguration(const StorageConfig& config) override;

private:
    // Cloud configuration
    CloudReaderConfig m_cloudConfig{};

    // Presigned URL cache
    struct CachedPresignedUrl {
        std::string url;
        std::chrono::system_clock::time_point expiryTime;
    };
    std::unordered_map<std::string, CachedPresignedUrl> m_presignedUrlCache;
    mutable std::mutex m_cacheMutex;

    // Helper methods
    bool initializeCloudReader();
    CloudReaderConfig convertStorageConfigToCloudConfig(const StorageConfig& config) const;
    StorageConfig convertCloudConfigToStorageConfig(const CloudReaderConfig& config) const;
    std::string getCloudStorageTypeFromConfig(const StorageConfig& config) const;

    // Utility functions
    FileInfo convertCloudObjectToFileInfo(const CloudObject& cloudObj) const;
    FileListResult convertCloudListResultToFileListResult(const CloudListResult& cloudResult) const;
    FileResult convertCloudResultToFileResult(const CloudResult& cloudResult) const;
    std::string extractBucketFromPath(const std::string& path) const;
    std::string extractObjectKeyFromPath(const std::string& path) const;
    std::string buildPathFromBucketAndKey(const std::string& bucket, const std::string& key) const;
    std::string cloudStorageTypeToString(CloudStorageType type) const;
    CloudStorageType stringToCloudStorageType(const std::string& type) const;
};

} // namespace nv_vms
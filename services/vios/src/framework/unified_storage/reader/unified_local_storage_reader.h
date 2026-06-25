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

#include "unified_storage_reader.h"
#include <filesystem>
#include <fstream>
#include <memory>
#include <map>
#include <vector>
#include <chrono>
#include <mutex>

namespace nv_vms
{

/**
 * @brief Configuration for local storage reader
 */
struct LocalReaderConfig {
    std::string basePath;               // Base directory path
    bool recursiveListing = false;      // Enable recursive directory listing
    bool followSymlinks = true;         // Follow symbolic links
    uint32_t maxDepth = 10;             // Maximum directory depth for recursive listing
    bool includeHidden = false;         // Include hidden files/directories
    uint32_t timeoutSeconds = 30;       // Operation timeout
    
    // File filtering
    struct FilterConfig {
        std::vector<std::string> includeExtensions;  // File extensions to include
        std::vector<std::string> excludeExtensions;  // File extensions to exclude
        std::vector<std::string> includePatterns;    // File name patterns to include
        std::vector<std::string> excludePatterns;    // File name patterns to exclude
        uint64_t minFileSize = 0;                    // Minimum file size
        uint64_t maxFileSize = UINT64_MAX;           // Maximum file size
    } filter;
    
    // Performance settings
    struct PerformanceConfig {
        uint32_t bufferSize = 8192;     // Read buffer size
        bool enableCaching = true;      // Enable file info caching
        uint32_t cacheTimeoutSec = 300; // Cache timeout in seconds
        uint32_t maxConcurrentReads = 4; // Maximum concurrent file reads
    } performance;
};

/**
 * @brief Concrete implementation of unified storage reader for local file system
 */
class UnifiedLocalStorageReader : public UnifiedStorageReader
{
public:
    UnifiedLocalStorageReader();
    virtual ~UnifiedLocalStorageReader();

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
    
    // Cloud-specific operations (not available for local storage)
    FileResult generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                   std::string& url) override;
    FileResult listBuckets(std::vector<std::string>& buckets) override;
    FileResult checkBucketExists(const std::string& bucket) override;

    // Statistics and monitoring
    ReaderStats getReaderStats() const override;
    void resetStats() override;

    // Error handling
    std::string getLastError() const override;

    // Health and diagnostics
    FileResult performHealthCheck() override;

protected:
    // Abstract methods implementation
    bool initializeStorage() override;
    bool validateConfiguration(const StorageConfig& config) override;

private:
    // Local configuration
    LocalReaderConfig m_localConfig;
    
    // Base path for local storage
    std::filesystem::path m_basePath;
    
    // Static MIME type mapping
    static const std::map<std::string, std::string, std::less<>> s_mimeTypes;
    
    // Helper methods
    bool initializeLocalReader();
    FileResult copyFile(const std::string& source, const std::string& destination);
    FileInfo getFileInfoFromPath(const std::filesystem::path& path) const;
    FileInfo createFileInfoFromPath(const std::filesystem::path& path) const;
    std::string getMimeType(const std::string& filePath) const;
    bool matchesPrefix(const std::string& fileName, const std::string& prefix) const;
    std::vector<FileInfo> filterFiles(const std::vector<FileInfo>& files, 
                                     const std::string& prefix, 
                                     const std::string& marker, 
                                     uint32_t maxKeys) const;
    
    // Utility functions
    std::string formatFileTime(const std::filesystem::file_time_type& time) const;
    bool shouldIncludeFile(const std::filesystem::path& path) const;
    std::filesystem::path normalizePath(const std::string& path) const;
};

} // namespace nv_vms 
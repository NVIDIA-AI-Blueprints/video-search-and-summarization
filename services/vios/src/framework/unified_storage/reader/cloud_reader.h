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

#include <string>
#include <memory>
#include <vector>
#include <functional>
#include <chrono>
#include <map>
#include <mutex>
#include <cstdint>
#include <thread>
#include <queue>
#include <atomic>
#include <condition_variable>
#include <jsoncpp/json/json.h>
#include "../unified_storage_types.h"

namespace nv_vms
{

/**
 * @brief Configuration for cloud reader
 */
struct CloudReaderConfig
{
    CloudStorageType storageType = CloudStorageType::UNKNOWN;
    std::string endpoint;
    std::string region;
    std::string accessKeyId;
    std::string secretAccessKey;
    std::string sessionToken;           // For temporary credentials
    bool useSSL = true;
    uint32_t timeoutSeconds = 30;
    uint32_t maxRetries = 3;
    
    // Authentication settings
    struct AuthConfig
    {
        bool useDefaultCredentials = false;
        bool useInstanceProfile = false;
        std::string credentialsFile;
        std::string profileName = "default";
    } auth;
    
    // Request settings
    struct RequestConfig
    {
        uint32_t maxKeys = 1000;        // Maximum objects per list request
        bool fetchMetadata = false;     // Fetch object metadata
        bool enableCache = true;        // Enable response caching
        uint32_t cacheTimeoutSec = 300; // Cache timeout in seconds
    } request;
    
    // Download worker settings
    struct DownloadWorkerConfig
    {
        uint32_t maxWorkerThreads = 1;      // Maximum number of worker threads
        uint32_t maxConcurrentDownloads = 1; // Maximum concurrent downloads per thread
        uint32_t downloadTimeoutSeconds = 300; // Timeout for individual downloads
        bool enableResumeDownload = true;   // Enable resume for interrupted downloads
        uint32_t chunkSizeBytes = 8 * 1024 * 1024; // 8MB chunks for large files
        bool enableProgressCallback = true; // Enable progress callbacks
    } downloadWorker;
};

/**
 * @brief Download task structure for worker threads
 */
struct DownloadTask
{
    std::string bucket;
    std::string objectKey;
    std::string localPath;
    uint64_t expectedSize = 0;
    std::string etag;
    std::chrono::system_clock::time_point createdAt;
    uint32_t priority = 0; // Higher number = higher priority
    
    DownloadTask() = default;
    DownloadTask(const std::string& b, const std::string& key, const std::string& path)
        : bucket(b), objectKey(key), localPath(path), createdAt(std::chrono::system_clock::now())
    {
    }
};

// Using unified DownloadResult from unified_storage_types.h

/**
 * @brief Multi-download result structure (using unified DownloadResult)
 */
using MultiDownloadResult = DownloadResult;

/**
 * @brief Progress callback function type
 */
using DownloadProgressCallback = std::function<void(const std::string& objectKey, 
                                                   uint64_t bytesDownloaded, 
                                                   uint64_t totalBytes, 
                                                   double speed)>;

/**
 * @brief Completion callback function type for async downloads
 */
using DownloadCompletionCallback = std::function<void(const std::string& sessionId, 
                                                     const DownloadResult& result)>;

/**
 * @brief Asynchronous download session structure
 */
struct AsyncDownloadSession
{
    std::string sessionId;
    std::vector<DownloadTask> tasks;
    std::vector<FileDownloadResult> results;
    std::chrono::system_clock::time_point startTime;
    std::atomic<uint32_t> completedTasks;
    std::atomic<uint32_t> successfulDownloads;
    std::atomic<uint32_t> failedDownloads;
    std::atomic<uint64_t> totalBytesDownloaded;
    DownloadCompletionCallback completionCallback;
    DownloadProgressCallback progressCallback;
    std::atomic<bool> isCompleted;
    std::mutex completion_mutex;
    std::condition_variable completion_cv;
    std::mutex results_mutex;  // Protect results vector from concurrent access
    
    AsyncDownloadSession() : completedTasks(0), successfulDownloads(0), failedDownloads(0), 
                            totalBytesDownloaded(0), isCompleted(false)
    {
    }
    
    AsyncDownloadSession(const std::string& id) : sessionId(id), startTime(std::chrono::system_clock::now()),
                                                 completedTasks(0), successfulDownloads(0), failedDownloads(0),
                                                 totalBytesDownloaded(0), isCompleted(false) {}
};

/**
 * @brief Statistics for cloud reader operations
 */
struct CloudReaderStats
{
    uint64_t totalRequests = 0;
    uint64_t successfulRequests = 0;
    uint64_t failedRequests = 0;
    uint64_t bytesRead = 0;
    uint64_t objectsListed = 0;
    std::chrono::milliseconds totalLatency{0};
    std::chrono::milliseconds averageLatency{0};
    std::chrono::system_clock::time_point lastRequestTime;
    
    // Error tracking
    std::map<std::string, uint32_t, std::less<>> errorCounts;
    
    void recordRequest(bool success, std::chrono::milliseconds latency, 
                      const std::string& errorCode = "")
    {
        totalRequests++;
        totalLatency += latency;
        lastRequestTime = std::chrono::system_clock::now();
        
        if (success)
        {
            successfulRequests++;
        }
        else
        {
            failedRequests++;
            if (!errorCode.empty())
            {
                errorCounts[errorCode]++;
            }
        }
        
        if (totalRequests > 0)
        {
            averageLatency = totalLatency / totalRequests;
        }
    }
};

/**
 * @brief Abstract base class for cloud storage readers
 */
class CloudReader
{
public:
    virtual ~CloudReader();
    
    // Core interface
    virtual bool isAvailable() const = 0;
    virtual std::string getStorageTypeName() const = 0;
    virtual CloudStorageType getStorageType() const = 0;
    
    // Configuration
    virtual bool configure(const CloudReaderConfig& config) = 0;
    virtual CloudReaderConfig getConfiguration() const = 0;
    
    // Object listing operations
    virtual CloudListResult listObjects(const std::string& bucket, 
                                       const std::string& prefix = "",
                                       uint32_t maxKeys = 1000) = 0;
    
    virtual CloudListResult listObjectsPaginated(const std::string& bucket,
                                                const std::string& prefix = "",
                                                const std::string& marker = "",
                                                uint32_t maxKeys = 1000) = 0;
    
    virtual CloudListResult listAllObjects(const std::string& bucket,
                                          const std::string& prefix = "") = 0;

    // Object operations
    virtual CloudResult downloadObject(const std::string& bucket,
                                     const std::string& objectKey,
                                     const std::string& localPath) = 0;
    
    virtual CloudResult getObjectInfo(const std::string& bucket,
                                    const std::string& objectKey,
                                    CloudObject& objectInfo) = 0;
    
    virtual CloudResult checkObjectExists(const std::string& bucket,
                                         const std::string& objectKey) = 0;
    
    // Multi-file download operations (implemented in base class)
    virtual DownloadResult downloadMultipleObjects(const std::string& bucket,
                                                  const std::vector<std::string>& objectKeys,
                                                  const std::string& localDirectory,
                                                  DownloadProgressCallback progressCallback = nullptr);
    
    virtual DownloadResult downloadObjectsWithPaths(const std::string& bucket,
                                                   const std::vector<std::pair<std::string, std::string>>& objectKeyPathPairs,
                                                   DownloadProgressCallback progressCallback = nullptr);
    
    // Asynchronous multi-file download operations
    virtual std::string downloadObjectsWithPathsAsync(const std::string& bucket,
                                                     const std::vector<std::pair<std::string, std::string>>& objectKeyPathPairs,
                                                     DownloadCompletionCallback completionCallback = nullptr,
                                                     DownloadProgressCallback progressCallback = nullptr);
    
    virtual std::string downloadMultipleObjectsAsync(const std::string& bucket,
                                                    const std::vector<std::string>& objectKeys,
                                                    const std::string& localDirectory,
                                                    DownloadCompletionCallback completionCallback = nullptr,
                                                    DownloadProgressCallback progressCallback = nullptr);
    
    // Async download session management
    virtual bool cancelAsyncDownload(const std::string& sessionId);
    virtual bool cancelAllAsyncDownloads();
    virtual std::vector<std::string> getActiveAsyncDownloads() const;
    virtual DownloadResult getAsyncDownloadResult(const std::string& sessionId) const;
    virtual bool isAsyncDownloadCompleted(const std::string& sessionId) const;
    virtual bool waitForAsyncDownloadCompletion(const std::string& sessionId, 
                                               std::chrono::milliseconds timeout = std::chrono::milliseconds(30000)) const;
    
    virtual bool cancelDownload(const std::string& objectKey);
    virtual bool cancelAllDownloads();
    virtual std::vector<std::string> getActiveDownloads() const;
    
    // Bucket operations  
    virtual CloudResult listBuckets(std::vector<std::string>& buckets) = 0;
    virtual CloudResult checkBucketExists(const std::string& bucket) = 0;
    
    // URL generation (for pre-signed URLs)
    virtual CloudResult generatePresignedUrl(const std::string& bucket,
                                           const std::string& objectKey,
                                           uint32_t expirationSeconds,
                                           std::string& url) = 0;
    
    // Statistics and monitoring
    virtual CloudReaderStats getStats() const = 0;
    virtual void resetStats() = 0;
    
    // Health and diagnostics
    virtual CloudResult performHealthCheck() = 0;
    virtual std::string getLastError() const = 0;
    
    // Object deletion (common functionality)
    virtual CloudResult deleteObject(const std::string& bucket, const std::string& objectKey);
    
    // Utility methods
    virtual Json::Value objectToJson(const CloudObject& object) const;
    virtual Json::Value listResultToJson(const CloudListResult& result) const;
    
    // Common validation methods (using shared utilities)
    virtual bool validateObjectName(const std::string& objectKey) const;
    virtual bool validateBucketName(const std::string& bucket) const;
    virtual bool validateFilePath(const std::string& filePath) const;
    
    /**
     * @brief Build unified path from bucket and object key
     * @param bucket Cloud bucket name
     * @param objectKey Cloud object key
     * @return Unified path string
     */
    virtual std::string buildPathFromBucketAndKey(const std::string& bucket, const std::string& objectKey) const;
    
    // Common utility methods (using shared utilities)
    virtual std::string sanitizeObjectName(const std::string& objectName) const;
    virtual std::string getContentTypeFromObjectKey(const std::string& objectKey) const;
    virtual std::string formatFileSize(uint64_t bytes) const;
    virtual std::string formatDuration(std::chrono::milliseconds duration) const;
    virtual double calculateTransferSpeed(uint64_t bytes, std::chrono::milliseconds duration) const;
    
    // Common file operations (using shared utilities)
    virtual bool ensureLocalDirectoryExists(const std::string& path) const;
    virtual bool isLocalFileAccessible(const std::string& filePath) const;
    virtual uint64_t getLocalFileSize(const std::string& filePath) const;
    
    // Common logging and statistics
    virtual void logOperationStats(const std::string& operation, bool success, 
                                  std::chrono::milliseconds duration, 
                                  uint64_t bytesTransferred = 0,
                                  const std::string& errorMessage = "") const;
    
protected:
    mutable std::mutex m_stats_mutex;
    CloudReaderStats m_stats;
    std::string m_lastError;
    CloudReaderConfig m_config;
    
    // Helper methods for implementations
    void updateStats(bool success, std::chrono::milliseconds latency, 
                    const std::string& errorCode = "");
    void setLastError(const std::string& error);
    std::string formatTimestamp(const std::string& timestamp) const;
    
    // Common validation helpers
    bool validateConfiguration(const CloudReaderConfig& config) const;
    bool validateEndpointUrl(const std::string& endpointUrl) const;
    
    // Download worker thread management
    void initializeDownloadWorkers();
    void shutdownDownloadWorkers();
    void downloadWorkerThread();
    bool processDownloadTask(const DownloadTask& task, FileDownloadResult& result);
    
    // Download worker thread members
    mutable std::mutex m_download_mutex;
    std::vector<std::thread> m_worker_threads;
    std::queue<DownloadTask> m_download_queue;
    std::map<std::string, bool, std::less<>> m_active_downloads; // objectKey -> isActive
    std::atomic<bool> m_shutdown_workers{false};
    std::condition_variable m_download_cv;
    DownloadProgressCallback m_progress_callback;
    
    // Async download session management
    mutable std::mutex m_async_sessions_mutex;
    std::map<std::string, std::shared_ptr<AsyncDownloadSession>, std::less<>> m_async_sessions;
    std::atomic<uint64_t> m_session_counter{0};
    std::condition_variable m_session_completion_cv;
    
    // Helper methods for async downloads
    std::string generateSessionId();
    void processAsyncDownloadSession(const std::string& sessionId);
    void completeAsyncDownloadSession(const std::string& sessionId);
    void cleanupCompletedSessions();
};

} // namespace nv_vms 
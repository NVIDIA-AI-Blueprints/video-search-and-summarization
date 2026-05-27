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

#include "cloud_reader.h"
#include "cloud_reader_utils.h"
#include "logger.h"
#include <ctime>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

namespace nv_vms
{

CloudReader::~CloudReader()
{
    try
    {
        LOG(info) << "CloudReader destructor called - cleaning up ongoing sessions" << std::endl;
        
        // Cancel all async download sessions
        cancelAllAsyncDownloads();
        
        // Cancel all active downloads
        cancelAllDownloads();
        
        // Shutdown worker threads
        shutdownDownloadWorkers();
        
        LOG(info) << "CloudReader destructor completed" << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during CloudReader destruction: " << e.what() << std::endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during CloudReader destruction" << std::endl;
    }
}

Json::Value CloudReader::objectToJson(const CloudObject& object) const
{
    Json::Value json;
    json["key"] = object.key;
    json["etag"] = object.etag;
    json["size"] = std::to_string(object.size);
    json["lastModified"] = object.lastModified;
    json["storageClass"] = object.storageClass;

    if (!object.metadata.empty())
    {
        Json::Value metadata;
        for (const auto& pair : object.metadata)
        {
            metadata[pair.first] = pair.second;
        }
        json["metadata"] = metadata;
    }

    return json;
}

Json::Value CloudReader::listResultToJson(const CloudListResult& result) const
{
    Json::Value json;
    json["success"] = result.success;
    json["message"] = result.message;
    json["bucket"] = result.bucket;
    json["prefix"] = result.prefix;
    json["count"] = result.count;
    json["totalSize"] = static_cast<Json::UInt64>(result.totalSize);
    json["isTruncated"] = result.isTruncated;
    json["nextMarker"] = result.nextMarker;

    if (!result.errorCode.empty())
    {
        json["errorCode"] = result.errorCode;
    }

    Json::Value files = Json::arrayValue;
    for (const auto& object : result.objects)
    {
        files.append(objectToJson(object));
    }
    json["files"] = files;

    return json;
}

void CloudReader::updateStats(bool success, std::chrono::milliseconds latency, const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats.recordRequest(success, latency, errorCode);
}

void CloudReader::setLastError(const std::string& error)
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_lastError = error;
}

std::string CloudReader::formatTimestamp(const std::string& timestamp) const
{
    return CloudReaderUtils::formatTimestamp(timestamp);
}

// Common validation methods
bool CloudReader::validateObjectName(const std::string& objectKey) const
{
    return CloudReaderUtils::isValidObjectName(objectKey);
}

bool CloudReader::validateBucketName(const std::string& bucket) const
{
    return CloudReaderUtils::isValidBucketName(bucket);
}

bool CloudReader::validateFilePath(const std::string& filePath) const
{
    return CloudReaderUtils::isValidFilePath(filePath);
}

// Common utility methods
std::string CloudReader::sanitizeObjectName(const std::string& objectName) const
{
    return CloudReaderUtils::sanitizeObjectName(objectName);
}

std::string CloudReader::getContentTypeFromObjectKey(const std::string& objectKey) const
{
    return CloudReaderUtils::getContentTypeFromExtension(objectKey);
}

std::string CloudReader::formatFileSize(uint64_t bytes) const
{
    return CloudReaderUtils::formatFileSize(bytes);
}

std::string CloudReader::formatDuration(std::chrono::milliseconds duration) const
{
    return CloudReaderUtils::formatDuration(duration);
}

double CloudReader::calculateTransferSpeed(uint64_t bytes, std::chrono::milliseconds duration) const
{
    return CloudReaderUtils::calculateTransferSpeed(bytes, duration);
}

// Common file operations
bool CloudReader::ensureLocalDirectoryExists(const std::string& path) const
{
    return CloudReaderUtils::ensureDirectoryExists(path);
}

bool CloudReader::isLocalFileAccessible(const std::string& filePath) const
{
    return CloudReaderUtils::isFileAccessible(filePath);
}

uint64_t CloudReader::getLocalFileSize(const std::string& filePath) const
{
    return CloudReaderUtils::getFileSize(filePath);
}

// Common logging and statistics
void CloudReader::logOperationStats(const std::string& operation, bool success, std::chrono::milliseconds duration,
                                    uint64_t bytesTransferred, const std::string& errorMessage) const
{
    CloudReaderUtils::logOperationStats(operation, success, duration, bytesTransferred, errorMessage);
}

// Common validation helpers
bool CloudReader::validateConfiguration(const CloudReaderConfig& config) const
{
    std::map<std::string, std::string, std::less<>> config_map;
    config_map["endpoint"] = config.endpoint;
    config_map["accessKeyId"] = config.accessKeyId;
    config_map["secretAccessKey"] = config.secretAccessKey;
    config_map["region"] = config.region;

    std::vector<std::string> required_keys = {"endpoint", "accessKeyId", "secretAccessKey"};
    return CloudReaderUtils::validateConfiguration(config_map, required_keys);
}

bool CloudReader::validateEndpointUrl(const std::string& endpointUrl) const
{
    return CloudReaderUtils::isValidUrl(endpointUrl);
}

// Default implementation for deleteObject (can be overridden by specific implementations)
CloudResult CloudReader::deleteObject(const std::string& bucket, const std::string& objectKey)
{
    CloudResult result;

    // Validate inputs
    if (!validateBucketName(bucket))
    {
        result.success = false;
        result.message = "Invalid bucket name: " + bucket;
        result.errorCode = "INVALID_BUCKET_NAME";
        return result;
    }

    if (!validateObjectName(objectKey))
    {
        result.success = false;
        result.message = "Invalid object name: " + objectKey;
        result.errorCode = "INVALID_OBJECT_NAME";
        return result;
    }

    // Default implementation - derived classes should override this
    result.success = false;
    result.message = "deleteObject not implemented for this cloud storage type";
    result.errorCode = "NOT_IMPLEMENTED";

    return result;
}

// Multi-file download operations implementation
MultiDownloadResult CloudReader::downloadMultipleObjects(const std::string& bucket,
                                                         const std::vector<std::string>& objectKeys,
                                                         const std::string& localDirectory,
                                                         DownloadProgressCallback progressCallback)
{
    MultiDownloadResult result;
    auto startTime = std::chrono::steady_clock::now();

    // Validate inputs
    if (!validateBucketName(bucket))
    {
        result.overall_success = false;
        result.error_message = "Invalid bucket name: " + bucket;
        result.error_code = "INVALID_BUCKET_NAME";
        return result;
    }

    if (!ensureLocalDirectoryExists(localDirectory))
    {
        result.overall_success = false;
        result.error_message = "Failed to create local directory: " + localDirectory;
        result.error_code = "DIRECTORY_CREATION_FAILED";
        return result;
    }

    // Create download tasks
    std::vector<DownloadTask> tasks;
    for (const auto& objectKey : objectKeys)
    {
        if (!validateObjectName(objectKey))
        {
            LOG(warning) << "Skipping invalid object key: " << objectKey << std::endl;
            continue;
        }

        std::string localPath = localDirectory + "/" + objectKey;
        // Ensure directory structure exists for nested paths
        std::filesystem::path path(localPath);
        ensureLocalDirectoryExists(path.parent_path().string());

        tasks.emplace_back(bucket, objectKey, localPath);
    }

    // Convert tasks to object key and path pairs
    std::vector<std::pair<std::string, std::string>> objectKeyPathPairs;
    for (const auto& task : tasks)
    {
        objectKeyPathPairs.emplace_back(task.objectKey, task.localPath);
    }

    // Perform the downloads
    result = downloadObjectsWithPaths(bucket, objectKeyPathPairs, progressCallback);

    auto endTime = std::chrono::steady_clock::now();
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);

    return result;
}

MultiDownloadResult CloudReader::downloadObjectsWithPaths(
    const std::string& bucket, const std::vector<std::pair<std::string, std::string>>& objectKeyPathPairs,
    DownloadProgressCallback progressCallback)
{
    MultiDownloadResult result;
    auto startTime = std::chrono::steady_clock::now();

    if (objectKeyPathPairs.empty())
    {
        result.overall_success = false;
        result.error_message = "No download tasks provided";
        result.error_code = "NO_TASKS";
        return result;
    }

    // Validate bucket name
    if (!validateBucketName(bucket))
    {
        result.overall_success = false;
        result.error_message = "Invalid bucket name: " + bucket;
        result.error_code = "INVALID_BUCKET_NAME";
        return result;
    }

    // Create download tasks from object key and path pairs
    std::vector<DownloadTask> tasks;
    for (const auto& pair : objectKeyPathPairs)
    {
        const std::string& objectKey = pair.first;
        const std::string& localPath = pair.second;

        // Validate object key
        if (!validateObjectName(objectKey))
        {
            LOG(warning) << "Skipping invalid object key: " << objectKey << std::endl;
            continue;
        }

        // Validate and ensure local path directory exists
        if (!validateFilePath(localPath))
        {
            LOG(warning) << "Skipping invalid local path: " << localPath << std::endl;
            continue;
        }

        // Ensure directory structure exists for the local path
        std::filesystem::path path(localPath);
        if (!ensureLocalDirectoryExists(path.parent_path().string()))
        {
            LOG(warning) << "Failed to create directory for: " << localPath << std::endl;
            continue;
        }

        tasks.emplace_back(bucket, objectKey, localPath);
    }

    if (tasks.empty())
    {
        result.overall_success = false;
        result.error_message = "No valid download tasks after validation";
        result.error_code = "NO_VALID_TASKS";
        return result;
    }

    // Initialize worker threads if not already done
    initializeDownloadWorkers();

    // Set progress callback
    {
        std::lock_guard<std::mutex> lock(m_download_mutex);
        m_progress_callback = progressCallback;
    }

    // Add tasks to queue
    {
        std::lock_guard<std::mutex> lock(m_download_mutex);
        for (const auto& task : tasks)
        {
            m_download_queue.push(task);
            m_active_downloads[task.objectKey] = true;
        }
    }

    // Notify worker threads
    m_download_cv.notify_all();

    // Wait for all downloads to complete
    bool allCompleted = false;
    while (!allCompleted)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));

        std::lock_guard<std::mutex> lock(m_download_mutex);
        allCompleted = m_download_queue.empty() && m_active_downloads.empty();
    }

    // Process results
    for (const auto& task : tasks)
    {
        FileDownloadResult downloadResult;
        downloadResult.bucket = task.bucket;
        downloadResult.object_key = task.objectKey;
        downloadResult.local_path = task.localPath;
        downloadResult.remote_path = buildPathFromBucketAndKey(task.bucket, task.objectKey);

        // Check if download was successful by verifying file exists and size
        if (isLocalFileAccessible(task.localPath))
        {
            downloadResult.success = true;
            downloadResult.bytes_downloaded = getLocalFileSize(task.localPath);
            downloadResult.error_message = "Download completed successfully";
        }
        else
        {
            downloadResult.success = false;
            downloadResult.error_message = "Download failed or file not found";
            downloadResult.error_code = "DOWNLOAD_FAILED";
        }

        result.file_results.push_back(downloadResult);

        if (downloadResult.success)
        {
            result.successful_downloads++;
            result.total_bytes_downloaded += downloadResult.bytes_downloaded;
        }
        else
        {
            result.failed_downloads++;
        }
    }

    auto endTime = std::chrono::steady_clock::now();
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);

    // Calculate average speed
    if (result.total_duration.count() > 0)
    {
        result.average_speed = calculateTransferSpeed(result.total_bytes_downloaded, result.total_duration);
    }

    result.overall_success = (result.failed_downloads == 0);
    result.error_message = "Downloaded " + std::to_string(result.successful_downloads) + " files, " +
                           std::to_string(result.failed_downloads) + " failed";

    return result;
}

// Asynchronous download methods implementation
std::string CloudReader::downloadObjectsWithPathsAsync(const std::string& bucket,
                                                      const std::vector<std::pair<std::string, std::string>>& objectKeyPathPairs,
                                                      DownloadCompletionCallback completionCallback,
                                                      DownloadProgressCallback progressCallback)
{
    std::string sessionId = generateSessionId();
    
    LOG(info) << "Starting async download session: " << sessionId << " with " 
              << objectKeyPathPairs.size() << " files" << std::endl;
    
    // Validate inputs
    if (!validateBucketName(bucket))
    {
        LOG(error) << "Invalid bucket name: " << bucket << std::endl;
        return "";
    }
    
    if (objectKeyPathPairs.empty())
    {
        LOG(warning) << "No download tasks provided for session: " << sessionId << std::endl;
        return "";
    }
    
    // Create async download session
    auto session = std::make_shared<AsyncDownloadSession>(sessionId);
    session->completionCallback = completionCallback;
    session->progressCallback = progressCallback;
    
    // Create download tasks from object key and path pairs
    for (const auto& pair : objectKeyPathPairs)
    {
        const std::string& objectKey = pair.first;
        const std::string& localPath = pair.second;
        
        // Validate object key
        if (!validateObjectName(objectKey))
        {
            LOG(warning) << "Skipping invalid object key: " << objectKey << std::endl;
            continue;
        }
        
        // Validate and ensure local path directory exists
        if (!validateFilePath(localPath))
        {
            LOG(warning) << "Skipping invalid local path: " << localPath << std::endl;
            continue;
        }
        
        // Ensure directory structure exists for the local path
        std::filesystem::path path(localPath);
        if (!ensureLocalDirectoryExists(path.parent_path().string()))
        {
            LOG(warning) << "Failed to create directory for: " << localPath << std::endl;
            continue;
        }
        
        session->tasks.emplace_back(bucket, objectKey, localPath);
    }
    
    if (session->tasks.empty())
    {
        LOG(error) << "No valid download tasks for session: " << sessionId << std::endl;
        return "";
    }
    
    // Store session
    {
        std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
        m_async_sessions[sessionId] = session;
    }
    
    // Start processing in background thread
    std::thread([this, sessionId]() {
        processAsyncDownloadSession(sessionId);
    }).detach();
    
    LOG(info) << "Async download session started: " << sessionId << std::endl;
    return sessionId;
}

std::string CloudReader::downloadMultipleObjectsAsync(const std::string& bucket,
                                                     const std::vector<std::string>& objectKeys,
                                                     const std::string& localDirectory,
                                                     DownloadCompletionCallback completionCallback,
                                                     DownloadProgressCallback progressCallback)
{
    std::string sessionId = generateSessionId();
    
    LOG(info) << "Starting async download session: " << sessionId << " with " 
              << objectKeys.size() << " objects to directory: " << localDirectory << std::endl;
    
    // Validate inputs
    if (!validateBucketName(bucket))
    {
        LOG(error) << "Invalid bucket name: " << bucket << std::endl;
        return "";
    }
    
    if (!ensureLocalDirectoryExists(localDirectory))
    {
        LOG(error) << "Failed to create local directory: " << localDirectory << std::endl;
        return "";
    }
    
    if (objectKeys.empty())
    {
        LOG(warning) << "No object keys provided for session: " << sessionId << std::endl;
        return "";
    }
    
    // Create async download session
    auto session = std::make_shared<AsyncDownloadSession>(sessionId);
    session->completionCallback = completionCallback;
    session->progressCallback = progressCallback;
    
    // Create download tasks
    for (const auto& objectKey : objectKeys)
    {
        if (!validateObjectName(objectKey))
        {
            LOG(warning) << "Skipping invalid object key: " << objectKey << std::endl;
            continue;
        }
        
        std::string localPath = localDirectory + "/" + objectKey;
        // Ensure directory structure exists for nested paths
        std::filesystem::path path(localPath);
        ensureLocalDirectoryExists(path.parent_path().string());
        
        session->tasks.emplace_back(bucket, objectKey, localPath);
    }
    
    if (session->tasks.empty())
    {
        LOG(error) << "No valid download tasks for session: " << sessionId << std::endl;
        return "";
    }
    
    // Store session
    {
        std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
        m_async_sessions[sessionId] = session;
    }
    
    // Start processing in background thread
    std::thread([this, sessionId]() {
        processAsyncDownloadSession(sessionId);
    }).detach();
    
    LOG(info) << "Async download session started: " << sessionId << std::endl;
    return sessionId;
}

bool CloudReader::cancelAsyncDownload(const std::string& sessionId)
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    auto it = m_async_sessions.find(sessionId);
    if (it == m_async_sessions.end())
    {
        LOG(warning) << "Async download session not found: " << sessionId << std::endl;
        return false;
    }
    
    auto session = it->second;
    if (session->isCompleted.load())
    {
        LOG(info) << "Async download session already completed: " << sessionId << std::endl;
        return true;
    }
    
    // Mark as completed to stop processing
    session->isCompleted.store(true);
    
    // Cancel individual downloads for this session
    for (const auto& task : session->tasks)
    {
        cancelDownload(task.objectKey);
    }
    
    LOG(info) << "Cancelled async download session: " << sessionId << std::endl;
    return true;
}

bool CloudReader::cancelAllAsyncDownloads()
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    for (auto& pair : m_async_sessions)
    {
        auto session = pair.second;
        if (!session->isCompleted.load())
        {
            session->isCompleted.store(true);
            
            // Cancel individual downloads for this session
            for (const auto& task : session->tasks)
            {
                cancelDownload(task.objectKey);
            }
        }
    }
    
    LOG(info) << "Cancelled all async download sessions" << std::endl;
    return true;
}

std::vector<std::string> CloudReader::getActiveAsyncDownloads() const
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    std::vector<std::string> activeSessions;
    
    for (const auto& pair : m_async_sessions)
    {
        if (!pair.second->isCompleted.load())
        {
            activeSessions.push_back(pair.first);
        }
    }
    
    return activeSessions;
}

MultiDownloadResult CloudReader::getAsyncDownloadResult(const std::string& sessionId) const
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    auto it = m_async_sessions.find(sessionId);
    if (it == m_async_sessions.end())
    {
        MultiDownloadResult result;
        result.overall_success = false;
        result.error_message = "Async download session not found: " + sessionId;
        return result;
    }
    
    auto session = it->second;
    MultiDownloadResult result;
    
    // Copy results with mutex protection
    {
        std::lock_guard<std::mutex> results_lock(session->results_mutex);
        result.file_results = session->results;
    }
    result.successful_downloads = session->successfulDownloads.load();
    result.failed_downloads = session->failedDownloads.load();
    result.total_bytes_downloaded = session->totalBytesDownloaded.load();
    result.overall_success = (session->failedDownloads.load() == 0);
    
    auto endTime = std::chrono::system_clock::now();
    result.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - session->startTime);
    
    if (result.total_duration.count() > 0)
    {
        result.average_speed = calculateTransferSpeed(result.total_bytes_downloaded, result.total_duration);
    }
    
    result.error_message = "Downloaded " + std::to_string(result.successful_downloads) + " files, " +
                           std::to_string(result.failed_downloads) + " failed";
    
    return result;
}

bool CloudReader::isAsyncDownloadCompleted(const std::string& sessionId) const
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    auto it = m_async_sessions.find(sessionId);
    if (it == m_async_sessions.end())
    {
        return false;
    }
    
    return it->second->isCompleted.load();
}

bool CloudReader::waitForAsyncDownloadCompletion(const std::string& sessionId, 
                                                std::chrono::milliseconds timeout) const
{
    std::shared_ptr<AsyncDownloadSession> session;
    
    {
        std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
        auto it = m_async_sessions.find(sessionId);
        if (it == m_async_sessions.end())
        {
            LOG(error) << "Async download session not found: " << sessionId << std::endl;
            return false;
        }
        session = it->second;
    }
    
    // Wait for completion using condition variable with timeout
    {
        std::unique_lock<std::mutex> lock(session->completion_mutex);
        bool completed = session->completion_cv.wait_for(lock, timeout, [session] {
            return session->completedTasks.load() >= session->tasks.size();
        });
        
        if (!completed)
        {
            LOG(warning) << "Timeout waiting for async download session completion: " << sessionId << std::endl;
            return false;
        }
    }
    
    return true;
}

// Helper methods for async downloads
std::string CloudReader::generateSessionId()
{
    uint64_t sessionNum = m_session_counter.fetch_add(1);
    std::stringstream ss;
    ss << "async_download_" << std::hex << sessionNum << "_" 
       << std::chrono::duration_cast<std::chrono::milliseconds>(
           std::chrono::system_clock::now().time_since_epoch()).count();
    return ss.str();
}

void CloudReader::processAsyncDownloadSession(const std::string& sessionId)
{
    std::shared_ptr<AsyncDownloadSession> session;
    
    {
        std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
        auto it = m_async_sessions.find(sessionId);
        if (it == m_async_sessions.end())
        {
            LOG(error) << "Async download session not found: " << sessionId << std::endl;
            return;
        }
        session = it->second;
    }
    
    LOG(info) << "Processing async download session: " << sessionId 
              << " with " << session->tasks.size() << " tasks" << std::endl;
    
    // Initialize worker threads if not already done
    initializeDownloadWorkers();
    
    // Add tasks to queue
    {
        std::lock_guard<std::mutex> lock(m_download_mutex);
        for (const auto& task : session->tasks)
        {
            m_download_queue.push(task);
            m_active_downloads[task.objectKey] = true;
        }
    }
    
    // Notify worker threads
    m_download_cv.notify_all();
    
    // Monitor progress
    while (!session->isCompleted.load())
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        
        // Check if all tasks are completed
        bool allCompleted = false;
        {
            std::lock_guard<std::mutex> lock(m_download_mutex);
            allCompleted = m_download_queue.empty() && m_active_downloads.empty();
        }
        
        if (allCompleted)
        {
            break;
        }
    }
    
    // Wait for all worker threads to complete using condition variable
    {
        std::unique_lock<std::mutex> lock(session->completion_mutex);
        session->completion_cv.wait(lock, [session] {
            return session->completedTasks.load() >= session->tasks.size();
        });
    }
    
    // Worker threads have already processed the downloads and updated session statistics
    // The results are already available in the session, no need to re-process
    LOG(info) << "All worker threads completed for session: " << sessionId 
              << " - Tasks completed: " << session->completedTasks.load() 
              << "/" << session->tasks.size() << std::endl;
    
    // Mark session as completed
    session->isCompleted.store(true);
    
    // Call completion callback if available
    if (session->completionCallback)
    {
        try
        {
            MultiDownloadResult result = getAsyncDownloadResult(sessionId);
            session->completionCallback(sessionId, result);
        }
        catch (...)
        {
            // Ignore callback exceptions
        }
    }
    
    LOG(info) << "Completed async download session: " << sessionId 
              << " - Success: " << session->successfulDownloads.load() 
              << ", Failed: " << session->failedDownloads.load() << std::endl;
    
    // Cleanup completed sessions periodically
    cleanupCompletedSessions();
}

void CloudReader::completeAsyncDownloadSession(const std::string& sessionId)
{
    // This method is called when a session is manually marked as completed
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    auto it = m_async_sessions.find(sessionId);
    if (it != m_async_sessions.end())
    {
        it->second->isCompleted.store(true);
    }
}

void CloudReader::cleanupCompletedSessions()
{
    std::lock_guard<std::mutex> lock(m_async_sessions_mutex);
    
    auto it = m_async_sessions.begin();
    while (it != m_async_sessions.end())
    {
        ++it;
    }
}

bool CloudReader::cancelDownload(const std::string& objectKey)
{
    std::lock_guard<std::mutex> lock(m_download_mutex);

    // Remove from active downloads
    auto it = m_active_downloads.find(objectKey);
    if (it != m_active_downloads.end())
    {
        m_active_downloads.erase(it);
        return true;
    }

    // Remove from queue
    std::queue<DownloadTask> tempQueue;
    while (!m_download_queue.empty())
    {
        DownloadTask task = m_download_queue.front();
        m_download_queue.pop();

        if (task.objectKey != objectKey)
        {
            tempQueue.push(task);
        }
    }

    m_download_queue = tempQueue;
    return true;
}

bool CloudReader::cancelAllDownloads()
{
    std::lock_guard<std::mutex> lock(m_download_mutex);
    m_active_downloads.clear();

    // Clear queue
    while (!m_download_queue.empty())
    {
        m_download_queue.pop();
    }

    return true;
}

std::vector<std::string> CloudReader::getActiveDownloads() const
{
    std::lock_guard<std::mutex> lock(m_download_mutex);
    std::vector<std::string> activeDownloads;

    for (const auto& pair : m_active_downloads)
    {
        if (pair.second)
        {
            activeDownloads.push_back(pair.first);
        }
    }

    return activeDownloads;
}

// Download worker thread management
void CloudReader::initializeDownloadWorkers()
{
    std::lock_guard<std::mutex> lock(m_download_mutex);

    if (!m_worker_threads.empty())
    {
        return; // Already initialized
    }

    m_shutdown_workers = false;
    uint32_t numThreads = m_config.downloadWorker.maxWorkerThreads;

    for (uint32_t i = 0; i < numThreads; ++i)
    {
        m_worker_threads.emplace_back(&CloudReader::downloadWorkerThread, this);
    }

    LOG(info) << "Initialized " << numThreads << " download worker threads" << std::endl;
}

void CloudReader::shutdownDownloadWorkers()
{
    {
        std::lock_guard<std::mutex> lock(m_download_mutex);
        m_shutdown_workers = true;
    }

    m_download_cv.notify_all();

    for (auto& thread : m_worker_threads)
    {
        if (thread.joinable())
        {
            thread.join();
        }
    }

    m_worker_threads.clear();
    LOG(info) << "Shutdown download worker threads" << std::endl;
}

void CloudReader::downloadWorkerThread()
{
    while (!m_shutdown_workers)
    {
        DownloadTask task;
        bool hasTask = false;

        {
            std::unique_lock<std::mutex> lock(m_download_mutex);
            m_download_cv.wait(lock, [this] { return m_shutdown_workers || !m_download_queue.empty(); });

            if (m_shutdown_workers)
            {
                break;
            }

            if (!m_download_queue.empty())
            {
                task = m_download_queue.front();
                m_download_queue.pop();
                hasTask = true;
            }
        }

        if (hasTask)
        {
            FileDownloadResult result;
            bool success = processDownloadTask(task, result);

            // Update active downloads
            {
                std::lock_guard<std::mutex> lock(m_download_mutex);
                auto it = m_active_downloads.find(task.objectKey);
                if (it != m_active_downloads.end())
                {
                    m_active_downloads.erase(it);
                }
            }

            // Update session statistics for async downloads
            // Find the session that contains this task
            {
                std::lock_guard<std::mutex> session_lock(m_async_sessions_mutex);
                for (auto& session_pair : m_async_sessions)
                {
                    auto session = session_pair.second;
                    for (auto& session_task : session->tasks)
                    {
                        if (session_task.objectKey == task.objectKey && 
                            session_task.bucket == task.bucket &&
                            session_task.localPath == task.localPath)
                        {
                            // Store the result in session
                            {
                                std::lock_guard<std::mutex> results_lock(session->results_mutex);
                                session->results.push_back(result);
                            }
                            
                            // Update session statistics
                            if (result.success)
                            {
                                session->successfulDownloads.fetch_add(1);
                                if (result.bytes_downloaded > 0)
                                {
                                    session->totalBytesDownloaded.fetch_add(result.bytes_downloaded);
                                }
                            }
                            else
                            {
                                session->failedDownloads.fetch_add(1);
                            }
                            session->completedTasks.fetch_add(1);
                            
                            // Call session progress callback if available
                            if (session->progressCallback)
                            {
                                session->progressCallback(task.objectKey, result.bytes_downloaded,
                                                        task.expectedSize > 0 ? task.expectedSize : result.bytes_downloaded,
                                                        result.download_speed);
                            }
                            
                            // Notify completion condition variable
                            {
                                std::lock_guard<std::mutex> completion_lock(session->completion_mutex);
                                session->completion_cv.notify_one();
                            }
                            break;
                        }
                    }
                }
            }

            // Log result
            if (success)
            {
                LOG(info) << "Download completed: " << task.objectKey << " -> " << task.localPath << std::endl;
            }
            else
            {
                LOG(error) << "Download failed: " << task.objectKey << " - " << result.error_message << std::endl;
            }
        }
    }
}

bool CloudReader::processDownloadTask(const DownloadTask& task, FileDownloadResult& result)
{
    auto startTime = std::chrono::steady_clock::now();

    result.bucket = task.bucket;
    result.object_key = task.objectKey;
    result.local_path = task.localPath;
    result.remote_path = buildPathFromBucketAndKey(task.bucket, task.objectKey);

    // Check if file already exists locally
    if (std::filesystem::exists(task.localPath))
    {
        result.success = true;
        result.bytes_downloaded = getLocalFileSize(task.localPath);
        result.error_message = "File already exists locally, skipping download";
        result.was_resumed = false; // Not a resumed download, just skipped

        auto endTime = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
        result.download_speed = 0.0; // No download speed since we skipped
        result.duration = duration;

        LOG(info) << "File already exists, skipping download: " << task.objectKey 
                  << " -> " << task.localPath << " (" << result.bytes_downloaded << " bytes)" << std::endl;

        // Call progress callback if available
        if (m_progress_callback)
        {
            m_progress_callback(task.objectKey, result.bytes_downloaded,
                                task.expectedSize > 0 ? task.expectedSize : result.bytes_downloaded,
                                result.download_speed);
        }

        return true;
    }

    // Ensure final directory exists
    std::filesystem::path finalPath(task.localPath);
    if (!ensureLocalDirectoryExists(finalPath.parent_path().string()))
    {
        result.success = false;
        result.error_message = "Failed to create final directory";
        result.error_code = "DIR_CREATION_FAILED";
        return false;
    }

    // Perform the actual download directly to the final location
    CloudResult downloadResult = downloadObject(task.bucket, task.objectKey, task.localPath);

    if (downloadResult.success)
    {
        result.success = true;
        result.bytes_downloaded = getLocalFileSize(task.localPath);

        auto endTime = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
        result.download_speed = calculateTransferSpeed(result.bytes_downloaded, duration);
        result.duration = duration;

        LOG(info) << "Download completed: " << task.objectKey << " -> " << task.localPath 
                  << " (" << result.bytes_downloaded << " bytes in " << duration.count() << "ms)" << std::endl;

        // Call progress callback if available
        if (m_progress_callback)
        {
            m_progress_callback(task.objectKey, result.bytes_downloaded,
                                task.expectedSize > 0 ? task.expectedSize : result.bytes_downloaded,
                                result.download_speed);
        }

        return true;
    }
    else
    {
        // Check if this is a "NoSuchKey" error (object doesn't exist)
        if (downloadResult.errorCode == "NoSuchKey" || 
            downloadResult.message.find("Object does not exist") != std::string::npos ||
            downloadResult.message.find("NoSuchKey") != std::string::npos)
        {
            // Object doesn't exist, mark as failure
            result.success = false; // Mark as failure to count in failed downloads
            result.bytes_downloaded = 0;
            result.error_message = "Object does not exist in cloud storage, skipping";
            result.error_code = "OBJECT_NOT_FOUND";
            result.was_skipped = true; // Mark as skipped
            
            auto endTime = std::chrono::steady_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
            result.download_speed = 0.0;
            result.duration = duration;

            LOG(warning) << "Object does not exist, skipping download: " << task.objectKey 
                        << " (bucket: " << task.bucket << ")" << std::endl;

            // Call progress callback if available - indicate failure with totalBytes = 0
            if (m_progress_callback)
            {
                m_progress_callback(task.objectKey, 0, 0, 0.0); // totalBytes = 0 indicates failure
            }

            return true; // Return true to indicate successful handling (skip)
        }
        else
        {
            // This is a real failure, not a missing object
            result.success = false;
            result.error_message = downloadResult.message;
            result.error_code = downloadResult.errorCode;
            return false;
        }
    }
}

std::string CloudReader::buildPathFromBucketAndKey(const std::string& bucket, const std::string& objectKey) const
{
    // Build unified path in format: bucket/object_key
    return bucket + "/" + objectKey;
}

} // namespace nv_vms
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

#include "unified_cloud_storage_reader.h"
#include "cloud_reader_factory.h"
#include "logger.h"
#include <algorithm>
#include <chrono>
#include <exception>
#include <sstream>

namespace nv_vms
{

UnifiedCloudStorageReader::UnifiedCloudStorageReader()
    : UnifiedStorageReader(StorageType::CLOUD)
{
}

UnifiedCloudStorageReader::~UnifiedCloudStorageReader()
{
    try
    {
        LOG(info) << "UnifiedCloudStorageReader destructor called - cleaning up cloud reader" << std::endl;

        // Cancel all async downloads in the cloud reader
        if (m_cloudReader)
        {
            m_cloudReader->cancelAllAsyncDownloads();
        }

        // Reset the cloud reader (this will trigger its destructor)
        m_cloudReader.reset();

        LOG(info) << "UnifiedCloudStorageReader destructor completed" << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during UnifiedCloudStorageReader destruction: " << e.what() << std::endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during UnifiedCloudStorageReader destruction" << std::endl;
    }
}

bool UnifiedCloudStorageReader::isAvailable() const
{
    return m_cloudReader != nullptr && m_cloudReader->isAvailable();
}

std::string UnifiedCloudStorageReader::getStorageMode() const
{
    return StorageConstants::CLOUD_STORAGE;
}

bool UnifiedCloudStorageReader::configureStorage(const StorageConfig& config)
{
    LOG(info) << "UnifiedCloudStorageReader::configureStorage called" << std::endl;

    // Convert StorageConfig to CloudReaderConfig first
    m_cloudConfig = convertStorageConfigToCloudConfig(config);
    LOG(info) << "Config conversion completed" << std::endl;

    if (!UnifiedStorageReader::configureStorage(config))
    {
        LOG(error) << "configureStorage failed" << std::endl;
        return false;
    }

    LOG(info) << "configureStorage succeeded, cloud reader should already be initialized" << std::endl;

    // Verify that cloud reader was actually created
    if (!m_cloudReader)
    {
        LOG(error) << "Cloud reader was not created during initialization" << std::endl;
        return false;
    }

    LOG(info) << "Cloud storage reader configured successfully" << std::endl;
    return true;
}

StorageConfig UnifiedCloudStorageReader::getStorageConfiguration() const
{
    StorageConfig config = UnifiedStorageReader::getStorageConfiguration();

    // Add cloud-specific parameters
    config.setParameter(StorageConstants::CLOUD_TYPE_KEY, cloudStorageTypeToString(m_cloudConfig.storageType));
    config.setParameter(StorageConstants::ENDPOINT_KEY, m_cloudConfig.endpoint);
    config.setParameter(StorageConstants::REGION_KEY, m_cloudConfig.region);
    config.setParameter(StorageConstants::ACCESS_KEY_KEY, m_cloudConfig.accessKeyId);
    config.setParameter(StorageConstants::SECRET_KEY_KEY, m_cloudConfig.secretAccessKey);
    config.setParameter(StorageConstants::USE_SSL_KEY, m_cloudConfig.useSSL ? "true" : "false");
    config.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, std::to_string(m_cloudConfig.timeoutSeconds));
    config.setParameter(StorageConstants::MAX_RETRIES_KEY, std::to_string(m_cloudConfig.maxRetries));

    return config;
}

FileResult UnifiedCloudStorageReader::downloadFile(const std::string& remote_path, const std::string& local_path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    LOG(info) << "UnifiedCloudStorageReader::downloadFile called with remote_path: '" << remote_path
              << "' and local_path: '" << local_path << "'" << std::endl;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(remote_path);
            std::string objectKey = extractObjectKeyFromPath(remote_path);
            LOG(info) << "Downloading file from cloud: " << remote_path << " to " << local_path
                      << " from bucket: " << bucket << std::endl;

            CloudResult cloudResult = m_cloudReader->downloadObject(bucket, objectKey, local_path);
            result = convertCloudResultToFileResult(cloudResult);

            if (result.success)
            {
                // Get file size for statistics
                FileInfo fileInfo;
                FileResult infoResult = getFileInfo(remote_path, fileInfo);
                if (infoResult.success)
                {
                    result.bytes_read = fileInfo.size;
                }
            }
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

FileResult UnifiedCloudStorageReader::getFileInfo(const std::string& path, FileInfo& fileInfo)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectKey = extractObjectKeyFromPath(path);

            CloudObject cloudObject;
            CloudResult cloudResult = m_cloudReader->getObjectInfo(bucket, objectKey, cloudObject);

            if (cloudResult.success)
            {
                fileInfo = convertCloudObjectToFileInfo(cloudObject);
                result.success = true;
                result.message = "File info retrieved successfully";
            }
            else
            {
                result = convertCloudResultToFileResult(cloudResult);
            }
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

FileResult UnifiedCloudStorageReader::checkFileExists(const std::string& path)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    LOG(verbose) << "UnifiedCloudStorageReader::checkFileExists called with path: '" << path << "'" << std::endl;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectKey = extractObjectKeyFromPath(path);

            LOG(info) << "Extracted bucket: '" << bucket << "' and objectKey: '" << objectKey << "'" << std::endl;

            CloudResult cloudResult = m_cloudReader->checkObjectExists(bucket, objectKey);
            result = convertCloudResultToFileResult(cloudResult);
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

FileListResult UnifiedCloudStorageReader::listFiles(const std::string& path, const std::string& prefix)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectPrefix = extractObjectKeyFromPath(path);

            // Combine path and prefix
            if (!objectPrefix.empty() && !prefix.empty())
            {
                objectPrefix = objectPrefix + "/" + prefix;
            }
            else if (!prefix.empty())
            {
                objectPrefix = prefix;
            }

            CloudListResult cloudResult = m_cloudReader->listObjects(bucket, objectPrefix);
            result = convertCloudListResultToFileListResult(cloudResult);
            result.path = path;
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

FileListResult UnifiedCloudStorageReader::listFilesPaginated(const std::string& path, const std::string& prefix,
                                                             const std::string& marker, uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();

    FileListResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectPrefix = extractObjectKeyFromPath(path);

            // Combine path and prefix
            if (!objectPrefix.empty() && !prefix.empty())
            {
                objectPrefix = objectPrefix + "/" + prefix;
            }
            else if (!prefix.empty())
            {
                objectPrefix = prefix;
            }

            CloudListResult cloudResult = m_cloudReader->listObjectsPaginated(bucket, objectPrefix, marker, maxKeys);
            result = convertCloudListResultToFileListResult(cloudResult);
            result.path = path;
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

FileResult UnifiedCloudStorageReader::generatePresignedUrl(const std::string& path, uint32_t expirationSeconds,
                                                           std::string& url)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectKey = extractObjectKeyFromPath(path);

            CloudResult cloudResult = m_cloudReader->generatePresignedUrl(bucket, objectKey, expirationSeconds, url);
            result = convertCloudResultToFileResult(cloudResult);
        }
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

// Helper: Extract actual expiration from presigned URL
static uint32_t extractExpirationFromUrl(const std::string& url, uint32_t requestedExpiry)
{
    // Look for X-Amz-Expires or Expires parameter in URL
    size_t expiresPos = url.find("X-Amz-Expires=");
    if (expiresPos == std::string::npos)
    {
        expiresPos = url.find("Expires=");
        if (expiresPos == std::string::npos)
        {
            // No expiry parameter found, use requested value
            return requestedExpiry;
        }
        expiresPos += 8;  // Length of "Expires="
    }
    else
    {
        expiresPos += 14;  // Length of "X-Amz-Expires="
    }

    // Extract the number
    size_t endPos = url.find('&', expiresPos);
    if (endPos == std::string::npos)
    {
        endPos = url.length();
    }

    std::string expiryStr = url.substr(expiresPos, endPos - expiresPos);
    try
    {
        return std::stoul(expiryStr);
    }
    catch (...)
    {
        return requestedExpiry;  // Fallback to requested value
    }
}

FileResult UnifiedCloudStorageReader::getPresignedUrl(const std::string& path, std::string& url)
{
    auto start_time = std::chrono::steady_clock::now();
    FileResult result;

    try
    {
        // Check cache first (with minimal lock scope)
        {
            std::lock_guard<std::mutex> lock(m_cacheMutex);
            auto it = m_presignedUrlCache.find(path);
            if (it != m_presignedUrlCache.end())
            {
                // Check if the cached URL is still valid (with 30-minute safety margin)
                auto now = std::chrono::system_clock::now();
                auto timeUntilExpiry = std::chrono::duration_cast<std::chrono::seconds>(it->second.expiryTime - now).count();

                if (timeUntilExpiry > 30 * 60)
                {
                    url = it->second.url;
                    result.success = true;
                    result.message = "Cached presigned URL";

                    auto end_time = std::chrono::steady_clock::now();
                    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
                    updateStats(result.success, result.duration, result.errorCode);
                    return result;
                }
                else
                {
                    // URL expired or expiring soon, remove from cache
                    m_presignedUrlCache.erase(it);
                }
            }
        }  // Lock released here

        // Generate new presigned URL (NO LOCK - S3 API call can take time)
        const uint32_t REQUESTED_EXPIRATION_SECONDS = 12 * 60 * 60;  // Request 12 hours

        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            std::string bucket = extractBucketFromPath(path);
            std::string objectKey = extractObjectKeyFromPath(path);

            CloudResult cloudResult = m_cloudReader->generatePresignedUrl(bucket, objectKey, REQUESTED_EXPIRATION_SECONDS, url);
            result = convertCloudResultToFileResult(cloudResult);

            if (result.success)
            {
                // Extract actual expiration from the URL (server might grant less than requested)
                uint32_t actualExpiry = extractExpirationFromUrl(url, REQUESTED_EXPIRATION_SECONDS);
                LOG(info) << "Object_id: " << path << " expiration: " << actualExpiry << " seconds" << std::endl;
                // Cache the URL (acquire lock only for cache write)
                std::lock_guard<std::mutex> lock(m_cacheMutex);
                CachedPresignedUrl cachedUrl;
                cachedUrl.url = url;
                cachedUrl.expiryTime = std::chrono::system_clock::now() +
                                      std::chrono::seconds(actualExpiry);
                m_presignedUrlCache[path] = cachedUrl;

                result.message = "Generated and cached presigned URL";
            }
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during getPresignedUrl: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

FileResult UnifiedCloudStorageReader::listBuckets(std::vector<std::string>& buckets)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            CloudResult cloudResult = m_cloudReader->listBuckets(buckets);
            result = convertCloudResultToFileResult(cloudResult);
        }
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

FileResult UnifiedCloudStorageReader::checkBucketExists(const std::string& bucket)
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            CloudResult cloudResult = m_cloudReader->checkBucketExists(bucket);
            result = convertCloudResultToFileResult(cloudResult);
        }
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

CloudListResult UnifiedCloudStorageReader::listCloudObjects(const std::string& bucketName,
                                                           const std::string& prefix,
                                                           uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();

    CloudListResult result;
    result.bucket = bucketName;
    result.prefix = prefix;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
            LOG(error) << "Cloud reader not initialized for listCloudObjects" << std::endl;
        }
        else if (!m_cloudReader->isAvailable())
        {
            result.success = false;
            result.message = "Cloud reader is not available";
            result.errorCode = "NOT_AVAILABLE";
            LOG(error) << "Cloud reader is not available for listCloudObjects" << std::endl;
        }
        else
        {
            // Use the cloud reader to list objects
            result = m_cloudReader->listObjects(bucketName, prefix, maxKeys);

            if (result.success)
            {
                LOG(info) << "Successfully listed " << result.count << " objects from bucket: "
                          << bucketName << " with prefix: " << prefix << std::endl;
            }
            else
            {
                LOG(warning) << "Failed to list objects from bucket: " << bucketName
                            << ", error: " << result.message << std::endl;
            }
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listCloudObjects: ") + e.what();
        result.errorCode = "EXCEPTION";
        LOG(error) << "Exception in listCloudObjects: " << e.what() << std::endl;
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

CloudListResult UnifiedCloudStorageReader::listAllCloudObjects(const std::string& bucketName,
                                                              const std::string& prefix)
{
    auto start_time = std::chrono::steady_clock::now();

    CloudListResult result;
    result.bucket = bucketName;
    result.prefix = prefix;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
            LOG(error) << "Cloud reader not initialized for listAllCloudObjects" << std::endl;
        }
        else if (!m_cloudReader->isAvailable())
        {
            result.success = false;
            result.message = "Cloud reader is not available";
            result.errorCode = "NOT_AVAILABLE";
            LOG(error) << "Cloud reader is not available for listAllCloudObjects" << std::endl;
        }
        else
        {
            // Use the cloud reader's listAllObjects method (works for S3, MinIO, and other compatible storages)
            LOG(info) << "Using CloudReader::listAllObjects for complete listing" << std::endl;
            result = m_cloudReader->listAllObjects(bucketName, prefix);

            if (result.success)
            {
                LOG(info) << "Successfully listed ALL " << result.count << " objects from bucket: "
                          << bucketName << " with prefix: " << prefix << std::endl;
            }
            else
            {
                LOG(warning) << "Failed to list all objects from bucket: " << bucketName
                            << ", error: " << result.message << std::endl;
            }
        }
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during listAllCloudObjects: ") + e.what();
        result.errorCode = "EXCEPTION";
        LOG(error) << "Exception in listAllCloudObjects: " << e.what() << std::endl;
    }

    auto end_time = std::chrono::steady_clock::now();
    result.duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);

    updateStats(result.success, result.duration, result.errorCode);
    return result;
}

ReaderStats UnifiedCloudStorageReader::getReaderStats() const
{
    return UnifiedStorageReader::getReaderStats();
}

void UnifiedCloudStorageReader::resetStats()
{
    UnifiedStorageReader::resetStats();
}

std::string UnifiedCloudStorageReader::getLastError() const
{
    return UnifiedStorageReader::getLastError();
}

FileResult UnifiedCloudStorageReader::performHealthCheck()
{
    auto start_time = std::chrono::steady_clock::now();

    FileResult result;

    try
    {
        if (!m_cloudReader)
        {
            result.success = false;
            result.message = "Cloud reader not initialized";
            result.errorCode = "NOT_INITIALIZED";
        }
        else
        {
            CloudResult cloudResult = m_cloudReader->performHealthCheck();
            result = convertCloudResultToFileResult(cloudResult);
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

std::shared_ptr<CloudReader> UnifiedCloudStorageReader::getCloudReader() const
{
    return m_cloudReader;
}

// Async download operations implementation
std::string UnifiedCloudStorageReader::downloadFilesWithPathsAsync(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                                                  DownloadCompletionCallback completionCallback,
                                                                  DownloadProgressCallback progressCallback)
{
    if (!m_cloudReader)
    {
        setLastError("Cloud reader not initialized");
        return "";
    }

    // Convert remote paths to object keys and get bucket
    std::string bucket = getBucketName();
    if (bucket.empty())
    {
        setLastError("No bucket configured");
        return "";
    }

    std::vector<std::pair<std::string, std::string>> objectKeyPathPairs;
    for (const auto& pair : remoteLocalPairs)
    {
        std::string objectKey = extractObjectKeyFromPath(pair.first);
        objectKeyPathPairs.emplace_back(objectKey, pair.second);
    }

    return m_cloudReader->downloadObjectsWithPathsAsync(bucket, objectKeyPathPairs, completionCallback, progressCallback);
}

std::string UnifiedCloudStorageReader::downloadMultipleFilesAsync(const std::string& remote_directory,
                                                                 const std::vector<std::string>& file_paths,
                                                                 const std::string& local_directory,
                                                                 DownloadCompletionCallback completionCallback,
                                                                 DownloadProgressCallback progressCallback)
{
    if (!m_cloudReader)
    {
        setLastError("Cloud reader not initialized");
        return "";
    }

    // Convert file paths to object keys
    std::vector<std::string> objectKeys;
    for (const auto& filePath : file_paths)
    {
        std::string objectKey = extractObjectKeyFromPath(filePath);
        objectKeys.push_back(objectKey);
    }

    return m_cloudReader->downloadMultipleObjectsAsync(getBucketName(), objectKeys, local_directory, completionCallback, progressCallback);
}

bool UnifiedCloudStorageReader::cancelAsyncDownload(const std::string& sessionId)
{
    if (!m_cloudReader)
    {
        setLastError("Cloud reader not initialized");
        return false;
    }
    return m_cloudReader->cancelAsyncDownload(sessionId);
}

bool UnifiedCloudStorageReader::cancelAllAsyncDownloads()
{
    if (!m_cloudReader)
    {
        setLastError("Cloud reader not initialized");
        return false;
    }
    return m_cloudReader->cancelAllAsyncDownloads();
}

std::vector<std::string> UnifiedCloudStorageReader::getActiveAsyncDownloads() const
{
    if (!m_cloudReader)
    {
        return {};
    }
    return m_cloudReader->getActiveAsyncDownloads();
}

bool UnifiedCloudStorageReader::isAsyncDownloadCompleted(const std::string& sessionId) const
{
    if (!m_cloudReader)
    {
        return false;
    }
    return m_cloudReader->isAsyncDownloadCompleted(sessionId);
}

DownloadResult UnifiedCloudStorageReader::getAsyncDownloadResult(const std::string& sessionId) const
{
    DownloadResult result;

    if (!m_cloudReader)
    {
        result.overall_success = false;
        result.error_message = "Cloud reader not initialized";
        return result;
    }

    // Get result from cloud reader and convert
    MultiDownloadResult cloudResult = m_cloudReader->getAsyncDownloadResult(sessionId);

    result.overall_success = cloudResult.overall_success;
    result.error_message = cloudResult.error_message;
    result.error_code = cloudResult.error_code;
    result.total_duration = cloudResult.total_duration;
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

    return result;
}

bool UnifiedCloudStorageReader::initializeStorage()
{
    LOG(info) << "Initializing cloud storage reader" << std::endl;
    return initializeCloudReader();
}

bool UnifiedCloudStorageReader::validateConfiguration(const StorageConfig& config)
{
    LOG(info) << "UnifiedCloudStorageReader::validateConfiguration called" << std::endl;

    std::string cloudType = getCloudStorageTypeFromConfig(config);
    LOG(info) << "Cloud type from config: '" << cloudType << "'" << std::endl;

    if (cloudType.empty())
    {
        LOG(error) << "cloud_type parameter is required for cloud storage" << std::endl;
        setLastError("cloud_type parameter is required for cloud storage");
        return false;
    }

    std::string endpoint = config.getParameter("endpoint");
    LOG(info) << "Endpoint from config: '" << endpoint << "'" << std::endl;

    if (endpoint.empty())
    {
        LOG(error) << "endpoint parameter is required for cloud storage" << std::endl;
        setLastError("endpoint parameter is required for cloud storage");
        return false;
    }

    LOG(info) << "Configuration validation passed" << std::endl;
    return true;
}

bool UnifiedCloudStorageReader::initializeCloudReader()
{
    try
    {
        std::string cloudTypeStr = cloudStorageTypeToString(m_cloudConfig.storageType);
        LOG(info) << "Initializing cloud reader for type: '" << cloudTypeStr
                  << "' (enum: " << static_cast<int>(m_cloudConfig.storageType) << ")" << std::endl;

        // Use the factory's createReader function directly with the CloudStorageType enum
        m_cloudReader = CloudReaderFactory::createReader(m_cloudConfig.storageType, m_cloudConfig);

        if (!m_cloudReader)
        {
            LOG(error) << "Failed to create cloud reader for type: " + cloudTypeStr << std::endl;
            setLastError("Failed to create cloud reader for type: " + cloudTypeStr);
            return false;
        }

        if (!m_cloudReader->isAvailable())
        {
            setLastError("Cloud reader is not available for type: " + cloudTypeStr);
            return false;
        }

        LOG(info) << "Successfully created cloud reader for type: " + cloudTypeStr << std::endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError(std::string("Exception during cloud reader initialization: ") + e.what());
        return false;
    }
}

CloudReaderConfig UnifiedCloudStorageReader::convertStorageConfigToCloudConfig(const StorageConfig& config) const
{
    CloudReaderConfig cloudConfig;

    std::string cloudType = getCloudStorageTypeFromConfig(config);
    LOG(info) << "Converting cloud type from config: '" << cloudType << "'" << std::endl;

    // Use the factory's stringToStorageType function which supports case-insensitive matching and aliases
    cloudConfig.storageType = CloudReaderFactory::stringToStorageType(cloudType);
    LOG(info) << "Converted to CloudStorageType enum: " << static_cast<int>(cloudConfig.storageType) << std::endl;

    cloudConfig.endpoint = config.getParameter("endpoint");
    cloudConfig.region = config.getParameter("region");
    cloudConfig.accessKeyId = config.getParameter("access_key");
    cloudConfig.secretAccessKey = config.getParameter("secret_key");
    cloudConfig.useSSL = config.getParameter("use_ssl", "true") == "true";
    cloudConfig.timeoutSeconds = std::stoul(config.getParameter("timeout_seconds", "30"));
    cloudConfig.maxRetries = std::stoul(config.getParameter("max_retries", "3"));

    return cloudConfig;
}

StorageConfig UnifiedCloudStorageReader::convertCloudConfigToStorageConfig(const CloudReaderConfig& config) const
{
    StorageConfig storageConfig;
    storageConfig.storage_type = StorageConstants::CLOUD_STORAGE;
    storageConfig.setParameter(StorageConstants::CLOUD_TYPE_KEY, cloudStorageTypeToString(config.storageType));
    storageConfig.setParameter(StorageConstants::ENDPOINT_KEY, config.endpoint);
    storageConfig.setParameter(StorageConstants::REGION_KEY, config.region);
    storageConfig.setParameter(StorageConstants::ACCESS_KEY_KEY, config.accessKeyId);
    storageConfig.setParameter(StorageConstants::SECRET_KEY_KEY, config.secretAccessKey);
    storageConfig.setParameter(StorageConstants::USE_SSL_KEY, config.useSSL ? "true" : "false");
    storageConfig.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, std::to_string(config.timeoutSeconds));
    storageConfig.setParameter(StorageConstants::MAX_RETRIES_KEY, std::to_string(config.maxRetries));

    return storageConfig;
}

std::string UnifiedCloudStorageReader::getCloudStorageTypeFromConfig(const StorageConfig& config) const
{
    return config.getParameter(StorageConstants::CLOUD_TYPE_KEY);
}

FileInfo UnifiedCloudStorageReader::convertCloudObjectToFileInfo(const CloudObject& cloudObj) const
{
    FileInfo fileInfo;
    fileInfo.path = buildPathFromBucketAndKey("", cloudObj.key); // We'll need to get bucket from context
    fileInfo.name = std::filesystem::path(cloudObj.key).filename().string();
    fileInfo.size = cloudObj.size;
    fileInfo.lastModified = cloudObj.lastModified;
    fileInfo.contentType = "application/octet-stream"; // Default, could be extracted from metadata
    fileInfo.isDirectory = false;                      // Cloud objects are always files
    fileInfo.metadata = cloudObj.metadata;

    return fileInfo;
}

FileListResult UnifiedCloudStorageReader::convertCloudListResultToFileListResult(
    const CloudListResult& cloudResult) const
{
    FileListResult result;
    result.success = cloudResult.success;
    result.message = cloudResult.message;
    result.errorCode = cloudResult.errorCode;
    result.duration = cloudResult.duration;
    result.totalSize = cloudResult.totalSize;
    result.count = cloudResult.count;
    result.isTruncated = cloudResult.isTruncated;
    result.nextMarker = cloudResult.nextMarker;

    // Convert CloudObjects to FileInfos
    for (const auto& cloudObj : cloudResult.objects)
    {
        result.files.push_back(convertCloudObjectToFileInfo(cloudObj));
    }

    return result;
}

FileResult UnifiedCloudStorageReader::convertCloudResultToFileResult(const CloudResult& cloudResult) const
{
    FileResult result;
    result.success = cloudResult.success;
    result.message = cloudResult.message;
    result.errorCode = cloudResult.errorCode;
    result.duration = cloudResult.duration;

    return result;
}

std::string UnifiedCloudStorageReader::extractBucketFromPath(const std::string& path) const
{
    // Always use the configured bucket from storage config
    std::string bucket = m_config.getParameter(StorageConstants::BUCKET_NAME_KEY);
    if (bucket.empty())
    {
        LOG(error) << "No bucket configured in storage config" << std::endl;
        return "";
    }

    LOG(verbose) << "Using configured bucket: '" << bucket << "' for path: '" << path << "'" << std::endl;
    return bucket;
}

std::string UnifiedCloudStorageReader::extractObjectKeyFromPath(const std::string& path) const
{
    // Remove leading slash if present, as MinIO/S3 object keys should not start with '/'
    std::string objectKey = path;
    if (!objectKey.empty() && objectKey[0] == '/')
    {
        objectKey = objectKey.substr(1);
    }

    LOG(verbose) << "Extracted object key: '" << objectKey << "' from path: '" << path << "'" << std::endl;
    return objectKey;
}

std::string UnifiedCloudStorageReader::buildPathFromBucketAndKey(const std::string& bucket,
                                                                 const std::string& key) const
{
    if (bucket.empty())
    {
        return key;
    }
    return bucket + "/" + key;
}

std::string UnifiedCloudStorageReader::cloudStorageTypeToString(CloudStorageType type) const
{
    switch (type)
    {
        case CloudStorageType::AWS_S3:
            return StorageConstants::AWS_S3_TYPE;
        case CloudStorageType::GOOGLE_CLOUD:
            return StorageConstants::GOOGLE_CLOUD_TYPE;
        case CloudStorageType::AZURE_BLOB:
            return StorageConstants::AZURE_BLOB_TYPE;
        case CloudStorageType::MINIO:
            return StorageConstants::MINIO_TYPE;
        default:
            return "unknown";
    }
}

CloudStorageType UnifiedCloudStorageReader::stringToCloudStorageType(const std::string& type) const
{
    if (type == StorageConstants::AWS_S3_TYPE)
        return CloudStorageType::AWS_S3;
    if (type == StorageConstants::GOOGLE_CLOUD_TYPE)
        return CloudStorageType::GOOGLE_CLOUD;
    if (type == StorageConstants::AZURE_BLOB_TYPE)
        return CloudStorageType::AZURE_BLOB;
    if (type == StorageConstants::MINIO_TYPE)
        return CloudStorageType::MINIO;
    return CloudStorageType::UNKNOWN;
}

} // namespace nv_vms
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

#include "minio_cloud_reader.h"
#include "logger.h"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace nv_vms
{

// MinIO-specific constants and configurations
const std::string MINIO_DEFAULT_REGION = "us-east-1";
const unsigned int MINIO_DEFAULT_PORT = 9000;

MinioCloudReader::MinioCloudReader()
    : m_client_initialized(false), m_use_ssl(true), m_timeout_seconds(30), m_max_retries(3)
{
    LOG(info) << "MinioCloudReader created" << std::endl;
}

MinioCloudReader::~MinioCloudReader()
{
    try
    {
        // Mark as shutting down to prevent new operations
        m_client_initialized = false;

        // Wait a bit for any ongoing operations to complete
        std::this_thread::sleep_for(std::chrono::milliseconds(100));

        shutdownMinioClient();
        LOG(info) << "MinioCloudReader destroyed" << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during MinioCloudReader destruction: " << e.what() << std::endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during MinioCloudReader destruction" << std::endl;
    }
}

bool MinioCloudReader::isAvailable() const
{
    // Check if we're in the process of shutting down
    if (!m_client_initialized)
    {
        return false;
    }

    return !m_endpoint_url.empty() && !m_access_key.empty() && !m_secret_key.empty();
}

bool MinioCloudReader::configure(const CloudReaderConfig& config)
{
    auto start_time = std::chrono::steady_clock::now();

    try
    {
        // Store configuration
        m_config = config;

        LOG(info) << "MinioCloudReader::configure called with:" << std::endl;
        LOG(info) << "  endpoint: '" << config.endpoint << "'" << std::endl;
        LOG(info) << "  accessKeyId: '" << (config.accessKeyId.empty() ? "EMPTY" : "SET") << "'" << std::endl;
        LOG(info) << "  secretAccessKey: '" << (config.secretAccessKey.empty() ? "EMPTY" : "SET") << "'" << std::endl;
        LOG(info) << "  region: '" << config.region << "'" << std::endl;
        LOG(info) << "  useSSL: " << (config.useSSL ? "true" : "false") << std::endl;
        LOG(info) << "  timeoutSeconds: " << config.timeoutSeconds << std::endl;
        LOG(info) << "  maxRetries: " << config.maxRetries << std::endl;

        // Extract MinIO-specific configuration
        m_endpoint_url = config.endpoint;
        m_access_key = config.accessKeyId;
        m_secret_key = config.secretAccessKey;
        m_session_token = config.sessionToken;
        m_region = config.region;
        m_use_ssl = config.useSSL;
        m_timeout_seconds = config.timeoutSeconds;
        m_max_retries = config.maxRetries;

        // Validate required parameters
        if (m_endpoint_url.empty())
        {
            setLastError("MinIO endpoint URL is required");
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CONFIG_ERROR");
            return false;
        }

        if (m_access_key.empty() || m_secret_key.empty())
        {
            setLastError("MinIO access key and secret key are required");
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CONFIG_ERROR");
            return false;
        }

        // Initialize MinIO client
        if (!initializeMinioClient())
        {
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INIT_ERROR");
            return false;
        }

        LOG(info) << "MinioCloudReader configured successfully for endpoint: " << m_endpoint_url << std::endl;

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError(std::string("Configuration failed: ") + e.what());
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
        return false;
    }
}

CloudReaderConfig MinioCloudReader::getConfiguration() const
{
    return m_config;
}

CloudListResult MinioCloudReader::listObjects(const std::string& bucket, const std::string& prefix, uint32_t maxKeys)
{
    return listObjectsPaginated(bucket, prefix, "", maxKeys);
}

CloudListResult MinioCloudReader::listObjectsPaginated(const std::string& bucket, const std::string& prefix,
                                                       const std::string& marker, uint32_t maxKeys)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudListResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty())
        {
            result.success = false;
            result.message = "Bucket name is required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        // Use MinIO SDK to list objects
        CloudResult listResult = listObjectsWithMinioClient(bucket, prefix, marker, maxKeys, result);
        if (!listResult.success)
        {
            result.success = false;
            result.message = listResult.message;
            result.errorCode = listResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                listResult.errorCode);
            return result;
        }
        result.bucket = bucket;
        result.prefix = prefix;

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list objects: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudListResult MinioCloudReader::listAllObjects(const std::string& bucket, const std::string& prefix)
{
    auto start_time = std::chrono::high_resolution_clock::now();
    CloudListResult combinedResult;
    combinedResult.success = false;
    combinedResult.bucket = bucket;
    combinedResult.prefix = prefix;

    std::string marker = "";
    uint32_t totalPages = 0;
    const uint32_t maxKeysPerPage = 1000; // MinIO/S3 maximum

    LOG(info) << "Starting listAllObjects for bucket: " << bucket << ", prefix: " << prefix << std::endl;

    do
    {
        totalPages++;
        CloudListResult pageResult = listObjectsPaginated(bucket, prefix, marker, maxKeysPerPage);

        if (!pageResult.success)
        {
            combinedResult.message = "Failed at page " + std::to_string(totalPages) + ": " + pageResult.message;
            combinedResult.errorCode = pageResult.errorCode;
            auto end_time = std::chrono::high_resolution_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            LOG(error) << "listAllObjects failed after " << totalPages << " pages, duration: " << duration.count() << "ms" << std::endl;
            return combinedResult;
        }

        // Append objects from this page
        combinedResult.objects.insert(combinedResult.objects.end(),
                                      pageResult.objects.begin(),
                                      pageResult.objects.end());
        combinedResult.count += pageResult.count;
        combinedResult.totalSize += pageResult.totalSize;

        LOG(verbose) << "Page " << totalPages << ": fetched " << pageResult.count
                     << " objects, total so far: " << combinedResult.count << std::endl;

        // Check if there are more pages
        if (pageResult.isTruncated && !pageResult.nextMarker.empty())
        {
            marker = pageResult.nextMarker;
        }
        else
        {
            // No more pages
            combinedResult.isTruncated = false;
            combinedResult.nextMarker = "";
            break;
        }

    } while (true);

    combinedResult.success = true;
    combinedResult.message = "Successfully listed all " + std::to_string(combinedResult.count) +
                            " objects in " + std::to_string(totalPages) + " pages";

    auto end_time = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
    LOG(info) << "listAllObjects completed: " << combinedResult.count << " objects from "
              << totalPages << " pages in " << duration.count() << "ms" << std::endl;

    return combinedResult;
}

CloudResult MinioCloudReader::downloadObject(const std::string& bucket, const std::string& objectKey,
                                             const std::string& localPath)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    LOG(info) << "MinioCloudReader::downloadObject called with bucket: '" << bucket << "', objectKey: '" << objectKey
              << "', localPath: '" << localPath << "'" << std::endl;

    try
    {
        // Sanitize object key: remove consecutive slashes (// → /)
        // This is needed because path generation may create // but MinIO validation rejects it
        std::string sanitizedObjectKey = objectKey;
        size_t pos;
        while ((pos = sanitizedObjectKey.find("//")) != std::string::npos) {
            sanitizedObjectKey.replace(pos, 2, "/");
        }

        // Input validation using base class method (now with sanitized key)
        if (!validateObjectName(sanitizedObjectKey))
        {
            result.success = false;
            result.message = "Cannot download file: invalid object name '" + sanitizedObjectKey + "'";
            result.errorCode = "INVALID_OBJECT_NAME";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_OBJECT_NAME");
            return result;
        }

        LOG(verbose) << "Sanitized object key: '" << objectKey << "' → '" << sanitizedObjectKey << "'" << std::endl;

        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty() || objectKey.empty() || localPath.empty())
        {
            result.success = false;
            result.message = "Bucket, object key, and local path are required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        LOG(info) << "Downloading object: " << objectKey << " to: " << localPath << std::endl;

        // Check if file already exists using base class method
        if (isLocalFileAccessible(localPath))
        {
            LOG(info) << "File already downloaded: " << localPath << std::endl;
            result.success = true;
            result.message = "File already exists";
            updateStats(true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -
                                                                                    start_time));
            return result;
        }

        // Create local directory if it doesn't exist using base class method
        ensureLocalDirectoryExists(std::filesystem::path(localPath).parent_path().string());

        // Record start time for performance monitoring
        auto download_start = std::chrono::high_resolution_clock::now();

        // Use MinIO SDK to download object (use sanitized key)
        CloudResult downloadResult = getObjectWithMinioClient(bucket, sanitizedObjectKey, localPath);
        if (!downloadResult.success)
        {
            result.success = false;
            result.message = downloadResult.message;
            result.errorCode = downloadResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                downloadResult.errorCode);
            return result;
        }

        // Calculate download duration and speed
        auto download_end = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(download_end - download_start);

        // Verify the downloaded file exists and get its size using base class methods
        if (isLocalFileAccessible(localPath))
        {
            auto downloaded_size = getLocalFileSize(localPath);
            double speed_mbps = calculateTransferSpeed(downloaded_size, duration);

            LOG(info) << "Successfully downloaded object: " << objectKey << " to: " << localPath << std::endl;
            LOG(info) << "Download completed in " << duration.count() << "ms at " << std::fixed << std::setprecision(2)
                      << speed_mbps << " MB/s" << std::endl;
            LOG(info) << "Downloaded file size: " << formatFileSize(downloaded_size) << std::endl;

            result.success = true;
            result.message = "Object downloaded successfully";
        }
        else
        {
            result.success = false;
            result.message = "Downloaded file does not exist at: " + localPath;
            result.errorCode = "FILE_NOT_FOUND";
        }

        updateStats(result.success, std::chrono::duration_cast<std::chrono::milliseconds>(
                                        std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during download: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudResult MinioCloudReader::checkObjectExists(const std::string& bucket, const std::string& objectKey)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    LOG(info) << "MinioCloudReader::checkObjectExists called with bucket: '" << bucket << "', objectKey: '" << objectKey
              << "'" << std::endl;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty() || objectKey.empty())
        {
            result.success = false;
            result.message = "Bucket and object key are required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        // Use MinIO SDK to check if object exists
        CloudResult checkResult = checkObjectExistsWithMinioClient(bucket, objectKey);
        if (!checkResult.success)
        {
            result.success = false;
            result.message = checkResult.message;
            result.errorCode = checkResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                checkResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Object exists";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object existence check: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudResult MinioCloudReader::listBuckets(std::vector<std::string>& buckets)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        // Use MinIO SDK to list buckets
        CloudResult listResult = listBucketsWithMinioClient(buckets);
        if (!listResult.success)
        {
            result.success = false;
            result.message = listResult.message;
            result.errorCode = listResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                listResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Buckets listed successfully";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list buckets: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudResult MinioCloudReader::checkBucketExists(const std::string& bucket)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty())
        {
            result.success = false;
            result.message = "Bucket name is required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        // Use MinIO SDK to check if bucket exists
        CloudResult checkResult = checkBucketExistsWithMinioClient(bucket);
        if (!checkResult.success)
        {
            result.success = false;
            result.message = checkResult.message;
            result.errorCode = checkResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                checkResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Bucket exists";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during bucket existence check: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudResult MinioCloudReader::generatePresignedUrl(const std::string& bucket, const std::string& objectKey,
                                                   uint32_t expirationSeconds, std::string& url)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty() || objectKey.empty())
        {
            result.success = false;
            result.message = "Bucket and object key are required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        // Use MinIO SDK to generate presigned URL
        CloudResult urlResult = generatePresignedUrlWithMinioClient(bucket, objectKey, expirationSeconds, url);
        if (!urlResult.success)
        {
            result.success = false;
            result.message = urlResult.message;
            result.errorCode = urlResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                urlResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Presigned URL generated successfully";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during presigned URL generation: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudResult MinioCloudReader::deleteObject(const std::string& bucket, const std::string& objectKey)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty() || objectKey.empty())
        {
            result.success = false;
            result.message = "Bucket and object key are required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "INVALID_PARAMETER");
            return result;
        }

        // Use MinIO SDK to delete object
        CloudResult deleteResult = deleteObjectWithMinioClient(bucket, objectKey);
        if (!deleteResult.success)
        {
            result.success = false;
            result.message = deleteResult.message;
            result.errorCode = deleteResult.errorCode;
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                deleteResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Object deleted successfully";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object deletion: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

CloudReaderStats MinioCloudReader::getStats() const
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    return m_stats;
}

void MinioCloudReader::resetStats()
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats = CloudReaderStats();
}

CloudResult MinioCloudReader::performHealthCheck()
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "CLIENT_ERROR");
            return result;
        }

        // Try to list buckets as a health check
        std::vector<std::string> buckets;
        CloudResult listResult = listBucketsWithMinioClient(buckets);
        if (!listResult.success)
        {
            result.success = false;
            result.message = "Health check failed: " + listResult.message;
            result.errorCode = "HEALTH_CHECK_FAILED";
            updateStats(
                false,
                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
                "HEALTH_CHECK_FAILED");
            return result;
        }

        result.success = true;
        result.message = "Health check passed";

        updateStats(
            true, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during health check: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(
            false, std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start_time),
            "EXCEPTION");
    }

    return result;
}

std::string MinioCloudReader::getLastError() const
{
    return m_lastError;
}

// Private implementation methods

bool MinioCloudReader::initializeMinioClient()
{
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        if (m_client_initialized)
        {
            return true;
        }

        // Build MinIO endpoint URL
        std::string endpoint = buildMinioEndpoint();
        LOG(info) << "Building MinIO endpoint: '" << endpoint << "' from URL: '" << m_endpoint_url << "'" << std::endl;

        // Parse the endpoint URL to extract host and port
        std::string host;
        int port = m_use_ssl ? 443 : 9000;

        if (endpoint.find("://") != std::string::npos)
        {
            // Extract host from URL like "http://127.0.0.1:9000"
            size_t protocol_end = endpoint.find("://") + 3;
            std::string host_port = endpoint.substr(protocol_end);

            size_t colon_pos = host_port.find(':');
            if (colon_pos != std::string::npos)
            {
                host = host_port.substr(0, colon_pos);
                port = std::stoi(host_port.substr(colon_pos + 1));
            }
            else
            {
                host = host_port;
            }
        }
        else
        {
            // Direct hostname without protocol
            host = endpoint;
        }

        LOG(info) << "Parsed MinIO endpoint - Host: '" << host << "', Port: " << port << std::endl;

        // Create MinIO client using the correct constructor
        minio::s3::BaseUrl base_url;
        base_url.host = host;
        base_url.https = m_use_ssl;
        base_url.port = port;

        LOG(info) << "Creating MinIO client with host: '" << base_url.host << "', port: " << base_url.port << ", SSL: " << (base_url.https ? "enabled" : "disabled") << std::endl;

        // Create credential provider
        m_credentials = std::make_unique<minio::creds::StaticProvider>(
            m_access_key, m_secret_key, m_session_token);

        // Create MinIO client
        minio::s3::Client client(base_url, m_credentials.get());

        // Test connection by listing buckets
        LOG(info) << "Testing MinIO connection by listing buckets..." << std::endl;
        auto response = client.ListBuckets();
        if (!response)
        {
            std::string error_msg = "Failed to connect to MinIO server: " + response.Error().String();
            LOG(error) << error_msg << std::endl;
            setLastError(error_msg);
            return false;
        }

        m_minio_client = std::make_unique<minio::s3::Client>(client);
        m_client_initialized = true;

        LOG(info) << "MinIO client initialized successfully" << std::endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError(std::string("Failed to initialize MinIO client: ") + e.what());
        return false;
    }
}

void MinioCloudReader::shutdownMinioClient()
{
    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);
        m_minio_client.reset();
        m_client_initialized = false;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during MinIO client shutdown: " << e.what() << std::endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception during MinIO client shutdown" << std::endl;
    }
}

bool MinioCloudReader::ensureClientInitialized()
{
    // If we're shutting down, don't try to initialize
    if (!m_client_initialized)
    {
        // Check if we have the required configuration
        if (m_endpoint_url.empty() || m_access_key.empty() || m_secret_key.empty())
        {
            return false;
        }

        return initializeMinioClient();
    }
    return true;
}

CloudResult MinioCloudReader::getObjectWithMinioClient(const std::string& bucket, const std::string& objectKey,
                                                       const std::string& localPath)
{
    CloudResult result;

    LOG(info) << "MinioCloudReader::getObjectWithMinioClient called with bucket: '" << bucket << "', objectKey: '"
              << objectKey << "', localPath: '" << localPath << "'" << std::endl;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Create download arguments
        minio::s3::DownloadObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;
        args.filename = localPath;

        LOG(info) << "Starting download from bucket: " << bucket << std::endl;

        // Download the file directly to local path using DownloadObject
        minio::s3::DownloadObjectResponse resp = m_minio_client->DownloadObject(args);
        if (!resp)
        {
            result.success = false;
            result.message =
                "Failed to download object " + objectKey + " from bucket " + bucket + ": " + resp.Error().String();
            result.errorCode = "DOWNLOAD_OBJECT_ERROR";
            LOG(error) << "MinIO download failed: " << result.message << std::endl;
            return result;
        }

        result.success = true;
        result.message = "Object downloaded successfully";
        LOG(info) << "MinIO download successful: " << result.message << std::endl;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object download: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::statObjectWithMinioClient(const std::string& bucket, const std::string& objectKey,
                                                        CloudObject& objectInfo)
{
    CloudResult result;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::StatObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;

        auto response = m_minio_client->StatObject(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to stat object from MinIO: " + response.Error().String();
            result.errorCode = "STAT_OBJECT_ERROR";
            return result;
        }

        // Populate object info
        objectInfo.key = objectKey;
        objectInfo.size = response.size;
        objectInfo.lastModified = response.last_modified.ToISO8601UTC();
        objectInfo.etag = response.etag;

        // Try to get content type from headers
        auto headers = response.headers;
        if (headers.Contains("content-type"))
        {
            auto content_types = headers.Get("content-type");
            if (!content_types.empty())
            {
                objectInfo.metadata["content-type"] = content_types.front();
            }
            else
            {
                objectInfo.metadata["content-type"] = "application/octet-stream";
            }
        }
        else if (headers.Contains("Content-Type"))
        {
            auto content_types = headers.Get("Content-Type");
            if (!content_types.empty())
            {
                objectInfo.metadata["content-type"] = content_types.front();
            }
            else
            {
                objectInfo.metadata["content-type"] = "application/octet-stream";
            }
        }
        else
        {
            objectInfo.metadata["content-type"] = "application/octet-stream"; // Default
        }

        result.success = true;
        result.message = "Object info retrieved successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object stat: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::listObjectsWithMinioClient(const std::string& bucket, const std::string& prefix,
                                                         const std::string& marker, uint32_t maxKeys,
                                                         CloudListResult& result)
{

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::ListObjectsArgs args;
        args.bucket = bucket;
        if (!prefix.empty())
        {
            args.prefix = prefix;
        }
        if (!marker.empty())
        {
            args.marker = marker;
        }
        args.max_keys = maxKeys;

        auto response = m_minio_client->ListObjects(args);
        if (!static_cast<bool>(response))
        {
            result.success = false;
            result.message = "Failed to list objects from MinIO";
            result.errorCode = "LIST_OBJECTS_ERROR";
            CloudResult errResult;
            errResult.success = result.success;
            errResult.message = result.message;
            errResult.errorCode = result.errorCode;
            return errResult;
        }

        // Process objects - iterate through the response using the iterator pattern
        // ListObjectsResult is an iterator itself, so we need to use it directly
        while (static_cast<bool>(response))
        {
            CloudObject obj;
            obj.key = (*response).name;
            obj.size = (*response).size;
            obj.lastModified = (*response).last_modified.ToISO8601UTC();
            obj.etag = (*response).etag;

            result.objects.push_back(obj);
            result.totalSize += obj.size;

            ++response; // Move to next item
        }

        result.count = result.objects.size();
        // Note: ListObjectsResult doesn't expose is_truncated and next_marker directly
        // These would need to be extracted differently if needed
        result.isTruncated = false; // Default value
        result.nextMarker = "";     // Default value

        result.success = true;
        result.message = "Objects listed successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list objects: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    CloudResult base;
    base.success = result.success;
    base.message = result.message;
    base.errorCode = result.errorCode;
    base.duration = result.duration;
    return base;
}

CloudResult MinioCloudReader::listBucketsWithMinioClient(std::vector<std::string>& buckets)
{
    CloudResult result;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        auto response = m_minio_client->ListBuckets();
        if (!response)
        {
            result.success = false;
            result.message = "Failed to list buckets from MinIO: " + response.Error().String();
            result.errorCode = ErrorCodes::LIST_BUCKETS_ERROR;
            return result;
        }

        // Extract bucket names from the response
        for (const auto& bucket : response.buckets)
        {
            buckets.push_back(bucket.name);
        }

        result.success = true;
        result.message = "Buckets listed successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during list buckets: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::checkBucketExistsWithMinioClient(const std::string& bucket)
{
    CloudResult result;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Use BucketExists to check if bucket exists
        minio::s3::BucketExistsArgs args;
        args.bucket = bucket;

        auto response = m_minio_client->BucketExists(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to check bucket existence: " + response.Error().String();
            result.errorCode = "BUCKET_EXISTS_ERROR";
            return result;
        }

        if (!response.exist)
        {
            result.success = false;
            result.message = "Bucket does not exist";
            result.errorCode = "BUCKET_NOT_FOUND";
            return result;
        }

        result.success = true;
        result.message = "Bucket exists";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during bucket existence check: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::checkObjectExistsWithMinioClient(const std::string& bucket,
                                                              const std::string& objectKey)
{
    CloudResult result;

    LOG(verbose) << "MinioCloudReader::checkObjectExistsWithMinioClient called with bucket: '" << bucket << "', objectKey: '" << objectKey << "'" << std::endl;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Use StatObject to check if object exists
        minio::s3::StatObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;

        auto response = m_minio_client->StatObject(args);
        if (!response)
        {
            result.success = false;
            result.message = "Object does not exist";
            result.errorCode = "OBJECT_NOT_FOUND";
            LOG(warning) << "MinIO object check failed: " << result.message << " for bucket: " << bucket << " and objectKey: " << objectKey << std::endl;
            return result;
        }

        result.success = true;
        result.message = "Object exists";
        LOG(info) << "MinIO object check successful: " << result.message << " for bucket: " << bucket << " and objectKey: " << objectKey << std::endl;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object existence check: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::generatePresignedUrlWithMinioClient(const std::string& bucket,
                                                                  const std::string& objectKey,
                                                                  uint32_t expirationSeconds, std::string& url)
{
    CloudResult result;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::GetPresignedObjectUrlArgs args;
        args.bucket = bucket;
        args.object = objectKey;
        args.method = minio::http::Method::kGet;  // Required: Set HTTP method for presigned URL
        args.expiry_seconds = expirationSeconds;   // Set expiration time

        auto response = m_minio_client->GetPresignedObjectUrl(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to generate presigned URL: " + response.Error().String();
            result.errorCode = "PRESIGNED_URL_ERROR";
            return result;
        }

        url = response.url;
        result.success = true;
        result.message = "Presigned URL generated successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during presigned URL generation: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

std::string MinioCloudReader::buildMinioEndpoint() const
{
    std::string endpoint = m_endpoint_url;

    // For MinIO, we don't add region to the endpoint URL like we do for AWS S3
    // MinIO endpoints are typically direct URLs like http://localhost:9000
    // The region is used for signing requests, not for the endpoint URL

    LOG(info) << "MinioCloudReader::buildMinioEndpoint - Original URL: '" << m_endpoint_url << "', Region: '" << m_region << "'" << std::endl;
    LOG(info) << "MinioCloudReader::buildMinioEndpoint - Final endpoint: '" << endpoint << "'" << std::endl;

    return endpoint;
}

std::string MinioCloudReader::getRequiredConfigParameter(const std::string& key) const
{
    // This method can be used to extract required parameters from config
    // For now, return empty string as parameters are stored directly
    return "";
}

CloudResult MinioCloudReader::deleteObjectWithMinioClient(const std::string& bucket, const std::string& objectKey)
{
    CloudResult result;

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        if (bucket.empty() || objectKey.empty())
        {
            result.success = false;
            result.message = "Bucket and object key are required";
            result.errorCode = "INVALID_PARAMETER";
            return result;
        }

        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            return result;
        }

        LOG(info) << "Deleting object: " << objectKey << " from bucket: " << bucket << std::endl;

        minio::s3::RemoveObjectArgs args;
        args.bucket = bucket;
        args.object = objectKey;

        auto response = m_minio_client->RemoveObject(args);
        if (!response)
        {
            result.success = false;
            result.message = "Failed to delete object: " + objectKey + " from bucket: " + bucket +
                           ": " + response.Error().String();
            result.errorCode = "DELETE_OBJECT_ERROR";
            return result;
        }

        result.success = true;
        result.message = "Object deleted successfully";
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during object deletion: ") + e.what();
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult MinioCloudReader::getObjectInfo(const std::string& bucket, const std::string& objectKey, CloudObject& objectInfo)
{
    auto start_time = std::chrono::steady_clock::now();
    CloudResult result;

    try
    {
        if (!ensureClientInitialized())
        {
            result.success = false;
            result.message = "MinIO client not initialized";
            result.errorCode = "CLIENT_ERROR";
            updateStats(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), "CLIENT_ERROR");
            return result;
        }

        if (bucket.empty() || objectKey.empty())
        {
            result.success = false;
            result.message = "Bucket and object key are required";
            result.errorCode = "INVALID_PARAMETER";
            updateStats(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), "INVALID_PARAMETER");
            return result;
        }

        LOG(info) << "Getting object info for: " << objectKey << std::endl;

        // Use MinIO SDK to get object info
        CloudResult statResult = statObjectWithMinioClient(bucket, objectKey, objectInfo);
        if (!statResult.success)
        {
            result.success = false;
            result.message = statResult.message;
            result.errorCode = statResult.errorCode;
            updateStats(false, std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start_time), statResult.errorCode);
            return result;
        }

        LOG(info) << "Object info retrieved successfully:" << std::endl;
        LOG(info) << "  Object: " << objectKey << std::endl;
        LOG(info) << "  Size: " << objectInfo.size << " bytes" << std::endl;
        LOG(info) << "  ETag: " << objectInfo.etag << std::endl;
        LOG(info) << "  Last Modified: " << objectInfo.lastModified << std::endl;

        result.success = true;
        result.message = "Object info retrieved successfully";

        updateStats(true, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time));
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = std::string("Exception during get object info: ") + e.what();
        result.errorCode = "EXCEPTION";
        updateStats(false, std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time), "EXCEPTION");
    }

    return result;
}

} // namespace nv_vms
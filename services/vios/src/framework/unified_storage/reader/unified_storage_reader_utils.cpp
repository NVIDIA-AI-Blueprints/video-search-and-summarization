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

#include "unified_storage_reader_utils.h"
#include <chrono>
#include <exception>
#include <thread>
#include <queue>
#include <condition_variable>
#include <algorithm>
#include <atomic>

namespace nv_vms
{

// Static member initialization
std::string UnifiedStorageReaderUtils::m_lastError;
std::mutex UnifiedStorageReaderUtils::m_mutex;

std::shared_ptr<UnifiedStorageReader> UnifiedStorageReaderUtils::createStorageReader(const DeviceConfig& deviceConfig)
{
    try
    {
        // Debug logging
        LOG(info) << "=== Unified Storage Reader Configuration Debug ===" << std::endl;
        LOG(info) << "enable_cloud_storage: " << (deviceConfig.enable_cloud_storage ? "true" : "false") << std::endl;
        LOG(info) << "cloud_storage_type: '" << deviceConfig.cloud_storage_type << "'" << std::endl;
        LOG(info) << "cloud_storage_endpoint: '" << deviceConfig.cloud_storage_endpoint << "'" << std::endl;
        LOG(info) << "cloud_storage_bucket: '" << deviceConfig.cloud_storage_bucket << "'" << std::endl;
        LOG(info) << "cloud_storage_access_key: '" << (deviceConfig.cloud_storage_access_key.empty() ? "EMPTY" : "SET")
                  << "'" << std::endl;
        LOG(info) << "cloud_storage_secret_key: '" << (deviceConfig.cloud_storage_secret_key.empty() ? "EMPTY" : "SET")
                  << "'" << std::endl;

        // Determine storage type based on configuration
        StorageType storageType;
        bool enableStorage = false;

        if (deviceConfig.enable_cloud_storage)
        {
            LOG(info) << "Cloud storage is enabled, checking parameters..." << std::endl;

            // Validate cloud storage configuration
            if (!validateCloudStorageConfig(deviceConfig))
            {
                LOG(warning) << "Cloud storage configuration validation failed, falling back to local storage"
                             << std::endl;
                storageType = StorageType::LOCAL;
                enableStorage = true;
                LOG(info) << "Selected storage type: LOCAL (fallback)" << std::endl;
            }
            else
            {
                storageType = StorageType::CLOUD;
                enableStorage = true;
                LOG(info) << "Selected storage type: CLOUD (type: " << deviceConfig.cloud_storage_type << ")"
                          << std::endl;
            }
        }
        else
        {
            // Use local storage
            storageType = StorageType::LOCAL;
            enableStorage = true;
            LOG(info) << "Cloud storage is disabled, using local storage" << std::endl;
        }

        if (!enableStorage)
        {
            LOG(info) << "No storage configuration found, using local storage by default" << std::endl;
            storageType = StorageType::LOCAL;
        }

        // Configure storage settings based on type
        LOG(info) << "Configuring storage settings for type: "
                  << (storageType == StorageType::CLOUD ? "CLOUD" : "LOCAL") << std::endl;

        StorageConfig storageConfig = createStorageConfig(deviceConfig);

        // Create unified storage reader
        LOG(info) << "Creating unified storage reader for type: "
                  << (storageType == StorageType::CLOUD ? StorageConstants::CLOUD_STORAGE
                                                        : StorageConstants::LOCAL_STORAGE)
                  << std::endl;

        auto storageReader = UnifiedStorageReaderFactory::createReader(storageType, storageConfig);

        if (!storageReader)
        {
            m_lastError = "Failed to create unified storage reader for " +
                          (storageType == StorageType::CLOUD ? StorageConstants::CLOUD_STORAGE
                                                             : StorageConstants::LOCAL_STORAGE) +
                          " storage";
            LOG(error) << m_lastError << std::endl;
            return nullptr;
        }

        LOG(info) << "Unified storage reader created successfully" << std::endl;
        return storageReader;
    }
    catch (const std::exception& e)
    {
        m_lastError = "Exception during unified storage reader creation: " + std::string(e.what());
        LOG(error) << m_lastError << std::endl;
        return nullptr;
    }
}

bool UnifiedStorageReaderUtils::getFile(std::shared_ptr<UnifiedStorageReader> reader, const std::string& remote_path,
                                  std::string& local_path)
{
    LOG(info) << "UnifiedStorageReaderUtils::getFile called with remote_path: '" << remote_path << "' and local_path: '"
              << local_path << "'" << std::endl;

    if (!reader)
    {
        m_lastError = "Storage reader is null";
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    if (!isCloudStorageEnabled(reader))
    {
        m_lastError = "Cloud storage is not enabled for this reader";
        LOG(warning) << m_lastError << std::endl;
        return false;
    }

    try
    {
        // Check if file already exists locally
        if (std::filesystem::exists(local_path))
        {
            LOG(info) << "File already exists locally: " << local_path << std::endl;
            return true;
        }

        LOG(info) << "Downloading file from cloud: " << remote_path << " to " << local_path << std::endl;

        // Download the file using unified storage reader
        FileResult result = reader->downloadFile(remote_path, local_path);

        if (result.success)
        {
            LOG(info) << "Successfully downloaded file: " << remote_path << " (" << result.bytes_read << " bytes) in "
                      << result.duration.count() << "ms"
                      << " to " << local_path << std::endl;

            return true;
        }
        else
        {
            m_lastError = "Failed to download file: " + remote_path + " - " + result.message;
            LOG(error) << m_lastError << std::endl;
            return false;
        }
    }
    catch (const std::exception& e)
    {
        m_lastError = "Exception during file download: " + std::string(e.what());
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}

bool UnifiedStorageReaderUtils::getFilesSync(std::shared_ptr<UnifiedStorageReader> reader,
                                       const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                       DownloadCompletionCallback completionCallback,
                                       DownloadProgressCallback progressCallback)
{
    LOG(info) << "UnifiedStorageReaderUtils::getFilesSync called with " << remoteLocalPairs.size() << " files" << std::endl;

    if (!reader)
    {
        m_lastError = "Storage reader is null";
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    if (!isCloudStorageEnabled(reader))
    {
        m_lastError = "Cloud storage is not enabled for this reader";
        LOG(warning) << m_lastError << std::endl;
        return false;
    }

    if (remoteLocalPairs.empty())
    {
        m_lastError = "No files provided for download";
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    try
    {
        LOG(info) << "Downloading " << remoteLocalPairs.size() << " files synchronously" << std::endl;

        // Create a DownloadResult to collect all file results
        DownloadResult downloadResult;
        downloadResult.total_files = remoteLocalPairs.size();
        downloadResult.successful_downloads = 0;
        downloadResult.failed_downloads = 0;
        downloadResult.total_bytes_downloaded = 0;
        downloadResult.total_duration = std::chrono::milliseconds(0);
        
        auto startTime = std::chrono::high_resolution_clock::now();

        // Download all files synchronously
        for (size_t i = 0; i < remoteLocalPairs.size(); ++i)
        {
            const auto& pair = remoteLocalPairs[i];
            const std::string& remotePath = pair.first;
            const std::string& localPath = pair.second;

            LOG(info) << "Downloading file " << (i + 1) << "/" << remoteLocalPairs.size() 
                      << ": " << remotePath << " -> " << localPath << std::endl;

            // Call progress callback if provided
            if (progressCallback)
            {
                progressCallback(remotePath, i, remoteLocalPairs.size(), 0.0);
            }

            // Check if file already exists locally
            if (std::filesystem::exists(localPath))
            {
                LOG(info) << "File already exists locally: " << localPath << std::endl;
                
                // Create a success result for existing file
                FileDownloadResult fileResult;
                fileResult.remote_path = remotePath;
                fileResult.local_path = localPath;
                fileResult.success = true;
                fileResult.bytes_downloaded = std::filesystem::file_size(localPath);
                fileResult.duration = std::chrono::milliseconds(0);
                fileResult.was_skipped = true;
                
                // Add to overall result
                downloadResult.addResult(fileResult);
                continue;
            }

            // Ensure directory exists
            std::filesystem::path path(localPath);
            if (!std::filesystem::exists(path.parent_path()))
            {
                if (!std::filesystem::create_directories(path.parent_path()))
                {
                    LOG(warning) << "Failed to create directory for: " << path.parent_path().string() 
                                 << ", skipping file: " << remotePath << std::endl;
                    
                    // Create a failure result
                    FileDownloadResult fileResult;
                    fileResult.remote_path = remotePath;
                    fileResult.local_path = localPath;
                    fileResult.success = false;
                    fileResult.error_message = "Failed to create directory: " + path.parent_path().string();
                    fileResult.duration = std::chrono::milliseconds(0);
                    
                    // Add to overall result
                    downloadResult.addResult(fileResult);
                    continue;
                }
            }

            // Download file synchronously
            FileResult fileResult = reader->downloadFile(remotePath, localPath);
            
            // Create download result
            FileDownloadResult result;
            result.remote_path = remotePath;
            result.local_path = localPath;
            result.success = fileResult.success;
            result.bytes_downloaded = fileResult.bytes_read;
            result.duration = fileResult.duration;
            
            if (fileResult.success)
            {
                LOG(info) << "Successfully downloaded file: " << remotePath 
                          << " (" << fileResult.bytes_read << " bytes) in " << fileResult.duration.count() << "ms" << std::endl;
            }
            else
            {
                result.error_message = fileResult.message;
                LOG(error) << "Failed to download file: " << remotePath << " - " << fileResult.message << std::endl;
            }

            // Add to overall result
            downloadResult.addResult(result);
        }

        // Calculate total duration
        auto endTime = std::chrono::high_resolution_clock::now();
        downloadResult.total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
        
        // Finalize the result
        downloadResult.finalize();
        
        // Call completion callback if provided
        if (completionCallback)
        {
            completionCallback("sync_download", downloadResult);
        }

        // Log final summary
        if (downloadResult.overall_success)
        {
            LOG(info) << "All " << downloadResult.successful_downloads << " files downloaded successfully in " 
                      << downloadResult.total_duration.count() << "ms (" << downloadResult.total_bytes_downloaded << " bytes total)" << std::endl;
            return true;
        }
        else
        {
            LOG(warning) << "Downloaded " << downloadResult.successful_downloads << " out of " << downloadResult.total_files 
                         << " files successfully" << std::endl;
            m_lastError = "Some files failed to download: " + std::to_string(downloadResult.successful_downloads) + 
                          " out of " + std::to_string(downloadResult.total_files) + " successful";
            return false;
        }
    }
    catch (const std::exception& e)
    {
        m_lastError = "Exception during synchronous file download: " + std::string(e.what());
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}

bool UnifiedStorageReaderUtils::getFiles(std::shared_ptr<UnifiedStorageReader> reader,
                                         const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                                         std::string& asyncSessionId,
                                         DownloadCompletionCallback completionCallback,
                                         DownloadProgressCallback progressCallback)
{
    LOG(info) << "UnifiedStorageReaderUtils::getFiles called with " << remoteLocalPairs.size() 
              << " files (first sync, rest async)" << std::endl;

    if (!reader)
    {
        m_lastError = "Storage reader is null";
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    if (!isCloudStorageEnabled(reader))
    {
        m_lastError = "Cloud storage is not enabled for this reader";
        LOG(warning) << m_lastError << std::endl;
        return false;
    }

    if (remoteLocalPairs.empty())
    {
        m_lastError = "No files provided for download";
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    try
    {
        // Step 1: Download the first file synchronously
        const auto& firstPair = remoteLocalPairs[0];
        const std::string& firstRemotePath = firstPair.first;
        const std::string& firstLocalPath = firstPair.second;

        LOG(info) << "Downloading first file synchronously: " << firstRemotePath << " -> " << firstLocalPath << std::endl;

        // Check if first file already exists locally
        if (std::filesystem::exists(firstLocalPath))
        {
            LOG(info) << "First file already exists locally: " << firstLocalPath << std::endl;
        }
        else
        {
            // Ensure directory exists for first file
            std::filesystem::path firstPath(firstLocalPath);
            if (!std::filesystem::exists(firstPath.parent_path()))
            {
                if (!std::filesystem::create_directories(firstPath.parent_path()))
                {
                    m_lastError = "Failed to create directory for first file: " + firstPath.parent_path().string();
                    LOG(error) << m_lastError << std::endl;
                    return false;
                }
            }

            // Download first file synchronously
            FileResult firstResult = reader->downloadFile(firstRemotePath, firstLocalPath);
            if (!firstResult.success)
            {
                m_lastError = "Failed to download first file: " + firstRemotePath + " - " + firstResult.message;
                LOG(error) << m_lastError << std::endl;
                return false;
            }

            LOG(info) << "Successfully downloaded first file: " << firstRemotePath 
                      << " (" << firstResult.bytes_read << " bytes) in " << firstResult.duration.count() << "ms" << std::endl;
        }

        // Step 2: If there are more files, download them asynchronously
        if (remoteLocalPairs.size() > 1)
        {
            LOG(info) << "Starting async download for remaining " << (remoteLocalPairs.size() - 1) << " files" << std::endl;

            // Create vector of remaining files (skip the first one)
            std::vector<std::pair<std::string, std::string>> remainingFiles;
            remainingFiles.reserve(remoteLocalPairs.size() - 1);
            
            for (size_t i = 1; i < remoteLocalPairs.size(); ++i)
            {
                const auto& pair = remoteLocalPairs[i];
                const std::string& remotePath = pair.first;
                const std::string& localPath = pair.second;

                // Ensure directory exists for each remaining file
                std::filesystem::path path(localPath);
                if (!std::filesystem::exists(path.parent_path()))
                {
                    if (!std::filesystem::create_directories(path.parent_path()))
                    {
                        LOG(warning) << "Failed to create directory for: " << path.parent_path().string() 
                                     << ", skipping file: " << remotePath << std::endl;
                        continue;
                    }
                }

                remainingFiles.emplace_back(remotePath, localPath);
            }

            if (!remainingFiles.empty())
            {
                // Start async download for remaining files using unified interface
                asyncSessionId = reader->downloadFilesWithPathsAsync(
                    remainingFiles,
                    completionCallback,
                    progressCallback
                );

                if (asyncSessionId.empty())
                {
                    m_lastError = "Failed to start async download for remaining files";
                    LOG(error) << m_lastError << std::endl;
                    return false;
                }

                LOG(info) << "Started async download session: " << asyncSessionId 
                          << " for " << remainingFiles.size() << " files" << std::endl;
            }
            else
            {
                LOG(info) << "No valid remaining files to download asynchronously" << std::endl;
                asyncSessionId = "";
            }
        }
        else
        {
            LOG(info) << "Only one file provided, no async download needed" << std::endl;
            asyncSessionId = "";
        }

        LOG(info) << "Hybrid download completed successfully - First file: sync, Remaining: async session " 
                  << (asyncSessionId.empty() ? "none" : asyncSessionId) << std::endl;
        return true;
    }
    catch (const std::exception& e)
    {
        m_lastError = "Exception during hybrid file download: " + std::string(e.what());
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}



bool UnifiedStorageReaderUtils::isCloudStorageEnabled(std::shared_ptr<UnifiedStorageReader> reader)
{
    if (!reader)
    {
        return false;
    }

    // Check if this is a cloud storage reader by checking the storage type
    // This is a simplified check - in a real implementation, you might want to
    // add a method to the reader interface to check if it supports cloud operations
    return getStorageType(reader) == StorageType::CLOUD;
}

StorageType UnifiedStorageReaderUtils::getStorageType(std::shared_ptr<UnifiedStorageReader> reader)
{
    if (!reader)
    {
        return StorageType::LOCAL; // Default fallback
    }

    StorageType storageType = reader->getStorageType();
    return storageType;
}

std::string UnifiedStorageReaderUtils::getLastError()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_lastError;
}

bool UnifiedStorageReaderUtils::validateCloudStorageConfig(const DeviceConfig& deviceConfig)
{
    // Check if required cloud storage parameters are set
    if (deviceConfig.cloud_storage_type.empty() || deviceConfig.cloud_storage_endpoint.empty() ||
        deviceConfig.cloud_storage_access_key.empty() || deviceConfig.cloud_storage_secret_key.empty() ||
        deviceConfig.cloud_storage_bucket.empty())
    {
        LOG(warning) << "Cloud storage is enabled but required parameters are missing" << std::endl;
        LOG(warning) << "  cloud_storage_type: '" << deviceConfig.cloud_storage_type << "'" << std::endl;
        LOG(warning) << "  cloud_storage_endpoint: '" << deviceConfig.cloud_storage_endpoint << "'" << std::endl;
        LOG(warning) << "  cloud_storage_access_key: '"
                     << (deviceConfig.cloud_storage_access_key.empty() ? "EMPTY" : "SET") << "'" << std::endl;
        LOG(warning) << "  cloud_storage_secret_key: '"
                     << (deviceConfig.cloud_storage_secret_key.empty() ? "EMPTY" : "SET") << "'" << std::endl;
        LOG(warning) << "  cloud_storage_bucket: '" << deviceConfig.cloud_storage_bucket << "'" << std::endl;
        return false;
    }

    // Validate cloud storage type
    std::string cloudType = deviceConfig.cloud_storage_type;
    LOG(info) << "Validating cloud storage type: '" << cloudType << "'" << std::endl;

    if (cloudType.empty() ||
        (cloudType != StorageConstants::AWS_S3_TYPE && cloudType != StorageConstants::GOOGLE_CLOUD_TYPE &&
         cloudType != StorageConstants::AZURE_BLOB_TYPE && cloudType != StorageConstants::MINIO_TYPE))
    {
        LOG(warning) << "Invalid cloud storage type: '" << cloudType
                     << "'. Valid types are: " << StorageConstants::AWS_S3_TYPE << ", "
                     << StorageConstants::GOOGLE_CLOUD_TYPE << ", " << StorageConstants::AZURE_BLOB_TYPE << ", "
                     << StorageConstants::MINIO_TYPE << "." << std::endl;
        return false;
    }

    return true;
}

StorageConfig UnifiedStorageReaderUtils::createStorageConfig(const DeviceConfig& deviceConfig)
{
    StorageConfig storageConfig;

    // Determine storage type based on configuration
    StorageType storageType = StorageType::LOCAL;
    if (deviceConfig.enable_cloud_storage && validateCloudStorageConfig(deviceConfig))
    {
        storageType = StorageType::CLOUD;
    }

    if (storageType == StorageType::CLOUD)
    {
        // Configure cloud storage settings
        storageConfig.storage_type = StorageConstants::CLOUD_STORAGE;

        LOG(info) << "Setting cloud_type parameter to: '" << deviceConfig.cloud_storage_type << "'" << std::endl;

        // Ensure cloud_type is not empty
        if (deviceConfig.cloud_storage_type.empty())
        {
            LOG(error) << "Cloud storage type is empty, cannot configure cloud storage. This should not happen after "
                          "validation."
                       << std::endl;
            return storageConfig;
        }

        storageConfig.setParameter(StorageConstants::CLOUD_TYPE_KEY, deviceConfig.cloud_storage_type);
        storageConfig.setParameter(StorageConstants::ENDPOINT_KEY, deviceConfig.cloud_storage_endpoint);
        storageConfig.setParameter(StorageConstants::ACCESS_KEY_KEY, deviceConfig.cloud_storage_access_key);
        storageConfig.setParameter(StorageConstants::SECRET_KEY_KEY, deviceConfig.cloud_storage_secret_key);
        storageConfig.setParameter(StorageConstants::BUCKET_NAME_KEY, deviceConfig.cloud_storage_bucket);
        storageConfig.setParameter(StorageConstants::REGION_KEY, deviceConfig.cloud_storage_region.empty()
                                                                     ? "us-east-1"
                                                                     : deviceConfig.cloud_storage_region);
        storageConfig.setParameter(StorageConstants::USE_SSL_KEY,
                                   deviceConfig.cloud_storage_use_ssl ? "true" : "false");

        // Configure download worker settings for cloud storage (using hardcoded defaults)
        storageConfig.setParameter("max_worker_threads", "1");
        storageConfig.setParameter("max_concurrent_downloads", "1");
        storageConfig.setParameter("download_timeout_seconds", "300");
        storageConfig.setParameter("enable_resume_download", "true");
        storageConfig.setParameter("chunk_size_bytes", "8388608"); // 8MB
        storageConfig.setParameter("enable_progress_callback", "true");
        storageConfig.setParameter("temp_download_dir", "/tmp/vst_downloads");

        LOG(info) << "Cloud storage (" << deviceConfig.cloud_storage_type << ") configured successfully" << std::endl;
        LOG(info) << "  Endpoint: " << deviceConfig.cloud_storage_endpoint << std::endl;
        LOG(info) << "  Bucket: " << deviceConfig.cloud_storage_bucket << std::endl;
        LOG(info) << "  Region: "
                  << (deviceConfig.cloud_storage_region.empty() ? "us-east-1" : deviceConfig.cloud_storage_region)
                  << std::endl;
        LOG(info) << "  SSL: " << (deviceConfig.cloud_storage_use_ssl ? "enabled" : "disabled") << std::endl;
    }
    else
    {
        // Configure local storage settings
        storageConfig.storage_type = StorageConstants::LOCAL_STORAGE;
        storageConfig.setParameter(StorageConstants::BASE_PATH_KEY, deviceConfig.recorded_video_root); // Root path for local storage
        storageConfig.setParameter(StorageConstants::ENABLE_FILE_OPERATIONS_KEY, "true");
        storageConfig.setParameter(StorageConstants::ENABLE_DIRECTORY_OPERATIONS_KEY, "true");

        LOG(info) << "Local storage configured successfully" << std::endl;
    }

    return storageConfig;
}

} // namespace nv_vms
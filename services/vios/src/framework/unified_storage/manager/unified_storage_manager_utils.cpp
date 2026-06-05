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

#include "unified_storage_manager_utils.h"
#include "unified_storage_manager_factory.h"
#include "../unified_storage_types.h"
#include "logger.h"
#include "device_manager.h"
#include <chrono>
#include <exception>
#include <thread>
#include <queue>
#include <condition_variable>
#include <algorithm>
#include <atomic>
#include <regex>
#include <filesystem>

namespace nv_vms
{

// Static member initialization
std::string UnifiedStorageManagerUtils::m_lastError;
std::mutex UnifiedStorageManagerUtils::m_mutex;

std::shared_ptr<UnifiedStorageManager> UnifiedStorageManagerUtils::initializeStorageManager(const DeviceConfig& deviceConfig)
{
    try
    {
        // Debug logging
        LOG(info) << "=== Unified Storage Manager Configuration Debug ===" << std::endl;
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

        StorageConfig storageConfig = createStorageManagerConfig(deviceConfig, storageType);

        // Create unified storage manager
        LOG(info) << "Creating unified storage manager for type: "
                  << (storageType == StorageType::CLOUD ? StorageConstants::CLOUD_STORAGE
                                                        : StorageConstants::LOCAL_STORAGE)
                  << std::endl;

        auto uniqueStorageManager = UnifiedStorageManagerFactory::createManager(storageType, storageConfig);

        if (!uniqueStorageManager)
        {
            m_lastError = "Failed to create unified storage manager for " +
                          (storageType == StorageType::CLOUD ? StorageConstants::CLOUD_STORAGE
                                                             : StorageConstants::LOCAL_STORAGE) +
                          " storage";
            LOG(error) << m_lastError << std::endl;
            return nullptr;
        }

        // Convert unique_ptr to shared_ptr
        std::shared_ptr<UnifiedStorageManager> storageManager = std::move(uniqueStorageManager);

        // Initialize the storage manager
        if (!storageManager->configureStorage(storageConfig))
        {
            m_lastError = "Failed to configure unified storage manager";
            LOG(error) << m_lastError << std::endl;
            return nullptr;
        }

        LOG(info) << "Unified storage manager created and configured successfully" << std::endl;
        return storageManager;
    }
    catch (const std::exception& e)
    {
        m_lastError = "Exception during unified storage manager creation: " + std::string(e.what());
        LOG(error) << m_lastError << std::endl;
        return nullptr;
    }
}

DeleteResult UnifiedStorageManagerUtils::deleteFile(std::shared_ptr<UnifiedStorageManager> manager, const std::string& filePath)
{
    LOG(verbose) << "UnifiedStorageManagerUtils::deleteFile called with filePath: '" << filePath << "'" << std::endl;

    if (!manager)
    {
        setLastError("Storage manager is null");
        LOG(error) << m_lastError << std::endl;
        DeleteResult result(false, "Storage manager is null");
        result.errorCode = "NULL_MANAGER";
        return result;
    }

    try
    {
        if (!isFileExist(manager, filePath))
        {
            LOG(error) << "File does not exist: " << filePath << std::endl;
            DeleteResult result(false, "File does not exist");
            result.errorCode = "FILE_NOT_FOUND";
            return result;
        }

        // Delete the file using unified storage manager
        DeleteResult result = manager->deleteFile(filePath);

        if (result.success)
        {
            LOG(verbose) << "Successfully deleted file: " << filePath << " (" << result.deletedSize << " bytes) in "
                      << result.duration.count() << "ms" << std::endl;
        }
        else
        {
            setLastError("Failed to delete file: " + filePath + " - " + result.message);
            LOG(error) << m_lastError << std::endl;
        }

        return result;
    }
    catch (const std::exception& e)
    {
        std::string errorMsg = "Exception during file deletion: " + std::string(e.what());
        setLastError(errorMsg);
        LOG(error) << m_lastError << std::endl;
        DeleteResult result(false, errorMsg);
        result.errorCode = "EXCEPTION";
        return result;
    }
}

bool UnifiedStorageManagerUtils::isFileExist(std::shared_ptr<UnifiedStorageManager> manager, const std::string& filePath)
{
    LOG(verbose) << "UnifiedStorageManagerUtils::isFileExist called with filePath: '" << filePath << "'" << std::endl;

    if (!manager)
    {
        setLastError("Storage manager is null");
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    try
    {
        // Check if the file exists using unified storage manager
        bool exists = manager->isFileExist(filePath);

        if (exists)
        {
            LOG(info) << "File exists: " << filePath << std::endl;
        }
        else
        {
            LOG(verbose) << "File does not exist: " << filePath << std::endl;
        }

        return exists;
    }
    catch (const std::exception& e)
    {
        std::string errorMsg = "Exception during file existence check: " + std::string(e.what());
        setLastError(errorMsg);
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}

bool UnifiedStorageManagerUtils::deleteFilesSync(std::shared_ptr<UnifiedStorageManager> manager,
                                                const std::vector<std::string>& filePaths,
                                                DeleteCompletionCallback completionCallback,
                                                DeleteProgressCallback progressCallback)
{
    LOG(verbose) << "UnifiedStorageManagerUtils::deleteFilesSync called with " << filePaths.size() << " files" << std::endl;

    if (!manager)
    {
        setLastError("Storage manager is null");
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    if (filePaths.empty())
    {
        setLastError("No files provided for deletion");
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    try
    {
        MultiDeleteResult result = manager->deleteMultipleFiles(filePaths);

        if (result.overall_success)
        {
            LOG(info) << "Successfully deleted " << result.successful_deletes << " out of " << result.total_files 
                      << " files in " << result.total_duration.count() << "ms" << std::endl;
            
            // Call completion callback for each file if provided
            if (completionCallback)
            {
                for (const auto& deleteResult : result.delete_results)
                {
                    completionCallback(deleteResult.deletedPath, deleteResult.success, deleteResult.message);
                }
            }

            // Call progress callback for each file if provided
            if (progressCallback)
            {
                for (size_t i = 0; i < result.delete_results.size(); ++i)
                {
                    const auto& deleteResult = result.delete_results[i];
                    progressCallback(deleteResult.deletedPath, i, result.total_files);
                }
            }

            return true;
        }
        else
        {
            setLastError("Failed to delete files: " + result.error_message);
            LOG(error) << m_lastError << std::endl;
            
            // Call completion callback for failed files if provided
            if (completionCallback)
            {
                for (const auto& deleteResult : result.delete_results)
                {
                    if (!deleteResult.success)
                    {
                        completionCallback(deleteResult.deletedPath, false, deleteResult.message);
                    }
                }
            }

            return false;
        }
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during file deletion: " + std::string(e.what()));
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}

bool UnifiedStorageManagerUtils::deleteFilesInDirectorySync(std::shared_ptr<UnifiedStorageManager> manager,
                                                           const std::string& directoryPath,
                                                           const std::string& pattern,
                                                           bool recursive,
                                                           DeleteCompletionCallback completionCallback,
                                                           DeleteProgressCallback progressCallback)
{
    LOG(verbose) << "UnifiedStorageManagerUtils::deleteFilesInDirectorySync called with directory: '" << directoryPath 
              << "', pattern: '" << pattern << "', recursive: " << (recursive ? "true" : "false") << std::endl;

    if (!manager)
    {
        setLastError("Storage manager is null");
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    if (directoryPath.empty())
    {
        setLastError("Directory path is empty");
        LOG(error) << m_lastError << std::endl;
        return false;
    }

    try
    {
        LOG(info) << "Deleting files in directory: " << directoryPath << std::endl;

        // Use the manager's directory deletion method
        MultiDeleteResult result = manager->deleteFilesInDirectory(directoryPath, pattern, recursive);

        if (result.overall_success)
        {
            LOG(info) << "Successfully deleted " << result.successful_deletes << " out of " << result.total_files 
                      << " files in directory " << directoryPath << " in " << result.total_duration.count() << "ms" << std::endl;
            
            // Call completion callback for each file if provided
            if (completionCallback)
            {
                for (const auto& deleteResult : result.delete_results)
                {
                    completionCallback(deleteResult.deletedPath, deleteResult.success, deleteResult.message);
                }
            }

            // Call progress callback for each file if provided
            if (progressCallback)
            {
                for (size_t i = 0; i < result.delete_results.size(); ++i)
                {
                    const auto& deleteResult = result.delete_results[i];
                    progressCallback(deleteResult.deletedPath, i, result.total_files);
                }
            }

            return true;
        }
        else
        {
            setLastError("Failed to delete files in directory: " + result.error_message);
            LOG(error) << m_lastError << std::endl;
            
            // Call completion callback for failed files if provided
            if (completionCallback)
            {
                for (const auto& deleteResult : result.delete_results)
                {
                    if (!deleteResult.success)
                    {
                        completionCallback(deleteResult.deletedPath, false, deleteResult.message);
                    }
                }
            }

            return false;
        }
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during directory file deletion: " + std::string(e.what()));
        LOG(error) << m_lastError << std::endl;
        return false;
    }
}

bool UnifiedStorageManagerUtils::isCloudStorageEnabled(std::shared_ptr<UnifiedStorageManager> manager)
{
    if (!manager)
    {
        return false;
    }

    // Check if this is a cloud storage manager by checking the storage type
    return getStorageType(manager) == StorageType::CLOUD;
}

StorageType UnifiedStorageManagerUtils::getStorageType(std::shared_ptr<UnifiedStorageManager> manager)
{
    if (!manager)
    {
        return StorageType::LOCAL; // Default fallback
    }

    StorageType storageType = manager->getStorageType();
    return storageType;
}

std::string UnifiedStorageManagerUtils::getLastError()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_lastError;
}

bool UnifiedStorageManagerUtils::validateStorageManagerConfig(const DeviceConfig& deviceConfig)
{
    // Check if required storage parameters are set
    if (deviceConfig.enable_cloud_storage)
    {
        return validateCloudStorageConfig(deviceConfig);
    }
    else
    {
        return validateLocalStorageConfig(deviceConfig);
    }
}

StorageConfig UnifiedStorageManagerUtils::createStorageManagerConfig(const DeviceConfig& deviceConfig, StorageType storageType)
{
    StorageConfig storageConfig;

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

        // Configure manager-specific settings for cloud storage
        storageConfig.setParameter("enable_bucket_operations", "true");
        storageConfig.setParameter("enable_file_operations", "true");
        storageConfig.setParameter("enable_directory_operations", "true");
        storageConfig.setParameter("delete_timeout_seconds", "300");
        storageConfig.setParameter("enable_batch_operations", "true");
        storageConfig.setParameter("max_batch_size", "1000");

        LOG(info) << "Cloud storage manager (" << deviceConfig.cloud_storage_type << ") configured successfully" << std::endl;
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
        storageConfig.setParameter(StorageConstants::DELETE_TIMEOUT_SECONDS_KEY, "60");
        storageConfig.setParameter(StorageConstants::ENABLE_BATCH_OPERATIONS_KEY, "true");
        storageConfig.setParameter(StorageConstants::MAX_BATCH_SIZE_KEY, "1000");

        LOG(info) << "Local storage manager configured successfully" << std::endl;
    }

    return storageConfig;
}

void UnifiedStorageManagerUtils::setLastError(const std::string& error)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_lastError = error;
}

bool UnifiedStorageManagerUtils::validateCloudStorageConfig(const DeviceConfig& deviceConfig)
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

bool UnifiedStorageManagerUtils::validateLocalStorageConfig(const DeviceConfig& deviceConfig)
{
    // For local storage, we don't need any specific validation
    // The manager will use default local file system operations
    LOG(info) << "Local storage configuration validation passed" << std::endl;
    return true;
}

} // namespace nv_vms 
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

#include "unified_storage_manager.h"
#include "../unified_storage_types.h"
#include <memory>
#include <string>
#include <vector>
#include <mutex>
#include <functional>

namespace nv_vms
{

// Forward declarations
struct DeviceConfig;

/**
 * @brief Callback for delete operation completion
 */
using DeleteCompletionCallback = std::function<void(const std::string& filePath, bool success, 
                                                   const std::string& errorMessage)>;

/**
 * @brief Callback for delete operation progress
 */
using DeleteProgressCallback = std::function<void(const std::string& filePath, size_t currentIndex, 
                                                size_t totalFiles)>;

/**
 * @brief Utility class for unified storage manager operations
 * 
 * This class provides high-level utility functions for storage management operations,
 * following the same patterns as UnifiedStorageUtils for readers.
 */
class UnifiedStorageManagerUtils
{
public:
    /**
     * @brief Initialize and create a storage manager
     * @param deviceConfig Device configuration containing storage settings
     * @return Shared pointer to initialized storage manager, nullptr if failed
     */
    static std::shared_ptr<UnifiedStorageManager> initializeStorageManager(const DeviceConfig& deviceConfig);

    /**
     * @brief Delete a single file using the storage manager
     * @param manager Storage manager instance
     * @param filePath Path to the file to delete
     * @return DeleteResult containing success status, file size, duration, and error details
     */
    static DeleteResult deleteFile(std::shared_ptr<UnifiedStorageManager> manager, const std::string& filePath);

    /**
     * @brief Check if a file exists using the storage manager
     * @param manager Storage manager instance
     * @param filePath Path to the file to check
     * @return true if file exists, false otherwise
     */
    static bool isFileExist(std::shared_ptr<UnifiedStorageManager> manager, const std::string& filePath);

    /**
     * @brief Delete multiple files using the storage manager (synchronous)
     * @param manager Storage manager instance
     * @param filePaths Vector of file paths to delete
     * @param completionCallback Optional callback for completion notifications
     * @param progressCallback Optional callback for progress updates
     * @return true if all files were deleted successfully
     */
    static bool deleteFilesSync(std::shared_ptr<UnifiedStorageManager> manager,
                               const std::vector<std::string>& filePaths,
                               DeleteCompletionCallback completionCallback = nullptr,
                               DeleteProgressCallback progressCallback = nullptr);

    /**
     * @brief Delete files in a directory matching a pattern (synchronous)
     * @param manager Storage manager instance
     * @param directoryPath Directory path to search for files
     * @param pattern Optional regex pattern to match files
     * @param recursive Whether to search recursively in subdirectories
     * @param completionCallback Optional callback for completion notifications
     * @param progressCallback Optional callback for progress updates
     * @return true if all files were deleted successfully
     */
    static bool deleteFilesInDirectorySync(std::shared_ptr<UnifiedStorageManager> manager,
                                          const std::string& directoryPath,
                                          const std::string& pattern = "",
                                          bool recursive = false,
                                          DeleteCompletionCallback completionCallback = nullptr,
                                          DeleteProgressCallback progressCallback = nullptr);

    /**
     * @brief Check if cloud storage is enabled for the manager
     * @param manager Storage manager instance
     * @return true if cloud storage is enabled
     */
    static bool isCloudStorageEnabled(std::shared_ptr<UnifiedStorageManager> manager);

    /**
     * @brief Get the storage type of the manager
     * @param manager Storage manager instance
     * @return Storage type
     */
    static StorageType getStorageType(std::shared_ptr<UnifiedStorageManager> manager);

    /**
     * @brief Get the last error message
     * @return Last error message
     */
    static std::string getLastError();

    /**
     * @brief Validate storage manager configuration
     * @param deviceConfig Device configuration to validate
     * @return true if configuration is valid
     */
    static bool validateStorageManagerConfig(const DeviceConfig& deviceConfig);

    /**
     * @brief Create storage configuration from device configuration
     * @param deviceConfig Device configuration
     * @return Storage configuration
     */
    static StorageConfig createStorageManagerConfig(const DeviceConfig& deviceConfig, StorageType storageType);

private:
    // Static member variables
    static std::string m_lastError;
    static std::mutex m_mutex;

    // Helper methods
    static void setLastError(const std::string& error);
    static bool validateCloudStorageConfig(const DeviceConfig& deviceConfig);
    static bool validateLocalStorageConfig(const DeviceConfig& deviceConfig);
};

} // namespace nv_vms 
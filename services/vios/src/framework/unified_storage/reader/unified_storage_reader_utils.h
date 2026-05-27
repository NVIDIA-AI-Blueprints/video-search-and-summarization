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

#include "unified_storage_types.h"
#include "unified_storage_reader.h"
#include "unified_storage_reader_factory.h"
#include "cloud_reader.h"
#include "logger.h"
#include "device_manager.h"
#include <memory>
#include <mutex>
#include <string>
#include <filesystem>

namespace nv_vms {

/**
 * @brief Utility class for unified storage operations
 * This class provides a centralized way to create and manage unified storage readers
 * across different modules in the VST system.
 */
class UnifiedStorageReaderUtils {
public:
    /**
     * @brief Create a unified storage reader with the given device configuration
     * @param deviceConfig Device configuration containing storage settings
     * @return Shared pointer to the storage reader, or nullptr if creation failed
     */
    static std::shared_ptr<UnifiedStorageReader> createStorageReader(const DeviceConfig& deviceConfig);
    
    /**
     * @brief Download a file from cloud storage to local path using the provided reader
     * @param reader The storage reader to use for download
     * @param remote_path Remote path in cloud storage
     * @param local_path Local path where file should be downloaded
     * @param add_to_protection_list Whether to add file to protection list
     * @return true if download successful, false otherwise
     */
    static bool getFile(std::shared_ptr<UnifiedStorageReader> reader,
                                     const std::string& remote_path, 
                                     std::string& local_path);
    
    /**
     * @brief Check if the given reader supports cloud storage
     * @param reader The storage reader to check
     * @return true if cloud storage is available, false otherwise
     */
    static bool isCloudStorageEnabled(std::shared_ptr<UnifiedStorageReader> reader);
    
    /**
     * @brief Get the storage type of the given reader
     * @param reader The storage reader to check
     * @return StorageType enum value
     */
    static StorageType getStorageType(std::shared_ptr<UnifiedStorageReader> reader);
    
    /**
     * @brief Get the last error message from the utility
     * @return Last error message
     */
    static std::string getLastError();

    /**
     * @brief Download files synchronously (all files downloaded in sequence)
     * @param reader The storage reader to use for download
     * @param remoteLocalPairs Vector of pairs containing (remote_path, local_path)
     * @param completionCallback Optional callback for download completion notifications
     * @param progressCallback Optional callback for download progress updates
     * @return true if all files downloaded successfully, false otherwise
     */
    static bool getFilesSync(std::shared_ptr<UnifiedStorageReader> reader,
                            const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                            std::function<void(const std::string&, const DownloadResult&)> completionCallback = nullptr,
                            DownloadProgressCallback progressCallback = nullptr);

    /**
     * @brief Download files using hybrid approach: first file synchronously, rest asynchronously
     * @param reader The storage reader to use for download
     * @param remoteLocalPairs Vector of pairs containing (remote_path, local_path)
     * @param asyncSessionId Output parameter for async session ID (empty if no async downloads)
     * @param completionCallback Optional callback for async download completion
     * @param progressCallback Optional callback for async download progress
     * @return true if first file download successful, false otherwise
     */
    static bool getFiles(std::shared_ptr<UnifiedStorageReader> reader,
                              const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs,
                              std::string& asyncSessionId,
                              std::function<void(const std::string&, const DownloadResult&)> completionCallback = nullptr,
                              DownloadProgressCallback progressCallback = nullptr);

private:
    static std::string m_lastError;
    static std::mutex m_mutex;
    
    /**
     * @brief Validate cloud storage configuration from device config
     * @param deviceConfig Device configuration to validate
     * @return true if valid, false otherwise
     */
    static bool validateCloudStorageConfig(const DeviceConfig& deviceConfig);
    
    /**
     * @brief Create storage configuration from device configuration
     * @param deviceConfig Device configuration
     * @return StorageConfig object
     */
    static StorageConfig createStorageConfig(const DeviceConfig& deviceConfig);
};

} // namespace nv_vms 
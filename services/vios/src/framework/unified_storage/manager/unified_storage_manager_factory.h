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

namespace nv_vms
{

/**
 * @brief Factory for creating unified storage managers
 */
class UnifiedStorageManagerFactory
{
public:
    /**
     * @brief Create a unified storage manager based on storage type
     * @param type Storage type (LOCAL or CLOUD)
     * @return Unique pointer to unified storage manager, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageManager> createManager(StorageType type);

    /**
     * @brief Create a unified storage manager based on type string
     * @param type_name Storage type as string (StorageConstants::LOCAL_STORAGE or StorageConstants::CLOUD_STORAGE)
     * @return Unique pointer to unified storage manager, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageManager> createManager(const std::string& type_name);

    /**
     * @brief Create a unified storage manager with configuration
     * @param type Storage type
     * @param config Storage configuration
     * @return Unique pointer to unified storage manager, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageManager> createManager(StorageType type,
                                                               const StorageConfig& config);

    /**
     * @brief Create a unified storage manager with configuration
     * @param type_name Storage type as string
     * @param config Storage configuration
     * @return Unique pointer to unified storage manager, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageManager> createManager(const std::string& type_name,
                                                               const StorageConfig& config);

    /**
     * @brief Get list of supported storage types
     * @return Vector of supported type strings
     */
    static std::vector<std::string> getSupportedTypes();

    /**
     * @brief Check if a storage type is supported
     * @param type_name Storage type as string
     * @return true if supported
     */
    static bool isTypeSupported(const std::string& type_name);

    /**
     * @brief Check if a storage type enum is supported
     * @param type Storage type enum
     * @return true if supported
     */
    static bool isTypeSupported(StorageType type);

    /**
     * @brief Create default configuration for a storage type
     * @param type_name Storage type as string
     * @return Default configuration
     */
    static StorageConfig createDefaultConfig(const std::string& type_name);

    /**
     * @brief Create default configuration for a storage type
     * @param type Storage type enum
     * @return Default configuration
     */
    static StorageConfig createDefaultConfig(StorageType type);

    /**
     * @brief Validate configuration for a storage type
     * @param type_name Storage type as string
     * @param config Configuration to validate
     * @return true if valid
     */
    static bool validateConfig(const std::string& type_name, const StorageConfig& config);

    /**
     * @brief Validate configuration for a storage type
     * @param type Storage type enum
     * @param config Configuration to validate
     * @return true if valid
     */
    static bool validateConfig(StorageType type, const StorageConfig& config);

    /**
     * @brief Get required parameters for a storage type
     * @param type_name Storage type as string
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(const std::string& type_name);

    /**
     * @brief Get required parameters for a storage type
     * @param type Storage type enum
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(StorageType type);

    /**
     * @brief Convert string to storage type enum
     * @param type_name Storage type as string
     * @return Storage type enum
     */
    static StorageType stringToStorageType(const std::string& type_name);

    /**
     * @brief Convert storage type enum to string
     * @param type Storage type enum
     * @return Storage type as string
     */
    static std::string storageTypeToString(StorageType type);

private:
    /**
     * @brief Create local storage manager
     * @param config Storage configuration
     * @return Unique pointer to local storage manager
     */
    static std::unique_ptr<UnifiedStorageManager> createLocalManager(const StorageConfig& config);

    /**
     * @brief Create cloud storage manager
     * @param config Storage configuration
     * @return Unique pointer to cloud storage manager
     */
    static std::unique_ptr<UnifiedStorageManager> createCloudManager(const StorageConfig& config);

    /**
     * @brief Validate local storage configuration
     * @param config Configuration to validate
     * @return true if valid
     */
    static bool validateLocalConfig(const StorageConfig& config);

    /**
     * @brief Validate cloud storage configuration
     * @param config Configuration to validate
     * @return true if valid
     */
    static bool validateCloudConfig(const StorageConfig& config);
};

} // namespace nv_vms 
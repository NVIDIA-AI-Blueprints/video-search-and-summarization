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

#include "../unified_storage_types.h"
#include "unified_storage_reader.h"
#include <memory>

namespace nv_vms
{

/**
 * @brief Factory for creating unified storage readers
 */
class UnifiedStorageReaderFactory
{
public:
    /**
     * @brief Create a unified storage reader based on storage type
     * @param type Storage type (LOCAL or CLOUD)
     * @return Unique pointer to unified storage reader, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageReader> createReader(StorageType type);

    /**
     * @brief Create a unified storage reader based on type string
     * @param type_name Storage type as string (StorageConstants::LOCAL_STORAGE or StorageConstants::CLOUD_STORAGE)
     * @return Unique pointer to unified storage reader, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageReader> createReader(const std::string& type_name);

    /**
     * @brief Create a unified storage reader with configuration
     * @param type Storage type
     * @param config Storage configuration
     * @return Unique pointer to unified storage reader, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageReader> createReader(StorageType type,
                                                             const StorageConfig& config);

    /**
     * @brief Create a unified storage reader with configuration
     * @param type_name Storage type as string
     * @param config Storage configuration
     * @return Unique pointer to unified storage reader, nullptr if creation failed
     */
    static std::unique_ptr<UnifiedStorageReader> createReader(const std::string& type_name,
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
     * @return Default StorageConfig for the type
     */
    static StorageConfig createDefaultConfig(const std::string& type_name);

    /**
     * @brief Create default configuration for a storage type enum
     * @param type Storage type enum
     * @return Default StorageConfig for the type
     */
    static StorageConfig createDefaultConfig(StorageType type);

    /**
     * @brief Validate configuration for a storage type
     * @param type_name Storage type as string
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(const std::string& type_name, const StorageConfig& config);

    /**
     * @brief Validate configuration for a storage type enum
     * @param type Storage type enum
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(StorageType type, const StorageConfig& config);

    /**
     * @brief Get required configuration parameters for a storage type
     * @param type_name Storage type as string
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(const std::string& type_name);

    /**
     * @brief Get required configuration parameters for a storage type enum
     * @param type Storage type enum
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(StorageType type);

    /**
     * @brief Convert string to StorageType enum
     * @param type_name Storage type as string
     * @return StorageType enum value
     */
    static StorageType stringToStorageType(const std::string& type_name);

    /**
     * @brief Convert StorageType enum to string
     * @param type Storage type enum
     * @return String representation of the storage type
     */
    static std::string storageTypeToString(StorageType type);

private:
    UnifiedStorageReaderFactory() = delete;  // Static class
    
    // Internal helper methods
    static std::unique_ptr<UnifiedStorageReader> createLocalReader(const StorageConfig& config);
    static std::unique_ptr<UnifiedStorageReader> createCloudReader(const StorageConfig& config);
    
    // Configuration validation helpers
    static bool validateLocalConfig(const StorageConfig& config);
    static bool validateCloudConfig(const StorageConfig& config);
};

} // namespace nv_vms 
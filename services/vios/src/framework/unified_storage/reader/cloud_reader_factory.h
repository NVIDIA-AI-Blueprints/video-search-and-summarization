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

#include "cloud_reader.h"
#include <memory>
#include <string>
#include <vector>

namespace nv_vms {

/**
 * @brief Factory class for creating cloud readers
 */
class CloudReaderFactory {
public:
    /**
     * @brief Create a cloud reader based on type string
     * @param type_string Storage type as string (StorageConstants::AWS_S3_TYPE, StorageConstants::GOOGLE_CLOUD_TYPE, StorageConstants::AZURE_BLOB_TYPE, StorageConstants::MINIO_TYPE)
     * @param config Configuration for the cloud reader
     * @return Unique pointer to cloud reader, nullptr if creation failed
     */
    static std::unique_ptr<CloudReader> createReader(const std::string& type_string,
                                                    const CloudReaderConfig& config);

    /**
     * @brief Create a cloud reader with default configuration
     * @param type_string Storage type as string
     * @return Unique pointer to cloud reader, nullptr if creation failed
     */
    static std::unique_ptr<CloudReader> createReader(const std::string& type_string);

    /**
     * @brief Create cloud reader from storage type enum
     * @param storage_type CloudStorageType enum value
     * @param config Configuration for the cloud reader
     * @return Unique pointer to cloud reader, nullptr if creation failed
     */
    static std::unique_ptr<CloudReader> createReader(CloudStorageType storage_type,
                                                    const CloudReaderConfig& config);

    /**
     * @brief Get list of supported storage types
     * @return Vector of supported type strings
     */
    static std::vector<std::string> getSupportedTypes();

    /**
     * @brief Check if a storage type is supported
     * @param type_string Storage type as string
     * @return true if supported
     */
    static bool isTypeSupported(const std::string& type_string);

    /**
     * @brief Check if a storage type enum is supported
     * @param storage_type CloudStorageType enum value
     * @return true if supported
     */
    static bool isTypeSupported(CloudStorageType storage_type);

    /**
     * @brief Create default configuration for a storage type
     * @param type_string Storage type as string
     * @return Default CloudReaderConfig for the type
     */
    static CloudReaderConfig createDefaultConfig(const std::string& type_string);

    /**
     * @brief Create default configuration for a storage type enum
     * @param storage_type CloudStorageType enum value
     * @return Default CloudReaderConfig for the type
     */
    static CloudReaderConfig createDefaultConfig(CloudStorageType storage_type);

    /**
     * @brief Validate configuration for a storage type
     * @param type_string Storage type as string
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(const std::string& type_string, const CloudReaderConfig& config);

    /**
     * @brief Validate configuration for a storage type enum
     * @param storage_type CloudStorageType enum value
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(CloudStorageType storage_type, const CloudReaderConfig& config);

    /**
     * @brief Get required configuration parameters for a storage type
     * @param type_string Storage type as string
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(const std::string& type_string);

    /**
     * @brief Get required configuration parameters for a storage type enum
     * @param storage_type CloudStorageType enum value
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(CloudStorageType storage_type);

    /**
     * @brief Convert string to CloudStorageType enum
     * @param type_string Storage type as string
     * @return CloudStorageType enum value, UNKNOWN if not recognized
     */
    static CloudStorageType stringToStorageType(const std::string& type_string);

    /**
     * @brief Convert CloudStorageType enum to string
     * @param storage_type CloudStorageType enum value
     * @return String representation of the storage type
     */
    static std::string storageTypeToString(CloudStorageType storage_type);

private:
    CloudReaderFactory() = delete;  // Static class
    
    // Internal helper methods
    static std::unique_ptr<CloudReader> createS3Reader(const CloudReaderConfig& config);
    static std::unique_ptr<CloudReader> createGCSReader(const CloudReaderConfig& config);
    static std::unique_ptr<CloudReader> createAzureReader(const CloudReaderConfig& config);
    static std::unique_ptr<CloudReader> createMinIOReader(const CloudReaderConfig& config);
};

}  // namespace nv_vms 
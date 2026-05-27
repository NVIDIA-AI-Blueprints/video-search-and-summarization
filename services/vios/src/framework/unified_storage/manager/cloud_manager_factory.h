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

#include "cloud_manager.h"
#include <memory>
#include <string>
#include <vector>

namespace nv_vms {

/**
 * @brief Factory class for creating cloud managers
 */
class CloudManagerFactory {
public:
    /**
     * @brief Create a cloud manager based on type string
     * @param type_string Storage type as string (StorageConstants::AWS_S3_TYPE, StorageConstants::GOOGLE_CLOUD_TYPE, StorageConstants::AZURE_BLOB_TYPE, StorageConstants::MINIO_TYPE)
     * @param config Configuration for the cloud manager
     * @return Unique pointer to cloud manager, nullptr if creation failed
     */
    static std::unique_ptr<CloudManager> createManager(const std::string& type_string,
                                                      const CloudManagerConfig& config);

    /**
     * @brief Create a cloud manager with default configuration
     * @param type_string Storage type as string
     * @return Unique pointer to cloud manager, nullptr if creation failed
     */
    static std::unique_ptr<CloudManager> createManager(const std::string& type_string);

    /**
     * @brief Create cloud manager from storage type enum
     * @param storage_type CloudStorageType enum value
     * @param config Configuration for the cloud manager
     * @return Unique pointer to cloud manager, nullptr if creation failed
     */
    static std::unique_ptr<CloudManager> createManager(CloudStorageType storage_type,
                                                      const CloudManagerConfig& config);

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
     * @return Default CloudManagerConfig for the type
     */
    static CloudManagerConfig createDefaultConfig(const std::string& type_string);

    /**
     * @brief Create default configuration for a storage type enum
     * @param storage_type CloudStorageType enum value
     * @return Default CloudManagerConfig for the type
     */
    static CloudManagerConfig createDefaultConfig(CloudStorageType storage_type);

    /**
     * @brief Validate configuration for a storage type
     * @param type_string Storage type as string
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(const std::string& type_string, const CloudManagerConfig& config);

    /**
     * @brief Validate configuration for a storage type enum
     * @param storage_type CloudStorageType enum value
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(CloudStorageType storage_type, const CloudManagerConfig& config);

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
     * @brief Convert string to storage type enum
     * @param type_string Storage type as string
     * @return CloudStorageType enum value
     */
    static CloudStorageType stringToStorageType(const std::string& type_string);

    /**
     * @brief Convert storage type enum to string
     * @param storage_type CloudStorageType enum value
     * @return Storage type as string
     */
    static std::string storageTypeToString(CloudStorageType storage_type);

private:
    /**
     * @brief Create S3 cloud manager
     * @param config Configuration for S3
     * @return Unique pointer to S3 cloud manager
     */
    static std::unique_ptr<CloudManager> createS3Manager(const CloudManagerConfig& config);

    /**
     * @brief Create Google Cloud Storage manager
     * @param config Configuration for GCS
     * @return Unique pointer to GCS cloud manager
     */
    static std::unique_ptr<CloudManager> createGCSManager(const CloudManagerConfig& config);

    /**
     * @brief Create Azure Blob Storage manager
     * @param config Configuration for Azure
     * @return Unique pointer to Azure cloud manager
     */
    static std::unique_ptr<CloudManager> createAzureManager(const CloudManagerConfig& config);

    /**
     * @brief Create MinIO cloud manager
     * @param config Configuration for MinIO
     * @return Unique pointer to MinIO cloud manager
     */
    static std::unique_ptr<CloudManager> createMinIOManager(const CloudManagerConfig& config);
};

} // namespace nv_vms 
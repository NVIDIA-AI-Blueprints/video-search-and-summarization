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

#include "cloud_storage_writer.h"
#include <memory>
#include <string>
#include <vector>

namespace nv_vms
{

/**
 * @brief Factory class for creating storage writers
 */
class CloudStorageWriterFactory
{
public:
    /**
     * @brief Create a storage writer based on type string
     * @param type_string Storage type as string (StorageConstants::LOCAL_STORAGE, StorageConstants::AWS_S3_TYPE, StorageConstants::GOOGLE_CLOUD_TYPE, StorageConstants::AZURE_BLOB_TYPE, StorageConstants::MINIO_TYPE)
     * @param config Configuration for the storage writer
     * @return Unique pointer to storage writer, nullptr if creation failed
     */
    static std::unique_ptr<StorageWriter> createWriter(const std::string& type_string, const StorageConfig& config);

    /**
     * @brief Create a storage writer with default configuration
     * @param type_string Storage type as string
     * @return Unique pointer to storage writer, nullptr if creation failed
     */
    static std::unique_ptr<StorageWriter> createWriter(const std::string& type_string);

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
     * @brief Create default configuration for a storage type
     * @param type_string Storage type as string
     * @return Default StorageConfig for the type
     */
    static StorageConfig createDefaultConfig(const std::string& type_string);

    /**
     * @brief Validate configuration for a storage type
     * @param type_string Storage type as string
     * @param config Configuration to validate
     * @return true if configuration is valid
     */
    static bool validateConfig(const std::string& type_string, const StorageConfig& config);

    /**
     * @brief Get required configuration parameters for a storage type
     * @param type_string Storage type as string
     * @return Vector of required parameter names
     */
    static std::vector<std::string> getRequiredParameters(const std::string& type_string);

private:
    CloudStorageWriterFactory() = delete; // Static class
};

} // namespace nv_vms
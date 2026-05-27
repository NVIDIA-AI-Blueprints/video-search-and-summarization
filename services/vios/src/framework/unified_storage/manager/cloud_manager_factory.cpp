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

#include "cloud_manager_factory.h"
#include "minio_cloud_manager.h"
#include "s3_cloud_manager.h"
#include "../unified_storage_types.h"
#include <iostream>
#include <memory>
#include <vector>

namespace nv_vms {

std::unique_ptr<CloudManager> CloudManagerFactory::createManager(const std::string& type_string,
                                                                const CloudManagerConfig& config)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return createManager(storage_type, config);
}

std::unique_ptr<CloudManager> CloudManagerFactory::createManager(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    CloudManagerConfig config = createDefaultConfig(storage_type);
    return createManager(storage_type, config);
}

std::unique_ptr<CloudManager> CloudManagerFactory::createManager(CloudStorageType storage_type,
                                                                const CloudManagerConfig& config)
{
    switch (storage_type) {
        case CloudStorageType::AWS_S3:
            return createS3Manager(config);
        case CloudStorageType::MINIO:
            return createMinIOManager(config);
        case CloudStorageType::GOOGLE_CLOUD:
            return createGCSManager(config);
        case CloudStorageType::AZURE_BLOB:
            return createAzureManager(config);
        default:
            std::cerr << "Unsupported storage type: " << static_cast<int>(storage_type) << std::endl;
            return nullptr;
    }
}

std::vector<std::string> CloudManagerFactory::getSupportedTypes()
{
    return {
        StorageConstants::AWS_S3_TYPE,
        StorageConstants::MINIO_TYPE
    };
}

bool CloudManagerFactory::isTypeSupported(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return isTypeSupported(storage_type);
}

bool CloudManagerFactory::isTypeSupported(CloudStorageType storage_type)
{
    switch (storage_type) {
        case CloudStorageType::AWS_S3:
        case CloudStorageType::MINIO:
            return true;
        default:
            return false;
    }
}

CloudManagerConfig CloudManagerFactory::createDefaultConfig(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return createDefaultConfig(storage_type);
}

CloudManagerConfig CloudManagerFactory::createDefaultConfig(CloudStorageType storage_type)
{
    CloudManagerConfig config;
    config.storageType = storage_type;
    
    switch (storage_type) {
        case CloudStorageType::AWS_S3:
            config.endpoint = "https://s3.amazonaws.com";
            config.region = "us-east-1";
            config.useSSL = true;
            break;
        case CloudStorageType::MINIO:
            config.endpoint = "http://localhost:9000";
            config.region = "us-east-1";
            config.useSSL = false;
            break;
        default:
            config.useSSL = true;
            break;
    }
    
    config.timeoutSeconds = 30;
    config.maxRetries = 3;
    
    return config;
}

bool CloudManagerFactory::validateConfig(const std::string& type_string, const CloudManagerConfig& config)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return validateConfig(storage_type, config);
}

bool CloudManagerFactory::validateConfig(CloudStorageType storage_type, const CloudManagerConfig& config)
{
    if (config.storageType != storage_type) {
        return false;
    }
    
    if (config.endpoint.empty()) {
        return false;
    }
    
    if (config.accessKeyId.empty() || config.secretAccessKey.empty()) {
        return false;
    }
    
    if (config.timeoutSeconds == 0) {
        return false;
    }
    
    return true;
}

std::vector<std::string> CloudManagerFactory::getRequiredParameters(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return getRequiredParameters(storage_type);
}

std::vector<std::string> CloudManagerFactory::getRequiredParameters(CloudStorageType storage_type)
{
    std::vector<std::string> params = {"endpoint", "accessKeyId", "secretAccessKey"};
    
    switch (storage_type) {
        case CloudStorageType::AWS_S3:
            params.push_back("region");
            break;
        case CloudStorageType::MINIO:
            // MinIO can work without region
            break;
        default:
            break;
    }
    
    return params;
}

CloudStorageType CloudManagerFactory::stringToStorageType(const std::string& type_string)
{
    if (type_string == StorageConstants::AWS_S3_TYPE) {
        return CloudStorageType::AWS_S3;
    } else if (type_string == StorageConstants::MINIO_TYPE) {
        return CloudStorageType::MINIO;
    } else {
        return CloudStorageType::UNKNOWN;
    }
}

std::string CloudManagerFactory::storageTypeToString(CloudStorageType storage_type)
{
    switch (storage_type) {
        case CloudStorageType::AWS_S3:
            return StorageConstants::AWS_S3_TYPE;
        case CloudStorageType::MINIO:
            return StorageConstants::MINIO_TYPE;
        default:
            return "unknown";
    }
}

std::unique_ptr<CloudManager> CloudManagerFactory::createS3Manager(const CloudManagerConfig& config)
{
    auto manager = std::make_unique<S3CloudManager>();
    if (manager->configure(config)) {
        return manager;
    }
    return nullptr;
}

std::unique_ptr<CloudManager> CloudManagerFactory::createGCSManager(const CloudManagerConfig& config)
{
    // For now, return nullptr as GCS implementation is not complete
    std::cerr << "Google Cloud Storage manager not yet implemented" << std::endl;
    return nullptr;
}

std::unique_ptr<CloudManager> CloudManagerFactory::createAzureManager(const CloudManagerConfig& config)
{
    // For now, return nullptr as Azure implementation is not complete
    std::cerr << "Azure Blob Storage manager not yet implemented" << std::endl;
    return nullptr;
}

std::unique_ptr<CloudManager> CloudManagerFactory::createMinIOManager(const CloudManagerConfig& config)
{
    auto manager = std::make_unique<MinioCloudManager>();
    if (manager->configure(config)) {
        return manager;
    }
    return nullptr;
}

} // namespace nv_vms 
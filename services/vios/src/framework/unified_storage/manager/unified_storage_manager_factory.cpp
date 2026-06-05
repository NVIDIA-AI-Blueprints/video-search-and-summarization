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

#include "unified_storage_manager_factory.h"
#include "unified_local_storage_manager.h"
#include "unified_cloud_storage_manager.h"
#include "../unified_storage_types.h"
#include <algorithm>
#include <iostream>

namespace nv_vms
{

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createManager(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return std::make_unique<UnifiedLocalStorageManager>();
        case StorageType::CLOUD:
            return std::make_unique<UnifiedCloudStorageManager>();
        default:
            std::cerr << "Unknown storage type" << std::endl;
            return nullptr;
    }
}

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createManager(const std::string& type_name)
{
    if (type_name == StorageConstants::LOCAL_STORAGE || type_name == "filesystem")
    {
        return createManager(StorageType::LOCAL);
    }
    else if (type_name == StorageConstants::CLOUD_STORAGE || type_name == StorageConstants::MINIO_TYPE ||
             type_name == StorageConstants::AWS_S3_TYPE || type_name == StorageConstants::GOOGLE_CLOUD_TYPE ||
             type_name == StorageConstants::AZURE_BLOB_TYPE)
    {
        return createManager(StorageType::CLOUD);
    }
    else
    {
        std::cerr << "Unknown storage type name: " << type_name << std::endl;
        return nullptr;
    }
}

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createManager(StorageType type,
                                                                                   const StorageConfig& config)
{
    auto manager = createManager(type);
    if (manager && manager->configureStorage(config))
    {
        return manager;
    }
    return nullptr;
}

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createManager(const std::string& type_name,
                                                                                   const StorageConfig& config)
{
    auto manager = createManager(type_name);
    if (manager && manager->configureStorage(config))
    {
        return manager;
    }
    return nullptr;
}

std::vector<std::string> UnifiedStorageManagerFactory::getSupportedTypes()
{
    std::vector<std::string> supported_types = {StorageConstants::LOCAL_STORAGE};
    supported_types.push_back(StorageConstants::CLOUD_STORAGE);
    return supported_types;
}

bool UnifiedStorageManagerFactory::isTypeSupported(const std::string& type_name)
{
    auto supported = getSupportedTypes();
    std::string lower_type = type_name;
    std::transform(lower_type.begin(), lower_type.end(), lower_type.begin(), ::tolower);

    return std::find(supported.begin(), supported.end(), lower_type) != supported.end() ||
           type_name == StorageConstants::MINIO_TYPE ||
           type_name == StorageConstants::AWS_S3_TYPE ||
           type_name == StorageConstants::GOOGLE_CLOUD_TYPE ||
           type_name == StorageConstants::AZURE_BLOB_TYPE;
}

bool UnifiedStorageManagerFactory::isTypeSupported(StorageType type)
{
    if (type == StorageType::LOCAL)
    {
        return true;
    }
    if (type == StorageType::CLOUD)
    {
        return true;
    }
    return false;
}

StorageConfig UnifiedStorageManagerFactory::createDefaultConfig(const std::string& type_name)
{
    StorageType type = stringToStorageType(type_name);
    return createDefaultConfig(type);
}

StorageConfig UnifiedStorageManagerFactory::createDefaultConfig(StorageType type)
{
    StorageConfig config;
    
    switch (type)
    {
        case StorageType::LOCAL:
            config.storage_type = StorageConstants::LOCAL_STORAGE;
            config.setParameter(StorageConstants::BASE_PATH_KEY, "/tmp/vms_storage");
            config.setParameter(StorageConstants::CREATE_DIRECTORIES_KEY, "true");
            config.setParameter(StorageConstants::RECURSIVE_LISTING_KEY, "false");
            config.setParameter(StorageConstants::MAX_DEPTH_KEY, "10");
            config.setParameter(StorageConstants::INCLUDE_HIDDEN_KEY, "false");
            break;
            
        case StorageType::CLOUD:
            config.storage_type = StorageConstants::CLOUD_STORAGE;
            config.setParameter(StorageConstants::CLOUD_TYPE_KEY, StorageConstants::MINIO_TYPE);
            config.setParameter(StorageConstants::BUCKET_NAME_KEY, "vms-storage");
            config.setParameter(StorageConstants::ENDPOINT_KEY, "http://localhost:9000");
            config.setParameter(StorageConstants::ACCESS_KEY_KEY, "CHANGE_ME");
            config.setParameter(StorageConstants::SECRET_KEY_KEY, "CHANGE_ME");
            config.setParameter(StorageConstants::REGION_KEY, "us-east-1");
            config.setParameter(StorageConstants::USE_SSL_KEY, "false");
            config.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, "30");
            config.setParameter(StorageConstants::MAX_RETRIES_KEY, "3");
            break;
            
        default:
            break;
    }
    
    return config;
}

bool UnifiedStorageManagerFactory::validateConfig(const std::string& type_name, const StorageConfig& config)
{
    StorageType type = stringToStorageType(type_name);
    return validateConfig(type, config);
}

bool UnifiedStorageManagerFactory::validateConfig(StorageType type, const StorageConfig& config)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return validateLocalConfig(config);
        case StorageType::CLOUD:
            return validateCloudConfig(config);
        default:
            return false;
    }
}

std::vector<std::string> UnifiedStorageManagerFactory::getRequiredParameters(const std::string& type_name)
{
    StorageType type = stringToStorageType(type_name);
    return getRequiredParameters(type);
}

std::vector<std::string> UnifiedStorageManagerFactory::getRequiredParameters(StorageType type)
{
    std::vector<std::string> required_params;
    
    switch (type)
    {
        case StorageType::LOCAL:
            // No required parameters for local storage (base_path has default)
            break;
            
        case StorageType::CLOUD:
            required_params.push_back(StorageConstants::BUCKET_NAME_KEY);
            required_params.push_back(StorageConstants::ENDPOINT_KEY);
            required_params.push_back(StorageConstants::ACCESS_KEY_KEY);
            required_params.push_back(StorageConstants::SECRET_KEY_KEY);
            break;
            
        default:
            break;
    }
    
    return required_params;
}

StorageType UnifiedStorageManagerFactory::stringToStorageType(const std::string& type_name)
{
    if (type_name == StorageConstants::LOCAL_STORAGE || type_name == "filesystem")
    {
        return StorageType::LOCAL;
    }
    else if (type_name == StorageConstants::CLOUD_STORAGE || type_name == StorageConstants::MINIO_TYPE ||
             type_name == StorageConstants::AWS_S3_TYPE || type_name == StorageConstants::GOOGLE_CLOUD_TYPE ||
             type_name == StorageConstants::AZURE_BLOB_TYPE)
    {
        return StorageType::CLOUD;
    }
    else
    {
        return StorageType::UNKNOWN; // Return UNKNOWN for unrecognized types
    }
}

std::string UnifiedStorageManagerFactory::storageTypeToString(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return StorageConstants::LOCAL_STORAGE;
        case StorageType::CLOUD:
            return StorageConstants::CLOUD_STORAGE;
        case StorageType::UNKNOWN:
            return "unknown";
        default:
            return "unknown";
    }
}

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createLocalManager(const StorageConfig& config)
{
    auto manager = std::make_unique<UnifiedLocalStorageManager>();
    if (manager && manager->configureStorage(config))
    {
        return manager;
    }
    return nullptr;
}

std::unique_ptr<UnifiedStorageManager> UnifiedStorageManagerFactory::createCloudManager(const StorageConfig& config)
{
    auto manager = std::make_unique<UnifiedCloudStorageManager>();
    if (manager && manager->configureStorage(config))
    {
        return manager;
    }
    return nullptr;
}

bool UnifiedStorageManagerFactory::validateLocalConfig(const StorageConfig& config)
{
    // Check if storage type is correct
    if (config.storage_type != StorageConstants::LOCAL_STORAGE)
    {
        return false;
    }
    
    // Base path is optional (has default)
    std::string base_path = config.getParameter(StorageConstants::BASE_PATH_KEY, "/tmp/vms_storage");
    if (base_path.empty())
    {
        return false;
    }
    
    return true;
}

bool UnifiedStorageManagerFactory::validateCloudConfig(const StorageConfig& config)
{
    // Check if storage type is correct
    if (config.storage_type != StorageConstants::CLOUD_STORAGE)
    {
        return false;
    }
    
    // Check required parameters
    std::string bucket_name = config.getParameter(StorageConstants::BUCKET_NAME_KEY, "");
    std::string endpoint = config.getParameter(StorageConstants::ENDPOINT_KEY, "");
    std::string access_key = config.getParameter(StorageConstants::ACCESS_KEY_KEY, "");
    std::string secret_key = config.getParameter(StorageConstants::SECRET_KEY_KEY, "");
    
    if (bucket_name.empty() || endpoint.empty() || access_key.empty() || secret_key.empty())
    {
        return false;
    }
    
    return true;
}

} // namespace nv_vms 
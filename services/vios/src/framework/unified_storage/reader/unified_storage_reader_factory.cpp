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

#include "unified_storage_reader_factory.h"
#include "unified_cloud_storage_reader.h"
#include "unified_local_storage_reader.h"
#include <algorithm>
#include <cctype>
#include <sstream>

namespace nv_vms
{

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createReader(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return createLocalReader(StorageConfig());
        case StorageType::CLOUD:
            return createCloudReader(StorageConfig());
        default:
            return nullptr;
    }
}

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createReader(const std::string& type_name)
{
    StorageType type = stringToStorageType(type_name);
    if (type == StorageType::LOCAL || type == StorageType::CLOUD)
    {
        return createReader(type);
    }
    return nullptr;
}

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createReader(StorageType type,
                                                                                const StorageConfig& config)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return createLocalReader(config);
        case StorageType::CLOUD:
            return createCloudReader(config);
        default:
            return nullptr;
    }
}

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createReader(const std::string& type_name,
                                                                                const StorageConfig& config)
{
    StorageType type = stringToStorageType(type_name);
    if (type == StorageType::LOCAL || type == StorageType::CLOUD)
    {
        return createReader(type, config);
    }
    return nullptr;
}

std::vector<std::string> UnifiedStorageReaderFactory::getSupportedTypes()
{
    return {StorageConstants::LOCAL_STORAGE, StorageConstants::CLOUD_STORAGE};
}

bool UnifiedStorageReaderFactory::isTypeSupported(const std::string& type_name)
{
    std::string lowerType = type_name;
    std::transform(lowerType.begin(), lowerType.end(), lowerType.begin(), ::tolower);

    return lowerType == StorageConstants::LOCAL_STORAGE || lowerType == StorageConstants::CLOUD_STORAGE;
}

bool UnifiedStorageReaderFactory::isTypeSupported(StorageType type)
{
    return type == StorageType::LOCAL || type == StorageType::CLOUD;
}

StorageConfig UnifiedStorageReaderFactory::createDefaultConfig(const std::string& type_name)
{
    std::string lowerType = type_name;
    std::transform(lowerType.begin(), lowerType.end(), lowerType.begin(), ::tolower);

    StorageConfig config;

    if (lowerType == StorageConstants::LOCAL_STORAGE)
    {
        config.storage_type = StorageConstants::LOCAL_STORAGE;
        config.setParameter(StorageConstants::BASE_PATH_KEY, "/tmp/storage");
        config.setParameter(StorageConstants::RECURSIVE_LISTING_KEY, "false");
        config.setParameter(StorageConstants::MAX_DEPTH_KEY, "10");
        config.setParameter(StorageConstants::INCLUDE_HIDDEN_KEY, "false");
        config.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, "30");
    }
    else if (lowerType == StorageConstants::CLOUD_STORAGE)
    {
        config.storage_type = StorageConstants::CLOUD_STORAGE;
        config.setParameter(StorageConstants::CLOUD_TYPE_KEY, StorageConstants::AWS_S3_TYPE);
        config.setParameter(StorageConstants::ENDPOINT_KEY, "s3.amazonaws.com");
        config.setParameter(StorageConstants::REGION_KEY, "us-west-2");
        config.setParameter(StorageConstants::USE_SSL_KEY, "true");
        config.setParameter(StorageConstants::TIMEOUT_SECONDS_KEY, "30");
        config.setParameter(StorageConstants::MAX_RETRIES_KEY, "3");
    }

    return config;
}

StorageConfig UnifiedStorageReaderFactory::createDefaultConfig(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return createDefaultConfig(StorageConstants::LOCAL_STORAGE);
        case StorageType::CLOUD:
            return createDefaultConfig(StorageConstants::CLOUD_STORAGE);
        default:
            return StorageConfig();
    }
}

bool UnifiedStorageReaderFactory::validateConfig(const std::string& type_name, const StorageConfig& config)
{
    std::string lowerType = type_name;
    std::transform(lowerType.begin(), lowerType.end(), lowerType.begin(), ::tolower);

    if (lowerType == StorageConstants::LOCAL_STORAGE)
    {
        return validateLocalConfig(config);
    }
    else if (lowerType == StorageConstants::CLOUD_STORAGE)
    {
        return validateCloudConfig(config);
    }

    return false;
}

bool UnifiedStorageReaderFactory::validateConfig(StorageType type, const StorageConfig& config)
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

std::vector<std::string> UnifiedStorageReaderFactory::getRequiredParameters(const std::string& type_name)
{
    std::string lowerType = type_name;
    std::transform(lowerType.begin(), lowerType.end(), lowerType.begin(), ::tolower);

    if (lowerType == StorageConstants::LOCAL_STORAGE)
    {
        return {StorageConstants::BASE_PATH_KEY};
    }
    else if (lowerType == StorageConstants::CLOUD_STORAGE)
    {
        return {StorageConstants::CLOUD_TYPE_KEY, StorageConstants::ENDPOINT_KEY};
    }

    return {};
}

std::vector<std::string> UnifiedStorageReaderFactory::getRequiredParameters(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return getRequiredParameters(StorageConstants::LOCAL_STORAGE);
        case StorageType::CLOUD:
            return getRequiredParameters(StorageConstants::CLOUD_STORAGE);
        default:
            return {};
    }
}

StorageType UnifiedStorageReaderFactory::stringToStorageType(const std::string& type_name)
{
    std::string lowerType = type_name;
    std::transform(lowerType.begin(), lowerType.end(), lowerType.begin(), ::tolower);

    if (lowerType == StorageConstants::LOCAL_STORAGE)
    {
        return StorageType::LOCAL;
    }
    else if (lowerType == StorageConstants::CLOUD_STORAGE)
    {
        return StorageType::CLOUD;
    }

    return StorageType::LOCAL; // Default fallback
}

std::string UnifiedStorageReaderFactory::storageTypeToString(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return StorageConstants::LOCAL_STORAGE;
        case StorageType::CLOUD:
            return StorageConstants::CLOUD_STORAGE;
        default:
            return "unknown";
    }
}

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createLocalReader(const StorageConfig& config)
{
    try
    {
        auto reader = std::make_unique<UnifiedLocalStorageReader>();
        if (reader && reader->configureStorage(config))
        {
            return reader;
        }
    }
    catch (const std::exception& e)
    {
        // Log error if needed
    }
    return nullptr;
}

std::unique_ptr<UnifiedStorageReader> UnifiedStorageReaderFactory::createCloudReader(const StorageConfig& config)
{
    try
    {
        auto reader = std::make_unique<UnifiedCloudStorageReader>();
        if (reader && reader->configureStorage(config))
        {
            return reader;
        }
    }
    catch (const std::exception& e)
    {
        // Log error if needed
    }
    return nullptr;
}

bool UnifiedStorageReaderFactory::validateLocalConfig(const StorageConfig& config)
{
    // Check required parameters
    std::string basePath = config.getParameter(StorageConstants::BASE_PATH_KEY);
    if (basePath.empty())
    {
        return false;
    }

    // Validate optional parameters
    std::string maxDepthStr = config.getParameter(StorageConstants::MAX_DEPTH_KEY);
    if (!maxDepthStr.empty())
    {
        try
        {
            uint32_t maxDepth = std::stoul(maxDepthStr);
            if (maxDepth > 100)
            { // Reasonable limit
                return false;
            }
        }
        catch (const std::exception&)
        {
            return false;
        }
    }

    std::string timeoutStr = config.getParameter(StorageConstants::TIMEOUT_SECONDS_KEY);
    if (!timeoutStr.empty())
    {
        try
        {
            uint32_t timeout = std::stoul(timeoutStr);
            if (timeout > 3600)
            { // 1 hour max
                return false;
            }
        }
        catch (const std::exception&)
        {
            return false;
        }
    }

    return true;
}

bool UnifiedStorageReaderFactory::validateCloudConfig(const StorageConfig& config)
{
    // Check required parameters
    std::string cloudType = config.getParameter(StorageConstants::CLOUD_TYPE_KEY);
    if (cloudType.empty())
    {
        return false;
    }

    std::string endpoint = config.getParameter(StorageConstants::ENDPOINT_KEY);
    if (endpoint.empty())
    {
        return false;
    }

    // Validate cloud type
    std::transform(cloudType.begin(), cloudType.end(), cloudType.begin(), ::tolower);
    if (cloudType != StorageConstants::AWS_S3_TYPE && cloudType != StorageConstants::GOOGLE_CLOUD_TYPE &&
        cloudType != StorageConstants::AZURE_BLOB_TYPE && cloudType != StorageConstants::MINIO_TYPE)
    {
        return false;
    }

    // Validate optional parameters
    std::string timeoutStr = config.getParameter(StorageConstants::TIMEOUT_SECONDS_KEY);
    if (!timeoutStr.empty())
    {
        try
        {
            uint32_t timeout = std::stoul(timeoutStr);
            if (timeout > 3600)
            { // 1 hour max
                return false;
            }
        }
        catch (const std::exception&)
        {
            return false;
        }
    }

    std::string maxRetriesStr = config.getParameter(StorageConstants::MAX_RETRIES_KEY);
    if (!maxRetriesStr.empty())
    {
        try
        {
            uint32_t maxRetries = std::stoul(maxRetriesStr);
            if (maxRetries > 10)
            { // Reasonable limit
                return false;
            }
        }
        catch (const std::exception&)
        {
            return false;
        }
    }

    return true;
}

} // namespace nv_vms
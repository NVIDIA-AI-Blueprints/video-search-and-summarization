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

#include "cloud_reader_factory.h"
#include "minio_cloud_reader.h"
#include "s3_cloud_reader.h"
#include <algorithm>
#include <map>

namespace nv_vms
{

std::unique_ptr<CloudReader> CloudReaderFactory::createReader(const std::string& type_string,
                                                              const CloudReaderConfig& config)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return createReader(storage_type, config);
}

std::unique_ptr<CloudReader> CloudReaderFactory::createReader(const std::string& type_string)
{
    CloudReaderConfig config = createDefaultConfig(type_string);
    return createReader(type_string, config);
}

std::unique_ptr<CloudReader> CloudReaderFactory::createReader(CloudStorageType storage_type,
                                                              const CloudReaderConfig& config)
{
    switch (storage_type)
    {
        case CloudStorageType::AWS_S3:
            return createS3Reader(config);
        case CloudStorageType::GOOGLE_CLOUD:
            return createGCSReader(config);
        case CloudStorageType::AZURE_BLOB:
            return createAzureReader(config);
        case CloudStorageType::MINIO:
            return createMinIOReader(config);
        default:
            return nullptr;
    }
}

std::vector<std::string> CloudReaderFactory::getSupportedTypes()
{
    return {StorageConstants::AWS_S3_TYPE, StorageConstants::GOOGLE_CLOUD_TYPE, StorageConstants::AZURE_BLOB_TYPE,
            StorageConstants::MINIO_TYPE};
}

bool CloudReaderFactory::isTypeSupported(const std::string& type_string)
{
    auto supported = getSupportedTypes();
    std::string lower_type = type_string;
    std::transform(lower_type.begin(), lower_type.end(), lower_type.begin(), ::tolower);

    return std::find(supported.begin(), supported.end(), lower_type) != supported.end();
}

bool CloudReaderFactory::isTypeSupported(CloudStorageType storage_type)
{
    return storage_type != CloudStorageType::UNKNOWN;
}

CloudReaderConfig CloudReaderFactory::createDefaultConfig(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return createDefaultConfig(storage_type);
}

CloudReaderConfig CloudReaderFactory::createDefaultConfig(CloudStorageType storage_type)
{
    CloudReaderConfig config;
    config.storageType = storage_type;

    switch (storage_type)
    {
        case CloudStorageType::AWS_S3:
            config.region = "us-west-1";
            config.useSSL = true;
            config.timeoutSeconds = 30;
            config.maxRetries = 3;
            config.request.maxKeys = 1000;
            config.request.fetchMetadata = false;
            config.request.enableCache = true;
            config.request.cacheTimeoutSec = 300;
            break;
        case CloudStorageType::GOOGLE_CLOUD:
            config.region = "us-central1";
            config.useSSL = true;
            config.timeoutSeconds = 30;
            config.maxRetries = 3;
            break;
        case CloudStorageType::AZURE_BLOB:
            config.useSSL = true;
            config.timeoutSeconds = 30;
            config.maxRetries = 3;
            break;
        case CloudStorageType::MINIO:
            config.useSSL = false; // MinIO often used locally without SSL
            config.timeoutSeconds = 30;
            config.maxRetries = 3;
            break;
        default:
            break;
    }

    return config;
}

bool CloudReaderFactory::validateConfig(const std::string& type_string, const CloudReaderConfig& config)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return validateConfig(storage_type, config);
}

bool CloudReaderFactory::validateConfig(CloudStorageType storage_type, const CloudReaderConfig& config)
{
    if (config.storageType != storage_type)
    {
        return false;
    }

    switch (storage_type)
    {
        case CloudStorageType::AWS_S3:
            return !config.accessKeyId.empty() && !config.secretAccessKey.empty() && !config.region.empty();
        case CloudStorageType::GOOGLE_CLOUD:
            return !config.accessKeyId.empty() && !config.secretAccessKey.empty();
        case CloudStorageType::AZURE_BLOB:
            return !config.accessKeyId.empty() && !config.secretAccessKey.empty();
        case CloudStorageType::MINIO:
            return !config.accessKeyId.empty() && !config.secretAccessKey.empty() && !config.endpoint.empty();
        default:
            return false;
    }
}

std::vector<std::string> CloudReaderFactory::getRequiredParameters(const std::string& type_string)
{
    CloudStorageType storage_type = stringToStorageType(type_string);
    return getRequiredParameters(storage_type);
}

std::vector<std::string> CloudReaderFactory::getRequiredParameters(CloudStorageType storage_type)
{
    switch (storage_type)
    {
        case CloudStorageType::AWS_S3:
            return {"accessKeyId", "secretAccessKey", "region"};
        case CloudStorageType::GOOGLE_CLOUD:
            return {"accessKeyId", "secretAccessKey"};
        case CloudStorageType::AZURE_BLOB:
            return {"accessKeyId", "secretAccessKey"};
        case CloudStorageType::MINIO:
            return {"accessKeyId", "secretAccessKey", "endpoint"};
        default:
            return {};
    }
}

CloudStorageType CloudReaderFactory::stringToStorageType(const std::string& type_string)
{
    static const std::map<std::string, CloudStorageType, std::less<>> type_map = {
        {StorageConstants::AWS_S3_TYPE, CloudStorageType::AWS_S3},
        {StorageConstants::AWS_S3_ALT_TYPE, CloudStorageType::AWS_S3},
        {StorageConstants::AWS_S3_ALT_TYPE2, CloudStorageType::AWS_S3},
        {StorageConstants::GOOGLE_CLOUD_TYPE, CloudStorageType::GOOGLE_CLOUD},
        {StorageConstants::GOOGLE_CLOUD_ALT_TYPE, CloudStorageType::GOOGLE_CLOUD},
        {StorageConstants::GOOGLE_CLOUD_ALT_TYPE2, CloudStorageType::GOOGLE_CLOUD},
        {StorageConstants::AZURE_BLOB_TYPE, CloudStorageType::AZURE_BLOB},
        {StorageConstants::AZURE_BLOB_ALT_TYPE, CloudStorageType::AZURE_BLOB},
        {StorageConstants::MINIO_TYPE, CloudStorageType::MINIO}};

    std::string lower_type = type_string;
    std::transform(lower_type.begin(), lower_type.end(), lower_type.begin(), ::tolower);

    auto it = type_map.find(lower_type);
    return (it != type_map.end()) ? it->second : CloudStorageType::UNKNOWN;
}

std::string CloudReaderFactory::storageTypeToString(CloudStorageType storage_type)
{
    switch (storage_type)
    {
        case CloudStorageType::AWS_S3:
            return StorageConstants::AWS_S3_TYPE;
        case CloudStorageType::GOOGLE_CLOUD:
            return StorageConstants::GOOGLE_CLOUD_TYPE;
        case CloudStorageType::AZURE_BLOB:
            return StorageConstants::AZURE_BLOB_TYPE;
        case CloudStorageType::MINIO:
            return StorageConstants::MINIO_TYPE;
        default:
            return "unknown";
    }
}

// Private helper methods
std::unique_ptr<CloudReader> CloudReaderFactory::createS3Reader(const CloudReaderConfig& config)
{
    auto reader = std::make_unique<S3CloudReader>();
    if (reader->configure(config))
    {
        return std::move(reader);
    }
    return nullptr;
}

std::unique_ptr<CloudReader> CloudReaderFactory::createGCSReader(const CloudReaderConfig& config)
{
    // Google Cloud Storage reader not implemented yet
    return nullptr;
}

std::unique_ptr<CloudReader> CloudReaderFactory::createAzureReader(const CloudReaderConfig& config)
{
    // Azure Blob Storage reader not implemented yet
    return nullptr;
}

std::unique_ptr<CloudReader> CloudReaderFactory::createMinIOReader(const CloudReaderConfig& config)
{
    auto reader = std::make_unique<MinioCloudReader>();
    if (reader->configure(config))
    {
        return std::move(reader);
    }
    return nullptr;
}

} // namespace nv_vms
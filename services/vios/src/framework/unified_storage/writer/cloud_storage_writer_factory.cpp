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

#include "cloud_storage_writer_factory.h"
#include "logger.h"
#include "minio_storage_writer.h"
#include <algorithm>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

namespace nv_vms
{

std::unique_ptr<StorageWriter> CloudStorageWriterFactory::createWriter(const std::string& type_string,
                                                                  const StorageConfig& config)
{
    LOG(info) << "Creating storage writer for type: " << type_string << std::endl;

    if (type_string == StorageConstants::AWS_S3_TYPE)
    {
        LOG(warning) << "S3 storage writer not yet implemented" << std::endl;
        return nullptr;
    }
    else if (type_string == StorageConstants::GOOGLE_CLOUD_TYPE)
    {
        LOG(warning) << "Google Cloud Storage writer not yet implemented" << std::endl;
        return nullptr;
    }
    else if (type_string == StorageConstants::AZURE_BLOB_TYPE)
    {
        LOG(warning) << "Azure storage writer not yet implemented" << std::endl;
        return nullptr;
    }
    else if (type_string == StorageConstants::MINIO_TYPE)
    {
        auto writer = std::make_unique<MinioStorageWriter>();
        if (writer->configure(config))
        {
            return std::move(writer);
        }
        else
        {
            LOG(error) << "Failed to configure MinIO storage writer" << std::endl;
            return nullptr;
        }
    }
    else
    {
        LOG(error) << "Unsupported storage type: " << type_string << std::endl;
        return nullptr;
    }
}

std::unique_ptr<StorageWriter> CloudStorageWriterFactory::createWriter(const std::string& type_string)
{
    StorageConfig config = createDefaultConfig(type_string);
    return createWriter(type_string, config);
}

std::vector<std::string> CloudStorageWriterFactory::getSupportedTypes()
{
    return {StorageConstants::LOCAL_STORAGE, StorageConstants::AWS_S3_TYPE, 
            StorageConstants::GOOGLE_CLOUD_TYPE, StorageConstants::AZURE_BLOB_TYPE, 
            StorageConstants::MINIO_TYPE};
}

bool CloudStorageWriterFactory::isTypeSupported(const std::string& type_string)
{
    auto supported_types = getSupportedTypes();
    return std::find(supported_types.begin(), supported_types.end(), type_string) != supported_types.end();
}

StorageConfig CloudStorageWriterFactory::createDefaultConfig(const std::string& type_string)
{
    StorageConfig config;
    config.storage_type = type_string;

    if (type_string == StorageConstants::AWS_S3_TYPE)
    {
        config.setParameter(StorageConstants::BUCKET_NAME_KEY, "");
        config.setParameter(StorageConstants::REGION_KEY, "us-east-1");
        config.setParameter(StorageConstants::ACCESS_KEY_KEY, "");
        config.setParameter(StorageConstants::SECRET_KEY_KEY, "");
        config.setParameter(StorageConstants::ENDPOINT_KEY, "");

        // Enable buffering for cloud storage
        config.buffering.enabled = true;
        config.buffering.buffer_size_mb = 50;  // Reduced for lower latency
        config.buffering.max_frames = 1500;     // Reduced for lower latency
        config.buffering.max_upload_fps = 35.0; // Set to slightly above input rate (30 FPS + 5 FPS buffer)
        config.buffering.flush_timeout_sec = 30; // Timeout for flush operations
    }
    else if (type_string == StorageConstants::GOOGLE_CLOUD_TYPE)
    {
        config.setParameter(StorageConstants::BUCKET_NAME_KEY, "");
        config.setParameter(StorageConstants::PROJECT_ID_KEY, "");
        config.setParameter(StorageConstants::KEY_FILE_PATH_KEY, "");

        // Enable buffering for cloud storage
        config.buffering.enabled = true;
        config.buffering.buffer_size_mb = 50;  // Reduced for lower latency
        config.buffering.max_frames = 1500;     // Reduced for lower latency
        config.buffering.max_upload_fps = 35.0; // Set to slightly above input rate (30 FPS + 5 FPS buffer)
        config.buffering.flush_timeout_sec = 30; // Timeout for flush operations
    }
    else if (type_string == StorageConstants::AZURE_BLOB_TYPE)
    {
        config.setParameter(StorageConstants::STORAGE_ACCOUNT_KEY, "");
        config.setParameter(StorageConstants::ACCESS_KEY_KEY, "");
        config.setParameter(StorageConstants::CONTAINER_NAME_KEY, "");

        // Enable buffering for cloud storage
        config.buffering.enabled = true;
        config.buffering.buffer_size_mb = 50;  // Reduced for lower latency
        config.buffering.max_frames = 1500;     // Reduced for lower latency
        config.buffering.max_upload_fps = 35.0; // Set to slightly above input rate (30 FPS + 5 FPS buffer)
        config.buffering.flush_timeout_sec = 30; // Timeout for flush operations
    }
    else if (type_string == StorageConstants::MINIO_TYPE)
    {
        config.setParameter(StorageConstants::ENDPOINT_KEY, "");
        config.setParameter(StorageConstants::ACCESS_KEY_KEY, "");
        config.setParameter(StorageConstants::SECRET_KEY_KEY, "");
        config.setParameter(StorageConstants::BUCKET_NAME_KEY, "");
        config.setParameter(StorageConstants::USE_SSL_KEY, "false");

        // Enable buffering for cloud storage
        config.buffering.enabled = true;
        config.buffering.buffer_size_mb = 50;  // Reduced for lower latency
        config.buffering.max_frames = 1500;     // Reduced for lower latency
        config.buffering.max_upload_fps = 35.0; // Set to slightly above input rate (30 FPS + 5 FPS buffer)
        config.buffering.flush_timeout_sec = 30; // Timeout for flush operations
    }

    return config;
}

bool CloudStorageWriterFactory::validateConfig(const std::string& type_string, const StorageConfig& config)
{
    if (config.storage_type != type_string)
    {
        LOG(error) << "Storage type mismatch in config" << std::endl;
        return false;
    }

    auto required_params = getRequiredParameters(type_string);
    for (const auto& param : required_params)
    {
        if (config.getParameter(param).empty())
        {
            LOG(error) << "Missing required parameter: " << param << std::endl;
            return false;
        }
    }

    return true;
}

std::vector<std::string> CloudStorageWriterFactory::getRequiredParameters(const std::string& type_string)
{
    std::vector<std::string> required_params;

    if (type_string == StorageConstants::AWS_S3_TYPE)
    {
        required_params = {StorageConstants::BUCKET_NAME_KEY, StorageConstants::REGION_KEY, StorageConstants::ACCESS_KEY_KEY, StorageConstants::SECRET_KEY_KEY};
    }
    else if (type_string == StorageConstants::GOOGLE_CLOUD_TYPE)
    {
        required_params = {StorageConstants::BUCKET_NAME_KEY, StorageConstants::PROJECT_ID_KEY};
    }
    else if (type_string == StorageConstants::AZURE_BLOB_TYPE)
    {
        required_params = {StorageConstants::STORAGE_ACCOUNT_KEY, StorageConstants::ACCESS_KEY_KEY, StorageConstants::CONTAINER_NAME_KEY};
    }
    else if (type_string == StorageConstants::MINIO_TYPE)
    {
        required_params = {StorageConstants::ENDPOINT_KEY, StorageConstants::ACCESS_KEY_KEY, StorageConstants::SECRET_KEY_KEY, StorageConstants::BUCKET_NAME_KEY};
    }

    return required_params;
}

} // namespace nv_vms
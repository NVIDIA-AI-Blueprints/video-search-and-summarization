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

#include "unified_storage_writer_factory.h"
#include "unified_cloud_storage_writer.h"
#include "unified_local_storage_writer.h"

using namespace nv_vms;

// UnifiedStorageWriterFactory implementation
std::unique_ptr<UnifiedStorageWriter> UnifiedStorageWriterFactory::createWriter(StorageType type)
{
    switch (type)
    {
        case StorageType::LOCAL:
            return std::make_unique<UnifiedLocalStorageWriter>();
        case StorageType::CLOUD:
            return std::make_unique<UnifiedCloudStorageWriter>();
        default:
            LOG(error) << "Unknown storage type" << endl;
            return nullptr;
    }
}

std::unique_ptr<UnifiedStorageWriter> UnifiedStorageWriterFactory::createWriter(const std::string& type_name)
{
    if (type_name == StorageConstants::LOCAL_STORAGE || type_name == "filesystem")
    {
        return createWriter(StorageType::LOCAL);
    }
    else if (type_name == StorageConstants::CLOUD_STORAGE || type_name == StorageConstants::MINIO_TYPE ||
         type_name == StorageConstants::AWS_S3_TYPE || type_name == StorageConstants::GOOGLE_CLOUD_TYPE ||
         type_name == StorageConstants::AZURE_BLOB_TYPE)
    {
        return createWriter(StorageType::CLOUD);
    }
    else
    {
        LOG(error) << "Unknown storage type name: " << type_name << endl;
        return nullptr;
    }
}
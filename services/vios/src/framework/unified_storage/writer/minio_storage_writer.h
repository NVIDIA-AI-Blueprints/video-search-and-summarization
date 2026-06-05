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
#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <sstream>
#include <list>

// Include MinIO SDK headers directly
#include "minio-cpp/client.h"
#include "minio-cpp/credentials.h"

namespace nv_vms
{

/**
 * @brief MinIO storage writer implementation
 *
 * This class provides MinIO S3-compatible object storage support.
 * MinIO is an open-source object storage server compatible with Amazon S3 APIs.
 */
class MinioStorageWriter : public CloudStorageWriter
{
public:
    MinioStorageWriter();
    virtual ~MinioStorageWriter();
    
    // Delete copy and move operations to prevent unsafe MinIO SDK object lifecycle issues
    MinioStorageWriter(const MinioStorageWriter&) = delete;
    MinioStorageWriter& operator=(const MinioStorageWriter&) = delete;
    MinioStorageWriter(MinioStorageWriter&&) = delete;
    MinioStorageWriter& operator=(MinioStorageWriter&&) = delete;

    // StorageWriter interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override
    {
        return "MinIO";
    }

    std::string startSession(const std::string& remote_path, const std::string& stream_id,
                             size_t estimated_size = 0) override;

    StorageResult uploadFile(const std::string& local_path, const std::string& remote_path) override;

    bool deleteFile(const std::string& remote_path) override;
    bool fileExists(const std::string& remote_path) const override;
    size_t getFileSize(const std::string& remote_path) const override;

    bool configure(const StorageConfig& config) override;
    StorageConfig getConfiguration() const override;
    bool performHealthCheck() override;
    std::string getLastError() const override;

protected:
    // CloudStorageWriter abstract methods
    bool doWriteData(const std::string& session_id, const void* data, size_t size, int64_t pts,
                     const std::string& media_type) override;

    StorageResult doCompleteSession(const std::string& session_id, const std::string& stream_id) override;

    bool doCancelSession(const std::string& session_id, const std::string& stream_id) override;

private:
    // Configuration
    StorageConfig m_config;
    std::string m_endpoint_url;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_bucket_name;
    bool m_use_ssl;

    // MinIO client
    std::unique_ptr<minio::s3::Client> m_minio_client;
    std::unique_ptr<minio::creds::StaticProvider> m_credentials;
    mutable std::mutex m_client_mutex;
    std::atomic<bool> m_client_initialized{false};

    // Error handling
    mutable std::mutex m_error_mutex;
    mutable std::string m_last_error;

    // Session management for multipart uploads
    struct MultipartSession
    {
        std::string m_bucket_name;
        std::string m_object_key;
        std::string m_upload_id;
        std::vector<std::string> m_etags;
        size_t m_part_number = 1;
        size_t m_bytes_written = 0;
        bool m_is_active = true;
        
        // Data accumulation for efficient part uploads
        std::vector<uint8_t> m_accumulated_data;
        size_t m_min_part_size; // Configurable minimum part size
        
        // Parts tracking for multipart upload completion
        struct PartInfo {
            size_t part_number;
            std::string etag;
            
            PartInfo(size_t num, const std::string& tag) 
                : part_number(num), etag(tag) {}
        };
        std::vector<PartInfo> m_parts;
    };

    std::map<std::string, std::unique_ptr<MultipartSession>, std::less<>> m_active_sessions;
    mutable std::mutex m_session_mutex;
    std::atomic<uint64_t> m_session_counter{0};
    std::string m_current_stream_id; // Store current streamId for session generation

    // Internal helper methods
    std::string generateObjectKey(const std::string& remote_path) const;

    // MinIO client operations
    bool initializeMinioClient();
    void shutdownMinioClient();
    bool ensureClientInitialized();

    // Multipart upload operations using MinIO SDK
    bool startMultipartUpload(MultipartSession& session);
    bool uploadPart(MultipartSession& session, const std::vector<uint8_t>& data);
    bool completeMultipartUpload(MultipartSession& session);
    bool abortMultipartUpload(MultipartSession& session);

    // Single operations using MinIO SDK
    bool putObject(const std::string& bucket_name, const std::string& object_key, const std::vector<uint8_t>& data);
    bool deleteObject(const std::string& bucket_name, const std::string& object_key);
    bool objectExists(const std::string& bucket_name, const std::string& object_key) const;
    size_t getObjectSize(const std::string& bucket_name, const std::string& object_key) const;

    // Connection testing
    bool testConnection() const;

    // Error management
    void setLastError(const std::string& error) const;
    void clearLastError();
};

} // namespace nv_vms
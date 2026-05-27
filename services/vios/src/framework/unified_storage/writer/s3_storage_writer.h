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
#include <queue>
#include <thread>
#include <unordered_map>

namespace nv_vms
{

/**
 * @brief AWS S3 storage writer implementation
 * Note: This requires AWS SDK for C++ to be linked
 */
class S3StorageWriter : public CloudStorageWriter
{
private:
    struct StreamingSession
    {
        std::string m_bucket_name;
        std::string m_object_key;
        std::string m_upload_id;
        std::vector<std::string> m_etags;
        size_t m_bytes_written;
        size_t m_part_number;
        std::atomic<bool> m_is_active;
        std::queue<std::vector<uint8_t>> m_buffer_queue;
        std::mutex m_buffer_mutex;

        StreamingSession() : m_bytes_written(0), m_part_number(1), m_is_active(false) {}
    };

public:
    S3StorageWriter();
    virtual ~S3StorageWriter();

    // CloudStorageWriter interface implementation
    bool isAvailable() const override;
    std::string getStorageTypeName() const override
    {
        return "Amazon S3";
    }
    bool configure(const StorageConfig& config) override;
    StorageConfig getConfiguration() const override;
    bool performHealthCheck() override;
    std::string getLastError() const override;

    // File operations
    StorageResult uploadFile(const std::string& local_file_path, const std::string& remote_path) override;
    bool deleteFile(const std::string& remote_path) override;
    bool fileExists(const std::string& remote_path) const override;
    size_t getFileSize(const std::string& remote_path) const override;

protected:
    // CloudStorageWriter abstract methods
    bool doWriteData(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                     const std::string& media_type = "video") override;

    StorageResult doCompleteSession(const std::string& session_id, const std::string& stream_id) override;

    bool doCancelSession(const std::string& session_id, const std::string& stream_id) override;

private:
    std::string m_bucket_name;
    std::string m_region;
    std::string m_access_key;
    std::string m_secret_key;
    std::string m_endpoint_url; // For custom endpoints
    bool m_use_ssl;

    std::unordered_map<std::string, std::unique_ptr<StreamingSession>> m_streaming_sessions;
    std::atomic<uint64_t> m_session_counter;

    // AWS SDK related members would go here
    // std::shared_ptr<Aws::S3::S3Client> m_s3_client;
    std::string generateObjectKey(const std::string& remote_path) const;
    bool initializeS3Client();
    void shutdownS3Client();

    // Helper methods for multipart upload
    bool startMultipartUpload(StreamingSession& session);
    bool uploadPart(StreamingSession& session, const std::vector<uint8_t>& data);
    bool completeMultipartUpload(StreamingSession& session);
    bool abortMultipartUpload(StreamingSession& session);
};

} // namespace nv_vms
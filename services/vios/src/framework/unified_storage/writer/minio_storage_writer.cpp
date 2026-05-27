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

#include "minio_storage_writer.h"
#include "logger.h"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <list>
#include <random>
#include <sstream>
#include <stdexcept>

// Include MinIO SDK headers only in implementation
#include "minio-cpp/client.h"
#include "minio-cpp/credentials.h"

namespace nv_vms
{

MinioStorageWriter::MinioStorageWriter() : m_use_ssl(false)
{
    LOG(info) << "MinioStorageWriter created" << std::endl;
}

MinioStorageWriter::~MinioStorageWriter()
{
    shutdownMinioClient();
    LOG(info) << "MinioStorageWriter destroyed" << std::endl;
}

bool MinioStorageWriter::isAvailable() const
{
    return m_client_initialized.load(std::memory_order_acquire) && !m_endpoint_url.empty() && !m_access_key.empty() && !m_secret_key.empty() &&
           !m_bucket_name.empty();
}

std::string MinioStorageWriter::startSession(const std::string& remote_path, const std::string& stream_id,
                                             size_t estimated_size)
{
    // Call base class method first to initialize buffering
    std::string session_id = CloudStorageWriter::startSession(remote_path, stream_id, estimated_size);
    if (session_id.empty())
    {
        return "";
    }

    std::lock_guard<std::mutex> lock(m_session_mutex);

    if (!ensureClientInitialized())
    {
        setLastError("MinIO client not properly initialized");
        return "";
    }

    std::string object_key = generateObjectKey(remote_path);

    auto session = std::make_unique<MultipartSession>();
    session->m_bucket_name = m_bucket_name;
    session->m_object_key = object_key;
    session->m_is_active = true;
    
    // Set configurable minimum part size (convert MB to bytes)
    session->m_min_part_size = m_config.buffering.min_part_size_mb * 1024 * 1024;

    if (!startMultipartUpload(*session))
    {
        setLastError("Failed to start multipart upload for " + object_key);
        return "";
    }

    m_active_sessions[session_id] = std::move(session);

    LOG(info) << "Started MinIO recording session: " << session_id << " -> " << m_endpoint_url << "/" << m_bucket_name
              << "/" << object_key << " (part_size: " << m_config.buffering.min_part_size_mb << " MB)" << std::endl;
    return session_id;
}

bool MinioStorageWriter::doWriteData(const std::string& session_id, const void* data, size_t size, int64_t pts,
                                     const std::string& media_type)
{
    // Validate input parameters with better error messages
    if (!data || size == 0)
    {
        setLastError("Invalid data parameters: data=" + std::string(data ? "valid" : "null") + 
                     ", size=" + std::to_string(size));
        return false;
    }

    if (session_id.empty())
    {
        setLastError("Invalid session ID: empty");
        return false;
    }

    std::lock_guard<std::mutex> lock(m_session_mutex);

    auto it = m_active_sessions.find(session_id);
    if (it == m_active_sessions.end())
    {
        setLastError("Session not found: " + session_id);
        return false;
    }

    MultipartSession& session = *it->second;
    if (!session.m_is_active)
    {
        setLastError("Session is not active: " + session_id);
        return false;
    }

    // Validate session state
    if (session.m_upload_id.empty() || session.m_bucket_name.empty() || session.m_object_key.empty())
    {
        setLastError("Invalid session state: missing upload_id, bucket_name, or object_key");
        return false;
    }

    // Accumulate data with exception safety
    try
    {
        size_t old_size = session.m_accumulated_data.size();
        session.m_accumulated_data.resize(old_size + size);
        std::memmove(session.m_accumulated_data.data() + old_size, data, size);
    }
    catch (const std::exception& e)
    {
        setLastError("Failed to accumulate data: " + std::string(e.what()));
        return false;
    }
    
    // Upload part if we have enough accumulated data
    if (session.m_accumulated_data.size() >= session.m_min_part_size)
    {
        if (!uploadPart(session, session.m_accumulated_data))
        {
            setLastError("Failed to upload part for session: " + session_id);
            return false;
        }
        
        // Clear accumulated data after successful upload
        session.m_accumulated_data.clear();
    }

    session.m_bytes_written += size;
    return true;
}

StorageResult MinioStorageWriter::doCompleteSession(const std::string& session_id, const std::string& stream_id)
{
    StorageResult result;
    std::lock_guard<std::mutex> lock(m_session_mutex);

    auto it = m_active_sessions.find(session_id);
    if (it == m_active_sessions.end())
    {
        result.success = false;
        result.message = "Session not found: " + session_id;
        setLastError(result.message);
        return result;
    }

    MultipartSession& session = *it->second;
    if (!session.m_is_active)
    {
        result.success = false;
        result.message = "Session is not active: " + session_id;
        setLastError(result.message);
        return result;
    }

    // Upload any remaining accumulated data
    if (!session.m_accumulated_data.empty())
    {
        LOG(info) << "Uploading remaining " << session.m_accumulated_data.size() 
                  << " bytes of accumulated data for session: " << session_id << endl;
        
        if (!uploadPart(session, session.m_accumulated_data))
        {
            result.success = false;
            result.message = "Failed to upload remaining data for session: " + session_id;
            setLastError(result.message);
            return result;
        }
        
        session.m_accumulated_data.clear();
    }

    // Complete multipart upload
    if (!completeMultipartUpload(session))
    {
        result.success = false;
        result.message = "Failed to complete multipart upload for session: " + session_id;
        setLastError(result.message);
        return result;
    }

    session.m_is_active = false;
    m_active_sessions.erase(it);

    result.success = true;
    result.message = "Successfully completed MinIO recording session: " + session_id;
    result.object_id = session.m_object_key;  // Set the object ID for database update
    result.bytes_written = session.m_bytes_written;
    LOG(info) << result.message << " (total bytes: " << session.m_bytes_written << ", object_id: " << session.m_object_key << ")" << std::endl;
    return result;
}

bool MinioStorageWriter::doCancelSession(const std::string& session_id, const std::string& stream_id)
{
    std::lock_guard<std::mutex> lock(m_session_mutex);

    auto it = m_active_sessions.find(session_id);
    if (it == m_active_sessions.end())
    {
        setLastError("Session not found: " + session_id);
        return false;
    }

    MultipartSession& session = *it->second;
    if (!session.m_is_active)
    {
        setLastError("Session is not active: " + session_id);
        return false;
    }

    // Abort multipart upload
    if (!abortMultipartUpload(session))
    {
        setLastError("Failed to abort multipart upload for session: " + session_id);
        return false;
    }

    session.m_is_active = false;
    m_active_sessions.erase(it);

    LOG(info) << "Cancelled MinIO recording session: " << session_id << std::endl;
    return true;
}


StorageResult MinioStorageWriter::uploadFile(const std::string& local_path, const std::string& remote_path)
{
    StorageResult result;

    if (!ensureClientInitialized())
    {
        result.success = false;
        result.message = "MinIO client not properly initialized";
        setLastError(result.message);
        return result;
    }

    if (!std::filesystem::exists(local_path))
    {
        result.success = false;
        result.message = "Local file does not exist: " + local_path;
        setLastError(result.message);
        return result;
    }

    std::string object_key = generateObjectKey(remote_path);

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::UploadObjectArgs args;
        args.bucket = m_bucket_name;
        args.object = object_key;
        args.filename = local_path;
        args.part_size = 10 * 1024 * 1024; // 10MB part size

        // Set content type based on file extension
        std::filesystem::path path(local_path);
        std::string ext = path.extension().string();
        if (ext == ".mp4")
        {
            args.content_type = "video/mp4";
        }
        else if (ext == ".mkv")
        {
            args.content_type = "video/x-matroska";
        }
        else if (ext == ".jpg" || ext == ".jpeg")
        {
            args.content_type = "image/jpeg";
        }
        else if (ext == ".png")
        {
            args.content_type = "image/png";
        }

        minio::s3::UploadObjectResponse resp = m_minio_client->UploadObject(args);
        if (!resp)
        {
            result.success = false;
            result.message = "Failed to upload file: " + resp.Error().String();
            setLastError(result.message);
            return result;
        }

        result.success = true;
        result.message = "Successfully uploaded file: " + local_path + " -> " + object_key;
        LOG(info) << result.message << std::endl;
    }
    catch (const std::exception& e)
    {
        result.success = false;
        result.message = "Exception during upload: " + std::string(e.what());
        setLastError(result.message);
    }

    return result;
}

bool MinioStorageWriter::deleteFile(const std::string& remote_path)
{
    std::string object_key = generateObjectKey(remote_path);
    return deleteObject(m_bucket_name, object_key);
}

bool MinioStorageWriter::fileExists(const std::string& remote_path) const
{
    std::string object_key = generateObjectKey(remote_path);
    return objectExists(m_bucket_name, object_key);
}

size_t MinioStorageWriter::getFileSize(const std::string& remote_path) const
{
    std::string object_key = generateObjectKey(remote_path);
    size_t size = getObjectSize(m_bucket_name, object_key);
    LOG(info) << "Getting file size: " << size << " bytes" << " for object key: " << object_key << endl;
    return size;
}

bool MinioStorageWriter::configure(const StorageConfig& config)
{
    // First configure the base class (CloudStorageWriter) to initialize buffering
    if (!CloudStorageWriter::configure(config))
    {
        setLastError("Failed to configure base CloudStorageWriter");
        return false;
    }

    m_endpoint_url = config.getParameter(StorageConstants::ENDPOINT_KEY);
    m_access_key = config.getParameter(StorageConstants::ACCESS_KEY_KEY);
    m_secret_key = config.getParameter(StorageConstants::SECRET_KEY_KEY);
    m_bucket_name = config.getParameter(StorageConstants::BUCKET_NAME_KEY);
    m_use_ssl = (config.getParameter(StorageConstants::USE_SSL_KEY, "false") == "true");

    m_config = config;

    // Initialize client with new configuration
    if (!initializeMinioClient())
    {
        setLastError("Failed to initialize MinIO client with new configuration");
        return false;
    }

    LOG(info) << "MinioStorageWriter configured for endpoint: " << m_endpoint_url << endl;
    
    // Debug: Log buffering configuration
    LOG(info) << "MinIO buffering config - buffer_size_mb=" << m_config.buffering.buffer_size_mb 
              << ", max_frames=" << m_config.buffering.max_frames 
              << ", max_upload_fps=" << m_config.buffering.max_upload_fps 
              << ", enabled=" << m_config.buffering.enabled << endl;
    return true;
}

StorageConfig MinioStorageWriter::getConfiguration() const
{
    return m_config;
}

bool MinioStorageWriter::performHealthCheck()
{
    if (!ensureClientInitialized())
    {
        return false;
    }

    return testConnection();
}

std::string MinioStorageWriter::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    return m_last_error;
}

std::string MinioStorageWriter::generateObjectKey(const std::string& remote_path) const
{
    std::string key = remote_path;
    if (!key.empty() && key[0] == '/')
    {
        key = key.substr(1);
    }
    return key;
}

bool MinioStorageWriter::initializeMinioClient()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);

    try
    {
        // Parse endpoint URL
        std::string host = m_endpoint_url;
        unsigned int port = m_use_ssl ? 443 : 9000;

        // Remove http:// or https:// prefix if present
        if (host.find("http://") == 0)
        {
            host = host.substr(7);
            m_use_ssl = false;
        }
        else if (host.find("https://") == 0)
        {
            host = host.substr(8);
            m_use_ssl = true;
        }

        // Parse port if present in host
        size_t colon_pos = host.find(':');
        if (colon_pos != std::string::npos)
        {
            try
            {
                port = std::stoi(host.substr(colon_pos + 1));
                host = host.substr(0, colon_pos);
            }
            catch (const std::exception& e)
            {
                setLastError("Failed to parse port number: " + std::string(e.what()));
                return false;
            }
        }

        // Create S3 base URL
        minio::s3::BaseUrl base_url;
        base_url.host = host;
        base_url.https = m_use_ssl;
        base_url.port = port;

        if (!base_url)
        {
            setLastError("Failed to create base URL: " + base_url.Error().String());
            return false;
        }

        // Create credential provider
        m_credentials = std::make_unique<minio::creds::StaticProvider>(m_access_key, m_secret_key);

        // Create S3 client
        m_minio_client = std::make_unique<minio::s3::Client>(base_url, m_credentials.get());
        if (!m_minio_client)
        {
            setLastError("Failed to create MinIO client");
            return false;
        }

        // Check if bucket exists and create if needed
        minio::s3::BucketExistsArgs args;
        args.bucket = m_bucket_name;

        minio::s3::BucketExistsResponse resp = m_minio_client->BucketExists(args);
        if (!resp)
        {
            setLastError("Failed to check bucket existence: " + resp.Error().String());
            return false;
        }

        if (!resp.exist)
        {
            minio::s3::MakeBucketArgs make_args;
            make_args.bucket = m_bucket_name;

            minio::s3::MakeBucketResponse make_resp = m_minio_client->MakeBucket(make_args);
            if (!make_resp)
            {
                setLastError("Failed to create bucket: " + make_resp.Error().String());
                return false;
            }
            LOG(info) << "Created MinIO bucket: " << m_bucket_name << endl;
        }

        m_client_initialized.store(true, std::memory_order_release);
        LOG(info) << "MinIO client initialized successfully" << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during client initialization: " + std::string(e.what()));
        return false;
    }
}

void MinioStorageWriter::shutdownMinioClient()
{
    std::lock_guard<std::mutex> lock(m_client_mutex);
    m_minio_client.reset();
    m_credentials.reset();
    m_client_initialized.store(false, std::memory_order_release);
}

bool MinioStorageWriter::ensureClientInitialized()
{
    if (m_client_initialized.load(std::memory_order_acquire) && m_minio_client)
    {
        return true;
    }
    return initializeMinioClient();
}

bool MinioStorageWriter::startMultipartUpload(MultipartSession& session)
{
    // Validate client is initialized
    if (!m_minio_client)
    {
        setLastError("MinIO client not initialized in startMultipartUpload");
        return false;
    }

    // Validate session parameters
    if (session.m_bucket_name.empty() || session.m_object_key.empty())
    {
        setLastError("Invalid session parameters: missing bucket_name or object_key");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::CreateMultipartUploadArgs args;
        args.bucket = session.m_bucket_name;
        args.object = session.m_object_key;

        minio::s3::CreateMultipartUploadResponse resp = m_minio_client->CreateMultipartUpload(args);
        if (!resp)
        {
            setLastError("Failed to create multipart upload: " + resp.Error().String());
            return false;
        }

        session.m_upload_id = resp.upload_id;
        session.m_etags.clear();
        session.m_part_number = 1;

        LOG(info) << "Started multipart upload: " << session.m_upload_id << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during multipart upload creation: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::uploadPart(MultipartSession& session, const std::vector<uint8_t>& data)
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        setLastError("MinIO client not initialized");
        return false;
    }

    if (session.m_upload_id.empty())
    {
        setLastError("Invalid upload ID for part upload");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Validate data before upload
        if (data.empty())
        {
            setLastError("Cannot upload empty part data");
            return false;
        }

        // Upload part with proper error handling using MinIO SDK
        minio::s3::UploadPartArgs args;
        args.bucket = session.m_bucket_name;
        args.object = session.m_object_key;
        args.upload_id = session.m_upload_id;
        args.part_number = session.m_part_number;
        args.data = std::string_view(reinterpret_cast<const char*>(data.data()), data.size());

        minio::s3::UploadPartResponse response = m_minio_client->UploadPart(args);
        if (!response)
        {
            setLastError("Failed to upload part " + std::to_string(session.m_part_number) + 
                        ": " + response.Error().String());
            return false;
        }

        // Store part info for completion with current part number
        session.m_parts.push_back(MultipartSession::PartInfo(session.m_part_number, response.etag));

        LOG(verbose) << "Uploaded part " << session.m_part_number << " with ETag: " << response.etag
                  << " bucket=" << session.m_bucket_name << " object=" << session.m_object_key
                  << " upload_id=" << session.m_upload_id << " stream_id=" << m_current_stream_id
                  << " data_size=" << data.size() << " bytes" << endl;

        // Increment part number for next upload
        session.m_part_number++;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during part upload: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::completeMultipartUpload(MultipartSession& session)
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        setLastError("MinIO client not initialized");
        return false;
    }

    if (session.m_upload_id.empty())
    {
        setLastError("Invalid upload ID for completion");
        return false;
    }

    if (session.m_parts.empty())
    {
        setLastError("No parts uploaded, cannot complete multipart upload");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Sort parts by part number to ensure correct order
        std::sort(session.m_parts.begin(), session.m_parts.end(),
                  [](const auto& a, const auto& b) { return a.part_number < b.part_number; });

        // Convert parts to MinIO SDK format
        std::list<minio::s3::Part> parts;
        for (const auto& part : session.m_parts)
        {
            parts.push_back(minio::s3::Part(part.part_number, part.etag));
        }

        // Create completion request
        minio::s3::CompleteMultipartUploadArgs args;
        args.bucket = session.m_bucket_name;
        args.object = session.m_object_key;
        args.upload_id = session.m_upload_id;
        args.parts = parts;

        minio::s3::CompleteMultipartUploadResponse response = m_minio_client->CompleteMultipartUpload(args);
        if (!response)
        {
            setLastError("Failed to complete multipart upload: " + response.Error().String());
            return false;
        }

        LOG(info) << "Completed multipart upload: " << session.m_upload_id 
                  << " -> " << session.m_bucket_name << "/" << session.m_object_key 
                  << " (" << session.m_parts.size() << " parts)" << endl;

        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during multipart upload completion: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::abortMultipartUpload(MultipartSession& session)
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        setLastError("MinIO client not initialized");
        return false;
    }

    if (session.m_upload_id.empty())
    {
        setLastError("Invalid upload ID for abort");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::AbortMultipartUploadArgs args;
        args.bucket = session.m_bucket_name;
        args.object = session.m_object_key;
        args.upload_id = session.m_upload_id;

        minio::s3::AbortMultipartUploadResponse response = m_minio_client->AbortMultipartUpload(args);
        if (!response)
        {
            setLastError("Failed to abort multipart upload: " + response.Error().String());
            return false;
        }

        LOG(info) << "Aborted multipart upload: " << session.m_upload_id << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during multipart upload abort: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::putObject(const std::string& bucket_name, const std::string& object_key, 
                                   const std::vector<uint8_t>& data)
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        setLastError("MinIO client not initialized");
        return false;
    }

    if (data.empty())
    {
        setLastError("Cannot upload empty object data");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Create a string stream from the data
        std::stringstream stream;
        stream.write(reinterpret_cast<const char*>(data.data()), data.size());
        stream.seekg(0);

        minio::s3::PutObjectArgs args(stream, data.size(), 10 * 1024 * 1024);
        args.bucket = bucket_name;
        args.object = object_key;

        minio::s3::PutObjectResponse response = m_minio_client->PutObject(args);
        if (!response)
        {
            setLastError("Failed to put object: " + response.Error().String());
            return false;
        }

        LOG(verbose) << "Put object: " << bucket_name << "/" << object_key 
                    << " (" << data.size() << " bytes)" << endl;

        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during put object: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::deleteObject(const std::string& bucket_name, const std::string& object_key)
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        setLastError("MinIO client not initialized");
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::RemoveObjectArgs args;
        args.bucket = bucket_name;
        args.object = object_key;

        minio::s3::RemoveObjectResponse response = m_minio_client->RemoveObject(args);
        if (!response)
        {
            setLastError("Failed to delete object: " + response.Error().String());
            return false;
        }

        LOG(verbose) << "Deleted object: " << bucket_name << "/" << object_key << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        setLastError("Exception during object deletion: " + std::string(e.what()));
        return false;
    }
}

bool MinioStorageWriter::objectExists(const std::string& bucket_name, const std::string& object_key) const
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::StatObjectArgs args;
        args.bucket = bucket_name;
        args.object = object_key;

        minio::s3::StatObjectResponse response = m_minio_client->StatObject(args);
        return static_cast<bool>(response);
    }
    catch (const std::exception& e)
    {
        return false;
    }
}

size_t MinioStorageWriter::getObjectSize(const std::string& bucket_name, const std::string& object_key) const
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        return 0;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        minio::s3::StatObjectArgs args;
        args.bucket = bucket_name;
        args.object = object_key;

        minio::s3::StatObjectResponse response = m_minio_client->StatObject(args);
        if (!response)
        {
            return 0;
        }

        return response.size;
    }
    catch (const std::exception& e)
    {
        return 0;
    }
}

bool MinioStorageWriter::testConnection() const
{
    if (!m_client_initialized.load(std::memory_order_acquire))
    {
        return false;
    }

    try
    {
        std::lock_guard<std::mutex> lock(m_client_mutex);

        // Try to list objects in the bucket (limit to 1 to minimize overhead)
        minio::s3::ListObjectsArgs args;
        args.bucket = m_bucket_name;
        args.max_keys = 1;

        minio::s3::ListObjectsResult response = m_minio_client->ListObjects(args);
        // ListObjectsResult uses operator bool() to check for errors
        // Check if we can get at least one item to verify connection
        bool has_objects = static_cast<bool>(response);

        if (!has_objects)
        {
            return false;
        }

        LOG(info) << "MinIO connection test successful" << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        return false;
    }
}

void MinioStorageWriter::setLastError(const std::string& error) const
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error = error;
}

void MinioStorageWriter::clearLastError()
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error.clear();
}

} // namespace nv_vms

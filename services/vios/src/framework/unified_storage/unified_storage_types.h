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

#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>
#include <cstdint> // For uint32_t, uint64_t
 
namespace nv_vms
{

/**
 * @brief Storage type enumeration for both reader and writer
 */
enum class StorageType
{
    UNKNOWN,
    LOCAL,
    CLOUD
};
 
/**
 * @brief Enumeration for cloud storage types
 */
enum class CloudStorageType
{
    UNKNOWN = 0,
    AWS_S3,
    GOOGLE_CLOUD,
    AZURE_BLOB,
    MINIO
};
 
 /**
 * @brief Structure representing a cloud storage object/file
 */
struct CloudObject
{
    std::string key;                    // Object key/path
    std::string etag;                   // Entity tag
    uint64_t size = 0;                  // Size in bytes
    std::string lastModified;           // Last modified timestamp
    std::string storageClass;           // Storage class (STANDARD, IA, etc.)
    std::map<std::string, std::string, std::less<>> metadata; // Custom metadata
    
    CloudObject() = default;
    CloudObject(const std::string& key, uint64_t size) : key(key), size(size)
    {
    }
};

/**
 * @brief Bucket information structure for cloud storage
 */
struct BucketInfo
{
    std::string name;                   // Bucket name
    std::string region;                 // Bucket region
    std::string creationDate;           // Creation date
    uint64_t objectCount;               // Number of objects in bucket
    uint64_t totalSize;                 // Total size of all objects
    std::map<std::string, std::string, std::less<>> metadata; // Custom metadata
    
    BucketInfo() : objectCount(0), totalSize(0)
    {
    }
    BucketInfo(const std::string& name) : name(name), objectCount(0), totalSize(0)
    {
    }
};

/**
 * @brief File information structure for both local and cloud storage
 */
struct FileInfo
{
    std::string path;                   // File path/key
    std::string name;                   // File name
    uint64_t size;                      // File size in bytes
    std::string lastModified;           // Last modified timestamp
    std::string contentType;            // MIME type
    std::map<std::string, std::string, std::less<>> metadata; // Custom metadata
    bool isDirectory;                   // Whether this is a directory
    
    FileInfo() : size(0), isDirectory(false) {}
    FileInfo(const std::string& path, const std::string& name, uint64_t size) 
        : path(path), name(name), size(size), isDirectory(false) {}
};
 
 /**
 * @brief Result structure for cloud operations
 */
struct CloudResult {
    bool success = false;
    std::string message;
    std::string errorCode;
    std::chrono::milliseconds duration{0};
    
    CloudResult() = default;
    CloudResult(bool success, const std::string& message = "") 
        : success(success), message(message) {}
};

/**
 * @brief Common result structure for storage operations
 */
struct StorageResult
{
    bool success;
    std::string message;
    std::string errorCode;
    std::string storage_path;
    std::string object_id;  // Object ID for cloud storage (e.g., MinIO object name)
    size_t bytes_written;
    size_t bytes_read;
    std::chrono::milliseconds duration;
    
    StorageResult() : success(false), bytes_written(0), bytes_read(0), duration(0) {}
    StorageResult(bool success, const std::string& message = "") 
        : success(success), message(message), bytes_written(0), bytes_read(0), duration(0) {}
};

/**
 * @brief Result structure for list operations
 */
struct CloudListResult : public CloudResult {
    std::string bucket;
    std::string prefix;
    std::vector<CloudObject> objects;
    uint64_t totalSize = 0;
    uint32_t count = 0;
    bool isTruncated = false;
    std::string nextMarker;
    
    CloudListResult() = default;
    CloudListResult(bool success, const std::string& message = "") 
        : CloudResult(success, message) {}
};
 
 /**
  * @brief Result structure for file operations (alias for StorageResult for backward compatibility)
  */
 using FileResult = StorageResult;
 
 /**
  * @brief Structure for file download request
  */
 struct FileDownloadRequest
 {
     std::string remote_path;    // Remote path in cloud storage
     std::string local_path;     // Local path where file should be downloaded
     std::string file_name;      // Optional file name for identification
     
     FileDownloadRequest() = default;
     FileDownloadRequest(const std::string& remote, const std::string& local) 
         : remote_path(remote), local_path(local) {}
     FileDownloadRequest(const std::string& remote, const std::string& local, const std::string& name) 
         : remote_path(remote), local_path(local), file_name(name) {}
 };
 
 /**
 * @brief Structure for individual file download result
 */
 struct FileDownloadResult
 {
     std::string remote_path;    // Original remote path
     std::string local_path;     // Local path where file was downloaded
     std::string bucket;         // Cloud bucket (for cloud storage)
     std::string object_key;     // Cloud object key (for cloud storage)
     bool success;               // Whether download was successful
     std::string error_message;  // Error message if failed
     std::string error_code;     // Error code if failed
     size_t bytes_downloaded;    // Number of bytes downloaded
     double download_speed;      // Download speed in MB/s
     bool was_resumed;           // Whether download was resumed
     bool was_skipped;           // Whether download was skipped (e.g., object doesn't exist)
     std::chrono::milliseconds duration; // Time taken for download
     
     FileDownloadResult() : success(false), bytes_downloaded(0), download_speed(0.0), 
                           was_resumed(false), was_skipped(false), duration(0) {}
     FileDownloadResult(const std::string& remote, const std::string& local, bool success = false) 
         : remote_path(remote), local_path(local), success(success), bytes_downloaded(0), 
           download_speed(0.0), was_resumed(false), was_skipped(false), duration(0) {}
 };

 /**
  * @brief Structure for bulk download operation result
  */
 struct DownloadResult
 {
     bool overall_success;                           // Overall success of the operation
     std::string error_message;                      // General error message
     std::string error_code;                         // Error code
     std::vector<FileDownloadResult> file_results;   // Results for each file
     size_t total_files;                             // Total number of files requested
     size_t successful_downloads;                    // Number of successful downloads
     size_t failed_downloads;                        // Number of failed downloads
     size_t total_bytes_downloaded;                  // Total bytes downloaded
     double average_speed;                           // Average download speed in MB/s
     std::chrono::milliseconds total_duration;       // Total time taken
     
     DownloadResult() : overall_success(false), total_files(0), successful_downloads(0), 
                       failed_downloads(0), total_bytes_downloaded(0), average_speed(0.0), total_duration(0) {}
     
     void addResult(const FileDownloadResult& result) {
         file_results.push_back(result);
         total_duration += result.duration;
         if (result.success) {
             successful_downloads++;
             total_bytes_downloaded += result.bytes_downloaded;
         } else {
             failed_downloads++;
         }
     }
     
     void finalize() {
         total_files = file_results.size();
         overall_success = (failed_downloads == 0);
         
         // Calculate average speed
         if (total_duration.count() > 0) {
             average_speed = (total_bytes_downloaded / 1024.0 / 1024.0) / (total_duration.count() / 1000.0);
         }
     }
 };
 
 /**
  * @brief Result structure for list operations
  */
struct FileListResult : public StorageResult
{
    std::string path;                   // Path that was listed
    std::vector<FileInfo> files;        // List of files/directories
    uint64_t totalSize;                 // Total size of all files
    uint32_t count;                     // Number of files
    bool isTruncated;                   // Whether the list was truncated
    std::string nextMarker;             // Marker for pagination
    
    FileListResult() : totalSize(0), count(0), isTruncated(false) {}
    FileListResult(bool success, const std::string& message = "") 
        : StorageResult(success, message), totalSize(0), count(0), isTruncated(false) {}
};
 
 /**
  * @brief Multi-download result (alias for backward compatibility with cloud reader)
  */
 using MultiDownloadResult = DownloadResult;
 
 /**
  * @brief Progress callback function type for operations
  */
 using ProgressCallback = std::function<void(const std::string& filePath, 
                                            uint64_t bytesProcessed, 
                                            uint64_t totalBytes, 
                                            double speed)>;
 
 /**
  * @brief Download progress callback (alias for backward compatibility)
  */
 using DownloadProgressCallback = ProgressCallback;
 
 /**
  * @brief Common storage configuration structure used across all storage systems
  */
 struct StorageConfig
{
    std::string storage_type;
    std::map<std::string, std::string, std::less<>> parameters;

    // Buffering configuration (for cloud storage only)
    struct BufferingConfig
    {
        bool enabled;
        size_t buffer_size_mb;
        size_t max_frames;
        double max_upload_fps;
        bool auto_adapt_rate;
        size_t flush_timeout_sec;
        size_t min_part_size_mb;
        
        BufferingConfig() : enabled(false), buffer_size_mb(50), max_frames(1500), 
                           max_upload_fps(35.0), auto_adapt_rate(true), 
                           flush_timeout_sec(30), min_part_size_mb(5) {}
    } buffering;

    StorageConfig() : buffering() {}

    void setParameter(const std::string& key, const std::string& value)
    {
        parameters[key] = value;
    }

    std::string getParameter(const std::string& key, const std::string& default_value = "") const
    {
        std::map<std::string, std::string, std::less<>>::const_iterator it = parameters.find(key);
        return (it != parameters.end()) ? it->second : default_value;
    }
};
 
 /**
  * @brief Common storage statistics structure used across all storage systems
  */
 struct StorageStats
 {
     uint64_t totalRequests = 0;
     uint64_t successfulRequests = 0;
     uint64_t failedRequests = 0;
     uint64_t bytesRead = 0;
     uint64_t bytesWritten = 0;
     uint64_t framesWritten = 0;
     uint64_t filesListed = 0;
     double averageSpeed = 0.0;          // MB/s
     std::chrono::milliseconds totalLatency{0};
     std::chrono::milliseconds averageLatency{0};
     std::chrono::system_clock::time_point lastRequestTime;
     
     // Error tracking
     std::map<std::string, uint32_t, std::less<>> errorCounts;
     
     // Buffering-specific stats (for cloud storage)
     struct BufferingStats
     {
         uint64_t frames_buffered = 0;
         uint64_t frames_uploaded = 0;
         uint64_t frames_dropped = 0;
         double buffer_utilization_percent = 0.0;
         double upload_rate_fps = 0.0;
         std::chrono::milliseconds buffer_delay{0};
         bool is_buffering_active = false;
     } buffering;
     
     void recordRequest(bool success, std::chrono::milliseconds latency, 
                       const std::string& errorCode = "") {
         totalRequests++;
         totalLatency += latency;
         lastRequestTime = std::chrono::system_clock::now();
         
         if (success) {
             successfulRequests++;
         } else {
             failedRequests++;
             if (!errorCode.empty()) {
                 errorCounts[errorCode]++;
             }
         }
         
         if (totalRequests > 0) {
             averageLatency = totalLatency / totalRequests;
         }
     }
 };
 
 /**
  * @brief Reader statistics (alias for backward compatibility)
  */
 using ReaderStats = StorageStats;
 
 /**
  * @brief Common error codes used across storage systems
  */
 namespace ErrorCodes
 {
     const std::string NOT_INITIALIZED = "NOT_INITIALIZED";
     const std::string NOT_IMPLEMENTED = "NOT_IMPLEMENTED";
     const std::string INVALID_CONFIGURATION = "INVALID_CONFIGURATION";
     const std::string FILE_NOT_FOUND = "FILE_NOT_FOUND";
     const std::string PERMISSION_DENIED = "PERMISSION_DENIED";
     const std::string NETWORK_ERROR = "NETWORK_ERROR";
     const std::string TIMEOUT = "TIMEOUT";
     const std::string QUOTA_EXCEEDED = "QUOTA_EXCEEDED";
     const std::string EXCEPTION = "EXCEPTION";
     const std::string INVALID_BUCKET_NAME = "INVALID_BUCKET_NAME";
     const std::string INVALID_OBJECT_KEY = "INVALID_OBJECT_KEY";
     const std::string DELETE_FAILED = "DELETE_FAILED";
     
     // Cloud storage specific error codes
     const std::string LIST_BUCKETS_ERROR = "LIST_BUCKETS_ERROR";
     const std::string BUCKET_EXISTS_ERROR = "BUCKET_EXISTS_ERROR";
     const std::string BUCKET_NOT_FOUND = "BUCKET_NOT_FOUND";
     const std::string OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND";
     const std::string UPLOAD_FAILED = "UPLOAD_FAILED";
     const std::string DOWNLOAD_FAILED = "DOWNLOAD_FAILED";
     const std::string RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED";
     const std::string BUFFER_FULL = "BUFFER_FULL";
     const std::string SESSION_CANCELLED = "SESSION_CANCELLED";
 }
 
 /**
  * @brief Common storage constants
  */
 namespace StorageConstants
 {
    // Storage types
    const std::string LOCAL_STORAGE = "local";
    const std::string CLOUD_STORAGE = "cloud";
    
    // Core configuration keys
    const std::string CLOUD_TYPE_KEY = "cloud_type";
    
    // Cloud storage configuration keys
    const std::string BUCKET_NAME_KEY = "bucket_name";
    const std::string ENDPOINT_KEY = "endpoint";
    const std::string ACCESS_KEY_KEY = "access_key";
    const std::string SECRET_KEY_KEY = "secret_key";
    const std::string REGION_KEY = "region";
    const std::string USE_SSL_KEY = "use_ssl";
    const std::string TIMEOUT_SECONDS_KEY = "timeout_seconds";
    const std::string MAX_RETRIES_KEY = "max_retries";
    
    // Local storage configuration keys
    const std::string BASE_PATH_KEY = "base_path";
    const std::string CREATE_DIRECTORIES_KEY = "create_directories";
    const std::string RECURSIVE_LISTING_KEY = "recursive_listing";
    const std::string MAX_DEPTH_KEY = "max_depth";
    const std::string INCLUDE_HIDDEN_KEY = "include_hidden";
    const std::string MAX_BATCH_SIZE_KEY = "max_batch_size";
    const std::string ENABLE_FILE_OPERATIONS_KEY = "enable_file_operations";
    const std::string ENABLE_DIRECTORY_OPERATIONS_KEY = "enable_directory_operations";
    const std::string DELETE_TIMEOUT_SECONDS_KEY = "delete_timeout_seconds";
    const std::string ENABLE_BATCH_OPERATIONS_KEY = "enable_batch_operations";
    
    // Media configuration keys
    const std::string VIDEO_CODEC_KEY = "video_codec";
    const std::string AUDIO_SUPPORTED_KEY = "audio_supported";
    const std::string AUDIO_CODEC_KEY = "audio_codec";
    const std::string AUDIO_SAMPLE_RATE_KEY = "audio_sample_rate";
    const std::string AUDIO_CHANNELS_KEY = "audio_channels";
    const std::string AUDIO_ENABLE_KEY = "audio_enable";    
    const std::string AUDIO_CONTAINER_KEY = "audio_container";
    const std::string CODEC_DATA_KEY = "codec_data";
    
    // Cloud provider specific keys
    const std::string PROJECT_ID_KEY = "project_id";
    const std::string KEY_FILE_PATH_KEY = "key_file_path";
    const std::string STORAGE_ACCOUNT_KEY = "storage_account";
    const std::string CONTAINER_NAME_KEY = "container_name";
    
    // Cloud storage type constants
    const std::string AWS_S3_TYPE = "s3";
    const std::string AWS_S3_ALT_TYPE = "aws_s3";
    const std::string AWS_S3_ALT_TYPE2 = "amazon_s3";
    const std::string GOOGLE_CLOUD_TYPE = "gcs";
    const std::string GOOGLE_CLOUD_ALT_TYPE = "google_cloud";
    const std::string GOOGLE_CLOUD_ALT_TYPE2 = "gcp";
    const std::string AZURE_BLOB_TYPE = "azure";
    const std::string AZURE_BLOB_ALT_TYPE = "azure_blob";
    const std::string MINIO_TYPE = "minio";
 } // namespace StorageConstants
 
 } // namespace nv_vms
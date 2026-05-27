# Unified Storage Framework - API Reference

This document provides a comprehensive reference for all APIs in the Unified Storage Framework.

## Table of Contents

1. [Common Types and Structures](#common-types-and-structures)
2. [Reader API](#reader-api)
3. [Writer API](#writer-api)
4. [Manager API](#manager-api)
5. [Factory Classes](#factory-classes)
6. [Configuration](#configuration)
7. [Error Handling](#error-handling)

## Common Types and Structures

### StorageType Enum
```cpp
enum class StorageType {
    LOCAL,
    CLOUD
};
```

### CloudStorageType Enum
```cpp
enum class CloudStorageType {
    UNKNOWN = 0,
    AWS_S3,
    GOOGLE_CLOUD,
    AZURE_BLOB,
    MINIO
};
```

### FileInfo Structure
```cpp
struct FileInfo {
    std::string path;                   // File path/key
    std::string name;                   // File name
    uint64_t size;                      // File size in bytes
    std::string lastModified;           // Last modified timestamp
    std::string contentType;            // MIME type
    std::map<std::string, std::string> metadata; // Custom metadata
    bool isDirectory;                   // Whether this is a directory
};
```

### CloudObject Structure
```cpp
struct CloudObject {
    std::string key;                    // Object key/path
    std::string etag;                   // Entity tag
    uint64_t size = 0;                  // Size in bytes
    std::string lastModified;           // Last modified timestamp
    std::string storageClass;           // Storage class
    std::map<std::string, std::string> metadata; // Custom metadata
};
```

### StorageConfig Structure
```cpp
struct StorageConfig {
    std::string storage_type;
    std::map<std::string, std::string> parameters;
    
    void setParameter(const std::string& key, const std::string& value);
    std::string getParameter(const std::string& key, const std::string& defaultValue = "") const;
    bool getParameterAsBool(const std::string& key, bool defaultValue = false) const;
    int getParameterAsInt(const std::string& key, int defaultValue = 0) const;
};
```

## Reader API

### UnifiedStorageReader (Base Class)

#### Core Methods
```cpp
class UnifiedStorageReader {
public:
    virtual ~UnifiedStorageReader() = default;
    
    // Configuration
    virtual bool configureStorage(const StorageConfig& config) = 0;
    virtual bool isAvailable() const = 0;
    
    // File operations
    virtual CloudListResult listObjects(const std::string& bucketName, 
                                       const std::string& prefix = "",
                                       int maxKeys = 1000) = 0;
    virtual CloudResult downloadObject(const std::string& bucketName,
                                      const std::string& objectKey,
                                      const std::string& localFilePath) = 0;
    
    // Utility methods
    virtual std::string generateSignedUrl(const std::string& bucketName,
                                         const std::string& objectKey,
                                         int expirationSeconds = 3600) = 0;
    virtual Json::Value listResultToJson(const CloudListResult& result) const = 0;
    
    // Statistics
    virtual StorageStats getReaderStats() const = 0;
    virtual void resetReaderStats() = 0;
};
```

#### Example Usage
```cpp
#include "unified_storage_reader_factory.h"

// Create reader
auto reader = UnifiedStorageReaderFactory::createReader("cloud");

// Configure
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
reader->configureStorage(config);

// List objects
auto listResult = reader->listObjects("my-bucket", "camera1/", 100);
if (listResult.success) {
    for (const auto& obj : listResult.objects) {
        std::cout << "Object: " << obj.key << " (" << obj.size << " bytes)" << std::endl;
    }
}

// Download object
auto downloadResult = reader->downloadObject("my-bucket", "camera1/video.mkv", "/tmp/video.mkv");
if (downloadResult.success) {
    std::cout << "Downloaded successfully" << std::endl;
}
```

## Writer API

### UnifiedStorageWriter (Base Class)

#### Core Methods
```cpp
class UnifiedStorageWriter {
public:
    virtual ~UnifiedStorageWriter() = default;
    
    // Configuration
    virtual bool configureStorage(const StorageConfig& config) = 0;
    virtual bool isAvailable() const = 0;
    
    // Session management
    virtual std::string startWrite(const std::string& filePath,
                                  int width, int height, int fps,
                                  const std::string& codec = "h264",
                                  bool enableAudio = false) = 0;
    virtual bool stopWrite(const std::string& sessionId) = 0;
    virtual bool pauseWrite(const std::string& sessionId) = 0;
    virtual bool resumeWrite(const std::string& sessionId) = 0;
    
    // Pipeline management
    virtual bool changeFileLocation(const std::string& sessionId, 
                                   const std::string& newFilePath) = 0;
    virtual bool isSessionActive(const std::string& sessionId) const = 0;
    
    // Statistics
    virtual StorageStats getWriterStats() const = 0;
    virtual void resetWriterStats() = 0;
    
    // Test mode
    virtual void enableTestMode(bool enable, int failureAfter = 1) = 0;
};
```

#### Example Usage
```cpp
#include "unified_storage_writer_factory.h"

// Create writer
auto writer = UnifiedStorageWriterFactory::createWriter("cloud");

// Configure
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-storage");
config.setParameter("video_codec", "h264");
config.setParameter("audio_supported", "false");
writer->configureStorage(config);

// Start recording
std::string sessionId = writer->startWrite(
    "camera1/2024/01/15/10/1234567890.mkv",
    1920, 1080, 30, "h264", false
);

// Change file location (for new recording segment)
writer->changeFileLocation(sessionId, "camera1/2024/01/15/10/1234567891.mkv");

// Stop recording
writer->stopWrite(sessionId);
```

## Manager API

### UnifiedStorageManager (Base Class)

#### Core Methods
```cpp
class UnifiedStorageManager {
public:
    virtual ~UnifiedStorageManager() = default;
    
    // Configuration
    virtual bool configureStorage(const StorageConfig& config) = 0;
    virtual bool isAvailable() const = 0;
    virtual std::string getStorageMode() const = 0;
    virtual StorageType getStorageType() const = 0;
    
    // File operations
    virtual DeleteResult deleteFile(const std::string& path) = 0;
    virtual DeleteResult deleteDirectory(const std::string& path, bool recursive = false) = 0;
    virtual bool isFileExist(const std::string& path) const = 0;
    
    // Multi-file operations
    virtual MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths) = 0;
    virtual MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path,
                                                    const std::string& pattern = "",
                                                    bool recursive = false) = 0;
    
    // Bucket operations (cloud storage)
    virtual BucketResult createBucket(const std::string& bucket_name) = 0;
    virtual BucketResult deleteBucket(const std::string& bucket_name, bool force = false) = 0;
    virtual BucketResult checkBucketExists(const std::string& bucket_name) = 0;
    virtual std::vector<BucketInfo> listBuckets() = 0;
    
    // Directory operations (local storage)
    virtual bool createDirectory(const std::string& path, bool create_parents = true) = 0;
    virtual bool directoryExists(const std::string& path) const = 0;
    
    // Statistics and monitoring
    virtual StorageStats getManagerStats() const = 0;
    virtual void resetManagerStats() = 0;
    virtual bool performHealthCheck() = 0;
    virtual std::string getLastError() const = 0;
};
```

#### Result Structures

##### DeleteResult
```cpp
struct DeleteResult {
    bool success;
    std::string message;
    std::string errorCode;
    std::string deletedPath;
    size_t deletedSize;
    std::chrono::milliseconds duration;
};
```

##### MultiDeleteResult
```cpp
struct MultiDeleteResult {
    bool overall_success;
    std::string error_message;
    std::string error_code;
    std::vector<DeleteResult> delete_results;
    size_t total_files;
    size_t successful_deletes;
    size_t failed_deletes;
    size_t total_bytes_freed;
    std::chrono::milliseconds total_duration;
};
```

##### BucketResult
```cpp
struct BucketResult {
    bool success;
    std::string message;
    std::string errorCode;
    std::string bucketName;
    std::chrono::milliseconds duration;
};
```

#### Example Usage
```cpp
#include "unified_storage_manager_factory.h"

// Create manager
auto manager = UnifiedStorageManagerFactory::createManager("cloud");

// Configure
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-storage");
manager->configureStorage(config);

// Delete single file
DeleteResult result = manager->deleteFile("camera1/old_video.mkv");
if (result.success) {
    std::cout << "Deleted " << result.deletedSize << " bytes in " 
              << result.duration.count() << "ms" << std::endl;
}

// Delete multiple files
std::vector<std::string> files = {"file1.mkv", "file2.mkv", "file3.mkv"};
MultiDeleteResult multiResult = manager->deleteMultipleFiles(files);
std::cout << "Deleted " << multiResult.successful_deletes << " of " 
          << multiResult.total_files << " files" << std::endl;

// Delete files matching pattern
MultiDeleteResult patternResult = manager->deleteFilesInDirectory(
    "camera1/2024/01/15/", "*.mkv", true);
std::cout << "Deleted " << patternResult.successful_deletes << " files matching pattern" << std::endl;

// Bucket operations
BucketResult bucketResult = manager->createBucket("new-bucket");
if (bucketResult.success) {
    std::cout << "Created bucket: " << bucketResult.bucketName << std::endl;
}
```

## Factory Classes

### UnifiedStorageReaderFactory
```cpp
class UnifiedStorageReaderFactory {
public:
    static std::shared_ptr<UnifiedStorageReader> createReader(const std::string& type);
    static std::shared_ptr<UnifiedStorageReader> createReader(CloudStorageType type, 
                                                             const CloudReaderConfig& config);
};
```

### UnifiedStorageWriterFactory
```cpp
class UnifiedStorageWriterFactory {
public:
    static std::shared_ptr<UnifiedStorageWriter> createWriter(const std::string& type);
    static std::shared_ptr<UnifiedStorageWriter> createWriter(StorageType type, 
                                                             const StorageConfig& config);
};
```

### UnifiedStorageManagerFactory
```cpp
class UnifiedStorageManagerFactory {
public:
    static std::shared_ptr<UnifiedStorageManager> createManager(const std::string& type);
    static std::shared_ptr<UnifiedStorageManager> createManager(StorageType type, 
                                                               const StorageConfig& config);
};
```

## Configuration

### Common Parameters

| Parameter | Type | Description | Default | Required |
|-----------|------|-------------|---------|----------|
| `storage_type` | string | "local" or "cloud" | - | Yes |
| `cloud_type` | string | "minio", "s3", "gcs", "azure" | - | For cloud |
| `endpoint` | string | Cloud storage endpoint | - | For cloud |
| `access_key` | string | Access key for authentication | - | For cloud |
| `secret_key` | string | Secret key for authentication | - | For cloud |
| `bucket_name` | string | Cloud storage bucket | - | For cloud |
| `region` | string | Cloud storage region | "us-east-1" | No |
| `use_ssl` | string | Use SSL/TLS | "true" | No |
| `timeout_seconds` | string | Operation timeout | "30" | No |
| `max_retries` | string | Maximum retry attempts | "3" | No |

### Writer-Specific Parameters

| Parameter | Type | Description | Default | Required |
|-----------|------|-------------|---------|----------|
| `video_codec` | string | "h264" or "h265" | "h264" | No |
| `audio_supported` | string | Enable audio support | "false" | No |
| `buffer_size_mb` | string | Cloud buffer size | "100" | No |
| `max_upload_fps` | string | Max upload rate | "30" | No |
| `auto_adapt_rate` | string | Adaptive rate control | "true" | No |
| `flush_timeout_sec` | string | Buffer flush timeout | "5" | No |
| `min_part_size_mb` | string | Minimum part size for multipart upload | "5" | No |

## Error Handling

### Error Codes

| Error Code | Description |
|------------|-------------|
| `FILE_NOT_FOUND` | File or object does not exist |
| `PERMISSION_DENIED` | Insufficient permissions |
| `NOT_INITIALIZED` | Manager/Reader/Writer not initialized |
| `INVALID_CONFIGURATION` | Invalid configuration parameters |
| `NETWORK_ERROR` | Network connectivity issues |
| `TIMEOUT` | Operation timed out |
| `BUCKET_NOT_FOUND` | Cloud storage bucket does not exist |
| `BUCKET_ALREADY_EXISTS` | Bucket already exists |
| `BUCKET_NOT_EMPTY` | Bucket is not empty (for deletion) |
| `EXCEPTION` | Unexpected exception occurred |

### Error Handling Example
```cpp
// Example error handling
auto result = manager->deleteFile("nonexistent.mkv");
if (!result.success) {
    std::cout << "Error: " << result.message << std::endl;
    std::cout << "Error Code: " << result.errorCode << std::endl;
    std::cout << "Duration: " << result.duration.count() << "ms" << std::endl;
    
    // Handle specific error types
    if (result.errorCode == "FILE_NOT_FOUND") {
        std::cout << "File does not exist" << std::endl;
    } else if (result.errorCode == "PERMISSION_DENIED") {
        std::cout << "Permission denied" << std::endl;
    } else if (result.errorCode == "NETWORK_ERROR") {
        std::cout << "Network error - check connectivity" << std::endl;
    }
}
```

### Statistics and Monitoring

All components provide comprehensive statistics through the `StorageStats` structure:

```cpp
struct StorageStats {
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
    std::map<std::string, uint32_t> errorCounts;
    
    // Buffering-specific stats (for cloud storage writers)
    struct BufferingStats {
        uint64_t frames_buffered = 0;
        uint64_t frames_uploaded = 0;
        uint64_t frames_dropped = 0;
        double buffer_utilization_percent = 0.0;
        double upload_rate_fps = 0.0;
        std::chrono::milliseconds buffer_delay{0};
        bool is_buffering_active = false;
    } buffering;
};
```

### Statistics Usage Example
```cpp
// Get statistics
StorageStats stats = manager->getManagerStats();
std::cout << "Total operations: " << stats.totalRequests << std::endl;
std::cout << "Success rate: " << (stats.successfulRequests * 100.0 / stats.totalRequests) << "%" << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// For writers, additional buffering stats
std::cout << "Buffer utilization: " << stats.buffering.buffer_utilization_percent << "%" << std::endl;
std::cout << "Upload rate: " << stats.buffering.upload_rate_fps << " fps" << std::endl;

// Reset statistics
manager->resetManagerStats();
```

## Thread Safety

All components are thread-safe and can be used in multi-threaded environments:

```cpp
// Multiple threads can safely use the same instance
std::thread thread1([&manager]() {
    manager->deleteFile("file1.txt");
});

std::thread thread2([&manager]() {
    manager->deleteFile("file2.txt");
});

thread1.join();
thread2.join();
```

## Performance Considerations

### Best Practices

1. **Reuse Instances**: Create reader/writer/manager instances once and reuse them
2. **Batch Operations**: Use batch operations when deleting multiple files
3. **Pattern Matching**: Use specific patterns to avoid unnecessary enumeration
4. **Connection Pooling**: The framework handles connection pooling internally
5. **Monitoring**: Monitor statistics for performance optimization

### Performance Tuning

```cpp
// Configure for high-performance scenarios
StorageConfig config;
config.setParameter("timeout_seconds", "60");  // Longer timeout for large files
config.setParameter("max_retries", "5");      // More retries for reliability
config.setParameter("buffer_size_mb", "200"); // Larger buffer for cloud storage
config.setParameter("max_upload_fps", "60");  // Higher upload rate
```

## Recent API Enhancements

### Timing Measurements
All operations now include accurate timing measurements in the result structures:
- `DeleteResult.duration`
- `MultiDeleteResult.total_duration`
- `BucketResult.duration`
- `CloudResult.duration`

### Bytes Reporting
File deletion operations now return actual bytes deleted:
- `DeleteResult.deletedSize`
- `MultiDeleteResult.total_bytes_freed`

### Enhanced Error Handling
More detailed error codes and messages for better debugging and monitoring. 
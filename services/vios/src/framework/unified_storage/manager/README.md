# Unified Storage Manager Library

A C++ abstraction library for unified storage management operations supporting both local file systems and cloud storage providers including AWS S3, Google Cloud Storage, Azure Blob Storage, and MinIO.

## Overview

The Unified Storage Manager Library provides a consistent interface for administrative operations on different storage backends. It follows the same architectural patterns as the Unified Storage Reader and Writer, ensuring consistency across the storage framework.

### Key Features

- **Unified Interface**: Single API for both local and cloud storage management
- **Multi-Cloud Support**: AWS S3, Google Cloud Storage, Azure Blob Storage, MinIO
- **Local File System**: Full support for local file operations
- **Factory Pattern**: Easy creation of managers with proper configuration
- **Thread-Safe**: Concurrent operations with proper synchronization
- **Performance Monitoring**: Built-in statistics and performance metrics
- **Error Handling**: Comprehensive error reporting and retry logic
- **Bucket Management**: Create, delete, and list cloud storage buckets
- **File Deletion**: Delete individual files and directories
- **Pattern Matching**: Delete files matching specific patterns

## Architecture

### Core Components

1. **UnifiedStorageManager** - Abstract base class that defines the unified interface
2. **UnifiedLocalStorageManager** - Concrete implementation for local file systems
3. **UnifiedCloudStorageManager** - Concrete implementation for cloud storage
4. **UnifiedStorageManagerFactory** - Factory for creating manager instances

### Class Hierarchy

```
UnifiedStorageManager (abstract)
├── UnifiedLocalStorageManager
└── UnifiedCloudStorageManager
    └── CloudReader (existing)
```

## Features

- **Unified Interface**: Single API for both local and cloud storage management
- **Multi-Cloud Support**: AWS S3, Google Cloud Storage, Azure Blob Storage, MinIO
- **Local File System**: Full support for local file operations
- **Factory Pattern**: Easy creation of managers with proper configuration
- **Thread-Safe**: Concurrent operations with proper synchronization
- **Performance Monitoring**: Built-in statistics and performance metrics
- **Error Handling**: Comprehensive error reporting and retry logic
- **Bucket Management**: Create, delete, and list cloud storage buckets
- **File Deletion**: Delete individual files and directories
- **Pattern Matching**: Delete files matching specific patterns

## Quick Start

### Basic Usage

```cpp
#include "unified_storage_manager_factory.h"

// Create a local storage manager
auto localManager = UnifiedStorageManagerFactory::createManager("local");
StorageConfig config;
config.storage_type = "local";
config.setParameter("base_path", "/data/storage");
localManager->configureStorage(config);

// Create a cloud storage manager
auto cloudManager = UnifiedStorageManagerFactory::createManager("cloud");
StorageConfig cloudConfig;
cloudConfig.storage_type = "cloud";
cloudConfig.setParameter("cloud_type", "minio");
cloudConfig.setParameter("endpoint", "http://minio.internal:9000");
cloudConfig.setParameter("access_key", "minio_user");
cloudConfig.setParameter("secret_key", "minio_password");
cloudConfig.setParameter("bucket_name", "vms-storage");
cloudManager->configureStorage(cloudConfig);
```

### File Operations

```cpp
// Delete a single file
DeleteResult result = manager->deleteFile("path/to/file.txt");
if (result.success) {
    std::cout << "File deleted successfully. Size: " << result.deletedSize << " bytes" << std::endl;
} else {
    std::cout << "Failed to delete file: " << result.message << std::endl;
}

// Delete a directory (recursive)
DeleteResult dirResult = manager->deleteDirectory("path/to/directory", true);
if (dirResult.success) {
    std::cout << "Directory deleted successfully. Size: " << dirResult.deletedSize << " bytes" << std::endl;
}

// Delete multiple files
std::vector<std::string> files = {"file1.txt", "file2.txt", "file3.txt"};
MultiDeleteResult multiResult = manager->deleteMultipleFiles(files);
std::cout << "Deleted " << multiResult.successful_deletes << " of " << multiResult.total_files << " files" << std::endl;

// Delete files matching a pattern
MultiDeleteResult patternResult = manager->deleteFilesInDirectory("logs", "*.log", true);
std::cout << "Deleted " << patternResult.successful_deletes << " log files" << std::endl;
```

### Bucket Operations (Cloud Storage)

```cpp
// Create a new bucket
BucketResult createResult = cloudManager->createBucket("new-bucket");
if (createResult.success) {
    std::cout << "Bucket created successfully: " << createResult.bucketName << std::endl;
}

// Check if bucket exists
BucketResult existsResult = cloudManager->checkBucketExists("my-bucket");
if (existsResult.success) {
    std::cout << "Bucket exists: " << existsResult.bucketName << std::endl;
}

// Delete a bucket (force delete if not empty)
BucketResult deleteResult = cloudManager->deleteBucket("old-bucket", true);
if (deleteResult.success) {
    std::cout << "Bucket deleted successfully: " << deleteResult.bucketName << std::endl;
}

// List all buckets
std::vector<BucketInfo> buckets = cloudManager->listBuckets();
for (const auto& bucket : buckets) {
    std::cout << "Bucket: " << bucket.name << " (Region: " << bucket.region << ")" << std::endl;
}
```

### Directory Operations (Local Storage)

```cpp
// Create a directory
bool created = localManager->createDirectory("new/directory", true);
if (created) {
    std::cout << "Directory created successfully" << std::endl;
}

// Check if directory exists
bool exists = localManager->directoryExists("path/to/directory");
if (exists) {
    std::cout << "Directory exists" << std::endl;
}
```

### Statistics and Monitoring

```cpp
// Get manager statistics
StorageStats stats = manager->getManagerStats();
std::cout << "Total operations: " << stats.totalRequests << std::endl;
std::cout << "Successful operations: " << stats.successfulRequests << std::endl;
std::cout << "Failed operations: " << stats.failedRequests << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// Reset statistics
manager->resetManagerStats();
```

### Health Check

```cpp
// Perform health check
bool healthy = manager->performHealthCheck();
if (healthy) {
    std::cout << "Storage manager is healthy" << std::endl;
} else {
    std::cout << "Health check failed: " << manager->getLastError() << std::endl;
}
```

## Configuration

### Local Storage Configuration

```cpp
StorageConfig config;
config.storage_type = "local";
config.setParameter("base_path", "/data/storage");
config.setParameter("create_directories", "true");
config.setParameter("recursive_listing", "false");
config.setParameter("max_depth", "10");
config.setParameter("include_hidden", "false");
```

### Cloud Storage Configuration

```cpp
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");  // or "s3", "gcs", "azure"
config.setParameter("bucket_name", "vms-storage");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("region", "us-east-1");
config.setParameter("use_ssl", "false");
config.setParameter("timeout_seconds", "30");
config.setParameter("max_retries", "3");
```

## Error Handling

The manager provides comprehensive error handling with detailed error codes and messages:

```cpp
DeleteResult result = manager->deleteFile("nonexistent.txt");
if (!result.success) {
    switch (result.errorCode) {
        case ErrorCodes::FILE_NOT_FOUND:
            std::cout << "File not found" << std::endl;
            break;
        case ErrorCodes::PERMISSION_DENIED:
            std::cout << "Permission denied" << std::endl;
            break;
        case ErrorCodes::NOT_INITIALIZED:
            std::cout << "Manager not initialized" << std::endl;
            break;
        default:
            std::cout << "Error: " << result.message << std::endl;
    }
}
```

## Thread Safety

All manager operations are thread-safe and can be used in multi-threaded environments:

```cpp
// Multiple threads can safely use the same manager instance
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

- **Local Storage**: Direct filesystem operations with minimal overhead
- **Cloud Storage**: Network operations with configurable timeouts and retries
- **Batch Operations**: Use `deleteMultipleFiles` for better performance when deleting multiple files
- **Pattern Matching**: Use specific patterns to avoid unnecessary file enumeration

## Future Enhancements

- **Asynchronous Operations**: Non-blocking delete operations
- **Progress Callbacks**: Real-time progress reporting for long operations
- **Advanced Filtering**: More sophisticated file filtering options
- **Batch Operations**: Optimized batch operations for cloud storage
- **Metrics Integration**: Prometheus/Grafana integration
- **Object Lifecycle Management**: Automatic object expiration and cleanup
- **Versioning Support**: Object versioning for cloud storage

## License

This library follows the same license as the parent VMS project.

## Contributing

1. Follow the existing code style and patterns
2. Add unit tests for new features
3. Update documentation for API changes
4. Ensure thread safety for all operations 
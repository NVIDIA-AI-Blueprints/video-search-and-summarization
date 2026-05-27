# Unified Storage Reader Library

A C++ abstraction library for unified storage read operations supporting both local file systems and cloud storage providers including AWS S3, Google Cloud Storage, Azure Blob Storage, and MinIO.

## Overview

The Unified Storage Reader Library provides a consistent interface for reading files from different storage backends. It follows the same architectural patterns as the Unified Storage Writer, ensuring consistency across the storage framework.

### Key Features

- **Unified Interface**: Single API for both local and cloud storage
- **Multi-Cloud Support**: AWS S3, Google Cloud Storage, Azure Blob Storage, MinIO
- **Local File System**: Full support for local file operations
- **Factory Pattern**: Easy creation of readers with proper configuration
- **Thread-Safe**: Concurrent operations with proper synchronization
- **Performance Monitoring**: Built-in statistics and performance metrics
- **Error Handling**: Comprehensive error reporting and retry logic
- **Pre-signed URLs**: Generate time-limited access URLs for cloud objects

## Architecture

### Core Components

1. **UnifiedStorageReader** - Abstract base class that defines the unified interface
2. **UnifiedLocalStorageReader** - Concrete implementation for local file systems
3. **UnifiedCloudStorageReader** - Concrete implementation for cloud storage
4. **UnifiedStorageReaderFactory** - Factory for creating reader instances
5. **CloudReader** - Existing cloud reader interface (direct usage)

### Class Hierarchy

```
UnifiedStorageReader (abstract)
├── UnifiedLocalStorageReader
└── UnifiedCloudStorageReader
    └── CloudReader (existing)
```

## Features

- **Unified Interface**: Single API for both local and cloud storage
- **Multi-Cloud Support**: AWS S3, Google Cloud Storage, Azure Blob Storage, MinIO
- **Local File System**: Full support for local file operations
- **AWS Signature V4 Authentication**: Secure authentication for private cloud resources
- **Factory Pattern**: Easy creation of readers with proper configuration
- **Thread-Safe**: Concurrent operations with proper synchronization
- **Performance Monitoring**: Built-in statistics and performance metrics
- **Error Handling**: Comprehensive error reporting and retry logic
- **Pre-signed URLs**: Generate time-limited access URLs for cloud objects

## Quick Start

### Building the Library

The Cloud Reader Library is integrated with the main VMS build system:

```bash
# Build as part of the main project
cd $(TOP)
make arch=x86_64

# Or build just the cloud reader library
cd src/framework/cloud_storage/reader
make TOP=/path/to/vms_shim arch=x86_64

# Clean build artifacts
make clean
```

The library will be built as `libnvstoragereader.so` in the prebuilts directory.

### Basic Usage

```cpp
#include <cloud_reader_factory.h>
#include <iostream>

using namespace nv_vms;

int main() {
    // Create S3 cloud reader configuration
    CloudReaderConfig config;
    config.storageType = CloudStorageType::AWS_S3;
    config.accessKeyId = "your-access-key";
    config.secretAccessKey = "your-secret-key";
    config.region = "us-west-1";
    
    // Create S3 cloud reader
    auto reader = CloudReaderFactory::createReader("s3", config);
    if (!reader || !reader->isAvailable()) {
        std::cerr << "Failed to create S3 reader" << std::endl;
        return 1;
    }
    
    // List objects in bucket
    auto result = reader->listObjects("my-bucket", "my-prefix/");
    if (result.success) {
        std::cout << "Found " << result.count << " objects:" << std::endl;
        for (const auto& object : result.objects) {
            std::cout << "  " << object.key << " (" << object.size << " bytes)" << std::endl;
        }
    } else {
        std::cerr << "List failed: " << result.message << std::endl;
    }
    
    // Download an object
    auto downloadResult = reader->downloadObject("my-bucket", "path/to/file.txt", "/local/path/file.txt");
    if (downloadResult.success) {
        std::cout << "Download successful" << std::endl;
    } else {
        std::cerr << "Download failed: " << downloadResult.message << std::endl;
    }
    
    return 0;
}
```

## API Reference

### CloudReaderFactory

Factory class for creating cloud readers.

```cpp
// Create reader with configuration
auto reader = CloudReaderFactory::createReader("s3", config);

// Create reader with default configuration
auto reader = CloudReaderFactory::createReader("s3");

// Check supported types
auto types = CloudReaderFactory::getSupportedTypes();

// Validate configuration
bool valid = CloudReaderFactory::validateConfig("s3", config);
```

### CloudReader Interface

Base interface for all cloud readers.

#### Object Listing

```cpp
// List all objects with prefix
CloudListResult result = reader->listObjects("bucket", "prefix/");

// Paginated listing
CloudListResult result = reader->listObjectsPaginated("bucket", "prefix/", "marker", 100);
```

#### Object Operations

```cpp
// Download object to local file
CloudResult result = reader->downloadObject("bucket", "object-key", "/local/path");

// Get object information
CloudObject objectInfo;
CloudResult result = reader->getObjectInfo("bucket", "object-key", objectInfo);

// Check if object exists
CloudResult result = reader->checkObjectExists("bucket", "object-key");
```

#### Bucket Operations

```cpp
// List accessible buckets
std::vector<std::string> buckets;
CloudResult result = reader->listBuckets(buckets);

// Check if bucket exists
CloudResult result = reader->checkBucketExists("bucket-name");
```

#### URL Generation

```cpp
// Generate pre-signed URL (valid for 1 hour)
std::string url;
CloudResult result = reader->generatePresignedUrl("bucket", "object-key", 3600, url);
```

### Configuration

#### AWS S3 Configuration

```cpp
CloudReaderConfig config;
config.storageType = CloudStorageType::AWS_S3;
config.accessKeyId = "AKIAIOSFODNN7EXAMPLE";
config.secretAccessKey = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY";
config.region = "us-west-1";
config.useSSL = true;
config.timeoutSeconds = 30;
config.maxRetries = 3;

// Request-specific settings
config.request.maxKeys = 1000;
config.request.fetchMetadata = false;
config.request.enableCache = true;
config.request.cacheTimeoutSec = 300;
```

#### MinIO Configuration

```cpp
CloudReaderConfig config;
config.storageType = CloudStorageType::MINIO;
config.accessKeyId = "minioadmin";
config.secretAccessKey = "minioadmin";
config.endpoint = "http://localhost:9000";
config.useSSL = false;
```

### Error Handling

All operations return result objects with success status and error information:

```cpp
CloudListResult result = reader->listObjects("bucket");
if (!result.success) {
    std::cerr << "Error: " << result.message << std::endl;
    std::cerr << "Error Code: " << result.errorCode << std::endl;
}
```

### Statistics and Monitoring

```cpp
// Get performance statistics
CloudReaderStats stats = reader->getStats();
std::cout << "Total requests: " << stats.totalRequests << std::endl;
std::cout << "Success rate: " << (stats.successfulRequests * 100.0 / stats.totalRequests) << "%" << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// Reset statistics
reader->resetStats();
```

## Unified Storage Reader Interface

The Unified Storage Reader provides a consistent API for both local and cloud storage operations.

### UnifiedStorageReaderFactory

Factory class for creating unified storage readers.

```cpp
// Create reader by type
auto localReader = UnifiedStorageReaderFactory::createReader("local");
auto cloudReader = UnifiedStorageReaderFactory::createReader("cloud");

// Create reader with configuration
StorageConfig config;
config.storage_type = "local";
config.setParameter("base_path", "/data/storage");
auto reader = UnifiedStorageReaderFactory::createReader("local", config);

// Check supported types
auto types = UnifiedStorageReaderFactory::getSupportedTypes();

// Validate configuration
bool valid = UnifiedStorageReaderFactory::validateConfig("local", config);
```

### UnifiedStorageReader Interface

Base interface for all unified storage readers.

#### File Operations

```cpp
// Download a file
FileResult result = reader->downloadFile("remote/path/file.txt", "/local/path/file.txt");

// Get file information
FileInfo fileInfo;
result = reader->getFileInfo("path/to/file.txt", fileInfo);

// Check if file exists
result = reader->checkFileExists("path/to/file.txt");
```

#### Directory Operations

```cpp
// List files in a directory
FileListResult listResult = reader->listFiles("/data/storage", "*.txt");

// Paginated listing
FileListResult paginatedResult = reader->listFilesPaginated("/data/storage", "", "", 100);
```

#### Cloud-Specific Operations

```cpp
// Generate pre-signed URL (cloud only)
std::string presignedUrl;
FileResult urlResult = cloudReader->generatePresignedUrl("path/to/file.txt", 3600, presignedUrl);

// List buckets (cloud only)
std::vector<std::string> buckets;
FileResult bucketResult = cloudReader->listBuckets(buckets);
```

#### Statistics and Monitoring

```cpp
// Get statistics
ReaderStats stats = reader->getReaderStats();
std::cout << "Total requests: " << stats.totalRequests << std::endl;
std::cout << "Bytes read: " << stats.bytesRead << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// Reset statistics
reader->resetStats();

// Health check
FileResult healthResult = reader->performHealthCheck();
```

## Integration with Storage Management

The Cloud Reader Library is already integrated with the Storage Management module:

```cpp
// In storage_management.cpp
#include "cloud_reader_factory.h"

VmsErrorCode StorageManagement::listS3Objects(const string& bucketName, const string& prefix,
                                              const string& region, const string& accessKeyId, 
                                              const string& secretAccessKey, Json::Value& response) {
    // Configure cloud reader
    nv_vms::CloudReaderConfig config;
    config.storageType = nv_vms::CloudStorageType::AWS_S3;
    config.accessKeyId = accessKeyId;
    config.secretAccessKey = secretAccessKey;
    config.region = region;
    config.useSSL = true;
    config.timeoutSeconds = 30;
    config.maxRetries = 3;
    
    // Create S3 cloud reader
    auto reader = nv_vms::CloudReaderFactory::createReader(config.storageType, config);
    if (!reader || !reader->isAvailable()) {
        LOG(error) << "Failed to create or initialize S3 cloud reader" << endl;
        response = Json::nullValue;
        return VmsErrorCode::VMSInternalError;
    }
    
    // List objects using cloud reader
    nv_vms::CloudListResult result = reader->listObjects(bucketName, prefix, 1000);
    if (result.success) {
        // Convert cloud reader result to JSON response
        response = reader->listResultToJson(result);
        LOG(info) << "Successfully listed " << result.count << " objects from S3 via Cloud Reader" << endl;
        return VmsErrorCode::NoError;
    }
    
    // Fallback to Python if needed
    LOG(warning) << "Cloud Reader method failed: " << result.message << ", trying Python fallback" << endl;
    // ... fallback implementation
    return VmsErrorCode::VMSInternalError;
}
```

### API Endpoints Using Cloud Reader

- **GET `/api/v1/storage/file/list`**: Lists S3 objects using the Cloud Reader Library
- **POST `/api/v1/storage/file/import`**: Downloads S3 objects using the Cloud Reader Library

## Dependencies

- **OpenSSL**: For HMAC-SHA256 authentication
- **libcurl**: For HTTP/HTTPS requests
- **JsonCpp**: For JSON handling
- **C++17**: Modern C++ features

## Building Requirements

```bash
# Ubuntu/Debian
sudo apt-get install build-essential libssl-dev libcurl4-openssl-dev libjsoncpp-dev

# CentOS/RHEL
sudo yum install gcc-c++ openssl-devel libcurl-devel jsoncpp-devel

# Or using package manager of your choice
```

## License

This library follows the same license as the parent VMS project.

## Contributing

1. Follow the existing code style and patterns
2. Add unit tests for new features
3. Update documentation for API changes
4. Ensure thread safety for all operations

## Migration Guide

### From Cloud Reader to Unified Reader

The unified reader interface provides backward compatibility while offering a more consistent API:

```cpp
// Old way (Cloud Reader)
auto cloudReader = CloudReaderFactory::createReader("s3", config);
CloudResult result = cloudReader->downloadObject("bucket", "key", "local_path");

// New way (Unified Reader)
auto reader = UnifiedStorageReaderFactory::createReader("cloud", config);
FileResult result = reader->downloadFile("bucket/key", "local_path");
```

### Benefits of Unified Reader

1. **Consistent API**: Same interface for local and cloud storage
2. **Better Error Handling**: Standardized error reporting
3. **Enhanced Statistics**: Comprehensive performance metrics
4. **Future-Proof**: Extensible architecture for new storage types
5. **Thread Safety**: Improved concurrency support

## Future Enhancements

- **Streaming Support**: Direct streaming of file content
- **Compression**: Built-in compression/decompression
- **Encryption**: File encryption/decryption support
- **Advanced Caching**: Multi-level caching strategies
- **Connection Pooling**: Optimized connection management
- **Asynchronous Operations**: Non-blocking file operations
- **Metrics Integration**: Prometheus/Grafana integration
- **Google Cloud Storage**: Native GCS implementation
- **Azure Blob Storage**: Native Azure implementation 
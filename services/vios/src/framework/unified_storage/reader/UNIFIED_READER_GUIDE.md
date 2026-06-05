# Unified Storage Reader Interface

## Overview

The Unified Storage Reader Interface provides a consistent API for reading files from both local file systems and cloud storage services (S3, MinIO, GCS, Azure). It follows the same architectural patterns as the Unified Storage Writer, ensuring consistency across the storage framework.

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

## Key Features

### Unified Interface
- **File Operations**: downloadFile, getFileInfo, checkFileExists
- **Directory Operations**: listFiles, listFilesPaginated
- **Cloud-Specific**: generatePresignedUrl, listBuckets, checkBucketExists
- **Statistics**: getReaderStats, resetStats
- **Health Check**: performHealthCheck

### Storage Types
- **LOCAL**: Local file system operations
- **CLOUD**: Cloud storage operations (S3, MinIO, GCS, Azure)

### Configuration
- **StorageConfig**: Common configuration structure
- **LocalReaderConfig**: Local storage specific configuration
- **CloudReaderConfig**: Cloud storage specific configuration

## Usage Examples

### Basic Usage

```cpp
#include "unified_storage_reader_factory.h"

// Create a local storage reader
auto localReader = UnifiedStorageReaderFactory::createReader("local");
StorageConfig config;
config.storage_type = "local";
config.setParameter("base_path", "/data/storage");
localReader->configureStorage(config);

// Create a cloud storage reader
auto cloudReader = UnifiedStorageReaderFactory::createReader("cloud");
StorageConfig cloudConfig;
cloudConfig.storage_type = "cloud";
cloudConfig.setParameter("endpoint", "s3.amazonaws.com");
cloudConfig.setParameter("access_key", "your_access_key");
cloudConfig.setParameter("secret_key", "your_secret_key");
cloudConfig.setParameter("bucket", "my-bucket");
cloudReader->configureStorage(cloudConfig);
```

### File Operations

```cpp
// Download a file
FileResult result = reader->downloadFile("remote/path/file.txt", "/local/path/file.txt");
if (result.success) {
    std::cout << "File downloaded successfully" << std::endl;
} else {
    std::cout << "Download failed: " << result.message << std::endl;
}

// Get file information
FileInfo fileInfo;
result = reader->getFileInfo("path/to/file.txt", fileInfo);
if (result.success) {
    std::cout << "File size: " << fileInfo.size << " bytes" << std::endl;
    std::cout << "Last modified: " << fileInfo.lastModified << std::endl;
}

// Check if file exists
result = reader->checkFileExists("path/to/file.txt");
if (result.success) {
    std::cout << "File exists" << std::endl;
}
```

### Directory Operations

```cpp
// List files in a directory
FileListResult listResult = reader->listFiles("/data/storage", "*.txt");
if (listResult.success) {
    std::cout << "Found " << listResult.count << " files" << std::endl;
    for (const auto& file : listResult.files) {
        std::cout << "File: " << file.name << " (" << file.size << " bytes)" << std::endl;
    }
}

// Paginated listing
FileListResult paginatedResult = reader->listFilesPaginated("/data/storage", "", "", 100);
if (paginatedResult.success) {
    std::cout << "Total files: " << paginatedResult.count << std::endl;
    if (paginatedResult.isTruncated) {
        std::cout << "More files available. Next marker: " << paginatedResult.nextMarker << std::endl;
    }
}
```

### Cloud-Specific Operations

```cpp
// Generate pre-signed URL (cloud only)
std::string presignedUrl;
FileResult urlResult = cloudReader->generatePresignedUrl("path/to/file.txt", 3600, presignedUrl);
if (urlResult.success) {
    std::cout << "Pre-signed URL: " << presignedUrl << std::endl;
}

// List buckets (cloud only)
std::vector<std::string> buckets;
FileResult bucketResult = cloudReader->listBuckets(buckets);
if (bucketResult.success) {
    for (const auto& bucket : buckets) {
        std::cout << "Bucket: " << bucket << std::endl;
    }
}
```

### Statistics and Monitoring

```cpp
// Get statistics
ReaderStats stats = reader->getReaderStats();
std::cout << "Total requests: " << stats.totalRequests << std::endl;
std::cout << "Successful requests: " << stats.successfulRequests << std::endl;
std::cout << "Failed requests: " << stats.failedRequests << std::endl;
std::cout << "Bytes read: " << stats.bytesRead << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// Reset statistics
reader->resetStats();

// Health check
FileResult healthResult = reader->performHealthCheck();
if (healthResult.success) {
    std::cout << "Storage system is healthy" << std::endl;
} else {
    std::cout << "Health check failed: " << healthResult.message << std::endl;
}
```

## Configuration

### Local Storage Configuration

```cpp
LocalReaderConfig localConfig;
localConfig.basePath = "/data/storage";
localConfig.recursiveListing = true;
localConfig.maxDepth = 5;
localConfig.includeHidden = false;
localConfig.timeoutSeconds = 30;

// File filtering
localConfig.filter.includeExtensions = {".txt", ".log", ".csv"};
localConfig.filter.excludeExtensions = {".tmp", ".bak"};
localConfig.filter.minFileSize = 1024;  // 1KB
localConfig.filter.maxFileSize = 100 * 1024 * 1024;  // 100MB

// Performance settings
localConfig.performance.bufferSize = 8192;
localConfig.performance.enableCaching = true;
localConfig.performance.cacheTimeoutSec = 300;
localConfig.performance.maxConcurrentReads = 4;
```

### Cloud Storage Configuration

```cpp
CloudReaderConfig cloudConfig;
cloudConfig.storageType = CloudStorageType::AWS_S3;
cloudConfig.endpoint = "s3.amazonaws.com";
cloudConfig.region = "us-west-2";
cloudConfig.accessKeyId = "your_access_key";
cloudConfig.secretAccessKey = "your_secret_key";
cloudConfig.useSSL = true;
cloudConfig.timeoutSeconds = 30;
cloudConfig.maxRetries = 3;

// Authentication settings
cloudConfig.auth.useDefaultCredentials = false;
cloudConfig.auth.useInstanceProfile = false;
cloudConfig.auth.credentialsFile = "/path/to/credentials";
cloudConfig.auth.profileName = "default";

// Request settings
cloudConfig.request.maxKeys = 1000;
cloudConfig.request.fetchMetadata = true;
cloudConfig.request.enableCache = true;
cloudConfig.request.cacheTimeoutSec = 300;
```

## Error Handling

The interface provides comprehensive error handling through the `FileResult` structure:

```cpp
FileResult result = reader->downloadFile("source.txt", "destination.txt");
if (!result.success) {
    std::cout << "Operation failed: " << result.message << std::endl;
    if (!result.errorCode.empty()) {
        std::cout << "Error code: " << result.errorCode << std::endl;
    }
    std::cout << "Duration: " << result.duration.count() << "ms" << std::endl;
}
```

## Performance Considerations

### Caching
- Local storage reader includes file info caching
- Cloud storage reader supports response caching
- Configurable cache timeouts

### Buffering
- Configurable read buffer sizes
- Support for chunked file reading
- Optimized for large file operations

### Concurrency
- Thread-safe implementations
- Support for concurrent operations
- Configurable concurrency limits

## Integration with Existing Code

The unified reader interface is designed to work seamlessly with the existing cloud reader infrastructure:

```cpp
// Access underlying cloud reader if needed
auto cloudReader = std::dynamic_pointer_cast<UnifiedCloudStorageReader>(reader);
if (cloudReader) {
    auto underlyingReader = cloudReader->getCloudReader();
    // Use underlying reader for advanced operations
}
```

## Testing

The interface includes comprehensive testing support:

```cpp
// Unit test support
#ifdef UNIFIED_STORAGE_READER_UNIT_TEST
// Test-specific methods and configurations
#endif
```

## Future Enhancements

1. **Streaming Support**: Direct streaming of file content
2. **Compression**: Built-in compression/decompression
3. **Encryption**: File encryption/decryption support
4. **Caching**: Advanced caching strategies
5. **Monitoring**: Enhanced monitoring and alerting
6. **Performance**: Optimizations for specific use cases

## Migration Guide

### From Direct Cloud Reader Usage

```cpp
// Old way
auto cloudReader = CloudReaderFactory::createReader("s3", config);
CloudResult result = cloudReader->downloadObject("bucket", "key", "local_path");

// New way
auto reader = UnifiedStorageReaderFactory::createReader("cloud", config);
FileResult result = reader->downloadFile("bucket/key", "local_path");
```

### From Local File Operations

```cpp
// Old way
std::ifstream file("path/to/file.txt");
// Manual file operations...

// New way
auto reader = UnifiedStorageReaderFactory::createReader("local", config);
FileResult result = reader->downloadFile("path/to/file.txt", "destination.txt");
```

This unified interface provides a consistent, robust, and extensible solution for file reading operations across different storage backends. 
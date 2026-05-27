# Unified Storage Framework

A comprehensive C++ framework for unified storage operations supporting both local file systems and cloud storage providers including AWS S3, Google Cloud Storage, Azure Blob Storage, and MinIO.

## Overview

The Unified Storage Framework provides a consistent interface for reading, writing, and managing files across different storage backends. It consists of three main components:

- **Reader**: For reading files from local and cloud storage
- **Writer**: For writing files to local and cloud storage with GStreamer integration
- **Manager**: For administrative operations like deletion, bucket management, etc.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Unified Storage Framework                    │
├─────────────────┬─────────────────┬─────────────────────────────┤
│     READER      │     WRITER      │          MANAGER            │
│                 │                 │                             │
│ • Local Reader  │ • Local Writer  │ • Local Manager             │
│ • Cloud Reader  │ • Cloud Writer  │ • Cloud Manager             │
│ • S3/MinIO      │ • GStreamer     │ • Bucket Management         │
│ • File Listing  │ • Pipeline      │ • File Deletion             │
│ • Downloads     │ • Buffering     │ • Pattern Matching          │
└─────────────────┴─────────────────┴─────────────────────────────┘
```

## Key Features

### 🔄 Unified Interface
- Single API for both local and cloud storage operations
- Consistent error handling and result structures
- Thread-safe operations with proper synchronization

### ☁️ Multi-Cloud Support
- **AWS S3**: Full S3 compatibility with Signature V4 authentication
- **MinIO**: S3-compatible object storage
- **Google Cloud Storage**: GCS integration
- **Azure Blob Storage**: Azure integration

### 📁 Local File System
- Direct filesystem operations with minimal overhead
- Directory management and file operations
- Path validation and security checks

### 🎬 GStreamer Integration (Writer)
- Unified GStreamer pipeline for video/audio encoding
- Real-time streaming to both local and cloud storage
- Buffering system for cloud storage with rate limiting
- Pipeline reuse optimization for efficient file changes

### 📊 Performance & Monitoring
- Built-in statistics and performance metrics
- Timing measurements for all operations
- Health checks and diagnostics
- Configurable timeouts and retry logic

## Quick Start

### 1. Reader Operations

```cpp
#include "unified_storage_reader_factory.h"

// Create a reader
auto reader = UnifiedStorageReaderFactory::createReader("cloud");

// Configure for MinIO
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-storage");
reader->configureStorage(config);

// List files
auto result = reader->listFiles("camera1/2024/01/15/", true);
if (result.success) {
    for (const auto& file : result.files) {
        std::cout << "File: " << file.path << " (" << file.size << " bytes)" << std::endl;
    }
}

// Download file
auto downloadResult = reader->downloadFile("camera1/video.mkv", "/tmp/local_video.mkv");
if (downloadResult.success) {
    std::cout << "Downloaded " << downloadResult.bytes_downloaded << " bytes" << std::endl;
}
```

### 2. Writer Operations

```cpp
#include "unified_storage_writer_factory.h"

// Create a writer
auto writer = UnifiedStorageWriterFactory::createWriter("cloud");

// Configure storage
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

// Start recording session
std::string session_id = writer->startWrite(
    "camera1/2024/01/15/10/1234567890.mkv",
    1920, 1080, 30, "h264", false
);

// Write frames (in your GStreamer pipeline)
// The writer handles the rest automatically

// Stop recording
writer->stopWrite(session_id);
```

### 3. Manager Operations

```cpp
#include "unified_storage_manager_factory.h"

// Create a manager
auto manager = UnifiedStorageManagerFactory::createManager("cloud");

// Configure storage
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-storage");
manager->configureStorage(config);

// Delete files
DeleteResult result = manager->deleteFile("camera1/old_video.mkv");
if (result.success) {
    std::cout << "Deleted " << result.deletedSize << " bytes" << std::endl;
    std::cout << "Operation took " << result.duration.count() << "ms" << std::endl;
}

// Delete multiple files
std::vector<std::string> files = {"file1.mkv", "file2.mkv", "file3.mkv"};
MultiDeleteResult multiResult = manager->deleteMultipleFiles(files);
std::cout << "Deleted " << multiResult.successful_deletes << " of " 
          << multiResult.total_files << " files" << std::endl;
```

## Configuration

### Common Configuration Parameters

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| `storage_type` | "local" or "cloud" | - | Yes |
| `cloud_type` | "minio", "s3", "gcs", "azure" | - | For cloud |
| `endpoint` | Cloud storage endpoint | - | For cloud |
| `access_key` | Access key for authentication | - | For cloud |
| `secret_key` | Secret key for authentication | - | For cloud |
| `bucket_name` | Cloud storage bucket | - | For cloud |
| `region` | Cloud storage region | "us-east-1" | For cloud |
| `use_ssl` | Use SSL/TLS | "true" | For cloud |
| `timeout_seconds` | Operation timeout | "30" | No |
| `max_retries` | Maximum retry attempts | "3" | No |

### Writer-Specific Configuration

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| `video_codec` | "h264" or "h265" | "h264" | No |
| `audio_supported` | Enable audio support | "false" | No |
| `buffer_size_mb` | Cloud buffer size | "100" | No |
| `max_upload_fps` | Max upload rate | "30" | No |
| `auto_adapt_rate` | Adaptive rate control | "true" | No |

## Error Handling

All operations return structured results with detailed error information:

```cpp
// Example error handling
auto result = manager->deleteFile("nonexistent.mkv");
if (!result.success) {
    std::cout << "Error: " << result.message << std::endl;
    std::cout << "Error Code: " << result.errorCode << std::endl;
    std::cout << "Duration: " << result.duration.count() << "ms" << std::endl;
}
```

### Common Error Codes

| Error Code | Description |
|------------|-------------|
| `FILE_NOT_FOUND` | File or object does not exist |
| `PERMISSION_DENIED` | Insufficient permissions |
| `NOT_INITIALIZED` | Manager/Reader/Writer not initialized |
| `INVALID_CONFIGURATION` | Invalid configuration parameters |
| `NETWORK_ERROR` | Network connectivity issues |
| `TIMEOUT` | Operation timed out |
| `EXCEPTION` | Unexpected exception occurred |

## Performance Considerations

### Local Storage
- Direct filesystem operations with minimal overhead
- No network latency or bandwidth limitations
- Optimal for high-frequency operations

### Cloud Storage
- Network operations with configurable timeouts
- Buffering system for efficient uploads
- Rate limiting to prevent overwhelming the storage
- Retry logic for transient failures

### Best Practices
1. **Batch Operations**: Use batch operations when deleting multiple files
2. **Pattern Matching**: Use specific patterns to avoid unnecessary enumeration
3. **Connection Pooling**: Reuse reader/writer instances when possible
4. **Monitoring**: Monitor statistics for performance optimization

## Monitoring and Statistics

All components provide comprehensive statistics:

```cpp
// Get statistics
StorageStats stats = manager->getManagerStats();
std::cout << "Total operations: " << stats.totalRequests << std::endl;
std::cout << "Success rate: " << (stats.successfulRequests * 100.0 / stats.totalRequests) << "%" << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;

// For writers, additional buffering stats
std::cout << "Buffer utilization: " << stats.buffering.buffer_utilization_percent << "%" << std::endl;
std::cout << "Upload rate: " << stats.buffering.upload_rate_fps << " fps" << std::endl;

// Enhanced error tracking
for (const auto& error : stats.errorCounts) {
    std::cout << "Error " << error.first << " occurred " << error.second << " times" << std::endl;
}
```

## Recent Updates

### Latest Implementation Features (2025-01-24)

#### 🚀 Major Enhancements
1. **Accurate Timing Measurements**: Fixed duration reporting showing 0ms - all operations now include precise timing information
2. **Bytes Deleted Reporting**: File deletion operations return actual bytes deleted from storage
3. **Enhanced Error Handling**: Comprehensive error codes, messages, and tracking for better debugging
4. **Thread Safety Improvements**: Enhanced mutex protection and concurrent operation safety
5. **Performance Optimizations**: Improved buffering, upload mechanisms, and memory management

#### 🔧 Technical Improvements
- **Fixed Timing Issues**: Duration now properly assigned to all result structures
- **Enhanced API Responses**: Both MB and bytes returned for better precision
- **Improved Error Tracking**: Detailed error statistics and monitoring
- **Better Resource Management**: Proper cleanup and memory management

### API Response Enhancements

File deletion APIs now return both MB and bytes for better precision:
```json
{
  "spaceSaved": 1024,        // Size in MB
  "bytesDeleted": 1073741824 // Size in bytes
}
```

### Performance Improvements

#### Before vs After
- **Timing**: Inconsistent or missing timing information → Accurate timing for all operations
- **Error Handling**: Basic error reporting → Detailed error codes and comprehensive tracking  
- **Thread Safety**: Limited thread safety → Comprehensive mutex protection
- **Bytes Reporting**: Only MB reported → Both MB and bytes for precision

### 🐛 Recent Bug Fixes

#### Timing Issues Resolved
- **Issue**: Duration showing 0ms in operation results
- **Root Cause**: Duration not assigned to result structures
- **Solution**: Proper duration assignment in all code paths
- **Impact**: All operations now report accurate timing information

#### Memory Management Improvements
- **Fixed**: Potential memory leaks in GStreamer pipeline management
- **Improved**: Proper cleanup in destructors
- **Enhanced**: Resource management for cloud storage operations

#### Error Handling Enhancements
- **Fixed**: Inconsistent error code reporting
- **Improved**: Exception handling in all operations
- **Enhanced**: Error message clarity and detail

### 🔗 VMS Integration Improvements

#### Storage Management Integration
- **Enhanced**: `deleteMediaFile()` function now returns actual bytes deleted
- **Improved**: API responses include both `spaceSaved` (MB) and `bytesDeleted` (bytes)
- **Updated**: `deleteFilesByTime` and `deleteFilesByNames` APIs return precise byte counts
- **Fixed**: Timing measurements in unified storage manager operations

#### API Response Format
```json
// Before
{
  "spaceSaved": 1024
}

// After  
{
  "spaceSaved": 1024,
  "bytesDeleted": 1073741824
}
```

## Building

The unified storage framework is integrated with the main VMS build system:

```bash
# Build the entire VMS with unified storage
cd $(TOP)
make arch=x86_64

# Build specific components
cd src/framework/unified_storage/reader
make TOP=/path/to/vms_shim arch=x86_64

cd src/framework/unified_storage/writer
make TOP=/path/to/vms_shim arch=x86_64

cd src/framework/unified_storage/manager
make TOP=/path/to/vms_shim arch=x86_64
```

## Testing

Each component includes comprehensive tests:

```bash
# Run tests for each component
cd src/framework/unified_storage/reader
make test

cd src/framework/unified_storage/writer
make test

cd src/framework/unified_storage/manager
make test
```

## Debugging and Troubleshooting

### Enhanced Logging
```cpp
// Enable verbose logging for detailed debugging
LOG(verbose) << "Operation details: " << details << std::endl;
LOG(info) << "Operation completed: " << result.success << std::endl;
LOG(error) << "Operation failed: " << result.message << std::endl;
```

### Performance Monitoring
```cpp
// Monitor operation timing and success rates
StorageStats stats = manager->getManagerStats();
LOG(info) << "Success rate: " << (stats.successfulRequests * 100.0 / stats.totalRequests) << "%" << std::endl;
LOG(info) << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;
```

### Common Issues and Solutions

#### Timing Shows 0ms
- **Cause**: Duration not assigned to result structure
- **Solution**: Ensure `result.duration = duration;` is called in all code paths

#### Permission Denied Errors
- **Check**: Access key and secret key configuration
- **Verify**: Bucket permissions and file/directory access rights
- **Test**: Connection to storage endpoint

#### Network Errors
- **Check**: Endpoint URL and network connectivity
- **Verify**: Firewall settings and SSL configuration
- **Test**: Basic connectivity to storage service

## Contributing

1. Follow the existing code style and patterns
2. Add unit tests for new features
3. Update documentation for API changes
4. Ensure thread safety for all operations
5. Include performance measurements
6. Add timing measurements to all operations
7. Include comprehensive error handling

## License

This framework follows the same license as the parent VMS project (BSD-3-Clause).

## Development Status

### ✅ Production Ready Features
- **Local Storage**: Fully implemented and production-ready
- **MinIO Cloud Storage**: Complete implementation with buffering
- **File Deletion**: Accurate timing and bytes reporting
- **Error Handling**: Comprehensive error tracking and reporting
- **Thread Safety**: Full thread safety across all components
- **Performance Monitoring**: Detailed statistics and metrics

### 🔄 In Progress
- **AWS S3**: Enhanced integration and optimization
- **Google Cloud Storage**: Advanced features and performance tuning
- **Azure Blob Storage**: Complete implementation

### 🚀 Planned Features
- **Asynchronous Operations**: Non-blocking operations with progress callbacks
- **Advanced Monitoring**: Prometheus/Grafana integration
- **Object Lifecycle Management**: Automatic expiration and cleanup
- **Versioning Support**: Object versioning for cloud storage

## Support

For issues and questions:
1. Check the component-specific documentation in each subdirectory
2. Review the error handling guide for common issues
3. Check the performance monitoring for optimization opportunities
4. Contact the development team for specific issues
5. Review the debugging and troubleshooting section above 
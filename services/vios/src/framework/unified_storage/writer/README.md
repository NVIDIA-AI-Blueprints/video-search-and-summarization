# Unified Storage Writer System for VMS

This directory contains a comprehensive unified storage system that allows the VMS to write recordings to local file systems and cloud storage providers through a unified GStreamer pipeline interface.

## Overview

The unified storage system provides a single interface for both local and cloud storage using the same GStreamer pipeline structure, with only the sink element being different:

- **Local Storage**: `Raw H.264/H.265 Frame → appsrc → h264parse/h265parse → matroskamux → filesink → MKV File`
- **Cloud Storage**: `Raw H.264/H.265 Frame → appsrc → h264parse/h265parse → matroskamux → appsink → Cloud Storage`

## Architecture

```
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────┐
│   GstMux        │───▶│ UnifiedStorageWriter│───▶│ Storage Writers │
│   Pipeline      │    │   (Base Class)      │    │                 │
└─────────────────┘    └─────────────────────┘    └─────────────────┘
                                │                           │
                                ▼                           ▼
                    ┌─────────────────────┐    ┌─────────────────────┐
                    │ Pipeline Elements   │    │ Local/Cloud Writers │
                    │                     │    │                     │
                    │ • appsrc            │    │ • UnifiedLocal      │
                    │ • h264parse/h265parse│    │ • UnifiedCloud      │
                    │ • matroskamux       │    │ • MinIO/S3          │
                    │ • sink (filesink/   │    │ • CloudStorageBuffer│
                    │   appsink)          │    │                     │
                    └─────────────────────┘    └─────────────────────┘
```

## Features

- ✅ **Unified GStreamer Pipeline** (fully implemented)
- ✅ **Local file system storage** (fully implemented and production-ready)
- ✅ **MinIO cloud storage** (buffering system implemented, upload in progress)
- 🔄 **AWS S3 storage** (stub implementation - requires AWS SDK)
- ✅ **Streaming uploads** (real-time data writing)
- ✅ **Pipeline reuse optimization** (efficient file location changes)
- ✅ **Session management** (unique session IDs and state tracking)
- ✅ **Error handling and recovery** (comprehensive error management)
- ✅ **Test mode** (single-failure simulation for testing)
- ✅ **Thread safety** (proper mutex protection)
- ✅ **Audio support** (PCMU, PCMA, AAC codecs)

## Core Components

### 1. UnifiedStorageWriter (Base Class)
- **Status**: ✅ Fully implemented
- **Features**:
  - Common GStreamer pipeline management
  - Buffer handling and session management
  - Error handling and recovery
  - Test mode for failure simulation
  - Thread-safe operations

### 2. UnifiedLocalStorageWriter
- **Status**: ✅ Fully implemented and production-ready
- **Features**:
  - Direct file writing with filesink
  - Optimized pipeline reuse for file changes
  - Directory creation and permission verification
  - No buffering overhead

### 3. UnifiedCloudStorageWriter
- **Status**: 🔄 Partially implemented (buffering mode active)
- **Features**:
  - Buffered uploads with CloudStorageBuffer
  - Rate limiting and adaptive upload control
  - MinIO and S3 backend support
  - Background upload processing

### 4. CloudStorageBuffer
- **Status**: ✅ Fully implemented
- **Features**:
  - Configurable buffer sizes
  - Upload rate limiting
  - Background upload processing
  - Error recovery and retry logic

## Usage Examples

### 1. Local Storage

```cpp
#include "unified_storage_writer_factory.h"

// Create local storage writer
auto writer = UnifiedStorageWriterFactory::createWriter("local");

// Configure storage
StorageConfig config;
config.setParameter("storage_type", "local");
config.setParameter("video_codec", "h264");
config.setParameter("audio_supported", "false");
writer->configureStorage(config);

// Start recording session
std::string session_id = writer->startWrite(
    "/var/recordings/camera1/2024/01/15/10/1234567890.mkv", 
    "camera1"
);

// Write video data
writer->onFrame(session_id, frame_data, frame_size, pts, "video");

// Complete recording session
StorageResult result = writer->stopWrite(session_id, "camera1");
```

### 2. Cloud Storage (MinIO)

```cpp
// Create cloud storage writer
auto writer = UnifiedStorageWriterFactory::createWriter("cloud");

// Configure MinIO storage
StorageConfig config;
config.setParameter("storage_type", "cloud");
config.setParameter("underlying_storage", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-recordings");
config.setParameter("use_ssl", "false");
config.setParameter("video_codec", "h264");
config.setParameter("audio_supported", "false");

writer->configureStorage(config);

// Start recording session
std::string session_id = writer->startWrite(
    "recordings/camera1/2024/01/15/10/1234567890.mkv", 
    "camera1"
);

// Write video data (buffered and uploaded in background)
writer->onFrame(session_id, frame_data, frame_size, pts, "video");

// Complete recording session
StorageResult result = writer->stopWrite(session_id, "camera1");
```

### 3. Integration with GstMux

```cpp
// In GstMux::CreateUnifiedStorage()
m_unifiedStorageWriter = UnifiedStorageWriterFactory::createWriter(storage_type);
m_unifiedStorageWriter->configureStorage(config);

// In GstMux::create()
#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
    // Disable test mode by passing 0 (or use positive number for testing)
    m_unifiedStorageWriter->testPushBufferFailure(0);
#endif

// In GstMux::pushBuffer()
if (m_unifiedStorageWriter) {
    m_unifiedStorageWriter->onFrame(session_id, data, size, pts, media_type);
}

// In GstMux::createNewFileAndSetPlayState()
if (m_unifiedStorageWriter) {
    m_currentUploadSession = m_unifiedStorageWriter->startWrite(path, device_id);
}

// In GstMux::sendEOSAndUpdateDuration()
if (m_unifiedStorageWriter) {
    StorageResult result = m_unifiedStorageWriter->stopWrite(session_id, device_id);
}
```

## Configuration

### Environment Variables
```bash
export STORAGE_TYPE=local  # or "cloud"
export ENABLE_CLOUD_STORAGE=false  # or true
export MINIO_ENDPOINT=http://minio.internal:9000
export MINIO_ACCESS_KEY=minio_user
export MINIO_SECRET_KEY=minio_password
export MINIO_BUCKET=vms-recordings
```

### Configuration File Example
```ini
[storage]
type=local
video_codec=h264
audio_supported=false

[storage_cloud]
type=cloud
underlying_storage=minio
endpoint_url=http://minio.internal:9000
access_key=minio_user
secret_key=minio_password
bucket_name=vms-recordings
use_ssl=false
video_codec=h264
audio_supported=false
```

## Implementation Status

### ✅ Fully Implemented and Production-Ready
- **UnifiedStorageWriter** base class with complete GStreamer pipeline management
- **UnifiedLocalStorageWriter** with direct file writing and pipeline reuse
- **CloudStorageBuffer** with configurable buffering and rate limiting
- **Session management** with unique session IDs and proper state tracking
- **Error handling** with comprehensive validation and recovery
- **Test mode** with single-failure simulation
- **Thread safety** with proper mutex protection
- **Pipeline reuse optimization** for efficient file location changes

### 🔄 Partially Implemented
- **UnifiedCloudStorageWriter** with buffering system (cloud upload in progress)
- **MinIO integration** through CloudStorageBuffer
- **S3 integration** (stub implementation - needs AWS SDK)

### ❌ To Be Implemented
- **Complete cloud upload** functionality in UnifiedCloudStorageWriter
- **Additional cloud providers** (Google Cloud, Azure)
- **Advanced buffering** with adaptive buffer sizes

## Key Features

### Pipeline Reuse Optimization
- **Local Storage**: Optimized pipeline reuse reduces delay when changing file locations
- **State Management**: Pipeline is set to NULL state for filesink location changes
- **Health Monitoring**: Pipeline health is checked before reuse, with automatic recreation on failure

### Session Management
- **Session IDs**: Unique session IDs generated with timestamp, random number, and stream ID
- **Active Session Tracking**: Proper tracking of active sessions with thread-safe operations
- **Session Continuity**: Seamless transition between files in continuous recording

### Error Handling
- **Pipeline Failures**: Automatic pipeline recreation on state change failures
- **Buffer Validation**: Input validation for buffer data and size limits
- **Error Recovery**: Comprehensive error messages and recovery mechanisms
- **Test Mode**: Single-failure simulation for testing error recovery

### Test Mode
The implementation includes an optional test mode for error simulation:

```cpp
#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
// Simulate single pushBuffer failure for testing error recovery
writer->testPushBufferFailure(30);  // Fail once after 30 seconds, then work normally
#endif
```

**Test Mode Behavior:**
- **Single Failure**: Only fails once, then disables test mode automatically
- **Configurable Timing**: Specify when the failure should occur (in seconds)
- **Auto-Recovery**: After the single failure, normal operation resumes
- **Production Safe**: Test mode is disabled by default (pass 0 to disable)

## Performance Considerations

### Local Storage Performance
- **Direct File Writing**: No buffering overhead
- **Pipeline Reuse**: Optimized file location changes
- **Minimal Latency**: Direct GStreamer to filesystem path
- **Memory Efficient**: No additional buffering required

### Cloud Storage Performance
- **Buffered Uploads**: Configurable buffer sizes
- **Rate Limiting**: Prevents overwhelming cloud storage
- **Multipart Uploads**: Efficient large file handling
- **Background Processing**: Non-blocking upload operations

## Dependencies

### Required (already in project)
- C++17 or later
- GStreamer
- UUID library (`uuid/uuid.h`)
- Your existing logging system

### Optional (for cloud providers)
- **AWS SDK for C++** (for S3 support)
- **Google Cloud SDK** (for GCS support)
- **Azure SDK** (for Azure Blob support)

## Building

The unified storage writer system is integrated into the existing VMS build system. No additional build steps are required.

## Documentation

- **UNIFIED_STORAGE_WRITER.md**: Comprehensive overview of the unified storage system
- **STORAGE_FLOW_GUIDE.md**: Detailed flow guide for local and cloud storage
- **BUFFERING_SYSTEM_GUIDE.md**: Cloud storage buffering system documentation
- **ERROR_HANDLING_GUIDE.md**: Error handling and recovery documentation

## Future Enhancements

1. **Complete Cloud Upload**: Finish cloud storage upload functionality
2. **Multiple Cloud Providers**: Support for AWS S3, Azure Blob Storage, Google Cloud
3. **Advanced Buffering**: Adaptive buffer sizes based on network conditions
4. **Compression**: Additional compression options for cloud storage
5. **Encryption**: End-to-end encryption for cloud storage
6. **CDN Integration**: Direct upload to CDN for better distribution
7. **Real-time Monitoring**: Enhanced pipeline health monitoring and metrics
8. **Performance Optimization**: Further optimization of pipeline reuse and buffer management 
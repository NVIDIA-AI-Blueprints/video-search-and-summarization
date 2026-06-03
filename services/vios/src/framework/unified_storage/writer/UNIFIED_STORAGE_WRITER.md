# Unified GStreamer Storage Writer

## Overview

This implementation provides a unified approach to video storage using GStreamer pipelines for both local and cloud storage. The key insight is that both storage types use the same pipeline structure, with only the sink element being different:

- **Local Storage**: `Raw H.264/H.265 Frame → appsrc → h264parse/h265parse → matroskamux → filesink → MKV File`
- **Cloud Storage**: `Raw H.264/H.265 Frame → appsrc → h264parse/h265parse → matroskamux → appsink → Cloud Storage`

## Architecture

### Core Classes

1. **UnifiedStorageWriter** (Base Class)
   - Abstract base class that manages the common GStreamer pipeline
   - Handles pipeline creation, buffer management, and session management
   - Provides virtual methods for sink element creation and configuration
   - Manages GStreamer main loop and bus message handling
   - Supports both H.264 and H.265 video codecs
   - Optional audio support with various codecs (PCMU, PCMA, AAC)
   - **Test Mode**: Optional single-failure simulation for testing error recovery

2. **UnifiedLocalStorageWriter** (Derived Class)
   - Creates `filesink` element for local file writing
   - Handles local filesystem operations and directory creation
   - Optimized pipeline reuse for continuous recording
   - Direct file writing with no buffering needed
   - **Status**: ✅ Fully implemented and production-ready

3. **UnifiedCloudStorageWriter** (Derived Class)
   - Creates `appsink` element for cloud storage
   - Handles cloud storage operations through underlying storage writer
   - Includes buffering system for better performance
   - Supports MinIO and S3 storage backends
   - **Status**: 🔄 Partially implemented (buffering mode active)

4. **UnifiedStorageWriterFactory**
   - Factory class to create appropriate storage writers
   - Handles configuration and initialization

### Integration with GstMux

The GstMux class has been enhanced to use the unified storage writer:

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

## Benefits

1. **Code Reuse**: The same GStreamer pipeline code is used for both local and cloud storage
2. **Consistent Containerization**: Both storage types produce properly containerized MKV files
3. **Simplified GstMux**: GstMux doesn't need to manage different pipeline types
4. **Extensibility**: Easy to add new storage types by implementing the base class
5. **Performance**: Optimized pipeline reuse reduces delays when changing file locations
6. **Thread Safety**: Proper mutex protection for concurrent operations
7. **Error Recovery**: Comprehensive error handling and pipeline reset capabilities
8. **Test Mode**: Single-failure simulation for testing error recovery scenarios

## Configuration

### Local Storage Configuration
```cpp
StorageConfig config;
config.setParameter("storage_type", "local");
config.setParameter("base_path", "/path/to/recordings");
config.setParameter("video_codec", "h264");  // or "h265"
config.setParameter("audio_supported", "false");
config.setParameter("audio_codec", "pcmu");  // if audio enabled
config.setParameter("audio_sample_rate", "8000");
config.setParameter("audio_channels", "1");
```

### Cloud Storage Configuration
```cpp
StorageConfig config;
config.setParameter("storage_type", "cloud");
config.setParameter("underlying_storage", "minio");  // or "s3"
config.setParameter("video_codec", "h264");  // or "h265"
config.setParameter("audio_supported", "false");
config.setParameter("audio_codec", "pcmu");  // if audio enabled
config.setParameter("audio_sample_rate", "8000");
config.setParameter("audio_channels", "1");

// MinIO specific configuration
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-recordings");
config.setParameter("use_ssl", "false");
```

## Usage Example

```cpp
// Create local storage writer
auto local_writer = UnifiedStorageWriterFactory::createWriter("local");
local_writer->configureStorage(config);

// Create cloud storage writer
auto cloud_writer = UnifiedStorageWriterFactory::createWriter("cloud");
cloud_writer->configureStorage(config);

// Start recording session
std::string session_id = writer->startWrite(
    "recordings/camera1/2024/01/15/10/1234567890.mkv", 
    "camera1"
);

// Write video data
writer->onFrame(session_id, frame_data, frame_size, pts, "video");

// Complete recording session
StorageResult result = writer->stopWrite(session_id, "camera1");
```

## Pipeline Flow

### Local Storage Flow
1. **GstMux receives raw H.264/H.265 frames** from video encoder
2. **Frames are pushed to `appsrc`** in the unified pipeline via `onFrame()`
3. **`h264parse` or `h265parse`** processes the frames and extracts metadata
4. **`matroskamux`** creates MKV container with proper timestamps
5. **`filesink`** writes the containerized video directly to local file system
6. **Pipeline reuse optimization**: When changing file location, pipeline is set to NULL state, filesink location is updated, then set back to PLAYING

### Cloud Storage Flow
1. **GstMux receives raw H.264/H.265 frames** from video encoder
2. **Frames are pushed to `appsrc`** in the unified pipeline via `onFrame()`
3. **`h264parse` or `h265parse`** processes the frames and extracts metadata
4. **`matroskamux`** creates MKV container with proper timestamps
5. **`appsink`** receives the containerized video data via `onNewSampleCloud()`
6. **Data is buffered** in memory using CloudStorageBuffer for later upload
7. **Background upload** to cloud storage (MinIO/S3) when buffer conditions are met

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
- **Test Mode**: Optional single-failure simulation for testing error recovery

### Thread Safety
- **Mutex Protection**: Pipeline operations protected by mutex
- **Atomic Variables**: Thread-safe counters for bytes and frames written
- **Condition Variables**: Proper synchronization for EOS handling

## Performance Considerations

- **Local Storage**: Direct file writing with optimized pipeline reuse
- **Cloud Storage**: Buffered uploads with configurable buffer size and rate limiting
- **Memory Usage**: Configurable buffer sizes to balance memory usage and performance
- **Pipeline Reuse**: Reduces initialization overhead for continuous recording
- **State Changes**: Optimized state transitions for filesink location changes

## Error Handling

The unified approach includes comprehensive error handling:

- **Pipeline Creation Failures**: Automatic cleanup and error reporting
- **Buffer Push Failures**: Input validation and error recovery
- **Storage Availability Checks**: Directory creation and write permission verification
- **Session Management Errors**: Proper session state tracking and cleanup
- **Network Connectivity Issues**: Cloud storage error handling with retry mechanisms
- **Test Mode**: Single-failure simulation for testing error recovery

## Test Mode

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

## Current Implementation Status

### ✅ Fully Implemented
- **UnifiedStorageWriter** base class with complete GStreamer pipeline management
- **UnifiedLocalStorageWriter** with direct file writing and pipeline reuse
- **Session management** with unique session IDs and proper state tracking
- **Error handling** with comprehensive validation and recovery
- **Test mode** with single-failure simulation
- **Thread safety** with proper mutex protection

### 🔄 Partially Implemented
- **UnifiedCloudStorageWriter** with buffering system (cloud upload in progress)
- **MinIO integration** through CloudStorageBuffer
- **S3 integration** (stub implementation)

### ❌ To Be Implemented
- **Complete cloud upload** functionality in UnifiedCloudStorageWriter
- **Additional cloud providers** (Google Cloud, Azure)
- **Advanced buffering** with adaptive buffer sizes

## Future Enhancements

1. **Complete Cloud Upload**: Finish cloud storage upload functionality
2. **Multiple Cloud Providers**: Support for AWS S3, Azure Blob Storage, Google Cloud
3. **Advanced Buffering**: Adaptive buffer sizes based on network conditions
4. **Compression**: Additional compression options for cloud storage
5. **Encryption**: End-to-end encryption for cloud storage
6. **CDN Integration**: Direct upload to CDN for better distribution
7. **Real-time Monitoring**: Enhanced pipeline health monitoring and metrics
8. **Performance Optimization**: Further optimization of pipeline reuse and buffer management 
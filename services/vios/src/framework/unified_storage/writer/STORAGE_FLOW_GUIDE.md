# Storage Flow Guide - Local and Cloud Storage Writers

## Overview

This document provides a detailed flow guide for the unified storage writer system, covering both local and cloud storage implementations. The system uses a unified GStreamer pipeline approach with different sink elements for each storage type.

## Architecture Overview

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

## Local Storage Flow

### 1. Initialization Flow

```cpp
// 1. Factory creates local storage writer
auto writer = UnifiedStorageWriterFactory::createWriter("local");

// 2. Configure storage
StorageConfig config;
config.setParameter("storage_type", "local");
config.setParameter("video_codec", "h264");
writer->configureStorage(config);

// 3. Initialize storage (local doesn't need special init)
writer->initializeStorage(); // Returns true immediately

// 4. Create pipeline
writer->createPipeline("h264", false, "stream_id");
```

### 2. Pipeline Creation Flow

```
1. Create GStreamer elements:
   - appsrc (video source)
   - h264parse/h265parse (video parser)
   - matroskamux (container muxer)
   - filesink (local file sink)

2. Link elements:
   appsrc → h264parse → matroskamux → filesink

3. Initialize GMainLoop and bus handling

4. Set pipeline state to READY
```

### 3. Recording Session Flow

```cpp
// 1. Start recording session
std::string session_id = writer->startWrite(
    "/path/to/recordings/camera1/2024/01/15/10/1234567890.mkv",
    "camera1"
);

// 2. Configure filesink for new file
StorageResult config_result = writer->configureSinkElement(filesink, file_path, session_id);
if (!config_result.success) {
    LOG(error) << "Failed to configure filesink: " << config_result.error_message 
               << " for session: " << session_id << endl;
    writer->cleanupSession(session_id);
    return false;
}
// - Creates directory if needed
// - Verifies write permissions
// - Sets filesink location property

// 3. Set pipeline to PLAYING state with error handling
GstStateChangeReturn state_result = writer->setPipelineState(GST_STATE_PLAYING);
if (state_result == GST_STATE_CHANGE_FAILURE) {
    LOG(error) << "Failed to set pipeline to PLAYING state for session: " 
               << session_id << endl;
    writer->cleanupSession(session_id);
    return false;
} else if (state_result == GST_STATE_CHANGE_ASYNC) {
    // Wait for state change to complete
    GstState current_state, pending_state;
    GstStateChangeReturn wait_result = gst_element_get_state(
        writer->getPipeline(), &current_state, &pending_state, 5 * GST_SECOND);
    
    if (wait_result != GST_STATE_CHANGE_SUCCESS) {
        LOG(error) << "Pipeline failed to reach PLAYING state within timeout for session: " 
                   << session_id << " current_state=" << current_state 
                   << " pending_state=" << pending_state << endl;
        writer->cleanupSession(session_id);
        return false;
    }
}

LOG(info) << "Recording session started successfully: " << session_id << endl;
```

### 4. Buffer Processing Flow

```
1. GstMux receives raw H.264/H.265 frames
2. Calls writer->onFrame(session_id, data, size, pts, "video")
3. onFrame() validates session and calls pushBufferToPipeline()
4. pushBufferToPipeline():
   - Validates input data
   - Creates GStreamer buffer
   - Sets PTS/DTS timestamps
   - Pushes to appsrc
   - Updates statistics (bytes_written, frames_written)
5. GStreamer pipeline processes:
   - h264parse: Extracts metadata, validates stream
   - matroskamux: Creates MKV container with timestamps
   - filesink: Writes directly to local file system
```

### 5. Pipeline Reuse Flow (File Change)

```cpp
// When changing to new file location:
// 1. Set pipeline to NULL state with error handling
GstStateChangeReturn null_result = writer->setPipelineState(GST_STATE_NULL);
if (null_result == GST_STATE_CHANGE_FAILURE) {
    LOG(error) << "Failed to set pipeline to NULL state for file change" << endl;
    return false;
} else if (null_result == GST_STATE_CHANGE_ASYNC) {
    GstState current_state, pending_state;
    GstStateChangeReturn wait_result = gst_element_get_state(
        writer->getPipeline(), &current_state, &pending_state, 3 * GST_SECOND);
    if (wait_result != GST_STATE_CHANGE_SUCCESS) {
        LOG(error) << "Pipeline failed to reach NULL state within timeout" << endl;
        return false;
    }
}

// 2. Configure filesink with new location
StorageResult config_result = writer->configureSinkElement(filesink, new_file_path, session_id);
if (!config_result.success) {
    LOG(error) << "Failed to configure filesink for new location: " 
               << config_result.error_message << endl;
    return false;
}

// 3. Set pipeline back to PLAYING state with error handling
GstStateChangeReturn play_result = writer->setPipelineState(GST_STATE_PLAYING);
if (play_result == GST_STATE_CHANGE_FAILURE) {
    LOG(error) << "Failed to set pipeline to PLAYING state after file change" << endl;
    return false;
} else if (play_result == GST_STATE_CHANGE_ASYNC) {
    GstState current_state, pending_state;
    GstStateChangeReturn wait_result = gst_element_get_state(
        writer->getPipeline(), &current_state, &pending_state, 5 * GST_SECOND);
    if (wait_result != GST_STATE_CHANGE_SUCCESS) {
        LOG(error) << "Pipeline failed to reach PLAYING state after file change" << endl;
        return false;
    }
}

// 4. Continue recording with new file
LOG(info) << "Successfully changed file location to: " << new_file_path << endl;

// This optimization reduces delay when changing file locations
```

### 6. Session Completion Flow

```cpp
// 1. Stop recording
StorageResult result = writer->stopWrite(session_id, "camera1");
if (!result.success) {
    LOG(error) << "Failed to stop recording: " << result.error_message 
               << " for session: " << session_id << endl;
    // Continue with cleanup even if stop failed
}

// 2. Send EOS to pipeline
bool eos_sent = writer->sendEOS();
if (!eos_sent) {
    LOG(warning) << "Failed to send EOS to pipeline for session: " << session_id << endl;
}

// 3. Wait for EOS message with timeout handling
bool eos_received = writer->waitForEOSMessage(5000);
if (!eos_received) {
    LOG(warning) << "EOS message not received within timeout for session: " 
                 << session_id << endl;
    // Continue with cleanup even if EOS timeout
}

// 4. Set pipeline to NULL state with error handling
GstStateChangeReturn null_result = writer->setPipelineState(GST_STATE_NULL);
if (null_result == GST_STATE_CHANGE_FAILURE) {
    LOG(error) << "Failed to set pipeline to NULL state during cleanup for session: " 
               << session_id << endl;
} else if (null_result == GST_STATE_CHANGE_ASYNC) {
    GstState current_state, pending_state;
    GstStateChangeReturn wait_result = gst_element_get_state(
        writer->getPipeline(), &current_state, &pending_state, 3 * GST_SECOND);
    if (wait_result != GST_STATE_CHANGE_SUCCESS) {
        LOG(warning) << "Pipeline failed to reach NULL state within timeout during cleanup" << endl;
    }
}

// 5. Finalize session (verify file exists and get size)
result = writer->finalizeSession(session_id, "camera1");
if (!result.success) {
    LOG(error) << "Failed to finalize session: " << result.error_message 
               << " for session: " << session_id << endl;
    // Continue with cleanup even if finalization failed
}

// 6. Clean up session
bool cleanup_success = writer->cleanupSession(session_id);
if (!cleanup_success) {
    LOG(error) << "Failed to cleanup session: " << session_id << endl;
    return false;
}

LOG(info) << "Session completed and cleaned up successfully: " << session_id << endl;
```

## Cloud Storage Flow

### 1. Initialization Flow

```cpp
// 1. Factory creates cloud storage writer
auto writer = UnifiedStorageWriterFactory::createWriter("cloud");

// 2. Configure storage
StorageConfig config;
config.setParameter("storage_type", "cloud");
config.setParameter("underlying_storage", "minio");
config.setParameter("endpoint", "http://minio.internal:9000");
config.setParameter("access_key", "minio_user");
config.setParameter("secret_key", "minio_password");
config.setParameter("bucket_name", "vms-recordings");
writer->configureStorage(config);

// 3. Initialize cloud storage
writer->initializeStorage();
// - Creates MinIO/S3 storage writer
// - Performs health check
// - Initializes CloudStorageBuffer

// 4. Create pipeline
writer->createPipeline("h264", false, "stream_id");
```

### 2. Pipeline Creation Flow

```
1. Create GStreamer elements:
   - appsrc (video source)
   - h264parse/h265parse (video parser)
   - matroskamux (container muxer)
   - appsink (cloud storage sink)

2. Link elements:
   appsrc → h264parse → matroskamux → appsink

3. Configure appsink:
   - Set emit-signals = TRUE
   - Set sync = FALSE
   - Connect new-sample signal to onNewSampleCloud()
   - Connect eos signal to onEOSCloud()

4. Initialize GMainLoop and bus handling

5. Set pipeline state to READY
```

### 3. Recording Session Flow

```cpp
// 1. Start recording session
std::string session_id = writer->startWrite(
    "recordings/camera1/2024/01/15/10/1234567890.mkv",
    "camera1"
);

// 2. Configure appsink for new session
writer->configureSinkElement(appsink, remote_path, session_id);

// 3. Set pipeline to PLAYING state
writer->setPipelineState(GST_STATE_PLAYING);

// 4. Start cloud storage session
m_cloud_writer->startSession(session_id, remote_path);
```

### 4. Buffer Processing Flow

```
1. GstMux receives raw H.264/H.265 frames
2. Calls writer->onFrame(session_id, data, size, pts, "video")
3. onFrame() validates session and calls pushBufferToPipeline()
4. pushBufferToPipeline():
   - Validates input data
   - Creates GStreamer buffer
   - Sets PTS/DTS timestamps
   - Pushes to appsrc
   - Updates statistics
5. GStreamer pipeline processes:
   - h264parse: Extracts metadata, validates stream
   - matroskamux: Creates MKV container with timestamps
   - appsink: Triggers onNewSampleCloud() callback
6. onNewSampleCloud():
   - Extracts buffer data from GStreamer sample
   - Adds to CloudStorageBuffer
   - Triggers background upload when buffer conditions are met
```

### 5. Cloud Storage Buffer Flow

```
1. Data arrives in onNewSampleCloud()
2. CloudStorageBuffer::bufferFrame():
   - Validates frame data
   - Adds to internal buffer queue
   - Updates buffer statistics
   - Checks buffer conditions for upload

3. Upload Rate Limiting:
   - UploadRateLimiter controls upload frequency
   - Adaptive rate limiting based on buffer utilization
   - Prevents overwhelming cloud storage

4. Background Upload:
   - Worker thread processes buffered data
   - Uploads to MinIO/S3 using multipart upload
   - Handles retry logic and error recovery
   - Updates upload statistics
```

### 6. Session Completion Flow

```cpp
// 1. Stop recording
StorageResult result = writer->stopWrite(session_id, "camera1");

// 2. Send EOS to pipeline
writer->sendEOS();

// 3. Wait for EOS message
writer->waitForEOSMessage(5000);

// 4. Set pipeline to NULL state
writer->setPipelineState(GST_STATE_NULL);

// 5. Finalize cloud session
result = writer->finalizeSession(session_id, "camera1");
// - Ensures all buffered data is uploaded
// - Completes multipart upload
// - Verifies upload success

// 6. Clean up session
writer->cleanupSession(session_id);
```

## Error Handling Flow

### 1. Pipeline Errors

```
1. GStreamer bus message handler detects error
2. Logs error details and debug information
3. Sets last error message
4. Triggers error recovery:
   - Reset pipeline state
   - Recreate pipeline if necessary
   - Restart recording session
```

### 2. Buffer Push Errors

```
1. Input validation fails:
   - Invalid data pointer
   - Zero buffer size
   - Buffer size too large (>100MB)
   - Test mode failure simulation

2. Pipeline not ready:
   - Pipeline not in PLAYING state
   - Pipeline creation failed
   - Session not active

3. GStreamer push failures:
   - appsrc queue full
   - Pipeline state issues
   - Memory allocation failures
```

### 3. Storage Errors

#### Local Storage:
```
1. Directory creation fails:
   - Insufficient permissions
   - Disk space full
   - Invalid path

2. File write failures:
   - Disk full
   - Permission denied
   - File system errors
```

#### Cloud Storage:
```
1. Network connectivity issues:
   - Connection timeout
   - DNS resolution failure
   - SSL/TLS errors

2. Authentication errors:
   - Invalid credentials
   - Expired tokens
   - Permission denied

3. Upload failures:
   - Multipart upload failures
   - Retry exhaustion
   - Storage quota exceeded
```

### 4. Test Mode Error Simulation

```cpp
// Enable test mode with configuration object
StorageConfig test_config;
test_config.setParameter("test_mode", "enabled");
test_config.setParameter("failure_interval_seconds", "30");
test_config.setParameter("failure_type", "push_buffer");
test_config.setParameter("auto_disable", "true");
test_config.setParameter("clear_error_after_ms", "100");

writer->enableTestMode(test_config);

// Alternative: Simple API for common use cases
writer->enableTestMode(30); // Fail once after 30 seconds, auto-disable

// Test mode behavior:
1. Wait for specified interval (30 seconds)
2. Set error message to simulate failure
3. Next buffer push will fail
4. Clear error after 100ms
5. Disable test mode automatically
6. Normal operation resumes
```

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

### Memory Management
- **Buffer Size Limits**: 100MB maximum buffer size
- **Queue Management**: Configurable buffer queue sizes
- **Garbage Collection**: Automatic cleanup of completed uploads
- **Memory Monitoring**: Buffer utilization tracking

## Configuration Examples

### Local Storage Configuration
```cpp
StorageConfig local_config;
local_config.setParameter("storage_type", "local");
local_config.setParameter("video_codec", "h265");
local_config.setParameter("audio_supported", "true");
local_config.setParameter("audio_codec", "pcma");
local_config.setParameter("audio_sample_rate", "8000");
local_config.setParameter("audio_channels", "1");
```

### MinIO Cloud Storage Configuration
```cpp
StorageConfig minio_config;
minio_config.setParameter("storage_type", "cloud");
minio_config.setParameter("underlying_storage", "minio");
minio_config.setParameter("endpoint", "http://minio.internal:9000");
minio_config.setParameter("access_key", "minio_user");
minio_config.setParameter("secret_key", "minio_password");
minio_config.setParameter("bucket_name", "vms-recordings");
minio_config.setParameter("use_ssl", "false");
minio_config.setParameter("video_codec", "h264");
minio_config.setParameter("audio_supported", "false");
```

### S3 Cloud Storage Configuration
```cpp
StorageConfig s3_config;
s3_config.setParameter("storage_type", "cloud");
s3_config.setParameter("underlying_storage", "s3");
s3_config.setParameter("bucket_name", "my-recordings-bucket");
s3_config.setParameter("region", "us-east-1");
s3_config.setParameter("access_key", "AKIAEXAMPLE");
s3_config.setParameter("secret_key", "secret-key-here");
s3_config.setParameter("video_codec", "h264");
s3_config.setParameter("audio_supported", "false");
```

## Monitoring and Debugging

### Logging
- **Pipeline State Changes**: Detailed logging of GStreamer state transitions
- **Buffer Statistics**: Bytes and frames written tracking
- **Error Details**: Comprehensive error messages with context
- **Performance Metrics**: Upload rates and buffer utilization

### Health Monitoring
- **Pipeline Health**: Regular checks of pipeline state
- **Storage Availability**: Cloud storage connectivity checks
- **Buffer Status**: Buffer utilization and upload progress
- **Session Tracking**: Active session monitoring

### Debug Features
- **Test Mode**: Single-failure simulation for error testing
- **Verbose Logging**: Detailed pipeline and buffer operations
- **Statistics Tracking**: Performance and usage metrics
- **Error Recovery**: Automatic pipeline reset and session recovery 
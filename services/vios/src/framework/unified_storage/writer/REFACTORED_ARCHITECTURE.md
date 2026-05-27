# Refactored Architecture: Buffering in StorageWriter Classes

## Why This Refactoring is Better

### Current Issues with GstMux Having Buffering:
1. **Mixed Responsibilities**: GstMux handles both video pipeline AND cloud buffering
2. **Complex Interface**: GstMux needs to know about buffer management, session handling, etc.
3. **Storage-Agnostic Buffering**: All cloud providers use same buffering strategy
4. **Code Duplication**: Buffering logic scattered across multiple classes
5. **Testing Complexity**: Hard to test buffering independently from video pipeline

### Benefits of StorageWriter-Integrated Buffering:
1. **Clean Separation**: GstMux only handles video pipeline, StorageWriter handles storage
2. **Simple Interface**: GstMux just calls `writeRecordingData()` - buffering is transparent
3. **Storage-Specific Optimization**: Each cloud provider can optimize for their APIs
4. **Polymorphic Behavior**: Local = direct write, Cloud = buffered write
5. **Better Encapsulation**: Buffering is implementation detail of storage
6. **Easier Testing**: Each component can be tested independently

## Architectural Comparison

### BEFORE: Complex GstMux with Mixed Responsibilities

```cpp
class GstMux {
private:
    // Video pipeline components
    GstElement* m_pipeline;
    GstElement* m_sourceVideo;
    
    // Cloud storage components
    std::unique_ptr<GstMuxStorageIntegration> m_storageIntegration;
    std::unique_ptr<CloudStorageBuffer> m_cloudBuffer;  // ❌ Mixed responsibility
    std::string m_currentUploadSession;
    std::string m_cloudRemotePath;
    
public:
    // Complex buffering methods in video class
    bool startCloudBuffering(size_t buffer_size_mb, size_t max_frames);  // ❌ Wrong place
    void stopCloudBuffering();                                           // ❌ Wrong place
    void flushCloudBuffer();                                             // ❌ Wrong place
    CloudStorageBuffer::BufferStats getBufferStats() const;             // ❌ Wrong place
    
    bool pushBuffer(FrameInfo frameinfo) {
        if (isUsingLocalStorage()) {
            // GStreamer pipeline operations
            ret = gst_app_src_push_buffer((GstAppSrc*)m_sourceVideo, gstbuffer);
        } else {
            // Complex buffering logic in video class ❌
            if (!m_cloudBuffer || !validateCloudStorageSession()) {
                setError();
                return true;
            }
            
            bool buffered = m_cloudBuffer->bufferFrame(
                frameinfo.content.data(), 
                frameinfo.size, 
                bufferPTS,
                frameinfo.mediaType,
                m_currentUploadSession
            );
            
            if (!buffered) {
                // Complex error handling in wrong place ❌
                double utilization = m_cloudBuffer->getBufferUtilizationPercent();
                if (utilization > 95.0) {
                    LOG(error) << "Cloud buffer critically full";
                }
                // ... more complex logic
            }
        }
    }
};
```

### AFTER: Clean GstMux with Single Responsibility

```cpp
class GstMux {
private:
    // Video pipeline components ONLY
    GstElement* m_pipeline;
    GstElement* m_sourceVideo;
    
    // Simple storage interface
    std::unique_ptr<StorageWriter> m_storageWriter;  // ✅ Clean interface
    std::string m_currentSession;
    
public:
    // Simple, clean methods
    bool initializeStorage(const std::string& storage_type, const StorageConfig& config) {
        m_storageWriter = StorageWriterFactory::create(storage_type, config);
        return m_storageWriter != nullptr;
    }
    
    bool pushBuffer(FrameInfo frameinfo) {
        if (isUsingLocalStorage()) {
            // GStreamer pipeline operations
            ret = gst_app_src_push_buffer((GstAppSrc*)m_sourceVideo, gstbuffer);
        } else {
            // Simple, clean cloud write ✅
            bool success = m_storageWriter->writeRecordingData(
                m_currentSession,
                frameinfo.content.data(),
                frameinfo.size
            );
            
            if (!success) {
                LOG(error) << "Storage write failed: " << m_storageWriter->getLastError();
                setError();
                return true;
            }
        }
        return false;
    }
    
    void sendEOSAndUpdateDuration() {
        if (isUsingLocalStorage()) {
            sendEOS();
            updateVideoRecordInDb(m_prevFileName, m_prevTS, m_prevFileStartTime, true);
        } else {
            // Simple completion ✅
            StorageResult result = m_storageWriter->completeRecordingSession(m_currentSession, m_deviceId);
            if (result.success) {
                updateVideoRecordInDbWithCloudPath(result.storage_path, m_prevTS, m_prevFileStartTime, true);
            } else {
                LOG(error) << "Storage completion failed: " << result.message;
                setError();
            }
        }
    }
};
```

## StorageWriter Class Hierarchy

### Base Interface (Clean and Simple)

```cpp
class StorageWriter {
public:
    // Simple interface - buffering is transparent
    virtual std::string startRecordingSession(const std::string& remote_path, 
                                              const std::string& device_id,
                                              size_t estimated_size = 0) = 0;
    
    virtual bool writeRecordingData(const std::string& session_id, 
                                   const void* data, 
                                   size_t size) = 0;
    
    virtual StorageResult completeRecordingSession(const std::string& session_id, 
                                                  const std::string& device_id) = 0;
    
    virtual StorageStats getStats() const = 0; // Includes buffering stats for cloud
};
```

### Local Storage (No Buffering Needed)

```cpp
class LocalStorageWriter : public StorageWriter {
public:
    bool writeRecordingData(const std::string& session_id, 
                           const void* data, 
                           size_t size) override {
        // Direct write to file - no buffering overhead ✅
        auto it = m_sessions.find(session_id);
        if (it != m_sessions.end()) {
            it->second.file_stream.write(static_cast<const char*>(data), size);
            return it->second.file_stream.good();
        }
        return false;
    }
};
```

### Cloud Storage (Automatic Buffering)

```cpp
class CloudStorageWriter : public StorageWriter {
public:
    bool writeRecordingData(const std::string& session_id, 
                           const void* data, 
                           size_t size) override final {
        // Automatic buffering for ALL cloud storage ✅
        if (!m_buffer) {
            initializeBuffering(); // Auto-initialize on first write
        }
        
        return m_buffer->bufferFrame(data, size, getCurrentTimestamp(), 
                                    "video", session_id);
    }
    
    StorageResult completeRecordingSession(const std::string& session_id, 
                                          const std::string& device_id) override final {
        // Flush buffer and complete upload ✅
        if (m_buffer) {
            m_buffer->flushSession(session_id);
        }
        
        return doCompleteRecordingSession(session_id, device_id);
    }
    
protected:
    // Each cloud provider implements these
    virtual bool doWriteRecordingData(const std::string& session_id, 
                                     const void* data, 
                                     size_t size) = 0;
};
```

### S3 Implementation (Optimized for S3)

```cpp
class S3StorageWriter : public CloudStorageWriter {
protected:
    bool doWriteRecordingData(const std::string& session_id, 
                             const void* data, 
                             size_t size) override {
        // S3-specific optimizations ✅
        // - Use multipart uploads
        // - Optimize part sizes for S3
        // - Handle S3-specific errors
        auto& session = m_s3_sessions[session_id];
        
        std::string etag;
        bool success = uploadPart(session.upload_id, session.key, 
                                 session.part_number++, data, size, etag);
        if (success) {
            session.etags.push_back(etag);
        }
        return success;
    }
};
```

### Google Cloud Implementation (Optimized for GCS)

```cpp
class GCSStorageWriter : public CloudStorageWriter {
protected:
    bool doWriteRecordingData(const std::string& session_id, 
                             const void* data, 
                             size_t size) override {
        // GCS-specific optimizations ✅
        // - Use resumable uploads
        // - Optimize chunk sizes for GCS
        // - Handle GCS-specific errors
        auto& session = m_gcs_sessions[session_id];
        
        return uploadChunk(session.upload_url, session.bytes_uploaded, 
                          data, size);
    }
};
```

## Benefits in Practice

### 1. Simplified GstMux Code

**Before (Complex)**:
```cpp
// 50+ lines of buffering management in video class
bool GstMux::createNewFileAndSetPlayState(FrameInfo &frameinfo) {
    // ... complex logic mixing video and storage concerns
    if (!startCloudBuffering()) {
        // error handling
    }
    // ... more mixed responsibilities
}
```

**After (Simple)**:
```cpp
// 5 lines of clean storage interface
bool GstMux::createNewFileAndSetPlayState(FrameInfo &frameinfo) {
    if (!isUsingLocalStorage()) {
        m_currentSession = m_storageWriter->startRecordingSession(remotePath, m_deviceId);
        return !m_currentSession.empty();
    }
    // ... local storage logic
}
```

### 2. Storage-Specific Optimizations

**S3 Writer**:
```cpp
// Optimized for S3 multipart uploads
configure(config) {
    // S3-specific buffering: larger chunks for fewer API calls
    config.buffering.buffer_size_mb = 100;  // Larger buffer for S3
    config.buffering.max_upload_fps = 20.0; // Conservative for S3 rate limits
}
```

**GCS Writer**:
```cpp
// Optimized for Google Cloud resumable uploads  
configure(config) {
    // GCS-specific buffering: smaller chunks for resumable uploads
    config.buffering.buffer_size_mb = 50;   // Smaller buffer for GCS
    config.buffering.max_upload_fps = 28.0; // More aggressive for GCS
}
```

### 3. Easy Testing

**Test Storage Without Video Pipeline**:
```cpp
TEST(S3StorageWriter, BufferingPerformance) {
    S3StorageWriter writer;
    StorageConfig config = createS3Config();
    writer.configure(config);
    
    std::string session = writer.startRecordingSession("test-path", "device1");
    
    // Simulate 30 FPS writes
    for (int i = 0; i < 900; i++) { // 30 seconds
        std::vector<uint8_t> frame_data(100000); // 100KB frame
        bool success = writer.writeRecordingData(session, frame_data.data(), frame_data.size());
        EXPECT_TRUE(success);
        
        std::this_thread::sleep_for(std::chrono::milliseconds(33)); // 30 FPS
    }
    
    StorageResult result = writer.completeRecordingSession(session, "device1");
    EXPECT_TRUE(result.success);
    
    // Check buffering performance
    StorageStats stats = writer.getStats();
    EXPECT_GT(stats.buffering.upload_rate_fps, 25.0);
    EXPECT_LT(stats.buffering.frames_dropped, 5);
}
```

**Test Video Pipeline Without Storage**:
```cpp
TEST(GstMux, VideoProcessing) {
    MockStorageWriter mockStorage;
    GstMux mux(RecordState::Continuous);
    mux.setStorageWriter(std::make_unique<MockStorageWriter>());
    
    // Test video pipeline independently
    // Storage is mocked out
}
```

### 4. Configuration Flexibility

```cpp
// Different configurations for different storage types
StorageConfig s3Config;
s3Config.storage_type = "s3";
s3Config.buffering.buffer_size_mb = 100;  // Large buffer for S3
s3Config.buffering.max_upload_fps = 20.0; // Conservative for API limits

StorageConfig gcsConfig;
gcsConfig.storage_type = "gcs";  
gcsConfig.buffering.buffer_size_mb = 50;  // Smaller buffer for GCS
gcsConfig.buffering.max_upload_fps = 28.0; // More aggressive

StorageConfig localConfig;
localConfig.storage_type = "local";
// No buffering configuration needed for local storage
```

## Migration Path

### Step 1: Create New StorageWriter Interface
- Define clean interface without buffering concerns
- Create CloudStorageWriter base class with buffering

### Step 2: Implement Storage Types
- LocalStorageWriter (direct writes)
- S3StorageWriter (with S3-optimized buffering)
- GCSStorageWriter (with GCS-optimized buffering)

### Step 3: Simplify GstMux
- Remove all buffering-related code
- Replace with simple StorageWriter calls
- Remove CloudStorageBuffer from GstMux

### Step 4: Update Factory
- StorageWriterFactory creates appropriate type
- Each type self-configures its optimal buffering

## Final Result

**GstMux becomes simple and focused**:
- Only handles video pipeline concerns
- Clean, simple storage interface
- No knowledge of buffering implementation

**Storage Writers become specialized**:
- Each optimized for specific cloud provider
- Buffering tuned for API characteristics
- Independent testing and development

**Better Architecture**:
- Single Responsibility Principle
- Polymorphic behavior
- Easy to extend with new storage types
- Clean separation of concerns

This refactoring makes the code much more maintainable, testable, and allows for storage-specific optimizations while keeping the video pipeline simple and focused. 
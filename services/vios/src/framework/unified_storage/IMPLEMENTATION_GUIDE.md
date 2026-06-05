# Unified Storage Framework - Implementation Guide

This guide provides detailed information about implementing and extending the Unified Storage Framework.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component Implementation](#component-implementation)
3. [Timing and Performance](#timing-and-performance)
4. [Error Handling Implementation](#error-handling-implementation)
5. [Thread Safety](#thread-safety)
6. [Testing and Validation](#testing-and-validation)
7. [Integration with VMS](#integration-with-vms)
8. [Troubleshooting](#troubleshooting)

## Architecture Overview

### Design Principles

The Unified Storage Framework follows these key design principles:

1. **Unified Interface**: Single API for both local and cloud storage
2. **Factory Pattern**: Easy creation and configuration of components
3. **Thread Safety**: All operations are thread-safe
4. **Performance Monitoring**: Built-in timing and statistics
5. **Error Handling**: Comprehensive error reporting
6. **Extensibility**: Easy to add new storage backends

### Class Hierarchy

```
UnifiedStorageReader (abstract)
├── UnifiedLocalStorageReader
└── UnifiedCloudStorageReader
    └── CloudReader (existing)

UnifiedStorageWriter (abstract)
├── UnifiedLocalStorageWriter
└── UnifiedCloudStorageWriter
    └── CloudStorageBuffer

UnifiedStorageManager (abstract)
├── UnifiedLocalStorageManager
└── UnifiedCloudStorageManager
    └── CloudManager (existing)
```

## Component Implementation

### 1. Reader Implementation

#### Base Class Structure
```cpp
class UnifiedStorageReader {
public:
    virtual ~UnifiedStorageReader() = default;
    
    // Configuration
    virtual bool configureStorage(const StorageConfig& config) = 0;
    virtual bool isAvailable() const = 0;
    
    // Core operations
    virtual CloudListResult listObjects(const std::string& bucketName, 
                                       const std::string& prefix = "",
                                       int maxKeys = 1000) = 0;
    virtual CloudResult downloadObject(const std::string& bucketName,
                                      const std::string& objectKey,
                                      const std::string& localFilePath) = 0;
    
    // Statistics
    virtual StorageStats getReaderStats() const = 0;
    virtual void resetReaderStats() = 0;

protected:
    // Helper methods for derived classes
    void recordOperation(bool success, std::chrono::milliseconds duration, 
                        const std::string& errorCode = "");
    std::string getLastError() const;
    
private:
    StorageStats m_stats;
    std::string m_lastError;
    mutable std::mutex m_mutex;
};
```

#### Local Reader Implementation
```cpp
class UnifiedLocalStorageReader : public UnifiedStorageReader {
public:
    bool configureStorage(const StorageConfig& config) override;
    bool isAvailable() const override;
    
    CloudListResult listObjects(const std::string& bucketName, 
                               const std::string& prefix = "",
                               int maxKeys = 1000) override;
    CloudResult downloadObject(const std::string& bucketName,
                              const std::string& objectKey,
                              const std::string& localFilePath) override;

private:
    std::string m_basePath;
    bool m_initialized = false;
    
    // Helper methods
    std::string getFullPath(const std::string& relativePath) const;
    bool isPathWithinBase(const std::string& path) const;
    std::vector<FileInfo> scanDirectory(const std::string& path, 
                                       const std::string& prefix,
                                       int maxKeys) const;
};
```

#### Cloud Reader Implementation
```cpp
class UnifiedCloudStorageReader : public UnifiedStorageReader {
public:
    bool configureStorage(const StorageConfig& config) override;
    bool isAvailable() const override;
    
    CloudListResult listObjects(const std::string& bucketName, 
                               const std::string& prefix = "",
                               int maxKeys = 1000) override;
    CloudResult downloadObject(const std::string& bucketName,
                              const std::string& objectKey,
                              const std::string& localFilePath) override;

private:
    std::shared_ptr<CloudReader> m_cloudReader;
    CloudStorageType m_cloudType;
    bool m_initialized = false;
    
    // Helper methods
    bool initializeCloudReader(const StorageConfig& config);
    std::string getCloudTypeString() const;
};
```

### 2. Writer Implementation

#### Base Class Structure
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
    virtual bool changeFileLocation(const std::string& sessionId, 
                                   const std::string& newFilePath) = 0;
    
    // Statistics
    virtual StorageStats getWriterStats() const = 0;
    virtual void resetWriterStats() = 0;

protected:
    // Helper methods for derived classes
    void recordOperation(bool success, std::chrono::milliseconds duration, 
                        const std::string& errorCode = "");
    std::string generateSessionId() const;
    bool validateSession(const std::string& sessionId) const;
    
private:
    StorageStats m_stats;
    std::map<std::string, SessionInfo> m_sessions;
    mutable std::mutex m_mutex;
};
```

#### Local Writer Implementation
```cpp
class UnifiedLocalStorageWriter : public UnifiedStorageWriter {
public:
    bool configureStorage(const StorageConfig& config) override;
    bool isAvailable() const override;
    
    std::string startWrite(const std::string& filePath,
                          int width, int height, int fps,
                          const std::string& codec = "h264",
                          bool enableAudio = false) override;
    bool stopWrite(const std::string& sessionId) override;
    bool changeFileLocation(const std::string& sessionId, 
                           const std::string& newFilePath) override;

private:
    std::string m_basePath;
    bool m_initialized = false;
    
    // GStreamer pipeline management
    GstElement* m_pipeline = nullptr;
    GstElement* m_appsrc = nullptr;
    GstElement* m_filesink = nullptr;
    
    // Helper methods
    bool createPipeline(const std::string& filePath, int width, int height, 
                       int fps, const std::string& codec, bool enableAudio);
    bool destroyPipeline();
    bool changeSinkLocation(const std::string& newFilePath);
};
```

#### Cloud Writer Implementation
```cpp
class UnifiedCloudStorageWriter : public UnifiedStorageWriter {
public:
    bool configureStorage(const StorageConfig& config) override;
    bool isAvailable() const override;
    
    std::string startWrite(const std::string& filePath,
                          int width, int height, int fps,
                          const std::string& codec = "h264",
                          bool enableAudio = false) override;
    bool stopWrite(const std::string& sessionId) override;
    bool changeFileLocation(const std::string& sessionId, 
                           const std::string& newFilePath) override;

private:
    std::shared_ptr<CloudStorageBuffer> m_buffer;
    std::shared_ptr<CloudStorageWriter> m_cloudWriter;
    bool m_initialized = false;
    
    // Helper methods
    bool initializeCloudWriter(const StorageConfig& config);
    bool configureBuffer(const StorageConfig& config);
};
```

### 3. Manager Implementation

#### Base Class Structure
```cpp
class UnifiedStorageManager {
public:
    virtual ~UnifiedStorageManager() = default;
    
    // Configuration
    virtual bool configureStorage(const StorageConfig& config) = 0;
    virtual bool isAvailable() const = 0;
    
    // File operations
    virtual DeleteResult deleteFile(const std::string& path) = 0;
    virtual DeleteResult deleteDirectory(const std::string& path, bool recursive = false) = 0;
    virtual bool isFileExist(const std::string& path) const = 0;
    
    // Multi-file operations
    virtual MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths) = 0;
    virtual MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path,
                                                    const std::string& pattern = "",
                                                    bool recursive = false) = 0;
    
    // Statistics
    virtual StorageStats getManagerStats() const = 0;
    virtual void resetManagerStats() = 0;

protected:
    // Helper methods for derived classes
    void recordOperation(bool success, std::chrono::milliseconds duration, 
                        const std::string& errorCode = "");
    std::string getLastError() const;
    
private:
    StorageStats m_stats;
    std::string m_lastError;
    mutable std::mutex m_mutex;
};
```

#### Local Manager Implementation
```cpp
class UnifiedLocalStorageManager : public UnifiedStorageManager {
public:
    bool configureStorage(const StorageConfig& config) override;
    bool isAvailable() const override;
    
    DeleteResult deleteFile(const std::string& path) override;
    DeleteResult deleteDirectory(const std::string& path, bool recursive = false) override;
    bool isFileExist(const std::string& path) const override;
    
    MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths) override;
    MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path,
                                            const std::string& pattern = "",
                                            bool recursive = false) override;

private:
    std::string m_basePath;
    bool m_initialized = false;
    
    // Helper methods
    std::string getFullPath(const std::string& relativePath) const;
    bool isPathWithinBase(const std::string& path) const;
    size_t calculateDirectorySize(const std::string& path) const;
    std::vector<std::string> findFilesMatchingPattern(const std::string& directory,
                                                      const std::string& pattern,
                                                      bool recursive) const;
};
```

## Timing and Performance

### Timing Implementation

All operations include accurate timing measurements:

```cpp
// Example timing implementation in deleteFile
DeleteResult UnifiedLocalStorageManager::deleteFile(const std::string& path)
{
    DeleteResult result;
    auto start_time = std::chrono::steady_clock::now();
    
    try {
        // Perform the actual deletion
        std::filesystem::path file_path(path);
        result.deletedSize = std::filesystem::file_size(file_path);
        std::filesystem::remove(file_path);
        
        result.success = true;
        result.message = "File deleted successfully: " + path;
        result.deletedPath = path;
        
        // Calculate and assign duration
        auto end_time = std::chrono::steady_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        result.duration = duration;
        
        // Record operation for statistics
        recordOperation(true, duration);
        
        LOG(info) << "Successfully deleted file: " << path << " (" << result.deletedSize 
                  << " bytes) in " << duration.count() << "ms" << std::endl;
    }
    catch (const std::exception& e) {
        result.errorCode = ErrorCodes::EXCEPTION;
        result.message = "Exception: " + std::string(e.what());
        
        // Calculate duration even for failed operations
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        
        recordOperation(false, duration, result.errorCode);
    }
    
    return result;
}
```

### Performance Monitoring

Statistics are tracked automatically:

```cpp
void UnifiedStorageManager::recordOperation(bool success, std::chrono::milliseconds duration, 
                                           const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    
    m_stats.totalRequests++;
    m_stats.totalLatency += duration;
    m_stats.lastRequestTime = std::chrono::system_clock::now();
    
    if (success) {
        m_stats.successfulRequests++;
    } else {
        m_stats.failedRequests++;
        if (!errorCode.empty()) {
            m_stats.errorCounts[errorCode]++;
        }
    }
    
    // Calculate average latency
    if (m_stats.totalRequests > 0) {
        m_stats.averageLatency = m_stats.totalLatency / m_stats.totalRequests;
    }
}
```

## Error Handling Implementation

### Error Code System

```cpp
namespace ErrorCodes {
    const std::string FILE_NOT_FOUND = "FILE_NOT_FOUND";
    const std::string PERMISSION_DENIED = "PERMISSION_DENIED";
    const std::string NOT_INITIALIZED = "NOT_INITIALIZED";
    const std::string INVALID_CONFIGURATION = "INVALID_CONFIGURATION";
    const std::string NETWORK_ERROR = "NETWORK_ERROR";
    const std::string TIMEOUT = "TIMEOUT";
    const std::string BUCKET_NOT_FOUND = "BUCKET_NOT_FOUND";
    const std::string BUCKET_ALREADY_EXISTS = "BUCKET_ALREADY_EXISTS";
    const std::string BUCKET_NOT_EMPTY = "BUCKET_NOT_EMPTY";
    const std::string EXCEPTION = "EXCEPTION";
}
```

### Error Handling Pattern

```cpp
// Consistent error handling pattern
DeleteResult result;
auto start_time = std::chrono::steady_clock::now();

try {
    // Validate input
    if (!isAvailable()) {
        result.errorCode = ErrorCodes::NOT_INITIALIZED;
        result.message = "Manager not initialized";
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        recordOperation(false, duration, result.errorCode);
        return result;
    }
    
    // Validate path
    if (!isPathWithinBase(path)) {
        result.errorCode = ErrorCodes::PERMISSION_DENIED;
        result.message = "Path outside base directory: " + path;
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_time);
        result.duration = duration;
        recordOperation(false, duration, result.errorCode);
        return result;
    }
    
    // Perform operation
    // ... actual operation code ...
    
    // Success case
    result.success = true;
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    result.duration = duration;
    recordOperation(true, duration);
    
} catch (const std::filesystem::filesystem_error& e) {
    result.errorCode = ErrorCodes::EXCEPTION;
    result.message = "Filesystem error: " + std::string(e.what());
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    result.duration = duration;
    recordOperation(false, duration, result.errorCode);
} catch (const std::exception& e) {
    result.errorCode = ErrorCodes::EXCEPTION;
    result.message = "Exception: " + std::string(e.what());
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start_time);
    result.duration = duration;
    recordOperation(false, duration, result.errorCode);
}

return result;
```

## Thread Safety

### Mutex Protection

All components use mutex protection for thread safety:

```cpp
class UnifiedStorageManager {
private:
    mutable std::mutex m_mutex;
    StorageStats m_stats;
    std::string m_lastError;
    
public:
    void recordOperation(bool success, std::chrono::milliseconds duration, 
                        const std::string& errorCode = "")
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        // Update statistics safely
    }
    
    StorageStats getManagerStats() const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return m_stats;
    }
    
    std::string getLastError() const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        return m_lastError;
    }
};
```

### Session Management (Writers)

Writers use thread-safe session management:

```cpp
class UnifiedStorageWriter {
private:
    std::map<std::string, SessionInfo> m_sessions;
    mutable std::mutex m_mutex;
    
public:
    std::string startWrite(const std::string& filePath, ...)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        
        std::string sessionId = generateSessionId();
        SessionInfo session;
        session.filePath = filePath;
        session.startTime = std::chrono::system_clock::now();
        session.active = true;
        
        m_sessions[sessionId] = session;
        return sessionId;
    }
    
    bool stopWrite(const std::string& sessionId)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        
        auto it = m_sessions.find(sessionId);
        if (it != m_sessions.end()) {
            it->second.active = false;
            it->second.endTime = std::chrono::system_clock::now();
            return true;
        }
        return false;
    }
};
```

## Testing and Validation

### Unit Testing

Each component should include comprehensive unit tests:

```cpp
// Example test for UnifiedLocalStorageManager
TEST(UnifiedLocalStorageManagerTest, DeleteFileSuccess)
{
    // Setup
    auto manager = std::make_unique<UnifiedLocalStorageManager>();
    StorageConfig config;
    config.setParameter("base_path", "/tmp/test_storage");
    manager->configureStorage(config);
    
    // Create test file
    std::string testFile = "/tmp/test_storage/test_file.txt";
    std::ofstream file(testFile);
    file << "test content";
    file.close();
    
    // Test deletion
    DeleteResult result = manager->deleteFile(testFile);
    
    // Verify results
    EXPECT_TRUE(result.success);
    EXPECT_GT(result.deletedSize, 0);
    EXPECT_GT(result.duration.count(), 0);
    EXPECT_FALSE(std::filesystem::exists(testFile));
}

TEST(UnifiedLocalStorageManagerTest, DeleteFileNotFound)
{
    // Setup
    auto manager = std::make_unique<UnifiedLocalStorageManager>();
    StorageConfig config;
    config.setParameter("base_path", "/tmp/test_storage");
    manager->configureStorage(config);
    
    // Test deletion of non-existent file
    DeleteResult result = manager->deleteFile("/tmp/test_storage/nonexistent.txt");
    
    // Verify results
    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.errorCode, ErrorCodes::FILE_NOT_FOUND);
    EXPECT_GT(result.duration.count(), 0);
}
```

### Performance Testing

```cpp
TEST(UnifiedLocalStorageManagerTest, PerformanceBenchmark)
{
    // Setup
    auto manager = std::make_unique<UnifiedLocalStorageManager>();
    StorageConfig config;
    config.setParameter("base_path", "/tmp/test_storage");
    manager->configureStorage(config);
    
    // Create multiple test files
    std::vector<std::string> testFiles;
    for (int i = 0; i < 100; ++i) {
        std::string testFile = "/tmp/test_storage/test_file_" + std::to_string(i) + ".txt";
        std::ofstream file(testFile);
        file << "test content " << i;
        file.close();
        testFiles.push_back(testFile);
    }
    
    // Test batch deletion
    auto start = std::chrono::steady_clock::now();
    MultiDeleteResult result = manager->deleteMultipleFiles(testFiles);
    auto end = std::chrono::steady_clock::now();
    
    // Verify results
    EXPECT_TRUE(result.overall_success);
    EXPECT_EQ(result.successful_deletes, 100);
    EXPECT_EQ(result.failed_deletes, 0);
    EXPECT_GT(result.total_bytes_freed, 0);
    EXPECT_GT(result.total_duration.count(), 0);
    
    // Performance assertion
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
    EXPECT_LT(duration.count(), 1000); // Should complete within 1 second
}
```

## Integration with VMS

### Storage Management Integration

The unified storage framework integrates with VMS storage management:

```cpp
// In storage_management.cpp
bool StorageManagement::initUnifiedStorageManager()
{
    std::lock_guard<std::mutex> lock(m_unifiedStorageMutex);

    try {
        const nv_vms::DeviceConfig& config = GET_CONFIG();
        m_unifiedStorageManager = nv_vms::UnifiedStorageManagerUtils::initializeStorageManager(config);
        
        if (!m_unifiedStorageManager) {
            LOG(error) << "Failed to create unified storage manager: " 
                       << nv_vms::UnifiedStorageManagerUtils::getLastError() << std::endl;
            return false;
        }

        LOG(info) << "Unified storage manager initialized successfully" << std::endl;
        return true;
    }
    catch (const std::exception& e) {
        LOG(error) << "Exception during unified storage manager initialization: " << e.what() << std::endl;
        return false;
    }
}

DeleteResult StorageManagement::deleteFileWithStatus(const string& filePath, const string& objectId)
{
    std::lock_guard<std::mutex> lock(m_unifiedStorageMutex);
    
    if (!m_unifiedStorageManager) {
        LOG(error) << "Unified storage manager not initialized" << std::endl;
        DeleteResult result(false, "Unified storage manager not initialized");
        result.errorCode = "NOT_INITIALIZED";
        return result;
    }
    
    try {
        string pathToDelete = objectId.empty() ? filePath : objectId;
        nv_vms::DeleteResult result = nv_vms::UnifiedStorageManagerUtils::deleteFile(
            m_unifiedStorageManager, pathToDelete);
        
        if (result.success) {
            LOG(info) << "Successfully deleted file using unified storage: " << pathToDelete 
                      << " (" << result.deletedSize << " bytes) in " << result.duration.count() << "ms" << std::endl;
        } else {
            LOG(error) << "Failed to delete file using unified storage: " << pathToDelete 
                       << " - " << result.message << " (Error: " << result.errorCode << ")" << std::endl;
        }
        
        return result;
    }
    catch (const std::exception& e) {
        std::string errorMsg = "Exception during unified storage file deletion: " + std::string(e.what());
        LOG(error) << errorMsg << std::endl;
        DeleteResult result(false, errorMsg);
        result.errorCode = "EXCEPTION";
        return result;
    }
}
```

### API Response Enhancement

File deletion APIs now return both MB and bytes:

```cpp
// In deleteFilesByTime API
VmsErrorCode StorageManagement::deleteFilesByTime(const Json::Value& req_info, Json::Value &response)
{
    // ... existing code ...
    
    if(deleteFilesByTime(stream_id, startTime, endTime, spaceSaved) != 0)
    {
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        LOG(error) << "Unable to delete video files or database entries" << std::endl;
        return VmsErrorCode::VMSInternalError;
    }
    
    LOG(info) << "Space saved: " << spaceSaved << " MB" << std::endl;
    response["spaceSaved"] = spaceSaved;
    response["bytesDeleted"] = to_bytes(spaceSaved);  // New field
    return VmsErrorCode::NoError;
}

// In deleteFilesByNames API
VmsErrorCode StorageManagement::deleteFilesByNames(const Json::Value& req_info, Json::Value &response)
{
    // ... existing code ...
    
    response["spaceSaved"] = to_MB(spaceSaved);
    response["bytesDeleted"] = spaceSaved;  // New field
    
    return VmsErrorCode::NoError;
}
```

## Troubleshooting

### Common Issues

1. **Timing Shows 0ms**
   - **Cause**: Duration not assigned to result structure
   - **Solution**: Ensure `result.duration = duration;` is called in all code paths

2. **Thread Safety Issues**
   - **Cause**: Missing mutex protection
   - **Solution**: Use `std::lock_guard<std::mutex>` for all shared data access

3. **Memory Leaks**
   - **Cause**: Improper cleanup of GStreamer pipelines
   - **Solution**: Ensure proper destruction in destructors

4. **Performance Issues**
   - **Cause**: Inefficient file operations
   - **Solution**: Use batch operations and optimize I/O patterns

### Debugging Tips

1. **Enable Verbose Logging**
   ```cpp
   LOG(verbose) << "Operation details: " << details << std::endl;
   ```

2. **Monitor Statistics**
   ```cpp
   StorageStats stats = manager->getManagerStats();
   LOG(info) << "Success rate: " << (stats.successfulRequests * 100.0 / stats.totalRequests) << "%" << std::endl;
   ```

3. **Check Error Counts**
   ```cpp
   for (const auto& error : stats.errorCounts) {
       LOG(warning) << "Error " << error.first << " occurred " << error.second << " times" << std::endl;
   }
   ```

### Performance Optimization

1. **Buffer Sizing**
   ```cpp
   config.setParameter("buffer_size_mb", "200");  // Increase for better performance
   ```

2. **Batch Operations**
   ```cpp
   // Use batch deletion instead of individual deletions
   MultiDeleteResult result = manager->deleteMultipleFiles(fileList);
   ```

3. **Connection Pooling**
   ```cpp
   // Reuse manager instances
   static auto manager = UnifiedStorageManagerFactory::createManager("cloud");
   ```

This implementation guide provides the foundation for building robust, performant, and maintainable unified storage components. 
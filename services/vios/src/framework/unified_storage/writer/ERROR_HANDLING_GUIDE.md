# Cloud Storage Error Handling Guide

This document outlines the comprehensive error handling mechanisms implemented in the VMS cloud storage integration to ensure robust video recording operations.

## Error Categories and Handling

### 1. Storage Initialization Errors

**Error Conditions:**
- Storage integration not initialized
- Invalid storage configuration
- Authentication failures during setup
- Network connectivity issues during initialization

**Handling:**
- Configuration validation before initialization
- Detailed error logging with specific failure reasons
- Graceful fallback to local storage if cloud initialization fails
- Retry mechanism for transient initialization failures

**Implementation:**
```cpp
// In GstMux::initializeCloudStorage()
bool success = m_storageIntegration->initializeStorage(storage_type, config);
if (!success) {
    LOG(error) << "Failed to initialize cloud storage, falling back to local";
    // Fallback handled automatically by isUsingLocalStorage() check
}
```

### 2. Upload Session Creation Errors

**Error Conditions:**
- Cloud storage not available when starting recording
- Failed to create upload session (auth, quota, permissions)
- Network timeout during session creation
- Invalid remote path generation

**Handling:**
- Storage availability check with recovery attempts
- Retry mechanism for session creation (up to 2 attempts)
- Exponential backoff between retry attempts
- Detailed error logging with session information

**Implementation:**
```cpp
// In GstMux::createNewFileAndSetPlayState()
const int MAX_START_ATTEMPTS = 2;
for (int attempt = 1; attempt <= MAX_START_ATTEMPTS; ++attempt) {
    m_currentUploadSession = m_storageIntegration->startRecordingUpload(...);
    if (!m_currentUploadSession.empty()) break;
    // Retry with delay
}
```

### 3. Data Writing Errors

**Error Conditions:**
- Network interruptions during streaming upload
- Authentication token expiration mid-stream
- Storage quota exhaustion
- Temporary service unavailability
- Data corruption during transmission

**Handling:**
- Frame-level retry mechanism (up to 2 attempts per frame)
- Session validation before each write operation
- Automatic session cleanup on persistent failures
- Graceful error recovery with database updates
- Preserve recording metadata even if upload fails

**Implementation:**
```cpp
// In GstMux::writeFrameToCloudWithRetry()
for (int attempt = 1; attempt <= MAX_RETRY_ATTEMPTS; ++attempt) {
    if (m_storageIntegration->writeRecordingData(session, data, size)) {
        return true; // Success
    }
    // Check storage availability and retry
}
```

### 4. Upload Completion Errors

**Error Conditions:**
- Network failure during upload finalization
- Server-side processing errors
- Invalid session state at completion
- Metadata synchronization failures

**Handling:**
- Multiple completion attempts with increasing delays
- Terminal error detection (don't retry auth/permission failures)
- Comprehensive result validation
- Database update with appropriate status
- Session cleanup regardless of completion success

**Implementation:**
```cpp
// In GstMux::completeCloudUploadWithRetry()
for (int attempt = 1; attempt <= MAX_RETRY_ATTEMPTS; ++attempt) {
    result = m_storageIntegration->completeRecordingUpload(...);
    if (result.success) break;
    
    // Don't retry terminal errors
    if (isTerminalError(result.message)) break;
    
    sleep(RETRY_DELAY_MS * attempt); // Exponential backoff
}
```

### 5. Storage Availability Errors

**Error Conditions:**
- Network connectivity loss
- Cloud service outages
- DNS resolution failures
- Firewall/proxy blocking

**Handling:**
- Continuous availability monitoring
- Automatic reconnection attempts
- Service recovery detection
- Transparent switching between storage modes
- Preservation of recording continuity

**Implementation:**
```cpp
// In GstMux::validateCloudStorageSession()
if (!m_storageIntegration->isStorageAvailable()) {
    LOG(error) << "Cloud storage not available";
    return false;
}
```

### 6. Authentication and Authorization Errors

**Error Conditions:**
- Expired access tokens
- Insufficient permissions
- API key revocation
- Account suspension

**Handling:**
- Terminal error detection (no retry for auth failures)
- Clear error messages for troubleshooting
- Automatic fallback to local storage
- Database recording of authentication failures

**Implementation:**
```cpp
// Terminal error detection
if (result.message.find("authentication") != std::string::npos ||
    result.message.find("permission") != std::string::npos) {
    LOG(error) << "Terminal error detected, not retrying";
    break;
}
```

### 7. Resource Management Errors

**Error Conditions:**
- Memory allocation failures
- File handle exhaustion
- Storage quota exceeded
- Disk space limitations (for local fallback)

**Handling:**
- Resource cleanup on all error paths
- Session cancellation for failed uploads
- Memory management in retry loops
- Graceful degradation of service

**Implementation:**
```cpp
// In error paths
if (!cloudWriteSuccess) {
    m_storageIntegration->cancelRecordingUpload(m_currentUploadSession, m_deviceId);
    m_currentUploadSession.clear();
    // Handle failure gracefully
}
```

### 8. Recovery and Fallback Mechanisms

**Error Conditions:**
- Persistent cloud storage failures
- Irrecoverable network issues
- Service maintenance windows
- Configuration errors

**Handling:**
- Multiple recovery strategies:
  1. Retry with exponential backoff
  2. Storage reinitialization attempts
  3. Database preservation of failed uploads
  4. Graceful error state handling
- No data loss - metadata preserved even on upload failure
- Automatic recovery when conditions improve

**Implementation:**
```cpp
// In GstMux::handleCloudStorageFailure()
// Strategy 1: Try to recover storage
if (!m_storageIntegration->isStorageAvailable()) {
    sleep(2000); // Wait for recovery
    if (m_storageIntegration->isStorageAvailable()) {
        return true; // Recovered successfully
    }
}

// Strategy 2: Preserve metadata with failure indication
dbRow.filepath_value = m_cloudRemotePath + " [UPLOAD_FAILED]";
VideoRecordUpdater::getInstance().addToQueue(dbRow);
```

## Error States and Transitions

### Normal Operation
```
Initialize → Create Session → Write Data → Complete Upload → Update DB
```

### Error Recovery Paths
```
Initialize Failed → Log Error → Continue with Local Storage
Session Failed → Retry → Log Error → Set Error State
Write Failed → Retry → Cancel Session → Handle Failure → Recover
Complete Failed → Retry → Handle Failure → Update DB
```

## Monitoring and Diagnostics

### Error Logging
- **INFO**: Successful operations and recovery
- **WARNING**: Retry attempts and temporary failures
- **ERROR**: Persistent failures and terminal errors
- **VERBOSE**: Detailed operation status for debugging

### Error Metrics
- Upload success/failure rates
- Retry attempt frequencies
- Recovery success rates
- Session duration and data volumes

### Debugging Information
- Session IDs for tracing failed uploads
- Detailed error messages with context
- Timestamps for failure correlation
- Storage type and configuration details

## Configuration Recommendations

### Retry Settings
```cpp
const int MAX_RETRY_ATTEMPTS = 3;    // Upload completion retries
const int MAX_START_ATTEMPTS = 2;    // Session creation retries
const int RETRY_DELAY_MS = 1000;     // Base retry delay
```

### Timeout Settings
- Connection timeout: 30 seconds
- Upload timeout: Based on data size
- Completion timeout: 10 seconds

### Storage Configuration
- Always configure fallback to local storage
- Set appropriate storage quotas
- Configure authentication with sufficient permissions
- Monitor storage usage and costs

## Best Practices

1. **Always handle errors gracefully** - Never lose recording data
2. **Use appropriate retry mechanisms** - Don't retry terminal errors
3. **Log comprehensive error information** - Include context for debugging
4. **Clean up resources on all paths** - Prevent resource leaks
5. **Monitor error rates** - Alert on persistent failures
6. **Test error scenarios** - Validate error handling under various conditions
7. **Document error codes** - Provide clear troubleshooting guidance

## Testing Error Conditions

### Network Simulation
- Disconnect network during upload
- Simulate slow/unstable connections
- Test DNS resolution failures

### Service Simulation
- Mock authentication failures
- Simulate storage quota exceeded
- Test service unavailability

### Resource Simulation
- Limit memory availability
- Test disk space exhaustion
- Simulate high CPU usage

This comprehensive error handling ensures that the VMS system maintains recording continuity and data integrity even under adverse conditions, providing a robust and reliable video management solution. 
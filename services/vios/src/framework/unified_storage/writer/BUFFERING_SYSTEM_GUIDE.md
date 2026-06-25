# Cloud Storage Buffering System Guide

## Problem Statement

When recording video at 30 FPS to cloud storage, network bandwidth and cloud API limitations can cause bottlenecks that result in:

- **Frame drops**: Main recording pipeline blocked waiting for slow uploads
- **Pipeline stalls**: GStreamer pipeline freezes when cloud writes are slow  
- **Recording gaps**: Lost frames due to synchronous upload blocking
- **Memory issues**: Unbounded frame accumulation when network is slow
- **Inconsistent performance**: Recording quality varies with network conditions

## Solution: Asynchronous Buffering System

The `CloudStorageBuffer` provides a decoupled, asynchronous upload system that:

1. **Decouples recording from uploading**: Main pipeline never waits for cloud operations
2. **Provides frame buffering**: Intelligent queue management with configurable limits
3. **Implements rate limiting**: Prevents overwhelming cloud APIs or network
4. **Offers graceful degradation**: Drops frames intelligently when buffer fills
5. **Enables monitoring**: Comprehensive statistics and health monitoring

## Architecture Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   30 FPS Video  │───▶│ CloudStorage    │───▶│   Upload        │
│   Recording     │    │ Buffer          │    │   Worker        │
│   Pipeline      │    │ (Queue)         │    │   Thread        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                ▲                        │
                                │                        ▼
                        ┌─────────────────┐    ┌─────────────────┐
                        │ Buffer          │    │ Cloud Storage   │
                        │ Management      │    │ API (S3, etc.)  │
                        │ & Rate Limiting │    │                 │
                        └─────────────────┘    └─────────────────┘
```

## Key Components

### 1. CloudStorageBuffer

**Purpose**: Asynchronous frame buffering and upload management

**Features**:
- Configurable buffer size (MB and frame count limits)
- Thread-safe frame queuing
- Automatic buffer overflow protection
- Comprehensive statistics tracking
- Graceful shutdown with flush capability

### 2. UploadRateLimiter

**Purpose**: Prevent overwhelming cloud storage APIs

**Features**:
- Configurable max upload rate (default: 25 FPS, slightly below 30 FPS input)
- Dynamic rate calculation and monitoring
- Intelligent frame pacing to match network capacity

### 3. BufferedFrame Structure

**Purpose**: Encapsulate frame data with metadata

**Contents**:
- Frame data (video/audio bytes)
- Timestamp for ordering and delay calculation
- Media type identification
- Session ID for multi-stream tracking

## Usage Examples

### Basic Setup

```cpp
// Initialize cloud storage with buffering
GstMux mux(RecordState::Continuous);

// Configure cloud storage
StorageConfig config;
config.storage_type = "s3";
config.bucket_name = "my-video-bucket";
config.region = "us-west-2";

// Initialize with 50MB buffer, max 1500 frames
bool success = mux.initializeCloudStorage("s3", config);
if (!success) {
    LOG(error) << "Failed to initialize cloud storage" << endl;
    return -1;
}

// Start recording - buffering starts automatically
mux.play();
```

### Advanced Configuration

```cpp
// Custom buffer configuration for high-throughput scenarios
size_t buffer_size_mb = 100;  // 100MB buffer for high bitrate streams
size_t max_frames = 3000;     // Support up to 3000 buffered frames (100 seconds at 30fps)

bool buffering_started = mux.startCloudBuffering(buffer_size_mb, max_frames);
if (!buffering_started) {
    LOG(error) << "Failed to start cloud buffering" << endl;
}

// Monitor buffer health during recording
while (recording) {
    auto stats = mux.getBufferStats();
    double utilization = mux.getBufferUtilization();
    
    LOG(info) << "Buffer status: " 
              << "utilization=" << utilization << "%, "
              << "upload_rate=" << stats.avg_upload_rate_fps << "fps, "
              << "buffer_delay=" << stats.avg_buffer_delay.count() << "ms" << endl;
    
    if (utilization > 90.0) {
        LOG(warning) << "High buffer utilization detected!" << endl;
    }
    
    sleep(10); // Check every 10 seconds
}
```

### Handling Network Issues

```cpp
// Monitor for network problems
void monitorNetworkHealth(GstMux& mux) {
    auto stats = mux.getBufferStats();
    
    // Check for dropped frames (network too slow)
    if (stats.frames_dropped > 0) {
        LOG(warning) << "Network performance issue detected: " 
                     << stats.frames_dropped << " frames dropped" << endl;
        
        // Consider reducing recording quality or increasing buffer size
        // mux.stopCloudBuffering();
        // mux.startCloudBuffering(200, 6000); // Double buffer size
    }
    
    // Check upload rate vs input rate
    double upload_efficiency = stats.avg_upload_rate_fps / 30.0 * 100.0;
    if (upload_efficiency < 80.0) {
        LOG(warning) << "Upload efficiency low: " << upload_efficiency << "%" << endl;
    }
    
    // Check buffer delay
    if (stats.avg_buffer_delay.count() > 5000) { // 5 seconds
        LOG(warning) << "High buffer delay: " << stats.avg_buffer_delay.count() << "ms" << endl;
    }
}
```

## Performance Characteristics

### Buffer Sizing Guidelines

| Scenario | Buffer Size | Max Frames | Rationale |
|----------|-------------|------------|-----------|
| **Low Bitrate (1-2 Mbps)** | 25MB | 1000 | ~100 seconds at 30fps |
| **Medium Bitrate (5-8 Mbps)** | 50MB | 1500 | ~50 seconds at 30fps |
| **High Bitrate (15+ Mbps)** | 100MB | 3000 | ~100 seconds at 30fps |
| **Network Unstable** | 200MB | 6000 | ~200 seconds buffer |

### Rate Limiting Configuration

```cpp
// Conservative: For limited bandwidth
UploadRateLimiter limiter(20.0); // 20 FPS max upload

// Standard: Slightly below input rate
UploadRateLimiter limiter(25.0); // 25 FPS max upload (default)

// Aggressive: For high bandwidth
UploadRateLimiter limiter(30.0); // Match input rate
```

## Monitoring and Alerting

### Key Metrics to Monitor

1. **Buffer Utilization**: Should stay below 80% normally
   ```cpp
   double utilization = mux.getBufferUtilization();
   if (utilization > 80.0) {
       // Alert: Buffer getting full
   }
   ```

2. **Upload Rate**: Should approximate input rate
   ```cpp
   auto stats = mux.getBufferStats();
   if (stats.avg_upload_rate_fps < 25.0) {
       // Alert: Upload lagging behind
   }
   ```

3. **Frame Drops**: Should be minimal
   ```cpp
   if (stats.frames_dropped > 0) {
       // Alert: Network performance issue
   }
   ```

4. **Buffer Delay**: Indicates how far behind uploads are
   ```cpp
   if (stats.avg_buffer_delay.count() > 3000) {
       // Alert: High latency detected
   }
   ```

### Health Check Implementation

```cpp
class BufferHealthMonitor {
public:
    struct HealthStatus {
        bool healthy = true;
        std::string message;
        double score = 100.0; // 0-100 health score
    };
    
    HealthStatus checkHealth(const GstMux& mux) {
        HealthStatus status;
        auto stats = mux.getBufferStats();
        double utilization = mux.getBufferUtilization();
        
        // Calculate health score
        double util_score = std::max(0.0, 100.0 - utilization);
        double rate_score = std::min(100.0, (stats.avg_upload_rate_fps / 30.0) * 100.0);
        double drop_score = std::max(0.0, 100.0 - (stats.frames_dropped * 10.0));
        
        status.score = (util_score + rate_score + drop_score) / 3.0;
        
        if (status.score < 70.0) {
            status.healthy = false;
            status.message = "Poor network performance detected";
        }
        
        return status;
    }
};
```

## Troubleshooting Common Issues

### 1. High Buffer Utilization

**Symptoms**: Buffer utilization consistently > 90%
**Causes**: 
- Network bandwidth insufficient
- Cloud storage API rate limits
- High video bitrate

**Solutions**:
```cpp
// Increase buffer size
mux.stopCloudBuffering();
mux.startCloudBuffering(200, 6000); // Double buffer

// Or reduce recording quality
// videoEncoder.setBitrate(lower_bitrate);
```

### 2. Frequent Frame Drops

**Symptoms**: `frames_dropped` increasing rapidly
**Causes**:
- Severe network congestion
- Cloud storage service issues
- Insufficient buffer size

**Solutions**:
```cpp
// Monitor and adjust dynamically
if (stats.frames_dropped > 100) {
    LOG(warning) << "Switching to local storage fallback" << endl;
    // Implement fallback to local storage
}
```

### 3. High Upload Delay

**Symptoms**: `avg_buffer_delay` > 5 seconds
**Causes**:
- Network latency issues
- Upload rate too conservative
- Cloud storage regional issues

**Solutions**:
```cpp
// Adjust rate limiter
UploadRateLimiter faster_limiter(28.0); // Increase upload rate

// Or check network configuration
// - DNS resolution speed
// - Cloud storage region selection
// - Network proxy settings
```

## Integration Best Practices

### 1. Initialization Sequence

```cpp
// Correct initialization order
GstMux mux(RecordState::Continuous);
mux.initializeCloudStorage("s3", config);  // 1. Initialize storage
mux.create(stream, errorQueue);              // 2. Create pipeline  
// Buffering starts automatically in createNewFileAndSetPlayState()
mux.play();                                  // 3. Start recording
```

### 2. Graceful Shutdown

```cpp
// Proper cleanup sequence
mux.flushCloudBuffer();    // 1. Flush remaining frames
auto final_stats = mux.getBufferStats();
LOG(info) << "Final upload stats: " << final_stats.frames_uploaded 
          << " frames uploaded, " << final_stats.frames_dropped << " dropped";
mux.destroy();             // 2. Cleanup (stops buffering automatically)
```

### 3. Error Recovery

```cpp
// Handle buffer overflow gracefully
void handleBufferOverflow(GstMux& mux) {
    LOG(warning) << "Buffer overflow detected, implementing recovery";
    
    // Strategy 1: Increase buffer size
    mux.stopCloudBuffering();
    mux.startCloudBuffering(mux.getCurrentBufferSize() * 2, 
                           mux.getCurrentMaxFrames() * 2);
    
    // Strategy 2: Or switch to local storage temporarily
    // mux.switchToLocalStorage();
}
```

This buffering system ensures that your 30 FPS video recording pipeline remains smooth and uninterrupted, regardless of network conditions or cloud storage performance variations. The asynchronous design prevents the main recording thread from being blocked by slow cloud operations, while intelligent buffer management ensures efficient memory usage and graceful degradation under adverse conditions. 
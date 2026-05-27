# MinIO Cloud Storage Integration

This document describes the MinIO cloud storage integration for the unified storage reader framework.

## Overview

The MinIO integration provides S3-compatible cloud storage access through the unified storage reader interface. MinIO is an open-source object storage server that is compatible with Amazon S3 APIs, making it ideal for local development, testing, and production deployments.

## Features

- **S3-Compatible API**: Full compatibility with Amazon S3 APIs
- **Unified Interface**: Seamless integration with the unified storage reader framework
- **Authentication**: Support for access key/secret key authentication
- **SSL/TLS Support**: Configurable SSL/TLS encryption
- **Performance Monitoring**: Built-in statistics and health monitoring
- **Error Handling**: Comprehensive error handling and retry logic

## Configuration

### Basic Configuration

```cpp
StorageConfig minioConfig;
minioConfig.storage_type = "cloud";
minioConfig.setParameter("cloud_type", "minio");
minioConfig.setParameter("endpoint", "localhost:9000");
minioConfig.setParameter("access_key", "minioadmin");
minioConfig.setParameter("secret_key", "minioadmin");
minioConfig.setParameter("region", "us-east-1");
minioConfig.setParameter("use_ssl", "false");
minioConfig.setParameter("timeout_seconds", "30");
minioConfig.setParameter("max_retries", "3");
```

### Configuration Parameters

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `cloud_type` | Must be "minio" | Yes | - |
| `endpoint` | MinIO server endpoint (host:port) | Yes | - |
| `access_key` | MinIO access key | Yes | - |
| `secret_key` | MinIO secret key | Yes | - |
| `region` | MinIO region | No | "us-east-1" |
| `use_ssl` | Enable SSL/TLS encryption | No | "true" |
| `timeout_seconds` | Request timeout in seconds | No | "30" |
| `max_retries` | Maximum retry attempts | No | "3" |

## Usage

### Creating a MinIO Reader

```cpp
#include "unified_storage_reader_factory.h"

// Create MinIO configuration
StorageConfig config;
config.storage_type = "cloud";
config.setParameter("cloud_type", "minio");
config.setParameter("endpoint", "localhost:9000");
config.setParameter("access_key", "minioadmin");
config.setParameter("secret_key", "minioadmin");

// Create reader
auto reader = UnifiedStorageReaderFactory::createReader("cloud", config);
if (!reader) {
    // Handle error
}
```

### Basic Operations

```cpp
// Check availability
if (!reader->isAvailable()) {
    std::cerr << "MinIO not available: " << reader->getLastError() << std::endl;
}

// List buckets
std::vector<std::string> buckets;
auto result = reader->listBuckets(buckets);
if (result.success) {
    for (const auto& bucket : buckets) {
        std::cout << "Bucket: " << bucket << std::endl;
    }
}

// List objects in a bucket
std::string bucketPath = "mybucket/";
auto listResult = reader->listFiles(bucketPath);
if (listResult.success) {
    for (const auto& file : listResult.files) {
        std::cout << "File: " << file.name << " (size: " << file.size << ")" << std::endl;
    }
}

// Download a file
std::string remotePath = "mybucket/myfile.txt";
std::string localPath = "/tmp/myfile.txt";
auto downloadResult = reader->downloadFile(remotePath, localPath);
if (downloadResult.success) {
    std::cout << "File downloaded successfully" << std::endl;
}

// Get file information
FileInfo fileInfo;
auto infoResult = reader->getFileInfo(remotePath, fileInfo);
if (infoResult.success) {
    std::cout << "File size: " << fileInfo.size << std::endl;
    std::cout << "Last modified: " << fileInfo.lastModified << std::endl;
}

// Generate presigned URL
std::string presignedUrl;
auto urlResult = reader->generatePresignedUrl(remotePath, 3600, presignedUrl);
if (urlResult.success) {
    std::cout << "Presigned URL: " << presignedUrl << std::endl;
}
```

## MinIO Server Setup

### Using Docker

```bash
# Start MinIO server
docker run -p 9000:9000 -p 9001:9001 \
  --name minio \
  -e "MINIO_ROOT_USER=minioadmin" \
  -e "MINIO_ROOT_PASSWORD=minioadmin" \
  -v /mnt/data:/data \
  minio/minio server /data --console-address ":9001"
```

### Using MinIO Binary

```bash
# Download MinIO
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio

# Start MinIO server
./minio server /mnt/data --console-address ":9001"
```

### Access MinIO Console

Open your browser and navigate to `http://localhost:9001` to access the MinIO web console.

## Error Handling

The MinIO integration provides comprehensive error handling:

```cpp
auto result = reader->downloadFile(remotePath, localPath);
if (!result.success) {
    std::cerr << "Error: " << result.message << std::endl;
    std::cerr << "Error code: " << result.errorCode << std::endl;
}
```

Common error codes:
- `CLIENT_ERROR`: MinIO client not initialized
- `INVALID_PARAMETER`: Missing or invalid parameters
- `OBJECT_NOT_FOUND`: Object does not exist
- `BUCKET_NOT_FOUND`: Bucket does not exist
- `ACCESS_DENIED`: Authentication or authorization failure
- `NETWORK_ERROR`: Network connectivity issues

## Performance Monitoring

The MinIO reader provides built-in performance monitoring:

```cpp
auto stats = reader->getReaderStats();
std::cout << "Total requests: " << stats.totalRequests << std::endl;
std::cout << "Successful requests: " << stats.successfulRequests << std::endl;
std::cout << "Failed requests: " << stats.failedRequests << std::endl;
std::cout << "Bytes read: " << stats.bytesRead << std::endl;
std::cout << "Average latency: " << stats.averageLatency.count() << "ms" << std::endl;
```

## Health Checks

Perform health checks to verify MinIO connectivity:

```cpp
auto healthResult = reader->performHealthCheck();
if (healthResult.success) {
    std::cout << "MinIO is healthy" << std::endl;
} else {
    std::cerr << "MinIO health check failed: " << healthResult.message << std::endl;
}
```

## Security Considerations

1. **Access Keys**: Use strong, unique access keys for production
2. **SSL/TLS**: Enable SSL/TLS for production deployments
3. **Network Security**: Restrict network access to MinIO servers
4. **Bucket Policies**: Configure appropriate bucket policies
5. **IAM**: Use IAM policies for fine-grained access control

## Troubleshooting

### Common Issues

1. **Connection Refused**
   - Verify MinIO server is running
   - Check endpoint URL and port
   - Ensure firewall allows connections

2. **Authentication Failed**
   - Verify access key and secret key
   - Check MinIO server credentials
   - Ensure proper permissions

3. **SSL/TLS Errors**
   - Verify SSL configuration
   - Check certificate validity
   - Use `use_ssl=false` for local development

4. **Bucket Not Found**
   - Create bucket in MinIO console
   - Check bucket name spelling
   - Verify bucket permissions

### Debug Information

Enable debug logging to troubleshoot issues:

```cpp
// The reader automatically logs operations
// Check logs for detailed error information
```

## Example

See `minio_example_usage.cpp` for a complete example of using the MinIO integration.

## Dependencies

- MinIO C++ SDK (`libminiocpp`)
- OpenSSL
- cURL
- JSONCPP

## Build Configuration

The MinIO integration requires the following build configuration:

```makefile
# Include MinIO headers
CLOUD_READER_INCLUDES += -I $(TOP)/include/3rdparty/minio

# Link MinIO library
CLOUD_READER_LIBS += -lminiocpp
```

## License

This MinIO integration is part of the NVIDIA VMS framework and is subject to the same license terms. 
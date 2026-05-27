# ucf.svc.vst-minio

## Description

### Overview
MinIO is a high-performance, S3-compatible object storage service integrated into the VST (Video Storage and Transfer) system for testing and development purposes. It provides a lightweight, scalable storage solution that mimics Amazon S3 behavior while running locally in your environment.

### Purpose
- **Object Storage**: Enables VST's object storage capabilities without requiring external cloud storage services
- **S3 Compatibility**: Provides full S3 API compatibility for seamless integration with applications expecting S3-style storage
- **Video Storage**: Configured with a default "videos" bucket for storing video files and metadata
- **Development Environment**: Ideal for local development and testing scenarios

## Configuration

The MinIO service is configured with the following default settings:

| Setting | Value | Description |
|---------|-------|-------------|
| **API Port** | `9000` | S3-compatible API endpoint for programmatic access |
| **Console Port** | `9001` | Web-based administrative interface |
| **Root User** | `admin` | Default administrative username |
| **Root Password** | `nvidia123!` | Default administrative password |
| **Data Path** | `${VST_VOLUME}/minio/data` | Local storage path for MinIO data |

## Access Points

### MinIO Console
- **URL**: `http://<Host_IP>:9001`
- **Description**: Web-based GUI for bucket management, file uploads, and administration
- **Credentials**: `admin` / `nvidia123!`

### MinIO API
- **URL**: `http://<Host_IP>:9000`
- **Description**: S3-compatible REST API endpoint
- **Usage**: Compatible with AWS CLI, SDKs, or any S3-compatible client

## Usage with VST

To enable MinIO with VST:

1. **Configure VST**: Set `"enable_minio": true` in `vst_config.json`
2. **Start Services**: 
   ```bash
   sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile minio --profile monitoring up --force-recreate -d
   ```
3. **Access Storage**: Use the MinIO console or API to manage video storage

## Key Features

- **High Performance**: Optimized for large file storage and retrieval
- **S3 Compatibility**: Works with existing S3 tools and libraries
- **Scalable**: Supports distributed deployments for production use
- **Secure**: Configurable access policies and encryption
- **Monitoring**: Integrated with Prometheus metrics collection

## Environment Variables

The following environment variables are configured in `compose.env`:

```bash
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
MINIO_DATA_PATH=${VST_VOLUME}/minio/data
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=nvidia123!
MINIO_API_DELETE_CLEANUP_INTERVAL=20s
MINIO_API_STALE_UPLOADS_CLEANUP_INTERVAL=300s
MINIO_API_STALE_UPLOADS_EXPIRY=7d
```

## Notes

- The service uses host networking mode for optimal performance
- Cleanup intervals are configured to automatically manage stale uploads and deleted objects
- Default bucket name: `videos`
- For production deployments, consider implementing proper security measures, backup strategies, and monitoring

This MinIO integration provides a robust foundation for object storage testing while maintaining compatibility with cloud storage solutions.

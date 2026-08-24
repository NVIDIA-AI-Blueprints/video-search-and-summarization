# MinIO Integration for Mode 3 Testing

This guide explains how to use MinIO (S3-compatible storage) for testing Mode 3 (Direct Media URL) processing.

## Quick Start

- install minio

### 1. Start MinIO

```bash
cd alert_agent/test/test_lite/minio
docker compose up -d
```

This starts:
- **MinIO Server**: `http://localhost:9000` (S3 API)
- **MinIO Console**: `http://localhost:9001` (Web UI)
- Creates bucket `alert-media` with public read access

### 2. Upload Media & Send Kafka Payloads

```bash
cd alert_agent/test/test_lite/minio

# Upload and send each file as separate payload
python send_minio_media_payload.py /path/to/media_folder

# Upload and send all images as single multi-image payload (batch mode)
python send_minio_media_payload.py /path/to/media_folder --batch --images-only

# Upload only (no Kafka)
python send_minio_media_payload.py /path/to/media_folder --upload-only

# List files in bucket
python send_minio_media_payload.py /path/to/media_folder --list-bucket

# Custom MinIO host (e.g., remote server)
python send_minio_media_payload.py /path/to/media_folder --minio-host localhost
```

### 3. Access MinIO Console

Open browser: `http://localhost:9001`

Login:
- **Username**: `minioadmin`
- **Password**: `minioadmin123`

## Payload Format

### Single Image/Video

```json
{
  "info": {
    "media_urls": ["http://localhost:9000/alert-media/image.jpg"],
    "media_type": "image"
  }
}
```

### Multiple Images (Batch Mode)

```json
{
  "info": {
    "media_urls": [
      "http://localhost:9000/alert-media/image1.jpg",
      "http://localhost:9000/alert-media/image2.jpg",
      "http://localhost:9000/alert-media/image3.jpg"
    ],
    "media_type": "images"
  }
}
```

### Video

```json
{
  "info": {
    "media_urls": ["http://localhost:9000/alert-media/video.mp4"],
    "media_type": "video"
  }
}
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--batch` | Send all images as a single multi-image payload |
| `--images-only` | Only process image files (skip videos) |
| `--upload-only` | Upload to MinIO without sending Kafka payloads |
| `--list-bucket` | List files in MinIO bucket and exit |
| `--category` | Alert category for payload (default: "Barcode Scanner Module") |
| `--minio-host` | MinIO host (default: localhost) |
| `--minio-port` | MinIO port (default: 9000) |
| `--minio-bucket` | MinIO bucket (default: alert-media) |
| `--minio-access-key` | MinIO access key |
| `--minio-secret-key` | MinIO secret key |

## Configuration

### minio_config.yaml

MinIO settings are configured in `test/test_lite/minio/minio_config.yaml`:

```yaml
minio:
  host: "localhost"           # MinIO server host
  port: 9000                  # MinIO S3 API port
  access_key: "minioadmin"    # MinIO access key
  secret_key: "minioadmin123" # MinIO secret key
  bucket: "alert-media"       # Default bucket
  secure: false               # Use HTTPS
```

> **Note**: This config is separate from the main `config.yaml` since MinIO is only for testing.

### Alert Agent Config (config.yaml)

```yaml
alert_agent:
  media_download:
    enabled: true
    max_media_count: 5      # Max number of media URLs per request (default: 5)
    timeout_seconds: 30     # Download timeout per file
    max_size_mb: 50         # Max file size
    use_verdict: false      # Parse structured VLM response
    allow_private_urls: false  # SSRF protection
```

### Command Line Override

All MinIO settings can be overridden via command line:

```bash
python send_minio_media_payload.py /media \
    --minio-host localhost \
    --minio-port 9000 \
    --minio-bucket my-bucket \
    --minio-access-key mykey \
    --minio-secret-key mysecret
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TEST WORKFLOW                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. Upload Media                                                     │
│     /path/to/images/*.jpg ───► MinIO (alert-media bucket)           │
│                                                                      │
│  2. Send Kafka Payload                                               │
│     {                                                                │
│       "info": {                                                      │
│         "media_urls": [                                              │
│           "http://localhost:9000/alert-media/image1.jpg",           │
│           "http://localhost:9000/alert-media/image2.jpg"            │
│         ],                                                           │
│         "media_type": "images"                                       │
│       }                                                              │
│     }                                                                │
│           │                                                          │
│           ▼                                                          │
│     Kafka (mdx-incidents topic)                                      │
│           │                                                          │
│           ▼                                                          │
│  3. Alert Agent (Mode 3 - DirectMediaHandler)                       │
│     - Extracts media_urls from payload                              │
│     - Applies max_media_count limit (default: 5)                    │
│     - Downloads all images from MinIO                               │
│     - Sends ALL images to VLM in single request                     │
│     - Publishes result to Elastic/Kafka                             │
│                                                                      │
│  4. Response (in info field)                                         │
│     {                                                                │
│       "media_urls": [...],                                          │
│       "media_type": "images",                                        │
│       "images_processed": 2,                                         │
│       "images_total": 2,                                             │
│       "reasoning": "VLM analysis result..."                         │
│     }                                                                │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Examples

### Send Single Image

```bash
# Create a test folder with one image
mkdir -p /tmp/test_images
cp my_image.jpg /tmp/test_images/

# Send single image
python send_minio_media_payload.py /tmp/test_images
```

### Send Multiple Images (Batch)

```bash
# Create a test folder with multiple images
mkdir -p /tmp/test_images
cp image1.jpg image2.jpg image3.jpg /tmp/test_images/

# Send all images as single multi-image payload
python send_minio_media_payload.py /tmp/test_images --batch --images-only
```

### Send Video

```bash
mkdir -p /tmp/test_video
cp my_video.mp4 /tmp/test_video/

python send_minio_media_payload.py /tmp/test_video
```

## Docker Commands

```bash
cd alert_agent/test/test_lite/minio

# Start MinIO
docker compose up -d

# View logs
docker compose logs -f

# Stop MinIO
docker compose down

# Stop and remove data
docker compose down -v
```

## MinIO CLI (mc)

You can also use MinIO CLI directly:

```bash
# Install mc
wget https://dl.min.io/client/mc/release/linux-amd64/mc
chmod +x mc
sudo mv mc /usr/local/bin/

# Configure alias
mc alias set myminio http://localhost:9000 minioadmin minioadmin123

# Upload file
mc cp /path/to/video.mp4 myminio/alert-media/

# List files
mc ls myminio/alert-media/

# Get file URL
mc share download myminio/alert-media/video.mp4
```

## Troubleshooting

### Media count exceeds limit

If you see warning: `Media URLs count (N) exceeds limit (5), truncating`

This means you sent more than `max_media_count` images. Either:
- Increase `max_media_count` in `config.yaml`
- Send fewer images per request

### Download failed

Check:
1. MinIO is running: `docker compose ps`
2. URL is accessible: `curl http://localhost:9000/alert-media/image.jpg`
3. `allow_private_urls: true` in config if using private IPs

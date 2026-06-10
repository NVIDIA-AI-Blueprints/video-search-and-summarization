# VST BDD Test Suite

Comprehensive Behavior-Driven Development (BDD) test suite for the VST API, featuring organized test categories, parallel execution, and automated resource monitoring.

## Quick Start

### Option 1: Automated Setup (Recommended)

```bash
# Run the setup script (handle everything automatically)
./setup.sh

# The script will:
# - Check Python version (3.8+)
# - Install Poetry if needed
# - Install all system dependencies (ffmpeg, mediainfo, jpeginfo, libav, etc.)
# - Install Python dependencies
# - Create necessary directories
# - Verify installation

# Then configure and run
# 1. Edit config.json and set api.base_url
# 2. Run tests
poetry run pytest tests/
```

### Option 2: Manual Setup

```bash
# 1. Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# 2. Install Python dependencies
poetry install

# 3. Install system dependencies
poetry run setup-system-deps
# OR manually: sudo apt-get install ffmpeg mediainfo jpeginfo libavformat-dev ...

# 4. Configure your VST API endpoint
# Edit config.json and set api.base_url

# 5. Run tests
poetry run pytest tests/
```

## Project Structure

```
bdd_tests/
├── config.json                    # Unified test configuration
├── conftest.py                    # Root pytest configuration & hooks
├── pyproject.toml                 # Poetry dependencies
│
├── features/                      # BDD Feature files (Gherkin)
│   ├── file_upload/              # 9 upload features
│   │   ├── concurrent_upload_race.feature
│   │   ├── file_extension_conflicts.feature
│   │   ├── multipart_file_upload.feature
│   │   ├── post_bframe_upload.feature
│   │   ├── put_bframe_upload.feature
│   │   ├── sensor_id_handling.feature
│   │   ├── special_filenames.feature        # special chars, path traversal, oversize sensor name
│   │   ├── timestamp_handling.feature
│   │   └── upload_lifecycle.feature         # re-upload-after-delete, cancelled PUT, pass-through delete
│   ├── file_download/            # 8 download features
│   │   ├── download_codecs_and_overlays.feature  # H.265, overlays, no-NVENC fallback, boundary frame, transcode 'original'
│   │   ├── download_contracts.feature            # reversed time range, invalid fullLength, deleted-sensor, sub-window
│   │   ├── download_inter_file_gap.feature
│   │   ├── download_recent_video.feature
│   │   ├── download_stability.feature            # sequential and parallel long-run download load
│   │   ├── download_video.feature
│   │   ├── video_download_by_blocking_url.feature
│   │   └── video_download_by_non_blocking_url.feature
│   ├── picture/                  # 3 picture features
│   ├── webrtc/                   # 5 WebRTC features
│   │   ├── bbox_overlay.feature              # bbox/overlay rendering on live/replay/download (needs metadata fixture)
│   │   ├── live_webrtc_stream.feature
│   │   ├── query_replay_stream.feature
│   │   ├── replay_webrtc_stream.feature
│   │   ├── video_wall.feature                # multi-tile video wall, tile pause, 30-min stability
│   │   └── webrtc_resilience.feature         # network break, picture burst, replay seek / speed / mode-switch
│   ├── url_optimization/         # 2 URL optimization features
│   │   ├── replay_url_caching.feature
│   │   └── url_expiry_enforcement.feature    # picture URL expiry, invalid expirySec
│   ├── perf/                     # 4 performance scenarios
│   └── unit_tests/               # Unit test features (per service)
│       ├── live_stream/
│       ├── replay_stream/
│       ├── rtsp_proxy/
│       ├── sensor_management/
│       ├── storage_management/
│       ├── stream_recorder/
│       │   ├── continuous_recording.feature  # always-on: gap-free, cold-start, size accounting
│       │   └── stream_recorder_api.feature
│       └── mcp_gateway/
│
├── tests/                         # BDD Test implementations
│   ├── file_upload/              # Upload tests + utilities
│   ├── file_download/            # Download tests + utilities
│   ├── picture/                  # Picture tests + utilities
│   ├── webrtc/                   # WebRTC tests + utilities
│   ├── perf/                     # Performance tests + utilities
│   └── unit_tests/               # Unit tests (API endpoint validation)
│       ├── conftest.py           # Shared context + fixtures
│       ├── unit_test_utils.py    # HTTP helpers, validators
│       ├── live_stream/
│       ├── replay_stream/
│       ├── rtsp_proxy/
│       ├── sensor_management/
│       ├── storage_management/
│       ├── stream_recorder/
│       └── mcp_gateway/
│
├── scripts/                       # Utility scripts
│   ├── container_monitor.py      # Container resource monitoring
│   ├── setup_deps.py             # System dependency setup
│   └── run_unit_tests.sh         # Run unit tests with per-service CSV
│
└── reports/                       # Generated test reports
    ├── report.html               # pytest HTML test report
    ├── junit.xml                 # JUnit XML report
    ├── unit_tests/               # Per-service unit test CSVs
    │   ├── live_stream.csv
    │   ├── replay_stream.csv
    │   ├── rtsp_proxy.csv
    │   ├── sensor_management.csv
    │   ├── storage_management.csv
    │   ├── stream_recorder.csv
    │   └── mcp_gateway.csv
    ├── stats/                    # Container resource monitoring
    │   ├── container_stats.json
    │   ├── container_stats.csv
    │   ├── container_stats_summary.txt
    │   └── container_stats_viewer.html
    ├── latency/                  # Latency test results
    │   ├── api_latency_comprehensive_*.csv
    │   └── latency_viewer.html   # Interactive latency results viewer
    └── performance/              # Other performance test results
        └── *_performance_*.csv
```

## Complete Test List

| # | Test Name | Test Description | Test Type |
|---|-----------|------------------|-----------|
| **File Upload Tests** ||||
| 1 | `test_upload_same_file_concurrently_should_result_in_only_one_success` | Validates that uploading the same file concurrently results in only one successful upload (race condition handling) | Functional |
| 2 | `test_upload_different_files_concurrently_should_all_succeed` | Validates that uploading different files concurrently all succeed | Functional |
| 3 | `test_upload_file_with_extension_when_file_without_extension_exists_should_fail` | Validates file extension conflict detection when uploading file with extension after file without extension | Functional |
| 4 | `test_upload_file_without_extension_when_file_with_extension_exists_should_fail` | Validates file extension conflict detection when uploading file without extension after file with extension | Functional |
| 5 | `test_upload_same_filename_with_extension_twice_should_fail` | Validates that uploading same filename with extension twice fails (duplicate detection) | Functional |
| 6 | `test_upload_without_sensorid_should_generate_random_uuid` | Validates that uploads without sensorId parameter generate a random UUID | Functional |
| 7 | `test_upload_multiple_files_with_same_sensorid_should_create_substreams` | Validates that multiple files with same sensorId create substreams correctly | Functional |
| 8 | `test_upload_without_timestamp_should_use_epoch_time` | Validates that uploads without timestamp parameter use epoch time | Functional |
| 9 | `test_upload_with_timestamp_should_use_provided_timestamp` | Validates that uploads with timestamp parameter use the provided timestamp | Functional |
| 10 | `test_upload_file_with_multipart_post_should_succeed` | Validates multipart POST upload succeeds | Functional |
| 11 | `test_upload_same_filename_twice_with_multipart_post_should_create_unique_files` | Validates multipart POST creates unique files even with same filename | Functional |
| 12 | `test_upload_bframe_video_with_post_should_succeed` | Validates B-frame video upload via multipart POST | Functional |
| 13 | `test_upload_bframe_video_multiple_times_should_create_unique_files` | Validates multiple B-frame video uploads create unique files (POST) | Functional |
| 14 | `test_upload_bframe_video_with_put_should_succeed` | Validates B-frame video upload via PUT | Functional |
| 15 | `test_upload_same_bframe_video_twice_with_put_should_fail` | Validates duplicate B-frame video upload fails (PUT) | Functional |
| **File Download Tests** ||||
| 16 | `test_download_and_validate_videos_with_parallel_requests` | Downloads videos for multiple streams/time ranges in parallel and validates with mediainfo | Functional |
| 17 | `test_download_and_validate_recent_videos_with_parallel_requests` | Downloads recent videos with optional transcode/overlay and validates with mediainfo | Functional |
| 18 | `test_download_and_validate_videos_with_parallel_blocking_url_requests` | Downloads videos via blocking URL generation and validates with mediainfo | Functional |
| 19 | `test_download_and_validate_videos_with_parallel_nonblocking_url_requests` | Downloads videos via non-blocking URL generation and validates with mediainfo | Functional |
| **Picture Tests** ||||
| 20 | `test_validate_live_pictures_with_parallel_requests` | Fetches live pictures for multiple streams in parallel and validates with jpeginfo | Functional |
| 21 | `test_validate_replay_pictures_with_parallel_requests` | Fetches replay pictures for multiple streams/timestamps in parallel and validates with jpeginfo | Functional |
| **WebRTC Tests** ||||
| 22 | `test_validate_webrtc_stream_establishment_and_health` | Establishes live WebRTC stream, validates frame reception, and measures FPS | Functional |
| 23 | `test_validate_replay_webrtc_stream_establishment_and_health` | Establishes replay WebRTC stream, validates frame reception, and measures FPS | Functional |
| **Performance Tests** ||||
| 24 | `test_api_latency` | Comprehensive latency test covering 37 scenarios: video downloads (15s/30s/60s with various offsets), picture API (with/without overlay), concurrent loads (2/5/10/15), with/without transcode. Always passes - collects metrics for analysis. | Non-Functional |
| **Unit Tests - Live Stream** ||||
| 25 | `test_get_list_of_available_live_streams` | GET /vst/api/v1/live/streams - validates 200 + JSON array | Unit |
| 26 | `test_get_live_stream_service_version` | GET /vst/api/v1/live/version - validates version response | Unit |
| 27 | `test_get_live_stream_service_help` | GET /vst/api/v1/live/help - validates supported API list | Unit |
| 28 | `test_get_live_stream_service_configuration` | GET /vst/api/v1/live/configuration - validates config object | Unit |
| 29 | `test_get_live_picture_url_for_a_stream` | GET /vst/api/v1/live/stream/{id}/picture/url - validates picture URL fields | Unit |
| **Unit Tests - Replay Stream** ||||
| 30 | `test_get_list_of_available_replay_streams` | GET /vst/api/v1/replay/streams - validates 200 + JSON array | Unit |
| 31 | `test_get_replay_stream_service_version` | GET /vst/api/v1/replay/version - validates version response | Unit |
| 32 | `test_get_replay_stream_service_help` | GET /vst/api/v1/replay/help - validates supported API list | Unit |
| 33 | `test_get_replay_stream_service_configuration` | GET /vst/api/v1/replay/configuration - validates config object | Unit |
| **Unit Tests - RTSP Proxy** ||||
| 34 | `test_get_list_of_available_proxy_streams` | GET /vst/api/v1/proxy/streams - validates 200 + JSON array | Unit |
| 35 | `test_get_rtsp_proxy_service_version` | GET /vst/api/v1/proxy/version - validates version response | Unit |
| 36 | `test_get_rtsp_proxy_service_help` | GET /vst/api/v1/proxy/help - validates supported API list | Unit |
| 37 | `test_get_rtsp_proxy_service_configuration` | GET /vst/api/v1/proxy/configuration - validates config object | Unit |
| 38 | `test_get_rtsp_proxy_info` | GET /vst/api/v1/proxy/info - validates urlPrefix, activeClientSessions | Unit |
| **Unit Tests - Sensor Management** ||||
| 39 | `test_get_list_of_sensors` | GET /vst/api/v1/sensor/list - validates 200 + JSON array | Unit |
| 40 | `test_get_status_of_all_sensors` | GET /vst/api/v1/sensor/status - validates 200 | Unit |
| 41 | `test_get_streams_of_all_sensors` | GET /vst/api/v1/sensor/streams - validates 200 + JSON array | Unit |
| 42 | `test_get_sensor_management_service_version` | GET /vst/api/v1/sensor/version - validates version response | Unit |
| 43 | `test_get_sensor_management_service_help` | GET /vst/api/v1/sensor/help - validates supported API list | Unit |
| 44 | `test_get_sensor_management_service_configuration` | GET /vst/api/v1/sensor/configuration - validates config object | Unit |
| 45 | `test_get_qos_stats_for_all_sensors` | GET /vst/api/v1/sensor/qos - validates 200 | Unit |
| 46 | `test_get_system_stats` | GET /vst/api/v1/sensor/debug/system/stats - validates 200 | Unit |
| 47 | `test_get_recording_timelines_for_all_sensors` | GET /vst/api/v1/sensor/timelines - validates 200 | Unit |
| 48 | `test_get_streams_for_a_specific_sensor` | GET /vst/api/v1/sensor/{id}/streams - validates 200 | Unit |
| 49 | `test_get_status_for_a_specific_sensor` | GET /vst/api/v1/sensor/{id}/status - validates 200 | Unit |
| 50 | `test_get_info_for_a_specific_sensor` | GET /vst/api/v1/sensor/{id}/info - validates 200 | Unit |
| 51 | `test_get_timelines_for_a_specific_sensor` | GET /vst/api/v1/sensor/{id}/timelines - validates 200 | Unit |
| **Unit Tests - Storage Management** ||||
| 52 | `test_get_total_storage_size` | GET /vst/api/v1/storage/size - validates 200 | Unit |
| 53 | `test_get_storage_info` | GET /vst/api/v1/storage/info - validates total, used, available | Unit |
| 54 | `test_get_storage_management_service_version` | GET /vst/api/v1/storage/version - validates version response | Unit |
| 55 | `test_get_storage_management_service_help` | GET /vst/api/v1/storage/help - validates supported API list | Unit |
| 56 | `test_get_storage_management_service_configuration` | GET /vst/api/v1/storage/configuration - validates config object | Unit |
| 57 | `test_get_list_of_all_media_files` | GET /vst/api/v1/storage/file/list - validates 200 | Unit |
| 58 | `test_get_protected_file_list` | GET /vst/api/v1/storage/file/protected - validates 200 | Unit |
| **Unit Tests - Stream Recorder** ||||
| 59 | `test_get_list_of_available_record_streams` | GET /vst/api/v1/record/streams - validates 200 + JSON array | Unit |
| 60 | `test_get_stream_recorder_service_version` | GET /vst/api/v1/record/version - validates version response | Unit |
| 61 | `test_get_stream_recorder_service_help` | GET /vst/api/v1/record/help - validates supported API list | Unit |
| 62 | `test_get_stream_recorder_service_configuration` | GET /vst/api/v1/record/configuration - validates config object | Unit |
| 63 | `test_get_recording_timelines_for_all_streams` | GET /vst/api/v1/record/timelines - validates 200 | Unit |
| **Unit Tests - MCP Gateway** ||||
| 64 | `test_list_all_sensors` | MCP tool sensor_list - validates sensor data returned | Unit |
| 65 | `test_get_sensor_status` | MCP tool sensor_status - validates JSON response | Unit |
| 66 | `test_check_sensor_health` | MCP tool sensor_health_check - validates status field | Unit |
| 67 | `test_trigger_sensor_scan` | MCP tool sensor_scan - validates JSON response | Unit |
| 68 | `test_get_sensor_status_by_id` | MCP tool sensor_status_by_id - validates JSON response | Unit |
| 69 | `test_get_sensor_info_by_id` | MCP tool sensor_info_by_id - validates JSON response | Unit |
| 70 | `test_get_sensor_network_info_by_id` | MCP tool sensor_network_by_id - validates JSON response | Unit |
| 71 | `test_get_sensor_settings_by_id` | MCP tool sensor_settings_by_id - validates JSON response | Unit |
| 72 | `test_get_recording_status_for_a_stream` | MCP tool record_stream_status - validates JSON response | Unit |
| 73 | `test_get_recording_timelines_for_a_stream` | MCP tool record_stream_timelines - validates JSON response | Unit |
| 74 | `test_start_and_stop_recording_for_a_stream` | MCP tools record_stream_start + stop - validates JSON response | Unit |
| 75 | `test_get_live_picture_as_base64_for_a_stream` | MCP tool get_live_picture_base64 - validates base64 image data | Unit |
| 76 | `test_get_live_picture_url_for_a_stream` | MCP tool get_live_picture_url - validates imageUrl | Unit |
| 77 | `test_list_all_storage_files` | MCP tool storage_file_list - validates JSON response | Unit |
| 78 | `test_list_storage_files_by_sensor` | MCP tool storage_file_list_by_sensor - validates JSON response | Unit |
| 79 | `test_get_storage_file_paths_by_sensor` | MCP tool storage_file_path_by_sensor - validates JSON response | Unit |

**Total Tests:** 138 scenarios across 33 test files
- **Functional / Negative / Boundary / Stress Tests:** 82
- **Non-Functional Tests:** 1 (comprehensive latency and performance testing)
- **Unit Tests:** 55 (API endpoint validation across 7 services)

> Default `pytest` runs collect 128 tests; 10 are deselected by opt-in markers
> (`@longrun`, `@needs_iptables`, `@needs_bbox_metadata`). See [Test Markers](#test-markers).

### Coverage notes

Every scenario is self-healing: the autouse cleanup hook in `tests/file_upload/conftest.py` and `tests/file_download/conftest.py` deletes every uploaded stream and every sensor referenced or created by the scenario, regardless of pass/fail. New scenarios should track resources by appending to `context.uploaded_stream_ids` and/or `context.created_sensor_ids` instead of inlining DELETE calls in test steps.

A handful of replay-side WebRTC scenarios (mid-session seek, playback-speed change, recorded->live mode switch, live-during-parallel-download) are scaffolded as skipping stubs. They run, skip with a clear reason, and create no resources. Promoting them to real assertions only requires a shared stateful WebRTC client utility — no feature-file or step-name changes.

## Test Markers

Some scenarios are opt-in via pytest markers. They are tagged in the feature file (e.g. `@longrun`) and the root `conftest.py` adds `pytest.skip` to them unless the user explicitly selects the marker with `-m`.

| Marker | Skipped by default? | What it gates | How to enable |
|---|---|---|---|
| `longrun` | Yes | Stress / long-run tests with 30 min – 2 h wall-clock | `pytest -m longrun` |
| `needs_iptables` | Yes | Tests that require iptables / privileged Docker (e.g. WebRTC network-break simulation) | `pytest -m needs_iptables` |
| `needs_bbox_metadata` | Yes | Tests that require a sensor seeded with stored bbox / overlay metadata from a Metropolis perception pipeline | `pytest -m needs_bbox_metadata` |
| `mcp_gateway` | Yes | MCP gateway tests (require the vios-mcp container on port 8001) | `pytest -m mcp_gateway` |

### Examples

```bash
# Default — exclude longrun, iptables, bbox-metadata and mcp_gateway:
poetry run pytest tests/

# Run only the long-running stress tests:
poetry run pytest -m longrun tests/

# Run everything including longrun on a CI machine that can afford the time:
poetry run pytest -m "longrun or not longrun" tests/

# Run the WebRTC network-break test on a privileged runner:
poetry run pytest -m needs_iptables tests/webrtc/

# Run bbox-overlay tests against an environment seeded with metadata
# (also requires tests.bbox_overlay_tests.test_parameters.bbox_stream_id in config.json):
poetry run pytest -m needs_bbox_metadata tests/webrtc/test_bbox_overlay.py
```

### Long-running tests (`@longrun`)

| Scenario | Wall-clock |
|---|---|
| 100 sequential blocking downloads (`download_stability.feature`) | ~2 h |
| storage-ms heavy parallel download load (`download_stability.feature`) | 2 h |
| 12-tile wall stable for 30 minutes (`video_wall.feature`) | 30 min |
| Always-on recording gap-free for 1 hour (`continuous_recording.feature`) | 1 h |

### Conditional configuration

A few markers also require a value in `config.json` to be meaningful:

- `needs_bbox_metadata` — set `tests.bbox_overlay_tests.test_parameters.bbox_stream_id` to the streamId of a sensor that already has stored bbox metadata.
- Continuous-recording tests under `unit_tests/stream_recorder/continuous_recording.feature` — set `tests.continuous_recording_tests.test_parameters.alwaysOn_sensor_id` to the sensorId of an always-on RTSP sensor in the deployment. If missing, the scenarios skip with guidance.

## Test Categories

### File Upload Tests (15 scenarios)

Tests for file upload functionality via PUT and POST APIs.

**Test Files:**
- `test_concurrent_upload_race.py` - Race condition tests for concurrent uploads
- `test_file_extension_conflicts.py` - File extension conflict handling
- `test_timestamp_handling.py` - Timestamp parameter validation
- `test_sensor_id_handling.py` - SensorId parameter validation
- `test_multipart_upload.py` - Multipart POST upload tests
- `test_bframe_post_upload.py` - B-frame video uploads (POST)
- `test_bframe_put_upload.py` - B-frame video uploads (PUT)

**Key Features:**
- Concurrent upload race condition testing
- File conflict detection (same filename, extensions)
- Automatic file cleanup via storage API
- SensorId and timestamp handling
- B-frame video support

### File Download Tests (4 scenarios)

Tests for video download functionality from storage.

**Test Files:**
- `test_download_video.py` - Basic video download and validation
- `test_download_recent_video.py` - Recent video download with transcode
- `test_video_download_by_blocking_url.py` - Blocking URL downloads
- `test_video_download_by_non_blocking_url.py` - Non-blocking URL downloads

**Key Features:**
- Parallel async downloads
- Video validation with mediainfo
- URL expiry verification
- Transcode with overlay support
- Automatic cleanup of downloaded videos

### Picture Tests (2 scenarios)

Tests for picture/snapshot retrieval and validation.

**Test Files:**
- `test_live_picture.py` - Live picture/snapshot tests
- `test_replay_picture.py` - Replay picture/snapshot tests

**Key Features:**
- Parallel async picture fetching
- JPEG validation with jpeginfo
- Live and replay picture support
- Automatic cleanup of downloaded images

### WebRTC Tests (2 scenarios)

Tests for WebRTC streaming functionality.

**Test Files:**
- `test_live_webrtc_stream.py` - Live WebRTC stream tests
- `test_replay_webrtc_stream.py` - Replay WebRTC stream tests

**Key Features:**
- Full WebRTC peer connection establishment
- WebSocket signaling
- Frame rate validation
- ICE connection health checks
- Automatic connection cleanup

### Unit Tests (55 scenarios)

API endpoint validation tests for each VST microservice. Each service produces its own CSV report under `reports/unit_tests/<service>.csv`.

**Services covered:**

| Service | Scenarios | Endpoints Tested |
|---------|-----------|------------------|
| Live Stream | 5 | streams, version, help, configuration, picture/url |
| Replay Stream | 4 | streams, version, help, configuration |
| RTSP Proxy | 5 | streams, version, help, configuration, info |
| Sensor Management | 13 | list, status, streams, info, qos, system/stats, timelines, version, help, configuration (+ per-sensor variants) |
| Storage Management | 7 | size, info, version, help, configuration, file/list, file/protected |
| Stream Recorder | 5 | streams, version, help, configuration, timelines |
| MCP Gateway | 16 | sensor tools, recording tools, picture tools, storage tools (via MCP protocol) |

**Running all unit tests (generates per-service CSVs):**

```bash
bash scripts/run_unit_tests.sh
```

**Running unit tests for a single service:**

```bash
# Live Stream
poetry run pytest tests/unit_tests/live_stream/ \
    --csv=reports/unit_tests/live_stream.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# Replay Stream
poetry run pytest tests/unit_tests/replay_stream/ \
    --csv=reports/unit_tests/replay_stream.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# RTSP Proxy
poetry run pytest tests/unit_tests/rtsp_proxy/ \
    --csv=reports/unit_tests/rtsp_proxy.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# Sensor Management
poetry run pytest tests/unit_tests/sensor_management/ \
    --csv=reports/unit_tests/sensor_management.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# Storage Management
poetry run pytest tests/unit_tests/storage_management/ \
    --csv=reports/unit_tests/storage_management.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# Stream Recorder
poetry run pytest tests/unit_tests/stream_recorder/ \
    --csv=reports/unit_tests/stream_recorder.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor

# MCP Gateway
poetry run pytest tests/unit_tests/mcp_gateway/ \
    --csv=reports/unit_tests/mcp_gateway.csv \
    --override-ini="addopts=" -v --tb=short --disable-container-monitor
```

**Notes:**
- `--override-ini="addopts="` clears default addopts so only the per-service CSV is generated
- `--disable-container-monitor` prevents redundant monitor start/stop per service
- WebRTC signaling endpoints are excluded (require full WebRTC handshake)
- MCP tests require the MCP gateway to be running (port 8001 by default)

## Configuration

All tests are configured via `config.json`:

```json
{
  "api": {
    "base_url": "http://your-vst-server:30888",
    "verify_ssl": false
  },
  "container_monitoring": {
    "enabled": true,
    "interval_seconds": 30
  },
  "tests": {
    "file_upload_tests": {
      "test_parameters": {
        "race_condition_thread_count": 10,
        "concurrent_different_files_count": 10,
        "multipart_files_per_iteration": 10,
        "auto_cleanup_after_test": true
      }
    },
    "file_download_tests": {
      "test_parameters": {
        "timeout": 30,
        "download_timeout": 120,
        "video_duration_seconds": 10,
        "temp_download_dir": "/tmp/vst_test_downloads",
        "auto_cleanup_after_test": true
      }
    },
    "picture_tests": {
      "test_parameters": {
        "timeout": 30,
        "temp_image_dir": "/tmp/vst_test_images",
        "auto_cleanup_after_test": true
      }
    },
    "webrtc_tests": {
      "test_parameters": {
        "timeout": 30,
        "signaling_timeout": 60,
        "min_fps": 5.0,
        "min_frames_for_validation": 60
      }
    }
  }
}
```

## Installation (Local Setup)

### Prerequisites

- Python 3.8.1 or higher
- Poetry (Python dependency management)
- Docker (for container monitoring)

### System Dependencies

Different test categories require different system tools:

```bash
# Install all dependencies automatically
poetry install
poetry run setup-system-deps

# Or install manually:

# For file upload tests
sudo apt-get install ffmpeg

# For file download tests
sudo apt-get install mediainfo

# For picture tests
sudo apt-get install jpeginfo

# For WebRTC tests
sudo apt-get install -y \
    libavformat-dev libavcodec-dev libavdevice-dev \
    libavutil-dev libswscale-dev libswresample-dev \
    libavfilter-dev libopus-dev libvpx-dev
```

### Python Dependencies

Managed automatically by Poetry:

```bash
# Install all Python dependencies
poetry install

# Key packages installed:
# - pytest, pytest-bdd (BDD testing framework)
# - pytest-xdist (parallel test execution)
# - pytest-html (HTML test reports)
# - pytest-repeat (test repetition)
# - requests, aiohttp (HTTP clients)
# - aiortc (WebRTC implementation)
# - websockets (WebSocket client)
```

### Setup Script (setup.sh)

The `setup.sh` script provides automated, interactive setup for the entire test suite.

**What it does:**

1. ✅ **OS Detection** - Verifies Ubuntu/Debian compatibility
2. ✅ **Python Check** - Ensures Python 3.8+ is installed
3. ✅ **Poetry Setup** - Installs Poetry if not present
4. ✅ **System Dependencies** - Installs ffmpeg, mediainfo, jpeginfo, libav libraries
5. ✅ **Python Dependencies** - Runs `poetry install`
6. ✅ **Directory Creation** - Creates temp directories for tests
7. ✅ **Verification** - Validates all dependencies are working

**Features:**

- Interactive prompts for each installation step
- Color-coded output (green=success, red=error, yellow=warning)
- Checks before installing (won't reinstall if already present)
- Version validation (ensures minimum requirements are met)
- Adds Poetry to PATH automatically
- Comprehensive error handling

**Usage:**

```bash
# Run with default settings
./setup.sh

# The script will prompt before installing anything
# Example prompts:
#   "Python 3.8+ is required. Install it? [y/N]:"
#   "System dependencies are missing. Install them? [y/N]:"
```

**What gets installed:**

System packages:
- `ffmpeg` - For creating test video files
- `mediainfo` - For video validation
- `jpeginfo` - For JPEG image validation
- `libavformat-dev`, `libavcodec-dev`, `libavdevice-dev` - For WebRTC
- `libavutil-dev`, `libswscale-dev`, `libswresample-dev` - For WebRTC
- `libavfilter-dev`, `libopus-dev`, `libvpx-dev` - For WebRTC codecs
- `pkg-config` - For dependency detection

Python packages (via Poetry):
- All dependencies from `pyproject.toml`

**After setup:**

The script provides next steps:
```bash
To run the tests:
  cd /path/to/bdd_tests
  poetry run pytest tests/

To run tests in parallel:
  poetry run pytest tests/ -n auto
```

## Docker Usage

### Building the Image

```bash
docker build -t vst-bdd-tests:latest .
```

### Running Tests

**Run all tests:**
```bash
# Using config.json
docker run --rm \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests

# Or override with --base-url (no config needed)
docker run --rm \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests pytest --base-url http://<HOST>:30888 -v

  # Or override with --base-url (no config needed)
docker run --rm \
  -v $(pwd)/reports:/app/reports \
  --network host \
  <INTERNAL_REGISTRY>/bdd_tests:v1.0_x86 pytest --base-url http://<HOST>:30888 -v
```

**Run specific test categories:**
```bash
# File upload tests
docker run --rm \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests pytest --base-url http://<HOST>:30888 tests/file_upload/ -v

# Latency test with custom iterations
docker run --rm \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests pytest --base-url http://<HOST>:30888 \
    tests/perf/test_latency.py --perf-iterations 20 -v --log-cli-level=INFO
```

**Interactive shell for debugging:**
```bash
docker run --rm -it \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests bash
```

### Configuration Options

**Option 1: Command-line (Recommended for Docker):**
```bash
docker run --rm \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests pytest --base-url http://your-api:30888 -v
```

**Option 2: Config file:**
```bash
docker run --rm \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/reports:/app/reports \
  --network host \
  vst-bdd-tests
```

### Volume Mounts

- `./reports:/app/reports` - Test reports directory (required)
- `./config.json:/app/config.json:ro` - Configuration file (optional if using --base-url)

### Network Mode

Uses `--network host` to access services on localhost. For remote APIs, remove this flag or use appropriate Docker networking.

## Running Tests (Local)

### Basic Usage

```bash
# Run all tests (excluding unit tests)
poetry run pytest tests/ --ignore=tests/unit_tests

# Run unit tests only (all services, per-service CSVs)
bash scripts/run_unit_tests.sh

# Run both unit tests and BDD tests
bash scripts/run_unit_tests.sh && poetry run pytest tests/ --ignore=tests/unit_tests

# Run specific test category
poetry run pytest tests/file_upload/
poetry run pytest tests/file_download/
poetry run pytest tests/picture/
poetry run pytest tests/webrtc/

# Run specific test file
poetry run pytest tests/file_upload/test_concurrent_upload_race.py

# Run specific scenario
poetry run pytest tests/file_upload/ -k "race condition"
```

### Test Repetition (No Hardcoded Iterations!)

All manual iteration loops have been removed. Use pytest's `--count` flag instead:

```bash
# Run each test 3 times
poetry run pytest tests/ --count=3

# Run specific category 5 times
poetry run pytest tests/file_upload/ --count=5

# Stress test: run until failure
poetry run pytest tests/file_upload/ --count=100 -x
```

**Benefits:**
- Each repetition is tracked as a separate test run
- Better reporting (know exactly which iteration failed)
- Can run iterations in parallel with `-n auto`
- No code changes needed to adjust iteration count

**Exception - Performance/Latency Tests:**

Performance tests use internal iterations for statistical accuracy. Use `--perf-iterations` instead of `--count`:

```bash
# Performance test with 20 iterations per scenario
poetry run pytest tests/perf/test_latency.py --perf-iterations 20 -v

# Quick performance test (5 iterations)
poetry run pytest tests/perf/test_latency.py --perf-iterations 5 -v

# Default (10 iterations, from config.json)
poetry run pytest tests/perf/test_latency.py -v
```

Do NOT use `--count` with performance tests (it generates duplicate CSV files).

### Parallel Execution

```bash
# Run tests in parallel (auto-detect CPU count)
poetry run pytest tests/ -n auto

# Specify number of workers
poetry run pytest tests/ -n 4

# Combine repetition with parallel execution
poetry run pytest tests/file_upload/ --count=5 -n auto
```

**Note:** WebRTC tests should run sequentially (not in parallel) due to resource requirements.

### Verbose Output

```bash
# Verbose test output
poetry run pytest tests/ -v

# Show test logs in real-time
poetry run pytest tests/ -v --log-cli-level=INFO

# Debug mode
poetry run pytest tests/ -v --log-cli-level=DEBUG

# Short traceback on failures
poetry run pytest tests/ --tb=short
```

## Test Reports

After running tests, multiple reports are generated:

### HTML Test Report

**Location:** `reports/report.html`

Interactive HTML report showing:
- Test results (pass/fail)
- Test duration
- Error messages and tracebacks
- Test metadata

Open it:
```bash
xdg-open reports/report.html
```

### Container Stats Viewer

**Location:** `reports/stats/container_stats_viewer.html`

Beautiful interactive dashboard showing:
- CPU usage graphs (all containers over time)
- Memory usage graphs (all containers over time)
- Statistics summary table
- Resource usage trends

Open it:
```bash
xdg-open reports/stats/container_stats_viewer.html
```

### JUnit XML Report

**Location:** `reports/junit.xml`

Standard JUnit XML format for CI/CD integration.

### Container Stats Data

**Locations:**
- `reports/stats/container_stats.json` - Detailed JSON data
- `reports/stats/container_stats.csv` - CSV format for spreadsheets
- `reports/stats/container_stats_summary.txt` - Human-readable summary

## Test Architecture

### Test Isolation

**Every test scenario is completely independent:**

- Fresh context instance per test
- Unique identifiers (UUIDs for sensors, streams)
- Isolated temporary directories
- Automatic cleanup after each test
- No shared state between tests

**Benefits:**
- Tests can run in any order
- Parallel execution is safe
- No test dependencies
- Easy debugging

### Automatic Cleanup

Each test category handles cleanup automatically:

**Upload Tests:**
- Queries timelines for uploaded files
- Deletes files via `DELETE /vst/api/v1/storage/file/{streamId}?startTime=X&endTime=Y`
- Sensors are automatically removed when files are deleted

**Download Tests:**
- Removes downloaded videos from temp directory
- No server-side cleanup needed (read-only operations)

**Picture Tests:**
- Removes downloaded images from temp directory
- No server-side cleanup needed (read-only operations)

**WebRTC Tests:**
- Closes WebRTC peer connections
- No server-side cleanup needed (temporary connections)

Configure cleanup behavior in `config.json`:
```json
{
  "file_upload_tests": {
    "test_parameters": {
      "auto_cleanup_after_test": true  // Set to false for debugging
    }
  }
}
```

## Configuration Reference

### API Configuration

```json
{
  "api": {
    "base_url": "http://<HOST>:30888",  // VST API server URL
    "verify_ssl": false                       // SSL certificate verification
  }
}
```

### Container Monitoring

```json
{
  "container_monitoring": {
    "enabled": true,          // Enable container resource monitoring
    "interval_seconds": 30    // Stats collection interval
  }
}
```

Disable monitoring via CLI:
```bash
poetry run pytest tests/ --disable-container-monitor
```

### File Upload Test Parameters

```json
{
  "file_upload_tests": {
    "test_parameters": {
      "race_condition_thread_count": 10,          // Threads for race condition tests
      "concurrent_different_files_count": 10,     // Number of unique files to upload concurrently
      "multipart_files_per_iteration": 10,        // Files to upload in multipart tests
      "auto_cleanup_after_test": true             // Auto-delete uploaded files
    }
  }
}
```

### File Download Test Parameters

```json
{
  "file_download_tests": {
    "test_parameters": {
      "timeout": 30,                      // General request timeout
      "download_timeout": 120,            // Video download timeout
      "video_duration_seconds": 10,       // Duration of video clips to download
      "url_request_timeout": 300,         // URL generation timeout
      "video_durations": [1, 70],         // Test different video durations
      "expiry_minutes": [1],              // URL expiry times to test
      "offset_ms": 5000,                  // Offset from current time (recent videos)
      "duration_ms": 30000,               // Duration for recent videos
      "enable_transcode": false,          // Enable transcode with overlay
      "temp_download_dir": "/tmp/vst_test_downloads",
      "auto_cleanup_after_test": true     // Auto-delete downloaded videos
    }
  }
}
```

### Picture Test Parameters

```json
{
  "picture_tests": {
    "test_parameters": {
      "timeout": 30,                      // Request timeout
      "temp_image_dir": "/tmp/vst_test_images",
      "auto_cleanup_after_test": true     // Auto-delete downloaded images
    }
  }
}
```

### WebRTC Test Parameters

```json
{
  "webrtc_tests": {
    "test_parameters": {
      "timeout": 30,                      // General timeout
      "signaling_timeout": 60,            // WebRTC signaling timeout
      "replay_duration_seconds": 60,      // Replay stream duration
      "min_fps": 5.0,                     // Minimum acceptable FPS
      "min_frames_for_validation": 60,    // Minimum frames to validate
      "min_duration_for_validation": 15.0 // Minimum stream duration
    }
  }
}
```

## Development Guide

### Project Organization

Tests are organized into logical categories:

```
tests/
├── file_upload/
│   ├── conftest.py              # Shared fixtures for upload tests
│   ├── upload_test_utils.py     # Shared utility functions
│   ├── test_*.py                # Individual test files
│   └── __init__.py
│
├── file_download/
│   ├── conftest.py              # Shared fixtures for download tests
│   ├── download_test_utils.py   # Shared utility functions
│   ├── test_*.py                # Individual test files
│   └── __init__.py
│
├── picture/
│   ├── conftest.py              # Shared fixtures for picture tests
│   ├── picture_test_utils.py    # Shared utility functions
│   ├── test_*.py                # Individual test files
│   └── __init__.py
│
└── webrtc/
    ├── conftest.py              # Shared fixtures for WebRTC tests
    ├── webrtc_test_utils.py     # Shared utility functions
    ├── test_*.py                # Individual test files
    └── __init__.py
```

### Adding New Tests

#### Step 1: Create Feature File

Create a `.feature` file in the appropriate `features/` subdirectory:

```gherkin
Feature: My New Feature
  Description of what we're testing

  Scenario: Test scenario name
    Given some precondition
    When some action is performed
    Then some assertion is validated
```

#### Step 2: Implement Test Steps

Create or update a test file in the corresponding `tests/` subdirectory:

```python
from pytest_bdd import scenarios, given, when, then

scenarios('../../features/category/my_feature.feature')

@given('some precondition')
def setup_precondition(context, api_config, test_params):
    # Setup code
    pass

@when('some action is performed')
def perform_action(context, api_config):
    # Action code
    pass

@then('some assertion is validated')
def verify_result(context):
    # Assertion code
    assert condition, "Error message"
```

#### Step 3: Use Shared Fixtures

Each test category provides shared fixtures via `conftest.py`:

- `context` - Fresh context instance per test
- `test_params` - Configuration parameters
- `test_endpoints` - API endpoints
- Automatic cleanup fixtures

#### Step 4: Track Resources for Cleanup

For upload tests, track streamIds:
```python
if result.get('success') and result.get('streamId'):
    context.uploaded_stream_ids.add(result['streamId'])
```

Cleanup happens automatically via `conftest.py`.

### Best Practices

✅ **DO:**
- Use the provided `context` fixture
- Use shared utility functions from `*_test_utils.py`
- Let `conftest.py` handle cleanup automatically
- Use `pytest --count` for test repetition
- Add type hints to functions
- Use descriptive variable names
- Log important operations

❌ **DON'T:**
- Create manual iteration loops (`for i in range(iterations)`)
- Create custom cleanup fixtures (use centralized ones)
- Share state between test scenarios
- Create manual cleanup code
- Use hardcoded values (use `test_params`)

### Shared Utilities

Each test category provides shared utilities:

**upload_test_utils.py:**
- `UploadContext` - Test context with streamId tracking
- `create_test_video_file()` - Create valid H.264 MP4 files
- `upload_file_sync()` - Thread-safe upload for concurrent tests
- `upload_file_simple()` - Simple PUT upload
- `upload_file_multipart()` - Multipart POST upload

**download_test_utils.py:**
- `DownloadContext` - Test context for downloads
- `fetch_streams()`, `fetch_timelines()` - Fetch API data
- `select_time_ranges_from_timelines()` - Select valid time ranges
- `download_video_async()` - Async video download
- `validate_video_with_mediainfo()` - Video validation

**picture_test_utils.py:**
- `PictureContext` - Test context for pictures
- `fetch_streams()`, `fetch_timelines()` - Fetch API data
- `select_timestamps_from_timelines()` - Select valid timestamps
- `fetch_picture_async()` - Async picture fetch
- `validate_jpeg_with_jpeginfo()` - JPEG validation

**webrtc_test_utils.py:**
- `VideoFrameTracker` - Track and measure frame rates
- `WebRTCStreamContext` - WebRTC connection context
- `parse_ice_candidate()` - ICE candidate parsing
- `fetch_streams()`, `extract_stream_names()` - Stream utilities

## Container Monitoring

The test suite automatically monitors Docker container resource usage during test execution.

### How It Works

1. **Before tests:** Monitoring starts, initial snapshot collected
2. **During tests:** Stats collected every 30 seconds (configurable)
3. **After tests:** Final snapshot collected, reports generated

### Viewing Container Stats

```bash
# Interactive HTML viewer (recommended)
xdg-open reports/stats/container_stats_viewer.html

# Or view summary
cat reports/stats/container_stats_summary.txt

# Or analyze in spreadsheet
libreoffice reports/stats/container_stats.csv
```

### Configuration

```json
{
  "container_monitoring": {
    "enabled": true,          // Enable/disable monitoring
    "interval_seconds": 30    // Collection interval
  }
}
```

Override via CLI:
```bash
# Disable monitoring
poetry run pytest tests/ --disable-container-monitor

# Custom interval
poetry run pytest tests/ --monitor-interval=60
```

## Test Execution Examples

### Quick Smoke Test

```bash
# Run all tests once
poetry run pytest tests/
```

### Standard Test Run

```bash
# Run each test 3 times
poetry run pytest tests/ --count=3
```

### Intensive Stress Test

```bash
# Run 10 iterations in parallel
poetry run pytest tests/file_upload/ --count=10 -n auto
```

### Category-Specific Testing

```bash
# Test uploads thoroughly
poetry run pytest tests/file_upload/ --count=5 -v

# Test downloads with transcode enabled
# Set enable_transcode: true in config.json first
poetry run pytest tests/file_download/test_download_recent_video.py -v

# Test WebRTC with detailed logs
poetry run pytest tests/webrtc/ -v --log-cli-level=INFO
```

### Debugging

```bash
# Keep resources on server for inspection
# Set auto_cleanup_after_test: false in config.json

# Run with detailed logs
poetry run pytest tests/file_upload/ -v --log-cli-level=DEBUG

# Stop on first failure
poetry run pytest tests/ -x

# Run last failed tests
poetry run pytest tests/ --lf
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: VST BDD Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Install Poetry
        run: |
          curl -sSL https://install.python-poetry.org | python3 -
          echo "$HOME/.local/bin" >> $GITHUB_PATH
      
      - name: Install dependencies
        run: |
          poetry install
          poetry run setup-system-deps
      
      - name: Run tests
        run: poetry run pytest tests/ --count=3
      
      - name: Upload test reports
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: test-reports
          path: reports/
```

### Jenkins Example

```groovy
pipeline {
    agent any
    
    stages {
        stage('Install') {
            steps {
                sh 'poetry install'
                sh 'poetry run setup-system-deps'
            }
        }
        
        stage('Test') {
            steps {
                sh 'poetry run pytest tests/ --count=3'
            }
        }
        
        stage('Publish Reports') {
            steps {
                publishHTML([
                    reportDir: 'reports',
                    reportFiles: 'report.html',
                    reportName: 'Test Report'
                ])
                junit 'reports/junit.xml'
            }
        }
    }
}
```

## Troubleshooting

### Tests Fail to Find Streams

**Problem:** `No streams found` error

**Solution:**
- Ensure VST server is running
- Check `api.base_url` in config.json
- Verify network connectivity
- Ensure streams exist on the server

### Upload Tests Leave Files on Server

**Problem:** Files not cleaned up after tests

**Solution:**
- Check `auto_cleanup_after_test: true` in config
- Verify storage API is accessible
- Check test logs for cleanup errors
- Manually delete via: `DELETE /vst/api/v1/storage/file/{streamId}?startTime=X&endTime=Y`

### Video Validation Fails

**Problem:** `mediainfo not found` or validation errors

**Solution:**
```bash
# Install mediainfo
sudo apt-get install mediainfo

# Verify installation
mediainfo --version
```

### WebRTC Tests Fail

**Problem:** aiortc or codec errors

**Solution:**
```bash
# Install all WebRTC dependencies
poetry run setup-system-deps

# Or manually:
sudo apt-get install -y \
    libavformat-dev libavcodec-dev libavdevice-dev \
    libavutil-dev libswscale-dev libswresample-dev \
    libavfilter-dev libopus-dev libvpx-dev
```

### Container Monitoring Not Working

**Problem:** No container stats generated

**Solution:**
- Ensure Docker is installed and running
- Check user has permissions to run `docker stats`
- Verify `container_monitoring.enabled: true` in config
- Check for errors in test logs
- Stats are saved to `reports/stats/` directory

## Key Features

### Organized Structure
- Tests grouped by functionality (upload, download, picture, webrtc, performance)
- Features and tests mirror each other
- Clear separation of concerns

### No Manual Iterations
- Removed ~430 lines of manual iteration code
- Use `pytest --count` for repetition
- Better test reporting
- Flexible iteration control via CLI

### Unified Configuration
- 5 test configurations (upload, download, picture, webrtc, performance)
- Single source of truth per category
- Clear, descriptive parameter names
- Flexible performance tuning options

### Automatic Resource Management
- Upload: Deletes files and sensors automatically
- Download: Cleans temp videos automatically
- Picture: Cleans temp images automatically
- WebRTC: Closes connections automatically
- Performance: Cleans test streams and files automatically

### Parallel Execution Support
- Tests can run in parallel with `pytest-xdist`
- Async operations for better performance
- Configurable worker count

### Container Monitoring
- Automatic Docker stats collection
- Beautiful HTML visualization
- CSV export for analysis
- Configurable collection interval

## Statistics

- **Total Tests:** 138 scenarios across 33 test files
- **Test Categories:** 7 (upload, download, picture, webrtc, url_optimization, performance, unit tests)
- **Unit Tests:** 55 scenarios across 7 services (live, replay, proxy, sensor, storage, recorder, MCP)
- **Performance Tests:** 1 comprehensive latency test (40+ internal scenarios)
- **Opt-in scenarios:** 10 (gated by `@longrun`, `@needs_iptables`, `@needs_bbox_metadata`)
- **Shared Utilities:** 7 modules (one per category + unit test utils)

## Contributing

When adding new tests:

1. Choose the appropriate test category folder
2. Create feature file in `features/<category>/`
3. Implement test in `tests/<category>/`
4. Use shared utilities from `<category>_test_utils.py`
5. Let `conftest.py` handle cleanup automatically
6. Don't create manual iteration loops
7. Track resources for cleanup if needed

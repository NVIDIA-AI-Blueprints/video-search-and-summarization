# VST & NVStreamer deployment steps

## Prerequisites

**a.** We have tested 100 streams on vst & nvstreamer containers with below cpu configuration

```
Processor: Intel(R) Xeon(R) Platinum 8352Y CPU @ 2.20GHz
CPU(s): 128
Network Card: We need minimum 2.5 Gb/s bandwidth network card to support 500 streams assuming 5Mbps streams
```

**b.** Network buffer tuning required for high-throughput streaming. The
`oneclick_dc_deployment.py` script applies these automatically before each
deploy and restores the originals on script exit. If you bring up
docker compose manually, set them yourself first:

```bash
sudo sysctl -w net.core.rmem_max=2000000
sudo sysctl -w net.core.wmem_max=2000000
sudo sysctl -w net.ipv4.tcp_rmem='4096 2000000 6291456'
sudo sysctl -w net.ipv4.tcp_wmem='4096 2000000 6291456'
```

**c.** Required:

- Docker + Docker Compose v2 (`docker compose version`)
- NVIDIA Container Toolkit (`docker info | grep -i nvidia` should show the runtime)
- Python 3.6+ (only if using `oneclick_dc_deployment.py`)
- sudo privileges (used by sysctl + cleanup commands)



## One-click deployment (recommended)

The `oneclick_dc_deployment.py` script wraps every step below into a single command.

**A)** Untar the `vst-2.1.0-26.05.2.tar.gz`, go to the deployment directory:

```bash
cd vst-2.1.0-26.05.2
```

**B)** Deploy VST + NVStreamer together (default 1 NVStreamer instance):

```bash
python3 oneclick_dc_deployment.py deploy all
```

**C)** If you want to deploy VST & Nvstreamer separately:

```bash
# NVStreamer only (default: 1 instance)
python3 oneclick_dc_deployment.py deploy nvstreamer --force

# Use an existing videos directory directly.
python3 oneclick_dc_deployment.py deploy nvstreamer \
    --nvstreamer-video-path /home/ubuntu/nvstreamer_videos --force

# VST only
python3 oneclick_dc_deployment.py deploy vst --force

```

**D)** Stop services:

```bash
# Stop everything
python3 oneclick_dc_deployment.py stop all

# Stop only VST stream-processing
python3 oneclick_dc_deployment.py stop vst

# Stop only NVStreamer
python3 oneclick_dc_deployment.py stop nvstreamer
```

**E)** Stop + clean persistent data (`--clean`):

```bash
# Stop everything and clean state.
python3 oneclick_dc_deployment.py stop all --clean

# Stop VST + remove vst_volume/ (database, recordings, configs)
python3 oneclick_dc_deployment.py stop vst --clean

# Stop NVStreamer + clean its state.
python3 oneclick_dc_deployment.py stop nvstreamer --clean
```

**F)** Override Docker image tags:

```bash
# All VST service images (stream-processor + sensor)
python3 oneclick_dc_deployment.py deploy all --all-tag 2.1.0-26.05.2

# Individual overrides
python3 oneclick_dc_deployment.py deploy all \
    --streamprocessor-tag 2.1.0-26.05.2 \
    --sensor-tag 2.1.0-26.05.2 \
    --nvstreamer-tag 2.1.0-26.05.2
```

**G)** Help:

```bash
python3 oneclick_dc_deployment.py --help
```



## Manual Docker Compose Deployment Steps

If you prefer to run docker compose directly (e.g. for debugging / one-off testing),
follow the steps below. The one-click script does exactly the same thing under the hood.

**A)** Untar the `vst-2.1.0-26.05.2.tar.gz`, go to the stream-processing directory:

```bash
cd vst-2.1.0-26.05.2
```

**B)** Deploy NVStreamer:

a. Navigate to the nvstreamer directory:

```bash
cd docker-compose/nvstreamer
```

b. Modify `compose.env` with absolute video paths for the active NVStreamer
instance(s). The default ships with `COMPOSE_PROFILES=nvstreamer-1` (single
instance). To run more, edit `COMPOSE_PROFILES` accordingly:

```env
# Single instance (default)
COMPOSE_PROFILES=nvstreamer-1

# Five instances
COMPOSE_PROFILES=nvstreamer-1,nvstreamer-2,nvstreamer-3,nvstreamer-4,nvstreamer-5

# Then set absolute paths for each active instance:
NVSTREAMER_VIDEO_1=/absolute/path/to/nvstreamer/videos1
NVSTREAMER_VIDEO_2=/absolute/path/to/nvstreamer/videos2
NVSTREAMER_VIDEO_3=/absolute/path/to/nvstreamer/videos3
NVSTREAMER_VIDEO_4=/absolute/path/to/nvstreamer/videos4
NVSTREAMER_VIDEO_5=/absolute/path/to/nvstreamer/videos5
```

Note: Paths MUST be absolute. Relative paths break docker compose volume
bind-mounts because compose resolves them relative to the compose file's
directory. Create the folders ahead of time, or upload videos via the
NVStreamer UI later.

c. Start/Stop NVStreamer instances:

```bash
# Start
sudo docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

# Stop
sudo docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans -v
```

d. Access NVStreamer instances (only the ones in `COMPOSE_PROFILES` are running):

```
http://<Host IP>:31000/#/dashboard
http://<Host IP>:31001/#/dashboard
http://<Host IP>:31002/#/dashboard
http://<Host IP>:31003/#/dashboard
http://<Host IP>:31004/#/dashboard
```

e. Note:

- HTTP ports: 31000-31004 (one per instance)
- RTSP ports: 31554, 31564, 31574, 31584, 31594 (one per instance)
- Change ports in `compose.env` if you encounter port conflicts.

**C)** Deploy VST stream-processing:

a. Navigate to the docker-compose directory:

```bash
cd docker-compose/
```

b. Modify `compose.env`:

```env
HOST_IP=<Host IP>
VST_CONFIG_PATH=/absolute/path/to/configs/directory
VST_VOLUME=/absolute/path/to/vst_volume/directory
```

c. Configure `configs/vst_config.json`:

```
Set redis_server_env_var: "<Host IP>:6379"
Set max_devices_supported: 500
```

d. Configure `configs/rtsp_streams.json` with NVStreamer instances:

```json
{
    "Nvstreamer": [
        {
            "enabled": true,
            "endpoint": "<Host IP>:31000",
            "api": "/api/v1/sensor/streams",
            "max_stream_count": 100
        }
    ]
}
```

e. If you want to increase the storage for VST then update `configs/vst_storage.json`:

```
update total_video_storage_size_MB
eg: "total_video_storage_size_MB": 500000
```

f. Start/Stop VST stream-processing:

```bash
# Start (without monitoring)
sudo docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

# Start (with Grafana + Prometheus)
sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring up --force-recreate -d

# Stop
sudo docker compose -f docker-compose.yaml --env-file ./compose.env --profile monitoring down --remove-orphans -v
```

g. Access VST UI:

```
http://<Host IP>:30888/vst/#/dashboard
```

If `--with-monitoring` (or `--profile monitoring`) is used:

```
Grafana: http://<Host IP>:3000
```

**D)** Recommended deploy ordering:

1. NVStreamer first (so VST has stream sources to scan).
2. Wait until each active NVStreamer instance answers HTTP on its port
  (the one-click script does this automatically; the manual flow can use
   `curl http://<Host IP>:31000/` until it returns a response).
3. VST stream-processing next.


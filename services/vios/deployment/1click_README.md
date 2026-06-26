# VIOS and NVStreamer Deployment

This directory contains deployment scripts and configurations for VIOS and NVStreamer services using Docker Compose.

## 📋 Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [Configuration](#configuration)
- [Directory Structure](#directory-structure)
- [Support](#support)

## 🔍 Overview

The one-click deployment script automates the complete setup of VIOS and NVStreamer services with:

- **Automated Prerequisites Check**: Docker, Docker Compose, NVIDIA runtime
- **Smart Configuration**: Auto-detection of system parameters (non-interactive by default; pass `--interactive` to prompt)
- **Service Management**: Deploy, stop, and manage VIOS and NVStreamer services
- **Health Monitoring**: Built-in health checks and service verification
- **Scalable Architecture**: Support for multiple NVStreamer instances (1–5) and an SDRC mode toggle for VIOS

### CLI Syntax

```
python3 oneclick_dc_deployment.py [ACTION] [TARGET] [OPTIONS]
python3 oneclick_dc_deployment.py [ACTION] [--target TARGET] [OPTIONS]

Actions:  deploy (default) | stop | config-only
Targets:  vst (default; alias: vios) | nvstreamer | all
```

Run `python3 oneclick_dc_deployment.py --help` for the full flag list.

## 🛠️ Prerequisites

### System Requirements
- **OS**: Ubuntu 20.04+ or compatible Linux distribution
- **Python**: 3.6 or higher
- **Docker**: Latest version with Docker Compose v2
- **NVIDIA Docker**: Runtime for GPU support
- **Permissions**: Sudo privileges for system configuration

## 🚀 Quick Start

### 1. Basic Deployment

```bash
cd stream-processing
python3 oneclick_dc_deployment.py
```

### 2. Fully Automated Deployment

```bash
python3 oneclick_dc_deployment.py deploy --force
```

`--force` skips the "existing deployment detected" prompt. Smart defaults are applied automatically — add `--interactive` if you want the old prompt-driven flow.

## 📚 Usage Examples

All commands below assume you are inside `services/vios/deployment/stream-processing/`. Adjust the path if invoking from elsewhere.

### Deployment Options

#### Standard Deployment

```bash
# Non-interactive deployment with smart defaults (the default)
python3 oneclick_dc_deployment.py deploy --force

# Prompt for every value
python3 oneclick_dc_deployment.py deploy --interactive
```

#### Target-Specific Deployment

```bash
# Deploy VIOS stream-processor (default; --target vst, or --target vios for backwards compat)
python3 oneclick_dc_deployment.py deploy --force

# Deploy only NVStreamer services
python3 oneclick_dc_deployment.py deploy --target nvstreamer --force

# Deploy VIOS + NVStreamer together
python3 oneclick_dc_deployment.py deploy --target all --force
```

#### Enhanced Deployment

```bash
# Deploy with monitoring (Grafana, Prometheus)
python3 oneclick_dc_deployment.py deploy --with-monitoring --force

# Always pull the latest images before bringing services up
python3 oneclick_dc_deployment.py deploy --pull-always --force
```

#### Clean Deployment

```bash
# Complete clean start (stops existing, removes VST volume data)
python3 oneclick_dc_deployment.py deploy --fresh-start --force
```

### Service Management

#### Stop Services

```bash
# Stop all services
python3 oneclick_dc_deployment.py stop

# Stop only VIOS services
python3 oneclick_dc_deployment.py stop vst

# Stop only NVStreamer services
python3 oneclick_dc_deployment.py stop nvstreamer

# Stop and also remove persistent data:
#   stop vst --clean         -> remove VST volume directory
#   stop nvstreamer --clean  -> remove NVStreamer videos directory
#   stop --clean             -> remove both
python3 oneclick_dc_deployment.py stop --clean
```

#### Configuration Only

```bash
# Update configuration files without deployment
python3 oneclick_dc_deployment.py config-only
```

### Configuration Overrides

#### Network & Path Configuration

```bash
# Override host IP address
python3 oneclick_dc_deployment.py deploy --host 192.168.1.100 --force

# Override VIOS config path
python3 oneclick_dc_deployment.py deploy --config-path /custom/config/path --force

# Override VIOS volume path
python3 oneclick_dc_deployment.py deploy --volume-path /custom/volume/path --force

# Use a specific NVStreamer videos directory (used as-is for every instance)
python3 oneclick_dc_deployment.py deploy --nvstreamer-video-path /custom/nvstreamer/videos --force
```

#### Multi-Instance NVStreamer

```bash
# Run N NVStreamer instances (1..5). Updates COMPOSE_PROFILES to nvstreamer-1..nvstreamer-N.
python3 oneclick_dc_deployment.py deploy --target nvstreamer --instances 3 --force

# Single-instance NVStreamer + VST together
python3 oneclick_dc_deployment.py deploy --target all --instances 1 --force
```

#### Image Tag Overrides

```bash
# Override the tag for both stream-processor and sensor images (most common dev case)
python3 oneclick_dc_deployment.py deploy --all-tag v2.1.0 --force

# Override individual service tags
python3 oneclick_dc_deployment.py deploy --streamprocessor-tag v2.1.1 --sensor-tag v2.1.2 --force

# Override the NVStreamer image tag
python3 oneclick_dc_deployment.py deploy --nvstreamer-tag v1.5.0 --force
```

#### Image Registry / Repository Overrides

```bash
# Swap the registry/org prefix only (basename and tag preserved)
# Useful when the build script tagged images with a custom org, e.g. vios/vst-sensor:<tag>
python3 oneclick_dc_deployment.py deploy --image-registry vios --nvstreamer-image nvstreamer --force

# Full image reference overrides (replace everything but the tag — pair with --*-tag)
# Useful when local builds have different image names than the shipped nvcr.io refs.
python3 oneclick_dc_deployment.py deploy \
  --streamprocessor-image vios/vst-streamprocessing --streamprocessor-tag latest \
  --sensor-image vios/vst-sensor --sensor-tag latest \
  --nvstreamer-image nvstreamer --nvstreamer-tag latest \
  --force
```

### Deployment Mode (SDRC vs direct)

VIOS supports two topologies, selected via the toggle block in `docker-compose/compose.env`:

- **Direct mode** (default): sensor-MS posts `/api/v1/proxy/stream/add` directly to the stream-processor pod on `:30001`. No SDR/Envoy in the data path. 4 containers, single-pod only.
- **SDRC mode**: `sdr-controller` (sdr-mw-l) plus its init containers route stream-bound APIs via Envoy on `:10000`. 8 containers, header-routed.

Switch by editing `docker-compose/compose.env` (comment one block, uncomment the other), then `docker compose up` or re-run the deployment script. See the comment block at the top of `compose.env` for the exact lines to flip.

## ⚙️ Configuration

### Environment Files

The script automatically configures these environment files depending on the target:

- `stream-processing/docker-compose/compose.env` — VST + SDRC mode toggle (target: `vst`)
- `stream-processing/docker-compose/nvstreamer/compose.env` — NVStreamer configuration (target: `nvstreamer`)

### Key Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `HOST_IP` | Server IP address | Auto-detected |
| `VST_CONFIG_PATH` | VST configuration directory | `./configs/` |
| `VST_VOLUME` | VST volume directory | `./vst_volume/` |
| `VST_INGRESS_HTTP_PORT` | VST web interface port | `30888` |
| `STREAM_PROCESSOR_HTTP_PORT_1` | Stream-processor HTTP port | `30001` |
| `VST_USE_SDRC` | Deployment mode toggle (`false` = direct, `true` = SDRC) | `false` |
| `NVSTREAMER_HTTP_PORT_1..N` | NVStreamer instance HTTP ports | `31000–31004` |

### Network Buffer Optimization

The script automatically configures system network buffers for optimal performance.

## 📁 Directory Structure

```
deployment/
├── 1click_README.md                                # This file
└── stream-processing/
    ├── oneclick_dc_deployment.py                   # Main deployment script
    └── docker-compose/                             # VST stream-processor compose
        ├── compose.env                             # VST_USE_SDRC toggle + base config
        ├── docker-compose.yaml                     # sensor-ms, streamprocessing-ms-1, vst-ingress, centralizedb
        ├── configs/                                # nginx-vst.conf (direct), nginx-vst-sdrc.conf (SDRC), vst_config.json
        ├── sdrc/                                   # SDRC overlay (sdr-controller + init chain) — gated by COMPOSE_PROFILES=sdrc
        └── nvstreamer/                             # NVStreamer compose
            ├── compose.env
            └── docker-compose.yaml
```

## 🌐 Access URLs

After successful deployment, services are accessible at:

| Service | URL | Description |
|---------|-----|-------------|
| VIOS UI | `http://<HOST_IP>:30888/vst/#/dashboard` | Main VIOS interface |
| NVStreamer 1 | `http://<HOST_IP>:31000/#/dashboard` | NVStreamer instance 1 (default) |
| NVStreamer 2 | `http://<HOST_IP>:31001/#/dashboard` | NVStreamer instance 2 (if enabled) |
| NVStreamer 3 | `http://<HOST_IP>:31002/#/dashboard` | NVStreamer instance 3 (if enabled) |
| NVStreamer 4 | `http://<HOST_IP>:31003/#/dashboard` | NVStreamer instance 4 (if enabled) |
| NVStreamer 5 | `http://<HOST_IP>:31004/#/dashboard` | NVStreamer instance 5 (if enabled) |
| Grafana | `http://<HOST_IP>:3000` | Monitoring dashboard (if `--with-monitoring`) |

## 📞 Support

### Getting Help

1. **Check Logs**: Always check Docker Compose logs first
2. **Verify Prerequisites**: Ensure all system requirements are met
3. **Network Connectivity**: Verify ports are accessible
4. **Resource Availability**: Check CPU, memory, and disk space

### Script Options

For complete list of options:

```bash
python3 oneclick_dc_deployment.py --help
```

**Note**: This deployment script includes advanced network optimization and safety features. It automatically configures system-wide network buffers with proper validation and rollback mechanisms to ensure optimal performance without compromising system stability.

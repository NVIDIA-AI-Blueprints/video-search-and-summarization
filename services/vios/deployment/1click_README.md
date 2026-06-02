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
- **Smart Configuration**: Auto-detection of system parameters with user confirmation
- **Service Management**: Deploy, stop, and manage VIOS and NVStreamer services
- **Health Monitoring**: Built-in health checks and service verification
- **Scalable Architecture**: Support for multiple NVStreamer instances and VIOS services

### CLI Syntax

```
python3 oneclick_dc_deployment_for_dev.py [ACTION] [--target TARGET] [OPTIONS]

Actions:  deploy (default) | stop | config-only
Targets:  vios (default)   | nvstreamer | all
```

## 🛠️ Prerequisites

### System Requirements
- **OS**: Ubuntu 20.04+ or compatible Linux distribution
- **Python**: 3.6 or higher
- **Docker**: Latest version with Docker Compose
- **NVIDIA Docker**: Runtime for GPU support
- **Permissions**: Sudo privileges for system configuration

## 🚀 Quick Start

### 1. Basic Deployment
```bash
cd deployment
python3 oneclick_dc_deployment_for_dev.py
```

### 2. Fully Automated Deployment
```bash
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
```

### 3. Copy Deployment Configuration
```bash
# Copy scaling folder from another VMS shim directory
python3 oneclick_dc_deployment_for_dev.py --path /path/to/vms/vms_shim/deployment

```
**Note**: Run the above command only when the script is present in another directory.

## 📚 Usage Examples

### Deployment Options

#### Standard Deployment
```bash
# Interactive deployment with smart defaults
python3 oneclick_dc_deployment_for_dev.py deploy

# Automated deployment without prompts
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
```

#### Target-Specific Deployment
```bash
# Deploy VIOS stream-processor (default)
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force

# Deploy only NVStreamer services
python3 oneclick_dc_deployment_for_dev.py deploy --target nvstreamer --auto --force

# Deploy VIOS + NVStreamer together
python3 oneclick_dc_deployment_for_dev.py deploy --target all --auto --force
```

#### Enhanced Deployment
```bash
# Deploy with MinIO object storage
python3 oneclick_dc_deployment_for_dev.py deploy --with-minio --auto --force

# Deploy with monitoring (Grafana, Prometheus)
python3 oneclick_dc_deployment_for_dev.py deploy --with-monitoring --auto --force

# Deploy with both MinIO and monitoring
python3 oneclick_dc_deployment_for_dev.py deploy --with-minio --with-monitoring --auto --force
```

#### Clean Deployment
```bash
# Complete clean start (removes all data)
python3 oneclick_dc_deployment_for_dev.py deploy --fresh-start --auto --force
```

### Service Management

#### Stop Services
```bash
# Stop all services
python3 oneclick_dc_deployment_for_dev.py stop

# Stop only VIOS services
python3 oneclick_dc_deployment_for_dev.py stop --target vios

# Stop only NVStreamer services
python3 oneclick_dc_deployment_for_dev.py stop --target nvstreamer
```

#### Configuration Only
```bash
# Update configuration files without deployment
python3 oneclick_dc_deployment_for_dev.py config-only
```

### Configuration Overrides

#### Network Configuration
```bash
# Override host IP address
python3 oneclick_dc_deployment_for_dev.py deploy --port 192.168.1.100 --auto --force

# Override VIOS config path
python3 oneclick_dc_deployment_for_dev.py deploy --config-path /custom/config/path --auto --force

# Override VIOS volume path
python3 oneclick_dc_deployment_for_dev.py deploy --volume-path /custom/volume/path --auto --force

# Override NVStreamer base path
python3 oneclick_dc_deployment_for_dev.py deploy --nvstreamer-path /custom/nvstreamer/path --auto --force
```

#### Image Tag Overrides
```bash
# Override all service image tags
python3 oneclick_dc_deployment_for_dev.py deploy --all-tag v2.1.0 --auto --force

# Override specific service tags
python3 oneclick_dc_deployment_for_dev.py deploy --sensor-tag v2.1.1 --rtsp-tag v2.1.2 --auto --force

# Override MCP and NVStreamer tags
python3 oneclick_dc_deployment_for_dev.py deploy --mcp-tag latest --nvstreamer-tag v1.5.0 --auto --force
```

## ⚙️ Configuration

### Environment Files
The script automatically configures these environment files depending on the target:

- `stream-processing/docker-compose/compose.env` - VIOS config (target: vios)
- `stream-processing/docker-compose/nvstreamer/compose.env` - NVStreamer configuration

### Key Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `HOST_IP` | Server IP address | Auto-detected |
| `VST_CONFIG_PATH` | VST configuration directory | `./configs/` |
| `VST_VOLUME_PATH` | VST volume directory | `./vst_volume/` |
| `NVSTREAMER_BASE_PATH` | NVStreamer videos directory | `/home/ubuntu/nvstreamer_videos` |
| `VST_HTTP_PORT` | VST web interface port | `30888` |
| `NVSTREAMER_PORTS` | NVStreamer instance ports | `31000-31004` |

### Network Buffer Optimization
The script automatically configures system network buffers for optimal performance



## 📁 Directory Structure

```
deployment/
├── 1click_README.md                    # This file
├── oneclick_dc_deployment_for_dev.py   # Main deployment script
└── stream-processing/                  # Stream-processor compose (--target vios)
    └── docker-compose/
        ├── compose.env
        ├── docker-compose.yaml
        ├── configs/
        └── nvstreamer/                 # NVStreamer compose
            ├── compose.env
            └── docker-compose.yaml
```

## 🌐 Access URLs

After successful deployment, services are accessible at:

| Service | URL | Description |
|---------|-----|-------------|
| VIOS UI | `http://<HOST_IP>:30888/vios/#/dashboard` | Main VIOS interface |
| NVStreamer 1 | `http://<HOST_IP>:31000/#/dashboard` | NVStreamer instance 1 |
| NVStreamer 2 | `http://<HOST_IP>:31001/#/dashboard` | NVStreamer instance 2 |
| NVStreamer 3 | `http://<HOST_IP>:31002/#/dashboard` | NVStreamer instance 3 |
| NVStreamer 4 | `http://<HOST_IP>:31003/#/dashboard` | NVStreamer instance 4 |
| NVStreamer 5 | `http://<HOST_IP>:31004/#/dashboard` | NVStreamer instance 5 |
| Grafana | `http://<HOST_IP>:3000` | Monitoring dashboard (if enabled) |
| MinIO Console | `http://<HOST_IP>:9001` | Object storage console (if enabled) |


## 📞 Support

### Getting Help

1. **Check Logs**: Always check Docker Compose logs first
2. **Verify Prerequisites**: Ensure all system requirements are met
3. **Network Connectivity**: Verify ports are accessible
4. **Resource Availability**: Check CPU, memory, and disk space

### Script Options

For complete list of options:
```bash
python3 oneclick_dc_deployment_for_dev.py --help
```


**Note**: This deployment script includes advanced network optimization and safety features. It automatically configures system-wide network buffers with proper validation and rollback mechanisms to ensure optimal performance without compromising system stability.
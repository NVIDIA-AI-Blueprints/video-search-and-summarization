# VSS Engine Docker Build

This directory contains files needed to build the VSS Engine Docker container image.

## Prerequisites

- Docker installed and configured
- Access to NVIDIA NGC registry (NGC API key) to download the vss-engine-base image.

## Building the Container

1. Set your NGC API key as an environment variable:

```bash
export NGC_API_KEY=<your_ngc_api_key>
```

2. Login to NGC registry:

```bash
docker login nvcr.io -u '$oauthtoken' -p $NGC_API_KEY
```

3. Build the container:

```bash
export BASE_IMG_NAME="nvcr.io/nvidia/blueprint/vss-engine-base:2.4.1"

# For DGX Spark
# export BASE_IMG_NAME="nvcr.io/nvidia/blueprint/vss-engine-base:2.4.1-sbsa"

DOCKER_BUILDKIT=1 docker build --network host --progress=plain --build-arg "BASE_IMAGE=$BASE_IMG_NAME" -t vss-engine:<image_tag> -f Dockerfile ..
```







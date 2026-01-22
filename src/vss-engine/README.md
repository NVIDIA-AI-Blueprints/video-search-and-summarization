# VSS Engine Development Guide

This guide provides instructions for modifying the VSS Engine source code, building a new Docker image, and updating the Helm chart for deployment.

## Directory Structure

```
vss-engine/
├── binaries/      # Binaries like whl files
├── config/        # Configuration files
├── docker/        # Docker build files
├── src/           # Source code
├── TritonGdino/   # Triton model files
└── start_via.sh   # Startup script
```

## Prerequisites

- Docker installed and configured
- Access to NVIDIA NGC registry (NGC API key)
- Helm 3.x installed
- Kubernetes cluster access

## Modifying Source Code

1. Navigate to the source code directory and make your desired changes.

2. If modifying model files, update the files in the TritonGdino directory.

3. Similarly, config files can be changed in config directory.

4. The video timeline component source is available in ``../video_timeline``. To use a modified version, build the whl file
   from the source code and copy it to the ``binaries`` directory. If needed, update the whl file name in ``docker/Dockerfile``.

## Building New Docker Image

1. Set up NGC credentials:
```bash
export NGC_API_KEY=<your_ngc_api_key>
docker login nvcr.io -u '$oauthtoken' -p $NGC_API_KEY
```

2. Set the base image and new version:
```bash
export BASE_IMAGE="nvcr.io/nvidia/blueprint/vss-engine-base:2.4.1"
export NEW_VERSION="x.y.z"  # Replace with your version
```

3. Build the Docker image:
```bash
cd docker/
DOCKER_BUILDKIT=1 docker build \
  --network host \
  --progress=plain \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  -t vss-engine:${NEW_VERSION} \
  -f Dockerfile ..
```

4. Tag and push the image to your registry:
```bash
docker tag vss-engine:${NEW_VERSION} <your-registry>/vss-engine:${NEW_VERSION}
docker push <your-registry>/vss-engine:${NEW_VERSION}
```

## Deploying with Helm

1. Create a Kubernetes secret for pulling images from your private registry:
```bash
kubectl create secret docker-registry private-reg-secret \
  --docker-server=<your-registry> \
  --docker-username=<user-name> \
  --docker-password=<password>
```

Please also create additional secrets necessary, following the [VSS Helm Quickstart Guide](https://docs.nvidia.com/vss/latest/content/vss_dep_helm.html#deploy-using-helm).

2. Create a custom values override file (e.g., `my-values.yaml`):

Please make sure to manually replace the ``${NEW_VERSION}`` tag in the file.

```yaml
# my-values.yaml
vss:
  applicationSpecs:
    vss-deployment:
      containers:
        vss:
          # Update to override with custom VSS image
          image:
            repository: <your-registry>/vss-engine
            tag: ${NEW_VERSION} #please manually replace the version tag
  # Add this if using private registry
  imagePullSecrets:
    - name: private-reg-secret
```

3. Install/Upgrade using the override values:
```bash
helm install vss-blueprint nvidia-blueprint-vss-2.4.1.tgz \
    --set global.ngcImagePullSecretName=ngc-docker-reg-secret \
    -f my_values.yaml
```

Please set ``global.ngcImagePullSecretName`` to NGC secret following documentation [here](https://docs.nvidia.com/vss/latest/content/vss_dep_helm.html#deploy-using-helm). 

This is necessary to pull docker images from NGC for the various NVIDIA NIMs used by VSS in [the default helm chart deployment topology](https://docs.nvidia.com/vss/latest/content/vss_dep_helm.html#default-deployment-topology-and-models-in-use).

For more detailed information about Helm deployment and configuration, please refer to the [VSS Helm Quickstart Guide](https://docs.nvidia.com/vss/latest/content/vss_dep_helm.html#deploy-using-helm).

## Deploying with Docker Compose
Export the `VIA_IMAGE` environment variable before deploying VSS to use the new container image.
```bash
export VIA_IMAGE=<your-image>
```

For more detailed information about Docker Compose deployment and configuration, please refer to the [VSS Docker Compose Quickstart Guide for x86 here](https://docs.nvidia.com/vss/latest/content/vss_dep_docker_compose_x86.html), or [VSS Docker Compose Quickstart Guide for ARM here](https://docs.nvidia.com/vss/latest/content/vss_dep_docker_compose_arm.html).

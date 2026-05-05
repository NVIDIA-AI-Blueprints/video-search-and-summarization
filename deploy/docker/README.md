# Deployment Package

This deployment package contains the necessary components for deploying the application.

## Contents

- `warehouse/` - Warehouse service components
- `vlm-as-verifier/` - VLM as verifier service components
- `build.sh` - Build script for setting up the deployment
- `compose.yml` - Docker Compose configuration
- `MANIFEST` - Build metadata and version information

## Usage

1. Extract the tar.gz file:
   ```bash
   tar -xzf deploy-deployment-package.tar.gz
   cd docker-compose
   ```

2. Run the build script:
   ```bash
   ./build.sh
   ```

3. Start the services using Docker Compose:
   ```bash
   export MODE=2d or 3d 
   export BP_PROFILE=bp_wh or bp_wh_kafka or bp_wh_redis
   
   # Profile variants:
   # 2d:
   #   bp_wh
   #   bp_wh_kafka
   #   bp_wh_redis
   # 3d:
   #   bp_wh_kafka
   #   bp_wh_redis

	# Update .env file with the appropriate values
   docker compose -f compose.yml up -d
   ```

## Requirements

- Docker
- Docker Compose
- bash

## Building from Source

To build this package from source:

```bash
cd deploy/docker
make
```

This will create a `tar.gz` files with shape name defined at build.yml.

## Support

For issues and questions, please refer to the main documentation.


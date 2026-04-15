# This Dockerfile is used when Harbor runs in local Docker mode.
# When using BrevEnvironment, the Brev instance IS the environment
# and setup.sh runs directly on it instead.
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    curl git jq python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY setup.sh /setup.sh
RUN chmod +x /setup.sh

FROM node:22.22.3-bullseye AS builder

# Build web-api-core from source
COPY ./src/web-api-core /web-api-core

# Install dependencies in web-api-core
WORKDIR /web-api-core
RUN npm install

# Copy app and install with locally built web-api-core
RUN mkdir /web-api-app
COPY ./src/app /web-api-app
WORKDIR /web-api-app

RUN mkdir -p /web-api-app/files
RUN npm install --save ../web-api-core
RUN npm install

###################################################
FROM nvcr.io/nvidia/distroless/node:22-v4.0.7 AS node-deploy

COPY --from=builder --chown=1000:1000 /web-api-core /web-api-core
COPY --from=builder --chown=1000:1000 /web-api-app /web-api-app
COPY --chown=1000:1000 ./configs /configs
WORKDIR /web-api-app
USER 1000:1000

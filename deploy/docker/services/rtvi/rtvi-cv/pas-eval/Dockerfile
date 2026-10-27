ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-cv@sha256:51fad387d3ef9673fc2c4822954a4d74280d9c841d62d5bffc484e3e8dfe9909
FROM ${BASE_IMAGE}
USER root
COPY frame-sensor-identity.patch /tmp/frame-sensor-identity.patch
RUN cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app \
    && patch --fuzz=0 -p0 < /tmp/frame-sensor-identity.patch \
    && make -j8 CUDA_VER=13.2 NVDS_VERSION=9.1 \
    && make install CUDA_VER=13.2 NVDS_VERSION=9.1

COPY source-identity-before-batching.patch source_identity_meta.h test_source_identity.cpp /tmp/source-identity/
RUN cd /opt/nvidia/deepstream/deepstream/sources/libs/gstnvdscustomhelper \
    && cp /tmp/source-identity/source_identity_meta.h . \
    && patch --fuzz=0 -p0 < /tmp/source-identity/source-identity-before-batching.patch \
    && make -j8 CUDA_VER=13.2 \
    && make install CUDA_VER=13.2 \
    && g++ -std=c++17 -I. -I../../includes $(pkg-config --cflags gstreamer-1.0) \
       /tmp/source-identity/test_source_identity.cpp -o /tmp/test_source_identity \
       -Wl,-rpath,/opt/nvidia/deepstream/deepstream/lib -L/opt/nvidia/deepstream/deepstream/lib -lnvdsgst_meta -lnvds_meta $(pkg-config --libs gstreamer-1.0) \
    && /tmp/test_source_identity

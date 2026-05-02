# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
#   ES_VERSION    - Elasticsearch image tag (9.3.3)
#   CUDA_VERSION  - CUDA runtime version (12.9.0)
#   CUVS_VERSION  - Elastic cuVS tarball version (25.12.0)
#

FROM nvidia/cuda:12.9.0-cudnn-runtime-ubuntu22.04 AS cuda12libs
ARG CUVS_VERSION=25.12.0
RUN apt-get update && apt-get install -y --no-install-recommends --allow-change-held-packages \
    libnccl2 curl tar gzip libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /out/cuvs && cd /out/cuvs \
    && curl -fLO "https://storage.googleapis.com/elasticsearch-cuvs-snapshots/libcuvs/libcuvs-${CUVS_VERSION}.tar.gz" \
    && tar -xzf "libcuvs-${CUVS_VERSION}.tar.gz" && rm -f "libcuvs-${CUVS_VERSION}.tar.gz" \
    && if [ -d "${CUVS_VERSION}" ]; then mv "${CUVS_VERSION}"/* .; rmdir "${CUVS_VERSION}" 2>/dev/null || true; fi \
    && cp -P /usr/lib/x86_64-linux-gnu/libgomp.so* /out/cuvs/

FROM docker.elastic.co/elasticsearch/elasticsearch:9.3.3

ENV ES_HOME=/usr/share/elasticsearch
ENV LIBCUVS_DIR=/opt/cuvs
ENV CUDA12_LIBS=/opt/cuda12-libs
ENV LD_LIBRARY_PATH=${LIBCUVS_DIR}:${CUDA12_LIBS}:${LD_LIBRARY_PATH}
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV ES_SETTING_VECTORS_INDEXING_USE__GPU=true

COPY --from=cuda12libs /usr/local/cuda/lib64/ "${CUDA12_LIBS}/"
COPY --from=cuda12libs /usr/lib/x86_64-linux-gnu/libnccl*.so* "${CUDA12_LIBS}/"
COPY --from=cuda12libs /out/cuvs/ "${LIBCUVS_DIR}/"

USER root
RUN chown -R 1000:1000 "${ES_HOME}" "${LIBCUVS_DIR}" "${CUDA12_LIBS}"
USER 1000:1000
WORKDIR ${ES_HOME}

EXPOSE 9200 9300
ENTRYPOINT ["/usr/share/elasticsearch/bin/elasticsearch"]

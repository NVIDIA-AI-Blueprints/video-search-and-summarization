ARG CARLA_BASE_IMAGE
FROM ${CARLA_BASE_IMAGE}

ARG ADDITIONAL_MAP_LINK

USER root

# Disable audio warnings
ENV SDL_AUDIODRIVER=dummy
ENV DISPLAY=

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Optionally import official additional maps. Leave ADDITIONAL_MAP_LINK empty to
# skip this long download during image build.
RUN if [ -n "${ADDITIONAL_MAP_LINK}" ]; then \
        curl -fL --retry 3 "${ADDITIONAL_MAP_LINK}" --output AdditionalMaps_0.9.16.tar.gz \
        && tar -xvzf AdditionalMaps_0.9.16.tar.gz \
        && rm AdditionalMaps_0.9.16.tar.gz; \
    else \
        echo "ADDITIONAL_MAP_LINK not set; skipping CARLA additional maps import."; \
    fi

# Import a custom map
# COPY Montague.tar.gz .
# RUN tar -xvzf Montague.tar.gz && rm Montague.tar.gz

USER carla

ENTRYPOINT ["/workspace/CarlaUE4.sh", "-carla-rpc-port=2000",  "-carla-server", "-RenderOffScreen"]

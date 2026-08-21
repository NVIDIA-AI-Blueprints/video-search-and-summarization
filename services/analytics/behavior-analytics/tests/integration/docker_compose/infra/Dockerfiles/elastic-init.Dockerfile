FROM alpine:3.23.4

WORKDIR /opt/mdx/

COPY ./elk/init-scripts ./init-scripts

# Single RUN: avoid apk at container start (can hang in some CI). Build once, run scripts at start.
RUN chmod +x ./init-scripts/*.sh && apk add --no-cache bash curl

FROM alpine:3.23.4

# Create a working directory
WORKDIR /opt/mdx/

# Copy the init scripts into the working directory
COPY ./elk/init-scripts ./init-scripts

# Make scripts executable
RUN chmod +x ./init-scripts/*.sh

# Install bash and curl commands.
RUN apk update && apk add bash

RUN apk --no-cache add curl

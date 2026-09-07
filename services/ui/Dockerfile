ARG USER_ID=65532
ARG GROUP_ID=65532
ARG BUILD_TYPE=prod


# Build the architecture-independent Next.js output once on the native build
# host. SWC and Turbo are build-only dependencies, so their architecture does
# not need to match the runtime image.
FROM --platform=$BUILDPLATFORM node:22.23.2 AS builder
ARG USER_ID
ARG GROUP_ID

WORKDIR /repo

# Copy source files
COPY services/ui/ services/ui/

ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_BUILD_TRACES=false

# `npm ci` and the subsequent `turbo` invocation must run from the UI workspace root,
# which is where `package.json` and `package-lock.json` live after the COPY above.
WORKDIR /repo/services/ui

RUN npm ci
# Matches CI: run `bundle` after `build` where defined (e.g. nv-metropolis-bp-vss-ui). The runner
# stage still COPYs standalone, `.next/static`, and `public` explicitly for custom-server.js, so this
# mainly keeps Docker builds aligned with `turbo run build bundle` and validates those tasks.
RUN npx turbo run build bundle

# Set permissions for __ENV.js files to be writable at runtime
RUN chmod -R 755 apps/nv-metropolis-bp-vss-ui/public && \
    chown -R ${USER_ID}:${GROUP_ID} apps/nv-metropolis-bp-vss-ui/public


# Next.js traces Sharp into the standalone runtime. Resolve only that small
# native dependency set for each target architecture while still running npm
# natively on the build host; this avoids repeating the full UI build in QEMU.
FROM --platform=$BUILDPLATFORM node:22.23.2 AS target-native-deps
ARG TARGETARCH

COPY services/ui/package-lock.json /tmp/package-lock.json

# npm validates direct package CPU against the build host, so --force is needed
# for the cross-target fetch. Scripts stay disabled and versions are lock-pinned.
RUN case "${TARGETARCH}" in \
      amd64) npm_cpu=x64 ;; \
      arm64) npm_cpu=arm64 ;; \
      *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    sharp_package="@img/sharp-linux-${npm_cpu}" && \
    libvips_package="@img/sharp-libvips-linux-${npm_cpu}" && \
    sharp_version="$(node -p \
      "require('/tmp/package-lock.json').packages['node_modules/${sharp_package}'].version")" && \
    libvips_version="$(node -p \
      "require('/tmp/package-lock.json').packages['node_modules/${libvips_package}'].version")" && \
    npm_config_cpu="${npm_cpu}" npm_config_os=linux \
      npm install \
        --prefix /target-native-deps \
        --force \
        --ignore-scripts \
        --no-package-lock \
        "${sharp_package}@${sharp_version}" \
        "${libvips_package}@${libvips_version}"


# Conditional runner phase
FROM nvcr.io/nvidia/distroless/node:22-v4.1.2 AS runner-prod
ARG BUILD_TYPE

FROM nvcr.io/nvidia/distroless/node:22-v4.1.2-dev AS runner-dev
ARG BUILD_TYPE


FROM runner-${BUILD_TYPE} AS runner
ARG USER_ID
ARG GROUP_ID


WORKDIR /repo


# Copy standalone output from each app into its own subdirectory (read-only)
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/.next/standalone ./apps/nv-metropolis-bp-vss-ui/
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/.next/static ./apps/nv-metropolis-bp-vss-ui/apps/nv-metropolis-bp-vss-ui/.next/static
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=755 /repo/services/ui/apps/nv-metropolis-bp-vss-ui/public ./apps/nv-metropolis-bp-vss-ui/apps/nv-metropolis-bp-vss-ui/public
COPY --from=target-native-deps --chmod=555 /target-native-deps/node_modules/@img ./apps/nv-metropolis-bp-vss-ui/node_modules/@img

# Copy required configuration files with their dependencies for next-runtime-env in custom-server.js (read-only)
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/next-runtime-env ./node_modules/next-runtime-env
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/chalk ./node_modules/chalk
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/ansi-styles ./node_modules/ansi-styles
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/supports-color ./node_modules/supports-color
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/color-convert ./node_modules/color-convert
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/color-name ./node_modules/color-name
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/node_modules/has-flag ./node_modules/has-flag

# Copy custom server (read-only)
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=555 /repo/services/ui/custom-server.js ./custom-server.js

# Copy license files (read-only): LICENSE (MIT) and LICENSE-3rd-party.txt.
COPY --from=builder --chown=${USER_ID}:${GROUP_ID} --chmod=444 /repo/services/ui/LICENSE* ./

# The NVIDIA Software License Agreement is not vendored in this source tree.
# BuildKit performs this download itself, so no HTTP client is needed in any
# stage, and --checksum verifies the bytes before the layer is created: a
# CDN error page or a silently replaced document fails the build. Owned by
# root so the non-root runtime process cannot modify it.
ADD --chown=root:root --chmod=444 \
    --checksum=sha256:f5ad3da1faf5734a1f9ea1ad4ab6862236bf061a0ebbb02dea0fb726b81d7e0d \
    https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/NVIDIA-Software-License-Agreement-2026.5.7.pdf \
    ./NVIDIA-Software-License-Agreement.pdf

# Set environment variables
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOST=0.0.0.0


# Create separate stages for prod and dev commands
FROM runner AS runner-prod-cmd
ARG USER_ID
ARG GROUP_ID

# Ensure we run as nonroot user
USER ${USER_ID}:${GROUP_ID}
CMD ["node", "custom-server.js"]


FROM runner AS runner-dev-cmd
ARG USER_ID
ARG GROUP_ID

USER ${USER_ID}:${GROUP_ID}


# Final stage that selects the appropriate command based on BUILD_TYPE
FROM runner-${BUILD_TYPE}-cmd
ARG USER_ID
ARG GROUP_ID

USER ${USER_ID}:${GROUP_ID}

# Example sandbox Dockerfile — OpenClaw harness on the NemoClaw-lineage base.
# tags: [nemoclaw-lineage, openclaw]
# The Dockerfile is the SOURCE OF TRUTH for a profile: edit freely — install any
# CLI/tools you need. The harness bakes it into a content-addressed image; the run
# manifest records the digest, so the exact experiment is repeatable.
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}
# openclaw + node 22 already baked in the base (~/.nvm, nvm use 22)

# your skills are COPY'd here by the profile (skills/<name> → /opt/skills/<name>)
# your vss config.json is COPY'd to /home/ubuntu/.vss/config.json

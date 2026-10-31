# Template for compose/.env.
# setup.sh copies this file to .env when none exists, then appends the
# credentials and paths it prompts for. To add it to an existing .env:
#   cat .env.tpl >> .env
# Values set in .env take precedence over the image defaults in the compose files.

# VSS 3.3 images: LVS 3.3.0 GA and RT-VLM 3.3.0-26.10.2.
# x86, Jetson Thor and Orin NX
LVS_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization:develop-21f686e37db7
RTVI_VLM_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-7ddad4454e4d

# DGX Spark (use these instead of the two lines above; setup.sh switches them on Spark)
# LVS_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization:develop-21f686e37db7-sbsa
# RTVI_VLM_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-7ddad4454e4d-sbsa

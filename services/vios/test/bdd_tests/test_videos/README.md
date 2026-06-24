<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# test_videos

Sample clips used to seed NVStreamer for the BDD suite:

| File | Codec / container |
|---|---|
| `sample_10sec_h264.mp4` | H.264 / MP4 |
| `sample_10sec_h264.mkv` | H.264 / MKV |
| `sample_10sec_h265.mp4` | H.265 / MP4 |
| `sample_10sec_h265.mkv` | H.265 / MKV |

## These binaries are NOT in the repository

They are **baked into the BDD test image** at build time
(`Dockerfile` -> `COPY test_videos/ ./test_videos/` -> `/app/test_videos`) and
pushed to `gitlab-master.nvidia.com:5005/l4tmm/vms_shim/bdd_tests`. The pushed
image is the source of truth. `.gitignore` excludes the binaries so they never
land in git history.

At test time the session prerequisite in `conftest.py`
(`scripts/stream_prerequisite.py`) uploads these clips to NVStreamer and runs a
VST sensor scan when NVStreamer has no streams.

## How to obtain the clips for a rebuild

The files must be present in this directory before building/pushing the image.
Obtain them from any of:

- The current published image:
  `docker run --rm <bdd-image> tar -C /app/test_videos -cf - . | tar -C test/bdd_tests/test_videos -xf -`
- A local backup (e.g. created during the migration): `~/vms_shim_test_videos_backup/`
- Regenerate equivalent 10-second H.264/H.265 clips with ffmpeg.

After placing the files here, follow `.cursor/skills/bdd-container-update/SKILL.md`
to rebuild, push, and bump the image tag.

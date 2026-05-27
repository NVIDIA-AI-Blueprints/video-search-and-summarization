---
name: "build-help"
description: "Get the exact build.sh command for your VIOS build scenario"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - build
    - docker
    - cmake
  languages:
    - bash
  domain: devops
---

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

Help the user find the right `./build.sh` invocation by asking clarifying questions if needed, then output the exact command(s).

Gather the following from the user's request (ask only for what is missing or ambiguous):

- **What to build**: single module, all modules, container, Helm chart, monolith, nvstreamer, MCP, ingress
- **Module(s)**: sensor, rtspserver, recorder, livestream, replaystream, streambridge, storage, streamprocessing (comma-separated for multiple)
- **Architecture**: x86_64 (default), arm64/aarch64, sbsa
- **Build type**: default, debug, or release
- **Output type**: binary only, package, container
- **Registry**: GitLab (use for dev/testing) or NVCR (production/release only)
- **Push**: push to registry after build?
- **Tag**: specific image tag, or omit for `latest`
- **Docker cache**: use cache (default) or `no-cache`
- **Clean first**: clean before build?

Rules to apply:
- Multiple modules are only valid with `package` or `container` — warn if user requests a plain multi-module build
- Dev/test builds must use `gitlab` flag, not NVCR
- SBSA cross-compiles via Docker automatically
- Always use `./build.sh`, never raw `make`
- If the user wants to clean first, prepend `./build.sh clean` (or show it as a separate step)

Output the exact command, then a one-line explanation of what it does. If there are common follow-up commands (e.g., clean first, then build), show those too.

No emojis.

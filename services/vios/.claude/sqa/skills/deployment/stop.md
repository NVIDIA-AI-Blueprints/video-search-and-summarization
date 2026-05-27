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

# Skill: Stop VIOS Services

Stop deployed VIOS and/or NVStreamer services.

---

## Step 1 — Determine scope

Infer from context. Default to stopping all services.
Only pause to confirm if the user has not specified scope AND active recordings or in-progress tests could be lost (check `docker ps` for running recorder containers).

---

## Step 2 — Stop services

```bash
cd "$(git rev-parse --show-toplevel)/deployment"

# Stop all services
python3 oneclick_dc_deployment_for_dev.py stop

# Stop only VIOS services
python3 oneclick_dc_deployment_for_dev.py stop --target vios

# Stop only NVStreamer services
python3 oneclick_dc_deployment_for_dev.py stop --target nvstreamer
```

---

## Step 3 — Verify shutdown

```bash
# Confirm no VIOS/NVStreamer containers are running
docker ps --format "{{.Names}}" | grep -E "vios|nvstreamer|redis"
# Expected: no output
```

If containers are still running after the stop command, force-remove them:
```bash
docker ps -q --filter "name=vios" | xargs -r docker stop
docker ps -q --filter "name=nvstreamer" | xargs -r docker stop
```

---

## Notes

- `--fresh-start` removes Docker volumes (persistent data). **Do not use unless explicitly requested** — this destroys recordings, configuration, and database state.
- Redis is shared infrastructure; stopping it affects all services.

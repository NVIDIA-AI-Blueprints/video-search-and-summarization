<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

-->

# vss-agent configs by profile

Config is selected by `values.profile`: **base** | **lvs** | **search** | **alerts**.

- **base/config.yml** — from `deployments/developer-workflow/dev-profile-base/vss-agent/configs/config.yml`
- **lvs/config.yml** — from `deployments/developer-workflow/dev-profile-lvs/vss-agent/configs/config.yml`
- **search/config.yml** — from `deployments/developer-workflow/dev-profile-search/vss-agent/configs/config.yml`
- **alerts/config.yml** — from `deployments/developer-workflow/dev-profile-alerts/vss-agent/configs/config.yml`

To refresh from Compose source (from repo root):

```bash
for p in lvs search alerts; do
  mkdir -p helm_dp/vss-agent/configs/$p
  cp deployments/developer-workflow/dev-profile-$p/vss-agent/configs/config.yml helm_dp/vss-agent/configs/$p/config.yml
done
```

(Base is already in the chart; add the same `cp` for `base` if you want to refresh it.)

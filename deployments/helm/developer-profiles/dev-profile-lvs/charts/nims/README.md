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

# nims (umbrella subchart) — LVS profile

Bundles NIM Operator charts. Service names remain `<Release.Name>-<chart-name>`.

From **dev-profile-lvs** root:

```bash
helm dependency update charts/nims
helm dependency update .
```

Parent values: set **`nims.<model-chart>.enabled`** and optional **`hardwareProfile`** under **`nims:`** (see parent `values.yaml` and `values-lvs.yaml`).

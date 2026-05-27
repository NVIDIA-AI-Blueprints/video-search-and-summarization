---
name: "new-adaptor"
description: "Scaffold a new VIOS VMS adaptor following the project's adaptor pattern"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - scaffolding
    - adaptor
    - cpp
  languages:
    - cpp
  domain: backend
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

The user wants to create a new VIOS adaptor. Ask for the adaptor name if not provided.

Then perform the following steps:

1. **Read existing adaptor for reference** — read `src/adaptors/onvif/` or `src/adaptors/native_sensors/` to understand the interface that must be implemented.

2. **Read `src/adaptors/Makefile`** to understand how existing adaptors are registered and linked.

3. **Read `src/adaptors/adaptor_loader.cpp`** to understand how adaptors are discovered at runtime.

4. **Create the directory structure:**
   ```plaintext
   src/adaptors/<name>/
     <Name>Adaptor.h
     <Name>Adaptor.cpp
     Makefile
   ```

5. **Stub `<Name>Adaptor.h`** — implement the required interface. The base interfaces are `include/sensor_control_adaptor.h` and/or `include/sensor_discovery_adaptor.h` depending on what the adaptor provides. Add the NVIDIA SPDX copyright header. Use C++17 idioms.

6. **Stub `<Name>Adaptor.cpp`** — include the header, add the SPDX header, stub all interface methods with `nvlogger`-based not-implemented logs.

7. **Create `src/adaptors/<name>/Makefile`** — model it after an existing adaptor's Makefile.

8. **Register in `src/adaptors/Makefile`** — add the new adaptor as a subdirectory target.

9. Report what was created and what the user still needs to implement (method stubs).

No emojis. Keep output concise.

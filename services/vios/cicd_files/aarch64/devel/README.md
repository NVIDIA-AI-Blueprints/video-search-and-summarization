<!--
SPDX-FileCopyrightText: Copyright (c) 2020-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# aarch64 cross-compilation container

## Build

```
cd cicd_files/aarch64/devel
./build_cross_compile_container.sh
```

Produces `vios-build:aarch64-cross-compiler`. Pass an argument to use a different tag:

```
./build_cross_compile_container.sh <image-tag>
```

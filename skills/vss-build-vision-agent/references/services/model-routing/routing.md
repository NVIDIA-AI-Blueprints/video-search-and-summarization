<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# What the router does

```
before   VSS ──────────────────────────────► vss-llm-nim:8000
after    VSS ──► switchyard:4000 ──► weak  or  frontier
                 (VSS cannot tell the difference)
```

The router is not consulted and does not call back. It receives an ordinary
OpenAI-compatible request, decides, and forwards. VSS never learns a router
exists, which is why no VSS source changes.

Two constraints that affect what you can promise:

- **The router is told nothing about the call.** It sees the prompt, tool
  schemas and history, and infers the kind of call from shape. VSS cannot steer
  a specific call toward the frontier tier.
- **Classification costs a model call.** On measured runs it was a large share
  of the routing bill.

The case for routing is **cost**, not quality: on workloads measured so far,
routing showed no accuracy gain over choosing a model well up front.

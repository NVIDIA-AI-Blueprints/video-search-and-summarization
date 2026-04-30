# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Backend adapter implementations.

Each adapter self-registers via `@register_adapter("name")` when its
module is imported. The factory imports them lazily so optional deps
(e.g. nvidia-rag) are only pulled in for the backend actually configured.
"""

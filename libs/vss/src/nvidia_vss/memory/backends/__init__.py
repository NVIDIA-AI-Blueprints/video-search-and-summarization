# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Concrete ``MemoryStore`` backends.

Heavy backends (Elasticsearch) are imported lazily by ``build_memory_service``.
Named ``backends`` rather than ``adapters`` so it does not collide with the
group payload adapters in ``memory.adapters``.
"""

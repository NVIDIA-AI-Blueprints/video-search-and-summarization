# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# alert-src-path
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

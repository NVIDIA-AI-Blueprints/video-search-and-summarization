# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Configuration package facade.

Config *contracts* (pydantic schemas) live in per-backend modules — ``base``
(shared + ``Settings`` + the ``config_type -> schema`` registry), ``nmp``,
``deft`` — so they're grouped and visible and carry no SDK. This ``__init__``
only re-exports, so existing ``from src.config import X`` / ``import src.config``
importers keep working; the per-backend build/select logic stays in
``src/backends/`` and imports these schemas from here.

Nothing is DEFINED here on purpose: this file is still on its first import
statement while ``base`` builds ``settings``, so anything defined here would be
unreachable from the loaders (hence the registry living in ``base``).
"""

from src.config import base, deft, nmp
from src.config.base import *  # noqa: F403
from src.config.deft import *  # noqa: F403
from src.config.nmp import *  # noqa: F403

# Concatenation (not star-unpacking) so ``__all__`` holds only plain strings
# (Ruff PLE0604) while staying in sync with the per-module export lists.
__all__: list[str] = list(base.__all__) + list(nmp.__all__) + list(deft.__all__)

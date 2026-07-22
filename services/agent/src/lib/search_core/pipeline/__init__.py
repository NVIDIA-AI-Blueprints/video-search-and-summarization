# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Synchronous, composable search pipeline: retrieval legs, scorers, rankers.

One stage kind — ``Ranks -> Ranks`` — with three disciplined sub-kinds:

======================  ==============  ============  ================  ==========
Stage kind              Adds candidates Adds scores   Reorders/filters  IO
======================  ==============  ============  ================  ==========
``retrieve(leg)``       union-append    its leg key   no                retrieval
scorer / enricher       no              named key     no                features
ranker                  no              no            yes               none
======================  ==============  ============  ================  ==========

Stages are free functions chained on a frozen :class:`~.ranks.Ranks` value with
``|`` (or ``.pipe()``); everyday callers use the :mod:`.facade` instead of
composing chains by hand.
"""

from .facade import run_search
from .ranks import Hit
from .ranks import Ranks
from .ranks import Stage

__all__ = ["Hit", "Ranks", "Stage", "run_search"]

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
"""Bridge builder tests: executor construction is lazy and side-effect free."""

from types import SimpleNamespace

from lib.search_core.pipeline import bridge
from lib.search_core.pipeline.facade import SearchDeps


def _runtime_like() -> SimpleNamespace:
    return SimpleNamespace(
        behavior_index="mdx-behavior-2025-01-01",
        behavior_index_wildcard="mdx-behavior-*",
        behavior_es_endpoint="http://es:9200",
        vst_internal_url="http://vst:81",
        vst_external_url="http://vst.example",
    )


class TestBuilders:
    def test_deps_from_runtime_builds_full_executor_set_without_io(self):
        deps = bridge.deps_from_runtime(_runtime_like())  # type: ignore[arg-type]
        assert isinstance(deps, SearchDeps)
        assert callable(deps.embed_exec)
        assert callable(deps.attribute_exec)
        assert callable(deps.object_exec)
        assert callable(deps.object_enrich)
        assert callable(deps.sensor_resolver)

    def test_run_sync_executes_coroutines(self):
        async def _five() -> int:
            return 5

        assert bridge._run_sync(_five()) == 5

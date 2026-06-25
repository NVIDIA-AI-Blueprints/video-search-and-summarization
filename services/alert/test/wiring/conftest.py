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

"""Per-file ``sys.modules`` isolation for the wiring tests.

The wiring test modules in this directory each build their own world of stub
modules by mutating ``sys.modules`` at *module import time* — and they
``assert`` on the very ``Mock`` objects they install. Different files install
*conflicting* stubs for the same module names, and several even
``setattr(...Mock())`` on the **real** shared modules. Because pytest imports
every test module up front during collection, in a normal run this:

* corrupts ~100 unrelated (non-wiring) tests, and
* lets whichever wiring file is imported last win the shared ``sys.modules``
  entries, breaking the others.

These tests are therefore written to be *file-isolated*. This conftest provides
that isolation inside a single process:

1. ``_CLEAN`` captures the pristine, real modules before this directory's test
   modules are imported.
2. A custom ``Module`` collector imports each wiring file, snapshots the exact
   ``sys.modules`` state that file produced (real modules + that file's stubs),
   and then restores ``_CLEAN`` so the next file — and every non-wiring test —
   starts from real modules again.
3. An autouse fixture re-applies a file's snapshot just before each of its
   tests runs, then restores ``_CLEAN`` afterwards.
"""

import sys
import types
from unittest.mock import Mock

import pytest


class _AutoMockModule(types.ModuleType):
    """A stub module that materialises a ``Mock`` for any attribute access."""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        value = Mock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value

# Modules the wiring tests overwrite attribute-by-attribute or replace whole.
# ``metrics.prometheus_metrics`` is intentionally excluded — touching it
# re-registers Prometheus collectors ("Duplicated timeseries").
_WATCHED_MODULES = (
    "metrics",
    "metrics.recorder",
    "vss",
    "clients.redis_handler",
    "mdx",
    "mdx.sink",
    "mdx.utils",
    "mdx.event_bridge_factory",
    "mdx.sink.vlm_enhanced_sink",
    "mdx.utils.elastic_ready",
    "handlers",
    "handlers.prompt_handler",
    "handlers.enrichment",
    "handlers.direct_media",
    "handlers.prompt_handler.alert_type_config_loader",
    "handlers.async_dispatch_mixin",
    "handlers.async_external_io_mixin",
    "handlers.async_vlm_mode_mixin",
    "utils.schema_util",
    "utils.logging_config",
    "vlm.warmup",
    "vlm.vlm_client",
    "enhance_alert_with_vlm",
)

_WATCHED_PREFIXES = (
    "metrics",
    "vss",
    "clients",
    "mdx",
    "handlers",
    "utils",
    "vlm",
    "its_redis",
    "enhance_alert_with_vlm",
)


def _is_watched(name):
    return name in _WATCHED_MODULES or name.split(".")[0] in _WATCHED_PREFIXES


def _is_synthetic(mod):
    return (
        getattr(mod, "__spec__", None) is None
        and getattr(mod, "__file__", None) is None
    )


def _capture():
    state = {}
    for name, mod in list(sys.modules.items()):
        if not _is_watched(name) or mod is None:
            continue
        state[name] = (mod, dict(getattr(mod, "__dict__", {})) or None)
    return state


def _apply(state):
    # Drop watched synthetic stubs that are not part of the target state so the
    # real module/package can re-import cleanly (a bare stub has no ``__path__``
    # and otherwise blocks ``from pkg.sub import x`` with "is not a package").
    for name in list(sys.modules):
        if name in state or not _is_watched(name):
            continue
        if _is_synthetic(sys.modules.get(name)):
            del sys.modules[name]
    for name, (mod, saved) in state.items():
        if sys.modules.get(name) is not mod:
            sys.modules[name] = mod
        if saved is None:
            continue
        cur = mod.__dict__
        for key, value in saved.items():
            if cur.get(key) is not value:
                cur[key] = value
        for key in [k for k in cur if k not in saved]:
            del cur[key]


# Pristine state, captured before any wiring test module is imported.
_CLEAN = _capture()

# module __name__ -> the sys.modules state that module installed at import time.
_PER_FILE = {}


def _seed_metrics_stubs():
    """Seed a complete auto-mocking ``metrics`` / ``metrics.recorder`` stub.

    Several wiring files stub these only partially (they historically relied on
    a sibling, collected earlier, to register the full recorder symbol set).
    Once each file is imported in isolation that leakage is gone and the
    monolithic ``enhance_alert_with_vlm`` import fails on a missing recorder
    symbol. An auto-mocking module makes every file self-sufficient while still
    letting a file override the specific symbols it asserts on.

    ``metrics.prometheus_metrics`` is left untouched (not seeded, not popped) so
    Prometheus collectors are never re-registered.
    """
    metrics_pkg = _AutoMockModule("metrics")
    metrics_pkg.__path__ = []
    metrics_pkg.PROMETHEUS_ENABLED = False
    sys.modules["metrics"] = metrics_pkg
    sys.modules["metrics.recorder"] = _AutoMockModule("metrics.recorder")


class _IsolatingModule(pytest.Module):
    """Import each wiring module against a pristine, fully-stubbed world, then
    snapshot the result and reset so sibling files and non-wiring tests are not
    contaminated."""

    def _getobj(self):
        # Remove the real watched modules so the file builds its own stub world
        # exactly as it does in isolation (its preamble only creates a stub when
        # the name is absent). Seed a complete metrics stub so files with only a
        # partial metrics stub still import the monolithic SUT successfully.
        stashed = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if _is_watched(name) and name != "metrics.prometheus_metrics"
        }
        _seed_metrics_stubs()
        try:
            mod = super()._getobj()
            _PER_FILE[mod.__name__] = _capture()
        finally:
            # Drop the file's stubs and hand the real modules back to the next
            # file / non-wiring tests.
            for name in list(sys.modules):
                if _is_watched(name) and _is_synthetic(sys.modules.get(name)):
                    del sys.modules[name]
            for name, real in stashed.items():
                sys.modules[name] = real
            _apply(_CLEAN)
        return mod


def pytest_pycollect_makemodule(module_path, parent):
    return _IsolatingModule.from_parent(parent, path=module_path)


@pytest.fixture(autouse=True)
def _wiring_module_environment(request):
    # Re-establish this file's stub world for the duration of the test, then
    # hand pristine modules back to whatever runs next.
    state = _PER_FILE.get(request.module.__name__)
    if state is not None:
        _apply(state)
    try:
        yield
    finally:
        _apply(_CLEAN)

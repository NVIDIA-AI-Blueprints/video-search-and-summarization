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

"""Which adapter packages may import which.

The registry loads every adapter, so this is not about what a running process
pulls in -- it already pulls in all of them. It is about the *direction* of the
dependency between sibling adapter packages: one backend's package reaching into
another's makes the second one's internals load-bearing for the first, and the
next adapter inherits the same shortcut.
"""

import subprocess
import sys

_PROBE = """
import json, sys
import src.adapters.unified.activation  # noqa: F401
import src.adapters.unified.adapter     # noqa: F401
import src.adapters.unified.client      # noqa: F401
print(json.dumps(sorted(m for m in sys.modules if m.startswith("src.adapters.aoi"))))
"""


def _aoi_modules_pulled_in_by_unified() -> list[str]:
    """Import the unified package in a FRESH interpreter and report aoi fallout.

    A subprocess is not incidental here. Every other test module in this suite
    imports the aoi package, so by the time an in-process assertion runs,
    ``sys.modules`` already contains it whatever the unified package did or did
    not do -- the check would pass or fail on test ordering rather than on the
    import graph.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    return __import__("json").loads(result.stdout.strip().splitlines()[-1])


def test_the_unified_package_does_not_import_the_aoi_package():
    """The unified adapter must not depend on a sibling backend's package.

    It inherits its staging from ``StagingActivation`` in
    :mod:`src.adapters.activation`. Inheriting it from
    ``aoi.activation.ExplicitActivation`` instead — as it did before the shared
    module existed — also put three AOI-only methods (``_await_build``,
    ``_read_build_status``, ``_is_ready``) on its MRO, all dereferencing an
    ``inference`` client the unified adapter does not have, held together by a
    ``self.inference = None`` stub rather than by structure. This test is what
    stops that arrangement coming back.
    """
    assert _aoi_modules_pulled_in_by_unified() == []

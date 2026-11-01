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

"""Guard for V1's frozen, tracked artifacts.

V1's results, manifests, configs, prompts, methodology, ground-truth freeze records and reproducibility files
must never change silently. ``frozen_v1.json`` (written once, when V2 began) holds the SHA-256 of every tracked
file matched by ``FROZEN_GLOBS``; ``drift()`` reports a changed, removed or new file. A deliberate change, for
example a typo fix in a frozen report, needs a reviewed edit of ``frozen_v1.json`` with the reason in the PR.
"""

from __future__ import annotations

import json
from pathlib import Path

from evalv2.core.hashing import sha256_file

PROFILE_DIR = Path(__file__).resolve().parents[3]
HASHES_PATH = Path(__file__).resolve().with_name("frozen_v1.json")
FROZEN_GLOBS = (
    "eval/docs/vlm_benchmark_results.md",
    "eval/docs/experiments/*.md",
    "eval/experiments/configs/*.json",
    "eval/experiments/stage3/manifest.json",
    "eval/experiments/e2e-profile/manifest.json",
    "eval/experiments/judge-caps.json",
    "eval/experiments/pricing.json",
    "eval/experiments/stage2-drafts/*",
    "contracts/experiments/p1/*.txt",
    "eval/methodology/evaluation-methodology-v1.*.md",
    "eval/methodology/judge-prompt-v1.txt",
    "eval/methodology/calibration/*",
    "eval/ground_truth/annotation-guidelines-v1.0.md",
    "eval/ground_truth/correction_prompts/*.md",
    "eval/ground_truth/freeze/ground-truth-v1.0-MANIFEST.json",
    "eval/ground_truth/freeze/ground-truth-v1.0-approval.json",
    "eval/eval_reproducibility/*.json",
)


def current_hashes(root: Path = PROFILE_DIR) -> dict[str, str]:
    files = sorted({p for pattern in FROZEN_GLOBS for p in root.glob(pattern) if p.is_file()})
    return {str(p.relative_to(root)): sha256_file(p) for p in files}


def recorded_hashes(path: Path = HASHES_PATH) -> dict[str, str]:
    return json.loads(path.read_text())["files"]


def drift(root: Path = PROFILE_DIR, path: Path = HASHES_PATH) -> list[str]:
    recorded, current = recorded_hashes(path), current_hashes(root)
    problems = [f"frozen V1 file removed: {name}" for name in sorted(set(recorded) - set(current))]
    problems += [f"new file under a frozen V1 path: {name}" for name in sorted(set(current) - set(recorded))]
    problems += [f"frozen V1 file changed: {name}" for name in sorted(set(recorded) & set(current))
                 if recorded[name] != current[name]]
    return problems

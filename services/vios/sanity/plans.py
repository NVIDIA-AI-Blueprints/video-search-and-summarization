# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Load and expand the sanity plans file (sanity_plans.yaml)."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from usecases import USECASE_FUNCS, VERB_FUNCS

# A plan use-case NAME expands to this ordered list of individual use-cases.
# Groups (download/picture/webrtc) bundle several; the rest map 1:1.
NAME_REGISTRY: Dict[str, List[str]] = {
    "nvstreamer_file_upload": ["nvstreamer_file_upload"],
    "rtsp_add_recording_check": ["rtsp_add_recording_check"],
    "vios_file_upload": ["vios_file_upload"],
    "download": ["download_overlay"],
    "picture": ["live_picture_overlay", "replay_picture_overlay"],
    "webrtc": ["webrtc_live_overlay", "webrtc_replay_overlay", "video_wall"],
    "milestone_adaptor_test": ["milestone_adaptor_test"],
    "onvif_adaptor_test": ["onvif_adaptor_test"],
}


def load_plans(path: str) -> Tuple[dict, List[dict]]:
    """Return (defaults, plans). Each plan has `defaults` merged in (plan wins)."""
    doc = yaml.safe_load(Path(path).read_text()) or {}
    defaults = doc.get("defaults", {}) or {}
    plans = []
    for p in doc.get("plans", []) or []:
        merged = dict(defaults)
        merged.update(p)   # plan overrides defaults (nested system/setup kept as-is)
        plans.append(merged)
    return defaults, plans


def _label(test: str, params: dict) -> str:
    if not params:
        return test
    return test + "[" + ",".join(f"{k}={v}" for k, v in params.items()) + "]"


# Item keys that are meta (not verb params); separated before binding the verb.
_META_KEYS = {"evidence"}


def expand_usecases(items: List, ctx=None) -> List[Tuple[str, callable, dict]]:
    """Expand a plan's `usecases:` list into ordered [(label, callable(ctx), meta)].

    Two item forms are accepted:
      * a string  -> a named use-case / group (NAME_REGISTRY, 1:many).
      * a mapping  {test: <verb>, ...params, evidence: bool} -> a parametric verb
        (VERB_FUNCS) bound with its params via functools.partial. `evidence` is a
        meta flag (kept out of the verb params) that marks the result for the PDF
        evidence gallery. Each distinct label runs once."""
    out, seen = [], set()
    for item in items or []:
        if isinstance(item, dict):
            test = item.get("test")
            verb = VERB_FUNCS.get(test)
            if not verb:
                continue
            meta = {k: item[k] for k in _META_KEYS if k in item}
            params = {k: v for k, v in item.items() if k != "test" and k not in _META_KEYS}
            label = _label(test, params)
            if label in seen:
                continue
            out.append((label, functools.partial(verb, **params), meta))
            seen.add(label)
        else:
            for fn in NAME_REGISTRY.get(item, [item]):
                if fn in seen:
                    continue
                f = USECASE_FUNCS.get(fn)
                if f:
                    out.append((fn, f, {}))
                    seen.add(fn)
    return out

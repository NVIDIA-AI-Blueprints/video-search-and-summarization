# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pin on `.openclaw/Dockerfile`'s VSS_REF default."""

import re
from pathlib import Path


def test_vss_ref_default_is_readable():
    """The image fetches its skills, docs and CLI from a ref, since NemoClaw
    builds it with the Dockerfile's own directory as the context. The default is
    a ref a person can read: the `develop` branch, or a `v*` release tag; a bare
    commit sha is an override for a reproducible rebuild, never the default."""
    df = Path(__file__).resolve().parents[1] / "Dockerfile"
    m = re.search(r"^ARG VSS_REF=(\S+)$", df.read_text(), re.M)
    assert m, f"{df} has no VSS_REF default"
    ref = m.group(1)
    assert ref == "develop" or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?", ref), (
        f"VSS_REF default {ref!r} is neither `develop` nor a v* release tag")

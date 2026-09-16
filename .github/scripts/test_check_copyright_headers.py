#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fixture-based tests for check_copyright_headers.py.

Covers the validation surface the repository tree cannot: malformed licence
values, empty holder fields, wrong NVIDIA entity, comment terminators, and
the exclusion boundaries around vendored trees.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_copyright_headers as chk

NVIDIA_HOLDER = (
    "SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & "
    "AFFILIATES. All rights reserved."
)


def problem_for(content: str) -> str | None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        path = handle.name
    try:
        return chk.header_problem(path)
    finally:
        Path(path).unlink()


class HeaderProblemTests(unittest.TestCase):
    def test_valid_header_passes(self):
        self.assertIsNone(problem_for(
            f"# {NVIDIA_HOLDER}\n# SPDX-License-Identifier: Apache-2.0\n"
        ))

    def test_valid_c_style_header_with_terminator(self):
        self.assertIsNone(problem_for(
            f"/* {NVIDIA_HOLDER} */\n/* SPDX-License-Identifier: MIT */\n"
        ))

    def test_missing_identifier(self):
        self.assertEqual(
            problem_for(f"# {NVIDIA_HOLDER}\n"),
            "missing SPDX-License-Identifier",
        )

    def test_malformed_licence_value_rejected(self):
        problem = problem_for(
            f"# {NVIDIA_HOLDER}\n# SPDX-License-Identifier: Apache-2.0/\n"
        )
        self.assertIn("not on the allowed list", problem or "")

    def test_disallowed_licence_rejected(self):
        problem = problem_for(
            f"# {NVIDIA_HOLDER}\n# SPDX-License-Identifier: GPL-3.0-only\n"
        )
        self.assertIn("not on the allowed list", problem or "")

    def test_missing_holder(self):
        self.assertEqual(
            problem_for("# SPDX-License-Identifier: Apache-2.0\n"),
            "missing SPDX-FileCopyrightText",
        )

    def test_empty_holder_field_rejected(self):
        problem = problem_for(
            "# SPDX-FileCopyrightText:\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
        )
        self.assertEqual(problem, "SPDX-FileCopyrightText carries no copyright text")

    def test_prose_holder_field_rejected(self):
        problem = problem_for(
            "# SPDX-FileCopyrightText: see the notice file\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
        )
        self.assertEqual(problem, "SPDX-FileCopyrightText carries no copyright text")

    def test_wrong_nvidia_entity_rejected(self):
        problem = problem_for(
            "# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA Corporation.\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
        )
        self.assertIn("copyright entity", problem or "")

    def test_non_nvidia_holder_passes(self):
        self.assertIsNone(problem_for(
            "# SPDX-FileCopyrightText: Copyright (c) 2019 The Example Authors\n"
            "# SPDX-License-Identifier: BSD-3-Clause\n"
        ))

    def test_header_beyond_first_five_lines_fails(self):
        filler = "# filler\n" * 5
        problem = problem_for(
            filler + f"# {NVIDIA_HOLDER}\n# SPDX-License-Identifier: Apache-2.0\n"
        )
        self.assertEqual(problem, "missing SPDX-License-Identifier")


class ExclusionBoundaryTests(unittest.TestCase):
    def test_vendored_trees_excluded(self):
        for path in (
            "services/vios/src/framework/webrtc_streamer/inc/webrtc_headers/src/api/x.h",
            "services/vios/include/opentelemetry/sdk/logs/logger_provider.h",
            "services/vios/src/framework/live555/inc/live/liveMedia/rtcp_from_spec.h",
            "libs/nvschema/protobuf/struct.proto",
            "services/agent/3rdparty/ffmpeg/x.c",
            "services/ui/apps/nv-metropolis-bp-vss-ui/next-env.d.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(chk.is_excluded(path))

    def test_first_party_paths_not_excluded(self):
        for path in (
            "services/vios/src/framework/live555/inc/live555helper/sdpclient.h",
            "services/ui/packages/common/lib-src/index.ts",
            "services/vios/src/framework/notification/ds_schema.proto",
            "deploy/docker/scripts/blueprint-deploy.sh",
        ):
            with self.subTest(path=path):
                self.assertFalse(chk.is_excluded(path))

    def test_extension_gating(self):
        self.assertIn(".sh", chk.CHECK_EXTENSIONS)
        self.assertIn(".proto", chk.CHECK_EXTENSIONS)
        self.assertNotIn(".md", chk.CHECK_EXTENSIONS)
        self.assertNotIn(".yml", chk.CHECK_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()

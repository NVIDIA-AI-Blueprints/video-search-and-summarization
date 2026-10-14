# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[2] / "SKILL.md"
QUALITY_GATES = Path(__file__).resolve().parents[2] / "references" / "quality-gates.md"


class SkillDocsTest(unittest.TestCase):
    def test_model_path_placeholder_is_not_html(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertNotIn("MODEL_PATH=<", text)
        self.assertIn("`MODEL_PATH` to an `ngc:` URI or a mounted snapshot path", text)
        self.assertIn("configuration only", text)
        self.assertIn("vllm-compatible", text)
        self.assertIn("vss-deploy-dense-captioning", text)

    def test_quality_gates_document_openapi_and_lfs(self):
        text = QUALITY_GATES.read_text(encoding="utf-8")
        for needle in (
            "/v1/health/ready",
            "/v1/models",
            "/v1/files",
            "/v1/generate_captions",
            "/v1/chat/completions",
            "media_type",
            "git-lfs.github.com",
            "FILE_URL_ALLOWED_DIRS",
            "use_fps_for_chunking",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("MODEL_PATH=<", text)


if __name__ == "__main__":
    unittest.main()

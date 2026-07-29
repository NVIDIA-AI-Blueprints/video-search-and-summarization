# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the agent third-party notice generator."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("generate_python_third_party_licenses.py")
MODULE_SPEC = importlib.util.spec_from_file_location(
    "generate_python_third_party_licenses", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
generator = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = generator
MODULE_SPEC.loader.exec_module(generator)


class ExistingNoticeTest(unittest.TestCase):
    def test_parses_triple_and_quadruple_fenced_sections(self) -> None:
        notice = """# Dependencies Licenses

## first_package (1.2.3)

**License:** MIT

**License URL:** https://example.com/first

```
first text
```

--------------------------------------------------------------------------------

## second-package (4.5.6)

**License:** Apache-2.0

**License URL:** https://example.com/second

````text
second text
````
"""

        sections = generator.parse_existing_sections(notice)

        self.assertEqual({"first-package", "second-package"}, set(sections))
        self.assertEqual("first text", sections["first-package"].text)
        self.assertEqual("second text", sections["second-package"].text)
        self.assertEqual("4.5.6", sections["second-package"].version)

    def test_identifies_license_and_notice_files(self) -> None:
        self.assertTrue(
            generator.is_notice_file("sample.dist-info/licenses/LICENSE.txt")
        )
        self.assertTrue(generator.is_notice_file("sample/NOTICE"))
        self.assertTrue(generator.is_notice_file("sample/COPYING.LESSER"))
        self.assertFalse(generator.is_notice_file("sample/license_helper.py"))
        self.assertFalse(generator.is_notice_file("sample/AUTHORS.rst"))

    def test_normalizes_non_semantic_trailing_whitespace(self) -> None:
        self.assertEqual(
            "first line\nsecond line",
            generator.normalize_notice_text("  first line  \r\nsecond line  \n"),
        )


class RenderTest(unittest.TestCase):
    def test_renders_inventory_scope_and_every_notice_path(self) -> None:
        component = generator.Component(
            license_name="MIT",
            license_url="https://example.com/license",
            name="sample",
            notes=("Verified note.",),
            scope="Installed in the default image",
            texts=(
                generator.NoticeText(source="LICENSE", text="license text"),
                generator.NoticeText(source="NOTICE", text="attribution"),
            ),
            version="1.0.0",
        )

        rendered = generator.render([component], 1, "test image")

        self.assertIn("Installed third-party Python distributions: 1", rendered)
        self.assertIn("Total documented components: 1", rendered)
        self.assertIn("**Notice source:** `LICENSE`", rendered)
        self.assertIn("**Notice source:** `NOTICE`", rendered)
        self.assertIn("Verified note.", rendered)


if __name__ == "__main__":
    unittest.main()

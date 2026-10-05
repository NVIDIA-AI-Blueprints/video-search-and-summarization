# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check Hugging Face model revisions are downloaded and cached separately."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


DOWNLOADER = (
    Path(__file__).resolve().parents[1] / "src/vlm_pipeline/ngc_model_downloader.py"
)
spec = importlib.util.spec_from_file_location("ngc_model_downloader", DOWNLOADER)
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class ModelRevisionTest(unittest.TestCase):
    def test_huggingface_revision_is_pinned_and_cache_isolated(self):
        revision = "344d602b128d1bbdacb43b08d0a3626f46343e29"
        url = f"https://huggingface.co/nvidia/Cosmos3-Edge@{revision}"
        with (
            tempfile.TemporaryDirectory() as cache,
            patch.object(downloader.subprocess, "run") as run,
        ):
            pinned = downloader.download_model_git(url, cache)
            unpinned = downloader.download_model_git(
                "https://huggingface.co/nvidia/Cosmos3-Edge", cache
            )
            self.assertNotEqual(pinned, unpinned)
            before_cached_call = run.call_count
            self.assertEqual(downloader.download_model_git(url, cache), pinned)
            self.assertEqual(run.call_count, before_cached_call)
            self.assertIn(
                [
                    "hf",
                    "download",
                    "nvidia/Cosmos3-Edge",
                    "--revision",
                    revision,
                    "--local-dir",
                ],
                [call.args[0][:6] for call in run.call_args_list],
            )
            with self.assertRaises(ValueError):
                downloader.download_model_git(
                    "https://huggingface.co/nvidia/Cosmos3-Edge@main", cache
                )


if __name__ == "__main__":
    unittest.main()

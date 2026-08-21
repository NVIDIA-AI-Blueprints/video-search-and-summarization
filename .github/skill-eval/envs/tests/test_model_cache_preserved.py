# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The per-trial docker reset must keep model weights and wipe everything else.

Wiping the weight caches made every spec's first trial re-download models it
had just thrown away: ~20 min against ~55 s warm, 17.8 GB measured on a live
deployment. These tests pin both halves of the bargain, because getting either
one wrong is expensive in a different way: keep too little and the cold
download comes back, keep too much and a stateful volume leaks into the next
trial, which is the contamination the reset exists to prevent.
"""

from __future__ import annotations

import re
import subprocess
import sys
import types
import unittest
from pathlib import Path

_base = types.ModuleType("harbor.environments.base")


class _BaseEnvironment:
    def __init__(self, *a, **kw):
        pass


class _ExecResult:
    def __init__(self, stdout=None, stderr=None, return_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


_base.BaseEnvironment = _BaseEnvironment
_base.ExecResult = _ExecResult
sys.modules.setdefault("harbor", types.ModuleType("harbor"))
sys.modules.setdefault("harbor.environments", types.ModuleType("harbor.environments"))
sys.modules["harbor.environments.base"] = _base

ENVS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENVS_DIR))

import brev_env  # noqa: E402

KEEP = re.compile(brev_env.MODEL_CACHE_VOLUME_RE)

# Real volume names. The NIM ones come from deploy/docker/services/nim/*,
# the rtvi ones from services/rtvi/*, and the `mdx_` prefix is what compose
# actually creates because none of them declare `name:`.
PRESERVE = [
    "rtvi-hf-cache",
    "mdx_rtvi-hf-cache",
    "rtvi-ngc-model-cache",
    "mdx_rtvi-ngc-model-cache",
    "cosmos_reason1_7b_cache",
    "mdx_cosmos_reason1_7b_cache",
    "cosmos_reason2_8b_cache",
    "cosmos3_reasoner_cache",
    "gpt_oss_20b_cache",
    "llama_3.3_nemotron_super_49b_v1.5_cache",
    "nemotron_3_nano_cache",
    "nvidia_nemotron_nano_9b_v2_cache",
    "nvidia_nemotron_nano_9b_v2_fp8_cache",
    "qwen3_vl_8b_instruct_cache",
]

WIPE = [
    "mdx_vss-data",                 # stateful; leaking it across trials is the bug
    "vios_apt_cache",               # APT packages, not weights; the `_cache$` trap
    "mdx_vios_apt_cache",
    "rtvi-triton-model-repo",       # assembled at deploy time, not a download cache
    "mdx_cameraModelFilepath",
    "rtvi-hf-cache-old",            # trailing junk must not sneak past the anchor
    "notrtvi-hf-cache",             # must be preceded by start-of-name or `_`
    "cache",                        # bare word is not a model cache
    "anonymous0123456789abcdef",
]


class WhichVolumesSurvive(unittest.TestCase):
    def test_every_model_weight_cache_is_kept(self):
        for name in PRESERVE:
            with self.subTest(volume=name):
                self.assertRegex(name, KEEP)

    def test_everything_else_is_wiped(self):
        for name in WIPE:
            with self.subTest(volume=name):
                self.assertNotRegex(name, KEEP)


class TheResetScript(unittest.TestCase):
    """Exercise the generated shell, not a paraphrase of it."""

    def _script(self) -> str:
        src = (ENVS_DIR / "brev_env.py").read_text()
        start = src.index('r"""set -uo pipefail')
        end = src.index('""".replace("__KEEP_RE__", keep_re)', start)
        body = src[start + len('r"""'):end]
        return body.replace("__KEEP_RE__", brev_env.MODEL_CACHE_VOLUME_RE)

    def _run_selection(self, volumes: list[str]) -> tuple[list[str], list[str]]:
        """Run the script's own keep/delete expressions against a fake list."""
        script = self._script()
        keep_line = next(ln for ln in script.splitlines()
                         if ln.startswith("KEEP_RE="))
        listing = "\n".join(volumes)
        prog = (
            f"{keep_line}\n"
            f'vols=$(printf "%s\\n" "$LIST" | grep -Ev "$KEEP_RE" || true)\n'
            f'kept=$(printf "%s\\n" "$LIST" | grep -E "$KEEP_RE" || true)\n'
            'echo "---DELETE---"; echo "$vols"; echo "---KEEP---"; echo "$kept"'
        )
        out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True,
                             check=False,
                             env={"LIST": listing, "PATH": "/usr/bin:/bin"}).stdout
        delete, keep = out.split("---KEEP---")
        delete = [x for x in delete.replace("---DELETE---", "").split("\n") if x]
        keep = [x for x in keep.split("\n") if x]
        return delete, keep

    def test_the_shipped_script_keeps_and_wipes_the_right_volumes(self):
        delete, keep = self._run_selection(PRESERVE + WIPE)
        self.assertEqual(sorted(keep), sorted(PRESERVE))
        self.assertEqual(sorted(delete), sorted(WIPE))

    def test_a_box_holding_only_caches_does_not_abort(self):
        """`grep -Ev` exits 1 on no match; with pipefail that would kill the reset."""
        delete, keep = self._run_selection(PRESERVE)
        self.assertEqual(delete, [])
        self.assertEqual(sorted(keep), sorted(PRESERVE))

    def test_the_guard_counts_only_volumes_that_should_be_gone(self):
        """Otherwise the reset fails loud on the caches it just preserved."""
        script = self._script()
        guard = next(ln for ln in script.splitlines() if ln.startswith("rv="))
        self.assertIn("grep -Ev", guard)

    def test_the_escape_hatch_restores_the_old_behaviour(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"SKILL_EVAL_WIPE_MODEL_CACHES": "1"}):
            self.assertTrue(brev_env._wipe_model_caches())
        with mock.patch.dict("os.environ", {"SKILL_EVAL_WIPE_MODEL_CACHES": ""}):
            self.assertFalse(brev_env._wipe_model_caches())


if __name__ == "__main__":
    unittest.main()


class TheAllowlistStaysInSyncWithCompose(unittest.TestCase):
    """A NIM added without updating the allowlist must fail CI, not go slow.

    The allowlist is named rather than matched on a name shape, so discovery
    lives here: an omission fails CI instead of costing every leg that deploys
    that model a re-download.
    """

    REPO = Path(__file__).resolve().parents[4]

    def _nim_cache_volumes(self) -> set[str]:
        found = set()
        compose = list((self.REPO / "deploy" / "docker").rglob("*.yml"))
        compose += list((self.REPO / "deploy" / "docker").rglob("*.yaml"))
        for path in compose:
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip().lstrip("- ").strip('"\'')
                if ":/opt/nim/.cache" in line:
                    found.add(line.split(":/opt/nim/.cache")[0])
        return found

    def test_every_nim_weight_cache_is_in_the_allowlist(self):
        discovered = self._nim_cache_volumes()
        self.assertTrue(discovered, "found no /opt/nim/.cache mounts; glob broke")
        missing = sorted(v for v in discovered if not KEEP.search(v))
        self.assertEqual(
            missing, [],
            f"NIM weight caches not covered by MODEL_CACHE_VOLUME_RE: {missing}. "
            "Add them, or every leg deploying that model re-downloads it.",
        )

    def test_the_apt_cache_is_not_swept_up(self):
        """The concrete volume that made a `_cache$` pattern unsafe."""
        self.assertNotRegex("vios_apt_cache", KEEP)
        self.assertNotRegex("mdx_vios_apt_cache", KEEP)

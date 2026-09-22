# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run instruction snippets without a checkout; optionally use the real image.

VSS_TEST_IMAGE=<cached image> python3 -m unittest discover -s .openclaw/tests
"""

import os
import json
from pathlib import Path
import re
import shlex
import subprocess
import textwrap
import unittest


ROOT = Path(os.environ.get("VSS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
IMAGE = os.environ.get("VSS_TEST_IMAGE")
WORKSPACE = ROOT / ".openclaw/workspace/_nemoclaw"
SKILL = ROOT / "skills/operations/vss-ask-video/SKILL.md"


def bash_block(path, heading):
    section = path.read_text().split(heading, 1)[1]
    return re.search(r"```bash\n(.*?)\n```", section, re.S).group(1)


class EnvironmentInstructions(unittest.TestCase):
    def exports(self, values, names=("VSS_PUBLIC_URL", "HOST_IP"), script=None):
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                (
                    script
                    if script is not None
                    else bash_block(WORKSPACE / "ENV.md", "## Exports")
                )
                + '\nprintf "%s\\n" '
                + " ".join(f'"${name}"' for name in names),
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", **values},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.splitlines()

    def test_explicit_origin_and_host_survive_session_setup(self):
        self.assertEqual(
            self.exports(
                {
                    "VSS_PUBLIC_URL": "https://selected.example:8443",
                    "VSS_GATEWAY_ORIGIN": "https://different.example:9443",
                    "HOST_IP": "operator-host.example",
                }
            ),
            ["https://selected.example:8443", "operator-host.example"],
        )

    def test_harness_origin_is_used_without_public_url(self):
        self.assertEqual(
            self.exports({"VSS_GATEWAY_ORIGIN": "http://selected.example:80"}),
            ["http://selected.example:80", "host.openshell.internal"],
        )

    def test_no_origin_is_invented(self):
        self.assertEqual(self.exports({}), ["", "host.openshell.internal"])

    def test_hitl_defaults_only_when_unset(self):
        self.assertEqual(self.exports({}, ("HITL_ENABLED",)), ["false"])
        for value in ("true", "false", ""):
            with self.subTest(value=value):
                self.assertEqual(
                    self.exports({"HITL_ENABLED": value}, ("HITL_ENABLED",)), [value]
                )

    def test_explicit_empty_origin_and_host_remain_empty(self):
        self.assertEqual(
            self.exports(
                {
                    "VSS_PUBLIC_URL": "",
                    "VSS_GATEWAY_ORIGIN": "http://selected.example:80",
                    "HOST_IP": "",
                }
            ),
            ["", ""],
        )

    def notebook_script(self, origin, hitl):
        path = ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb"
        cells = json.loads(path.read_text())["cells"]
        source = next(
            "".join(cell.get("source", []))
            for cell in cells
            if "_rendered, _origin_filled = re.subn(" in "".join(cell.get("source", []))
        )
        start = source.index("    _rendered = _text\n")
        end = source.index("    with tempfile.TemporaryDirectory", start)
        scope = {
            "_text": (WORKSPACE / "ENV.md").read_text(),
            "_env_md": "ENV.md",
            "VSS_PUBLIC_URL": origin,
            "HITL_ENABLED": hitl,
            "re": re,
            "shlex": shlex,
        }
        exec(compile(textwrap.dedent(source[start:end]), str(path), "exec"), scope)
        return re.search(r"```bash\n(.*?)\n```", scope["_rendered"], re.S).group(1)

    def test_actual_notebook_renderer_preserves_supplied_and_empty_values(self):
        origin = "https://notebook.example:8443"
        script = self.notebook_script(origin, False)
        cases = [
            ({}, [origin, "false"]),
            (
                {
                    "VSS_PUBLIC_URL": "https://selected.example:443",
                    "HITL_ENABLED": "true",
                },
                ["https://selected.example:443", "true"],
            ),
            ({"VSS_PUBLIC_URL": "", "HITL_ENABLED": ""}, ["", ""]),
            (
                {"VSS_GATEWAY_ORIGIN": "https://selected.example:443"},
                ["https://selected.example:443", "false"],
            ),
            ({"VSS_GATEWAY_ORIGIN": ""}, ["", "false"]),
        ]
        for inherited, expected in cases:
            with self.subTest(inherited=inherited):
                self.assertEqual(
                    self.exports(inherited, ("VSS_PUBLIC_URL", "HITL_ENABLED"), script),
                    expected,
                )

    def test_actual_notebook_renderer_quotes_literal_defaults(self):
        origin = "https://notebook.example:8443/?q='$(printf injected)'\\1&x=two words"
        script = self.notebook_script(origin, True)
        self.assertEqual(
            self.exports({}, ("VSS_PUBLIC_URL", "HITL_ENABLED"), script),
            [origin, "true"],
        )


@unittest.skipUnless(IMAGE, "Set VSS_TEST_IMAGE to exercise the installed image CLI")
class InstalledCliInstructions(unittest.TestCase):
    def run_image(self, script):
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=128",
                "--memory=512m",
                "--cpus=2",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m",
                "--env=OPENCLAW_CHILD_OOM_SCORE_ADJ=0",
                "--entrypoint=/bin/bash",
                "-i",
                IMAGE,
                "--noprofile",
                "--norc",
                "-s",
            ],
            input=script,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def skill_selectors(self):
        blocks = re.findall(r"```bash\n(.*?)\n```", SKILL.read_text(), re.S)
        selectors = [
            block.split("\n\n", 1)[0].split("\nVLM_FPS", 1)[0]
            for block in blocks
            if "VSS=(" in block
        ]
        self.assertEqual(len(selectors), 8)
        return selectors

    def test_all_skill_selectors_and_shared_bootstrap_use_baked_cli(self):
        repo_bootstrap = bash_block(ROOT / "AGENTS.md", "### Setup")
        selected_commands = "\n".join(
            selector + '\n"${VSS[@]}" --version\n'
            for selector in self.skill_selectors()
        )
        result = self.run_image(
            "set -eu\n"
            'test ! -e "$HOME/video-search-and-summarization"\n'
            "test ! -e /usr/local/src/vss/.git\n"
            'test "$(command -v vss)" = /usr/local/bin/vss\n'
            'git() { echo "unexpected git" >&2; exit 97; }\n'
            'uv() { echo "unexpected uv" >&2; exit 98; }\n'
            + repo_bootstrap
            + "\n"
            + selected_commands
            + '\n"${VSS[@]}" vlm run --help\n'
            + '\n"${VSS[@]}" vios --help\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--media-url", result.stdout)
        self.assertNotIn("unexpected", result.stderr)

    def test_missing_baked_cli_stops_before_development_fallback(self):
        blocks = [bash_block(ROOT / "AGENTS.md", "### Setup"), *self.skill_selectors()]
        for index, block in enumerate(blocks):
            with self.subTest(selector=index):
                result = self.run_image(
                    "test -x /usr/local/bin/nemoclaw-start || exit 96\n"
                    "command() { return 1; }\n"
                    'uv() { echo "unexpected uv" >&2; exit 98; }\n'
                    + block
                    + '\necho "unexpected continuation"\n'
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Baked VSS CLI missing", result.stderr)
                self.assertNotIn("unexpected", result.stdout + result.stderr)

    def test_oom_default_disables_installed_runtime_proc_write(self):
        dockerfile = (ROOT / ".openclaw/Dockerfile").read_text()
        self.assertRegex(dockerfile, r"(?m)^ENV OPENCLAW_CHILD_OOM_SCORE_ADJ=0$")
        result = self.run_image("""node --input-type=module <<'JS'
import { t as prepare } from '/usr/local/lib/node_modules/openclaw/dist/linux-oom-score-eO5nXmjv.js';
const options = { platform: 'linux', shellAvailable: () => true };
const baseline = prepare('/bin/bash', ['-c', 'true'], { ...options, env: {} });
const configured = prepare('/bin/bash', ['-c', 'true'], { ...options, env: process.env });
if (!baseline.wrapped || configured.wrapped || configured.command !== '/bin/bash') {
  throw new Error('OpenClaw child OOM-score override did not disable wrapping');
}
console.log('OpenClaw child OOM-score write disabled');
JS
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write disabled", result.stdout)


if __name__ == "__main__":
    unittest.main()

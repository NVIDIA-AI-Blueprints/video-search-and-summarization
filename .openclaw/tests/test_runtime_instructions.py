# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run instruction snippets; VSS_TEST_IMAGE enables cached-image checks."""

import json
import os
import re
import shlex
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VSS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
IMAGE = os.environ.get("VSS_TEST_IMAGE")
WORKSPACE = ROOT / ".openclaw/workspace/_nemoclaw"
SKILL = ROOT / "skills/operations/vss-ask-video/SKILL.md"


def bash_block(path, heading):
    section = path.read_text().split(heading, 1)[1]
    return re.search(r"```bash\n(.*?)\n```", section, re.DOTALL).group(1)


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

    def test_direct_url_does_not_require_memory_configuration(self):
        script = (
            bash_block(SKILL, "## Prerequisites")
            + "\n"
            + bash_block(SKILL, "For a trusted bounded URL")
        )
        result = self.exports(
            {
                "VIDEO_URL": "https://media.example/video.mp4",
                "USER_QUESTION": "What happened?",
            },
            names=(),
            script="""set -e
            vss() {
              case "$*" in
                'configure check') return 0 ;;
                'vlm run '*) printf 'direct-url-answer' ;;
                *) echo 'memory is not configured' >&2; return 2 ;;
              esac
            }
            """
            + script,
        )
        self.assertIn("direct-url-answer", result)

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
        exec(compile(textwrap.dedent(source[start:end]), str(path), "exec"), scope)  # noqa: S102 — test the actual renderer
        return re.search(r"```bash\n(.*?)\n```", scope["_rendered"], re.DOTALL).group(1)

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
            check=False,
        )

    def test_skill_calls_the_baked_cli_as_is(self):
        blocks = re.findall(r"```bash\n(.*?)\n```", SKILL.read_text(), re.DOTALL)
        calls = [block for block in blocks if re.search(r"(?m)^\s*vss |\$\(vss ", block)]
        self.assertTrue(calls, "the skill must call vss")
        for block in blocks:
            self.assertNotIn("VSS=(", block)
            self.assertNotIn("uv run", block)
        result = self.run_image(
            "set -eu\n"
            'test ! -e "$HOME/video-search-and-summarization"\n'
            "test ! -e /usr/local/src/vss/.git\n"
            'test "$(command -v vss)" = /usr/local/bin/vss\n'
            'git() { echo "unexpected git" >&2; exit 97; }\n'
            'uv() { echo "unexpected uv" >&2; exit 98; }\n'
            "vss --version\n"
            "vss vlm run --help\n"
            "vss vios --help\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--media-url", result.stdout)
        self.assertNotIn("unexpected", result.stderr)

    def test_oom_default_disables_installed_runtime_proc_write(self):
        dockerfile = (ROOT / ".openclaw/Dockerfile").read_text()
        self.assertRegex(dockerfile, r"(?m)^ENV OPENCLAW_CHILD_OOM_SCORE_ADJ=0$")
        result = self.run_image(r"""node --input-type=module <<'JS'
import { readdirSync } from 'node:fs';
const dist = '/usr/local/lib/node_modules/openclaw/dist/';
const chunks = readdirSync(dist).filter(name => /^linux-oom-score-.*\.js$/.test(name));
if (chunks.length !== 1) throw new Error(`Expected one OOM helper, found ${chunks}`);
const { t: prepare } = await import(dist + chunks[0]);
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

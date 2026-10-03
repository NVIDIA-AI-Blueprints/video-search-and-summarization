# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep the Qwen vLLM chart's locked policy and runtime defaults aligned."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HELM_ROOT = REPO_ROOT / "helm"
CHART = HELM_ROOT / "services" / "qwen-vllm"

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


@cache
def _values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


@cache
def _docs() -> list[dict]:
    env = os.environ.copy()
    env["HELM_REPOSITORY_CONFIG"] = os.devnull
    result = subprocess.run(
        ["helm", "template", "test", str(CHART)],
        cwd=HELM_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _kind(kind: str) -> dict:
    matches = [document for document in _docs() if document.get("kind") == kind]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}, found {len(matches)}")
    return matches[0]


class QwenVllmValuesTests(unittest.TestCase):
    def test_long_video_runtime_defaults(self):
        values = _values()
        video = values["vllm"]["mediaIoKwargs"]["video"]

        self.assertEqual(values["vllm"]["maxNumSeqs"], 4)
        self.assertEqual(video["fps"], 2)
        self.assertEqual(video["num_frames"], -1)
        self.assertEqual(video["max_frames"], 8192)
        self.assertEqual(values["resources"]["requests"]["memory"], "256Gi")
        self.assertEqual(values["resources"]["limits"]["memory"], "256Gi")

    def test_request_policy_matches_vllm_generation_defaults(self):
        values = _values()
        policy = values["requestPolicy"]["payload"]
        generation = values["vllm"]["overrideGenerationConfig"]

        self.assertEqual(policy["max_tokens"], 16384)
        self.assertEqual(generation["max_new_tokens"], policy["max_tokens"])
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "presence_penalty",
            "repetition_penalty",
        ):
            self.assertEqual(generation[key], policy[key], key)
        self.assertEqual(
            values["vllm"]["defaultChatTemplateKwargs"],
            policy["chat_template_kwargs"],
        )


@helm_required
class QwenVllmRenderTests(unittest.TestCase):
    def test_runtime_contract_reaches_deployment_and_policy_configmap(self):
        deployment = _kind("Deployment")
        configmap = _kind("ConfigMap")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        args = container["args"]

        def arg_after(flag: str) -> str:
            return args[args.index(flag) + 1]

        self.assertEqual(arg_after("--max-num-seqs"), "4")
        self.assertEqual(
            json.loads(arg_after("--media-io-kwargs"))["video"]["max_frames"],
            8192,
        )
        self.assertEqual(
            json.loads(arg_after("--override-generation-config"))["max_new_tokens"],
            16384,
        )
        self.assertEqual(container["resources"]["limits"]["memory"], "256Gi")
        self.assertEqual(
            json.loads(configmap["data"]["request-policy.json"])["max_tokens"],
            16384,
        )


if __name__ == "__main__":
    unittest.main()

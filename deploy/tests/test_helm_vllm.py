# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep the generic vLLM chart and Qwen benchmark profile aligned."""

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
CHART = HELM_ROOT / "benchmark-profiles" / "vllm"
QWEN_VALUES = CHART / "values-qwen.yaml"

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


@cache
def _base_values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


@cache
def _qwen_values() -> dict:
    return yaml.safe_load(QWEN_VALUES.read_text())


@cache
def _docs(profile: str = "base") -> list[dict]:
    env = os.environ.copy()
    env["HELM_REPOSITORY_CONFIG"] = os.devnull
    command = ["helm", "template", "test", str(CHART)]
    if profile == "qwen":
        command.extend(["-f", str(QWEN_VALUES)])
    result = subprocess.run(
        command,
        cwd=HELM_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _kind(kind: str, profile: str = "base") -> dict:
    matches = [document for document in _docs(profile) if document.get("kind") == kind]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}, found {len(matches)}")
    return matches[0]


class GenericVllmValuesTests(unittest.TestCase):
    def test_base_values_are_model_agnostic(self):
        values = _base_values()

        self.assertFalse(values["requestPolicy"]["enabled"])
        self.assertEqual(values["vllm"]["mmProcessorKwargs"], {})
        self.assertEqual(values["vllm"]["mediaIoKwargs"], {})
        self.assertEqual(values["vllm"]["overrideGenerationConfig"], {})
        self.assertEqual(values["vllm"]["defaultChatTemplateKwargs"], {})
        self.assertIsNone(values["vllm"]["maxModelLen"])
        self.assertIsNone(values["vllm"]["maxNumSeqs"])

    @helm_required
    def test_base_render_omits_model_specific_flags_and_policy(self):
        deployment = _kind("Deployment")
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]

        for flag in (
            "--max-model-len",
            "--max-num-seqs",
            "--mm-processor-cache-gb",
            "--mm-processor-kwargs",
            "--media-io-kwargs",
            "--override-generation-config",
            "--default-chat-template-kwargs",
            "--middleware",
        ):
            self.assertNotIn(flag, args)
        self.assertFalse(any(doc.get("kind") == "ConfigMap" for doc in _docs()))


class QwenVllmValuesTests(unittest.TestCase):
    def test_long_video_runtime_defaults(self):
        values = _qwen_values()
        video = values["vllm"]["mediaIoKwargs"]["video"]

        self.assertEqual(values["vllm"]["maxNumSeqs"], 4)
        self.assertEqual(video["fps"], 2)
        self.assertEqual(video["num_frames"], -1)
        self.assertEqual(video["max_frames"], 8192)
        self.assertEqual(values["resources"]["requests"]["memory"], "256Gi")
        self.assertEqual(values["resources"]["limits"]["memory"], "256Gi")

    def test_request_policy_matches_vllm_generation_defaults(self):
        values = _qwen_values()
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
        deployment = _kind("Deployment", "qwen")
        configmap = _kind("ConfigMap", "qwen")
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
        self.assertEqual(arg_after("--middleware"), "request_policy.RequestPolicy")
        self.assertEqual(container["resources"]["limits"]["memory"], "256Gi")
        self.assertIn("request_policy.py", configmap["data"])
        self.assertEqual(
            json.loads(configmap["data"]["request-policy.json"])["max_tokens"],
            16384,
        )


if __name__ == "__main__":
    unittest.main()

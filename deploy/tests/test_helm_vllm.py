# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep the generic vLLM chart and Qwen benchmark profile aligned."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
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
def _docs(profile: str = "base", set_values: tuple[str, ...] = ()) -> list[dict]:
    env = os.environ.copy()
    env["HELM_REPOSITORY_CONFIG"] = os.devnull
    command = ["helm", "template", "test", str(CHART)]
    if profile == "qwen":
        command.extend(["-f", str(QWEN_VALUES)])
    for value in set_values:
        command.extend(["--set", value])
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


def _kind(
    kind: str, profile: str = "base", set_values: tuple[str, ...] = ()
) -> dict:
    matches = [
        document for document in _docs(profile, set_values) if document.get("kind") == kind
    ]
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

    @helm_required
    def test_local_false_naming_override_wins_over_global_true(self):
        deployment = _kind(
            "Deployment",
            set_values=("global.useReleaseNamePrefix=true", "useReleaseNamePrefix=false"),
        )
        self.assertEqual(deployment["metadata"]["name"], "vllm")

    @helm_required
    def test_local_true_naming_override_wins_over_global_false(self):
        deployment = _kind(
            "Deployment",
            set_values=("global.useReleaseNamePrefix=false", "useReleaseNamePrefix=true"),
        )
        self.assertEqual(deployment["metadata"]["name"], "test-vllm")


class RequestPolicyTests(unittest.TestCase):
    def _load_policy_module(self, policy: dict):
        with tempfile.TemporaryDirectory() as tmp_dir:
            module_path = Path(tmp_dir) / "request_policy.py"
            module_path.write_text((CHART / "files" / "request_policy.py").read_text())
            module_path.with_name("request-policy.json").write_text(json.dumps(policy))
            spec = importlib.util.spec_from_file_location(
                f"request_policy_test_{id(policy)}", module_path
            )
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

    def test_policy_rewrites_body_replays_request_and_adds_hash_header(self):
        policy = {"temperature": 1, "max_tokens": 8}
        module = self._load_policy_module(policy)
        original = json.dumps(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.1,
                "max_completion_tokens": 3,
            }
        ).encode()
        request_events = [
            {"type": "http.request", "body": original[:10], "more_body": True},
            {"type": "http.request", "body": original[10:], "more_body": False},
        ]
        downstream = {}
        response_events = []

        async def app(scope, receive, send):
            downstream["scope"] = scope
            downstream["event"] = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return request_events.pop(0)

        async def send(event):
            response_events.append(event)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [(b"content-length", str(len(original)).encode())],
        }
        asyncio.run(module.RequestPolicy(app)(scope, receive, send))

        rewritten = json.loads(downstream["event"]["body"])
        self.assertEqual(rewritten["temperature"], 1)
        self.assertEqual(rewritten["max_tokens"], 8)
        self.assertNotIn("max_completion_tokens", rewritten)
        self.assertFalse(downstream["event"]["more_body"])
        headers = dict(response_events[0]["headers"])
        self.assertEqual(headers[b"x-vllm-policy-sha256"], module.POLICY_SHA256.encode())
        self.assertEqual(
            dict(downstream["scope"]["headers"])[b"content-length"],
            str(len(downstream["event"]["body"])).encode(),
        )

    def test_oversized_chunked_request_returns_413_without_calling_app(self):
        module = self._load_policy_module({"temperature": 1})
        app_called = False
        response_events = []

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True

        async def receive():
            return {"type": "http.request", "body": b"too large", "more_body": False}

        async def send(event):
            response_events.append(event)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
        }
        asyncio.run(module.RequestPolicy(app, max_body_bytes=4)(scope, receive, send))

        self.assertFalse(app_called)
        self.assertEqual(response_events[0]["status"], 413)
        self.assertEqual(response_events[1]["type"], "http.response.body")


class QwenVllmValuesTests(unittest.TestCase):
    def test_long_video_runtime_defaults(self):
        values = _qwen_values()
        video = values["vllm"]["mediaIoKwargs"]["video"]

        self.assertEqual(values["vllm"]["maxNumSeqs"], 4)
        self.assertEqual(video["fps"], 2)
        self.assertEqual(video["num_frames"], -1)
        self.assertEqual(video["max_frames"], 8192)
        self.assertEqual(values["requestPolicy"]["maxBodyBytes"], 67108864)
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
    def test_integer_body_limit_override_renders_as_decimal(self):
        deployment = _kind(
            "Deployment",
            "qwen",
            set_values=("requestPolicy.maxBodyBytes=1048576",),
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"] if "value" in item}

        self.assertEqual(env["VLLM_REQUEST_POLICY_MAX_BODY_BYTES"], "1048576")

    def test_runtime_contract_reaches_deployment_and_policy_configmap(self):
        deployment = _kind("Deployment", "qwen")
        configmap = _kind("ConfigMap", "qwen")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        args = container["args"]
        env = {item["name"]: item["value"] for item in container["env"] if "value" in item}

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
        self.assertEqual(env["VLLM_REQUEST_POLICY_MAX_BODY_BYTES"], "67108864")
        self.assertEqual(container["resources"]["limits"]["memory"], "256Gi")
        self.assertIn("request_policy.py", configmap["data"])
        self.assertEqual(
            json.loads(configmap["data"]["request-policy.json"])["max_tokens"],
            16384,
        )


if __name__ == "__main__":
    unittest.main()

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise worker cache ownership without importing CUDA or starting vLLM."""

import ast
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace


RUNNER = (
    Path(__file__).resolve().parents[3]
    / "docker/rtvi_vlm/patches/evs_vllm_public_files/v1/worker/gpu_model_runner.py"
)


def load_cache_lifecycle():
    """Load the actual cache-update prefix, before unrelated batch bookkeeping."""
    tree = ast.parse(RUNNER.read_text())
    runner = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GPUModelRunner"
    )
    methods = []
    for node in runner.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_update_states",
            "_free_evs_encoder_cache",
            "reset_encoder_cache",
        }:
            if node.name == "_update_states":
                cutoff = next(
                    i
                    for i, child in enumerate(node.body)
                    if isinstance(child, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "scheduled_req_ids"
                        for t in child.targets
                    )
                )
                node.body = node.body[:cutoff]
            methods.append(node)
    cls = ast.ClassDef(
        name="CacheLifecycle", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    namespace = {"logger": logging.getLogger(__name__)}
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    exec(compile(module, str(RUNNER), "exec"), namespace)
    return namespace["CacheLifecycle"]


def request(*hashes):
    return SimpleNamespace(mm_features=[SimpleNamespace(identifier=h) for h in hashes])


class TestEvsSchedulerCacheLifetime(unittest.TestCase):
    def test_waiting_request_keeps_free_pending_until_last_owner_finishes(self):
        source = RUNNER.parents[1] / "core/sched/scheduler.py"
        tree = ast.parse(source.read_text())
        scheduler = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Scheduler"
        )
        method = next(
            n
            for n in scheduler.body
            if isinstance(n, ast.FunctionDef) and n.name == "_drain_free_ec_connector_hashes"
        )
        namespace = {}
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        exec(compile(module, str(source), "exec"), namespace)
        state = SimpleNamespace(
            requests={"waiting": request("merged"), "running": request("merged")},
            _pending_free_ec_connector_hashes=["merged", "clip", "merged"],
        )
        drain = namespace["_drain_free_ec_connector_hashes"]
        self.assertEqual(drain(state), ["clip"])
        del state.requests["running"]
        self.assertEqual(drain(state), [])
        del state.requests["waiting"]
        self.assertEqual(drain(state), ["merged"])
        self.assertEqual(drain(state), [])


class TestEvsEncoderCacheLifetime(unittest.TestCase):
    def setUp(self):
        self.runner = load_cache_lifecycle()()
        self.runner.encoder_cache = {"merged": object(), "clip": object()}
        self.runner._pinned_encoder_mm_hashes = {"merged", "clip"}
        self.runner._pending_free_evs_mm_hashes = set()
        self.runner.requests = {}
        self.runner.num_prompt_logprobs = {}
        self.runner.input_batch = SimpleNamespace(remove_request=lambda _id: None)
        self.ipc_frees = []
        self.runner.maybe_free_ec_from_connector = self.ipc_frees.append

    def step(self, *, discard=(), finished=(), new=(), evict=()):
        self.runner._update_states(
            SimpleNamespace(
                finished_req_ids=set(finished),
                new_block_ids_to_zero=None,
                free_ec_connector_mm_hashes=list(discard),
                scheduled_new_reqs=list(new),
                free_encoder_mm_hashes=list(evict),
            )
        )

    def test_session_delete_preserves_active_embedding_until_completion(self):
        self.runner.requests["generate"] = request("merged")
        embedding = self.runner.encoder_cache["merged"]
        self.step(discard=["merged", "clip"], evict=["merged"])
        self.assertIn("merged", self.runner.encoder_cache)
        self.assertIs(self.runner.encoder_cache["merged"], embedding)
        self.assertIn("merged", self.runner._pinned_encoder_mm_hashes)
        self.assertNotIn("clip", self.runner.encoder_cache)
        self.assertEqual(self.ipc_frees, [["merged", "clip"]])
        self.step(finished=["generate"])
        self.assertNotIn("merged", self.runner.encoder_cache)
        self.assertFalse(self.runner._pending_free_evs_mm_hashes)

    def test_new_queued_request_is_an_owner_before_installation(self):
        queued = request("merged")
        self.step(discard=["merged"], new=[queued])
        self.assertIn("merged", self.runner.encoder_cache)
        self.runner.requests["queued"] = queued
        self.step()  # Unscheduled/preempted requests must retain their entry.
        self.assertIn("merged", self.runner.encoder_cache)
        self.step(finished=["queued"])
        self.assertNotIn("merged", self.runner.encoder_cache)

    def test_shared_embedding_waits_for_last_owner(self):
        self.runner.requests = {"first": request("merged"), "second": request("merged")}
        self.step(discard=["merged"], finished=["first"])
        self.assertIn("merged", self.runner.encoder_cache)
        self.step(finished=["second"])
        self.assertNotIn("merged", self.runner.encoder_cache)

    def test_repeated_frees_and_missing_entries_are_idempotent(self):
        self.step(discard=["merged", "merged", "missing"])
        self.step(discard=["merged"])
        self.assertFalse(self.runner._pending_free_evs_mm_hashes)
        self.assertNotIn("merged", self.runner._pinned_encoder_mm_hashes)

    def test_normal_scheduler_eviction_still_releases_unpinned_entries(self):
        self.runner._pinned_encoder_mm_hashes.remove("clip")
        self.step(evict=["clip"])
        self.assertNotIn("clip", self.runner.encoder_cache)
        self.assertIn("merged", self.runner.encoder_cache)

    def test_explicit_cache_reset_clears_deferred_frees(self):
        self.runner.requests["generate"] = request("merged")
        self.step(discard=["merged"])
        self.runner.reset_encoder_cache()
        self.assertFalse(self.runner.encoder_cache)
        self.assertFalse(self.runner._pending_free_evs_mm_hashes)
        self.assertNotIn("merged", self.runner._pinned_encoder_mm_hashes)
        self.assertIn("clip", self.runner._pinned_encoder_mm_hashes)
        # A repopulated discarded hash must be eligible for normal eviction.
        self.runner.encoder_cache["merged"] = object()
        self.step(finished=["generate"], evict=["merged"])
        self.assertNotIn("merged", self.runner.encoder_cache)


if __name__ == "__main__":
    unittest.main()

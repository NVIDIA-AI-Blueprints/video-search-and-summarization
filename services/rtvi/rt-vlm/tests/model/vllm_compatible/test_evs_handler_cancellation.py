# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify cancellation reaches engine iteration through the installed EVS handler."""

import asyncio
from types import SimpleNamespace
import unittest


class TestEvsHandlerCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_local_encode_cancellation_drains_engine_before_session_delete(self):
        from vllm.entrypoints.openai.serving_video_sessions import OpenAIServingVideoSessions

        entered, cancelled, drain = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def encode(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await drain.wait()
                raise
            yield None

        async def free_ec_caches(*args):
            pass

        handler = OpenAIServingVideoSessions(
            SimpleNamespace(
                encode=encode, free_ec_caches=free_ec_caches, get_tokenizer=lambda: None
            )
        )
        sid = handler.manager.create_session(model="test-model", token_budget=1, prompt="describe")
        task = asyncio.create_task(
            handler.add_clip_tensors(sid, [], {"total_num_frames": 3}, chunk_id=0)
        )
        delete_task = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(cancelled.wait(), timeout=2)
            self.assertFalse(task.done())
            delete_task = asyncio.create_task(handler.delete_session(sid))
            await asyncio.sleep(0)
            self.assertFalse(
                delete_task.done(), "Session deletion raced engine cancellation cleanup"
            )
            drain.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
            await asyncio.wait_for(delete_task, timeout=2)
        finally:
            drain.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if delete_task is not None:
                await asyncio.gather(delete_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import unittest

from vss_agent_gateway.contract import ContractError, CreateRunRequest, RunEvent
from vss_agent_gateway.sse import iter_sse


class ContractTest(unittest.TestCase):
    def test_parses_run_request_and_rejects_unknown_roles(self) -> None:
        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "hello"}],
                "history": [{"role": "user", "content": "hello"}],
                "surface": "vss-ui",
            },
        )
        self.assertEqual(request.thread_id, "thread-1")
        self.assertEqual(request.input[0].content, "hello")

        with self.assertRaisesRegex(ContractError, "role"):
            CreateRunRequest.from_dict(
                {
                    "thread_id": "thread-1",
                    "input": [{"role": "tool", "content": "unsafe"}],
                },
            )

    def test_serializes_versioned_replayable_event(self) -> None:
        event = RunEvent.create(
            sequence=3,
            type="message.delta",
            run_id="run_1",
            thread_id="thread-1",
            data={"delta": "hi"},
        )
        encoded = event.to_sse().decode()
        self.assertIn("id: 3\n", encoded)
        self.assertIn("event: message.delta\n", encoded)
        self.assertIn('"protocol_version":"1.0"', encoded)

    def test_sse_parser_handles_multiline_data_and_comments(self) -> None:
        frames = list(
            iter_sse(
                io.BytesIO(
                    b": heartbeat\r\nevent: test\r\nid: 7\r\ndata: one\r\ndata: two\r\n\r\n",
                ),
            ),
        )
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].event, "test")
        self.assertEqual(frames[0].id, "7")
        self.assertEqual(frames[0].data, "one\ntwo")


if __name__ == "__main__":
    unittest.main()

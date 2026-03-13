# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for vst.snapshot inner function."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from vss_agents.tools.vst.snapshot import VSTSnapshotConfig
from vss_agents.tools.vst.snapshot import VSTSnapshotISOInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOffsetInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOutput
from vss_agents.tools.vst.snapshot import vst_snapshot


class TestVSTSnapshotInner:
    """Test vst_snapshot inner function."""

    @pytest.fixture
    def config(self):
        return VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
        )

    @pytest.fixture
    def config_iso(self):
        return VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            time_format="iso",
        )

    @pytest.fixture
    def config_with_overlay(self):
        return VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_snapshot_success_with_seconds(self, config, mock_builder):
        """Test snapshot with seconds-based start_time."""
        with patch("vss_agents.tools.vst.snapshot.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.snapshot.get_timeline", new_callable=AsyncMock) as mock_timeline:
                mock_timeline.return_value = ("2025-01-01T00:00:00.000+00:00", "2025-01-01T01:00:00.000+00:00")

                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.text = AsyncMock(
                    return_value=json.dumps({"imageUrl": "http://10.0.0.1:30888/vst/img.jpg"})
                )
                mock_response_cm = AsyncMock()
                mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response_cm.__aexit__ = AsyncMock(return_value=False)

                mock_session = MagicMock()
                mock_session.get.return_value = mock_response_cm
                mock_session_cm = AsyncMock()
                mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session_cm.__aexit__ = AsyncMock(return_value=False)

                with patch("vss_agents.tools.vst.snapshot.aiohttp.ClientSession", return_value=mock_session_cm):
                    with patch("vss_agents.tools.vst.snapshot.create_retry_strategy") as mock_retry:

                        async def fake_retry(*args, **kwargs):
                            yield MagicMock(
                                __enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False)
                            )

                        mock_retry.return_value = fake_retry()

                        gen = vst_snapshot.__wrapped__(config, mock_builder)
                        fi = await gen.__anext__()
                        inner_fn = fi.single_fn

                        inp = VSTSnapshotOffsetInput(sensor_id="camera1", start_time=30.0)
                        result = await inner_fn(inp)

                        assert isinstance(result, VSTSnapshotOutput)
                        assert "1.2.3.4:30888" in result.image_url
                        assert result.stream_id == "stream-uuid"

    @pytest.mark.asyncio
    async def test_snapshot_success_with_iso_timestamp(self, config_iso, mock_builder):
        """Test snapshot with ISO 8601 timestamp start_time."""
        with patch("vss_agents.tools.vst.snapshot.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=json.dumps({"imageUrl": "http://10.0.0.1:30888/vst/img.jpg"}))
            mock_response_cm = AsyncMock()
            mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response_cm.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get.return_value = mock_response_cm
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cm.__aexit__ = AsyncMock(return_value=False)

            with patch("vss_agents.tools.vst.snapshot.aiohttp.ClientSession", return_value=mock_session_cm):
                with patch("vss_agents.tools.vst.snapshot.create_retry_strategy") as mock_retry:

                    async def fake_retry(*args, **kwargs):
                        yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                    mock_retry.return_value = fake_retry()

                    gen = vst_snapshot.__wrapped__(config_iso, mock_builder)
                    fi = await gen.__anext__()
                    inner_fn = fi.single_fn

                    inp = VSTSnapshotISOInput(sensor_id="camera1", start_time="2025-01-01T00:05:00.000Z")
                    result = await inner_fn(inp)

                    assert isinstance(result, VSTSnapshotOutput)
                    assert "1.2.3.4:30888" in result.image_url
                    assert result.stream_id == "stream-uuid"

    @pytest.mark.asyncio
    async def test_snapshot_uses_correct_input_schema_offset(self, config, mock_builder):
        """Test that offset mode uses VSTSnapshotOffsetInput schema."""
        gen = vst_snapshot.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        assert fi.input_schema is VSTSnapshotOffsetInput

    @pytest.mark.asyncio
    async def test_snapshot_uses_correct_input_schema_iso(self, config_iso, mock_builder):
        """Test that iso mode uses VSTSnapshotISOInput schema."""
        gen = vst_snapshot.__wrapped__(config_iso, mock_builder)
        fi = await gen.__anext__()
        assert fi.input_schema is VSTSnapshotISOInput

    @pytest.mark.asyncio
    async def test_snapshot_out_of_range(self, config, mock_builder):
        with patch("vss_agents.tools.vst.snapshot.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.snapshot.get_timeline", new_callable=AsyncMock) as mock_timeline:
                # Short video - 10 seconds
                mock_timeline.return_value = ("2025-01-01T00:00:00.000+00:00", "2025-01-01T00:00:10.000+00:00")

                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.text = AsyncMock(
                    return_value=json.dumps({"imageUrl": "http://10.0.0.1:30888/vst/img.jpg"})
                )
                mock_response_cm = AsyncMock()
                mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response_cm.__aexit__ = AsyncMock(return_value=False)

                mock_session = MagicMock()
                mock_session.get.return_value = mock_response_cm
                mock_session_cm = AsyncMock()
                mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session_cm.__aexit__ = AsyncMock(return_value=False)

                with patch("vss_agents.tools.vst.snapshot.aiohttp.ClientSession", return_value=mock_session_cm):
                    with patch("vss_agents.tools.vst.snapshot.create_retry_strategy") as mock_retry:

                        async def fake_retry(*args, **kwargs):
                            yield MagicMock(
                                __enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False)
                            )

                        mock_retry.return_value = fake_retry()

                        gen = vst_snapshot.__wrapped__(config, mock_builder)
                        fi = await gen.__anext__()
                        inner_fn = fi.single_fn

                        # Request timestamp beyond the timeline
                        inp = VSTSnapshotOffsetInput(sensor_id="camera1", start_time=60.0)
                        with pytest.raises(ValueError, match="out of the video timeline"):
                            await inner_fn(inp)

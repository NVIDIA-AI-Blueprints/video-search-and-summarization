# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Serializer for dense caption data."""

import json

from common.chunk_info import ChunkInfo
from common.logger import logger
from models.base_vlm_model import VlmModelOutput
from vlm_pipeline import PipelineChunkResult


class DenseCaptionSerializer:
    """Handles serialization and deserialization of dense caption data to/from JSON."""

    @staticmethod
    def to_json(processed_chunk_list: list[PipelineChunkResult], file_path: str) -> None:
        """Serialize processed chunk results to a JSON file.

        Args:
            processed_chunk_list: List of processed chunk results to serialize
            file_path: Path to the output JSON file
        """
        try:
            with open(file_path, "w") as f:
                for chunk_result in processed_chunk_list:
                    json.dump(
                        {
                            "vlm_response": (
                                chunk_result.vlm_model_output.output
                                if chunk_result.vlm_model_output
                                else ""
                            ),
                            "frame_times": chunk_result.frame_times,
                            "chunk": {
                                "streamId": chunk_result.chunk.streamId,
                                "chunkIdx": chunk_result.chunk.chunkIdx,
                                "file": chunk_result.chunk.file,
                                "pts_offset_ns": chunk_result.chunk.pts_offset_ns,
                                "start_pts": chunk_result.chunk.start_pts,
                                "end_pts": chunk_result.chunk.end_pts,
                                "start_ntp": chunk_result.chunk.start_ntp,
                                "end_ntp": chunk_result.chunk.end_ntp,
                                "start_ntp_float": chunk_result.chunk.start_ntp_float,
                                "end_ntp_float": chunk_result.chunk.end_ntp_float,
                                "is_first": chunk_result.chunk.is_first,
                                "is_last": chunk_result.chunk.is_last,
                                "asset_dir": chunk_result.chunk.asset_dir,
                            },
                        },
                        f,
                    )
                    f.write("\n")
        except Exception as e:
            logger.warning("Failed to write dense caption JSON: %s", e)

    @staticmethod
    def from_json(file_path: str) -> list[PipelineChunkResult]:
        """Deserialize processed chunk results from a JSON file.

        Args:
            file_path: Path to the input JSON file

        Returns:
            List of deserialized PipelineChunkResult objects, sorted by chunk index
        """
        processed_chunk_list: list[PipelineChunkResult] = []
        try:
            with open(file_path, "r") as f:
                for line in f:
                    data = json.loads(line)
                    chunk_info = ChunkInfo()
                    chunk_info.streamId = data["chunk"]["streamId"]
                    chunk_info.chunkIdx = data["chunk"]["chunkIdx"]
                    chunk_info.file = data["chunk"]["file"]
                    chunk_info.pts_offset_ns = data["chunk"]["pts_offset_ns"]
                    chunk_info.start_pts = data["chunk"]["start_pts"]
                    chunk_info.end_pts = data["chunk"]["end_pts"]
                    chunk_info.start_ntp = data["chunk"]["start_ntp"]
                    chunk_info.end_ntp = data["chunk"]["end_ntp"]
                    chunk_info.start_ntp_float = data["chunk"]["start_ntp_float"]
                    chunk_info.end_ntp_float = data["chunk"]["end_ntp_float"]
                    chunk_info.is_first = data["chunk"]["is_first"]
                    chunk_info.is_last = data["chunk"]["is_last"]
                    chunk_result = PipelineChunkResult()

                    chunk_result.vlm_model_output = VlmModelOutput(output=data["vlm_response"])
                    chunk_result.frame_times = data["frame_times"]
                    chunk_result.chunk = chunk_info

                    processed_chunk_list.append(chunk_result)
                # Sort the processed_chunk_list by chunkIdx
                if processed_chunk_list:
                    processed_chunk_list.sort(key=lambda x: x.chunk.chunkIdx)
        except Exception as e:
            logger.warning("Failed to read dense caption JSON: %s", e)
        return processed_chunk_list

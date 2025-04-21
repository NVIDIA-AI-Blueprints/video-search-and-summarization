######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

"""VIA Video Embedding Helper"""

import os
from pathlib import Path

import torch
from pydantic import BaseModel
from safetensors.torch import load_file, save_file

from chunk_info import ChunkInfo


class VideoFrameTimes(BaseModel):
    video_frame_times: list[float]


class EmbeddingHelper:
    """Embedding Helper. Save / retrieve embeddings for a file/chunk."""

    def __init__(self, asset_dir: str, use_gpu_mem=True) -> None:
        """Default constructor

        Args:
            asset_dir: Asset storage directory
            use_gpu_mem: Whether to use GPU memory to load embedding from file
        """
        self._asset_dir = asset_dir
        self._use_gpu_mem = use_gpu_mem

    def _get_embedding_dir(self, chunk: ChunkInfo):
        return os.path.join(
            self._asset_dir, chunk.streamId, "embeddings", f"{chunk.start_pts}_{chunk.end_pts}"
        )

    def save_embeddings(
        self, chunk: ChunkInfo, embeddings: torch.tensor, video_frames_times: list[float]
    ):
        """Save embeddings for a chunk

        Args:
            chunk (ChunkInfo): Chunk to save the embedding for
            embeddings: Embeddings for the chunk
            video_frames_times: List of frame timestamps used for generating the embeddings.
                                The time is in seconds.
        """
        embedding_dir = self._get_embedding_dir(chunk)
        # Create a directory for each chunk
        os.makedirs(embedding_dir, exist_ok=True)

        # Pickle and save the chunk info object
        with open(os.path.join(embedding_dir, "chunk_info.json"), "w") as f:
            f.write(chunk.model_dump_json())

        # Save the embeddings torch tensor
        save_file(
            {"embeddings": embeddings.cpu()}, os.path.join(embedding_dir, "embeddings.safetensors")
        )

        # Pickle and save the list of video frame timestamps
        with open(os.path.join(embedding_dir, "video_frames_times.json"), "w") as f:
            f.write(VideoFrameTimes(video_frame_times=video_frames_times).model_dump_json())

    def have_embedding(self, chunk: ChunkInfo):
        """Check if embeddings are available for a chunk"""
        embedding_dir = self._get_embedding_dir(chunk)
        emb_file = os.path.join(embedding_dir, "video_frames_times.json")
        ft_file = os.path.join(embedding_dir, "embeddings.safetensors")
        return os.path.isfile(emb_file) and os.path.isfile(ft_file)

    def get_chunks_list(self, asset_id: str):
        """Get a list of chunks of an asset whose embeddings are available

        Args:
            asset_id: ID of the asset to check for available chunks.

        Returns:
            A list of chunks
        """
        embedding_dir = os.path.join(self._asset_dir, asset_id, "embeddings")
        chunks: list[ChunkInfo] = []
        for fn in Path(embedding_dir).glob("*/chunk_info.json"):
            with open(fn, "r") as f:
                chunks.append(ChunkInfo.model_validate_json(f.read()))
        chunks.sort(key=lambda chunk: chunk.start_pts)
        return chunks

    def clear_chunks(self, asset_id: str):
        """Clear all chunks and embeddings"""
        embedding_dir = os.path.join(self._asset_dir, asset_id, "embeddings")
        os.system(f"rm -rf {embedding_dir}")

    def get_embedding(self, chunk: ChunkInfo):
        """Get embeddings for a chunk

        Args:
            chunk: Chunk to get embeddings for

        Returns:
            (embeddings, frame_times) as a tuple.
        """
        embedding_dir = self._get_embedding_dir(chunk)
        with open(os.path.join(embedding_dir, "video_frames_times.json"), "r") as f1:
            emb_raw = load_file(os.path.join(embedding_dir, "embeddings.safetensors"))["embeddings"]
            if self._use_gpu_mem:
                emb = emb_raw.cuda()
            else:
                emb = emb_raw.cpu()
            ft = VideoFrameTimes.model_validate_json(f1.read()).video_frame_times
            return emb, ft

    def get_num_frames_embedding(self, chunk: ChunkInfo):
        """Returns the number of frames used for generating the embeddings"""
        embedding_dir = self._get_embedding_dir(chunk)
        with open(os.path.join(embedding_dir, "video_frames_times.json"), "r") as f1:
            return len(VideoFrameTimes.model_validate_json(f1.read()).video_frame_times)

    def remove_chunk_data(self, chunk: ChunkInfo):
        """Remove embeddings and related data for a chunk"""
        embedding_dir = self._get_embedding_dir(chunk)
        os.system(f"rm -rf {embedding_dir}")

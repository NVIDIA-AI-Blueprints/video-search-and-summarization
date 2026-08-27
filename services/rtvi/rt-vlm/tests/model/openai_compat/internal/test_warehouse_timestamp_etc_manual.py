################################################################################
#  SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
#  All rights reserved.
#  SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
#  NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
#  property and proprietary rights in and to this material, related
#  documentation and any modifications thereto. Any use, reproduction,
#  disclosure or distribution of this material and related documentation
#  without an express license agreement from NVIDIA CORPORATION or
#  its affiliates is strictly prohibited.
################################################################################

# PYTHONPATH=src pytest tests/

import os

from models.common.frame_jpeg_tensor_generator import FrameJPEGTensorGenerator

# ModelContextFrameInput logic moved to CompOpenAIModel
from models.openai_compat.openai_compat_model import CompOpenAIModel
from vlm_pipeline.video_file_frame_getter import (
    ChunkInfo,
    DefaultFrameSelector,
    VideoFileFrameGetter,
)


def check_timestamp_output_warehouse_80(model_ctx):
    chunk = ChunkInfo()
    chunk.file = "/opt/nvidia/rtvi/streams/concat_wh_52.mp4"  # noqa: E501

    chunk.start_pts = 15 * 60 * 1000000000
    chunk.end_pts = 16 * 60 * 1000000000

    NUM_FRAMES = 10
    frame_getter = VideoFileFrameGetter(DefaultFrameSelector(NUM_FRAMES), enable_jpeg_output=True)
    frames, frames_pts = frame_getter.get_frames(chunk)
    embeds = FrameJPEGTensorGenerator().get_embeddings([frames])  # args.use_trt

    print("length of embeds", len(frames), len(embeds))
    assert len(frames) == NUM_FRAMES
    assert len(embeds) == 1
    model_ctx.set_video_embeds(
        [chunk],
        embeds,
        None,
        [frames_pts],
    )
    responses, stats = model_ctx.ask(
        "Write a concise and clear dense caption for the provided warehouse video,"
        " focusing on irregular or hazardous events such as boxes falling, workers not wearing PPE,"
        " workers falling, workers taking photographs, workers chitchatting, forkift stuck, etc."
    )
    assert len(responses) == 1

    response = responses[0]

    print("response is ", response)


# Manual environments needed:
# export OPENAI_API_KEY=key
# Specific video file input
# "/opt/models/streams/Warehouse_240219_GoPro_9_GX010002/Warehouse_240219_GoPro_9_GX010002.MP4"
def test_openai_gpt4o_chunk_check_event_and_timestamp_correctness():
    # Note: NV_SECRET if set, VIA will default to use LLM Gateway
    os.environ.pop("NV_LLMG_CLIENT_SECRET", None)
    os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"] = "gpt-4o"
    # os.environ['OPENAI_API_VERSION'] = "2023-07-01-preview"
    model = CompOpenAIModel()
    model_ctx = model

    check_timestamp_output_warehouse_80(model_ctx=model_ctx)

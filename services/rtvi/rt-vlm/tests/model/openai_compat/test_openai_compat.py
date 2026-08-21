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

from test_common import TempEnv


# Manual environments needed:
# export OPENAI_API_KEY=key
def test_openai_gpt4o_chunk_1s():
    # Note: NV_SECRET if set, VIA will default to use LLM Gateway
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
        }
    ):
        model = CompOpenAIModel()
        model_ctx = model

        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4"
        chunk.start_pts = 1 * 1000000000
        chunk.end_pts = 2 * 1000000000

        NUM_FRAMES = 2
        frame_getter = VideoFileFrameGetter(
            DefaultFrameSelector(NUM_FRAMES), enable_jpeg_output=True
        )
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
        responses, stats = model_ctx.ask("Summarize the video.")
        assert len(responses) == 1

        response = responses[0]

        print("response is ", response)


# Manual environments needed:
# unset NV_LLMG_CLIENT_SECRET
# export OPENAI_API_KEY=key
def test_openai_gpt4v_turbo_chunk_1s():
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4-turbo-2024-04-09",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
        }
    ):
        model = CompOpenAIModel()
        model_ctx = model

        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4"
        chunk.start_pts = 1 * 1000000000
        chunk.end_pts = 2 * 1000000000

        NUM_FRAMES = 2
        frame_getter = VideoFileFrameGetter(
            DefaultFrameSelector(NUM_FRAMES), enable_jpeg_output=True
        )
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
        responses, stats = model_ctx.ask("Summarize the video.")
        assert len(responses) == 1

        response = responses[0]

        print("response is ", response)


# Manual environments needed:
# export OPENAI_API_KEY=key
def test_openai_with_base_url_gpt4o_chunk_1s():
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
            "VIA_VLM_ENDPOINT": "https://api.openai.com/v1/",
        }
    ):
        model = CompOpenAIModel()
        model_ctx = model

        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4"
        chunk.start_pts = 1 * 1000000000
        chunk.end_pts = 2 * 1000000000

        NUM_FRAMES = 2
        frame_getter = VideoFileFrameGetter(
            DefaultFrameSelector(NUM_FRAMES), enable_jpeg_output=True
        )
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
        responses, stats = model_ctx.ask("Summarize the video.")
        assert len(responses) == 1

        response = responses[0]

        print("response is ", response)


def test_openai_model_info_NO_MODEL():
    from models.openai_compat.openai_compat_model import CompOpenAIModel

    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
            "VIA_VLM_ENDPOINT": "",
        }
    ):
        os.environ.pop("NV_LLMG_CLIENT_SECRET", None)
        os.environ.pop("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", None)
        os.environ.pop("VIA_VLM_ENDPOINT", None)
        id, api_type, owned_by = CompOpenAIModel.get_model_info()
        print(api_type, id, owned_by)
        assert api_type == "openai"
        assert id == "ModelNotLoaded"
        assert owned_by == "ModelNotLoaded"


def test_openai_model_info_gpt4o():
    # Note: NV_SECRET if set, VIA will default to use LLM Gateway
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
            "VIA_VLM_ENDPOINT": "https://api.openai.com/v1/",
        }
    ):
        id, api_type, owned_by = CompOpenAIModel.get_model_info()
        print(api_type, id, owned_by)
        assert api_type == "openai"
        assert id == "gpt-4o"
        assert owned_by == "https--api-openai-com-v1-"


def test_openai_model_info_gpt4v():
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4-turbo-2024-04-09",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
        }
    ):
        CompOpenAIModel()
        id, api_type, owned_by = CompOpenAIModel.get_model_info()
        assert api_type == "openai"
        assert id == "gpt-4-turbo-2024-04-09"
        assert owned_by == "https--api-openai-com-v1-"


# Manual environments needed:
# export VIA_VLM_API_KEY=key
def test_openai_with_base_url_gpt4o_chunk_1s_with_VIA_VLM_API_KEY():
    # Note: NV_SECRET if set, VIA will default to use LLM Gateway
    with TempEnv(
        {
            "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            # "OPENAI_API_VERSION": "2023-07-01-preview",
            "NV_LLMG_CLIENT_SECRET": "",
            "OPENAI_API_KEY": "",
            "VIA_VLM_ENDPOINT": "https://api.openai.com/v1/",
        }
    ):
        model = CompOpenAIModel()
        model_ctx = model

        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4"
        chunk.start_pts = 1 * 1000000000
        chunk.end_pts = 2 * 1000000000

        NUM_FRAMES = 2
        frame_getter = VideoFileFrameGetter(
            DefaultFrameSelector(NUM_FRAMES), enable_jpeg_output=True
        )
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
        responses, stats = model_ctx.ask("Summarize the video.")
        assert len(responses) == 1

        response = responses[0]

        print("response is ", response)

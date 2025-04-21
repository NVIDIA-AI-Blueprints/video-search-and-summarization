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


from via_logger import logger


class ModelContextFrameInput:

    def __init__(self, model) -> None:
        self._model = model
        self.messages = []

    def set_video_embeds(self, chunks, video_embeds, video_frames, video_frames_times):
        self._video_frames_times = video_frames_times
        self._chunks = chunks

        self._video_embeds = video_embeds

    def ask(self, query, respond=True, skip_time_tokens=False, generation_config=None, chunk=None):

        logger.debug(f"query is {str(query)}")
        return self._model.generate(
            query, self._video_embeds, self._video_frames_times, generation_config, chunk=chunk
        )


if __name__ == "__main__":

    # To test and debug, please use harness:
    # PYTHONPATH=src pytest tests/model/gpt4/ -s
    # Please add new test case for each bug
    pass

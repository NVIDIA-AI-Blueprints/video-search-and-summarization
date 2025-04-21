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

import os
import sys

import torch

sys.path.append(os.path.dirname(__file__) + "/VILA")


class Vila15Context:
    """VILA 1.5 VLM Model Conversation context.

    This helps maintain conversation context for different chunks/files while
    not having to reload the actual model."""

    def __init__(self, model) -> None:
        """Vila15Context constructor

        Args:
            model: Vila15Model instance
        """
        # Get the conversation object
        self._conv = model.get_conv()
        self._model = model
        if "mpt" in model.model_name.lower():
            self._roles = ("user", "assistant")
        else:
            self._roles = self._conv.roles
        self.messages = []

    def set_video_embeds(self, chunks, video_embeds, video_frames, video_frames_times):
        """Set the chunks, and corresponding video embeddings and frame times.
        Accepts batched inputs (lists)"""
        self._video_frames_times = video_frames_times
        self._chunks = chunks

        self._video_embeds = video_embeds
        self._video_embeds = torch.stack([v.half().cuda() for v in self._video_embeds])

    def ask(self, query, respond=True, skip_time_tokens=False, generation_config=None, chunk=None):
        """Ask a query to the model

        Args:
            query: Prompt for the VLM model
            respond: If true, generate response. If false, only add to the conversation context.
                     Defaults to True.
            skip_time_tokens: Skip decoding time tokens in the response. Defaults to False.
            generation_config: Dictionary of VLM output parameters (top-k, seed etc).
                               Defaults to None.

        Returns:
            List of VLM responses per chunk for the batched input
        """

        if "<image>" in query:
            self._conv.messages = []

        # Add the <image> tag to the prompt, to mark where the video embeddings should be inserted
        inp = "<image>\n" * len(self._video_frames_times[0])

        # Add the user prompt to the conversation context
        self._conv.append_message(self._conv.roles[0], inp + query)
        self._conv.append_message(self._conv.roles[1], None)

        if not respond:
            # Only for adding the prompt to the context. No need for VLM response
            return

        # Convert the conversation to a string prompt
        prompt = self._conv.get_prompt()

        # Generate a response from the VLM model
        return self._model.generate(
            prompt, self._video_embeds, self._video_frames_times, generation_config, chunk=chunk
        )

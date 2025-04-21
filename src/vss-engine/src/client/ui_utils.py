######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import time
from logging import Logger

import gradio as gr

from utils import MediaFileInfo, StreamSettingsCache


def get_live_stream_preview_chunks(ls_id):
    if not ls_id:
        return

    def update_preview_file(ls_id):
        # Create or touch preview file to signal active UI preview
        preview_file = f"/tmp/assets/{ls_id}/.ui_preview"
        try:
            os.makedirs(os.path.dirname(preview_file), exist_ok=True)
            with open(preview_file, "a"):
                os.utime(preview_file, None)
        except Exception:
            return

    last_file = None
    try:
        update_preview_file(ls_id)
        # Check both files exist
        file_paths = [f"/tmp/assets/{ls_id}/{ls_id}_{i}.ts" for i in range(2)]

        while not all(os.path.exists(p) for p in file_paths):
            time.sleep(0.1)
            update_preview_file(ls_id)
            try:
                yield gr.update()
            except GeneratorExit:
                return

        while True:
            update_preview_file(ls_id)
            try:
                # Get modification times
                mtimes = []
                for p in file_paths:
                    mtime = os.path.getmtime(p)
                    mtimes.append((p, mtime))

                # Sort by mtime to get oldest file
                mtimes.sort(key=lambda x: x[1])
                oldest_file = mtimes[0][0]

                # Check if file is older than 20 seconds
                current_time = time.time()
                if (
                    current_time - mtimes[1][1] <= 20
                    and current_time - mtimes[0][1] <= 20
                    and oldest_file != last_file
                ):
                    if (
                        MediaFileInfo.get_info(mtimes[0][0]).video_duration_nsec < 1000000000 * 12
                        and MediaFileInfo.get_info(mtimes[1][0]).video_duration_nsec
                        < 1000000000 * 12
                    ):
                        yield oldest_file
                        last_file = oldest_file

            except (IOError, OSError):
                yield None
                return
            except GeneratorExit:
                return

            try:
                time.sleep(0.1)
                yield gr.update()
            except GeneratorExit:
                return
    finally:
        try:
            yield None
        except GeneratorExit:
            return


def get_overlay_live_stream_preview_chunks(ls_id):
    if not ls_id:
        return

    second_newest_file = None
    last_file = None
    try:
        # Directory path
        directory_path = f"/tmp/via/cached_frames/{ls_id}"

        while not os.path.exists(directory_path):
            time.sleep(0.1)
            try:
                yield gr.update()
            except GeneratorExit:
                return

        while True:
            try:
                # List all files in the directory
                all_files = os.listdir(directory_path)
                # Filter for .ts files
                ts_files = [f for f in all_files if f.endswith(".ts")]

                # Get modification times and sort by them in descending order
                ts_files_with_mtime = [
                    (f, os.path.getmtime(os.path.join(directory_path, f))) for f in ts_files
                ]
                ts_files_with_mtime.sort(key=lambda x: x[1], reverse=True)

                # Select the second newest file
                if len(ts_files_with_mtime) > 1:
                    second_newest_file = ts_files_with_mtime[1][0]
                else:
                    time.sleep(0.1)
                    yield gr.update()

                if second_newest_file != last_file:
                    yield os.path.join(directory_path, second_newest_file)
                    last_file = second_newest_file
                else:
                    time.sleep(0.1)
                    yield gr.update()

            except (IOError, OSError):
                yield None
                return
            except GeneratorExit:
                return

            try:
                time.sleep(0.1)
                yield gr.update()
            except GeneratorExit:
                return
    finally:
        try:
            yield None
        except GeneratorExit:
            return


class RetrieveCache:
    def __init__(self, logger: Logger = None):
        self.stream_settings_cache = StreamSettingsCache(logger=logger)
        self.logger = logger

    def retreive_UI_updates(self, video_id: str, stream_settings: dict = {}):
        """Retreive UI updates based on stream settings"""

        if not stream_settings:
            self.logger.info(f"Getting stream settings for {video_id}.")
            stream_settings = self.stream_settings_cache.load_stream_settings(video_id=video_id)
            self.logger.info(f"Stream settings: {stream_settings}")

        id_settings = stream_settings if stream_settings else {}

        if not id_settings:
            self.logger.debug(f"No stream settings found for {video_id}.")
            return [gr.update(interactive=True)] * 30 + [gr.update(value=[[""] * 4] * 10)] + [[[]]]
            # other components              # alerts_table              # table_state

        # Process table data
        # First create simplified table state (name, events only)
        table_state = [
            [i["alert"]["name"], ", ".join(i["alert"]["events"]), "Edit", "X"]
            for i in id_settings.get("tools", [])
            if i["type"] == "alert"
        ]
        table_data = table_state.copy()

        # Map settings to Gradio updates
        updates = [
            gr.update(value=id_settings.get("enable_chat", False), interactive=True),  # enable_chat
            gr.update(value=id_settings.get("chunk_duration", 10), interactive=True),  # chunk_size
            gr.update(
                value=id_settings.get("summary_duration", 60), interactive=True
            ),  # summary_duration
            gr.update(value=id_settings.get("prompt", ""), interactive=True),  # summary_prompt
            gr.update(
                value=id_settings.get("caption_summarization_prompt", ""), interactive=True
            ),  # caption_summarization_prompt
            gr.update(
                value=id_settings.get("summary_aggregation_prompt", ""), interactive=True
            ),  # summary_aggregation_prompt
            gr.update(value=id_settings.get("temperature", 0.4), interactive=True),  # temperature
            gr.update(value=id_settings.get("top_p", 1), interactive=True),  # top_p
            gr.update(value=id_settings.get("top_k", 100), interactive=True),  # top_k
            gr.update(value=id_settings.get("max_tokens", 512), interactive=True),  # max_new_tokens
            gr.update(value=id_settings.get("seed", 1), interactive=True),  # seed
            gr.update(
                value=id_settings.get("num_frames_per_chunk", 0), interactive=True
            ),  # num_frames_per_chunk
            gr.update(
                value=id_settings.get("vlm_input_width", 0), interactive=True
            ),  # vlm_input_width
            gr.update(
                value=id_settings.get("vlm_input_height", 0), interactive=True
            ),  # vlm_input_height
            gr.update(
                value=id_settings.get("summarize_top_p", 1), interactive=True
            ),  # summarize_top_p
            gr.update(
                value=id_settings.get("summarize_temperature", 0.5), interactive=True
            ),  # summarize_temperature
            gr.update(
                value=id_settings.get("summarize_max_tokens", 512), interactive=True
            ),  # summarize_max_tokens
            gr.update(value=id_settings.get("chat_top_p", 0.5), interactive=True),  # chat_top_p
            gr.update(
                value=id_settings.get("chat_temperature", 0.5), interactive=True
            ),  # chat_temperature
            gr.update(
                value=id_settings.get("chat_max_tokens", 512), interactive=True
            ),  # chat_max_tokens
            gr.update(
                value=id_settings.get("notification_top_p", 0.5), interactive=True
            ),  # notification_top_p
            gr.update(
                value=id_settings.get("notification_temperature", 0.5), interactive=True
            ),  # notification_temperature
            gr.update(
                value=id_settings.get("notification_max_tokens", 512), interactive=True
            ),  # notification_max_tokens
            gr.update(
                value=id_settings.get("summarize_batch_size", 100), interactive=True
            ),  # summarize_batch_size
            gr.update(
                value=id_settings.get("rag_batch_size", 100), interactive=True
            ),  # rag_batch_size
            gr.update(
                value=id_settings.get("rag_type", "vector-rag"), interactive=True
            ),  # rag_type
            gr.update(value=id_settings.get("rag_top_k", 10), interactive=True),  # rag_top_k
            gr.update(
                value=id_settings.get("enable_cv_metadata", False), interactive=True
            ),  # enable_cv_metadata
            gr.update(
                value=id_settings.get("cv_pipeline_prompt", ""), interactive=True
            ),  # cv_pipeline_prompt
            gr.update(
                value=id_settings.get("enable_audio", False), interactive=True
            ),  # enable_audio
            gr.update(
                value=table_data, interactive=True
            ),  # alerts_table (TODO: headers/column names)
            [table_state],  # table_state
        ]

        return updates

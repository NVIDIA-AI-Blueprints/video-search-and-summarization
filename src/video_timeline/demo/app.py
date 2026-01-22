# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import os
import gradio as gr
from gradio_videotimeline import VideoTimeline

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")

example = {
    "video": os.path.join(MEDIA_DIR, "world.mp4"),
    "subtitles": None,
    'timestamps': [2, 5, 10, 15, 20, 28],
    'marker_labels': ['Aa', 'Bb', 'Cc', 'Dd', 'Ee', 'Ff'],
    'start_times': [2, 5, 10, 15, 25, 28],
    'end_times': [3, 8, 13, 20, 28, 30],
    'descriptions': ['Description 1', 'Description 2', 'Description 3', 'Description 4', 'Description 5', 'Description 6']
}

with gr.Blocks() as demo:
    video = VideoTimeline(interactive=False,
                          show_label=False,
                          show_download_button=False,
                          )
    gr.Examples(examples=[example], inputs=[video], label="Select an example", elem_id="example")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)

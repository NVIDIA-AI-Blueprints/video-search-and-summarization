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

import numpy as np
import cv2 
from core.utils.video_capture import open_video_capture, open_video_writer
import matplotlib.pyplot as plt

def read_image_from_video(video_path, output_path):
    # read a video file
    cap = open_video_capture(video_path)

    # get the number of frames in the video file
    num_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print('#frame: ', num_frames)

    # get the width and height of the video file
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    #print(width)
    #print(height)

    # get a frame from the video file
    ret, frame = cap.read()

    if ret:
        print(frame.shape)


        # Save the frame as an image file
        cv2.imwrite(f'{output_path}/first_frame.jpg', frame)
    else:
        print("Error: Could not read frame.")

    # Release the video capture object
    cap.release()
    return frame

def draw_bboxes(img, bboxes, show=True):
    out_img = img.copy()
    out_img = cv2.cvtColor(out_img, cv2.COLOR_RGB2BGR)
    for bbox in bboxes:
        x1, y1, x2, y2 = bbox
        color = tuple((np.random.random(size=3)) * 256)
        cv2.rectangle(out_img, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
    #cv2.imwrite(output_path, out_img)

    if show:
        plt.imshow(out_img)
        plt.show()
    return out_img

def draw_bboxes_with_df(img, df):
    out_img = img.copy()
    for idx, row in df.iterrows():
        x1 = row['x1']
        x2 = row['x2']
        y1 = row['y1']
        y2 = row['y2']
        cv2.rectangle(out_img, (int(x1), int(y1)), (int(x2), int(y2)), (255, 0, 0), 2)
    plt.imshow(out_img)
    plt.show()


def generate_video_bbox(video_path, bboxes, output_path):
    # read a video file
    cap = open_video_capture(video_path)

    # get the number of frames in the video file
    num_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print('#frame: ', num_frames)

    # get the width and height of the video file
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    #print(width)
    #print(height)

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    output = open_video_writer(output_path, fps, (width, height)) 
    # get a frame from the video file
    # capture the frame 
    for i in tqdm(range(num_frames)):
        ret, frame = cap.read()

        if ret:
            #print(frame.shape)

            out = draw_bboxes(frame, bboxes, False)
            # Save the frame as an image file
            #cv2.imwrite(f'{output_path}/first_frame.jpg', frame)
            output.write(out)

        else:
            print("Error: Could not read frame.")

    # Release the video capture object
    cap.release()
    output.release()
    return frame

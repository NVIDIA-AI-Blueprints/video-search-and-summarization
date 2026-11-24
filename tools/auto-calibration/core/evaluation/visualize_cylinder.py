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

import cv2
from core.utils.video_capture import open_video_capture, open_video_writer
import numpy as np
import yaml
import sys

from evaluate import project_3d_point_to_image_3x4_proj_mat

def get_circle_points(x0, y0, proj_mat):
    radius = 0.35 
    height = 1.75
    # top circle
    top_circle_points = []
    for angle in np.arange(0, 2 * np.pi, 0.1):
        x = x0 + radius * np.cos(angle)
        y = y0 + radius * np.sin(angle)
        top_circle_points.append((x, y, height))

    # bottom circle
    bottom_circle_points = []
    for angle in np.arange(0, 2 * np.pi, 0.1):
        x = x0 + radius * np.cos(angle)
        y = y0 + radius * np.sin(angle)
        bottom_circle_points.append((x, y, 0))

    return top_circle_points, bottom_circle_points

def draw_cylinder(image, top_circle_points, bottom_circle_points):
    for i in range(len(top_circle_points)-1):
        cv2.line(image, (int(top_circle_points[i][0]), int(top_circle_points[i][1])), 
                 (int(top_circle_points[i+1][0]), int(top_circle_points[i+1][1])), (0, 0, 255), 2)
        cv2.line(image, (int(bottom_circle_points[i][0]), int(bottom_circle_points[i][1])), 
                 (int(bottom_circle_points[i+1][0]), int(bottom_circle_points[i+1][1])), (0, 0, 255), 2)
    left_idx = len(top_circle_points) // 4
    right_idx = len(top_circle_points) * 3 // 4
    cv2.line(image, (int(top_circle_points[left_idx][0]), int(top_circle_points[left_idx][1])), 
             (int(bottom_circle_points[left_idx][0]), int(bottom_circle_points[left_idx][1])), (0, 0, 255), 2)
    cv2.line(image, (int(top_circle_points[right_idx][0]), int(top_circle_points[right_idx][1])), 
             (int(bottom_circle_points[right_idx][0]), int(bottom_circle_points[right_idx][1])), (0, 0, 255), 2)

def draw_cylinders(image, proj_mat):
    grid_size = 2 # 1m x 1m
    min_x = -10
    max_x = 10
    min_y = -10
    max_y = 10

    for x in np.arange(min_x, max_x, grid_size):
        for y in np.arange(min_y, max_y, grid_size):
            pt3d = np.array([x, y, 0])
            # get the points of top circle and bottom circle
            top_circle_points, bottom_circle_points = get_circle_points(x, y, proj_mat)

            top3d = [project_3d_point_to_image_3x4_proj_mat(pt, proj_mat) for pt in top_circle_points]
            bottom3d = [project_3d_point_to_image_3x4_proj_mat(pt, proj_mat) for pt in bottom_circle_points]

            draw_cylinder(image, top3d, bottom3d)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input-dir", type=str, default="director of AutoMagicCalib output")
    args = parser.parse_args()

    with open(f"{args.input_dir}/camInfo_hyper_00_opencv.yaml", "r") as f:
        data = yaml.safe_load(f)
    print(data)

    # get projection matrix
    proj_mat = np.array(data["projectionMatrix_3x4_w2p"]).reshape(3, 4)
    print(proj_mat)

    # read video
    video_path = f"{args.input_dir}/rectified.mp4"
    cap = open_video_capture(video_path)
    out_video_path = f"{args.input_dir}/rectified_annotated.mp4"
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    out_video = open_video_writer(out_video_path, fps, (width, height))
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        #cv2.imshow("frame", frame)
        #cv2.waitKey(1)

        # draw cylinder
        draw_cylinders(frame, proj_mat)
        out_video.write(frame)

    cap.release()
    out_video.release()
    cv2.destroyAllWindows()

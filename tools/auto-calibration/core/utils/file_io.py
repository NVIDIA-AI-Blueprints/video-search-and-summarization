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

import yaml
import pandas as pd
import numpy as np

# my package
from core.camera_estimation.preprocess_bbox import get_data_gt, remove_bbox_touching_borders


def read_config(filename):
    """ Read configuration file

    Args:
        filename(str): configuration file path

    Returns:
        config(dict): configuration dictionary
    """
    with open(filename, 'r') as f:
        config = yaml.safe_load(f)
    return config

def read_bboxes(input_bbox_path):
    """ Read bboxes from a file
    Args:
        input_bbox_path(str): bounding box file path
    
    Returns:
        df(pd.DataFrame): DataFrame containing bounding boxes and metadata
    """
    # read raw boudning boxes 
    input_bboxes = get_data_gt(input_bbox_path)
    bboxes = [list(bbox[0]) for bbox in input_bboxes.values()]
    bboxes = np.array(bboxes)

    bboxes = remove_bbox_touching_borders(bboxes, 15, (1920, 1080)) 
    bboxes = np.array(bboxes)

    # create a dataframe from a text file - SECURE: Use open() to prevent SSRF
    try:
        with open(input_bbox_path, 'r') as f:
            df = pd.read_csv(
                f, 
                names=['fid', 'd1', 'x1', 'y1', 'w', 'h', 'confidence', 'd2', 'd3', 'd4']
            )
    except FileNotFoundError:
        raise ValueError(f"Bounding box file not found: {input_bbox_path}")
    except Exception as e:
        raise ValueError(f"Error reading bounding box file {input_bbox_path}: {e}")
    
    df['x2'] = df['x1'] + df['w']
    df['y2'] = df['y1'] + df['h']
    center_x = (df['x1'] + df['x2'])/2.0
    center_y = (df['y1'] + df['y2'])/2.0
    width = df['x2'] - df['x1']
    height = df['y2'] - df['y1']
    aspect_ratio = [w/h for w, h in zip(width, height)]
    df['cx'] = center_x 
    df['cy'] = center_y 
    df['width'] = width
    df['height'] = height 
    df['aspect_ratio'] = aspect_ratio
    return df


def save_bboxes(bbox_gt, output_path):
    df_kitti = pd.DataFrame()

    frame = []
    x1, y1, w, h = [], [], [], []
    for i, bbox in enumerate(bbox_gt):
        frame.append(i)
        x1.append(bbox[0])
        y1.append(bbox[1])
        w.append(bbox[2] - bbox[0])
        h.append(bbox[3] - bbox[1])

    n = len(frame)
    df_kitti['frame'] = frame 
    df_kitti['dummy1'] = [-1] * n 
    df_kitti['x1'] = x1
    df_kitti['y1'] = y1
    df_kitti['w'] = w
    df_kitti['h'] = h
    df_kitti['confidence'] = [1.0] * n 
    df_kitti['dummy2'] = [-1] * n 
    df_kitti['dummy3'] = [-1] * n 
    df_kitti['dummy4'] = [-1] * n 

    #print(df_kitti.shape)
    #print(df_kitti.head())
    df_kitti.to_csv(output_path, index=False, header=False, sep=',')

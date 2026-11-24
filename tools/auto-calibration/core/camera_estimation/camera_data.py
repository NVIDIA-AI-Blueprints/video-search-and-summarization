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
import numpy as np
import logging

class CameraData:
    def __init__(self, config:dict, cam_file:str, tracking_file:str, id:int):
        self.id = id
        self.cam_file = cam_file
        self.tracking_file = tracking_file
        self.res_x = config['video_resolution'][0]
        self.res_y = config['video_resolution'][1]
        self.cam_data = self.read_camera_parameters()
        self.tracking_data, self.track_ids = self.read_tracking_metadata()

    def read_camera_parameters(self):
        # read camera parameters
        with open(self.cam_file, 'r') as f:
            cam_data = yaml.safe_load(f)
        if 'projectionMatrix_3x4_w2p' in cam_data:
            self.proj_mat = np.array(cam_data['projectionMatrix_3x4_w2p']).reshape(3,4)
        else:
            self.proj_mat = np.array(cam_data['projectionMatrix_3x4']).reshape(3,4)
        self.f = cam_data['cameraInfo']['focalLength'] # unit: pixel
        self.cx = self.res_x // 2
        self.cy = self.res_y // 2
        self.int_mat = np.array([[self.f, 0, self.cx],
                                 [0, self.f, self.cy],
                                 [0, 0, 1]])
        return cam_data
    
    def read_tracking_metadata(self):
        # read tracking metadata text file
        # frame, track_id, x, y, w, h, conf, foot_world_x, foot_world_y, blank, tracker_state, visibility,?,foot_image_x, foot_image_y, convex_hull 
        # 1,1,893,711,250,677,1.000,-10.320,0.157,-1,0,2,0.320,1013,1309,-123|-291|...
        # 2,1,893,713,251,679,0.946,-10.326,0.156,-1,0,2,0.318,1013,1311,-123|-291|...
        # 3,1,893,714,251,680,0.800,-10.331,0.157,-1,0,2,0.316,1013,1312,-123|-291|...
        # 4,1,893,714,251,680,0.750,-10.327,0.157,-1,0,2,0.317,1013,1311,-123|-291|...
        tracking_data = {}
        track_ids = set({})
        with open(self.tracking_file, 'r') as f:
            for line in f:
                data = line.split(',')
                frame = int(data[0])
                track_id = int(data[1])
                x = int(data[2])
                y = int(data[3])
                w = int(data[4])
                h = int(data[5])
                conf = float(data[6])
                foot_world_x = float(data[7])
                foot_world_y = float(data[8])

                # Two tracking file formats exist:
                #   DS9 (no visibility field): use trajectory dumping method
                #                              ..., tracker_state, ?,         foot_image_x, foot_image_y, convex_hull
                #                              idx  10              11(float)  12            13
                #   DS8 (with visibility): use terminated dumping method    
                #                              ..., tracker_state, visibility, ?,            foot_image_x, foot_image_y, convex_hull
                #                              idx  10              11(int)    12(float)     13            14
                # Auto-detect by checking if data[12] is an integer.
                try:
                    foot_image_x = int(data[12])
                    foot_image_y = int(data[13])
                except ValueError:
                    foot_image_x = int(data[13])
                    foot_image_y = int(data[14])

                track_ids.add(track_id)
                if frame not in tracking_data.keys():
                    tracking_data[frame] = {}
                tracking_data[frame][track_id] = {
                    'bbox':[x, y, w, h],
                    'foot_world':[foot_world_x, foot_world_y], 
                    'foot_image':[foot_image_x, foot_image_y]}
        # convert tracking_data to list
        fids = tracking_data.keys()
        max_fid = max(fids)
        tracking_data_list = []
        for fid in range(1, max_fid+2):
            if fid in tracking_data.keys():
                tracking_data_list.append(tracking_data[fid])
            else:
                tracking_data_list.append({})
        logging.debug(f'Track ids: {track_ids}')

        # smooth tracklets
        tracking_data_list = self.smooth_tracklets(track_ids, tracking_data_list)
        return tracking_data_list, track_ids
    
    def smooth_tracklets(self, track_ids, tracking_data_list):
        """
        Smooths the 'foot_image' positions for each tracklet using a moving average.
        The smoothed positions are stored in 'foot_image_smooth' for each tracklet in each frame.
        This version properly handles boundaries and edge cases.

        Args:
            track_ids (set): Set of tracklet IDs.
            tracking_data_list (list): List of dicts, each dict is {track_id: {...}} for a frame.

        Returns:
            tracking_data_list (list): The same list, but with 'foot_image_smooth' added for each tracklet.
        """
        # Build a dict: tid -> list of (frame_idx, [foot_image_x, foot_image_y])
        tid_to_points = {tid: [] for tid in track_ids}
        for fid, frame_data in enumerate(tracking_data_list):
            for tid in frame_data:
                if 'foot_image' in frame_data[tid]:
                    tid_to_points[tid].append((fid, frame_data[tid]['foot_image']))

        # For each tracklet, smooth the points and store back
        window = 10
        for tid in track_ids:
            points = tid_to_points[tid]
            if not points:
                continue
            fids, coords = zip(*points)
            coords = np.array(coords)
            
            if len(coords) < window:
                # Not enough points to smooth, just copy original
                smoothed = coords
            else:
                # Use proper moving average with boundary handling
                smoothed = np.zeros_like(coords)
                half_window = window // 2
                
                for i in range(len(coords)):
                    # Calculate start and end indices for the window
                    start_idx = max(0, i - half_window)
                    end_idx = min(len(coords), i + half_window + 1)
                    
                    # Get the actual window size for this point
                    actual_window_size = end_idx - start_idx
                    
                    # Calculate moving average for this window
                    window_coords = coords[start_idx:end_idx]
                    smoothed[i, 0] = np.mean(window_coords[:, 0])
                    smoothed[i, 1] = np.mean(window_coords[:, 1])
            
            # Write back to tracking_data_list
            for i, fid in enumerate(fids):
                if 'foot_image_smooth' not in tracking_data_list[fid][tid]:
                    tracking_data_list[fid][tid]['foot_image_smooth'] = [float(smoothed[i][0]), float(smoothed[i][1])]
        
        return tracking_data_list
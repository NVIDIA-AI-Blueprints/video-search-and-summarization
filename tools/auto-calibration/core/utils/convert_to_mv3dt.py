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

import os
import sys
import cv2
import json
import yaml
import shutil
import argparse
import numpy as np
from os.path import join
from glob import glob

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(current_dir)
sys.path.append(project_root)

from core.camera_estimation.overlay_visualization import get_transform_from_cam0_to_layout

np.set_printoptions(precision=3, suppress=True)

def get_cam_projection_decomposition(P, debug=False):
    K, R, t, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
    Rt = np.hstack([R, -R @ (t[:3, :1] / t[-1])])
    if K[-1, -1] < 0:
        K /= -K[-1, -1]
        K[:, -1] = -K[:, -1]
        Rt[-1, :] = -Rt[-1, :]
        R = Rt[:, :3]
        t = -R.T @ Rt[:, -1:]
    else: K = K / K[-1, -1]
    if debug: print('P\n', P / P[-1, -1])
    if debug: print("P recomputed\n", (K @ Rt) / (K @ Rt)[-1, -1])
    return K, Rt, R, t

def create_caminfo_from_amc_ba_refined(in_dir, out_dir, Rt_world2cam0):
    est_dir = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba', 'refined')
    if not os.path.isdir(est_dir):
        raise FileNotFoundError(f"est_camera_params_in_world not found: {est_dir}")

    caminfo_dir = join(out_dir, 'camInfo')
    os.makedirs(caminfo_dir, exist_ok=True)

    files = [f for f in sorted(os.listdir(est_dir)) if f.endswith(('.yml', '.yaml'))]
    if not files:
        raise FileNotFoundError(f"No YAML camera files found in {est_dir}")

    for fname in files:
        fpath = join(est_dir, fname)
        with open(fpath, 'r') as fh:
            data = yaml.safe_load(fh)

        if 'projectionMatrix_3x4' in data:
            P_cam02cami = np.array(data['projectionMatrix_3x4'], dtype=float)
        else:
            Ki = np.array(data.get('K'), dtype=float)
            R0_cam02cami = np.array(data.get('R'), dtype=float)
            t0_cam02cami = np.array(data.get('t'), dtype=float).reshape(3, 1)
            if Ki.shape != (3, 3) or R0_cam02cami.shape != (3, 3) or t0_cam02cami.shape != (3, 1):
                raise ValueError(f"{fname}: missing or invalid K/R/t and no projectionMatrix_3x4 present")
            Rt_cam02cami = np.hstack([R0_cam02cami, t0_cam02cami])
            P_cam02cami = Ki @ Rt_cam02cami

        if P_cam02cami.shape != (3, 4):
            raise ValueError(f"{fname}: projection matrix must be 3x4")
        
        P_world2cami = P_cam02cami @ np.vstack([Rt_world2cam0, [[0, 0, 0, 1]]]) # Convert {world} to {cam0} 
        P_world2cami = P_world2cami / P_world2cami[-1, -1]

        base = os.path.splitext(fname)[0]
        out_path = join(caminfo_dir, f"{base}.yml")
        with open(out_path, 'w') as fh:
            yaml.safe_dump({'projectionMatrix_3x4_w2p': P_world2cami.reshape(-1).tolist()}, fh)
            fh.write('\n')
            fh.write('modelInfo:\n')
            fh.write('  - classID: 1 # person\n')
            fh.write('    height: 1.7\n')
            fh.write('    radius: 0.3\n')

def create_caminfo_from_amc_world(cam_info_src, out_dir):
    caminfo_dir = join(out_dir, 'camInfo')
    os.makedirs(caminfo_dir, exist_ok=True)

    files = [f for f in sorted(os.listdir(cam_info_src)) if f.endswith(('.yml', '.yaml'))]
    if not files:
        raise FileNotFoundError(f"No YAML camera files found in {cam_info_src}")

    for fname in files:
        fpath = join(cam_info_src, fname)
        with open(fpath, 'r') as fh:
            data = yaml.safe_load(fh)

        if 'projectionMatrix_3x4' in data:
            P = np.array(data['projectionMatrix_3x4'], dtype=float)
        else:
            K = np.array(data.get('K'), dtype=float)
            R = np.array(data.get('R'), dtype=float)
            t = np.array(data.get('t'), dtype=float).reshape(3, 1)
            if K.shape != (3, 3) or R.shape != (3, 3) or t.shape != (3, 1):
                raise ValueError(f"{fname}: missing or invalid K/R/t and no projectionMatrix_3x4 present")
            Rt = np.hstack([R, t])
            P = K @ Rt

        if P.shape != (3, 4):
            raise ValueError(f"{fname}: projection matrix must be 3x4")

        base = os.path.splitext(fname)[0]
        out_path = join(caminfo_dir, f"{base}.yml")
        with open(out_path, 'w') as fh:
            yaml.safe_dump({'projectionMatrix_3x4_w2p': P.reshape(-1).tolist()}, fh)
            fh.write('\n')
            fh.write('modelInfo:\n')
            fh.write('  - classID: 1 # person\n')
            fh.write('    height: 1.7\n')
            fh.write('    radius: 0.3\n')

def create_transforms(in_dir, cam_info_src, out_dir, ground_truth=False, debug=False):
    if ground_truth:
        if not os.path.isfile(join(cam_info_src, 'camInfo_00.yaml')):
            print(f"[error] {join(cam_info_src, 'camInfo_00.yaml')} not found", file=sys.stderr)
            sys.exit(1)
        cam_data = yaml.safe_load(open(join(cam_info_src, 'camInfo_00.yaml'), 'r'))
        P_cam0_w2p = np.array(cam_data['projectionMatrix_3x4'], dtype=float).reshape(3, 4)
        _, Rt_world2cam0, _, _ = get_cam_projection_decomposition(P_cam0_w2p, debug) # P_cam0_w2p = K0 @ Rt_world2cam0
    
    
        json_path = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba_scaled_world', 'transform_cam0_to_map.json')
        if debug: print('P_cam0_w2p:\n', P_cam0_w2p)
        H_floor2cam0 = np.delete(P_cam0_w2p, 2, axis=1) # since P_cam0_w2p @ [x y z 1] w/ z=0
        if debug: print("H_floor2cam0:\n", H_floor2cam0)
        T_floor2map = np.eye(3)
        if os.path.exists(json_path):
            with open(json_path, 'r') as f:
                dat = json.load(f)
            if 'transform_2d' in dat:
                H_cam02map = np.array(dat['transform_2d']['H_cam0_layout'], dtype=float).reshape(3, 3)
                if debug: print("H_cam02map:\n", H_cam02map)
                T_floor2map = H_cam02map @ H_floor2cam0
                T_floor2map = T_floor2map / T_floor2map[-1, -1]
                if debug: print("T_floor2map:\n", T_floor2map)
            else:
                raise ValueError(f"Invalid transform_cam0_to_map.json: {json_path}. No 'transform_2d' key found.")
        else: 
            raise FileNotFoundError(f"transform_cam0_to_map.json not found: {json_path}")
    else:
        # when ground truth is not available, 
        # We set the world coordinate frame(floor) at the top-left corner of the layout (BEV) map
        # So, the transform from floor to map is simply the scale factor(ppm), but y-axis is flipped
        # T_floor2map = [scale 0 0; 0 -scale 0; 0 0 1]
        # However, T_floor2map is not simplay the scale factor sometimes. 
        # We force T_floor2map = [scale 0 0; 0 -scale 0; 0 0 1] as a workaround for now.
        # We need to investigate why T_floor2map is not simply the scale factor.
        # We use the transform_world_to_map.json to get the scale factor
        # World coorindate frame: 
        # x: right, y: up, z: up  
        # Layout(BEV) coordinate frame:
        # x: right, y: down, z: down 
        # Z axis of the world coordinate frame is aligned with the Z axis of MV3DT 
        json_path = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba_scaled_world', 'transform_world_to_map.json')
        if not os.path.isfile(json_path):
            print(f"[error] {json_path} not found", file=sys.stderr)
            sys.exit(1)
        with open(json_path, 'r') as f:
            transform_world2map = json.load(f)
            scale = transform_world2map['scale']
            T_floor2map = np.eye(3)
            T_floor2map[0, 0] = scale
            T_floor2map[1, 1] = -scale

    out_path = join(out_dir, 'transforms.yml')
    with open(out_path, 'w') as f:
        f.write('# 2D transform from ground/world coordinates to pixel coordinates (row-major)\n')
        f.write('T_gt2px:\n')
        for val in T_floor2map.reshape(-1).tolist():
            f.write(f'  - {val:.6f}\n')
        f.write('\n')
        f.write('T_ov2px:\n')
        for val in T_floor2map.reshape(-1).tolist():
            f.write(f'  - {val:.6f}\n')
    
    return T_floor2map

def verify_transforms(in_dir, cam_info_src, out_dir, T_floor2map, debug=False):
    traj_dump_path = join(in_dir, 'single_view_results', 'cam_00', 'trajDump_Stream_0_3d.txt')
    proj_mat_sv_cam0_w2p_path = join(in_dir, 'single_view_results', 'cam_00', 'camInfo_hyper_00_opencv.yaml')
    proj_mat_mv_cam0_w2p_path = join(cam_info_src, 'camInfo_00.yaml')
    proj_mat_sv_cam0_w2p = yaml.safe_load(open(proj_mat_sv_cam0_w2p_path, 'r'))['projectionMatrix_3x4_w2p']
    proj_mat_mv_cam0_w2p = yaml.safe_load(open(proj_mat_mv_cam0_w2p_path, 'r'))['projectionMatrix_3x4_w2p']
    P0_sv = np.array(proj_mat_sv_cam0_w2p, dtype=float).reshape(3, 4)
    P0_mv = np.array(proj_mat_mv_cam0_w2p, dtype=float).reshape(3, 4)
    _, Rt_world2cam0_sv, _, _ = get_cam_projection_decomposition(P0_sv, debug) # proj_mat_sv_cam0_w2p = K0 @ Rt_world2cam0
    _, Rt_world2cam0_mv, _, _ = get_cam_projection_decomposition(P0_mv, debug) # proj_mat_mv_cam0_w2p = K0 @ Rt_world2cam0

    # Compute transform that converts from floor in sv to floor in mv
    T_floorsv2cam0 = np.delete(P0_sv, 2, axis=1)
    T_floormv2cam0 = np.delete(P0_mv, 2, axis=1)
    T_floorsv2floormv = np.linalg.inv(T_floormv2cam0) @ T_floorsv2cam0
    T_floorsv2floormv = T_floorsv2floormv / T_floorsv2floormv[-1, -1]
    if debug: print("T_floorsv2floormv:\n", T_floorsv2floormv)

    map = cv2.imread(join(out_dir, 'map.png'))
    with open(traj_dump_path, 'r') as f:
        traj_dump = f.readlines()
    current_target, prev_pos = None, None
    for line in traj_dump:
        fields = line.split(',')
        feet_x = float(fields[7])
        feet_y = float(fields[8])
        pos_sv = np.array([feet_x, feet_y, 1])
        pos_mv = T_floorsv2floormv @ pos_sv
        pos_px = T_floor2map @ pos_mv
        x, y = pos_px[:2] / pos_px[2]
        cv2.circle(map, (int(x), int(y)), 3, (0, 0, 255), -1)
        if current_target is not None and current_target == int(fields[1]):
            cv2.line(map, (int(prev_pos[0]), int(prev_pos[1])), (int(x), int(y)), (0, 0, 255), 3)
        current_target, prev_pos = int(fields[1]), np.array([x, y])
    out_image_path = join(out_dir, 'map_with_traj.png')
    cv2.imwrite(out_image_path, map)
    print(f"[DEBUG] Wrote map_with_traj.png to {out_dir}")


def generate_mv3dt_files(camera_pairs, s_c2l, R_c2l, t_c2l, proj_mats_c0, ppm, output_dir):
    """ Generate data files for MV3DT

    Args:
        camera_pairs (list): list of camera pairs
        s_c2l (float): scale of the transformation from camera 0 to layout
        R_c2l (np.ndarray): rotation matrix of the transformation from camera 0 to layout
        t_c2l (np.ndarray): translation vector of the transformation from camera 0 to layout
        proj_mats_c0 (dict): dictionary of projection matrices relative to camera 0
        ppm (float): pixels per meter
        output_dir (str): output directory

    Returns:
        None
    """

     # temporaray WAR
    # P_c2i = K[R_est|t_est]
    # T_c2w = [R|t]
    # P_w2i = P_c2i * T_w2c 
    
    cam_ids = []
    for cid0, cid1 in camera_pairs:
        cam_ids.append(cid0)
    cam_ids.append(camera_pairs[-1][1])

    #ppm = 30.18 # NV-warehouse
    #ppm = 68.965 # KION_112125. based on the floor map
    ##ppm = 55.0 # KION_112125. tuning
    #ppm = 24.22 # SDG v8_2
    # Axis flip from world -> layout
    F = np.diag([1, -1, -1])   # 3x3
    R_c2l_T = R_c2l.T
    P_w2i_list = []
    for cid in cam_ids:
        P_c2i= proj_mats_c0[cid]
        A = (ppm / s_c2l * R_c2l_T @ F)
        b = -(1.0 / s_c2l) * (R_c2l_T @ t_c2l.reshape(3,1))

        # Build 4x4 transform W→C0
        T_w2ci = np.eye(4)
        T_w2ci[:3, :3] = A
        T_w2ci[:3, 3:] = b

        P_w2i = P_c2i @ T_w2ci

        #P_w2i_list.append(P_w2i)
        print("================================================")
        print(f'Camera {cid} projection matrix:')
        print("================================================")
        print(f'P_w2i: {P_w2i}')
        param = cv2.decomposeProjectionMatrix(P_w2i)
        K = param[0]
        R = param[1]
        C = (param[2][:3]/param[2][3]).flatten()
        t = -R @ C
        print("")
        print(f'K: {K}')
        print(f'R: {R}')
        print(f't: {t}')
        print(f'C: {C}')

        # normalize the projection matrix
        K = K / K[2, 2]
        P_w2i_norm = K @ np.concatenate((R, t.reshape(3,1)), axis=1)
        print("================================================")
        print(f'Normalized P_w2i: {P_w2i_norm}')
        print("================================================")
        param = cv2.decomposeProjectionMatrix(P_w2i_norm)
        K = param[0]
        R = param[1]
        C = (param[2][:3]/param[2][3]).flatten()
        t = -R @ C
        print("")
        print(f'Normalized K: {K}')
        print(f'Normalized R: {R}')
        print(f'Normalized t: {t}')
        print(f'Normalized C: {C}')
        print("================================================")

        P_w2i_list.append(P_w2i_norm)
 
    # save to camInfo_xx.yaml file
    os.makedirs(f'{output_dir}/camInfo', exist_ok=True)
    for i, P_w2i in enumerate(P_w2i_list):
        cam_info = {}
        cam_info['projectionMatrix_3x4_w2p'] = P_w2i.flatten().tolist()

        with open(f'{output_dir}/camInfo/camInfo_{cam_ids[i]:02d}.yml', 'w') as f:
            yaml.dump(cam_info, f)
            f.write('\n')
            f.write('modelInfo:\n')
            f.write('  - classID: 1 # person\n')
            f.write('    height: 1.7\n')
            f.write('    radius: 0.3\n')
        print(f'Saved camInfo_{cam_ids[i]:02d}.yml to {output_dir}/camInfo/camInfo_{cam_ids[i]:02d}.yml')

    # save homography matrix from world to layout
    H_w2l = np.eye(3)
    H_w2l[0][0] = ppm
    H_w2l[1][1] = -ppm

    transforms = {}
    transforms['T_gt2px'] = H_w2l.flatten().tolist()
    transforms['T_ov2px'] = H_w2l.flatten().tolist()
    with open(f'{output_dir}/transforms.yml', 'w') as f:
        yaml.dump(transforms, f)
        print(f'Saved transforms.yml to {output_dir}/transforms.yml')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='AMC -> MV3DT file structure generator',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--input_dir', '-i', required=True, help=(
        'Path to input directory expected to contain:\n'
        'INPUT_DIR\n'
        '├── multi_view_results\n'
        '│    ├── BA_output\n'
        '│    │    ├── results_ba\n'
        '│    │    │     └── refined\n'
        '│    │    │         ├── camInfo_00.yaml\n'
        '│    │    │         └── camInfo_*.yaml # other camera files\n'
        '│    │    └── results_ba_scaled_world\n'
        '│    │          └── refined\n'
        '│    │              ├── camInfo_00.yaml\n'
        '│    │              └── camInfo_*.yaml # other camera files\n'
        '│    ├── evaluation\n'
        '│    │    ├── est_camera_params_in_world\n'
        '│    │    │    ├── camInfo_00.yaml\n'
        '│    │    │    └── camInfo_*.yaml # other camera files\n'
        '│    │    └── * # other files and/or directories\n'
        '│    └── * # other files and/or directories\n'
        '└── single_view_results\n'
        '    ├── cam_00\n'
        '    │    ├── camInfo_hyper_00_opencv.yaml\n'
        '    │    ├── camInfo_hyper_00.yaml\n'
        '    │    └── * # other files and/or directories\n'
        '    └── cam_* # other camera directories\n'
    ))
    parser.add_argument('--output_dir', '-o', required=True, help=(
        'Path to output directory which will contain:\n'
        'OUTPUT_DIR/\n'
        '├── camInfo\n'
        '│   ├── camInfo_00.yml\n'
        '│   └── camInfo_*.yml # other camera files\n'
        '├── map.png\n'
        '├── transforms.yml\n'
        '└── * # other files and/or directories if OUTPUT_DIR was not empty\n'
    ))
    parser.add_argument('--ground_truth', '-g', required=False, action='store_true', help='Ground truth available')
    parser.add_argument('--debug', '-d', required=False, action='store_true', help='Debug mode')
    args = parser.parse_args()
    
    if args.ground_truth:
        print("Ground truth functionality not yet implemented - setting to False.")
        args.ground_truth = False

    # Handle relative paths, '.' and '..' in the paths
    in_dir = os.path.abspath(args.input_dir)
    out_dir = os.path.abspath(args.output_dir)

    # Create the output directory if it doesn't exist
    os.makedirs(out_dir, exist_ok=True)

    # Copy the layout.png to the output directory with the expected name by MV3DT
    layout_src = join(in_dir, 'alignment_data', 'layout.png')
    if not os.path.isfile(layout_src):
        print(f"[error] {layout_src} not found", file=sys.stderr)
        sys.exit(1)
    shutil.copyfile(layout_src, join(out_dir, 'map.png'))

    # Load the camera 0 transform from the single view results
    if args.ground_truth:
        cam_info_src = join(in_dir, 'multi_view_results', 'evaluation', 'est_camera_params_in_world')
    else:
        cam_info_src = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba_scaled_world')

    print(f"[info] Creating camera information from {cam_info_src}")

    # Create the camera information using the AMC files and the camera 0 transform
    create_caminfo_from_amc_world(cam_info_src, out_dir)

    # Create the transform.yml (2D) using the AMC files and the camera 0 transform
    T_floor2map = create_transforms(in_dir, cam_info_src, out_dir, args.ground_truth, args.debug)

    if args.debug: verify_transforms(in_dir, cam_info_src, out_dir, T_floor2map)

    # If successfully created the files, print the [done] message
    print(f"[done] Wrote camInfo/, map.png, transforms.yml to {out_dir}")

    #experimental_method(in_dir, out_dir)


def experimental_method(in_dir, out_dir):
    """ Experimental method to generate MV3DT files using estimated camera parameters 

    Args:
        in_dir (str): input directory
        out_dir (str): output directory

    Returns:
        None
    """
    # method 2: use estimated camera parameters
    print("================================================")
    print(" Method 2: use estimated camera parameters")
    print("================================================")
    cam_info_src = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba', 'refined')
    cam_files = glob(join(cam_info_src, 'camInfo_*.yaml'))
    cam_files.sort()
    proj_mats = {}
    camera_pairs = []
    # read estimated projection matrices
    for cam_file in cam_files:
        cam_id = int(os.path.basename(cam_file).split('.')[0].split('_')[1])  
        proj_mats[cam_id] = yaml.safe_load(open(cam_file, 'r'))['projectionMatrix_3x4']
        if cam_id < len(cam_files) - 1:
            camera_pairs.append((cam_id, cam_id + 1))
    
    # alignment data
    alignment_data_path = join(in_dir, 'alignment_data', 'alignment_data.json')
    if not os.path.isfile(alignment_data_path):
        print(f"[error] {alignment_data_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(alignment_data_path, 'r') as f:
        alignment_data = json.load(f)
    # scale factor
    json_path = join(in_dir, 'multi_view_results', 'BA_output', 'results_ba_scaled_world', 'transform_world_to_map.json')
    if not os.path.isfile(json_path):
        print(f"[error] {json_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(json_path, 'r') as f:
        transform_world2map = json.load(f)
        ppm = transform_world2map['scale']
    
    # get transform from camera 0 to layout (world frame)
    proj_mat0 = proj_mats[0]
    proj_mat1 = proj_mats[1]
    scale, R, t = get_transform_from_cam0_to_layout(alignment_data, 
                                                    proj_mat0, 
                                                    proj_mat1)
    generate_mv3dt_files(camera_pairs, scale, R, t, proj_mats, ppm, f'{out_dir}/experimental')

if __name__ == '__main__':
    main()
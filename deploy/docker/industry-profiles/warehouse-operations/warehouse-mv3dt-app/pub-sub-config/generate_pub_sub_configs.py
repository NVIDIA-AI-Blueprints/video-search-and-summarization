import argparse
import numpy as np
import tqdm
import yaml
import os
import cv2
import glob
import re
from os.path import join
METERS_TO_FEET = 3.28084

def get_camera_fov_mask(cam_calib, num_pix, range_of_interest=None):
    P, Q, K, R, _, pos, end, height = cam_calib
    cx, cy = K[0, 2], K[1, 2]
    cam_h, cam_w = int(cy * 2), int(cx * 2)
    limits_ov = range_of_interest
    # print ("range_of_interests", limits_ov[0, 0, 0], limits_ov[1, 0, 0], limits_ov[0, 0, 1], limits_ov[1, 0, 1])
    x_ov = np.arange(np.round(limits_ov[0]), np.round(limits_ov[2]))
    y_ov = np.arange(np.round(limits_ov[1]), np.round(limits_ov[3]))
    xy_ov = np.array(np.meshgrid(x_ov, y_ov), dtype=float).reshape(2, -1)
    # Create 3D points in z=0, z=h/2, z=h
    xyz_0 = np.vstack([xy_ov, np.zeros(xy_ov.shape[1])]).T.reshape(len(x_ov), len(y_ov), 3)
    xyz_h = np.vstack([xy_ov, height * np.ones(xy_ov.shape[1])]).T.reshape(len(x_ov), len(y_ov), 3)
    # Project to the image plane
    xy0_cam = cv2.perspectiveTransform(xyz_0, P).reshape(-1, 2).T
    xyh_cam = cv2.perspectiveTransform(xyz_h, P).reshape(-1, 2).T
    # Create the mask
    mask1 = (xy0_cam[0] > 0) & (xy0_cam[0] < cam_w)
    mask2 = (xy0_cam[1] > 0) & (xy0_cam[1] < cam_h)
    mask3 = np.linalg.norm(xyh_cam - xy0_cam, ord=np.inf, axis=0) > num_pix # object size: OK
    mask4 = ((xy_ov.T - pos.T) @ (end - pos)).flatten() > 0                   # direction: OK
    # Update the mask
    mask = mask1 & mask2 & mask3 & mask4 
    return mask

def load_and_process_camera_matrices(cam_info_path):
    # Load all .yml and .yaml files
    cam_files = glob.glob(os.path.join(cam_info_path, "*.yml")) + glob.glob(os.path.join(cam_info_path, "*.yaml"))
    
    cam_matrices = {}
    cam_names = {}  # cam_id -> camInfo filename stem (used as map key in pub/sub configs)
    for idx, cam_file in enumerate(sorted(cam_files)):
        cam = idx + 1
        cam_names[cam] = os.path.splitext(os.path.basename(cam_file))[0]

        with open(cam_file, 'r') as file:
            yaml_data = yaml.safe_load(file)
        if isinstance(yaml_data['modelInfo'], list):
            heights = [yaml_data['modelInfo'][i]['height'] for i in range(len(yaml_data['modelInfo']))]
            height = max(heights) * METERS_TO_FEET # meters to feet (OV)
        else:
            height = yaml_data['modelInfo']["height"] * METERS_TO_FEET # meters to feet (OV)
        P = np.array(yaml_data["projectionMatrix_3x4_w2p"]).reshape(3, 4)
        Q = np.linalg.pinv(P)
        K, R, t, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
        K = K / K[2, 2]
        # Translation vector in OV (-R.t @ t)
        pos = t[:2] / t[-1]
        # point at 3 OV units (feet) in front of the camera
        end = pos + R[-1:, :2].T * (3 / np.linalg.norm(R[-1, :2]))
        # pos_px = cv2.perspectiveTransform(pos.reshape(1, 1, 2), T_ov2px).reshape(2)
        # end_px = cv2.perspectiveTransform(end.reshape(1, 1, 2), T_ov2px).reshape(2)
        cam_matrices[cam] = P, Q, K, R, t, pos, end, height
    return cam_matrices, cam_names


def get_overlap_of_2_masks(mask1, mask2):
    overlap = np.logical_and(mask1, mask2)
    overlap_count = np.sum(overlap)
    mask1_ratio = overlap_count / np.sum(mask1)
    mask2_ratio = overlap_count / np.sum(mask2)
    return mask1_ratio, mask2_ratio, overlap_count


def get_overlap_matrix(cam_matrices, minimum_object_size, range_of_interest):
    overlap_matrix = {}
    masks = {}
    
    # Generate masks for all cameras
    for cam in tqdm.tqdm(cam_matrices, desc="Generating masks"):
        mask = get_camera_fov_mask(cam_matrices[cam], num_pix=minimum_object_size, range_of_interest=range_of_interest)
        masks[cam] = mask
    
    # Calculate overlap ratios for all camera pairs
    for cam1 in tqdm.tqdm(cam_matrices, desc="Calculating overlaps"):
        overlap_matrix[cam1] = {}
        mask1 = masks[cam1]
        for cam2 in cam_matrices:
            if cam1 == cam2: continue
            mask2 = masks[cam2]
            mask1_ratio, mask2_ratio, _ = get_overlap_of_2_masks(mask1, mask2)
            overlap_matrix[cam1][cam2] = mask1_ratio
    
    return overlap_matrix


def get_subscription_map(overlap_matrix, criteria):
    criteria_type, value = criteria.split(':')
    subscription_map = {}
    if criteria_type == 'top_N':
        N = int(value)
        for cam in overlap_matrix:
            subscription_map[cam] = []
            neighbors = list(overlap_matrix[cam].keys())
            top_cam_idxs = np.argpartition([overlap_matrix[cam][nei] for nei in neighbors], -N)[-N:].tolist()
            top_cams = [neighbors[i] for i in top_cam_idxs]
            subscription_map[cam] = top_cams
        
                
    elif criteria_type == 'overlap_threshold':
        threshold = float(value)
        for cam in overlap_matrix:
            subscription_map[cam] = []
            for neighbor in overlap_matrix[cam]:
                if overlap_matrix[cam][neighbor] >= threshold:
                    subscription_map[cam].append(neighbor)
    
    return subscription_map

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--cam_info_path',
        type=str,
        default=join(os.getenv("PWD"), "camInfo"),
        help='Directory containing camera calibration info (intrinsic & extrinsic params)'
    )

    parser.add_argument(
        '--mqtt_brokers',
        type=str,
        default='127.0.0.1:1883',
        help='Comma-separated MQTT broker addresses, one per DS instance (e.g. 127.0.0.1:1884,127.0.0.1:1885). '
             'Cameras are distributed evenly across instances in sorted order.'
    )

    parser.add_argument(
        '--minimum_object_size',
        type=int,
        default=150,
        help='Number of pixels (in height) to consider an object visible when rendering FOV'
    )

    parser.add_argument(
        '--neighbor_criteria',
        type=str,
        default='overlap_threshold:%f' % (2 / (1920 * 1080)),
        help='Format: "top_N:{N}" or "overlap_threshold:{thres}". Determines neighbor selection method'
    )

    parser.add_argument(
        '--output_path',
        type=str,
        default='./peer_configs',
        help='Directory to store output pub_sub_info_config.yml'
    )

    parser.add_argument(
        '--range_of_interest',
        type=str,
        default=None,
        help='Range of interest of world plane in format "x1,y1,x2,y2" where (x1,y1) is min corner and (x2,y2) is max corner'
    )

    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()

    # Load all cameras from camInfo directory
    cam_matrices, cam_names = load_and_process_camera_matrices(args.cam_info_path)
    cam_ids = sorted(cam_matrices.keys())
    n = len(cam_ids)
    print(f"Loaded {n} cameras: {[cam_names[c] for c in cam_ids]}")

    # Broker list determines the number of DS instances; cameras are split into
    # sequential blocks of equal size across instances.
    mqtt_brokers = [b.strip() for b in args.mqtt_brokers.split(',')]
    num_instances = len(mqtt_brokers)
    block_size = (n + num_instances - 1) // num_instances  # ceiling division
    cam2instance = {cam: min((cam - 1) // block_size, num_instances - 1) for cam in cam_ids}
    print(f"Distributing {n} cameras across {num_instances} instance(s), ~{block_size} per instance")

    # Parse range of interest
    if args.range_of_interest:
        x1, y1, x2, y2 = map(float, args.range_of_interest.split(','))
        range_of_interest_ov = np.array([x1, y1, x2, y2], dtype=float)
    else:
        range_padding = 100
        cam_poses = [cam_matrices[cam][5] for cam in cam_ids]
        min_x = min([pose[0][0] for pose in cam_poses])
        max_x = max([pose[0][0] for pose in cam_poses])
        min_y = min([pose[1][0] for pose in cam_poses])
        max_y = max([pose[1][0] for pose in cam_poses])
        range_of_interest_ov = np.array([min_x - range_padding, min_y - range_padding, max_x + range_padding, max_y + range_padding], dtype=float)

    overlap_matrix = get_overlap_matrix(cam_matrices, args.minimum_object_size, range_of_interest_ov)
    subscription_map = get_subscription_map(overlap_matrix, args.neighbor_criteria)

    num_neighbors = np.mean([len(subscription_map[cam]) for cam in subscription_map])
    print(f'Average number of neighbors: {num_neighbors}')
    for cam in subscription_map:
        print(f'    {cam_names[cam]}:', [cam_names[nei] for nei in subscription_map[cam]])

    # Generate a single pub_sub_info_config.yml covering all instances.
    # Topics are named /trck/{cam_name}; broker is determined by the instance the camera belongs to.
    config = {"pubBrokerTopicStr": {}, "subPeerBrokerTopicStrs": {}}
    for cam in cam_ids:
        cam_name = cam_names[cam]
        cam_broker = mqtt_brokers[cam2instance[cam]]
        config["pubBrokerTopicStr"][cam_name] = f'{cam_broker};/trck/{cam_name}'
        config["subPeerBrokerTopicStrs"][cam_name] = []
        for nei in subscription_map[cam]:
            nei_name = cam_names[nei]
            nei_broker = mqtt_brokers[cam2instance[nei]]
            config["subPeerBrokerTopicStrs"][cam_name].append(f'{nei_broker};/trck/{nei_name}')

    if not os.path.exists(args.output_path):
        os.makedirs(args.output_path)
    out_path = os.path.join(args.output_path, 'pub_sub_info_config.yml')
    with open(out_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    print(f'Written: {out_path}')

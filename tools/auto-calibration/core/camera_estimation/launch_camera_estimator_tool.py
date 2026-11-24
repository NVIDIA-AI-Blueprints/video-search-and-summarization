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
import argparse
import shutil
import signal
import numpy as np
from sympy import Plane, Point3D, Line3D
import subprocess
import sys
import time
import yaml
import psutil
import matplotlib.pyplot as plt

KEY_NUM_THREAD = "<thread_nb>"
KEY_TIMEOUT = "<timeout>"
KEY_FILE_OPTIMIZERS = "<optimizers.yaml>"
KEY_FILE_CAMERA_OPTIM_RANGE = "<param_range.yaml>"
KEY_FILE_PREPROCESS = "<preprocess.yaml>"
KEY_FILE_GT = "<gt.txt>"
KEY_FOLDER_OUTPUT = "<output_folder>"
KEY_CENTER_VIEW_ORIGIN = "center_cam_view"
PATH_CAMERA_RAW_CAPTURED_DATA = "/tmp/.tmpXXXX_camera_estimator_raw_data.yaml"
PATH_CAMERA_CONFIG_FILE = "/tmp/camera_estimator_config.yaml"
#ROOT_CAMERA_DATA_FOLDER = "/tmp/camera_estimator_data/"
#ROOT_CAMERA_DATA_FOLDER = "/data/DSTuner/apps/camera_matching/" #TODO: temorary change
ROOT_CAMERA_DATA_FOLDER = "/data/code/pipe-tuner/apps/camera_matching/" #TODO: temorary change

NAME_OPTI_PERF_IMG = "optimizer_performances.png"

def make_parser():
    parser = argparse.ArgumentParser(
        prog="camera estimator tool",
        description="Estimate camera positions from a ground truth file")
    parser.add_argument(KEY_FILE_OPTIMIZERS, type=str,
                        help='Yaml file containing the optimizer information')
    parser.add_argument(KEY_FILE_CAMERA_OPTIM_RANGE, type=str,
                        help='Yaml file containing the parameter range information')
    parser.add_argument(KEY_FILE_PREPROCESS, type=str,
                        help='Yaml file containing the preprocessing information')
    parser.add_argument(KEY_FILE_GT, type=str,
                        help='Csv file containing the ground truth information')
    parser.add_argument(KEY_FOLDER_OUTPUT, type=str,
                        help='Output folder containing the camera parameters yaml files')

    parser.add_argument(KEY_NUM_THREAD, type=int,
                        help='Specify the number of threads to use')
    parser.add_argument(KEY_TIMEOUT, type=int,
                        help='Specify the maximum optimization time (in seconds, range: 30-86400)')
    parser.add_argument("--center-view-on-origin", dest=KEY_CENTER_VIEW_ORIGIN, action='store_true', default=False,
                        help='Invert the camera rotation vector if needed to make the camera look down.'
                             ' Center the camera view on 0,0,0 by translating it.')
    return parser


def parse_all():
    parser = make_parser()
    nsp = parser.parse_args()
    return vars(nsp)


def port_is_used(port):
    ports_in_use = set([elm.laddr.port for elm in psutil.net_connections()
                        if elm.status == psutil.CONN_LISTEN])
    return port in ports_in_use


def make_paramater_space(path_param_space):
    with open(path_param_space, "r") as f:
        y_node = yaml.safe_load(f.read())
    human_radius = list(map(float, y_node["human_radius"]))
    human_height = list(map(float, y_node["human_height"]))
    focal_length = list(map(float, y_node["focal_length"]))
    rotation_x = list(map(float, y_node["rotation_x"]))
    rotation_y = list(map(float, y_node["rotation_y"]))
    rotation_z = list(map(float, y_node["rotation_z"]))
    translation_y = list(map(float, y_node["translation_y"]))

    bbox_matching_function = y_node["bbox_matching_function"]

    res_dict = {
        "exec": {
            "human": {
                "radius": [human_radius[0], human_radius[1], "linear", "real"],
                "height": [human_height[0], human_height[1], "linear", "real"]
            },
            "focal_length": [focal_length[0], focal_length[1], "linear", "real"],
            "rotation": {
                "x": [rotation_x[0], rotation_x[1], "linear", "real"],
                "y": 0, # [rotation_y[0], rotation_y[1], "linear", "real"], # experiment
                "z": [rotation_z[0], rotation_z[1], "linear", "real"]
            },
            "translation": {
                "x": 0, 
                "y": [translation_y[0], translation_y[1], "linear", "real"],
                "z": 0, 
            },
        },
        "eval": {
            "bbox_matching_function": bbox_matching_function
        }
    }
    return res_dict


def make_camera_config(path_gt, path_preprocess, path_param_space, port_backend, thread_number, optimizer_dict):
    if not os.path.exists(ROOT_CAMERA_DATA_FOLDER):
        os.makedirs(ROOT_CAMERA_DATA_FOLDER)

    if not os.path.exists(os.path.join(ROOT_CAMERA_DATA_FOLDER, "configs")):
        os.makedirs(os.path.join(ROOT_CAMERA_DATA_FOLDER, "configs"))

    gt_file_path = "configs/gt.txt"
    preprocess_file_path = "configs/preprocess_config.yaml"

    shutil.copy(path_gt, os.path.join(ROOT_CAMERA_DATA_FOLDER, gt_file_path))
    shutil.copy(path_preprocess, os.path.join(ROOT_CAMERA_DATA_FOLDER, preprocess_file_path))

    dict_cam_cfg = {
        "folder_to_send": ROOT_CAMERA_DATA_FOLDER,
        "preprocessing": {
            "ignore": False,
            "arg0": "python/preprocess_bbox_test.py", 
            "supplementary_args": f"{gt_file_path} {preprocess_file_path} bbox.yaml",
            "produced_files": ["bbox.yaml"],
        },
        "processing": {
            #"arg0": "python/main.py",
            "arg0": "python/main_test.py", #TODO: temporary change
            "supplementary_args": "bbox.yaml",
            "parameter_space": make_paramater_space(path_param_space)
        },
        "servers": {
            "server1": {
                "ip": "0.0.0.0",
                "port": port_backend,
                "worker_number": thread_number,
            }
        },
        "bbo": {
            "problem_type_is_min": False,
            "job_batch_size": 20,
            "optimizers": optimizer_dict
        }
    }
    with open(PATH_CAMERA_CONFIG_FILE, "w") as f:
        yml_str = yaml.dump(dict_cam_cfg)
        f.write(yml_str)


def sanitize_input(dict_args):
    thread_nb = dict_args[KEY_NUM_THREAD]
    timeout = dict_args[KEY_TIMEOUT]
    file_optimizers = dict_args[KEY_FILE_OPTIMIZERS]
    file_preprocess = dict_args[KEY_FILE_PREPROCESS]
    path_paramater_space = dict_args[KEY_FILE_CAMERA_OPTIM_RANGE]
    file_gt = dict_args[KEY_FILE_GT]
    folder_output = dict_args[KEY_FOLDER_OUTPUT]

    if thread_nb < 1:
        raise Exception("Invalid thread number; must be >= 1")
    if timeout < 30:
        raise Exception("Invalid timeout ; must be >= 30")
    if timeout > 86400:  # Prevent DoS by Sleep: max 24 hours (86400 seconds)
        raise Exception("Invalid timeout ; must be <= 86400 (24 hours)")
    if not os.path.exists(file_optimizers):
        raise Exception(f"Missing file optimizer: {file_optimizers}")
    if not os.path.exists(file_preprocess):
        raise Exception(f"Missing file preprocess: {file_preprocess}")
    if not os.path.exists(path_paramater_space):
        raise Exception(f"Missing file parameter space: {path_paramater_space}")
    if not os.path.exists(file_gt):
        raise Exception(f"Missing file ground truth: {file_gt}")
    if os.path.exists(folder_output):
        raise Exception(f"Output folder already exist: {folder_output}\n"
                        f"Rename it or delete it.")


def find_available_ports(begin_port, port_num):
    res_ports = []
    curr_port = begin_port
    while len(res_ports) < port_num:
        if not port_is_used(curr_port):
            res_ports.append(curr_port)
        curr_port += 1
    return res_ports


def make_optimizer_dict_from_yaml_path(path):
    res_dict = {}
    with open(path, "r") as f:
        res_dict = yaml.safe_load(f.read())

    # Sanity check
    for key, val in res_dict.items():
        name = val["name"]
        max_iteration = int(val["max_iteration"])
        redundancy = int(val["redundancy"])
        batch_size = int(val["batch_size"])
        if not name:
            raise Exception(f"Error in optimizer file at key {key}: name can't be empty")
        if max_iteration < batch_size:
            raise Exception(f"Error in optimizer file at key {key}: max iteration must be > batch size")
        if max_iteration < 5:
            raise Exception(f"Error in optimizer file at key {key}: max iteration must be > 5")
        if redundancy < 1:
            raise Exception(f"Error in optimizer file at key {key}: redundancy must be > 1")
        if batch_size < 1:
            raise Exception(f"Error in optimizer file at key {key}: batch_size must be > 1")

    return res_dict


def setup(dict_args, port_backend):
    sanitize_input(dict_args)

    thread_nb = dict_args[KEY_NUM_THREAD]

    file_optimizers = os.path.abspath(dict_args[KEY_FILE_OPTIMIZERS])
    file_preprocess = os.path.abspath(dict_args[KEY_FILE_PREPROCESS])
    path_paramater_space = os.path.abspath(dict_args[KEY_FILE_CAMERA_OPTIM_RANGE])
    file_gt = os.path.abspath(dict_args[KEY_FILE_GT])
    folder_output = os.path.abspath(dict_args[KEY_FOLDER_OUTPUT])
    os.makedirs(folder_output)

    optim_dict = make_optimizer_dict_from_yaml_path(file_optimizers)
    make_camera_config(file_gt, file_preprocess, path_paramater_space, port_backend, thread_nb, optim_dict)

    if os.path.exists(PATH_CAMERA_RAW_CAPTURED_DATA):
        os.remove(PATH_CAMERA_RAW_CAPTURED_DATA)


def run_bbo_backend(port):
    print('\nRunning bbo backend...')
    binary = "ds_bbo_server"
    cmd = f"{binary} --port {port} "
    print('Command: ', cmd)
    p = subprocess.Popen(cmd.split(),
    #                     stdout=subprocess.DEVNULL
                         )
    return p


def run_bbo_frontend(port):
    print('\nRunning bbo frontend...')
    binary = "python3 ./src/client/main.py"
    cmd = f"{binary} " \
          f" -c {PATH_CAMERA_CONFIG_FILE} " \
          f" -p {port} " \
          f" --output {PATH_CAMERA_RAW_CAPTURED_DATA}"
    print('Command: ', cmd)
    p = subprocess.Popen(cmd.split(),
                         stdout=subprocess.DEVNULL
                         )
    return p


def run(dict_args, port_frontend, port_backend):
    timeout = dict_args[KEY_TIMEOUT]

    p_back = run_bbo_backend(port_backend)
    time.sleep(1)
    p_front = run_bbo_frontend(port_frontend)

    print("Starting optimization...")
    while timeout > 0:
        print("Remaining time:", timeout, "s")
        sys.stdout.flush()
        to_wait = min(10, timeout)
        timeout -= 10
        time.sleep(to_wait)
    print("Timeout reached... stopping optimizers...")
    p_front.terminate()
    p_back.terminate()
    time.sleep(1)
    p_front.kill()
    p_back.kill()
    print("Optimization done.")



def make_rot_matrix_from_angles(angles):
    yaw, roll, pitch = angles
    x = -(pitch + 90) * np.pi / 180
    y = (roll + 90) * np.pi / 180
    z = -yaw * np.pi / 180
    cos = np.cos
    sin = np.sin
    matrix_rotation_x = np.array([
        [1, 0, 0],
        [0, cos(x), -sin(x)],
        [0, sin(x), cos(x)]
    ])
    matrix_rotation_y = np.array([
        [cos(y), 0, sin(y)],
        [0, 1, 0],
        [-sin(y), 0, cos(y)]
    ])
    matrix_rotation_z = np.array([
        [cos(z), -sin(z), 0],
        [sin(z), cos(z), 0],
        [0, 0, 1]
    ])
    res1 = np.matmul(matrix_rotation_y, matrix_rotation_z)
    res = np.matmul(res1, matrix_rotation_x)
    return res


def make_translation_matrix(coord):
    x, y, z = coord
    res = np.array([-z, x, y])
    return res


def make_rotation_translation_matrix(rotation, translation):
    translation = np.array(translation)
    res = np.array([
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0]
    ])
    matrix_rotation = make_rot_matrix_from_angles(rotation)
    res[:3, :3] = matrix_rotation
    matT = make_translation_matrix(translation)
    side = np.matmul(-matrix_rotation, matT)
    res[0:3, 3] = side
    return res


def make_matrix_from_params(fl, rx, ry, rz, tx, ty, tz):
    mat_focal_length = np.identity(4)
    mat_focal_length[0][0] = fl 
    mat_focal_length[1][1] = fl 
    mat_rotation_translation = make_rotation_translation_matrix(
        (rx, ry, rz),
        (tx, ty, tz)
    )
    res = np.matmul(mat_focal_length, mat_rotation_translation)
    res = res.reshape((4, 4))[:-1].flatten()
    res_list = list(map(float, res.tolist()))
    return res_list

def make_matrix_from_params_opencv(fl, rx, ry, rz, tx, ty, tz):
    mat_focal_length = np.identity(4)
    mat_focal_length[0][0] = fl 
    mat_focal_length[1][1] = fl 
    mat_focal_length[0][2] = 1920/2 # TODO: remove hardcoded value 
    mat_focal_length[1][2] = 1080/2 # TODO: remove hardcoded value 
    mat_rotation_translation = make_rotation_translation_matrix(
        (rx, ry, rz),
        (tx, ty, tz)
    )
    res = np.matmul(mat_focal_length, mat_rotation_translation)
    res = res.reshape((4, 4))[:-1].flatten()
    res_list = list(map(float, res.tolist()))
    return res_list

def compute_angle_diff(rotation_angle, vector_dir):
    RTMA = make_rot_matrix_from_angles(rotation_angle)
    direction_vector = np.matmul(vector_dir, RTMA)
    plane_normal = np.array([0, 0, -1])
    dot_product = np.dot(plane_normal, direction_vector)
    norm_product = np.linalg.norm(direction_vector) * np.linalg.norm(plane_normal)
    res = np.arccos(dot_product / norm_product)
    return res


def get_angle_flipped_direction_if_needed(rotation_angle):
    rotation_angle = np.array(rotation_angle)
    vd1 = np.array([0, 0, -1])
    angle = compute_angle_diff(rotation_angle, vd1)
    if angle < np.pi / 2:
        return rotation_angle
    return rotation_angle * -1


def compute_intersection_floor(origin, rotatioXYZ):
    origin = np.array(origin)
    rotatioXYZ = np.array(rotatioXYZ)
    vd1 = np.array([0, 0, -1]) # camera forward direction vector
    RTMA = make_rot_matrix_from_angles(rotatioXYZ)
    direction = np.matmul(vd1, RTMA)
    vector = Line3D(origin, direction_ratio=direction)
    A = Point3D(0, 0, 0) # origin
    B = Point3D(1, 0, 0) # x axis (Omniverse world)
    C = Point3D(0, 1, 0) # y axis (Omniverse world)
    floor = Plane(A, B, C) # ground floor
    inter = floor.intersection(vector)
    res = inter[0].evalf()
    return np.array([-res[1], origin[2], res[0]]) # Omniverse camera frame. https://docs.omniverse.nvidia.com/isaacsim/latest/reference_conventions.html


def update_camera_param_for_ov(rotation, height):
    res_vector = get_angle_flipped_direction_if_needed(rotation)
    pt_res = compute_intersection_floor([0, 0, height], rotation)
    return res_vector, pt_res


def update_translation_rotation_to_look_at_map_center(translation, rotation):
    rotation = rotation
    height = translation[1] # translation_y. Camera upward direction 
    res = update_camera_param_for_ov(rotation, height)
    new_rot = tuple(map(float, res[0]))
    new_translation = tuple(map(float, res[1]))
    return new_translation, new_rot


def write_output_file(output_path, dict_result_node, center_view_on_origin):
    fl = float(dict_result_node["focal_length"])
    translation = (
        0, 
        float(dict_result_node["translation_y"]),
        0 
    )
    rot = (
        dict_result_node["rotation_x"],
        0, #dict_result_node["rotation_y"], # experiment. set yaw to 0
        dict_result_node["rotation_z"],
    )
    if center_view_on_origin:
        # change translation and rotation to look at the scene center
        translation, rot = update_translation_rotation_to_look_at_map_center(translation, rot)

    rx, ry, rz = rot
    tx, ty, tz = translation

    res_yaml = {
        "cameraInfo": {
            "focalLength": fl,
            "yaw": rx,
            "roll": ry,
            "pitch": rz,
            "Tx": tx,
            "Ty": ty,
            "Tz": tz,
        },
        "H_Cam2MappedImage": {
            "PosOffset_x": -0.19,
            "PosOffset_y": 0.11,
            "Scale": 6400
        },
        "projectionMatrix_3x4": make_matrix_from_params(fl, rx, ry, rz, tx, ty, tz),
        "modelInfo": {
            "height": float(dict_result_node["human_height"]),
            "radius": float(dict_result_node["human_radius"]),
        },
    }
    with open(output_path, "w") as f:
        f.write("# File generated by Camera Model Estimator Tool\n")
        score = dict_result_node["score"]
        f.write(f"# score: {score}\n")
        f.write(yaml.dump(res_yaml, sort_keys=False))

class OptimizerData:
    def __init__(self, score, step,param_list):
        self.score = score
        self.step = step
        self.paramlist = param_list

    def save(self, outpath, keys_content, center_view_on_origin):
        val = dict(zip(keys_content, self.paramlist))
        val["score"] = self.score
        write_output_file(outpath, val, center_view_on_origin)

class OptimizerProgress:
    def __init__(self, name, keys_content):
        self.name = name
        self.last_score = 0
        self.keys_content = keys_content
        self.content = []

    def add(self, score, step, param_list):
        self.last_score = score
        self.content.append(OptimizerData(score, step, param_list))

    def save_best(self, folder_output, center_view_on_origin):
        path_cinfo = os.path.join(folder_output, f"camInfo_{self.name}.yaml")
        self.content[-1].save(path_cinfo, self.keys_content, center_view_on_origin)

    def save_all_steps(self, folder_output, center_view_on_origin):
        for i, elm in enumerate(self.content):
            path_cinfo = os.path.join(folder_output, f"camInfo_{self.name}_step_{i:05}.yaml")
            elm.save(path_cinfo, self.keys_content, center_view_on_origin)

    def get_score_list(self):
        return [elm.score for elm in self.content]

    def get_step_list(self):
        return [elm.step for elm in self.content]


def get_best_score_parameters_dict(path):
    with open(path, "r") as f:
        all_content = f.readlines()
    all_content = [elm.split("\n")[0] for elm in all_content]
    first_line = all_content[0]
    c_keys = first_line.split(",")[3:]
    c_keys = [elm.split("exec:")[1] for elm in c_keys]
    c_keys = [elm.replace(":", "_") for elm in c_keys]
    all_content = all_content[1:]

    dict_optimizers = {}
    for line in all_content:
        list_content = line.split(",")
        optim_name = list_content[0]
        step = int(list_content[1])
        optim_score = float(list_content[2])
        optim_line = list(map(float, list_content[3:]))
        if optim_name not in dict_optimizers:
            dict_optimizers[optim_name] = OptimizerProgress(optim_name, c_keys)

        if optim_score > dict_optimizers[optim_name].last_score:
            dict_optimizers[optim_name].add(optim_score, step, optim_line)

    return dict_optimizers



def plot_optimizer_results(optim_progress_dict: dict, output_path):

    fig = plt.figure(figsize=(20, 10))
    ax = fig.add_subplot(1, 1, 1)
    for dict_val in optim_progress_dict.values():
        iters = dict_val.get_step_list()
        scores =  dict_val.get_score_list()
        best_iter = iters[-1]
        best_score = scores[-1]
        ax.semilogx(iters, scores, label=f"{dict_val.name} => {best_score:0.3f}", marker='o')
        ax.text(best_iter, best_score, ('%0.2f' % best_score), rotation=30)

    optimizer_types = list(set(elm.split("_")[0] for elm in optim_progress_dict.keys()))
    title = 'Budget Evaluation: '
    for optimizer in optimizer_types:
        title += (optimizer + ', ').upper()
    title = title[:-2]

    plt.title(title)
    plt.xlabel("Evaluations")
    plt.ylabel("metric score")
    plt.legend()
    plt.grid()
    plt.savefig(output_path)


def write_output_files(dict_results, folder_output, center_view_on_origin):
    for key, val in dict_results.items():
        path_cinfo = os.path.join(folder_output, f"camInfo_{key}.yaml")
        write_output_file(path_cinfo, val, center_view_on_origin)


def post_process(dict_args):
    folder_output = os.path.abspath(dict_args[KEY_FOLDER_OUTPUT])
    center_view_on_origin = dict_args[KEY_CENTER_VIEW_ORIGIN]
    print("Retrieving best parameters...")
    dict_optimizers = get_best_score_parameters_dict(PATH_CAMERA_RAW_CAPTURED_DATA)
    steps_folder = os.path.join(folder_output, "steps")
    os.makedirs(steps_folder)
    for optim in dict_optimizers.values():
        optim.save_best(folder_output, center_view_on_origin)
        optim.save_all_steps(steps_folder, center_view_on_origin)

    print(f"Done. Results are written under {folder_output}")
    print(f"Plotting performances for each optimizer...")
    output_path_perf = os.path.join(folder_output, NAME_OPTI_PERF_IMG)
    plot_optimizer_results(dict_optimizers, output_path_perf)
    print(f"Done. Results are written under {output_path_perf}")




def main():
    print("\nStarting Camera Model Estimator Tool Dev Version\n")
    dict_args = parse_all()
    ports = find_available_ports(31010, 2)
    p_front = ports[0]
    p_back = ports[1]
    setup(dict_args, p_back)
    sys.stdout.flush()
    run(dict_args, p_front, p_back)
    sys.stdout.flush()
    post_process(dict_args)
    sys.stdout.flush()


if __name__ == '__main__':
    main()

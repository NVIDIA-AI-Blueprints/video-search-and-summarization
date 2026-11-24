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

import functools
import argparse
import sys

import yaml

INPUT_BBOX_KEY = '<input_bbox_txt>'
INPUT_BBOX_CONFIG = '<input_bbox_config>'
OUTPUT_BBOX_KEY = '<output_bbox_yaml>'


class BboxConfig:
    def __init__(self, config):
        self.img_width = config["image"]["width"]
        self.img_height = config["image"]["height"]
        self.region_split_width = config["region_split"]["width"]
        self.region_split_height = config["region_split"]["height"]
        self.sampling_confidence = config["box_sampling"]["confidence"]
        self.sampling_ar_residual = config["box_sampling"]["aspect_ratio_residual"]
        self.sampling_width_residual = config["box_sampling"]["width_residual"]
        self.sampling_height_residual = config["box_sampling"]["height_residual"]

def bbox_list_is_valid(bbox_gt_list):
    if not bbox_gt_list:
        print("The bounding box ground truth list is empty")
        return False
    for elm in bbox_gt_list:
        if len(elm) != 4:
            print("The bounding box ground truth list is empty")
            return False
    return True


def is_bbox_valid(min_coord, max_coord, bbox):
    """ Checks if the bounding box is overlapping with a region of grid
    
    Args:
        min_coord: The minimum coordinate of a region
        max_coord: The maximum coordinate of a region
        bbox: The bounding box
    
    Returns:
        True if the bounding box is overlapping with a region of grid"""
    min_x_under_max_coord = bbox[0] < max_coord[0]
    max_x_above_min_coord = min_coord[0] < bbox[2]
    min_y_under_max_coord = bbox[1] < max_coord[1]
    max_y_above_min_coord = min_coord[1] < bbox[3]
    return min_x_under_max_coord and max_x_above_min_coord \
           and min_y_under_max_coord and max_y_above_min_coord


def separate_bbox_per_region(bboxes, split_dim, img_dim, offset_dim_px):
    cols, lines = split_dim
    width, height = img_dim
    region_width = width // cols
    region_height = height // lines
    res = {}
    offset_x, offset_y = offset_dim_px
    for i in range(cols):
        res[i] = {}
        for j in range(lines):
            min_coord = i * region_width + offset_x, j * region_height + offset_y
            max_coord = (i + 1) * region_width - offset_x, (j + 1) * region_height - offset_y
            bbox_filtered = [elm for elm in bboxes if is_bbox_valid(min_coord, max_coord, elm)]
            res[i][j] = bbox_filtered
    return res

def separate_bbox_per_region_with_center(bboxes, split_dim, img_dim, offset_dim_px):
    """ experimental method for splitting boxes in a region with a center coordinate
    
    Args:
        bboxes: list of bboxes
        split_dim: (cols, rows)
        img_dim: (width, height)
        offset_dim_px: (offset_x, offset_y)
    
    Returns:
        res: dict of dicts of bboxes. res[i][j] where i is row index and j is column index
    """
    cols, rows = split_dim
    width, height = img_dim
    region_width = width // cols
    region_height = height // rows
    res = {}
    offset_x, offset_y = offset_dim_px
    for i in range(cols):
        res[i] = {}
        for j in range(rows):
            res[i][j] = []
    
    for box in bboxes:
        center_x = (box[0] + box[2]) // 2
        center_y = (box[1] + box[3]) // 2
        region_x = center_x // region_width
        region_y = center_y // region_height
        
        # Ensure region indices are within bounds
        region_x = min(region_x, cols - 1)
        region_y = min(region_y, rows - 1)
        
        res[region_x][region_y].append(box)
    return res

def is_bbox_strictly_in_region(bbox, min_coord, max_coord):
    x1, y1, x2, y2 = bbox
    minx, miny = min_coord
    maxx, maxy = max_coord
    return x1 > minx and y1 > miny and x2 < maxx and y2 < maxy


def remove_bbox_touching_borders(bbox_list, pixel_num, image_dim):
    min_coord = pixel_num, pixel_num
    max_coord = image_dim[0] - pixel_num, image_dim[1] - pixel_num
    bbox_filtered = [
        elm for elm in bbox_list
        if is_bbox_strictly_in_region(elm, min_coord, max_coord)
    ]
    return bbox_filtered


def extract_bbox_from_NxN_regions(bbox_list, split_dim, img_dim, offset_dim_px=None, method='center'):
    # add offset to check if bbox is in region, with a 0.5% of offset
    if not offset_dim_px:
        offset_dim_px = (
            int(img_dim[0] / split_dim[0]) * 0.05,
            int(img_dim[1] / split_dim[1]) * 0.05
        )

    if method == 'legacy':
        regions = separate_bbox_per_region(bbox_list, split_dim, img_dim, offset_dim_px)
    elif method == 'center':
        # experimental feature
        regions = separate_bbox_per_region_with_center(bbox_list, split_dim, img_dim, offset_dim_px)
    else:
        raise Exception("Unknown method!!!")

    res_bbox = []
    for line in regions.values():
        for bbox_list_in_region in line.values():
            if not bbox_list_in_region:
                continue
            mid_index = len(bbox_list_in_region) // 2
            res_bbox.append(bbox_list_in_region[mid_index])
    return res_bbox


def compare_size(item1, item2):
    i1x1, i1y1, i1x2, i1y2 = item1
    i2x1, i2y1, i2x2, i2y2 = item2

    i1_w = i1x2 - i1x1
    i1_h = i1y2 - i1y1
    i2_w = i2x2 - i2x1
    i2_h = i2y2 - i2y1

    area_1 = abs(i1_w * i1_h)
    area_2 = abs(i2_w * i2_h)
    return area_1 - area_2


def preprocess_bbox(gt_data_framelist, bb_cfg: BboxConfig):
    subregion_dim = bb_cfg.region_split_width, bb_cfg.region_split_height
    image_dim = bb_cfg.img_width, bb_cfg.img_height
    bbox_gt = [bbox
               for framelist in gt_data_framelist
               for bbox in framelist
               ]
    bbox_gt = remove_bbox_touching_borders(bbox_gt, 5, image_dim)
    if not bbox_gt:
        return []
    bbox_gt.sort(key=functools.cmp_to_key(compare_size))
    bbox_gt = extract_bbox_from_NxN_regions(
        bbox_gt, subregion_dim, image_dim
    )
    if not bbox_gt:
        return []
    return bbox_gt


def flatten_data(data):
    max_index = max(data.keys())
    data_list = []
    for i in range(max_index):
        if i not in data:
            data_list.append([])
        else:
            data_list.append(data[i])
    return data_list


def remove_backslash_n(elm):
    if elm[-1] == "\n":
        return elm[:-1]
    return elm


def get_data_gt(path):
    final_data = {}
    with open(path, "r") as f:
        lines = f.readlines()

    lines = [remove_backslash_n(elm) for elm in lines]

    for i, line in enumerate(lines):
        try:
            elm = list(map(int, line.split(",")[:6]))
        except ValueError:
            print(f"Invalid line {path}:{i} {line}")
            continue
        if len(elm) < 5:
            print(f"Invalid line {path}:{i} {line}")
            continue
        key = elm[0]
        val = elm[1:]
        if key not in final_data:
            final_data[key] = []
        min_x = val[1]
        min_y = val[2]
        max_x = val[1] + val[3]
        max_y = val[2] + val[4]
        final_data[key].append((min_x, min_y, max_x, max_y))
    return final_data


def get_config_data(config_path) -> BboxConfig:
    with open(config_path, "r") as f:
        yamlfile = yaml.safe_load(f)
    bbox_config = BboxConfig(yamlfile)
    return bbox_config


def get_bbox_list_from_path(path, config_path):
    try:
        data = get_data_gt(path)
    except FileNotFoundError:
        print("Invalid ground truth bbox file path")
        return None
    if not data:
        return None
    data = flatten_data(data)
    bdt = get_config_data(config_path)
    data = preprocess_bbox(data, bdt)
    if not bbox_list_is_valid(data):
        return None
    return data


def make_parser():
    parser = argparse.ArgumentParser(description='Preprocess bounding GT boxes to '
                                                 'select some relevant ones')
    # Add the arguments
    parser.add_argument(INPUT_BBOX_KEY,
                        type=str,
                        help='yaml input file')
    parser.add_argument(INPUT_BBOX_CONFIG,
                        type=str,
                        help='yaml input file')
    parser.add_argument(OUTPUT_BBOX_KEY,
                        type=str,
                        help='yaml output file')
    return parser


def get_parse_path(args):
    args = vars(args)
    return args[INPUT_BBOX_KEY], args[INPUT_BBOX_CONFIG], args[OUTPUT_BBOX_KEY]


def main():
    print('\nThis is develop version of preprocess_bbox.py\n') 
    parser = make_parser()
    args = parser.parse_args()
    finput, fconfig, foutput = get_parse_path(args)
    bbox_list: list = get_bbox_list_from_path(finput, fconfig)
    if not bbox_list:
        print("could not find relevant bbox!")
        sys.exit(1)
    y_str = yaml.safe_dump(bbox_list)
    with open(foutput, "w") as f:
        f.write(y_str)


if __name__ == '__main__':
    main()

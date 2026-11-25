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
import matplotlib.pyplot as plt
import os
import glob
import pandas as pd
from datetime import datetime
import argparse
from scipy import stats

# my package
import conf
from line_detection import *

def get_points_outside(pts, img_w, img_h):
    margin_percent = 25 
    margin = margin_percent / 100.0
    x_min = int(img_w * margin)
    x_max = img_w - x_min
    y_min = int(img_h * margin)
    y_max = img_h - y_min

    out = []
    for pt in pts: 
        if pt[0] > x_min and pt[0] < x_max and pt[1] > y_min and pt[1] < y_max:
            continue
        out.append(pt)
    return out

def get_dist_point_line(line, pt, form):
    """ Get the distance between a point and a line
    
    https://docs.opencv.org/3.4/d9/db0/tutorial_hough_lines.html
    https://en.wikipedia.org/wiki/Distance_from_a_point_to_a_line

    Args:
        line(list): [rho, theta] (r=xcosθ+ysinθ)
        pt(list): [x, y]
        form: standard or polar
    
    Returns:
        dist(float): distance between a point and a line
    """
    # standard form line equation: ax + by + c = 0

    if form =='standard':
        a = line[0]
        b = line[1]
        c = line[2]
    else:
        # opencv polar form line equation: xcos(theta) + ysin(theta) - rho = 0
        rho = line[0]
        theta = line[1]
        a = np.cos(theta)
        b = np.sin(theta)
        c = -rho 
    # distance between a point(x0, y0) and a line(a,b,c)
    # |ax0 + by0 + c| / sqrt(a**2 + b**2)
    x0 = pt[0]
    y0 = pt[1]
    dist = np.abs(a * x0 + b * y0 + c) / np.sqrt(a**2 + b**2)
    return dist

def get_average_distance(lines, points_list):
    """ Get the average distance between lines and the points

    Args:
        lines(list): list of lines(rho, angle, votes)
        points_list(list): list of points on the lines
    
    Returns:
        avg_dist(float): average distance between lines and the points
    """
    avg_dist = 0.0
    n_pts = 0
    for i, line in enumerate(lines):
        pts = points_list[i]
        for pt in pts:
            # distance between a line and a point
            avg_dist += get_dist_point_line(line, pt, 'polar')
        n_pts += len(pts)

    if n_pts == 0:
        return 1.0
    avg_dist /= n_pts
    return avg_dist


def score_fn(k1s, edge_img):
    """ Compute the score for a given edge image
    
    Args:
        k1s(list): k1 list
        edge_img: edge image
        
    Returns:
    score(float): score for a given edge image
    """
    lines_acc = [] 
    points_list = []
    params = {}

    # score metrics
    n_lines = [] # number of lines per k1
    n_inlier_points = [] # total number of points per k1
    max_inliers = [] # maximum number of points per kq
    n_inliers_outside = [] # total number of points per k1 excluding middle points
    avg_distances = [] # average distance
    for i, k1 in enumerate(k1s):
        params['k1'] = k1
        img_u = undistort_image(edge_img, params)
        lines, edge_u = detect_lines_on_edge(img_u)
        lines_acc.append({'k1':k1, 'lines': lines})
        points, edge_lines = detect_points_on_lines(edge_u, lines, 3) 
        # average distance between a line and points on the line
        dist = get_average_distance(lines, points)
        
        avg_distances.append(dist)
        points_list.append(points)
        n_lines.append(len(lines))
        n_pts = 0
        max_points = 0
        for j, pts in enumerate(points):
            if max_points < len(pts):
                max_points = len(pts)
            n_pts += len(pts)

        n_inlier_points.append(n_pts)
        max_inliers.append(max_points)

        # exclude the points in the middle of the image 
        n_pts = 0
        for j, pts in enumerate(points):
            pts_outside = get_points_outside(pts, edge_img.shape[1], edge_img.shape[0])
            n_pts += len(pts_outside) 
        n_inliers_outside.append(n_pts)
        
        if i % 10 == 0:
            #print(points)
            edge_points = draw_points(edge_u, points)
            cv.imwrite(f'debug/score_edge_lines_{i}.jpg', edge_lines)
            cv.putText(edge_points, f'#lines: {n_lines[-1]}', (10,10), 1, 1, (0, 0, 2550), 1)
            cv.putText(edge_points, f'#total inliers: {n_inlier_points[-1]}', (10,30), 1, 1, (0, 0, 255), 1)
            cv.putText(edge_points, f'#max inliers: {max_inliers[-1]}', (10,50), 1, 1, (0, 0, 255), 1)
            cv.putText(edge_points, f'#total inliers(outside): {n_inliers_outside[-1]}', (10,70), 1, 1, (0, 0, 255), 1)
            cv.putText(edge_points, f'#avg distance: {avg_distances[-1]}', (10,90), 1, 1, (0, 0, 255), 1)
            cv.imwrite(f'debug/score_edge_points_{i}.jpg', edge_points)
    return n_lines, n_inlier_points, max_inliers, n_inliers_outside, avg_distances

def line_fit(pts):
    """Fit a line to points, handling edge cases properly.
    
    Args:
        pts: array-like of points [[x1,y1], [x2,y2], ...]
    
    Returns:
        [a, b, c] for line equation ax + by + c = 0
        Returns None if fitting is not possible
    """
    pts = np.array(pts)
    
    # Validate: need at least 2 points
    if len(pts) < 2:
        return None
    
    x = pts[:, 0]
    y = pts[:, 1]
    
    # Check for duplicate points (all same point)
    if np.all(x == x[0]) and np.all(y == y[0]):
        return None
    
    # Check for vertical line (all x identical)
    x_range = np.max(x) - np.min(x)
    y_range = np.max(y) - np.min(y)
    
    # Use vertical line representation if x variance is very small
    if x_range < 1e-6 or (y_range / max(x_range, 1e-10)) > 100:
        # Vertical line: x = constant → 1*x + 0*y + (-x_mean) = 0
        x_mean = np.mean(x)
        return [1, 0, -x_mean]
    
    # Check for horizontal line (all y identical)
    if y_range < 1e-6 or (x_range / max(y_range, 1e-10)) > 100:
        # Horizontal line: 0*x + 1*y + (-y_mean) = 0
        y_mean = np.mean(y)
        return [0, 1, -y_mean]
    
    # Normal case: use linear regression
    try:
        slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
        # Convert y = mx + b to ax + by + c = 0
        # mx - y + b = 0 → a=m, b=-1, c=b
        return [slope, -1, intercept]
    except Exception as e:
        # Fallback for any other numerical issues
        print(f"Warning: line_fit failed with error: {e}, using PCA fallback")
        # Use PCA as robust fallback
        pts_centered = pts - np.mean(pts, axis=0)
        _, _, vh = np.linalg.svd(pts_centered)
        normal = vh[1]  # Normal to the line
        point = np.mean(pts, axis=0)
        # Line equation: normal[0]*x + normal[1]*y = normal·point
        c = -np.dot(normal, point)
        return [normal[0], normal[1], c]

def get_distance_score(lines, width, height, params):
    """ Get the average distance between lines and the points

    Args:
        lines(list): list of points
        width: image width
        height: image height 
        points_list(list): list of points on the lines
    
    Returns:
        avg_dist(float): average distance between lines and the points (penalized for invalid lines)
    """
    avg_dist = 0.0
    n_pts = 0
    n_invalid = 0  # Track invalid lines
    n_total = len(lines)
    pts_u_list = []

    debug_img = np.zeros((height, width, 3), dtype=np.uint8)
    if len(lines) == 0:
        print('Any line is not detected')
        return 1000.0, debug_img # TODO: 1000.0 is arbitrary number. Need better one 

    for i, pts in enumerate(lines):
        # Skip empty point sets but count them as invalid
        if len(pts) == 0:
            n_invalid += 1
            continue
            
        pts_u = undistort_points(pts, width, height, params)
        
        # Skip if no valid undistorted points
        if len(pts_u) == 0:
            n_invalid += 1
            continue
            
        line = line_fit(pts_u)
        
        # Skip if line fitting failed - this indicates bad k1
        if line is None:
            n_invalid += 1
            continue
        
        # Calculate distance for valid lines
        for pt_u in pts_u:
            avg_dist += get_dist_point_line(line, pt_u, 'standard')
        n_pts += len(pts)
        pts_u_list.append(pts_u)

        color = tuple((np.random.random(size=3)) * 256)
        # Visualization only: guard against NaN/Inf points so drawing never crashes.
        for pt in pts_u:
            x, y = float(pt[0]), float(pt[1])
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            cv.circle(debug_img, (int(x), int(y)), 1, color, -1)
        # draw line for debugging
        a = line[0] 
        b = line[1]
        c = line[2]
        # Visualization only: guard against NaN/Inf line parameters.
        if np.isfinite(a) and np.isfinite(c):
            x1 = 0
            y1f = a * x1 + c
            x2 = width
            y2f = a * x2 + c
            if np.isfinite(y1f) and np.isfinite(y2f):
                y1 = int(y1f)
                y2 = int(y2f)
                cv.line(debug_img, (x1, y1), (x2, y2), color, 2)

    # Handle case where no valid lines were fitted
    if n_pts == 0:
        return 1000.0, debug_img
    
    # Calculate base average distance
    avg_dist /= n_pts
    
    # Penalize based on percentage of invalid lines
    # If many lines are invalid, this k1 is probably bad
    invalid_ratio = n_invalid / max(n_total, 1)
    
    # Apply penalty: more invalid lines → higher (worse) score
    # Penalty factor: 1.0 (no invalid) to 10.0 (all invalid)
    penalty = 1.0 + (invalid_ratio * 9.0)
    
    penalized_dist = avg_dist * penalty
    
    # Log if significant penalty applied
    if n_invalid > 0:
        valid_lines = n_total - n_invalid
        print(f"[get_distance_score] k1={params.get('k1', 'N/A'):.6e}: {valid_lines}/{n_total} valid lines, penalty={penalty:.2f}x, dist={avg_dist:.2f}→{penalized_dist:.2f}")
    
    return penalized_dist, debug_img

def score_fn_line_candidates(line_candidiates, width, height, k1s):
    """ Calculate scores from line candidates
    
    Args: 
        line_candidiates(list): list of line candidates (list of points)
        width: image width
        height: image height 
        k1s(list): list of k1s
    
    Returns:
        avg_dist: average distance between line candidates and corresponding points
    """
    params = {}
    # score metrics
    avg_distances = [] # average distance
    for i, k1 in enumerate(k1s):
        params['k1'] = k1
        dist, debug_img = get_distance_score(line_candidiates, width, height, params)
        avg_distances.append(dist)

        if i % 10 == 0:
            cv.imwrite(f'debug/corrected_points_{i}.jpg', debug_img)
    return avg_distances

def auto_rectification(img, param_space, verbose=False, debug_dir=None, out_dir=None):
    """ Auto lens rectification

    Estimate distoriton parameters using Hough line detection.
    Only one distortion parameter(k1) is used for now. 
    This function consists of 
    1. detect line in 3D Hough space
    2. get line candidates
    3. reconstruct edge images from the line candidates
    4. calculate score function
    5. parameter search using grid search (we can replace grid search with BBO 
    or other optimization methods)
    6. undistortion

    Args:
        img(ndarray): image
        param_space(dict): parameter space for lens distortion
        verbose(bool): verbose output
        debug_dir(str): debugging folder
        out_dir(str): output folder
    
    Returns:
        params_est(dict): estimated parameters
        img_u(numpy array): undistorted image
        best_k1s(list): best k1s of score metrics 
        score_metrics(list): score metric data vs search space
    """
    # 1. detect lines in 3D Hough space
    k1s, lines_list, points_list, edge_img = detect_lines_with_undistortion(img, param_space)

    # 2. get line candidates that can be used for calculating score function
    line_candidates, _ = get_line_candidates(img, edge_img, k1s, lines_list, points_list)

    # draw output
    if debug_dir is not None:
        edge_lines = draw_lines(edge_img, lines_list)
        edge_points = draw_points(edge_img, points_list)
        cv.imwrite(f'{debug_dir}/edge_lines_filtered.jpg', edge_lines)
        cv.imwrite(f'{debug_dir}/edge_points_filtered.jpg', edge_points)
        cv.imwrite(f'{debug_dir}/edge_filtered.jpg', edge_img)

    # 3. reconstruct an edge image with the points on the lines
    edge_img_c = cv.cvtColor(edge_img, cv.COLOR_GRAY2RGB)
    edge_img, edge_point_img = reconstruct_edge_image(edge_img_c, k1s, points_list)

    # remove boundary edges
    w_d = int((edge_img.shape[1] - img.shape[1]) / 2) + 5 # 5 is selected experimentally
    h_d = int((edge_img.shape[0] - img.shape[0]) / 2) + 5
    edge_img[:, :w_d] = 0
    edge_img[:, -w_d:] = 0
    edge_img[:h_d, :] = 0
    edge_img[-h_d:, :] = 0
    if debug_dir is not None:
        cv.imwrite(f'{debug_dir}/edge_reconstructed.jpg', edge_img)
        cv.imwrite(f'{debug_dir}/edge_point_reconstructed.jpg', edge_point_img)

    # 4. calculate score function
    k1s = param_space['k1']
    n_lines, n_inlier_points, max_inliers, n_inlier_outside, avg_dist = score_fn(k1s, edge_img)
    score_metrics = [n_lines, n_inlier_points, max_inliers, n_inlier_outside, avg_dist]

    # score function using line candidates
    avg_dist = score_fn_line_candidates(line_candidates, edge_img.shape[1], edge_img.shape[0], k1s)
    score_metrics = [n_lines, n_inlier_points, max_inliers, n_inlier_outside, avg_dist]

    # 5. parameter search using grid search 
    plt.figure(figsize=(20,6))

    # maximum number of lines
    plt.subplot(1, 5, 1)
    plt.plot(k1s, n_lines)
    plt.title('k1 vs n_lines')
    best = k1s[np.argmax(n_lines)]
    print('K1 of the most lines: ', best)
    best_k1s = [best]

    # total number of points 
    plt.subplot(1, 5, 2)
    plt.plot(k1s, n_inlier_points)
    plt.title('k1 vs n_inliners')
    best = k1s[np.argmax(n_inlier_points)]
    print('K1 of the maximum number of points: ', best)
    best_k1s.append(best)
 
    # max number of points per line 
    plt.subplot(1, 5, 3)
    plt.plot(k1s, max_inliers)
    plt.title('k1 vs max_inliers')
    best = k1s[np.argmax(max_inliers)]
    print('K1 of the most inlier points: ', best)
    best_k1s.append(best)

    # total number of points excluding the point in the middle of image 
    plt.subplot(1, 5, 4)
    plt.plot(k1s, n_inlier_outside)
    plt.title('k1 vs n_inlier_outside')
    best = k1s[np.argmax(n_inlier_outside)]
    print('K1 of the total inlier points(outside): ', best)
    best_k1s.append(best)

    # average distance between lines and points on the lines
    plt.subplot(1, 5, 5)
    plt.plot(k1s, avg_dist)
    plt.title('k1 vs avg_dist')
    best = k1s[np.argmin(avg_dist)]
    print('K1 of min distance: ', best)
    best_k1s.append(best)

    plt.savefig(f'{out_dir}/score_metrics.jpg')
 
    # best parameters
    params_est = {'k1': best_k1s[4]} # use average distance
    print('params: ', params_est)

    # 6. undistortion 
    img_u = undistort_image(img, params_est, 100)

    plt.figure(figsize=(20,6))
    plt.subplot(1, 2, 1)
    img = cv.cvtColor(img, cv.COLOR_BGR2RGB)
    plt.imshow(img)
    plt.title('Input image')
    plt.subplot(1, 2, 2)
    img_u = cv.cvtColor(img_u, cv.COLOR_BGR2RGB)
    plt.imshow(img_u)
    plt.title('Undistortion image')
    if verbose:
        plt.show()
    plt.savefig(f'{out_dir}/plot_images.jpg')
    return params_est, img_u, best_k1s, score_metrics 


def main_single_camera(args):
    """ Main function to process a single camera """
    debug_dir = conf.DEBUG_DIR
    file_name = args.input.split('/')[-1]
    # datetime object containing current date and time
    now = datetime.now()
    print("now =", now)
    # dd/mm/YY H:M:S
    dt_string = now.strftime("%m-%d-%Y_%H-%M-%S")
    print("date and time =", dt_string)
    result_dir = conf.RESULT_DIR + "/" + dt_string
    conf.RESULT_DIR = result_dir

    os.mkdir(result_dir)
    os.mkdir(result_dir + '/debug')
    conf.DEBUG_FILE_PREFIX = f'{result_dir}/{debug_dir}/' + file_name.split('.')[0]

    src_path = args.input
    img = cv.imread(src_path)

    # resize
    if img.shape[1] > 960 and img.shape[0] > 540:
        img = cv.resize(img, None, fx=0.5, fy=0.5)
    param_est, img_u, best_k1s, score_metrics = auto_rectification(img, True)
    img_u = cv.cvtColor(img_u, cv.COLOR_BGR2RGB)
    cv.imwrite(f'{conf.RESULT_DIR}/{conf.FILE_NAME}', img_u)

    # save results to file
    with open(f'{conf.RESULT_DIR}/{conf.FILE_NAME}.txt', 'w') as f:
        f.write(str(param_est) + '\n')
        f.write(str(best_k1s) + '\n')
        f.write(str(score_metrics) + '\n')
    print(f'{conf.RESULT_DIR}/{conf.FILE_NAME}.txt')


def mean_squared_error(y_true, y_pred):
    return np.mean(np.square(y_true - y_pred))

def mean_absolute_error(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))


def main_multiple_cameras(args):
    """ Main function to process multiple cameras """
    k1_gt = args.k1
    src_dir = args.input
    debug_dir = conf.DEBUG_DIR
    # datetime object containing current date and time
    now = datetime.now()
    print("now =", now)
    # date and time
    dt_string = now.strftime("%m-%d-%Y_%H-%M-%S")
    print("date and time =", dt_string)
    result_dir = conf.RESULT_DIR + "/" + dt_string
    conf.RESULT_DIR = result_dir

    os.mkdir(result_dir)
    os.mkdir(result_dir + '/debug')
    img_files = glob.glob(src_dir + '/*.png')
    img_files.sort()
    param_gt = [] 
    param_est = []
    best_k1_list = []
    score_metrics_list = []
    for i, file_name in enumerate(img_files):
        print('\n' + '=' * 100)
        file_name = file_name.split('/')[-1]
        print(f'Input file: {file_name}')
        name_only = file_name.split('.')[0]
        conf.DEBUG_FILE_PREFIX = f'{result_dir}/{debug_dir}/' + name_only

        src_path = os.path.join(src_dir, file_name) 
        img = cv.imread(src_path)

        # resize
        if img.shape[1] > 960 and img.shape[0] > 540:
            img = cv.resize(img, None, fx=0.5, fy=0.5)

        #auto_rectification_naive(img)
        param, img_u, best_k1s, score_metrics = \
            auto_rectification(img, False, name_only)
        param_gt.append(k1_gt)
        param_est.append(param['k1'])
        best_k1_list.append(best_k1s)
        score_metrics_list.append(score_metrics)

        # save undistort image
        img_u = cv.cvtColor(img_u, cv.COLOR_BGR2RGB)
        file_name = f'{name_only}_undist.png'
        cv.imwrite(f'{conf.RESULT_DIR}/{file_name}', img_u)
    
    mse = mean_squared_error(np.array(param_gt), np.array(param_est))
    print('mean_squared_error: ', mse)
    mae = mean_absolute_error(np.array(param_gt), np.array(param_est))
    print('mean_absolute_error: ', mae)

    # save results
    df = pd.DataFrame()
    df['k1_gt'] = param_gt
    df['k1_est'] = param_est
    best_k1_array = np.array(best_k1_list)
    score_metrics_array = np.array(score_metrics_list)
    for i in range(len(conf.SCORE_METRICS)):
        df['k1_est_' + conf.SCORE_METRICS[i]] = best_k1_array[:, i].tolist()
        df[conf.SCORE_METRICS[i]] = score_metrics_array[:, i].tolist()
    df.to_csv(f'{conf.RESULT_DIR}/results.csv', index=True)
    print(df)

    print('mean_absolute_error: ')
    print(np.mean(np.abs(df.k1_gt - df['k1_est_n_lines'])))
    print(np.mean(np.abs(df.k1_gt - df['k1_est_total_points'])))
    print(np.mean(np.abs(df.k1_gt - df['k1_est_max_points'])))
    print(np.mean(np.abs(df.k1_gt - df['k1_est_total_points_outside'])))
    print(np.mean(np.abs(df.k1_gt - df['k1_est_avg_distance'])))

    df = df.reset_index()
    ax = df.plot(kind='line', x='index', y='k1_gt', color='r', label='k1_gt', 
                 title='Results')
    df.plot(kind='scatter', x='index', y='k1_est', color='b', label='k1_est', 
            ax=ax)
    colors = ['g', 'k', 'y', 'm', 'c']
    for i in range(len(conf.SCORE_METRICS)):
        df.plot(kind='scatter', x='index', y='k1_est_' + conf.SCORE_METRICS[i], 
                color=colors[i], label=conf.SCORE_METRICS[i], ax=ax)
    plt.savefig(f'{conf.RESULT_DIR}/ground_truth_estimation.png')
    plt.show()

if __name__ == '__main__':
    # arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--type', type=str, default='single', 
                        help='single or multiple')
    parser.add_argument('-i', '--input', type=str, default='./data', 
                        help='input image(single) or input folder(multiple)')
    parser.add_argument('-k', '--k1', type=float, default=-0.0, 
                        help='distortion parameter k1')
    args = parser.parse_args()

    if args.type == 'single':
        # run a single image
        main_single_camera(args)
    else: 
        # run multiple images
        main_multiple_cameras(args)

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
import cv2 as cv

# my package
import conf
from lens_distortion import *


def draw_points(img, points_list):
    """ draw points of lines 
    
    Args:
        img: image
        points_list: list of points
    
    Returns:
        out_img: image with points
    """
    out_img = img.copy()
    if len(out_img.shape) == 2:
        out_img = cv.cvtColor(out_img, cv.COLOR_GRAY2RGB)
    for points in points_list:
        color = tuple((np.random.random(size=3)) * 256)
        for x, y in points:
            cv.circle(out_img, (x, y), 1, color, -1)
    
    return out_img


def get_number_of_points_on_line(edge_img, line, thickness):
    """ get number of points on line
    
    Args:
        edge_img(ndarray): edge image
        line(list): line parameters
        thickness(int): line thickness
    
    Returns:
        count(int): number of points on line
    """
    width = edge_img.shape[1]
    height = edge_img.shape[0]
    line_img = np.zeros((height, width))
    d = line[0]
    theta = line[1]
    a = np.cos(theta)
    b = np.sin(theta)
    x0 = a * d 
    y0 = b * d 
    x1 = int(x0 + 1000 * (-b))
    y1 = int(y0 + 1000 * (a))
    x2 = int(x0 - 1000 * (-b))
    y2 = int(y0 - 1000 * (a))

    cv.line(line_img, (x1, y1), (x2, y2), (255, 255, 255), thickness)
    res = (edge_img>0) & (line_img>0)

    # Apply the Component analysis function 
    bin_img = np.array(res * 255, dtype=np.uint8)
    area_th = conf.MIN_POINTS_ON_LINE
    analysis = cv.connectedComponentsWithStats(bin_img, 
                                                4, 
                                                cv.CV_32S) 
    (totalLabels, label_ids, values, centroid) = analysis 
    
    # Initialize a new image to 
    # store all the output components 
    output = np.zeros(edge_img.shape, dtype="uint8")

    # Loop through each component 
    for i in range(1, totalLabels): 
        area = values[i, cv.CC_STAT_AREA]   
    
        if area > area_th: 
            # Labels stores all the IDs of the components on the each pixel 
            # It has the same dimension as the threshold 
            # So we'll check the component 
            # then convert it to 255 value to mark it white 
            componentMask = (label_ids == i).astype("uint8") * 255

            # Creating the Final output mask 
            output = cv.bitwise_or(output, componentMask) 

    num = sum(output)
    
    return output, num

def draw_lines(img, lines):
    """ Draw lines on the image
    
    Args:
        img(ndarray): image
        lines(list): list of lines. A line is represented by r and angle
        
    Returns:
        out_c(ndarray): image with lines"""
    out = img.copy()
    out_c = cv.cvtColor(out, cv.COLOR_GRAY2RGB)
    for line in lines:
        d = line[0]
        theta = line[1]
        a = np.cos(theta)
        b = np.sin(theta)
        x0 = a * d 
        y0 = b * d 
        x1 = int(x0 + 1000 * (-b))
        y1 = int(y0 + 1000 * (a))
        x2 = int(x0 - 1000 * (-b))
        y2 = int(y0 - 1000 * (a))
        cv.line(out_c, (x1, y1), (x2, y2), (0, 0, 255), 2)
    return out_c

def detect_points_on_lines(edge_img, lines, thickness=2):
    """ detect points on lines

    Args:
        edge_img: edge image 
        lines: list of lines 
        thickness: thickness of inlier lines

    Returns:
        points_list: list of points on lines
        edge_c: edge image with lines
    """
    # line with dist and theta 
    # xcos(theta) + ycos(theta) = dist
    points_list = []
    edge_c = edge_img.copy()
    edge_c = cv.cvtColor(edge_c, cv.COLOR_GRAY2RGB)
    for line in lines:
        #print(line)
        d = line[0]
        theta = line[1]
        a = np.cos(theta)
        b = np.sin(theta)
        x0 = a * d 
        y0 = b * d 
        x1 = int(x0 + 1000 * (-b))
        y1 = int(y0 + 1000 * (a))
        x2 = int(x0 - 1000 * (-b))
        y2 = int(y0 - 1000 * (a))
        cv.line(edge_c, (x1, y1), (x2, y2), (0, 0, 255), 2)

        # Vectorized approach: use NumPy's where for massive speedup
        res, n_pts = get_number_of_points_on_line(edge_img, line, thickness)
        y_coords, x_coords = np.where(res)
        points = [[x, y] for x, y in zip(x_coords, y_coords)]
        
        points_list.append(points)
    return points_list, edge_c


def detect_lines(img):
    """ Detect lines
    
    Args: 
        img(ndarray): image
    
    Returns:
        lines(ndarray): detected lines. numpy array of (d, theta, votes)
        edges(ndarray): edge image 
    """
    img = cv.medianBlur(img, conf.BLUR_KSIZE) 
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
    edges = cv.Canny(gray, conf.CANNY_TH1, conf.CANNY_TH2)
    lines = cv.HoughLinesWithAccumulator(edges, 1,  np.pi / 180, 
                                         conf.HOUGH_LINE_THRESH)
    if lines is None:
        lines = np.array([[0, 0, 0]])
    else:
        lines = lines.reshape((-1, 3))
    
    # non maximum supression
    filtered_lines = np.array([lines[0]])
    for line in lines:
        # For this line, check which of the existing 4 it is similar to.
        rho = line[0]
        theta = line[1]
        closeness_rho   = np.isclose(rho,   
                                     np.array(filtered_lines[:,0]), 
                                     atol = conf.CLOSENESS_RHO_RAW) 
        closeness_theta = np.isclose(theta, 
                                     np.array(filtered_lines[:,1]), 
                                     atol = np.pi/36.0 * conf.CLOSENESS_THETA_RAW) 

        similar_rho = np.any(closeness_rho)
        similar_theta = np.any(closeness_theta)
        similar = (similar_rho and similar_theta)
        if not similar:
            filtered_lines = np.concatenate((filtered_lines, [line]), axis=0)
    return filtered_lines, edges

def detect_lines_on_edge(edge_img):
    """detect lines on edge image
    
    Args: 
        edge_img: image
    
    Returns:
        lines: detected lines (d, theta, votes)
        edges: edge image 
    """
    margin = 30
    width = edge_img.shape[1]
    height = edge_img.shape[0]
    edge_img[:margin, :] = 0 
    edge_img[height-margin:, :] = 0 
    edge_img[:, :margin] = 0 
    edge_img[:, width-margin:] = 0 
    if edge_img.shape[2] == 3:
        edge_img = cv.cvtColor(edge_img, cv.COLOR_RGB2GRAY)
    lines = cv.HoughLinesWithAccumulator(edge_img, 1, np.pi / 180, 
                                         conf.HOUGH_LINE_THRESH_EDGE)
    if lines is None:
        lines = np.array([[0, 0, 0]])
    else:
        lines = lines.reshape((-1, 3))
    
    # non maximum supression
    filtered_lines = np.array([lines[0]])
    for line in lines:
        # For this line, check which of the existing 4 it is similar to.
        rho = line[0]
        theta = line[1]
        closeness_rho   = np.isclose(rho,   
                                     np.array(filtered_lines[:,0]), 
                                     atol = conf.CLOSENESS_RHO_EDGE) 
        closeness_theta = np.isclose(theta, 
                                     np.array(filtered_lines[:,1]), 
                                     atol = np.pi/36.0 * conf.CLOSENESS_THETA_EDGE) 

        similar_rho = np.any(closeness_rho)
        similar_theta = np.any(closeness_theta)
        similar = (similar_rho and similar_theta)
        if not similar:
            filtered_lines = np.concatenate((filtered_lines, [line]), axis=0)
    return filtered_lines, edge_img


def detect_lines_with_undistortion(img, param_space):
    """ detect lines using Hough transform while changing k1
        This function rectifies the input image with k1 and detects edges in 
        the rectified image.

    Args: 
        img(ndarray): image 
        param_space(dict): distortion coefficient search space 
    
    Returns:
        k1s(ndarray): k1 parameter space 
        lines_list(list): (KxLx3) list of list of line detection output(rho, theta, votes).
        points_list(list): (KxLxPx2) list of list of points on the lines.  
        edges(ndarray): edge image
    """
    k1s = param_space['k1'] # use only k1 for now
    lines_list = [] 
    points_list = []
    params = {}
    for i, k1 in enumerate(k1s):
        params['k1'] = k1
        params['k2'] = 0.0
        params['k3'] = 0.0
        img_u = undistort_image(img, params)
        lines, edges = detect_lines(img_u)
        lines_list.append(lines)
        points, edge_lines = detect_points_on_lines(edges, lines)
        points_list.append(points)

        if conf.DEBUG_FILE_PREFIX is not None and i % 10 == 0:
            edge_points = draw_points(edges, points)
            cv.imwrite(f'{conf.DEBUG_FILE_PREFIX}_edge_lines_{i}.jpg', edge_lines)
            cv.imwrite(f'{conf.DEBUG_FILE_PREFIX}_edge_points_{i}.jpg', edge_points)
    
    # filtering similar lines accross k1s
    k1s, lines_list, points_list = filter_lines(k1s, lines_list, points_list)
    return k1s, lines_list, points_list, edges


def get_similar_line_id(lines_list, line_t, rho_th, theta_th):
    """  get similar line id in lines_list
    
    Args:
        lines_list: (Nx3). list of line detection output(rho, theta, votes)
        line: line information (rho, theta, votes) 
        rho_th: the rho threshold 
        theta_th: the theta threshold 
    
    Returns:
        out: index of the similar line
    """
    out = -1
    for i, line in enumerate(lines_list):
        if abs(line[0] - line_t[0]) < rho_th and \
            abs(line[1] - line_t[1]) < theta_th: 
            out = i
    return out

def filter_lines(k1s, lines_list, points_list):
    """ filter lines accross k1s
    
    Find the lines with maximum votes among similar lines
    
    Args:
        k1s: list of k1
        lines_list: (KxLx3). list of list of line detection output(rho, theta, votes)
        points_list: (KxLxPx2) list of list of points on the lines
    
    Returns:
        k1s: list of k1
        lines_list: (Lx3). list of line detection output(rho, theta, votes)
        points_list: (Px2) list of points on the lines
    """
    # find the lines with the maximum votes
    # lines are sorted by the number of votes
    # initialize
    new_k1s = [k1s[0]]
    new_lines_list = [lines_list[0][0]]
    new_points_list = [points_list[0][0]]
    for i, lines in enumerate(lines_list): # for k1s
        for j, line in enumerate(lines): # for lines of each k1
            rho = line[0]
            theta = line[1]
            vote = line[2]
            # compare the similarity
            # rho tolerance: 10 pixels
            # angle tolerance: 30 degrees
            id = get_similar_line_id(new_lines_list, 
                                     line, 
                                     conf.LINE_FILTER_RHO_TH, 
                                     np.pi/36.0 * conf.LINE_FILTER_THETA_TH)
            if id >= 0:
                # replace the similar line with the larger one
                if new_lines_list[id][2] < line[2]:
                    new_k1s[id] = k1s[i]
                    new_lines_list[id] = line
                    new_points_list[id] = points_list[i][j]
            else:
                new_k1s.append(k1s[i])
                new_lines_list.append(line)
                new_points_list.append(points_list[i][j])
    return new_k1s, new_lines_list, new_points_list


def check_overlap(pts1, pts2):
    """ Check overlap between two point sets.
    
        True if there is overlap, False otherwise
    
    Args:
        pts1: (list) (Nx2) point set 1
        pts2: (list) (Mx2) point set 2
    
    Returns:
        True if there is overlap, False otherwise
    """
    pts1 = set(pts1)
    pts2 = set(pts2)
    pts_union = pts1.union(pts2)
    thresh = 5
    if abs(len(pts1) + len(pts2) - len(pts_union)) > thresh:
        return True
    return False

def merge_points(pts1, pts2):
    pts1 = set(pts1)
    pts2 = set(pts2)
    pts3 = pts1.union(pts2)
    return list(pts3)

def reconstruct_edge_image(img, k1s, points_list):
    """ Reconstruct an edge image with the points on the lines
    
    Args:
        img: input image
        k1s: k1 list
        points_list: (KxLxPx2) list of list of points on the lines
    
    Returns:
        edge_img: reconstructed edge image
    """
    edge_img = np.zeros(img.shape, dtype=np.uint8)
    width = img.shape[1]
    height = img.shape[0]
    for i, points in enumerate(points_list):
        for point in points:
            params = {'k1': k1s[i], 'k2':0, 'k3':0}
            pt_c = [img.shape[1]//2, img.shape[0]//2]
            pt_d = distort_point(point, pt_c, params)
            x = int(pt_d[0])
            y = int(pt_d[1])
            if x>=0 and x<width and y>=0 and y<height:
                edge_img[y, x] = 255

    edge_point_img = edge_img.copy() # edge image with overlayed points
    edge_point_img = cv.cvtColor(edge_point_img, cv.COLOR_RGB2GRAY)
    edge_point_img = draw_points(edge_point_img, points_list)

    return edge_img, edge_point_img


def get_line_candidates(img, edge_img, k1s, lines_list, points_list):
    """ Get line candidates from the points on the lines of the rectified images 
    
    Args:
        img(ndarray): input image
        edge_img(ndarray): edge image
        k1s(list): (Nx1) k1 list
        lines_list(list): (Nx3) list of lines [r, theta, votes]
        points_list(list): (NxPx2)  list of points on the lines in undistorted image
    
    Returns:
        filtered_line_points_d(list): list of points on the candidate lines in distrotion image
        line_candidate_img: image with line candidate
    """
    width = edge_img.shape[1]
    height = edge_img.shape[0]
    pt_c = [width//2, height//2] # image center

    # convert undistort points to distort points
    n_lines = len(lines_list)
    points_list_d = []
    for i in range(n_lines): # for lines 
        params = {'k1': k1s[i], 'k2':0, 'k3':0}
        pts_d = []
        for pt_u in points_list[i]: # for points 
            if len(pt_u) == 0:
                continue
            pt_d = []
            pt_d = distort_point(pt_u, pt_c, params)
            pts_d.append((int(pt_d[0]), int(pt_d[1])))
        points_list_d.append(pts_d)

    # merge points if the points are overlapping and the theta is similar
    new_lines = [] # merged lines. a list of lines (r, theta, votes)
    new_line_points_d = [] # merged points. a list of points 
    th_theta = np.deg2rad(20.0) # threshold for similar lines 
    for i in range(n_lines): # for lines 
        pts = points_list_d[i]
        # check if the points sets are overlapping and the lines' theta are similar
        overlap = False
        for k, new_pts in enumerate(new_line_points_d): 
            if check_overlap(pts, new_pts) and \
                abs(lines_list[i][1] - new_lines[k][1]) < th_theta:
                new_line_points_d[k] = merge_points(pts, new_pts)
                overlap = True
                break
        if not overlap:    
            new_lines.append(lines_list[i])
            new_line_points_d.append(pts)

    # remove points in boundaries
    filtered_line_points_d = []
    for i, pts in enumerate(new_line_points_d):
        new_pts = []
        for pt in pts:
            w_d = int((edge_img.shape[1] - img.shape[1]) / 2) + 5 # 5 is margin
            h_d = int((edge_img.shape[0] - img.shape[0]) / 2) + 5
            if pt[0] > w_d and pt[0] < width - w_d and \
                pt[1] > h_d and pt[1] < height - h_d: 
                new_pts.append(pt)
        if len(new_pts) != 0:
            filtered_line_points_d.append(new_pts)
            
    line_candidate_img = np.zeros(edge_img.shape, dtype=np.uint8)
    line_candidate_img = draw_points(line_candidate_img, filtered_line_points_d)

    print('#line candidates: ', len(new_line_points_d))
    if conf.DEBUG_FILE_PREFIX is not None:
        cv.imwrite(f'{conf.DEBUG_FILE_PREFIX}_line_candidate_img.jpg', line_candidate_img)

    return filtered_line_points_d, line_candidate_img

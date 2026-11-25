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
import math
import argparse

def distort_point(pt_u, pt_c, params):
    """ calculate a point coordinate in the distorted image with given parameters
    
    Args:
        pt_u: point in a rectified image 
        pt_c: image center point of the rectified image
        params(dict): lens distortion parameters
    
    Returns:
        point coordinate in the distorted image 
    """
    # parameters
    k1 = params['k1']
    k2 = params['k2']
    k3 = params['k3']

    xc = pt_c[0]
    yc = pt_c[1]
    x = pt_u[0]
    y = pt_u[1]
    xn = (x - xc) 
    yn = (y - yc) 
    r2 = xn**2 + yn**2 
    pt_d_x = xn * (1 + k1 * r2 + k2 * r2**2 + k3 * r2**3) + xc
    pt_d_y = yn * (1 + k1 * r2 + k2 * r2**2 + k3 * r2**3) + yc
    return [pt_d_x, pt_d_y]


def distort_image(src, params, crop=False):
    """ distort image with given parameters using inverse Brown-Conrady model

    The inverse of the Brown-Conrady model is used to map the corrected (ideal) 
    coordinates back to the distorted coordinates. This process is more complex 
    than the forward transformation because it involves solving non-linear 
    equations. However, an iterative approach can be used to approximate 
    the inverse transformation.

    Args:
        src: image to be undistorted
        params: dictionary
        crop(bool): crop the output image
    
    Returns:
        distorted image
    """
    # parameters
    k1 = params['k1']
    k2 = params['k2']
    k3 = params['k3']

    # update map
    map_x = np.zeros((src.shape[0], src.shape[1]), dtype=np.float64)
    map_y = np.zeros((src.shape[0], src.shape[1]), dtype=np.float64)
    height = src.shape[0]
    width = src.shape[1]
    xc = width // 2
    yc = height // 2
    for y in range(height):
        for x in range(width):
            xn = (x - xc) #/ xc
            yn = (y - yc) #/ yc

            x_d, y_d = inverse_brown_conrady(xn, yn, k1, k2, k3)
            map_x[y,x] = x_d + xc
            map_y[y,x] = y_d + yc 
            
            if x == 200 and y == 200:
                print('x=200, y=200:')
                print(map_x[y,x], map_y[y,x])

    dst = cv.remap(src, map_x, map_y, cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT, borderValue=0)
    if crop:
        x_d, y_d = inverse_brown_conrady(0, 0, k1, k2, k3)
        print(x_d, y_d)
        x0 = -int(map_x[0, 0])
        y0 = -int(map_y[0, 0])
        if x0 > width:
            x0 -= width
            y0 -= height
        x1 = width - x0 
        y1 = height - y0 
        print(x0, y0, x1, y1)
        dst = dst[y0:y1, x0:x1]
 
    return dst


def inverse_brown_conrady(x_c, y_c, k1, k2, k3, max_iter=100, tolerance=1e-6):
    """ Inverse Brown Conrady function 
    The inverse of the Brown-Conrady model is used to map the corrected (ideal) 
    coordinates back to the distorted coordinates. 

    Args:
        x_c, y_c: corrected (ideal) coordinates
        k1, k2, k3: distortion parameters
        max_iter(int): maximum number of iterations
        tolerance(float): convergence tolerance

    Returns:
        x_d, y_d: distorted coordinates after max_iter iterations or until convergence
    """
    # Initialize distorted coordinates
    x_d, y_d = x_c, y_c
    
    for i in range(max_iter):
        # Compute radial distance
        r = math.sqrt(x_d**2 + y_d**2)
        
        # Compute distortion factor
        D = 1 + k1 * r**2 + k2 * r**4 + k3 * r**6
        
        # Update distorted coordinates
        x_d_new = x_c / D
        y_d_new = y_c / D
        
        # Check for convergence
        if abs(x_d_new - x_d) < tolerance and abs(y_d_new - y_d) < tolerance:
            break
        
        # Update for next iteration
        x_d, y_d = x_d_new, y_d_new
    
    return x_d, y_d

def undistort_image(src, params, padding=50):
    """ Undistort an image with parameters
    
    Args:
        src(ndarray): image to be undistorted
        params(dict): lens distortion parameter dictionary
        padding(int): padding for the undistorted image
        
    Returns:
        img_u(ndarray): undistorted image
    """
    width = src.shape[1]
    height = src.shape[0]

    new_width = width + 2 * padding
    new_height = height + 2 * padding
    img_ext = np.full((new_height, new_width, 3), (0, 0, 0), dtype=np.uint8)
    img_ext[padding:padding+height, padding:padding+width] = src

    new_width = img_ext.shape[1]
    new_height = img_ext.shape[0]

    camera_matrix = np.zeros((3,3))
    camera_matrix[0,0] = 1
    camera_matrix[1,1] = 1
    camera_matrix[2,2] = 1
    camera_matrix[0,2] = width // 2
    camera_matrix[1,2] = height // 2
    dist_coeff = np.array([params['k1'], 0, 0 ,0])
    new_camera_matrix = camera_matrix
    new_camera_matrix[0,2] = new_width // 2
    new_camera_matrix[1,2] = new_height // 2
    map_x, map_y = cv.initUndistortRectifyMap(camera_matrix, dist_coeff, None, 
                                              camera_matrix, 
                                              (new_width, new_height), 
                                              cv.CV_32FC1)
    img_u = cv.remap(img_ext, map_x, map_y, cv.INTER_LINEAR)
    return img_u 

def undistort_points(pts, width, height, params):
    """ Calculate a point in a rectified image  
    
    Args:
        pts(list): list of points in the distored image
        width, height: width and height of the distorted image
        params(dict): lens distortion parameter dictionary
    
    Returns:
        pts_u(ndarray): list of points in the rectified image
    """
    camera_mat = np.zeros((3,3))
    camera_mat[0,0] = 1
    camera_mat[1,1] = 1
    camera_mat[2,2] = 1
    camera_mat[0,2] = width // 2
    camera_mat[1,2] = height // 2
    dist_coeff = np.array([params['k1'], 0, 0 ,0])
    pts = np.array(pts, dtype=np.float64)
    pts_u = cv.undistortPoints(pts, camera_mat, dist_coeff, None, camera_mat) 
    return pts_u.reshape(-1, 2)

if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Distort image')
    parser.add_argument('-i', '--image_path', type=str,
                        help='Path to the input image')
    args = parser.parse_args()
    src = cv.imread(args.image_path)  
    params = {'k1': -0.000001, 'k2': 0.0000000000000, 'k3': 0.00000000000000}
    # resize image
    src = cv.resize(src, None, fx=0.5, fy=0.5) 
    pt = [200,200]
    cv.circle(src, (pt[0], pt[1]), 2, (255,0,0), 2)
    cv.imshow("input", src)

    img_d = distort_image(src, params, False)
    xc = img_d.shape[1] // 2
    yc = img_d.shape[0] // 2
    pt_d = distort_point(pt, [xc, yc], params)
    print(pt_d)
    cv.circle(img_d, (int(pt_d[0]), int(pt_d[1])), 2, (0,0,255), 2)
    cv.imshow("distortion image", img_d)

    img_u = undistort_image(img_d, params)
    cv.imshow("undistortion image", img_u)
    cv.waitKey()

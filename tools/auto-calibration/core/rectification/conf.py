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

DEBUG_DIR = 'debug'
DEBUG_FILE_PREFIX = None

# score metrics
SCORE_METRICS = ['n_lines', 'total_points', 'max_points', 'total_points_outside', 'avg_distance']

# line_detection.py
# line similarity
LINE_FILTER_RHO_TH = 10.0 # rho tolerance(pixel)
LINE_FILTER_THETA_TH = 30.0 # angle tolerance(degree)

# detect lines on raw image
BLUR_KSIZE = 9 # kernel size of blurring
CANNY_TH1 = 5 # threshold1 of Canny operator
CANNY_TH2 = 60 # threshold2 of Canny operator
HOUGH_LINE_THRESH = 100
# non maximum supression in detecting lines on raw image
CLOSENESS_RHO_RAW = 20.0
CLOSENESS_THETA_RAW = 30.0

# detect lines on edgeimage
HOUGH_LINE_THRESH_EDGE = 150
# non maximum supression in detecting lines on edge image
CLOSENESS_RHO_EDGE = 50.0
CLOSENESS_THETA_EDGE = 30.0

# minimum number of points on line
MIN_POINTS_ON_LINE = 50

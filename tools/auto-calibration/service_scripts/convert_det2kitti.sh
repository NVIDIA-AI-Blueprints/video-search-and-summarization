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

echo $1
# Convert to absolute path to avoid relative path issues
target_dir=$(realpath "$1")
chown $(id -u):$(id -g) "$target_dir"
cd "$target_dir"
mkdir kitti_detector/Stream_0
mv kitti_detector/00_000* kitti_detector/Stream_0/.
cd $OLDPWD

SCRIPT_ROOT="$(dirname "$0")"

bash $SCRIPT_ROOT/kittiDetect2mot_4_viz.sh \
    -i "$target_dir/kitti_detector/Stream_0" \
    -o "$target_dir/Det-Stream_0.log"


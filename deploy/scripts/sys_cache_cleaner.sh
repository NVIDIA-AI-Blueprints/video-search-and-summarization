######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

set -e

echo "disable vm/nr_hugepage"
echo 0 | tee /proc/sys/vm/nr_hugepages

echo "Starting cache cleaner - Running"
echo "Press Ctrl + C to stop"
while true; do
	sync && echo 3 | tee /proc/sys/vm/drop_caches > /dev/null
	sleep 3
done

#!/bin/sh
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


# Check if API_URL is provided
if [ -z "$API_URL" ]; then
    echo "=============================================="
    echo "WARNING: API_URL not provided!"
    echo "Run with: docker run -e API_URL=http://your-server:8000 ..."
    echo "=============================================="
fi

# Replace placeholder with actual environment variable (or empty if not set)
sed -i "s|__API_URL_PLACEHOLDER__|${API_URL:-}|g" /usr/share/nginx/html/env-config.js

# Start nginx
exec nginx -g "daemon off;"


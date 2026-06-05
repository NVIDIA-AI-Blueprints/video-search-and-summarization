#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# The streaming library lives in this repo as the sibling directory "streaming-lib".
REPO_DIR="../streaming-lib"

# Check that the streaming library directory exists
if [ ! -d "$REPO_DIR" ]; then
	echo "Error: streaming library directory '$REPO_DIR' not found." >&2
	exit 1
fi

# Go to the streaming library directory
cd "$REPO_DIR"

# Install dependencies and build
npm install
npm run build
npm link

# Go back to the original project directory
cd -

# Link the package
npm link vst-streaming-lib

echo "Installation and linking completed successfully!"

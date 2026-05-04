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

FROM alpine:3.23.4

# Create a working directory
WORKDIR /opt/mdx/

# Copy the init scripts into the working directory
COPY ./elk/init-scripts ./init-scripts

# Make scripts executable
RUN chmod +x ./init-scripts/*.sh

# Install bash and curl commands.
RUN apk update && apk add bash

RUN apk --no-cache add curl

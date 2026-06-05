/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// Minimal stub for S3 storage writer
// This is a placeholder implementation that will be expanded when AWS SDK integration is added

#include "logger.h"
#include <iostream>

namespace nv_vms
{

// This file is a stub implementation for S3 storage writer
// The actual implementation will be added when AWS SDK for C++ is integrated
// For now, this file ensures compilation without external dependencies

void s3_storage_writer_stub()
{
    LOG(info) << "S3 storage writer stub - to be implemented with AWS SDK" << std::endl;
}

} // namespace nv_vms
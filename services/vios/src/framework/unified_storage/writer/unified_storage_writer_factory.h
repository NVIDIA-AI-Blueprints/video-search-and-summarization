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

#pragma once

#include "../unified_storage_types.h"
#include "unified_storage_writer.h"
#include <memory>

namespace nv_vms
{

/**
 * @brief Factory for creating unified storage writers
 */
class UnifiedStorageWriterFactory
{
public:
    static std::unique_ptr<UnifiedStorageWriter> createWriter(StorageType type);
    static std::unique_ptr<UnifiedStorageWriter> createWriter(const std::string& type_name);
};

} // namespace nv_vms
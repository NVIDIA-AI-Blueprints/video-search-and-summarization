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

#include <vector>
#include "SchemaValidator.h"

#include "sensor_spec.h"
#include "live_stream_spec.h"
#include "replay_stream_spec.h"
#include "record_stream_spec.h"
#include "stream_bridge_spec.h"
#include "proxy_stream_spec.h"
#include "storage_spec.h"
#include "websocket_spec.h"

/**
To define APIs:
1. Create a vector of ApiSpec objects, where each ApiSpec represents an API endpoint.
2. For each ApiSpec, define a vector of FieldRule objects to specify the JSON structure.
3. Each FieldRule represents a field in the JSON, with the following properties:
   - json_path: JSON path to the field (e.g., "user.name" or "items[0].id")
   - type: JsonType enum value specifying the field's data type
   - required: Set to true if the field must be present
   - format: Format enum value for additional type checking (e.g., UUID, date)
   - children: Vector of nested FieldRule objects for complex structures
   - customValidator: Optional function for custom validation logic
 */

inline const std::vector<ApiSpec> VST_API_SPEC = []()
{
    const auto specs = {
        &SENSOR_API_SPEC,
        &LIVE_API_SPEC,
        &REPLAY_API_SPEC,
        &RECORD_API_SPEC,
        &STREAMBRIDGE_API_SPEC,
        &PROXY_STREAM_API_SPEC,
        &STORAGE_API_SPEC,
        &WEBSOCKET_API_SPEC
    };
    
    std::vector<ApiSpec> all_specs;
    
    for (const auto* spec : specs)
    {
        all_specs.insert(all_specs.end(), spec->begin(), spec->end());
    }
    
    return all_specs;
}();

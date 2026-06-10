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
const std::vector<ApiSpec> PROXY_STREAM_API_SPEC = {
    // GET /v1/proxy/streams - No request body validation needed
    {"/api/v1/proxy/streams"},

    {"/api/v1/proxy/stream/add",
     {
         {"id", JsonType::String, true}, // Swagger: pattern: "^.+$", maxLength: 100
         {"url", JsonType::String, true}, // Swagger: maxLength: 128, pattern: ".*"
     }},

    {"/api/v1/proxy/stream/{streamId}",
     {
         {"streamId", JsonType::String, true}, // Swagger: pattern: "^.+$", maxLength: 100
     }},

    {"/api/v1/proxy/session/{streamId}",
     {
         {"streamId", JsonType::String, true}, // Swagger: pattern: "^.+$", maxLength: 100
     }},

    {"/api/v1/proxy/info"},

    {"/api/v1/proxy/configuration"},

    {"/api/v1/proxy/version"},

    {"/api/v1/proxy/help"},

    {"/api/v1/proxy/stream/"},

    {"/api/v1/proxy/debug/qos"},
};

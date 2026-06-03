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
const std::vector<ApiSpec> RECORD_API_SPEC = {
    {"/api/v1/record/streams"},

    {"/api/v1/record/stream/add",
     {
         {"url", JsonType::String, true},
     }},

    {"/api/v1/record/{streamId}",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/start",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/stop",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/event",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/schedule",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY},
      {"$",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]",
         JsonType::Object,
         false,
         Format::NONE,
         {{"endTime", JsonType::String, true, Format::CRON_TIME},
          {"startTime", JsonType::String, true, Format::CRON_TIME}}}}}}},

    {"/api/v1/record/{streamId}/schedule",
     {
         {"streamId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {
         {"startTime", JsonType::String, true, Format::NONE},
         {"endTime", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/timelines",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     },
     {
         {"startTime", JsonType::String, false, Format::NONE},
         {"endTime", JsonType::String, false, Format::NONE},
     }},

    {"/api/v1/record/{streamId}/status",
     {
         {"streamId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/record/configuration"},

    {"/api/v1/record/version"},

    {"/api/v1/record/help"},

    {"/api/v1/record/timelines",
     {},
     {
         {"streams", JsonType::Array, false, Format::NONE, 
          {{"$[*]", JsonType::String, false, Format::UUID}}},
     }},

    {"/api/v1/record/"},
};

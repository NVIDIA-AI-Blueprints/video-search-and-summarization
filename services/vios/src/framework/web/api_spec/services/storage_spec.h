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

const std::vector<ApiSpec> STORAGE_API_SPEC = {
    // IMPORTANT: Specific paths must come before parameterized paths to ensure correct matching
    {"/api/v1/storage/file/protect",
     {{"filePath",
       JsonType::Array,
       true,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"protect", JsonType::Boolean, true}}},

    {"/api/v1/storage/file/mediainfo",
     {},
     {
         {"sensorId", JsonType::String, false, Format::NONE},
         {"id", JsonType::String, false, Format::NONE},
     }},

    {"/api/v1/storage/file/{streamId}",
     {
         {"streamId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {
         // startTime/endTime are required only when fullFile=true is NOT set;
         // the runtime handler enforces that conditionally.
         {"startTime", JsonType::String, false},
         {"endTime", JsonType::String, false},
         {"fullLength", JsonType::String, false},
         {"container", JsonType::String, false},
         {"fullFile", JsonType::String, false},
         {"configuration", JsonType::Object, false},
     }},

    {"/api/v1/storage/stream/{streamId}/picture/url",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}},
     {
         {"startTime", JsonType::String, true},
         {"expiryMinutes", JsonType::String, false},
     }},

    {"/api/v1/storage/stream/{streamId}/picture",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}},
     {
         {"startTime", JsonType::String, true},
         {"expiryMinutes", JsonType::String, false},
     }},

    {"/api/v1/storage/{streamId}",
     {
         {"streamId", JsonType::String, true, Format::NOT_EMPTY},
     }},

    {"/api/v1/storage/file/list",
     {},
     {}},

    {"/api/v1/storage/file/path",
     {},
     {
         {"id", JsonType::String, true, Format::NOT_EMPTY},
         {"metadata", JsonType::String, false},
     }},

    {"/api/v1/storage/file",
     {},
     {
         {"id", JsonType::String, true, Format::NOT_EMPTY},
         {"startTime", JsonType::String, false},
         {"endTime", JsonType::String, false},
         {"fullFile", JsonType::String, false},
     }},

    {"/api/v1/storage/file",
     {},
     {
         {"filePath", JsonType::Array, true},
     }},

    {"/api/v1/storage/file",
     {{"mediaFilePath", JsonType::String},
      {"metadataFilePath", JsonType::String},
      {
          "metadata",
          JsonType::Object,
          false,
          Format::NONE,
          {{"eventInfo", JsonType::String},
           {"timestamp", JsonType::Int, true},
           {"streamname", JsonType::String},
           {"tag", JsonType::String},
           {"sensorId", JsonType::String, true, Format::NOT_EMPTY},
           {"checksum", JsonType::String}},
      }}},

    {"/api/v1/storage/file/{sensorId}/list",
     {
         {"sensorId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {}},

    {"/api/v1/storage/file/{sensorId}/url",
     {
         {"sensorId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {
         // startTime/endTime are required only when fullFile=true is NOT set;
         // the runtime handler enforces that conditionally.
         {"startTime", JsonType::String, false},
         {"endTime", JsonType::String, false},
         {"container", JsonType::String, false},
         {"expiryMinutes", JsonType::String, false},
         {"fullFile", JsonType::String, false},
         {"blocking", JsonType::String, false},
     }},

    {"/api/v1/storage/file/{sensorId}/path",
     {
         {"sensorId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {
         {"startTime", JsonType::String, false},
         {"endTime", JsonType::String, false},
         {"metadata", JsonType::String, false},
     }},

    {"/api/v1/storage/file/{sensorId}",
     {
         {"sensorId", JsonType::String, true, Format::NOT_EMPTY},
     },
     {
         {"id", JsonType::String, false, Format::NONE},
         // startTime/endTime are required only when fullFile=true is NOT set;
         // the runtime handler enforces that conditionally.
         {"startTime", JsonType::String, false},
         {"endTime", JsonType::String, false},
         {"fullLength", JsonType::String, false},
         {"fullFile", JsonType::String, false},
     }},

    {"/api/v1/storage/file/{id}",
     {
         {"id", JsonType::String, true, Format::NOT_EMPTY},
     },
     {}},

    {"/api/v1/storage/configuration"},

    {"/api/v1/storage/version"},

    {"/api/v1/storage/help"},

    {"/api/v1/storage/size",
     {},
     {
         {"timelines", JsonType::String, false},
         {"streams", JsonType::Array, false},
     }},

    {"/api/v1/storage/info"},

    {"/api/v1/storage/file/protected"},

    {"/storage/{filename}",
     {
         {"filename", JsonType::String, true, Format::NOT_EMPTY},
     }},

    {"/storage/temp_files/{filename}",
     {
         {"filename", JsonType::String, true, Format::NOT_EMPTY},
     }},

    // PUT raw upload with filename (mandatory) and timestamp segment
    {"/api/v1/storage/file/{filename}/{timestamp}",
     {
         {"filename", JsonType::String, true, Format::NOT_EMPTY},
         {"timestamp", JsonType::String, true, Format::NONE},
     }},
};
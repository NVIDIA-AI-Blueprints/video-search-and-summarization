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

const std::vector<ApiSpec> STREAMBRIDGE_API_SPEC = {
    {"/api/v1/streambridge/streams"},

    {"/api/v1/streambridge/stream/start",
     {{"isClient", JsonType::Boolean, false},
      {"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {
          "options",
          JsonType::Object,
          true,
          Format::NONE,
          {{"rtptransport", JsonType::String, true},
           {"timeout", JsonType::Int, true},
           {"streamId", JsonType::String, true},
           {"quality", JsonType::String, true}},
      },
      {
          "sessionDescription",
          JsonType::Object,
          true,
          Format::NONE,
          {{"type", JsonType::String, true},
           {"sdp", JsonType::String, true}},
      }}},

    {"/api/v1/streambridge/stream/stop",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true}}},

    {"/api/v1/streambridge/stream/status",
     {
         {"peerId", JsonType::String, true, Format::NOT_EMPTY},
         {"mediaSessionId", JsonType::String, true},
     }},

    {"/api/v1/streambridge/setAnswer",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"sessionDescription", JsonType::String, true},
      {"mediaSessionId", JsonType::String, true}}},

    {"/api/v1/streambridge/iceCandidate",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {
          "candidate",
          JsonType::Object,
          true,
          Format::NONE,
          {{"candidate", JsonType::String, true},
           {"sdpMLineIndex", JsonType::Int, true},
           {"sdpMid", JsonType::String, true}},
      }}},

    {"/api/v1/streambridge/iceServers",
     {
         {"peerId", JsonType::String, true, Format::NOT_EMPTY},
     }},

    {"/api/v1/streambridge/configuration"},

    {"/api/v1/streambridge/configuration",
     {{"stunUrlList",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"useTwilioStunTurn", JsonType::Boolean},
      {"twilioAccountSid", JsonType::String},
      {"twilioAuthToken", JsonType::String},
      {"reverseProxyServerAddress", JsonType::String},
      {"staticTurnUrlList",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"useCoturnAuthSecret", JsonType::Boolean},
      {"useReverseProxy", JsonType::Boolean}}},

    {"/api/v1/streambridge/version"},

    {"/api/v1/streambridge/help"},
};

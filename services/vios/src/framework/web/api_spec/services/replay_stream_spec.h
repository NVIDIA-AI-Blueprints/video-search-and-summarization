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

const std::vector<ApiSpec> REPLAY_API_SPEC = {
    {"/api/v1/replay/streams"},

    {"/api/v1/replay/stream/query"},

    {"/api/v1/replay/stream/start",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY},
      {"tag", JsonType::String, false, Format::NONE},
      {"startTime", JsonType::String},
      {"endTime", JsonType::String},
      {"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {
          "options",
          JsonType::Object,
          true,
          Format::NONE,
          {{"quality", JsonType::String, true},
           {"rtptransport", JsonType::String, true},
           {"timeout", JsonType::Int, true},
           {
               "composite",
               JsonType::Object,
               false,
               Format::NONE,
               {{"includeFloorPlan", JsonType::Boolean},
                {"doComposite", JsonType::Boolean},
                {"streamIds", JsonType::Array},
                {
                    "showSensorName",
                    JsonType::Object,
                    false,
                    Format::NONE,
                    {{"enable", JsonType::Boolean},
                     {"position", JsonType::Array}},
                }},
           },
           {
               "overlay",
               JsonType::Object,
               false,
               Format::NONE,
               {{"objectId", JsonType::Array},
                {"color", JsonType::String},
                {"thickness", JsonType::Int},
                {"debug", JsonType::Boolean},
                {"needBbox", JsonType::Boolean},
                {"needTripwire", JsonType::Boolean},
                {"needRoi", JsonType::Boolean},
                {"opacity", JsonType::Int},
                {"framerate", JsonType::Int},
                {"proximityClass", JsonType::Array},
                {"entrantClass", JsonType::Array},
                {"proximityAreaFactor", JsonType::Number},
                {"overlayColorCode", JsonType::Array},
                {"proximityAnimation", JsonType::String}},
           }},
      },
      {
          "sessionDescription",
          JsonType::Object,
          true,
          Format::NONE,
          {{"type", JsonType::String, true},
           {"sdp", JsonType::String, true}},
      }}},

    {"/api/v1/replay/stream/swap",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"streamId", JsonType::String, true, Format::NOT_EMPTY},
      {"startTime", JsonType::String},
      {"endTime", JsonType::String}}},

    {"/api/v1/replay/stream/stop",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/stream/pause",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/stream/resume",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/stream/seek",
     {{"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY},
      {"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"action", JsonType::String, true},
      {"value", JsonType::StringOrNumber, false}}},

    {"/api/v1/replay/stream/status"},

    {"/api/v1/replay/stream/{streamId}/picture",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/stream/{streamId}/picture/url",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/stream/stats"},

    {"/api/v1/replay/setAnswer",
     {{"sessionDescription", JsonType::String, true},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/replay/iceCandidate",
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

    {"/api/v1/replay/iceCandidate"},

    {"/api/v1/replay/iceServers"},

    {"/api/v1/replay/configuration"},

    {"/api/v1/replay/configuration",
     {{"coturnTurnUrlListWithSecret",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"stunUrlList",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"useTwilioStunTurn", JsonType::Boolean},
      {"twilioAccountSid", JsonType::String},
      {"twilioAuthToken", JsonType::String},
      {"useReverseProxy", JsonType::Boolean},
      {"reverseProxyServerAddress", JsonType::String},
      {"staticTurnUrlList",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]", JsonType::String, false, Format::NONE}}},
      {"useCoturnAuthSecret", JsonType::Boolean}}},

    {"/api/v1/replay/version"},

    {"/api/v1/replay/help"},

    {"/api/v1/replay/stream/add"},

    {"/api/v1/replay/stream/"},
};
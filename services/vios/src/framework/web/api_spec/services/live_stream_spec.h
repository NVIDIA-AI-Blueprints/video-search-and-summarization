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

const std::vector<ApiSpec> LIVE_API_SPEC = {
    {"/api/v1/live/streams"},

    {"/api/v1/live/stream/settings",
     {{"framerate", JsonType::Int},
      {"resolution", JsonType::String},
      {"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {
          "overlay",
          JsonType::Object,
          false,
          Format::NONE,
          {{"bbox",
            JsonType::Object,
            false,
            Format::NONE,
            {{"showAll", JsonType::Boolean},
             {"objectId",
              JsonType::Array,
              false,
              Format::NONE,
              {{"$[*]", JsonType::Int, false, Format::NONE}}},
             {"classType",
              JsonType::Array,
              false,
              Format::NONE,
              {{"$[*]", JsonType::String, false, Format::NONE}}},
             {"showObjId", JsonType::Boolean},
             {"objIdPosition", JsonType::Int},
             {"objIdTextColor", JsonType::String},
             {"objIdTextBGColor", JsonType::String},
            }},
           {"tripwire",
            JsonType::Object,
            false,
            Format::NONE,
            {{"showAll", JsonType::Boolean},
             {"id", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::Int, false, Format::NONE}}}}},
           {"roi",
            JsonType::Object,
            false,
            Format::NONE,
            {{"showAll", JsonType::Boolean},
             {"id", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::Int, false, Format::NONE}}}}},
           {"color", JsonType::String},
           {"thickness", JsonType::Int},
           {"opacity", JsonType::Int},
           {"debug", JsonType::Boolean}},
      }}},

    {"/api/v1/live/stream/query"},

    {"/api/v1/live/stream/start",
     {{"clientIpAddr", JsonType::String},
      {"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"streamId", JsonType::String, false, Format::NONE},
      {"tag", JsonType::String, false, Format::NONE},
      {
          "options",
          JsonType::Object,
          true,
          Format::NONE,
          {{"rtptransport", JsonType::String, true},
           {"timeout", JsonType::Int, true},
           {"quality", JsonType::String, true},
           {
               "composite",
               JsonType::Object,
               false,
               Format::NONE,
               {{"includeFloorPlan", JsonType::Boolean},
                {"doComposite", JsonType::Boolean},
                {"streamIds", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::String, false, Format::NONE}}},
                {"quality", JsonType::String},
                {
                    "showSensorName",
                    JsonType::Object,
                    false,
                    Format::NONE,
                    {{"enable", JsonType::Boolean},
                     {"position", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::Int, false, Format::NONE}}}},
                }},
           },
           {
               "overlay",
               JsonType::Object,
               false,
               Format::NONE,
               {{"objectId", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::Int, false, Format::NONE}}},
                {"color", JsonType::String},
                {"thickness", JsonType::Int},
                {"debug", JsonType::Boolean},
                {"needBbox", JsonType::Boolean},
                {"needTripwire", JsonType::Boolean},
                {"needRoi", JsonType::Boolean},
                {"opacity", JsonType::Int},
                {"framerate", JsonType::Int},
                {"proximityClass", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::String, false, Format::NONE}}},
                {"entrantClass", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::String, false, Format::NONE}}},
                {"proximityAreaFactor", JsonType::Number},
                {"overlayColorCode", JsonType::Array, false, Format::NONE, {{"$[*]", JsonType::Object, false, Format::NONE}}},
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

    {"/api/v1/live/stream/swap",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"streamId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/stop",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/pause",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/resume",
     {{"peerId", JsonType::String, true, Format::NOT_EMPTY},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/status"},

    {"/api/v1/live/stream/{streamId}/picture",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/{streamId}/picture/url",
     {{"streamId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/stream/stats"},

    {"/api/v1/live/setAnswer",
     {{"sessionDescription", JsonType::String, true},
      {"mediaSessionId", JsonType::String, true, Format::NOT_EMPTY}}},

    {"/api/v1/live/iceCandidate",
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

    {"/api/v1/live/iceCandidate"},

    {"/api/v1/live/iceServers"},

    {"/api/v1/live/configuration"},

    {"/api/v1/live/configuration",
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

    {"/api/v1/live/version"},

    {"/api/v1/live/help"},

    {"/api/v1/live/stream/add"},

    {"/api/v1/live/stream/"},
};
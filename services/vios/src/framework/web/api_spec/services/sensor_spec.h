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

const std::vector<ApiSpec> SENSOR_API_SPEC = {
    {"/api/v1/sensor/list"},

    {"/api/v1/sensor/scan"},

    {"/api/v1/sensor/{sensorId}/streams",
     {
         {"sensorId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/sensor/streams"},

    {"/api/v1/sensor/add",
     {
         {"username", JsonType::String, true},
         {"password", JsonType::String, true},
         {"name", JsonType::String},
         {"location", JsonType::String},
         {"hardware", JsonType::String},
         {"manufacturer", JsonType::String},
         {"serialNumber", JsonType::String},
         {"firmwareVersion", JsonType::String},
         {"hardwareId", JsonType::String},
         {"tags", JsonType::String},
         {"sensorIp", JsonType::String},
         {"sensorUrl", JsonType::String},
         {"verifyRtsp", JsonType::Boolean},
     }},

    {"/api/v1/sensor/status"},

    {"/api/v1/sensor/{sensorId}/status",
     {
         {"sensorId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/sensor/{inactiveSensorId}/replace",
     {
         {"inactiveSensorId", JsonType::String, true, Format::NONE},
         {"sensorId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/sensor/{sensorId}",
     {
         {"sensorId", JsonType::String, true, Format::NONE},
     }},

    {"/api/v1/sensor/{sensorId}/info",
     {
         {"sensorId", JsonType::String, true, Format::NONE},
         {"name", JsonType::String},
         {"location", JsonType::String},
         {"hardware", JsonType::String},
         {"manufacturer", JsonType::String},
         {"serialNumber", JsonType::String},
         {"firmwareVersion", JsonType::String},
         {"hardwareId", JsonType::String},
         {"tags", JsonType::String},
         {"sensorIp", JsonType::String},
         {
             "position",
             JsonType::Object,
             false,
             Format::NONE,
             {{"depth", JsonType::String},
              {"direction", JsonType::String},
              {"fieldOfView", JsonType::String},
              {
                  "coordinates",
                  JsonType::Object,
                  false,
                  Format::NONE,
                  {
                      {"x", JsonType::String},
                      {"y", JsonType::String},
                  },
              },
              {
                  "geoLocation",
                  JsonType::Object,
                  false,
                  Format::NONE,
                  {
                      {"latitude", JsonType::String},
                      {"longitude", JsonType::String},
                  },
              },
              {
                  "origin",
                  JsonType::Object,
                  false,
                  Format::NONE,
                  {
                      {"latitude", JsonType::String},
                      {"longitude", JsonType::String},
                  },
              }},
         },
     }},

    {"/api/v1/sensor/{sensorId}/settings",
     {{"sensorId", JsonType::String, true, Format::NONE},
      {
          "Encode",
          JsonType::Object,
          false,
          Format::NONE,
          {{"Bitrate", JsonType::String},
           {"Encoding", JsonType::String},
           {"EncodingInterval", JsonType::String},
           {"FrameRate", JsonType::String},
           {"GovLength", JsonType::String},
           {"Profiles", JsonType::String},
           {"Quality", JsonType::String},
           {
               "Resolution",
               JsonType::Object,
               false,
               Format::NONE,
               {{"Height", JsonType::String},
                {"Width", JsonType::String}},
           }},
      },
      {
          "Image",
          JsonType::Object,
          false,
          Format::NONE,
          {{"BacklightCompensationLevel", JsonType::String},
           {"BacklightCompensationMode", JsonType::String},
           {"Brightness", JsonType::String},
           {"ColorSaturation", JsonType::String},
           {"Contrast", JsonType::String},
           {"ExposureGain", JsonType::String},
           {"ExposureMaxGain", JsonType::String},
           {"ExposureMode", JsonType::String},
           {"ExposurePriority", JsonType::String},
           {"ExposureWindow", JsonType::String},
           {"ExposureTime", JsonType::String},
           {"IrCutFilterMode", JsonType::String},
           {"MinExposureTime", JsonType::String},
           {"MaxExposureTime", JsonType::String},
           {"Sharpness", JsonType::String},
           {"WhiteBalanceMode", JsonType::String},
           {"WhiteBalanceYbGain", JsonType::String},
           {"WhiteBalanceYrGain", JsonType::String},
           {"WideDynamicRangeLevel", JsonType::String},
           {"WideDynamicRangeMode", JsonType::String}},
      }}},

    {"/api/v1/sensor/{sensorId}/credentials",
     {{"sensorId", JsonType::String, true, Format::NONE},
      {"username", JsonType::String, true},
      {"password", JsonType::String, true}}},

    {"/api/v1/sensor/debug/unplug",
     {{"ip", JsonType::String, true},
      {"action", JsonType::String, true}}},

    {"/api/v1/sensor/debug/plug",
     {{"ip", JsonType::String, true},
      {"action", JsonType::String, true}}},

    {"/api/v1/sensor/configuration",
     {{"deviceDiscoveryInterfaces",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]",
         JsonType::String,
         false,
         Format::NONE}}},
      {"ntpServers",
       JsonType::Array,
       false,
       Format::NONE,
       {{"$[*]",
         JsonType::String,
         false,
         Format::NONE}}}}},

    {"/api/v1/sensor/debug/status"},

    {"/api/v1/sensor/qos"},

    {"/api/v1/sensor/debug/system/stats"},

    {"/api/v1/sensor/version"},

    {"/api/v1/sensor/help"},

    {"/api/v1/sensor/{sensorId}/network",
     {{"sensorId", JsonType::String, true, Format::NONE},
      {"dhcpV4", JsonType::String},
      {"dhcpV6", JsonType::String},
      {"ipAddressV4", JsonType::String},
      {"ipAddressV6", JsonType::String},
      {"isIpv4Enabled", JsonType::Boolean},
      {"isIpv6Enabled", JsonType::Boolean},
      {"subnetMaskV4", JsonType::String},
      {"subnetMaskV6", JsonType::String}}},

    {"/api/v1/sensor/{sensorId}/reboot",
     {{"sensorId", JsonType::String, true, Format::NONE}}},
};
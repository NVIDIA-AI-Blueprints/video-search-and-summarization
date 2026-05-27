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

#include <json/json.h>
#include <vector>
#include <string>
#include <mutex>
#include <unordered_map>
#include <functional>

enum class JsonType
{
    String,
    Number,
    Boolean,
    Object,
    Array,
    Int,
    UInt,
    Int64,
    Double,
    Null,
    StringOrNumber
};

enum class Format
{
    NONE,                   // No format needed
    NOT_EMPTY,              // Not empty
    UUID,                   // UUID
    COMMA_SEPARATED_STRING, // Comma separated string
    CRON_TIME               // Cron time format
};

struct FieldRule
{
    std::string json_path;
    JsonType type;
    bool required = false;
    Format format = Format::NONE;
    std::vector<FieldRule> children;
    std::function<bool(const Json::Value &)> customValidator = nullptr;
};

struct ApiSpec
{
    std::string api_path;
    std::vector<FieldRule> fields;
    std::vector<FieldRule> queryParams;  // Query parameter validation rules
};

class SchemaValidator
{
public:
    SchemaValidator() = delete;
    static bool validateRequest(const std::string &apiPath, const Json::Value &jsonData, const std::string &queryString = "");
    static bool validateWebSocketRequest(const std::string &apiPath, const Json::Value &jsonData);

private:
    static const std::vector<ApiSpec> API_SPEC;

    static bool validate(const Json::Value &data, const ApiSpec &spec);
    static bool checkJsonType(const Json::Value &val, JsonType type);
    static bool checkField(const Json::Value &node, const FieldRule &rule);
    static bool matchApiPath(const std::string &requestPath,
                             const std::string &specPath,
                             std::unordered_map<std::string, std::string> &params);
    static std::vector<std::string> splitPath(const std::string &path);
    static bool validationBypassHandler(const std::string &apiPath, const Json::Value &jsonData);
};

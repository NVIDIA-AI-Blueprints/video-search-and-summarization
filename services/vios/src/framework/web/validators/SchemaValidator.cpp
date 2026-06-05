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

#include "SchemaValidator.h"
#include "vst_api_spec.h"
#include <json/json.h>
#include <regex>
#include <logger.h>
#include <cstdlib>
#include "utils.h"
#include "profiler.h"

// Macro to check if API path matches streaming endpoints that need validation bypass
#define IS_STREAM_BYPASS_PATH(path) \
    ((path) == "/api/v1/live/stream/add" || (path) == "/api/v1/live/stream/" || \
     (path) == "/api/v1/replay/stream/add" || (path) == "/api/v1/replay/stream/" || \
     (path) == "/api/v1/proxy/stream/add" || (path) == "/api/v1/proxy/stream/" || \
     (path) == "/api/v1/record/stream/add" || (path) == "/api/v1/record/")

const std::vector<ApiSpec> SchemaValidator::API_SPEC = VST_API_SPEC;

bool SchemaValidator::validateWebSocketRequest(const std::string &apiPath, const Json::Value &jsonData)
{
    std::string webSocketApi = jsonData.get("apiKey", EMPTY_STRING).asString();
    if (webSocketApi == EMPTY_STRING)
    {
        // No API Key found - check if this is a response message with requestId
        std::string requestId = jsonData.get("requestId", EMPTY_STRING).asString();
        if (!requestId.empty())
        {
            // This is a response message with requestId - allow it to pass validation
            LOG(verbose2) << "WebSocket response message with requestId found: " << requestId << endl;
            return true;
        }
        else
        {
            // No apiKey and no requestId - invalid message
            LOG(error) << "WebSocket Message must have either API key or Request ID for API path: " << apiPath << endl;
            return false;
        }
    }
    // Regular WebSocket API request - validate the data field
    Json::Value websocketData = jsonData.get("data", Json::nullValue);
    return validateRequest("/" + webSocketApi, websocketData);
}

bool SchemaValidator::validateRequest(const std::string &apiPath, const Json::Value &jsonData, const std::string &queryString)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    // Starting validation request for path
    bool valid = false;
    for (const auto &spec : API_SPEC)
    {
        // Check API spec
        std::unordered_map<std::string, std::string> pathParams;
        if (matchApiPath(apiPath, spec.api_path, pathParams))
        {
            valid = true;
            Json::Value mergedData = jsonData;
            // Add a validationBypassHandler here to bypass validation for the request
            if (validationBypassHandler(apiPath, mergedData))
            {
                LOG(info) << "Validation bypassed for path: " << apiPath << endl;
                return true;
            }
            if (mergedData.isNull())
            {
                mergedData = Json::objectValue;
            }
            
            // For array request bodies, wrap in object to allow path parameter validation
            if (jsonData.isArray())
            {
                Json::Value wrapperObject = Json::objectValue;
                wrapperObject["$"] = mergedData; // Array becomes the root element
                mergedData = wrapperObject;
            }
            
            // Add all path params to merged data for validation
            for (const auto &[key, value] : pathParams)
            {
                mergedData[key] = value;
            }

            // Add query parameters to merged data for validation if query string is provided
            if (!queryString.empty())
            {
                Json::Value queryData = parseQueryStringToJson(queryString);
                for (const auto& memberName : queryData.getMemberNames())
                {
                    // Reject requests where query parameters conflict with request body fields (excluding path parameters)
                    if (mergedData.isMember(memberName) && pathParams.find(memberName) == pathParams.end())
                    {
                        LOG(error) << "Query parameter '" << memberName << "' conflicts with request body field. Request rejected." << endl;
                        valid = false;
                        break;
                    }
                    mergedData[memberName] = queryData[memberName];
                }
                if (!valid)
                {
                    continue;
                }
            }

            if (validate(mergedData, spec))
            {
                return true;
            }
            valid = false;
            continue;
        }
    }
    LOG(error) << "No matching API spec found for path: " << apiPath << endl;
    return false;
}

bool SchemaValidator::validate(const Json::Value &data, const ApiSpec &spec)
{
    // Validation for spec.api_path
    for (const auto &field : spec.fields)
    {
        // Validate each field
        if (!checkField(data, field))
        {
            LOG(error) << "Validation failed for API path: " << spec.api_path << ", field: " << field.json_path << endl;
            return false;
        }
        LOG(verbose2) << "Field validation passed: " << field.json_path << endl;
    }
    
    // Validate query parameters if defined
    for (const auto &queryParam : spec.queryParams)
    {
        // Validate each query parameter
        if (!checkField(data, queryParam))
        {
            LOG(error) << "Query parameter validation failed for API path: " << spec.api_path << ", parameter: " << queryParam.json_path << endl;
            return false;
        }
        LOG(verbose2) << "Query parameter validation passed: " << queryParam.json_path << endl;
    }
    
    LOG(verbose2) << "All fields and query parameters validated successfully for API path: " << spec.api_path << endl;
    return true;
}

bool SchemaValidator::matchApiPath(const std::string &requestPath,
                                   const std::string &specPath,
                                   std::unordered_map<std::string, std::string> &params)
{
    auto requestParts = splitPath(requestPath);
    auto specParts = splitPath(specPath);
    // Split the API path using '/' as delimiter. Compare size of splitted parts against the API spec
    if (requestParts.size() != specParts.size())
    {
        return false;
    }

    // Compare each split part against the API spec
    for (size_t i = 0; i < specParts.size(); ++i)
    {
        // Add any path params to JSON body
        if (specParts[i].find('{') == 0 &&
            specParts[i].rfind('}') == specParts[i].size() - 1)
        {
            std::string paramName = specParts[i].substr(1, specParts[i].size() - 2);
            params[paramName] = requestParts[i];
        }
        else if (specParts[i] != requestParts[i])
        {
            return false;
        }
    }
    // Path matching successful
    return true;
}

std::vector<std::string> SchemaValidator::splitPath(const std::string &path)
{
    std::vector<std::string> parts;
    std::stringstream ss(path);
    std::string part;

    while (std::getline(ss, part, '/'))
    {
        if (!part.empty())
        {
            parts.push_back(part);
        }
    }
    return parts;
}

bool SchemaValidator::checkJsonType(const Json::Value &val, JsonType type)
{
    switch (type)
    {
    case JsonType::String:
        return val.isString();
    case JsonType::Number:
        return val.isNumeric();
    case JsonType::Boolean:
        return val.isBool();
    case JsonType::Object:
        return val.isObject();
    case JsonType::Array:
        return val.isArray();
    case JsonType::Int:
        return val.isInt();
    case JsonType::UInt:
        return val.isUInt();
    case JsonType::Int64:
        return val.isInt64();
    case JsonType::Double:
        return val.isDouble();
    case JsonType::Null:
        return val.isNull();
    case JsonType::StringOrNumber:
        return val.isString() || val.isNumeric();
    default:
        return false;
    }
}

bool SchemaValidator::checkField(const Json::Value &node, const FieldRule &rule)
{
    Json::Value target;
    try
    {
        Json::Path path(rule.json_path);
        // Check if JSON key is present. If present get its value
        target = path.resolve(node);
    }
    catch (...)
    {
        LOG(error) << "Path resolution failed for " << rule.json_path << endl;
        // Return error if that key is marked as required in API Spec
        return !rule.required;
    }

    // Check if value is null
    if (target.isNull())
    {
        // Required field values must not be null
        if (rule.required || rule.format != Format::NONE)
        {
            LOG(error) << "Missing required field: " << rule.json_path << endl;
            return false;
        }
        // Optional field values can be missing or null
        LOG(verbose2) << "Optional field missing allowed: " << rule.json_path << endl;
        return true;
    }

    // Type validation for value
    if (!checkJsonType(target, rule.type))
    {
        LOG(error) << "Type validation failed for: " << rule.json_path << endl;
        return false;
    }

    // Format validation
    if (rule.format == Format::UUID)
    {
        static const std::regex uuid_regex(
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");
        if (!std::regex_match(target.asString(), uuid_regex))
        {
            LOG(error) << "UUID validation failed: " << target.asString() << endl;
            return false;
        }
    }

    // Run custom validator if any
    if (rule.customValidator)
    {
        LOG(info) << "Running custom validator for: " << rule.json_path << endl;
        if (!rule.customValidator(target))
        {
            LOG(error) << "Custom validation failed for: " << rule.json_path << endl;
            return false;
        }
        LOG(info) << "Custom validation passed for: " << rule.json_path << endl;
    }

    // If JSON is nested then check its children
    for (const auto &child : rule.children)
    {
        if (rule.type == JsonType::Array)
        {
            for (Json::ArrayIndex i = 0; i < target.size(); ++i)
            {
                if (!checkField(target[i], child))
                {
                    LOG(error) << "Array element validation failed at index " << i << endl;
                    return false;
                }
            }
        }
        else
        {
            if (!checkField(target, child))
            {
                LOG(error) << "Nested object validation failed" << endl;
                return false;
            }
        }
    }
    // Field validation passed
    return true;
}

/*
 * Bypass validation for specific live, replay, proxy, and record stream APIs with camera status change alerts.
 * This is to avoid validation of internal APIs used by SDR.
 */
bool SchemaValidator::validationBypassHandler(const std::string &apiPath, const Json::Value &jsonData)
{
    LOG(verbose2) << "validationBypassHandler called for path: " << apiPath << ", jsonData type: " 
                  << (jsonData.isNull() ? "null" : jsonData.isObject() ? "object" : "other") << endl;
    
    if (IS_STREAM_BYPASS_PATH(apiPath))
    {
        LOG(verbose2) << "Path matches bypass criteria, checking bypass conditions" << endl;
        
        // Case 1: Bypass for DELETE requests without JSON body (common for stream deletion)
        if (jsonData.isNull() || (jsonData.isObject() && jsonData.getMemberNames().empty()))
        {
            LOG(verbose) << "Validation bypassed for stream API with no JSON body (likely DELETE request)" << endl;
            return true;
        }
        
        // Case 2: Bypass for requests with alert_type field (camera status change alerts)
        if (jsonData.isObject() && jsonData.isMember("alert_type") && jsonData["alert_type"].isString())
        {
            std::string alertType = jsonData["alert_type"].asString();
            if (alertType == "camera_status_change")
            {
                LOG(verbose) << "Validation bypassed for stream API with alert_type: " << alertType << endl;
                return true;
            }
        }
        
        LOG(warning) << "No bypass criteria met for JSON data" << endl;
    }
    else
    {
        LOG(verbose2) << "Path does not match bypass criteria" << endl;
    }
    
    return false;
}

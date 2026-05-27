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

#include "cloud_manager.h"
#include "../unified_storage_types.h"
#include "unified_storage_manager.h"
#include <iostream>
#include <sstream>
#include <iomanip>
#include <ctime>
#include <regex>

namespace nv_vms
{

CloudManager::CloudManager()
    : m_initialized(false)
{
}

CloudManager::~CloudManager()
{
}

bool CloudManager::configure(const CloudManagerConfig& config)
{
    // Validate the configuration first
    if (!validateConfiguration(config))
    {
        return false;
    }
    
    // Store the configuration
    m_config = config;
    
    // Mark as initialized after successful configuration
    m_initialized = true;
    
    return true;
}

void CloudManager::updateStats(bool success, std::chrono::milliseconds latency, 
                              const std::string& errorCode)
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats.recordRequest(success, latency, errorCode);
}

void CloudManager::setLastError(const std::string& error)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_last_error = error;
}

std::string CloudManager::formatTimestamp(const std::string& timestamp) const
{
    if (timestamp.empty()) {
        return "ERROR: Empty timestamp";
    }
    
    // Define common ISO 8601 / RFC 3339 format patterns
    const std::vector<std::string> format_patterns = {
        "%Y-%m-%dT%H:%M:%S",           // 2023-12-25T14:30:00
        "%Y-%m-%dT%H:%M:%SZ",          // 2023-12-25T14:30:00Z
        "%Y-%m-%dT%H:%M:%S%z",         // 2023-12-25T14:30:00+00:00
        "%Y-%m-%dT%H:%M:%S.%3NZ",      // 2023-12-25T14:30:00.123Z
        "%Y-%m-%dT%H:%M:%S.%6NZ",      // 2023-12-25T14:30:00.123456Z
        "%Y-%m-%dT%H:%M:%S.%9NZ",      // 2023-12-25T14:30:00.123456789Z
        "%Y-%m-%dT%H:%M:%S.%3N%z",     // 2023-12-25T14:30:00.123+00:00
        "%Y-%m-%dT%H:%M:%S.%6N%z",     // 2023-12-25T14:30:00.123456+00:00
        "%Y-%m-%dT%H:%M:%S.%9N%z",     // 2023-12-25T14:30:00.123456789+00:00
        "%Y-%m-%d %H:%M:%S",           // 2023-12-25 14:30:00
        "%Y-%m-%d %H:%M:%S%z",         // 2023-12-25 14:30:00+00:00
        "%Y-%m-%d %H:%M:%S.%3N",       // 2023-12-25 14:30:00.123
        "%Y-%m-%d %H:%M:%S.%6N",       // 2023-12-25 14:30:00.123456
        "%Y-%m-%d %H:%M:%S.%9N",       // 2023-12-25 14:30:00.123456789
        "%Y-%m-%d %H:%M:%S.%3N%z",     // 2023-12-25 14:30:00.123+00:00
        "%Y-%m-%d %H:%M:%S.%6N%z",     // 2023-12-25 14:30:00.123456+00:00
        "%Y-%m-%d %H:%M:%S.%9N%z"      // 2023-12-25 14:30:00.123456789+00:00
    };
    
    std::tm tm = {};
    std::istringstream ss(timestamp);
    
    // Try each format pattern
    for (const auto& format : format_patterns) {
        ss.clear();
        ss.seekg(0);
        
        // Handle timezone offset parsing manually for formats with %z
        if (format.find("%z") != std::string::npos) {
            // Extract the timezone part and parse separately
            std::string time_part = timestamp;
            std::string tz_part;
            
            // Find timezone offset (starts with + or - after the time)
            size_t tz_pos = time_part.find_last_of("+-");
            if (tz_pos != std::string::npos && tz_pos > 10) { // Ensure it's after the date/time
                tz_part = time_part.substr(tz_pos);
                time_part = time_part.substr(0, tz_pos);
                
                // Parse the time part without timezone
                std::istringstream time_ss(time_part);
                std::string time_format = format.substr(0, format.find("%z"));
                time_ss >> std::get_time(&tm, time_format.c_str());
                
                if (!time_ss.fail()) {
                    // Successfully parsed the time part
                    std::ostringstream formatted;
                    formatted << std::put_time(&tm, "%Y-%m-%d %H:%M:%S");
                    if (!tz_part.empty()) {
                        formatted << " " << tz_part;
                    }
                    return formatted.str();
                }
            }
        } else {
            // Standard format parsing
            ss >> std::get_time(&tm, format.c_str());
            
            if (!ss.fail()) {
                std::ostringstream formatted;
                formatted << std::put_time(&tm, "%Y-%m-%d %H:%M:%S");
                return formatted.str();
            }
        }
    }
    
    // If all parsing attempts failed, return error message with original input
    return "ERROR: Unsupported timestamp format: " + timestamp;
}

bool CloudManager::validateConfiguration(const CloudManagerConfig& config) const
{
    if (config.storageType == CloudStorageType::UNKNOWN) {
        return false;
    }
    
    if (config.endpoint.empty()) {
        return false;
    }
    
    if (config.accessKeyId.empty() || config.secretAccessKey.empty()) {
        return false;
    }
    
    if (config.timeoutSeconds == 0) {
        return false;
    }
    
    return true;
}

bool CloudManager::validateEndpointUrl(const std::string& endpointUrl) const
{
    if (endpointUrl.empty()) {
        return false;
    }
    
    // Basic URL validation
    std::regex url_pattern("^(http|https)://[a-zA-Z0-9.-]+(:[0-9]+)?(/.*)?$");
    return std::regex_match(endpointUrl, url_pattern);
}

Json::Value CloudManager::bucketInfoToJson(const BucketInfo& bucketInfo) const
{
    Json::Value json;
    json["name"] = bucketInfo.name;
    json["region"] = bucketInfo.region;
    json["creationDate"] = bucketInfo.creationDate;
    json["objectCount"] = Json::UInt64(bucketInfo.objectCount);
    json["totalSize"] = Json::UInt64(bucketInfo.totalSize);
    
    Json::Value metadata;
    for (const auto& pair : bucketInfo.metadata) {
        metadata[pair.first] = pair.second;
    }
    json["metadata"] = metadata;
    
    return json;
}

Json::Value CloudManager::statsToJson(const CloudManagerStats& stats) const
{
    Json::Value json;
    json["totalRequests"] = Json::UInt64(stats.totalRequests);
    json["successfulRequests"] = Json::UInt64(stats.successfulRequests);
    json["failedRequests"] = Json::UInt64(stats.failedRequests);
    json["objectsDeleted"] = Json::UInt64(stats.objectsDeleted);
    json["bucketsCreated"] = Json::UInt64(stats.bucketsCreated);
    json["bucketsDeleted"] = Json::UInt64(stats.bucketsDeleted);
    json["totalLatencyMs"] = Json::UInt64(stats.totalLatency.count());
    json["averageLatencyMs"] = Json::UInt64(stats.averageLatency.count());
    
    Json::Value errorCounts;
    for (const auto& pair : stats.errorCounts) {
        errorCounts[pair.first] = Json::UInt(pair.second);
    }
    json["errorCounts"] = errorCounts;
    
    return json;
}

} // namespace nv_vms 
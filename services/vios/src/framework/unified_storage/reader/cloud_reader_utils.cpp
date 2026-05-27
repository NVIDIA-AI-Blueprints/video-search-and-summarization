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

#include "cloud_reader_utils.h"
#include "logger.h"
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <random>
#include <regex>
#include <sstream>

namespace nv_vms
{
namespace CloudReaderUtils
{

bool isValidObjectName(const std::string& object_name)
{
    if (object_name.empty())
    {
        return false;
    }
    if (object_name.length() > MAX_OBJECT_NAME_LENGTH)
    {
        return false;
    }

    // Check for invalid path patterns
    for (const auto& pattern : INVALID_PATH_PATTERNS)
    {
        if (object_name.find(pattern) != std::string::npos)
        {
            return false;
        }
    }

    // Additional security checks
    if (object_name.find('\0') != std::string::npos)
    {
        return false; // Null byte injection
    }
    if (object_name.find("..") != std::string::npos)
    {
        return false; // Path traversal
    }

    // Check for control characters
    for (char c : object_name)
    {
        if (std::iscntrl(c))
        {
            return false;
        }
    }

    // Check for leading/trailing slashes or whitespace
    if (object_name.front() == '/' || object_name.back() == '/' || std::isspace(object_name.front()) ||
        std::isspace(object_name.back()))
    {
        return false;
    }

    return true;
}

bool isValidBucketName(const std::string& bucket_name)
{
    if (bucket_name.empty() || bucket_name.length() < MIN_BUCKET_NAME_LENGTH ||
        bucket_name.length() > MAX_BUCKET_NAME_LENGTH)
    {
        return false;
    }

    // Check for reserved patterns
    for (const auto& pattern : RESERVED_BUCKET_PATTERNS)
    {
        if (bucket_name.find(pattern) == 0)
        {
            return false;
        }
    }

    // Must start and end with letter or number
    if (!std::isalnum(bucket_name.front()) || !std::isalnum(bucket_name.back()))
    {
        return false;
    }

    // Can contain letters, numbers, hyphens, and dots
    for (char c : bucket_name)
    {
        if (!std::isalnum(c) && c != '-' && c != '.')
        {
            return false;
        }
    }

    // Cannot contain consecutive dots or hyphens
    for (size_t i = 1; i < bucket_name.length(); ++i)
    {
        if ((bucket_name[i] == '.' && bucket_name[i - 1] == '.') ||
            (bucket_name[i] == '-' && bucket_name[i - 1] == '-'))
        {
            return false;
        }
    }

    return true;
}

bool isValidFilePath(const std::string& file_path)
{
    if (file_path.empty() || file_path.length() > MAX_PATH_LENGTH)
    {
        return false;
    }

    // Check for invalid patterns
    for (const auto& pattern : INVALID_PATH_PATTERNS)
    {
        if (file_path.find(pattern) != std::string::npos)
        {
            return false;
        }
    }

    // Check for null bytes
    if (file_path.find('\0') != std::string::npos)
    {
        return false;
    }

    // Check for control characters
    for (char c : file_path)
    {
        if (std::iscntrl(c))
        {
            return false;
        }
    }

    return true;
}

std::string sanitizeObjectName(const std::string& object_name)
{
    std::string sanitized = object_name;

    // Remove leading/trailing whitespace and slashes
    sanitized = trimString(sanitized);
    while (sanitized.front() == '/')
        sanitized.erase(0, 1);
    while (sanitized.back() == '/')
        sanitized.pop_back();

    // Replace invalid characters
    for (char& c : sanitized)
    {
        if (std::iscntrl(c) || c == '\0')
        {
            c = '_';
        }
    }

    // Replace invalid patterns
    for (const auto& pattern : INVALID_PATH_PATTERNS)
    {
        size_t pos = 0;
        while ((pos = sanitized.find(pattern, pos)) != std::string::npos)
        {
            sanitized.replace(pos, pattern.length(), "_");
            pos += 1;
        }
    }

    return sanitized;
}

std::string sanitizeBucketName(const std::string& bucket_name)
{
    std::string sanitized = toLowerCase(trimString(bucket_name));

    // Remove invalid characters
    sanitized = removeInvalidCharacters(sanitized);

    // Ensure it starts and ends with alphanumeric
    while (!sanitized.empty() && !std::isalnum(sanitized.front()))
    {
        sanitized.erase(0, 1);
    }
    while (!sanitized.empty() && !std::isalnum(sanitized.back()))
    {
        sanitized.pop_back();
    }

    // Limit length
    if (sanitized.length() > MAX_BUCKET_NAME_LENGTH)
    {
        sanitized = sanitized.substr(0, MAX_BUCKET_NAME_LENGTH);
    }

    return sanitized;
}

std::string getFileExtension(const std::string& object_key)
{
    std::filesystem::path path(object_key);
    std::string extension = path.extension().string();

    // Remove the dot
    if (!extension.empty() && extension.front() == '.')
    {
        extension = extension.substr(1);
    }

    return toLowerCase(extension);
}

std::string getContentTypeFromExtension(const std::string& object_key)
{
    std::string extension = getFileExtension(object_key);

    // Common content types
    static const std::map<std::string, std::string, std::less<>> content_types = {
        {"mp4", "video/mp4"},         {"mkv", "video/x-matroska"}, {"avi", "video/x-msvideo"},
        {"mov", "video/quicktime"},   {"wmv", "video/x-ms-wmv"},   {"flv", "video/x-flv"},
        {"webm", "video/webm"},       {"jpg", "image/jpeg"},       {"jpeg", "image/jpeg"},
        {"png", "image/png"},         {"gif", "image/gif"},        {"bmp", "image/bmp"},
        {"tiff", "image/tiff"},       {"txt", "text/plain"},       {"json", "application/json"},
        {"xml", "application/xml"},   {"pdf", "application/pdf"},  {"zip", "application/zip"},
        {"tar", "application/x-tar"}, {"gz", "application/gzip"}};

    auto it = content_types.find(extension);
    return (it != content_types.end()) ? it->second : "application/octet-stream";
}

std::string formatFileSize(uint64_t bytes)
{
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int unit_index = 0;
    double size = static_cast<double>(bytes);

    while (size >= 1024.0 && unit_index < 4)
    {
        size /= 1024.0;
        unit_index++;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2) << size << " " << units[unit_index];
    return oss.str();
}

std::string formatDuration(std::chrono::milliseconds duration)
{
    auto total_ms = duration.count();
    auto seconds = total_ms / 1000;
    auto minutes = seconds / 60;
    auto hours = minutes / 60;

    std::ostringstream oss;

    if (hours > 0)
    {
        oss << hours << "h " << (minutes % 60) << "m " << (seconds % 60) << "s";
    }
    else if (minutes > 0)
    {
        oss << minutes << "m " << (seconds % 60) << "s";
    }
    else if (seconds > 0)
    {
        oss << seconds << "s";
    }
    else
    {
        oss << total_ms << "ms";
    }

    return oss.str();
}

double calculateTransferSpeed(uint64_t bytes, std::chrono::milliseconds duration)
{
    if (duration.count() == 0)
    {
        return 0.0;
    }

    double duration_seconds = duration.count() / 1000.0;
    return (bytes / (1024.0 * 1024.0)) / duration_seconds; // MB/s
}

std::string formatTimestamp(const std::string& timestamp)
{
    // Try to parse and format timestamp
    // This is a basic implementation - can be enhanced for specific formats
    if (timestamp.empty())
    {
        return "";
    }

    return timestamp;
}

std::string generateSessionId(const std::string& prefix)
{
    static std::random_device rd;
    static std::mt19937 gen(rd());
    static std::uniform_int_distribution<> dis(0, 15);

    std::ostringstream oss;
    if (!prefix.empty())
    {
        oss << prefix << "_";
    }

    oss << std::hex;
    for (int i = 0; i < 8; ++i)
    {
        oss << dis(gen);
    }
    oss << "_";
    for (int i = 0; i < 4; ++i)
    {
        oss << dis(gen);
    }
    oss << "_";
    for (int i = 0; i < 4; ++i)
    {
        oss << dis(gen);
    }
    oss << "_";
    for (int i = 0; i < 4; ++i)
    {
        oss << dis(gen);
    }
    oss << "_";
    for (int i = 0; i < 12; ++i)
    {
        oss << dis(gen);
    }

    return oss.str();
}

bool ensureDirectoryExists(const std::string& path)
{
    try
    {
        std::filesystem::create_directories(path);
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to create directory: " << path << " - " << e.what() << std::endl;
        return false;
    }
}

bool isFileAccessible(const std::string& file_path)
{
    try
    {
        return std::filesystem::exists(file_path) && std::filesystem::is_regular_file(file_path);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error checking file accessibility: " << file_path << " - " << e.what() << std::endl;
        return false;
    }
}

uint64_t getFileSize(const std::string& file_path)
{
    try
    {
        if (std::filesystem::exists(file_path))
        {
            return std::filesystem::file_size(file_path);
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error getting file size: " << file_path << " - " << e.what() << std::endl;
    }
    return 0;
}

bool isValidUrl(const std::string& url)
{
    if (url.empty())
    {
        return false;
    }

    // Basic URL validation
    std::regex url_pattern(R"(^(https?|ftp)://[^\s/$.?#].[^\s]*$)");
    return std::regex_match(url, url_pattern);
}

bool parseEndpointUrl(const std::string& endpoint_url, std::string& host, unsigned int& port, bool& use_ssl)
{
    if (endpoint_url.empty())
    {
        return false;
    }

    std::string url = endpoint_url;

    // Determine protocol
    if (url.find("https://") == 0)
    {
        use_ssl = true;
        url = url.substr(8);
    }
    else if (url.find("http://") == 0)
    {
        use_ssl = false;
        url = url.substr(7);
    }
    else
    {
        // Default to HTTPS for cloud storage
        use_ssl = true;
    }

    // Parse host and port
    size_t colon_pos = url.find(':');
    if (colon_pos != std::string::npos)
    {
        try
        {
            host = url.substr(0, colon_pos);
            port = std::stoi(url.substr(colon_pos + 1));
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Failed to parse port from URL: " << endpoint_url << " - " << e.what() << std::endl;
            return false;
        }
    }
    else
    {
        host = url;
        port = use_ssl ? 443 : 80;
    }

    return true;
}

std::string buildEndpointUrl(const std::string& host, unsigned int& port, bool use_ssl)
{
    std::ostringstream oss;
    oss << (use_ssl ? "https://" : "http://") << host;

    // Only add port if it's not the default
    if ((use_ssl && port != 443) || (!use_ssl && port != 80))
    {
        oss << ":" << port;
    }

    return oss.str();
}

std::string normalizeRegion(const std::string& region)
{
    std::string normalized = toLowerCase(trimString(region));

    // Remove invalid characters
    normalized = removeInvalidCharacters(normalized);

    // Replace spaces with hyphens
    std::replace(normalized.begin(), normalized.end(), ' ', '-');

    return normalized;
}

bool containsValidCharacters(const std::string& str)
{
    for (char c : str)
    {
        if (std::iscntrl(c) || c == '\0')
        {
            return false;
        }
    }
    return true;
}

std::string removeInvalidCharacters(const std::string& str)
{
    std::string result;
    result.reserve(str.length());

    for (char c : str)
    {
        if (!std::iscntrl(c) && c != '\0')
        {
            result += c;
        }
    }

    return result;
}

std::string toLowerCase(const std::string& str)
{
    std::string result = str;
    std::transform(result.begin(), result.end(), result.begin(), ::tolower);
    return result;
}

std::string toUpperCase(const std::string& str)
{
    std::string result = str;
    std::transform(result.begin(), result.end(), result.begin(), ::toupper);
    return result;
}

std::string trimString(const std::string& str)
{
    size_t start = str.find_first_not_of(" \t\n\r\f\v");
    if (start == std::string::npos)
    {
        return "";
    }

    size_t end = str.find_last_not_of(" \t\n\r\f\v");
    return str.substr(start, end - start + 1);
}

std::vector<std::string> splitString(const std::string& str, char delimiter)
{
    std::vector<std::string> result;
    std::stringstream ss(str);
    std::string item;

    while (std::getline(ss, item, delimiter))
    {
        result.push_back(item);
    }

    return result;
}

std::string joinStrings(const std::vector<std::string>& strings, char delimiter)
{
    if (strings.empty())
    {
        return "";
    }

    std::ostringstream oss;
    for (size_t i = 0; i < strings.size(); ++i)
    {
        if (i > 0)
        {
            oss << delimiter;
        }
        oss << strings[i];
    }

    return oss.str();
}

std::string generateFileHash(const std::string& file_path)
{
    try
    {
        std::ifstream file(file_path, std::ios::binary);
        if (!file.is_open())
        {
            return "";
        }

        // Simple hash calculation (can be enhanced with proper hash algorithms)
        std::hash<std::string> hasher;
        std::string content((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());

        return std::to_string(hasher(content));
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error generating file hash: " << file_path << " - " << e.what() << std::endl;
        return "";
    }
}

bool validateConfiguration(const std::map<std::string, std::string, std::less<>>& config,
                           const std::vector<std::string>& required_keys)
{
    for (const auto& key : required_keys)
    {
        if (config.find(key) == config.end() || config.at(key).empty())
        {
            LOG(error) << "Missing required configuration key: " << key << std::endl;
            return false;
        }
    }
    return true;
}

void logOperationStats(const std::string& operation, bool success, std::chrono::milliseconds duration,
                       uint64_t bytes_transferred, const std::string& error_message)
{
    std::ostringstream oss;
    oss << "Operation: " << operation << " | Status: " << (success ? "SUCCESS" : "FAILED")
        << " | Duration: " << formatDuration(duration);

    if (bytes_transferred > 0)
    {
        oss << " | Bytes: " << formatFileSize(bytes_transferred);
        double speed = calculateTransferSpeed(bytes_transferred, duration);
        if (speed > 0)
        {
            oss << " | Speed: " << std::fixed << std::setprecision(2) << speed << " MB/s";
        }
    }

    if (!error_message.empty())
    {
        oss << " | Error: " << error_message;
    }

    if (success)
    {
        LOG(info) << oss.str() << std::endl;
    }
    else
    {
        LOG(error) << oss.str() << std::endl;
    }
}

} // namespace CloudReaderUtils
} // namespace nv_vms
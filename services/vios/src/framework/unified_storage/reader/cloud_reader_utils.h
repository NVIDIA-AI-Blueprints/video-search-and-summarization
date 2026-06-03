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

#include <string>
#include <vector>
#include <map>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <filesystem>

namespace nv_vms {

/**
 * @brief Utility functions for cloud storage operations
 * This namespace contains shared functionality that can be used across
 * all cloud storage types (S3, MinIO, Azure, Google Cloud, etc.)
 */
namespace CloudReaderUtils {

// Constants for cloud operations
constexpr size_t MAX_OBJECT_NAME_LENGTH = 1024;
constexpr size_t MAX_BUCKET_NAME_LENGTH = 63;
constexpr size_t MIN_BUCKET_NAME_LENGTH = 3;
constexpr size_t MAX_PATH_LENGTH = 2048;

// Invalid patterns for security validation
const std::vector<std::string> INVALID_PATH_PATTERNS = {
    "..", "//", "\\", "~", "..\\", "../"
};

// Reserved bucket names and patterns
const std::vector<std::string> RESERVED_BUCKET_PATTERNS = {
    "xn--", "sthree-", "sthree-configurator", "s3alias", "aws-"
};

/**
 * @brief Validates object name/key for cloud storage
 * @param object_name The object name/key to validate
 * @return true if valid, false otherwise
 */
bool isValidObjectName(const std::string& object_name);

/**
 * @brief Validates bucket name for cloud storage
 * @param bucket_name The bucket name to validate
 * @return true if valid, false otherwise
 */
bool isValidBucketName(const std::string& bucket_name);

/**
 * @brief Validates file path for local operations
 * @param file_path The file path to validate
 * @return true if valid, false otherwise
 */
bool isValidFilePath(const std::string& file_path);

/**
 * @brief Sanitizes object name/key for cloud storage
 * @param object_name The object name to sanitize
 * @return Sanitized object name
 */
std::string sanitizeObjectName(const std::string& object_name);

/**
 * @brief Sanitizes bucket name for cloud storage
 * @param bucket_name The bucket name to sanitize
 * @return Sanitized bucket name
 */
std::string sanitizeBucketName(const std::string& bucket_name);

/**
 * @brief Extracts file extension from object key
 * @param object_key The object key
 * @return File extension (without dot)
 */
std::string getFileExtension(const std::string& object_key);

/**
 * @brief Determines content type based on file extension
 * @param object_key The object key
 * @return MIME content type
 */
std::string getContentTypeFromExtension(const std::string& object_key);

/**
 * @brief Formats file size in human-readable format
 * @param bytes File size in bytes
 * @return Formatted string (e.g., "1.5 MB", "2.3 GB")
 */
std::string formatFileSize(uint64_t bytes);

/**
 * @brief Formats duration in human-readable format
 * @param duration Duration in milliseconds
 * @return Formatted string (e.g., "1.5s", "2m 30s")
 */
std::string formatDuration(std::chrono::milliseconds duration);

/**
 * @brief Calculates transfer speed in MB/s
 * @param bytes Number of bytes transferred
 * @param duration Duration of transfer in milliseconds
 * @return Transfer speed in MB/s
 */
double calculateTransferSpeed(uint64_t bytes, std::chrono::milliseconds duration);

/**
 * @brief Converts timestamp to ISO 8601 format
 * @param timestamp Raw timestamp string
 * @return ISO 8601 formatted timestamp
 */
std::string formatTimestamp(const std::string& timestamp);

/**
 * @brief Generates a unique session ID
 * @param prefix Optional prefix for the session ID
 * @return Unique session ID
 */
std::string generateSessionId(const std::string& prefix = "");

/**
 * @brief Creates directory path if it doesn't exist
 * @param path Directory path to create
 * @return true if successful, false otherwise
 */
bool ensureDirectoryExists(const std::string& path);

/**
 * @brief Checks if file exists and is accessible
 * @param file_path Path to the file
 * @return true if file exists and is accessible, false otherwise
 */
bool isFileAccessible(const std::string& file_path);

/**
 * @brief Gets file size in bytes
 * @param file_path Path to the file
 * @return File size in bytes, 0 if error
 */
uint64_t getFileSize(const std::string& file_path);

/**
 * @brief Validates URL format
 * @param url URL to validate
 * @return true if valid URL format, false otherwise
 */
bool isValidUrl(const std::string& url);

/**
 * @brief Extracts host and port from endpoint URL
 * @param endpoint_url The endpoint URL
 * @param host Output host name
 * @param port Output port number
 * @param use_ssl Output SSL flag
 * @return true if successful, false otherwise
 */
bool parseEndpointUrl(const std::string& endpoint_url, 
                     std::string& host, 
                     unsigned int& port, 
                     bool& use_ssl);

/**
 * @brief Builds endpoint URL from components
 * @param host Host name
 * @param port Port number
 * @param use_ssl Whether to use SSL
 * @return Complete endpoint URL
 */
std::string buildEndpointUrl(const std::string& host, 
                            unsigned int& port, 
                            bool use_ssl);

/**
 * @brief Validates and normalizes region name
 * @param region Region name to validate
 * @return Normalized region name
 */
std::string normalizeRegion(const std::string& region);

/**
 * @brief Checks if string contains only valid characters for cloud storage
 * @param str String to check
 * @return true if valid, false otherwise
 */
bool containsValidCharacters(const std::string& str);

/**
 * @brief Removes invalid characters from string
 * @param str String to clean
 * @return Cleaned string
 */
std::string removeInvalidCharacters(const std::string& str);

/**
 * @brief Converts string to lowercase
 * @param str String to convert
 * @return Lowercase string
 */
std::string toLowerCase(const std::string& str);

/**
 * @brief Converts string to uppercase
 * @param str String to convert
 * @return Uppercase string
 */
std::string toUpperCase(const std::string& str);

/**
 * @brief Trims whitespace from string
 * @param str String to trim
 * @return Trimmed string
 */
std::string trimString(const std::string& str);

/**
 * @brief Splits string by delimiter
 * @param str String to split
 * @param delimiter Delimiter character
 * @return Vector of substrings
 */
std::vector<std::string> splitString(const std::string& str, char delimiter);

/**
 * @brief Joins vector of strings with delimiter
 * @param strings Vector of strings
 * @param delimiter Delimiter character
 * @return Joined string
 */
std::string joinStrings(const std::vector<std::string>& strings, char delimiter);

/**
 * @brief Generates ETag-like hash for local file
 * @param file_path Path to the file
 * @return ETag-like hash string
 */
std::string generateFileHash(const std::string& file_path);

/**
 * @brief Validates configuration parameters
 * @param config Configuration map
 * @param required_keys Vector of required keys
 * @return true if all required keys are present, false otherwise
 */
bool validateConfiguration(const std::map<std::string, std::string, std::less<>>& config,
                          const std::vector<std::string>& required_keys);

/**
 * @brief Logs operation statistics
 * @param operation Operation name
 * @param success Whether operation was successful
 * @param duration Operation duration
 * @param bytes_transferred Number of bytes transferred
 * @param error_message Error message if failed
 */
void logOperationStats(const std::string& operation,
                      bool success,
                      std::chrono::milliseconds duration,
                      uint64_t bytes_transferred = 0,
                      const std::string& error_message = "");

} // namespace CloudReaderUtils

} // namespace nv_vms 
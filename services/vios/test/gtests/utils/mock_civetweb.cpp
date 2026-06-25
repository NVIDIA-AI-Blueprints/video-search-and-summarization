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

/**
 * @file mock_civetweb.cpp
 * @brief Mock CivetWeb function implementations for unit testing
 * 
 * OVERVIEW:
 * This file provides mock implementations that override CivetWeb library functions.
 * By linking these objects BEFORE libcivetweb.a and using --allow-multiple-definition,
 * our mock functions take precedence over the real library functions.
 * 
 * HOW IT WORKS:
 * 1. Makefile adds: -Wl,--allow-multiple-definition
 * 2. Test object files (including this mock) link BEFORE libcivetweb.a
 * 3. Linker uses FIRST definition (our mocks) and ignores library versions
 * 4. Result: Unit tests can run without real HTTP server
 * 
 * MOCKED FUNCTIONS:
 * - mg_get_request_info: Returns mock request info from MockConnection
 * - mg_handle_form_request: Returns success without parsing (for unit tests)
 * - mg_get_header: Looks up headers from mock request info
 * - mg_read: Reads REAL file data if loaded, otherwise generates fake data
 * 
 * FEATURES:
 * - Can load and upload REAL video files
 * - Validates complete upload workflow including media validation
 * - Multipart form data is NOT parsed (POST tests skip file content)
 * - PUT tests upload actual file content for complete validation
 */

#include "mock_civetweb.h"

// Mock version identifier
const char* MOCK_VERSION = "MOCK_CIVET_V1.0";

// ==================== MockRequestInfo Implementation ====================

MockRequestInfo::MockRequestInfo()
{
    memset(&info, 0, sizeof(mg_request_info));
    info.request_method = nullptr;
    info.request_uri = nullptr;
    info.query_string = nullptr;
    info.http_version = "1.1";
    info.content_length = 0;
    info.num_headers = 0;
    
    // Pre-reserve space to prevent reallocation and pointer invalidation
    header_names.reserve(20);
    header_values.reserve(20);
}

void MockRequestInfo::setMethod(const std::string& method)
{
    request_method_str = method;
    info.request_method = request_method_str.c_str();
}

void MockRequestInfo::setUri(const std::string& uri)
{
    request_uri_str = uri;
    info.request_uri = request_uri_str.c_str();
}

void MockRequestInfo::setQueryString(const std::string& query)
{
    query_string_str = query;
    info.query_string = query_string_str.c_str();
}

void MockRequestInfo::setContentLength(long long length)
{
    info.content_length = length;
}

void MockRequestInfo::addHeader(const std::string& name, const std::string& value)
{
    if (info.num_headers >= MG_MAX_HEADERS)
    {
        std::cerr << "Warning: Max headers reached" << std::endl;
        return;
    }
    
    header_names.push_back(name);
    header_values.push_back(value);
    
    // CRITICAL FIX: Refresh ALL pointers after adding (in case of reallocation)
    refreshHeaderPointers();
}

void MockRequestInfo::refreshHeaderPointers()
{
    for (int i = 0; i < static_cast<int>(header_names.size()); i++)
    {
        info.http_headers[i].name = header_names[i].c_str();
        info.http_headers[i].value = header_values[i].c_str();
    }
    info.num_headers = static_cast<int>(header_names.size());
}

// ==================== MockConnection Implementation ====================

MockConnection::MockConnection() 
    : user_data(nullptr), uploadBytesRead(0), fakeBytesRead(0)
{
}

bool MockConnection::loadFileForUpload(const std::string& filePath)
{
    std::ifstream file(filePath, std::ios::binary | std::ios::ate);
    if (!file.is_open())
    {
        std::cerr << "[MOCK] Failed to open file: " << filePath << std::endl;
        return false;
    }
    
    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);
    
    uploadFileData.resize(size);
    if (!file.read(uploadFileData.data(), size))
    {
        std::cerr << "[MOCK] Failed to read file: " << filePath << std::endl;
        return false;
    }
    
    // Update Content-Length to match actual file size
    requestInfo.setContentLength(size);
    
    // Update or add Content-Length header
    bool headerFound = false;
    for (size_t i = 0; i < requestInfo.header_names.size(); i++)
    {
        if (requestInfo.header_names[i] == "Content-Length")
        {
            // Update existing header value
            requestInfo.header_values[i] = std::to_string(size);
            headerFound = true;
            break;
        }
    }
    
    // If header doesn't exist yet, add it
    if (!headerFound)
    {
        requestInfo.header_names.push_back("Content-Length");
        requestInfo.header_values.push_back(std::to_string(size));
    }
    
    // CRITICAL: Refresh ALL header pointers after vector modifications
    // (vector reallocation invalidates old pointers!)
    requestInfo.refreshHeaderPointers();
    
    uploadBytesRead = 0;
    std::cout << "[MOCK] Loaded real file: " << filePath << " (" << size << " bytes)" << std::endl;
    std::cout << "[MOCK] Updated Content-Length to: " << size << std::endl;
    std::cout << "[MOCK] Total headers now: " << requestInfo.info.num_headers << std::endl;
    return true;
}

// ==================== Mock CivetWeb Function Implementations ====================

/**
 * @brief Mock implementation of mg_get_request_info for testing
 * 
 * This function intercepts calls to mg_get_request_info and returns
 * our mock request info instead of calling the real CivetWeb function.
 * 
 * NOTE: This overrides the CivetWeb function. Must be linked BEFORE libcivetweb.
 */
extern "C" const struct mg_request_info *mg_get_request_info(const struct mg_connection *conn)
{
    std::cout << "[MOCK-v1.0] mg_get_request_info called" << std::endl;
    
    if (conn == nullptr)
    {
        std::cout << "[MOCK] mg_get_request_info: conn is null" << std::endl;
        return nullptr;
    }
    
    // Cast the mock connection to get our request info
    MockConnection* mockConn = reinterpret_cast<MockConnection*>(const_cast<struct mg_connection*>(conn));
    std::cout << "[MOCK] mg_get_request_info: returning mock info with " 
              << mockConn->requestInfo.info.num_headers << " headers" << std::endl;
    return &(mockConn->requestInfo.info);
}

/**
 * @brief Mock implementation of mg_handle_form_request for testing
 * 
 * This function intercepts calls to mg_handle_form_request. For unit tests,
 * we don't actually parse multipart form data - the test should set up the
 * data structure directly or skip tests that require actual file uploads.
 * 
 * NOTE: This overrides the symbol from libcivetweb.
 */
extern "C" int mg_handle_form_request(struct mg_connection *conn, struct mg_form_data_handler *fdh)
{
    // For unit tests, we don't actually parse multipart form data
    // Return 0 to indicate "success" without errors
    std::cout << "[MOCK] mg_handle_form_request called - returning 0 (no actual form parsing)" << std::endl;
    return 0;
}

/**
 * @brief Mock implementation of mg_get_header for testing
 * 
 * This function looks up headers in our mock request info structure.
 * 
 * NOTE: This overrides the symbol from libcivetweb.
 */
extern "C" const char *mg_get_header(const struct mg_connection *conn, const char *name)
{
    std::cout << "[MOCK-v1.0] mg_get_header called for: " << (name ? name : "NULL") << std::endl;
    
    if (conn == nullptr || name == nullptr)
    {
        std::cout << "[MOCK] mg_get_header returning nullptr (conn or name is null)" << std::endl;
        return nullptr;
    }
    
    MockConnection* mockConn = reinterpret_cast<MockConnection*>(const_cast<struct mg_connection*>(conn));
    const mg_request_info& info = mockConn->requestInfo.info;
    
    std::cout << "[MOCK] Searching " << info.num_headers << " headers for: '" << name << "'" << std::endl;
    std::cout << "[MOCK] Connection address: " << (void*)conn << std::endl;
    std::cout << "[MOCK] MockConnection address: " << (void*)mockConn << std::endl;
    
    // Search through headers (case-insensitive for HTTP header names)
    for (int i = 0; i < info.num_headers; i++)
    {
        std::cout << "[MOCK]   Header[" << i << "]: '" 
                  << (info.http_headers[i].name ? info.http_headers[i].name : "NULL") 
                  << "' = '" 
                  << (info.http_headers[i].value ? info.http_headers[i].value : "NULL")
                  << "'" << std::endl;
        
        if (info.http_headers[i].name && strcasecmp(info.http_headers[i].name, name) == 0)
        {
            std::cout << "[MOCK] ✓ mg_get_header FOUND: " << name << " = " << info.http_headers[i].value << std::endl;
            return info.http_headers[i].value;
        }
    }
    
    std::cout << "[MOCK] ❌ mg_get_header returning nullptr (header '" << name << "' NOT FOUND)" << std::endl;
    return nullptr;
}

/**
 * @brief Mock implementation of mg_read for testing
 * 
 * This function reads request body data from the mock connection.
 * If real file data is loaded (via loadFileForUpload), it returns that data.
 * Otherwise, it generates fake binary data.
 * 
 * NOTE: This overrides the symbol from libcivetweb.
 */
extern "C" int mg_read(struct mg_connection *conn, void *buf, size_t len)
{
    if (conn == nullptr || buf == nullptr)
    {
        std::cout << "[MOCK] mg_read: conn or buf is null" << std::endl;
        return -1;
    }
    
    MockConnection* mockConn = reinterpret_cast<MockConnection*>(conn);
    const mg_request_info& info = mockConn->requestInfo.info;
    long long contentLength = info.content_length;
    
    // Check if we have real file data loaded
    if (!mockConn->uploadFileData.empty())
    {
        // Read from actual file data
        size_t remaining = mockConn->uploadFileData.size() - mockConn->uploadBytesRead;
        
        if (remaining <= 0)
        {
            std::cout << "[MOCK] mg_read: EOF (real file data exhausted)" << std::endl;
            return 0; // EOF
        }
        
        size_t toRead = std::min(remaining, len);
        memcpy(buf, mockConn->uploadFileData.data() + mockConn->uploadBytesRead, toRead);
        mockConn->uploadBytesRead += toRead;
        
        //std::cout << "[MOCK] mg_read: read " << toRead << " bytes from REAL file (total: " 
          //        << mockConn->uploadBytesRead << "/" << mockConn->uploadFileData.size() << ")" << std::endl;
        
        return static_cast<int>(toRead);
    }
    else
    {
        long long remaining = contentLength - mockConn->fakeBytesRead;
        
        if (remaining <= 0)
        {
            std::cout << "[MOCK] mg_read: EOF (fake data)" << std::endl;
            return 0;
        }
        
        size_t toRead = std::min(static_cast<size_t>(remaining), len);
        memset(buf, 0x42, toRead);
        mockConn->fakeBytesRead += toRead;
        
        std::cout << "[MOCK] mg_read: read " << toRead << " bytes FAKE data (total: " 
                  << mockConn->fakeBytesRead << "/" << contentLength << ")" << std::endl;
        
        return static_cast<int>(toRead);
    }
}

/**
 * @brief Mock implementation of mg_send_mime_file2 for testing
 * 
 * This function simulates sending a file to the client.
 * For unit tests, we don't actually send data - just return success.
 * 
 * NOTE: This overrides the symbol from libcivetweb.
 */
extern "C" void mg_send_mime_file2(
    struct mg_connection *conn,
    const char *path,
    const char *mime_type,
    const char *additional_headers)
{
    std::cout << "[MOCK] mg_send_mime_file2 called" << std::endl;
    std::cout << "[MOCK]   Path: " << (path ? path : "NULL") << std::endl;
    std::cout << "[MOCK]   MIME: " << (mime_type ? mime_type : "NULL") << std::endl;
    
    // For unit tests, we don't actually send the file
    // Just log and return (simulates successful send)
    std::cout << "[MOCK] mg_send_mime_file2: Simulated file send (no actual data transfer)" << std::endl;
}

/**
 * @brief Mock implementation of mg_send_file for testing
 * 
 * Alternative file sending function that some code paths might use.
 */
extern "C" void mg_send_file(struct mg_connection *conn, const char *path)
{
    std::cout << "[MOCK] mg_send_file called for: " << (path ? path : "NULL") << std::endl;
    std::cout << "[MOCK] mg_send_file: Simulated file send" << std::endl;
}

/**
 * @brief Mock marker function for binary verification
 * 
 * This function exists only to verify our mocks are compiled into the binary.
 * Can be checked with: nm vst_test | grep __mock_civet_marker
 */
extern "C" void __mock_civet_marker() 
{
    std::cout << "Mock CivetWeb functions are active - version: " << MOCK_VERSION << std::endl;
}

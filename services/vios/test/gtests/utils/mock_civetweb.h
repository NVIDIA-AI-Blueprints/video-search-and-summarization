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
 * @file mock_civetweb.h
 * @brief Mock CivetWeb functions for unit testing
 * 
 * This header provides mock implementations of CivetWeb HTTP server functions
 * to enable unit testing without requiring a real web server.
 */

#ifndef MOCK_CIVETWEB_H
#define MOCK_CIVETWEB_H

#include <string>
#include <vector>
#include <cstring>
#include <iostream>
#include <fstream>
#include "civetweb.h"

/**
 * @brief Mock implementation of mg_request_info for testing
 * 
 * This class wraps the CivetWeb mg_request_info structure and provides
 * helper methods to set up test data without requiring a real HTTP server.
 */
class MockRequestInfo
{
public:
    mg_request_info info;
    
    // Storage for string values (mg_request_info uses const char*)
    std::string request_method_str;
    std::string request_uri_str;
    std::string query_string_str;
    std::string http_version_str;
    std::vector<std::string> header_names;
    std::vector<std::string> header_values;
    
    MockRequestInfo();
    
    void setMethod(const std::string& method);
    void setUri(const std::string& uri);
    void setQueryString(const std::string& query);
    void setContentLength(long long length);
    void addHeader(const std::string& name, const std::string& value);
    void refreshHeaderPointers();
};

/**
 * @brief Mock connection structure for testing
 * 
 * This structure mimics mg_connection layout to allow tests to work
 * with code that expects mg_connection pointers.
 * 
 * IMPORTANT: This is a testing workaround. The real mg_connection is opaque
 * and managed by CivetWeb. We create this mock to avoid integration tests
 * for unit testing purposes.
 */
struct MockConnection
{
    MockRequestInfo requestInfo;
    void* user_data;
    
    // File upload support - store actual file data for PUT uploads
    std::vector<char> uploadFileData;
    size_t uploadBytesRead;
    long long fakeBytesRead;
    
    MockConnection();
    
    // Load a real file for upload testing
    bool loadFileForUpload(const std::string& filePath);
};

// Mock version identifier
extern const char* MOCK_VERSION;

// Mock marker function for verification
extern "C" void __mock_civet_marker();

// Additional mocked CivetWeb functions (declared for reference)
extern "C" const struct mg_request_info *mg_get_request_info(const struct mg_connection *conn);
extern "C" const char *mg_get_header(const struct mg_connection *conn, const char *name);
extern "C" int mg_handle_form_request(struct mg_connection *conn, struct mg_form_data_handler *fdh);
extern "C" int mg_read(struct mg_connection *conn, void *buf, size_t len);
extern "C" void mg_send_mime_file2(struct mg_connection *conn, const char *path, 
                                   const char *mime_type, const char *additional_headers);
extern "C" void mg_send_file(struct mg_connection *conn, const char *path);

#endif // MOCK_CIVETWEB_H

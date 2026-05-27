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

#include <jsoncpp/json/json.h>
#include "CivetServer.h"
#include "error_code.h"


inline constexpr const char* LOGIN_API = "/api/user/login";
inline constexpr int CREDENTIAL_LENGTH = 200;

class UserAuthHandler
{
public:
    /**
     * Extract a specific field from a cookie string
     */
    static bool extractCookieField(const char* cookie, const char* fieldName, std::string& fieldValue);

    /**
     * Check session cookie and username provided in the HTTP Cookie header by clients.
     * Return true if the session cookie and username match with an entry in database, otherwise
     * return false. The maximum length of cookie is CREDENTIAL_LENGTH characters.
     */
    static bool validateSessionIdandUsername(const Json::Value &req, const Json::Value &in, 
                    const struct mg_connection *conn);

    /**
     * Check if request is authorized. Always allow LOGIN_API but check session cookie
     * for all other endpoints
     */
    static bool isAuthorized(const Json::Value &req, const Json::Value &in,
                        struct mg_connection *conn);

    /**
     * Extract username and session ID from cookie header for authenticated requests
     * Returns true if successful, false otherwise
     */
    static bool extractUserCredentials(struct mg_connection *conn, std::string& username, std::string& sessionId);
}; 
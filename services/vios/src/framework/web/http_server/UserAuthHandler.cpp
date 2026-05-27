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

#include <string.h>
#include <iostream>

#include "UserAuthHandler.h"
#include "logger.h"
#include "database.h"

using namespace std;

bool UserAuthHandler::extractCookieField(const char* cookie, const char* fieldName, std::string& fieldValue)
{
    char tempBuffer[CREDENTIAL_LENGTH];
    int ret = mg_get_cookie(cookie, fieldName, tempBuffer, CREDENTIAL_LENGTH);
    if (ret == -1)
    {
        LOG(error) << "either Cookie header is not present at all or the requested parameter is not found" << endl;
        return false;
    }
    if (ret == -2)
    {
        LOG(error) << "destination buffer is NULL, zero length or too small to hold the cookie value" << endl;
        return false;
    }
    if (tempBuffer[0] == '\0')
    {
        LOG(error) << "failed to extract " << fieldName << endl;
        return false;
    }
    fieldValue = std::string(tempBuffer);
    return true;
}

bool UserAuthHandler::validateSessionIdandUsername(const Json::Value &req, const Json::Value &in, 
                const struct mg_connection *conn)
{
    std::string sessionId;
    std::string username;
    LOG(verbose) << "checking session" << endl;
    const char *sessionCookie = mg_get_header(conn, "Cookie");
    if (sessionCookie == nullptr) 
    {
        LOG(error) << "Cookie header not found" << endl;
        return false;
    }
    if (!extractCookieField(sessionCookie, "vst_session", sessionId))
    {
        return false;
    }
    if (!extractCookieField(sessionCookie, "username", username))
    {
        return false;
    }
    std::vector<UserSessionsDBColumns> allSessions = GET_DB_INSTANCE()->getAllSessions();
    for (auto session: allSessions)
    {
        if (strcmp(session.session_cookie_value.c_str(), sessionId.c_str()) == 0)
        {
            // found a matching session ID, now check username
            if (strcmp(session.username_value.c_str(), username.c_str()) != 0)
            {
                LOG(error) << "session ID and username pair is invalid for " << maskSensitiveData(username, MaskType::USERNAME) << endl;
                return false;
            }
            else
            {
                GET_DB_INSTANCE()->extendSession(username, sessionId);
                LOG(verbose) << "auth successful for " << maskSensitiveData(username, MaskType::USERNAME) << endl;
                return true;
            }
        }
    }
    return false;
}

bool UserAuthHandler::isAuthorized(const Json::Value &req, const Json::Value &in,
                    struct mg_connection *conn)
{
    // always allow LOGIN_API
    if ((req["url"] == LOGIN_API))
    {
        return true;
    }
    return validateSessionIdandUsername(req, in, conn);
}

bool UserAuthHandler::extractUserCredentials(struct mg_connection *conn, std::string& username, std::string& sessionId)
{
    const char *sessionCookie = mg_get_header(conn, "Cookie");

    if (sessionCookie == nullptr) 
    {
        LOG(error) << "Cookie header not found" << endl;
        return false;
    }
    
    if (!extractCookieField(sessionCookie, "username", username))
    {
        return false;
    }
    if (!extractCookieField(sessionCookie, "vst_session", sessionId))
    {
        return false;
    }
    
    return true;
} 
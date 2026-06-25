/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "user_apis.h"
#include "vst_common.h"
#include "modules_apis.h"

using namespace std;

UserRESTApis::UserRESTApis()
{
    m_func["/api/user/login"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return loginUser(req_info, in, out, conn);
    };
    m_func["/api/user/logout"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return logoutUser(req_info, in, out);
    };
    m_func["/api/user/new"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return addNewUser(req_info, in, out);
    };
    m_func["/api/user/delete"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return deleteUser(req_info, in, out);
    };
    m_func["/api/user/credentials"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return handleUserCredentials(in, out);
    };
}

/**
 * Validate username and password hash from database
 */
bool checkUserPassword(const string username, const string passwordHash)
{
    UserDetailsDBColumns userDetails = GET_DB_INSTANCE()->getUserDetail(username);
    if (userDetails.password_hash_value == passwordHash)
    {
        LOG(info) << "password matched for " << maskSensitiveData(username, MaskType::USERNAME) << endl;
        return true;
    }
    LOG(error) << "invalid password provided for " << maskSensitiveData(username, MaskType::USERNAME) << endl;
    return false;
}

VmsErrorCode UserRESTApis::loginUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn)
{
    string username = in.get("username", "").asString();
    string password = in.get("password", "").asString();
    string vstUser = req_info.get("username", EMPTY_STRING).asString();
    bool createUser = in.get("create_user", false).asBool();
    string passwordhash;
    if (!vst_common::getSha256(password, passwordhash))
    {
        string error_message = string("Failed to generate SHA256 hash");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str());
        return VmsErrorCode::VMSInternalError;
    }
    if (createUser)
    {
        LOG(warning) << "Created user on the fly... " << endl;
        VmsErrorCode res = addNewUser(username, username, password, createUser, response);
        if(res != VmsErrorCode::NoError)
        {
            LOG(error) << "Failed to created user on the fly... " << endl;
            return res;
        }
    }
    if (checkUserPassword(username, passwordhash))
    {
        DeviceConfig config = GET_CONFIG();
        // generate and send session cookie with expiry date of one month
        LOG(info) << "generate session cookie" << endl;
        const string sessionCookie = generate_uuid();
        mg_printf(conn,"HTTP/1.1 200 OK\r\n");
        std::string extraHeaders = EMPTY_STRING;
        if(GET_CONFIG().multi_user_extra_options.size() > 0)
        {
            extraHeaders = ";";
            for (auto itr: GET_CONFIG().multi_user_extra_options)
            {
                extraHeaders += " " + itr + ";";
            }
            if(extraHeaders.size() > 0)
            {
                extraHeaders.pop_back();
            }
        }
        mg_printf(conn,"Set-Cookie: vst_session=%s; path=%s; max-age=%u; HttpOnly%s\r\n",
                                sessionCookie.c_str(), "/", config.session_max_age_sec, extraHeaders.c_str());
        mg_printf(conn,"Set-Cookie: username=%s; path=%s; max-age=%u; HttpOnly%s\r\n",
                                username.c_str(), "/", config.session_max_age_sec, extraHeaders.c_str());
        mg_printf(conn,"Access-Control-Allow-Origin: *\r\n");
        mg_printf(conn,"Connection: close\r\n");
        mg_printf(conn,"\r\n");
        // store the session info in database for future reference
        UserSessionsDBColumns row;
        row.username_value = username;
        row.session_cookie_value = sessionCookie;
        row.cookie_max_age_value = getCurrentUnixTimestamp() + config.session_max_age_sec;
        GET_DB_INSTANCE()->setUserSession(row);
    }
    else
    {
        string error_message = string("User is not authorized");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::ClientUnauthorizedError, response, error_message.c_str());
        return VmsErrorCode::ClientUnauthorizedError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode UserRESTApis::addNewUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    string username;
    string password;
    string passwordHash;
    username = in.get("username", EMPTY_STRING).asString();
    password = in.get("password", EMPTY_STRING).asString();
    string vstUser = req_info.get("username", EMPTY_STRING).asString();
    return addNewUser(username, vstUser, password, false, response);
}

VmsErrorCode UserRESTApis::deleteUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    string username;
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    // get user to be deleted from query params
    CivetServer::getParam(query_string, "username", username);
    string vstUser = req_info.get("username", EMPTY_STRING).asString();
    if (vstUser != EMPTY_STRING && vstUser != DEFAULT_ADMIN_USERNAME)
    {
        string error_message = string("insufficient rights to access resource");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::CameraUnauthorizedError, response, error_message.c_str())
        return VmsErrorCode::CameraUnauthorizedError;
    }
    if (GET_DB_INSTANCE()->deleteUserDetails(username) != 0)
    {
        string error_message = string("Failed to delete user");
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str())
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode UserRESTApis::logoutUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    string vstUser = req_info.get("username", EMPTY_STRING).asString();
    string sessionId = req_info.get("session_id", EMPTY_STRING).asString();
    if (GET_DB_INSTANCE()->deleteUserSession(vstUser, sessionId) != 0)
    {
        string error_message = string("Failed to delete session");
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str())
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode UserRESTApis::addNewUser(string username, string vstUser, string password, bool createUser, Json::Value &response)
{
    UserDetailsDBColumns user;
    string passwordHash;
    if (!createUser && (vstUser != EMPTY_STRING && vstUser != DEFAULT_ADMIN_USERNAME))
    {
        string error_message = string("insufficient rights to access resource");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::CameraUnauthorizedError, response, error_message.c_str())
        return VmsErrorCode::CameraUnauthorizedError;
    }
    if (username.empty() || password.empty() || !validatePassword(password))
    {
        string error_message = string("invalid credentials");
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }
    user.username_value = username;
    if (!vst_common::getSha256(password, passwordHash))
    {
        string error_message = string("Failed to add user");
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str())
        return VmsErrorCode::VMSInternalError;
    }
    user.password_hash_value = passwordHash;
    if (GET_DB_INSTANCE()->setUserDetail(user) != 0)
    {
        string error_message = string("Failed to add user");
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str())
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode UserRESTApis::handleUserCredentials(const Json::Value& data, Json::Value& response)
{
    //Only default user supported. This method can be extended to support management of multiple users.
    std::string username;
    std::string password;
    int isUpdateSuccessful;
    DeviceConfig config =  GET_CONFIG();
    if(!config.use_http_digest_authentication && !config.use_rtsp_authentication)
    {
        response = Json::nullValue;
        string error_message = string("Authentication is disabled");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, error_message.c_str());
        return VmsErrorCode::VMSNotSupportedError;
    }
    username = data.get("username", EMPTY_STRING).asString();
    password = data.get("password", EMPTY_STRING).asString();
    //return error if username or password is empty. Check for default username and existence of password file.
    if(username.empty() || password.empty() || username != DEFAULT_USERNAME || !isFileExist(config.password_file_path))
    {
        goto return_error;
    }
    //change credentials of default user.
    isUpdateSuccessful = mg_modify_passwords_file(config.password_file_path.c_str(), AUTHENTICATION_DOMAIN,
                        username.c_str(), password.c_str());
    updateFilePermissions(config.password_file_path, permissions::owner_read | permissions::group_read);
    if(isUpdateSuccessful)
    {
        response = Json::nullValue;
        response["username"] = username;
        response["message"] = "Password updated successfully";
        LOG(info) << "Password updated successfully" << endl;
        // inform rtsp server
        if(config.use_rtsp_authentication)
        {
            vst_rtsp::updateUser(DEFAULT_USERNAME);
        }
        return VmsErrorCode::NoError;
    }
    else
    {
        goto return_error;
    }

    return_error:
        response = Json::nullValue;
        string error_message = string("Invalid username or password");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
}
/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <utility>


inline constexpr const char* EMPTY_STRING = "";
inline constexpr const char* UNKNOWN_STRING = "unknown";
inline constexpr const char* CAMERA_STATE_ONLINE = "online";
inline constexpr const char* CAMERA_STATE_OFFLINE = "offline";
inline constexpr const char* CAMERA_COMMUNICATION_ERROR_MSG = "Device communication failed";
inline constexpr const char* CAMERA_CAMERA_NOT_FOUND_MSG = "Device Not Found";
inline constexpr const char* CAMERA_CAMERA_REQUEST_TIMEOUT_MSG = "Device request timeout";
inline constexpr int CAMERA_CAMERA_NOT_FOUND_CODE = 404;
inline constexpr int CAMERA_CAMERA_REQUEST_TIMEOUT = 408;
inline constexpr const char* CAMERA_NO_ERROR_MSG = "No Error";
inline constexpr int CAMERA_NO_ERROR_CODE = 200;
inline constexpr const char* UNDERSCORE_STR = "_";
inline constexpr char UNDERSCORE_CHAR = '_';
inline constexpr char HYPHEN_CHAR = '-';
inline constexpr char WHITESPACE_CHAR = ' ';

inline constexpr int FATAL_ERROR_CODE = -100;


#define SET_VMS_ERROR(err_code, value) { std::pair<string, string> err = getCameraErrorCodeString(err_code); \
                                        value["error_code"] = err.first; \
                                        value["error_message"] = err.second; }

#define SET_VMS_ERROR2(err_code, value, message) { std::pair<string, string> err = getCameraErrorCodeString(err_code); \
                                        value["error_code"] = err.first; \
                                        string msg(message); \
                                        if (msg.empty()) {value["error_message"] = err.second;} else {value["error_message"] = msg;} }


#define CHECK_JSON_OBJECT_IF_ERROR_RETURN(json_obj) {\
                                        if (!json_obj.isObject() ||  json_obj.empty()) { \
                                            LOG(error) << "Invalid Parameter" << endl; \
                                            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response) \
                                            return VmsErrorCode::InvalidParameterError; } }
namespace nv_vms
{
    enum VmsErrorCode
    {
        NoError = 0,
        CameraUnauthorizedError = 0x1F,  // HTTP error code : 403
        ClientUnauthorizedError,         // HTTP error code : 401
        InvalidParameterError,           // HTTP error code : 400
        CameraNotFoundError,             // HTTP error code : 404
        MethodNotAllowedError,           // HTTP error code : 405
        DeviceRequestTimeoutError,       // HTTP error code : 408
        CommunicationError,              // HTTP error code : 500
        VMSInternalError,                // HTTP error code : 500
        VMSNotSupportedError,            // HTTP error code : 501
        VMSInsufficientStorage,          // HTTP error code : 507
        VMSNoDataError,                  // HTTP error code : 404 (no streams/data found for given time range)
        ResourceConflictError,           // HTTP error code : 409
        PayloadTooLargeError,            // HTTP error code : 413
        UnsupportedMediaTypeError,       // HTTP error code : 415
        UnprocessableEntityError,        // HTTP error code : 422
        TooManyRequestsError,            // HTTP error code : 429
        ServiceUnavailableError,         // HTTP error code : 503
    };

    enum SensorStatusEvent
    {
        SensorStatusOffline = 0,
        SensorStatusOnline = 1,
        SensorStatusStreaming = 2,
        SensorStatusProxy = 3,
        SensorStatusUnknown = 0xFFF
    };

    enum StreamStatus
    {
        STREAM_STATUS_UNKNOWN = -1,
        STREAM_STATUS_ONLINE,
        STREAM_STATUS_OFFLINE,
        STREAM_STATUS_STREAMING,
        STREAM_STATUS_END_OF_STREAM,
        STREAM_STATUS_PROXY,
        STREAM_STATUS_REMOVED
    };
} // nv_vms
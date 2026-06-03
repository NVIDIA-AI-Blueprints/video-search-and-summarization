/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "streamrecorder.h"
#include "vst_common.h"
#include "health_probes.h"

using namespace std;

static string gRecorderApiList = R"([
        {"method": "GET - Get streams list", "endpoint": "api/v1/record/streams"},
        {"method": "GET - Get Recorder configuration", "endpoint": "api/v1/record/configuration"},
        {"method": "POST - Start Recording for all", "endpoint": "api/v1/record/start"},
        {"method": "POST - Stop Recording for all", "endpoint": "api/v1/record/stop"},
        {"method": "POST - Start Recording for specfic stream", "endpoint": "api/v1/record/<streamId>/start"},
        {"method": "POST - Stop Recording for specfic stream", "endpoint": "api/v1/record/<streamId>/stop"},
        {"method": "GET - Get Recording status for specfic stream", "endpoint": "api/v1/record/<streamId>/status"},
        {"method": "GET - Get Recording timelines for specfic stream", "endpoint": "api/v1/record/<streamId>/timelines"},
        {"method": "GET - Get Recording schedule for specfic stream", "endpoint": "api/v1/record/<streamId>/schedule"},
        {"method": "POST - Set Recording schedule for specfic stream", "endpoint": "api/v1/record/<streamId>/schedule"},
        {"method": "DELETE - Delete Recording schedule for specfic stream", "endpoint": "api/v1/record/<streamId>/schedule"},
        {"method": "GET - Get Recording files for specfic stream", "endpoint": "api/v1/record/<streamId>/files"},
        {"method": "POST - Set Recorder configuration", "endpoint": "api/v1/record/configuration"},
        {"method": "GET - Get recording timelines for all streams or given streams", "endpoint": "api/v1/record/timelines"}
])";

void StreamRecorder::recorderApis()
{
    m_func["/api/v1/record/streams"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return recorder->streams(out);
    };
	m_func["/api/v1/record/stream/add"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        string url = in.get("url", EMPTY_STRING).asString();
        string id = in.get("id", EMPTY_STRING).asString();
        string codec = in.get("codec", EMPTY_STRING).asString();
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        if (id.empty() || url.empty())
        {
            if (in.isMember("event") && !in["event"].isNull())
            {
                Json::Value event = in["event"];
                id = event.get("camera_id", EMPTY_STRING).asString();
                url = event.get("camera_url", "").asString();
                std::string change = event.get("change", "").asString();

                if (event.isMember("metadata") && !event["metadata"].isNull())
                {
                    Json::Value metadata = event["metadata"];
                    codec = metadata.get("codec", EMPTY_STRING).asString();
                }
                string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusStreaming);

                if (change != changeLocal || id.empty() || url.empty())
                {
                    string error_message = "Invalid parameter";
                    LOG(error) << error_message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str())
                    return VmsErrorCode::InvalidParameterError;
                }
            }
        }

        // Validate URL scheme - only accept RTSP/RTSPS URLs for recording
        // Reject S3, HTTP, HTTPS, and other non-RTSP protocols
        if (url.compare(0, 7, "rtsp://") != 0 && url.compare(0, 8, "rtsps://") != 0)
        {
            string error_message = "Invalid URL scheme for recorder. Only RTSP/RTSPS URLs are supported. Received: " + url;
            LOG(info) << "Skipping recorder service for non-RTSP stream: " << id << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, out, "Recorder service only supports RTSP/RTSPS URLs")
            return VmsErrorCode::VMSNotSupportedError;
        }

        return recorder->addStream(id, url, codec);
    };
    m_func["/api/v1/record/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return recorder->recordStatus(out);
    };
    m_func["/api/v1/record/configuration"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return recorder->getConfiguration(out);
    };
    m_func["/api/v1/record/version"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        string recorder_version;
        recorder->getVersion(recorder_version);
        out["recorder_version"] = recorder_version;
        return VmsErrorCode::NoError;
    };
    m_func["/api/v1/record/help"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        Json::CharReaderBuilder builder;
        std::istringstream iss(gRecorderApiList);

        std::string errs;
        if (!Json::parseFromStream(builder, iss, &out, &errs))
        {
            LOG(error) << "Failed to parse the API list JSON string: " << errs << endl;
        }
        return VmsErrorCode::NoError;
    };
    m_func["/api/v1/record/timelines"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return recorder->GetAllRecordTimelines(req_info, out);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, out, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }
    };
    m_func["/v1/live"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return VmsErrorCode::NoError;
    };
    m_func["/v1/ready"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkReadinessProbe(conn, out);
    };
    m_func["/v1/startup"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkCivetWebServerRunning(conn, out);
    };

    m_func[RECORD_API] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder == nullptr)
        {
            LOG(error) << "Recorder module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return handleRecordAPIrequest(req_info, in, out, conn);
    };
}

VmsErrorCode StreamRecorder::handleRecordAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                                struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();

    string device_api(RECORD_API);
    string path = request_api.substr(device_api.size() - 1);

    LOG(verbose2) << "Recorder API path: " << path << std::endl;
    vector<string> path_arr = splitString(path, "/");
    string stream_id;
    string action;

    if (path_arr.size() > 0)
    {
        stream_id = path_arr[0];
        action = path_arr.size() >= 2 ? path_arr[1] : "";
        if (path_arr[0] == "stream")
        {
            stream_id = path_arr.size() >= 2 ? path_arr[1] : "";
            action = path_arr.size() >= 3 ? path_arr[2] : "";
        }
    }
    else
    {
        if (in.isMember("event") && !in["event"].isNull())
        {
            Json::Value event = in["event"];
            stream_id = event.get("camera_id", EMPTY_STRING).asString();
            std::string change = event.get("change", "").asString();

            string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusOffline);
            if (change != changeLocal || stream_id.empty())
            {
                LOG(error) << "Requested API is not allowed" << std::endl;
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
                return VmsErrorCode::MethodNotAllowedError;
            }

            request_method = "delete"; // SDR sends only POST requests. So as of now we changed it to delete request. Once it is handled from the SDR we can remove this.
        }
        else
        {
            LOG(error) << "Requested API is not allowed" << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
            return VmsErrorCode::MethodNotAllowedError;
        }
    }

    StreamRecorder* recorder = GET_RECORDER();
    if (recorder == nullptr)
    {
        LOG(error) << "Recorder module is not loaded" << stream_id << endl;
        ret = VmsErrorCode::MethodNotAllowedError;
        SET_VMS_ERROR2(ret, response, "Recorder module is not loaded");
        return ret;
    }

    if (iequals(request_method, "get"))
    {
        if (iequals(action, "timelines"))
        {
            string startTime;
            string endTime;
            CivetServer::getParam(query_string, "startTime", startTime);
            CivetServer::getParam(query_string, "endTime", endTime);
            ret = recorder->getRecordTimelines(stream_id, startTime, endTime, response);
        }
        else if (iequals(action, "status") || iequals(action, "record"))
        {
            string recording_state = recorder->recordStatus(stream_id);
            response["recordingStatus"] = recording_state;
            return VmsErrorCode::NoError;
        }
        else if (iequals(action, "schedule"))
        {
            ret = recorder->getRecordSchedules(stream_id, response);
            if (ret != VmsErrorCode::NoError)
            {
                LOG(error) << "Failed to get recording Schedules" << endl;
                SET_VMS_ERROR2(ret, response, "Failed to get recording Schedules")
            }
        }
        else if (iequals(action, "files"))
        {
            // Not included in new VST API
            string startTime;
            string endTime;
            CivetServer::getParam(query_string, "start_time", startTime);
            CivetServer::getParam(query_string, "end_time", endTime);
            ret = recorder->getStreamRecordFiles(stream_id, startTime, endTime, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else if (iequals(request_method, "post"))
    {
        if (iequals(action, "add"))
        {
            string rtsp_url = in.get("url", EMPTY_STRING).asString();
            return recorder->addStream(stream_id, rtsp_url);
        }
        else if (iequals(action, "remove"))
        {
            return recorder->removeStream(stream_id);
        }
        else if (iequals(action, "start"))
        {
            RecordScheduleStatus status = recorder->startRecord(stream_id, User);
            if (status == RecordScheduleFailed)
            {
                LOG(error) << "Failed to start recording" << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to start recording");
                return VmsErrorCode::VMSInternalError;
            }
            ret = VmsErrorCode::NoError;
        }
        else if (iequals(action, "stop"))
        {
            StopRecordStatus status = recorder->stopRecord(stream_id);
            if (status == StopRecordError)
            {
                LOG(error) << "Failed to stop recording" << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to stop recording");
                return VmsErrorCode::VMSInternalError;
            }
            else if (status == StopRecordIgnore)
            {
                LOG(error) << "Stopping event based recording is disabled" << endl;
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Stopping event based recording is disabled");
                return VmsErrorCode::MethodNotAllowedError;
            }
            ret = VmsErrorCode::NoError;
        }
        else if (iequals(action, "schedule"))
        {
            ret = setDeviceRecordSchedule(stream_id, in, response);
        }
        else if(iequals(action, "event"))
        {
            ret = recorder->onEvent(stream_id, response);
        }
        else if (iequals(action, "configuration"))
        {
            ret = VmsErrorCode::VMSInternalError;
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    else if (iequals(request_method, "delete"))
    {
        if (action.empty())
        {
            return recorder->removeStream(stream_id);
        }
        else if (iequals(action, "schedule"))
        {
            ret = deleteCameraRecordSchedule(stream_id, query_string, in, response);
        }
        else
        {
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            ret = VmsErrorCode::MethodNotAllowedError;
        }
    }
    return ret;
}

VmsErrorCode StreamRecorder::setDeviceRecordSchedule(const string stream_id, const Json::Value& value, Json::Value &response)
{
    bool result = false;
    StreamRecorder* recorder = GET_RECORDER();
    if (recorder == nullptr)
    {
        LOG(error) << "Recorder module is not loaded" << stream_id << endl;
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Recorder module is not loaded");
        return VmsErrorCode::MethodNotAllowedError;
    }
    if (value.isObject() && !value.empty())
    {
        // Accepting this object for backward compatibility
        const string start_time = value.get("start_time", EMPTY_STRING).asString();
        const string end_time = value.get("end_time", EMPTY_STRING).asString();
        if (start_time.empty() || end_time.empty() || recorder->createNewRecordSchedule(stream_id, start_time, end_time) != 0)
        {
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        result = true;
    }
    else if (value.isArray())
    {
        uint32_t invalidSchedules = 0;
        for (uint32_t i = 0; i < value.size(); i++)
        {
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(value[i])
        }
        for (uint32_t i = 0; i < value.size(); i++)
        {
            const string start_time = value[i].get("startTime", EMPTY_STRING).asString();
            const string end_time = value[i].get("endTime", EMPTY_STRING).asString();
            if (start_time.empty() || end_time.empty() || recorder->createNewRecordSchedule(stream_id, start_time, end_time) != 0)
            {
                LOG(warning) << "Unable to set schedule with start_time: " << start_time << " and end_time: " << end_time << endl;
                invalidSchedules++;
            }
        }
        if (invalidSchedules == value.size())
        {
            //all schedules are invalid
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        else
        {
            result = true;
        }
    }
    else
    {
        LOG(error) << "Invalid Parameter" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }
    response = result;
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::deleteCameraRecordSchedule(const string stream_id, const string query_string,
    const Json::Value& value, Json::Value &response)
{
    bool result = false;
        StreamRecorder* recorder = GET_RECORDER();
    if (recorder == nullptr)
    {
        LOG(error) << "Recorder module is not loaded" << stream_id << endl;
        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Recorder module is not loaded");
        return VmsErrorCode::MethodNotAllowedError;
    }
    if (value.isObject() && !value.empty())
    {
        // Accepting this object for backward compatibility
        // depricated in OPEN API 3.0
        const string start_time = value.get("start_time", EMPTY_STRING).asString();
        const string end_time = value.get("end_time", EMPTY_STRING).asString();
        if (start_time.empty() || end_time.empty() ||
            recorder->deleteStreamRecordSchedule(stream_id, start_time, end_time) != VmsErrorCode::NoError)
        {
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        result = true;
    }
    else if (value.isArray())
    {
        // depricated in OPEN API 3.0
        uint32_t invalidSchedules = 0;
        for (uint32_t i = 0; i < value.size(); i++)
        {
            CHECK_JSON_OBJECT_IF_ERROR_RETURN(value[i])
        }
        for (uint32_t i = 0; i < value.size(); i++)
        {
            const string start_time = value[i].get("start_time", EMPTY_STRING).asString();
            const string end_time = value[i].get("end_time", EMPTY_STRING).asString();
            if(start_time.empty() || end_time.empty() ||
                recorder->deleteStreamRecordSchedule(stream_id, start_time, end_time) != VmsErrorCode::NoError)
            {
                LOG(warning) << "Unable to delete schedule with start_time: " << start_time << " and end_time: " << end_time << endl;
                invalidSchedules++;
            }
        }
        if (invalidSchedules == value.size())
        {
            //all schedules are invalid
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        else
        {
            result = true;
        }
    }
    else if (value.empty())
    {
        // if body is empty then check for query params, OPEN API 3.0 support
        string start_time, end_time;
        CivetServer::getParam(query_string, "startTime", start_time);
        CivetServer::getParam(query_string, "endTime", end_time);
        if (start_time.empty() || end_time.empty() ||
            recorder->deleteStreamRecordSchedule(stream_id, start_time, end_time) != VmsErrorCode::NoError)
        {
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        result = true;
    }
    else
    {
        LOG(error) << "Invalid Parameter" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }
    response = result;
    return VmsErrorCode::NoError;
}

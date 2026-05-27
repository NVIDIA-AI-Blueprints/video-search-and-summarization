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

#include "nvgrpc.h"

#include "logger.h"
#include "config.h"
#include "udpclientpool.h"
#include "sensor_monitoring.h"
#include "cmdline_parser.h"
using grpc::Status;
using grpc::StatusCode;

#ifdef USE_GRPC_SERVER
using grpc::ServerBuilder;
#endif

constexpr const char* DEFAULT_IP = "0.0.0.0";
constexpr const char* DEFAULT_UDP_DEVICE_NAME = "Tokkio_Avatar";


constexpr int DEFAULT_SAMPLE_RATE = 16000;
constexpr int DEFAULT_CHANNELS = 1;
constexpr const char* DEFAULT_AUDIO_CODEC_PCM = "pcm";
constexpr int DEFAULT_BITS_PER_SAMPLE = 32;

constexpr const char* DEFAULT_WIDTH = "1920";
constexpr const char* DEFAULT_HEIGHT = "1080";

#define EXECUTE_FUNC(func_key, arg1, arg2, arg3, agr4)  m_callbackMap[func_key](arg1, arg2, arg3, agr4);

extern "C" void destroyUDPDiscoveryObject(ISensorDiscoveryInterface* object)
{
    // do nothing
}

#ifdef USE_UDP_CONFIG_FROM_FILE
namespace
{
Json::Value parseRtspJson()
{
    Json::Value stream_info;
    Json::Value config;
    Json::Reader reader;
    std::ifstream file((GET_CMDLINE_PARSER()->getRtspStreamsFilePath()).c_str());
    if(file.good())
    {
        if(!reader.parse(file, config, true))
        {
            LOG(error) << "Failed to parse rtsp_streams configuration" << endl;
            return -1;
        }
        Json::Value details_array = config["streams"];
        if (details_array.isArray())
        {
            Json::Value elem = Json::nullValue;
            for (Json::Value::ArrayIndex i = 0; i != details_array.size(); i++)
            {
                elem = details_array[i];
                // bool enabled = elem.get("enabled", false).asBool();
                string stream_in = elem.get("stream_in", "").asString();
                string stream_name = elem.get("name", "").asString();
                // if (enabled == true)
                {
                    if (stream_in.find("udp") != string::npos)
                    {
                        Json::Value video_info = elem.get("video", Json::nullValue);
                        if (video_info != Json::nullValue)
                        {
                            unsigned video_port = (unsigned)video_info.get("port", 0).asUInt();
                            stream_info["video_port"] = video_port;
                            stream_info["video_codec"] = video_info.get("codec", "").asString();
                            stream_info["framerate"] = video_info.get("framerate", DEFAULT_FRAMERATE).asInt();
                        }
                        Json::Value audio_info = elem.get("audio", Json::nullValue);
                        if (audio_info != Json::nullValue)
                        {
                            stream_info["audio_enabled"] = audio_info.get("enabled", false).asBool();
                            unsigned audio_port = (unsigned)audio_info.get("port", 0).asUInt();
                            stream_info["audio_port"] = audio_port;
                            stream_info["audio_codec"] = audio_info.get("codec", "").asString();
                            stream_info["sample_rate"] = (unsigned int)audio_info.get("sample_rate_Hz", 0).asUInt();
                            stream_info["bits_per_sample"] = (unsigned int)audio_info.get("bits_per_sample", 0).asUInt();
                        }
                        stream_info["name"] = stream_name;
                        break;
                    }
                }
            }
        }
    }
    else
    {
        LOG(error) << "Error opening rtsp_streams file : " << GET_CMDLINE_PARSER()->getRtspStreamsFilePath() << endl;
    }
    return stream_info;
}
}
#endif

Json::Value parseFromClientRequest(const CreateUDPConnectionRequest* request)
{
    Json::Value stream_info;
    if (request == nullptr)
    {
        LOG(error) << "CreateUDPConnectionRequest is null" << endl;
        return stream_info;
    }

    if (request->has_video_params())
    {
        LOG(info) << "GRPC Client video params codec:" << request->video_params().codec()
                << ", fps:" << request->video_params().framerate() << endl;

        stream_info["video_codec"] = request->video_params().codec().empty() ? DEFAULT_VIDEO_CODEC : request->video_params().codec();
        stream_info["framerate"] = request->video_params().framerate() == 0 ? DEFAULT_FRAMERATE : request->video_params().framerate();
    }
    if (request->has_audio_params())
    {
        LOG(info) << "GRPC Client audio params codec:" << request->audio_params().codec()
                << ", sample_rate:" << request->audio_params().sample_rate_hz()
                << ", bps:" << request->audio_params().bits_per_sample() << endl;

        stream_info["audio_enabled"] = true;
        stream_info["audio_codec"] = request->audio_params().codec().empty() ? DEFAULT_AUDIO_CODEC_PCM : request->audio_params().codec();
        stream_info["sample_rate"] = request->audio_params().sample_rate_hz() == 0 ? DEFAULT_SAMPLE_RATE : request->audio_params().sample_rate_hz();
        stream_info["bits_per_sample"] = request->audio_params().bits_per_sample() == 0 ? DEFAULT_BITS_PER_SAMPLE : request->audio_params().bits_per_sample();
    }
    stream_info["connection_id"] = request->connection_id();
    return stream_info;
}

SensorInfo CreateUdpSensor(Json::Value streamInfo, std::string id)
{
    /* Create new sensor since it is not present in database */
    SensorInfo freshSensor;
    freshSensor.sensorId = freshSensor.id = id;
    freshSensor.name = streamInfo.get("name", DEFAULT_UDP_DEVICE_NAME).asString();
    freshSensor.type = SENSOR_TYPE_UDP;
    freshSensor.m_notify = false;

    return freshSensor;
}

#ifdef USE_GRPC_SERVER
Status GrpcUdpService::CreateWebrtcConnection(ServerContext* context, const CreateWebrtcConnectionRequest* request,
    CreateWebrtcConnectionReply* response)
{
    int32_t error_code = 0;
    string error_msg = "OK";
    if (request == nullptr)
    {
        LOG(error) << "CreateWebrtcConnection is null" << endl;
        response->set_error_code(-1);
        response->set_message("CreateWebrtcConnection request params is null");
        Status err_status(StatusCode::INTERNAL, "CreateWebrtcConnection request params is null");
        return err_status;
    }

    Json::Value req_info;
    req_info["sensorId"] = request->connection_id();
    req_info["webrtcVendor"] = request->webrtc_vendor();
    req_info["ipAddress"] = request->host_address();
    req_info["signalingPort"] = request->signaling_port();
    req_info["mediaPort"] = request->media_port();
    req_info["width"] = request->width();
    req_info["height"] = request->height();
    req_info["framerate"] = request->framerate() == 0 ? DEFAULT_FRAMERATE : request->framerate();

    int ret = EXECUTE_FUNC("/grpc/setWebrtcClientParams", req_info, req_info, req_info, nullptr)
    if (ret != 0)
    {
        string err_msg = "Error while /grpc/setWebrtcClientParams";
        LOG(error) << err_msg << endl;
        Status err_status(StatusCode::INTERNAL, err_msg);
        return err_status;
    }

    response->set_error_code(error_code);
    response->set_message(error_msg.c_str());
    return Status::OK;
}

Status GrpcUdpService::CreateUDPConnection(ServerContext* context,
                                    const CreateUDPConnectionRequest* request,
                                    CreateUDPConnectionReply* reply)
{
    LOG(info) << "GRPC call CreateUDPConnection" << endl;
    string peerid = "", stream_id = "";
    Status status = addDevice(m_deviceManager, request, reply, peerid, stream_id);
    return status;
}

std::string GrpcUdpService::createUniqueStreamId(std::shared_ptr<SensorInfo> sensor)
{
    string stream_id = sensor->id;
    vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
    if (streams.size() == 0)
        return stream_id;

    int i = 0;
    bool streamNameExist = false;
    do
    {
        streamNameExist = false;
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream_id == stream->id)
            {
                streamNameExist = true;
                stream_id = sensor->id + string("_") + std::to_string(++i);
                break;
            }
        }
    } while (streamNameExist);
    return stream_id;
}

Status GrpcUdpService::addDevice(std::shared_ptr<nv_vms::DeviceManager> deviceMngr,
    const CreateUDPConnectionRequest* request, CreateUDPConnectionReply* reply,
    string& peerid, string& stream_id)
{
    LOG(info) << __FUNCTION__ << endl;
    string width, height;
    int video_port = 0, audio_port = 0;
    string connection_id;
    bool is_sensorAlreadyPresent = false;

    Json::Value stream_info;
#ifdef USE_UDP_CONFIG_FROM_FILE
    stream_info = parseRtspJson();
#else
    stream_info = parseFromClientRequest(request);
#endif
    if (stream_info.empty() || deviceMngr == nullptr)
    {
        LOG(error) << "stream info empty" << endl;
        return Status(StatusCode::INTERNAL, "[Error] Internal error found");
    }

    std::shared_ptr<SensorInfo> existed_sensor;
    connection_id = stream_info.get("connection_id", EMPTY_STRING).asString();
    if (connection_id.empty())
    {
        return Status(StatusCode::INVALID_ARGUMENT,
            "[Error] Empty connection_id recieved");
    }
    

    existed_sensor = deviceMngr->findSensor(connection_id);
    if (existed_sensor)
    {
        /* It means sensor is already present in the database */
        is_sensorAlreadyPresent = true;
    }
    else
    {
        return Status(StatusCode::INVALID_ARGUMENT,
                    "[Error] Invalid connection_id received");
    }

    std::shared_ptr<StreamInfo> stream = nullptr;
    // Check if default Tokkio substream is already added
    if (existed_sensor)
    {
        string avatar_stream_id = existed_sensor->id + "_1";
        stream = existed_sensor->getStream(avatar_stream_id);
    }
    bool new_substream = false;
    if (!stream)
    {
        LOG(error) << "Avatar stream not found, creating new stream" << endl;
        stream = std::make_shared<StreamInfo>();
        new_substream = true;
        if (is_sensorAlreadyPresent == true)
        {
            stream->isMainStream = false;
            stream->sensorId = existed_sensor->id;
            stream->id = createUniqueStreamId(existed_sensor);
        }

        stream->name = string(DEFAULT_UDP_DEVICE_NAME) + string("_") + stream->id;
        stream->updateStreamtype(StreamType::Udp);
        stream->direction = StreamDirectionOut;

        /* Obtain video & audio ports  */
    #ifdef USE_UDP_CONFIG_FROM_FILE
        video_port = stream_info.get("video_port", 0).asUInt();
        if (video_port == 0)
        {
            video_port = UdpClientPool::getInstance()->getUdpPort();
        }
        audio_port = stream_info.get("audio_port", 0).asUInt();
        if (audio_port == 0)
        {
            audio_port = UdpClientPool::getInstance()->getUdpPort();
        }
    #else
        video_port = UdpClientPool::getInstance()->getUdpPort();
        audio_port = UdpClientPool::getInstance()->getUdpPort();
    #endif

        if (video_port <= 0 || audio_port <= 0)
        {
            return Status(StatusCode::RESOURCE_EXHAUSTED, "Port range is exhausted");
        }

        // Create live_url as => udp:<video_port>:<audio_port>
        string stream_url = "udp";
        stream->live_url = stream_url + ":" + to_string(video_port) + ":" + to_string(audio_port);
        LOG(info) << "stream name = " << stream->name << ", live_url = " << secureUrlForLogging(stream->live_url) << endl;
    }
    else
    {
        vector<string> url_info = splitString(stream->live_url, ":");
        video_port = stringToInt(url_info[1], 0);
        audio_port = stringToInt(url_info[2], 0);
    }
    {
        SensorAudioEncoderSettingsValues values;
        values.enable = stream_info.get("audio_enabled", false).asBool();
        values.encoding = stream_info.get("audio_codec", DEFAULT_AUDIO_CODEC_PCM).asString();
        values.sample_rate = to_string(stream_info.get("sample_rate", DEFAULT_SAMPLE_RATE).asUInt());
        values.bits_per_sample = to_string(stream_info.get("bits_per_sample", DEFAULT_BITS_PER_SAMPLE).asUInt());
        stream->updateAudioEncoderValues(values);
        LOG(info) << "audio_enable:" << values.enable << ", audio_codec = " << values.encoding << ", sample_rate = "
                << values.sample_rate << ", bps = " << values.bits_per_sample << endl;
    }
    stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
            translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));

    if (width.empty() || height.empty())
    {
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty())
        {
            width = resolution.width;
            height = resolution.height;
        }
        else
        {
            width = DEFAULT_WIDTH;
            height = DEFAULT_HEIGHT;
        }
    }

    SensorVideoEncoderSettingsValues values;
    values.encoding = stream_info.get("video_codec", DEFAULT_VIDEO_CODEC).asString();
    values.frameRate = to_string(stream_info.get("framerate", DEFAULT_FRAMERATE).asInt());
    values.resolution.width = width;
    values.resolution.height = height;
    stream->updateVideoEncoderValues(values);

    /* Push newly created stream into the sensor */
    if (new_substream)
    {
        if (is_sensorAlreadyPresent == true)
        {
            bool isStreamAdded = existed_sensor->addStreams(stream);
            if (isStreamAdded == false)
            {
                LOG(error) << "failed to add stream into sensor" << endl;
                return Status(StatusCode::INTERNAL, "[Error] failed to add stream into sensor");
            }
            existed_sensor->printInfo();
        }
    }
    else
    {
        existed_sensor->printInfo();
    }

    peerid = connection_id;
    stream_id = stream->id;

    /* Grpc response */
    LOG(info) << "Grpc response video_port:" << video_port << ", audio_port:" << audio_port << endl;
    reply->set_video_port(video_port);
    reply->set_audio_port(audio_port);
    reply->set_host_address(g_hostIp);

    return Status::OK;
}

void GrpcUdpService::addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func)
{
    string grpc_api = "/grpc/";
    // register handlers
    for (auto it : func)
    {
        if (it.first.find(grpc_api, 0) == 0)
        {
            m_callbackMap.insert({it.first, it.second});
        }
    }
}

void GrpcWebrtcSignallingService::addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func)
{
    string grpc_api = "/grpc/";
    // register handlers
    for (auto it : func)
    {
        if (it.first.find(grpc_api, 0) == 0)
        {
            m_callbackMap.insert({it.first, it.second});
        }
    }
}

VmsErrorCode GrpcWebrtcSignallingService::remotePeerAnswer(const Json::Value &in)
{
    string peerId = in.get("peerId", EMPTY_STRING).asString();
    string type = in.get("type", EMPTY_STRING).asString();
    string sdp = in.get("sdp", EMPTY_STRING).asString();
    std::shared_ptr<RemotePeerAnswer> remotePeerAnswer;
    {
        std::lock_guard<std::mutex> lock(m_remoteAnswersMutex);
        auto it = m_remoteAnswers.find(peerId);
        if (it == m_remoteAnswers.end())
        {
            LOG(error) << "No Peer found for ID " << peerId << std::endl;
            return VmsErrorCode::InvalidParameterError;
        }
        remotePeerAnswer = it->second;
    }

    {
        std::lock_guard<std::mutex> lock(remotePeerAnswer->m_mtx);
        remotePeerAnswer->m_answer = in;
        remotePeerAnswer->m_ready = true;
    }
    remotePeerAnswer->m_cv.notify_one();
    LOG(info) << "UI sdp answer available for " << peerId << endl;
    return VmsErrorCode::NoError;
}

VmsErrorCode GrpcWebrtcSignallingService::remotePeerCandidate(const Json::Value &in)
{
    string peerId = in.get("peerId", EMPTY_STRING).asString();
    Json::Value candidate = in.get("candidate", EMPTY_STRING);
    std::shared_ptr<RemotePeerCandidate> remotePeerCandidate;
    {
        std::lock_guard<std::mutex> lock(m_remoteCandidatesMutex);
        auto it = m_candidates.find(peerId);
        if (it == m_candidates.end())
        {
            LOG(error) << "No Peer found for ID " << peerId << std::endl;
            return VmsErrorCode::InvalidParameterError;
        }
        remotePeerCandidate = it->second;
    }

    {
        std::lock_guard<std::mutex> lock(remotePeerCandidate->m_mtx);
        remotePeerCandidate->m_candidateList.push(candidate);
        remotePeerCandidate->m_ready = true;
    }
    remotePeerCandidate->m_cv.notify_one();
    LOG(info) << "UI Candidate pushed for " << peerId << endl;
    return VmsErrorCode::NoError;
}

Status GrpcWebrtcSignallingService::sdpExchange(ServerContext* context, const Sdp* request, Sdp* reply)
{
    LOG(verbose) << "Vst received sdp of type: " << request->type() << endl;
    LOG(verbose) << "sdp value:\n" << request->sdp() << endl;
    string sensorId = request->id().stream_id();
    string streamId = sensorId + "_1";
    Json::Value sessionDescription;
    sessionDescription["sdp"] = request->sdp();
    sessionDescription["type"] = "offer";

    std::string client_ip = context->peer();
    m_ipToStreamid[client_ip] = streamId;

    // Add data structure to receive answer from UI
    auto remotePeerAnswer = std::make_shared<RemotePeerAnswer>();
    {
        std::lock_guard<std::mutex> lock(m_remoteAnswersMutex);
        if (m_remoteAnswers.find(streamId) != m_remoteAnswers.end())
        {
            string err_msg = "Already processing answer for " + sensorId;
            LOG(error) << err_msg << endl;
            Status err_status(StatusCode::ALREADY_EXISTS, err_msg);
            return err_status;
        }
        m_remoteAnswers[streamId] = remotePeerAnswer;
        LOG(info) << "Added data structure for remotePeerAnswer for " << streamId << endl;
    }

    // Add data structure to receive ICE Candidates from UI
    auto remotePeerCandidate = std::make_shared<RemotePeerCandidate>();
    {
        std::lock_guard<std::mutex> lock(m_remoteCandidatesMutex);
        if (m_candidates.find(streamId) != m_candidates.end())
        {
            string err_msg = "Already processing candidates for " + sensorId;
            LOG(error) << err_msg << endl;
            Status err_status(StatusCode::ALREADY_EXISTS, err_msg);
            return err_status;
        }
        m_candidates[streamId] = remotePeerCandidate;
        LOG(info) << "Added data structure for remotePeerCandidate for " << streamId << endl;
    }

    // Send offer to UI over websocket
    Json::Value req_info;
    req_info["sensorId"] = sensorId;
    req_info["streamId"] = streamId;
    req_info["sessionDescription"] = sessionDescription;
    VmsErrorCode ret = EXECUTE_FUNC("/grpc/remotePeerOffer", req_info, req_info, req_info, nullptr)
    bool timeout = false;
    if (ret == VmsErrorCode::NoError)
    {
        // Wait for answer from UI
        std::unique_lock<std::mutex> lock(remotePeerAnswer->m_mtx);
        if (!remotePeerAnswer->m_cv.wait_for(lock, std::chrono::seconds(GET_CONFIG().webrtc_peer_conn_timeout_sec), [&]{ return remotePeerAnswer->m_ready; }))
        {
            timeout = true;
        }
        else
        {
            // Send answer in reply
            LOG(verbose) << "Data processed for " << streamId << endl;
            Json::Value& answer = remotePeerAnswer->m_answer;
            string sdp = answer.get("sdp", EMPTY_STRING).asString();
            string peerId = answer.get("peerId", EMPTY_STRING).asString();
            LOG(verbose) << "\n" << answer.toStyledString() << endl;
            reply->set_sdp(sdp);
            reply->set_type(vstserver::SdpType::ANSWER);
            vstserver::SessionId sessionId;
            sessionId.set_stream_id(peerId);
            *reply->mutable_id() = sessionId;
        }
    }

    // Remove the data remotePeerAnswer for the ID as it's no longer needed
    {
        std::lock_guard<std::mutex> mapLock(m_remoteAnswersMutex);
        m_remoteAnswers.erase(streamId);
    }
    if (timeout)
    {
        string err_msg = "Timeout while waiting for answer for " + sensorId;
        LOG(error) << err_msg << endl;
        Status err_status(StatusCode::UNAVAILABLE, err_msg);
        return err_status;
    }
    return Status::OK;
}

Status GrpcWebrtcSignallingService::iceCandidateExchange(ServerContext* context,
                            grpc::ServerReaderWriter<IceCandidate, IceCandidate>* stream)
{
    std::string client_ip = context->peer();
    string streamId = m_ipToStreamid[client_ip];
    string sensorId = "";

    // writerThread to send candidates from UI to grpc client
    std::thread writerThread([this, context, stream, streamId]()
    {
        while (true)
        {
            std::shared_ptr<RemotePeerCandidate> remotePeerCandidate;
            {
                std::lock_guard<std::mutex> lock(m_remoteCandidatesMutex);
                auto it = m_candidates.find(streamId);
                if (it == m_candidates.end())
                {
                    LOG(error) << "Candidate data structure not found for ID " << streamId << std::endl;
                    break;
                }
                remotePeerCandidate = it->second;
            }
            std::unique_lock<std::mutex> lock(remotePeerCandidate->m_mtx);
            if (!remotePeerCandidate->m_cv.wait_for(lock, std::chrono::seconds(GET_CONFIG().webrtc_peer_conn_timeout_sec), [&]{ return remotePeerCandidate->m_candidateList.size(); }))
            {
                string err_msg = "Timeout while waiting for candidate for " + streamId;
                LOG(error) << err_msg << endl;
                break;
            }

            IceCandidate response;
            vstserver::SessionId* session_id = response.mutable_id();
            session_id->set_stream_id(streamId);
            LOG(verbose) << "size: " << remotePeerCandidate->m_candidateList.size() << endl;
            while(remotePeerCandidate->m_candidateList.size() > 0)
            {
                Json::Value candidate = remotePeerCandidate->m_candidateList.front();
                LOG(verbose) << "json:\n" << candidate.toStyledString() << endl;
                string str_candidate = jsonToString(candidate);
                LOG(verbose) << "string:\n" << candidate << endl;
                response.add_ice_candidate(str_candidate);
                remotePeerCandidate->m_candidateList.pop();
            }
            if (!stream->Write(response))
            {
                LOG(error) << "write error" << endl;
                // Handle error here
                break;
            }
            LOG(verbose) << "size: " << remotePeerCandidate->m_candidateList.size() << endl;
            // Sleep for a short period of time before sending the next message
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        LOG(info) << "Exiting writer thread" << endl;
    });

    // Send candidates to UI over websocket
    bool terminate = false;
    IceCandidate request;
    atomic<bool> readComplete = false;
    std::thread readerThread;
    while (!terminate)
    {
        std::mutex mtx;
        std::condition_variable cv;
        readerThread = std::thread([&]()
        {
            if (stream->Read(&request) == false)
            {
                LOG(error) << "IceCandidate Read failed for " << request.id().stream_id() << endl;
                terminate = true;
                readComplete = true;
                cv.notify_one();
                return;
            }
            LOG(verbose) << "read success for " << request.id().stream_id() << endl;
            Json::Value candidates;
            for (const auto& candidate : request.ice_candidate())
            {
                LOG(info) << "candidate from grpc: " << candidate << endl;
                if (!candidate.empty())
                {
                    candidates.append(stringToJson(candidate));
                }
                else
                {
                    LOG(info) << "Empty candidate received from grpc, will exit from read thread" << endl;
                    terminate = true;
                    readComplete = true;
                    cv.notify_one();
                    return;
                }
            }
            if (candidates.size())
            {
                Json::Value req_info;
                sensorId = request.id().stream_id();
                req_info["sensorId"] = sensorId;
                req_info["streamId"] = streamId;
                req_info["candidates"] = candidates;
                EXECUTE_FUNC("/grpc/remotePeerSendIceCandidate", req_info, req_info, req_info, nullptr)
            }
            readComplete = true;
            cv.notify_one();
        });
        {
            std::unique_lock<std::mutex> lock(mtx);
            if (cv.wait_for(lock, std::chrono::seconds(GET_CONFIG().webrtc_peer_conn_timeout_sec), [&]() { return readComplete.load(); }))
            {
                if (readerThread.joinable())
                {
                    LOG(info) << "Joining iceCandidate Read thread" << endl;
                    readerThread.join();
                }
                readComplete = false;
            }
            else
            {
                LOG(error) << "Timeout for " << request.id().stream_id() << endl;
                terminate = true;
                break;
            }
        }
    }

    if (writerThread.joinable())
    {
        writerThread.join();
    }

    if (readComplete == false)
    {
        LOG(info) << "Trying to cancel grpc Read" << endl;
        context->TryCancel();
    }
    if (readerThread.joinable())
    {
        LOG(info) << "Joining reader thread" << endl;
        readerThread.join();
    }

    // Erase remote Peer from streamBridge
    {
        Json::Value req_info;
        req_info["sensorId"] = sensorId;
        req_info["streamId"] = streamId;
        EXECUTE_FUNC("/grpc/remotePeerErase", req_info, req_info, req_info, nullptr)
    }

    // Erase Candidates data structure
    {
        std::lock_guard<std::mutex> lock(m_remoteCandidatesMutex);
        if (m_candidates.find(streamId) != m_candidates.end())
        {
            m_candidates.erase(streamId);
        }
    }

    LOG(info) << "Closing grpc bidirectional stream for " << streamId << endl;
    return Status::OK;
}
#endif

#ifdef USE_GRPC_CLIENT
GrpcClient* GrpcClient::m_instance = nullptr;
GrpcClient* GrpcClient::getInstance()
{
    if (m_instance == nullptr)
    {
        m_instance = new GrpcClient();
    }
    return m_instance;
}

void GrpcClient::deleteInstance()
{
    if (m_instance != nullptr)
    {
        delete m_instance;
        m_instance = nullptr;
    }
}

GrpcClient::GrpcClient()
{
    CreateStub();
}

void GrpcClient::CreateDummyUDPDevice(string connection_id, int32_t& audio_port, int32_t& video_port)
{
    LOG(info) << __METHOD_NAME__ << endl;
    CreateUDPConnectionRequest request;
    CreateUDPConnectionReply reply;
    ClientContext context;

    auto* video_params = request.mutable_video_params();
    auto* audio_params = request.mutable_audio_params();
    video_params->set_framerate(DEFAULT_FRAMERATE);
    video_params->set_codec(DEFAULT_VIDEO_CODEC);
    audio_params->set_bits_per_sample(DEFAULT_BITS_PER_SAMPLE);
    audio_params->set_sample_rate_hz(DEFAULT_SAMPLE_RATE);
    audio_params->set_codec(DEFAULT_AUDIO_CODEC_PCM);

    request.set_connection_id(connection_id);

    if (m_stub)
    {
        Status status = m_stub->CreateUDPConnection(&context, request, &reply);
        if (status.ok()) {
            LOG(warning) << reply.video_port() << ": " << reply.audio_port() << std::endl;
            audio_port = reply.audio_port();
            video_port = reply.video_port();
        } else {
            LOG(error) << status.error_code() << ": " << status.error_message()
                    << std::endl;
        }
    }
}

void GrpcClient::CreateStub()
{
    LOG(info) << __METHOD_NAME__ << endl;
    std::string target_str;
    target_str = "localhost:50051";
    std::shared_ptr<Channel> test_channel = grpc::CreateChannel(target_str, grpc::InsecureChannelCredentials());
    m_stub = VstGrpcServer::NewStub(test_channel);
    LOG(info) << "Created grpc client stub for target: " << target_str << endl;
}

void GrpcClient::CreateStub(string target)
{
    LOG(info) << __METHOD_NAME__ << endl;
    std::shared_ptr<Channel> test_channel = grpc::CreateChannel(target, grpc::InsecureChannelCredentials());
    m_stub.reset(nullptr);
    m_stub = VstGrpcServer::NewStub(test_channel);
    LOG(info) << "Created grpc client stub for target: " << target << endl;
}
#endif

#ifdef USE_GRPC_SERVER
GrpcServer::GrpcServer(std::shared_ptr<nv_vms::DeviceManager> deviceMngr,
                        std::shared_ptr<nv_vms::SensorManagement> sensorMngt):
                        m_deviceManager(deviceMngr),
                        m_udpService(deviceMngr)
{
    m_grpcThread = std::thread(&GrpcServer::RunServer, this);
    m_func["/grpc/remotePeerAnswer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        m_signallingService.remotePeerAnswer(in);
        return VmsErrorCode::NoError;
    };

    m_func["/grpc/remotePeerCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        m_signallingService.remotePeerCandidate(in);
        return VmsErrorCode::NoError;
    };
}

GrpcServer::~GrpcServer()
{
    try {
        if (m_grpcThread.joinable())
        {
            LOG(info) << "Waiting for m_grpcThread thread join" << endl;
            StopServer();
            m_grpcThread.join();
        }
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~GrpcServer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~GrpcServer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void GrpcServer::RunServer() {
    DeviceConfig& config = GET_CONFIG();
    bool use_grpc = config.enable_grpc;
    if (!use_grpc)
    {
        return;
    }
    string server_address = string(DEFAULT_IP) + string(":") + config.grpc_server_port;

    grpc::EnableDefaultHealthCheckService(true);
    grpc::reflection::InitProtoReflectionServerBuilderPlugin();
    ServerBuilder builder;
    // Listen on the given address without any authentication mechanism.
    builder.AddListeningPort(server_address, grpc::InsecureServerCredentials());
    builder.RegisterService(&m_udpService);
    builder.RegisterService(&m_signallingService);

    // Finally assemble the server.
    m_server = builder.BuildAndStart();
    if (m_server == nullptr)
    {
        LOG(error) << "Unable to Start grpc server" << endl;
        return;
    }
    LOG(info) << "gRPC Server listening on " << server_address << endl;

    // Wait for the server to shutdown. Note that some other thread must be
    // responsible for shutting down the server for this call to ever return.
    m_server->Wait();
}

void GrpcServer::StopServer()
{
    if (m_server)
    {
        LOG(info) << "Shutdown grpc server" << endl;
        m_server->Shutdown();
    }
}

void GrpcServer::addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func)
{
    m_udpService.addRequestHandler(func);
    m_signallingService.addRequestHandler(func);
}
#endif

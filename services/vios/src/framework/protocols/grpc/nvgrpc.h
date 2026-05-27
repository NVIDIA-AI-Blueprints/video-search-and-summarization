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

#pragma once
#include <memory>
#include <string>

#include "sensor_discovery_adaptor.h"
#include "sensor_management.h"
#include "HttpServerRequestHandler.h"

#include <grpcpp/grpcpp.h>
#include <grpcpp/ext/proto_server_reflection_plugin.h>
#include <grpcpp/health_check_service_interface.h>

#include "vstserver.grpc.pb.h"

#ifdef USE_GRPC_SERVER
using grpc::ServerContext;
#endif

#ifdef USE_GRPC_CLIENT
using grpc::Channel;
using grpc::ClientContext;
#endif

using vstserver::VstGrpcServer;
using vstserver::CreateUDPConnectionRequest;
using vstserver::CreateUDPConnectionReply;
using vstserver::CreateWebrtcConnectionRequest;
using vstserver::CreateWebrtcConnectionReply;
using vstserver::VstGrpcWebRTCSignalingServer;
using vstserver::Sdp;
using vstserver::IceCandidate;

#ifdef USE_GRPC_SERVER
class  GrpcUdpService final : public VstGrpcServer::Service
{
public:
    GrpcUdpService(std::shared_ptr<nv_vms::DeviceManager> deviceMngr): m_deviceManager(deviceMngr)
    {}

    ~GrpcUdpService()
    {}

    grpc::Status CreateUDPConnection(ServerContext* context, const CreateUDPConnectionRequest* request,
                            CreateUDPConnectionReply* reply) override;
    grpc::Status CreateWebrtcConnection(ServerContext* context, const CreateWebrtcConnectionRequest* request,
                            CreateWebrtcConnectionReply* response) override;

    void addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func);

     grpc::Status addDevice(std::shared_ptr<DeviceManager> deviceMngr,
                            const CreateUDPConnectionRequest* request,
                            CreateUDPConnectionReply* reply,
                            string& peerid, string& stream_id);
    std::string createUniqueStreamId(std::shared_ptr<SensorInfo> sensor);

private:
    std::shared_ptr<nv_vms::DeviceManager>      m_deviceManager;
    std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>         m_callbackMap;
};

class GrpcWebrtcSignallingService final : public VstGrpcWebRTCSignalingServer::Service
{
public:
    // struct to receive answer from UI
    struct RemotePeerAnswer
    {
        std::mutex              m_mtx;
        std::condition_variable m_cv;
        bool                    m_ready = false;
        Json::Value             m_answer;
    };

    // Queue to receive candidates from UI
    struct RemotePeerCandidate
    {
        std::mutex              m_mtx;
        std::condition_variable m_cv;
        bool                    m_ready = false;
        std::queue<Json::Value> m_candidateList;
    };

    void addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func);
    VmsErrorCode remotePeerAnswer(const Json::Value &in);
    VmsErrorCode remotePeerCandidate(const Json::Value &in);

    grpc::Status sdpExchange(ServerContext* context, const Sdp* request, Sdp* reply) override;
    grpc::Status iceCandidateExchange(ServerContext* context, grpc::ServerReaderWriter<IceCandidate, IceCandidate>* stream) override;
private:
    std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>   m_callbackMap;
    std::unordered_map<string, std::shared_ptr<RemotePeerAnswer>>   m_remoteAnswers;
    std::unordered_map<string, std::shared_ptr<RemotePeerCandidate>> m_candidates;
    std::mutex                                                      m_remoteAnswersMutex;
    std::mutex                                                      m_remoteCandidatesMutex;
    std::unordered_map<string, string>                              m_ipToStreamid;
};

class GrpcServer
{
public:
    GrpcServer(std::shared_ptr<nv_vms::DeviceManager> deviceMngr,
                std::shared_ptr<nv_vms::SensorManagement> sensorMngt);
    ~GrpcServer();

    void RunServer();
    void StopServer();

    void addRequestHandler(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func);
    const std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() { return m_func; };

private:
    std::unique_ptr<grpc::Server>                                   m_server;
    std::shared_ptr<nv_vms::DeviceManager>                          m_deviceManager;
    std::thread                                                     m_grpcThread;
    std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>   m_func;
    GrpcUdpService                                                  m_udpService;
    GrpcWebrtcSignallingService                                     m_signallingService;
};
#endif

#ifdef USE_GRPC_CLIENT
class GrpcClient
{
public:
    GrpcClient(std::shared_ptr<Channel> channel)
        : m_stub(VstGrpcServer::NewStub(channel)) {}
    GrpcClient();

    static GrpcClient* getInstance();
    static void deleteInstance();

    void CreateStub(string target);
    void CreateDummyUDPDevice(string connection_id, int32_t& audio_port, int32_t& video_port);

private:
    static GrpcClient* m_instance;
    void CreateStub();
    std::unique_ptr<VstGrpcServer::Stub> m_stub;
};
#endif
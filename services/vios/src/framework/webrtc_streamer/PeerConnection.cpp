/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "PeerConnection.h"
#include "api/rtc_event_log/rtc_event_log_factory.h"
#include "api/task_queue/default_task_queue_factory.h"
#include "media/engine/webrtc_media_engine.h"
#include "rtc_base/ssl_adapter.h"
#include "modules/audio_device/include/fake_audio_device.h"
#include "rtc_base/ref_counted_object.h"

#include "api/audio_codecs/builtin_audio_encoder_factory.h"
#include "api/audio_codecs/builtin_audio_decoder_factory.h"
#include "api/video_codecs/video_decoder_factory.h"
#include "api/video_codecs/video_decoder_factory_template.h"
#include "api/video_codecs/video_decoder_factory_template_libvpx_vp8_adapter.h"
#include "api/video_codecs/video_decoder_factory_template_open_h264_adapter.h"
#include "api/video_codecs/video_decoder_factory_template_libnv_adapter.h"
#include "api/video_codecs/video_decoder_factory_template_libnv_passthrough_adapter.h"
#include "api/video_codecs/video_encoder_factory.h"
#include "api/video_codecs/video_encoder_factory_template.h"
#include "api/video_codecs/video_encoder_factory_template_libvpx_vp8_adapter.h"
#include "api/video_codecs/video_encoder_factory_template_open_h264_adapter.h"
#include "api/video_codecs/video_encoder_factory_template_libnv_passthrough_adapter.h"

#include "CapturerFactory.h"
#include "profiler.h"
#include "elasticSearch.h"
#include "gst_utils.h"
#include "vst_common.h"
#include "utils.h"

using namespace vst_webrtc;

constexpr int START_WEBRTC_TIMEOUT = 5000;
constexpr const char* DATA_CHANNEL_LABEL = "VST_CLIENT";
constexpr int DEFAULT_HEIGHT = 1440;

#define CHECK_VALID_QUALITY(quality) \
    do { \
        if (quality != "high" &&    \
            quality != "medium" &&  \
            quality != "low" &&     \
            quality != "pass_through") \
        { \
            LOG(error) << "Invalid quality " << quality << ". Selecting " << DEFAULT_WEBRTC_SENDER_QUALITY << endl; \
            quality = DEFAULT_WEBRTC_SENDER_QUALITY; \
        } \
    } while(0)

// Names used for a IceCandidate JSON object.
const char kCandidateSdpMidName[] = "sdpMid";
const char kCandidateSdpMlineIndexName[] = "sdpMLineIndex";
const char kCandidateSdpName[] = "candidate";

// Names used for a SessionDescription JSON object.
const char kSessionDescriptionTypeName[] = "type";
const char kSessionDescriptionSdpName[] = "sdp";

struct PeerData : public EventLoopData
{
    Json::Value m_queryParams;
    Json::Value m_dataParams;
};

struct PeerOutData : public EventLoopOutData
{
    Json::Value m_response;
    nv_vms::VmsErrorCode m_error;
};

webrtc::PeerConnectionFactoryDependencies CreatePeerConnectionFactoryDependencies(
    rtc::scoped_refptr<webrtc::AudioDeviceModule> audioDeviceModule,
    rtc::scoped_refptr<webrtc::AudioDecoderFactory> audioDecoderfactory,
    rtc::Thread* signalingThread, rtc::Thread* workerThread,
    std::unordered_map<std::string, std::string>& opts)
{
    webrtc::PeerConnectionFactoryDependencies dependencies;
    dependencies.network_thread = nullptr;
    dependencies.worker_thread = workerThread;
    dependencies.signaling_thread = signalingThread;
    dependencies.call_factory = webrtc::CreateCallFactory();
    dependencies.task_queue_factory = webrtc::CreateDefaultTaskQueueFactory();
    dependencies.event_log_factory = absl::make_unique<webrtc::RtcEventLogFactory>(dependencies.task_queue_factory.get());

    cricket::MediaEngineDependencies mediaDependencies;
    mediaDependencies.task_queue_factory = dependencies.task_queue_factory.get();
    mediaDependencies.adm = std::move(audioDeviceModule);
    mediaDependencies.audio_encoder_factory = webrtc::CreateBuiltinAudioEncoderFactory();
    mediaDependencies.audio_decoder_factory = webrtc::CreateBuiltinAudioDecoderFactory();
    mediaDependencies.audio_processing = webrtc::AudioProcessingBuilder().Create();

    bool enc_passthrough = false;
    if(opts.find("quality") != opts.end())
    {
        enc_passthrough = opts.at("quality") == PASS_THROUGH_QUALITY ? true : false;
    }

    bool is_recorded_playback = false;
    if(opts.find("recorded_playback") != opts.end())
    {
        is_recorded_playback = opts.at("recorded_playback") == "true" ? true : false;
    }

    // VST will default work in pass through mode only for live playback
    if (!is_recorded_playback &&
        (GET_CONFIG().webrtc_out_encode_fallback_option == WEBRTC_OUT_FALLBACK_PASS_THROUGH ||
        enc_passthrough))
    {
        enc_passthrough = true;
        if ( opts.find("codec") != opts.end() )
        {
            string codec = opts.at("codec");
            /* Pass thru is unsupported for h265 webrtc playback in non edge to cloud use case */
            if (iequals(codec, "H265") && GET_CONFIG().remote_vst_address.empty())
            {
                enc_passthrough = false;
            }
        }
    }

    string use_inbuilt_encoder = GET_CONFIG().use_webrtc_inbuilt_encoder;
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc || enc_passthrough)
    {
        LOG(info) << "using LibNvPassthroughVideoEncoderTemplateAdapter" << endl;
        mediaDependencies.video_encoder_factory = std::make_unique<
            webrtc::VideoEncoderFactoryTemplate<webrtc::LibNvPassthroughVideoEncoderTemplateAdapter>>();
    }
    else
    {
        if (use_inbuilt_encoder.empty() == false)
        {
            if (iequals(use_inbuilt_encoder, "h264"))
            {
                LOG(info) << "using OpenH264EncoderTemplateAdapter" << endl;
                mediaDependencies.video_encoder_factory = std::make_unique<
                        webrtc::VideoEncoderFactoryTemplate<webrtc::OpenH264EncoderTemplateAdapter>>();
            }
            else if (iequals(use_inbuilt_encoder, "vp8"))
            {
                LOG(info) << "using LibvpxVp8EncoderTemplateAdapter" << endl;
                mediaDependencies.video_encoder_factory = std::make_unique<
                        webrtc::VideoEncoderFactoryTemplate<webrtc::LibvpxVp8EncoderTemplateAdapter>>();
            }
            else
            {
                LOG(warning) << "Provided encoder value is wrong, Using default h264 encoder" << endl;
                LOG(info) << "using OpenH264EncoderTemplateAdapter" << endl;
                mediaDependencies.video_encoder_factory = std::make_unique<
                        webrtc::VideoEncoderFactoryTemplate<webrtc::OpenH264EncoderTemplateAdapter>>();
            }
        }
        else
        {
            LOG(info) << "using OpenH264EncoderTemplateAdapter" << endl;
            mediaDependencies.video_encoder_factory = std::make_unique<
                webrtc::VideoEncoderFactoryTemplate<webrtc::OpenH264EncoderTemplateAdapter>>();
        }
    }

#ifdef JETSON_PLATFORM
    if (GET_CONFIG().use_webrtc_hw_dec && NvHwDetection::getInstance()->m_useNvV4l2Dec
        && NvHwDetection::getInstance()->m_useNvV4l2Enc)
    {
        LOG(info) << "using LibNvVideoDecoderTemplateAdapter" << endl;
        mediaDependencies.video_decoder_factory = std::make_unique<
            webrtc::VideoDecoderFactoryTemplate<webrtc::LibNvVideoDecoderTemplateAdapter>>();
    }
    else
    {
        LOG(info) << "using vp8 and h264 decoder" << endl;
        mediaDependencies.video_decoder_factory = std::make_unique<
            webrtc::VideoDecoderFactoryTemplate<webrtc::LibvpxVp8DecoderTemplateAdapter,
                                                webrtc::OpenH264DecoderTemplateAdapter>>();
    }
#else
    bool dec_passthrough = false;
    if(opts.find("decoder_factory") != opts.end())
    {
        dec_passthrough = opts.at("decoder_factory") == DECODER_FACTORY_PASS_THROUGH ? true : false;
    }
    if (dec_passthrough)
    {
        LOG(info) << "using LibNvPassthroughVideoDecoderTemplateAdapter" << endl;
        mediaDependencies.video_decoder_factory = std::make_unique<
            webrtc::VideoDecoderFactoryTemplate<webrtc::LibNvPassthroughVideoDecoderTemplateAdapter>>();
    }
    else if (GET_CONFIG().use_webrtc_hw_dec && NvHwDetection::getInstance()->m_useNvV4l2Dec
        && NvHwDetection::getInstance()->m_useNvV4l2Enc)
    {
        LOG(info) << "using LibNvVideoDecoderTemplateAdapter" << endl;
        mediaDependencies.video_decoder_factory = std::make_unique<
            webrtc::VideoDecoderFactoryTemplate<webrtc::LibNvVideoDecoderTemplateAdapter>>();
    }
    else
    {
        LOG(info) << "using vp8 and h264 decoder" << endl;
        mediaDependencies.video_decoder_factory = std::make_unique<
            webrtc::VideoDecoderFactoryTemplate<webrtc::LibvpxVp8DecoderTemplateAdapter,
                                                webrtc::OpenH264DecoderTemplateAdapter>>();
    }
#endif

    dependencies.media_engine = cricket::CreateMediaEngine(std::move(mediaDependencies));

    return dependencies;
}

void process_pc_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<PeerData> in_data = std::static_pointer_cast<PeerData>(data);
    shared_ptr<PeerOutData> out_data = std::static_pointer_cast<PeerOutData>(data->m_outResult);
    PeerConnection* peer = static_cast <PeerConnection*>(parent);
    if (in_data == nullptr || peer == nullptr)
    {
        LOG(error) << "Received null in data" << endl;
        return;
    }

    Json::Value in = in_data->m_dataParams;
    Json::Value out;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    const string query_string = in_data->m_queryParams.get("query", EMPTY_STRING).asString();
    const string request_method = in_data->m_queryParams.get("method", UNKNOWN_STRING).asString();
    string peerid;

    if(in_data->m_expectResult)
    {
        if(out_data.get() == nullptr)
        {
            LOG(error) << "Received null out data" << endl;
            return;
        }
    }

    if(in_data->m_taskName == "call")
    {
        error_code = peer->call(in_data->m_queryParams, in_data->m_dataParams, out);
    }
    else if(in_data->m_taskName == "removeTracks")
    {
        peer->removeTracks(in);
    }
    else if(in_data->m_taskName == "toggleStream")
    {
        error_code = peer->toggleStream(in_data->m_queryParams, in, out);
    }
    else if(in_data->m_taskName == "pause")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->controlStream("pause", peerid, in, out);
    }
    else if(in_data->m_taskName == "resume")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        error_code = peer->controlStream("resume", peerid, in, out);
    }
    else if(in_data->m_taskName == "seek")
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
        string action = in.get("action", EMPTY_STRING).asString();
        error_code = peer->controlStream(action, peerid, in, out);
    }
    else if(in_data->m_taskName == "getPosition")
    {
        peerid = in_data->m_msgId;
        error_code = peer->getCurrentPosition(peerid, in, out);
    }
    else if(in_data->m_taskName == "stats")
    {
        peer->getStats(out);
    }
    else if(in_data->m_taskName == "query")
    {
        error_code = peer->getQuery(in_data->m_queryParams, in, out);
    }
    else if(in_data->m_taskName == "getStartTime")
    {
        string mediaSessionId = "";
        CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
        error_code = peer->getStartTime(mediaSessionId, out);
    }
    else if(in_data->m_taskName == "getDurationStream")
    {
        string mediaSessionId = "";
        CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
        error_code = peer->getDurationStream(mediaSessionId, out);
    }
    else if(in_data->m_taskName == "status")
    {
        string overlay = "", mediaSessionId = "";
        CivetServer::getParam(query_string, "peerid", peerid);
        CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
        if(!CivetServer::getParam(query_string, "overlay", overlay))
        {
            overlay = "false";
        }
        error_code = peer->getStatus(in_data->m_msgId, mediaSessionId, overlay, out);
    }
    else if(in_data->m_taskName == "getIceCandidate")
    {
        peer->getIceCandidateList(out);
    }
    else if(in_data->m_taskName == "addIceCandidate")
    {
        peerid = in.get("peerId", EMPTY_STRING).asString();
        Json::Value candidate = in.get("candidate", EMPTY_STRING);
        error_code = peer->addIceCandidate(peerid, candidate, out);
    }
    else if(in_data->m_taskName == "getPeerConnectionList")
    {
        error_code = peer->getPeerConnectionList(out);
    }
    else if(in_data->m_taskName == "getStreamList")
    {
        error_code = peer->getStreamList(out);
    }
    else if(in_data->m_taskName == "setAnswer")
    {
        Json::Value jmessage = in.get("sessionDescription", EMPTY_STRING);
        error_code = peer->setAnswer(jmessage, out);
    }
    else if(in_data->m_taskName == "setAudioPlayout")
    {
        bool value = in.get("audioPlayout", false).asBool();
        peer->setAudioPlayout(value);
    }
    else if (in_data->m_taskName == "addUdpTrack")
    {
        string sensorId = in.get("device_id", EMPTY_STRING).asString();
        string stream_id = in.get("stream_id", EMPTY_STRING).asString();
        error_code = peer->addUdpTrack(sensorId, stream_id);
    }
    else if (in_data->m_taskName == "createOffer")
    {
        error_code = peer->createOffer(in, out);
    }
    else if (in_data->m_taskName == "startConnection")
    {
        error_code = peer->startConnection(in_data->m_queryParams, in_data->m_dataParams, out);
    }
    else if(in_data->m_taskName == "streamSettings")
    {
        peerid = in.get("peerId", EMPTY_STRING).asString();
        error_code = peer->streamSettings(peerid, in, out);
    }
    else if(in_data->m_taskName == "setOffer")
    {
        error_code = peer->setOffer(in, out);
    }
    else if(in_data->m_taskName == "getAnswer")
    {
        error_code = peer->getAnswer(in, out);
    }
    else
    {
        LOG(warning) << "Invalid message received " << endl;
    }
    if(in_data->m_expectResult)
    {
        out_data->m_response = out;
        out_data->m_error = error_code;
    }
}


PeerConnection::PeerConnection(PeerConnectionManager* peerConnectionManager,
                                const std::string& peerid,
                                const webrtc::PeerConnectionInterface::RTCConfiguration & config,
                                std::unordered_map<std::string, std::string>& opts)
            : m_signalingThread(rtc::Thread::Create())
            , m_workerThread(rtc::Thread::Create())
            , m_peerConnectionManager(peerConnectionManager)
            , m_peerid(peerid)
            , m_deleting(false)
            , m_publishFilter(std::string(".*"))
            , m_audioDecoderfactory(webrtc::CreateBuiltinAudioDecoderFactory())
            , m_eventLoop("peerconnection_event_loop", process_pc_message)
            , m_deviceManager(m_peerConnectionManager->getDeviceManager())
            , m_prevTimestamp(0)
            , m_prevBytesReceived(0)
            , m_streamStats(peerid)
{
    m_workerThread->SetName("worker_" + peerid, nullptr);
    m_workerThread->Start();

    m_workerThread->BlockingCall([this] {
        m_audioDeviceModule = new webrtc::FakeAudioDeviceModule();
    });

    m_signalingThread->SetName("signaling_" + peerid, nullptr);
    m_signalingThread->Start();

    m_peer_connection_factory = webrtc::CreateModularPeerConnectionFactory(
        CreatePeerConnectionFactoryDependencies(m_audioDeviceModule, m_audioDecoderfactory,
        m_signalingThread.get(), m_signalingThread.get(), opts)
    );
    m_observer.reset(new PeerConnectionObserver(this, peerid));
    webrtc::PeerConnectionDependencies dependencies(m_observer.get());
    LOG(verbose) << __FUNCTION__ << "CreatePeerConnection peerid:" << peerid;
    auto result = m_peer_connection_factory->CreatePeerConnectionOrError(config, std::move(dependencies));
    bool enableDataChannel = false;
    if (!result.ok())
    {
        LOG(error) << "Error from webrtc" << result.error().message() << endl;
        throw std::invalid_argument("Failed to create peer connection due to webrtc internal error");
        m_pc = nullptr;
    }
    else
    {
        m_pc = result.MoveValue();
    }

    // Initiate WebRTC data channel
    if(opts.find("isDataChannel") != opts.end())
    {
        enableDataChannel = opts.at("isDataChannel") == "true" ? true : false;
    }
    if (enableDataChannel)
    {
        webrtc::DataChannelInit dataChannelConfig;
        auto dataChannelCreationResult = m_pc->CreateDataChannelOrError(DATA_CHANNEL_LABEL, &dataChannelConfig);
        if (!dataChannelCreationResult.ok())
        {
            LOG(error) << "Failed to create data channel " << dataChannelCreationResult.error().message() << endl;
        }
        else
        {
            LOG(info) << "Data channel created successfully" << endl;
            rtc::scoped_refptr<webrtc::DataChannelInterface> dataChannel = dataChannelCreationResult.MoveValue();
            if (m_deviceManager != nullptr)
            {
                GET_DATA_CHANNEL()->addChannelObserver(m_deviceManager->getDeviceId(), dataChannel);
            }
        }
    }

    m_statsCallback = rtc::make_ref_counted<PeerConnectionStatsCollectorCallback>();
    m_eventLoop.setParent(this);
}

PeerConnection::~PeerConnection()
{
    LOG(info) << __PRETTY_FUNCTION__ << endl;
    m_deleting = true;
    if (m_localPort > 0)
    {
        UdpClientPool::getInstance()->freeWebrtcUdpPort(m_localPort);
    }
    GET_DATA_CHANNEL()->removeChannelObserver(m_peerid);
    if (m_pc.get())
    {
        m_pc->Close();
    }
    m_observer->shutdown();

    // Destroy PeerConnection and Factory BEFORE deleting FakeADM.
    // The factory holds an internal pointer to the ADM (via MediaEngine).
    // FakeADM has no-op AddRef/Release, so there is no real ref counting.
    // We must tear down in dependency order: PC → Factory → ADM.
    m_pc = nullptr;
    m_peer_connection_factory = nullptr;
    m_workerThread->BlockingCall([this] {
        auto* fakeAdm = static_cast<webrtc::FakeAudioDeviceModule*>(m_audioDeviceModule.get());
        m_audioDeviceModule = nullptr;
        delete fakeAdm;
    });
}

VmsErrorCode PeerConnection::post(const string& taskName, const string& peerId,
                    Json::Value in, Json::Value req_info, Json::Value& response,
                    bool is_sync, uint32_t timeout)
{
    return postToEventLoop(taskName, peerId, in, req_info, response, is_sync, timeout);
}

void PeerConnection::closePeerConnection()
{
    if (m_deleting == true)
    {
        /* Already deletion in progress, ignore it */
        return;
    }
    std::shared_ptr<nv_vms::DeviceManager> deviceManager = m_peerConnectionManager->getDeviceManager();
    shared_ptr<SensorInfo> sensor = deviceManager->getSensorInfo(m_deviceId);
    // Skip sensor deletion for Edge device
    if (sensor.get() && (sensor->type == SENSOR_TYPE_WEBRTC || sensor->type == SENSOR_TYPE_UDP))
    {
        LOG(warning) << "calling delete for " << m_deviceId << endl;
        m_peerConnectionManager->notify("camera_remove", m_deviceId);
        vst_common::deleteWebrtcSensor(m_deviceId, deviceManager);
    }
    else if (sensor.get() && sensor->type == SENSOR_TYPE_REMOTE)
    {
        m_peerConnectionManager->notify("camera_remove", m_deviceId);
        // remove stream only
        deviceManager->removeStreamsFromSensor(m_deviceId);
    }

    if (GET_CONFIG().use_reverse_proxy == true && m_peerConnectionManager->isRpStunAvailable() == false)
    {
        deleteSeatFromRP(m_peerid);
        if (GET_CONFIG().use_external_peerconnection)
        {
            string session_id = m_peerid + "_1";
            deleteSeatFromRP(session_id);
        }
    }
    WebrtcStreamProducer::getInstance()->removeStreamProducer (m_deviceId);
    if (!m_deleting)
    {
        m_deleting = true;
        std::thread([this]()
        {
            Json::Value value;
            LOG(info) << "OnIceConnectionChange: hangUp: " << m_peerid << endl;
            Json::Value in;
#ifdef ASYNC_API
            in["peerid"] = m_peerid;
            m_peerConnectionManager->postToEventLoop("hangUp", m_peerid, in, in, value);
#else
            m_peerConnectionManager->hangUp(m_peerid, in, value);
#endif
        }).detach();
    }
    if(GET_CONFIG().use_multi_user && GET_CONFIG().enable_user_cleanup)
    {
        GET_DB_INSTANCE()->deleteUserDetails(m_peerid);
    }
}

void PeerConnection::getStats(Json::Value& content)
{
    Json::Value stats;
    webrtc::PeerConnectionInterface::IceConnectionState ice_state = m_pc->standardized_ice_connection_state();
    if (ice_state == webrtc::PeerConnectionInterface::IceConnectionState::kIceConnectionConnected ||
        ice_state == webrtc::PeerConnectionInterface::IceConnectionState::kIceConnectionCompleted)
    {
        m_statsCallback->clearReport();
        m_pc->GetStats(m_statsCallback.get());
        int count=10;
        while (m_statsCallback->getReport().empty() && count > 0) {
            --count;
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        }
        stats = Json::Value(m_statsCallback->getReport());
        stats["timestamp"] = std::to_string(getCurrentUnixTimestamp());
        stats["transportId"] = m_statsCallback->getTransportID();
        content["stats"] = stats;
    }
    else
    {
        /* ICE state is not in conencted state, so ignore the stats call */
        LOG(info) << "ICE state is not in connected state, so ignore the stats call" << endl;
        return;
    }
}

void PeerConnection::isIceCandidateAdded()
{
    if(!m_isIceConnected)
    {
        if (!m_deleting)
        {
            m_deleting = true;
            std::thread([this]()
            {
                Json::Value value;
                LOG(info) << "Ice candidate not received: hangUp: " << m_peerid << endl;
                Json::Value in;
#ifdef ASYNC_API
                in["peerid"] = m_peerid;
                m_peerConnectionManager->postToEventLoop("hangUp", m_peerid, in, in, value);
#else
                m_peerConnectionManager->hangUp(m_peerid, in, value);
#endif
            }).detach();
        }
    }
    else
    {
        LOG(info) << "Ice candidate added" << endl;
    }
}

VmsErrorCode
PeerConnection::postToEventLoop(const string& task_name, const string& peerid,
                                Json::Value in, Json::Value req_info,
                                Json::Value& response, bool is_sync, uint32_t timeout)
{
    std::shared_ptr<PeerData> in_data(new PeerData);
    in_data->m_taskName = task_name;
    in_data->m_msgId = peerid;
    in_data->m_queryParams = req_info;
    in_data->m_dataParams = in;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    std::shared_ptr<PeerOutData> out_data;
    if(is_sync)
    {
        out_data.reset(new PeerOutData);
        if (timeout)
        {
            out_data->m_timeout = timeout;
        }
        in_data->m_outResult = out_data;
        in_data->m_expectResult = is_sync;
    }
    bool ret = m_eventLoop.postMsg(in_data);
    if(is_sync && ret)
    {
        if (out_data.get())
        {
            response = out_data->m_response;
            error_code = out_data->m_error;
        }
    }
    else
    {
        if (ret)
        {
            response = true;
            error_code = VmsErrorCode::NoError;
        }
        else
        {
            response = false;
            error_code = VmsErrorCode::VMSInternalError;
        }
    }
    return error_code;
}

VmsErrorCode PeerConnection::addIceCandidate(const std::string &peerid,
                                            const Json::Value &jmessage,
                                            Json::Value& value)
{
    bool result = false;
    std::string sdp_mid;
    int sdp_mlineindex = 0;
    std::string sdp;

    if (GET_CONFIG().use_reverse_proxy == true && m_peerConnectionManager->isRpStunAvailable() == false)
    {
        /* Ignore the ice-candidates if RP is enabled */
        value = true;
        return VmsErrorCode::NoError;
    }

    if (!rtc::GetStringFromJsonObject(jmessage, kCandidateSdpMidName, &sdp_mid) ||
        !rtc::GetIntFromJsonObject(jmessage, kCandidateSdpMlineIndexName, &sdp_mlineindex) ||
        !rtc::GetStringFromJsonObject(jmessage, kCandidateSdpName, &sdp))
    {
        LOG(warning) << "Can't parse received message:" << jmessage << endl;
    }
    else
    {
        LOG(info) << "Received remote IceCandidate:" << sdp << endl;
        std::unique_ptr<webrtc::IceCandidateInterface> candidate(webrtc::CreateIceCandidate(sdp_mid, sdp_mlineindex, sdp, nullptr));
        if (!candidate.get())
        {
            LOG(warning) << "Can't parse received candidate message." << endl;
        }
        else
        {
            rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = this->getRtcPeerConnection();
            if (rtcPeerConnection)
            {
                if (!isRemoteDescriptionSet())
                {
                    LOG(info) << "Received early candidate, caching it."<< endl;
                    std::lock_guard<std::mutex> lock(m_earlyRemoteCandidatesMutex);
                    m_earlyRemoteCandidates.push_back(std::move(candidate));
                    result = true;
                }
                else if (rtcPeerConnection->AddIceCandidate(candidate.get()))
                {
                    result = true;
                }
                else
                {
                    LOG(error) << "Failed to apply the received candidate"<< endl;
                }
            }
            else
            {
                LOG(error) << "No peer connection found for peerId: " << peerid << endl;
            }
        }
    }
    if (result)
    {
        value = result;
        return VmsErrorCode::NoError;
    }
    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, "Failed to add Ice Candidate")
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode PeerConnection::setAnswer(const Json::Value &jmessage, Json::Value& answer)
{
    LOG(verbose) << jmessage;

    std::string type;
    std::string sdp;
    if (!rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionTypeName, &type) || !rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionSdpName, &sdp))
    {
        LOG(warning) << "Can't parse received message.";
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, answer, "Can't parse received message.")
        return VmsErrorCode::InvalidParameterError;
    }
    else
    {
        webrtc::SessionDescriptionInterface *session_description(webrtc::CreateSessionDescription(type, sdp, nullptr));
        if (!session_description)
        {
            LOG(warning) << "Can't parse received session description message.";
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Can't parse received session description message.")
            return VmsErrorCode::VMSInternalError;
        }
        else
        {
            if (m_pc)
            {
                std::promise<const webrtc::SessionDescriptionInterface *> remotepromise;
                std::string sdp;
                rtc::scoped_refptr<SetSessionDescriptionObserver> remoteSessionObserver(SetSessionDescriptionObserver::Create(m_pc.get(), remotepromise,sdp));
                m_pc->SetRemoteDescription(remoteSessionObserver.get(), session_description);
                // waiting for remote description
                std::future<const webrtc::SessionDescriptionInterface *> remotefuture = remotepromise.get_future();
                if (remotefuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
                {
                    LOG(info) << "remote_description is ready" << endl;
                    processRemoteCandidatesFromCache();
                    const webrtc::SessionDescriptionInterface *desc = remotefuture.get();
                    if (desc)
                    {
                        std::string sdp;
                        desc->ToString(&sdp);

                        answer[kSessionDescriptionTypeName] = desc->type();
                        answer[kSessionDescriptionSdpName] = sdp;
                    }
                    else
                    {
                        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Can't get remote description")
                        return VmsErrorCode::VMSInternalError;
                    }
                }
                else
                {
                    LOG(warning) << "Can't get remote description.";
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Can't get remote description")
                    return VmsErrorCode::VMSInternalError;
                }
            }
        }
    }
    return VmsErrorCode::NoError;
}

bool PeerConnection::removeAudioTrack(std::string peerid)
{
    bool removed = false;
    /* Remove Audio Track in case of recorded playback request */
    std::vector<rtc::scoped_refptr<webrtc::RtpSenderInterface>> senders = m_pc->GetSenders();
    for (auto stream : senders)
    {
        if (stream->media_type() == cricket::MediaType::MEDIA_TYPE_AUDIO)
        {
            if(m_pc->RemoveTrackOrError(stream).ok())
            {
                LOG(info) << "Audio track removed" << endl;
                removed = true;
                break;
            }
        }
    }
    return removed;
}

bool PeerConnection::isRemoteDescriptionSet()
{
    if (m_pc.get())
    {
        return m_pc->current_remote_description() != nullptr;
    }
    return false;
}

void PeerConnection::processRemoteCandidatesFromCache()
{
    std::lock_guard<std::mutex> lock(m_earlyRemoteCandidatesMutex);
    LOG(info) << "Processing early candidates, total early candidates are " << m_earlyRemoteCandidates.size() << endl;
    if (isRemoteDescriptionSet())
    {
        for (auto& candidate : m_earlyRemoteCandidates)
        {
            if (m_pc.get())
            {
                if (!m_pc->AddIceCandidate(candidate.get()))
                {
                    LOG(error) << "Failed to apply the early ICE candidate" << endl;
                }
            }
            else
            {
                LOG(error) << "Failed to add early ICE candidate, Peer Connection not found" << endl;
            }
        }
        m_earlyRemoteCandidates.clear();
    }
    else
    {
        LOG(error) << "Failed to add early ICE candidate. remote_description need to be set before adding early candidates" << endl;
    }
}

VmsErrorCode
PeerConnection::addAudioTrack(rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource
                            , rtc::scoped_refptr<webrtc::AudioSourceInterface> audioSource
                            , std::string peerid, std::string video
                            , std::map<std::string, std::string, std::less<>> opts, Json::Value &value)
{
    VmsErrorCode code = VmsErrorCode::NoError;
    /* Add Audio Track in of live playback request */
    if (audioSource == nullptr)
    {
        try
        {
            audioSource = this->CreateAudioSource(video, opts);
        }
        catch(const std::invalid_argument& e)
        {
            string err_msg = e.what();
            LOG(error) << err_msg << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, err_msg.c_str())
            return VmsErrorCode::VMSInternalError;
        }
    }

    if (audioSource)
    {
        m_streamMap[peerid] = std::make_pair(videoSource, audioSource);
        rtc::scoped_refptr<webrtc::AudioTrackInterface> audio_track = nullptr;
        audio_track = m_peer_connection_factory->CreateAudioTrack(peerid + "_audio", audioSource.get());
        if ((audio_track) && (!m_pc->AddTrack(audio_track, {peerid}).ok()))
        {
            string err_msg = "Adding audio track failed";
            LOG(error) << err_msg << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, err_msg.c_str());
            return VmsErrorCode::VMSInternalError;
        }
        else
        {
            LOG(verbose) << "stream added to PeerConnection" << endl;
        }
    }
    return code;
}

VmsErrorCode
PeerConnection::toggleStream(const Json::Value& req_info, const Json::Value &in, Json::Value &value)
{
    MEASURE_FUNCTION_EXECUTION_TIME

    /* Declare the data structure */
    std::string peerid;
    std::string streamId;
    std::string startTime;
    std::string endTime;
    std::string remote_addr;
    std::string video;
    std::string mediaSessionId;

    /* Initialize the data structure */
    peerid          = in.get("peerId", EMPTY_STRING).asString();
    streamId        = in.get("streamId", EMPTY_STRING).asString();
    startTime       = in.get("startTime", EMPTY_STRING).asString();
    endTime         = in.get("endTime", EMPTY_STRING).asString();
    mediaSessionId  = in.get("mediaSessionId", EMPTY_STRING).asString();
    remote_addr     = req_info.get("remoteAddr", EMPTY_STRING).asString();

    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
    if (remote_addr.empty())
    {
        remote_addr = req_info.get("remote_addr", EMPTY_STRING).asString();
    }

    /* Populate the opts map */
    std::map<std::string, std::string, std::less<>> opts;

    LOG(verbose) << __FUNCTION__ << " peerid:" << peerid  << " streamId:" << streamId
                << " startTime:" << startTime << " endTIme:" << endTime << endl;

    /* Sending values in opts */
    opts["peerid"] = peerid;

    /* Create URL for recorded playback */
    shared_ptr<SensorInfo> sensor = m_deviceManager->searchSensor(streamId);
    if (sensor)
    {
        shared_ptr<StreamInfo> stream_info = sensor->getStream (streamId);
        if (stream_info)
        {
            // set framerate for swap api usecase
            opts["framerate"] = stream_info->settings.encoderValues.frameRate;
            opts["isBframesPresent"] = stream_info->settings.encoderValues.isBframesPresent ? "true" : "false";
            if (!startTime.empty() || !endTime.empty())
            {
                /* Erase unwanted characters from user given timestamps */
                string tmp("-");
                eraseString(startTime, tmp);
                eraseString(endTime, tmp);
                tmp = ":";
                eraseString(startTime, tmp);
                eraseString(endTime, tmp);

                video = stream_info->replay_url + "?startTime=" + startTime.c_str() + "&endTime=" + endTime.c_str();
                LOG(info) << "REPLAY URL = " << video << endl;
            }
            /* Create URL for live playback */
            else
            {
                video = stream_info->live_proxy_url.empty() ? stream_info->live_url : stream_info->live_proxy_url;
                LOG(info) << "LIVE  URL = " << video << endl;
            }
        }
        else
        {
            string msg = "Stream not found";
            LOG(error) << msg << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, msg.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
    }
    else
    {
        string msg = "Device not found";
        LOG(error) << msg << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, msg.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, value);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    /* If usecase of replay playback */
    if (video.find("startTime") != string::npos && sensor->type != SENSOR_TYPE_MMS_ONVIF)
    {
        /* Replace rtsp with file */
        bool audio_removed = false;
        bool ret = replaceString(video, "rtsp://", "file://");
        if(!ret)
        {
            LOG(error) << "Replace string failed" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, "Error Ocurred, please try again");
            return VmsErrorCode::VMSInternalError;
        }

        audio_removed = removeAudioTrack (peerid);
        LOG(info) << "Audio Removed ? " << audio_removed << endl;
    }
/* TODO implement Audio Support later */
#ifdef AUDIO_SUPPORTED
    /* else usecase of live playback */
    else
    {
        VmsErrorCode audio_error = addAudioTrack(videoSource, audioSource, peerid, video, opts, value);
        if (audio_error != VmsErrorCode::NoError)
        {
            LOG(error) << "Stream Error for "<< streamId << endl;
            return audio_error;
        }
    }
#endif
    TrackSource<NvGstVideoCapturer>* video_capturer = static_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);

    if (video_capturer != nullptr)
    {
        try
        {
            video_capturer->switchStreamTrackSource(video, opts);
            value = true;
        }
        catch(const std::invalid_argument& e)
        {
            string err_msg = e.what();
            LOG(error) << err_msg << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, err_msg.c_str())
            return VmsErrorCode::VMSInternalError;
        }
    }

    return VmsErrorCode::NoError;
}

std::string PeerConnection::addWebrtcBitrateToSDP(const Json::Value& in, const std::string& sdp)
{
    /* Default values */
    string start_bitrate = to_string(DEFAULT_WEBRTC_START_BITRATE);
    string min_bitrate = to_string(DEFAULT_WEBRTC_MIN_BITRATE);
    string max_bitrate = to_string(DEFAULT_WEBRTC_MAX_BITRATE);

    string streamId = in.get("streamId", EMPTY_STRING).asString();
    shared_ptr<StreamInfo> stream_info = m_deviceManager->getStream(m_deviceId, streamId);
    uint32_t height = DEFAULT_HEIGHT;
    Resolution resolution;
    resolution = GET_CONFIG().webrtc_out_default_resolution;
    if (!resolution.empty())
    {
        height = stringToInt(resolution.height, DEFAULT_HEIGHT);
    }
    else if (stream_info)
    {
        height = stringToInt(stream_info->settings.encoderValues.resolution.height, DEFAULT_HEIGHT);
    }
    Json::Value webrtc_video_quality_tunning = VmsConfigManager::getInstance()->getWebrtcVideoQualityValues(height);
    if (webrtc_video_quality_tunning != Json::nullValue)
    {
        unsigned int value_start_bitrate = webrtc_video_quality_tunning["bitrate_start"].asInt();
        start_bitrate = to_string(value_start_bitrate);

        std::vector<int> bitrate_range = jsonArrayToVector(webrtc_video_quality_tunning["bitrate_range"]);
        if (bitrate_range.size() == 2)
        {
            min_bitrate = to_string(bitrate_range[0]);
            max_bitrate = to_string(bitrate_range[1]);
        }
    }
    LOG(info) << "Bitrate settings for height:" << height << ", start_bitrate:" << start_bitrate << ", range:" << min_bitrate << ", " << max_bitrate << endl;

    std::string sdpStringFind = "a=fmtp:(.*) (.*)";
    std::string sdpStringReplace = "a=fmtp:$1 $2;x-google-max-bitrate=" + max_bitrate +
                                    ";x-google-min-bitrate=" + min_bitrate +
                                    ";x-google-start-bitrate=" + start_bitrate;

    std::regex regex(sdpStringFind);
    std::string newSDP = std::regex_replace(sdp, regex, sdpStringReplace);
    return newSDP;
}

VmsErrorCode
PeerConnection::call(const Json::Value& req_info, const Json::Value &in, Json::Value &answer)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    std::string peerid;
    std::string audiourl;
    std::string options;
    std::string streamId;
    std::string sensorId;
    std::string startTime;
    std::string endTime;
    std::string remote_addr;
    std::string username;
    bool is_client = false;
    bool is_dataChannel = false;
    Json::Value optionsJson;
    vector<string> list_sensorids;
    bool is_composite_stream = false;
    unordered_map<string, string> urlParameters;

    peerid = in.get("peerId", EMPTY_STRING).asString();
    streamId = in.get("streamId", EMPTY_STRING).asString();
    Json::Value jsensor = in.get("sensorId", EMPTY_STRING);
    std::map<std::string, std::string, std::less<>> opts_map =  getStreamOptions(in);
    if (opts_map.find("doComposite") != opts_map.end())
    {
        if (opts_map.find("streamIds") != opts_map.end())
        {
            string objects = opts_map.at("streamIds");
            list_sensorids = splitString(objects, ",");
            if (list_sensorids.size())
            {
                is_composite_stream = true;
            }
            else
            {
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, answer, "Stream IDs not found")
                return VmsErrorCode::InvalidParameterError;
            }
        }
    }
    else if (jsensor.isString())
    {
        sensorId = jsensor.asString();
    }

    if (opts_map.find("showSensorName") != opts_map.end())
    {
        urlParameters["overlayShowSensorName"] = true;
        auto it2 = opts_map.find("showSensorNamePosition");
        if (it2 != opts_map.end() && !it2->second.empty())
        {
            string xyposition = it2->second;
            auto tokens = splitString(xyposition, ",");
            if (tokens.size() == 2)
            {
                urlParameters["overlaySensorPosX"] = tokens[0];
                urlParameters["overlaySensorPosY"] = tokens[1];
            }
        }
    }

    startTime = in.get("startTime", EMPTY_STRING).asString();
    endTime = in.get("endTime", EMPTY_STRING).asString();
    audiourl = in.get("audiourl", EMPTY_STRING).asString();
    is_client = in.get("is_client", false).asBool();
    is_dataChannel = in.get("is_dataChannel", false).asBool();
    remote_addr = req_info.get("remote_addr", EMPTY_STRING).asString();
    std::map<std::string, std::string, std::less<>> opts = getStreamOptions(in);
    username = req_info.get("username", EMPTY_STRING).asString();
    m_clientPublicIpAddr = in.get("clientIpAddr", EMPTY_STRING).asString();
    LOG(info) << "Client ip address from remote:" << m_clientPublicIpAddr << endl;
    if (m_clientPublicIpAddr.empty() && !remote_addr.empty())
    {
        m_clientPublicIpAddr = remote_addr;
    }

    // backward compatibility
    if (peerid.empty())
    {
        peerid = in.get("peerid", EMPTY_STRING).asString();
    }
    if (!is_client)
    {
        is_client = in.get("isClient", false).asBool();
    }
    if (!is_dataChannel)
    {
        is_dataChannel = in.get("isDataChannel", false).asBool();
    }
    if (sensorId.empty() == true && is_client == false && is_dataChannel == false && is_composite_stream == false)
    {

        if (!m_deviceManager->getSensorIdFromStreamId(streamId, sensorId))
        {
            LOG(warning) << "Stream Not Found" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, answer, "Stream Not Found")
            return VmsErrorCode::InvalidParameterError;
        }
    }

    urlParameters["peerid"] = peerid;
    urlParameters["sensorId"] = sensorId;
    // set streamId for starting stream using sub stream
    urlParameters["streamId"] = streamId;
    urlParameters["startTime"] = startTime;
    urlParameters["endTime"] = endTime;
    urlParameters["audiourl"] = audiourl;
    urlParameters["remote_addr"] = remote_addr;

    LOG(info) << __FUNCTION__ << " peerid:" << peerid  << ", sensorId:" << sensorId << ", streamId: " << streamId
                << ", startTime:" << startTime << ", endTIme:" << endTime
                << ", remote_addr:" << remote_addr << ", clientIp:" << m_clientPublicIpAddr << endl;

    LOG(info) << "is data channel connection: " << is_dataChannel << endl;
    LOG(info) << "is client: " << is_client << endl;

    for (const auto& x : opts)
    {
        LOG(info) << x.first << ": " << x.second << endl;
    }

    string wsConnectionId = in.get("wsConnectionId", EMPTY_STRING).asString();
    if (!wsConnectionId.empty())
    {
        setWsConnectionId(wsConnectionId);
        LOG(verbose) << "setWsConnectionId: " << wsConnectionId << endl;
    }

    std::string type;
    std::string sdp;
    Json::Value jmessage = in.get("sessionDescription", EMPTY_STRING);
    if (!rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionTypeName, &type) || !rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionSdpName, &sdp))
    {
        LOG(warning) << "Can't parse received message.";
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, answer, "Can't parse received message")
        return VmsErrorCode::InvalidParameterError;
    }
    else
    {
        if (sdp.find("x-google-start-bitrate") == string::npos)
        {
            /* If client doesn't provide the bitrates in sdp, then add it here */
            sdp = addWebrtcBitrateToSDP(in, sdp);
        }
        // set remote offer
        webrtc::SessionDescriptionInterface *session_description(webrtc::CreateSessionDescription(type, sdp, nullptr));
        if (!session_description)
        {
            LOG(warning) << "Can't parse received session description message.";
        }
        else
        {
            std::promise<const webrtc::SessionDescriptionInterface *> remotepromise;
            std::string sdp;
            rtc::scoped_refptr<SetSessionDescriptionObserver> remoteSessionObserver(SetSessionDescriptionObserver::Create(m_pc.get(), remotepromise,sdp));
            m_pc->SetRemoteDescription(remoteSessionObserver.get(), session_description);
            // waiting for remote description
            std::future<const webrtc::SessionDescriptionInterface *> remotefuture = remotepromise.get_future();
            if (remotefuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
            {
                LOG(verbose) << "remote_description is ready";
            }
            else
            {
                LOG(warning) << "remote_description is NULL";
            }
        }

        if (!is_client && !is_dataChannel)
        {
            LOG(warning) << "Calling Addstreams" << endl;
            // add local stream
            if (is_composite_stream == false)
            {
                VmsErrorCode ret = AddStreams(urlParameters, opts, answer);
                if (ret != VmsErrorCode::NoError)
                {
                    LOG(error) << "Can't add stream" << endl;
                    return ret;
                }
            }
            else
            {
                VmsErrorCode ret = AddCompositorStreams(urlParameters, opts, answer, list_sensorids);
                if (ret != VmsErrorCode::NoError)
                {
                    LOG(error) << "Can't add stream" << endl;
                    return ret;
                }
            }
        }
        else
        {
            ClientInfo client;
            string deviceid = peerid;
            client.m_ipAddress = urlParameters["remote_addr"];
            client.m_deviceId = deviceid;
            client.m_streamId = deviceid;
            m_peerConnectionManager->ClientInsert(peerid, client);
        }

        // create answer
        webrtc::PeerConnectionInterface::RTCOfferAnswerOptions rtcoptions;
        rtcoptions.offer_to_receive_video = 1;
        rtcoptions.offer_to_receive_audio = 1;
        std::promise<const webrtc::SessionDescriptionInterface *> localpromise;
        std::string sdp;
        m_pc->CreateAnswer(CreateSessionDescriptionObserver::Create(m_pc.get(), localpromise, sdp), rtcoptions);

        // waiting for answer
        std::future<const webrtc::SessionDescriptionInterface *> localfuture = localpromise.get_future();
        if (localfuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
        {
            // answer with the created answer
            webrtc::SessionDescriptionInterface *descInterface = const_cast<webrtc::SessionDescriptionInterface *>(localfuture.get());
            if (descInterface)
            {
                if (GET_CONFIG().use_reverse_proxy == true /*&& m_peerConnectionManager->isRpStunAvailable() == false*/)
                {
                    string sdpLite = m_observer->getSdpWithIceLite(descInterface, m_peerid, m_clientPublicIpAddr);
                    if (sdpLite.empty() == false)
                    {
                        sdp = sdpLite;
                    }
                }
                answer[kSessionDescriptionTypeName] = descInterface->type();
                answer[kSessionDescriptionSdpName] = sdp;
            }
            else
            {
                LOG(error) << "Failed to create answer";
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to create answer")
                return VmsErrorCode::VMSInternalError;
            }
        }
        else
        {
            LOG(error) << "Failed to create answer";
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to create answer")
            return VmsErrorCode::VMSInternalError;
        }
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnection::getPeerConnectionList(Json::Value& content)
{
    // get local SDP
    rtc::scoped_refptr<webrtc::PeerConnectionInterface> rtcPeerConnection = getRtcPeerConnection();
    if ((rtcPeerConnection) && (rtcPeerConnection->local_description()))
    {
        content["pc_state"] = (int)(rtcPeerConnection->peer_connection_state());
        content["signaling_state"] = (int)(rtcPeerConnection->signaling_state());
        content["ice_state"] = (int)(rtcPeerConnection->ice_connection_state());

        std::string sdp;
        rtcPeerConnection->local_description()->ToString(&sdp);
        content["sdp"] = sdp;

        Json::Value streams;
        std::vector<rtc::scoped_refptr<webrtc::RtpSenderInterface>> senders = rtcPeerConnection->GetSenders();
        for (auto localStream : senders)
        {
            if (localStream != nullptr)
            {
                rtc::scoped_refptr<webrtc::MediaStreamTrackInterface> mediaTrack = localStream->track();
                if (mediaTrack) {
                    Json::Value track;
                    track["kind"] = mediaTrack->kind();
                    if (track["kind"] == "video") {
                        webrtc::VideoTrackInterface* videoTrack = (webrtc::VideoTrackInterface*)mediaTrack.get();
                        webrtc::VideoTrackSourceInterface::Stats stats;
                        if (videoTrack->GetSource())
                        {
                            track["state"] = videoTrack->GetSource()->state();
                            if (videoTrack->GetSource()->GetStats(&stats))
                            {
                                track["width"] = stats.input_width;
                                track["height"] = stats.input_height;
                            }
                        }
                    } else if (track["kind"] == "audio") {
                        webrtc::AudioTrackInterface* audioTrack = (webrtc::AudioTrackInterface*)mediaTrack.get();
                        if (audioTrack->GetSource())
                        {
                            track["state"] = audioTrack->GetSource()->state();
                        }
                        int level = 0;
                        if (audioTrack->GetSignalLevel(&level)) {
                            track["level"] = level;
                        }
                    }

                    Json::Value tracks;
                    tracks[mediaTrack->id()] = track;
                    std::string streamLabel = localStream->stream_ids()[0];
                    streams[streamLabel] = tracks;
                }
            }
        }
        content["streams"] = streams;
    }

    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnection::getStreamList(Json::Value& value)
{
    for (auto it : m_streamMap)
    {
        value.append(it.first);
    }
    return VmsErrorCode::NoError;
}

void PeerConnection::startPlayback(const std::string &peerId)
{
    if (peerId.empty())
    {
        LOG(error) << "Empty peer ID received" << endl;
        return;
    }
    LOG(verbose) << "PEER ID: " << peerId << endl;
    std::map<std::string, AudioVideoPair, std::less<>>::iterator audio_video_pair_it;
    audio_video_pair_it = m_streamMap.find(peerId);
    if (audio_video_pair_it == m_streamMap.end())
    {
        // To be changed when on-demand is implemented.
        // Using first AudioVideoPair in map.
        if (m_streamMap.size() == 0)
        {
            LOG(error) << "Video source not found" << endl;
            return;
        }
        audio_video_pair_it = m_streamMap.begin();
    }

    AudioVideoPair pair = audio_video_pair_it->second;
    LOG(verbose) << "Video Source Found " << pair.first.get() << endl;

    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource(pair.first);
    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video != nullptr)
        {
            LOG(info) << "Starting playback for VST" << endl;
            gst_video->startPlayback();
        }
        else
        {
            TrackSource<NvGstUDPVideoCapturer>* udp_video = dynamic_cast<TrackSource<NvGstUDPVideoCapturer>*>(videoSourceInterface);
            if (udp_video != nullptr)
            {
                LOG(info) << "Starting playback for UDP Pipeline" << endl;
                udp_video->startPlayback();
            }
        }
    }
    else if(m_deviceManager->getDeviceType() == TYPE_STREAMER)
    {
        TrackSource<RTSPVideoCapturer>* rtsp_video = static_cast<TrackSource<RTSPVideoCapturer>*>(videoSourceInterface);
        if (rtsp_video != nullptr)
        {
            LOG(info) << "Starting playback for MMS/Streamer" << endl;
            rtsp_video->startPlayback();
        }
    }
}

VmsErrorCode
PeerConnection::controlStream(const std::string action, const std::string &peerId,
                                const Json::Value &data, Json::Value& value)
{
    bool result = false;

    std::string seek_value = data.get("seek_value", EMPTY_STRING).asString();
    std::string mediaSessionId = data.get("mediaSessionId", EMPTY_STRING).asString();

    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, value);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        // To avoid "goto crosses initialization" error
        if (gst_video != nullptr)
        {
            if (action == "rewind")
            {
                string error_message = string("Rewind operation not supported");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, error_message.c_str())
                LOG(warning) << "Value = " << value << endl;
                return VmsErrorCode::InvalidParameterError;
            }
            ret = gst_video->controlStreamTrackSource(action, seek_value);
            if (ret != VmsErrorCode::NoError)
            {
                if (ret == VmsErrorCode::InvalidParameterError)
                {
                    string error_message = string("Invalid seek_value");
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, value, error_message.c_str())
                    return VmsErrorCode::InvalidParameterError;
                }
                else
                {
                    string error_message = string("Pipeline is currently seeking, ignoring this seek operation");
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, value, error_message.c_str())
                    return VmsErrorCode::VMSInternalError;
                }
            }
            else
            {
                result = true;
                goto success;
            }

        }
    }
    if(m_deviceManager->getDeviceType() == TYPE_STREAMER)
    {
        TrackSource<RTSPVideoCapturer>* rtsp_video = static_cast<TrackSource<RTSPVideoCapturer>*>(videoSourceInterface);
        if (rtsp_video != nullptr)
        {
            if (action == "rewind" || action == "fast_forward")
            {
                ret = rtsp_video->controlStreamTrackSource(action, seek_value);
            }
            else
            {
                ret = rtsp_video->controlStreamTrackSource(action);
            }
            result = true;
            goto success;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, value)
    return VmsErrorCode::VMSInternalError;

success:
    value = result;
    return VmsErrorCode::NoError;
}

VmsErrorCode
PeerConnection::getCurrentPosition(const std::string &peerId, Json::Value& data, Json::Value& response)
{
    gint64 position = 0;

    std::string mediaSessionId = data.get("mediaSessionId", EMPTY_STRING).asString();

    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    // For server type VST
    TrackSource<NvGstVideoCapturer>* gst_video = static_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
    if (gst_video != nullptr)
    {
        position = gst_video->getPositionTrackSource();
        LOG(info) << "got position: " << position << endl;
        if (position != 0)
        {
            response["position"] = Json::Int64(position);
            return VmsErrorCode::NoError;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode
PeerConnection::getStatus(const string peerId, const string mediaSessionId,
                        const string overlay, Json::Value& response)
{
    if (peerId.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "PeerID expected in request")
        return VmsErrorCode::InvalidParameterError;
    }
    LOG(verbose) << "PEER ID: " << peerId << endl;

    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = static_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video != nullptr)
        {
            response["state"] = gst_video->getStreamState();
            response["error"] = gst_video->isStreamError();
            return VmsErrorCode::NoError;
        }
    }
    if(m_deviceManager->getDeviceType() == TYPE_STREAMER)
    {
        TrackSource<RTSPVideoCapturer>* rtsp_video = static_cast<TrackSource<RTSPVideoCapturer>*>(videoSourceInterface);
        if (rtsp_video != nullptr)
        {
            response["state"] = rtsp_video->getStreamState();
            response["error"] = rtsp_video->isStreamError();
            if (overlay == "true")
            {
                response["overlay"] = rtsp_video->getOverlayStatus();
            }
            return VmsErrorCode::NoError;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode
PeerConnection::getMetadataLastFrame(const std::string &mediaSessionId,
                                    Json::Value& response, const bool metadata)
{
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    string sensorName = "", search_key = "";
    string sensorId;
    uint64_t ts = 0;
    bool use_id = false;
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video == nullptr)
        {
            LOG(error) << "Video track not found" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
        sensorName = gst_video->getSensorName();
        sensorId = gst_video->getSensorId();
        ts = gst_video->getLastTS();
    }
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    /*
    if(m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<RTSPVideoCapturer>* rtsp_video = static_cast<TrackSource<RTSPVideoCapturer>*>(videoSourceInterface);
        if (rtsp_video == nullptr)
        {
            LOG(error) << "Video track not found" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
        sensorName = rtsp_video->getSensorName();
        sensorId = rtsp_video->getSensorId();
        ts = rtsp_video->getLastTS();
        int num_digits = ceil(log10(ts));
        // Convert to microseconds if ts is in milliseconds, i.e., live playback
        if (num_digits == 13)
        {
            ts = ts * 1000;
        }
        // WAR: Getting in nanosecs for MMS from gstnvvideodecoder
        // Bug 3949817
        if (num_digits == 19)
        {
            ts = ts / 1000;
        }
    }*/
    if (m_deviceManager->getDeviceType() == TYPE_STREAMER)
    {
        TrackSource<RTSPVideoCapturer>* rtsp_video = static_cast<TrackSource<RTSPVideoCapturer>*>(videoSourceInterface);
        if (rtsp_video == nullptr)
        {
            LOG(error) << "Video track not found" << endl;
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }
        sensorName = rtsp_video->getSensorName();
        use_id = true;
        ts = rtsp_video->getLastTS();
        if (GET_CONFIG().enable_rtsp_server_sei_metadata == true)
        {
            response["id"] = Json::UInt64(ts);
        }
        else
        {
            response["ts"] = Json::UInt64(ts);
        }
        sensorId = rtsp_video->getSensorId();

        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
        if (sensor && GET_CONFIG().enable_rtsp_server_sei_metadata == true)
        {
            shared_ptr<StreamInfo> stream_info = sensor->streams[0];
            if (stream_info)
            {
                double frameRate = stringToDouble(stream_info->settings.encoderValues.frameRate);
                response["ts"] = getRelativeTimeUsingFrameId(ts, frameRate);
            }
        }
        search_key = to_string(ts);
    }
    else
    {
        search_key = convertEpocToISO8601_2(ts);
        response["ts"] = Json::UInt64(ts/1000);
    }
    LOG(verbose) << "TS: " << ts << endl;
    LOG(verbose) << "search_key: " << search_key << endl;
    LOG(verbose) << "sensorName: " << sensorName << endl;

    if (metadata == false)
    {
        return VmsErrorCode::NoError;
    }

    Json::Value frame_metadata;
    uint64_t elasticTS;
    if (use_id == true)
    {
        SearchParams inData(search_key, "", sensorName);
        frame_metadata = elasticSearch::getMetadata(inData, use_id);
    }
    else
    {
        double frameRate = 30.0;
        int number_of_tolerance_frames = 3;

        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
        if (sensor)
        {
            shared_ptr<StreamInfo> stream_info = sensor->streams[0];
            if (stream_info)
            {
                frameRate = stringToDouble(stream_info->settings.encoderValues.frameRate, 30.0);
            }
        }

        /* Consider tolerance of +-3 frames */
        uint32_t tolerance = uint((1.0 / frameRate) * 1000) * 1000 * number_of_tolerance_frames;
        string start_time = convertEpocToISO8601_2(ts - tolerance);
        string end_time = convertEpocToISO8601_2(ts + tolerance);

        /* Getting frames metadata from elastic serevr  */
        BBoxMetaData bboxMetaData;
        SearchParams inData(start_time, end_time, sensorName);
        bboxMetaData.m_searchParams = inData;
        elasticSearch::getBboxPosition(bboxMetaData);

        if (bboxMetaData.m_qHits.empty())
        {
            LOG(warning) << "Metadata is empty for this range" << endl;
            response["metadata"] = frame_metadata;
            return VmsErrorCode::NoError;
        }

        frame_metadata = bboxMetaData.m_qHits.front();
        elasticTS = frame_metadata["epocTime"].asUInt64() * 1000;
        while(!bboxMetaData.m_qHits.empty())
        {
            if (elasticTS >= ts)
            {
                break;
            }
            bboxMetaData.m_qHits.pop();
            if (!bboxMetaData.m_qHits.empty())
            {
                frame_metadata = bboxMetaData.m_qHits.front();
            }
            elasticTS = frame_metadata["epocTime"].asUInt64() * 1000;
        }
    }
    response["metadata"] = frame_metadata;
    LOG(verbose) << "metadata\n" << response["metadata"] << endl;

    return VmsErrorCode::NoError;
}

VmsErrorCode
PeerConnection::getStartTime(const std::string &mediaSessionId, Json::Value& response)
{
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    if (m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video != nullptr)
        {
            int64_t fileStartTime = gst_video->getFileStartTime();
            response["startTime"] = Json::Int64(fileStartTime);
            return VmsErrorCode::NoError;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode
PeerConnection::getDurationStream(const std::string &mediaSessionId, Json::Value& response)
{
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    if (m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video != nullptr)
        {
            uint32_t duration = gst_video->getDurationStream();
            response["duration"] = Json::UInt(duration);
            return VmsErrorCode::NoError;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode
PeerConnection::getQuery(const Json::Value& req_info, const Json::Value& in,
                        Json::Value& response)
{
    string metadata = "", peerid = "", mediaSessionId = "";
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (query_string.empty() == false)
    {
        CivetServer::getParam(query_string, "peerid", peerid);
    }
    if (query_string.empty() == false)
    {
        if(!CivetServer::getParam(query_string, "metadata", metadata))
        {
            metadata = "false";
        }
    }
    if (query_string.empty() == false)
    {
        CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
    }

    bool get_metadata = metadata == "true" ? true : false;
    return getMetadataLastFrame(mediaSessionId, response, get_metadata);
}

void PeerConnection::removeTracks(const Json::Value &in)
{
    LOG(info) << "Deleting stream map" << endl;
    std::string mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
    if (m_pc.get())
    {
        std::vector<rtc::scoped_refptr<webrtc::RtpSenderInterface>> senders = m_pc->GetSenders();
        for (auto stream : senders)
        {
            std::vector<std::string> streamVector = stream->stream_ids();
            if (streamVector.size() > 0)
            {
                std::string streamLabel = streamVector[0];
                // If mediaSessionId is provided, remove track only for that id
                if (!mediaSessionId.empty())
                {
                    if(streamLabel != mediaSessionId)
                    {
                        continue;
                    }
                }
                // bool stillUsed = this->streamStillUsed(streamLabel);
                // if (!stillUsed)
                {
                    LOG(info) << "hangUp stream is no more used " << streamLabel << endl;
                    // std::lock_guard<std::mutex> mlock(m_streamMapMutex);
                    std::map<std::string, AudioVideoPair, std::less<>>::iterator it = m_streamMap.find(streamLabel);
                    if (it != m_streamMap.end())
                    {
                        m_streamMap.erase(it);
                        LOG(info) << "hangUp stream closed " << streamLabel << endl;
                    }
                }
                m_pc->RemoveTrackOrError(stream);
            }
        }
        if (m_isClient)
        {
            GstDummyUdpPipeline::getInstance()->stopUdpPipeline(m_peerid);
        }
    }
}

/*
** ---------------------------------------------------------------------------
**  get the capturer from its URL
** ---------------------------------------------------------------------------
*/
rtc::scoped_refptr<webrtc::VideoTrackSourceInterface>
PeerConnection::CreateVideoSource(const std::string &videourl, const std::map<std::string, std::string, std::less<>> &opts)
{
    LOG(verbose) << "videourl:" << videourl;

    return CapturerFactory::CreateVideoSource(videourl, opts, m_publishFilter, m_peer_connection_factory);
}

rtc::scoped_refptr<webrtc::AudioSourceInterface>
PeerConnection::CreateAudioSource(const std::string &audiourl, const std::map<std::string, std::string, std::less<>> &opts)
{
    LOG(verbose) << "audiourl:" << audiourl;
    return m_workerThread->BlockingCall([this, audiourl, opts] {
        return CapturerFactory::CreateAudioSource(audiourl, opts, m_publishFilter, m_peer_connection_factory
                                            , m_audioDecoderfactory, m_audioDeviceModule);
    });
}

/*
** -------------------------------------------------------------------------
**  Add a Compositor stream to a PeerConnection
** -------------------------------------------------------------------------
*/
VmsErrorCode PeerConnection::AddCompositorStreams(
                                            unordered_map<string, string> urlParameters,
                                            std::map<std::string, std::string, std::less<>>& opts,
                                            Json::Value& response, vector<string> &list_sensorids)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    std::string peerid = urlParameters["peerid"];
    std::string audiourl = urlParameters["audiourl"];
    std::string sensorId = urlParameters["sensorId"];
    std::string startTime = urlParameters["startTime"];
    std::string endTime = urlParameters["endTime"];
    bool is_audio_required = false;
    //to be used in sanitizeLabel
    std::string videourl;
    if (startTime.length() > 0)
    {
        LOG(error) << "Compositor with recorded playback not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Compositor with recorded playback not supported")
        return VmsErrorCode::InvalidParameterError;
    }
    else
    {
        videourl = sensorId;
    }
    std::string video = videourl;

    for (auto sensorId : list_sensorids)
    {
        /* Check the sensor sanity */
        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
        VmsErrorCode device_error = m_peerConnectionManager->checkDeviceSanity (sensor, response);
        if (device_error != VmsErrorCode::NoError)
        {
            continue;
        }
        std::string streamId;
        streamId = sensorId;
        LOG(info) << "streamId: " << streamId << endl;

        /* Check Stream Sanity */
        shared_ptr<StreamInfo> stream_info = sensor->getStream (streamId);
        VmsErrorCode stream_error = m_peerConnectionManager->checkStreamSanity (stream_info, startTime, response);
        if (stream_error != VmsErrorCode::NoError)
        {
            LOG(error) << "Stream Error for "<< sensorId << endl;
            continue;
        }

        LOG(info) << "Stream status["<< translateStreamStatusToString(stream_info->getErrorStatus().first) << "]: "
                        << stream_info->getErrorStatus().second << endl;
        const string& format = stream_info->settings.encoderValues.encoding;
        if (sensor->type != SENSOR_TYPE_WEBRTC &&
            VmsConfigManager::getInstance()->isVideoFormatSupported(format) == false)
        {
            LOG(error) << "Video encode format not supported"<< endl;
            continue;
        }

        // Sending values in opts for overlay feature.
        opts["sensorID"] = sensor->name;
        opts["sensorId"] = sensor->id;
        if (opts.find("framerate") == opts.end())
        {
            opts["framerate"] = stream_info->settings.encoderValues.frameRate;
        }
        opts["govLength"] = stream_info->settings.encoderValues.govLength;
        opts["width"] = stream_info->settings.encoderValues.resolution.width;
        opts["height"] = stream_info->settings.encoderValues.resolution.height;
        opts["isBframesPresent"] = stream_info->settings.encoderValues.isBframesPresent ? "true" : "false";
        opts["startTime"] = startTime;
        opts["endTime"] = endTime;
        opts["codec"]   = format;
        opts["sensor_type"] = sensor->type;

        video = video + (stream_info->live_proxy_url.empty() ? stream_info->live_url : stream_info->live_proxy_url);
        video = video + "#" + sensor->name + ",";

        /* TODO, changeMe when webrtc scaling is ready */
        if (m_deviceManager && m_deviceManager->needStreamMonitoring && m_deviceManager->needRtspServer == false)
        {
            StreamMonitor* streamMonitor = StreamMonitor::getInstance();
            if (streamMonitor)
            {
                streamMonitor->addStream(stream_info);
            }
        }
    }

    LOG(info) << "URL: " << video << endl;
    // compute audiourl if not set
    std::string audio(audiourl);
    if (audio.empty())
    {
        audio = videourl;
    }

    // set bandwidth
    if (opts.find("bitrate") != opts.end())
    {
        int bitrate = stringToInt(opts.at("bitrate"), 0);

        webrtc::BitrateSettings bitrateParam;
        bitrateParam.min_bitrate_bps = absl::optional<int>(bitrate / 2);
        bitrateParam.start_bitrate_bps = absl::optional<int>(bitrate);
        bitrateParam.max_bitrate_bps = absl::optional<int>(bitrate * 2);
        m_pc->SetBitrate(bitrateParam);

        LOG(info) << "set bitrate:" << bitrate;

    }

    // keep capturer options (to improve!!!)
    std::string optcapturer;
    if ((video.find("rtsp://") == 0) || (audio.find("rtsp://") == 0))
    {
        if (opts.find("rtptransport") != opts.end())
        {
            optcapturer += opts["rtptransport"];
        }
        if (opts.find("timeout") != opts.end())
        {
            optcapturer += opts["timeout"];
        }
    }

    // compute stream label removing space because SDP use label
    //std::string streamLabel = this->sanitizeLabel(videourl + "|" + audiourl + "|" + optcapturer + "|" + peerid);
    std::string streamLabel = generate_uuid();
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if (m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        opts["capture_type"] = "stream_sharing";
    }

    if (!peerid.empty())
    {
        opts["peerid"] = peerid;
    }
    opts["do_composition"] = "true";
    if (urlParameters.find("overlayShowSensorName") != urlParameters.end())
    {
        opts["overlayShowSensorName"] = true;
        if (urlParameters.find("overlaySensorPosX") != urlParameters.end() &&
            urlParameters.find("overlaySensorPosY") != urlParameters.end())
        {
            opts["overlaySensorPosX"] = urlParameters["overlaySensorPosX"];
            opts["overlaySensorPosY"] = urlParameters["overlaySensorPosY"];
        }
    }
    if (CreateAndAddTrack(video, opts, is_audio_required, streamLabel, nullptr, response) == -1)
    {
        return VmsErrorCode::VMSInternalError;
    }
    // Return mediaSessionId in response.
    response["mediaSessionId"] = streamLabel;

    ClientInfo client;
    string deviceid = peerid;
    client.m_ipAddress = urlParameters["remote_addr"];
    client.m_deviceId = peerid;
    client.m_streamId = opts["streamId"];
    m_peerConnectionManager->ClientInsert(peerid, client);

    return ret;
}

/*
** -------------------------------------------------------------------------
**  Add a stream to a PeerConnection
** -------------------------------------------------------------------------
*/
VmsErrorCode
PeerConnection::AddStreams(unordered_map<string, string> urlParameters,
                            std::map<std::string, std::string, std::less<>>& opts,
                            Json::Value& response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    std::string peerid = urlParameters["peerid"];
    std::string audiourl = urlParameters["audiourl"];
    std::string sensorId = urlParameters["sensorId"];
    std::string streamId = urlParameters["streamId"];
    std::string startTime = urlParameters["startTime"];
    std::string endTime = urlParameters["endTime"];
    bool is_audio_required = false;
    //to be used in sanitizeLabel
    std::string videourl;
    if (startTime.length() > 0)
    {
        videourl = sensorId + string("?") + string("startTime=") + startTime + string("&endTime=") + endTime;
    }
    else
    {
        videourl = streamId;
    }
    std::string video = videourl;

    /* Check the sensor sanity */
    shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
    VmsErrorCode device_error = m_peerConnectionManager->checkDeviceSanity (sensor, response);
    if (device_error != VmsErrorCode::NoError)
    {
        return device_error;
    }
    LOG(info) << "streamId: " << streamId << " sensorId: " << sensorId << endl;
    /* Check Stream Sanity */
    shared_ptr<StreamInfo> stream_info = sensor->getStream (streamId);
    VmsErrorCode stream_error = m_peerConnectionManager->checkStreamSanity (stream_info, startTime, response);
    if (stream_error != VmsErrorCode::NoError)
    {
        LOG(error) << "Stream Error for "<< sensorId << endl;
        return stream_error;
    }

    LOG(info) << "Stream status["<< translateStreamStatusToString(stream_info->getErrorStatus().first) << "]: "
                    << stream_info->getErrorStatus().second << endl;
    const string& format = stream_info->settings.encoderValues.encoding;
    if (sensor->type != SENSOR_TYPE_WEBRTC && sensor->type != SENSOR_TYPE_CSI &&
        startTime.empty() && VmsConfigManager::getInstance()->isVideoFormatSupported(format) == false &&
        m_deviceManager->needRtspServer == true)
    {
        LOG(error) << "Video encode format "<< format << " not supported"<< endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Video encode format not supported")
        return VmsErrorCode::VMSNotSupportedError;
    }

    // Sending values in opts for overlay feature.
    opts["sensorID"] = sensor->name;
    opts["sensorId"] = sensor->id;
    opts["framerate"] = stream_info->settings.encoderValues.frameRate;
    opts["govLength"] = stream_info->settings.encoderValues.govLength;
    opts["width"] = stream_info->settings.encoderValues.resolution.width;
    opts["height"] = stream_info->settings.encoderValues.resolution.height;
    opts["isBframesPresent"] = stream_info->settings.encoderValues.isBframesPresent ? "true" : "false";
    opts["startTime"] = startTime;
    opts["endTime"] = endTime;
    opts["codec"]   = format;
    opts["sensor_type"] = sensor->type;
    opts["streamId"] = streamId;
    
    // Determine storage location based on live/replay request    
    if (!startTime.empty() || !endTime.empty())
    {
        opts["storageLocation"] = stream_info->storageLocation == StreamStorageTypeCloud ? "cloud" : "local";
    }
    else
    {
        opts["storageLocation"] = "local";
    }
    LOG(info) << "---- Storage location: " << opts["storageLocation"] << endl;

    if (sensor->type == SENSOR_TYPE_NVSTREAM)
    {
        int64_t req_startTime_ms = 0, req_endTime_ms = 0;
        uint64_t file_start_time = 0;
        string startFrameId, endFrameId;
        double frameRate = stringToDouble(stream_info->settings.encoderValues.frameRate);

        string filepath = getFilePathFromUrl(stream_info->live_url, NV_STREAMER);
        if (filepath.empty())
        {
            LOG(error) << "File not found for given url" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "File not found for given url")
            return VmsErrorCode::VMSInternalError;
        }
        file_start_time = getFileTimestamp(filepath);

        /* Check whether HH:MM:SS time is provided or frameId is provided for snippet */
        if (startTime.empty() && endTime.empty())
        {
            startFrameId = urlParameters["startFrameId"];
            endFrameId = urlParameters["endFrameId"];
            if (!startFrameId.empty() && frameRate > 0)
            {
                startTime = getRelativeTimeUsingFrameId(stringToInt(startFrameId, -1), frameRate);
                opts["startFrameId"] = startFrameId;
            }
            if(!endFrameId.empty() && frameRate > 0)
            {
                endTime = getRelativeTimeUsingFrameId(stringToInt(endFrameId, -1), frameRate);
                opts["endFrameId"] = endFrameId;
            }
        }

        if(startTime.empty() == false)
        {
            req_startTime_ms = convertStringToSeconds(startTime);
            if ( (req_startTime_ms/1000) > stream_info->duration )
            {
                LOG(error) << "Requeted startTime exceeds the file duration:" << stream_info->duration << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Requeted startTime exceeds the file duration")
                return VmsErrorCode::InvalidParameterError;
            }
            if (startFrameId.empty())
            {
                startFrameId = to_string(getFrameIdUsingRelativeTime(startTime, frameRate));
                opts["startFrameId"] = startFrameId;
            }
            req_startTime_ms = (file_start_time + req_startTime_ms);
            startTime = convertEpocToISO8601_2(req_startTime_ms * 1000);
            opts["startTime"] = startTime;
        }
        if (endTime.empty() == false)
        {
            req_endTime_ms = convertStringToSeconds(endTime);
            if ((req_endTime_ms/1000) > stream_info->duration)
            {
                LOG(error) << "Requeted endTime exceeds the file duration:" << stream_info->duration << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Requeted endTime exceeds the file duration")
                return VmsErrorCode::InvalidParameterError;
            }
            req_endTime_ms = (file_start_time + req_endTime_ms);
            if (req_endTime_ms < req_startTime_ms)
            {
                LOG(error) << "Wrong endTime/startTime provided start:" << req_startTime_ms << ", end:" << req_endTime_ms << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Wrong endTime/startTime provided")
                return VmsErrorCode::InvalidParameterError;
            }
            if (endFrameId.empty())
            {
                endFrameId = to_string(getFrameIdUsingRelativeTime(endTime, frameRate));
                opts["endFrameId"] = endFrameId;
            }
            endTime = convertEpocToISO8601_2(req_endTime_ms * 1000);
            opts["endTime"] = endTime;
        }
        LOG(info) << "Streamer file start_time:" << file_start_time
                << ", start_time:" << req_startTime_ms << ", end_time" << req_endTime_ms << endl;
    }

    LOG(info) << "Start Time: " << startTime << " End Time: " << endTime << endl;
    if (!startTime.empty() || !endTime.empty())
    {
        string replay_url = stream_info->replay_url.empty() ? stream_info->live_url : stream_info->replay_url;
        video = replay_url + "?startTime=" + startTime.c_str() + "&endTime=" + endTime.c_str();
    }
    else
    {
        /* Live playback */
        video = stream_info->live_proxy_url.empty() ? stream_info->live_url : stream_info->live_proxy_url;

        /* TODO, changeMe when webrtc scaling is ready */
        if (m_deviceManager && m_deviceManager->needStreamMonitoring && m_deviceManager->needRtspServer == false)
        {
            StreamMonitor::getInstance()->addStream(stream_info);
        }
    }
    LOG(info) << "URL: " << video << endl;
    // compute audiourl if not set
    std::string audio(audiourl);
    if (audio.empty())
    {
        audio = videourl;
    }

    // set bandwidth
    if (opts.find("bitrate") != opts.end())
    {
        int bitrate = stringToInt(opts.at("bitrate"), 0);

        webrtc::BitrateSettings bitrateParam;
        bitrateParam.min_bitrate_bps = absl::optional<int>(bitrate / 2);
        bitrateParam.start_bitrate_bps = absl::optional<int>(bitrate);
        bitrateParam.max_bitrate_bps = absl::optional<int>(bitrate * 2);
        m_pc->SetBitrate(bitrateParam);

        LOG(info) << "set bitrate:" << bitrate;

    }

    // keep capturer options (to improve!!!)
    std::string optcapturer;
    if ((video.find("rtsp://") == 0) || (audio.find("rtsp://") == 0))
    {
        if (opts.find("rtptransport") != opts.end())
        {
            optcapturer += opts["rtptransport"];
        }
        if (opts.find("timeout") != opts.end())
        {
            optcapturer += opts["timeout"];
        }
    }

    // compute stream label removing space because SDP use label
    //std::string streamLabel = this->sanitizeLabel(videourl + "|" + audiourl + "|" + optcapturer + "|" + peerid);
    std::string streamLabel = generate_uuid();

    // need to create the stream
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if ((m_deviceManager->getDeviceType() ==  TYPE_VST && sensor->type != SENSOR_TYPE_MMS_ONVIF) && video.find("startTime") != string::npos && sensor->type != SENSOR_TYPE_FILE)
    {
        bool ret = replaceString(video, "rtsp://", "file://");
        if(!ret)
        {
            LOG(error) << "Replace string failed: " << video << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to process stream")
            return VmsErrorCode::VMSInternalError;
        }
    }

    /* To create Audio Source, Server Type should be VMS and
    ** playback use case should be live stream as currently
    ** we don't need audio for recorded streams */
    is_audio_required = ((m_deviceManager->getDeviceType() == TYPE_VST) && (video.find("rtsp://") == 0));
    if (m_deviceManager->getDeviceType() ==  TYPE_STREAMER)
    {
        opts["capture_type"] = "rtsp";
        if (m_deviceManager->getDeviceType() == TYPE_STREAMER && GET_CONFIG().enable_rtsp_server_sei_metadata)
        {
            if (video.find("?") != string::npos)
            {
                video = video + string("&includeFrameId=true");
            }
            else
            {
                video = video + string("?includeFrameId=true");
            }
        }
    }
    else if (stream_info->stream_type == StreamType::Udp && stream_info->live_url.find("udp:") == 0)
    {
        opts["capture_type"] = "udp";
        vector<string> url_info = splitString(stream_info->live_url, ":");
        if (url_info.size() > 2)
        {
            if (url_info[1] != "0")
            {
                opts["video_port"] = url_info[1];
                opts["framerate"] = stream_info->settings.encoderValues.frameRate;
                opts["video_codec"] = stream_info->settings.encoderValues.encoding;
                opts["isBframesPresent"] = stream_info->settings.encoderValues.isBframesPresent ? "true" : "false";
            }
            if (url_info[2] != "0")
            {
                opts["audio_port"] = url_info[2];
                opts["audio_codec"] = stream_info->settings.audioEncoderValues.encoding;
                opts["sample_rate"] = stream_info->settings.audioEncoderValues.sample_rate;
                opts["bits_per_sample"] = stream_info->settings.audioEncoderValues.bits_per_sample;
                is_audio_required = stream_info->settings.audioEncoderValues.enable;
            }
        }
    }
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    else if (m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        if (sensor->type == SENSOR_TYPE_CSI)
        {
            opts["capture_type"] = "native_stream";
        }
        else
        {
            opts["capture_type"] = "stream_sharing";
        }
    }

    if (sensor->type == SENSOR_TYPE_FILE)
    {
        opts["video_codec"] = stream_info->settings.encoderValues.encoding;
        // Only add file:// prefix if URL doesn't already have a scheme (like s3://, http://, https://)
        if (video.find("://") == std::string::npos)
        {
            video = "file://" + video;
        }
    }
    if (sensor->type == SENSOR_TYPE_MMS_ONVIF)
    {
        opts["sensor_type"] = SENSOR_TYPE_MMS_ONVIF;
    }

    if (!peerid.empty())
    {
        opts["peerid"] = peerid;
    }
    if (CreateAndAddTrack(video, opts, is_audio_required, streamLabel, stream_info, response) == -1)
    {
        return VmsErrorCode::VMSInternalError;
    }
    // Return mediaSessionId in response.
    response["mediaSessionId"] = streamLabel;

    ClientInfo client;
    string deviceid = peerid;
    client.m_ipAddress = urlParameters["remote_addr"];
    client.m_deviceId = sensor->id;
    client.m_streamId = stream_info->id;
    m_peerConnectionManager->ClientInsert(peerid, client);

    return ret;
}

VmsErrorCode PeerConnection::createOffer(const Json::Value& in, Json::Value &offer)
{
    LOG(info) << __METHOD_NAME__ << endl;
    webrtc::PeerConnectionInterface::RTCOfferAnswerOptions rtcoptions;
    rtcoptions.offer_to_receive_video = 1;
    rtcoptions.offer_to_receive_audio = 1;
    std::promise<const webrtc::SessionDescriptionInterface *> localpromise;
    std::string sdp;

    m_pc->CreateOffer(CreateSessionDescriptionObserver::Create(m_pc.get(), localpromise, sdp), rtcoptions);

    std::future<const webrtc::SessionDescriptionInterface *> localfuture = localpromise.get_future();
    if (localfuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
    {
        // answer with the created offer
        webrtc::SessionDescriptionInterface *desc = const_cast<webrtc::SessionDescriptionInterface *>(localfuture.get());
        if (desc)
        {
            if (GET_CONFIG().use_reverse_proxy == true /*&& m_peerConnectionManager->isRpStunAvailable() == false*/)
            {
                string sdpLite = m_observer->getSdpWithIceLite(desc, m_peerid, m_clientPublicIpAddr);
                if (sdpLite.empty() == false)
                {
                    sdp = sdpLite;
                }
            }
            /* Add the bitrates in the sdp */
            sdp = addWebrtcBitrateToSDP(in, sdp);
            offer[kSessionDescriptionTypeName] = desc->type();
            offer[kSessionDescriptionSdpName] = sdp;
        }
        else
        {
            LOG(error) << "Failed to create offer" << endl;
            return VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        LOG(error) << "Failed to create offer";
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode
PeerConnection::addUdpTrack(const string sensorId, const string stream_id)
{
    shared_ptr<SensorInfo> sensor = m_deviceManager->getSensorInfo(sensorId);
    if (!sensor.get())
    {
        LOG(error) << "Sensor not found for id: " << sensorId << endl;
        return VmsErrorCode::InvalidParameterError;
    }
    shared_ptr<StreamInfo> stream = sensor->getStream(stream_id);
    if (!stream.get())
    {
        LOG(error) << "Stream not found for stream id: " << stream_id << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    bool is_audio_required = false;
    std::map<std::string, std::string, std::less<>> opts;
    opts["capture_type"] = "udp";
    opts["peerid"] = opts["streamId"] = stream_id;
    opts["quality"] = "auto";
    vector<string> url_info = splitString(stream->live_url, ":");
    if (url_info.size() > 2)
    {
        if (url_info[1] != "0")
        {
            opts["video_port"] = url_info[1];
            opts["framerate"] = stream->settings.encoderValues.frameRate;
            opts["video_codec"] = stream->settings.encoderValues.encoding;
            opts["isBframesPresent"] = stream->settings.encoderValues.isBframesPresent ? "true" : "false";
        }
        if (url_info[2] != "0")
        {
            opts["audio_port"] = url_info[2];
            opts["audio_codec"] = stream->settings.audioEncoderValues.encoding;
            opts["sample_rate"] = stream->settings.audioEncoderValues.sample_rate;
            opts["bits_per_sample"] = stream->settings.audioEncoderValues.bits_per_sample;
            is_audio_required = stream->settings.audioEncoderValues.enable;
        }
    }
    Json::Value dummy_response;
    int ret = CreateAndAddTrack(stream->live_url, opts, is_audio_required, stream_id, stream, dummy_response);
    if (ret != 0)
    {
        LOG(error) << "Unsuccessful in adding track for stream: " << stream_id << endl;
        return VmsErrorCode::VMSInternalError;
    }

    return VmsErrorCode::NoError;
}

int
PeerConnection::CreateAndAddTrack(string video, std::map<string, string, std::less<>>& opts
                                , bool is_audio_required, string streamLabel
                                , shared_ptr<StreamInfo> stream_info, Json::Value& response)
{
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource = nullptr;
    rtc::scoped_refptr<webrtc::AudioSourceInterface> audioSource = nullptr;
    try
    {
        videoSource = this->CreateVideoSource(video, opts);
        if (!videoSource)
        {
            throw std::invalid_argument("Failed to create Video Source");
        }
        if(is_audio_required)
        {
            std::map<string, media_info, std::less<>> media_details = StreamMonitor::getInstance()->getSupportedSubSessions(video);
            std::map<string, media_info, std::less<>>::iterator it;
            it = media_details.find("audio");
            if (it != media_details.end() || stream_info->stream_type == StreamType::Udp)
            {
                LOG(info) << "Audio supported for uri " << video << endl;
                audioSource = this->CreateAudioSource(video, opts);
                if (!audioSource)
                {
                    throw std::invalid_argument("Failed to create Audio Source");
                }
            }
            else
            {
                LOG(info) << "Audio unsupported for uri " << video << endl;
                /* this flag is used to add track below, so setting it to false */
                is_audio_required = false;
            }
        }
    }
    catch(const std::invalid_argument& e)
    {
        string err_msg = e.what();
        LOG(error) << err_msg << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, err_msg.c_str())
        return -1;
    }

    LOG(verbose) << "Adding Stream to map" << endl;
    m_streamMap[streamLabel] = std::make_pair(videoSource, audioSource);

    std::map<std::string, AudioVideoPair, std::less<>>::iterator it = m_streamMap.find(streamLabel);
    if (it != m_streamMap.end())
    {
        AudioVideoPair pair = it->second;
        rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource(pair.first);
        if (!videoSource)
        {
            LOG(error) << "Cannot create capturer video:" << video << endl;
        }
        else
        {
            rtc::scoped_refptr<webrtc::VideoTrackInterface> video_track;
            video_track = m_peer_connection_factory->CreateVideoTrack(videoSource, streamLabel + "_video");
            if ((video_track) && (!m_pc->AddTrack(video_track, {streamLabel}).ok()))
            {
                LOG(error) << "Adding video track to stream failed" << endl;
                goto cleanup;
            }
            else
            {
                LOG(verbose) << "video track added to PeerConnection" << endl;
            }
        }
        if(is_audio_required)
        {
            rtc::scoped_refptr<webrtc::AudioSourceInterface> audioSource(pair.second);
            if (!audioSource)
            {
                LOG(error) << "Cannot create capturer audio:" << video << endl;
            }
            else
            {
                rtc::scoped_refptr<webrtc::AudioTrackInterface> audio_track;
                audio_track = m_peer_connection_factory->CreateAudioTrack(streamLabel + "_audio", audioSource.get());
                if ((audio_track) && (!m_pc->AddTrack(audio_track, {streamLabel}).ok()))
                {
                    LOG(error) << "Adding audio track to stream failed" << endl;
                    goto cleanup;
                }
                else
                {
                    LOG(verbose) << "stream added to PeerConnection" << endl;
                }
            }
        }
    }
    else
    {
        LOG(error) << "Cannot find stream" << endl;
        goto cleanup;
    }
    return 0;
cleanup:
    string err_msg = "Adding MediaStream failed";
    LOG(error) << err_msg << endl;
    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, err_msg.c_str())
    return -1;
}

void PeerConnection::setAudioPlayout(bool value)
{
    if (m_pc)
    {
        m_pc->SetAudioPlayout(value);
    }
}

void PeerConnection::deleteSeatFromRP(const string& peerId)
{
    vector<string> headers;
    string response;
    bool res = false;
    string RP_HTTP_URL;

    LOG(info) << "Deleting RP seat for peerId="<<peerId<<endl;
    string sessionId = string("sessionId: ") + peerId;
    headers.push_back(sessionId);

    RP_HTTP_URL = "http://" + GET_CONFIG().reverse_proxy_server_address + "/v1/routes/seats";
    res = curlDeleteRequest(RP_HTTP_URL, headers, response);
    if (res == false)
    {
        LOG(error) << "Curl post request failed for RP" << endl;
        return;
    };
    return;
}

VmsErrorCode
PeerConnection::getAudioVideoPair(std::map<std::string, AudioVideoPair, std::less<>>::iterator& it
                                , const std::string &mediaSessionId
                                , Json::Value& response)
{
    it = m_streamMap.find(mediaSessionId);
    if (it == m_streamMap.end())
    {
        if (mediaSessionId.empty() && !m_streamMap.empty())
        {
            it = m_streamMap.begin();
        }
        else
        {
            string msg = "Video source not found " + mediaSessionId;
            LOG(error) << msg << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, msg.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode
PeerConnection::getMediaSources(const std::string &mediaSessionId,
                                rtc::scoped_refptr<webrtc::VideoTrackSourceInterface>& videoSource,
                                rtc::scoped_refptr<webrtc::AudioSourceInterface>& audioSource,
                                Json::Value& response)
{
    std::map<std::string, AudioVideoPair, std::less<>>::iterator audio_video_pair_it;
    VmsErrorCode ret = getAudioVideoPair(audio_video_pair_it, mediaSessionId, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    AudioVideoPair pair = audio_video_pair_it->second;
    LOG(verbose) << "Video Source Found " << pair.first.get() << endl;

    videoSource = pair.first;
    audioSource = pair.second;
    return ret;
}

VmsErrorCode
PeerConnection::startConnection(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    std::string peerid;
    std::string audiourl;
    std::string streamId;
    std::string startTime;
    std::string endTime;
    std::string remote_addr;
    bool isDataChannel = false;
    unordered_map<string, string> urlParameters;

    peerid = in.get("peerid", EMPTY_STRING).asString();
    streamId = in.get("streamId", EMPTY_STRING).asString();
    startTime = in.get("startTime", EMPTY_STRING).asString();
    endTime = in.get("endTime", EMPTY_STRING).asString();
    audiourl = in.get("audiourl", EMPTY_STRING).asString();
    isDataChannel = in.get("isDataChannel", false).asBool();
    remote_addr = req_info.get("remote_addr", EMPTY_STRING).asString();

    std::map<std::string, std::string, std::less<>> opts;
    std::string quality = GET_CONFIG().webrtc_sender_quality;
    CHECK_VALID_QUALITY(quality);
    opts["quality"] = quality;

    urlParameters["peerid"] = peerid;
    urlParameters["streamId"] = streamId;
    urlParameters["sensorId"] = streamId;
    urlParameters["startTime"] = startTime;
    urlParameters["endTime"] = endTime;
    urlParameters["audiourl"] = audiourl;
    urlParameters["remote_addr"] = remote_addr;

    LOG(info) << __FUNCTION__ << " peerid:" << peerid  << ", streamId:" << streamId
                << ", startTime:" << startTime << ", endTIme:" << endTime
                << ", remote_addr:" << remote_addr << endl;

    for (const auto& x : opts)
    {
        LOG(info) << x.first << ": " << x.second << endl;
    }

    // add local stream
    if (!isDataChannel)
    {
        VmsErrorCode ret = AddStreams(urlParameters, opts, response);
        if (ret != VmsErrorCode::NoError)
        {
            LOG(error) << "Can't add stream" << endl;
            return ret;
        }
    }

    return VmsErrorCode::NoError;
}

void PeerConnection::setDeviceName(const string& sensorId)
{
    std::shared_ptr<nv_vms::DeviceManager> deviceManager = m_peerConnectionManager->getDeviceManager();
    shared_ptr<SensorInfo> sensor = deviceManager->getSensorInfo(m_deviceId);
    if (sensor.get())
    {
        m_streamStats.setCameraName(sensor->name);
        m_observer->setDeviceName(sensor->name);
    }
}

std::string PeerConnection::getNwInterface()
{
    if(m_observer.get())
    {
        return m_observer->getNwInterface();
    }
    return "";
}

const pair<string, int> PeerConnection::getAvailableSeatFromRP(const string& sessionId, const string& remote_ipAddr,
    const string& private_ip, const string& private_port)
{
    pair<string, int> seat;
    if(m_peerConnectionManager)
    {
        seat = m_peerConnectionManager->getAvailableSeatFromRP(sessionId, remote_ipAddr, private_ip, private_port);
    }
    return seat;
}

VmsErrorCode PeerConnection::streamSettings(const std::string &peerId,
                                            const Json::Value &data,
                                            Json::Value& response)
{
    std::string mediaSessionId = data.get("mediaSessionId", EMPTY_STRING).asString();
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
    rtc::scoped_refptr<webrtc::AudioSourceInterface>      audioSource;
    VmsErrorCode ret = getMediaSources(mediaSessionId, videoSource, audioSource, response);
    if (ret != VmsErrorCode::NoError)
    {
        return ret;
    }

    rtc::VideoSourceInterface<webrtc::VideoFrame>* videoSourceInterface = static_cast<rtc::VideoSourceInterface<webrtc::VideoFrame>*>(videoSource.get());
    /* TODO MMS Phase 2: Need to revisit during MMS Phase 2 support */
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        TrackSource<NvGstVideoCapturer>* gst_video = dynamic_cast<TrackSource<NvGstVideoCapturer>*>(videoSourceInterface);
        if (gst_video != nullptr)
        {
            std::unordered_map<std::string, std::string> opts;
            Json::Value overlay = data.get("overlay", EMPTY_STRING);
            if (overlay.isObject())
            {
                // Use the unified parsing function with temporary map
                std::map<std::string, std::string> tempOpts;
                setOverlayOptsBasedOnJson(tempOpts, overlay);

                // Convert map to unordered_map for compatibility
                for (const auto& pair : tempOpts)
                {
                    opts[pair.first] = pair.second;
                }
            }
            gst_video->streamSettingTrackSource(opts);
            goto success;
        }
    }

    LOG(error) << "Video track not found" << endl;
    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
    return VmsErrorCode::VMSInternalError;

success:
    response = true;
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnection::setOffer(const Json::Value& in, Json::Value& answer)
{
    std::string type;
    std::string sdp;
    Json::Value jmessage = in.get("sessionDescription", EMPTY_STRING);
    if (!rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionTypeName, &type) || !rtc::GetStringFromJsonObject(jmessage, kSessionDescriptionSdpName, &sdp))
    {
        LOG(warning) << "Can't parse received message.";
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, answer, "Can't parse received message")
        return VmsErrorCode::InvalidParameterError;
    }
    else
    {
        // if (sdp.find("x-google-start-bitrate") == string::npos)
        // {
        //     /* If client doesn't provide the bitrates in sdp, then add it here */
        //     sdp = addWebrtcBitrateToSDP(in, sdp);
        // }
        // set remote offer
        webrtc::SessionDescriptionInterface *session_description(webrtc::CreateSessionDescription(type, sdp, nullptr));
        if (!session_description)
        {
            LOG(warning) << "Can't parse received session description message.";
        }
        else
        {
            std::promise<const webrtc::SessionDescriptionInterface *> remotepromise;
            std::string sdp;
            rtc::scoped_refptr<SetSessionDescriptionObserver> remoteSessionObserver(SetSessionDescriptionObserver::Create(m_pc.get(), remotepromise,sdp));
            m_pc->SetRemoteDescription(remoteSessionObserver.get(), session_description);
            // waiting for remote description
            std::future<const webrtc::SessionDescriptionInterface *> remotefuture = remotepromise.get_future();
            if (remotefuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
            {
                LOG(verbose) << "remote_description is ready";
            }
            else
            {
                LOG(warning) << "remote_description is NULL";
            }
        }

    }
    return VmsErrorCode::NoError;
}

VmsErrorCode PeerConnection::getAnswer(const Json::Value& in, Json::Value& answer)
{
    m_clientPublicIpAddr = in.get("clientIpAddr", EMPTY_STRING).asString();
    LOG(info) << "Client ip address from remote:" << m_clientPublicIpAddr << endl;

    // create answer
    webrtc::PeerConnectionInterface::RTCOfferAnswerOptions rtcoptions;
    rtcoptions.offer_to_receive_video = 1;
    rtcoptions.offer_to_receive_audio = 1;
    std::promise<const webrtc::SessionDescriptionInterface *> localpromise;
    std::string sdp;
    m_pc->CreateAnswer(CreateSessionDescriptionObserver::Create(m_pc.get(), localpromise, sdp), rtcoptions);

    // waiting for answer
    std::future<const webrtc::SessionDescriptionInterface *> localfuture = localpromise.get_future();
    if (localfuture.wait_for(std::chrono::milliseconds(5000)) == std::future_status::ready)
    {
        // answer with the created answer
        webrtc::SessionDescriptionInterface *descInterface = const_cast<webrtc::SessionDescriptionInterface *>(localfuture.get());
        if (descInterface)
        {
            if (GET_CONFIG().use_reverse_proxy == true /*&& m_peerConnectionManager->isRpStunAvailable() == false*/)
            {
                string sdpLite = m_observer->getSdpWithIceLite(descInterface, m_peerid, m_clientPublicIpAddr);
                if (sdpLite.empty() == false)
                {
                    sdp = sdpLite;
                }
            }
            answer[kSessionDescriptionTypeName] = descInterface->type();
            sdp = addWebrtcBitrateToSDP(in, sdp);
            answer[kSessionDescriptionSdpName] = sdp;
        }
        else
        {
            LOG(error) << "Failed to create answer";
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to create answer")
            return VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        LOG(error) << "Failed to create answer";
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, answer, "Failed to create answer")
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

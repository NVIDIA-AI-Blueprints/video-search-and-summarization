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

#include "WebrtcCallbacks.h"
#include "PeerConnection.h"
#include "network_utils.h"
#include "pc/session_description.h"
#include "p2p/base/transport_info.h"
#include "rtc_base/ref_counted_object.h"
#include "modules/video_coding/codecs/nvidia/NvVideoFrameBuffer.h"
#include "nvbufsurface.h"
#include "Websocket.h"

#if ENABLE_DUMMY_UDP_TRACK
// Debug code to trigger dummy udp pipeline
#include "nvgrpc.h"
#include "gst_utils.h"
#endif
using namespace vst_webrtc;

constexpr int CODEC_TYPE_DEFAULT = 1;

// Names used for a IceCandidate JSON object.
const char kCandidateSdpMidName[] = "sdpMid";
const char kCandidateSdpMlineIndexName[] = "sdpMLineIndex";
const char kCandidateSdpName[] = "candidate";

// Names used for a SessionDescription JSON object.
const char kSessionDescriptionTypeName[] = "type";
const char kSessionDescriptionSdpName[] = "sdp";

inline const char* PeerConnectionStateString(webrtc::PeerConnectionInterface::PeerConnectionState state)
{
    switch (state)
    {
        case webrtc::PeerConnectionInterface::PeerConnectionState::kNew:   return "New";
        case webrtc::PeerConnectionInterface::PeerConnectionState::kConnecting:   return "Connecting";
        case webrtc::PeerConnectionInterface::PeerConnectionState::kConnected:   return "Connected";
        case webrtc::PeerConnectionInterface::PeerConnectionState::kDisconnected:   return "Disconnected";
        case webrtc::PeerConnectionInterface::PeerConnectionState::kFailed:   return "Failed";
        case webrtc::PeerConnectionInterface::PeerConnectionState::kClosed:   return "Closed";
        default:      return "Invalid";
    }
}

inline const char* IceConnectionStateString(webrtc::PeerConnectionInterface::IceConnectionState state)
{
    switch (state)
    {
        case webrtc::PeerConnectionInterface::kIceConnectionNew:   return "New";
        case webrtc::PeerConnectionInterface::kIceConnectionChecking:   return "Checking";
        case webrtc::PeerConnectionInterface::kIceConnectionConnected:   return "Connected";
        case webrtc::PeerConnectionInterface::kIceConnectionCompleted:   return "Completed";
        case webrtc::PeerConnectionInterface::kIceConnectionFailed:   return "Failed";
        case webrtc::PeerConnectionInterface::kIceConnectionDisconnected:   return "Disconnected";
        case webrtc::PeerConnectionInterface::kIceConnectionClosed:   return "Closed";
        case webrtc::PeerConnectionInterface::kIceConnectionMax:   return "Max";
        default:      return "Invalid";
    }
}

PeerConnectionObserver::PeerConnectionObserver(PeerConnection* peerConnection,
                                                const std::string& peerid)
                            : m_peerConnection(peerConnection)
                            , m_peerid(peerid)
                            , m_iceCandidateList(Json::arrayValue)
                            , m_prevTimestamp(0)
                            , m_prevBytesReceived(0)
{
    LOG(verbose) << __FUNCTION__ << "CreatePeerConnection peerid:" << peerid;
    m_statsCallback = rtc::make_ref_counted<PeerConnectionStatsCollectorCallback>();
}

PeerConnectionObserver::~PeerConnectionObserver()
{
    LOG(info) << __PRETTY_FUNCTION__ << endl;
    m_videosink.reset();
    m_audiosink.reset();
    if(m_peerConnectionTimeout)
    {
        m_peerConnectionTimeout.reset();
    }
}

/* ---------------------------------------------------------------------------
**  ICE callback
** -------------------------------------------------------------------------*/
void PeerConnectionObserver::OnIceCandidate(const webrtc::IceCandidateInterface *candidate)
{
    if (candidate == nullptr)
    {
        return;
    }
    std::string candidate_string;
    if (!candidate->ToString(&candidate_string))
    {
        LOG(error) << "Failed to serialize candidate";
    }
    else
    {
        LOG(info) << "OnIceCandidate candidate = " << candidate_string << endl;
        if (GET_CONFIG().use_reverse_proxy == true /*&& m_peerConnection->isRpStunAvailable() == false*/)
        {
            /* Ignore the ice candidate in case of RP */
            std::unique_lock<std::mutex> iceCandidate_lock(m_iceCandidateMonitorMutex);
            m_isIceCandidateReceived = true;
            m_iceCandidateMonitorCv.notify_all();
            return;
        }
        Json::Value jmessage;
        jmessage[kCandidateSdpMidName] = candidate->sdp_mid();
        jmessage[kCandidateSdpMlineIndexName] = candidate->sdp_mline_index();
        jmessage[kCandidateSdpName] = candidate_string;
        m_iceCandidateList.append(jmessage);
        string wsConnectionId = m_peerConnection->getWsConnectionId();
        string connectionId = !wsConnectionId.empty() ? wsConnectionId : m_deviceId;
        string pcType = m_peerConnection->getPcType();
        {
            Json::Value candidates;
            candidates.append(jmessage);
            Json::Value wsResponse;
            wsResponse["apiKey"] = "api/v1/" + pcType + "/iceCandidate";
            wsResponse["peerId"] = m_peerid;
            wsResponse["data"] = candidates;
            GET_WEBSOCKET_INSTANCE()->sendMessage(connectionId, jsonToString(wsResponse), MG_WEBSOCKET_OPCODE_TEXT);
        }

        do
        {
            string newHost_candidate;
            if (candidate->candidate().type() == cricket::LOCAL_PORT_TYPE && m_isHostCandidateGenerated == false)
            {
                string node_ip;
                char *node_ip_env = getenv("NODE_IP");
                if (node_ip_env != nullptr)
                {
                    node_ip = string(node_ip_env);
                }
                else
                {
                    node_ip = g_hostIp;
                }

                string local_ip = candidate->candidate().address().ipaddr().ToString();
                if (local_ip == node_ip)
                {
                    /* No need to generate new candidate since hostIp is same as localIp */
                    LOG(info) << "Not generating duplicate candidate, Since hostIp and localIp are same = " << node_ip << endl;
                    break;
                }
                newHost_candidate = candidate_string;
                size_t pos = candidate_string.find(local_ip);
                if (pos != std::string::npos)
                {
                    newHost_candidate.replace(pos, local_ip.length(), node_ip);
                }
                LOG(info) << "Generated new host-candidate = " << newHost_candidate << endl;
                if (newHost_candidate.empty() == false)
                {
                    jmessage[kCandidateSdpName] = newHost_candidate;
                    m_iceCandidateList.append(jmessage);

                    Json::Value candidates;
                    candidates.append(jmessage);
                    Json::Value wsResponse;
                    wsResponse["apiKey"] = "api/v1/" + pcType + "/iceCandidate";
                    wsResponse["peerId"] = m_peerid;
                    wsResponse["data"] = candidates;
                    GET_WEBSOCKET_INSTANCE()->sendMessage(connectionId, jsonToString(wsResponse), MG_WEBSOCKET_OPCODE_TEXT);
                    m_isHostCandidateGenerated = true;
                }
            }
        } while (0);

        /* Get the public IpAddress from stun candidates */
        if (m_myPublicIpAddr.empty())
        {
            if (candidate->candidate().type().find("stun") != string::npos)
            {
                const rtc::SocketAddress address = candidate->candidate().address();
                m_myPublicIpAddr = address.HostAsURIString();
            }
        }
    }
}

/* ---------------------------------------------------------------------------
**  ICE candidate pair selected callback
** -------------------------------------------------------------------------*/
void PeerConnectionObserver::OnIceSelectedCandidatePairChanged(const cricket::CandidatePairChangeEvent &event)
{
    LOG(info) << "Last pair received time:" << event.last_data_received_ms << ", reason: " << event.reason << endl;
    LOG(info) << "Local candidate = { " << event.selected_candidate_pair.local.network_id()
                                    << ", " << event.selected_candidate_pair.local.type()
                                    << ", " << event.selected_candidate_pair.local.network_name()
                                    << ", " << event.selected_candidate_pair.local.protocol()
                                    << ", " << event.selected_candidate_pair.local.address().ToString() << " }" << endl;
    m_nwInterface = event.selected_candidate_pair.local.network_name();

    LOG(info) << "Remote candidate = { " << event.selected_candidate_pair.remote.network_id()
                                    << ", " << event.selected_candidate_pair.remote.type()
                                    << ", " << event.selected_candidate_pair.remote.network_name()
                                    << ", " << event.selected_candidate_pair.remote.protocol()
                                    << ", " << event.selected_candidate_pair.remote.address().ToString() << " }" << endl;
}

/* ---------------------------------------------------------------------------
**  ICE Candidate Error callback
** -------------------------------------------------------------------------*/
void PeerConnectionObserver::OnIceCandidateError(const std::string& host_candidate,
                                                const std::string& url,
                                                int error_code,
                                                const std::string& error_text)
{
    LOG(error) << "ICE candidate error host_candidate:" << host_candidate << ", url:" << url << endl;
    if (error_code >= 300 && error_code <= 699)
    {
        // STUN errors are in the range 300-699. See RFC 5389, section 15.6 for a list of codes.
        // TURN adds a few more error codes; see RFC 5766, section 15 for details.
        LOG(error) << "STUN/TURN error[" << error_code << "] : " << error_text << endl;
    }
    else if (error_code >= 700 && error_code <= 799)
    {
        // Server could not be reached; a specific error number is
        // provided but these are not yet specified.
        LOG(error) << "Server error["<< error_code << "] : " << error_text << endl;
    }
}

VideoSink::VideoSink(webrtc::VideoTrackInterface* track,
                        std::shared_ptr<WebrtcStream> producer,
                        string deviceId, PeerConnection* pcm,
                        string peerid):
                            m_track(track),
                            m_producer(producer),
                            m_deviceId(deviceId),
                            m_peerConnection(pcm),
                            m_peerid(peerid)
{
    LOG(info) << __PRETTY_FUNCTION__ << " track:" << m_track->id();
    m_fpsDisplay.reset(new FPSDisplay(
                    WEBRTC_INPUT_FPS_CAPTURE_INTERVAL_SEC,
                    WEBRTC_INPUT_FPS_PUBLISH_INTERVAL_SEC));
    m_track->AddOrUpdateSink(this, rtc::VideoSinkWants());
    if (GET_CONFIG().webrtc_in_passthrough)
    {
        m_passThrough = true;
    }
}

VideoSink::~VideoSink()
{
    LOG(info) << __PRETTY_FUNCTION__ << " track:" << m_track->id();
    m_track->RemoveSink(this);
}

void VideoSink::OnFrame(const webrtc::VideoFrame& video_frame)
{
    rtc::scoped_refptr<webrtc::VideoFrameBuffer> frame_buffer = video_frame.video_frame_buffer();
    rtc::scoped_refptr<webrtc::I420BufferInterface> buffer(frame_buffer->ToI420());
    int width = frame_buffer->width();
    int height = frame_buffer->height();
    void* dataY = nullptr;
    /* Size is 1.5 times resolution for I420 Buffer
       and Size is stored in width and codec type is specified in height for pass through */
    unsigned int size = width * height * 1.5;
    int64_t startLatencyTime = 0;
    if (video_frame.start_processing_time() != 0)
    {
        startLatencyTime = video_frame.start_processing_time();
    }

    /* If SW I420 buffer is unavailable, then Webrtc HW decoder was used */
    if (buffer == nullptr)
    {
        size = sizeof(NvBufSurface);
        dataY = (void *)&frame_buffer; //rtc::scoped_refptr<NvVideoFrameBuffer>*
    }
    else
    {
        width = buffer->width();
        height = buffer->height();
        dataY = m_passThrough ? (void *)buffer.get()->DataY() : (void *)buffer.get();
    }

    /* Frame rate measurements */
    m_fpsDisplay->displayFPS(video_frame.timestamp_us()/1000, m_peerid + string(":") + m_deviceId + string("_in"));
    Stats& pcStreamStats = Stats::getInstance();
    double avg_fps = m_fpsDisplay->getAvgFPS();
    if (avg_fps >= 0)
    {
        pcStreamStats.setPeerStatsMapFps(m_peerid, avg_fps);
    }

    pcStreamStats.setPeerStatsMapResolution(m_peerid, width, height);

    if (m_notify)
    {
        // Notify for first frame, then set m_notify to false
        int isNotifySuccessful = m_peerConnection->notify("camera_streaming", m_deviceId, width, height);
        if (isNotifySuccessful != -2)
        {
            m_notify = false;
        }
    }

    int ret = 0;
    if (m_passThrough)
    {
        ret = m_producer->addFrame ("video", dataY, width, m_fpsDisplay->getInstFPS(), 0, height, startLatencyTime);
    }
    else
    {
        ret = m_producer->addFrame ("video", dataY, size, m_fpsDisplay->getInstFPS(), 0, CODEC_TYPE_DEFAULT, startLatencyTime);
    }

    if (ret == FATAL_ERROR_CODE)
    {
        LOG(error) << "FATAL error observed during encoder add frame, Closing peer connection for m_peerid:" << m_peerid << endl;
        m_peerConnection->closePeerConnection();
        return;
    }

    m_webrtcVideoInDataFlow = true;
}

AudioSink::AudioSink(webrtc::AudioTrackInterface* track,
    std::shared_ptr<WebrtcStream> producer, string deviceId, PeerConnection* pcm)
        : m_track(track)
        , m_producer(producer)
        , m_deviceId(deviceId)
        , m_peerConnection(pcm)
{
    std::cout << __PRETTY_FUNCTION__ << " track:" << m_track->id() << std::endl;
    m_track->AddSink(this);
}

AudioSink::~AudioSink()
{
    std::cout << __PRETTY_FUNCTION__ << " track:" << m_track->id() << std::endl;
}       

void AudioSink::OnData(const void* audio_data,
    int bits_per_sample,
    int sample_rate,
    size_t number_of_channels,
    size_t number_of_frames)
{
    if (m_notify)
    {
        // Notify for first frame, then set m_notify to false
        int isNotifySuccessful = m_peerConnection->notify("camera_streaming", m_deviceId);
        if (isNotifySuccessful != -2)
        {
            m_notify = false;
        }
    }
    size_t streamSizeBytes = (bits_per_sample / 8) * number_of_channels * number_of_frames;
    m_producer->addFrame ("audio", (unsigned char *)audio_data, streamSizeBytes, sample_rate, number_of_channels);
    m_webrtcAudioInDataFlow = true;
}

void SetSessionDescriptionObserver::OnSuccess()
{
    if (m_pc && m_pc->local_description())
    {
        m_pc->local_description()->ToString(&m_sdp);
        m_promise.set_value(m_pc->local_description());
    }
    else if (m_pc && m_pc->remote_description())
    {
        m_promise.set_value(m_pc->remote_description());
    }
}
void SetSessionDescriptionObserver::OnFailure(webrtc::RTCError error)
{
    LOG(error) << __PRETTY_FUNCTION__ << " " << error.message() << endl;
    m_promise.set_value(nullptr);
}

void CreateSessionDescriptionObserver::OnSuccess(webrtc::SessionDescriptionInterface* desc)
{
    if (desc)
    {
        m_pc->SetLocalDescription(SetSessionDescriptionObserver::Create(m_pc, m_promise, m_sdp), desc);
    }
}

void CreateSessionDescriptionObserver::OnFailure(webrtc::RTCError error)
{
    LOG(error) << __PRETTY_FUNCTION__ << " " << error.message() << endl;
    m_promise.set_value(nullptr);
}

void PeerConnectionStatsCollectorCallback::OnStatsDelivered(const rtc::scoped_refptr<const webrtc::RTCStatsReport>& report)
{
    for (const webrtc::RTCStats& stats : *(report.get()))
    {
        Json::Value statsMembers;
        for (const webrtc::RTCStatsMemberInterface* member : stats.Members())
        {
            if (member->is_defined())
            {
                statsMembers[member->name()] = member->ValueToJson();
            }
        }
        // Store the transport ID as it is required for further parsing of stats
        if (stats.id().at(0) == 'T')
        {
            m_transportId = stats.id();
        }
        m_report[stats.id()] = statsMembers;
    }
}

void PeerConnectionObserver::OnAddStream(rtc::scoped_refptr<webrtc::MediaStreamInterface> stream)
{
    LOG(info) << __PRETTY_FUNCTION__;
    if (m_peerConnection->m_isClient == false)
    {
        /* Returning, since this is only for webrtc-reciever */
        return;
    }
    /**
     * Device might not be created by the time this function is executed. Add stream to recorder
     * when device creation is successfully done in PeerConnectionManager::createWebrtcDevice.
     */
    m_producer = std::make_shared<WebrtcStream>(m_deviceId, m_deviceName, m_peerid);
    WebrtcStreamProducer::getInstance()->addStreamProducer (m_deviceId, m_producer);

    LOG(info) << __PRETTY_FUNCTION__ << " nb video tracks:" << stream->GetVideoTracks().size();
    std::cout << " nb audio tracks:" << stream->GetAudioTracks().size() << std::endl;

    webrtc::VideoTrackVector videoTracks = stream->GetVideoTracks();
    if (videoTracks.size() > 0)
    {
        m_videosink.reset(new VideoSink(videoTracks.at(0).get(), m_producer, m_deviceId, m_peerConnection, m_peerid));
        m_producer->setVideoTrackEnabled(true);
    }

    webrtc::AudioTrackVector audioTracks = stream->GetAudioTracks();
    if (audioTracks.size() > 0)
    {
        m_audiosink.reset(new AudioSink(audioTracks.at(0).get(), m_producer, m_deviceId, m_peerConnection));
        m_producer->setAudioTrackEnabled(true);
    }

    if(GET_CONFIG().dump_webrtc_input_stats)
    {
        m_peerConnection->m_streamStats.createLogFile();
    }
    m_bitrateThresold = (100 - GET_CONFIG().webrtc_in_video_bitrate_thresold_percentage) / 100.0;
    m_bitrateThresold *= STANDARD_BITRATE_720P_KBPS;
    m_webrtcInputDataWatchDog = make_unique<Bosma::Scheduler>(1);
    m_webrtcInputDataWatchDog->every(
                WEBRTC_INPUT_DATA_WATCH_DOG_SCHEDULER_INTERVAL, [=]() {
                checkInputDataFlowStatus();
                Json::Value inboundVideoStats = getInboundVideoStats();
                uint64_t currentBitrate = calculateCurrentBitrate(inboundVideoStats);
                if (GET_CONFIG().enable_network_bandwidth_notification)
                {
                    sendBandwidthQualityMessage(currentBitrate);
                }
                if(GET_CONFIG().dump_webrtc_input_stats)
                {
                    m_peerConnection->m_streamStats.logStatsInFile(inboundVideoStats, currentBitrate * 1000);
                }
    });
}
void PeerConnectionObserver::OnRemoveStream(rtc::scoped_refptr<webrtc::MediaStreamInterface> stream)
{
    LOG(info) << __PRETTY_FUNCTION__ << endl;
    if (m_peerConnection->m_isClient == false)
    {
        /* Returning, since this is only for webrtc-reciever */
        return;
    }
}

void PeerConnectionObserver::OnDataChannel(rtc::scoped_refptr<webrtc::DataChannelInterface> channel)
{
    LOG(verbose) << __PRETTY_FUNCTION__;
    GET_DATA_CHANNEL()->addChannelObserver(m_peerid, channel);
}

void PeerConnectionObserver::OnRenegotiationNeeded()
{
    LOG(verbose) << __PRETTY_FUNCTION__ << " peerid:" << m_peerid << endl;
}

void PeerConnectionObserver::OnSignalingChange(webrtc::PeerConnectionInterface::SignalingState state)
{
    LOG(info) << __PRETTY_FUNCTION__ << " state:" << state << " peerid:" << m_peerid << endl;
    if(state == webrtc::PeerConnectionInterface::kStable)
    {
        //If peer connection is established wait for ten seconds and check for
        //ICE Candidates. If ICE Candidates not received, then close that peer.
        const uint64_t frequency = GET_CONFIG().webrtc_peer_conn_timeout_sec;
        std::chrono::seconds seconds (frequency);
        m_peerConnectionTimeout = make_unique<Bosma::Scheduler>(PEER_CONNECTION_TIMEOUT_THREAD_COUNT);
        m_peerConnectionTimeout->in(seconds, [=]() {
            m_peerConnection->isIceCandidateAdded();
        });

        rtc::scoped_refptr<webrtc::PeerConnectionInterface> pc = m_peerConnection->getRtcPeerConnection();
        if (pc.get() && m_peerConnection->m_isClient == true)
        {
            std::vector<rtc::scoped_refptr<webrtc::RtpSenderInterface>> senders = pc->GetSenders();
            for (auto stream : senders)
            {
                if (stream->media_type() == cricket::MediaType::MEDIA_TYPE_VIDEO)
                {
                    std::vector<std::string> streamVector = stream->stream_ids();
                    if (streamVector.size() > 0)
                    {
                        std::string streamLabel = streamVector[0];
                        LOG(info) << "Starting Playback for " << streamLabel << endl;
                        m_peerConnection->startPlayback(streamLabel);
                    }
                }
            }
        }
    }
}

// Called any time the PeerConnectionState changes.
void PeerConnectionObserver::OnConnectionChange(webrtc::PeerConnectionInterface::PeerConnectionState new_state)
{
    LOG(info) << " Peer connection state:" << PeerConnectionStateString(new_state)  << " peerid:" << m_peerid << endl;
    if (new_state == webrtc::PeerConnectionInterface::PeerConnectionState::kDisconnected ||
        new_state == webrtc::PeerConnectionInterface::PeerConnectionState::kFailed ||
        new_state == webrtc::PeerConnectionInterface::PeerConnectionState::kClosed)
    {
        m_iceCandidateList.clear();
        m_webrtcInputDataWatchDog.reset();
        if (m_peerConnection)
        {
            m_peerConnection->closePeerConnection();
        }
    }
    if(new_state == webrtc::PeerConnectionInterface::PeerConnectionState::kConnected)
    {
        m_peerConnection->startPlayback(m_peerid);
#if ENABLE_DUMMY_UDP_TRACK
// Debug code to trigger dummy udp pipeline
        if (m_peerid.substr(m_peerid.length() - 2) == "_1")
        {
            int32_t audio_port = 0, video_port = 0;
            GrpcClient::getInstance()->CreateDummyUDPDevice(m_deviceId, audio_port, video_port);
            GstDummyUdpPipeline::getInstance()->startUdpPipeline(m_deviceId, audio_port, video_port, true);
        }
        else
        {
            LOG(warning) << "Dummy UDP pipeline not triggered for peerid:" << m_peerid << endl;
        }
#endif
    }
}

void PeerConnectionObserver::OnIceConnectionChange(webrtc::PeerConnectionInterface::IceConnectionState state)
{
    LOG(info) << " ICE connection state:" << IceConnectionStateString(state)  << " peerid:" << m_peerid << endl;
    if (state == webrtc::PeerConnectionInterface::kIceConnectionConnected)
    {
        m_peerConnection->m_isIceConnected = true;
        if (m_peerid.substr(m_peerid.length() - 2) == "_1")
        {
            m_peerConnection->notify("camera_add", m_deviceId);
        }
    }
    else if (state == webrtc::PeerConnectionInterface::kIceConnectionDisconnected)
    {
        if (m_peerConnection->m_isClient == true)
        {
            if (!GET_WEBSOCKET_INSTANCE()->isConnected(m_peerid))
            {
                m_peerConnection->closePeerConnection();
            }
        }
    }
}

void PeerConnectionObserver::checkInputDataFlowStatus()
{
    if((m_videosink && m_videosink->m_webrtcVideoInDataFlow.load() == false) &&
        (m_audiosink && m_audiosink->m_webrtcAudioInDataFlow.load() == false))
    {
        LOG(info) << "Webrtc input data flow is stalled closing session peerId: " << m_peerid << endl;
        m_videosink->m_webrtcVideoInDataFlow = true;
        m_audiosink->m_webrtcAudioInDataFlow = true;
        m_peerConnection->closePeerConnection();
    }
    else
    {
        if (m_videosink)
        {
            m_videosink->m_webrtcVideoInDataFlow = false;
        }
        if (m_audiosink)
            m_audiosink->m_webrtcAudioInDataFlow = false;
    }
}

Json::Value PeerConnectionObserver::getInboundVideoStats()
{
    Json::Value retJsonVal = Json::nullValue;
    Json::Value reqInfo = Json::nullValue;
    Json::Value in = Json::nullValue;
    Json::Value inboundVideoStats = Json::nullValue;
    std::vector<std::string> stats;
    string peerId = m_peerid;
    bool is_sync = true;
    uint32_t timeout_sec = 2;
    std::string transportID;
    std::string rtcInboundRTPVideoStream;
    m_peerConnection->postToEventLoop("stats", peerId, in, reqInfo, retJsonVal, is_sync, timeout_sec);
    if (retJsonVal.isObject() == false)
    {
        LOG(error) << "Failed to get webrtc inbound stats" << endl;
        return inboundVideoStats;
    }
    if (retJsonVal.isMember("stats"))
    {
        stats = retJsonVal["stats"].getMemberNames();
        if (retJsonVal["stats"].get("transportId", "ERROR") != "ERROR")
        {
            transportID = retJsonVal["stats"]["transportId"].asString();
        }
        rtcInboundRTPVideoStream = "I" + transportID + "V";
        for(uint32_t i = 0; i < stats.size(); i++)
        {
            /* Check for "RTCInboundRTPVideoStream" stats */
            if (stats.at(i).find(rtcInboundRTPVideoStream) == 0)
            {
                inboundVideoStats = retJsonVal["stats"][stats.at(i)];
                break;
            }
        }
    }
    else
    {
        LOG(error) << "Failed to get webrtc inbound stats" << endl;
    }
    return inboundVideoStats;
}

void PeerConnectionObserver::sendBandwidthQualityMessage(uint64_t currentBitrate)
{
    Json::Value msg;
    msg["message_type"] = "alert";
    msg["message_catogory"] = "network_qos";
    // if bitrate is less than threshold send low quality message
    msg["network_bandwidth_state"] = currentBitrate < m_bitrateThresold ?
                                    "low" : "high";
    GET_DATA_CHANNEL()->sendMessageOnAllDataChannels(jsonToString(msg));
}

uint64_t PeerConnectionObserver::calculateCurrentBitrate(const Json::Value &inboundVideoStats)
{
    uint64_t currentBitrate = 0;
    if (inboundVideoStats.isObject())
    {
        uint64_t currentTimestamp = 0;
        uint64_t currentBytesReceived = 0;
        if (inboundVideoStats.isMember("lastPacketReceivedTimestamp"))
        {
            try
            {
                string ts = inboundVideoStats.get("lastPacketReceivedTimestamp", "0").asString();
                size_t pos = ts.find_first_of("eE");
                // scientific notation conversion
                if (pos != std::string::npos)
                {
                    double value = std::stod(ts);
                    currentTimestamp = static_cast<uint64_t>(std::round(value));
                }
                else
                {
                    currentTimestamp = stoll(ts);
                }
            }
            catch(const std::invalid_argument& e)
            {
                LOG(error) << e.what() << endl;
            }
        }
        if (inboundVideoStats.isMember("bytesReceived"))
        {
            try
            {
                currentBytesReceived = stoll(inboundVideoStats.get("bytesReceived", "0").asString());
            }
            catch(const std::invalid_argument& e)
            {
                LOG(error) << e.what() << endl;
            }
        }
        if (m_prevBytesReceived != 0 && m_prevTimestamp != 0)
        {
            // bitrate calculation in kbps
            double elapsedTime = (currentTimestamp - m_prevTimestamp) / 1000;
            currentBitrate = elapsedTime > 0 ?
                                static_cast<uint64_t>((currentBytesReceived - m_prevBytesReceived) * 8.0 / elapsedTime) / 1000 : 0;
            LOG(warning) << "peer ID: " << m_peerid << ", current bitrate: " << currentBitrate << " kbps" << std::endl;
        }
        m_prevBytesReceived = currentBytesReceived;
        m_prevTimestamp = currentTimestamp;
    }
    else
    {
        LOG(error) << "Failed to get Inbound Stream Stats" << endl;
    }
    return currentBitrate;
}

const string PeerConnectionObserver::getSdpWithIceLite(
    webrtc::SessionDescriptionInterface *descInterface, const string& session_id, const string& remote_ipAddr)
{
    string sdp;
    pair<string, int> seat;
    bool is_first_transport = false;
    if (descInterface == nullptr)
    {
        LOG(error) << "SessionDescriptionInterface is null" << endl;
        return sdp;
    }

    if (!m_peerConnection->isRpStunAvailable())
    {
        /* Wait till we get the local ice candidate for the RP mapping */
        {
            std::unique_lock<std::mutex> iceCandidate_lock(m_iceCandidateMonitorMutex);
            if (m_isIceCandidateReceived == false)
            {
                auto until = std::chrono::system_clock::now() + std::chrono::milliseconds(1000);
                if (m_iceCandidateMonitorCv.wait_until(iceCandidate_lock, until) == std::cv_status::timeout)
                {
                    LOG(error) << "Timeout while wating for local host candidate" << endl;
                }
            }
        }
        string private_port;
        using webrtc::IceCandidateCollection;
        for (size_t i = 0; i < descInterface->number_of_mediasections(); ++i)
        {
            const IceCandidateCollection* cc1 = descInterface->candidates(i);
            for (size_t j = 0; j < cc1->count(); ++j)
            {
                cricket::Candidate candidate_for_rp = cc1->at(j)->candidate();
                if (candidate_for_rp.type() == cricket::LOCAL_PORT_TYPE)
                {
                    string candidate_string = candidate_for_rp.ToString();
                    const rtc::SocketAddress address = candidate_for_rp.address();
                    private_port = address.PortAsString();
                }
            }
            if (!private_port.empty())
            {
                break;
            }
        }
        if (private_port.empty())
        {
            LOG(error) << "Local host canidate is not generated, Skip RP mapping" << endl;
            return sdp;
        }

        /* Get the public-Ip & port from ReverseProxy */
        LOG(info) << "client_ipAddr = " << remote_ipAddr << " private_port:" << private_port << endl;
        seat = m_peerConnection->getAvailableSeatFromRP(session_id, remote_ipAddr, "", private_port);
        if (seat.first.empty() || seat.second == -1)
        {
            LOG(error) << "Failed to get the RP seat" << endl;
            return sdp;
        }
    }
    else
    {
        seat = m_peerConnection->getRpSeat();
        if (seat.first.empty() && seat.second != -1)
        {
            LOG(error) << "RP seat is not allocated for session_id:" << session_id << endl;
            return sdp;
        }
    }
    LOG(info) << "Creating candidate for publicIp:" << seat.first << ", publicPort:" << seat.second << endl;

    /* Get the endpoint from RP & add it as a candidate into sdp */
    const rtc::SocketAddress host_address(seat.first, seat.second);

    /* Higher the priority value, more the preference while selecting candidate for ice connection */
    static const uint32_t kCandidatePriority = 2130706431U;  // pref = 1.0
    /* Foundation property is a string which uniquely identifies the candidate across multiple transports.*/
    static const char kCandidateFoundation1[] = "a0+B/1";

    cricket::Candidate candidate;
    candidate.set_component(cricket::ICE_CANDIDATE_COMPONENT_DEFAULT);
    candidate.set_protocol(cricket::UDP_PROTOCOL_NAME);
    candidate.set_address(host_address);
    candidate.set_type(cricket::LOCAL_PORT_TYPE);
    candidate.set_foundation(kCandidateFoundation1);
    candidate.set_priority(kCandidatePriority);
    candidate.set_network_id(1);
    cricket::SessionDescription *desc = descInterface->description();
    if (desc == nullptr)
    {
        LOG(error) << "SessionDescription is null" << endl;
        return sdp;
    }
    for (auto& content : desc->contents())
    {
        if (is_first_transport == false)
        {
            candidate.set_transport_name(content.name);
            std::unique_ptr<webrtc::IceCandidateInterface> sdp_candidate =
                webrtc::CreateIceCandidate(content.name, 0, candidate);
            descInterface->AddCandidate(sdp_candidate.get());
            is_first_transport= true;
        }
        cricket::TransportInfo *transport_info = desc->GetTransportInfoByName(content.name);
        if (transport_info == nullptr)
        {
            LOG(error) << "webrtc::transport_info is null" << endl;
            return sdp;
        }
        transport_info->description.ice_mode = cricket::IceMode::ICEMODE_LITE;
    }
    descInterface->ToString(&sdp);
    return sdp;
}

void PeerConnectionObserver::shutdown()
{
    GET_DATA_CHANNEL()->removeChannelObserver(m_peerid);
}

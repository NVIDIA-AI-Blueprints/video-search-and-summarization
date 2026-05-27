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

#pragma once

#include "api/peer_connection_interface.h"
#include "Scheduler.h"
#include "webrtcstreamproducer.h"
#include "fps_display.h"
#include "webrtcDataChannel.h"
#include "logger.h"

#include <string>
#include <jsoncpp/json/json.h>

class PeerConnection;

namespace vst_webrtc
{
class PeerConnectionObserver;
class VideoSink : public rtc::VideoSinkInterface<webrtc::VideoFrame>
{
public:
    VideoSink(webrtc::VideoTrackInterface* track,
            std::shared_ptr<WebrtcStream> producer, std::string deviceId,
            PeerConnection* pcm, std::string peerid);

    virtual ~VideoSink();

    // VideoSinkInterface implementation
    virtual void OnFrame(const webrtc::VideoFrame& video_frame);

    std::atomic<bool>   m_webrtcVideoInDataFlow{false};
    std::string         m_fpsValues;
protected:
    rtc::scoped_refptr<webrtc::VideoTrackInterface> m_track;
    std::shared_ptr<WebrtcStream>                   m_producer;
    bool                                            m_notify {true};
    std::string                                     m_deviceId {""};
    PeerConnection*                                 m_peerConnection{nullptr};
    std::string                                     m_peerid {""};
    std::unique_ptr<FPSDisplay>                     m_fpsDisplay = nullptr;
    bool                                            m_passThrough {false};
};

class AudioSink : public webrtc::AudioTrackSinkInterface
{
public:
    AudioSink(webrtc::AudioTrackInterface* track,
        std::shared_ptr<WebrtcStream> producer, std::string deviceId,
        PeerConnection* pcm);

    virtual ~AudioSink();

    virtual void OnData(const void* audio_data,
        int bits_per_sample,
        int sample_rate,
        size_t number_of_channels,
        size_t number_of_frames);

    std::atomic<bool>   m_webrtcAudioInDataFlow{false};
protected:
    rtc::scoped_refptr<webrtc::AudioTrackInterface> m_track;
    std::shared_ptr<WebrtcStream>                   m_producer;
    bool                                            m_notify {true};
    std::string                                     m_deviceId {""};
    PeerConnection*                                 m_peerConnection{nullptr};
};

class SetSessionDescriptionObserver : public webrtc::SetSessionDescriptionObserver
{
    public:
        static SetSessionDescriptionObserver* Create(webrtc::PeerConnectionInterface* pc, std::promise<const webrtc::SessionDescriptionInterface*> & promise, std::string &sdp)
        {
            return new rtc::RefCountedObject<SetSessionDescriptionObserver>(pc, promise, sdp);
        }
        virtual void OnSuccess();
        virtual void OnFailure(webrtc::RTCError error);
    protected:
        SetSessionDescriptionObserver(webrtc::PeerConnectionInterface* pc, std::promise<const webrtc::SessionDescriptionInterface*> & promise, std::string &sdp) : m_pc(pc), m_promise(promise), m_sdp(sdp) {};

    private:
        webrtc::PeerConnectionInterface* m_pc;
        std::promise<const webrtc::SessionDescriptionInterface*> & m_promise;
        std::string &m_sdp;
};

class CreateSessionDescriptionObserver : public webrtc::CreateSessionDescriptionObserver
{
    public:
        static CreateSessionDescriptionObserver* Create(webrtc::PeerConnectionInterface* pc, std::promise<const webrtc::SessionDescriptionInterface*> & promise, std::string &sdp)
        {
            return new rtc::RefCountedObject<CreateSessionDescriptionObserver>(pc,promise, sdp);
        }
        virtual void OnSuccess(webrtc::SessionDescriptionInterface* desc);
        virtual void OnFailure(webrtc::RTCError error);
    protected:
        CreateSessionDescriptionObserver(webrtc::PeerConnectionInterface* pc, std::promise<const webrtc::SessionDescriptionInterface*> & promise, std::string &sdp) : m_pc(pc), m_promise(promise), m_sdp(sdp) {};

    private:
        webrtc::PeerConnectionInterface*                           m_pc;
        std::promise<const webrtc::SessionDescriptionInterface*> & m_promise;
        std::string &m_sdp;
};

class PeerConnectionStatsCollectorCallback : public webrtc::RTCStatsCollectorCallback
{
    public:
        PeerConnectionStatsCollectorCallback() {}
        void clearReport() { m_report.clear(); }
        Json::Value getReport() { return m_report; }
        std::string getTransportID() { return m_transportId; }

    protected:
        virtual void OnStatsDelivered(const rtc::scoped_refptr<const webrtc::RTCStatsReport>& report);

        Json::Value m_report;
        std::string m_transportId;
};

class PeerConnectionObserver : public webrtc::PeerConnectionObserver
{
public:
    PeerConnectionObserver(PeerConnection* peerConnection, const std::string& peerid);
    virtual ~PeerConnectionObserver();

    void getIceCandidateList(Json::Value& out) { out = m_iceCandidateList; }

    // PeerConnectionObserver interface
    virtual void OnAddStream(rtc::scoped_refptr<webrtc::MediaStreamInterface> stream);
    virtual void OnRemoveStream(rtc::scoped_refptr<webrtc::MediaStreamInterface> stream);
    virtual void OnDataChannel(rtc::scoped_refptr<webrtc::DataChannelInterface> channel);
    virtual void OnRenegotiationNeeded();

    virtual void OnIceCandidate(const webrtc::IceCandidateInterface* candidate);

    virtual void OnIceCandidateError(const std::string& host_candidate,
                            const std::string& url,
                            int error_code,
                            const std::string& error_text);

    virtual void OnIceSelectedCandidatePairChanged(const cricket::CandidatePairChangeEvent &event);

    virtual void OnSignalingChange(webrtc::PeerConnectionInterface::SignalingState state);

    // Called any time the PeerConnectionState changes.
    virtual void OnConnectionChange(webrtc::PeerConnectionInterface::PeerConnectionState new_state);
    virtual void OnIceConnectionChange(webrtc::PeerConnectionInterface::IceConnectionState state);
    virtual void OnIceGatheringChange(webrtc::PeerConnectionInterface::IceGatheringState) {
    }

    void setDeviceId(std::string deviceId)
    {
        m_deviceId = deviceId;
    }
    void setDeviceName(const string& device_name)
    {
        m_deviceName = device_name;
    }
    std::string getNwInterface()
    {
        return m_nwInterface;
    }
    string getPublicAddress() { return m_myPublicIpAddr; }
    void checkInputDataFlowStatus();
    Json::Value getInboundVideoStats();
    void sendBandwidthQualityMessage(uint64_t currentBitrate);
    uint64_t calculateCurrentBitrate(const Json::Value &inboundVideoStats);
    const std::string getSdpWithIceLite(webrtc::SessionDescriptionInterface *descInterface, const string& session_id, const std::string& remote_ipAddr);
    void shutdown();

private:
    PeerConnection*                                          m_peerConnection;
    const std::string                                        m_peerid;
    std::string                                              m_deviceId;
    std::string                                              m_deviceName;
    Json::Value                                              m_iceCandidateList;
    rtc::scoped_refptr<PeerConnectionStatsCollectorCallback> m_statsCallback;
    std::unique_ptr<vst_webrtc::VideoSink>                   m_videosink;
    std::unique_ptr<AudioSink>                               m_audiosink;
    std::unique_ptr<Bosma::Scheduler>                        m_peerConnectionTimeout;
    std::shared_ptr<WebrtcStream>                            m_producer;
    std::unique_ptr<Bosma::Scheduler>                        m_webrtcInputDataWatchDog;
    std::mutex                                               m_iceCandidateMonitorMutex;
    std::condition_variable                                  m_iceCandidateMonitorCv;
    std::atomic<uint64_t>                                    m_prevTimestamp;
    std::atomic<uint64_t>                                    m_prevBytesReceived;
    std::atomic<bool>                                        m_isIceCandidateReceived{false};
    double                                                   m_bitrateThresold;
    std::string                                              m_nwInterface;
    std::string                                              m_myPublicIpAddr;
    bool                                                     m_isHostCandidateGenerated = false;
};
} // namespace vst_webrtc
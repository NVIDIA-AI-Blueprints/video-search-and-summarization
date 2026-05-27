/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "stream_buffer.h"
#include "logger.h"
#include "NvMediaSource.hh"
#include "webrtcstreamproducer.h"
#include "mm_utils.h"
#include "RtspSyncPlayback.h"
#ifdef ENABLE_NATIVE_STREAM_MONITOR
#include "native_stream_monitor.h"
#endif

#define RTSP_SERVER_MAX_OUTPUT_BUFFER_SIZE 500*1000
#define AUDIO_CODEC_CONFIG_ID_16K_STEREO "1410"

using namespace std;

NvMediaSource
::NvMediaSource (const std::string& filename, eMediaType mediaType, eSourceType sourceType, string url_params, string session_id)
        : IMediaDataConsumer("NvMediaSource_" + session_id)
        , m_filename(filename)
        , m_mediaType(mediaType)
        , m_sourceType(sourceType)
        , m_demux (nullptr)
        , m_frameId(0)
        , m_url_params(url_params)
        , m_sessionId(session_id)
        , m_eventLoop("source_event_loop", process_source_message)
        , m_streamBuf(filename, STREAM_DEFAULT_BUFFER_SIZE)
        , m_is_error (false)
{
    LOG(info) << "::NvMediaSource filename:" << m_filename << ", media:" << mediaTypeAsString(m_mediaType)
        << ", source:" << m_sourceType << ", m_sessionId:" << m_sessionId << endl;

    setConsumerMediaType(m_mediaType);
    if (m_sourceType == SourceTypeFile)
    {
        if (m_mediaType == MediaTypeVideo)
        {
            /* Keep large bufferSize in case of 4k or high bit-rate contents */
            OutPacketBuffer::maxSize = GET_CONFIG().nv_streamer_rtsp_server_output_buffer_size_kb * 1000;
        }
        m_demux.reset(new GstDeMux(m_filename, mediaType));
        if (m_demux)
        {
            m_demux->setUrlParams(url_params);
            m_demux->setSessionId(m_sessionId);
        }
        if (GET_CONFIG().nv_streamer_sync_playback == true)
        {
            RtspSyncPlayback::getInstance()->insertDemuxer(m_demux);
        }
    }
    else if (m_sourceType == SourceTypeLive && m_mediaType == MediaTypeVideo)
    {
        OutPacketBuffer::maxSize = RTSP_SERVER_MAX_OUTPUT_BUFFER_SIZE;
        m_videoHeaderFrames = WebrtcStreamProducer::getInstance()->getVideoHeaders(m_filename);
    }
    else if (m_sourceType == SourceTypeNative && m_mediaType == MediaTypeVideo)
    {
        OutPacketBuffer::maxSize = RTSP_SERVER_MAX_OUTPUT_BUFFER_SIZE;
    }

    if (m_url_params.find("includeFrameId=true") != string::npos || GET_CONFIG().enable_rtsp_server_sei_metadata == true)
    {
        m_includeFrameId = true;
    }

    m_uuid = stringToHex(MEGA_SEI_CUSTOM_META_UUID) + "00";
    LOG(info) << "Session uuid: " << m_uuid << endl;
    if (GET_CONFIG().enable_mega_simulation)
    {
        string mega_sim_base_time = GET_CONFIG().mega_simulation_base_time;
        if (!mega_sim_base_time.empty())
        {
            m_simulationBaseTime = isoToEpoch(mega_sim_base_time, true) * 1000;
        }
        else
        {
            m_simulationBaseTime = std::chrono::duration_cast<std::chrono::nanoseconds>
                (std::chrono::system_clock::now().time_since_epoch()).count();
        }
        LOG(info) << "Using Simulation BaseTime:" << m_simulationBaseTime << endl;
    }
}

NvMediaSource::~NvMediaSource ()
{
    LOG(info) << "~NvMediaSource filename:" << m_filename << ", media:" << mediaTypeAsString(m_mediaType)
        << ", source:" << m_sourceType << ", m_sessionId:" << m_sessionId << endl;
}

int NvMediaSource::create()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "create";
    m_eventLoop.postMsg(data);
    return 0;
}

bool NvMediaSource::isError()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->m_isError;
    }
    return false;
}

void NvMediaSource::registerCallback(cb_frameSourceEvent_t callback, void *owner)
{
    m_callback[owner] = callback;
}

void NvMediaSource::sendSourceEvent(eFrameSourceEvent sourceEvent)
{
    for (auto& callback: m_callback)
    {
        if (callback.second != nullptr)
        {
            callback.second(sourceEvent, callback.first);
        }
    }
}

void NvMediaSource::setClock(GstClock* global_clock, GstClockTime base_time)
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_demux->setGstClock(global_clock, base_time);
    }
}

void NvMediaSource::play()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "play";

    //m_frameId = 0;
    if (GET_CONFIG().enable_mega_simulation && GET_CONFIG().nv_streamer_sync_playback == true)
    {
        m_frameId = RtspSyncPlayback::getInstance()->getGlobalFrameId();
    }

    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_streamBuf.clear();
        m_streamBuf.play();
        m_demux->registerDataCallback(m_filename, getself());
    }
    else if (m_sourceType == SourceTypeLive)
    {
        WebrtcStreamProducer::getInstance()->registerDataCallback(m_filename, getself());
        m_streamBuf.play();
    }
    else if (m_sourceType == SourceTypeNative)
    {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
        NativeStreamMonitor::getInstance()->registerDataCallback(m_filename, getself(), BITSTREAM_H265);
        m_streamBuf.play();
#endif
    }
    m_eventLoop.postMsg(data);
    return;
}

void NvMediaSource::setBufferState(eBufferMsg buffer_msg)
{
    switch(buffer_msg)
    {
        case BufferMsgPlay:
            m_streamBuf.play();
            break;
        case BufferMsgPause:
            m_streamBuf.pause();
            break;
        case BufferMsgClear:
            m_streamBuf.clear();
            break;
        default:
            LOG(error) << "Wrong Buffer state" << endl;
            break;
    }
}

void NvMediaSource::onFrame(FrameParams& params)
{
    std::vector<uint8_t> content;
    uint8_t nal_type = NaluType::kNalUnknown;

    if (params.m_buffer == nullptr || params.m_size == 0)
    {
        /* Consider zero-sized frame as EOS and Broadcast RTCP-Bye message */
        string eos_msg = STREAM_MSG_EOS;
        content.insert(content.end(), eos_msg.begin(), eos_msg.end());
    }
    else
    {
        content.insert(content.end(), params.m_buffer, params.m_buffer + params.m_size);
    }

    /* Push the contents in the stream buffer */
    LOG(verbose2) << "MediaSource OnFrame size: " << content.size() << ", pts:" << params.m_presentationTime.tv_sec << "." << params.m_presentationTime.tv_usec << endl;
    if (!m_isFirstFrame && m_sourceState.find("play") != string::npos)
    {
        m_isFirstFrame = true;
        int64_t pts_val = params.m_presentationTime.tv_sec * 1000000 + params.m_presentationTime.tv_usec;
        LOG(warning) << "MediaSource Got first frame size:" << content.size()
                     << ", pts:" << convertEpocToISO8601_2(pts_val).c_str()
                     << ", mediaType:" << mediaTypeAsString(m_mediaType) << endl;
    }

    if (m_vodOverlayManager)
    {
        m_vodOverlayManager->sendFrame(params);
        return;
    }

    if (m_mediaType == MediaTypeVideo)
    {
        if(iequals(params.m_codec, "h265"))
        {
            nal_type = parseH265NaluType(content.data(), content.size());
        }
        else
        {
            nal_type = parseH264NaluType(content.data(), content.size());
        }

        /* Generate frameId, timestamp & insert as a sei frame */
        if (isValidDataNAL(nal_type, params.m_codec))
        {
            if (m_sourceType == SourceTypeLive || m_sourceType == SourceTypeNative)
            {
                if (m_includeFrameId == true)
                {
                    insertSeiFrame(m_frameId, params.m_presentationTime, params.m_codec);
                }
                m_frameId++;
            }
            else
            {
                /* Use frameId from demuxer if provided */
                if (GET_CONFIG().enable_mega_simulation)
                {
                    struct timeval frame_pts;
                    frame_pts.tv_sec = params.m_serverPts / 1000000;
                    frame_pts.tv_usec = params.m_serverPts % 1000000;

                    /* If needed, Simulate after few initial frames */
                    if (m_sourceState.find("play") != string::npos)
                    {
                        int randomTimeInMilliSeconds = 0;
                        if (GET_CONFIG().nv_streamer_sync_playback == true)
                        {
                            randomTimeInMilliSeconds = RtspSyncPlayback::getInstance()->getSimulationWaitTime();
                        }
                        else
                        {
                            const int minSimTime_ms = GET_CONFIG().mega_simulation_delay_min_ms;
                            const int maxSimTime_ms = GET_CONFIG().mega_simulation_delay_max_ms;
                            /* Use secure random generator to prevent biased random sampling */
                            randomTimeInMilliSeconds = getSecureRandomInt(minSimTime_ms, maxSimTime_ms);
                        }
                        usleep(randomTimeInMilliSeconds * 1000);
                        LOG(verbose) << "Sending frameId:" << params.m_serverFrameId << ", size:" << params.m_size
                                << ", pts:" << convertEpocToISO8601_2(params.m_serverPts).c_str()
                                << ", delay:" << randomTimeInMilliSeconds << "ms" << endl;
                    }
                    if (m_includeFrameId == true)
                    {
                        insertMegaSimSeiFrame(m_frameId, frame_pts, params.m_codec);
                    }
                    m_frameId++;
                }
                else if (m_includeFrameId == true && params.m_serverFrameId != -1)
                {
                    insertSeiFrame(params.m_serverFrameId, params.m_presentationTime, params.m_codec);
                }
            }
        }

        /* Ignore AUD frames if any */
        if ((params.m_codec == "h264" && nal_type == NaluType::kAud) ||
            (params.m_codec == "h265" && nal_type == H265NaluType::AUD_NUT))
        {
            return;
        }
        /* This is required for live555 stack in case of discreteFramer */
        removeH264NalStartCodes(content);

        if (GET_CONFIG().enable_mega_simulation && isValidDataNAL(nal_type, params.m_codec) == false && params.m_size > 0)
        {
            /* Save the sps/pps/vps etc. headers to send it alongwith next simulated IDR frame */
            std::shared_ptr<DiscreteFrame> discrete_frame(
                    new DiscreteFrame(content, params.m_presentationTime, params.m_latencyStartTime));
            discrete_frame->m_codec = params.m_codec;
            discrete_frame->m_nalType = nal_type;
            m_spsPpsContent.push(discrete_frame);
            return;
        }
    }

    if (m_spsPpsContent.size() > 0)
    {
        while (!m_spsPpsContent.empty())
        {
            std::shared_ptr<DiscreteFrame> data = m_spsPpsContent.front();
            m_spsPpsContent.pop();
            m_streamBuf.push(data);
        }
    }

    /* Create FrameInfo object */
    std::shared_ptr<DiscreteFrame> discreteFrame(
            new DiscreteFrame(content, params.m_presentationTime, params.m_latencyStartTime));
    discreteFrame->m_codec = params.m_codec;
    discreteFrame->m_nalType = nal_type;

    /* Push the contents in the stream buffer */
    m_streamBuf.push(discreteFrame);
    return;
}

std::vector<uint8_t> NvMediaSource::getFramesForSdp()
{
    std::vector<uint8_t> frame;
    if (!m_videoHeaderFrames.empty())
    {
        frame = m_videoHeaderFrames.front();
        m_videoHeaderFrames.pop();
    }
    return frame;
}

void NvMediaSource::insertSeiFrame(int64_t frameId, struct timeval pts, string codec)
{
    std::vector<uint8_t> sei_frame;
    uint64_t frame_epoch = pts.tv_sec * 1000000000 + pts.tv_usec * 1000;
    string iso_time = convertEpocNsToISO8601(frame_epoch);

    Json::Value jsonData;
    jsonData["timestamp_iso8601"] = iso_time;
    jsonData["timestamp"] = frame_epoch;
    jsonData["frame_id"] = frameId;

    sei_frame = getUserDefinedSeiFrameFromJson(jsonData, m_uuid, codec);
    if (sei_frame.empty() == true)
    {
        return;
    }

    /* This is required for live555 stack in case of discreteFramer */
    removeH264NalStartCodes(sei_frame);

    /* Create FrameInfo object */
    std::shared_ptr<DiscreteFrame> discreteFrame(
            new DiscreteFrame(sei_frame, pts));

    /* Push the contents in the stream buffer */
    m_streamBuf.push(discreteFrame);
    return;
}

void NvMediaSource::insertMegaSimSeiFrame(int64_t frameId, struct timeval pts, string codec)
{
    std::vector<uint8_t> sei_frame;

    /* Providing time in nanoseconds in the SEI frames */
    //int64_t tick_ts = (pts.tv_sec * 1000 * 1000 * 1000) + pts.tv_usec * 1000;
    double tick_ts = (frameId == 0) ? 0.0 : (frameId / getFrameRate());
    uint64_t tick_ts_ns = static_cast<uint64_t>(tick_ts * 1000000000.0);
    uint64_t frame_epoch = m_simulationBaseTime + tick_ts_ns;
    string iso_time = convertEpocNsToISO8601(frame_epoch);

    Json::Value jsonData;
    jsonData["sim_time"] = tick_ts /*0.03333333333333333*/;
    jsonData["latent_sim_time_s"] = 0;
    jsonData["timestamp_iso8601"] = iso_time;
    jsonData["timestamp"] = frame_epoch;
    jsonData["frame_id"] = frameId;

    sei_frame = getUserDefinedSeiFrameFromJson(jsonData, m_uuid, codec);
    if (sei_frame.empty() == true)
    {
        return;
    }

    /* This is required for live555 stack in case of discreteFramer */
    removeH264NalStartCodes(sei_frame);

    /* Create FrameInfo object */
    std::shared_ptr<DiscreteFrame> discreteFrame(
            new DiscreteFrame(sei_frame, pts));

    /* Push the contents in the stream buffer */
    m_streamBuf.push(discreteFrame);
    return;
}

int64_t NvMediaSource::getStartTime()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getFileStartTime();
    }
    return 0;
}

int64_t NvMediaSource::getActualStartTime()
{
    if (m_vodOverlayManager)
    {
        return m_vodOverlayManager->getFirstTs();
    }
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getActualStartTime();
    }
    return 0;
}


double NvMediaSource::getFrameRate()
{
    double default_frame_rate = 30.0;
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getFrameRate();
    }
    return default_frame_rate;
}

string NvMediaSource::getVideoCodec()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getVideoCodec();
    }
    else if (m_sourceType == SourceTypeLive)
    {
        return WebrtcStreamProducer::getInstance()->getVideoCodec(m_filename);
    }
    else if (m_sourceType == SourceTypeNative)
    {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
        return NativeStreamMonitor::getInstance()->getVideoCodec(m_filename);
#endif
    }
    return "";
}

string NvMediaSource::getAudioCodec()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getAudioCodec();
    }
    else if (m_sourceType == SourceTypeLive)
    {
        return WebrtcStreamProducer::getInstance()->getAudioCodec(m_filename);
    }
    return "";
}

int NvMediaSource::getSampleRate()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getSampleRate();
    }
    else if (m_sourceType == SourceTypeLive)
    {
        return WebrtcStreamProducer::getInstance()->getSampleRate(m_filename);
    }
    return 0;
}

int NvMediaSource::getChannels()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        return m_demux->getChannels();
    }
    else if (m_sourceType == SourceTypeLive)
    {
        return WebrtcStreamProducer::getInstance()->getChannels(m_filename);
    }
    return 0;
}

AacParams NvMediaSource::getAacParams()
{
    /* Coherent default for live + every fallback path: AAC-LC, 16 kHz,
     * stereo. config "1410" decodes to (AOT=2, sfi=8 -> 16000, ch_cfg=2),
     * which MUST match the sample_rate/channels we hand back, otherwise
     * the SDP rtpmap and fmtp lines disagree and VLC will decode but not
     * play (clock-rate mismatch at the audio sink). */
    AacParams fallback;
    fallback.sample_rate = 16000;
    fallback.channels    = 2;
    fallback.configStr   = AUDIO_CODEC_CONFIG_ID_16K_STEREO;

    if (m_sourceType == SourceTypeLive)
    {
        /* WebRTC live AAC encoder pipeline is hardcoded to 16k/stereo,
         * which already matches the fallback. */
        LOG(info) << "AacParams: live, fixed 16k stereo, config="
                  << fallback.configStr << endl;
        return fallback;
    }
    if (m_sourceType != SourceTypeFile)
    {
        LOG(info) << "AacParams: unknown sourceType, using fallback config="
                  << fallback.configStr << endl;
        return fallback;
    }

    /* Build AudioSpecificConfig deterministically from the audio metadata
     * already probed by GstDeMux::updateFileMetadata() (libav). This is
     * the single source of truth for the three values that MUST agree in
     * the audio SDP: rtpmap clock-rate, rtpmap channel count, and the
     * fmtp 'config=' AudioSpecificConfig.
     *
     * Previous implementation read the first 4 bytes from m_streamBuf to
     * parse an ADTS header, but ran before the GStreamer audio pipeline
     * had pushed any data into the buffer (only a 10ms usleep gate
     * upstream), which made the syncword check fail and forced the
     * 16k-stereo default. That produced an SDP where rtpmap claimed the
     * real rate (e.g. 44100) but fmtp 'config=' decoded to 16000 -- VLC
     * then decoded frames but dropped them all at the sink ("decoded > 0,
     * played = 0"). */
    int sample_rate = getSampleRate();
    int channels    = getChannels();

    /* Path A: prefer the AudioSpecificConfig bytes captured directly from
     * the GStreamer audio caps "codec_data" field. These are the canonical
     * config bytes that libav/qtdemux extracted from the file -- they
     * carry the *actual* AAC profile (LC, HE-AAC v1/v2, LD, ...) AND any
     * channel layout that can't be expressed via the 4-bit
     * channel_configuration field (e.g. 7-channel streams carried via PCE,
     * 22.2 surround, etc.). Trying codec_data BEFORE the strict channel
     * mapping below avoids spurious 16k-stereo fallbacks for valid AAC
     * files that simply have an unusual layout. */
    std::string codecDataHex;
    if (m_demux)
    {
        codecDataHex = m_demux->getAudioCodecData();
    }
    if (!codecDataHex.empty() && sample_rate > 0 && channels >= 1)
    {
        AacParams out;
        out.sample_rate = sample_rate;
        out.channels    = channels;
        out.configStr   = codecDataHex;
        LOG(info) << "AacParams: rate=" << out.sample_rate
                  << ", channels=" << out.channels
                  << ", configStr=" << out.configStr
                  << " (from codec_data)" << endl;
        return out;
    }

    /* Path B: no codec_data captured yet (e.g. DESCRIBE phase before the
     * audio pad-added has fired). Reconstruct an AAC-LC AudioSpecificConfig
     * from (sample_rate, channels). This path is restricted to channel
     * counts and sample rates that fit the simple 4-bit-field layout. */

    /* Map raw channel count to MPEG-4 AAC channel_configuration (4-bit
     * field in AudioSpecificConfig). Per ISO/IEC 14496-3 Table 1.19:
     *   1..6   -> configurations 1..6 (mirror)
     *   8      -> configuration 7 (7.1 surround; the spec skips
     *             configuration 7 == 7 channels intentionally)
     *   7      -> NO valid configuration; reject so the decoder doesn't
     *             mis-decode 7-channel audio as 7.1.
     * Anything outside this set falls back coherently. (7-channel PCE
     * layouts are NOT representable here -- they need to come in via the
     * codec_data path above.) */
    u_int8_t channel_configuration = 0;
    if (channels >= 1 && channels <= 6)
    {
        channel_configuration = static_cast<u_int8_t>(channels);
    }
    else if (channels == 8)
    {
        channel_configuration = 7;
    }
    if (sample_rate <= 0 || channel_configuration == 0)
    {
        /* Invalid probe result AND no authoritative codec_data to fall
         * back to. Use the coherent default for ALL THREE values
         * (rate, channels, configStr) so the SDP rtpmap can never disagree
         * with the SDP fmtp. */
        LOG(warning) << "Unsupported audio params from demux (rate=" << sample_rate
                     << ", channels=" << channels
                     << ") and no codec_data; using fallback AacParams"
                        " (16k stereo, config=" << fallback.configStr
                     << ")" << endl;
        return fallback;
    }

    /* Reverse-lookup MPEG-4 sampling_frequency_index (4 bits) for the given
     * sample rate. Indices 13..15 are reserved/explicit and not used here. */
    // 13 = count of standard MPEG-4 AAC sampling-frequency entries
    // (indices 0..12 in samplingFrequencyTable). Indices 13..15 in the
    // 16-entry table are spec-reserved zeros, so std::size() of the table
    // can't be used here.
    constexpr u_int8_t MPEG4_VALID_SAMPLING_FREQ_COUNT = 13;
    u_int8_t sampling_frequency_index = 0xF;
    for (u_int8_t i = 0; i < MPEG4_VALID_SAMPLING_FREQ_COUNT; ++i)
    {
        if (samplingFrequencyTable[i] == static_cast<unsigned>(sample_rate))
        {
            sampling_frequency_index = i;
            break;
        }
    }
    if (sampling_frequency_index == 0xF)
    {
        /* Non-standard sample rate (e.g. 50000): MPEG-4 sample-rate table
         * has no index for it. Fall back coherently. */
        LOG(warning) << "Sample rate " << sample_rate
                     << " not in MPEG-4 sample-rate table and no codec_data; "
                        "using fallback AacParams (16k stereo, config="
                     << fallback.configStr << ")" << endl;
        return fallback;
    }

    /* AAC-LC (audioObjectType=2). Correct for:
     *   - Live (WebRTC) streams handled above (voaacenc-only -> LC).
     *   - File uploads that go through our transcode pipeline (avenc_aac
     *     / voaacenc / fdkaacenc all default to LC).
     * Passthrough HE-AAC files would be mislabeled here, but those should
     * hit Path A (codec_data) once the audio pad has fired. */
    constexpr u_int8_t audioObjectType = 2;

    unsigned char asc[2];
    asc[0] = (audioObjectType << 3) | (sampling_frequency_index >> 1);
    asc[1] = ((sampling_frequency_index & 0x01) << 7) | (channel_configuration << 3);

    char configBuf[5];
    snprintf(configBuf, sizeof(configBuf), "%02X%02X", asc[0], asc[1]);

    AacParams out;
    out.sample_rate = sample_rate;
    out.channels    = channels;
    out.configStr   = configBuf;
    LOG(info) << "AacParams: rate=" << out.sample_rate
              << ", channels=" << out.channels
              << ", channel_config=" << static_cast<int>(channel_configuration)
              << ", configStr=" << out.configStr
              << " (reconstructed, AOT=LC fallback)" << endl;
    return out;
}

string NvMediaSource::getCodecConfigId()
{
    /* Backward-compatible accessor: delegates to getAacParams() so the
     * fallback policy is defined in exactly one place. Prefer
     * getAacParams() at SDP-build time so rtpmap and fmtp stay coherent. */
    return getAacParams().configStr;
}

void NvMediaSource::playWithOverlay()
{
    if (m_sourceType == SourceTypeFile)
    {
        m_streamBuf.pause();
        m_streamBuf.clear();
        m_streamBuf.play();

        std::shared_ptr<EventLoopData> data(new EventLoopData);
        data->m_taskName = "startOverlayPipeline";
        m_eventLoop.postMsg(data);
    }
}

void NvMediaSource::startOverlayPipeline_internal()
{
    LOG(info) << "Start overlay pipeline m_filename:" << m_filename << endl;
    m_vodOverlayManager = std::make_shared<VodOverlayManager>(this);
    m_vodOverlayManager->startOverlayPipeline();
    LOG(info) << "Started overlay pipeline m_filename:" << m_filename << endl;
}

void NvMediaSource::stopOverlayPipeline()
{
    if (m_sourceType == SourceTypeFile)
    {
        m_streamBuf.pause();

        std::shared_ptr<EventLoopData> data(new EventLoopData);
        data->m_taskName = "stopOverlayPipeline";
        m_eventLoop.postMsg(data);
    }
}

void NvMediaSource::stopOverlayPipeline_internal()
{
    LOG(info) << "Stop overlay pipeline m_filename:" << m_filename << endl;
    if (m_vodOverlayManager)
    {
        m_vodOverlayManager->stopOverlayPipeline();
        m_vodOverlayManager.reset();
    }
}

void NvMediaSource::seek (int64_t seek_pos , uint64_t end_time, float rate /*1*/)
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        std::shared_ptr<EventLoopData> data(new EventLoopData);
        data->m_taskName = "seek";
        m_streamBuf.pause();
        m_streamBuf.clear();
        m_demux->registerDataCallback(m_filename, getself());

        data->m_inData["seek_value"] = seek_pos;
        data->m_inData["rate"] = rate;
        data->m_inData["end_time"] = end_time;
        m_streamBuf.play();
        m_eventLoop.postMsg(data);
    }
}

void NvMediaSource::seekToStart ()
{
    m_eventLoop.setParent(this);
    if (GET_CONFIG().enable_mega_simulation && GET_CONFIG().nv_streamer_sync_playback == true)
    {
        m_frameId = RtspSyncPlayback::getInstance()->getGlobalFrameId();
    }

    if (m_sourceType == SourceTypeFile && m_demux)
    {
        std::shared_ptr<EventLoopData> data(new EventLoopData);
        data->m_taskName = "seekToStart";

        m_streamBuf.pause();
        m_streamBuf.clear();
        m_demux->registerDataCallback(m_filename, getself());
        m_eventLoop.postMsg(data);
        m_streamBuf.play();
    }
}


void NvMediaSource::pause()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "pause";

    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_demux->deregisterDataCallback(getself(), m_filename);
        m_streamBuf.pause();
        m_eventLoop.postMsg(data);
    }
    return;
}

void NvMediaSource::resume()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "resume";

    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_demux->registerDataCallback(m_filename, getself());
        m_eventLoop.postMsg(data);
    }
    m_streamBuf.play();
    return;
}

void NvMediaSource::resetActualStartTime()
{
    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_demux->resetActualStartTime();
    }
}

void NvMediaSource::destroy()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "destroy";

    if (m_sourceType == SourceTypeFile && m_demux)
    {
        m_demux->deregisterDataCallback(getself(), m_filename);
        if (GET_CONFIG().nv_streamer_sync_playback == true)
        {
            RtspSyncPlayback::getInstance()->removeDemuxer(m_demux);
        }
    }
    else if (m_sourceType == SourceTypeLive)
    {
        WebrtcStreamProducer::getInstance()->deregisterDataCallback(getself(), m_filename);
    }
    else if (m_sourceType == SourceTypeNative)
    {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
        NativeStreamMonitor::getInstance()->deregisterDataCallback(getself(), m_filename, BITSTREAM_H265);
#endif
    }
    m_streamBuf.pause();
    m_streamBuf.clear();
    m_eventLoop.postMsg(data);
    return;
}

void NvMediaSource::process_source_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<EventLoopData> source_data = std::static_pointer_cast<EventLoopData>(data);
    NvMediaSource* source = static_cast <NvMediaSource*>(parent);
    if (source == nullptr || source_data == nullptr)
    {
        LOG(error) << "Received null data" << endl;
        return;
    }
    LOG(verbose) << source_data->m_taskName << endl;
    if (source->getSourceType() == SourceTypeFile && source->m_demux)
    {
        if (source_data->m_taskName == "create")
        {
            source->m_demux->create_internal();
        }
        else if (source_data->m_taskName == "play")
        {
            source->m_demux->play_internal();
        }
        else if (source_data->m_taskName == "pause")
        {
            source->m_demux->pause_internal();
        }
        else if (source_data->m_taskName == "seekToStart")
        {
            source->m_demux->seekToStart();
        }
        else if (source_data->m_taskName == "seek")
        {
            int64_t seek_pos = source_data->m_inData["seek_value"].asInt64();
            uint64_t end_time = source_data->m_inData["end_time"].asUInt64();
            float rate = source_data->m_inData["rate"].asFloat();
            source->m_demux->seek(seek_pos, end_time, rate);
        }
        else if (source_data->m_taskName == "resume")
        {
            source->m_demux->resume_internal();
        }
        else if (source_data->m_taskName == "destroy")
        {
            source->m_demux->destroy_internal();
        }
        else if (source_data->m_taskName == "startOverlayPipeline")
        {
            source->startOverlayPipeline_internal();
        }
        else if (source_data->m_taskName == "stopOverlayPipeline")
        {
            source->stopOverlayPipeline_internal();
        }
        else
        {
            LOG(warning) << "Invalid action" << endl;
        }
    }
}

/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <memory>
#include <vector>
#include <jsoncpp/json/json.h>
#include <limits>
#include <atomic>
#include <gst/gst.h>
#include "stats.h"
#include "nvbufsurface.h"
#include "gstnvvstmeta.h"

using namespace std;

typedef enum
{
    InvalidMedia = -1,
    MediaTypeVideo,
    MediaTypeAudio,
    MediaTypeAudioVideo
} eMediaType;

inline const char* mediaTypeAsString(eMediaType media_type)
{
    switch (media_type)
    {
        case MediaTypeVideo:   return "Video";
        case MediaTypeAudio:   return "Audio";
        case MediaTypeAudioVideo:   return "AudioVideo";
        default:      return "InvalidMedia";
    }
}


enum ConsumerType
{
    encoder = 0,
    compositor = 1,
    webrtcConsumer = 2
};

typedef enum
{
    Buffer = 0,
    Exit = 1,
    Reset = 2,
    Last_Frame = 3,
    Invalid = 0xFFF
} EncoderMsgType;

class fdWrapper;
struct _RawFrameParams
{
    _RawFrameParams()
        : m_eos(false)
    {
        // Set the map data and size to 0
        m_map.data = nullptr;
        m_map.size = 0;
    }

    ~_RawFrameParams()
    {
        if (m_gstBuffer && m_map.data)
        {
            gst_buffer_unmap (m_gstBuffer, &m_map);
        }
        m_map.data = nullptr;
        m_map.size = 0;

        if (m_sample)
        {
            gst_sample_unref (m_sample);
            m_sample = nullptr;
        }
        // If this instance owns an independent buffer (e.g. created via gst_buffer_copy_deep),
        // we must unref it explicitly. Otherwise, the buffer is owned by m_sample or elsewhere.
        if (m_owns_gstBuffer && m_gstBuffer)
        {
            gst_buffer_unref(m_gstBuffer);
        }
        m_gstBuffer = nullptr;

        if (m_caps)
        {
            gst_caps_unref (m_caps);
            m_caps = nullptr;
        }
        if (m_fdWrapperObj)
        {
            delete (m_fdWrapperObj);
            m_fdWrapperObj = nullptr;
        }
    }

    string     m_streamId;
    unsigned char* m_buffer     = nullptr;
    bool m_isYuvBuffer          = false;
    GstBuffer* m_gstBuffer      = nullptr;
    bool m_owns_gstBuffer       = false;  // true if m_gstBuffer must be gst_buffer_unref()'d here
    GstMapInfo m_map;
    GstSample*  m_sample        = nullptr;
    GstCaps*    m_caps          = nullptr;
    int         m_sourceWidth   = 0;
    int         m_sourceHeight  = 0;
    /** Source strides (0 = use width/width/2 as contiguous). Set from decoder when non-contiguous. */
    int         m_srcStrideY    = 0;
    int         m_srcStrideU    = 0;
    int         m_srcStrideV    = 0;
    int         m_fd            = -1;
    int         m_index         = -1;
    bool        m_isTransformed = false;
    int         m_targetWidth   = 0;
    int         m_targetHeight  = 0;
    NvBufSurfaceLayout m_sourceLayout = NVBUF_LAYOUT_PITCH;
    NvBufSurfaceLayout m_targetLayout = NVBUF_LAYOUT_PITCH;
    NvBufSurfaceColorFormat m_sourceColorFormat = NVBUF_COLOR_FORMAT_NV12;
    NvBufSurfaceColorFormat m_targetColorFormat = NVBUF_COLOR_FORMAT_NV12;
    void *meta = nullptr;
    int64_t pts = 0;

    std::shared_ptr<fdWrapper>*       m_fdWrapperObj = nullptr;
    EncoderMsgType m_encoderMsgType = Buffer;
    std::atomic<bool> m_eos;
} typedef RawFrameParams;

typedef struct _FrameParams
{
    string          m_media;
    string          m_codec;
    unsigned char*  m_buffer;
    ssize_t         m_size;
    bool            m_needParsing;
    uint32_t        m_width;
    uint32_t        m_height;
    struct timeval  m_presentationTime;
    int64_t         m_serverPts;
    int64_t         m_serverFrameId;
    void*           m_extdata;
    uint32_t        m_frameNum;
    struct timeval  m_latencyStartTime;

    _FrameParams()
        : m_media("")
        , m_codec ("")
        , m_buffer (nullptr)
        , m_size (0)
        , m_needParsing (true)
        , m_width (1920)
        , m_height (1080)
        , m_presentationTime ({})
        , m_serverPts (0)
        , m_serverFrameId (-1)
        , m_extdata(nullptr)
        , m_frameNum(0)
        , m_latencyStartTime ({std::numeric_limits<time_t>::max(), std::numeric_limits<time_t>::max()})
        {
        }
} FrameParams;

class IMediaDataConsumer : public std::enable_shared_from_this<IMediaDataConsumer>
{
    public:
        // Default constructor - should be avoided, use named constructor instead
        IMediaDataConsumer() : m_consumerName("UnknownConsumer")
        {
            m_transcodeStats.setElementName(m_consumerName);
        }

        // Constructor with consumer name - preferred way
        IMediaDataConsumer(const std::string& consumerName) : m_consumerName(consumerName)
        {
            m_transcodeStats.setElementName(m_consumerName);
        }

        shared_ptr<IMediaDataConsumer> getself()
        {
            try
            {
                return shared_from_this();
            }
            catch (const bad_weak_ptr& e)
            {
                // LOG(error) << "Bad Weak pointer error: " << e.what() << endl;
            }
            return shared_ptr<IMediaDataConsumer>(nullptr);
        }

            virtual ~IMediaDataConsumer()
            {
                m_transcodeStats.printTotalStats();
                m_transcodeStats.clearQueue();
            }

        virtual void onFrame(FrameParams& frame_params) {}
        virtual void onFrame(std::shared_ptr<RawFrameParams> frame_data) {};

        virtual eMediaType getConsumerMediaType() { return m_mediaType; }
        virtual void setConsumerMediaType(eMediaType media_type) { m_mediaType = media_type; }

        std::vector<uint8_t> parseAndCreateFrame(FrameParams& params, bool *retIDR = nullptr);
        bool isSpsAvailable();
        bool isPpsAvailable();
        bool isSpsPpsAvailable();

        virtual void setWebrtcBroadcaster(void* broadcaster) { };
        virtual void onLastFrame() { }
        virtual void reset() { }
        /* Update start time for overlay */
        virtual void updateStartTime(string start_time) { }
        /* Set decoder frame size provides original resolution decoded */
        virtual void setOriginalFrameSize(int w, int h) { }
        virtual void setOriginalFrameSize() { }
        virtual void setIPCMeta() { };
        virtual void getwebRTCFeedback(int* qp, int* bitrate, double* frame_rate) {}
        void startStatsProcessing()
        {
            m_transcodeStats.startProcessing();
        }
        virtual void setConsumerType(ConsumerType type)
        {
            m_consumerType = type;
        }
        virtual ConsumerType getConsumerType()
        {
            return m_consumerType;
        }

        // Get consumer name
        virtual std::string getConsumerName() const
        {
            return m_consumerName;
        }

        // ─────────────────────────────────────────────────────────────
        // Writer lifecycle methods - default no-op implementations
        // ─────────────────────────────────────────────────────────────
        virtual bool start() { return true; }
        virtual void stop() { }
        virtual void sendEOS() { }
        virtual bool waitForCompletion(int64_t /*timeout_secs*/) { return true; }
        virtual bool hasError() const { return false; }
        virtual std::shared_ptr<IMediaDataConsumer> getAudioConsumer() { return nullptr; }
        virtual void* getPipeline() const { return nullptr; }

        // Get actual first frame PTS in milliseconds (for remux mode filename correction)
        // Returns -1 if not available/not tracked
        virtual int64_t getActualStartPtsMs() const { return -1; }

        // Get actual last frame PTS in milliseconds (offset from file start, i.e.
        // FIXED_TS_OFFSET already subtracted).
        // Returns -1 if not available/not tracked.
        virtual int64_t getActualEndPtsMs() const { return -1; }

        bool m_startConsuming = false;
        CodecStats   m_transcodeStats;
    private:
        std::vector<uint8_t>    m_spsCfg;
        std::vector<uint8_t>    m_ppsCfg;
        std::vector<uint8_t>    m_vpsCfg;
        eMediaType m_mediaType {MediaTypeVideo};
        ConsumerType m_consumerType {encoder};
        std::string m_consumerName;
};

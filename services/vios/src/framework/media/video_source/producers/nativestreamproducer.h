/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "logger.h"
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <atomic>
#include "nvvideoencoder.h"
#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include "videosinkinfo.h"

typedef enum
{
    RAW_NV12 = 0,
    BITSTREAM_H264,
    BITSTREAM_H265
} ConsumerStreamType;

typedef enum
{
    BASELINE = 0,
    MAIN,
    HIGH
} ProfileTypes;

inline constexpr const char* DEFAULT_NATIVE_STREAM_VIDEO_CODEC = "h265";
inline constexpr int DEFAULT_NATIVE_STREAM_FRAME_RATE = 30;
inline constexpr int DEFAULT_NATIVE_STREAM_KEY_FRAME_INTERVAL = 30;
inline constexpr int DEFAULT_NATIVE_STREAM_BITRATE = 8000000;
inline constexpr int DEFAULT_NATIVE_STREAM_GOV_LENGTH = 30;

inline constexpr int MAX_RESET_ATTEMPTS = 3;

struct PropertyDescription
{
    gint propertyValue;
    std::string description;
};

// Enable it if you want to dmup the yuv data and bitstream data into the file
//#define DUMP_FRAMES
class NativeStreamProducer
{
    public:
        string m_streamId;
        NativeStreamProducer(std::string streamId, std::string sensorName, const string location);
        ~NativeStreamProducer();
        bool startPipeline();
        void stopPipeline();
        void setOptions(const std::map<std::string, std::string, std::less<>> &opts);

        void addConsumer(shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType);
        void removeConsumer(shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType);
        GstFlowReturn onNewSampleYUV(GstElement* sink);
        static GstFlowReturn onNewSampleBitstream(GstElement* sink, NativeStreamProducer* self);
        static gboolean busWatch(GstBus *bus, GstMessage *msg, gpointer data);

        string getVideoCodec() { return m_videoCodec; }
        int getFramerate() { return m_frameRate; }
        int getIdrInterval() { return m_idrInterval; }
        int getIframeInterval() { return m_iframeInterval; }
        int getBitrate() { return m_bitrate; }
        void getResolution(string& width, string& height)
        {
            width = std::to_string(m_sourceWidth);
            height = std::to_string(m_sourceHeight);
        }
        int getProfile() { return m_profile; }
        bool getImageSettings(SensorImageSettingsValues& imageValues, SensorImageSettingsOptions& imageOptions);
        void getEncodeSettings(SensorVideoEncoderSettingsValues& encoderValues, SensorEncoderSettingsOptions& encoderOptions);

        std::string getstate();
        void setQuality(const std::string&, const std::string& quality);
        void removeConsumer(const std::string&);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>> getWebrtcBroacasterList() { return m_videoSinkList; }
        void setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer);
        void setConsumerReady(const string& peerid, bool isReady = true);
        int getVideoSinkListSize() { return m_videoSinkList.size(); }
        FrameSize handleDRC(const string& peerid, int targetPixels, int targetFPS);
        std::atomic<GstState>   m_state{GST_STATE_NULL};

    private:
        string                                      m_sensorName;
        string                                      m_location;
        int                                         m_resetAttempts;
        string                                      m_videoCodec;
        int                                         m_frameRate;
        int                                         m_idrInterval;
        int                                         m_iframeInterval;
        int                                         m_bitrate;
        int                                         m_profile;
        std::atomic<int>                            m_sourceWidth{WIDTH_1080p};
        std::atomic<int>                            m_sourceHeight{HEIGHT_1080p};
        std::mutex                                  m_consumerLock;
        std::vector<shared_ptr<IMediaDataConsumer>>    m_yuvConsumersList;
        std::vector<shared_ptr<IMediaDataConsumer>> m_bitstreamConsumersList;
        GstElement* m_pipeline = nullptr;
        GstElement* m_source = nullptr;
        GstElement* m_capsFilterArgusCameraSrc = nullptr;
        GstElement* m_capsFilterBeforeEncoder = nullptr;
        GstElement* m_yuvSink = nullptr;
        GstElement* m_tee = nullptr;
        GstElement* m_queueYuvSink = nullptr;
        GstElement* m_queueBitstreamSink = nullptr;
        GstElement* m_encoder = nullptr;
        GstElement* m_bitstreamSink = nullptr;
        GstElement* m_h265parse = nullptr;
        GstElement* m_nvvidconv = nullptr;
        GstElement* m_capsFilterNvvidconv = nullptr;
        GstElement* m_capsFilterAfterEncoder = nullptr;
        guint m_busWatchId = G_MAXUINT;
        std::string m_peerid;
        std::string m_sensorType;
        std::atomic<bool> m_stop{false};

        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>> m_videoSinkList;
        std::mutex m_videoSinkLock;
        std::time_t m_lastDRCTime {0};
        std::size_t m_resolutionIndex = 0;
        std::mutex  m_pipelineLock;

        #ifdef DUMP_FRAMES
        std::string m_yuvFilePath;
        std::ofstream m_yuvFile;
        std::string m_bitstreamFilePath;
        std::ofstream m_bitstreamFile;
        #endif

        int getVideoDeviceIndex(const std::string& devicePath);
        FrameSize qualityToFrameSize(const string& quality);
        bool isSinkPresent ();
        bool isDRCAllowed ();
        void updateEncoderConfig()
        {
            m_videoCodec = DEFAULT_NATIVE_STREAM_VIDEO_CODEC;
            m_frameRate = DEFAULT_NATIVE_STREAM_FRAME_RATE;
            m_sourceWidth = WIDTH_1080p;
            m_sourceHeight = HEIGHT_1080p;
            m_idrInterval = DEFAULT_NATIVE_STREAM_KEY_FRAME_INTERVAL;
            m_iframeInterval = DEFAULT_NATIVE_STREAM_KEY_FRAME_INTERVAL;
            m_bitrate = DEFAULT_NATIVE_STREAM_BITRATE;
            m_profile = MAIN;
        }
        void resetPipeline();
        void getEnumSupportedProperty(const std::string& propertyName, const std::vector<PropertyDescription>& descriptions,
        std::vector<std::string>& outOptions);
        void getEnumPropertyValue(const std::string& propertyName, const std::vector<PropertyDescription>& descriptions,
        std::string& outValue);
        void getFloatProperty(const std::string& propertyName, std::string& minString,
        std::string& maxString, std::string& currentString);
        void getGainRangeInfo(std::string& currentString);
};
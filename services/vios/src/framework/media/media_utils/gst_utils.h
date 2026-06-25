/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <gst/gst.h>
#include <string>
#include "error_code.h"
#include "logger.h"
#include "libasync++/async++.h"
#include "../overlays/overlay_internal.h"
#include <gst/app/gstappsrc.h>
#include <json/json.h>
#include <algorithm>
#include <climits>
#include <sstream>

inline constexpr int DEFAUL_KEY_FRAME_INTERVAL = 30;
inline constexpr int MAX_KEYFRAME_INTERVAL_SEC = 5;
inline constexpr int UDP_BUFFER_SIZE = 1000000;

using namespace std;

typedef struct _gstElements {
    _gstElements() : m_pipeline(nullptr), m_source(nullptr), m_typefind(nullptr), m_depay(nullptr)
                    , m_demuxer(nullptr), m_muxer(nullptr), m_identity(nullptr)
                    , m_videoQueue(nullptr), m_videoParser(nullptr), m_filter(nullptr)
                    , m_videoDecoder(nullptr), m_videoEncoder(nullptr)
                    , m_audioQueue(nullptr), m_audioDec(nullptr), m_audioConvert(nullptr)
                    , m_opusEnc(nullptr), m_opusParse(nullptr), m_mux (nullptr)
                    , m_sink(nullptr), m_overlayBin(nullptr), m_fileLocations(nullptr), m_isError(false)
                    , m_playInLoop(false), m_busWatchId(G_MAXUINT), m_bus(nullptr), m_parserSrcPadOutCount(0)
                    , m_transcodebin(nullptr), m_identity1(nullptr), m_identity2(nullptr), m_identity3(nullptr)
                    , m_capsfilter(nullptr), m_streamStartCount(0), m_lastFrameTimestamp(-1), m_seekStartPos(0)
                    , m_seekEndPos(0), m_videoCodec("h264")
                    , m_isQtMux(false), m_enableOverlay(false), m_overlayTsOffset(0) , m_overlayFileTs(0)
    {
    }

    GstElement*     m_pipeline = nullptr;
    GstElement*     m_source = nullptr;
    GstElement*     m_typefind = nullptr;
    GstElement*     m_depay = nullptr;
    GstElement*     m_demuxer = nullptr;
    GstElement*     m_muxer = nullptr;
    GstElement*     m_identity = nullptr;

    GstElement*     m_videoQueue = nullptr;
    GstElement*     m_videoParser = nullptr;
    GstElement*     m_filter = nullptr;
    GstElement*     m_parserAfterEncode = nullptr;
    GstElement*     m_videoDecoder = nullptr;
    GstElement*     m_videoEncoder = nullptr;
    GstElement*     m_videoConverter = nullptr;

    GstElement*     m_audioQueue = nullptr;
    GstElement*     m_audioDec = nullptr;
    GstElement*     m_audioConvert = nullptr;
    GstElement*     m_opusEnc = nullptr;
    GstElement*     m_opusParse = nullptr;

    GstElement*     m_mux = nullptr;
    GstElement*     m_sink = nullptr;
    GstElement*     m_overlayBin = nullptr;
    GStrv           m_fileLocations;
    bool            m_isError;
    bool            m_playInLoop;
    guint           m_busWatchId;
    GstBus*         m_bus = nullptr;
    int             m_parserSrcPadOutCount;

    GstElement*     m_transcodebin;
    GstElement*     m_identity1;
    GstElement*     m_identity2;
    GstElement*     m_identity3;
    GstElement*     m_capsfilter;

    int64_t         m_streamStartCount;
    int64_t         m_lastFrameTimestamp;
    int64_t         m_seekStartPos;
    int64_t         m_seekEndPos;
    string          m_videoCodec;
    Json::Value     m_capsJson; // holds profile/level/tier and other caps fields
    bool            m_isQtMux = false;
    bool            m_isAudio = true;
    bool            m_enableOverlay;
    int64_t         m_overlayTsOffset;
    int64_t         m_overlayFileTs;
    int             m_width  = 0;
    int             m_height = 0;
    int             m_fpsNumerator = 0;
    int             m_fpsDenominator = 0;
} gstElements;

typedef struct _StreamParam
{
    _StreamParam() : m_inFilePath   ("")
                    , m_inContainer  ("")
                    , m_inCodec      ("")
    {
    }
    string m_inFilePath;
    string m_inContainer;
    string m_inCodec;
} StreamParam;

class GstNvElements
{
public:
    GstElement*     m_pipeline = nullptr;
    GstElement*     m_source = nullptr;
    GstElement*     m_demuxer = nullptr;

    GstElement*     m_videoQueue = nullptr;
    GstElement*     m_videoParser = nullptr;
    GstElement*     m_capsFilter = nullptr;
    GstElement*     m_sink = nullptr;
    bool            m_isError = false;

    void pollBusMessages();
};

class GstkeyframeParser : public GstNvElements
{
public:
    typedef struct _VideoEncodeParams
    {
        string  m_codec;
        int     m_keyFrameInterval = 0;
        bool    m_isBframesPresent = false;
        bool    m_isLargeIdrPresent = false;
        int     m_FrameCount = 0;
        int     m_prevIdrIndex = 0;

        // Enhanced GOP analysis
        std::vector<int> m_gopIntervals;           // Store all GOP intervals
        int              m_bframeStartFrame = -1;  // Frame number when B-frames first appear
        int              m_minGopInterval = INT_MAX;
        int              m_maxGopInterval = 0;
        bool             m_hasVariableGop = false;

        void reset() {
            m_keyFrameInterval = 0;
            m_isBframesPresent = false;
            m_isLargeIdrPresent = false;
            m_FrameCount = 0;
            m_prevIdrIndex = 0;
            m_gopIntervals.clear();
            m_bframeStartFrame = -1;
            m_minGopInterval = INT_MAX;
            m_maxGopInterval = 0;
            m_hasVariableGop = false;
        }

        void analyzeGopPattern() {
            if (m_gopIntervals.size() >= 1) {
                m_minGopInterval = *std::min_element(m_gopIntervals.begin(), m_gopIntervals.end());
                m_maxGopInterval = *std::max_element(m_gopIntervals.begin(), m_gopIntervals.end());
                m_hasVariableGop = (m_maxGopInterval != m_minGopInterval);
            } else {
                if (m_keyFrameInterval > 0) {
                    m_minGopInterval = m_keyFrameInterval;
                    m_maxGopInterval = m_keyFrameInterval;
                    m_hasVariableGop = false;
                }
            }
        }
    } VideoEncodeParams;

    Json::Value parseKeyframeInterval (StreamParam params);
    GstFlowReturn processNewSampleFromSink(GstElement * appsink);

private:
    VideoEncodeParams m_videoEncodeParams;
};

class GstTranscode : public GstNvElements
{
 public:
    typedef struct _TranscodeParam : public StreamParam
    {
        _TranscodeParam() : m_outFilePath  ("")
                          , m_outContainer ("")
                          , m_outCodec ("")
                          , m_isUserFrameRate (false)
                          , m_outframeRate (0)
                          , m_framerateNum (0)
                          , m_framerateDenom (0)
                          , m_outBitrate   (0)
                          , m_outKeyFrameInterval (0)
                          , m_noBframes    (true)
                          , m_allIframes   (false)
                          , m_inCtrCaps ("")
                          , m_inVideoCaps ("")
                          , m_inAudioCaps ("")
        {
        }
        string m_outFilePath;
        string m_outContainer;
        string m_outCodec;
        bool m_isUserFrameRate;
        int m_outframeRate;
        uint m_framerateNum;
        uint m_framerateDenom;
        int m_fileFrameRate;
        uint m_fileFramerateNum;
        uint m_fileFramerateDenom;
        double m_outBitrate; // in bps
        int    m_outKeyFrameInterval;
        bool   m_noBframes;
        bool   m_allIframes;
        string m_inCtrCaps;
        string m_inVideoCaps;
        string m_inAudioCaps;
    } TranscodeParam;

    GstTranscode()
    {
    }

    ~GstTranscode()
    {
        LOG(info) << "Destroying GstTranscode" << endl;
    }

    bool transcode (TranscodeParam params);

    GstElement*     m_typefind = nullptr;
    GstElement*     m_muxer = nullptr;
    GstElement*     m_identity = nullptr;
    GstElement*     m_videoParser2 = nullptr;
    GstElement*     m_filter = nullptr;
    GstElement*     m_dec = nullptr;
    GstElement*     m_enc = nullptr;
    GstElement*     m_parse1 = nullptr;
    GstElement*     m_parse2 = nullptr;
    GstElement*     m_videoRate = nullptr;
    GstElement*     m_rateCapsFilter = nullptr;
    GstElement*     m_tsMux = nullptr;
    GstElement*     m_videoConverter = nullptr;
    GstElement*     m_videoConverter2 = nullptr;  // Second converter for pure hw pipeline (raw->NVMM)
    GStrv           m_fileLocations = nullptr;

    // Decodebin-based pipeline elements (simplified approach)
    GstElement*     m_decodebin = nullptr;
    GstElement*     m_audioconvert = nullptr;
    GstElement*     m_audioresample = nullptr;
    GstElement*     m_audioencoder = nullptr;

    // Manual pipeline elements for HW+HW path (avoids nvvideoconvert deadlock)
    GstElement*     m_inputParser = nullptr;   // Input h264/h265 parser

    // Audio elements for manual pipeline
    GstElement*     m_audioQueue = nullptr;
    GstElement*     m_audiodecoder = nullptr;

    // HW/SW usage flags
    bool            m_useHwDecoder = true;
    bool            m_useHwEncoder = true;
    bool            m_hasVideo = false;
    bool            m_hasAudio = false;
    bool            m_useManualPipeline = false;  // True for HW+HW path
};

class TranscodeTaskManager
{
    public:
        static TranscodeTaskManager* getInstace()
        {
            static TranscodeTaskManager _instance;
            return &_instance;
        }

        bool addTask(GstTranscode::TranscodeParam params);

    private:
        std::vector<async::task<bool>> m_transcodeTaskList;
};
double getAvgFPSForFile (string& file_path, const string& codec);
int getFrameCountForFile  (std::string& file_path, const string& codec);
GstClockTime getMediaFileDuration (const std::string& file_path);
GstClockTime fixMediaFileAndGetDuration (const std::string& file_path);
bool isRecordedFileExist(const string& sensorId, const int64_t& epochStartTime, const int64_t& epochEndTime);
Json::Value getRTSPStreamDetails (const string &url, std::string& codec,  std::vector<std::vector<uint8_t>> sps_pps_idr_frames);

// Container format detection and demuxer/muxer selection utilities
string detectContainerFormatFromFile(const string& filePath);
string detectContainerFormatFromExtension(const string& filePath);
GstElement* createDemuxerForContainer(const string& containerFormat);
GstElement* createDemuxerForFile(const string& filePath, const string& containerFormat = "");
GstElement* createMuxerForContainer(const string& containerFormat);
GstElement* createMuxerForFile(const string& filePath, const string& containerFormat = "");

// Mux elementary stream into container format
bool muxElementaryStream(const std::string& elementaryFilePath, const std::string& codec,
                        const std::string& containerFormat, std::string& outputFilePath, int32_t frameRate);

class GstDummyUdpPipeline
{
public:
    GstDummyUdpPipeline() {}
    ~GstDummyUdpPipeline();
    static GstDummyUdpPipeline* getInstance();
    static void deleteInstance();
    int startUdpPipeline(string id, int32_t audio_port, int32_t video_port, bool loop = false);
    int stopUdpPipeline(string id);
    void stopAllUdpPipelines();

private:
    static GstDummyUdpPipeline*                                          m_instance;
    std::mutex                                                      m_pipelineMapMutex;
    std::unordered_map<std::string, std::shared_ptr<gstElements> >  m_udpPipelines;

    std::shared_ptr<gstElements> getPipeline(string id);
    void insertPipeline(string id, std::shared_ptr<gstElements>);
    void erasePipeline(string id);
};
int createAndRunUdpPipeline(shared_ptr<gstElements> elements, int32_t audio_port, int32_t video_port, bool loop = false);
int destroyUdpPipeline(shared_ptr<gstElements> elements);

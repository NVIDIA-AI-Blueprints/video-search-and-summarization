/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>
#include <glib.h>
#include <gst/gst.h>
#include <condition_variable>

#include "api/video/i420_buffer.h"
#include "api/video/video_frame.h"
#include "modules/video_coding/include/video_error_codes.h"
#include "modules/video_coding/h264_sprop_parameter_sets.h"
#include "environment.h"
#include "logger.h"
#include "device_manager.h"
#include "event_loop.h"
#include "stream_monitor.h"

#include "libyuv/video_common.h"
#include "libyuv/convert.h"

#include "media/base/codec.h"
#include "media/base/video_common.h"
#include "media/engine/internal_decoder_factory.h"
#include "common_video/h264/h264_common.h"
#include "common_video/h264/sps_parser.h"
#include "api/video_codecs/video_decoder.h"
#include "stats.h"
#include "gstnvimageencode.h"
#include "gstnvvideoencodeout.h"
#include "gstnvdecodebin.h"
#include "Scheduler.h"
#include "gstnvdecoder.h"
#include "nvbufwrapper.h"
#include "media_consumer.h"
#include "../senders/videosenderpool.h"
#include "mm_utils.h"
#include "storage_management.h"
#include "videosinkinfo.h"
#include "media_consumer.h"
#include "../processors/compositors/nvcompositor.h"
#include "unified_storage/reader/unified_storage_reader.h"
#include "unified_storage/reader/unified_storage_reader_factory.h"
#include "unified_storage/manager/unified_storage_manager_utils.h"
#include "unified_storage_types.h"
#include "media_producer.h"
#include "s3stream_producer.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

struct DecoderData : public EventLoopData
{
    map<string, string> actions;
};

struct DecoderOutData : public EventLoopOutData
{
    map<string, string> data;
    void* data2;
    int result;
};

class GstNvVideoDecoder : public IMediaDataConsumer, public GstNvDecoder, public DecoderBase
{
    public:
        GstNvVideoDecoder (const std::string& consumer_name, const std::string& uri, const std::map<std::string, std::string, std::less<>> &opts);
        ~GstNvVideoDecoder ()
        {
            try {
                m_stop = true;
                if (m_frameThread.joinable()) {
                    m_frameThread.join();
                }
                {
                    std::lock_guard<std::mutex> lock(m_frameMutex);
                    if (m_cachedSample) {
                        gst_sample_unref(m_cachedSample);
                        m_cachedSample = nullptr;
                    }
                }
                {
                    std::lock_guard<std::mutex> lock(m_videoSinkLock);
                    m_videoSinkList.clear();
                }
                if (m_perfLogging)
                {
                    m_decStats.printTotalStats();
                    m_decStats.clearQueue();
                }
                destroy(true);
                LOG(info) << "Decoder instance is deleted "<< m_peerid << endl;
            } catch (const std::exception& e) {
                try { LOG(error) << "Exception in ~GstNvVideoDecoder: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
            } catch (...) {
                try { LOG(error) << "Unknown exception in ~GstNvVideoDecoder" << endl; } catch (...) { (void)std::current_exception(); }
            }
        }

        /* GstNvDecoder Interfaces */
        int create(bool blocking = false);
        void destroy(bool expect_result = false);
        bool pause();
        std::string getstate();
        std::string getstate(const std::string& peerid);
        bool isPlaying();
        bool getError() { return m_error; }
        void setResolution(int width, int height) override;
        void setDecoderStride(int stride_y, int stride_u, int stride_v) override;

        bool play();
#ifdef JETSON_PLATFORM
        void registerDecoderPlayingStatusListener(IStreamStatusEvent *listener);
        void deregisterDecoderPlayingStatusListener(IStreamStatusEvent *listener);
#endif
        int createSwDecodePipeline ();
        void setQuality(const std::string&, const std::string& quality);
        void setQuality(const std::string&, const std::string& quality, int width, int height);
        void removeConsumer(const std::string&);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>> getWebrtcBroacasterList() { return m_videoSinkList; }
        bool isCreated() { return (m_pipeline != nullptr); }
        void setError() { m_error = true; };
        void stop();
        string getUri() { return m_uri; }
        unsigned int getPort() { return m_port; }

        void getStats(const std::string& peerid, LatencyStats& stats);
        VmsErrorCode controlStream (const std::string& action, const std::string& speed);
        VmsErrorCode update (std::string action = "", std::string speed = "", bool eos = 0);
        bool setFileAndUpdatePipelineState (bool first_time = false);
        gint64 getNextFile ();
        GstFlowReturn processNewSampleFromSink(GstElement * appsink);
        GstFlowReturn processJpegImageFromSink(GstElement *appsink);
        void setSourceFrameSize(uint32_t w, uint32_t h);
        friend gboolean busWatch (GstBus *bus, GstMessage *message, gpointer data);
        friend void process_dec_message(std::shared_ptr<EventLoopData> data, void*);

        int create_internal();
        int create_recorded_internal();
        int create_hls_internal();
        int createJpegDecoderPipeline(const std::string& filepath);
        bool play_internal();
        bool pause_internal();
        void getstate_async();
        void getstate_internal();
        void sendStateChangeWebSocketMessage(const string& peerid, bool is_ready, const std::string& updated_state = "");
        void stop_internal();
        void destroy_internal();
        bool getPosition_internal (gint64* position);
        bool getDuration_internal (gint64* position);
        GStrv getFileLocations();
        bool getPosition(gint64& position);
        bool getDuration (gint64& duration);
        bool isSeeking() { return m_isSeeking; }
        gint64 getAbsPosition();
        void updateDecoderElement();
        uint64_t getLastTS();
        int64_t getFileStartTime();
        uint32_t getDurationStream();
        int64_t getFirstTs();
        string getSensorName();
        string getSensorId();

        std::shared_ptr<IMediaDataConsumer> getSelf() { return shared_from_this(); }
        EventLoop& getEventLoop() { return m_eventLoop; }
        FrameSize handleDRC(const string& peerid, int targetPixel, int targetFPS);
        std::string getImageBuffer();
        void setNeedSharedStream() { m_needSharedStream = true; }
        void setProducer(std::shared_ptr<IMediaDataProducer> producer) { m_producer = producer; }
        std::shared_ptr<IMediaDataProducer> getProducer() { return m_producer; }
        std::pair <std::string, std::string> getUrlPath();
        std::pair <std::string, std::string> getUrlPath_internal();
        void setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer);
        void setConsumerReady(const string& peerid, bool is_ready = true);
        std::shared_ptr<NvEncoderVideoConsumer> getConsumer(const string& media_type);
        void addFrameTs(int64_t ts);
        void setEOS();
        void setOptions(const std::map<std::string, std::string, std::less<>> &opts);
        int getVideoSinkListSize() { return m_videoSinkList.size(); }
        std::vector<VideoFileInfo> getActiveFileList() { return m_fileNameArray; }
#ifdef UNIT_TEST
        void setPeerid(const string& peer_id);
        uint32_t getCurrentFileNumber();
        std::vector<VideoFileInfo> getFileInfo();
#endif

        //IVideoDataConsumer virtual function
        virtual void onFrame (FrameParams& frame_params);
        // Handler for frames from ClipReaderProducer (parsed byte-stream)
        virtual void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
        bool isOverlay() { return m_isOverlay; }

        // B-frame presence API for low-latency mode optimization
        [[nodiscard]] bool hasBframes() const override { return m_hasBframes; }

    private:
        void initializeRecordParams();
        void setFileLocations();
        void unsetFileLocations();
        void pre_destroy();
        FrameSize qualityToFrameSize(const string& quality);
        FrameSize qualityToFrameSize(const string& quality, int width, int height);
        gint64 seekToEpoch (time_t epochs);
        void pushBufferToDecoder(const unsigned char *buffer, ssize_t size, int64_t id, uint64_t ts);
        void initial_seek();
        void resetPipeline();
        bool isDRCAllowed ();
        bool checkSinksStatus ();
        void sendCachedFrameLoop();
        void sendEosToSink();
        void flushDecoderPipeline();
        
        // Unified storage configuration and management
        bool initUnifiedStorageReader();
        
        bool isCloudStorageEnabled() const { return m_cloudStorageEnabled && m_unifiedStorageReader != nullptr; }

        // Cloud storage configuration getters
        std::string getCloudType() const { return m_storageConfig.getParameter(StorageConstants::CLOUD_TYPE_KEY, ""); }
        std::string getCloudEndpoint() const { return m_storageConfig.getParameter(StorageConstants::ENDPOINT_KEY, ""); }
        std::string getCloudBucket() const { return m_storageConfig.getParameter(StorageConstants::BUCKET_NAME_KEY, ""); }
    private:
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>> m_videoSinkList;
        std::string             m_uri;
        unsigned int            m_port {0};
        bool                    m_recordedPlayback;
        bool                    m_hlsPlayback;
        bool                    m_compositePlayback {false};
        bool                    m_compositeShowSensorName {false};
        GstElement*             m_pipeline = nullptr;
        GstElement*             m_decodeBin = nullptr;
        GstElement*             m_imageEncoder = nullptr;
        GstElement*             m_source = nullptr;
        GstElement*             m_sink = nullptr;
        GstElement*             m_tee = nullptr;
        GstElement*             m_ipcSink = nullptr;
        GstElement*             m_queueAppSink = nullptr;
        GstElement*             m_queueIPCSink = nullptr;
        GstElement*             m_videoConverter = nullptr;
        GstElement*             m_capsFilter = nullptr;
        guint                   m_bus_watch_id;
        int                     m_frameNum;
        double                  m_frameRate;
        uint64_t                m_maxDecLatency;
        uint32_t                m_govLength;
        uint32_t                m_currentFileIndex;
        gint64                  m_startTimeFirstFile;
        gint64                  m_endTimeLastFile;
        std::atomic<bool>       m_decoderCreated{false};
        std::vector<uint8_t>    m_outBuf;
        bool                    m_perfLogging {true};
        std::atomic<bool>       m_stop{false};
        std::string             m_seekValue;
        std::string             m_action;
        int                     m_playBackSpeed;
        gint64                  m_position_forward;
        gint64                  m_position_rewind;
        std::vector<VideoFileInfo> m_fileNameArray;
        std::string              m_startTime;
        std::string              m_endTime;
        gint64                   m_position;
        std::atomic<int>         m_sourceWidth;
        std::atomic<int>         m_sourceHeight;
        std::atomic<int>         m_decOutFrames{0};
        std::map<std::string, std::string, std::less<>>             m_opts;
        std::atomic<bool>       m_error{false};
        bool                    m_gpuExist;
        std::mutex              m_videoSinkLock;
        EventLoop               m_eventLoop;
        std::size_t             m_resolutionIndex = 0;
        std::unique_ptr<NvVideoEncodeOut> m_videoEncodeOut;
        std::unique_ptr<NvDecodeBin> m_nvDecodeBin;
        std::condition_variable m_imgBufferWait;
        std::mutex              m_imgBufferLock;
        std::string             m_imgBuffer;
        std::string             m_sensorName;
        std::mutex              m_debugData;
        uint64_t                m_firstFrameTS = 0;
        std::queue<std::pair< int64_t, uint64_t >>    m_frameTsQueue;
        std::mutex              m_frameTsQueueLock;
        std::condition_variable m_frameTsQueueCond;
        bool                    m_needSharedStream = false;
        bool                    m_hasBframes = false;
        std::string m_codec = "h264";
        std::atomic<int> m_playbackState {0};
        std::unique_ptr<Bosma::Scheduler> m_playbackWD;
        std::condition_variable m_playStateWait;
        std::mutex              m_playStateLock;
        std::atomic<bool>       m_isSeeking{false};
        std::atomic<bool>       m_forceResetEnc{false};
        std::pair <std::string, std::string> m_urlPath;
        std::string             m_prevFileName{""};
        NvBufferMode            m_nvBufferMode;
        bool                    m_isSwDecoder{false};
        std::atomic<uint64_t>   m_lastTS {0};
        std::string             m_sensorType{""};
        std::string             m_deviceId{""};
        std::string             m_objectId{""};
        std::string             m_isoStartTime{""};
        std::string             m_isoEndTime{""};
        int64_t                 m_epochStartTime{0};
        int64_t                 m_epochEndTime{0};
        int64_t                 m_fileStartTime{0};
        bool                    m_continuosPlayback = false;
        std::time_t             m_lastDRCTime {0};
#ifdef JETSON_PLATFORM
        std::set<IStreamStatusEvent*> m_listeners;
        std::mutex                    m_listenerMutex;
#endif
        bool                    m_isOverlay = false;
        GstPad*                 m_teeSrcAppsink = nullptr;
        GstPad*                 m_teeSrcIpcsink = nullptr;
    public:
        std::atomic<GstState>   m_state{GST_STATE_NULL};
        std::string              m_peerid;
        int m_appsrc_out_probe_count;
        int m_decoder_in_probe_count;
        int m_decoder_out_probe_count;
        bool                    m_isImageCapture = false;
        int                     m_resizeWidth = 0;
        int                     m_resizeHeight = 0;
        static bool             m_debug_logging_live;
        static bool             m_debug_logging_vod;
        ofstream                m_liveDebugFile;
        ofstream                m_vodDebugFile;
        CodecStats              m_decStats;
        int                     m_decoderWidth = WIDTH_1080p;
        int                     m_decoderHeight = HEIGHT_1080p;
        int                     m_decoderStrideY = 0;
        int                     m_decoderStrideU = 0;
        int                     m_decoderStrideV = 0;
        uint64_t                m_lastFrameTime = 0;
        bool                    m_godsEyeView = false;
        // Add these members to store the cached frame
        GstSample* m_cachedSample = nullptr;
        bool m_cachedSampleFirstTime = true;
        int64_t m_cachedSamplePTS = 0;
        std::thread m_frameThread;
        bool m_running;
        std::mutex m_frameMutex;
        bool m_cachedSampleCreated = false;
        // Cloud storage state
        std::atomic<bool> m_cloudStorageEnabled{false};
        
        // Unified storage reader for cloud storage access
        std::shared_ptr<nv_vms::UnifiedStorageReader> m_unifiedStorageReader = nullptr;
        StorageConfig m_storageConfig;
        std::string m_asyncDownloadSessionId; // Store async download session ID

        // Producer reference for data source
        std::shared_ptr<IMediaDataProducer> m_producer = nullptr;
        // CloudStreamProducer for S3/cloud streaming
        bool m_isCloudStream{false};
        // Counter for pipeline reset attempts on non-fatal errors
        std::atomic<int> m_resetAttempts{3};
};

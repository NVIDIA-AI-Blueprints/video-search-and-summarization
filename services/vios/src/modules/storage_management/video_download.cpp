/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/*
 * Two-pipeline downloader implementation
 * Reader: splitmuxsrc -> queue -> h26xparse -> capsfilter -> appsink (seek only here)
 * Writer: appsrc -> transcodebin(decoder/encoder/capssetter/valve) -> [h26xparse if mp4] -> mux -> filesink
 */

#include "storage_management_utils.h"
#include "storage_management.h"
#include "logger.h"
#include "nvhwdetection.h"
#include "database.h"
#include "clip_reader_producer.h"
#include "s3stream_producer.h"
#include "transcode_writer_consumer.h"
#include "remux_writer_consumer.h"
#include "vms_media_interface.h"
#include "vms_media_types.h"
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include <thread>
#include <sstream>
#include <functional>
#include <cstring>
#include <cstdio>

using namespace std;
using namespace nv_vms;

/* Macro defined in splitmuxsrc plugin */
#define FIXED_TS_OFFSET (1000*GST_SECOND)

namespace {

// Helpers to modularize common logic
static inline void sanitize_time_string(std::string& s)
{
    for (char &c : s)
    {
        if (c == ':' || c == '.' || c == 'T' || c == 'Z') c = '_';
    }
}

static inline void ensure_default_output_filename(std::string& output_file,
                                                  const std::string& device_name,
                                                  const std::string& user_start_time,
                                                  const std::string& user_end_time,
                                                  const std::string& container)
{
    if (!output_file.empty())
    {
        return;
    }
    std::string sanitizedStartTime = user_start_time;
    std::string sanitizedEndTime   = user_end_time;
    sanitize_time_string(sanitizedStartTime);
    sanitize_time_string(sanitizedEndTime);
    const char* ext = ".ts";
    if (iequals(container, "mp4")) ext = ".mp4";
    else if (iequals(container, "mkv")) ext = ".mkv";

    // Add thread ID to ensure unique filenames for concurrent requests
    std::stringstream tid;
    tid << std::this_thread::get_id();

    output_file = device_name + std::string("_") + sanitizedStartTime + std::string("_") +
                  sanitizedEndTime + std::string("_") + tid.str() + ext;
}


// Helper functions and structures for overlay, parser, and audio linking
// removed - now handled in TranscodeWriterConsumer and RemuxWriterConsumer

// Helper to convert epoch ms to ISO timestamp string (for filename)
static inline std::string epochMsToTimestampStr(int64_t epoch_ms)
{
    time_t secs = epoch_ms / 1000;
    struct tm tm_info;
    gmtime_r(&secs, &tm_info);
    char buffer[32] = {0};
    size_t len = strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H_%M_%S", &tm_info);
    // Add milliseconds (strftime returns number of characters written, 0 on error)
    if (len > 0 && len < sizeof(buffer))
    {
        int ms = epoch_ms % 1000;
        snprintf(buffer + len, sizeof(buffer) - len, "_%03dZ", ms);
    }
    return std::string(buffer);
}

// Rename output file with actual timestamps (for remux mode where start is from keyframe)
static inline void renameFileWithActualTimestamps(
    std::string& output_file,
    int64_t actual_start_pts_ms,
    int64_t actual_end_pts_ms,
    int64_t file_base_epoch_ms,
    const std::string& device_name,
    const std::string& user_end_time,
    const std::string& container)
{
    if (actual_start_pts_ms < 0 || output_file.empty())
    {
        return; // No actual timestamp available
    }

    // Calculate actual start epoch time
    int64_t actual_start_epoch_ms = file_base_epoch_ms + actual_start_pts_ms;
    std::string actual_start_str = epochMsToTimestampStr(actual_start_epoch_ms);

    std::string endTimeStr;
    if (actual_end_pts_ms >= 0)
    {
        int64_t actualEndEpochMs = file_base_epoch_ms + actual_end_pts_ms;
        endTimeStr = epochMsToTimestampStr(actualEndEpochMs);
    }
    else
    {
        endTimeStr = user_end_time;
        sanitize_time_string(endTimeStr);
    }

    const char* ext = ".ts";
    if (iequals(container, "mp4")) ext = ".mp4";
    else if (iequals(container, "mkv")) ext = ".mkv";

    std::stringstream tid;
    tid << std::this_thread::get_id();

    std::string new_filename = device_name + "_" + actual_start_str + "_" +
                               endTimeStr + "_" + tid.str() + ext;

    if (new_filename == output_file)
    {
        return; // No change needed
    }

    // Rename the file
    if (std::rename(output_file.c_str(), new_filename.c_str()) == 0)
    {
        LOG(info) << "Renamed output file from " << output_file << " to " << new_filename << endl;
        output_file = new_filename;
    }
    else
    {
        LOG(warning) << "Failed to rename output file: " << strerror(errno) << endl;
    }
}

} // anonymous namespace

nv_vms::VmsErrorCode downloadVideoFile(
    const VideoFileProcessingParams& params,
    string& output_file,
    string& video_codec,
    const string& container,
    const string& transcode,
    bool do_seek,
    const string& disable_audio,
    const string& enable_overlay,
    OverlayBBoxParams* ol_params,
    const string& user_start_time,
    const string& user_end_time,
    const string& device_name,
    const string& sensor_id,
    const string& sensor_type,
    const string& frameRate,
    nv_vms::IMediaInterface* media_interface,
    int64_t* actual_start_epoch_ms)
{
    const std::string stream_id = sensor_id.empty() ? "unknown" : sensor_id;
    const std::string log_id = stream_id + "_" + user_start_time + "_" + user_end_time;
    const std::string log_prefix = "[" + log_id + "] ";

    LOG(info) << log_prefix << "downloadVideoFile - Start" << endl;
    LOG(warning) << log_prefix << "Params: container: " << container << ", transcode: " << transcode
            << ", disable_audio: " << disable_audio << ", enable_overlay: " << enable_overlay
            << ", is_cloud_stream: " << params.is_cloud_stream << ", sensor_type: " << sensor_type << endl;

    if (media_interface != nullptr)
    {
        LOG(info) << log_prefix << "Using media interface to download clip" << endl;
        // Prepare ClipRequest from parameters
        nv_vms::ClipRequest clipReq;
        clipReq.camera_id = sensor_id; // Use sensor_id as camera_id
        clipReq.start_ms = params.epoch_user_start_time;
        clipReq.end_ms = params.epoch_user_end_time;
        clipReq.frame_rate = stringToInt(frameRate, 1);

        // Map codec
        if (video_codec == "h265" || video_codec == "H265")
        {
            clipReq.codec = nv_vms::VideoCodec::H265;
        }
        else
        {
            clipReq.codec = nv_vms::VideoCodec::H264;
        }

        // Map container
        if (container == "mkv" || container == "MKV")
        {
            clipReq.container = nv_vms::ContainerFormat::Mkv;
        }
        else
        {
            clipReq.container = nv_vms::ContainerFormat::Mp4;
        }

        nv_vms::ClipResponse clipResp;
        clipResp.file_path = output_file;
        int ret = media_interface->fetchClip(clipReq, clipResp);

        if (ret == nv_vms::MEDIA_OK)
        {
            LOG(info) << log_prefix << "fetchClip succeeded, output file: " << clipResp.file_path << endl;
            output_file = clipResp.file_path;
            return nv_vms::VmsErrorCode::NoError;
        }
        else
        {
            LOG(error) << log_prefix << "fetchClip failed with error code: " << ret << endl;
            LOG(info) << log_prefix << "Falling back to gstreamer pipeline" << endl;
            // Continue with gstreamer pipeline below
        }
    }

    bool isError = false;  // Track pipeline errors
    std::vector<nv_vms::VideoFileInfo> fileNameArray = params.fileNameArray;
    if (fileNameArray.empty())
    {
        LOG(error) << log_prefix << "No files to process" << endl;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    // Discard a trailing file whose start_time is past the user-requested end.
    // vst_common.cpp:getActiveLocalRecording (invoked from the DB getFileList
    // wrappers) appends the in-progress recording with up to a 1-second
    // tolerance against user_end.
    if (params.epoch_user_end_time > 0
        && params.epoch_user_end_time != std::numeric_limits<int64_t>::max()
        && fileNameArray.size() > 1
        && static_cast<int64_t>(fileNameArray.back().m_startTime) > params.epoch_user_end_time)
    {
        const auto& dropped = fileNameArray.back();
        LOG(warning) << log_prefix
                     << "Discarding trailing file whose start_time is past user_end_epoch_ms="
                     << params.epoch_user_end_time << ": " << dropped.m_filePath
                     << " (start=" << dropped.m_startTime
                     << ", duration=" << dropped.m_duration
                     << ", file_size=" << dropped.m_fileSize << ")" << endl;
        fileNameArray.pop_back();
    }

    // Audio is disabled by default; only enable if explicitly set to "false"
    bool enableAudio = iequals(disable_audio, "false");

    LOG(info) << log_prefix << "Total files: " << fileNameArray.size() << endl;
    LOG(info) << log_prefix << "File base timestamp: " << fileNameArray[0].m_startTime << endl;

    // Check if we should use cloud streaming (direct S3 access) vs downloading first
    bool isCloudStream = params.is_cloud_stream;

    if (video_codec.empty())
    {
        video_codec = fileNameArray[0].m_codec;
    }
    bool is_sw_encoder = !NvHwDetection::getInstance()->m_useNvV4l2Enc;
    LOG(info) << log_prefix << "video_codec: " << video_codec << ", is_sw_encoder: " << is_sw_encoder << ", enableAudio:" << enableAudio << endl;

    // Create CloudStreamProducer once if in cloud mode (used for both direct remux and transcode paths)
    std::shared_ptr<CloudStreamProducer> cloudProducer;
    if (isCloudStream)
    {
        LOG(info) << log_prefix << "Cloud stream mode: creating CloudStreamProducer" << endl;

        cloudProducer = std::make_shared<CloudStreamProducer>();
        bool syncMode = false;  // Fast mode for download (no frame rate limiting)

        // Configure using setConfig() which handles presigned URL generation internally
        cloudProducer->setConfig(fileNameArray, user_start_time, user_end_time,
                                video_codec, "", syncMode, enableAudio);

        // Enable appsink mode for clip download use-case (GstSample/GstBuffer forwarding)
        cloudProducer->setAppsinkMode(true);
        LOG(info) << log_prefix << "CloudStreamProducer configured: " << fileNameArray.size()
                  << " segments, codec=" << video_codec << ", container=" << container << endl;
    }

    // Determine if this is direct remux (no transcoding) or transcode path
    bool isDirectRemux = iequals(transcode, "none");

    // Choose producer based on cloud stream mode
    std::shared_ptr<IMediaDataProducer> readerProducer;
    if (isCloudStream)
    {
        // Reuse the already-created CloudStreamProducer
        readerProducer = cloudProducer;
    }
    else
    {
        // Local files - use ClipReaderProducer with splitmuxsrc mode
        LOG(info) << log_prefix << "Local file mode: using ClipReaderProducer" << endl;

        ClipReaderConfig readerCfg;
        readerCfg.stream_id = fileNameArray[0].m_filePath;
        readerCfg.log_id = log_id;
        readerCfg.video_codec = video_codec;
        readerCfg.enable_audio = enableAudio;
        readerCfg.seek_start_ms = params.seek_start_pos;

        // params is const, so carry the (possibly clamped) end position in a local
        // and use it for both the producer config and the is_growing_file decision.
        int64_t effectiveSeekEndPos = params.seek_end_pos;
        readerCfg.seek_end_ms = effectiveSeekEndPos;
        readerCfg.file_start_epoch_ms = fileNameArray[0].m_startTime;
        for (const auto& fi : fileNameArray)
        {
            readerCfg.file_paths.push_back(fi.m_filePath);
        }

        // Check if last file is a live/growing file
        bool is_file_sensor = (sensor_type == SENSOR_TYPE_FILE);

        // Cap end_time to the file duration if the user end time is greater than the file duration
        if (fileNameArray.size() == 1 && is_file_sensor)
        {
            int64_t fileDurationMs = fileNameArray[0].m_duration;
            if (params.seek_end_pos >= fileDurationMs)
            {
                LOG(warning) << log_prefix << "User end time is greater, capping end_time to the file duration " << fileDurationMs << "ms" << endl;
                readerCfg.seek_end_ms = std::numeric_limits<int64_t>::max();
            }
        }

        // Clamp user-requested end to the last available file boundary for non-file-sensor
        // (rtsp recording) downloads.
        // For a still-growing tail file (m_fileSize == 0), allow one inter-frame
        // tolerance (1000/fps) so the next-arriving frame at the live edge is not
        // accidentally clipped out.
        if (!is_file_sensor
            && params.seek_end_pos != std::numeric_limits<int64_t>::max()
            && params.epoch_user_end_time > 0
            && !fileNameArray.empty())
        {
            const auto& lastFile = fileNameArray.back();
            const int64_t lastFileEndEpochMs = static_cast<int64_t>(lastFile.m_startTime)
                                             + static_cast<int64_t>(lastFile.m_duration);
            const bool    lastFileIsGrowing  = (lastFile.m_fileSize == 0);
            uint64_t      lastFileFps        = lastFile.m_fileFPS;
            if (lastFileFps == 0)
            {
                lastFileFps = static_cast<uint64_t>(DEFAULT_VIDEO_FRAME_RATE);
            }
            const auto interFrameMs     = static_cast<int64_t>(1000 / lastFileFps);
            const int64_t clampLimitEpochMs = lastFileIsGrowing
                                              ? (lastFileEndEpochMs + interFrameMs)
                                              : lastFileEndEpochMs;

            if (params.epoch_user_end_time > clampLimitEpochMs)
            {
                // seek_end is a relative offset from fileNameArray[0].m_startTime, so
                // clamping to clampLimitEpochMs in the producer's frame is just the
                // absolute boundary minus the file base epoch.
                const auto fileBaseEpochMs = static_cast<int64_t>(fileNameArray[0].m_startTime);
                if (clampLimitEpochMs <= fileBaseEpochMs)
                {
                    // Defensive: would translate to seek_end_ms = 0, which the producer
                    // interprets as "EOS on the first frame past FIXED_TS_OFFSET" and
                    // produces a near-empty output. This requires a corrupted DB row
                    // (m_duration == 0 on a finalised file) or significant clock skew
                    // and should not happen in normal operation. Skip the clamp and let
                    // the producer's bus-EOS / premature-EOS path handle termination.
                    LOG(error) << log_prefix
                               << "Clamp limit not after file base; skipping clamp to avoid empty output. "
                               << "file_base_epoch_ms=" << fileBaseEpochMs
                               << ", last_file_start_ms=" << lastFile.m_startTime
                               << ", last_file_duration_ms=" << lastFile.m_duration
                               << ", last_file_is_growing=" << lastFileIsGrowing
                               << ", inter_frame_ms=" << interFrameMs
                               << ", clamp_limit_epoch_ms=" << clampLimitEpochMs
                               << ", user_end_epoch_ms=" << params.epoch_user_end_time << endl;
                }
                else
                {
                    const int64_t clampedSeekEnd = clampLimitEpochMs - fileBaseEpochMs;
                    LOG(warning) << log_prefix
                                 << "Clamping seek_end_pos to last-file boundary: user_end_epoch_ms="
                                 << params.epoch_user_end_time
                                 << ", file_base_epoch_ms=" << fileBaseEpochMs
                                 << ", last_file_end_epoch_ms=" << lastFileEndEpochMs
                                 << ", last_file_is_growing=" << lastFileIsGrowing
                                 << ", inter_frame_ms=" << interFrameMs
                                 << ", clamp_limit_epoch_ms=" << clampLimitEpochMs
                                 << ", seek_end_pos: " << effectiveSeekEndPos << "ms -> "
                                 << clampedSeekEnd << "ms" << endl;
                    effectiveSeekEndPos   = clampedSeekEnd;
                    readerCfg.seek_end_ms = effectiveSeekEndPos;
                }
            }
        }

        // Check if last file is completed/finalized, in that case, we don't need to use giosrc
        bool is_growing_file = true;
        if (!is_file_sensor)
        {
            bool is_completed_file = fileNameArray.back().m_fileSize > 0;
            int64_t last_file_durationMs = fileNameArray.back().m_duration;
            uint64_t last_file_fps = fileNameArray.back().m_fileFPS;
            if (last_file_fps == 0)
            {
                last_file_fps = static_cast<uint64_t>(DEFAULT_VIDEO_FRAME_RATE);
            }
            int64_t max_gap_between_files_ms = static_cast<int64_t>(1000 / last_file_fps) - 1;
            if (is_completed_file && effectiveSeekEndPos <= (last_file_durationMs + max_gap_between_files_ms))
            {
                LOG(warning) << log_prefix << "Last file is completed/finalized, not using giosrc, last_file_durationMs: "
                    << last_file_durationMs << "ms, seek_end_pos: " << effectiveSeekEndPos << "ms" << endl;
                is_growing_file = false;
            }
        }

        auto now = std::chrono::system_clock::now();
        int64_t nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()).count();
        int64_t diffMs = nowMs - params.epoch_user_end_time;

        // Within 2 seconds of current time means we're reading near the live edge
        readerCfg.is_growing_file = is_growing_file && !is_file_sensor && (diffMs <= 2000);
        LOG(info) << log_prefix << "ClipReaderProducer: is_growing_file: " << readerCfg.is_growing_file << ", diffMs: " << diffMs << "ms" << endl;

        readerCfg.bypass_giosrc_for_growing_file = params.disable_giosrc_for_growing_file;
        readerCfg.has_bframes = params.has_bframes;
        
        // Configure B-frame parameters for improved seek accuracy
        if (readerCfg.has_bframes)
        {
            // Estimate framerate from database or use conservative default
            double fps = 30.0;  // Conservative default
            
            // Try to get actual framerate from sensor stream info
            auto dbHelper = GET_DB_INSTANCE();
            if (dbHelper && !sensor_id.empty())
            {
                string fps_str = dbHelper->readStreamProperty(sensor_id, SensorStreamsDBColumns::frameRate);
                if (!fps_str.empty())
                {
                    try {
                        double parsed_fps = std::stod(fps_str);
                        if (parsed_fps > 0.1 && parsed_fps <= 120.0) {  // Sanity check
                            fps = parsed_fps;
                        }
                    } catch (const std::exception& e) {
                        LOG(warning) << "Failed to parse framerate '" << fps_str << "': " << e.what() << endl;
                    }
                }
            }
            
            readerCfg.estimated_framerate = fps;
            readerCfg.reorder_depth = MAX_REF_FRAMES;

            LOG(info) << "B-frame seek config: fps=" << fps 
                     << ", reorder_depth=" << readerCfg.reorder_depth << endl;
        }
        
        LOG(info) << "ClipReaderProducer: bypass_giosrc_for_growing_file: " << readerCfg.bypass_giosrc_for_growing_file << endl;

        readerProducer = std::make_shared<ClipReaderProducer>(readerCfg);
    }

    // Ensure output filename is set
    ensure_default_output_filename(output_file, device_name, user_start_time, user_end_time, container);

    // ─────────────────────────────────────────────────────────────
    // Create writer: RemuxWriterConsumer (passthrough) or TranscodeWriterConsumer
    // Both inherit from IMediaDataConsumer which provides common writer interface
    // ─────────────────────────────────────────────────────────────
    std::shared_ptr<IMediaDataConsumer> videoConsumer;

    if (isDirectRemux)
    {
        LOG(info) << log_prefix << "Using RemuxWriterConsumer (direct remux, no transcoding)" << endl;

        RemuxWriterConfig rcfg;
        rcfg.video_codec = video_codec;
        rcfg.container = container;
        rcfg.output_file = output_file;
        rcfg.enable_audio = enableAudio;
        rcfg.seek_start_ms = params.seek_start_pos;
        rcfg.end_time_ms = params.seek_end_pos;
        rcfg.log_id = log_id;

        videoConsumer = std::make_shared<RemuxWriterConsumer>(rcfg);
    }
    else
    {
        LOG(info) << log_prefix << "Using TranscodeWriterConsumer (with transcoding)" << endl;

        bool enableOverlay = iequals(enable_overlay, "true");

        TranscodeWriterConfig wcfg;
        wcfg.video_codec = video_codec;
        wcfg.container = container;
        wcfg.log_id = log_id;
        wcfg.file_start_time = fileNameArray[0].m_startTime;
        wcfg.enable_overlay = enableOverlay;
        wcfg.enable_audio = enableAudio;
        wcfg.overlay_bin = nullptr;
        wcfg.sensor_name = device_name;
        wcfg.stream_id = sensor_id;
        wcfg.user_start_time_iso = user_start_time;
        wcfg.user_end_time_iso = user_end_time;
        wcfg.overlay_params = ol_params;
        wcfg.is_software_encoder = is_sw_encoder;
        wcfg.seek_start_ms = params.seek_start_pos;
        wcfg.end_time_ms = params.seek_end_pos;
        wcfg.output_file = output_file;

        videoConsumer = std::make_shared<TranscodeWriterConsumer>(wcfg);
    }

    // ─────────────────────────────────────────────────────────────
    // Start writer and register consumers
    // ─────────────────────────────────────────────────────────────
    if (!videoConsumer->start())
    {
        LOG(error) << log_prefix << "Writer consumer: start failed" << endl;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    string identifier = "";
    readerProducer->registerConsumer(videoConsumer, identifier, "video");

    std::shared_ptr<IMediaDataConsumer> audioConsumer;
    if (enableAudio)
    {
        audioConsumer = videoConsumer->getAudioConsumer();
        if (audioConsumer)
        {
            readerProducer->registerConsumer(audioConsumer, identifier, "audio");
        }
    }

    LOG(info) << log_prefix << "Writer started via " << (isDirectRemux ? "RemuxWriterConsumer" : "TranscodeWriterConsumer") << endl;

    // ─────────────────────────────────────────────────────────────
    // Setup callbacks and start reader
    // ─────────────────────────────────────────────────────────────
    std::atomic<bool> finished{false};
    std::atomic<bool> readerError{false};

    readerProducer->onFinished([&]() {
        finished = true;
        LOG(info) << log_prefix << "Reader producer: EOS reached" << endl;
        videoConsumer->sendEOS();
    });

    readerProducer->onError([&](const std::string& errorMsg, int errorCode) {
        finished = true;
        readerError = true;
        LOG(error) << log_prefix << "Reader producer error: " << errorMsg << " (code: " << errorCode << ")" << endl;
        videoConsumer->sendEOS();
    });

    if (!readerProducer->start())
    {
        LOG(error) << log_prefix << "Reader producer: start failed" << endl;
        videoConsumer->stop();
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    // ─────────────────────────────────────────────────────────────
    // Wait for completion
    // ─────────────────────────────────────────────────────────────
    int64_t time_out_in_secs = GET_CONFIG().download_files_timeout_secs;
    bool waitResult = videoConsumer->waitForCompletion(time_out_in_secs);
    if (!waitResult)
    {
        LOG(warning) << log_prefix << "Writer bus timeout " << time_out_in_secs << "s - pipeline may be stuck" << endl;
        // Quiesce producer before forcing EOS to avoid pushing into a draining pipeline.
        if (readerProducer)
        {
            readerProducer->stop();
        }
        videoConsumer->sendEOS();
        // Best-effort short drain to let muxers finalize cleanly.
        constexpr int64_t kDrainTimeoutSecs = 2;
        if (!videoConsumer->waitForCompletion(kDrainTimeoutSecs))
        {
            LOG(warning) << log_prefix << "Writer bus still not responsive after drain timeout" << endl;
        }
    }

    // Check for errors from both reader and writer
    if (readerError.load())
    {
        LOG(error) << log_prefix << "Reader producer reported error" << endl;
        isError = true;
    }
    if (videoConsumer->hasError())
    {
        LOG(error) << log_prefix << "Writer consumer reported error" << endl;
        isError = true;
    }

    // ─────────────────────────────────────────────────────────────
    // For remux mode: rename file with actual start timestamp (from keyframe)
    // and return actual start epoch to caller for API response
    // ─────────────────────────────────────────────────────────────
    if (isDirectRemux && !isError)
    {
        int64_t actual_start_pts_offset_ms = videoConsumer->getActualStartPtsMs();
        int64_t actual_end_pts_offset_ms   = videoConsumer->getActualEndPtsMs();
        if (actual_start_pts_offset_ms >= 0)
        {
            // Calculate actual epoch time = file base epoch + offset from file start
            int64_t actual_epoch = fileNameArray[0].m_startTime + actual_start_pts_offset_ms;
            if (actual_start_epoch_ms != nullptr)
            {
                *actual_start_epoch_ms = actual_epoch;
                LOG(info) << log_prefix << "Actual start epoch (from keyframe): " << actual_epoch << " ms" << endl;
            }
            renameFileWithActualTimestamps(
                output_file,
                actual_start_pts_offset_ms,
                actual_end_pts_offset_ms,
                fileNameArray[0].m_startTime,  // file base epoch ms
                device_name,
                user_end_time,
                container);
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Cleanup - stop and destroy pipeline objects in a background thread.
    // The video clip file is fully written by this point, so nothing
    // below affects the caller's output_file or return code.
    // ─────────────────────────────────────────────────────────────
    readerProducer->onFinished(nullptr);
    readerProducer->onError(nullptr);

    std::thread([readerProducer = std::move(readerProducer),
                 videoConsumer = std::move(videoConsumer),
                 audioConsumer = std::move(audioConsumer),
                 cloudProducer = std::move(cloudProducer),
                 log_prefix]() mutable
    {
        try
        {
            readerProducer->stop();
            videoConsumer->stop();
            // Explicitly destroy all pipeline objects while this thread is
            // still alive.  This thread is detached, so if the process exits
            // before the lambda returns, captured shared_ptrs are never
            // destroyed and their destructors (teardown/unref) never run.
            audioConsumer.reset();
            videoConsumer.reset();
            cloudProducer.reset();
            readerProducer.reset();
        }
        catch (const std::exception& e)
        {
            LOG(error) << log_prefix << "Async cleanup exception: " << e.what() << endl;
        }
        catch (...)
        {
            LOG(error) << log_prefix << "Async cleanup unknown exception" << endl;
        }
    }).detach();

    // Free the locations array : TODO
    /*if (locations)
    {
        g_strfreev(locations);
    }*/

    if (isError)
    {
        LOG(error) << log_prefix << "Error in Pipeline" << endl;
        output_file = "";
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    LOG(info) << log_prefix << "downloadVideoFile - Done" << endl;
    return nv_vms::VmsErrorCode::NoError;
}

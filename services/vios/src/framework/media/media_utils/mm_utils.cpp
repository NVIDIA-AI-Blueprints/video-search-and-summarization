/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <gst/gst.h>
#include <gst/pbutils/gstdiscoverer.h>
#include <gst/pbutils/descriptions.h>

#include "mm_utils.h"
#include "utils.h"
#include "logger.h"
#include "cmdline_parser.h"
#include "media_consumer.h"
#include "nvhwdetection.h"
#include "vstmodule.h"
#include "device_manager.h"
#include "database.h"
#include "libav_wrapper.h"
#include "elasticSearch.h"

#include <chrono>
#include <cstdlib>
#include <random>

extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/avutil.h>
#include <libavutil/dict.h>
#include <libavutil/samplefmt.h>
}

constexpr int MAX_CHUNK_TO_READ = 8;
constexpr int MIN_SIZE_VIDEO_FRAME = 4;

// Default framerate values for media discovery fallback
static constexpr int DEFAULT_FRAMERATE_NUM = 30;
static constexpr int DEFAULT_FRAMERATE_DENOM = 1;

const uint8_t kNaluTypeMask = 0x1F;
static uint8_t H26X_marker_1[] = { 0, 0, 0, 1};
static uint8_t H26X_marker_2[] = { 0, 0, 1};

// Helper function to check if string is a remote URL (http/https)
static inline bool isRemoteUrl(const std::string& path)
{
    return (path.compare(0, 7, "http://") == 0 || path.compare(0, 8, "https://") == 0);
}

// Helper function to check if string is already a URI (http/https/s3/file)
static inline bool isUri(const std::string& path)
{
    return (path.compare(0, 7, "http://") == 0 || path.compare(0, 8, "https://") == 0 ||
            path.compare(0, 5, "s3://") == 0 || path.compare(0, 7, "file://") == 0);
}

struct _GstDiscovererPrivate
{
    gboolean async;

    /* allowed time to discover each uri in nanoseconds */
    GstClockTime timeout;

    /* list of pending URI to process (current excluded) */
    GList *pending_uris;

    GMutex lock;
    /* TRUE if cleaning up discoverer */
    gboolean cleanup;

    /* TRUE if processing a URI */
    gboolean processing;

    /* TRUE if discoverer has been started */
    gboolean running;

    /* current items */
    GstDiscovererInfo *current_info;
    GError *current_error;
    GstStructure *current_topology;

    GstTagList *all_tags;
    GstTagList *global_tags;

    /* List of private streams */
    GList *streams;

    /* List of these sinks and their handler IDs (to remove the probe) */
    guint pending_subtitle_pads;

    /* Whether we received no_more_pads */
    gboolean no_more_pads;

    GstState target_state;
    GstState current_state;

    /* Global elements */
    GstBin *pipeline;
    GstElement *uridecodebin;
    GstBus *bus;

    GType decodebin_type;

    /* Custom main context variables */
    GMainContext *ctx;
    GSource *bus_source;
    GSource *timeout_source;

    /* reusable queries */
    GstQuery *seeking_query;

    /* Handler ids for various callbacks */
    gulong pad_added_id;
    gulong pad_remove_id;
    gulong no_more_pads_id;
    gulong source_chg_id;
    gulong element_added_id;
    gulong bus_cb_id;

    gboolean use_cache;
};

vector<uint8_t> getH26xMarker(const unsigned char *buffer, ssize_t size)
{
    vector<uint8_t> h26xMarker;
    // NALU start-code can be either 00 00 00 01 or 00 00 01.
    if(size > (ssize_t)sizeof(H26X_marker_1) &&
        buffer[0] == 0x00 && buffer[1] == 0x00 && buffer[2] == 0x00 && buffer[3] == 0x01)
    {
        h26xMarker.assign(H26X_marker_1, H26X_marker_1 + 4);
    }
    else if (size > (ssize_t)sizeof(H26X_marker_2) &&
        buffer[0] == 0x00 && buffer[1] == 0x00 && buffer[2] == 0x01)
    {
        h26xMarker.assign(H26X_marker_2, H26X_marker_2 + 3);
    }
    return h26xMarker;
}

vector<uint8_t> getDefaultH26xMarker()
{
    vector<uint8_t> h26xMarker;
    h26xMarker.assign(H26X_marker_1, H26X_marker_1 + 4);
    return h26xMarker;
}

int getH26xMarkerSize(const unsigned char *buffer, ssize_t size)
{
    return getH26xMarker(buffer, size).size();
}

NaluType parseH264NaluType(const unsigned char *buffer, ssize_t size)
{
    unsigned int nal_index = 0;
    nal_index = getH26xMarkerSize(buffer, size);
    if (nal_index == sizeof(H26X_marker_1) || nal_index == sizeof(H26X_marker_2))
    {
        uint8_t data = buffer[nal_index];
        return static_cast<NaluType>(data & kNaluTypeMask);
    }
    return kNalUnknown;
}

H265NaluType parseH265NaluType(const unsigned char *buffer, ssize_t size)
{
    /* Mirror the parseH264NaluType contract: validate the start code via
     * getH26xMarkerSize() so that both 4-byte (00 00 00 01) and 3-byte
     * (00 00 01) markers are handled correctly, and return NAL_UNKNOWN if
     * the buffer doesn't begin with a valid start code. The previous
     * implementation hard-coded a 4-byte skip and skipped all start-code
     * validation, which silently misclassified any 3-byte-prefixed NAL and
     * returned a garbage type for non-byte-stream input. */
    unsigned int nal_index = getH26xMarkerSize(buffer, size);
    if (nal_index == sizeof(H26X_marker_1) || nal_index == sizeof(H26X_marker_2))
    {
        /* Bounds: getH26xMarker required size > nal_index before declaring
         * the start code valid, so buffer[nal_index] is guaranteed in range. */
        uint8_t data = buffer[nal_index];
        return static_cast<H265NaluType>((data >> 1) & 0x3f);
    }
    return H265NaluType::NAL_UNKNOWN;
}

void checkAndInsertEmulationPrevByte(std::vector<uint8_t>& vect)
{
    for (size_t i = 0; i < vect.size() - 2; ++i)
    {
        if (vect[i] == 0 && vect[i+1] == 0 && vect[i+2] == 0)
        {
            vect.erase(vect.begin() + i, vect.begin() + i + 3);
            vect.insert(vect.begin() + i, {0, 0, 3, 0});
            i += 3;
        }
        else if (vect[i] == 0 && vect[i+1] == 0 && vect[i+2] == 1)
        {
            vect.erase(vect.begin() + i, vect.begin() + i + 3);
            vect.insert(vect.begin() + i, {0, 0, 3, 1});
            i += 3;
        }
        else if (vect[i] == 0 && vect[i+1] == 0 && vect[i+2] == 2)
        {
            vect.erase(vect.begin() + i, vect.begin() + i + 3);
            vect.insert(vect.begin() + i, {0, 0, 3, 2});
            i += 3;
        }
        else if (vect[i] == 0 && vect[i+1] == 0 && vect[i+2] == 3)
        {
            vect.erase(vect.begin() + i, vect.begin() + i + 3);
            vect.insert(vect.begin() + i, {0, 0, 3, 3});
            i += 3;
        }
    }
}

void checkAndRemoveEmulationPrevByte(std::vector<uint8_t>& vect)
{
    const std::vector<std::vector<uint8_t>> patterns = {
            {0x00, 0x00, 0x03, 0x00},
            {0x00, 0x00, 0x03, 0x01},
            {0x00, 0x00, 0x03, 0x02},
            {0x00, 0x00, 0x03, 0x03}
    };
    const std::vector<std::vector<uint8_t>> replacements = {
            {0x00, 0x00, 0x00},
            {0x00, 0x00, 0x01},
            {0x00, 0x00, 0x02},
            {0x00, 0x00, 0x03}
    };

    for (size_t p = 0; p < patterns.size(); ++p)
    {
        const auto& pattern = patterns[p];
        const auto& replacement = replacements[p];
        for (size_t i = 0; i < vect.size() - pattern.size() + 1; ++i)
        {
            if (std::equal(pattern.begin(), pattern.end(), vect.begin() + i))
            {
                vect.erase(vect.begin() + i, vect.begin() + i + pattern.size());
                vect.insert(vect.begin() + i, replacement.begin(), replacement.end());
            }
        }
    }
}

std::string hexBytesToString(const uint8_t* hexBytes, size_t length)
{
    std::string result;
    for (size_t i = 0; i < length; ++i)
    {
        result.push_back(static_cast<char>(hexBytes[i]));
    }
    return result;
}

std::vector<uint8_t> getUserDefinedSeiFrame(FrameInfoSeiPayload& frameInfo, const string& uuid, const string& codec)
{
    vector<uint8_t> vect_sei_total;
    vector<uint8_t> vect_sei_payload;
    if (iequals(codec, "h265"))
    {
        uint8_t h265_sei_frameType[3] = { 0x4e, 0x01, 0x05 };   /* 0x01 sei type is cuvid specific */
        vect_sei_total.insert(vect_sei_total.end(), std::begin(h265_sei_frameType), std::end(h265_sei_frameType));
    }
    else if (iequals(codec, "h264"))
    {
        // Adding start codes to avoid webrtc errors.
        uint8_t h264_sei_frameType[6] = { 0x00, 0x00, 0x00, 0x01, 0x06, 0x05 };
        vect_sei_total.insert(vect_sei_total.end(), std::begin(h264_sei_frameType), std::end(h264_sei_frameType));
    }
    else
    {
        /* Not supported */
        return vect_sei_total;
    }

    /* Size of the payload is nothing but size of int64_t counter plus uuid size (128bit) */
    uint8_t payload_size = sizeof(FrameInfoSeiPayload) + UUID_STANDARD_SIZE;
    vect_sei_total.insert(vect_sei_total.end(), payload_size);

    /* Insert 16-byte uuid, convert 32-byte nibble to 16-byte hex uuid */
    for (size_t j = 0; j < uuid.size(); j+=2)
    {
        int hex_byte;
        string str_byte = uuid.substr(j, 2);
        std::istringstream(str_byte) >> std::hex >> hex_byte;
        vect_sei_total.insert(vect_sei_total.end(), (int8_t)hex_byte);
    }

    /* Insert frameInfo struct into the buffer */
    uint8_t *frameInfo_ptr = reinterpret_cast<uint8_t*>(&frameInfo);
    for (size_t j = 0; j < sizeof(FrameInfoSeiPayload); j++)
    {
        vect_sei_payload.insert(vect_sei_payload.end(), frameInfo_ptr[j]);
    }

#ifdef ENABLE_EMULATION_PREVENTATION_BYTE_SUPPORT
    checkAndInsertEmulationPrevByte(vect_sei_payload);
#endif

    /* Finally insert payload into final sei frame */
    vect_sei_total.insert(vect_sei_total.end(), vect_sei_payload.begin(), vect_sei_payload.end());
    return vect_sei_total;
}

std::vector<uint8_t> getUserDefinedSeiFrameFromJson(Json::Value& value, const string& uuid, const string& codec)
{
    vector<uint8_t> vect_sei_total;
    if (iequals(codec, "h265"))
    {
        uint8_t h265_sei_frameType[3] = { 0x4e, 0x01, 0x05 };   /* 0x01 sei type is cuvid specific */
        vect_sei_total.insert(vect_sei_total.end(), std::begin(h265_sei_frameType), std::end(h265_sei_frameType));
    }
    else if (iequals(codec, "h264"))
    {
        // Adding start codes to avoid webrtc errors.
        uint8_t h264_sei_frameType[6] = { 0x00, 0x00, 0x00, 0x01, 0x06, 0x05 };
        vect_sei_total.insert(vect_sei_total.end(), std::begin(h264_sei_frameType), std::end(h264_sei_frameType));
    }
    else
    {
        /* Not supported */
        return vect_sei_total;
    }

    Json::StreamWriterBuilder writerBuilder;
    std::string jsonString = Json::writeString(writerBuilder, value);

    /* Size of the payload is nothing but size of int64_t counter plus uuid size (128bit) */
    uint8_t payload_size = jsonString.size() + UUID_STANDARD_SIZE;
    vect_sei_total.insert(vect_sei_total.end(), payload_size);

    /* Insert 16-byte uuid, convert 32-byte nibble to 16-byte hex uuid */
    for (size_t j = 0; j < uuid.size(); j+=2)
    {
        int hex_byte;
        string str_byte = uuid.substr(j, 2);
        std::istringstream(str_byte) >> std::hex >> hex_byte;
        vect_sei_total.insert(vect_sei_total.end(), (int8_t)hex_byte);
    }

    /* Insert actual payload into the buffer */
    std::vector<uint8_t> vect_sei_payload(jsonString.begin(), jsonString.end());

#ifdef ENABLE_EMULATION_PREVENTATION_BYTE_SUPPORT
    checkAndInsertEmulationPrevByte(vect_sei_payload);
#endif

    /* Finally insert payload into final sei frame */
    vect_sei_total.insert(vect_sei_total.end(), vect_sei_payload.begin(), vect_sei_payload.end());
    return vect_sei_total;
}

int64_t parseSeiFrameId(const unsigned char *buffer, ssize_t size, int64_t& pts_from_server, const string& codec)
{
    int64_t frameId = -1;
    FrameInfoSeiPayload frameInfo;
    vector<uint8_t> vect_sei_payload;
    string uuid_str;

    /* |-- 6Bytes SEI startCode --|-- 1Byte PayloadSize --|-- 16Bytes UUID --|-- Payload (payloadSize - UUID) --|
       0                          6                       7                  23
    */
    if ((iequals(codec, "h264") && size >= H264_SEI_NAL_START_CODE_SIZE && buffer[5] == H264_NAL_SEI_USER_DEFINED_TYPE) ||
        (iequals(codec, "h265") && size >= H265_SEI_NAL_START_CODE_SIZE && buffer[6] == H265_NAL_SEI_USER_DEFINED_TYPE))
    {
        uint8_t *frameInfo_ptr = reinterpret_cast<uint8_t*>(&frameInfo);

        if (uuid_str.find(SEI_CUSTOM_META_UUID) != string::npos)
        {
            size_t seiPayloadSize = buffer[H264_SEI_NAL_START_CODE_SIZE] - UUID_STANDARD_SIZE;
            if (seiPayloadSize < sizeof(FrameInfoSeiPayload))
                return frameId;
        }

        /* Parse the SEI UUID */
        int nal_start_code_size = iequals(codec, "h265") ? H265_SEI_NAL_START_CODE_SIZE : H264_SEI_NAL_START_CODE_SIZE;
        ssize_t sei_uuid_index = nal_start_code_size + 1;
        if (size >= sei_uuid_index)
        {
            uint8_t *uuid_buf  = (uint8_t *)&buffer[sei_uuid_index];
            uuid_str = hexBytesToString(uuid_buf, UUID_STANDARD_SIZE);
            if (uuid_str.find(SEI_CUSTOM_META_UUID) == string::npos && uuid_str.find(MEGA_SEI_CUSTOM_META_UUID) == string::npos)
            {
                /* This is not vst intended sei frame, ignore it */
                return frameId;
            }
        }

        ssize_t seiPayload_index = nal_start_code_size + 1 + UUID_STANDARD_SIZE;
        if (GET_CONFIG().enable_frameid_in_webrtc_stream)
        {
            int total_sei_frame_size = nal_start_code_size + 1 + UUID_STANDARD_SIZE + sizeof(FrameInfoSeiPayload);
            if (size > total_sei_frame_size)
            {
                seiPayload_index += (size - total_sei_frame_size);
            }
        }

        /* Parse the actual payload frameId + time */
        if (size >= seiPayload_index)
        {
            uint8_t *seiPayload  = (uint8_t *)&buffer[seiPayload_index];
            vect_sei_payload.assign(seiPayload, seiPayload + size - 1);
#ifdef ENABLE_EMULATION_PREVENTATION_BYTE_SUPPORT
            checkAndRemoveEmulationPrevByte(vect_sei_payload);
#endif
            if (uuid_str.find(SEI_CUSTOM_META_UUID) != string::npos)
            {
                for (size_t i = 0; i < vect_sei_payload.size(); i++)
                {
                    frameInfo_ptr[i] = vect_sei_payload[i];
                }
                if (frameInfo.frameId != -1 && frameInfo.timestamp >= 0)
                {
                    frameId = frameInfo.frameId;
                    pts_from_server = frameInfo.timestamp / 1000;
                }
            }
            else if (uuid_str.find(MEGA_SEI_CUSTOM_META_UUID) != string::npos)
            {
                string jsonString = std::string(vect_sei_payload.begin(), vect_sei_payload.end());
                // Parse the string into a JSON object
                Json::CharReaderBuilder readerBuilder;
                Json::Value jsonValue;
                std::string errs;

                std::istringstream s(jsonString);
                if (!Json::parseFromStream(readerBuilder, s, &jsonValue, &errs))
                {
                    LOG(error) << "Failed to parse JSON: " << errs << endl;
                    return -1;
                }

                try
                {
                    if (jsonValue.isMember("frame_id") && jsonValue["frame_id"].isInt64())
                    {
                        frameId = jsonValue["frame_id"].asInt64();
                    }
                    if (jsonValue.isMember("timestamp") && jsonValue["timestamp"].isInt64())
                    {
                        pts_from_server = jsonValue["timestamp"].asInt64() / 1000;
                    }
                } catch (const Json::LogicError& e) {
                    LOG(error) << "Error accessing JSON value: " << e.what() << std::endl;
                }
            }
        }
    }
    return frameId;
}

int64_t getSeiIndex(std::vector<uint8_t>& content)
{
    NaluType nal_type = kNalUnknown;
    int index_for_sei = -1;
    int chunk_to_read = content.size() < 1024 ? content.size() : 1024;

    /* Iterate & find the index of the data nal-unit */
    for (int i = 0; i < chunk_to_read; i++)
    {
        if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x00) && (content[i + 3] == 0x01))
        {
            uint8_t data = content[i + 4];
            nal_type = static_cast<NaluType>(data & 0x1F);
        }
        else if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x01))
        {
            uint8_t data = content[i + 3];
            nal_type = static_cast<NaluType>(data & 0x1F);
        }

        if (nal_type == NaluType::kIdr || nal_type == NaluType::kSlice)
        {
            index_for_sei = i;
            break;
        }
    }
    return index_for_sei;
}

void removeH264NalStartCodes(std::vector<uint8_t>& content)
{
    int marker_size = -1;
    if (content.size() < MIN_SIZE_VIDEO_FRAME)
    {
        return;
    }

    int chunk_to_read = content.size() < MAX_CHUNK_TO_READ ? content.size() : MAX_CHUNK_TO_READ;

    /* Iterate & find the index of the data nal-unit */
    for (int i = 0; i < chunk_to_read; i++)
    {
        if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x00) && (content[i + 3] == 0x01))
        {
            marker_size = 4;
            break;
        }
        else if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x01))
        {
            marker_size = 3;
            break;
        }
    }
    if (marker_size >= 0)
    {
        content.erase(content.begin(), content.begin() + marker_size);
    }
    return;
}

bool isValidDataNAL(const uint8_t& nalu_type, const string& codec)
{
    if (iequals(codec, "H264"))
    {
        if (nalu_type == NaluType::kIdr || nalu_type == NaluType::kSlice)
        {
            return true;
        }
    }
    else if(iequals(codec, "H265"))
    {
        if (nalu_type <= H265NaluType::RSV_VCL31)
        {
            return true;
        }
    }
    return false;
}

bool isIDRFrame(const uint8_t& nalu_type, const string& codec)
{
    if (iequals(codec, "H264"))
    {
        if (nalu_type == NaluType::kIdr)
        {
            return true;
        }
    }
    else if(iequals(codec, "H265"))
    {
        if (nalu_type == H265NaluType::IDR_W_RADL || nalu_type == H265NaluType::IDR_N_LP ||
            nalu_type == H265NaluType::CRA_NUT || nalu_type == H265NaluType::RSV_IRAP_VCL22 ||
            nalu_type == H265NaluType::RSV_IRAP_VCL23)
        {
            return true;
        }
    }
    return false;
}

static NvGstAutoplugSelectResult
autoplug_select (GstElement * dbin, GstPad * pad, GstCaps * caps,
     GstElementFactory * factory, gpointer data)
{
    NvGstAutoplugSelectResult ret = NVGST_AUTOPLUG_SELECT_TRY;
    const gchar *klass = gst_element_factory_get_klass (factory);
    /* Check only Decode Klass */
    if (strstr (klass, "Decode"))
    {
        /* Check only Decode Video Klass */
        if (strstr (klass, "Video"))
        {
            /* Return SKIP value for Hardware Decoders */
            if (!strcmp ((GST_OBJECT_NAME (factory)), NV_V4L2_DECODER) || !strcmp ((GST_OBJECT_NAME (factory)), OMX_DECODER) ||
                !strcmp ((GST_OBJECT_NAME (factory)), NVCODEC_H264_DECODER) || !strcmp ((GST_OBJECT_NAME (factory)), NVCODEC_H265_DECODER))
            {
                ret = NVGST_AUTOPLUG_SELECT_SKIP;
            }
        }
    }
    return ret;
}

int getMediaInformationUsingLibav(const string& filename, Json::Value &media_info, bool millisec)
{
    if (filename.empty())
    {
        LOG(error) << "Empty filename provided" << endl;
        return -1;
    }

    // Log whether this is a URL or local file
    if (isRemoteUrl(filename))
    {
        // Mask presigned URLs before logging
        string maskedUrl = maskPresignedUrl(filename);
        LOG(info) << "Processing HTTP/HTTPS URL with libav: " << maskedUrl << endl;
    }
    else
    {
        LOG(info) << "Processing local file with libav: " << filename << endl;
    }

    LibavWrapper* lav = nullptr;
    try
    {
        lav = LibavWrapper::getInstance();
    }
    catch (const std::exception& e)
    {
        LOG(error) << "LibavWrapper initialization failed: " << e.what() << endl;
        return -1;
    }
    catch (...)
    {
        LOG(error) << "LibavWrapper initialization failed with unknown error" << endl;
        return -1;
    }
    if (!lav || !lav->isLibavAvailable())
    {
        LOG(error) << "Libav not available via LibavWrapper" << endl;
        return -1;
    }

    AVFormatContext *fmt_ctx = nullptr;
    int ret = 0;

    // FFmpeg/libav natively supports HTTP/HTTPS URLs
    // For remote URLs, optimize by limiting probe size and duration
    AVDictionary *format_opts = nullptr;
    bool isRemote = isRemoteUrl(filename);

    // Timing instrumentation for remote URLs only
    std::chrono::high_resolution_clock::time_point start_time, phase_start;

    if (isRemote)
    {
        // Initialize timing for remote URLs
        start_time = std::chrono::high_resolution_clock::now();
        phase_start = start_time;

        // Balanced optimization for remote files
        // Settings must be large enough to handle MP4 files with moov atom at end
        // but small enough to reduce unnecessary network I/O
        
        // Allow probesize to be configurable via environment variable (default: 10MB)
        const char* probe_env = std::getenv("LIBAV_PROBESIZE");
        const char* probesize = probe_env ? probe_env : "10000000";          // Default 10MB
        lav->av_dict_set(&format_opts, "probesize", probesize, 0);
        lav->av_dict_set(&format_opts, "analyzeduration", "2000000", 0);     // 2 seconds - reliable analysis
        lav->av_dict_set(&format_opts, "fflags", "+fastseek", 0);            // Fast seek

        // HTTP-specific optimizations to reduce latency
        lav->av_dict_set(&format_opts, "reconnect", "1", 0);                 // Auto-reconnect on failure
        lav->av_dict_set(&format_opts, "reconnect_streamed", "1", 0);        // Reconnect for streaming
        lav->av_dict_set(&format_opts, "reconnect_delay_max", "2", 0);       // Max 2s delay on reconnect
        lav->av_dict_set(&format_opts, "multiple_requests", "1", 0);         // Enable HTTP keep-alive
    }

    if (lav->avformat_open_input(&fmt_ctx, filename.c_str(), nullptr, &format_opts) < 0)
    {
        LOG(error) << "avformat_open_input failed for: " << filename << endl;
        if (format_opts)
            lav->av_dict_free(&format_opts);
        return -1;
    }

    // Log timing for avformat_open_input
    if (isRemote)
    {
        auto phase_end = std::chrono::high_resolution_clock::now();
        auto duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(phase_end - phase_start).count();
        LOG(info) << "TIMING: avformat_open_input took " << duration_ms << "ms" << endl;
        phase_start = phase_end;
    }

    if (format_opts)
        lav->av_dict_free(&format_opts);

    // avformat_find_stream_info will use the limits set in format context
    // No need for additional stream-specific options
    if (lav->avformat_find_stream_info(fmt_ctx, nullptr) < 0)
    {
        LOG(error) << "avformat_find_stream_info failed for file: " << filename << endl;
        lav->avformat_close_input(&fmt_ctx);
        return -1;
    }

    // Log timing for avformat_find_stream_info
    if (isRemote)
    {
        auto phase_end = std::chrono::high_resolution_clock::now();
        auto duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(phase_end - phase_start).count();
        LOG(info) << "TIMING: avformat_find_stream_info took " << duration_ms << "ms" << endl;
        phase_start = phase_end;
    }

    int64_t duration_us = fmt_ctx->duration;
    if (duration_us > 0)
    {
        double duration_out = millisec ? (duration_us / 1000.0) : (duration_us / 1000000.0);
        media_info["Duration"] = duration_out;
    }
    else
    {
        media_info["Duration"] = 0.0;
    }
    uint64_t duration_ms = duration_us > 0 ? (uint64_t)(duration_us / 1000) : 0;

    if (fmt_ctx->iformat)
    {
        std::string container_long;
        if (fmt_ctx->iformat->long_name)
            container_long = fmt_ctx->iformat->long_name;
        else if (fmt_ctx->iformat->name)
            container_long = fmt_ctx->iformat->name;

        if (!container_long.empty())
        {
            // Normalize common libav long_names to expected synonyms
            if (container_long.find("QuickTime") != std::string::npos || container_long.find("quicktime") != std::string::npos)
            {
                media_info["Container"] = "QuickTime";
            }
            else if (container_long.find("Matroska") != std::string::npos || container_long.find("matroska") != std::string::npos)
            {
                media_info["Container"] = "Matroska";
            }
            else
            {
                media_info["Container"] = container_long;
            }
        }
    }

    int video_stream_index = lav->av_find_best_stream(fmt_ctx, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (video_stream_index >= 0)
    {
        AVStream *vs = fmt_ctx->streams[video_stream_index];
        AVCodecParameters *vpar = vs->codecpar;

        // Prefer video stream duration over container duration when available.
        if (vs->duration > 0 && vs->time_base.num > 0 && vs->time_base.den > 0 && lav->av_rescale_q)
        {
            AVRational us_time_base = {1, 1000000};
            int64_t video_duration_us = lav->av_rescale_q(vs->duration, vs->time_base, us_time_base);
            if (video_duration_us > 0)
            {
                duration_us = video_duration_us;
                duration_ms = static_cast<uint64_t>(duration_us / 1000);
                double duration_out = millisec ? (duration_us / 1000.0) : (duration_us / 1000000.0);
                media_info["Duration"] = duration_out;
            }
        }

        const AVCodecDescriptor *vdesc = lav->avcodec_descriptor_get(vpar->codec_id);
        std::string codec_name = vdesc && vdesc->name ? std::string(vdesc->name) : "";
        std::string codec_field = codec_name;
        if (codec_name == "hevc" || codec_name == "h265")
            codec_field = "h265";
        else if (codec_name == "h264" || codec_name == "avc1")
            codec_field = "h264";

        if (!codec_field.empty())
            media_info["Codec"] = codec_field;

        media_info["Width"] = vpar->width;
        media_info["Height"] = vpar->height;

        AVRational fr = lav->av_guess_frame_rate(fmt_ctx, vs, nullptr);
        double fps = 0.0;
        if (fr.num != 0 && fr.den != 0)
        {
            fps = (double)fr.num / fr.den;
            media_info["FramerateNum"] = fr.num;
            media_info["FramerateDenom"] = fr.den;
        }
        else
        {
            AVRational avg = vs->avg_frame_rate;
            if (avg.num != 0 && avg.den != 0)
            {
                fps = (double)avg.num / avg.den;
                media_info["FramerateNum"] = avg.num;
                media_info["FramerateDenom"] = avg.den;
            }
            else
            {
                media_info["FramerateNum"] = 0;
                media_info["FramerateDenom"] = 0;
            }
        }

        media_info["Framerate"] = (float)fps;

        double frame_count = (fps > 0.0 && duration_ms > 0) ? ((double)duration_ms * fps / 1000.0) : 0.0;
        media_info["FrameCount"] = (unsigned int)frame_count;

        media_info["ScanType"] = "Progressive";

        if (vpar->bit_rate > 0)
            media_info["Bitrate"] = (Json::UInt)vpar->bit_rate;
        else if (fmt_ctx->bit_rate > 0)
            media_info["Bitrate"] = (Json::UInt)fmt_ctx->bit_rate;
    }

    int audio_stream_index = lav->av_find_best_stream(fmt_ctx, AVMEDIA_TYPE_AUDIO, -1, video_stream_index, nullptr, 0);
    if (audio_stream_index >= 0)
    {
        AVStream *as = fmt_ctx->streams[audio_stream_index];
        AVCodecParameters *apar = as->codecpar;

        const AVCodecDescriptor *adesc = lav->avcodec_descriptor_get(apar->codec_id);
        if (adesc)
        {
            if (adesc->long_name)
                media_info["AudioCodec"] = adesc->long_name;
            else if (adesc->name)
                media_info["AudioCodec"] = adesc->name;
        }

        if (apar->sample_rate > 0)
            media_info["SampleRate"] = apar->sample_rate;
#if LIBAVCODEC_VERSION_MAJOR >= 59
        int channels_count = apar->ch_layout.nb_channels;
#else
        int channels_count = apar->channels;
#endif
        if (channels_count > 0)
            media_info["Channels"] = channels_count;

        int bits_per_sample = lav->av_get_bits_per_sample(apar->codec_id);
        if (bits_per_sample <= 0 && apar->format != AV_SAMPLE_FMT_NONE)
        {
            int bytes_per_sample = lav->av_get_bytes_per_sample((AVSampleFormat)apar->format);
            if (bytes_per_sample > 0)
                bits_per_sample = bytes_per_sample * 8;
        }
        if (bits_per_sample > 0)
            media_info["Depth"] = bits_per_sample;
    }

    lav->avformat_close_input(&fmt_ctx);

    // Log total timing for remote URLs
    if (isRemote)
    {
        auto end_time = std::chrono::high_resolution_clock::now();
        auto total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time).count();
        LOG(info) << "TIMING: TOTAL libav processing took " << total_ms << "ms" << endl;
    }

    // Log result summary for libav method
    if (ret == 0)
    {
        double final_duration = media_info.get("Duration", 0.0).asDouble();
        unsigned int final_framecount = media_info.get("FrameCount", 0).asUInt();

        if (final_duration > 0.0 && final_framecount > 0)
        {
            LOG(info) << "Libav extraction successful - Duration: " << final_duration
                      << "s, FrameCount: " << final_framecount << endl;
        }
        else
        {
            LOG(warning) << "Libav extraction returned zeros - Duration: " << final_duration
                         << "s, FrameCount: " << final_framecount
                         << " (likely due to SEI corruption or missing metadata)" << endl;
        }
    }

    return ret;
}

int getMediaInformation (const string& filename, Json::Value &media_info, bool millisec)
{
    // Mask presigned URLs before logging
    string logFilename = filename;
    if (isRemoteUrl(filename))
    {
        logFilename = maskPresignedUrl(filename);
    }
    LOG(info) << "getMediaInformation filename: " << logFilename << endl;

    // Always try Libav implementation first (fast, handles most cases)
    LOG(info) << "Attempting primary method: Libav-based media information extraction" << endl;
    int libav_result = -1;
    try
    {
        libav_result = getMediaInformationUsingLibav(filename, media_info, millisec);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Libav-based extraction threw exception: " << e.what() << endl;
        libav_result = -1;
    }
    catch (...)
    {
        LOG(error) << "Libav-based extraction threw unknown exception" << endl;
        libav_result = -1;
    }

    // Check if libav extraction was successful (non-zero duration and frame count)
    bool libav_success = (libav_result == 0) &&
                         (media_info.get("Duration", 0.0).asDouble() > 0.0) &&
                         (media_info.get("FrameCount", 0).asUInt() > 0);

    if (libav_success)
    {
        LOG(info) << "SUCCESS: Libav extraction completed - Duration: "
                  << media_info.get("Duration", 0.0).asDouble() << "s, FrameCount: "
                  << media_info.get("FrameCount", 0).asUInt() << endl;
        return 0;  // Success
    }

    // Libav failed or returned zeros - fallback to GStreamer discoverer
    LOG(info) << "Libav extraction insufficient (Duration: " << media_info.get("Duration", 0.0).asDouble()
              << ", FrameCount: " << media_info.get("FrameCount", 0).asUInt() << ")" << endl;
    LOG(info) << "FALLBACK: Attempting GStreamer discoverer-based extraction" << endl;

    // Clear any partial results from libav attempt
    media_info.clear();

    GstDiscoverer *discoverer = nullptr;
    GstDiscovererPrivate *discovererPriv = nullptr;
    GstDiscovererInfo *info = nullptr;
    GList *videos = nullptr;
    GList *audios = nullptr;
    GstDiscovererStreamInfo *sinfo = nullptr;
    GstDiscovererVideoInfo *vinfo = nullptr;
    GstDiscovererAudioInfo *ainfo = nullptr;
    GError *err = nullptr;
    int ret = 0;
    const GstTagList *stream_tag_list;
    guint num, denom;
    gboolean value;
    uint64_t duration, duration_ms;
    string video_codec_string, container_string, audio_codec_string;
    gchar *ctr_caps_str, *video_caps_str, *audio_caps_str;
    int retry_count = 0;
    unsigned int divisor_factor;

    // Detect if filename is already a URL (http://, https://, s3://, or file://)
    string uri_string;
    if (isUri(filename))
    {
        // Already a URI, use as-is
        uri_string = filename;
    }
    else
    {
        // Local file path, add file:// prefix
        uri_string = string("file://") + filename;
    }
    gchar *uri = (gchar *) uri_string.c_str();

    discoverer = gst_discoverer_new (4 * GST_SECOND, &err);
    if (err != nullptr)
    {
        LOG(error) << "gst_discoverer_new failed with err:" << err << endl;
        ret = -1;
        goto exit;
    }

    /* Use default software decoder for discoverer */
    discovererPriv = discoverer->priv;
    if (discovererPriv && discovererPriv->uridecodebin)
    {
        g_signal_connect (discovererPriv->uridecodebin, "autoplug-select", G_CALLBACK (autoplug_select), nullptr);
    }

retry:
    info = gst_discoverer_discover_uri (discoverer, uri, &err);
    if (err != nullptr)
    {
        LOG(error) << "gst_discoverer_discover_uri failed with err:" << err << endl;
        ret = -1;
        goto exit;
    }

    sinfo = gst_discoverer_info_get_stream_info (info);
    duration = gst_discoverer_info_get_duration (info);
    divisor_factor = millisec ? 1000000 : 1000000000;
    media_info["Duration"] = (duration / static_cast<double>(divisor_factor));     // convert to seconds/ms
    duration_ms = (uint64_t)(duration / 1000000);

    if (GST_IS_DISCOVERER_CONTAINER_INFO (sinfo))
    {
        GstCaps *caps;

        caps = gst_discoverer_stream_info_get_caps (
            GST_DISCOVERER_STREAM_INFO(sinfo));
        container_string = gst_pb_utils_get_codec_description (caps);
        ctr_caps_str = gst_caps_to_string (caps);
        media_info["Container"] = container_string;
        media_info["ContainerCaps"] = ctr_caps_str;
        LOG(verbose) << "container: " << container_string << endl;
        LOG(verbose) << "container caps: " << ctr_caps_str << endl;
        gst_caps_unref (caps);
        g_free (ctr_caps_str);
    }

    if (GST_IS_DISCOVERER_VIDEO_INFO (sinfo))
    {
        vinfo = GST_DISCOVERER_VIDEO_INFO (sinfo);
    }
    else
    {
        videos = gst_discoverer_info_get_video_streams (info);
        if (videos != nullptr)
        {
            vinfo = (GstDiscovererVideoInfo *)videos->data;
        }
    }
    if (GST_IS_DISCOVERER_AUDIO_INFO (sinfo))
    {
        ainfo = GST_DISCOVERER_AUDIO_INFO (sinfo);
    }
    else
    {
        audios = gst_discoverer_info_get_audio_streams (info);
        if (audios != nullptr)
        {
            ainfo = (GstDiscovererAudioInfo *)audios->data;
        }
    }

    if (vinfo != nullptr)
    {
        GstCaps *caps;

        caps = gst_discoverer_stream_info_get_caps (GST_DISCOVERER_STREAM_INFO (vinfo));
        video_codec_string = gst_pb_utils_get_codec_description (caps);
        video_caps_str = gst_caps_to_string (caps);
        media_info["VideoCaps"] = video_caps_str;
        LOG(info) << "Video codec format for file:" << filename << ", codec:" << video_codec_string << endl;

        if (video_codec_string.find("H.264") != string::npos)
        {
            media_info["Codec"] = "h264";
        }
        else if (video_codec_string.find("H.265") != string::npos)
        {
            media_info["Codec"] = "h265";
        }
        else
        {
            media_info["Codec"] = video_codec_string;
        }
        media_info["Height"] = gst_discoverer_video_info_get_height (vinfo);
        media_info["Width"] = gst_discoverer_video_info_get_width (vinfo);

        num = gst_discoverer_video_info_get_framerate_num (vinfo);
        denom = gst_discoverer_video_info_get_framerate_denom (vinfo);
        
        // Defensive check to prevent division by zero from invalid media metadata
        if (denom == 0)
        {
            LOG(warning) << "Invalid framerate denominator (0) in media file, using default " 
                        << DEFAULT_FRAMERATE_NUM << "/" << DEFAULT_FRAMERATE_DENOM << " fps" << endl;
            num = DEFAULT_FRAMERATE_NUM;
            denom = DEFAULT_FRAMERATE_DENOM;
        }
        
        media_info["Framerate"] = (float) num/denom;
        media_info["FramerateNum"] = num;
        media_info["FramerateDenom"] = denom;
        double frameRate = (double) num/denom;
        media_info["FrameCount"] = (unsigned int)((duration_ms * frameRate) / 1000);
        LOG(info) << "FrameCount: " << media_info.get("FrameCount", 0).asUInt() << endl;
        LOG(verbose) << "Frame rate fraction: " << num << "/" << denom << endl;

        value = gst_discoverer_video_info_is_interlaced (vinfo);
        if (value)
        {
            media_info["ScanType"] = "Interlaced";
        }
        else
        {
            media_info["ScanType"] = "Progressive";
        }

        stream_tag_list = gst_discoverer_stream_info_get_tags (GST_DISCOVERER_STREAM_INFO (vinfo));
        if (stream_tag_list)
        {
            guint bitrate;
            if (gst_tag_list_get_uint (stream_tag_list, "bitrate", &bitrate))
            {
                media_info["Bitrate"] = bitrate;
                LOG(verbose) << "bitrate: " << bitrate << endl;
            }
        }
        LOG(verbose) << "video codec: " << video_codec_string << endl;
        LOG(verbose) << "video caps: " << video_caps_str << endl;
        gst_caps_unref (caps);
        g_free (video_caps_str);
    }
    if (ainfo != nullptr)
    {
        GstCaps *caps;
        guint data;
        caps = gst_discoverer_stream_info_get_caps (GST_DISCOVERER_STREAM_INFO (ainfo));
        audio_codec_string = gst_pb_utils_get_codec_description (caps);
        audio_caps_str = gst_caps_to_string (caps);
        LOG(info) << "Audio codec format for file:" << filename << ", codec:" << audio_codec_string << endl;
        media_info["AudioCodec"] = audio_codec_string;
        media_info["AudioCaps"] = audio_caps_str;

        data = gst_discoverer_audio_info_get_sample_rate (ainfo);
        if (data)
        {
            media_info["SampleRate"] = data;
        }
        data = gst_discoverer_audio_info_get_channels  (ainfo);
        if (data)
        {
            media_info["Channels"] = data;
        }
        data = gst_discoverer_audio_info_get_depth   (ainfo);
        if (data)
        {
            media_info["Depth"] = data;
        }
        LOG(verbose) << "audio codec: " << audio_codec_string << endl;
        LOG(verbose) << "audio caps: " << audio_caps_str << endl;
        gst_caps_unref (caps);
        g_free (audio_caps_str);
    }
    if (videos != nullptr)
    {
        gst_discoverer_stream_info_list_free (videos);
    }

    gst_discoverer_info_unref (info);
exit:
    if (container_string.empty() || video_codec_string.empty())
    {
        retry_count++;
        if (retry_count < 3)
        {
            LOG(error) << "container/codec info is empty, retrying count:" << retry_count << endl;
            goto retry;
        }
    }
    g_object_unref (discoverer);

    // Log the final result of GStreamer discoverer fallback
    if (ret == 0)
    {
        double gst_duration = media_info.get("Duration", 0.0).asDouble();
        unsigned int gst_framecount = media_info.get("FrameCount", 0).asUInt();

        if (gst_duration > 0.0 && gst_framecount > 0)
        {
            LOG(info) << "SUCCESS: GStreamer discoverer fallback completed - Duration: "
                      << gst_duration << "s, FrameCount: " << gst_framecount << endl;
        }
        else
        {
            LOG(warning) << "GStreamer discoverer completed but returned insufficient data - Duration: "
                         << gst_duration << "s, FrameCount: " << gst_framecount << endl;
        }
    }
    else
    {
        LOG(error) << "GStreamer discoverer fallback failed with error code: " << ret << endl;
    }

    return ret;
}

inline uint32_t get_bit(const uint8_t * const base, uint32_t offset)
{
    return ((*(base + (offset >> 0x3))) >> (0x7 - (offset & 0x7))) & 0x1;
}

/* This function implement decoding of exp-Golomb codes of zero range (used in H.264) */
uint32_t decode_UGolomb(const uint8_t *base, uint32_t bit_offset)
{
    uint32_t zeros = 0;
    if (base == nullptr || bit_offset > 31)
    {
        LOG(error) << "Base or offset is NULL" << endl;
        return -1;
    }
    /* calculate zero bits in the given bytes */
    while (0 == get_bit(base, bit_offset++)) zeros++;
    if (zeros > 31)
    {
        LOG(error) << "Failed to read, out of range" << endl;
        return -1;
    }

    /* insert first 1 bit */
    uint32_t info = 1 << zeros;
    for (int32_t i = zeros - 1; i >= 0; i--)
    {
        info |= get_bit(base, bit_offset++) << i;
    }
    return (info - 1);
}

SliceType parseH264SliceType(const unsigned char *buffer, ssize_t size)
{
    SliceType slice_type = SLICE_TYPE_UNKNOWN;
    unsigned int nal_index = getH26xMarkerSize(buffer, size);
    if (size > nal_index + 1)
    {
        const uint8_t *data = &buffer[++nal_index];
        slice_type = (SliceType)decode_UGolomb(data, 1);
    }
    return slice_type;
}

/**
 * Helper function to read an unsigned Exp-Golomb coded value from a bit stream.
 * Returns the value and updates bit_offset to point after the read value.
 * Returns -1 on error.
 */
static int32_t read_ue_golomb(const uint8_t *data, uint32_t &bit_offset, ssize_t max_bits)
{
    uint32_t zeros = 0;
    bool found_one = false;
    while (bit_offset < (uint32_t)max_bits)
    {
        int bit = get_bit(data, bit_offset);
        bit_offset++;
        if (bit != 0)
        {
            found_one = true;
            break;
        }
        zeros++;
        if (zeros > 31) return -1; // Invalid
    }
    if (!found_one) return -1; // Truncated bitstream

    uint32_t value = 1 << zeros;
    for (int32_t i = zeros - 1; i >= 0; i--)
    {
        if (bit_offset < (uint32_t)max_bits)
        {
            value |= get_bit(data, bit_offset++) << i;
        }
    }
    return (int32_t)(value - 1);
}

/**
 * Parse H.265/HEVC slice type from NAL unit buffer.
 *
 * H.265 slice_segment_header structure (simplified):
 *   - first_slice_segment_in_pic_flag (1 bit)
 *   - if (IRAP): no_output_of_prior_pics_flag (1 bit)
 *   - slice_pic_parameter_set_id (ue(v))
 *   - if (!first_slice_segment_in_pic_flag):
 *       - if (dependent_slice_segments_enabled_flag): dependent_slice_segment_flag (1 bit)
 *       - slice_segment_address (u(v)) - requires PPS to know bit length
 *   - if (!dependent_slice_segment_flag):
 *       - for (i = 0; i < num_extra_slice_header_bits; i++): slice_reserved_flag[i] (1 bit)
 *       - slice_type (ue(v))
 *
 * Since we don't have PPS, we can only reliably parse first slice segments where
 * dependent_slice_segment_flag is implicitly 0. We also try num_extra_slice_header_bits = 0
 * which is the most common case.
 */
SliceType parseH265SliceType(const unsigned char *buffer, ssize_t size, H265NaluType nal_type)
{
    SliceType slice_type = SLICE_TYPE_UNKNOWN;
    unsigned int nal_index = getH26xMarkerSize(buffer, size);

    // H.265 has a 2-byte NAL header
    if (size <= (ssize_t)(nal_index + 2))
    {
        return slice_type;
    }

    // Skip NAL header (2 bytes) to get to slice header
    const uint8_t *slice_header = &buffer[nal_index + 2];
    ssize_t remaining_size = size - nal_index - 2;
    ssize_t max_bits = remaining_size * 8;

    if (remaining_size < 3)
    {
        return slice_type;
    }

    // Bit offset starts at 0 in slice_header
    uint32_t bit_offset = 0;

    // first_slice_segment_in_pic_flag (1 bit)
    bool first_slice = (get_bit(slice_header, bit_offset++) != 0);

    // Check if this is an IRAP picture (BLA, IDR, CRA)
    bool is_irap = (nal_type >= H265NaluType::BLA_W_LP && nal_type <= H265NaluType::RSV_IRAP_VCL23);

    // If IRAP, skip no_output_of_prior_pics_flag (1 bit)
    if (is_irap)
    {
        bit_offset++;
    }

    // Read slice_pic_parameter_set_id (ue(v))
    int32_t pps_id = read_ue_golomb(slice_header, bit_offset, max_bits);
    if (pps_id < 0)
    {
        return slice_type; // Parse error
    }

    // If not first slice segment, we cannot reliably parse without PPS info
    // because we don't know:
    // 1. dependent_slice_segments_enabled_flag
    // 2. slice_segment_address bit length (depends on picture size in CTUs)
    if (!first_slice)
    {
        return slice_type;
    }

    // For first slice segments:
    // - dependent_slice_segment_flag is inferred to be 0
    // - No slice_segment_address field
    // - Next comes num_extra_slice_header_bits reserved flags (from PPS)
    // - Then slice_type

    // Since we don't have PPS, try parsing with num_extra_slice_header_bits = 0 (most common)
    // If that gives an invalid slice_type, the stream uses extra header bits

    // Read slice_type (ue(v))
    int32_t slice_type_val = read_ue_golomb(slice_header, bit_offset, max_bits);

    // H.265 slice types: 0=B, 1=P, 2=I
    // Valid slice_type values are 0, 1, 2
    if (slice_type_val >= 0 && slice_type_val <= 2)
    {
        switch (slice_type_val)
        {
            case 0: slice_type = SLICE_TYPE_B; break;
            case 1: slice_type = SLICE_TYPE_P; break;
            case 2: slice_type = SLICE_TYPE_I; break;
        }
    }
    // If slice_type_val > 2, the stream likely uses num_extra_slice_header_bits > 0
    // We could try skipping 1-8 bits and re-parsing, but this adds complexity
    // For now, return UNKNOWN for such streams

    return slice_type;
}

vector<std::pair<NaluType, int>> getListOfNalUnits(std::vector<uint8_t>& content)
{
    NaluType nal_type = kNalUnknown;
    vector<std::pair<NaluType, int>> list;
    int chunk_to_read = content.size() < 1024 ? content.size() : 1024;

    /* Iterate & find the index of the data nal-unit */
    for (int i = 0; i < chunk_to_read; i++)
    {
        if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x00) && (content[i + 3] == 0x01))
        {
            uint8_t data = content[i + 4];
            nal_type = static_cast<NaluType>(data & 0x1F);
            list.push_back(std::make_pair(nal_type, i));
            i += 3;
        }
        else if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x01))
        {
            uint8_t data = content[i + 3];
            nal_type = static_cast<NaluType>(data & 0x1F);
            list.push_back(std::make_pair(nal_type, i));
            i += 2;
        }
        if (GET_CONFIG().enable_frameid_in_webrtc_stream)
        {
            if (nal_type == NaluType::kSei)
            {
                i += UUID_STANDARD_SIZE + sizeof(FrameInfoSeiPayload) + 3;
            }
        }

        if (nal_type == NaluType::kIdr || nal_type == NaluType::kSlice)
        {
            break;
        }
    }
    return list;
}

vector<std::pair<H265NaluType, int>> getListOfH265NalUnits(std::vector<uint8_t>& content)
{
    H265NaluType nal_type = NAL_UNKNOWN;
    vector<std::pair<H265NaluType, int>> list;
    int chunk_to_read = content.size() < 1024 ? content.size() : 1024;

    /* Iterate & find the index of the data nal-unit */
    for (int i = 0; i < chunk_to_read; i++)
    {
        if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x00) && (content[i + 3] == 0x01))
        {
            uint8_t data = content[i + 4];
            nal_type = static_cast<H265NaluType>((data >> 1) & 0x3f);
            list.push_back(std::make_pair(nal_type, i));
            i += 3;
        }
        else if ((content[i] == 0x00) && (content[i + 1] == 0x00)
            && (content[i + 2] == 0x01))
        {
            uint8_t data = content[i + 3];
            nal_type = static_cast<H265NaluType>((data >> 1) & 0x3f);
            list.push_back(std::make_pair(nal_type, i));
            i += 2;
        }
        // TODO - Handle SEI
        if (GET_CONFIG().enable_frameid_in_webrtc_stream)
        {
            if (nal_type == H265NaluType::PREFIX_SEI_NUT || nal_type == H265NaluType::SUFFIX_SEI_NUT)
            {
                //i += UUID_STANDARD_SIZE + sizeof(FrameInfoSeiPayload) + 3;
            }
        }

        if (nal_type <= H265NaluType::RSV_VCL31)
        {
            break;
        }
    }
    return list;
}

Json::Value getVideoMetadata(const string& file_path)
{
    Json::Value response;
    bool found = false;
    string container = "Quicktime";
    string codec = "h264";

    /* Find it locally in device_manager */
    std::shared_ptr<DeviceManager> m_deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (m_deviceMngr)
    {
        std::vector<shared_ptr<StreamInfo>> streamList;
        streamList = m_deviceMngr->getStreamList();
        for (auto const& stream : streamList)
        {
            string path;
            if (m_deviceMngr->isRemoteDevice || m_deviceMngr->type != TYPE_STREAMER)
                path = getFilePathFromUrl(stream->live_url, "webrtc/");
            else
                path = getFilePathFromUrl(stream->live_url, NV_STREAMER);

            if (path == file_path || stream->id == file_path)
            {
                response["Container"] = stream->settings.encoderValues.container;
                response["Codec"] = stream->settings.encoderValues.encoding;
                response["Framerate"] = stringToDouble(stream->settings.encoderValues.frameRate, 30);
                response["FrameCount"] = stringToInt(stream->settings.encoderValues.numFrames, 0);
                if (stream->settings.encoderValues.container.empty() || stream->settings.encoderValues.encoding.empty())
                {
                    found = false;
                    break;
                }
                found = true;
                break;
            }
        }
    }

    /* Not found? find it in database */
    if (found == false)
    {
        auto dbHelper = GET_DB_INSTANCE();
        std::vector<shared_ptr<StreamInfo>> streamList;
        if (0 == dbHelper->getAllStreams(streamList, ModuleLoader::getInstance()->getDeviceId()))
        {
            for (auto const& stream : streamList)
            {
                string path;
                if (m_deviceMngr->isRemoteDevice || m_deviceMngr->type != TYPE_STREAMER)
                    path = getFilePathFromUrl(stream->live_url, "webrtc/");
                else
                    path = getFilePathFromUrl(stream->live_url, NV_STREAMER);

                if (path == file_path || stream->id == file_path)
                {
                    response["Container"] = stream->settings.encoderValues.container;
                    response["Codec"] = stream->settings.encoderValues.encoding;
                    response["Framerate"] = stringToDouble(stream->settings.encoderValues.frameRate, 30);
                    response["FrameCount"] = stringToInt(stream->settings.encoderValues.numFrames, 0);
                    found = true;
                    break;
                }
            }
        }
    }

    /* It should not happen, But get it again mediaInfo as a safer side */
    if (found == false)
    {
        Json::Value value;
        if (getMediaInformation(file_path, value) == 0)
        {
            response["Container"] = value.get("Container", container).asString();
            response["Codec"] = value.get("Codec", codec).asString();
            response["Framerate"] = stringToDouble(value.get("Framerate", "30").asString());
            response["FrameCount"] = stringToInt(value.get("FrameCount", "0").asString());
        }
    }
    return response;
}

Json::Value getAudioMetadata(const string& file_path)
{
    Json::Value response;
    bool found = false;
    string container = "Quicktime";
    string codec = "MPEG-4 AAC";

    /* Find it locally in device_manager */
    std::shared_ptr<DeviceManager> m_deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (m_deviceMngr)
    {
        std::vector<shared_ptr<StreamInfo>> streamList;
        streamList = m_deviceMngr->getStreamList();
        for (auto const& stream : streamList)
        {
            string path;
            if (m_deviceMngr->isRemoteDevice || m_deviceMngr->type != TYPE_STREAMER)
                path = getFilePathFromUrl(stream->live_url, "webrtc/");
            else
                path = getFilePathFromUrl(stream->live_url, NV_STREAMER);

            if (path == file_path || stream->id == file_path)
            {
                response["Container"] = stream->settings.audioEncoderValues.container;
                response["Codec"] = stream->settings.audioEncoderValues.encoding;
                response["SampleRate"] = stringToInt(stream->settings.audioEncoderValues.sample_rate);
                response["Channels"] = stringToInt(stream->settings.audioEncoderValues.channels);
                response["BitsPerSample"] = stringToInt(stream->settings.audioEncoderValues.bits_per_sample);
                found = true;
                break;
            }
        }
    }

    /* Not found? find it in database */
    if (found == false)
    {
        auto dbHelper = GET_DB_INSTANCE();
        std::vector<shared_ptr<StreamInfo>> streamList;
        if (0 == dbHelper->getAllStreams(streamList, ModuleLoader::getInstance()->getDeviceId()))
        {
            for (auto const& stream : streamList)
            {
                string path;
                if (m_deviceMngr->isRemoteDevice || m_deviceMngr->type != TYPE_STREAMER)
                    path = getFilePathFromUrl(stream->live_url, "webrtc/");
                else
                    path = getFilePathFromUrl(stream->live_url, NV_STREAMER);
                if (path == file_path || stream->id == file_path)
                {
                    response["Container"] = stream->settings.audioEncoderValues.container;
                    response["Codec"] = stream->settings.audioEncoderValues.encoding;
                    response["SampleRate"] = stringToInt(stream->settings.audioEncoderValues.sample_rate);
                    response["Channels"] = stringToInt(stream->settings.audioEncoderValues.channels);
                    response["BitsPerSample"] = stringToInt(stream->settings.audioEncoderValues.bits_per_sample);
                    found = true;
                    break;
                }
            }
        }
    }

    /* It should not happen, But get it again mediaInfo as a safer side */
    if (found == false)
    {
        Json::Value value;
        if (getMediaInformation(file_path, value) == 0)
        {
            response["Container"] = value.get("Container", container).asString();
            response["Codec"] = value.get("AudioCodec", codec).asString();
            response["SampleRate"] = value.get("SampleRate", "").asInt();
            response["Channels"] = value.get("Channels", "").asInt();
            response["BitsPerSample"] = value.get("Depth", "").asInt();
        }

    }
    return response;
}

// Generate non-deterministic random int in [min, max]. NOT cryptographically secure (uses MT19937).
// Replaces weak rand() to prevent biased random sampling.
int getSecureRandomInt(int min, int max)
{
    // Validate input range to prevent UB in uniform_int_distribution
    if (min > max)
    {
        LOG(warning) << "getSecureRandomInt: min (" << min << ") > max (" << max << "), swapping" << endl;
        std::swap(min, max);
    }
    
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<int> dis(min, max);
    return dis(gen);
}

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

#pragma once

#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include "logger.h"
#include "video_resolution.h"

using namespace std;
using namespace nv_vms;

inline constexpr const char* DECODER_NODE           = "/dev/nvidia0";
inline constexpr const char* NV_V4L2_DECODER        = "nvv4l2decoder";
inline constexpr const char* OMX_DECODER            = "omxh264dec";
inline constexpr const char* NVCODEC_H264_DECODER   = "nvh264dec";
inline constexpr const char* NVCODEC_H265_DECODER   = "nvh265dec";
inline constexpr const char* SW_AV_DECODER          = "avdec_h264";
inline constexpr const char* NV_VID_CONV            = "nvvidconv";
inline constexpr const char* NV_VID_CONVERT         = "nvvideoconvert";
inline constexpr const char* H264_PARSE             = "h264parse";
inline constexpr const char* H265_PARSE             = "h265parse";

inline constexpr const char* SEI_CUSTOM_META_UUID = "VST_CUSTOM_META";
inline constexpr const char* MEGA_SEI_CUSTOM_META_UUID = "NVDS_CUSTOMMETA";
inline constexpr int H264_SEI_NAL_START_CODE_SIZE = 6;
inline constexpr int H265_SEI_NAL_START_CODE_SIZE = 7;
inline constexpr int H264_NAL_SEI_USER_DEFINED_TYPE = 0x05;
inline constexpr int H265_NAL_SEI_START_CODE_SIZE = 6;
inline constexpr int H265_NAL_SEI_USER_DEFINED_TYPE = 0x05;
inline constexpr int UUID_STANDARD_SIZE = 16;

#define IS_NAL_UNIT_START(buffer_ptr) (!buffer_ptr[0] && !buffer_ptr[1] && \
                                        !buffer_ptr[2] && (buffer_ptr[3] == 1))
#define IS_NAL_UNIT_START1(buffer_ptr) (!buffer_ptr[0] && !buffer_ptr[1] && \
                                        (buffer_ptr[2] == 1))

/* This is for SEI frames as per h264 spec to add emulation prev byte
* Disabling it as of now. Need to enable at both sides here & client side (DS) */
//#define ENABLE_EMULATION_PREVENTATION_BYTE_SUPPORT 1


/* This macro is enabled by default. But to use this feature, enable vst_config option enable_frameid_in_webrtc_stream */
#define ENABLE_FRAMEID_SUPPORT_IN_WEBRTC

enum NaluType : uint8_t
{
  kSlice = 1,
  kIdr = 5,
  kSei = 6,
  kSps = 7,
  kPps = 8,
  kAud = 9,
  kEndOfSequence = 10,
  kEndOfStream = 11,
  kFiller = 12,
  kStapA = 24,
  kStapB = 25,
  kMtap16 = 26,
  kMtap24 = 27,
  kFuA = 28,
  kNalUnknown = 0xFF
};

enum H265NaluType : uint8_t
{
    TRAIL_N = 0,
    TRAIL_R = 1,
    TSA_N = 2,
    TSA_R = 3,
    STSA_N = 4,
    STSA_R = 5,
    RADL_N = 6,
    RADL_R = 7,
    RASL_N = 8,
    RASL_R = 9,
    RSV_VCL_N10 = 10,
    RSV_VCL_R11 = 11,
    RSV_VCL_N12 = 12,
    RSV_VCL_R13 = 13,
    RSV_VCL_N14 = 14,
    RSV_VCL_R15 = 15,
    BLA_W_LP = 16,
    BLA_W_RADL = 17,
    BLA_N_LP = 18,
    IDR_W_RADL = 19,
    IDR_N_LP = 20,
    CRA_NUT = 21,
    RSV_IRAP_VCL22 = 22,
    RSV_IRAP_VCL23 = 23,
    RSV_VCL24 = 24,
    RSV_VCL25 = 25,
    RSV_VCL26 = 26,
    RSV_VCL27 = 27,
    RSV_VCL28 = 28,
    RSV_VCL29 = 29,
    RSV_VCL30 = 30,
    RSV_VCL31 = 31,
    VPS_NUT = 32,
    SPS_NUT = 33,
    PPS_NUT = 34,
    AUD_NUT = 35,
    EOS_NUT = 36,
    EOB_NUT = 37,
    FD_NUT = 38,
    PREFIX_SEI_NUT = 39,
    SUFFIX_SEI_NUT = 40,
    RSV_NVCL41 = 41,
    RSV_NVCL42 = 42,
    RSV_NVCL43 = 43,
    RSV_NVCL44 = 44,
    RSV_NVCL45 = 45,
    RSV_NVCL46 = 46,
    RSV_NVCL47 = 47,
    NAL_UNKNOWN = 0xFF
};

enum SliceType
{
    SLICE_TYPE_UNKNOWN = -1,
    SLICE_TYPE_P = 0,
    SLICE_TYPE_B,
    SLICE_TYPE_I,
    SLICE_TYPE_SP,
    SLICE_TYPE_SI,
    SLICE_TYPE_EXT_P,
    SLICE_TYPE_EXT_B,
    SLICE_TYPE_EXT_I,
    SLICE_TYPE_EXT_SP,
    SLICE_TYPE_EXT_SI
};

typedef enum
{
    NVGST_AUTOPLUG_SELECT_TRY = 0,
    NVGST_AUTOPLUG_SELECT_EXPOSE,
    NVGST_AUTOPLUG_SELECT_SKIP
} NvGstAutoplugSelectResult;

struct media_info
{
    string codec;
    int    channel;
    int    frequency;
    int    codecData;
};

struct FrameSize
{
    FrameSize () : m_width(0), m_height(0)
    {}
    FrameSize (uint32_t w, uint32_t h) : m_width(w), m_height(h)
    {}
    void operator=(const FrameSize& size)
    {
        this->m_width = size.m_width;
        this->m_height = size.m_height;
    }
    int getPixels() { return m_width * m_height; }
    FrameSize minimum(FrameSize size)
    {
        if (this->getPixels() < size.getPixels())
        {
            return *this;
        }
        else
        {
            return size;
        }
    }
    uint32_t m_width;
    uint32_t m_height;
};

NaluType parseH264NaluType(const unsigned char *buffer, ssize_t size);
vector<uint8_t> getH26xMarker(const unsigned char *buffer);
int getH26xMarkerSize(const unsigned char *buffer);
vector<uint8_t> getDefaultH26xMarker();
std::vector<uint8_t> getUserDefinedSeiFrame(FrameInfoSeiPayload& frameInfo, const string& uuid, const string& codec);
std::vector<uint8_t> getUserDefinedSeiFrameFromJson(Json::Value& value, const string& uuid, const string& codec);
int64_t parseSeiFrameId(const unsigned char *buffer, ssize_t size, int64_t& pts_from_server, const string& codec);
H265NaluType parseH265NaluType(const unsigned char *buffer, ssize_t size);
void removeH264NalStartCodes(std::vector<uint8_t>& content);
int getMediaInformation (const string& filename, Json::Value &response, bool millisec = false);
int getMediaInformationUsingLibav(const string& filename, Json::Value &media_info, bool millisec = false);
SliceType parseH264SliceType(const unsigned char *buffer, ssize_t size);
SliceType parseH265SliceType(const unsigned char *buffer, ssize_t size, H265NaluType nal_type);
bool isValidDataNAL(const uint8_t& nalu_type, const string& codec);
bool isIDRFrame(const uint8_t& nalu_type, const string& codec);
vector<std::pair<NaluType, int>> getListOfNalUnits(std::vector<uint8_t>& content);
vector<std::pair<H265NaluType, int>> getListOfH265NalUnits(std::vector<uint8_t>& content);
int64_t getSeiIndex(std::vector<uint8_t>& content);
Json::Value getVideoMetadata(const string& file_path);
Json::Value getAudioMetadata(const string& file_path);
int getSecureRandomInt(int min, int max);

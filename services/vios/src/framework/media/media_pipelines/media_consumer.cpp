/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "media_consumer.h"
#include "logger.h"
#include "mm_utils.h"

bool IMediaDataConsumer::isSpsAvailable()
{
    return m_spsCfg.empty() ? false : true;
}
bool IMediaDataConsumer::isPpsAvailable()
{
    return m_ppsCfg.empty() ? false : true;
}

/* VPS is not being checked in case of H.265 as it is optional */
bool IMediaDataConsumer::isSpsPpsAvailable()
{
    return (isSpsAvailable () && isPpsAvailable ());
}

std::vector<uint8_t> IMediaDataConsumer::parseAndCreateFrame(FrameParams& params, bool *retIDR)
{
    std::vector<uint8_t> content;
    bool isIDR = false;
    if (params.m_buffer == nullptr)
    {
        LOG(info) << "Got null buffer, do not process it" << endl;
        goto _exit;
    }
    if (iequals(params.m_codec, "H264"))
    {
        NaluType nalu_type = parseH264NaluType(params.m_buffer, params.m_size);
        if (nalu_type == NaluType::kSps)
        {
            LOG(verbose2) << "LiveVideoSource:onData SPS" << endl;
            m_spsCfg.clear();
            m_spsCfg.insert(m_spsCfg.end(), params.m_buffer, params.m_buffer + params.m_size);
            goto _exit;
        }
        else if (nalu_type == NaluType::kPps)
        {
            LOG(verbose2) << "LiveVideoSource:onData PPS" << endl;
            m_ppsCfg.clear();
            m_ppsCfg.insert(m_ppsCfg.end(), params.m_buffer, params.m_buffer + params.m_size);
            goto _exit;
        }
        else if (nalu_type == NaluType::kSei)
        {
            LOG(verbose2) << "LiveVideoSource:onData SEI" << endl;
        }
        else if (nalu_type == NaluType::kAud)
        {
        }
        else if (nalu_type == NaluType::kNalUnknown)
        {
        }
        else
        {
            isIDR = isIDRFrame(nalu_type, params.m_codec);
            if (nalu_type == NaluType::kSlice)
            {
                SliceType slice_type = parseH264SliceType(params.m_buffer, params.m_size);
                LOG(verbose2) << "SLICE Type:" << slice_type << endl;
                if (slice_type == SLICE_TYPE_I || slice_type == SLICE_TYPE_EXT_I)
                {
                    LOG(verbose2) << "Received I-slice" << endl;
                    isIDR = true;
                }
            }
            // For multi-slice IDR access units, only prepend SPS/PPS to the first
            // slice of the picture. Re-emitting parameter sets between slices of
            // the same access unit causes h264parse to split the AU, which then
            // makes the decoder produce green/incomplete frames.
            // first_mb_in_slice is the first ue(v) field of the slice header
            // (byte 5 after the 4-byte start code + 1-byte NAL header). When the
            // value is 0 it is encoded as a single '1' bit, so MSB == 1 means
            // this is the first slice of the picture.
            bool isFirstSliceInPic = false;
            if (params.m_size > 5)
            {
                isFirstSliceInPic = (params.m_buffer[5] & 0x80) != 0;
            }
            if (isIDR && isSpsPpsAvailable ())
            {
                /* Client has received IDR, now we can start sending frames to decoder */
                if (retIDR)
                {
                    *retIDR = true;
                }
                m_startConsuming = true;
                if (isFirstSliceInPic)
                {
                    LOG(verbose2) << "IDR Frame " << endl;
                    content.insert(content.end(), m_spsCfg.begin(), m_spsCfg.end());
                    content.insert(content.end(), m_ppsCfg.begin(), m_ppsCfg.end());
                }
            }
            content.insert(content.end(), params.m_buffer, params.m_buffer + params.m_size);
        }
    }
    else if (iequals(params.m_codec, "H265"))
    {
        H265NaluType nalu_type = parseH265NaluType(params.m_buffer, params.m_size);
        if (nalu_type == H265NaluType::SPS_NUT)
        {
            m_spsCfg.clear ();
            m_spsCfg.insert(m_spsCfg.end(), params.m_buffer, params.m_buffer + params.m_size);
        }
        else if (nalu_type == H265NaluType::PPS_NUT)
        {
            m_ppsCfg.clear ();
            m_ppsCfg.insert(m_ppsCfg.end(), params.m_buffer, params.m_buffer + params.m_size);
        }
        else if (nalu_type == H265NaluType::VPS_NUT)
        {
            m_vpsCfg.clear ();
            m_vpsCfg.insert(m_vpsCfg.end(), params.m_buffer, params.m_buffer + params.m_size);
        }
        else if (nalu_type == H265NaluType::PREFIX_SEI_NUT || nalu_type == H265NaluType::SUFFIX_SEI_NUT)
        {

        }
        else
        {
            isIDR = isIDRFrame(nalu_type, params.m_codec);
            // For multi-slice IDR access units (common with Hikvision/Axis HEVC
            // cameras), the same picture is delivered as several VCL NAL units,
            // each carrying nalu_type=IDR_W_RADL. We must prepend VPS/SPS/PPS
            // ONLY to the first slice of the picture; otherwise h265parse treats
            // each repeated parameter-set sequence as a new access-unit boundary
            // and the decoder ends up with orphan slices => green frames.
            // first_slice_segment_in_pic_flag is the MSB of the first byte of
            // the slice header, which sits right after the 4-byte start code
            // and the 2-byte HEVC NAL header (i.e. params.m_buffer[6]).
            bool isFirstSliceInPic = false;
            if (params.m_size > 6)
            {
                isFirstSliceInPic = (params.m_buffer[6] & 0x80) != 0;
            }
            if (isIDR && isSpsPpsAvailable ())
            {
                /* Client has received IDR, now we can start sending frames to decoder */
                if (retIDR)
                {
                    *retIDR = true;
                }
                m_startConsuming = true;
                if (isFirstSliceInPic)
                {
                    // Spec-mandated order is VPS, then SPS, then PPS (SPS
                    // references VPS via vps_id; PPS references SPS via sps_id).
                    content.insert(content.end(), m_vpsCfg.begin(), m_vpsCfg.end());
                    content.insert(content.end(), m_spsCfg.begin(), m_spsCfg.end());
                    content.insert(content.end(), m_ppsCfg.begin(), m_ppsCfg.end());
                }
            }
            else
            {
                LOG(verbose2) << "H265 SLICE NALU:" << nalu_type << endl;
            }
            content.insert(content.end(), params.m_buffer, params.m_buffer + params.m_size);
        }
    }
    else
    {
        LOG(warning) << "Unsupported video format" << endl;
    }
_exit:
    return content;
}

/*
 * SPDX-FileCopyrightText: Copyright (c) 2016-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/* This file contains amendments to the V4L2 headers made after the
 * supported kernel version and NVIDIA extensions.
 */

#ifndef __V4L2_NV_EXTENSIONS_H__
#define __V4L2_NV_EXTENSIONS_H__

/**
 * @file
 * <b>NVIDIA V4L2 API Extensions</b>
 *
 * @b Description: This file declares NVIDIA V4L2 extensions,
 * controls and structures.
 */

/**
 *
 * @defgroup ee_extensions_group V4L2 NV Extensions API
 *
 * This file declares NVIDIA V4L2 extensions, controls, and structures.
 *
 */
/**
 * Defines V4L2 pixel format for DIVX.
 */
#define V4L2_PIX_FMT_DIVX4     v4l2_fourcc('D', 'V', 'X', '4')

#define V4L2_PIX_FMT_DIVX5     v4l2_fourcc('D', 'V', 'X', '5')
/**
 * Defines V4L2 pixel format for H.265.
 */
#define V4L2_PIX_FMT_H265     v4l2_fourcc('H', '2', '6', '5')

/**
 * Defines the V4L2 pixel format for VP9.
 */
#define V4L2_PIX_FMT_VP9      v4l2_fourcc('V', 'P', '9', '0')

/**
 * Defines the V4L2 pixel format for AV1.
 */
#define V4L2_PIX_FMT_AV1      v4l2_fourcc('A', 'V', '1', '0')

/**
 * Defines the V4L2 pixel format for representing single plane 10-bit Y/CbCr 4:2:0 decoder data.
 */
#define V4L2_PIX_FMT_P010    v4l2_fourcc('P', '0', '1', '0') /* Y/CbCr 4:2:0, 10 bits per channel */

/**
 * Defines the V4L2 pixel format for representing semi-planar 10-bit Y/CbCr 4:2:0 decoder data.
 */
#define V4L2_PIX_FMT_P010M   v4l2_fourcc('P', 'M', '1', '0') /* Y/CbCr 4:2:0, 10 bits per channel */

/**
 * Defines the V4L2 pixel format for representing single plane 12-bit Y/CbCr 4:2:0 decoder data.
 */
#define V4L2_PIX_FMT_P012    v4l2_fourcc('P', '0', '1', '2') /* Y/CbCr 4:2:0, 12 bits per channel */

/**
 * Defines the V4L2 pixel format for representing semi-planar 12-bit Y/CbCr 4:2:0 decoder data.
 */
#define V4L2_PIX_FMT_P012M   v4l2_fourcc('P', 'M', '1', '2') /* Y/CbCr 4:2:0, 12 bits per channel */

/**
 * Defines the V4L2 pixel format for representing semi-planar 8-bit Y/CbCr 4:4:4 decoder data.
 */
#define V4L2_PIX_FMT_NV24M   v4l2_fourcc('N', 'M', '2', '4') /* Y/CbCr 4:4:4, 8 bits per channel */

/**
 * Defines the V4L2 pixel format for representing semi-planar 10-bit Y/CbCr 4:4:4 decoder data.
 */
#define V4L2_PIX_FMT_NV24_10LE   v4l2_fourcc('N', 'V', '1', '0') /* Y/CbCr 4:4:4, 10 bits per channel */

/**
 * Defines the V4L2 pixel format for representing planar 10-bit Y/CbCr 4:4:4 decoder data.
 */
#define V4L2_PIX_FMT_YUV444_10LE   v4l2_fourcc('P', '4', '1', '0') /* Y/Cb/Cr 4:4:4, 10 bits per channel */

/**
 * Defines the V4L2 pixel format for representing planar 12-bit Y/CbCr 4:4:4 decoder data.
 */
#define V4L2_PIX_FMT_YUV444_12LE   v4l2_fourcc('P', '4', '1', '2') /* Y/Cb/Cr 4:4:4, 12 bits per channel */

/**
 * Defines the V4L2 pixel format for representing semi-planar 16-bit Y/CbCr 4:2:2 decoder data.
 */
#define V4L2_PIX_FMT_P216M   v4l2_fourcc('P', '2', '1', '6') /* Y/CbCr 4:2:2, 16 bits per channel */

/** @cond UNUSED */
/* >> The declarations from here to the next endcond statement are not
 * >> currently implemented. DO NOT USE. */

#define V4L2_PIX_FMT_YUV422RM v4l2_fourcc('4', '2', 'R', 'M')


#define V4L2_PIX_FMT_H264_SLICE v4l2_fourcc('S', '2', '6', '4') /** H264 parsed slices. */
#define V4L2_PIX_FMT_VP8_FRAME v4l2_fourcc('V', 'P', '8', 'F') /** VP8 parsed frames. */

#define V4L2_CTRL_FLAG_CAN_STORE    0x0200

/** @endcond */

/**
 * Defines the V4L2 event type for decoder resolution event change.
 */
#define V4L2_EVENT_RESOLUTION_CHANGE        5

/** @cond UNUSED */
/* >> The declarations from here to the next endcond statement are not
 * >> currently implemented. DO NOT USE. */

/*---------------Below are changes from the v4l2-controls.h----------------------*/

#define V4L2_CID_MPEG_VIDEO_H264_SPS        (V4L2_CID_MPEG_BASE+383)
#define V4L2_CID_MPEG_VIDEO_H264_PPS        (V4L2_CID_MPEG_BASE+384)
#define V4L2_CID_MPEG_VIDEO_H264_SCALING_MATRIX (V4L2_CID_MPEG_BASE+385)
#define V4L2_CID_MPEG_VIDEO_H264_SLICE_PARAM    (V4L2_CID_MPEG_BASE+386)
#define V4L2_CID_MPEG_VIDEO_H264_DECODE_PARAM   (V4L2_CID_MPEG_BASE+387)

#define V4L2_CID_MPEG_VIDEO_VP8_FRAME_HDR       (V4L2_CID_MPEG_BASE+512)

/** @endcond */

/**
 * Defines the control ID to set the H.265 encoder profile.
 *
 * A v4l2_mpeg_video_h265_profile must be passed.
 */
#define V4L2_CID_MPEG_VIDEO_H265_PROFILE        (V4L2_CID_MPEG_BASE+513)

/**
 * Defines the control ID to set the encoder IDR frame interval.
 * Must be used with \c VIDIOC_S_EXT_CTRLS IOCTL.
 */
#define V4L2_CID_MPEG_VIDEO_IDR_INTERVAL        (V4L2_CID_MPEG_BASE+514)

/** @cond UNUSED */
/* >> The declarations from here to the next endcond statement are not
 * >> currently implemented. DO NOT USE. */

/* Complex controls */

#define V4L2_H264_SPS_CONSTRAINT_SET0_FLAG          0x01
#define V4L2_H264_SPS_CONSTRAINT_SET1_FLAG          0x02
#define V4L2_H264_SPS_CONSTRAINT_SET2_FLAG          0x04
#define V4L2_H264_SPS_CONSTRAINT_SET3_FLAG          0x08
#define V4L2_H264_SPS_CONSTRAINT_SET4_FLAG          0x10
#define V4L2_H264_SPS_CONSTRAINT_SET5_FLAG          0x20

#define V4L2_H264_SPS_FLAG_SEPARATE_COLOUR_PLANE        0x01
#define V4L2_H264_SPS_FLAG_QPPRIME_Y_ZERO_TRANSFORM_BYPASS  0x02
#define V4L2_H264_SPS_FLAG_DELTA_PIC_ORDER_ALWAYS_ZERO      0x04
#define V4L2_H264_SPS_FLAG_GAPS_IN_FRAME_NUM_VALUE_ALLOWED  0x08
#define V4L2_H264_SPS_FLAG_FRAME_MBS_ONLY           0x10
#define V4L2_H264_SPS_FLAG_MB_ADAPTIVE_FRAME_FIELD      0x20
#define V4L2_H264_SPS_FLAG_DIRECT_8X8_INFERENCE         0x40

#define V4L2_H264_PPS_FLAG_ENTROPY_CODING_MODE              0x0001
#define V4L2_H264_PPS_FLAG_BOTTOM_FIELD_PIC_ORDER_IN_FRAME_PRESENT  0x0002
#define V4L2_H264_PPS_FLAG_WEIGHTED_PRED                0x0004
#define V4L2_H264_PPS_FLAG_DEBLOCKING_FILTER_CONTROL_PRESENT        0x0008
#define V4L2_H264_PPS_FLAG_CONSTRAINED_INTRA_PRED           0x0010
#define V4L2_H264_PPS_FLAG_REDUNDANT_PIC_CNT_PRESENT            0x0020
#define V4L2_H264_PPS_FLAG_TRANSFORM_8X8_MODE               0x0040
#define V4L2_H264_PPS_FLAG_PIC_SCALING_MATRIX_PRESENT           0x0080

struct v4l2_h264_pred_weight_table {
    __u8 luma_log2_weight_denom;
    __u8 chroma_log2_weight_denom;
    struct v4l2_h264_weight_factors weight_factors[2];
};

#define V4L2_SLICE_FLAG_FIELD_PIC       0x01
#define V4L2_SLICE_FLAG_BOTTOM_FIELD        0x02
#define V4L2_SLICE_FLAG_DIRECT_SPATIAL_MV_PRED  0x04
#define V4L2_SLICE_FLAG_SP_FOR_SWITCH       0x08
struct v4l2_ctrl_h264_slice_param {
    /** Holds the size in bytes, including the header. */
    __u32 size;
    /** Holds the offset in bits to slice_data() from the beginning of this slice. */
    __u32 header_bit_size;

    __u16 first_mb_in_slice;
    __u8 slice_type;
    __u8 pic_parameter_set_id;
    __u8 colour_plane_id;
    __u16 frame_num;
    __u16 idr_pic_id;
    __u16 pic_order_cnt_lsb;
    __s32 delta_pic_order_cnt_bottom;
    __s32 delta_pic_order_cnt0;
    __s32 delta_pic_order_cnt1;
    __u8 redundant_pic_cnt;

    struct v4l2_h264_pred_weight_table pred_weight_table;
    /* Size in bits of dec_ref_pic_marking() syntax element. */
    __u32 dec_ref_pic_marking_bit_size;
    /* Size in bits of pic order count syntax. */
    __u32 pic_order_cnt_bit_size;

    __u8 cabac_init_idc;
    __s8 slice_qp_delta;
    __s8 slice_qs_delta;
    __u8 disable_deblocking_filter_idc;
    __s8 slice_alpha_c0_offset_div2;
    __s8 slice_beta_offset_div2;
    __u32 slice_group_change_cycle;

    __u8 num_ref_idx_l0_active_minus1;
    __u8 num_ref_idx_l1_active_minus1;
    /*  Entries on each list are indices
     *  into v4l2_ctrl_h264_decode_param.dpb[]. */
    __u8 ref_pic_list0[32];
    __u8 ref_pic_list1[32];

    __u8 flags;
};

struct v4l2_ctrl_h264_decode_param {
    __u32 num_slices;
    __u8 idr_pic_flag;
    __u8 nal_ref_idc;
    __s32 top_field_order_cnt;
    __s32 bottom_field_order_cnt;
    __u8 ref_pic_list_p0[32];
    __u8 ref_pic_list_b0[32];
    __u8 ref_pic_list_b1[32];
    struct v4l2_h264_dpb_entry dpb[16];
};

#define V4L2_VP8_SEGMNT_HDR_FLAG_ENABLED              0x01
#define V4L2_VP8_SEGMNT_HDR_FLAG_UPDATE_MAP           0x02
#define V4L2_VP8_SEGMNT_HDR_FLAG_UPDATE_FEATURE_DATA  0x04
struct v4l2_vp8_sgmnt_hdr {
    __u8 segment_feature_mode;

    __s8 quant_update[4];
    __s8 lf_update[4];
    __u8 segment_probs[3];

    __u8 flags;
};

#define V4L2_VP8_LF_HDR_ADJ_ENABLE  0x01
#define V4L2_VP8_LF_HDR_DELTA_UPDATE    0x02
struct v4l2_vp8_loopfilter_hdr {
    __u8 type;
    __u8 level;
    __u8 sharpness_level;
    __s8 ref_frm_delta_magnitude[4];
    __s8 mb_mode_delta_magnitude[4];

    __u8 flags;
};

struct v4l2_vp8_quantization_hdr {
    __u8 y_ac_qi;
    __s8 y_dc_delta;
    __s8 y2_dc_delta;
    __s8 y2_ac_delta;
    __s8 uv_dc_delta;
    __s8 uv_ac_delta;
    __u16 dequant_factors[4][3][2];
};

struct v4l2_vp8_entropy_hdr {
    __u8 coeff_probs[4][8][3][11];
    __u8 y_mode_probs[4];
    __u8 uv_mode_probs[3];
    __u8 mv_probs[2][19];
};

#define V4L2_VP8_FRAME_HDR_FLAG_EXPERIMENTAL        0x01
#define V4L2_VP8_FRAME_HDR_FLAG_SHOW_FRAME      0x02
#define V4L2_VP8_FRAME_HDR_FLAG_MB_NO_SKIP_COEFF    0x04
struct v4l2_ctrl_vp8_frame_hdr {
    /** 0: keyframe, 1: not a keyframe. */
    __u8 key_frame;
    __u8 version;

    /** Populated also if not a key frame. */
    __u16 width;
    __u8 horizontal_scale;
    __u16 height;
    __u8 vertical_scale;

    struct v4l2_vp8_sgmnt_hdr sgmnt_hdr;
    struct v4l2_vp8_loopfilter_hdr lf_hdr;
    struct v4l2_vp8_quantization_hdr quant_hdr;
    struct v4l2_vp8_entropy_hdr entropy_hdr;

    __u8 sign_bias_golden;
    __u8 sign_bias_alternate;

    __u8 prob_skip_false;
    __u8 prob_intra;
    __u8 prob_last;
    __u8 prob_gf;

    __u32 first_part_size;
    /**
     * Holds the offset in bits of the MB data in the first partition,
     * i.e. bit offset starting from first_part_offset.
     */
    __u32 first_part_offset;
    __u32 macroblock_bit_offset;

    __u8 num_dct_parts;
    __u32 dct_part_sizes[8];

    __u8 bool_dec_range;
    __u8 bool_dec_value;
    __u8 bool_dec_count;

    /** Holds the v4l2_buffer index of the last reference frame. */
    __u32 last_frame;
     /** Holds the v4l2_buffer index of the golden reference frame. */
   __u32 golden_frame;
    /** Holds the v4l2_buffer index of the alt reference frame. */
    __u32 alt_frame;

    __u8 flags;
};

/** @endcond */

/*---------------Add below NVIDIA specific extensions ----------------------*/

/**
 * @defgroup V4L2Dec V4L2 Video Decoder
 *
 * @brief NVIDIA V4L2 Video Decoder Description and Extensions
 *
 * The video decoder device node is
 *
 *     /dev/nvhost-nvdec
 *
 * ### Supported Pixel Formats
 * OUTPUT PLANE       | CAPTURE PLANE        |        PLATFORM
 * :----------------: | :----------------:   | :--------------------:
 * V4L2_PIX_FMT_H264  | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_H265  | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_VP8   | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_VP9   | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_MPEG1 | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_MPEG2 | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_MPEG4 | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_DIVX4 | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_DIVX5 | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_JPEG  | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_MJPEG | V4L2_PIX_FMT_NV12M   | T210, T186, T194, T234
 * V4L2_PIX_FMT_AV1   | V4L2_PIX_FMT_NV12M   | T234
 *
 * ### Supported Memory Types
 * MEMORY               | OUTPUT PLANE | CAPTURE PLANE
 * :------------------: | :----------: | :-----------:
 * V4L2_MEMORY_MMAP     | Y            | Y
 * V4L2_MEMORY_DMABUF   | N            | Y
 * V4L2_MEMORY_USERPTR  | Y            | N
 *
 * ### Supported Controls
 * - #V4L2_CID_MPEG_VIDEO_DISABLE_COMPLETE_FRAME_INPUT
 * - #V4L2_CID_MPEG_VIDEO_DISABLE_DPB
 * - #V4L2_CID_MPEG_VIDEO_ERROR_REPORTING
 * - #V4L2_CID_MPEG_VIDEO_SKIP_FRAMES
 * - V4L2_CID_MIN_BUFFERS_FOR_CAPTURE (Get the minimum buffers to be allocated on capture plane.
 * Read only. Valid after #V4L2_EVENT_RESOLUTION_CHANGE)
 * - #V4L2_CID_MPEG_VIDEODEC_INPUT_METADATA
 * - #V4L2_CID_MPEG_VIDEODEC_METADATA
 * - #V4L2_CID_MPEG_VIDEO_CUDA_MEM_TYPE
 * - #V4L2_CID_MPEG_VIDEO_CUDA_GPU_ID
 * - #V4L2_CID_MPEG_VIDEODEC_DROP_FRAME_INTERVAL
 * - #V4L2_CID_MPEG_VIDEODEC_GDR_STREAM
 * - #V4L2_CID_MPEG_VIDEODEC_OPERATING_POINT
 * - #V4L2_CID_MPEG_VIDEODEC_ENABLE_MVC
 *
 * ### Supported Events
 * Event                         | Purpose
 * ----------------------------- | :----------------------------:
 * #V4L2_EVENT_RESOLUTION_CHANGE | Resolution of the stream has changed.
 *
 * ### Handling Resolution Change Events
 * When the decoder generates a \c V4L2_EVENT_RESOLUTION_CHANGE event, the
 * application calls \c STREAMOFF on the capture plane to tell the decoder to
 * deallocate the current buffers by calling REQBUF with count zero, get
 * the new capture plane format, and then proceed with setting up the buffers
 * for the capture plane.
 *
 * In case of decoder, the buffer format might differ from the display resolution.
 * The application must use \c VIDIOC_G_CROP to get the display resolution.
 *
 * ### EOS Handling
 * The following sequence must be followed for sending EOS and recieving EOS
 * from the decoder.
 * -# Send EOS to decoder by queueing on the output plane a buffer with
 * bytesused = 0 for the 0th plane (`v4l2_buffer.m.planes[0].bytesused = 0`).
 * -# Dequeues buffers on the output plane until it gets a buffer with bytesused = 0
 * for the 0th plane (`v4l2_buffer.m.planes[0].bytesused == 0`)
 * -# Dequeues buffers on the capture plane until it gets a buffer with bytesused = 0
 * for the 0th plane.
 *
 * ### Decoder Input Frame Metadata
 * Decoder supports reporting stream header parsing error info as input frame metadata.
 * See \c V4L2_CID_MPEG_VIDEO_ERROR_REPORTING, \c V4L2_CID_MPEG_VIDEODEC_INPUT_METADATA
 * and \c v4l2_ctrl_video_metadata for more information.
 *
 * ### Decoder Output Frame Metadata
 * Decoder supports reporting frame related metadata, including error reports and
 * DPB info. See \c V4L2_CID_MPEG_VIDEO_ERROR_REPORTING, \c V4L2_CID_MPEG_VIDEODEC_METADATA
 * and \c v4l2_ctrl_video_metadata for more information.
 *
 * @note Currently, V4L2 plugins do not support odd resolution.
 * @{
 * @ingroup ee_extensions_group
 */

/**
 * Defines the Control ID to indicate to the decoder that the input
 * buffers do not contain complete buffers.
 *
 * @note This control must be set in case of frames containing multiple slices
 * when the input buffers do not contain all the slices of the frame.
 *
 * A boolean value must be supplied with this control.
 *
 */
#define V4L2_CID_MPEG_VIDEO_DISABLE_COMPLETE_FRAME_INPUT (V4L2_CID_MPEG_BASE+515)

/**
 * Defines the Control ID to disable decoder DPB management.
 *
 * @note This only works for streams having a single reference frame.
 *
 * No addtional parameter should be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_DISABLE_DPB (V4L2_CID_MPEG_BASE+516)

/**
 * Defines the Control ID to enable decoder error and metadata reporting.
 *
 * No additional parameter should be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_ERROR_REPORTING (V4L2_CID_MPEG_BASE+517)

/**
 * Defines the Control ID to set the skip frames property of the decoder.
 *
 * Decoder must be configured to skip certain types of frames. One
 * \c v4l2_skip_frames_type must be passed.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 * This control ID is supported only for H264.
 */
#define V4L2_CID_MPEG_VIDEO_SKIP_FRAMES (V4L2_CID_MPEG_BASE+518)

/**
 * Defines the Control ID to get the decoder output metadata.
 *
 * @note Metadata reporting must be enabled using
 * #V4L2_CID_MPEG_VIDEO_ERROR_REPORTING IOCTL for this.
 *
 * A pointer to a valid \c v4l2_ctrl_video_metadata structure must be supplied
 * with this control.
 *
 * @attention This control must be read after dequeueing a buffer successfully from
 * the capture plane. The values in the structure are valid until the buffer is queued
 * again.
 */
#define V4L2_CID_MPEG_VIDEODEC_METADATA (V4L2_CID_MPEG_BASE+519)

/**
 * Defines the Control ID to get the decoder input header error metadata.
 *
 * @note Metadata reporting must be enabled using
 * #V4L2_CID_MPEG_VIDEO_ERROR_REPORTING IOCTL for this.
 *
 * A pointer to a valid \c v4l2_ctrl_video_metadata structure must be supplied
 * with this control.
 *
 * @attention This control must be read after dequeueing a buffer successfully from
 * the output plane. The values in the structure are valid until the buffer is queued
 * again.
 */
#define V4L2_CID_MPEG_VIDEODEC_INPUT_METADATA (V4L2_CID_MPEG_BASE+520)

/**
 * Defines the Control ID to check if display data is present.
 *
 * This control returns true if HDR metadata is present in the stream.
 *
 */
#define V4L2_CID_VIDEODEC_DISPLAYDATA_PRESENT (V4L2_CID_MPEG_BASE+521)

/**
 * Defines the Control ID to get display data if V4L2_CID_VIDEODEC_DISPLAYDATA_PRESENT returns true.
 *
 * This control returns display data such as display_primaries, white_point and
 * display_parameter_luminance required for display module.
 *
 */
#define V4L2_CID_VIDEODEC_HDR_MASTERING_DISPLAY_DATA (V4L2_CID_MPEG_BASE+522)

/**
 * Defines the Control ID to get Sample Aspect Ratio width for decoding.
 *
 * This control returns unsigned integer of Sample Aspect Ratio width.
 *
 * @attention This control must be set after receiving V4L2_EVENT_RESOLUTION_CHANGE.
 *
 */
#define V4L2_CID_MPEG_VIDEODEC_SAR_WIDTH (V4L2_CID_MPEG_BASE+580)

/**
 * Defines the Control ID to get Sample Aspect Ratio height for decoding.
 *
 * This control returns unsigned integer of Sample Aspect Ratio height.
 *
 * @attention This control must be set after receiving V4L2_EVENT_RESOLUTION_CHANGE.
 *
 */
#define V4L2_CID_MPEG_VIDEODEC_SAR_HEIGHT (V4L2_CID_MPEG_BASE+581)

 /**
 * Defines the Control ID to embed the SEI data coming from upstream plugins.
 *
 * This control sets the SEI payload size and SEI data pointer for encoder.
 *
 * @attention This control must be set after receiving V4L2_EVENT_RESOLUTION_CHANGE.
 *
 */
 #define V4L2_CID_MPEG_VIDEOENC_DS_SEI_DATA (V4L2_CID_MPEG_BASE+571)
 
/**
* Defines the Control ID to enable GDR stream playback.
*
* A boolean value must be supplied with this control.
*
* @attention This control must be set after setting formats on both the planes
* and before requesting buffers on either plane.
*/
#define V4L2_CID_MPEG_VIDEODEC_GDR_STREAM (V4L2_CID_MPEG_BASE+591)

/**
* Defines the Control ID to set SEI UUID
*
* A string of size 16 bytes to be suppplied with this control
*
* @attention This control must be set after setting formats on both the planes
* and before requesting buffers on either plane.
*/
#define V4L2_CID_MPEG_VIDEOENC_DS_SEI_UUID (V4L2_CID_MPEG_BASE+594)

/**
* Defines the Control ID to set the AV1 decoder operating point.
*
* A int value must be supplied with this control.
*
* @attention This control must be set after setting formats on both the planes
* and before requesting buffers on either plane.
*/
#define V4L2_CID_MPEG_VIDEODEC_OPERATING_POINT (V4L2_CID_MPEG_BASE+595)

/**
* Defines the Control ID to enable the AV1 decoder MVC feature.
*
* This will enable the AV1 MVC feature
*
* @attention This control must be set after setting formats on both the planes
* and before requesting buffers on either plane.
*/
#define V4L2_CID_MPEG_VIDEODEC_ENABLE_MVC (V4L2_CID_MPEG_BASE+596)

/**
 * Defines the Control ID to get number of operating points in AV1 decoder stream.
 *
 * This control returns unsigned integer of number of operating points.
 *
 * @attention This control must be set after receiving V4L2_EVENT_SVC_DATA.
 *
 */
#define V4L2_CID_MPEG_VIDEODEC_NUM_OPERATING_POINTS (V4L2_CID_MPEG_BASE+597)

/** @} */

/**
 * @defgroup V4L2Conv V4L2 Video Converter
 *
 * @brief NVIDIA V4L2 Video Converter Description and Extensions
 *
 * Use the video converter for color space conversion, scaling, and
 * conversion between hardware buffer memory (\c V4L2_MEMORY_MMAP/\c
 * V4L2_MEMORY_DMABUF), software buffer memory (\c V4L2_MEMORY_USERPTR), and
 * other operations such as cropping, flipping/rotating, and
 * temporal noise reduction (TNR).
 * The video converter device node is \c "/dev/nvhost-vic".
 *
 * ### Supported Pixelformats
 *  PIXEL FORMAT           | PIXEL FORMAT
 * :---------------------: | :--------------:
 * V4L2_PIX_FMT_YUV444M    | V4L2_PIX_FMT_YVU422M
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_YVU420M
 * V4L2_PIX_FMT_NV12M      | V4L2_PIX_FMT_GREY
 * V4L2_PIX_FMT_YUYV       | V4L2_PIX_FMT_YVYU
 * V4L2_PIX_FMT_UYVY       | V4L2_PIX_FMT_VYUY
 * V4L2_PIX_FMT_ABGR32     | V4L2_PIX_FMT_XBGR32
 *
 * ### Supported Pixel Formats for TNR
 *  PIXEL FORMAT           | PIXEL FORMAT
 * :---------------------: | :--------------:
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_NV12M
 * V4L2_PIX_FMT_UYVY       | V4L2_PIX_FMT_YUYV
 *
 * ### Supported Memory Types
 * MEMORY               | OUTPUT PLANE | CAPTURE PLANE
 * :------------------: | :----------: | :-----------:
 * V4L2_MEMORY_MMAP     | Y            | Y
 * V4L2_MEMORY_DMABUF   | Y            | Y
 * V4L2_MEMORY_USERPTR  | Y            | Y
 *
 * ### Supported Controls
 * - #V4L2_CID_VIDEO_CONVERT_OUTPUT_PLANE_LAYOUT
 * - #V4L2_CID_VIDEO_CONVERT_CAPTURE_PLANE_LAYOUT
 * - #V4L2_CID_VIDEO_CONVERT_FLIP_METHOD
 * - #V4L2_CID_VIDEO_CONVERT_INTERPOLATION_METHOD
 * - #V4L2_CID_VIDEO_CONVERT_TNR_ALGORITHM
 * - #V4L2_CID_VIDEO_CONVERT_YUV_RESCALE_METHOD
 *
 * ### Cropping
 * Video converter supports cropping using \c VIDIOC_S_SELECTION IOCTL with type
 * \c V4L2_BUF_TYPE_VIDEO_CAPTURE and target \c V4L2_SEL_TGT_CROP. This must
 * be set before requesting buffers on either plane.
 *
 * ### EOS Handling
 * The following sequence must be followed for sending EOS and recieving EOS
 * from the converter.
 * -# Send EOS to converter by queueing on the output plane a buffer with
 * bytesused = 0 for the 0th plane (`v4l2_buffer.m.planes[0].bytesused = 0`).
 * -# Dequeues buffers on the capture plane until it gets a buffer with bytesused = 0
 * for the 0th plane.
 *
 * @note Currently, V4L2 plugins do not support odd resolution.
 * @{
 * @ingroup ee_extensions_group
 */

/**
 * Defines the Control ID to set converter output plane buffer layout.
 *
 * A value of type \c v4l2_nv_buffer_layout must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on the output plane.
 */
#define V4L2_CID_VIDEO_CONVERT_OUTPUT_PLANE_LAYOUT   (V4L2_CID_MPEG_BASE+523)

/**
 * Defines the Control ID to set converter capture plane buffer layout.
 *
 * A value of type \c v4l2_nv_buffer_layout must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on the capture plane.
 */
#define V4L2_CID_VIDEO_CONVERT_CAPTURE_PLANE_LAYOUT  (V4L2_CID_MPEG_BASE+524)

/**
 * Defines the Control ID to set the converter flip/rotation method.
 *
 * A value of type \c v4l2_flip_method must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on either plane.
 */
#define V4L2_CID_VIDEO_CONVERT_FLIP_METHOD           (V4L2_CID_MPEG_BASE+525)

/**
 * Defines the Control ID to set the converter interpolation method.
 *
 * A value of type \c v4l2_interpolation_method must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on either plane.
 */
#define V4L2_CID_VIDEO_CONVERT_INTERPOLATION_METHOD  (V4L2_CID_MPEG_BASE+526)

/**
 * Defines the Control ID to set the converter Temporal Noise Reduction (TNR) algorithm.
 *
 * A value of type \c v4l2_tnr_algorithm must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on either plane.
 * @attention TNR algorithms are not supported with YUV422 and YUV444 capture
 *            plane formats.
 */
#define V4L2_CID_VIDEO_CONVERT_TNR_ALGORITHM         (V4L2_CID_MPEG_BASE+527)
/** @} */

/**
 * @defgroup V4L2Enc V4L2 Video Encoder
 *
 * @brief NVIDIA V4L2 Video Encoder Description and Extensions
 *
 * The video encoder device node is \c "/dev/nvhost-msenc".
 *
 * ### Supported Pixelformats
 * OUTPUT PLANE            | CAPTURE PLANE       |       PLATFORM
 * :---------------------: | :-----------------: | :--------------------:
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_H264   | T210, T186, T194, T234
 * V4L2_PIX_FMT_NV12M      |                     |
 * V4L2_PIX_FMT_YUV444M    |                     |
 * V4L2_PIX_FMT_NV24M      |                     |
 *                         |                     |
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_H265   | T210, T186, T194, T234
 * V4L2_PIX_FMT_NV12M      |                     |
 * V4L2_PIX_FMT_YUV444M    |                     |
 * V4L2_PIX_FMT_NV24M      |                     |
 * V4L2_PIX_FMT_P010M      |                     |
 * V4L2_PIX_FMT_NV24_10LE  |                     |
 *                         |                     |
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_VP8    | T210, T186
 * V4L2_PIX_FMT_NV12M      |                     |
 *                         |                     |
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_VP9    | T186, T194
 * V4L2_PIX_FMT_NV12M      |                     |
 *                         |                     |
 * V4L2_PIX_FMT_YUV420M    | V4L2_PIX_FMT_AV1    | T234
 * V4L2_PIX_FMT_NV12M      |                     |
 *
 * ### Supported Memory Types
 * MEMORY               | OUTPUT PLANE | CAPTURE PLANE
 * :------------------: | :----------: | :-----------:
 * V4L2_MEMORY_MMAP     | Y            | Y
 * V4L2_MEMORY_DMABUF   | Y            | N
 * V4L2_MEMORY_USERPTR  | N            | N
 * \attention For the video encoder, it is necessary that the capture plane
 *  format be set before the output plane format and only then request buffers on
 *  any of the planes.
 *
 * ### Supported Controls
 * The following sections describe the supported controls.
 *
 * #### Controls From the Open Source V4L2-Controls Header
 * Control ID                       | Purpose              | Runtime Configurable
 * -------------------------------- | -------------------- | :------------------:
 * V4L2_CID_MPEG_VIDEO_BITRATE      | Bitrate              | Y
 * V4L2_CID_MPEG_VIDEO_H264_PROFILE | H.264 Encode Profile | N
 * V4L2_CID_MPEG_VIDEO_BITRATE_MODE | Rate Control Mode    | N
 * V4L2_CID_MPEG_VIDEO_GOP_SIZE     | I-frame Interval     | N
 * V4L2_CID_MPEG_VIDEO_H264_LEVEL   | Encode Level         | N
 * V4L2_CID_MPEG_MFC51_VIDEO_FORCE_FRAME_TYPE | Force I-frame on one of queued output plane buffer | Y
 *
 * All non-runtime configurable options must be set after setting formats on
 * both the planes and before requesting buffers on either plane.
 *
 * The runtime configurable parameters can be called anytime after setting
 * formats on both the planes.
 *
 * #### NVIDIA-Specific Controls
 * - #V4L2_CID_MPEG_VIDEO_H265_PROFILE
 * - #V4L2_CID_MPEG_VIDEO_IDR_INTERVAL
 * - #V4L2_CID_MPEG_VIDEOENC_TEMPORAL_TRADEOFF_LEVEL
 * - #V4L2_CID_MPEG_VIDEOENC_SLICE_LENGTH_PARAM
 * - #V4L2_CID_MPEG_VIDEOENC_ROI_PARAMS
 * - #V4L2_CID_MPEG_VIDEOENC_VIRTUALBUFFER_SIZE
 * - #V4L2_CID_MPEG_VIDEOENC_NUM_REFERENCE_FRAMES
 * - #V4L2_CID_MPEG_VIDEOENC_SLICE_INTRAREFRESH_PARAM
 * - #V4L2_CID_MPEG_VIDEOENC_NUM_BFRAMES
 * - #V4L2_CID_MPEG_VIDEOENC_INSERT_SPS_PPS_AT_IDR
 * - #V4L2_CID_MPEG_VIDEOENC_METADATA
 * - #V4L2_CID_MPEG_VIDEOENC_METADATA_MV
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_METADATA_MV
 * - #V4L2_CID_MPEG_VIDEOENC_QP_RANGE
 * - #V4L2_CID_MPEG_VIDEOENC_HW_PRESET_TYPE_PARAM
 * - #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RPS_CONTROL
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RATE_CONTROL
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_ROI_PARAM
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_RECONCRC_PARAM
 * - #V4L2_CID_MPEG_VIDEOENC_INSERT_VUI
 * - #V4L2_CID_MPEG_VIDEOENC_INSERT_AUD
 * - #V4L2_CID_MPEG_VIDEOENC_EXTEDED_COLORFORMAT
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_ALLIFRAME_ENCODE
 * - #V4L2_CID_MPEG_VIDEOENC_H265_LEVEL
 * - #V4L2_CID_MPEG_VIDEOENC_ENABLE_SLICE_LEVEL_ENCODE
 * - #V4L2_CID_MPEG_VIDEOENC_POC_TYPE
 * - #V4L2_CID_MPEG_VIDEOENC_H265_VUI_EXT_SAR_WIDTH
 * - #V4L2_CID_MPEG_VIDEOENC_H265_VUI_EXT_SAR_HEIGHT
 * - #V4L2_CID_MPEG_VIDEOENC_FORCE_INTRA_FRAME
 * - #V4L2_CID_MPEG_VIDEOENC_FORCE_IDR_FRAME
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_HEADERS_WITH_FRAME
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_TILE_CONFIGURATION
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_SSIMRDO
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_DISABLE_CDF_UPDATE
 * - #V4L2_CID_MPEG_VIDEOENC_PPE_INIT_PARAMS
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_ERR_RESILIENT_MODE
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_FRAMEID_NUMBERS
 * - #V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_TILE_GROUPS
 *
 * #### Setting Framerate
 * The encoder framerate can be set with \c VIDIOC_S_PARM IOCTL by setting the numerator
 * and denominator in `v4l2_streamparm.parm.output.timeperframe`.
 *
 * ### Supported Encoder Profiles
 * #### H.264
 * - V4L2_MPEG_VIDEO_H264_PROFILE_MAIN
 * - V4L2_MPEG_VIDEO_H264_PROFILE_BASELINE
 * - V4L2_MPEG_VIDEO_H264_PROFILE_HIGH
 *
 * #### H.265
 * - V4L2_MPEG_VIDEO_H265_PROFILE_MAIN
 * - V4L2_MPEG_VIDEO_H265_PROFILE_MAIN10
 *
 * ### Encoder Output Metadata
 * The encoder supports reporting frame related metadata, including motion vectors
 * for that frame. See \c V4L2_CID_MPEG_VIDEOENC_METADATA,
 * \c V4L2_CID_MPEG_VIDEOENC_METADATA_MV and \c V4L2_CID_MPEG_VIDEOENC_ENABLE_METADATA_MV
 * for more information.
 *
 * ### EOS Handling
 * The following sequence must be followed for sending EOS and recieving EOS
 * from the encoder.
 * -# Send EOS to encoder by queueing on the output plane a buffer with
 * bytesused = 0 for the 0th plane (`v4l2_buffer.m.planes[0].bytesused = 0`).
 * -# Dequeues buffers on the capture plane until it gets a buffer with bytesused = 0
 * for the 0th plane.
 *
 * @note Currently, V4L2 plugins do not support odd resolution.
 * @{
 * @ingroup ee_extensions_group
 */


/**
 * Defines the Control ID to configure encoder to drop frames while encoding.
 *
 * A value of type \c v4l2_enc_temporal_tradeoff_level_type must be supplied
 * with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_TEMPORAL_TRADEOFF_LEVEL (V4L2_CID_MPEG_BASE+528)

/**
 * Defines the Control ID to configure encoder slice length either in terms of MBs or bits.
 *
 * A pointer to a valid \c v4l2_enc_slice_length_param structure must be supplied
 * with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_SLICE_LENGTH_PARAM (V4L2_CID_MPEG_BASE+529)

/**
 * Defines the Control ID to configure encoder to encode particular region of frame in high
 * quality.
 *
 * A pointer to a valid \c v4l2_enc_frame_ROI_params structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_ROI_PARAMS (V4L2_CID_MPEG_BASE+530)

/**
 * Defines the Control ID to specify virtual buffer size in bits for encoder.
 *
 * A pointer to a valid \c v4l2_enc_virtual_buffer_size structure must be
 * supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_VIRTUALBUFFER_SIZE (V4L2_CID_MPEG_BASE+531)

/**
 * Defines the Control ID to specify maximum number of reference frames that can be used.
 *
 * An integer value must be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_NUM_REFERENCE_FRAMES (V4L2_CID_MPEG_BASE+532)

/**
 * Defines the Control ID to specify the encoder slice intra refresh interval.
 *
 * A pointer to a valid
 * \c v4l2_enc_slice_intrarefresh_param structure for non-CUDA usecase and
 * \c v4l2_ctrl_intra_refresh structure for CUDA usecase
 * must be supplied with the control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_SLICE_INTRAREFRESH_PARAM (V4L2_CID_MPEG_BASE+533)

/**
 * Defines the Control ID to set number of B frames to be encoded between two P frames.
 *
 * This works with H.264 encoder. This also works with H.265 encoder for Jetson Xavier and
 * Jetson Xavier NX platforms. An integer value must be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_NUM_BFRAMES (V4L2_CID_MPEG_BASE+534)

/**
 * Defines the Control ID to enable/disable inserting SPS and PPS explicitly at IDR interval.
 *
 * A boolean value must be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_INSERT_SPS_PPS_AT_IDR (V4L2_CID_MPEG_BASE+535)

/**
 * Defines the Control ID to get encoder output metadata.
 *
 * A pointer to valid #v4l2_ctrl_video_metadata structure must be supplied with
 * this control.
 *
 * @attention This control must be read after dequeueing a buffer successfully from
 * the capture plane. The values in the structure are valid until the buffer is queued
 * again.
 */
#define V4L2_CID_MPEG_VIDEOENC_METADATA               (V4L2_CID_MPEG_BASE+536)

/**
 * Defines the Control ID to enable encoder motion vector reporting.
 *
 * No addtional parameter should be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_METADATA_MV     (V4L2_CID_MPEG_BASE+537)

/**
 * Defines the Control ID to get encoder output motion vector metadata.
 *
 * A pointer to valid \c v4l2_ctrl_videoenc_outputbuf_metadata_MV structure must
 * be supplied with this control.
 *
 * @attention This control must be read after dequeueing a buffer successfully from
 * the capture plane. The values in the structure are valid until the buffer is queued
 * again.
 */
#define V4L2_CID_MPEG_VIDEOENC_METADATA_MV            (V4L2_CID_MPEG_BASE+538)

/**
 * Defines the Control ID to set QP range for I/P/B frames.
 *
 * A pointer to a valid \c v4l2_ctrl_video_qp_range structure must
 * be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_QP_RANGE               (V4L2_CID_MPEG_BASE+539)

/**
 * Defines the Control ID to set encoder HW Preset type.
 *
 * A pointer to valid #v4l2_enc_hw_preset_type_param structure must
 * be supplied with this control.
 *
 * For CUDA usecases, tuning info is set to NV_ENC_TUNING_INFO_LOW_LATENCY.
 * It is recommended not to change the tuning info if \c v4l2_enc_hw_preset_type
 * definitions are used. If more control is needed for presets and tuning info,
 * use #V4L2_CID_MPEG_VIDEOENC_CUDA_PRESET_ID and #V4L2_CID_MPEG_VIDEOENC_CUDA_TUNING_INFO.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_HW_PRESET_TYPE_PARAM   (V4L2_CID_MPEG_BASE+540)

/**
 * Defines the Control ID to provide input metadata for encoder buffer.
 *
 * A pointer to valid #v4l2_ctrl_videoenc_input_metadata structure must be
 * supplied with this control.
 *
 * @attention This control must be called before queueing a buffer on the output
 * plane. Use the bitwise OR of v4l2_enc_input_metadata_param in the
 * v4l2_ctrl_videoenc_input_metadata.metadata_flag to provide different input
 * metadata parameters in one s_ctrl call.
 */
#define V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA         (V4L2_CID_MPEG_BASE+541)

/**
 * Defines the Control ID to configure encoder for external RPS control.
 *
 * A pointer to a valid #v4l2_enc_enable_ext_rps_ctr structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes. The value for V4L2_CID_MPEG_VIDEOENC_NUM_REFERENCE_FRAMES, if being entered,
 * must be set after this control.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RPS_CONTROL (V4L2_CID_MPEG_BASE+542)

/**
 * Defines the Control ID to configure encoder for external rate control.
 *
 * A pointer to a valid #v4l2_enc_enable_ext_rate_ctr structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RATE_CONTROL (V4L2_CID_MPEG_BASE+543)

/**
 * Defines the Control ID to configure ROI encoding for a session.
 *
 * A pointer to a valid #v4l2_enc_enable_roi_param structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_ROI_PARAM (V4L2_CID_MPEG_BASE+544)

/**
 * Defines the Control ID to configure Reconstructed CRC for a session.
 *
 * A pointer to a valid #v4l2_enc_enable_reconcrc_param structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_RECONCRC_PARAM  (V4L2_CID_MPEG_BASE+545)

/**
 * Control ID to enable/disable inserting VUI in SPS.
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_INSERT_VUI (V4L2_CID_MPEG_BASE+546)

/**
 * Control ID to enable/disable inserting AUD(Access Unit Delimiter).
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_INSERT_AUD (V4L2_CID_MPEG_BASE+547)

/**
 * Control ID to enable/disable setting extended color format.
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane. Also this control should be
 * enabled/disabled only after V4L2_CID_MPEG_VIDEOENC_INSERT_VUI is set
 */
#define V4L2_CID_MPEG_VIDEOENC_EXTEDED_COLORFORMAT (V4L2_CID_MPEG_BASE+548)

/**
 * Control ID to select which NVDEC IP to decode.
 *
 * @note This functionality is currently being deprecated and no longer
 * functional.
 *
 * A v4l2_decode_instance_type should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_DECODE_INSTANCE (V4L2_CID_MPEG_BASE+549)
/**
 * Control ID to issue a pseudo POLL call on the fd opened in non blocking mode.
 *
 * A pointer to a valid #v4l2_ctrl_video_device_poll must be supplied with this control.
 *
 * @attention This should only be called when the Decoder or Encoder is opened with
 * O_NONBLOCK flag.
 */
#define V4L2_CID_MPEG_VIDEO_DEVICE_POLL (V4L2_CID_MPEG_BASE+550)

/**
 * Control ID to set/clear the polling interrupt mode. Useful when a POLL issued from the
 * application but wants the wait to be interrupted.
 *
 * A boolean value must be supplied with this control, True indicates polling interrupt shall be
 * enabled and it shall stay enabled (i.e calls to POLL will return immediately) until a call to
 * same control ID is made by passing a boolean 0 value.
 *
 * @attention This should only be called when the Decoder or Encoder is opened with
 * O_NONBLOCK flag.
 */
#define V4L2_CID_MPEG_SET_POLL_INTERRUPT (V4L2_CID_MPEG_BASE+551)

/**
 * Control ID to enable/disable setting rate control two pass CBR.
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_TWO_PASS_CBR (V4L2_CID_MPEG_BASE+552)

/**
 * Defines the Control ID to set the converter YUV Rescale method.
 *
 * A value of type \c v4l2_yuv_rescale_method must be supplied with this control.
 *
 * @attention This control must be set before requesting buffers on either plane.
 */
#define V4L2_CID_VIDEO_CONVERT_YUV_RESCALE_METHOD (V4L2_CID_MPEG_BASE+553)

/**
 * Control ID to enable maximum Performance.
 *
 * An integer value must be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_MAX_PERFORMANCE (V4L2_CID_MPEG_BASE+554)

/**
 * Control ID to enable/disable setting for all i-Frame encoding.
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_ALLIFRAME_ENCODE (V4L2_CID_MPEG_BASE+555)

/**
 * Defines the Control ID to set buf api to be used by decoder/encoder.
 *
 * Note: This Control ID is no longer supported.
 */
#define V4L2_CID_MPEG_VIDEO_BUF_API_TYPE (V4L2_CID_MPEG_BASE+556)

/**
 * Defines the Control ID to set cuda memory type to be used by decoder/encoder.
 *
 * This control can be used by the decoder to set the memory type for surfaces.
 * A value of \c v4l2_cuda_mem_type needs to be set with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_CUDA_MEM_TYPE (V4L2_CID_MPEG_BASE+557)

/**
 * Defines the Control ID to set GPU ID to be used by decoder/encoder.
 *
 * An integer value should be supplied with this control.
 *
 * @attention This control must be set before setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_CUDA_GPU_ID (V4L2_CID_MPEG_BASE+558)

/**
 * Defines the Control ID to set drop frames interval for decoder.
 *
 * An integer value should be supplied with this control. A value of "x"
 * indicates every "x"th frame should be given out from the decoder, rest shall
 * dropped after decoding.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEODEC_DROP_FRAME_INTERVAL (V4L2_CID_MPEG_BASE+559)

/**
 * Control ID to enable/disable setting for attaching VP8/9 headers.
 * Only to be used for VP8/9 pixel format not for H264/5.
 *
 * A boolean value should be supplied with this control.
 * If value is false headers will be disabled and true will enable the headers.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
 #define V4L2_CID_MPEG_VIDEOENC_VPX_HEADERS_WITH_FRAME (V4L2_CID_MPEG_BASE+560)

/**
 * Defines the control ID to set the H.265 encoder level.
 *
 * A v4l2_mpeg_video_h265_level must be passed.
 */
#define V4L2_CID_MPEG_VIDEOENC_H265_LEVEL (V4L2_CID_MPEG_BASE+561)

/**
 * Control ID to enable/disable slice level encode output.
 *
 * A boolean value should be supplied with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_SLICE_LEVEL_ENCODE (V4L2_CID_MPEG_BASE+562)

/**
 * Defines the Control ID to set Picture Order Count property in frames.
 *
 * This works only with H.264 encoder. An integer value must be supplied with this
 * control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_POC_TYPE (V4L2_CID_MPEG_BASE+563)

/**
 * Defines the Control ID to set Sample Aspect Ratio width for H265 VUI encoding.
 *
 * An integer value must be supplied with this control.
 * The VUI Sample Aspect Ratio indicator for H265 follows the standard enum defined for
 * v4l2_mpeg_video_h264_vui_sar_idc.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_H265_VUI_EXT_SAR_WIDTH (V4L2_CID_MPEG_BASE+564)

/**
 * Defines the Control ID to set Sample Aspect Ratio height for H265 VUI encoding.
 *
 * An integer value must be supplied with this control.
 * The VUI Sample Aspect Ratio indicator for H265 follows the standard enum defined
 * for v4l2_mpeg_video_h264_vui_sar_idc.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_H265_VUI_EXT_SAR_HEIGHT (V4L2_CID_MPEG_BASE+565)

/**
 * Defines the Control ID to force INTRA frame.
 *
 * This control can be used by encoder to force encoding an intra frame.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_FORCE_INTRA_FRAME (V4L2_CID_MPEG_BASE+566)

/**
 * Defines the Control ID to force IDR frame.
 *
 * This control can be used by encoder to force encoding an idr frame.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_FORCE_IDR_FRAME (V4L2_CID_MPEG_BASE+567)

 /**
 * Defines the Control ID to set low latency to be used by decoder.
 *
 * This control can be used by decoder to set low latency for streams having
 * I and IPPP frames.
 *
 * @attention This control must be set before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEO_CUDA_LOW_LATENCY (V4L2_CID_MPEG_BASE+568)

/**
 * Control ID to enable/disable setting for attaching IVF headers.
 * Only to be used for AV1 codec.
 *
 * A boolean value should be supplied with this control.
 * If value is false headers will be disabled and true will enable the headers.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_AV1_HEADERS_WITH_FRAME (V4L2_CID_MPEG_BASE+569)

/**
 * Defines the Control ID to configure AV1 tile for a session.
 *
 * A pointer to a valid #v4l2_enc_av1_tile_config structure must be supplied
 * with this control.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_AV1_TILE_CONFIGURATION (V4L2_CID_MPEG_BASE+570)

/**
 * Defines the Control ID to enable variance based SSIM RDO for AV1.
 *
 * A boolean value should be supplied with this control.
 * If value is false SSIM RDO is disabled else true when enabled.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_SSIMRDO (V4L2_CID_MPEG_BASE+571)

/**
 * Defines the Control ID to enable CDF update for AV1.
 *
 * A boolean value should be supplied with this control.
 * If value is true the CDF update in the symbol decoding process is disabled.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_AV1_DISABLE_CDF_UPDATE (V4L2_CID_MPEG_BASE+572)

/**
 * Defines the Control ID to set  total frames to encode.
 *
 * An integer value should be supplied with this control. A value of "x"
 * indicates the number of frames that are given to encoder for encoding.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_FRAMES_TO_ENCODE (V4L2_CID_MPEG_BASE+573)

/**
 * Defines the Control ID to configure initial QP parameters for a encoder session.
 *
 * A pointer to a valid #v4l2_ctrl_video_init_qp structure must be supplied
 * with this control.
 *
 **/
#define V4L2_CID_MPEG_VIDEOENC_INIT_FRAME_QP (V4L2_CID_MPEG_BASE+574)

/**
 * Defines the Control ID to enable lossless H.264/H.265 encoding.
 *
 * A boolean value must be supplied with this control. Default is 0.
 * Lossless encoding is supported only for YUV444 8/10-bit format.
 * @note This control must be set in case of H.264 YUV444 encoding as
 * it does not support lossy encoding.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_LOSSLESS (V4L2_CID_MPEG_BASE+575)

/**
 * Defines the Control ID to set chroma_factor_idc for H.265 encoding.
 *
 * An integer value must be supplied with this control. Default is 1, and
 * 3 for YUV444 8/10-bit format.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_H265_CHROMA_FACTOR_IDC (V4L2_CID_MPEG_BASE+576)

/**
 * Defines the Control ID to configure preprocessing enhancement for a session.
 *
 * A pointer to a valid #v4l2_enc_ppe_init_params structure must be supplied
 * with this control.
 *
 * @attention This control must be set after requesting buffers on both the
 * planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_PPE_INIT_PARAMS (V4L2_CID_MPEG_BASE+577)

 /**
 * Defines Control ID to configure PRESET id for CUVID Encoder
 *
 * An integer value between 1 to 7 should be supplied with this control.
 *
 * Check PRESET Guide for more details at
 * https://docs.nvidia.com/video-technologies/video-codec-sdk/nvenc-preset-migration-guide/index.html
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_CUDA_PRESET_ID (V4L2_CID_MPEG_BASE+578)

 /**
 * Defines Control ID to configure TUNING INFO id for CUVID Encoder
 *
 * An integer value between 1 to 4 should be supplied with this control.
 * 
 * Check PRESET Guide for more details at
 * https://docs.nvidia.com/video-technologies/video-codec-sdk/nvenc-preset-migration-guide/index.html
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_CUDA_TUNING_INFO (V4L2_CID_MPEG_BASE+579)

/** Defines Control ID to configure CONSTQP VALUE for CUVID Encoder
 *
 * An integer value between 0 to 51 should be supplied with this control.
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_CUDA_CONSTQP (V4L2_CID_MPEG_BASE+580)

/**
 * Defines the Control ID to disable Asymmetric Motion Partitions for H.265 encoding.
 *
 * A boolean value must be supplied with this control. Default is 0.
 * @note This control is supported only for Xavier.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_H265_DISABLE_AMP (V4L2_CID_MPEG_BASE + 581)

 /** Defines Control ID to configure FPS VALUE for CUVID Encoder
 *
 * A positive integer value should be supplied with this control.
 *
 * @attention This control is runtime configurable and can be called anytime after setting
 * formats on both the planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_RECONFIG_FPS (V4L2_CID_MPEG_BASE+582)

/** Defines Control ID to configure MAXBITRATE for CUVID Encoder
 *
 * A positive integer value should be supplied with this control.
 *
 * @attention This control is runtime configurable and can be called anytime after setting
 * formats on both the planes.
 */
#define V4L2_CID_MPEG_VIDEO_MAXBITRATE 		(V4L2_CID_MPEG_BASE+583)

/** Defines Control ID to configure VBV BUFFERSIZE for CUVID Encoder
 *
 * A positive integer value should be supplied with this control.
 *
 * @attention This control is runtime configurable and can be called anytime after setting
 * formats on both the planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_VBVBUFSIZE 		(V4L2_CID_MPEG_BASE+584)

/** Defines Control ID to configure VBV INITIAL DELAY for CUVID Encoder
 *
 * A positive integer value should be supplied with this control.
 *
 * @attention This control is runtime configurable and can be called anytime after setting
 * formats on both the planes.
 */
#define V4L2_CID_MPEG_VIDEOENC_VBVINIT 		(V4L2_CID_MPEG_BASE+585)

/** Defines Control ID to enable Spatial AQ for CUVID Encoder
 *
 * A positive integer value in the range 0-15 should be supplied with this control.
 * Default is 0 (automatic).
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_AQ 		(V4L2_CID_MPEG_BASE+586)

/** Defines Control ID to enable temporal AQ for CUVID Encoder
 *
 * A boolean value must be supplied with this control. Default is 0
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_ENABLE_TEMPORAL_AQ 		(V4L2_CID_MPEG_BASE+587)

/** Defines Control ID to configure target Quality for CUVID Encoder
 *
 * A positive integer value in the range 0-51 should be supplied with this control.
 * Default is 0 (automatic).
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_TARGET_QUALITY (V4L2_CID_MPEG_BASE+588)

#define V4L2_CID_MPEG_VIDEOENC_COPY_TIMESTAMP (V4L2_CID_MPEG_BASE+589)

#define V4L2_CID_MPEG_VIDEOENC_INTRA_REFRESH (V4L2_CID_MPEG_BASE+590)

/**
 * Defines the Control ID to disable SAO filter for HEVC.
 *
 * A boolean value should be supplied with this control.
 * If value is true, SAO filter for H265 encoding is disabled. Enabled by default
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_H265_DISABLE_SAO (V4L2_CID_MPEG_BASE + 592)

/** 
 * Defines the Control ID to enable/disable Error Resilient Mode for AV1.
 *
 * A boolean value should be supplied with this control.
 * Default is true.
 *
 * @attention This control should be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 **/
#define V4L2_CID_MPEG_VIDEOENC_AV1_ERR_RESILIENT_MODE (V4L2_CID_MPEG_BASE + 593)

/** Defines Control ID to enable FrameId Present flag for AV1
 *
 * A boolean value must be supplied with this control. Default is 0
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_FRAMEID_NUMBERS (V4L2_CID_MPEG_BASE + 594)

/** Defines Control ID to enable Tile groups for AV1
 *
 * A boolean value must be supplied with this control. Default is 0
 *
 * @attention This control must be set after setting formats on both the planes
 * and before requesting buffers on either plane.
 */
#define V4L2_CID_MPEG_VIDEOENC_AV1_ENABLE_TILE_GROUPS (V4L2_CID_MPEG_BASE + 598)

/** Defines Control ID to flush output plane buffers of cuvid decoder
 *
 * A boolean value must be supplied with this control. Default is 0
 *
 * @attention This control must be set once we receive flush event
 */
#define V4L2_CID_MPEG_VIDEODEC_FLUSH_BUFFERS (V4L2_CID_MPEG_BASE + 599)

/** @} */

/**
 * @defgroup V4L2Argus V4L2 Video Camera
 *
 * @brief NVIDIA V4L2 Camera Description and Extensions
 *
 * The camera device node is \c "/dev/video%d".
 *
 * ### Supported Pixelformats
 * CAPTURE PLANE
 * :---------------------
 * V4L2_PIX_FMT_NV12M
 *           -
 *
 * ### Supported Memory Types
 * MEMORY               | CAPTURE PLANE
 * :------------------: | :-----------:
 * V4L2_MEMORY_MMAP     | Y
 * V4L2_MEMORY_DMABUF   | Y
 * V4L2_MEMORY_USERPTR  | N
 * \attention For the camera, it is necessary that the capture plane
 *  format be set and only then request buffers.
 *
 * ### Supported Controls
 * The following sections describe the supported controls.
 *
 * #### Controls From the Open Source V4L2-Controls Header
 * Control ID                       | Purpose              | Runtime Configurable
 * -------------------------------- | -------------------- | :------------------:
 * - #V4L2_CID_3A_LOCK              | AWB/AE Lock          | Y
 *
 * All non-runtime configurable options must be set after setting format on
 * the capture planes and before requesting buffers.
 *
 *
 * ### NVIDIA-Specific Controls
 * - #V4L2_CID_ARGUS_AUTO_WHITE_BALANCE_MODE
 * - #V4L2_CID_ARGUS_SENSOR_MODE
 * - #V4L2_CID_ARGUS_DENOISE_STRENGTH
 * - #V4L2_CID_ARGUS_DENOISE_MODE
 * - #V4L2_CID_ARGUS_EE_STRENGTH
 * - #V4L2_CID_ARGUS_EE_MODE
 * - #V4L2_CID_ARGUS_AE_ANTIBANDING_MODE
 * - #V4L2_CID_ARGUS_ISP_DIGITAL_GAIN_RANGE
 * - #V4L2_CID_ARGUS_COLOR_SATURATION
 * - #V4L2_CID_ARGUS_GAIN_RANGE
 * - #V4L2_CID_ARGUS_EXPOSURE_TIME_RANGE
 * - #V4L2_CID_ARGUS_METADATA
 *
 * ### Setting Framerate
 * The camera framerate can be set with \c VIDIOC_S_PARM IOCTL by setting the numerator
 * and denominator in `v4l2_streamparm.parm.output.timeperframe`.
 * Selection of camera mode can override this setting. If the camera mode is to be
 * selected along with the specified framerate, then camera mode must be selected before
 * setting the framerate.
 *
 * ### Camera Capture Metadata
 * The camera can be queried to report frame related metadata. See \c V4L2_CID_ARGUS_METADATA
 * for more information.
 *
 * ### EOS Handling
 * Camera outputs all the queued empty buffers with data and TIME_OUT if no more empty buffers
 * are queued. If error is encountered, am empty buffer is queued to the output with
 * V4L2_BUF_FLAG_LAST flag.
 * @{
 * @ingroup ee_extensions_group
 */
/**
 * Defines the Control ID to set auto white balance mode for camera.
 *
 * A value of type \c v4l2_argus_ac_awb_mode must be supplied
 * with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_AUTO_WHITE_BALANCE_MODE (V4L2_CID_CAMERA_CLASS_BASE+20)

/**
 * Defines the Control ID to set sensor mode for camera.
 *
 * A non-negative integer value must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * and before requesting buffers on the capture plane.
 */
#define V4L2_CID_ARGUS_SENSOR_MODE (V4L2_CID_CAMERA_CLASS_BASE+32)

/**
 * Defines the Control ID to set denoise strength for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_denoise_strength must be
 * supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_DENOISE_STRENGTH (V4L2_CID_CAMERA_CLASS_BASE+33)

/**
 * Defines the Control ID to set denoise mode for camera.
 *
 * A value of type \c v4l2_argus_denoise_mode must be
 * supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_DENOISE_MODE (V4L2_CID_CAMERA_CLASS_BASE+34)

/**
 * Defines the Control ID to set edge enhancement strength for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_edge_enhance_strength
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_EE_STRENGTH (V4L2_CID_CAMERA_CLASS_BASE+35)

/**
 * Defines the Control ID to set edge enhancement mode for camera.
 *
 * A value of type \c v4l2_argus_edge_enhance_mode
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_EE_MODE (V4L2_CID_CAMERA_CLASS_BASE+36)

/**
 * Defines the Control ID to set Auto Exposure antibanding mode for camera.
 *
 * A value of type \c v4l2_argus_ac_ae_antibanding_mode must be supplied
 * with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_AE_ANTIBANDING_MODE (V4L2_CID_CAMERA_CLASS_BASE+37)

/**
 * Defines the Control ID to set edge enhancement settings for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_exposure_compensation
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_EXPOSURE_COMPENSATION (V4L2_CID_CAMERA_CLASS_BASE+38)

/**
 * Defines the Control ID to set edge enhancement settings for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_ispdigital_gainrange
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_ISP_DIGITAL_GAIN_RANGE (V4L2_CID_CAMERA_CLASS_BASE+39)

/**
 * Defines the Control ID to set sensor mode for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_color_saturation
 * must be supplied with this control.
 * The flag `EnableSaturation` must be set to true to enable setting
 * the specified color saturation
 *
 * @attention This control should be set after setting format
 * and before requesting buffers on the capture plane.
 */
#define V4L2_CID_ARGUS_COLOR_SATURATION (V4L2_CID_CAMERA_CLASS_BASE+40)

/**
 * Defines the Control ID to set edge enhancement settings for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_gainrange
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_GAIN_RANGE (V4L2_CID_CAMERA_CLASS_BASE+41)

/**
 * Defines the Control ID to set edge enhancement settings for camera.
 *
 * A pointer to a valid structure \c v4l2_argus_exposure_timerange
 * must be supplied with this control.
 *
 * @attention This control should be set after setting format
 * on the capture plane.
 */
#define V4L2_CID_ARGUS_EXPOSURE_TIME_RANGE (V4L2_CID_CAMERA_CLASS_BASE+42)

/**
 * Defines the Control ID to get the camera argus output metadata.
 *
 * A pointer to a valid structure \c v4l2_argus_ctrl_metadata must be supplied
 * with this control.
 *
 * @attention This control must be read after dequeueing a buffer successfully from
 * the capture plane. The values in the structure are valid until the buffer is queued
 * again.
 */
#define V4L2_CID_ARGUS_METADATA (V4L2_CID_CAMERA_CLASS_BASE+43)

/** @} */

/** @addtogroup V4L2Dec */
/** @{ */
/**
 * Enum v4l2_skip_frames_type, possible methods for decoder skip frames. */
enum v4l2_skip_frames_type
{
    /** Do not skip any frame. */
    V4L2_SKIP_FRAMES_TYPE_NONE = 0,
    /** Skip all non-reference frames. */
    V4L2_SKIP_FRAMES_TYPE_NONREF = 1,
    /** Skip all frames except IDR */
    V4L2_SKIP_FRAMES_TYPE_DECODE_IDR_ONLY = 2,
    V4L2_SKIP_FRAMES_TYPE_FORCE32 = 0x7FFFFFFF
};

/**
 * Enum v4l2_cuda_mem_type, possible methods for cuda memory tpye. */
enum v4l2_cuda_mem_type {
    /** Memory type device. */
    V4L2_CUDA_MEM_TYPE_DEVICE = 0,
    /** Memory type host. */
    V4L2_CUDA_MEM_TYPE_PINNED = 1,
    /** Memory type unified. */
    V4L2_CUDA_MEM_TYPE_UNIFIED = 2,
    /** Memory type surface array. only for jetson */
    V4L2_CUDA_MEM_TYPE_SURFACE_ARRAY = 3,
    /** Memory type cuda array. Applicable only for Jetson */
    V4L2_CUDA_MEM_TYPE_ARRAY = 4,
    V4L2_CUDA_MEM_TYPE_FORCE32 = 0x7FFFFFFF
};

/**
 * Enum v4l2_videodec_input_error_type, possible error types for input stream. */
enum v4l2_videodec_input_error_type {
    /** no error. */
    V4L2_DEC_ERROR_NONE = 0x0,
    /** sps error. */
    V4L2_DEC_ERROR_SPS = 0x1,
    /** pps error. */
    V4L2_DEC_ERROR_PPS = 0x2,
    /** slice header error. */
    V4L2_DEC_ERROR_SLICE_HDR = 0x4,
    /** missing reference frame error. */
    V4L2_DEC_ERROR_MISSING_REF_FRAME = 0x8,
    /** VPS error. */
    V4L2_DEC_ERROR_VPS = 0x10,
    V4L2_DEC_ERROR_FORCE32 = 0x7FFFFFFF
};

/**
 * Holds the decoder error status metadata for the frame.
 */
typedef struct v4l2_ctrl_videodec_statusmetadata_
{
    /** Error types:
     *  bit 0: Fatal
     *  bit 1: MB level syntax
     *  bit 2: Missing Slice(s)
     *  bit 3: PrevFrameLostFlag */
    __u32  DecodeError;
    /** Number of macro blocks decoded without error. */
    __u32  DecodedMBs;
    /** Number of macro blocks where error was concealed. */
    __u32  ConcealedMBs;
    /** POC of the reference frame used for concealment. */
    __u32  nConcealedFromPOC;
    /** Time required to decode the frame, in microseconds. */
    __u32  FrameDecodeTime;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_ctrl_videodec_statusmetadata;

/**
 * Holds the the frame specific metadata for a reference frame.
 */
typedef struct v4l2_ctrl_videodec_refframe_metadata_
{
    /** Boolean value indicating if the frame is present in DPB. */
    __u32 bPresent;
    /** Boolean value indicating if the frame is an IDR. */
    __u32 bIdrFrame;
    /** Boolean value indicating if the frame is a long term reference frame. */
    __u32 bLTRefFrame;
    /** Boolean value indicating if it is a predicted frame. */
    __u32 bPredicted;
    /** Picture order count of the frame. */
    __u32 nPictureOrderCnt;
    /** Frame number. Resets to zero for an IDR frame. */
    __u32 nFrameNum;
    /** Long Term Frame Index of the frame. */
    __u32 nLTRFrameIdx;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videodec_refframe_metadata;

/**
 * Holds the the frame specific metadata for the current frame.
 */
typedef struct v4l2_ctrl_videodec_currentframe_metadata_
{
    /** Boolean value indicating if the current frame is a reference frame. */
    __u32 bRefFrame;
    /** Boolean value indicating if the current frame is an IDR. */
    __u32 bIdrFrame;
    /** Boolean value indicating if the current frame is a long term reference frame. */
    __u32 bLTRefFrame;
    /** Picture order count of the current frame. */
    __u32 nPictureOrderCnt;
    /** Frame number. Resets to zero for an IDR frame. */
    __u32 nFrameNum;
    /** Long Term Frame Index of the current frame. */
    __u32 nLTRFrameIdx;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videodec_currentframe_metadata;

/**
 * Holds the decoder DPB info metadata.
 */
typedef struct v4l2_ctrl_videodec_dpbinfometadata_
{
    /** Metadata for the current decoded frame. */
    v4l2_ctrl_videodec_currentframe_metadata currentFrame;
    /** Number of active frames present in the DPB. */
    __u32 nActiveRefFrames;
    /** An array of metadatas for the active frames in the DPB. Only
     *  nActiveRefFrames elements in the array are valid. */
    v4l2_ctrl_videodec_refframe_metadata RPSList[16];
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videodec_dpbinfometadata;

/**
 * Holds H.264 specific decoder metadata for the frame.
 */
typedef struct v4l2_ctrl_h264dec_bufmetadata_
{
    /** Holds the number of bits in the frame. */
    __u32 nFrameNumBits;
    /** Type of frame:
     *  0 = B
     *  1 = P
     *  2 = I */
    __u32  FrameType;
    /** Holds the current DPB information of the decoder. */
    v4l2_ctrl_videodec_dpbinfometadata dpbInfo;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_ctrl_h264dec_bufmetadata;

/**
 * Holds H.265 specific decoder metadata for the frame.
 */
typedef struct v4l2_ctrl_hevcdec_bufmetadata_
{
    /** Holds the number of bits in the frame. */
    __u32 nPocLsbBits;
    /** Type of frame:
     *  0 = B
     *  1 = P
     *  2 = I */
    __u32  FrameType;
    /** Holds the current DPB information of the decoder. */
    v4l2_ctrl_videodec_dpbinfometadata dpbInfo;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_ctrl_hevcdec_bufmetadata;

typedef struct v4l2_ctrl_av1dec_bufmetadata_
{
    /** Holds the picture width information */
    __u32 width;
    /** Holds the picture height information */
    __u32 height;
    /** Holds the picture ref frame index information */
    __s8 ref_frame_map_index[8];
    /** Holds the picture view ID information */
    __u16 viewIdInformation;
    /** Holds the picture bit depth */
    __u8  bitDepthInformation;
    /** Holds the Quantization related information of the decoder. */
    struct {
        __u8 delta_q_present;
        __u8 delta_q_res;
        __u8 base_qindex;
        __s8 y_dc_delta_q;
        __s8 u_dc_delta_q;
        __s8 v_dc_delta_q;
        __s8 u_ac_delta_q;
        __s8 v_ac_delta_q;
        __u8 qm_y;
        __u8 qm_u;
        __u8 qm_v;
    } quantization;

    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_ctrl_av1dec_bufmetadata;

/**
 * Holds the video decoder input header error metadata for a frame.
 */
typedef struct v4l2_ctrl_videodec_inputbuf_metadata_
{
    /** Bits represent types of error as defined
     *  with v4l2_videodec_input_error_type. */
    __u32 nBitStreamError;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videodec_inputbuf_metadata;

/**
 * Holds the video decoder output metadata for a frame.
 */
typedef struct v4l2_ctrl_videodec_outputbuf_metadata_
{
    /** Color primaries. */
    __u8 ucColorPrimaries;
    /** Transfer characteristics. */
    __u8 ucTransferCharacteristics;
    /** Matrix coefficients. */
    __u8 ucMatrixCoefficients;
    /** Boolean value indicating if \c FrameDecStats has valid contents. */
    __u32 bValidFrameStatus;
    /** Frame decode statistics. */
    v4l2_ctrl_videodec_statusmetadata    FrameDecStats;
    /** Codec specific metadata for the frame. */
    union {
        /** H.264 specific metadata. */
        v4l2_ctrl_h264dec_bufmetadata H264DecParams;
        /** H.265 specific metadata. */
        v4l2_ctrl_hevcdec_bufmetadata HEVCDecParams;
        /** AV1 specific metadata. */
        v4l2_ctrl_av1dec_bufmetadata AV1DecParams;
    }CodecParams;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videodec_outputbuf_metadata;
/** @} */

/** @addtogroup V4L2Enc */
/** @{ */

/**
 * Defines the possible profiles for H.265 encoder.
 */
enum v4l2_mpeg_video_h265_profile
{
    /** H.265 Main profile. */
    V4L2_MPEG_VIDEO_H265_PROFILE_MAIN = 0,
    /** H.265 Main10 profile. */
    V4L2_MPEG_VIDEO_H265_PROFILE_MAIN10 = 1,
    /** H.265 MainStillPicture profile. */
    V4L2_MPEG_VIDEO_H265_PROFILE_MAINSTILLPICTURE = 2,
    /** H.265 FREXT profile. */
    V4L2_MPEG_VIDEO_H265_PROFILE_FREXT = 3,

    V4L2_MPEG_VIDEO_H265_FORCE32 = 0x7FFFFFFF
};

/**
 * Specifies the types of encoder temporal tradeoff levels
 */
enum v4l2_enc_temporal_tradeoff_level_type
{
    /** Do not drop any buffers. */
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_DROPNONE = 0,
    /** Drop 1 in every 5 buffers. */
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_DROP1IN5,
    /** Drop 1 in every 3 buffers. */
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_DROP1IN3,
    /** Drop 1 in every 2 buffers. */
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_DROP1IN2,
    /** Drop 2 in every 3 buffers. */
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_DROP2IN3,
    V4L2_ENC_TEMPORAL_TRADEOFF_LEVEL_FORCE32 = 0x7FFFFFFF
};

/**
 * Specifies the encoder HW Preset type.
 */
enum v4l2_enc_hw_preset_type
{
    /** Encoder HWPreset DISABLED. */
    V4L2_ENC_HW_PRESET_DISABLE = 0,
    /** Encoder HWPreset with per frame encode time UltraFast. */
    V4L2_ENC_HW_PRESET_ULTRAFAST = 1,
    /** Encoder HWPreset with per frame encode time Fast. */
    V4L2_ENC_HW_PRESET_FAST,
    /** Encoder HWPreset with per frame encode time Medium. */
    V4L2_ENC_HW_PRESET_MEDIUM,
    /** Encoder HWPreset with per frame encode time Slow. */
    V4L2_ENC_HW_PRESET_SLOW,
    /** Last value is Max value. */
    V4L2_ENC_HW_PRESET_DEFAULT = 0x7FFFFFFF
};

/**
 * Specifies the encoder HW Preset type.
 */
enum v4l2_enc_hw_tuning_info_type
{
    /** Encoder Tuning Info Undefined */
    V4L2_ENC_TUNING_INFO_UNDEFINED = 0,
    /** Encoder Tuning Info High Quality */
    V4L2_ENC_TUNING_INFO_HIGH_QUALITY = 1,
    /** Encoder Tuning Info Low Latency */
    V4L2_ENC_TUNING_INFO_LOW_LATENCY,
    /** Encoder Tuning Info Ultra Low Latency */
    V4L2_ENC_TUNING_INFO_ULTRA_LOW_LATENCY,
    /** Encoder Tuning Info Lossless */
    V4L2_ENC_TUNING_INFO_LOSSLESS,
    /** Last value is Max value. */
    V4L2_ENC_TUNING_INFO_FORCE32 = 0x7FFFFFFF
};

/**
 * Holds encoder HW Preset type parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_HW_PRESET_TYPE_PARAM IOCTL.
 */
typedef struct v4l2_enc_hw_preset_type_param_
{
    /** Type in which the encoder hw preset is specified, one of type #v4l2_enc_hw_preset_type. */
    enum v4l2_enc_hw_preset_type hw_preset_type;
    /** Boolean value indicating if encoder set to max clock. */
    __u8 set_max_enc_clock;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_hw_preset_type_param;

/**
 * Enum specifying the type of slice length.
 */
enum v4l2_enc_slice_length_type
{
    /** Slice size is specified in terms of number of bytes. */
    V4L2_ENC_SLICE_LENGTH_TYPE_BITS = 0, /* DEPRECATED for CUDA NVENC*/
    /** Slice size is specified in terms of number of macroblocks. */
    V4L2_ENC_SLICE_LENGTH_TYPE_MBLK,
    /** Applicable for CUDA only*/
    /** Slice data specifies # of MB rows in each slice (except last slice). */
    V4L2_ENC_SLICE_LENGTH_TYPE_MB_ROW,
    /** Slice data specifies number of slices in the picture. Divided by encoder optimally */
    V4L2_ENC_SLICE_LENGTH_TYPE_NUM_SLICES,
    V4L2_ENC_SLICE_LENGTH_FORCE32 = 0x7FFFFFFF
};

/**
 * Specifies the input buffer metadata flag.
 */
enum v4l2_enc_input_metadata_param
{
    /** Input metadata structure contains ROI parameters.  */
    V4L2_ENC_INPUT_ROI_PARAM_FLAG = 1,
    /** Input metadata structure contains GDR parameters.  */
    V4L2_ENC_INPUT_GDR_PARAM_FLAG = 1 << 1,
    /** Input metadata structure contains External RPS parameters.  */
    V4L2_ENC_INPUT_RPS_PARAM_FLAG = 1 << 2,
    /** Input metadata structure contains External RC parameters.  */
    V4L2_ENC_INPUT_RC_PARAM_FLAG = 1 << 3,
    /** Input metadata structure contains ReconCRC parameters.  */
    V4L2_ENC_INPUT_RECONCRC_PARAM_FLAG = 1 << 4,
    /** Input metadata structure contains AV1 Tile Groups parameters.  */
    V4L2_ENC_INPUT_TG_PARAM_FLAG = 1 << 5,
    /** Last value is Max value. */
    V4L2_ENC_INPUT_FORCE32 = 0x7FFFFFFF
};

/**
 * Defines the possible levels for H.265 encoder.
 */
enum v4l2_mpeg_video_h265_level {

    V4L2_MPEG_VIDEO_H265_LEVEL_1_0_MAIN_TIER = 0,
    V4L2_MPEG_VIDEO_H265_LEVEL_1_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_2_0_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_2_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_2_1_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_2_1_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_3_0_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_3_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_3_1_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_3_1_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_4_0_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_4_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_4_1_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_4_1_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_0_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_1_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_1_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_2_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_5_2_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_0_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_0_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_1_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_1_HIGH_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_2_MAIN_TIER,
    V4L2_MPEG_VIDEO_H265_LEVEL_6_2_HIGH_TIER,
};

#define V4L2_MPEG_VIDEO_BITRATE_MODE_CONSTQP 0x2

/**
 * Holds encoder slice length parameters, to be used with
 * \c V4L2_CID_MPEG_VIDEOENC_SLICE_LENGTH_PARAM IOCTL.
 */
typedef struct v4l2_enc_slice_length_param_
{
    /** Type in which the slice length is specified, one of type \c v4l2_enc_slice_length_type. */
    enum v4l2_enc_slice_length_type slice_length_type;
    /** Size of the slice in either number of bytes or number of macro blocks. */
    __u32   slice_length;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_slice_length_param;

/**
 * Holds encoder virtual buffer size parameters, to be used with
 * \c V4L2_CID_MPEG_VIDEOENC_VIRTUALBUFFER_SIZE IOCTL.
 */
typedef struct v4l2_enc_virtual_buffer_size_
{
    /** Size of the virtual buffer, in bits. */
    __u32   size;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_virtual_buffer_size;

/**
 * Holds encoder number of reference frame parameters, to be used with
 * \c V4L2_CID_MPEG_VIDEOENC_NUM_REFERENCE_FRAMES IOCTL.
 */
typedef struct v4l2_enc_num_ref_frames_
{
    /** Number of reference frames. */
    __u32   frames;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_num_ref_frames;

/**
 * Holds encoder slice intrareferesh parameters, to be used with
 * \c V4L2_CID_MPEG_VIDEOENC_SLICE_INTRAREFRESH_PARAM IOCTL.
 */
typedef struct v4l2_enc_slice_intrarefresh_param_
{
    /** Slice intrarefresh interval, in number of slices. */
    __u32   interval;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_slice_intrarefresh_param;

/**
 * Defines the maximum number of ROI regions supported by the encoder.
 */
#define V4L2_MAX_ROI_REGIONS 8

/**
 * Holds the encoder quality parameters for a single ROI region.
 */
typedef struct v4l2_enc_ROI_param_
{
    /** Region of interest rectangle. */
    struct v4l2_rect  ROIRect;
    /** QP delta for the region. */
    __s32   QPdelta;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_enc_ROI_param;

/**
 * Holds the encoder frame ROI parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_ROI_PARAMS IOCTL.
 */
typedef struct v4l2_enc_frame_ROI_params_
{
    /** Number of regions. */
    __u32 num_ROI_regions;
    /** Array of indiviudal ROI parameters. */
    v4l2_enc_ROI_param ROI_params[V4L2_MAX_ROI_REGIONS];
    /** Config store integer to which this control is to be applied.
     *  This must be same as the value of config store of \c v4l2_buffer to which
     *  the ROI params is applied. */
    __u32   config_store;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_frame_ROI_params;

/**
 * Defines the available features for preprocessing enhancement (PPE) module
 */
enum v4l2_ppe_feature
{
    /** Default value for no feature enabled */
    V4L2_PPE_FEATURE_NONE = 0,
    /** Temporal Adaptive Quantization (TAQ) */
    V4L2_PPE_FEATURE_TAQ = (1 << 0),
    /** Spatial Adaptive Quantization (SAQ) */
    V4L2_PPE_FEATURE_SAQ = (1 << 1),
    /** Last value is Max value. */
    V4L2_PPE_FEATURE_FORCE32 = 0x7FFFFFFF
};

/**
 * Holds the preprocessing enhancement initialization parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_PPE_INIT_PARAMS IOCTL.
 */
typedef struct v4l2_enc_ppe_init_params_
{
    /** Boolean value indicating if PPE module should be enabled */
    __u8 enable_ppe;
    /** Bit flags to enable/disable each individual feature */
    __u32 feature_flags;
    /** Boolean value indicating if profiler should be enabled */
    __u8 enable_profiler;
    /** The max number of milliseconds that the encoder should wait for each frame processing */
    __s32 wait_time_ms;
    /** Maximum strength of QP delta map for TAQ */
    __u8 taq_max_qp_delta;
    /** Boolean value indicating if TAQ should be applied for B-frames */
    __u8 taq_b_frame_mode;
    /** Maximum strength of QP delta map for SAQ */
    __u8 saq_max_qp_delta;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_ppe_init_params;

/**
 * Holds the motion vector parameters for a single block.
 * For H.264, nvenc provides one motion vector per 16x16 block(Macroblock).
 * For H.265, nvenc provides one motion vector per 32x32 block(Coded Tree Block).
 */
typedef struct MVInfo_ {
    /** Number of pixels the macro block moved in horizontal direction. */
    __s32 mv_x   : 16;
    /** Number of pixels the macro block moved in vertical direction. */
    __s32 mv_y   : 14;
    /** Temporal hints used by hardware for Motion Estimation. */
    __u32 weight : 2;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} MVInfo;

/**
 * Holds the motion vector parameters for one complete frame.
 */
typedef struct v4l2_ctrl_videoenc_outputbuf_metadata_MV_ {
    /** Size of the pMVInfo buffer, in bytes. */
    __u32 bufSize;
    /** Pointer to the buffer containing the motion vectors. */
    MVInfo *pMVInfo;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videoenc_outputbuf_metadata_MV;

/**
 * Maximum number of reference frames supported by the encoder.
 */
#define V4L2_MAX_REF_FRAMES  8

/**
 * Holds the RPS List parameters of encoded frame.
 */
typedef struct v4l2_enc_frame_full_prop_
{
    /** Unique frame ID. */
    __u32  nFrameId;
    /** Boolean value indicating if current frame is an IDR. */
    __u8   bIdrFrame;
    /** Boolean value indicating if set Long Term Ref Flag. */
    __u8   bLTRefFrame;
    /** Picture Order Count. */
    __u32  nPictureOrderCnt;
    /** FrameNum. */
    __u32  nFrameNum;
    /** LongTermFrameIdx of a picture. */
    __u32  nLTRFrameIdx;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_enc_frame_full_prop;

/**
 * Defines metadata associated with H264 features.
 */
typedef struct
{
    /** Reserved fields are added for extensibility. */
    __u8 reserved[128];
} v4l2_ctrl_h264enc_metadata;

/**
 * Defines metadata associated with H265 features.
 */
typedef struct
{
    /** Reserved fields are added for extensibility. */
    __u8 reserved[128];
} v4l2_ctrl_hevcenc_metadata;

/**
 * Defines metadata associated with AV1 features.
 */
typedef struct
{
    /** Refresh frame flags */
    __u8 refreshFrameFlags;
    /** List of reference frame IDs used for current frame */
    __u8 refFrameIndex[V4L2_MAX_REF_FRAMES];
    /** Number of TileRows computed based on input log2TileRows */
    __u8 numTileRows;
    /** Number of TileCols computed based on input log2TileCols */
    __u8 numTileCols;
    /** Number of TileGroups */
    __u8 numTileGroups;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[116];
} v4l2_ctrl_av1enc_metadata;

/**
 * Holds the encoder output metadata for a frame, to be used with
 * \c V4L2_CID_MPEG_VIDEOENC_METADATA IOCTL.
 */
typedef struct v4l2_ctrl_videoenc_outputbuf_metadata_
{
    /** Boolean value indicating if current frame is a key frame. */
    __u8 KeyFrame;
    /** Boolean value indicating end of frame in case of multi slice encoding. */
    __u8 EndofFrame;
    /** Average QP value of the frame. */
    __u16 AvgQP;
    /** Boolean value indicating if current frame is a golden or alternate frame. */
    __u8 bIsGoldenOrAlternateFrame;
    /** CRC for Reconstructed frame. */
    __u8 bValidReconCRC;
    /** Recon Y-frame CRC */
    __u32 ReconFrame_Y_CRC;
    /** Recon U-frame CRC */
    __u32 ReconFrame_U_CRC;
    /** Recon V-frame CRC */
    __u32 ReconFrame_V_CRC;
    /** Number of bits needed to encode the frame. */
    __u32 EncodedFrameBits;
    /** Minumum QP value in the frame. */
    __u32 FrameMinQP;
    /** Maximum QP value in the frame. */
    __u32 FrameMaxQP;
    /** RPS Feedback. */
    __u32 bRPSFeedback_status;
    /**  Reference frame ID used for Motion Estimation of current frame,
         ignored for IDR */
    __u32 nCurrentRefFrameId;
    /** Number of active reference frames. */
    __u32 nActiveRefFrames;
    /** RPS List including most recent frame if it is reference frame. */
    v4l2_enc_frame_full_prop RPSList[V4L2_MAX_REF_FRAMES];

    union
    {
        /** H.264 specific metadata. */
        v4l2_ctrl_h264enc_metadata *pH264EncMeta;
        /** H.265 specific metadata. */
        v4l2_ctrl_hevcenc_metadata *pHEVCEncMeta;
        /** AV1 specific metadata. */
        v4l2_ctrl_av1enc_metadata *pAV1EncMeta;
    } CodecMeta;

    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videoenc_outputbuf_metadata;

/**
 * Holds the metadata parameters for video encoder and decoder.
 *
 * The metadata is valid for the buffer with index \c buffer_index after the
 * buffer is dequeued until it is queued again.
 */
typedef struct v4l2_ctrl_video_metadata_
{
    /** A pointer to #v4l2_ctrl_videodec_inputbuf_metadata structure.
     * This must be a valid pointer when used with #V4L2_CID_MPEG_VIDEODEC_INPUT_METADATA
     * IOCTL. */
    v4l2_ctrl_videodec_inputbuf_metadata *VideoDecHeaderErrorMetadata;
    /** A pointer to #v4l2_ctrl_videodec_outputbuf_metadata structure.
     * This must be a valid pointer when used with #V4L2_CID_MPEG_VIDEODEC_METADATA
     * IOCTL. */
    v4l2_ctrl_videodec_outputbuf_metadata *VideoDecMetadata;
    /** A pointer to #v4l2_ctrl_videoenc_outputbuf_metadata structure.
     * This must be a valid pointer when used with #V4L2_CID_MPEG_VIDEOENC_METADATA
     * IOCTL. */
    v4l2_ctrl_videoenc_outputbuf_metadata *VideoEncMetadata;
    /** A pointer to #v4l2_ctrl_videoenc_outputbuf_metadata_MV structure.
     * This must be a valid pointer when used with #V4L2_CID_MPEG_VIDEOENC_METADATA_MV
     * IOCTL. */
    v4l2_ctrl_videoenc_outputbuf_metadata_MV *VideoEncMetadataMV;
    /** Index of the buffer whose metadata is required. */
    __u32 buffer_index;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_video_metadata;

/**
 * Holds the encoder GDR parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA IOCTL.
 */
typedef struct v4l2_enc_gdr_params_
{
    /** Parameter for GDR (Intra Refresh) for specified number of frames. */
    __u32 nGDRFrames;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_enc_gdr_params;

/**
 * Holds the params to configure encoder for external rps control
 * to be used with #V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RPS_CONTROL IOCTL.
 */
typedef struct v4l2_enc_enable_ext_rps_ctrl_
{
    /** Boolean value indicating if enabled External RPS control. */
    __u8 bEnableExternalRPS;
    /** Boolean value indicating if allowed gap in frame number. */
    __u8 bGapsInFrameNumAllowed;
    /* TODO : Check for field details. */
    __u32 nH264FrameNumBits;
    /* TODO : Check for field details. */
    __u32 nH265PocLsbBits;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_enable_ext_rps_ctr;


/**
 * Holds the encoder frame property.
 */
typedef struct _v4l2_enc_frame_prop
{
    /** unique Id. */
    __u32 nFrameId;
    /** Long Term Ref Flag. */
    __u8 bLTRefFrame;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_enc_frame_prop;

/**
 * Defines input metadata associated with H264 features.
 */
typedef struct
{
    /** Reserved fields are added for extensibility. */
    __u8 reserved[128];
} v4l2_ctrl_h264enc_inputmetadata;

/**
 * Defines input metadata associated with H265 features.
 */
typedef struct
{
    /** Reserved fields are added for extensibility. */
    __u8 reserved[128];
} v4l2_ctrl_hevcenc_inputmetadata;

/**
 * Defines input metadata associated with AV1 features.
 */
typedef struct
{
    /** Refresh frame flags to inform which ref frame slots to update */
    __u8 refreshFrameFlags;
    /** RPS paramter for current frame to use */
    __u8 refFrameIndex[V4L2_MAX_REF_FRAMES];
    /** Reserved fields are added for extensibility. */
    __u8 reserved[119];
} v4l2_ctrl_av1enc_inputmetadata;

/**
 * Holds the encoder frame external rps control parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA IOCTL.
 */
typedef struct v4l2_enc_frame_ext_rps_ctrl_params_
{
    /** unique Id of current frame. */
    __u32 nFrameId;
    /** Boolean value indicating if current frame referenced or non-referenced. */
    __u8 bRefFrame;
    /** Boolean value indicating if current frame long Term Ref Flag. */
    __u8 bLTRefFrame;
   /** Max Number of reference frames to use for inter-motion search. */
    __u32 nMaxRefFrames;
    /** # of valid entries in RPS, 0 means IDR. */
    __u32 nActiveRefFrames;;
    /**  frame id of reference frame to be used for motion search, ignored for IDR. */
    __u32 nCurrentRefFrameId;
    /** Array of RPS */
    v4l2_enc_frame_prop RPSList[V4L2_MAX_REF_FRAMES];
    /** Input Metadata for each codec*/
    union
    {
        /** H.264 specific metadata. */
        v4l2_ctrl_h264enc_inputmetadata *pH264EncParams;
        /** H.265 specific metadata. */
        v4l2_ctrl_hevcenc_inputmetadata *pHEVCEncParams;
        /** AV1 specific metadata. */
        v4l2_ctrl_av1enc_inputmetadata *pAV1EncParams;
    } CodecParams;

    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_frame_ext_rps_ctrl_params;


/**
 * Holds the params to configure encoder for external rate control mode
 * to be used with #V4L2_CID_MPEG_VIDEOENC_ENABLE_EXTERNAL_RATE_CONTROL IOCTL.
 */
typedef struct v4l2_enc_enable_ext_rate_ctrl_
{
    /** Boolean value indicating if enabled External Picture RC. */
    __u8 bEnableExternalPictureRC;
    /** Max QP per session when external picture RC enabled. */
    __u32 nsessionMaxQP;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_enable_ext_rate_ctr;

/**
 * Holds the encoder frame external rate control parameters
 * to be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA ioctl.
 */
typedef struct v4l2_enc_frame_ext_rate_ctrl_params_
{
    /** Target frame bits. */
    __u32 nTargetFrameBits;
    /** Frame start QP. */
    __u32 nFrameQP;
    /** Frame min QP. */
    __u32 nFrameMinQp;
    /** Frame max QP. */
    __u32 nFrameMaxQp;
    /** Frame min QP deviation. */
    __u32 nMaxQPDeviation;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_frame_ext_rate_ctrl_params;

/**
 * Holds the params to configure encoder for ROI parameters encoding
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_ENABLE_ROI_PARAM IOCTL.
 */
typedef struct v4l2_enc_enable_roi_param_
{
    /** Boolean value to indicating ROI param encoding. */
    __u8 bEnableROI;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_enable_roi_param;

/**
 * Holds the params to configure encoder for Reconstructed CRC encoding
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_ENABLE_RECONCRC_PARAM IOCTL.
 */
typedef struct v4l2_enc_enable_reconcrc_param_
{
    /** Boolean value to indicating Reconstructed CRC encoding. */
    __u8 bEnableReconCRC;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_enable_reconcrc_param;

/**
 * Holds the encoder frame Reconstructed CRC parameters.
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA IOCTL.
 */
typedef struct v4l2_enc_frame_ReconCRC_params_
{
    /** Rectangle to specify the co-ordinates of the input frame
    * used to calculate reconstructed picture CRC. */
    struct v4l2_rect  ReconCRCRect;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_enc_frame_ReconCRC_params;

/**
 * Holds the encoder frame AV1 Tile groups.
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA IOCTL.
 */
typedef struct v4l2_enc_frame_tile_groups_params_
{
    /** Tile group related parameters **/
    __u8 tileRows;
    __u8 tileCols;
    __u8 tileGroups;
    __u8 tileIndexGroup[128];
    /** Reserved fields are added for extensibility. */
    __u8 reserved[128];
} v4l2_enc_frame_tile_groups_params;

/**
 * Holds the encoder frame input metadata parameters.
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_INPUT_METADATA IOCTL.
 */
typedef struct v4l2_ctrl_videoenc_input_metadata_
{
    /** Flag to indicate which inputbuffer metadata is valid. */
    __u32    flag;
    /** Pointer to the ROI params structure when ROI param is in metadata_flag. */
    v4l2_enc_frame_ROI_params *VideoEncROIParams;
    /** Pointer to the Reconstructed CRC parameter structure when ReconCRC param is in
    * metadata flag. */
    v4l2_enc_frame_ReconCRC_params *VideoReconCRCParams;
    /** Pointer to the GDR params structure when GDR param is in metadata_flag. */
    v4l2_enc_gdr_params   *VideoEncGDRParams;
    /** Pointer to the External RPL control parameter structure when RPS param is in
    * metadata flag. */
    v4l2_enc_frame_ext_rps_ctrl_params *VideoEncRPSParams;
    /** Pointer to the External Rate control parameter structure when RC param is in
    * metadata flag. */
    v4l2_enc_frame_ext_rate_ctrl_params *VideoEncExtRCParams;
    /** Pointer to the AV1 Tile Groups parameter structure when TG param is in
     * metadata flag. */
    v4l2_enc_frame_tile_groups_params *VideoEncAV1TGParams;
    /** Config store integer to which these parameters are to be applied.
     *  This must be same as the value of config store of queued v4l2_buffer
     *   for which these parameters are valid. */
    __u32    config_store;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
} v4l2_ctrl_videoenc_input_metadata;

/**
 * Setting Qp values in #v4l2_ctrl_video_qp_range to QP_RETAIN_VAL
 * retains default or previously set QP values.
 */
#define QP_RETAIN_VAL -1

/**
 * Holds the encoder frame min/max QP parameters.
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_QP_RANGE IOCTL.
 */
typedef struct _v4l2_ctrl_video_qp_range
{
    /** Minimum QP value for I frame. */
    __u32 MinQpI;
    /** Maximum QP value for I frame. */
    __u32 MaxQpI;
    /** Minimum QP value for P frame. */
    __u32 MinQpP;
    /** Maximum QP value for P frame. */
    __u32 MaxQpP;
    /** Minimum QP value for B frame. */
    __u32 MinQpB;
    /** Maximum QP value for B frame. */
    __u32 MaxQpB;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[8];
} v4l2_ctrl_video_qp_range;


typedef struct _v4l2_ctrl_intra_refresh
{
    /** Enabled intra refresh */
    __u32 enableIntraRefresh;
    /** Specifies the interval between successive intra refresh. */
    __u32 intraRefreshPeriod;
    /** Specifies the length of intra refresh in number of frames for periodic intra refresh.
     * Must be smaller than IDR interval. */
    __u32 intraRefreshCnt;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_ctrl_intra_refresh;

typedef struct _v4l2_ctrl_video_constqp
{
    __u32 constQpI;
    __u32 constQpP;
    __u32 constQpB;
    /** Reserved fields are added for extensibility. */
    __u32 reserved[4];
} v4l2_ctrl_video_constqp;

typedef struct _v4l2_ctrl_video_framerate
{
    __u32 fps_n;
    __u32 fps_d;
} v4l2_ctrl_video_framerate;

/**
 * Holds the encoder init QP parameters.
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_INIT_FRAME_QP IOCTL.
 */
typedef struct _v4l2_ctrl_video_init_qp
{
    /** Initial QP value for I frame. */
    __u32 IInitQP;
    /** Initial QP value for P frame. */
    __u32 PInitQP;
    /** Initial QP value for B frame. */
    __u32 BInitQP;
    /** Reserved fields are added for extensibility. */
    __u32 reserved[4];
} v4l2_ctrl_video_init_qp;

/**
 * Holds the params to configure tiles for AV1 encoding
 *
 * Must be used with #V4L2_CID_MPEG_VIDEOENC_AV1_TILE_CONFIGURATION IOCTL.
 */
typedef struct v4l2_enc_av1_tile_config_
{
    /** Boolean value to enable multi-tile */
    __u8 bEnableTile;
    /** Log2 Tile Rows */
    __u32 nLog2RowTiles;
    /** Log2 Tile Columns */
    __u32 nLog2ColTiles;
    /** Reserved fields are added for extensibility. */
    __u32 reserved[4];
}v4l2_enc_av1_tile_config;


/** @} */

/** @addtogroup V4L2Argus */
/** @{ */

/**
 * Enum specifying types of denoise modes.
 */
enum v4l2_argus_denoise_mode {
    V4L2_ARGUS_DENOISE_MODE_UNKNOWN         = 0,
    V4L2_ARGUS_DENOISE_MODE_OFF             = 1,
    V4L2_ARGUS_DENOISE_MODE_FAST            = 2,
    V4L2_ARGUS_DENOISE_MODE_HIGH_QUALITY    = 3,
    V4L2_ARGUS_DENOISE_MODE_FORCE32         = 0x7FFFFFF
};

/**
 * Enum specifying types of edge enhancement modes.
 */
enum v4l2_argus_edge_enhance_mode {
    V4L2_ARGUS_EDGE_ENHANCE_MODE_UNKNOWN        = 0,
    V4L2_ARGUS_EDGE_ENHANCE_MODE_OFF            = 1,
    V4L2_ARGUS_EDGE_ENHANCE_MODE_FAST           = 2,
    V4L2_ARGUS_EDGE_ENHANCE_MODE_HIGH_QUALITY   = 3,
    V4L2_ARGUS_EDGE_ENHANCE_MODE_FORCE32        = 0x7FFFFFF
};

/**
 * Enum specifying types of AE antibanding modes.
 */
enum v4l2_argus_ac_ae_antibanding_mode {
    V4L2_ARGUS_AE_ANTIBANDING_MODE_UNKNOWN  = 0,
    V4L2_ARGUS_AE_ANTIBANDING_MODE_OFF      = 1,
    V4L2_ARGUS_AE_ANTIBANDING_MODE_AUTO     = 2,
    V4L2_ARGUS_AE_ANTIBANDING_MODE_50HZ     = 3,
    V4L2_ARGUS_AE_ANTIBANDING_MODE_60HZ     = 4,
    V4L2_ARGUS_AE_ANTIBANDING_MODE_FORCE32  = 0x7FFFFFF
};

/**
 * Enum specifying types of AC AWB modes.
 */
enum v4l2_argus_ac_awb_mode {
    V4L2_ARGUS_AWB_MODE_OFF               = 1,
    V4L2_ARGUS_AWB_MODE_AUTO              = 2,
    V4L2_ARGUS_AWB_MODE_INCANDESCENT      = 3,
    V4L2_ARGUS_AWB_MODE_FLUORESCENT       = 4,
    V4L2_ARGUS_AWB_MODE_WARM_FLUORESCENT  = 5,
    V4L2_ARGUS_AWB_MODE_DAYLIGHT          = 6,
    V4L2_ARGUS_AWB_MODE_CLOUDY_DAYLIGHT   = 7,
    V4L2_ARGUS_AWB_MODE_TWILIGHT          = 8,
    V4L2_ARGUS_AWB_MODE_SHADE             = 9,
    V4L2_ARGUS_AWB_MODE_MANUAL            = 10,
    V4L2_ARGUS_AWB_MODE_FORCE32           = 0x7FFFFFF
};

/**
 * Enum specifying types of AE states.
 */
enum v4l2_argus_ae_state {
    V4L2_ARGUS_AeState_Unknown            = 0,
    V4L2_ARGUS_AE_STATE_INACTIVE          = 1,
    V4L2_ARGUS_AE_STATE_SEARCHING         = 2,
    V4L2_ARGUS_AE_STATE_CONVERGED         = 3,
    V4L2_ARGUS_AE_STATE_FLASH_REQUIRED    = 4,
    V4L2_ARGUS_AE_STATE_TIMEOUT           = 5,
    V4L2_ARGUS_AE_STATE_FORCE32           = 0x7FFFFFF
};

/**
 * Enum specifying types of AWB states.
 */
enum v4l2_argus_awb_state {
    V4L2_ARGUS_AwbState_Unknown           = 0,
    V4L2_ARGUS_AWB_STATE_INACTIVE         = 1,
    V4L2_ARGUS_AWB_STATE_SEARCHING        = 2,
    V4L2_ARGUS_AWB_STATE_CONVERGED        = 3,
    V4L2_ARGUS_AWB_STATE_LOCKED           = 4,
    V4L2_ARGUS_AWB_STATE_FORCE32          = 0x7FFFFFF
};

/**
 * Holds the strength value for denoise operation.
 *
 * Must be used with #V4L2_CID_ARGUS_DENOISE_STRENGTH ioctl.
 */
typedef struct _v4l2_argus_denoise_strength
{
    /** Denoise Strength. Range: {-1.0f, 1.0f} **/
    float DenoiseStrength;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_denoise_strength;

/**
 * Holds the strength value for edge enhancement operation.
 *
 * Must be used with #V4L2_CID_ARGUS_EE_STRENGTH ioctl.
 */
typedef struct _v4l2_argus_edge_enhance_strength
{
    /** Edge Enhance Strength. Range: {-1.0f, 1.0f} **/
    float EdgeEnhanceStrength;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_edge_enhance_strength;

/**
 * Holds the value for exposure compensation.
 *
 * Must be used with #V4L2_CID_ARGUS_EXPOSURE_COMPENSATION ioctl.
 */
typedef struct _v4l2_argus_exposure_compensation
{
    /** Exposure Compensation. Range: {-2.0f, 2.0f} **/
    float ExposureCompensation;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_exposure_compensation;

/**
 * Holds the value for Isp Digital gain range.
 *
 * Must be used with #V4L2_CID_ARGUS_ISP_DIGITAL_GAIN_RANGE ioctl.
 */
typedef struct _v4l2_argus_ispdigital_gainrange
{
    /** Range: {1, 256} **/
    /** Digital Gain Range start limit **/
    float MinISPDigitalGainRange;
    /** Digital Gain Range end limit **/
    float MaxISPDigitalGainRange;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_ispdigital_gainrange;

/**
 * Holds the value for absolute color saturation.
 *
 * Must be used with #V4L2_CID_ARGUS_COLOR_SATURATION ioctl.
 */
typedef struct _v4l2_argus_color_saturation
{
    /** Boolean value to indicate enable of user-specified absolute color saturation **/
    __u8 EnableSaturation;
    /** Specified absolute color saturation **/
    float ColorSaturation;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_color_saturation;

/**
 * Holds the value for gain range.
 *
 * Must be used with #V4L2_CID_ARGUS_GAIN_RANGE ioctl.
 */
typedef struct _v4l2_argus_gainrange
{
    /** Analog Gain Range start limit **/
    float MinGainRange;
    /** Analog Gain Range end limit **/
    float MaxGainRange;
}v4l2_argus_gainrange;

/**
 * Holds the value for exposure range.
 *
 * Must be used with #V4L2_CID_ARGUS_EXPOSURE_TIME_RANGE ioctl.
 */
typedef struct _v4l2_argus_exposure_timerange
{
    /** Exposure Time Range start limit **/
    __u64 MinExposureTimeRange;
    /** Exposure Time Range end limit **/
    __u64 MaxExposureTimeRange;
}v4l2_argus_exposure_timerange;

/**
 * Holds the value for camera output metadata.
 *
 * Must be used with #V4L2_CID_ARGUS_METADATA ioctl.
 */
typedef struct _v4l2_argus_ctrl_metadata
{
    /** Boolean value to indicate if AE was locked for this capture **/
    __u8 AeLocked;
    /** Boolean value to indicate if metadata has valid contents **/
    __u8 ValidFrameStatus;
    /** Index of the buffer captured **/
    __u32 BufferIndex;
    /** Focuser Position used for capture**/
    __u32 FocuserPosition;
    /** CCT value calculated by AWB **/
    __u32 AwbCCT;
    /** ISO value used for capture **/
    __u32 SensorSensitivity;
    /** Time (nanoseconds) taken to integrate the capture **/
    __u64 FrameDuration;
    /** Frame readout time for the capture **/
    __u64 FrameReadoutTime;
    /** Sensor Exposure time value for the capture **/
    __u64 SensorExposureTime;
    /** ISP Digital gain value for the capture **/
    float IspDigitalGain;
    /** Estimated scene brightness for the capture **/
    float SceneLux;
    /** Sensor analog gain for the capture **/
    float SensorAnalogGain;
    /** AE state ran for capture **/
    enum v4l2_argus_ae_state AEState;
    /** AWB state ran for capture **/
    enum v4l2_argus_awb_state AWBState;
    /** Reserved fields are added for extensibility. */
    __u8 reserved[64];
}v4l2_argus_ctrl_metadata;
/** @} */

/** @addtogroup V4L2Conv */
/** @{ */

/**
 * Enum specifying types of buffer layouts.
 */
enum v4l2_nv_buffer_layout {
    V4L2_NV_BUFFER_LAYOUT_PITCH = 0,  /**< Pitch Linear Layout. */
    V4L2_NV_BUFFER_LAYOUT_BLOCKLINEAR = 1, /**< Block Linear Layout. */
};

/**
 * Specifies the types of rotation/flip algorithms.
 */
enum v4l2_flip_method {
    V4L2_FLIP_METHOD_IDENTITY = 0, /**< Identity (no rotation). */
    V4L2_FLIP_METHOD_90L = 1,      /**< Rotate counter-clockwise 90 degrees. */
    V4L2_FLIP_METHOD_180 = 2,      /**< Rotate 180 degrees. */
    V4L2_FLIP_METHOD_90R = 3,      /**< Rotate clockwise 90 degrees. */
    V4L2_FLIP_METHOD_HORIZ = 4,    /**< Flip horizontally. */
    V4L2_FLIP_METHOD_INVTRANS = 5, /**< Flip across upper right/lower left diagonal. */
    V4L2_FLIP_METHOD_VERT = 6,     /**< Flip vertically. */
    V4L2_FLIP_METHOD_TRANS = 7,    /**< Flip across upper left/lower right diagonal. */
};

/**
 * Specifies the types of interpolation methods.
 */
enum v4l2_interpolation_method {
  V4L2_INTERPOLATION_NEAREST = 1,   /**< Nearest interpolation method */
  V4L2_INTERPOLATION_BILINEAR = 2,  /**< Bi-Linear interpolation method */
  V4L2_INTERPOLATION_5_TAP = 3,     /**< 5-Tap interpolation method */
  V4L2_INTERPOLATION_10_TAP = 4,    /**< 10-Tap interpolation method */
  V4L2_INTERPOLATION_SMART = 5,     /**< Smart interpolation method */
  V4L2_INTERPOLATION_NICEST = 6,    /**< Nicest interpolation method */
};

/**
 * Specifies the types of TNR algorithms.
 */
enum v4l2_tnr_algorithm {
  V4L2_TNR_ALGO_ORIGINAL = 0,           /**< Default TNR algorithm. */
  V4L2_TNR_ALGO_OUTDOOR_LOW_LIGHT = 1,  /**< Outdoor Low Light TNR algorithm. */
  V4L2_TNR_ALGO_OUTDOOR_MEDIUM_LIGHT = 2, /**< Outdoor Medium Light TNR algorithm. */
  V4L2_TNR_ALGO_OUTDOOR_HIGH_LIGHT = 3, /**< Outdoor High Light TNR algorithm. */
  V4L2_TNR_ALGO_INDOOR_LOW_LIGHT = 4, /**< Indoor Low Light TNR algorithm. */
  V4L2_TNR_ALGO_INDOOR_MEDIUM_LIGHT = 5, /**< Indoor Medium Light TNR algorithm. */
  V4L2_TNR_ALGO_INDOOR_HIGH_LIGHT = 6, /**< Indoor High Light TNR algorithm. */
};

/**
 * Specifies the types of YUV rescale methods.
 */
enum v4l2_yuv_rescale_method {
  /**< Disable */
  V4L2_YUV_RESCALE_NONE = 0,
  /**< Standard(limited range [16 235]) to extension(full range [0 255]) */
  V4L2_YUV_RESCALE_STD_TO_EXT = 1,
  /**< Extension(full range [0 255] to standard(limited range [16 235]) */
  V4L2_YUV_RESCALE_EXT_TO_STD = 2,
};

typedef struct v4l2_ctrl_video_displaydata_
{
    __u32 masteringdisplaydatapresent;
}v4l2_ctrl_video_displaydata;

/**
 * HDR Metadata.
 */

typedef struct _v4l2_ctrl_video_hdrmasteringdisplaydata
{
    // idx 0 : G, 1 : B, 2 : R
    __u16 display_primaries_x[3];       // normalized x chromaticity cordinate. It shall be in the range of 0 to 50000
    __u16 display_primaries_y[3];       // normalized y chromaticity cordinate. It shall be in the range of 0 to 50000
    __u16 white_point_x;    // normalized x chromaticity cordinate of white point of mastering display
    __u16 white_point_y;    // normalized y chromaticity cordinate of white point of mastering display
    __u32 max_display_parameter_luminance;      // nominal maximum display luminance in units of 0.0001 candelas per square metre
    __u32 min_display_parameter_luminance;      // nominal minimum display luminance in units of 0.0001 candelas per square metre
} v4l2_ctrl_video_hdrmasteringdisplaydata;


/**
 * Poll device
 */
typedef struct _v4l2_ctrl_video_device_poll
{
    __u16 req_events;    // Requested events, a bitmask of POLLIN, POLLOUT, POLLERR, POLLPRI.
    __u16 resp_events;    // Returned events a similar bitmask of above events.
} v4l2_ctrl_video_device_poll;

/** @} */

/**
 * @defgroup V4L2 V4L2 over IPC
 *
 * @brief NVIDIA V4L2 IPC Description and Extensions
 *
 */
/** @addtogroup V4L2 */
/** @{ */

typedef enum
{
    /** Specifies an invalid color format. */
    V4L2_COLORFORMAT_INVALID,
    /** Specifies 8 bit GRAY scale - single plane */
    V4L2_COLORFORMAT_GRAY8,
    /** Specifies BT.601 colorspace - YUV420 multi-planar. */
    V4L2_COLORFORMAT_YUV420,
    /** Specifies BT.601 colorspace - YUV420 multi-planar. */
    V4L2_COLORFORMAT_YVU420,
    /** Specifies BT.601 colorspace - YUV420 ER multi-planar. */
    V4L2_COLORFORMAT_YUV420_ER,
    /** Specifies BT.601 colorspace - YVU420 ER multi-planar. */
    V4L2_COLORFORMAT_YVU420_ER,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV12,
    /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV12_ER,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV21,
    /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV21_ER,
    /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
    V4L2_COLORFORMAT_UYVY,
    /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
    V4L2_COLORFORMAT_UYVY_ER,
    /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
    V4L2_COLORFORMAT_VYUY,
    /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
    V4L2_COLORFORMAT_VYUY_ER,
    /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
    V4L2_COLORFORMAT_YUYV,
    /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
    V4L2_COLORFORMAT_YUYV_ER,
    /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
    V4L2_COLORFORMAT_YVYU,
    /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
    V4L2_COLORFORMAT_YVYU_ER,
    /** Specifies BT.601 colorspace - YUV444 multi-planar. */
    V4L2_COLORFORMAT_YUV444,
    /** Specifies RGBA-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_RGBA,
    /** Specifies BGRA-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_BGRA,
    /** Specifies ARGB-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_ARGB,
    /** Specifies ABGR-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_ABGR,
    /** Specifies RGBx-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_RGBx,
    /** Specifies BGRx-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_BGRx,
    /** Specifies xRGB-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_xRGB,
    /** Specifies xBGR-8-8-8-8 single plane. */
    V4L2_COLORFORMAT_xBGR,
    /** Specifies RGB-8-8-8 single plane. */
    V4L2_COLORFORMAT_RGB,
    /** Specifies BGR-8-8-8 single plane. */
    V4L2_COLORFORMAT_BGR,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_10LE,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 12-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_12LE,
    /** Specifies BT.709 colorspace - YUV420 multi-planar. */
    V4L2_COLORFORMAT_YUV420_709,
    /** Specifies BT.709 colorspace - YUV420 ER multi-planar. */
    V4L2_COLORFORMAT_YUV420_709_ER,
    /** Specifies BT.709 colorspace - Y/CbCr 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV12_709,
    /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV12_709_ER,
    /** Specifies BT.2020 colorspace - YUV420 multi-planar. */
    V4L2_COLORFORMAT_YUV420_2020,
    /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 multi-planar. */
    V4L2_COLORFORMAT_NV12_2020,
    /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_10LE_ER,
    /** Specifies BT.709 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_10LE_709,
    /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_10LE_709_ER,
    /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_10LE_2020,
    /** Specifies color format for packed 2 signed shorts  */
    V4L2_COLORFORMAT_SIGNED_R16G16,
    /** Specifies RGB- unsigned 8 bit multiplanar plane. */
    V4L2_COLORFORMAT_R8_G8_B8,
    /** Specifies BGR- unsigned 8 bit multiplanar plane. */
    V4L2_COLORFORMAT_B8_G8_R8,
    /** Specifies RGB-32bit Floating point multiplanar plane. */
    V4L2_COLORFORMAT_R32F_G32F_B32F,
    /** Specifies BGR-32bit Floating point multiplanar plane. */
    V4L2_COLORFORMAT_B32F_G32F_R32F,
    /** Specifies BT.601 colorspace - YUV422 multi-planar. */
    V4L2_COLORFORMAT_YUV422,
    /** Specifies BT.601 colorspace - Y/CrCb 4:2:0 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV21_10LE,
    /** Specifies BT.601 colorspace - Y/CrCb 4:2:0 12-bit multi-planar. */
    V4L2_COLORFORMAT_NV21_12LE,
    /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 12-bit multi-planar. */
    V4L2_COLORFORMAT_NV12_12LE_2020,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:2 multi-planar. */
    V4L2_COLORFORMAT_NV16,
    /** Specifies BT.601 colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
    V4L2_COLORFORMAT_NV16_10LE,
    /** Specifies BT.601 colorspace - Y/CbCr 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24,
    /** Specifies BT.601 colorspace - Y/CrCb 4:4:4 10-bit multi-planar. */
    V4L2_COLORFORMAT_NV24_10LE,
    /** Specifies BT.601_ER colorspace - Y/CbCr 4:2:2 multi-planar. */
    V4L2_COLORFORMAT_NV16_ER,
    /** Specifies BT.601_ER colorspace - Y/CbCr 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_ER,
    /** Specifies BT.709 colorspace - Y/CbCr 4:2:2 multi-planar. */
    V4L2_COLORFORMAT_NV16_709,
    /** Specifies BT.709 colorspace - Y/CbCr 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_709,
    /** Specifies BT.709_ER colorspace - Y/CbCr 4:2:2 multi-planar. */
    V4L2_COLORFORMAT_NV16_709_ER,
    /** Specifies BT.709_ER colorspace - Y/CbCr 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_709_ER,
    /** Specifies BT.709 colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_10LE_709,
    /** Specifies BT.709 ER colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_10LE_709_ER,
    /** Specifies BT.2020 colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_10LE_2020,
    /** Specifies BT.2020 colorspace - Y/CbCr 12 bit 4:4:4 multi-planar. */
    V4L2_COLORFORMAT_NV24_12LE_2020,
    /** Specifies Non-linear RGB BT.709 colorspace - RGBA-10-10-10-2 planar. */
    V4L2_COLORFORMAT_RGBA_10_10_10_2_709,
    /** Specifies Non-linear RGB BT.2020 colorspace - RGBA-10-10-10-2 planar. */
    V4L2_COLORFORMAT_RGBA_10_10_10_2_2020,
    /** Specifies Non-linear RGB BT.709 colorspace - BGRA-10-10-10-2 planar. */
    V4L2_COLORFORMAT_BGRA_10_10_10_2_709,
    /** Specifies Non-linear RGB BT.2020 colorspace - BGRA-10-10-10-2 planar. */
    V4L2_COLORFORMAT_BGRA_10_10_10_2_2020,
    /** Specifies Optical flow SAD calculation Buffer format */
    V4L2_COLORFORMAT_A32,
    /** Specifies BT.601 colorspace - 10 bit YUV 4:2:2 interleaved. */
    V4L2_COLORFORMAT_UYVP,
    /** Specifies BT.601 colorspace - 10 bit YUV ER 4:2:2 interleaved. */
    V4L2_COLORFORMAT_UYVP_ER,

    V4L2_COLORFORMAT_LAST
} v4l2_color_format;

/**
 * Defines buffer surface layout.
 */
typedef enum
{
    /** Pitch linear layout. */
    V4L2_LAYOUT_PITCH,
    /** Block linear layout. */
    V4L2_LAYOUT_BLOCK_LINEAR,
} v4l2_surface_layout;

/**
 * Defines Display scan formats.
 */
typedef enum
{
    /** Progessive scan formats. */
    V4L2_DISPLAY_SCAN_FORMAT_PROGRESSIVE = 0,
    /** Interlaced scan formats. */
    V4L2_DISPLAY_SCAN_FORMAT_INTERLACED,
} v4l2_displayscan_format;

/**
 * Defined memory types for buffer.
 */
typedef enum
{
    V4L2_MEM_DEFAULT,
    /** CUDA Host memory type. */
    V4L2_MEM_CUDA_PINNED,
    /** CUDA Device memory type. */
    V4L2_MEM_CUDA_DEVICE,
    /** CUDA Unified memory type. */
    V4L2_MEM_CUDA_UNIFIED,
    /** NVRM Surface Array type. Valid only for Jetson. */
    V4L2_MEM_SURFACE_ARRAY,
    /** NVRM Handle type. Valid only for Jetson. */
    V4L2_MEM_HANDLE,
    /** Memory allocated by malloc() */
    V4L2_MEM_SYSTEM,
} v4l2_buffer_mem_type;

/**
 * Holds Chroma Subsampling parameters.
 */
typedef struct _v4l2_chroma_subsampling_params
{
    /** location settings */
    __u8 chromaloc_horiz;
    __u8 chromaloc_vert;
} v4l2_chroma_subsampling_params;

/**
 * Holds parameters for a hardware buffer.
 */
typedef struct _v4l2_map_plane
{
    /** width of each planes of hardware buffer. */
    __u32 width;
    /** height of each planes of hardware buffer. */
    __u32 height;
    /** pitch of each planes of hardware buffer. */
    __u32 pitch;
    /** memory offset values of each video planes of hardware buffer. */
    __u32 offset;
    /** size of each video planes of hardware buffer. */
    __u32 psize;
    /** block height of the planes for blockLinear layout buffer */
    __u32 blockheightlog2;
    /** offset of the second field for interlaced buffer */
    __u32 secondfieldoffset;
    /** flags associated with the planes */
    __u64 flags;

    __u8 reserved[64];
} v4l2_map_plane;

struct v4l2_map_buffer
{
    __u32 index;
    __u32 type;
    /** number of planes of hardware buffer. */
    __u32 num_planes;
    /** GPU ID */
    __u32 gpuid;
    /** DMABUF FD */
    __u64 fd;
    /** total size of allocated memory */
    __u32 total_size;
    /** type of memory */
    v4l2_buffer_mem_type memtype;
    /** BL or PL layout */
    v4l2_surface_layout layout;
    /** display scan format */
    v4l2_displayscan_format scanformat;
    /** color format */
    v4l2_color_format colorformat;
    /** Holds chroma subsampling parameters */
    v4l2_chroma_subsampling_params chromasubsampling;
    /** plane parameters */
    v4l2_map_plane *map_planes;

    __u8 reserved[64];
};

#define V4L2_IPC_MAP_BUFFER _IOWR('V', BASE_VIDIOC_PRIVATE + 1, struct v4l2_map_buffer)
#define V4L2_IPC_UNMAP_BUFFER _IOWR('V', BASE_VIDIOC_PRIVATE + 2, struct v4l2_map_buffer)

/** @} */

#endif /*__V4L2_NV_EXTENSIONS_H__*/

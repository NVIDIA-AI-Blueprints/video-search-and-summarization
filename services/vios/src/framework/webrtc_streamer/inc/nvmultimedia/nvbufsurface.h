/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */

/**
 * @file nvbufsurface.h
 * <b>NvBufSurface Interface </b>
 *
 * This file specifies the NvBufSurface management API.
 *
 * The NvBufSurface API provides methods to allocate / deallocate, map / unmap
 * and copy batched buffers.
 */
 /**
 * @defgroup  ds_nvbuf_api Buffer Management API module
 *
 * This section describes types and functions of NvBufSurface application
 * programming interface.
 *
 */

#ifndef NVBUFSURFACE_H_
#define NVBUFSURFACE_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C"
{
#endif

/** @defgroup ds_aaa NvBufSurface Types and Functions
 * Defines types and functions of \ref NvBufSurface application
 * programming interface.
 * @ingroup ds_nvbuf_api
 * @{ */

/** Defines the default padding length for reserved fields of structures. */
#define STRUCTURE_PADDING  4

/** Defines the maximum number of planes. */
#define NVBUF_MAX_PLANES   4

/**
  * Defines the default values for chroma subsampling.
  * The default value matches JPEG/MPEG use cases.
  */
#define NVBUFSURFACE_CHROMA_SUBSAMPLING_HORIZ_DEFAULT 0
#define NVBUFSURFACE_CHROMA_SUBSAMPLING_VERT_DEFAULT 1

#define NVBUFSURFACE_CHROMA_SUBSAMPLING_PARAMS_DEFAULT \
  { \
    NVBUFSURFACE_CHROMA_SUBSAMPLING_HORIZ_DEFAULT, \
    NVBUFSURFACE_CHROMA_SUBSAMPLING_VERT_DEFAULT \
  }

/**
 *  Defines mapping types of NvBufSurface.
 */
typedef enum
{
  NVBUF_MAP_READ,       /**< Specifies \ref NvBufSurface mapping type "read." */
  NVBUF_MAP_WRITE,      /**< Specifies \ref NvBufSurface mapping type
                            "write." */
  NVBUF_MAP_READ_WRITE, /**< Specifies \ref NvBufSurface mapping type
                            "read/write." */
} NvBufSurfaceMemMapFlags;

/**
  * Defines tags that identify the components requesting a memory allocation.
  * The tags can be used later to identify the total memory allocated to
  * particular types of components.
  * TODO: Check if DeepStream require more tags to be defined.
  */
typedef enum
{
  /** tag None. */
  NvBufSurfaceTag_NONE            = 0x0,
  /** tag for Camera. */
  NvBufSurfaceTag_CAMERA          = 0x200,
  /** tag for Jpeg Encoder/Decoder. */
  NvBufSurfaceTag_JPEG            = 0x1500,
  /** tag for VPR Buffers. */
  NvBufSurfaceTag_PROTECTED       = 0x1504,
  /** tag for H264/H265 Video Encoder. */
  NvBufSurfaceTag_VIDEO_ENC       = 0x1200,
  /** tag for H264/H265/VP9 Video Decoder. */
  NvBufSurfaceTag_VIDEO_DEC       = 0x1400,
  /** tag for Video Transform/Composite/Blend. */
  NvBufSurfaceTag_VIDEO_CONVERT   = 0xf01,
} NvBufSurfaceTag;

/**
 * Defines color formats for NvBufSurface.
 */
typedef enum
{
  /** Specifies an invalid color format. */
  NVBUF_COLOR_FORMAT_INVALID,
  /** Specifies 8 bit GRAY scale - single plane */
  NVBUF_COLOR_FORMAT_GRAY8,
  /** Specifies BT.601 colorspace - YUV420 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV420,
  /** Specifies BT.601 colorspace - YUV420 multi-planar. */
  NVBUF_COLOR_FORMAT_YVU420,
  /** Specifies BT.601 colorspace - YUV420 ER multi-planar. */
  NVBUF_COLOR_FORMAT_YUV420_ER,
  /** Specifies BT.601 colorspace - YVU420 ER multi-planar. */
  NVBUF_COLOR_FORMAT_YVU420_ER,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV12,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_ER,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV21,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV21_ER,
  /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_UYVY,
  /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_UYVY_ER,
  /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_VYUY,
  /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_VYUY_ER,
  /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_YUYV,
  /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_YUYV_ER,
  /** Specifies BT.601 colorspace - YUV 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_YVYU,
  /** Specifies BT.601 colorspace - YUV ER 4:2:2 planar. */
  NVBUF_COLOR_FORMAT_YVYU_ER,
  /** Specifies BT.601 colorspace - YUV444 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444,
  /** Specifies RGBA-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_RGBA,
  /** Specifies BGRA-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_BGRA,
  /** Specifies ARGB-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_ARGB,
  /** Specifies ABGR-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_ABGR,
  /** Specifies RGBx-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_RGBx,
  /** Specifies BGRx-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_BGRx,
  /** Specifies xRGB-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_xRGB,
  /** Specifies xBGR-8-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_xBGR,
  /** Specifies RGB-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_RGB,
  /** Specifies BGR-8-8-8 single plane. */
  NVBUF_COLOR_FORMAT_BGR,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_10LE,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_12LE,
  /** Specifies BT.709 colorspace - YUV420 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV420_709,
  /** Specifies BT.709 colorspace - YUV420 ER multi-planar. */
  NVBUF_COLOR_FORMAT_YUV420_709_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_709_ER,
  /** Specifies BT.2020 colorspace - YUV420 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV420_2020,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_2020,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_10LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_10LE_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_10LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_10LE_2020,
  /** Specifies color format for packed 2 signed shorts  */
  NVBUF_COLOR_FORMAT_SIGNED_R16G16,
  /** Specifies RGB- unsigned 8 bit multiplanar plane. */
  NVBUF_COLOR_FORMAT_R8_G8_B8,
  /** Specifies BGR- unsigned 8 bit multiplanar plane. */
  NVBUF_COLOR_FORMAT_B8_G8_R8,
  /** Specifies RGB-32bit Floating point multiplanar plane. */
  NVBUF_COLOR_FORMAT_R32F_G32F_B32F,
  /** Specifies BGR-32bit Floating point multiplanar plane. */
  NVBUF_COLOR_FORMAT_B32F_G32F_R32F,
  /** Specifies BT.601 colorspace - YUV422 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV422,
  /** Specifies BT.601 colorspace - Y/CrCb 4:2:0 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV21_10LE,
  /** Specifies BT.601 colorspace - Y/CrCb 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV21_12LE,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_12LE_2020,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:2 multi-planar. */
  NVBUF_COLOR_FORMAT_NV16,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_10LE,
  /** Specifies BT.601 colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24,
  /** Specifies BT.601 colorspace - Y/CrCb 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_10LE,
  /** Specifies BT.601_ER colorspace - Y/CbCr 4:2:2 multi-planar. */
  NVBUF_COLOR_FORMAT_NV16_ER,
  /** Specifies BT.601_ER colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:2 multi-planar. */
  NVBUF_COLOR_FORMAT_NV16_709,
  /** Specifies BT.709 colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_709,
  /** Specifies BT.709_ER colorspace - Y/CbCr 4:2:2 multi-planar. */
  NVBUF_COLOR_FORMAT_NV16_709_ER,
  /** Specifies BT.709_ER colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_709_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_10LE_709,
  /** Specifies BT.709 ER colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_10LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 10 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_10LE_2020,
  /** Specifies BT.2020 colorspace - Y/CbCr 12 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_12LE_2020,
  /** Specifies Non-linear RGB BT.709 colorspace - RGBA-10-10-10-2 planar. */
  NVBUF_COLOR_FORMAT_RGBA_10_10_10_2_709,
  /** Specifies Non-linear RGB BT.2020 colorspace - RGBA-10-10-10-2 planar. */
  NVBUF_COLOR_FORMAT_RGBA_10_10_10_2_2020,
  /** Specifies Non-linear RGB BT.709 colorspace - BGRA-10-10-10-2 planar. */
  NVBUF_COLOR_FORMAT_BGRA_10_10_10_2_709,
  /** Specifies Non-linear RGB BT.2020 colorspace - BGRA-10-10-10-2 planar. */
  NVBUF_COLOR_FORMAT_BGRA_10_10_10_2_2020,
  /** Specifies Optical flow SAD calculation Buffer format */
  NVBUF_COLOR_FORMAT_A32,
  /** Specifies BT.601 colorspace - 10 bit YUV 4:2:2 interleaved. */
  NVBUF_COLOR_FORMAT_UYVP,
  /** Specifies BT.601 colorspace - 10 bit YUV ER 4:2:2 interleaved. */
  NVBUF_COLOR_FORMAT_UYVP_ER,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_2020,
  /** Specifies BT.601 colorspace - Y/CbCr 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_10LE,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_10LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_10LE_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_10LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_10LE_2020,
  /** Specifies BT.601 colorspace - Y/CbCr 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_12LE,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_12LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_12LE_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_12LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_YUV444_12LE_2020,
  /** Specifies BT.601 colorspace - Y/CbCr ER 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_12LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_12LE_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:0 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV12_12LE_709_ER,
  /** Specifies 8 bit GRAY scale ER - single plane */
  NVBUF_COLOR_FORMAT_GRAY8_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:2 planar */
  NVBUF_COLOR_FORMAT_UYVY_709,
  /** Specifies BT.709 colorspace - Y/CbCr ER 4:2:2 planar */
  NVBUF_COLOR_FORMAT_UYVY_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:2 planar */
  NVBUF_COLOR_FORMAT_UYVY_2020,
  /** Specifies 16 bit GRAY scale - single plane */
  NVBUF_COLOR_FORMAT_GRAY16_LE,
  /** Specifies 64 bit BGRA (B16 G16 R16 A16) interleaved */
  NVBUF_COLOR_FORMAT_BGRA64_LE,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:2 multi-planar. */
  NVBUF_COLOR_FORMAT_NV16_2020,
  /** Specifies BT.601_ER colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_10LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_10LE_709,
  /** Specifies BT.709_ER colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_10LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:2 10-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_10LE_2020,
  /** Specifies BT.601 colorspace - Y/CbCr 4:2:2 12-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_12LE,
  /** Specifies BT.601_ER colorspace - Y/CbCr 4:2:2 12-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_12LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 4:2:2 12-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_12LE_709,
  /** Specifies BT.709_ER colorspace - Y/CbCr 4:2:2 12-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_12LE_709_ER,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:2:2 12-bit semi-planar. */
  NVBUF_COLOR_FORMAT_NV16_12LE_2020,
  /** Specifies BT.2020 colorspace - Y/CbCr 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_2020,
  /** Specifies BT.601 ER colorspace - Y/CbCr 4:4:4 10-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_10LE_ER,
  /** Specifies BT.601 colorspace - Y/CbCr 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_12LE,
  /** Specifies BT.601 ER colorspace - Y/CbCr 4:4:4 12-bit multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_12LE_ER,
  /** Specifies BT.709 colorspace - Y/CbCr 12 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_12LE_709,
  /** Specifies BT.709 ER colorspace - Y/CbCr 12 bit 4:4:4 multi-planar. */
  NVBUF_COLOR_FORMAT_NV24_12LE_709_ER,
  /** Specifies BT.709 colorspace - 10 bit YUV 4:2:2 interleaved. */
  NVBUF_COLOR_FORMAT_UYVP_709,
  /** Specifies BT.709 colorspace - 10 bit YUV ER 4:2:2 interleaved. */
  NVBUF_COLOR_FORMAT_UYVP_709_ER,
  /** Specifies BT.2020 colorspace - 10 bit YUV 4:2:2 interleaved. */
  NVBUF_COLOR_FORMAT_UYVP_2020,
  NVBUF_COLOR_FORMAT_LAST
} NvBufSurfaceColorFormat;

/**
 * Specifies layout formats for \ref NvBufSurface video planes.
 */
typedef enum
{
  /** Specifies pitch layout. */
  NVBUF_LAYOUT_PITCH,
  /** Specifies block linear layout. */
  NVBUF_LAYOUT_BLOCK_LINEAR,
} NvBufSurfaceLayout;

/**
 * Specifies memory types for \ref NvBufSurface.
 */
typedef enum
{
  /** Specifies the default memory type, i.e. \ref NVBUF_MEM_CUDA_DEVICE
   for dGPU, \ref NVBUF_MEM_SURFACE_ARRAY for Jetson. Use \ref NVBUF_MEM_DEFAULT
   to allocate whichever type of memory is appropriate for the platform. */
  NVBUF_MEM_DEFAULT,
  /** Specifies CUDA Host memory type. */
  NVBUF_MEM_CUDA_PINNED,
  /** Specifies CUDA Device memory type. */
  NVBUF_MEM_CUDA_DEVICE,
  /** Specifies CUDA Unified memory type. */
  NVBUF_MEM_CUDA_UNIFIED,
  /** Specifies NVRM Surface Array type. Valid only for Jetson. */
  NVBUF_MEM_SURFACE_ARRAY,
  /** Specifies NVRM Handle type. Valid only for Jetson. */
  NVBUF_MEM_HANDLE,
  /** Specifies memory allocated by malloc(). */
  NVBUF_MEM_SYSTEM,
  /** Specifies CUDA Array memory type. Valid only for Jetson. */
  NVBUF_MEM_CUDA_ARRAY,
} NvBufSurfaceMemType;

/**
  * Defines display scan formats for NvBufSurface video planes.
  */
typedef enum
{
  /** Progessive scan formats. */
  NVBUF_DISPLAYSCANFORMAT_PROGRESSIVE,
  /** Interlaced scan formats. */
  NVBUF_DISPLAYSCANFORMAT_INTERLACED,
} NvBufSurfaceDisplayScanFormat;

/**
 * Holds plane wise parameters(extended) of a buffer.
 */
typedef struct NvBufSurfacePlaneParamsEx
{
  /** display scan format - progressive/interlaced. */
  NvBufSurfaceDisplayScanFormat scanformat[NVBUF_MAX_PLANES];
  /** offset of the second field for interlaced buffer. */
  uint32_t secondfieldoffset[NVBUF_MAX_PLANES];
  /** block height of the planes for blockLinear layout buffer. */
  uint32_t blockheightlog2[NVBUF_MAX_PLANES];
  /** physical address of allocated planes. */
  uint32_t physicaladdress[NVBUF_MAX_PLANES];
  /** flags associated with planes */
  uint64_t flags[NVBUF_MAX_PLANES];
  /** DRM modifier for plane */
  uint64_t drmModifier[NVBUF_MAX_PLANES];
  /** Holds the reserved space for future use. */
  void * _reserved[STRUCTURE_PADDING * NVBUF_MAX_PLANES];
} NvBufSurfacePlaneParamsEx;

/**
 * Holds plane wise parameters of a buffer.
 */
typedef struct NvBufSurfacePlaneParams
{
  /** Holds the number of planes. */
  uint32_t num_planes;
  /** Holds the widths of planes. */
  uint32_t width[NVBUF_MAX_PLANES];
  /** Holds the heights of planes. */
  uint32_t height[NVBUF_MAX_PLANES];
  /** Holds the pitches of planes in bytes. */
  uint32_t pitch[NVBUF_MAX_PLANES];
  /** Holds the offsets of planes in bytes. */
  uint32_t offset[NVBUF_MAX_PLANES];
  /** Holds the sizes of planes in bytes. */
  uint32_t psize[NVBUF_MAX_PLANES];
  /** Holds the number of bytes occupied by a pixel in each plane. */
  uint32_t bytesPerPix[NVBUF_MAX_PLANES];
  /** Holds the reserved space for future use. */
  void * _reserved[STRUCTURE_PADDING * NVBUF_MAX_PLANES];
} NvBufSurfacePlaneParams;


/**
  * Holds Chroma Subsampling parameters for NvBufSurface allocation.
  * The members chromaLocHoriz and chromaLocVert accept these values:
  * 0: Left horizontal or top vertical position
  * 1: Center horizontal or center vertical position
  * 2: Right horizontal or bottom vertical position
  */
typedef struct NvBufSurfaceChromaSubsamplingParams
{
  /** location settings */
  uint8_t chromaLocHoriz;
  uint8_t chromaLocVert;
  /** Reserved for alignment */
  uint8_t _reserved[6];
} NvBufSurfaceChromaSubsamplingParams;

/**
 * Holds parameters required to allocate an \ref NvBufSurface.
 */
typedef struct NvBufSurfaceCreateParams {
  /** Holds the GPU ID. Valid only for a multi-GPU system. */
  uint32_t gpuId;
  /** Holds the width of the buffer. */
  uint32_t width;
  /** Holds the height of the buffer. */
  uint32_t height;
  /** Holds the amount of memory to be allocated. Optional; if set, all other
   parameters (width, height, etc.) are ignored. */
  uint32_t size;
  /** Holds a "contiguous memory" flag. If set, contiguous memory is allocated
   for the batch. Valid only for CUDA memory types. */
  bool isContiguous;
  /** Holds the color format of the buffer. */
  NvBufSurfaceColorFormat colorFormat;
  /** Holds the surface layout. May be Block Linear (BL) or Pitch Linear (PL).
   For a dGPU, only PL is valid. */
  NvBufSurfaceLayout layout;
  /** Holds the type of memory to be allocated. */
  NvBufSurfaceMemType memType;
  /** Holds the reserved space for future use. */
  void * _reserved[STRUCTURE_PADDING];
} NvBufSurfaceCreateParams;

/**
 * Hold extended parameters required to allocate NvBufSurface.
 * (Applicable for NvBufSurfaceAllocate API)
 */
typedef struct NvBufSurfaceAllocateParams {
  /** Hold legacy NvBufSurface creation parameters */
  NvBufSurfaceCreateParams params;
  /** Display scan format */
  NvBufSurfaceDisplayScanFormat displayscanformat;
  /** Chroma Subsampling parameters */
  NvBufSurfaceChromaSubsamplingParams chromaSubsampling;
  /** components tag to be used for memory allocation */
  NvBufSurfaceTag memtag;
  /** disable pitch padding allocation only applicable for cuda and system memory allocation
     pitch would be width times bytes per pixel for the plane, for odd width it would be
     multiple of 2, also note for some non standard video resolution cuda kernels may fail
     due to unaligned pitch
   */
  bool disablePitchPadding;
  /** Used void* from custom param for 64 bit machine, using other uint32_t param */
  uint32_t _reservedParam;
  /** Holds the reserved space for future use. */
  void * _reserved[STRUCTURE_PADDING];
} NvBufSurfaceAllocateParams;

/**
 * Hold the pointers of mapped buffer.
 */
typedef struct NvBufSurfaceMappedAddr {
  /** Holds planewise pointers to a CPU mapped buffer. */
  void * addr[NVBUF_MAX_PLANES];
  /** Holds a pointer to a mapped EGLImage. */
  void *eglImage;
  /** Holds a pointer to a mapped NVRM memory */
  void *nvmmPtr;
  /** Holds a pointer to a mapped CUDA memory */
  void *cudaPtr;
  /** Holds the reserved space for future use. */
  void * _reserved[STRUCTURE_PADDING];
} NvBufSurfaceMappedAddr;

/**
 * Hold the information(extended) of single buffer in the batch.
 */
typedef struct NvBufSurfaceParamsEx {
  /** offset in bytes from the start of the buffer to the first valid byte.
      (Applicable for NVBUF_MEM_HANDLE) */
  int32_t startofvaliddata;
  /** size of the valid data from the first to the last valid byte.
      (Applicable for NVBUF_MEM_HANDLE) */
  int32_t sizeofvaliddatainbytes;
  /** chroma subsampling parameters.
      (Applicable for NVBUF_MEM_SURFACE_ARRAY) */
  NvBufSurfaceChromaSubsamplingParams chromaSubsampling;
  /** get buffer vpr information. */
  bool is_protected;
  /** plane wise extended info */
  NvBufSurfacePlaneParamsEx planeParamsex;

  void * _reserved[STRUCTURE_PADDING];
} NvBufSurfaceParamsEx;

/**
 * Holds information of CUDA buffer.
 * Applicable for tegra OpenRM only.
 */
typedef struct NvBufSurfaceCudaBuffer {
  /**
   * Holds a base pointer to allocated CUDA memory.
   * It is different from dataPtr when CUDA allocated
   * address is not page aligned for image buffers.
   * It is same as dataPtr for other buffers.
   */
  void *basePtr;
  /**
   * Holds a page aligned data pointer to CUDA memory for image buffers
   * if CUDA allocated address is not page aligned.
   * It is same as basePtr for other buffers.
   */
  void *dataPtr;
  /** Holds a pointer to external CUDA memory for imported CUDA buffers */
  void *extMem;
  /** Holds a pointer to external CUDA mipmaped array for imported CUDA buffers */
  void *mipmap;
  /** Reserved */
  uint8_t reserved[64];
} NvBufSurfaceCudaBuffer;

/**
 * Hold the information of single buffer in the batch.
 */
typedef struct NvBufSurfaceParams {
  /** Holds the width of the buffer. */
  uint32_t width;
  /** Holds the height of the buffer. */
  uint32_t height;
  /** Holds the pitch of the buffer. */
  uint32_t pitch;
  /** Holds the color format of the buffer. */
  NvBufSurfaceColorFormat colorFormat;
  /** Holds BL or PL. For dGPU, only PL is valid. */
  NvBufSurfaceLayout layout;
  /** Holds a DMABUF FD. Valid only for \ref NVBUF_MEM_SURFACE_ARRAY and
   \ref NVBUF_MEM_HANDLE type memory. */
  uint64_t bufferDesc;
  /** Holds the amount of allocated memory. */
  uint32_t dataSize;
  /** Holds a pointer to allocated memory. Not valid for
   \ref NVBUF_MEM_SURFACE_ARRAY or \ref NVBUF_MEM_HANDLE. */
  void * dataPtr;
  /** Holds planewise information (width, height, pitch, offset, etc.). */
  NvBufSurfacePlaneParams planeParams;
  /** Holds pointers to mapped buffers. Initialized to NULL
   when the structure is created. */
  NvBufSurfaceMappedAddr mappedAddr;
  /** pointers of extended parameters of single buffer in the batch.*/
  NvBufSurfaceParamsEx *paramex;
  /** Holds a pointer to CUDA buffer. Applicable for CUDA Device, CUDA Host and CUDA Array memory on tegra OpenRM.*/
  NvBufSurfaceCudaBuffer *cudaBuffer;

  void * _reserved[STRUCTURE_PADDING];
} NvBufSurfaceParams;

/**
 * Holds information about batched buffers.
 */
typedef struct NvBufSurface {
  /** Holds a GPU ID. Valid only for a multi-GPU system. */
  uint32_t gpuId;
  /** Holds the batch size. */
  uint32_t batchSize;
  /** Holds the number valid and filled buffers. Initialized to zero when
   an instance of the structure is created. */
  uint32_t numFilled;
  /** Holds an "is contiguous" flag. If set, memory allocated for the batch
   is contiguous. */
  bool isContiguous;
  /** Holds type of memory for buffers in the batch. */
  NvBufSurfaceMemType memType;
  /** Holds a pointer to an array of batched buffers. */
  NvBufSurfaceParams *surfaceList;
  /** Holds a flag for Imported buffer. */
  bool isImportedBuf;

  void * _reserved[STRUCTURE_PADDING];
} NvBufSurface;

/**
 * Holds plane parameters to map the buffer received from another process.
 */
typedef struct NvBufSurfaceMapPlaneParams
{
  /** Holds the widths of planes */
  uint32_t width;
  /** Holds the heights of planes */
  uint32_t height;
  /** Holds the pitches of planes in bytes */
  uint32_t pitch;
  /** Holds the offsets of planes in bytes */
  uint32_t offset;
  /** Holds the sizes of planes in bytes */
  uint32_t psize;
  /** Holds offset of the second field for interlaced buffer */
  uint32_t secondfieldoffset;
  /** Holds block height of the planes for blockLinear layout buffer */
  uint32_t blockheightlog2;
  /** Holds flags associated with the planes */
  uint64_t flags;
  /** Reserved */
  uint8_t reserved[64];
} NvBufSurfaceMapPlaneParams;

/**
 * CUDA IPC memory handle for NvBufSurface
 */
typedef struct NvBufSurfaceCudaIpcMemHandle_t
{
  char reserved[64];
} NvBufSurfaceCudaIpcMemHandle;

/**
 * The extended map parameters NvBufSurface
 */
typedef struct NvBufSurfaceExtendedMapParams_t
{
  NvBufSurfaceCudaIpcMemHandle memHandle;
  void *reserved[64];
} NvBufSurfaceExtendedMapParams;

/**
 * Holds buffer parameters to map the buffer received from another process.
 */
typedef struct NvBufSurfaceMapParams {
  /** Holds the number of planes. */
  uint32_t num_planes;
  /** Holds a GPU ID */
  uint32_t gpuId;
  /** Holds a DMABUF FD */
  uint64_t fd;
  /** Holds the total size of allocated memory */
  uint32_t totalSize;
  /** Holds type of memory */
  NvBufSurfaceMemType memType;
  /** Holds BL or PL layout */
  NvBufSurfaceLayout layout;
  /** Holds display scan format */
  NvBufSurfaceDisplayScanFormat scanformat;
  /** Holds the color format */
  NvBufSurfaceColorFormat colorFormat;
  /** Holds chroma subsampling parameters */
  NvBufSurfaceChromaSubsamplingParams chromaSubsampling;
  /** Holds plane parameters */
  NvBufSurfaceMapPlaneParams planes[NVBUF_MAX_PLANES];
  /** Holds the extended Map parameters */
  void *extendedMapParams;
  /** Holds the reserved space for future use. */
  void *_reserved[STRUCTURE_PADDING];
} NvBufSurfaceMapParams;

/**
 * Holds information about mapped CUDA buffer
 */
typedef struct NvBufSurfaceNvmmBuffer {
  /** Holds a pointer to mapped nvmm memory */
  void *dataPtr;
  /** Holds a DMABUF FD */
  uint64_t bufferDesc;
  /** Reserved */
  uint8_t reserved[64];
} NvBufSurfaceNvmmBuffer;

/**
 * Defines the type of underlying kernel driver detected for GPU access.
 */
typedef enum {
  NVBUF_DRIVER_TYPE_UNKNOWN = 0,
  NVBUF_DRIVER_TYPE_NVGPU,
  NVBUF_DRIVER_TYPE_RM
} NvBufSurfaceDriverType;

typedef enum {
  NVBUF_PLATFORM_TYPE_UNKNOWN = 0,
  NVBUF_PLATFORM_TYPE_T186 = 1,
  NVBUF_PLATFORM_TYPE_T194 = 2,
  NVBUF_PLATFORM_TYPE_T210 = 3,
  NVBUF_PLATFORM_TYPE_T234 = 4,
  NVBUF_PLATFORM_TYPE_T256 = 5,
  NVBUF_PLATFORM_TYPE_T264 = 6,
} NvBufSurfacePlatformType;

/**
 * Holds information about the underlying device.
 */
typedef struct NvBufSurfaceDeviceInfo {
  /** The detected device type (nvgpu, OpenRM, etc.). */
  NvBufSurfaceDriverType driverType;
  /** Indicates if VIC is present on the platform. */
  bool isVicPresent;
  /** Indicates GPU is integrated GPU or not */
  bool isIntegratedGpu;
  bool reserved1;
  bool reserved2;
  /** Indicates the Jetson platform type */
  NvBufSurfacePlatformType platformType;
  /** Reserved for future use. */
  uint8_t reserved[60];
} NvBufSurfaceDeviceInfo;

typedef enum NvBufSurfaceGPUL2SyncCacheOps
{
    NVBUF_GPU_L2_CACHE_INVALIDATE            = 1,
    NVBUF_GPU_L2_CACHE_FLUSH                 = 2,
    NVBUF_GPU_L2_CACHE_FLUSH_INVALIDATE      = NVBUF_GPU_L2_CACHE_FLUSH | NVBUF_GPU_L2_CACHE_INVALIDATE
} NvBufSurfaceGPUL2SyncCacheOps;

typedef struct NvBufSurfaceGPUL2SyncParams
{
  /** Holds the type of GPU L2 cache sync operation. */
  NvBufSurfaceGPUL2SyncCacheOps type;
  /** Reserved fields for future use. */
  void *reserved[STRUCTURE_PADDING];
} NvBufSurfaceGPUL2SyncParams;

/**
 * \brief  Allocates a batch of buffers.
 *
 * Allocates memory for \a batchSize buffers and returns a pointer to an
 * allocated \ref NvBufSurface. The \a params structure must have
 * the allocation parameters of a single buffer. If \a params.size
 * is set, a buffer of that size is allocated, and all other
 * parameters (width, height, color format, etc.) are ignored.
 *
 * Call NvBufSurfaceDestroy() to free resources allocated by this function.
 *
 * @param[out] surf         An indirect pointer to the allocated batched
 *                           buffers.
 * @param[in]  batchSize    Batch size of buffers.
 * @param[in]  params       A pointer to an \ref NvBufSurfaceCreateParams
 *                           structure.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceCreate (NvBufSurface **surf, uint32_t batchSize,
                        NvBufSurfaceCreateParams *params);

/**
 * \brief  Allocate batch of buffers. (Using extended buffer allocation parameters)
 *
 * Allocates memory for batchSize buffers and returns in *surf a pointer to allocated NvBufSurface.
 * params structure should have allocation parameters of single buffer. If size field in
 * params is set, buffer of that size will be allocated and all other
 * parameters (w, h, color format etc.) will be ignored.
 *
 * Use NvBufSurfaceDestroy to free all the resources.
 *
 * @param[out] surf pointer to allocated batched buffers.
 * @param[in] batchSize batch size of buffers.
 * @param[in] paramsext pointer to NvBufSurfaceAllocateParams structure.
 *
 * @return 0 for success, -1 for failure.
 */
int NvBufSurfaceAllocate (NvBufSurface **surf, uint32_t batchSize,
                          NvBufSurfaceAllocateParams *paramsext);

/**
 * Free the batched buffers previously allocated through NvBufSurfaceCreate.
 *
 * @param[in] surf  A pointer to an \ref NvBufSurface to be freed.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceDestroy (NvBufSurface *surf);

/**
 * \brief  Maps hardware batched buffers to the HOST or CPU address space.
 *
 * Valid for \ref NVBUF_MEM_CUDA_UNIFIED type memory for dGPU and
 * \ref NVBUF_MEM_SURFACE_ARRAY and \ref NVBUF_MEM_HANDLE type memory for
 * Jetson.
 *
 * This function fills an array of pointers at
 * \a surf->surfaceList->mappedAddr->addr.
 * \a surf is a pointer to an \ref NvBufSurface.
 * \a surfaceList is a pointer to an \ref NvBufSurfaceParams.
 * \a mappedAddr is a pointer to an \ref NvBufSurfaceMappedAddr.
 * \a addr is declared as an array of pointers to void, and holds pointers
 * to the buffers.
 *
 * The client must call NvBufSurfaceSyncForCpu() with the virtual address
 * populated by this function before accessing mapped memory in the CPU.
 *
 * After memory mapping is complete, mapped memory modification
 * must be coordinated between the CPU and the hardware device as
 * follows:
 * - CPU: If the CPU modifies mapped memory, the client must call
 *   NvBufSurfaceSyncForDevice() before any hardware device accesses the memory.
 * - Hardware device: If a hardware device modifies mapped memory, the client
 *   must call NvBufSurfaceSyncForCpu() before the CPU accesses the memory.
 *
 * Use NvBufSurfaceUnMap() to unmap buffer(s) and release any resource.
 *
 * @param[in,out] surf  A pointer to an NvBufSurface structure. The function
 *                      stores pointers to the buffers in a descendant of this
 *                      structure; see the notes above.
 * @param[in] index     Index of a buffer in the batch. -1 refers to all buffers
 *                      in the batch.
 * @param[in] plane     Index of a plane in buffer. -1 refers to all planes
 *                      in the buffer.
 * @param[in] type      A flag for mapping type.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceMap (NvBufSurface *surf, int index, int plane, NvBufSurfaceMemMapFlags type);

/**
 * \brief  Unmaps previously mapped buffer(s).
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     Index of a buffer in the batch. -1 indicates
 *                      all buffers in the batch.
 * @param[in] plane     Index of a plane in the buffer. -1 indicates
 *                      all planes in the buffer.
 *
 * @return  0 if successful, or -1 otherwise.
 */
int NvBufSurfaceUnMap (NvBufSurface *surf, int index, int plane);

/**
 * \brief  Copies the content of source batched buffer(s) to destination
 * batched buffer(s).
 *
 * You can use this function to copy source buffer(s) of one memory type
 * to destination buffer(s) of another memory type,
 * e.g. CUDA host to CUDA device, malloc'ed memory to CUDA device, etc.
 *
 * The source and destination \ref NvBufSurface objects must have same
 * buffer and batch size.
 *
 * @param[in] srcSurf   A pointer to the source NvBufSurface structure.
 * @param[in] dstSurf   A pointer to the destination NvBufSurface structure.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceCopy (NvBufSurface *srcSurf, NvBufSurface *dstSurf);

/**
 * \brief Copies the NvBufSurface plane memory content to a raw buffer plane for a specific
 * batched buffer.
 *
 * This function can be used to copy plane memory content from source raw buffer pointer
 * to specific destination batch buffer of supported memory type.
 *
 * @param[in] Surf pointer to NvBufSurface structure.
 * @param[in] index index of buffer in the batch.
 * @param[in] plane index of plane in buffer.
 * @param[in] out_width aligned width of the raw data plane.
 * @param[in] out_height aligned height of the raw data plane.
 * @param[in] ptr pointer to the output raw plane data.
 *
 * @return 0 for success, -1 for failure.
 */
int NvBufSurface2Raw (NvBufSurface *Surf, unsigned int index, unsigned int plane, unsigned int out_width, unsigned int out_height, unsigned char *ptr);

/**
 * \brief Copies the raw buffer plane memory content to the NvBufSurface plane memory of a specific
 * batched buffer.
 *
 * This function can be used to copy plane memory content from batch buffer
 * to specific destination raw buffer pointer.
 *
 * @param[in] ptr pointer to the input raw plane data.
 * @param[in] index index of buffer in the batch.
 * @param[in] plane index of plane in buffer.
 * @param[in] in_width aligned width of the raw data plane.
 * @param[in] in_height aligned height of the raw data plane.
 * @param[in] Surf pointer to NvBufSurface structure.
 *
 * @return 0 for success, -1 for failure.
 */
int Raw2NvBufSurface (unsigned char *ptr, unsigned int index, unsigned int plane, unsigned int in_width, unsigned int in_height, NvBufSurface *Surf);

/**
 * Syncs the HW memory cache for the CPU.
 *
 * Valid only for memory types \ref NVBUF_MEM_SURFACE_ARRAY and
 * \ref NVBUF_MEM_HANDLE.
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     Index of the buffer in the batch. -1 refers to
 *                      all buffers in the batch.
 * @param[in] plane     Index of a plane in the buffer. -1 refers to all planes
 *                      in the buffer.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceSyncForCpu (NvBufSurface *surf, int index, int plane);

/**
 * \brief  Syncs the hardware memory cache for the device.
 *
 * Valid only for memory types \ref NVBUF_MEM_SURFACE_ARRAY and
 * \ref NVBUF_MEM_HANDLE.
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     Index of a buffer in the batch. -1 refers to all buffers
 *                      in the batch.
 * @param[in] plane     Index of a plane in the buffer. -1 refers to all planes
 *                      in the buffer.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceSyncForDevice (NvBufSurface *surf, int index, int plane);

/**
 * \brief  Gets the \ref NvBufSurface from the DMABUF FD.
 *
 * @param[in]  dmabuf_fd    DMABUF FD of the buffer.
 * @param[out] buffer       A pointer to the NvBufSurface.
 *
 * @return 0 for success, or -1 otherwise.
 */
int NvBufSurfaceFromFd (int dmabuf_fd, void **buffer);

/**
 * \brief  Fills each byte of the buffer(s) in an \ref NvBufSurface with a
 * provided value.
 *
 * You can also use this function to reset the buffer(s) in the batch.
 *
 * @param[in] surf  A pointer to the NvBufSurface structure.
 * @param[in] index Index of a buffer in the batch. -1 refers to all buffers
 *                  in the batch.
 * @param[in] plane Index of a plane in the buffer. -1 refers to all planes
 *                  in the buffer.
 * @param[in] value The value to be used as fill.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceMemSet (NvBufSurface *surf, int index, int plane, uint8_t value);

/**
 * \brief  Creates an EGLImage from the memory of one or more
 * \ref NvBufSurface buffers.
 *
 * Only memory type \ref NVBUF_MEM_SURFACE_ARRAY is supported.
 *
 * This function returns the created EGLImage by storing its address at
 * \a surf->surfaceList->mappedAddr->eglImage. (\a surf is a pointer to
 * an NvBufSurface. \a surfaceList is a pointer to an \ref NvBufSurfaceParams.
 * \a mappedAddr is a pointer to an \ref NvBufSurfaceMappedAddr.
 * \a eglImage is declared as a pointer to void, and holds an
 * EGLImageKHR.)
 *
 * You can use this function in scenarios where a CUDA operation on Jetson
 * hardware memory (identified by \ref NVBUF_MEM_SURFACE_ARRAY) is required.
 * The EGLImageKHR struct provided by this function can then be registered
 * with CUDA for further CUDA operations.
 *
 * @param[in,out] surf  A pointer to an NvBufSurface structure. The function
 *                      stores a pointer to the created EGLImage in
 *                      a descendant of this structure; see the notes above.
 * @param[in]     index Index of a buffer in the batch. -1 specifies all buffers
 *                      in the batch.
 *
 * @return 0 for success, or -1 otherwise.
 */
int NvBufSurfaceMapEglImage (NvBufSurface *surf, int index);

/**
 * \brief  Destroys the previously created EGLImage object(s).
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     The index of a buffer in the batch. -1 specifies all
 *                      buffers in the batch.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceUnMapEglImage (NvBufSurface *surf, int index);

/**
 * \brief Import parameters received from another process and create hardware buffer.
 *
 * Calling process must need to call NvBufferDestroy() to remove reference count for
 * hardware buffer handle of the imported DMA buffer.
 *
 * @param[out] out_nvbuf_surf  Pointer to hardware buffer.
 * @param[in]  in_params       Parameters to create hardware buffer.
 *
 * @return 0 for success, -1 for failure.
 */
int NvBufSurfaceImport (NvBufSurface **out_nvbuf_surf, const NvBufSurfaceMapParams *in_params);

/**
 * \brief Get buffer information to map the buffer in another process.
 *
 * @param[in]  surf     Pointer to NvBufSurface structure.
 * @param[in]  index    Index of a buffer in the batch.
 * @param[out] params   Pointer to NvBufSurfaceMapParams information of the buffer.
 *
 * @return 0 for success, -1 for failure.
 */
int NvBufSurfaceGetMapParams (const NvBufSurface *surf, int index, NvBufSurfaceMapParams *params);

/**
 * \brief  Creates an CUDA buffer from the memory of one or more
 * \ref NvBufSurface buffers.
 *
 * Only memory type \ref NVBUF_MEM_SURFACE_ARRAY is supported.
 *
 * This function returns the created CUDA buffer by storing its address at
 * \a surf->surfaceList->mappedAddr->cudaPtr. (\a surf is a pointer to
 * an NvBufSurface. \a surfaceList is a pointer to an \ref NvBufSurfaceParams.
 * \a mappedAddr is a pointer to an \ref NvBufSurfaceMappedAddr.
 * \a cudaPtr is a pointer to an \ref NvBufSurfaceCudaBuffer.
 *
 * You can use this function in scenarios where a CUDA operation on Jetson
 * hardware memory (identified by \ref NVBUF_MEM_SURFACE_ARRAY) is required.
 * The NvBufSurfaceCudaBuffer struct provided by this function can be used
 * to get dataPtr of CUDA memory.
 *
 * @param[in,out] surf  A pointer to an NvBufSurface structure. The function
 *                      stores a pointer to the created CUDA buffer in
 *                      a descendant of this structure; see the notes above.
 * @param[in]     index Index of a buffer in the batch. -1 specifies all buffers
 *                      in the batch.
 *
 * @return 0 for success, or -1 otherwise.
 */
int NvBufSurfaceMapCudaBuffer (NvBufSurface *surf, int index);

/**
 * \brief  Destroys the previously created CUDA buffer.
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     The index of a buffer in the batch. -1 specifies all
 *                      buffers in the batch.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceUnMapCudaBuffer (NvBufSurface *surf, int index);

/**
 * \brief  Creates an NVMM buffer from the memory of one or more
 * \ref NvBufSurface buffers.
 *
 * Only memory type \ref NVBUF_MEM_CUDA_DEVICE and \ref NVBUF_MEM_CUDA_PINNED
 * and \ref NVBUF_MEM_CUDA_ARRAY are supported.
 *
 * This function returns the created NVMM buffer by storing its address at
 * \a surf->surfaceList->mappedAddr->nvmmPtr. (\a surf is a pointer to
 * an NvBufSurface. \a surfaceList is a pointer to an \ref NvBufSurfaceParams.
 * \a mappedAddr is a pointer to an \ref NvBufSurfaceMappedAddr.
 * \a nvmmPtr is a pointer to NVMM buffer of memory type \ref NVBUF_MEM_SURFACE_ARRAY.
 *
 * You can use this function in scenarios where a NVBUF_MEM_SURFACE_ARRAY operation
 * on Jetson hardware memory identified by \ref NVBUF_MEM_CUDA_DEVICE and
 * \ref NVBUF_MEM_CUDA_PINNED and \ref NVBUF_MEM_CUDA_ARRAY are required.
 *
 * @param[in,out] surf  A pointer to an NvBufSurface structure. The function
 *                      stores a pointer to the created NVMM buffer in
 *                      a descendant of this structure; see the notes above.
 * @param[in]     index Index of a buffer in the batch. -1 specifies all buffers
 *                      in the batch.
 *
 * @return 0 for success, or -1 otherwise.
 */
int NvBufSurfaceMapNvmmBuffer (NvBufSurface *surf, int index);

/**
 * \brief  Destroys the previously created NVMM buffer.
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     The index of a buffer in the batch. -1 specifies all
 *                      buffers in the batch.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceUnMapNvmmBuffer (NvBufSurface *surf, int index);

/**
 * \brief  Retrieves information about the underlying GPU device driver.
 *
 * @param[out] info      Pointer to NvBufSurfaceDeviceInfo structure.
 *
 * @return 0 if successful, or -1 otherwise.
 *
 * This function attempts to determine the below system information:
 * 1. if the system is using 'nvgpu' or an OpenRM-based driver by checking loaded kernel modules.
 * 2. if VIC is present on the platform,
 * 3. if GPU is an integrated GPU,
 * 4. The type of Jetson platform.
 */
int NvBufSurfaceGetDeviceInfo (NvBufSurfaceDeviceInfo *info);

/**
 * \brief  Syncs the GPU L2 cache memory for the device.
 *
 * Valid only for memory types \ref NVBUF_MEM_CUDA_DEVICE,
 * \ref NVBUF_MEM_CUDA_ARRAY and \ref NVBUF_MEM_SURFACE_ARRAY.
 *
 * @param[in] surf      A pointer to an \ref NvBufSurface structure.
 * @param[in] index     Index of a buffer in the batch. -1 refers to all buffers
 *                      in the batch.
 * @param[in] params    Parameters for the GPU L2 cache sync operation.
 *
 * @return 0 if successful, or -1 otherwise.
 */
int NvBufSurfaceSyncGpuL2CacheForDevice (NvBufSurface *surf, int index, NvBufSurfaceGPUL2SyncParams *params);

#ifdef __cplusplus
}
#endif
#endif /* NVBUFSURFACE_H_ */

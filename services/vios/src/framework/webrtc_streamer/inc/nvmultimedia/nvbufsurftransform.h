/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
 * @file nvbufsurftransform.h
 * <b>NvBufSurfTransform Interface </b>
 *
 * This file specifies the NvBufSurfTransform image transformation APIs.
 *
 * The NvBufSurfTransform API provides methods to set and get session parameters
 * and to transform and composite APIs.
 */
#ifndef NVBUFSURFTRANSFORM_H_
#define NVBUFSURFTRANSFORM_H_
#include "nvbufsurface.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Defines the maximum number of sync objects.
 */
#define NVBUFSURF_MAX_SYNCOBJ 5

typedef struct CUstream_st* cudaStream_t; //!< Forward declaration of cudaStream_t.

/** @defgroup ds_bbb NvBufSurfTransform Types and Functions
 * Defines types and functions of the \ref NvBufSurfTransform
 * application programming interface.
 * @ingroup ds_nvbuf_api
 * @{ */

/**
 * Specifies compute devices used by \ref NvBufSurfTransform.
 */
typedef enum
{
  /** Specifies VIC as a compute device for Jetson or dGPU for an x86_64
   system. */
  NvBufSurfTransformCompute_Default,
  /** Specifies that the GPU is the compute device. */
  NvBufSurfTransformCompute_GPU,
  /** Specifies that the VIC as a compute device. Supported only for Jetson. */
  NvBufSurfTransformCompute_VIC
} NvBufSurfTransform_Compute;


/**
 * Specifies video flip methods.
 */
typedef enum
{
  /** Specifies no video flip. */
  NvBufSurfTransform_None,
  /** Specifies rotating 90 degrees clockwise. */
  NvBufSurfTransform_Rotate90,
  /** Specifies rotating 180 degree clockwise. */
  NvBufSurfTransform_Rotate180,
  /** Specifies rotating 270 degree clockwise. */
  NvBufSurfTransform_Rotate270,
  /** Specifies video flip with respect to the X-axis. */
  NvBufSurfTransform_FlipX,
  /** Specifies video flip with respect to the Y-axis. */
  NvBufSurfTransform_FlipY,
  /** Specifies video flip transpose. */
  NvBufSurfTransform_Transpose,
  /** Specifies video flip inverse transpose. */
  NvBufSurfTransform_InvTranspose,
} NvBufSurfTransform_Flip;


/**
 * Specifies video interpolation methods.
 */
typedef enum
{
  /** Specifies Nearest Interpolation Method interpolation. */
  NvBufSurfTransformInter_Nearest = 0,
  /** Specifies Bilinear Interpolation Method interpolation. */
  NvBufSurfTransformInter_Bilinear,
  /** Specifies GPU-Cubic, VIC-5 Tap interpolation. */
  NvBufSurfTransformInter_Algo1,
  /** Specifies GPU-Super, VIC-10 Tap interpolation. */
  NvBufSurfTransformInter_Algo2,
  /** Specifies GPU-Lanzos, VIC-Smart interpolation. */
  NvBufSurfTransformInter_Algo3,
  /** Specifies GPU-Ignored, VIC-Nicest interpolation. */
  NvBufSurfTransformInter_Algo4,
  /** Specifies GPU-Nearest, VIC-Nearest interpolation. */
  NvBufSurfTransformInter_Default
} NvBufSurfTransform_Inter;

/**
 * Specifies error codes returned by \ref NvBufSurfTransform functions.
 */
typedef enum
{
  /** Specifies an error in source or destination ROI. */
  NvBufSurfTransformError_ROI_Error = -4,
  /** Specifies invalid input parameters. */
  NvBufSurfTransformError_Invalid_Params = -3,
  /** Specifies a runtime execution error. */
  NvBufSurfTransformError_Execution_Error = -2,
  /** Specifies an unsupported feature or format. */
  NvBufSurfTransformError_Unsupported = -1,
  /** Specifies a successful operation. */
  NvBufSurfTransformError_Success = 0
} NvBufSurfTransform_Error;

/**
 * Specifies transform types.
 */
typedef enum {
  /** Specifies a transform to crop the source rectangle. */
  NVBUFSURF_TRANSFORM_CROP_SRC   = 1,
  /** Specifies a transform to crop the destination rectangle. */
  NVBUFSURF_TRANSFORM_CROP_DST   = 1 << 1,
  /** Specifies a transform to set the filter type. */
  NVBUFSURF_TRANSFORM_FILTER     = 1 << 2,
  /** Specifies a transform to set the flip method. */
  NVBUFSURF_TRANSFORM_FLIP       = 1 << 3,
  /** Specifies a transform to normalize output. */
  NVBUFSURF_TRANSFORM_NORMALIZE  = 1 << 4,
  /** Specifies a transform to allow odd crop. */
  NVBUFSURF_TRANSFORM_ALLOW_ODD_CROP  = 1 << 5

} NvBufSurfTransform_Transform_Flag;

/**
 * Specifies types of composition operations.
 */
typedef enum {
  /** Specifies a flag to describe the requested compositing operation. */
  NVBUFSURF_TRANSFORM_COMPOSITE  = 1,
  /** Specifies a flag to describe the requested blending operation.
   * This flag is applicable for NvBufSurfTransformMultiInputBufCompositeBlend
   * and NvBufSurfTransformMultiInputBufCompositeBlendAsync API to support
   * blending operation in upcoming releases.
   */
  NVBUFSURF_TRANSFORM_BLEND  = 1 << 1,
  /** Specifies a composite to set the filter type. */
  NVBUFSURF_TRANSFORM_COMPOSITE_FILTER     = 1 << 2,
} NvBufSurfTransform_Composite_Flag;

/**
 * Holds the coordinates of a rectangle.
 */
typedef struct
{
  /** Holds the rectangle top. */
  uint32_t top;
  /** Holds the rectangle left side. */
  uint32_t left;
  /** Holds the rectangle width. */
  uint32_t width;
  /** Holds the rectangle height. */
  uint32_t height;
  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];
}NvBufSurfTransformRect;

/**
 * Holds configuration parameters for a transform/composite session.
 */
typedef struct _NvBufSurfTransformConfigParams
{
  /** Holds the mode of operation: VIC (Jetson) or GPU (iGPU + dGPU)
   If VIC is configured, \a gpu_id is ignored. */
  NvBufSurfTransform_Compute compute_mode;

  /** Holds the GPU ID to be used for processing. */
  int32_t gpu_id;

  /** User configure stream to be used. If NULL, the default stream is used.
   Ignored if VIC is used. */
  cudaStream_t cuda_stream;

  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];

} NvBufSurfTransformConfigParams;

/**
 * Holds transform parameters for a transform call.
 */
typedef struct _NvBufSurfaceTransformParams
{
  /** Holds a flag that indicates which transform parameters are valid. */
  uint32_t transform_flag;
  /** Holds the flip method. */
  NvBufSurfTransform_Flip transform_flip;
  /** Holds a transform filter. */
  NvBufSurfTransform_Inter transform_filter;
  /** Holds a pointer to a list of source rectangle coordinates for
   a crop operation. */
  NvBufSurfTransformRect *src_rect;
  /** Holds a pointer to list of destination rectangle coordinates for
   a crop operation. */
  NvBufSurfTransformRect *dst_rect;
  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];
}NvBufSurfTransformParams;

/**
 * Holds composite parameters for a composite call.
 */
typedef struct _NvBufSurfTransformCompositeParams
{
  /** Holds a flag that indicates which composition parameters are valid. */
  uint32_t composite_flag;
  /** Holds the number of input buffers to be composited. */
  uint32_t input_buf_count;
 /** Holds source rectangle coordinates of input buffers for compositing. */
  NvBufSurfTransformRect *src_comp_rect;
  /** Holds destination rectangle coordinates of input buffers for
   compositing. */
  NvBufSurfTransformRect *dst_comp_rect;
  /** Holds a composite filter. */
  NvBufSurfTransform_Inter composite_filter;
  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];
}NvBufSurfTransformCompositeParams;

typedef struct _NvBufSurfTransform_ColorParams {
  double red;     /**< Holds the red component of color.
                   Value must be in the range 0.0-1.0. */

  double green;   /**< Holds the green component of color.
                   Value must be in the range 0.0-1.0.*/

  double blue;    /**< Holds the blue component of color.
                   Value must be in the range 0.0-1.0.*/

  double alpha;   /**< Holds the alpha component of color.
                   Value must be in the range 0.0-1.0.*/
  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];
} NvBufSurfTransform_ColorParams;

/**
 * Holds composite blend parameters for a composite blender call.
 */
typedef struct _NvBufSurfTransformCompositeBlendParams
{
  /** Holds a flag that indicates which composition parameters are valid. */
  uint32_t composite_blend_flag;
  /** Holds the number of input buffers to be composited. */
  uint32_t input_buf_count;
  /** Holds a blend/composite filter applicable only  */
  NvBufSurfTransform_Inter composite_blend_filter;
  /** Holds background color list for blending if background buffer is absent, if NULL
   * it wont be used, background buffer is expected to be NULL, if blending with
   * static color is required
   */
  NvBufSurfTransform_ColorParams *color_bg;
  /** Holds a boolean flag list indicating whether blending to be done for particular buffer,
   * if NULL, blending will be on all buffers, if valid value API expects at least numFilled
   * size list and each element can take value 0 or 1
   */
  uint32_t *perform_blending;
  /** Reserved fields for future expansion. */
  void *reserved[STRUCTURE_PADDING];

}NvBufSurfTransformCompositeBlendParams;

/**
 * Holds extended composite blend parameters for NvBufSurfTransformMultiInputBufCompositeBlend
 * and NvBufSurfTransformMultiInputBufCompositeBlendAsync API
 */
typedef struct _NvBufSurfTransformCompositeBlendParamsEx
{
  /** Holds legacy composite blend parameters */
  NvBufSurfTransformCompositeBlendParams params;
  /** Holds source rectangle coordinates of input buffers for compositing. */
  NvBufSurfTransformRect *src_comp_rect;
  /** Holds destination rectangle coordinates of input buffers for compositing. */
  NvBufSurfTransformRect *dst_comp_rect;
  /** Holds composite filters to use for composition/blending. */
  NvBufSurfTransform_Inter *composite_blend_filter;
  /** Holds alpha values of input buffers for the blending. */
  float *alpha;
  /** reserved fields. */
  void *reserved[STRUCTURE_PADDING];
}NvBufSurfTransformCompositeBlendParamsEx;

/**
 ** Holds the information about synchronization objects for asynchronous
 * transform/composite APIs
 *
 */
typedef  struct NvBufSurfTransformSyncObj* NvBufSurfTransformSyncObj_t;

/**
 * \brief  Sets user-defined session parameters.
 *
 * If user-defined session parameters are set, they override the
 * NvBufSurfTransform() function's default session.
 *
 * @param[in] config_params     A pointer to a structure that is populated
 *                              with the session parameters to be used.
 *
 * @return  An \ref NvBufSurfTransform_Error value indicating
 *  success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformSetSessionParams
(NvBufSurfTransformConfigParams *config_params);

/**
 * \brief Gets the session parameters used by NvBufSurfTransform().
 *
 * @param[out] config_params    A pointer to a caller-allocated structure to be
 *                              populated with the session parameters used.
 *
 * @return  An \ref NvBufSurfTransform_Error value indicating
 *  success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformGetSessionParams
(NvBufSurfTransformConfigParams *config_params);

/**
 * \brief Performs a transformation on batched input images.
 *
 * If user-defined session parameters are to be used, call
 * NvBufSurfTransformSetSessionParams() before calling this function.
 *
 * @param[in]  src  A pointer to input batched buffers to be transformed.
 * @param[out] dst  A pointer to a caller-allocated location where
 *                  transformed output is to be stored.
 *                  @par When destination cropping is performed, memory outside
 *                  the crop location is not touched, and may contain stale
 *                  information. The caller must perform a memset before
 *                  calling this function if stale information must be
 *                  eliminated.
 * @param[in]  transform_params
 *                  A pointer to an \ref NvBufSurfTransformParams structure
 *                  which specifies the type of transform to be performed. They
 *                  may include any combination of scaling, format conversion,
 *                  and cropping for both source and destination.
 *                  Flipping and rotation are supported on VIC.
 * @return  An \ref NvBufSurfTransform_Error value indicating
 *  success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransform (NvBufSurface *src, NvBufSurface *dst,
    NvBufSurfTransformParams *transform_params);

/**
 * \brief  Composites batched input images.
 *
 * The compositer scales and stitches
 * batched buffers indicated by \a src into a single destination buffer, \a dst.
 *
 * If user-defined session parameters are to be used, call
 * NvBufSurfTransformSetSessionParams() before calling this function.
 *
 * @param[in]  src  A pointer to input batched buffers to be transformed.
 * @param[out] dst  A pointer a caller-allocated location (a single buffer)
 *                  where composited output is to be stored.
 * @param[in]  composite_params
 *                  A pointer to an \ref NvBufSurfTransformCompositeParams
 *                  structure which specifies the compositing operation to be
 *                  performed, e.g., the source and destination rectangles
 *                  in \a src and \a dst.
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformComposite (NvBufSurface *src,
    NvBufSurface *dst, NvBufSurfTransformCompositeParams *composite_params);

/**
 * \brief An asynchronous (non-blocking) transformation on batched input images.
 *
 * If user-defined session parameters are to be used, call
 * NvBufSurfTransformSetSessionParams() before calling this function.
 *
 * @param[in]  src  A pointer to input batched buffers to be transformed.
 * @param[out] dst  A pointer to a caller-allocated location where
 *                  transformed output is to be stored.
 *                  @par When destination cropping is performed, memory outside
 *                  the crop location is not touched, and may contain stale
 *                  information. The caller must perform a memset before
 *                  calling this function if stale information must be
 *                  eliminated.
 * @param[in]  transform_params
 *                  A pointer to an \ref NvBufSurfTransformParams structure
 *                  which specifies the type of transform to be performed. They
 *                  may include any combination of scaling, format conversion,
 *                  and cropping for both source and destination.
 *                  Flipping and rotation are supported on VIC/GPU.
 * @param[out] sync_obj
 *                  A pointer to an \ref NvBufSurfTransformSyncObj structure
 *                  which holds synchronization information of the current
 *                  transform call. \ref NvBufSurfTransfromSyncObjWait() API to be
 *                  called on this object to wait for transformation to complete.
 *                  \ref NvBufSurfTransformSyncObjDestroy API should be called after
 *                  \ref NvBufSurfTransformSyncObjWait API to release the objects
 *                  If the parameter is NULL, the call would return only after
 *                  the transform is complete.
 * @return  An \ref NvBufSurfTransform_Error value indicating
 *  success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformAsync (NvBufSurface *src,
    NvBufSurface *dst, NvBufSurfTransformParams *transform_params,
    NvBufSurfTransformSyncObj_t *sync_obj);

/**
 * \brief  Composites batched input images Asynchronously (non-blocking).
 *
 * The compositer scales and stitches
 * batched buffers indicated by \a src into a single destination buffer, \a dst.
 *
 * If user-defined session parameters are to be used, call
 * NvBufSurfTransformSetSessionParams() before calling this function.
 *
 * @param[in]  src  A pointer to input batched buffers to be composited.
 * @param[out] dst  A pointer a caller-allocated location (a single buffer)
 *                  where composited output is to be stored.
 * @param[in]  composite_params
 *                  A pointer to an \ref NvBufSurfTransformCompositeParams
 *                  structure which specifies the compositing operation to be
 *                  performed, e.g., the source and destination rectangles
 *                  in \a src and \a dst.
 * @param[out] sync_obj
 *                  A pointer to an \ref NvBufSurfTransformSyncObj structure
 *                  which holds synchronization information of the current
 *                  composite call. ref\ NvBufSurfTransfromSyncObjWait() API to be
 *                  called on this object to wait for composition to complete.
 *                  \ref NvBufSurfTransformSyncObjDestroy API should be called after
 *                  \ref NvBufSurfTransformSyncObjWait API to release the objects
 *                  If the parameter is NULL, the call would return only after
 *                  the composite is complete.
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformCompositeAsync (NvBufSurface *src,
    NvBufSurface *dst, NvBufSurfTransformCompositeParams *composite_params,
    NvBufSurfTransformSyncObj_t *sync_obj);

/**
 * \brief  Composites/Blends batched input images
 *
 * The compositer scales and blends
 * batched buffers indicated by \a src0 with \a src1 into a batched
 * destination buffer \a dst  using \a alpha as the blending weights.
 * A Linear interpolation operation is performed to get the final pix value
 * each of \a src0, \a src1, \a alpha and \a dst have one to one mapping
 * For each pixel the following linear interpolation is performed.
 * \a dst = ( \a src0* \a alpha + \a src1* (255.0 - \a alpha))/255.0
 * If user-defined session parameters are to be used, call
 * NvBufSurfTransformSetSessionParams() before calling this function.
 *
 * @param[in]  src0  A pointer to input batched buffers to be blend.
 * @param[in]  src1  A pointer to input batched buffers to be blend with.
 * @param[in]  alpha  A pointer to input batched buffers which has blending weights.
 * @param[out] dst  A pointer to output batched buffers where blended composite
 *                  output is stored
 * @param[in]  blend_params
 *                  A pointer to an \ref NvBufSurfTransformCompositeBlendParams
 *                  structure which specifies the compositing operation to be
 *                  performed
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformCompositeBlend(NvBufSurface *src0,
    NvBufSurface *src1, NvBufSurface *alpha, NvBufSurface *dst,
    NvBufSurfTransformCompositeBlendParams *blend_params);

/**
 * Performs Composition and Blending on multiple input images(batch size=1) and provide
 * single output image(batch size=1)
 *
 * Composites and blends batched(batch size=1) input buffers pointed by src pointer.
 * Compositer scales, stitches and blends batched buffers pointed by src into single
 * dst buffer (batch size=1), the parameters for composition and blending is provided
 * by composite_blend_params.
 * Use NvBufSurfTransformSetSessionParams before each call, if user defined
 * session parameters are to be used.
 * It is different than the NvBufSurfTransformCompositeBlend API and It is currently
 * supported on Jetson only.
 *
 * @param[in] src pointer (multiple buffer) to input batched(batch size=1) buffers to be transformed.
 * @param[out] dst pointer (single buffer) where composited output would be stored.
 * @param[in] composite_blend_params pointer to NvBufSurfTransformCompositeBlendParamsEx structure.
 *
 * @return NvBufSurfTransform_Error indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformMultiInputBufCompositeBlend (NvBufSurface **src,
    NvBufSurface *dst, NvBufSurfTransformCompositeBlendParamsEx *composite_blend_params);

/**
 * Performs Composition and Blending on multiple input images(batch size=1) and provide
 * single output image(batch size=1) Asynchronously (non-blocking).
 *
 * Composites and blends batched(batch size=1) input buffers pointed by src pointer.
 * Compositer scales, stitches and blends batched buffers pointed by src into single
 * dst buffer (batch size=1), the parameters for composition and blending is provided
 * by composite_blend_params.
 * Use NvBufSurfTransformSetSessionParams before each call, if user defined
 * session parameters are to be used.
 * It is different than the NvBufSurfTransformCompositeBlend API and It is currently
 * supported on Jetson only.
 *
 * @param[in] src pointer (multiple buffer) to input batched(batch size=1) buffers to be transformed.
 * @param[out] dst pointer (single buffer) where composited output would be stored.
 * @param[in] composite_blend_params pointer to NvBufSurfTransformCompositeParams structure.
 * @param[out] sync_obj
 *                  A pointer to an \ref NvBufSurfTransformSyncObj structure
 *                  which holds synchronization information of the current
 *                  composite call. ref\ NvBufSurfTransfromSyncObjWait() API to be
 *                  called on this object to wait for composition to complete.
 *                  \ref NvBufSurfTransformSyncObjDestroy API should be called after
 *                  \ref NvBufSurfTransformSyncObjWait API to release the objects
 *                  If the parameter is NULL, the call would return only after
 *                  the composite is complete.

 *
 * @return NvBufSurfTransform_Error indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformMultiInputBufCompositeBlendAsync (NvBufSurface **src,
    NvBufSurface *dst, NvBufSurfTransformCompositeBlendParamsEx *composite_blend_params,
    NvBufSurfTransformSyncObj_t *sync_obj);


/**
 * \brief  Wait on the synchronization object.
 *
 * The API waits on the synchronization object to finish the corresponding
 * processing of transform/composite calls or returns on time_out
 *
 *
 * @param[in]  sync_obj  A pointer to sync object on which the API should wait
 * @param[in]  time_out  Maximum time in ms API should wait before returning, only
 *                       Only applicable for VIC as of now.
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformSyncObjWait(
NvBufSurfTransformSyncObj_t sync_obj, uint32_t time_out);


/**
 * \brief  Destroy the synchronization object.
 *
 * The API deletes the sync_obj which was used for previous transform/composite
 * Asynchronous calls
 *
 * @param[in]  sync_obj  A pointer sync_obj, which the API will delete
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 *
 */
NvBufSurfTransform_Error NvBufSurfTransformSyncObjDestroy(
    NvBufSurfTransformSyncObj_t* sync_obj);

/**
 * \brief  Get the synchronization object from sync point FD.
 *
 * The API gets the corresponding synchronization object from sync point FD.
 * It currently assumes one synchronization object for one file descriptor as
 * all the transform APIs supports single synchronization object only.
 *
 * @param[in]   file              sync point file descriptor.
 * @param[out]  sync_obj          A pointer to an \ref NvBufSurfTransformSyncObj_t which is a pointer
 *                                to an array of \ref NvBufSurfTransformSyncObj allocated in the function
 *                                having size (num_sync_objs * sizeof(NvBufSurfTransformSyncObj))
 *                                which holds synchronization information corresponding to FD.
 * @param[out]  num_sync_objs     number of sync objects.
 * @return An \ref NvBufSurfTransform_Error value indicating success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformSyncObjFromFile(
    int file, NvBufSurfTransformSyncObj_t* sync_obj, unsigned int *num_sync_objs);

/**
 * \brief Sets the default transform session as the current session for all upcoming transforms.
 *
 * @return  An \ref NvBufSurfTransform_Error value indicating
 *  success or failure.
 */
NvBufSurfTransform_Error NvBufSurfTransformSetDefaultSession(void);

/** @} */
#ifdef __cplusplus
}
#endif
#endif

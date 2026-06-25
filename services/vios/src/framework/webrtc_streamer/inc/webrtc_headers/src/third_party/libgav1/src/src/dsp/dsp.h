/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_DSP_DSP_H_
#define LIBGAV1_SRC_DSP_DSP_H_

#include <cstddef>
#include <cstdint>
#include <cstdlib>

#include "src/dsp/common.h"
#include "src/dsp/constants.h"
#include "src/dsp/film_grain_common.h"
#include "src/utils/cpu.h"
#include "src/utils/reference_info.h"
#include "src/utils/types.h"

namespace libgav1 {
namespace dsp {

#if !defined(LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS)
#define LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS 0
#endif

enum IntraPredictor : uint8_t {
  kIntraPredictorDcFill,
  kIntraPredictorDcTop,
  kIntraPredictorDcLeft,
  kIntraPredictorDc,
  kIntraPredictorVertical,
  kIntraPredictorHorizontal,
  kIntraPredictorPaeth,
  kIntraPredictorSmooth,
  kIntraPredictorSmoothVertical,
  kIntraPredictorSmoothHorizontal,
  kNumIntraPredictors
};

// List of valid 1D transforms.
enum Transform1d : uint8_t {
  kTransform1dDct,   // Discrete Cosine Transform.
  kTransform1dAdst,  // Asymmetric Discrete Sine Transform.
  kTransform1dIdentity,
  kTransform1dWht,  // Walsh Hadamard Transform.
  kNumTransform1ds
};

// List of valid 1D transform sizes. Not all transforms may be available for all
// the sizes.
enum Transform1dSize : uint8_t {
  kTransform1dSize4,
  kTransform1dSize8,
  kTransform1dSize16,
  kTransform1dSize32,
  kTransform1dSize64,
  kNumTransform1dSizes
};

// The maximum width of the loop filter, fewer pixels may be filtered depending
// on strength thresholds.
enum LoopFilterSize : uint8_t {
  kLoopFilterSize4,
  kLoopFilterSize6,
  kLoopFilterSize8,
  kLoopFilterSize14,
  kNumLoopFilterSizes
};

enum : uint8_t {
  kRow = 0,
  kColumn = 1,
};

//------------------------------------------------------------------------------
// ToString()
//
// These functions are meant to be used only in debug logging and within tests.
// They are defined inline to avoid including the strings in the release
// library when logging is disabled; unreferenced functions will not be added to
// any object file in that case.

inline const char* ToString(const IntraPredictor predictor) {
  switch (predictor) {
    case kIntraPredictorDcFill:
      return "kIntraPredictorDcFill";
    case kIntraPredictorDcTop:
      return "kIntraPredictorDcTop";
    case kIntraPredictorDcLeft:
      return "kIntraPredictorDcLeft";
    case kIntraPredictorDc:
      return "kIntraPredictorDc";
    case kIntraPredictorVertical:
      return "kIntraPredictorVertical";
    case kIntraPredictorHorizontal:
      return "kIntraPredictorHorizontal";
    case kIntraPredictorPaeth:
      return "kIntraPredictorPaeth";
    case kIntraPredictorSmooth:
      return "kIntraPredictorSmooth";
    case kIntraPredictorSmoothVertical:
      return "kIntraPredictorSmoothVertical";
    case kIntraPredictorSmoothHorizontal:
      return "kIntraPredictorSmoothHorizontal";
    case kNumIntraPredictors:
      return "kNumIntraPredictors";
  }
  abort();
}

inline const char* ToString(const Transform1d transform) {
  switch (transform) {
    case kTransform1dDct:
      return "kTransform1dDct";
    case kTransform1dAdst:
      return "kTransform1dAdst";
    case kTransform1dIdentity:
      return "kTransform1dIdentity";
    case kTransform1dWht:
      return "kTransform1dWht";
    case kNumTransform1ds:
      return "kNumTransform1ds";
  }
  abort();
}

inline const char* ToString(const Transform1dSize transform_size) {
  switch (transform_size) {
    case kTransform1dSize4:
      return "kTransform1dSize4";
    case kTransform1dSize8:
      return "kTransform1dSize8";
    case kTransform1dSize16:
      return "kTransform1dSize16";
    case kTransform1dSize32:
      return "kTransform1dSize32";
    case kTransform1dSize64:
      return "kTransform1dSize64";
    case kNumTransform1dSizes:
      return "kNumTransform1dSizes";
  }
  abort();
}

inline const char* ToString(const LoopFilterSize filter_size) {
  switch (filter_size) {
    case kLoopFilterSize4:
      return "kLoopFilterSize4";
    case kLoopFilterSize6:
      return "kLoopFilterSize6";
    case kLoopFilterSize8:
      return "kLoopFilterSize8";
    case kLoopFilterSize14:
      return "kLoopFilterSize14";
    case kNumLoopFilterSizes:
      return "kNumLoopFilterSizes";
  }
  abort();
}

inline const char* ToString(const LoopFilterType filter_type) {
  switch (filter_type) {
    case kLoopFilterTypeVertical:
      return "kLoopFilterTypeVertical";
    case kLoopFilterTypeHorizontal:
      return "kLoopFilterTypeHorizontal";
    case kNumLoopFilterTypes:
      return "kNumLoopFilterTypes";
  }
  abort();
}

//------------------------------------------------------------------------------
// Intra predictors. Section 7.11.2.
// These require access to one or both of the top row and left column. Some may
// access the top-left (top[-1]), top-right (top[width+N]), bottom-left
// (left[height+N]) or upper-left (left[-1]).

// Intra predictor function signature. Sections 7.11.2.2, 7.11.2.4 (#10,#11),
// 7.11.2.5, 7.11.2.6.
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes. |top| is an unaligned pointer to
// the row above |dst|. |left| is an aligned vector of the column to the left
// of |dst|. top-left and bottom-left may be accessed.
// The pointer arguments do not alias one another.
using IntraPredictorFunc = void (*)(void* dst, ptrdiff_t stride,
                                    const void* top, const void* left);
using IntraPredictorFuncs =
    IntraPredictorFunc[kNumTransformSizes][kNumIntraPredictors];

// Directional intra predictor function signature, zone 1 (0 < angle < 90).
// Section 7.11.2.4 (#7).
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes. |top| is an unaligned pointer to
// the row above |dst|. |width| and |height| give the dimensions of the block.
// |xstep| is the scaled starting index to |top| from
// kDirectionalIntraPredictorDerivative. |upsampled_top| indicates whether
// |top| has been upsampled as described in '7.11.2.11. Intra edge upsample
// process'. This can occur in cases with |width| + |height| <= 16. top-right
// is accessed.
// The pointer arguments do not alias one another.
using DirectionalIntraPredictorZone1Func = void (*)(void* dst, ptrdiff_t stride,
                                                    const void* top, int width,
                                                    int height, int xstep,
                                                    bool upsampled_top);

// Directional intra predictor function signature, zone 2 (90 < angle < 180).
// Section 7.11.2.4 (#8).
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes. |top| is an unaligned pointer to
// the row above |dst|. |left| is an aligned vector of the column to the left of
// |dst|. |width| and |height| give the dimensions of the block. |xstep| and
// |ystep| are the scaled starting index to |top| and |left|, respectively,
// from kDirectionalIntraPredictorDerivative. |upsampled_top| and
// |upsampled_left| indicate whether |top| and |left| have been upsampled as
// described in '7.11.2.11. Intra edge upsample process'. This can occur in
// cases with |width| + |height| <= 16. top-left and upper-left are accessed,
// up to [-2] in each if |upsampled_top/left| are set.
// The pointer arguments do not alias one another.
using DirectionalIntraPredictorZone2Func = void (*)(
    void* dst, ptrdiff_t stride, const void* top, const void* left, int width,
    int height, int xstep, int ystep, bool upsampled_top, bool upsampled_left);

// Directional intra predictor function signature, zone 3 (180 < angle < 270).
// Section 7.11.2.4 (#9).
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes. |left| is an aligned vector of the
// column to the left of |dst|. |width| and |height| give the dimensions of the
// block. |ystep| is the scaled starting index to |left| from
// kDirectionalIntraPredictorDerivative. |upsampled_left| indicates whether
// |left| has been upsampled as described in '7.11.2.11. Intra edge upsample
// process'. This can occur in cases with |width| + |height| <= 16. bottom-left
// is accessed.
// The pointer arguments do not alias one another.
using DirectionalIntraPredictorZone3Func = void (*)(void* dst, ptrdiff_t stride,
                                                    const void* left, int width,
                                                    int height, int ystep,
                                                    bool upsampled_left);

// Filter intra predictor function signature. Section 7.11.2.3.
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes. |top| is an unaligned pointer to
// the row above |dst|. |left| is an aligned vector of the column to the left
// of |dst|. |width| and |height| are the size of the block in pixels.
// The pointer arguments do not alias one another.
using FilterIntraPredictorFunc = void (*)(void* dst, ptrdiff_t stride,
                                          const void* top, const void* left,
                                          FilterIntraPredictor pred, int width,
                                          int height);

//------------------------------------------------------------------------------
// Chroma from Luma (Cfl) prediction. Section 7.11.5.

// Chroma from Luma (Cfl) intra prediction function signature. |dst| is an
// unaligned pointer to the output block. Pixel size is determined by bitdepth
// with |stride| given in bytes. |luma| contains subsampled luma pixels with 3
// fractional bits of precision. |alpha| is the signed Cfl alpha value for the
// appropriate plane.
using CflIntraPredictorFunc = void (*)(
    void* dst, ptrdiff_t stride,
    const int16_t luma[kCflLumaBufferStride][kCflLumaBufferStride], int alpha);
using CflIntraPredictorFuncs = CflIntraPredictorFunc[kNumTransformSizes];

// Chroma from Luma (Cfl) subsampler function signature. |luma| is an unaligned
// pointer to the output block. |src| is an unaligned pointer to the input
// block. Pixel size is determined by bitdepth with |stride| given in bytes.
using CflSubsamplerFunc =
    void (*)(int16_t luma[kCflLumaBufferStride][kCflLumaBufferStride],
             int max_luma_width, int max_luma_height, const void* source,
             ptrdiff_t stride);
using CflSubsamplerFuncs =
    CflSubsamplerFunc[kNumTransformSizes][kNumSubsamplingTypes];

//------------------------------------------------------------------------------
// Intra Edge Filtering and Upsampling. Step 4 in section 7.11.2.4.

// Intra edge filter function signature. |buffer| is a pointer to the top_row or
// left_column that needs to be filtered. Typically the -1'th index of |top_row|
// and |left_column| need to be filtered as well, so the caller can merely pass
// the |buffer| as top_row[-1] or left_column[-1]. Pixel size is determined by
// bitdepth. |size| is the number of pixels to be filtered. |strength| is the
// filter strength. Section 7.11.2.12 in the spec.
using IntraEdgeFilterFunc = void (*)(void* buffer, int size, int strength);

// Intra edge upsampler function signature. |buffer| is a pointer to the top_row
// or left_column that needs to be upsampled. Pixel size is determined by
// bitdepth. |size| is the number of pixels to be upsampled; valid values are:
// 4, 8, 12, 16. This function needs access to negative indices -1 and -2 of
// the |buffer|. Section 7.11.2.11 in the spec.
using IntraEdgeUpsamplerFunc = void (*)(void* buffer, int size);

//------------------------------------------------------------------------------
// Inverse transform add function signature.
//
// Steps 2 and 3 of section 7.12.3 (contains the implementation of section
// 7.13.3).
// Apply the inverse transforms and add the residual to the destination frame
// for the transform type and block size |tx_size| starting at position
// |start_x| and |start_y|. |dst_frame| is a pointer to an Array2D of Pixel
// values. |adjusted_tx_height| is the number of rows to process based on the
// non-zero coefficient count in the block. It will be 1 (non-zero coefficient
// count == 1), 4 or a multiple of 8 up to 32 or the original transform height,
// whichever is less. |src_buffer| is a pointer to an Array2D of Residual
// values. On input |src_buffer| contains the dequantized values, on output it
// contains the residual.
// The pointer arguments do not alias one another.
using InverseTransformAddFunc = void (*)(TransformType tx_type,
                                         TransformSize tx_size,
                                         int adjusted_tx_height,
                                         void* src_buffer, int start_x,
                                         int start_y, void* dst_frame);
// The final dimension holds row and column transforms indexed with kRow and
// kColumn.
using InverseTransformAddFuncs =
    InverseTransformAddFunc[kNumTransform1ds][kNumTransform1dSizes][2];

//------------------------------------------------------------------------------
// Post processing.

// Loop filter function signature. Section 7.14.
// |dst| is an unaligned pointer to the output block. Pixel size is determined
// by bitdepth with |stride| given in bytes.
// <threshold param> <spec name> <range>
// |outer_thresh|    blimit      [7, 193]
// |inner_thresh|    limit       [1, 63]
// |hev_thresh|      thresh      [0, 63]
// These are scaled by the implementation by 'bitdepth - 8' to produce
// the spec variables blimitBd, limitBd and threshBd.
// Note these functions are not called when the loop filter level is 0.
using LoopFilterFunc = void (*)(void* dst, ptrdiff_t stride, int outer_thresh,
                                int inner_thresh, int hev_thresh);
using LoopFilterFuncs =
    LoopFilterFunc[kNumLoopFilterSizes][kNumLoopFilterTypes];

// Cdef direction function signature. Section 7.15.2.
// |src| is a pointer to the source block. Pixel size is determined by bitdepth
// with |stride| given in bytes. |direction| and |variance| are output
// parameters and must not be nullptr.
// The pointer arguments do not alias one another.
using CdefDirectionFunc = void (*)(const void* src, ptrdiff_t stride,
                                   uint8_t* direction, int* variance);

// Cdef filtering function signature. Section 7.15.3.
// |source| is a pointer to the input block padded with kCdefLargeValue if at a
// frame border. |source_stride| is given in units of uint16_t.
// |block_width|, |block_height| are the width/height of the input block.
// |primary_strength|, |secondary_strength|, and |damping| are Cdef filtering
// parameters.
// |direction| is the filtering direction.
// |dest| is the output buffer. |dest_stride| is given in bytes.
// The pointer arguments do not alias one another.
using CdefFilteringFunc = void (*)(const uint16_t* source,
                                   ptrdiff_t source_stride, int block_height,
                                   int primary_strength, int secondary_strength,
                                   int damping, int direction, void* dest,
                                   ptrdiff_t dest_stride);

// The first index is block width: [0]: 4, [1]: 8. The second is based on
// non-zero strengths: [0]: |primary_strength| and |secondary_strength|, [1]:
// |primary_strength| only, [2]: |secondary_strength| only.
using CdefFilteringFuncs = CdefFilteringFunc[2][3];

// Upscaling coefficients function signature. Section 7.16.
// This is an auxiliary function for SIMD optimizations and has no corresponding
// C function. Different SIMD versions may have different outputs. So it must
// pair with the corresponding version of SuperResFunc.
// |upscaled_width| is the width of the output frame.
// |step| is the number of subpixels to move the kernel for the next destination
// pixel.
// |initial_subpixel_x| is a base offset from which |step| increments.
// |coefficients| is the upscale filter used by each pixel in a row.
using SuperResCoefficientsFunc = void (*)(int upscaled_width,
                                          int initial_subpixel_x, int step,
                                          void* coefficients);

// Upscaling process function signature. Section 7.16.
// |coefficients| is the upscale filter used by each pixel in a row. It is not
// used by the C function.
// |source| is the input frame buffer. It will be line extended.
// |source_stride| is given in pixels.
// |dest| is the output buffer.
// |dest_stride| is given in pixels.
// |height| is the height of the block to be processed.
// |downscaled_width| is the width of the input frame.
// |upscaled_width| is the width of the output frame.
// |step| is the number of subpixels to move the kernel for the next destination
// pixel.
// |initial_subpixel_x| is a base offset from which |step| increments.
// The pointer arguments do not alias one another.
using SuperResFunc = void (*)(const void* coefficients, void* source,
                              ptrdiff_t source_stride, int height,
                              int downscaled_width, int upscaled_width,
                              int initial_subpixel_x, int step, void* dest,
                              ptrdiff_t dest_stride);

// Loop restoration function signature. Sections 7.16, 7.17.
// |restoration_info| contains loop restoration information, such as filter
// type, strength.
// |source| is the input frame buffer, which is deblocked and cdef filtered.
// |top_border| and |bottom_border| are the top and bottom borders.
// |dest| is the output.
// |stride| is given in pixels, and shared by |source| and |dest|.
// |top_border_stride| and |bottom_border_stride| are given in pixels.
// |restoration_buffer| contains buffers required for self guided filter and
// wiener filter. They must be initialized before calling.
// The pointer arguments do not alias one another.
using LoopRestorationFunc = void (*)(
    const RestorationUnitInfo& restoration_info, const void* source,
    ptrdiff_t stride, const void* top_border, ptrdiff_t top_border_stride,
    const void* bottom_border, ptrdiff_t bottom_border_stride, int width,
    int height, RestorationBuffer* restoration_buffer, void* dest);

// Index 0 is Wiener Filter.
// Index 1 is Self Guided Restoration Filter.
// This can be accessed as LoopRestorationType - 2.
using LoopRestorationFuncs = LoopRestorationFunc[2];

// Convolve function signature. Section 7.11.3.4.
// This function applies a horizontal filter followed by a vertical filter.
// |reference| is the input block (reference frame buffer). |reference_stride|
// is the corresponding frame stride.
// |vertical_filter_index|/|horizontal_filter_index| is the index to
// retrieve the type of filter to be applied for vertical/horizontal direction
// from the filter lookup table 'kSubPixelFilters'.
// |horizontal_filter_id| and |vertical_filter_id| are the filter ids.
// |width| and |height| are width and height of the block to be filtered.
// |ref_last_x| and |ref_last_y| are the last pixel of the reference frame in
// x/y direction.
// |prediction| is the output block (output frame buffer).
// Rounding precision is derived from the function being called. For horizontal
// filtering kInterRoundBitsHorizontal & kInterRoundBitsHorizontal12bpp will be
// used. For compound vertical filtering kInterRoundBitsCompoundVertical will be
// used. Otherwise kInterRoundBitsVertical & kInterRoundBitsVertical12bpp will
// be used.
// The pointer arguments do not alias one another.
using ConvolveFunc = void (*)(const void* reference, ptrdiff_t reference_stride,
                              int horizontal_filter_index,
                              int vertical_filter_index,
                              int horizontal_filter_id, int vertical_filter_id,
                              int width, int height, void* prediction,
                              ptrdiff_t pred_stride);

// Convolve functions signature. Each points to one convolve function with
// a specific setting:
// ConvolveFunc[is_intra_block_copy][is_compound][has_vertical_filter]
// [has_horizontal_filter].
// If is_compound is false, the prediction is clipped to Pixel.
// If is_compound is true, the range of prediction is:
//   8bpp:  [-5132,  9212] (int16_t)
//   10bpp: [ 3988, 61532] (uint16_t)
//   12bpp: [ 3974, 61559] (uint16_t)
// See src/dsp/convolve.cc
using ConvolveFuncs = ConvolveFunc[2][2][2][2];

// Convolve + scale function signature. Section 7.11.3.4.
// This function applies a horizontal filter followed by a vertical filter.
// |reference| is the input block (reference frame buffer). |reference_stride|
// is the corresponding frame stride.
// |vertical_filter_index|/|horizontal_filter_index| is the index to
// retrieve the type of filter to be applied for vertical/horizontal direction
// from the filter lookup table 'kSubPixelFilters'.
// |subpixel_x| and |subpixel_y| are starting positions in units of 1/1024.
// |step_x| and |step_y| are step sizes in units of 1/1024 of a pixel.
// |width| and |height| are width and height of the block to be filtered.
// |ref_last_x| and |ref_last_y| are the last pixel of the reference frame in
// x/y direction.
// |prediction| is the output block (output frame buffer).
// Rounding precision is derived from the function being called. For horizontal
// filtering kInterRoundBitsHorizontal & kInterRoundBitsHorizontal12bpp will be
// used. For compound vertical filtering kInterRoundBitsCompoundVertical will be
// used. Otherwise kInterRoundBitsVertical & kInterRoundBitsVertical12bpp will
// be used.
// The pointer arguments do not alias one another.
using ConvolveScaleFunc = void (*)(const void* reference,
                                   ptrdiff_t reference_stride,
                                   int horizontal_filter_index,
                                   int vertical_filter_index, int subpixel_x,
                                   int subpixel_y, int step_x, int step_y,
                                   int width, int height, void* prediction,
                                   ptrdiff_t pred_stride);

// Convolve functions signature for scaling version.
// 0: single predictor. 1: compound predictor.
using ConvolveScaleFuncs = ConvolveScaleFunc[2];

// Weight mask function signature. Section 7.11.3.12.
// |prediction_0| is the first input block.
// |prediction_1| is the second input block. Both blocks are int16_t* when
// bitdepth == 8 and uint16_t* otherwise.
// |width| and |height| are the prediction width and height.
// The stride for the input buffers is equal to |width|.
// The valid range of block size is [8x8, 128x128] for the luma plane.
// |mask| is the output buffer. |mask_stride| is the output buffer stride.
// The pointer arguments do not alias one another.
using WeightMaskFunc = void (*)(const void* prediction_0,
                                const void* prediction_1, uint8_t* mask,
                                ptrdiff_t mask_stride);

// Weight mask functions signature. The dimensions (in order) are:
//   * Width index (4 => 0, 8 => 1, 16 => 2 and so on).
//   * Height index (4 => 0, 8 => 1, 16 => 2 and so on).
//   * mask_is_inverse.
using WeightMaskFuncs = WeightMaskFunc[6][6][2];

// Average blending function signature.
// Two predictors are averaged to generate the output.
// Input predictor values are int16_t. Output type is uint8_t, with actual
// range of Pixel value.
// Average blending is in the bottom of Section 7.11.3.1 (COMPOUND_AVERAGE).
// |prediction_0| is the first input block.
// |prediction_1| is the second input block. Both blocks are int16_t* when
// bitdepth == 8 and uint16_t* otherwise.
// |width| and |height| are the same for the first and second input blocks.
// The stride for the input buffers is equal to |width|.
// The valid range of block size is [8x8, 128x128] for the luma plane.
// |dest| is the output buffer. |dest_stride| is the output buffer stride.
// The pointer arguments do not alias one another.
using AverageBlendFunc = void (*)(const void* prediction_0,
                                  const void* prediction_1, int width,
                                  int height, void* dest,
                                  ptrdiff_t dest_stride);

// Distance weighted blending function signature.
// Weights are generated in Section 7.11.3.15.
// Weighted blending is in the bottom of Section 7.11.3.1 (COMPOUND_DISTANCE).
// This function takes two blocks (inter frame prediction) and produces a
// weighted output.
// |prediction_0| is the first input block.
// |prediction_1| is the second input block. Both blocks are int16_t* when
// bitdepth == 8 and uint16_t* otherwise.
// |weight_0| is the weight for the first block. It is derived from the relative
// distance of the first reference frame and the current frame.
// |weight_1| is the weight for the second block. It is derived from the
// relative distance of the second reference frame and the current frame.
// |width| and |height| are the same for the first and second input blocks.
// The stride for the input buffers is equal to |width|.
// The valid range of block size is [8x8, 128x128] for the luma plane.
// |dest| is the output buffer. |dest_stride| is the output buffer stride.
// The pointer arguments do not alias one another.
using DistanceWeightedBlendFunc = void (*)(const void* prediction_0,
                                           const void* prediction_1,
                                           uint8_t weight_0, uint8_t weight_1,
                                           int width, int height, void* dest,
                                           ptrdiff_t dest_stride);

// Mask blending function signature. Section 7.11.3.14.
// This function takes two blocks and produces a blended output stored into the
// output block |dest|. The blending is a weighted average process, controlled
// by values of the mask.
// |prediction_0| is the first input block. When prediction mode is inter_intra
// (or wedge_inter_intra), this refers to the inter frame prediction. It is
// int16_t* when bitdepth == 8 and uint16_t* otherwise.
// The stride for |prediction_0| is equal to |width|.
// |prediction_1| is the second input block. When prediction mode is inter_intra
// (or wedge_inter_intra), this refers to the intra frame prediction and uses
// Pixel values. It is only used for intra frame prediction when bitdepth >= 10.
// It is int16_t* when bitdepth == 8 and uint16_t* otherwise.
// |prediction_stride_1| is the stride, given in units of [u]int16_t. When
// |is_inter_intra| is false (compound prediction) then |prediction_stride_1| is
// equal to |width|.
// |mask| is an integer array, whose value indicates the weight of the blending.
// |mask_stride| is corresponding stride.
// |width|, |height| are the same for both input blocks.
// If it's inter_intra (or wedge_inter_intra), the valid range of block size is
// [8x8, 32x32], no 4:1/1:4 blocks (Section 5.11.28). Otherwise (including
// difference weighted prediction and compound average prediction), the valid
// range is [8x8, 128x128].
// If there's subsampling, the corresponding width and height are halved for
// chroma planes.
// |is_inter_intra| stands for the prediction mode. If it is true, one of the
// prediction blocks is from intra prediction of current frame. Otherwise, two
// prediction blocks are both inter frame predictions.
// |is_wedge_inter_intra| indicates if the mask is for the wedge prediction.
// |dest| is the output block.
// |dest_stride| is the corresponding stride for dest.
// The pointer arguments do not alias one another.
using MaskBlendFunc = void (*)(const void* prediction_0,
                               const void* prediction_1,
                               ptrdiff_t prediction_stride_1,
                               const uint8_t* mask, ptrdiff_t mask_stride,
                               int width, int height, void* dest,
                               ptrdiff_t dest_stride);

// Mask blending functions signature. Each points to one function with
// a specific setting:
// MaskBlendFunc[subsampling_x + subsampling_y][is_inter_intra].
using MaskBlendFuncs = MaskBlendFunc[3][2];

// This function is similar to the MaskBlendFunc. It is only used when
// |is_inter_intra| is true and |bitdepth| == 8.
// |prediction_[01]| are Pixel values (uint8_t).
// |prediction_1| is also the output buffer.
// The pointer arguments do not alias one another.
using InterIntraMaskBlendFunc8bpp = void (*)(const uint8_t* prediction_0,
                                             uint8_t* prediction_1,
                                             ptrdiff_t prediction_stride_1,
                                             const uint8_t* mask,
                                             ptrdiff_t mask_stride, int width,
                                             int height);

// InterIntra8bpp mask blending functions signature. When is_wedge_inter_intra
// is false, the function at index 0 must be used. Otherwise, the function at
// index subsampling_x + subsampling_y must be used.
using InterIntraMaskBlendFuncs8bpp = InterIntraMaskBlendFunc8bpp[3];

// Obmc (overlapped block motion compensation) blending function signature.
// Section 7.11.3.10.
// This function takes two blocks and produces a blended output stored into the
// first input block. The blending is a weighted average process, controlled by
// values of the mask.
// Obmc is not a compound mode. It is different from other compound blending,
// in terms of precision. The current block is computed using convolution with
// clipping to the range of pixel values. Its above and left blocks are also
// clipped. Therefore obmc blending process doesn't need to clip the output.
// |prediction| is the first input block, which will be overwritten.
// |prediction_stride| is the stride, given in bytes.
// |width|, |height| are the same for both input blocks. The range is [4x2,
// 32x32] for kObmcDirectionVertical and [2x4, 32x32] for
// kObmcDirectionHorizontal, see Section 7.11.3.9.
// |obmc_prediction| is the second input block.
// |obmc_prediction_stride| is its stride, given in bytes.
// The pointer arguments do not alias one another.
using ObmcBlendFunc = void (*)(void* prediction, ptrdiff_t prediction_stride,
                               int width, int height,
                               const void* obmc_prediction,
                               ptrdiff_t obmc_prediction_stride);
using ObmcBlendFuncs = ObmcBlendFunc[kNumObmcDirections];

// Warp function signature. Section 7.11.3.5.
// This function applies warp filtering for each 8x8 block inside the current
// coding block. The filtering process is similar to 2d convolve filtering.
// The horizontal filter is applied followed by the vertical filter.
// The function has to calculate corresponding pixel positions before and
// after warping.
// |source| is the input reference frame buffer.
// |source_stride|, |source_width|, |source_height| are corresponding frame
// stride, width, and height. |source_stride| is given in bytes.
// |warp_params| is the matrix of warp motion: warp_params[i] = mN.
//         [x'     (m2 m3 m0   [x
//     z .  y'  =   m4 m5 m1 *  y
//          1]      m6 m7 1)    1]
// |subsampling_x/y| is the current frame's plane subsampling factor.
// |block_start_x| and |block_start_y| are the starting position the current
// coding block.
// |block_width| and |block_height| are width and height of the current coding
// block. |block_width| and |block_height| are at least 8.
// |alpha|, |beta|, |gamma|, |delta| are valid warp parameters. See the
// comments in the definition of struct GlobalMotion for the range of their
// values.
// |dest| is the output buffer of type Pixel. The output values are clipped to
// Pixel values.
// |dest_stride| is the stride, in units of bytes.
// Rounding precision is derived from the function being called. For horizontal
// filtering kInterRoundBitsHorizontal & kInterRoundBitsHorizontal12bpp will be
// used. For vertical filtering kInterRoundBitsVertical &
// kInterRoundBitsVertical12bpp will be used.
//
// NOTE: WarpFunc assumes the source frame has left, right, top, and bottom
// borders that extend the frame boundary pixels.
// * The left and right borders must be at least 13 pixels wide. In addition,
//   Warp_NEON() may read up to 14 bytes after a row in the |source| buffer.
//   Therefore, there must be at least one extra padding byte after the right
//   border of the last row in the source buffer.
// * The top and bottom borders must be at least 13 pixels high.
// The pointer arguments do not alias one another.
using WarpFunc = void (*)(const void* source, ptrdiff_t source_stride,
                          int source_width, int source_height,
                          const int* warp_params, int subsampling_x,
                          int subsampling_y, int block_start_x,
                          int block_start_y, int block_width, int block_height,
                          int16_t alpha, int16_t beta, int16_t gamma,
                          int16_t delta, void* dest, ptrdiff_t dest_stride);

// Warp for compound predictions. Section 7.11.3.5.
// Similar to WarpFunc, but |dest| is a uint16_t predictor buffer,
// |dest_stride| is given in units of uint16_t and |inter_round_bits_vertical|
// is always 7 (kCompoundInterRoundBitsVertical).
// Rounding precision is derived from the function being called. For horizontal
// filtering kInterRoundBitsHorizontal & kInterRoundBitsHorizontal12bpp will be
// used. For vertical filtering kInterRoundBitsCompondVertical will be used.
using WarpCompoundFunc = WarpFunc;

constexpr int kNumAutoRegressionLags = 4;
// Applies an auto-regressive filter to the white noise in |luma_grain_buffer|.
// Section 7.18.3.3, second code block
// |params| are parameters read from frame header, mainly providing
// auto_regression_coeff_y for the filter and auto_regression_shift to right
// shift the filter sum by. Note: This method assumes
// params.auto_regression_coeff_lag is not 0. Do not call this method if
// params.auto_regression_coeff_lag is 0.
using LumaAutoRegressionFunc = void (*)(const FilmGrainParams& params,
                                        void* luma_grain_buffer);
// Function index is auto_regression_coeff_lag - 1.
using LumaAutoRegressionFuncs =
    LumaAutoRegressionFunc[kNumAutoRegressionLags - 1];

// Applies an auto-regressive filter to the white noise in u_grain and v_grain.
// Section 7.18.3.3, third code block
// The |luma_grain_buffer| provides samples that are added to the autoregressive
// sum when num_y_points > 0.
// |u_grain_buffer| and |v_grain_buffer| point to the buffers of chroma noise
// that were generated from the stored Gaussian sequence, and are overwritten
// with the results of the autoregressive filter. |params| are parameters read
// from frame header, mainly providing auto_regression_coeff_u and
// auto_regression_coeff_v for each chroma plane's filter, and
// auto_regression_shift to right shift the filter sums by.
// The pointer arguments do not alias one another.
using ChromaAutoRegressionFunc = void (*)(const FilmGrainParams& params,
                                          const void* luma_grain_buffer,
                                          int subsampling_x, int subsampling_y,
                                          void* u_grain_buffer,
                                          void* v_grain_buffer);
using ChromaAutoRegressionFuncs =
    ChromaAutoRegressionFunc[/*use_luma*/ 2][kNumAutoRegressionLags];

// Build an image-wide "stripe" of grain noise for every 32 rows in the image.
// Section 7.18.3.5, first code block.
// Each 32x32 luma block is copied at a random offset specified via
// |grain_seed| from the grain template produced by autoregression, and the same
// is done for chroma grains, subject to subsampling.
// |width| and |height| are the dimensions of the overall image.
// |noise_stripes_buffer| points to an Array2DView with one row for each stripe.
// Because this function treats all planes identically and independently, it is
// simplified to take one grain buffer at a time. This means duplicating some
// random number generations, but that work can be reduced in other ways.
// The pointer arguments do not alias one another.
using ConstructNoiseStripesFunc = void (*)(const void* grain_buffer,
                                           int grain_seed, int width,
                                           int height, int subsampling_x,
                                           int subsampling_y,
                                           void* noise_stripes_buffer);
using ConstructNoiseStripesFuncs =
    ConstructNoiseStripesFunc[/*overlap_flag*/ 2];

// Compute the one or two overlap rows for each stripe copied to the noise
// image.
// Section 7.18.3.5, second code block. |width| and |height| are the
// dimensions of the overall image. |noise_stripes_buffer| points to an
// Array2DView with one row for each stripe. |noise_image_buffer| points to an
// Array2D containing the allocated plane for this frame. Because this function
// treats all planes identically and independently, it is simplified to take one
// grain buffer at a time.
// The pointer arguments do not alias one another.
using ConstructNoiseImageOverlapFunc =
    void (*)(const void* noise_stripes_buffer, int width, int height,
             int subsampling_x, int subsampling_y, void* noise_image_buffer);

// Populate a scaling lookup table with interpolated values of a piecewise
// linear function where values in |point_value| are mapped to the values in
// |point_scaling|.
// |num_points| can be between 0 and 15. When 0, the lookup table is set to
// zero.
// |point_value| and |point_scaling| have |num_points| valid elements.
// The pointer arguments do not alias one another.
using InitializeScalingLutFunc = void (*)(int num_points,
                                          const uint8_t point_value[],
                                          const uint8_t point_scaling[],
                                          int16_t* scaling_lut,
                                          const int scaling_lut_length);

// Blend noise with image. Section 7.18.3.5, third code block.
// |width| is the width of each row, while |height| is how many rows to compute.
// |start_height| is an offset for the noise image, to support multithreading.
// |min_value|, |max_luma|, and |max_chroma| are computed by the caller of these
// functions, according to the code in the spec.
// |source_plane_y| and |source_plane_uv| are the plane buffers of the decoded
// frame. They are blended with the film grain noise and written to
// |dest_plane_y| and |dest_plane_uv| as final output for display.
// source_plane_* and dest_plane_* may point to the same buffer, in which case
// the film grain noise is added in place.
// |scaling_lut_y|  and |scaling_lut| represent a piecewise linear mapping from
// the frame's raw pixel value, to a scaling factor for the noise sample.
// |scaling_shift| is applied as a right shift after scaling, so that scaling
// down is possible. It is found in FilmGrainParams, but supplied directly to
// BlendNoiseWithImageLumaFunc because it's the only member used.
// The dest plane may point to the source plane, depending on the value of
// frame_header.show_existing_frame. |noise_image_ptr| and scaling_lut.* do not
// alias other arguments.
using BlendNoiseWithImageLumaFunc = void (*)(
    const void* noise_image_ptr, int min_value, int max_value,
    int scaling_shift, int width, int height, int start_height,
    const int16_t* scaling_lut_y, const void* source_plane_y,
    ptrdiff_t source_stride_y, void* dest_plane_y, ptrdiff_t dest_stride_y);

using BlendNoiseWithImageChromaFunc = void (*)(
    Plane plane, const FilmGrainParams& params, const void* noise_image_ptr,
    int min_value, int max_value, int width, int height, int start_height,
    int subsampling_x, int subsampling_y, const int16_t* scaling_lut,
    const void* source_plane_y, ptrdiff_t source_stride_y,
    const void* source_plane_uv, ptrdiff_t source_stride_uv,
    void* dest_plane_uv, ptrdiff_t dest_stride_uv);

using BlendNoiseWithImageChromaFuncs =
    BlendNoiseWithImageChromaFunc[/*chroma_scaling_from_luma*/ 2];

//------------------------------------------------------------------------------

struct FilmGrainFuncs {
  LumaAutoRegressionFuncs luma_auto_regression;
  ChromaAutoRegressionFuncs chroma_auto_regression;
  ConstructNoiseStripesFuncs construct_noise_stripes;
  ConstructNoiseImageOverlapFunc construct_noise_image_overlap;
  InitializeScalingLutFunc initialize_scaling_lut;
  BlendNoiseWithImageLumaFunc blend_noise_luma;
  BlendNoiseWithImageChromaFuncs blend_noise_chroma;
};

// Motion field projection function signature. Section 7.9.
// |reference_info| provides reference information for motion field projection.
// |reference_to_current_with_sign| is the precalculated reference frame id
// distance from current frame.
// |dst_sign| is -1 for LAST_FRAME and LAST2_FRAME, or 0 (1 in spec) for others.
// |y8_start| and |y8_end| are the start and end 8x8 rows of the current tile.
// |x8_start| and |x8_end| are the start and end 8x8 columns of the current
// tile.
// |motion_field| is the output which saves the projected motion field
// information.
// Note: Only the entry from the 8-bit Dsp table is used as this function is
// bitdepth agnostic.
using MotionFieldProjectionKernelFunc = void (*)(
    const ReferenceInfo& reference_info, int reference_to_current_with_sign,
    int dst_sign, int y8_start, int y8_end, int x8_start, int x8_end,
    TemporalMotionField* motion_field);

// Compound temporal motion vector projection function signature.
// Section 7.9.3 and 7.10.2.10.
// |temporal_mvs| is the aligned set of temporal reference motion vectors.
// |temporal_reference_offsets| specifies the number of frames covered by the
// original motion vector.
// |reference_offsets| specifies the number of frames to be covered by the
// projected motion vector.
// |count| is the number of the temporal motion vectors.
// |candidate_mvs| is the aligned set of projected motion vectors.
// The pointer arguments do not alias one another.
// Note: Only the entry from the 8-bit Dsp table is used as this function is
// bitdepth agnostic.
using MvProjectionCompoundFunc = void (*)(
    const MotionVector* temporal_mvs, const int8_t* temporal_reference_offsets,
    const int reference_offsets[2], int count,
    CompoundMotionVector* candidate_mvs);

// Single temporal motion vector projection function signature.
// Section 7.9.3 and 7.10.2.10.
// |temporal_mvs| is the aligned set of temporal reference motion vectors.
// |temporal_reference_offsets| specifies the number of frames covered by the
// original motion vector.
// |reference_offset| specifies the number of frames to be covered by the
// projected motion vector.
// |count| is the number of the temporal motion vectors.
// |candidate_mvs| is the aligned set of projected motion vectors.
// The pointer arguments do not alias one another.
// Note: Only the entry from the 8-bit Dsp table is used as this function is
// bitdepth agnostic.
using MvProjectionSingleFunc = void (*)(
    const MotionVector* temporal_mvs, const int8_t* temporal_reference_offsets,
    int reference_offset, int count, MotionVector* candidate_mvs);

struct Dsp {
  AverageBlendFunc average_blend;
  CdefDirectionFunc cdef_direction;
  CdefFilteringFuncs cdef_filters;
  CflIntraPredictorFuncs cfl_intra_predictors;
  CflSubsamplerFuncs cfl_subsamplers;
  ConvolveFuncs convolve;
  ConvolveScaleFuncs convolve_scale;
  DirectionalIntraPredictorZone1Func directional_intra_predictor_zone1;
  DirectionalIntraPredictorZone2Func directional_intra_predictor_zone2;
  DirectionalIntraPredictorZone3Func directional_intra_predictor_zone3;
  DistanceWeightedBlendFunc distance_weighted_blend;
  FilmGrainFuncs film_grain;
  FilterIntraPredictorFunc filter_intra_predictor;
  InterIntraMaskBlendFuncs8bpp inter_intra_mask_blend_8bpp;
  IntraEdgeFilterFunc intra_edge_filter;
  IntraEdgeUpsamplerFunc intra_edge_upsampler;
  IntraPredictorFuncs intra_predictors;
  InverseTransformAddFuncs inverse_transforms;
  LoopFilterFuncs loop_filters;
  LoopRestorationFuncs loop_restorations;
  MaskBlendFuncs mask_blend;
  MotionFieldProjectionKernelFunc motion_field_projection_kernel;
  MvProjectionCompoundFunc mv_projection_compound[3];
  MvProjectionSingleFunc mv_projection_single[3];
  ObmcBlendFuncs obmc_blend;
  SuperResCoefficientsFunc super_res_coefficients;
  SuperResFunc super_res;
  WarpCompoundFunc warp_compound;
  WarpFunc warp;
  WeightMaskFuncs weight_mask;
};

// Initializes function pointers based on build config and runtime
// environment. Must be called once before first use. This function is
// thread-safe.
void DspInit();

// Returns the appropriate Dsp table for |bitdepth| or nullptr if one doesn't
// exist.
const Dsp* GetDspTable(int bitdepth);

}  // namespace dsp

namespace dsp_internal {

// Visual Studio builds don't have a way to detect SSE4_1. Only exclude the C
// functions if /arch:AVX2 is used across all sources.
#if !LIBGAV1_TARGETING_AVX2 && \
    (defined(_MSC_VER) || (defined(_M_IX86) || defined(_M_X64)))
#undef LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS
#define LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS 1
#endif

// Returns true if a more highly optimized version of |func| is not defined for
// the associated bitdepth or if it is forcibly enabled with
// LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS. The define checked for |func| corresponds
// to the LIBGAV1_Dsp<bitdepth>bpp_|func| define in the header file associated
// with the module.
// |func| is one of:
//   - FunctionName, e.g., SelfGuidedFilter.
//   - [sub-table-index1][...-indexN] e.g.,
//     TransformSize4x4_IntraPredictorDc. The indices correspond to enum values
//     used as lookups with leading 'k' removed.
//
//  NEON support is the only extension available for ARM and it is always
//  required. Because of this restriction DSP_ENABLED_8BPP_NEON(func) is always
//  true and can be omitted.
#define DSP_ENABLED_8BPP_AVX2(func)    \
  (LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS || \
   LIBGAV1_Dsp8bpp_##func == LIBGAV1_CPU_AVX2)
#define DSP_ENABLED_10BPP_AVX2(func)   \
  (LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS || \
   LIBGAV1_Dsp10bpp_##func == LIBGAV1_CPU_AVX2)
#define DSP_ENABLED_8BPP_SSE4_1(func)  \
  (LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS || \
   LIBGAV1_Dsp8bpp_##func == LIBGAV1_CPU_SSE4_1)
#define DSP_ENABLED_10BPP_SSE4_1(func) \
  (LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS || \
   LIBGAV1_Dsp10bpp_##func == LIBGAV1_CPU_SSE4_1)

// Initializes C-only function pointers. Note some entries may be set to
// nullptr if LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS is not defined. This is meant
// for use in tests only, it is not thread-safe.
void DspInit_C();

// Returns the appropriate Dsp table for |bitdepth| or nullptr if one doesn't
// exist. This version is meant for use by test or dsp/*Init() functions only.
dsp::Dsp* GetWritableDspTable(int bitdepth);

}  // namespace dsp_internal
}  // namespace libgav1

#endif  // LIBGAV1_SRC_DSP_DSP_H_

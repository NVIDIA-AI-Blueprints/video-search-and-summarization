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

#ifndef LIBGAV1_SRC_UTILS_TYPES_H_
#define LIBGAV1_SRC_UTILS_TYPES_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>

#include "src/utils/array_2d.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"

namespace libgav1 {

union MotionVector {
  // Motion vectors will always fit in int16_t and using int16_t here instead
  // of int saves significant memory since some of the frame sized structures
  // store motion vectors.
  // Index 0 is the entry for row (horizontal direction) motion vector.
  // Index 1 is the entry for column (vertical direction) motion vector.
  int16_t mv[2];
  // A uint32_t view into the |mv| array. Useful for cases where both the
  // motion vectors have to be copied or compared with a single 32 bit
  // instruction.
  uint32_t mv32;
};

union CompoundMotionVector {
  MotionVector mv[2];
  // A uint64_t view into the |mv| array. Useful for cases where all the motion
  // vectors have to be copied or compared with a single 64 bit instruction.
  uint64_t mv64;
};

// Stores the motion information used for motion field estimation.
struct TemporalMotionField : public Allocable {
  Array2D<MotionVector> mv;
  Array2D<int8_t> reference_offset;
};

// MvContexts contains the contexts used to decode portions of an inter block
// mode info to set the y_mode field in BlockParameters.
//
// The contexts in the struct correspond to the ZeroMvContext, RefMvContext,
// and NewMvContext variables in the spec.
struct MvContexts {
  int zero_mv;
  int reference_mv;
  int new_mv;
};

struct PaletteModeInfo {
  uint8_t size[kNumPlaneTypes];
  uint16_t color[kMaxPlanes][kMaxPaletteSize];
};

// Stores the parameters used by the prediction process. The members of the
// struct are filled in when parsing the bitstream and used when the prediction
// is computed. The information in this struct is associated with a single
// block.
// While both BlockParameters and PredictionParameters store information
// pertaining to a Block, the only difference is that BlockParameters outlives
// the block itself (for example, some of the variables in BlockParameters are
// used to compute the context for reading elements in the subsequent blocks).
struct PredictionParameters : public Allocable {
  // Restore the index in the unsorted mv stack from the least 3 bits of sorted
  // |weight_index_stack|.
  const MotionVector& reference_mv(int stack_index) const {
    return ref_mv_stack[7 - (weight_index_stack[stack_index] & 7)];
  }
  const MotionVector& reference_mv(int stack_index, int mv_index) const {
    return compound_ref_mv_stack[7 - (weight_index_stack[stack_index] & 7)]
        .mv[mv_index];
  }

  void IncreaseWeight(ptrdiff_t index, int weight) {
    weight_index_stack[index] += weight << 3;
  }

  void SetWeightIndexStackEntry(int index, int weight) {
    weight_index_stack[index] = (weight << 3) + 7 - index;
  }

  bool use_filter_intra;
  FilterIntraPredictor filter_intra_mode;
  int angle_delta[kNumPlaneTypes];
  int8_t cfl_alpha_u;
  int8_t cfl_alpha_v;
  int max_luma_width;
  int max_luma_height;
  Array2D<uint8_t> color_index_map[kNumPlaneTypes];
  bool use_intra_block_copy;
  InterIntraMode inter_intra_mode;
  bool is_wedge_inter_intra;
  int wedge_index;
  int wedge_sign;
  bool mask_is_inverse;
  MotionMode motion_mode;
  CompoundPredictionType compound_prediction_type;
  union {
    // |ref_mv_stack| and |compound_ref_mv_stack| are not sorted after
    // construction. reference_mv() must be called to get the correct element.
    MotionVector ref_mv_stack[kMaxRefMvStackSize];
    CompoundMotionVector compound_ref_mv_stack[kMaxRefMvStackSize];
  };
  // The least 3 bits of |weight_index_stack| store the index information, and
  // the other bits store the weight. The index information is actually 7 -
  // index to make the descending order sort stable (preserves the original
  // order for elements with the same weight). Sorting an int16_t array is much
  // faster than sorting a struct array with weight and index stored separately.
  int16_t weight_index_stack[kMaxRefMvStackSize];
  // In the spec, the weights of all the nearest mvs are incremented by a bonus
  // weight which is larger than any natural weight, and later the weights of
  // the mvs are compared with this bonus weight to determine their contexts. We
  // replace this procedure by introducing |nearest_mv_count|, which records the
  // count of the nearest mvs. Since all the nearest mvs are in the beginning of
  // the mv stack, the index of a mv in the mv stack can be compared with
  // |nearest_mv_count| to get that mv's context.
  int nearest_mv_count;
  int ref_mv_count;
  int ref_mv_index;
  MotionVector global_mv[2];
  int num_warp_samples;
  int warp_estimate_candidates[kMaxLeastSquaresSamples][4];
  PaletteModeInfo palette_mode_info;
  int8_t segment_id;  // segment_id is in the range [0, 7].
  PredictionMode uv_mode;
  bool chroma_top_uses_smooth_prediction;
  bool chroma_left_uses_smooth_prediction;
};

// A lot of BlockParameters objects are created, so the smallest type is used
// for each field. The ranges of some fields are documented to justify why
// their types are large enough.
struct BlockParameters : public Allocable {
  BlockSize size;
  bool skip;
  bool is_inter;
  PredictionMode y_mode;
  TransformSize uv_transform_size;
  InterpolationFilter interpolation_filter[2];
  ReferenceFrameType reference_frame[2];
  // The index of this array is as follows:
  //  0 - Y plane vertical filtering.
  //  1 - Y plane horizontal filtering.
  //  2 - U plane (both directions).
  //  3 - V plane (both directions).
  uint8_t deblock_filter_level[kFrameLfCount];
  CompoundMotionVector mv;
  // When |Tile::split_parse_and_decode_| is true, each block gets its own
  // instance of |prediction_parameters|. When it is false, all the blocks point
  // to |Tile::prediction_parameters_|. This field is valid only as long as the
  // block is *being* decoded. The lifetime and usage of this field can be
  // better understood by following its flow in tile.cc.
  std::unique_ptr<PredictionParameters> prediction_parameters;
};

// Used to store the left and top block parameters that are used for computing
// the cdf context of the subsequent blocks.
struct BlockCdfContext {
  bool use_predicted_segment_id[32];
  bool is_explicit_compound_type[32];  // comp_group_idx in the spec.
  bool is_compound_type_average[32];   // compound_idx in the spec.
  bool skip_mode[32];
  uint8_t palette_size[kNumPlaneTypes][32];
  uint16_t palette_color[32][kNumPlaneTypes][kMaxPaletteSize];
  PredictionMode uv_mode[32];
};

// A five dimensional array used to store the wedge masks. The dimensions are:
//   - block_size_index (returned by GetWedgeBlockSizeIndex() in prediction.cc).
//   - flip_sign (0 or 1).
//   - wedge_index (0 to 15).
//   - each of those three dimensions is a 2d array of block_width by
//     block_height.
using WedgeMaskArray =
    std::array<std::array<std::array<Array2D<uint8_t>, 16>, 2>, 9>;

enum GlobalMotionTransformationType : uint8_t {
  kGlobalMotionTransformationTypeIdentity,
  kGlobalMotionTransformationTypeTranslation,
  kGlobalMotionTransformationTypeRotZoom,
  kGlobalMotionTransformationTypeAffine,
  kNumGlobalMotionTransformationTypes
};

// Global motion and warped motion parameters. See the paper for more info:
// S. Parker, Y. Chen, D. Barker, P. de Rivaz, D. Mukherjee, "Global and locally
// adaptive warped motion compensation in video compression", Proc. IEEE
// International Conference on Image Processing (ICIP), pp. 275-279, Sep. 2017.
struct GlobalMotion {
  GlobalMotionTransformationType type;
  int32_t params[6];

  // Represent two shearing operations. Computed from |params| by SetupShear().
  //
  // The least significant six (= kWarpParamRoundingBits) bits are all zeros.
  // (This means alpha, beta, gamma, and delta could be represented by a 10-bit
  // signed integer.) The minimum value is INT16_MIN (= -32768) and the maximum
  // value is 32704 = 0x7fc0, the largest int16_t value whose least significant
  // six bits are all zeros.
  //
  // Valid warp parameters (as validated by SetupShear()) have smaller ranges.
  // Their absolute values are less than 2^14 (= 16384). (This follows from
  // the warpValid check at the end of Section 7.11.3.6.)
  //
  // NOTE: Section 7.11.3.6 of the spec allows a maximum value of 32768, which
  // is outside the range of int16_t. When cast to int16_t, 32768 becomes
  // -32768. This potential int16_t overflow does not matter because either
  // 32768 or -32768 causes SetupShear() to return false,
  int16_t alpha;
  int16_t beta;
  int16_t gamma;
  int16_t delta;
};

// Loop filter parameters:
//
// If level[0] and level[1] are both equal to 0, the loop filter process is
// not invoked.
//
// |sharpness| and |delta_enabled| are only used by the loop filter process.
//
// The |ref_deltas| and |mode_deltas| arrays are used not only by the loop
// filter process but also by the reference frame update and loading
// processes. The loop filter process uses |ref_deltas| and |mode_deltas| only
// when |delta_enabled| is true.
struct LoopFilter {
  // Contains loop filter strength values in the range of [0, 63].
  std::array<int8_t, kFrameLfCount> level;
  // Indicates the sharpness level in the range of [0, 7].
  int8_t sharpness;
  // Whether the filter level depends on the mode and reference frame used to
  // predict a block.
  bool delta_enabled;
  // Whether additional syntax elements were read that specify which mode and
  // reference frame deltas are to be updated. loop_filter_delta_update field in
  // Section 5.9.11 of the spec.
  bool delta_update;
  // Contains the adjustment needed for the filter level based on the chosen
  // reference frame, in the range of [-64, 63].
  std::array<int8_t, kNumReferenceFrameTypes> ref_deltas;
  // Contains the adjustment needed for the filter level based on the chosen
  // mode, in the range of [-64, 63].
  std::array<int8_t, kLoopFilterMaxModeDeltas> mode_deltas;
};

struct Delta {
  bool present;
  uint8_t scale;
  bool multi;
};

struct Cdef {
  uint8_t damping;  // damping value from the spec + (bitdepth - 8).
  uint8_t bits;
  // All the strength values are the values from the spec and left shifted by
  // (bitdepth - 8).
  uint8_t y_primary_strength[kMaxCdefStrengths];
  uint8_t y_secondary_strength[kMaxCdefStrengths];
  uint8_t uv_primary_strength[kMaxCdefStrengths];
  uint8_t uv_secondary_strength[kMaxCdefStrengths];
};

struct TileInfo {
  bool uniform_spacing;
  int sb_rows;
  int sb_columns;
  int tile_count;
  int tile_columns_log2;
  int tile_columns;
  int tile_column_start[kMaxTileColumns + 1];
  // This field is not used by libgav1, but is populated for use by some
  // hardware decoders. So it must not be removed.
  int tile_column_width_in_superblocks[kMaxTileColumns + 1];
  int tile_rows_log2;
  int tile_rows;
  int tile_row_start[kMaxTileRows + 1];
  // This field is not used by libgav1, but is populated for use by some
  // hardware decoders. So it must not be removed.
  int tile_row_height_in_superblocks[kMaxTileRows + 1];
  int16_t context_update_id;
  uint8_t tile_size_bytes;
};

struct LoopRestoration {
  LoopRestorationType type[kMaxPlanes];
  int unit_size_log2[kMaxPlanes];
};

// Stores the quantization parameters of Section 5.9.12.
struct QuantizerParameters {
  // base_index is in the range [0, 255].
  uint8_t base_index;
  int8_t delta_dc[kMaxPlanes];
  // delta_ac[kPlaneY] is always 0.
  int8_t delta_ac[kMaxPlanes];
  bool use_matrix;
  // The |matrix_level| array is used only when |use_matrix| is true.
  // matrix_level[plane] specifies the level in the quantizer matrix that
  // should be used for decoding |plane|. The quantizer matrix has 15 levels,
  // from 0 to 14. The range of matrix_level[plane] is [0, 15]. If
  // matrix_level[plane] is 15, the quantizer matrix is not used.
  int8_t matrix_level[kMaxPlanes];
};

// The corresponding segment feature constants in the AV1 spec are named
// SEG_LVL_xxx.
enum SegmentFeature : uint8_t {
  kSegmentFeatureQuantizer,
  kSegmentFeatureLoopFilterYVertical,
  kSegmentFeatureLoopFilterYHorizontal,
  kSegmentFeatureLoopFilterU,
  kSegmentFeatureLoopFilterV,
  kSegmentFeatureReferenceFrame,
  kSegmentFeatureSkip,
  kSegmentFeatureGlobalMv,
  kSegmentFeatureMax
};

struct Segmentation {
  // 5.11.14.
  // Returns true if the feature is enabled in the segment.
  bool FeatureActive(int segment_id, SegmentFeature feature) const {
    return enabled && segment_id < kMaxSegments &&
           feature_enabled[segment_id][feature];
  }

  // Returns true if the feature is signed.
  static bool FeatureSigned(SegmentFeature feature) {
    // Only the first five segment features are signed, so this comparison
    // suffices.
    return feature <= kSegmentFeatureLoopFilterV;
  }

  bool enabled;
  bool update_map;
  bool update_data;
  bool temporal_update;
  // True if the segment id will be read before the skip syntax element. False
  // if the skip syntax element will be read first.
  bool segment_id_pre_skip;
  // The highest numbered segment id that has some enabled feature. Used as
  // the upper bound for decoding segment ids.
  int8_t last_active_segment_id;

  bool feature_enabled[kMaxSegments][kSegmentFeatureMax];
  int16_t feature_data[kMaxSegments][kSegmentFeatureMax];
  bool lossless[kMaxSegments];
  // Cached values of get_qindex(1, segmentId), to be consumed by
  // Tile::ReadTransformType(). The values are in the range [0, 255].
  uint8_t qindex[kMaxSegments];
};

// Section 6.8.20.
// Note: In spec, film grain section uses YCbCr to denote variable names,
// such as num_cb_points, num_cr_points. To keep it consistent with other
// parts of code, we use YUV, i.e., num_u_points, num_v_points, etc.
struct FilmGrainParams {
  bool apply_grain;
  bool update_grain;
  bool chroma_scaling_from_luma;
  bool overlap_flag;
  bool clip_to_restricted_range;

  uint8_t num_y_points;  // [0, 14].
  uint8_t num_u_points;  // [0, 10].
  uint8_t num_v_points;  // [0, 10].
  // Must be [0, 255]. 10/12 bit /= 4 or 16. Must be in increasing order.
  uint8_t point_y_value[14];
  uint8_t point_y_scaling[14];
  uint8_t point_u_value[10];
  uint8_t point_u_scaling[10];
  uint8_t point_v_value[10];
  uint8_t point_v_scaling[10];

  uint8_t chroma_scaling;             // grain_scaling_minus_8 + 8: [8, 11].
  uint8_t auto_regression_coeff_lag;  // ar_coeff_lag: [0, 3].
  // ar_coeffs_{y,u,v}_plus_128 - 128: [-128, 127].
  int8_t auto_regression_coeff_y[24];
  int8_t auto_regression_coeff_u[25];
  int8_t auto_regression_coeff_v[25];
  // Shift value: ar_coeff_shift_minus_6 + 6, auto regression coeffs range:
  // 6: [-2, 2)
  // 7: [-1, 1)
  // 8: [-0.5, 0.5)
  // 9: [-0.25, 0.25)
  uint8_t auto_regression_shift;

  uint16_t grain_seed;
  int reference_index;
  int grain_scale_shift;
  int8_t u_multiplier;       // cb_mult - 128:      [-128, 127].
  int8_t u_luma_multiplier;  // cb_luma_mult - 128: [-128, 127].
  int16_t u_offset;          // cb_offset - 256:    [-256, 255].
  int8_t v_multiplier;       // cr_mult - 128:      [-128, 127].
  int8_t v_luma_multiplier;  // cr_luma_mult - 128: [-128, 127].
  int16_t v_offset;          // cr_offset - 256:    [-256, 255].
};

struct ObuFrameHeader {
  uint16_t display_frame_id;
  uint16_t current_frame_id;
  int64_t frame_offset;
  uint16_t expected_frame_id[kNumInterReferenceFrameTypes];
  int32_t width;
  int32_t height;
  int32_t columns4x4;
  int32_t rows4x4;
  // The render size (render_width and render_height) is a hint to the
  // application about the desired display size. It has no effect on the
  // decoding process.
  int32_t render_width;
  int32_t render_height;
  int32_t upscaled_width;
  LoopRestoration loop_restoration;
  uint32_t buffer_removal_time[kMaxOperatingPoints];
  uint32_t frame_presentation_time;
  // Note: global_motion[0] (for kReferenceFrameIntra) is not used.
  std::array<GlobalMotion, kNumReferenceFrameTypes> global_motion;
  TileInfo tile_info;
  QuantizerParameters quantizer;
  Segmentation segmentation;
  bool show_existing_frame;
  // frame_to_show is in the range [0, 7]. Only used if show_existing_frame is
  // true.
  int8_t frame_to_show;
  FrameType frame_type;
  bool show_frame;
  bool showable_frame;
  bool error_resilient_mode;
  bool enable_cdf_update;
  bool frame_size_override_flag;
  // The order_hint syntax element in the uncompressed header. If
  // show_existing_frame is false, the OrderHint variable in the spec is equal
  // to this field, and so this field can be used in place of OrderHint when
  // show_existing_frame is known to be false, such as during tile decoding.
  uint8_t order_hint;
  int8_t primary_reference_frame;
  bool render_and_frame_size_different;
  bool use_superres;
  uint8_t superres_scale_denominator;
  bool allow_screen_content_tools;
  bool allow_intrabc;
  bool frame_refs_short_signaling;
  // A bitmask that specifies which reference frame slots will be updated with
  // the current frame after it is decoded.
  uint8_t refresh_frame_flags;
  static_assert(sizeof(ObuFrameHeader::refresh_frame_flags) * 8 ==
                    kNumReferenceFrameTypes,
                "");
  bool found_reference;
  int8_t force_integer_mv;
  bool allow_high_precision_mv;
  InterpolationFilter interpolation_filter;
  bool is_motion_mode_switchable;
  bool use_ref_frame_mvs;
  bool enable_frame_end_update_cdf;
  // True if all segments are losslessly encoded at the coded resolution.
  bool coded_lossless;
  // True if all segments are losslessly encoded at the upscaled resolution.
  bool upscaled_lossless;
  TxMode tx_mode;
  // True means that the mode info for inter blocks contains the syntax
  // element comp_mode that indicates whether to use single or compound
  // prediction. False means that all inter blocks will use single prediction.
  bool reference_mode_select;
  // The frames to use for compound prediction when skip_mode is true.
  ReferenceFrameType skip_mode_frame[2];
  bool skip_mode_present;
  bool reduced_tx_set;
  bool allow_warped_motion;
  Delta delta_q;
  Delta delta_lf;
  // A valid value of reference_frame_index[i] is in the range [0, 7]. -1
  // indicates an invalid value.
  //
  // NOTE: When the frame is an intra frame (frame_type is kFrameKey or
  // kFrameIntraOnly), reference_frame_index is not used and may be
  // uninitialized.
  int8_t reference_frame_index[kNumInterReferenceFrameTypes];
  // The ref_order_hint[ i ] syntax element in the uncompressed header.
  // Specifies the expected output order hint for each reference frame.
  uint8_t reference_order_hint[kNumReferenceFrameTypes];
  LoopFilter loop_filter;
  Cdef cdef;
  FilmGrainParams film_grain_params;
};

// Structure used for traversing the partition tree.
struct PartitionTreeNode {
  PartitionTreeNode() = default;
  PartitionTreeNode(int row4x4, int column4x4, BlockSize block_size)
      : row4x4(row4x4), column4x4(column4x4), block_size(block_size) {}
  int row4x4 = -1;
  int column4x4 = -1;
  BlockSize block_size = kBlockInvalid;
};

// Structure used for storing the transform parameters in a superblock.
struct TransformParameters {
  TransformParameters() = default;
  TransformParameters(TransformType type, int non_zero_coeff_count)
      : type(type), non_zero_coeff_count(non_zero_coeff_count) {}
  TransformType type;
  int non_zero_coeff_count;
};

}  // namespace libgav1
#endif  // LIBGAV1_SRC_UTILS_TYPES_H_

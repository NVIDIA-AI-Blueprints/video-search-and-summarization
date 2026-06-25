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

#ifndef LIBGAV1_SRC_SYMBOL_DECODER_CONTEXT_H_
#define LIBGAV1_SRC_SYMBOL_DECODER_CONTEXT_H_

#include <cassert>
#include <cstdint>

#include "src/dsp/constants.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"

namespace libgav1 {

enum {
  kPartitionContexts = 4,
  kSegmentIdContexts = 3,
  kUsePredictedSegmentIdContexts = 3,
  kSkipContexts = 3,
  kSkipModeContexts = 3,
  kBooleanFieldCdfSize = 3,
  kDeltaSymbolCount = 4,  // Used for both delta_q and delta_lf.
  kIntraModeContexts = 5,
  kYModeContexts = 4,
  kAngleDeltaSymbolCount = 2 * kMaxAngleDelta + 1,
  kCflAlphaSignsSymbolCount = 8,
  kCflAlphaContexts = 6,
  kCflAlphaSymbolCount = 16,
  kTxDepthContexts = 3,
  kMaxTxDepthSymbolCount = 3,
  kTxSplitContexts = 21,
  kCoefficientQuantizerContexts = 4,
  kNumSquareTransformSizes = 5,
  kAllZeroContexts = 13,
  kNumExtendedTransformSizes = 4,
  kEobPtContexts = 2,
  kEobPt16SymbolCount = 5,
  kEobPt32SymbolCount = 6,
  kEobPt64SymbolCount = 7,
  kEobPt128SymbolCount = 8,
  kEobPt256SymbolCount = 9,
  kEobPt512SymbolCount = 10,
  kEobPt1024SymbolCount = 11,
  kEobExtraContexts = 9,
  kCoeffBaseEobContexts = 4,
  kCoeffBaseEobSymbolCount = 3,
  kCoeffBaseContexts = 42,
  kCoeffBaseSymbolCount = 4,
  kCoeffBaseRangeContexts = 21,
  kCoeffBaseRangeSymbolCount = 4,
  kDcSignContexts = 3,
  kPaletteBlockSizeContexts = 7,
  kPaletteYModeContexts = 3,
  kPaletteUVModeContexts = 2,
  kPaletteSizeSymbolCount = 7,
  kPaletteColorIndexContexts = 5,
  kPaletteColorIndexSymbolCount = 8,
  kIsInterContexts = 4,
  kUseCompoundReferenceContexts = 5,
  kCompoundReferenceTypeContexts = 5,
  kReferenceContexts = 3,
  kCompoundPredictionModeContexts = 8,
  kNewMvContexts = 6,
  kZeroMvContexts = 2,
  kReferenceMvContexts = 6,
  kRefMvIndexContexts = 3,
  kInterIntraContexts = 3,
  kWedgeIndexSymbolCount = 16,
  kIsExplicitCompoundTypeContexts = 6,
  kIsCompoundTypeAverageContexts = 6,
  kInterpolationFilterContexts = 16,
  kMvContexts = 2,
  kMvClassSymbolCount = 11,
  kMvFractionSymbolCount = 4,
  kMvBitSymbolCount = 10,
  kNumMvComponents = 2,
};  // anonymous enum

struct SymbolDecoderContext {
  SymbolDecoderContext() = default;
  explicit SymbolDecoderContext(int base_quantizer_index) {
    Initialize(base_quantizer_index);
  }

  void Initialize(int base_quantizer_index);

  // Partition related variables and functions.
  static int PartitionCdfSize(int block_size_log2);

  // Returns the cdf array index for inter_tx_type or intra_tx_type based on
  // |tx_set|.
  static int TxTypeIndex(TransformSet tx_set) {
    switch (tx_set) {
      case kTransformSetInter1:
      case kTransformSetIntra1:
        return 0;
      case kTransformSetInter2:
      case kTransformSetIntra2:
        return 1;
      case kTransformSetInter3:
        return 2;
      default:
        // This path should not be hit. 0 is returned rather than -1 to avoid
        // -Warray-bounds.
        assert(tx_set != kTransformSetDctOnly && tx_set != kNumTransformSets);
        return 0;
    }
  }

  // Resets the intra_frame_y_mode_cdf array to the default.
  void ResetIntraFrameYModeCdf();

  // Resets the symbol counters of all the CDF arrays to zero. Symbol counter is
  // the last used element in the innermost dimension of each of the CDF array.
  void ResetCounters();

  // Note kMaxAlignment allows for aligned instructions to be used in the
  // copies done in Initialize().
  alignas(kMaxAlignment) uint16_t
      partition_cdf[kBlockWidthCount][kPartitionContexts]
                   [kMaxPartitionTypes + 1];
  alignas(kMaxAlignment) uint16_t
      segment_id_cdf[kSegmentIdContexts][kMaxSegments + 1];
  alignas(kMaxAlignment) uint16_t
      use_predicted_segment_id_cdf[kUsePredictedSegmentIdContexts]
                                  [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t skip_cdf[kSkipContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      skip_mode_cdf[kSkipModeContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t delta_q_cdf[kDeltaSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t delta_lf_cdf[kDeltaSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      delta_lf_multi_cdf[kFrameLfCount][kDeltaSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t intra_block_copy_cdf[kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      intra_frame_y_mode_cdf[kIntraModeContexts][kIntraModeContexts]
                            [kIntraPredictionModesY + 1];
  alignas(kMaxAlignment) uint16_t
      y_mode_cdf[kYModeContexts][kIntraPredictionModesY + 1];
  alignas(kMaxAlignment) uint16_t
      angle_delta_cdf[kDirectionalIntraModes][kAngleDeltaSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      uv_mode_cdf[kBooleanSymbolCount][kIntraPredictionModesY]
                 [kIntraPredictionModesUV + 1];
  alignas(kMaxAlignment) uint16_t
      cfl_alpha_signs_cdf[kCflAlphaSignsSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      cfl_alpha_cdf[kCflAlphaContexts][kCflAlphaSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      use_filter_intra_cdf[kMaxBlockSizes][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      filter_intra_mode_cdf[kNumFilterIntraPredictors + 1];
  alignas(kMaxAlignment) uint16_t
      tx_depth_cdf[4][kTxDepthContexts][kMaxTxDepthSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      tx_split_cdf[kTxSplitContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      all_zero_cdf[kNumSquareTransformSizes][kAllZeroContexts]
                  [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      inter_tx_type_cdf[3][kNumExtendedTransformSizes][kNumTransformTypes + 1];
  alignas(kMaxAlignment) uint16_t
      intra_tx_type_cdf[2][kNumExtendedTransformSizes][kIntraPredictionModesY]
                       [kNumTransformTypes + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_16_cdf[kNumPlaneTypes][kEobPtContexts][kEobPt16SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_32_cdf[kNumPlaneTypes][kEobPtContexts][kEobPt32SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_64_cdf[kNumPlaneTypes][kEobPtContexts][kEobPt64SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_128_cdf[kNumPlaneTypes][kEobPtContexts][kEobPt128SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_256_cdf[kNumPlaneTypes][kEobPtContexts][kEobPt256SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_512_cdf[kNumPlaneTypes][kEobPt512SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_pt_1024_cdf[kNumPlaneTypes][kEobPt1024SymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      eob_extra_cdf[kNumSquareTransformSizes][kNumPlaneTypes][kEobExtraContexts]
                   [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      coeff_base_eob_cdf[kNumSquareTransformSizes][kNumPlaneTypes]
                        [kCoeffBaseEobContexts][kCoeffBaseEobSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      coeff_base_cdf[kNumSquareTransformSizes][kNumPlaneTypes]
                    [kCoeffBaseContexts][kCoeffBaseSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      coeff_base_range_cdf[kNumSquareTransformSizes][kNumPlaneTypes]
                          [kCoeffBaseRangeContexts]
                          [kCoeffBaseRangeSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      dc_sign_cdf[kNumPlaneTypes][kDcSignContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      restoration_type_cdf[kRestorationTypeSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t use_wiener_cdf[kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t use_sgrproj_cdf[kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      has_palette_y_cdf[kPaletteBlockSizeContexts][kPaletteYModeContexts]
                       [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      palette_y_size_cdf[kPaletteBlockSizeContexts]
                        [kPaletteSizeSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      has_palette_uv_cdf[kPaletteUVModeContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      palette_uv_size_cdf[kPaletteBlockSizeContexts]
                         [kPaletteSizeSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      palette_color_index_cdf[kNumPlaneTypes][kPaletteSizeSymbolCount]
                             [kPaletteColorIndexContexts]
                             [kPaletteColorIndexSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      is_inter_cdf[kIsInterContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      use_compound_reference_cdf[kUseCompoundReferenceContexts]
                                [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      compound_reference_type_cdf[kCompoundReferenceTypeContexts]
                                 [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      compound_reference_cdf[kNumCompoundReferenceTypes][kReferenceContexts][3]
                            [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      compound_backward_reference_cdf[kReferenceContexts][2]
                                     [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      single_reference_cdf[kReferenceContexts][6][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      compound_prediction_mode_cdf[kCompoundPredictionModeContexts]
                                  [kNumCompoundInterPredictionModes + 1];
  alignas(kMaxAlignment) uint16_t
      new_mv_cdf[kNewMvContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      zero_mv_cdf[kZeroMvContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      reference_mv_cdf[kReferenceMvContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      ref_mv_index_cdf[kRefMvIndexContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      is_inter_intra_cdf[kInterIntraContexts][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      inter_intra_mode_cdf[kInterIntraContexts][kNumInterIntraModes + 1];
  alignas(kMaxAlignment) uint16_t
      is_wedge_inter_intra_cdf[kMaxBlockSizes][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      wedge_index_cdf[kMaxBlockSizes][kWedgeIndexSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      use_obmc_cdf[kMaxBlockSizes][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      motion_mode_cdf[kMaxBlockSizes][kNumMotionModes + 1];
  alignas(kMaxAlignment) uint16_t
      is_explicit_compound_type_cdf[kIsExplicitCompoundTypeContexts]
                                   [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      is_compound_type_average_cdf[kIsCompoundTypeAverageContexts]
                                  [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      compound_type_cdf[kMaxBlockSizes]
                       [kNumExplicitCompoundPredictionTypes + 1];
  alignas(kMaxAlignment) uint16_t
      interpolation_filter_cdf[kInterpolationFilterContexts]
                              [kNumExplicitInterpolationFilters + 1];
  alignas(kMaxAlignment) uint16_t
      mv_joint_cdf[kMvContexts][kNumMvJointTypes + 1];
  alignas(kMaxAlignment) uint16_t
      mv_sign_cdf[kMvContexts][kNumMvComponents][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      mv_class_cdf[kMvContexts][kNumMvComponents][kMvClassSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      mv_class0_bit_cdf[kMvContexts][kNumMvComponents][kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      mv_class0_fraction_cdf[kMvContexts][kNumMvComponents][kBooleanSymbolCount]
                            [kMvFractionSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      mv_class0_high_precision_cdf[kMvContexts][kNumMvComponents]
                                  [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t
      mv_bit_cdf[kMvContexts][kNumMvComponents][kMvBitSymbolCount]
                [kBooleanFieldCdfSize];
  alignas(kMaxAlignment) uint16_t mv_fraction_cdf[kMvContexts][kNumMvComponents]
                                                 [kMvFractionSymbolCount + 1];
  alignas(kMaxAlignment) uint16_t
      mv_high_precision_cdf[kMvContexts][kNumMvComponents]
                           [kBooleanFieldCdfSize];
};

}  // namespace libgav1
#endif  // LIBGAV1_SRC_SYMBOL_DECODER_CONTEXT_H_

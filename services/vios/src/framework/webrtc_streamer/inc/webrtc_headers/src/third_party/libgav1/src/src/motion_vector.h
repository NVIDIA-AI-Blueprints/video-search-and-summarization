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

#ifndef LIBGAV1_SRC_MOTION_VECTOR_H_
#define LIBGAV1_SRC_MOTION_VECTOR_H_

#include <algorithm>
#include <array>
#include <cstdint>

#include "src/buffer_pool.h"
#include "src/obu_parser.h"
#include "src/tile.h"
#include "src/utils/array_2d.h"
#include "src/utils/constants.h"
#include "src/utils/types.h"

namespace libgav1 {

constexpr bool IsGlobalMvBlock(const BlockParameters& bp,
                               GlobalMotionTransformationType type) {
  return (bp.y_mode == kPredictionModeGlobalMv ||
          bp.y_mode == kPredictionModeGlobalGlobalMv) &&
         !IsBlockDimension4(bp.size) &&
         type > kGlobalMotionTransformationTypeTranslation;
}

// The |contexts| output parameter may be null. If the caller does not need
// the |contexts| output, pass nullptr as the argument.
void FindMvStack(const Tile::Block& block, bool is_compound,
                 MvContexts* contexts);  // 7.10.2

void FindWarpSamples(const Tile::Block& block, int* num_warp_samples,
                     int* num_samples_scanned,
                     int candidates[kMaxLeastSquaresSamples][4]);  // 7.10.4.

// Section 7.9.1 in the spec. But this is done per tile instead of for the whole
// frame.
void SetupMotionField(
    const ObuFrameHeader& frame_header, const RefCountedBuffer& current_frame,
    const std::array<RefCountedBufferPtr, kNumReferenceFrameTypes>&
        reference_frames,
    int row4x4_start, int row4x4_end, int column4x4_start, int column4x4_end,
    TemporalMotionField* motion_field);

}  // namespace libgav1

#endif  // LIBGAV1_SRC_MOTION_VECTOR_H_

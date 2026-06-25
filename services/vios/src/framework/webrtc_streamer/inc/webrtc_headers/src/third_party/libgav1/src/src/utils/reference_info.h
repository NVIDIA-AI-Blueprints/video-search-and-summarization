/*
 * Copyright 2020 The libgav1 Authors
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

#ifndef LIBGAV1_SRC_UTILS_REFERENCE_INFO_H_
#define LIBGAV1_SRC_UTILS_REFERENCE_INFO_H_

#include <array>
#include <cstdint>

#include "src/utils/array_2d.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/types.h"

namespace libgav1 {

// This struct collects some members related to reference frames in one place to
// make it easier to pass them as parameters to some dsp functions.
struct ReferenceInfo {
  // Initialize |motion_field_reference_frame| so that
  // Tile::StoreMotionFieldMvsIntoCurrentFrame() can skip some updates when
  // the updates are the same as the initialized value.
  // Set to kReferenceFrameIntra instead of kReferenceFrameNone to simplify
  // branch conditions in motion field projection.
  // The following memory initialization of contiguous memory is very fast. It
  // is not recommended to make the initialization multi-threaded, unless the
  // memory which needs to be initialized in each thread is still contiguous.
  LIBGAV1_MUST_USE_RESULT bool Reset(int rows, int columns) {
    return motion_field_reference_frame.Reset(rows, columns,
                                              /*zero_initialize=*/true) &&
           motion_field_mv.Reset(
               rows, columns,
#if LIBGAV1_MSAN
               // It is set in Tile::StoreMotionFieldMvsIntoCurrentFrame() only
               // for qualified blocks. In MotionFieldProjectionKernel() dsp
               // optimizations, it is read no matter it was set or not.
               /*zero_initialize=*/true
#else
               /*zero_initialize=*/false
#endif
           );
  }

  // All members are used by inter frames only.
  // For intra frames, they are not initialized.

  std::array<uint8_t, kNumReferenceFrameTypes> order_hint;

  // An example when |relative_distance_from| does not equal
  // -|relative_distance_to|:
  // |relative_distance_from| = GetRelativeDistance(7, 71, 25) = -64
  // -|relative_distance_to| = -GetRelativeDistance(71, 7, 25) = 64
  // This is why we need both |relative_distance_from| and
  // |relative_distance_to|.
  // |relative_distance_from|: Relative distances from reference frames to this
  // frame.
  std::array<int8_t, kNumReferenceFrameTypes> relative_distance_from;
  // |relative_distance_to|: Relative distances to reference frames.
  std::array<int8_t, kNumReferenceFrameTypes> relative_distance_to;

  // Skip motion field projection of specific types of frames if their
  // |relative_distance_to| is negative or too large.
  std::array<bool, kNumReferenceFrameTypes> skip_references;
  // Lookup table to get motion field projection division multiplier of specific
  // types of frames. Derived from kProjectionMvDivisionLookup.
  std::array<int16_t, kNumReferenceFrameTypes> projection_divisions;

  // The current frame's |motion_field_reference_frame| and |motion_field_mv_|
  // are guaranteed to be allocated only when refresh_frame_flags is not 0.
  // Array of size (rows4x4 / 2) x (columns4x4 / 2). Entry at i, j corresponds
  // to MfRefFrames[i * 2 + 1][j * 2 + 1] in the spec.
  Array2D<ReferenceFrameType> motion_field_reference_frame;
  // Array of size (rows4x4 / 2) x (columns4x4 / 2). Entry at i, j corresponds
  // to MfMvs[i * 2 + 1][j * 2 + 1] in the spec.
  Array2D<MotionVector> motion_field_mv;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_REFERENCE_INFO_H_

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

#ifndef LIBGAV1_SRC_DSP_WARP_H_
#define LIBGAV1_SRC_DSP_WARP_H_

// Pull in LIBGAV1_DspXXX defines representing the implementation status
// of each function. The resulting value of each can be used by each module to
// determine whether an implementation is needed at compile time.
// IWYU pragma: begin_exports

// ARM:
#include "src/dsp/arm/warp_neon.h"

// x86:
// Note includes should be sorted in logical order avx2/avx/sse4, etc.
// The order of includes is important as each tests for a superior version
// before setting the base.
// clang-format off
#include "src/dsp/x86/warp_sse4.h"
// clang-format on

// IWYU pragma: end_exports

namespace libgav1 {
namespace dsp {

// Section 7.11.3.5.
struct WarpFilterParams {
  int64_t x4;
  int64_t y4;
  int ix4;
  int iy4;
};

// Initializes Dsp::warp. This function is not thread-safe.
void WarpInit_C();

// Section 7.11.3.5.
inline WarpFilterParams GetWarpFilterParams(int src_x, int src_y,
                                            int subsampling_x,
                                            int subsampling_y,
                                            const int* warp_params) {
  WarpFilterParams filter_params;
  // warp_params[2]/[5] require 17 bits (the others 14). With large resolutions
  // the result of the multiplication will require 33.
  const int64_t dst_x = static_cast<int64_t>(src_x) * warp_params[2] +
                        src_y * warp_params[3] + warp_params[0];
  const int64_t dst_y = src_x * warp_params[4] +
                        static_cast<int64_t>(src_y) * warp_params[5] +
                        warp_params[1];
  filter_params.x4 = dst_x >> subsampling_x;
  filter_params.y4 = dst_y >> subsampling_y;
  filter_params.ix4 =
      static_cast<int>(filter_params.x4 >> kWarpedModelPrecisionBits);
  filter_params.iy4 =
      static_cast<int>(filter_params.y4 >> kWarpedModelPrecisionBits);
  return filter_params;
}

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_SRC_DSP_WARP_H_

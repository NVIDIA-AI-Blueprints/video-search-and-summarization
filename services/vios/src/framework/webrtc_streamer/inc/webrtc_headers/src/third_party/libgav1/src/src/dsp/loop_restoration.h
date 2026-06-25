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

#ifndef LIBGAV1_SRC_DSP_LOOP_RESTORATION_H_
#define LIBGAV1_SRC_DSP_LOOP_RESTORATION_H_

// Pull in LIBGAV1_DspXXX defines representing the implementation status
// of each function. The resulting value of each can be used by each module to
// determine whether an implementation is needed at compile time.
// IWYU pragma: begin_exports

// ARM:
#include "src/dsp/arm/loop_restoration_neon.h"

// x86:
// Note includes should be sorted in logical order avx2/avx/sse4, etc.
// The order of includes is important as each tests for a superior version
// before setting the base.
// clang-format off
#include "src/dsp/x86/loop_restoration_avx2.h"
#include "src/dsp/x86/loop_restoration_sse4.h"
// clang-format on

// IWYU pragma: end_exports

namespace libgav1 {
namespace dsp {

extern const uint8_t kSgrMaLookup[256];

// Initializes Dsp::loop_restorations. This function is not thread-safe.
void LoopRestorationInit_C();

template <typename T>
void Circulate3PointersBy1(T* p[3]) {
  T* const p0 = p[0];
  p[0] = p[1];
  p[1] = p[2];
  p[2] = p0;
}

template <typename T>
void Circulate4PointersBy2(T* p[4]) {
  std::swap(p[0], p[2]);
  std::swap(p[1], p[3]);
}

template <typename T>
void Circulate5PointersBy2(T* p[5]) {
  T* const p0 = p[0];
  T* const p1 = p[1];
  p[0] = p[2];
  p[1] = p[3];
  p[2] = p[4];
  p[3] = p0;
  p[4] = p1;
}

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_SRC_DSP_LOOP_RESTORATION_H_

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

#ifndef LIBGAV1_SRC_DSP_COMMON_H_
#define LIBGAV1_SRC_DSP_COMMON_H_

#include <cstdint>

#include "src/dsp/constants.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"

namespace libgav1 {

enum { kSgrStride = kRestorationUnitWidth + 32 };  // anonymous enum

// Self guided projection filter.
struct SgrProjInfo {
  int index;
  int multiplier[2];
};

struct WienerInfo {
  static const int kVertical = 0;
  static const int kHorizontal = 1;
  int16_t number_leading_zero_coefficients[2];
  alignas(kMaxAlignment) int16_t filter[2][(kWienerFilterTaps + 1) / 2];
};

struct RestorationUnitInfo : public MaxAlignedAllocable {
  LoopRestorationType type;
  SgrProjInfo sgr_proj_info;
  WienerInfo wiener_info;
};

struct SgrBuffer {
  alignas(kMaxAlignment) uint16_t sum3[4 * kSgrStride];
  alignas(kMaxAlignment) uint16_t sum5[5 * kSgrStride];
  alignas(kMaxAlignment) uint32_t square_sum3[4 * kSgrStride];
  alignas(kMaxAlignment) uint32_t square_sum5[5 * kSgrStride];
  alignas(kMaxAlignment) uint16_t ma343[4 * kRestorationUnitWidth];
  alignas(kMaxAlignment) uint16_t ma444[3 * kRestorationUnitWidth];
  alignas(kMaxAlignment) uint16_t ma565[2 * kRestorationUnitWidth];
  alignas(kMaxAlignment) uint32_t b343[4 * kRestorationUnitWidth];
  alignas(kMaxAlignment) uint32_t b444[3 * kRestorationUnitWidth];
  alignas(kMaxAlignment) uint32_t b565[2 * kRestorationUnitWidth];
  // The following 2 buffers are only used by the C functions. Since SgrBuffer
  // is smaller than |wiener_buffer| in RestorationBuffer which is an union,
  // it's OK to always keep the following 2 buffers.
  alignas(kMaxAlignment) uint8_t ma[kSgrStride];  // [0, 255]
  // b is less than 2^16 for 8-bit. However, making it a template slows down the
  // C function by 5%. So b is fixed to 32-bit.
  alignas(kMaxAlignment) uint32_t b[kSgrStride];
};

union RestorationBuffer {
  // For self-guided filter.
  SgrBuffer sgr_buffer;
  // For wiener filter.
  // The array |intermediate| in Section 7.17.4, the intermediate results
  // between the horizontal and vertical filters.
  alignas(kMaxAlignment) int16_t
      wiener_buffer[(kRestorationUnitHeight + kWienerFilterTaps - 1) *
                    kRestorationUnitWidth];
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_DSP_COMMON_H_

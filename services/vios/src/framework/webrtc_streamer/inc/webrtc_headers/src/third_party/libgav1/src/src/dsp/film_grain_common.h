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

#ifndef LIBGAV1_SRC_DSP_FILM_GRAIN_COMMON_H_
#define LIBGAV1_SRC_DSP_FILM_GRAIN_COMMON_H_

#include <cstdint>

namespace libgav1 {

template <int bitdepth>
int GetGrainMax() {
  return (1 << (bitdepth - 1)) - 1;
}

template <int bitdepth>
int GetGrainMin() {
  return -(1 << (bitdepth - 1));
}

inline int GetFilmGrainRandomNumber(int bits, uint16_t* seed) {
  uint16_t s = *seed;
  uint16_t bit = (s ^ (s >> 1) ^ (s >> 3) ^ (s >> 12)) & 1;
  s = (s >> 1) | (bit << 15);
  *seed = s;
  return s >> (16 - bits);
}

enum {
  kAutoRegressionBorder = 3,
  // The width of the luma noise array.
  kLumaWidth = 82,
  // The height of the luma noise array.
  kLumaHeight = 73,
  // The two possible widths of the chroma noise array.
  kMinChromaWidth = 44,
  kMaxChromaWidth = 82,
  // The two possible heights of the chroma noise array.
  kMinChromaHeight = 38,
  kMaxChromaHeight = 73,
  // The standard scaling lookup table maps bytes to bytes, so only uses 256
  // elements, plus one for overflow in 12bpp lookups. The size is scaled up for
  // 10bpp.
  kScalingLookupTableSize = 257,
  // Padding is added to the scaling lookup table to permit overwrites by
  // InitializeScalingLookupTable_NEON.
  kScalingLookupTablePadding = 6,
  // Padding is added to each row of the noise image to permit overreads by
  // BlendNoiseWithImageLuma_NEON and overwrites by WriteOverlapLine8bpp_NEON.
  kNoiseImagePadding = 15,
  // Padding is added to the end of the |noise_stripes_| buffer to permit
  // overreads by WriteOverlapLine8bpp_NEON.
  kNoiseStripePadding = 7,
};  // anonymous enum

}  // namespace libgav1

#endif  // LIBGAV1_SRC_DSP_FILM_GRAIN_COMMON_H_

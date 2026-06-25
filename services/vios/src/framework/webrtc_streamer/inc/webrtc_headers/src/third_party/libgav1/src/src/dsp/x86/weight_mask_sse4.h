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

#ifndef LIBGAV1_SRC_DSP_ARM_WEIGHT_MASK_SSE4_H_
#define LIBGAV1_SRC_DSP_ARM_WEIGHT_MASK_SSE4_H_

#include "src/dsp/dsp.h"
#include "src/utils/cpu.h"

namespace libgav1 {
namespace dsp {

// Initializes Dsp::weight_mask. This function is not thread-safe.
void WeightMaskInit_SSE4_1();

}  // namespace dsp
}  // namespace libgav1

#if LIBGAV1_TARGETING_SSE4_1

#ifndef LIBGAV1_Dsp8bpp_WeightMask_8x8
#define LIBGAV1_Dsp8bpp_WeightMask_8x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_8x16
#define LIBGAV1_Dsp8bpp_WeightMask_8x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_8x32
#define LIBGAV1_Dsp8bpp_WeightMask_8x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_16x8
#define LIBGAV1_Dsp8bpp_WeightMask_16x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_16x16
#define LIBGAV1_Dsp8bpp_WeightMask_16x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_16x32
#define LIBGAV1_Dsp8bpp_WeightMask_16x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_16x64
#define LIBGAV1_Dsp8bpp_WeightMask_16x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_32x8
#define LIBGAV1_Dsp8bpp_WeightMask_32x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_32x16
#define LIBGAV1_Dsp8bpp_WeightMask_32x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_32x32
#define LIBGAV1_Dsp8bpp_WeightMask_32x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_32x64
#define LIBGAV1_Dsp8bpp_WeightMask_32x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_64x16
#define LIBGAV1_Dsp8bpp_WeightMask_64x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_64x32
#define LIBGAV1_Dsp8bpp_WeightMask_64x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_64x64
#define LIBGAV1_Dsp8bpp_WeightMask_64x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_64x128
#define LIBGAV1_Dsp8bpp_WeightMask_64x128 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_128x64
#define LIBGAV1_Dsp8bpp_WeightMask_128x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp8bpp_WeightMask_128x128
#define LIBGAV1_Dsp8bpp_WeightMask_128x128 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_8x8
#define LIBGAV1_Dsp10bpp_WeightMask_8x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_8x16
#define LIBGAV1_Dsp10bpp_WeightMask_8x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_8x32
#define LIBGAV1_Dsp10bpp_WeightMask_8x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_16x8
#define LIBGAV1_Dsp10bpp_WeightMask_16x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_16x16
#define LIBGAV1_Dsp10bpp_WeightMask_16x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_16x32
#define LIBGAV1_Dsp10bpp_WeightMask_16x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_16x64
#define LIBGAV1_Dsp10bpp_WeightMask_16x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_32x8
#define LIBGAV1_Dsp10bpp_WeightMask_32x8 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_32x16
#define LIBGAV1_Dsp10bpp_WeightMask_32x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_32x32
#define LIBGAV1_Dsp10bpp_WeightMask_32x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_32x64
#define LIBGAV1_Dsp10bpp_WeightMask_32x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_64x16
#define LIBGAV1_Dsp10bpp_WeightMask_64x16 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_64x32
#define LIBGAV1_Dsp10bpp_WeightMask_64x32 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_64x64
#define LIBGAV1_Dsp10bpp_WeightMask_64x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_64x128
#define LIBGAV1_Dsp10bpp_WeightMask_64x128 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_128x64
#define LIBGAV1_Dsp10bpp_WeightMask_128x64 LIBGAV1_CPU_SSE4_1
#endif

#ifndef LIBGAV1_Dsp10bpp_WeightMask_128x128
#define LIBGAV1_Dsp10bpp_WeightMask_128x128 LIBGAV1_CPU_SSE4_1
#endif
#endif  // LIBGAV1_TARGETING_SSE4_1

#endif  // LIBGAV1_SRC_DSP_ARM_WEIGHT_MASK_SSE4_H_

/*
 * Copyright 2021 The libgav1 Authors
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

#ifndef LIBGAV1_SRC_DSP_ARM_INTRAPRED_CFL_NEON_H_
#define LIBGAV1_SRC_DSP_ARM_INTRAPRED_CFL_NEON_H_

#include "src/dsp/dsp.h"
#include "src/utils/cpu.h"

namespace libgav1 {
namespace dsp {

// Initializes Dsp::cfl_intra_predictors and Dsp::cfl_subsamplers, see the
// defines below for specifics. These functions are not thread-safe.
void IntraPredCflInit_NEON();

}  // namespace dsp
}  // namespace libgav1

#if LIBGAV1_ENABLE_NEON
// 4x4
#define LIBGAV1_Dsp8bpp_TransformSize4x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 4x8
#define LIBGAV1_Dsp8bpp_TransformSize4x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 4x16
#define LIBGAV1_Dsp8bpp_TransformSize4x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize4x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x4
#define LIBGAV1_Dsp8bpp_TransformSize8x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x8
#define LIBGAV1_Dsp8bpp_TransformSize8x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x16
#define LIBGAV1_Dsp8bpp_TransformSize8x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x32
#define LIBGAV1_Dsp8bpp_TransformSize8x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize8x32_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x4
#define LIBGAV1_Dsp8bpp_TransformSize16x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x8
#define LIBGAV1_Dsp8bpp_TransformSize16x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x16
#define LIBGAV1_Dsp8bpp_TransformSize16x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x32
#define LIBGAV1_Dsp8bpp_TransformSize16x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize16x32_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x8
#define LIBGAV1_Dsp8bpp_TransformSize32x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x16
#define LIBGAV1_Dsp8bpp_TransformSize32x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x32
#define LIBGAV1_Dsp8bpp_TransformSize32x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_TransformSize32x32_CflSubsampler444 LIBGAV1_CPU_NEON

// -----------------------------------------------------------------------------
// 10bpp

// 4x4
#define LIBGAV1_Dsp10bpp_TransformSize4x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 4x8
#define LIBGAV1_Dsp10bpp_TransformSize4x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 4x16
#define LIBGAV1_Dsp10bpp_TransformSize4x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize4x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x4
#define LIBGAV1_Dsp10bpp_TransformSize8x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x8
#define LIBGAV1_Dsp10bpp_TransformSize8x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x16
#define LIBGAV1_Dsp10bpp_TransformSize8x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 8x32
#define LIBGAV1_Dsp10bpp_TransformSize8x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize8x32_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x4
#define LIBGAV1_Dsp10bpp_TransformSize16x4_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x4_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x4_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x8
#define LIBGAV1_Dsp10bpp_TransformSize16x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x16
#define LIBGAV1_Dsp10bpp_TransformSize16x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 16x32
#define LIBGAV1_Dsp10bpp_TransformSize16x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize16x32_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x8
#define LIBGAV1_Dsp10bpp_TransformSize32x8_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x8_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x8_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x16
#define LIBGAV1_Dsp10bpp_TransformSize32x16_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x16_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x16_CflSubsampler444 LIBGAV1_CPU_NEON

// 32x32
#define LIBGAV1_Dsp10bpp_TransformSize32x32_CflIntraPredictor LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x32_CflSubsampler420 LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_TransformSize32x32_CflSubsampler444 LIBGAV1_CPU_NEON

#endif  // LIBGAV1_ENABLE_NEON

#endif  // LIBGAV1_SRC_DSP_ARM_INTRAPRED_CFL_NEON_H_

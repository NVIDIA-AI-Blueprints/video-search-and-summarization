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

#ifndef LIBGAV1_SRC_DSP_ARM_CONVOLVE_NEON_H_
#define LIBGAV1_SRC_DSP_ARM_CONVOLVE_NEON_H_

#include "src/dsp/dsp.h"
#include "src/utils/cpu.h"

namespace libgav1 {
namespace dsp {

// Initializes Dsp::convolve. This function is not thread-safe.
void ConvolveInit_NEON();
void ConvolveInit10bpp_NEON();

}  // namespace dsp
}  // namespace libgav1

#if LIBGAV1_ENABLE_NEON
#define LIBGAV1_Dsp8bpp_ConvolveHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_Convolve2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp8bpp_ConvolveCompoundCopy LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveCompoundHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveCompoundVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveCompound2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp8bpp_ConvolveIntraBlockCopyHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveIntraBlockCopyVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveIntraBlockCopy2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp8bpp_ConvolveScale2D LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp8bpp_ConvolveCompoundScale2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp10bpp_ConvolveHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_Convolve2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp10bpp_ConvolveCompoundCopy LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveCompoundHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveCompoundVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveCompound2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp10bpp_ConvolveIntraBlockCopyHorizontal LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveIntraBlockCopyVertical LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveIntraBlockCopy2D LIBGAV1_CPU_NEON

#define LIBGAV1_Dsp10bpp_ConvolveScale2D LIBGAV1_CPU_NEON
#define LIBGAV1_Dsp10bpp_ConvolveCompoundScale2D LIBGAV1_CPU_NEON
#endif  // LIBGAV1_ENABLE_NEON

#endif  // LIBGAV1_SRC_DSP_ARM_CONVOLVE_NEON_H_

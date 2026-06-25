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

#ifndef LIBGAV1_SRC_DSP_ARM_INTRAPRED_DIRECTIONAL_NEON_H_
#define LIBGAV1_SRC_DSP_ARM_INTRAPRED_DIRECTIONAL_NEON_H_

#include "src/dsp/dsp.h"
#include "src/utils/cpu.h"

namespace libgav1 {
namespace dsp {

// Initializes Dsp::directional_intra_predictor_zone*, see the defines below for
// specifics. These functions are not thread-safe.
void IntraPredDirectionalInit_NEON();

}  // namespace dsp
}  // namespace libgav1

#if LIBGAV1_ENABLE_NEON
#ifndef LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone1
#define LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone1 LIBGAV1_CPU_NEON
#endif

#ifndef LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone2
#define LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone2 LIBGAV1_CPU_NEON
#endif

#ifndef LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone3
#define LIBGAV1_Dsp8bpp_DirectionalIntraPredictorZone3 LIBGAV1_CPU_NEON
#endif

#ifndef LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone1
#define LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone1 LIBGAV1_CPU_NEON
#endif

#ifndef LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone2
#define LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone2 LIBGAV1_CPU_NEON
#endif

#ifndef LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone3
#define LIBGAV1_Dsp10bpp_DirectionalIntraPredictorZone3 LIBGAV1_CPU_NEON
#endif

#endif  // LIBGAV1_ENABLE_NEON

#endif  // LIBGAV1_SRC_DSP_ARM_INTRAPRED_DIRECTIONAL_NEON_H_

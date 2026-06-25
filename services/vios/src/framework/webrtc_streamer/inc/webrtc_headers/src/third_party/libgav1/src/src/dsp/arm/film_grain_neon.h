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

#ifndef LIBGAV1_SRC_DSP_ARM_FILM_GRAIN_NEON_H_
#define LIBGAV1_SRC_DSP_ARM_FILM_GRAIN_NEON_H_

#include "src/dsp/dsp.h"
#include "src/utils/cpu.h"

namespace libgav1 {
namespace dsp {

// Initialize members of Dsp::film_grain. This function is not thread-safe.
void FilmGrainInit_NEON();

}  // namespace dsp
}  // namespace libgav1

#if LIBGAV1_ENABLE_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainAutoregressionLuma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainAutoregressionLuma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainAutoregressionChroma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainAutoregressionChroma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainConstructNoiseImageOverlap LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainConstructNoiseImageOverlap LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainInitializeScalingLutFunc LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainInitializeScalingLutFunc LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainBlendNoiseLuma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainBlendNoiseLuma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainBlendNoiseChroma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainBlendNoiseChroma LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp8bpp_FilmGrainBlendNoiseChromaWithCfl LIBGAV1_DSP_NEON
#define LIBGAV1_Dsp10bpp_FilmGrainBlendNoiseChromaWithCfl LIBGAV1_DSP_NEON
#endif  // LIBGAV1_ENABLE_NEON

#endif  // LIBGAV1_SRC_DSP_ARM_FILM_GRAIN_NEON_H_

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

#ifndef LIBGAV1_SRC_DSP_X86_COMMON_AVX2_H_
#define LIBGAV1_SRC_DSP_X86_COMMON_AVX2_H_

#include "src/utils/compiler_attributes.h"
#include "src/utils/cpu.h"

#if LIBGAV1_TARGETING_AVX2

#include <immintrin.h>

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace libgav1 {
namespace dsp {
namespace avx2 {

#include "src/dsp/x86/common_avx2.inc"
#include "src/dsp/x86/common_sse4.inc"

}  // namespace avx2

// NOLINTBEGIN(misc-unused-using-decls)
// These function aliases shall not be visible to external code. They are
// restricted to x86/*_avx2.cc files only. This scheme exists to distinguish two
// possible implementations of common functions, which may differ based on
// whether the compiler is permitted to use avx2 instructions.

// common_sse4.inc
using avx2::Load2;
using avx2::Load2x2;
using avx2::Load4;
using avx2::Load4x2;
using avx2::LoadAligned16;
using avx2::LoadAligned16Msan;
using avx2::LoadHi8;
using avx2::LoadHi8Msan;
using avx2::LoadLo8;
using avx2::LoadLo8Msan;
using avx2::LoadUnaligned16;
using avx2::LoadUnaligned16Msan;
using avx2::MaskHighNBytes;
using avx2::RightShiftWithRounding_S16;
using avx2::RightShiftWithRounding_S32;
using avx2::RightShiftWithRounding_U16;
using avx2::RightShiftWithRounding_U32;
using avx2::Store2;
using avx2::Store4;
using avx2::StoreAligned16;
using avx2::StoreHi8;
using avx2::StoreLo8;
using avx2::StoreUnaligned16;

// common_avx2.inc
using avx2::LoadAligned32;
using avx2::LoadAligned32Msan;
using avx2::LoadAligned64;
using avx2::LoadAligned64Msan;
using avx2::LoadUnaligned32;
using avx2::LoadUnaligned32Msan;
using avx2::SetrM128i;
using avx2::StoreAligned32;
using avx2::StoreAligned64;
using avx2::StoreUnaligned32;
// NOLINTEND

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_TARGETING_AVX2
#endif  // LIBGAV1_SRC_DSP_X86_COMMON_AVX2_H_

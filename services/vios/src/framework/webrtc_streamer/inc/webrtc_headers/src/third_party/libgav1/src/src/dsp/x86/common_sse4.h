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

#ifndef LIBGAV1_SRC_DSP_X86_COMMON_SSE4_H_
#define LIBGAV1_SRC_DSP_X86_COMMON_SSE4_H_

#include "src/utils/compiler_attributes.h"
#include "src/utils/cpu.h"

#if LIBGAV1_TARGETING_SSE4_1

#include <emmintrin.h>
#include <smmintrin.h>

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>

#if 0
#include <cinttypes>
#include <cstdio>

// Quite useful macro for debugging. Left here for convenience.
inline void PrintReg(const __m128i r, const char* const name, int size) {
  int n;
  union {
    __m128i r;
    uint8_t i8[16];
    uint16_t i16[8];
    uint32_t i32[4];
    uint64_t i64[2];
  } tmp;
  tmp.r = r;
  fprintf(stderr, "%s\t: ", name);
  if (size == 8) {
    for (n = 0; n < 16; ++n) fprintf(stderr, "%.2x ", tmp.i8[n]);
  } else if (size == 16) {
    for (n = 0; n < 8; ++n) fprintf(stderr, "%.4x ", tmp.i16[n]);
  } else if (size == 32) {
    for (n = 0; n < 4; ++n) fprintf(stderr, "%.8x ", tmp.i32[n]);
  } else {
    for (n = 0; n < 2; ++n)
      fprintf(stderr, "%.16" PRIx64 " ", static_cast<uint64_t>(tmp.i64[n]));
  }
  fprintf(stderr, "\n");
}

inline void PrintReg(const int r, const char* const name) {
  fprintf(stderr, "%s: %d\n", name, r);
}

inline void PrintRegX(const int r, const char* const name) {
  fprintf(stderr, "%s: %.8x\n", name, r);
}

#define PR(var, N) PrintReg(var, #var, N)
#define PD(var) PrintReg(var, #var);
#define PX(var) PrintRegX(var, #var);

#if LIBGAV1_MSAN
#include <sanitizer/msan_interface.h>

inline void PrintShadow(const void* r, const char* const name,
                        const size_t size) {
  fprintf(stderr, "Shadow for %s:\n", name);
  __msan_print_shadow(r, size);
}
#define PS(var, N) PrintShadow(var, #var, N)

#endif  // LIBGAV1_MSAN

#endif  // 0

namespace libgav1 {
namespace dsp {
namespace sse4 {

#include "src/dsp/x86/common_sse4.inc"

}  // namespace sse4

// NOLINTBEGIN(misc-unused-using-decls)
// These function aliases shall not be visible to external code. They are
// restricted to x86/*_sse4.cc files only. This scheme exists to distinguish two
// possible implementations of common functions, which may differ based on
// whether the compiler is permitted to use avx2 instructions.
using sse4::Load2;
using sse4::Load2x2;
using sse4::Load4;
using sse4::Load4x2;
using sse4::LoadAligned16;
using sse4::LoadAligned16Msan;
using sse4::LoadHi8;
using sse4::LoadHi8Msan;
using sse4::LoadLo8;
using sse4::LoadLo8Msan;
using sse4::LoadUnaligned16;
using sse4::LoadUnaligned16Msan;
using sse4::MaskHighNBytes;
using sse4::RightShiftWithRounding_S16;
using sse4::RightShiftWithRounding_S32;
using sse4::RightShiftWithRounding_U16;
using sse4::RightShiftWithRounding_U32;
using sse4::Store2;
using sse4::Store4;
using sse4::StoreAligned16;
using sse4::StoreHi8;
using sse4::StoreLo8;
using sse4::StoreUnaligned16;
// NOLINTEND

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_TARGETING_SSE4_1
#endif  // LIBGAV1_SRC_DSP_X86_COMMON_SSE4_H_

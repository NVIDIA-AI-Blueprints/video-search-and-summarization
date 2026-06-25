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

#ifndef LIBGAV1_SRC_UTILS_CPU_H_
#define LIBGAV1_SRC_UTILS_CPU_H_

#include <cstdint>

namespace libgav1 {

#if defined(__i386__) || defined(__x86_64__)
#define LIBGAV1_X86
#elif defined(_MSC_VER) && (defined(_M_IX86) || defined(_M_X64))
#define LIBGAV1_X86
#define LIBGAV1_X86_MSVC
#endif

#if defined(LIBGAV1_X86)

#if !defined(LIBGAV1_ENABLE_SSE4_1)
#define LIBGAV1_ENABLE_SSE4_1 1
#endif

#if LIBGAV1_ENABLE_SSE4_1
#if !defined(LIBGAV1_ENABLE_AVX2)
#define LIBGAV1_ENABLE_AVX2 1
#endif  // !defined(LIBGAV1_ENABLE_AVX2)
#else   // !LIBGAV1_ENABLE_SSE4_1
// Disable AVX2 when SSE4.1 is disabled as it may rely on shared components.
#undef LIBGAV1_ENABLE_AVX2
#define LIBGAV1_ENABLE_AVX2 0
#endif  // LIBGAV1_ENABLE_SSE4_1

#else  // !LIBGAV1_X86

#undef LIBGAV1_ENABLE_AVX2
#define LIBGAV1_ENABLE_AVX2 0
#undef LIBGAV1_ENABLE_SSE4_1
#define LIBGAV1_ENABLE_SSE4_1 0

#endif  // LIBGAV1_X86

// For x86 LIBGAV1_TARGETING_* indicate the source being built is targeting
// (at least) that instruction set. This prevents disabling other instruction
// sets if the current instruction set isn't a global target, e.g., building
// *_avx2.cc w/-mavx2, but the remaining files without the flag.
#if LIBGAV1_ENABLE_AVX2 && defined(__AVX2__)
#define LIBGAV1_TARGETING_AVX2 1
#else
#define LIBGAV1_TARGETING_AVX2 0
#endif

// Note: LIBGAV1_X86_MSVC isn't completely correct for Visual Studio, but there
// is no equivalent to __SSE4_1__. LIBGAV1_ENABLE_ALL_DSP_FUNCTIONS will be
// enabled in dsp.h to compensate for this.
#if LIBGAV1_ENABLE_SSE4_1 && (defined(__SSE4_1__) || defined(LIBGAV1_X86_MSVC))
#define LIBGAV1_TARGETING_SSE4_1 1
#else
#define LIBGAV1_TARGETING_SSE4_1 0
#endif

#undef LIBGAV1_X86

#if !defined(LIBGAV1_ENABLE_NEON)
// TODO(jzern): add support for _M_ARM64.
#if defined(__ARM_NEON__) || defined(__aarch64__) || \
    (defined(_MSC_VER) && defined(_M_ARM))
#define LIBGAV1_ENABLE_NEON 1
#else
#define LIBGAV1_ENABLE_NEON 0
#endif
#endif  // !defined(LIBGAV1_ENABLE_NEON)

enum CpuFeatures : uint8_t {
  kSSE2 = 1 << 0,
#define LIBGAV1_CPU_SSE2 (1 << 0)
  kSSSE3 = 1 << 1,
#define LIBGAV1_CPU_SSSE3 (1 << 1)
  kSSE4_1 = 1 << 2,
#define LIBGAV1_CPU_SSE4_1 (1 << 2)
  kAVX = 1 << 3,
#define LIBGAV1_CPU_AVX (1 << 3)
  kAVX2 = 1 << 4,
#define LIBGAV1_CPU_AVX2 (1 << 4)
  kNEON = 1 << 5,
#define LIBGAV1_CPU_NEON (1 << 5)
};

// Returns a bit-wise OR of CpuFeatures supported by this platform.
uint32_t GetCpuInfo();

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_CPU_H_

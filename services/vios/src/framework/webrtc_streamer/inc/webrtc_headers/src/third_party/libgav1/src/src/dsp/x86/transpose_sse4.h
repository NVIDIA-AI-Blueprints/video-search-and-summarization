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

#ifndef LIBGAV1_SRC_DSP_X86_TRANSPOSE_SSE4_H_
#define LIBGAV1_SRC_DSP_X86_TRANSPOSE_SSE4_H_

#include "src/utils/compiler_attributes.h"
#include "src/utils/cpu.h"

#if LIBGAV1_TARGETING_SSE4_1
#include <emmintrin.h>

namespace libgav1 {
namespace dsp {

LIBGAV1_ALWAYS_INLINE void Transpose2x16_U16(const __m128i* const in,
                                             __m128i* const out) {
  // Unpack 16 bit elements. Goes from:
  // in[0]:  00 01 10 11  20 21 30 31
  // in[1]:  40 41 50 51  60 61 70 71
  // in[2]:  80 81 90 91  a0 a1 b0 b1
  // in[3]:  c0 c1 d0 d1  e0 e1 f0 f1
  // to:
  // a0:     00 40 01 41  10 50 11 51
  // a1:     20 60 21 61  30 70 31 71
  // a2:     80 c0 81 c1  90 d0 91 d1
  // a3:     a0 e0 a1 e1  b0 f0 b1 f1
  const __m128i a0 = _mm_unpacklo_epi16(in[0], in[1]);
  const __m128i a1 = _mm_unpackhi_epi16(in[0], in[1]);
  const __m128i a2 = _mm_unpacklo_epi16(in[2], in[3]);
  const __m128i a3 = _mm_unpackhi_epi16(in[2], in[3]);
  // b0:     00 20 40 60  01 21 41 61
  // b1:     10 30 50 70  11 31 51 71
  // b2:     80 a0 c0 e0  81 a1 c1 e1
  // b3:     90 b0 d0 f0  91 b1 d1 f1
  const __m128i b0 = _mm_unpacklo_epi16(a0, a1);
  const __m128i b1 = _mm_unpackhi_epi16(a0, a1);
  const __m128i b2 = _mm_unpacklo_epi16(a2, a3);
  const __m128i b3 = _mm_unpackhi_epi16(a2, a3);
  // out[0]: 00 10 20 30  40 50 60 70
  // out[1]: 01 11 21 31  41 51 61 71
  // out[2]: 80 90 a0 b0  c0 d0 e0 f0
  // out[3]: 81 91 a1 b1  c1 d1 e1 f1
  out[0] = _mm_unpacklo_epi16(b0, b1);
  out[1] = _mm_unpackhi_epi16(b0, b1);
  out[2] = _mm_unpacklo_epi16(b2, b3);
  out[3] = _mm_unpackhi_epi16(b2, b3);
}

LIBGAV1_ALWAYS_INLINE __m128i Transpose4x4_U8(const __m128i* const in) {
  // Unpack 8 bit elements. Goes from:
  // in[0]: 00 01 02 03
  // in[1]: 10 11 12 13
  // in[2]: 20 21 22 23
  // in[3]: 30 31 32 33
  // to:
  // a0:    00 10 01 11  02 12 03 13
  // a1:    20 30 21 31  22 32 23 33
  const __m128i a0 = _mm_unpacklo_epi8(in[0], in[1]);
  const __m128i a1 = _mm_unpacklo_epi8(in[2], in[3]);

  // Unpack 32 bit elements resulting in:
  // 00 10 20 30  01 11 21 31  02 12 22 32  03 13 23 33
  return _mm_unpacklo_epi16(a0, a1);
}

LIBGAV1_ALWAYS_INLINE void Transpose8x8To4x16_U8(const __m128i* const in,
                                                 __m128i* out) {
  // Unpack 8 bit elements. Goes from:
  // in[0]:  00 01 02 03 04 05 06 07
  // in[1]:  10 11 12 13 14 15 16 17
  // in[2]:  20 21 22 23 24 25 26 27
  // in[3]:  30 31 32 33 34 35 36 37
  // in[4]:  40 41 42 43 44 45 46 47
  // in[5]:  50 51 52 53 54 55 56 57
  // in[6]:  60 61 62 63 64 65 66 67
  // in[7]:  70 71 72 73 74 75 76 77
  // to:
  // a0:     00 10 01 11  02 12 03 13  04 14 05 15  06 16 07 17
  // a1:     20 30 21 31  22 32 23 33  24 34 25 35  26 36 27 37
  // a2:     40 50 41 51  42 52 43 53  44 54 45 55  46 56 47 57
  // a3:     60 70 61 71  62 72 63 73  64 74 65 75  66 76 67 77
  const __m128i a0 = _mm_unpacklo_epi8(in[0], in[1]);
  const __m128i a1 = _mm_unpacklo_epi8(in[2], in[3]);
  const __m128i a2 = _mm_unpacklo_epi8(in[4], in[5]);
  const __m128i a3 = _mm_unpacklo_epi8(in[6], in[7]);

  // b0:     00 10 20 30  01 11 21 31  02 12 22 32  03 13 23 33
  // b1:     40 50 60 70  41 51 61 71  42 52 62 72  43 53 63 73
  // b2:     04 14 24 34  05 15 25 35  06 16 26 36  07 17 27 37
  // b3:     44 54 64 74  45 55 65 75  46 56 66 76  47 57 67 77
  const __m128i b0 = _mm_unpacklo_epi16(a0, a1);
  const __m128i b1 = _mm_unpacklo_epi16(a2, a3);
  const __m128i b2 = _mm_unpackhi_epi16(a0, a1);
  const __m128i b3 = _mm_unpackhi_epi16(a2, a3);

  // out[0]: 00 10 20 30  40 50 60 70  01 11 21 31  41 51 61 71
  // out[1]: 02 12 22 32  42 52 62 72  03 13 23 33  43 53 63 73
  // out[2]: 04 14 24 34  44 54 64 74  05 15 25 35  45 55 65 75
  // out[3]: 06 16 26 36  46 56 66 76  07 17 27 37  47 57 67 77
  out[0] = _mm_unpacklo_epi32(b0, b1);
  out[1] = _mm_unpackhi_epi32(b0, b1);
  out[2] = _mm_unpacklo_epi32(b2, b3);
  out[3] = _mm_unpackhi_epi32(b2, b3);
}

LIBGAV1_ALWAYS_INLINE void Transpose4x4_U16(const __m128i* in, __m128i* out) {
  // Unpack 16 bit elements. Goes from:
  // in[0]: 00 01 02 03  XX XX XX XX
  // in[1]: 10 11 12 13  XX XX XX XX
  // in[2]: 20 21 22 23  XX XX XX XX
  // in[3]: 30 31 32 33  XX XX XX XX
  // to:
  // ba:    00 10 01 11  02 12 03 13
  // dc:    20 30 21 31  22 32 23 33
  const __m128i ba = _mm_unpacklo_epi16(in[0], in[1]);
  const __m128i dc = _mm_unpacklo_epi16(in[2], in[3]);
  // Unpack 32 bit elements resulting in:
  // dcba_lo: 00 10 20 30  01 11 21 31
  // dcba_hi: 02 12 22 32  03 13 23 33
  const __m128i dcba_lo = _mm_unpacklo_epi32(ba, dc);
  const __m128i dcba_hi = _mm_unpackhi_epi32(ba, dc);
  // Assign or shift right by 8 bytes resulting in:
  // out[0]: 00 10 20 30  01 11 21 31
  // out[1]: 01 11 21 31  XX XX XX XX
  // out[2]: 02 12 22 32  03 13 23 33
  // out[3]: 03 13 23 33  XX XX XX XX
  out[0] = dcba_lo;
  out[1] = _mm_srli_si128(dcba_lo, 8);
  out[2] = dcba_hi;
  out[3] = _mm_srli_si128(dcba_hi, 8);
}

LIBGAV1_ALWAYS_INLINE void Transpose4x8To8x4_U16(const __m128i* in,
                                                 __m128i* out) {
  // Unpack 16 bit elements. Goes from:
  // in[0]: 00 01 02 03  XX XX XX XX
  // in[1]: 10 11 12 13  XX XX XX XX
  // in[2]: 20 21 22 23  XX XX XX XX
  // in[3]: 30 31 32 33  XX XX XX XX
  // in[4]: 40 41 42 43  XX XX XX XX
  // in[5]: 50 51 52 53  XX XX XX XX
  // in[6]: 60 61 62 63  XX XX XX XX
  // in[7]: 70 71 72 73  XX XX XX XX
  // to:
  // a0:    00 10 01 11  02 12 03 13
  // a1:    20 30 21 31  22 32 23 33
  // a2:    40 50 41 51  42 52 43 53
  // a3:    60 70 61 71  62 72 63 73
  const __m128i a0 = _mm_unpacklo_epi16(in[0], in[1]);
  const __m128i a1 = _mm_unpacklo_epi16(in[2], in[3]);
  const __m128i a2 = _mm_unpacklo_epi16(in[4], in[5]);
  const __m128i a3 = _mm_unpacklo_epi16(in[6], in[7]);

  // Unpack 32 bit elements resulting in:
  // b0: 00 10 20 30  01 11 21 31
  // b1: 40 50 60 70  41 51 61 71
  // b2: 02 12 22 32  03 13 23 33
  // b3: 42 52 62 72  43 53 63 73
  const __m128i b0 = _mm_unpacklo_epi32(a0, a1);
  const __m128i b1 = _mm_unpacklo_epi32(a2, a3);
  const __m128i b2 = _mm_unpackhi_epi32(a0, a1);
  const __m128i b3 = _mm_unpackhi_epi32(a2, a3);

  // Unpack 64 bit elements resulting in:
  // out[0]: 00 10 20 30  40 50 60 70
  // out[1]: 01 11 21 31  41 51 61 71
  // out[2]: 02 12 22 32  42 52 62 72
  // out[3]: 03 13 23 33  43 53 63 73
  out[0] = _mm_unpacklo_epi64(b0, b1);
  out[1] = _mm_unpackhi_epi64(b0, b1);
  out[2] = _mm_unpacklo_epi64(b2, b3);
  out[3] = _mm_unpackhi_epi64(b2, b3);
}

LIBGAV1_ALWAYS_INLINE void Transpose8x4To4x8_U16(const __m128i* in,
                                                 __m128i* out) {
  // Unpack 16 bit elements. Goes from:
  // in[0]: 00 01 02 03  04 05 06 07
  // in[1]: 10 11 12 13  14 15 16 17
  // in[2]: 20 21 22 23  24 25 26 27
  // in[3]: 30 31 32 33  34 35 36 37

  // to:
  // a0:    00 10 01 11  02 12 03 13
  // a1:    20 30 21 31  22 32 23 33
  // a4:    04 14 05 15  06 16 07 17
  // a5:    24 34 25 35  26 36 27 37
  const __m128i a0 = _mm_unpacklo_epi16(in[0], in[1]);
  const __m128i a1 = _mm_unpacklo_epi16(in[2], in[3]);
  const __m128i a4 = _mm_unpackhi_epi16(in[0], in[1]);
  const __m128i a5 = _mm_unpackhi_epi16(in[2], in[3]);

  // Unpack 32 bit elements resulting in:
  // b0: 00 10 20 30  01 11 21 31
  // b2: 04 14 24 34  05 15 25 35
  // b4: 02 12 22 32  03 13 23 33
  // b6: 06 16 26 36  07 17 27 37
  const __m128i b0 = _mm_unpacklo_epi32(a0, a1);
  const __m128i b2 = _mm_unpacklo_epi32(a4, a5);
  const __m128i b4 = _mm_unpackhi_epi32(a0, a1);
  const __m128i b6 = _mm_unpackhi_epi32(a4, a5);

  // Unpack 64 bit elements resulting in:
  // out[0]: 00 10 20 30  XX XX XX XX
  // out[1]: 01 11 21 31  XX XX XX XX
  // out[2]: 02 12 22 32  XX XX XX XX
  // out[3]: 03 13 23 33  XX XX XX XX
  // out[4]: 04 14 24 34  XX XX XX XX
  // out[5]: 05 15 25 35  XX XX XX XX
  // out[6]: 06 16 26 36  XX XX XX XX
  // out[7]: 07 17 27 37  XX XX XX XX
  const __m128i zeros = _mm_setzero_si128();
  out[0] = _mm_unpacklo_epi64(b0, zeros);
  out[1] = _mm_unpackhi_epi64(b0, zeros);
  out[2] = _mm_unpacklo_epi64(b4, zeros);
  out[3] = _mm_unpackhi_epi64(b4, zeros);
  out[4] = _mm_unpacklo_epi64(b2, zeros);
  out[5] = _mm_unpackhi_epi64(b2, zeros);
  out[6] = _mm_unpacklo_epi64(b6, zeros);
  out[7] = _mm_unpackhi_epi64(b6, zeros);
}

LIBGAV1_ALWAYS_INLINE void Transpose8x8_U16(const __m128i* const in,
                                            __m128i* const out) {
  // Unpack 16 bit elements. Goes from:
  // in[0]: 00 01 02 03  04 05 06 07
  // in[1]: 10 11 12 13  14 15 16 17
  // in[2]: 20 21 22 23  24 25 26 27
  // in[3]: 30 31 32 33  34 35 36 37
  // in[4]: 40 41 42 43  44 45 46 47
  // in[5]: 50 51 52 53  54 55 56 57
  // in[6]: 60 61 62 63  64 65 66 67
  // in[7]: 70 71 72 73  74 75 76 77
  // to:
  // a0:    00 10 01 11  02 12 03 13
  // a1:    20 30 21 31  22 32 23 33
  // a2:    40 50 41 51  42 52 43 53
  // a3:    60 70 61 71  62 72 63 73
  // a4:    04 14 05 15  06 16 07 17
  // a5:    24 34 25 35  26 36 27 37
  // a6:    44 54 45 55  46 56 47 57
  // a7:    64 74 65 75  66 76 67 77
  const __m128i a0 = _mm_unpacklo_epi16(in[0], in[1]);
  const __m128i a1 = _mm_unpacklo_epi16(in[2], in[3]);
  const __m128i a2 = _mm_unpacklo_epi16(in[4], in[5]);
  const __m128i a3 = _mm_unpacklo_epi16(in[6], in[7]);
  const __m128i a4 = _mm_unpackhi_epi16(in[0], in[1]);
  const __m128i a5 = _mm_unpackhi_epi16(in[2], in[3]);
  const __m128i a6 = _mm_unpackhi_epi16(in[4], in[5]);
  const __m128i a7 = _mm_unpackhi_epi16(in[6], in[7]);

  // Unpack 32 bit elements resulting in:
  // b0: 00 10 20 30  01 11 21 31
  // b1: 40 50 60 70  41 51 61 71
  // b2: 04 14 24 34  05 15 25 35
  // b3: 44 54 64 74  45 55 65 75
  // b4: 02 12 22 32  03 13 23 33
  // b5: 42 52 62 72  43 53 63 73
  // b6: 06 16 26 36  07 17 27 37
  // b7: 46 56 66 76  47 57 67 77
  const __m128i b0 = _mm_unpacklo_epi32(a0, a1);
  const __m128i b1 = _mm_unpacklo_epi32(a2, a3);
  const __m128i b2 = _mm_unpacklo_epi32(a4, a5);
  const __m128i b3 = _mm_unpacklo_epi32(a6, a7);
  const __m128i b4 = _mm_unpackhi_epi32(a0, a1);
  const __m128i b5 = _mm_unpackhi_epi32(a2, a3);
  const __m128i b6 = _mm_unpackhi_epi32(a4, a5);
  const __m128i b7 = _mm_unpackhi_epi32(a6, a7);

  // Unpack 64 bit elements resulting in:
  // out[0]: 00 10 20 30  40 50 60 70
  // out[1]: 01 11 21 31  41 51 61 71
  // out[2]: 02 12 22 32  42 52 62 72
  // out[3]: 03 13 23 33  43 53 63 73
  // out[4]: 04 14 24 34  44 54 64 74
  // out[5]: 05 15 25 35  45 55 65 75
  // out[6]: 06 16 26 36  46 56 66 76
  // out[7]: 07 17 27 37  47 57 67 77
  out[0] = _mm_unpacklo_epi64(b0, b1);
  out[1] = _mm_unpackhi_epi64(b0, b1);
  out[2] = _mm_unpacklo_epi64(b4, b5);
  out[3] = _mm_unpackhi_epi64(b4, b5);
  out[4] = _mm_unpacklo_epi64(b2, b3);
  out[5] = _mm_unpackhi_epi64(b2, b3);
  out[6] = _mm_unpacklo_epi64(b6, b7);
  out[7] = _mm_unpackhi_epi64(b6, b7);
}

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_TARGETING_SSE4_1
#endif  // LIBGAV1_SRC_DSP_X86_TRANSPOSE_SSE4_H_

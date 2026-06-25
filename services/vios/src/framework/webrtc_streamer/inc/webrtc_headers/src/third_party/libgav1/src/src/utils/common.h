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

#ifndef LIBGAV1_SRC_UTILS_COMMON_H_
#define LIBGAV1_SRC_UTILS_COMMON_H_

#if defined(_MSC_VER)
#include <intrin.h>
#pragma intrinsic(_BitScanForward)
#pragma intrinsic(_BitScanReverse)
#if defined(_M_X64) || defined(_M_ARM64)
#pragma intrinsic(_BitScanReverse64)
#define HAVE_BITSCANREVERSE64
#endif  // defined(_M_X64) || defined(_M_ARM64)
#endif  // defined(_MSC_VER)

#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <type_traits>

#include "src/utils/bit_mask_set.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"
#include "src/utils/types.h"

namespace libgav1 {

// LIBGAV1_RESTRICT
// Declares a pointer with the restrict type qualifier if available.
// This allows code to hint to the compiler that only this pointer references a
// particular object or memory region within the scope of the block in which it
// is declared. This may allow for improved optimizations due to the lack of
// pointer aliasing. See also:
// https://en.cppreference.com/w/c/language/restrict
// Note a template alias is not used for compatibility with older compilers
// (e.g., gcc < 10) that do not expand the type when instantiating a template
// function, either explicitly or in an assignment to a function pointer as is
// done within the dsp code. RestrictPtr<T>::type is an alternative to this,
// similar to std::add_const, but for conciseness the macro is preferred.
#ifdef __GNUC__
#define LIBGAV1_RESTRICT __restrict__
#elif defined(_MSC_VER)
#define LIBGAV1_RESTRICT __restrict
#else
#define LIBGAV1_RESTRICT
#endif

// Aligns |value| to the desired |alignment|. |alignment| must be a power of 2.
template <typename T>
inline T Align(T value, T alignment) {
  assert(alignment != 0);
  const T alignment_mask = alignment - 1;
  return (value + alignment_mask) & ~alignment_mask;
}

// Aligns |addr| to the desired |alignment|. |alignment| must be a power of 2.
inline uint8_t* AlignAddr(uint8_t* const addr, const uintptr_t alignment) {
  const auto value = reinterpret_cast<uintptr_t>(addr);
  return reinterpret_cast<uint8_t*>(Align(value, alignment));
}

inline int32_t Clip3(int32_t value, int32_t low, int32_t high) {
  return value < low ? low : (value > high ? high : value);
}

template <typename Pixel>
void ExtendLine(void* const line_start, const int width, const int left,
                const int right) {
  auto* const start = static_cast<Pixel*>(line_start);
  const Pixel* src = start;
  Pixel* dst = start - left;
  // Copy to left and right borders.
  Memset(dst, src[0], left);
  Memset(dst + left + width, src[width - 1], right);
}

// The following 2 templates set a block of data with uncontiguous memory to
// |value|. The compilers usually generate several branches to handle different
// cases of |columns| when inlining memset() and std::fill(), and these branches
// are unfortunately within the loop of |rows|. So calling these templates
// directly could be inefficient. It is recommended to specialize common cases
// of |columns|, such as 1, 2, 4, 8, 16 and 32, etc. in advance before
// processing the generic case of |columns|. The code size may be larger, but
// there would be big speed gains.
// Call template MemSetBlock<> when sizeof(|T|) is 1.
// Call template SetBlock<> when sizeof(|T|) is larger than 1.
template <typename T>
void MemSetBlock(int rows, int columns, T value, T* dst, ptrdiff_t stride) {
  static_assert(sizeof(T) == 1, "");
  do {
    memset(dst, value, columns);
    dst += stride;
  } while (--rows != 0);
}

template <typename T>
void SetBlock(int rows, int columns, T value, T* dst, ptrdiff_t stride) {
  do {
    std::fill(dst, dst + columns, value);
    dst += stride;
  } while (--rows != 0);
}

#if defined(__GNUC__)

inline int CountLeadingZeros(uint32_t n) {
  assert(n != 0);
  return __builtin_clz(n);
}

inline int CountLeadingZeros(uint64_t n) {
  assert(n != 0);
  return __builtin_clzll(n);
}

inline int CountTrailingZeros(uint32_t n) {
  assert(n != 0);
  return __builtin_ctz(n);
}

#elif defined(_MSC_VER)

inline int CountLeadingZeros(uint32_t n) {
  assert(n != 0);
  unsigned long first_set_bit;  // NOLINT(runtime/int)
  const unsigned char bit_set = _BitScanReverse(&first_set_bit, n);
  assert(bit_set != 0);
  static_cast<void>(bit_set);
  return 31 ^ static_cast<int>(first_set_bit);
}

inline int CountLeadingZeros(uint64_t n) {
  assert(n != 0);
  unsigned long first_set_bit;  // NOLINT(runtime/int)
#if defined(HAVE_BITSCANREVERSE64)
  const unsigned char bit_set =
      _BitScanReverse64(&first_set_bit, static_cast<unsigned __int64>(n));
#else   // !defined(HAVE_BITSCANREVERSE64)
  const auto n_hi = static_cast<unsigned long>(n >> 32);  // NOLINT(runtime/int)
  if (n_hi != 0) {
    const unsigned char bit_set = _BitScanReverse(&first_set_bit, n_hi);
    assert(bit_set != 0);
    static_cast<void>(bit_set);
    return 31 ^ static_cast<int>(first_set_bit);
  }
  const unsigned char bit_set = _BitScanReverse(
      &first_set_bit, static_cast<unsigned long>(n));  // NOLINT(runtime/int)
#endif  // defined(HAVE_BITSCANREVERSE64)
  assert(bit_set != 0);
  static_cast<void>(bit_set);
  return 63 ^ static_cast<int>(first_set_bit);
}

#undef HAVE_BITSCANREVERSE64

inline int CountTrailingZeros(uint32_t n) {
  assert(n != 0);
  unsigned long first_set_bit;  // NOLINT(runtime/int)
  const unsigned char bit_set = _BitScanForward(&first_set_bit, n);
  assert(bit_set != 0);
  static_cast<void>(bit_set);
  return static_cast<int>(first_set_bit);
}

#else  // !defined(__GNUC__) && !defined(_MSC_VER)

template <const int kMSB, typename T>
inline int CountLeadingZeros(T n) {
  assert(n != 0);
  const T msb = T{1} << kMSB;
  int count = 0;
  while ((n & msb) == 0) {
    ++count;
    n <<= 1;
  }
  return count;
}

inline int CountLeadingZeros(uint32_t n) { return CountLeadingZeros<31>(n); }

inline int CountLeadingZeros(uint64_t n) { return CountLeadingZeros<63>(n); }

// This is the algorithm on the left in Figure 5-23, Hacker's Delight, Second
// Edition, page 109. The book says:
//   If the number of trailing 0's is expected to be small or large, then the
//   simple loops shown in Figure 5-23 are quite fast.
inline int CountTrailingZeros(uint32_t n) {
  assert(n != 0);
  // Create a word with 1's at the positions of the trailing 0's in |n|, and
  // 0's elsewhere (e.g., 01011000 => 00000111).
  n = ~n & (n - 1);
  int count = 0;
  while (n != 0) {
    ++count;
    n >>= 1;
  }
  return count;
}

#endif  // defined(__GNUC__)

inline int FloorLog2(int32_t n) {
  assert(n > 0);
  return 31 ^ CountLeadingZeros(static_cast<uint32_t>(n));
}

inline int FloorLog2(uint32_t n) {
  assert(n > 0);
  return 31 ^ CountLeadingZeros(n);
}

inline int FloorLog2(int64_t n) {
  assert(n > 0);
  return 63 ^ CountLeadingZeros(static_cast<uint64_t>(n));
}

inline int FloorLog2(uint64_t n) {
  assert(n > 0);
  return 63 ^ CountLeadingZeros(n);
}

inline int CeilLog2(unsigned int n) {
  // The expression FloorLog2(n - 1) + 1 is undefined not only for n == 0 but
  // also for n == 1, so this expression must be guarded by the n < 2 test. An
  // alternative implementation is:
  // return (n == 0) ? 0 : FloorLog2(n) + static_cast<int>((n & (n - 1)) != 0);
  return (n < 2) ? 0 : FloorLog2(n - 1) + 1;
}

inline int RightShiftWithCeiling(int value, int bits) {
  assert(bits > 0);
  return (value + (1 << bits) - 1) >> bits;
}

inline int32_t RightShiftWithRounding(int32_t value, int bits) {
  assert(bits >= 0);
  return (value + ((1 << bits) >> 1)) >> bits;
}

inline uint32_t RightShiftWithRounding(uint32_t value, int bits) {
  assert(bits >= 0);
  return (value + ((1 << bits) >> 1)) >> bits;
}

// This variant is used when |value| can exceed 32 bits. Although the final
// result must always fit into int32_t.
inline int32_t RightShiftWithRounding(int64_t value, int bits) {
  assert(bits >= 0);
  return static_cast<int32_t>((value + ((int64_t{1} << bits) >> 1)) >> bits);
}

inline int32_t RightShiftWithRoundingSigned(int32_t value, int bits) {
  assert(bits > 0);
  // The next line is equivalent to:
  // return (value >= 0) ? RightShiftWithRounding(value, bits)
  //                     : -RightShiftWithRounding(-value, bits);
  return RightShiftWithRounding(value + (value >> 31), bits);
}

// This variant is used when |value| can exceed 32 bits. Although the final
// result must always fit into int32_t.
inline int32_t RightShiftWithRoundingSigned(int64_t value, int bits) {
  assert(bits > 0);
  // The next line is equivalent to:
  // return (value >= 0) ? RightShiftWithRounding(value, bits)
  //                     : -RightShiftWithRounding(-value, bits);
  return RightShiftWithRounding(value + (value >> 63), bits);
}

constexpr int DivideBy2(int n) { return n >> 1; }
constexpr int DivideBy4(int n) { return n >> 2; }
constexpr int DivideBy8(int n) { return n >> 3; }
constexpr int DivideBy16(int n) { return n >> 4; }
constexpr int DivideBy32(int n) { return n >> 5; }
constexpr int DivideBy64(int n) { return n >> 6; }
constexpr int DivideBy128(int n) { return n >> 7; }

// Convert |value| to unsigned before shifting to avoid undefined behavior with
// negative values.
inline int LeftShift(int value, int bits) {
  assert(bits >= 0);
  assert(value >= -(int64_t{1} << (31 - bits)));
  assert(value <= (int64_t{1} << (31 - bits)) - ((bits == 0) ? 1 : 0));
  return static_cast<int>(static_cast<uint32_t>(value) << bits);
}
inline int MultiplyBy2(int n) { return LeftShift(n, 1); }
inline int MultiplyBy4(int n) { return LeftShift(n, 2); }
inline int MultiplyBy8(int n) { return LeftShift(n, 3); }
inline int MultiplyBy16(int n) { return LeftShift(n, 4); }
inline int MultiplyBy32(int n) { return LeftShift(n, 5); }
inline int MultiplyBy64(int n) { return LeftShift(n, 6); }

constexpr int Mod32(int n) { return n & 0x1f; }
constexpr int Mod64(int n) { return n & 0x3f; }

//------------------------------------------------------------------------------
// Bitstream functions

constexpr bool IsIntraFrame(FrameType type) {
  return type == kFrameKey || type == kFrameIntraOnly;
}

inline TransformClass GetTransformClass(TransformType tx_type) {
  constexpr BitMaskSet kTransformClassVerticalMask(
      kTransformTypeIdentityDct, kTransformTypeIdentityAdst,
      kTransformTypeIdentityFlipadst);
  if (kTransformClassVerticalMask.Contains(tx_type)) {
    return kTransformClassVertical;
  }
  constexpr BitMaskSet kTransformClassHorizontalMask(
      kTransformTypeDctIdentity, kTransformTypeAdstIdentity,
      kTransformTypeFlipadstIdentity);
  if (kTransformClassHorizontalMask.Contains(tx_type)) {
    return kTransformClassHorizontal;
  }
  return kTransformClass2D;
}

inline int RowOrColumn4x4ToPixel(int row_or_column4x4, Plane plane,
                                 int8_t subsampling) {
  return MultiplyBy4(row_or_column4x4) >> (plane == kPlaneY ? 0 : subsampling);
}

constexpr PlaneType GetPlaneType(Plane plane) {
  return static_cast<PlaneType>(plane != kPlaneY);
}

// 5.11.44.
constexpr bool IsDirectionalMode(PredictionMode mode) {
  return mode >= kPredictionModeVertical && mode <= kPredictionModeD67;
}

// 5.9.3.
//
// |a| and |b| are order hints, treated as unsigned order_hint_bits-bit
// integers. |order_hint_shift_bits| equals (32 - order_hint_bits) % 32.
// order_hint_bits is at most 8, so |order_hint_shift_bits| is zero or a
// value between 24 and 31 (inclusive).
//
// If |order_hint_shift_bits| is zero, |a| and |b| are both zeros, and the
// result is zero. If |order_hint_shift_bits| is not zero, returns the
// signed difference |a| - |b| using "modular arithmetic". More precisely, the
// signed difference |a| - |b| is treated as a signed order_hint_bits-bit
// integer and cast to an int. The returned difference is between
// -(1 << (order_hint_bits - 1)) and (1 << (order_hint_bits - 1)) - 1
// (inclusive).
//
// NOTE: |a| and |b| are the order_hint_bits least significant bits of the
// actual values. This function returns the signed difference between the
// actual values. The returned difference is correct as long as the actual
// values are not more than 1 << (order_hint_bits - 1) - 1 apart.
//
// Example: Suppose order_hint_bits is 4 and |order_hint_shift_bits|
// is 28. Then |a| and |b| are in the range [0, 15], and the actual values for
// |a| and |b| must not be more than 7 apart. (If the actual values for |a| and
// |b| are exactly 8 apart, this function cannot tell whether the actual value
// for |a| is before or after the actual value for |b|.)
//
// First, consider the order hints 2 and 6. For this simple case, we have
//   GetRelativeDistance(2, 6, 28) = 2 - 6 = -4, and
//   GetRelativeDistance(6, 2, 28) = 6 - 2 = 4.
//
// On the other hand, consider the order hints 2 and 14. The order hints are
// 12 (> 7) apart, so we need to use the actual values instead. The actual
// values may be 34 (= 2 mod 16) and 30 (= 14 mod 16), respectively. Therefore
// we have
//   GetRelativeDistance(2, 14, 28) = 34 - 30 = 4, and
//   GetRelativeDistance(14, 2, 28) = 30 - 34 = -4.
//
// The following comments apply only to specific CPUs' SIMD implementations,
// such as intrinsics code.
// For the 2 shift operations in this function, if the SIMD packed data is
// 16-bit wide, try to use |order_hint_shift_bits| - 16 as the number of bits to
// shift; If the SIMD packed data is 8-bit wide, try to use
// |order_hint_shift_bits| - 24 as as the number of bits to shift.
// |order_hint_shift_bits| - 16 and |order_hint_shift_bits| - 24 could be -16 or
// -24. In these cases diff is 0, and the behavior of left or right shifting -16
// or -24 bits is defined for x86 SIMD instructions and ARM NEON instructions,
// and the result of shifting 0 is still 0. There is no guarantee that this
// behavior and result apply to other CPUs' SIMD instructions.
inline int GetRelativeDistance(const unsigned int a, const unsigned int b,
                               const unsigned int order_hint_shift_bits) {
  const int diff = static_cast<int>(a) - static_cast<int>(b);
  assert(order_hint_shift_bits <= 31);
  if (order_hint_shift_bits == 0) {
    assert(a == 0);
    assert(b == 0);
  } else {
    assert(order_hint_shift_bits >= 24);  // i.e., order_hint_bits <= 8
    assert(a < (1u << (32 - order_hint_shift_bits)));
    assert(b < (1u << (32 - order_hint_shift_bits)));
    assert(diff < (1 << (32 - order_hint_shift_bits)));
    assert(diff >= -(1 << (32 - order_hint_shift_bits)));
  }
  // Sign extend the result of subtracting the values.
  // Cast to unsigned int and then left shift to avoid undefined behavior with
  // negative values. Cast to int to do the sign extension through right shift.
  // This requires the right shift of a signed integer be an arithmetic shift,
  // which is true for clang, gcc, and Visual C++.
  // These two casts do not generate extra instructions.
  // Don't use LeftShift(diff) since a valid diff may fail its assertions.
  // For example, GetRelativeDistance(2, 14, 28), diff equals -12 and is less
  // than the minimum allowed value of LeftShift() which is -8.
  // The next 3 lines are equivalent to:
  // const int order_hint_bits = Mod32(32 - order_hint_shift_bits);
  // const int m = (1 << order_hint_bits) >> 1;
  // return (diff & (m - 1)) - (diff & m);
  return static_cast<int>(static_cast<unsigned int>(diff)
                          << order_hint_shift_bits) >>
         order_hint_shift_bits;
}

// Applies |sign| (must be 0 or -1) to |value|, i.e.,
//   return (sign == 0) ? value : -value;
// and does so without a branch.
constexpr int ApplySign(int value, int sign) { return (value ^ sign) - sign; }

// 7.9.3. (without the clamp for numerator and denominator).
inline void GetMvProjection(const MotionVector& mv, int numerator,
                            int division_multiplier,
                            MotionVector* projection_mv) {
  // Allow numerator and to be 0 so that this function can be called
  // unconditionally. When numerator is 0, |projection_mv| will be 0, and this
  // is what we want.
  assert(std::abs(numerator) <= kMaxFrameDistance);
  for (int i = 0; i < 2; ++i) {
    projection_mv->mv[i] =
        Clip3(RightShiftWithRoundingSigned(
                  mv.mv[i] * numerator * division_multiplier, 14),
              -kProjectionMvClamp, kProjectionMvClamp);
  }
}

// 7.9.4.
constexpr int Project(int value, int delta, int dst_sign) {
  return value + ApplySign(delta / 64, dst_sign);
}

inline bool IsBlockSmallerThan8x8(BlockSize size) {
  return size < kBlock8x8 && size != kBlock4x16;
}

// Returns true if the either the width or the height of the block is equal to
// four.
inline bool IsBlockDimension4(BlockSize size) {
  return size < kBlock8x8 || size == kBlock16x4;
}

// Converts bitdepth 8, 10, and 12 to array index 0, 1, and 2, respectively.
constexpr int BitdepthToArrayIndex(int bitdepth) { return (bitdepth - 8) >> 1; }

// Maps a square transform to an index between [0, 4]. kTransformSize4x4 maps
// to 0, kTransformSize8x8 maps to 1 and so on.
inline int TransformSizeToSquareTransformIndex(TransformSize tx_size) {
  assert(kTransformWidth[tx_size] == kTransformHeight[tx_size]);

  // The values of the square transform sizes happen to be in the right
  // ranges, so we can just divide them by 4 to get the indexes.
  static_assert(
      std::is_unsigned<std::underlying_type<TransformSize>::type>::value, "");
  static_assert(kTransformSize4x4 < 4, "");
  static_assert(4 <= kTransformSize8x8 && kTransformSize8x8 < 8, "");
  static_assert(8 <= kTransformSize16x16 && kTransformSize16x16 < 12, "");
  static_assert(12 <= kTransformSize32x32 && kTransformSize32x32 < 16, "");
  static_assert(16 <= kTransformSize64x64 && kTransformSize64x64 < 20, "");
  return DivideBy4(tx_size);
}

// Gets the corresponding Y/U/V position, to set and get filter masks
// in deblock filtering.
// Returns luma_position if it's Y plane, whose subsampling must be 0.
// Returns the odd position for U/V plane, if there is subsampling.
constexpr int GetDeblockPosition(const int luma_position,
                                 const int subsampling) {
  return luma_position | subsampling;
}

// Returns the size of the residual buffer required to hold the residual values
// for a block or frame of size |rows| by |columns| (taking into account
// |subsampling_x|, |subsampling_y| and |residual_size|). |residual_size| is the
// number of bytes required to represent one residual value.
inline size_t GetResidualBufferSize(const int rows, const int columns,
                                    const int subsampling_x,
                                    const int subsampling_y,
                                    const size_t residual_size) {
  // The subsampling multipliers are:
  //   Both x and y are subsampled: 3 / 2.
  //   Only x or y is subsampled: 2 / 1 (which is equivalent to 4 / 2).
  //   Both x and y are not subsampled: 3 / 1 (which is equivalent to 6 / 2).
  // So we compute the final subsampling multiplier as follows:
  //   multiplier = (2 + (4 >> subsampling_x >> subsampling_y)) / 2.
  // Add 32 * |kResidualPaddingVertical| padding to avoid bottom boundary checks
  // when parsing quantized coefficients.
  const int subsampling_multiplier_num =
      2 + (4 >> subsampling_x >> subsampling_y);
  const int number_elements =
      (rows * columns * subsampling_multiplier_num) >> 1;
  const int tx_padding = 32 * kResidualPaddingVertical;
  return residual_size * (number_elements + tx_padding);
}

// This function is equivalent to:
// std::min({kTransformWidthLog2[tx_size] - 2,
//           kTransformWidthLog2[left_tx_size] - 2,
//           2});
constexpr LoopFilterTransformSizeId GetTransformSizeIdWidth(
    TransformSize tx_size, TransformSize left_tx_size) {
  return static_cast<LoopFilterTransformSizeId>(
      static_cast<int>(tx_size > kTransformSize4x16 &&
                       left_tx_size > kTransformSize4x16) +
      static_cast<int>(tx_size > kTransformSize8x32 &&
                       left_tx_size > kTransformSize8x32));
}

// This is used for 7.11.3.4 Block Inter Prediction Process, to select convolve
// filters.
inline int GetFilterIndex(const int filter_index, const int length) {
  if (length <= 4) {
    if (filter_index == kInterpolationFilterEightTap ||
        filter_index == kInterpolationFilterEightTapSharp) {
      return 4;
    }
    if (filter_index == kInterpolationFilterEightTapSmooth) {
      return 5;
    }
  }
  return filter_index;
}

// This has identical results as RightShiftWithRounding since |subsampling| can
// only be 0 or 1.
constexpr int SubsampledValue(int value, int subsampling) {
  return (value + subsampling) >> subsampling;
}

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_COMMON_H_

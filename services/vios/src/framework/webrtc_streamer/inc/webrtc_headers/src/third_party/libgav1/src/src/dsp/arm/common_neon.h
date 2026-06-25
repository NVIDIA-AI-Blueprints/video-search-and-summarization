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

#ifndef LIBGAV1_SRC_DSP_ARM_COMMON_NEON_H_
#define LIBGAV1_SRC_DSP_ARM_COMMON_NEON_H_

#include "src/utils/cpu.h"

#if LIBGAV1_ENABLE_NEON

#include <arm_neon.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "src/utils/compiler_attributes.h"

#if 0
#include <cstdio>
#include <string>

constexpr bool kEnablePrintRegs = true;

union DebugRegister {
  int8_t i8[8];
  int16_t i16[4];
  int32_t i32[2];
  uint8_t u8[8];
  uint16_t u16[4];
  uint32_t u32[2];
};

union DebugRegisterQ {
  int8_t i8[16];
  int16_t i16[8];
  int32_t i32[4];
  uint8_t u8[16];
  uint16_t u16[8];
  uint32_t u32[4];
};

// Quite useful macro for debugging. Left here for convenience.
inline void PrintVect(const DebugRegister r, const char* const name, int size) {
  int n;
  if (kEnablePrintRegs) {
    fprintf(stderr, "%s\t: ", name);
    if (size == 8) {
      for (n = 0; n < 8; ++n) fprintf(stderr, "%.2x ", r.u8[n]);
    } else if (size == 16) {
      for (n = 0; n < 4; ++n) fprintf(stderr, "%.4x ", r.u16[n]);
    } else if (size == 32) {
      for (n = 0; n < 2; ++n) fprintf(stderr, "%.8x ", r.u32[n]);
    }
    fprintf(stderr, "\n");
  }
}

// Debugging macro for 128-bit types.
inline void PrintVectQ(const DebugRegisterQ r, const char* const name,
                       int size) {
  int n;
  if (kEnablePrintRegs) {
    fprintf(stderr, "%s\t: ", name);
    if (size == 8) {
      for (n = 0; n < 16; ++n) fprintf(stderr, "%.2x ", r.u8[n]);
    } else if (size == 16) {
      for (n = 0; n < 8; ++n) fprintf(stderr, "%.4x ", r.u16[n]);
    } else if (size == 32) {
      for (n = 0; n < 4; ++n) fprintf(stderr, "%.8x ", r.u32[n]);
    }
    fprintf(stderr, "\n");
  }
}

inline void PrintReg(const int32x4x2_t val, const std::string& name) {
  DebugRegisterQ r;
  vst1q_s32(r.i32, val.val[0]);
  const std::string name0 = name + std::string(".val[0]");
  PrintVectQ(r, name0.c_str(), 32);
  vst1q_s32(r.i32, val.val[1]);
  const std::string name1 = name + std::string(".val[1]");
  PrintVectQ(r, name1.c_str(), 32);
}

inline void PrintReg(const uint32x4_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_u32(r.u32, val);
  PrintVectQ(r, name, 32);
}

inline void PrintReg(const uint32x2_t val, const char* name) {
  DebugRegister r;
  vst1_u32(r.u32, val);
  PrintVect(r, name, 32);
}

inline void PrintReg(const uint16x8_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_u16(r.u16, val);
  PrintVectQ(r, name, 16);
}

inline void PrintReg(const uint16x4_t val, const char* name) {
  DebugRegister r;
  vst1_u16(r.u16, val);
  PrintVect(r, name, 16);
}

inline void PrintReg(const uint8x16_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_u8(r.u8, val);
  PrintVectQ(r, name, 8);
}

inline void PrintReg(const uint8x8_t val, const char* name) {
  DebugRegister r;
  vst1_u8(r.u8, val);
  PrintVect(r, name, 8);
}

inline void PrintReg(const int32x4_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_s32(r.i32, val);
  PrintVectQ(r, name, 32);
}

inline void PrintReg(const int32x2_t val, const char* name) {
  DebugRegister r;
  vst1_s32(r.i32, val);
  PrintVect(r, name, 32);
}

inline void PrintReg(const int16x8_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_s16(r.i16, val);
  PrintVectQ(r, name, 16);
}

inline void PrintReg(const int16x4_t val, const char* name) {
  DebugRegister r;
  vst1_s16(r.i16, val);
  PrintVect(r, name, 16);
}

inline void PrintReg(const int8x16_t val, const char* name) {
  DebugRegisterQ r;
  vst1q_s8(r.i8, val);
  PrintVectQ(r, name, 8);
}

inline void PrintReg(const int8x8_t val, const char* name) {
  DebugRegister r;
  vst1_s8(r.i8, val);
  PrintVect(r, name, 8);
}

// Print an individual (non-vector) value in decimal format.
inline void PrintReg(const int x, const char* name) {
  if (kEnablePrintRegs) {
    fprintf(stderr, "%s: %d\n", name, x);
  }
}

// Print an individual (non-vector) value in hexadecimal format.
inline void PrintHex(const int x, const char* name) {
  if (kEnablePrintRegs) {
    fprintf(stderr, "%s: %x\n", name, x);
  }
}

#define PR(x) PrintReg(x, #x)
#define PD(x) PrintReg(x, #x)
#define PX(x) PrintHex(x, #x)

#if LIBGAV1_MSAN
inline void PrintShadow(const void* r, const char* const name,
                        const size_t size) {
  if (kEnablePrintRegs) {
    fprintf(stderr, "Shadow for %s:\n", name);
    __msan_print_shadow(r, size);
  }
}
#define PS(var, N) PrintShadow(var, #var, N)

#endif  // LIBGAV1_MSAN

#endif  // 0

namespace libgav1 {
namespace dsp {

//------------------------------------------------------------------------------
// Load functions.

// Load 2 uint8_t values into lanes 0 and 1. Zeros the register before loading
// the values. Use caution when using this in loops because it will re-zero the
// register before loading on every iteration.
inline uint8x8_t Load2(const void* const buf) {
  const uint16x4_t zero = vdup_n_u16(0);
  uint16_t temp;
  memcpy(&temp, buf, 2);
  return vreinterpret_u8_u16(vld1_lane_u16(&temp, zero, 0));
}

// Load 2 uint8_t values into |lane| * 2 and |lane| * 2 + 1.
template <int lane>
inline uint8x8_t Load2(const void* const buf, uint8x8_t val) {
  uint16_t temp;
  memcpy(&temp, buf, 2);
  return vreinterpret_u8_u16(
      vld1_lane_u16(&temp, vreinterpret_u16_u8(val), lane));
}

template <int lane>
inline uint16x4_t Load2(const void* const buf, uint16x4_t val) {
  uint32_t temp;
  memcpy(&temp, buf, 4);
  return vreinterpret_u16_u32(
      vld1_lane_u32(&temp, vreinterpret_u32_u16(val), lane));
}

// Load 4 uint8_t values into the low half of a uint8x8_t register. Zeros the
// register before loading the values. Use caution when using this in loops
// because it will re-zero the register before loading on every iteration.
inline uint8x8_t Load4(const void* const buf) {
  const uint32x2_t zero = vdup_n_u32(0);
  uint32_t temp;
  memcpy(&temp, buf, 4);
  return vreinterpret_u8_u32(vld1_lane_u32(&temp, zero, 0));
}

// Load 4 uint8_t values into 4 lanes staring with |lane| * 4.
template <int lane>
inline uint8x8_t Load4(const void* const buf, uint8x8_t val) {
  uint32_t temp;
  memcpy(&temp, buf, 4);
  return vreinterpret_u8_u32(
      vld1_lane_u32(&temp, vreinterpret_u32_u8(val), lane));
}

// Convenience functions for 16-bit loads from a uint8_t* source.
inline uint16x4_t Load4U16(const void* const buf) {
  return vld1_u16(static_cast<const uint16_t*>(buf));
}

inline uint16x8_t Load8U16(const void* const buf) {
  return vld1q_u16(static_cast<const uint16_t*>(buf));
}

//------------------------------------------------------------------------------
// Load functions to avoid MemorySanitizer's use-of-uninitialized-value warning.

inline uint8x8_t MaskOverreads(const uint8x8_t source,
                               const ptrdiff_t over_read_in_bytes) {
  uint8x8_t dst = source;
#if LIBGAV1_MSAN
  if (over_read_in_bytes > 0) {
    uint8x8_t mask = vdup_n_u8(0);
    uint8x8_t valid_element_mask = vdup_n_u8(-1);
    const int valid_bytes =
        std::min(8, 8 - static_cast<int>(over_read_in_bytes));
    for (int i = 0; i < valid_bytes; ++i) {
      // Feed ff bytes into |mask| one at a time.
      mask = vext_u8(valid_element_mask, mask, 7);
    }
    dst = vand_u8(dst, mask);
  }
#else
  static_cast<void>(over_read_in_bytes);
#endif
  return dst;
}

inline uint8x16_t MaskOverreadsQ(const uint8x16_t source,
                                 const ptrdiff_t over_read_in_bytes) {
  uint8x16_t dst = source;
#if LIBGAV1_MSAN
  if (over_read_in_bytes > 0) {
    uint8x16_t mask = vdupq_n_u8(0);
    uint8x16_t valid_element_mask = vdupq_n_u8(-1);
    const int valid_bytes =
        std::min(16, 16 - static_cast<int>(over_read_in_bytes));
    for (int i = 0; i < valid_bytes; ++i) {
      // Feed ff bytes into |mask| one at a time.
      mask = vextq_u8(valid_element_mask, mask, 15);
    }
    dst = vandq_u8(dst, mask);
  }
#else
  static_cast<void>(over_read_in_bytes);
#endif
  return dst;
}

inline uint16x8_t MaskOverreadsQ(const uint16x8_t source,
                                 const ptrdiff_t over_read_in_bytes) {
  return vreinterpretq_u16_u8(
      MaskOverreadsQ(vreinterpretq_u8_u16(source), over_read_in_bytes));
}

inline uint8x8_t Load1MsanU8(const uint8_t* const source,
                             const ptrdiff_t over_read_in_bytes) {
  return MaskOverreads(vld1_u8(source), over_read_in_bytes);
}

inline uint8x16_t Load1QMsanU8(const uint8_t* const source,
                               const ptrdiff_t over_read_in_bytes) {
  return MaskOverreadsQ(vld1q_u8(source), over_read_in_bytes);
}

inline uint16x8_t Load1QMsanU16(const uint16_t* const source,
                                const ptrdiff_t over_read_in_bytes) {
  return vreinterpretq_u16_u8(MaskOverreadsQ(
      vreinterpretq_u8_u16(vld1q_u16(source)), over_read_in_bytes));
}

inline uint32x4_t Load1QMsanU32(const uint32_t* const source,
                                const ptrdiff_t over_read_in_bytes) {
  return vreinterpretq_u32_u8(MaskOverreadsQ(
      vreinterpretq_u8_u32(vld1q_u32(source)), over_read_in_bytes));
}

//------------------------------------------------------------------------------
// Store functions.

// Propagate type information to the compiler. Without this the compiler may
// assume the required alignment of the type (4 bytes in the case of uint32_t)
// and add alignment hints to the memory access.
template <typename T>
inline void ValueToMem(void* const buf, T val) {
  memcpy(buf, &val, sizeof(val));
}

// Store 4 int8_t values from the low half of an int8x8_t register.
inline void StoreLo4(void* const buf, const int8x8_t val) {
  ValueToMem<int32_t>(buf, vget_lane_s32(vreinterpret_s32_s8(val), 0));
}

// Store 4 uint8_t values from the low half of a uint8x8_t register.
inline void StoreLo4(void* const buf, const uint8x8_t val) {
  ValueToMem<uint32_t>(buf, vget_lane_u32(vreinterpret_u32_u8(val), 0));
}

// Store 4 uint8_t values from the high half of a uint8x8_t register.
inline void StoreHi4(void* const buf, const uint8x8_t val) {
  ValueToMem<uint32_t>(buf, vget_lane_u32(vreinterpret_u32_u8(val), 1));
}

// Store 2 uint8_t values from |lane| * 2 and |lane| * 2 + 1 of a uint8x8_t
// register.
template <int lane>
inline void Store2(void* const buf, const uint8x8_t val) {
  ValueToMem<uint16_t>(buf, vget_lane_u16(vreinterpret_u16_u8(val), lane));
}

// Store 2 uint16_t values from |lane| * 2 and |lane| * 2 + 1 of a uint16x8_t
// register.
template <int lane>
inline void Store2(void* const buf, const uint16x8_t val) {
  ValueToMem<uint32_t>(buf, vgetq_lane_u32(vreinterpretq_u32_u16(val), lane));
}

// Store 2 uint16_t values from |lane| * 2 and |lane| * 2 + 1 of a uint16x4_t
// register.
template <int lane>
inline void Store2(void* const buf, const uint16x4_t val) {
  ValueToMem<uint32_t>(buf, vget_lane_u32(vreinterpret_u32_u16(val), lane));
}

// Simplify code when caller has |buf| cast as uint8_t*.
inline void Store4(void* const buf, const uint16x4_t val) {
  vst1_u16(static_cast<uint16_t*>(buf), val);
}

// Simplify code when caller has |buf| cast as uint8_t*.
inline void Store8(void* const buf, const uint16x8_t val) {
  vst1q_u16(static_cast<uint16_t*>(buf), val);
}

inline void Store4QMsanS16(void* const buf, const int16x8x4_t src) {
#if LIBGAV1_MSAN
  // The memory shadow is incorrect for vst4q_u16, only marking the first 16
  // bytes of the destination as initialized. To avoid missing truly
  // uninitialized memory, check the input vectors first, before marking the
  // whole 64 bytes initialized. If any input vector contains unused values, it
  // should pass through MaskOverreadsQ first.
  __msan_check_mem_is_initialized(&src.val[0], sizeof(src.val[0]));
  __msan_check_mem_is_initialized(&src.val[1], sizeof(src.val[1]));
  __msan_check_mem_is_initialized(&src.val[2], sizeof(src.val[2]));
  __msan_check_mem_is_initialized(&src.val[3], sizeof(src.val[3]));
  vst4q_s16(static_cast<int16_t*>(buf), src);
  __msan_unpoison(buf, sizeof(int16x8x4_t));
#else
  vst4q_s16(static_cast<int16_t*>(buf), src);
#endif  // LIBGAV1_MSAN
}

//------------------------------------------------------------------------------
// Pointer helpers.

// This function adds |stride|, given as a number of bytes, to a pointer to a
// larger type, using native pointer arithmetic.
template <typename T>
inline T* AddByteStride(T* ptr, const ptrdiff_t stride) {
  return reinterpret_cast<T*>(
      const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(ptr) + stride));
}

//------------------------------------------------------------------------------
// Multiply.

// Shim vmull_high_u16 for armv7.
inline uint32x4_t VMullHighU16(const uint16x8_t a, const uint16x8_t b) {
#if defined(__aarch64__)
  return vmull_high_u16(a, b);
#else
  return vmull_u16(vget_high_u16(a), vget_high_u16(b));
#endif
}

// Shim vmull_high_s16 for armv7.
inline int32x4_t VMullHighS16(const int16x8_t a, const int16x8_t b) {
#if defined(__aarch64__)
  return vmull_high_s16(a, b);
#else
  return vmull_s16(vget_high_s16(a), vget_high_s16(b));
#endif
}

// Shim vmlal_high_u16 for armv7.
inline uint32x4_t VMlalHighU16(const uint32x4_t a, const uint16x8_t b,
                               const uint16x8_t c) {
#if defined(__aarch64__)
  return vmlal_high_u16(a, b, c);
#else
  return vmlal_u16(a, vget_high_u16(b), vget_high_u16(c));
#endif
}

// Shim vmlal_high_s16 for armv7.
inline int32x4_t VMlalHighS16(const int32x4_t a, const int16x8_t b,
                              const int16x8_t c) {
#if defined(__aarch64__)
  return vmlal_high_s16(a, b, c);
#else
  return vmlal_s16(a, vget_high_s16(b), vget_high_s16(c));
#endif
}

// Shim vmul_laneq_u16 for armv7.
template <int lane>
inline uint16x4_t VMulLaneQU16(const uint16x4_t a, const uint16x8_t b) {
#if defined(__aarch64__)
  return vmul_laneq_u16(a, b, lane);
#else
  if (lane < 4) return vmul_lane_u16(a, vget_low_u16(b), lane & 0x3);
  return vmul_lane_u16(a, vget_high_u16(b), (lane - 4) & 0x3);
#endif
}

// Shim vmulq_laneq_u16 for armv7.
template <int lane>
inline uint16x8_t VMulQLaneQU16(const uint16x8_t a, const uint16x8_t b) {
#if defined(__aarch64__)
  return vmulq_laneq_u16(a, b, lane);
#else
  if (lane < 4) return vmulq_lane_u16(a, vget_low_u16(b), lane & 0x3);
  return vmulq_lane_u16(a, vget_high_u16(b), (lane - 4) & 0x3);
#endif
}

// Shim vmla_laneq_u16 for armv7.
template <int lane>
inline uint16x4_t VMlaLaneQU16(const uint16x4_t a, const uint16x4_t b,
                               const uint16x8_t c) {
#if defined(__aarch64__)
  return vmla_laneq_u16(a, b, c, lane);
#else
  if (lane < 4) return vmla_lane_u16(a, b, vget_low_u16(c), lane & 0x3);
  return vmla_lane_u16(a, b, vget_high_u16(c), (lane - 4) & 0x3);
#endif
}

// Shim vmlaq_laneq_u16 for armv7.
template <int lane>
inline uint16x8_t VMlaQLaneQU16(const uint16x8_t a, const uint16x8_t b,
                                const uint16x8_t c) {
#if defined(__aarch64__)
  return vmlaq_laneq_u16(a, b, c, lane);
#else
  if (lane < 4) return vmlaq_lane_u16(a, b, vget_low_u16(c), lane & 0x3);
  return vmlaq_lane_u16(a, b, vget_high_u16(c), (lane - 4) & 0x3);
#endif
}

//------------------------------------------------------------------------------
// Bit manipulation.

// vshXX_n_XX() requires an immediate.
template <int shift>
inline uint8x8_t LeftShiftVector(const uint8x8_t vector) {
  return vreinterpret_u8_u64(vshl_n_u64(vreinterpret_u64_u8(vector), shift));
}

template <int shift>
inline uint8x8_t RightShiftVector(const uint8x8_t vector) {
  return vreinterpret_u8_u64(vshr_n_u64(vreinterpret_u64_u8(vector), shift));
}

template <int shift>
inline int8x8_t RightShiftVector(const int8x8_t vector) {
  return vreinterpret_s8_u64(vshr_n_u64(vreinterpret_u64_s8(vector), shift));
}

// Shim vqtbl1_u8 for armv7.
inline uint8x8_t VQTbl1U8(const uint8x16_t a, const uint8x8_t index) {
#if defined(__aarch64__)
  return vqtbl1_u8(a, index);
#else
  const uint8x8x2_t b = {vget_low_u8(a), vget_high_u8(a)};
  return vtbl2_u8(b, index);
#endif
}

// Shim vqtbl2_u8 for armv7.
inline uint8x8_t VQTbl2U8(const uint8x16x2_t a, const uint8x8_t index) {
#if defined(__aarch64__)
  return vqtbl2_u8(a, index);
#else
  const uint8x8x4_t b = {vget_low_u8(a.val[0]), vget_high_u8(a.val[0]),
                         vget_low_u8(a.val[1]), vget_high_u8(a.val[1])};
  return vtbl4_u8(b, index);
#endif
}

// Shim vqtbl2q_u8 for armv7.
inline uint8x16_t VQTbl2QU8(const uint8x16x2_t a, const uint8x16_t index) {
#if defined(__aarch64__)
  return vqtbl2q_u8(a, index);
#else
  return vcombine_u8(VQTbl2U8(a, vget_low_u8(index)),
                     VQTbl2U8(a, vget_high_u8(index)));
#endif
}

// Shim vqtbl3q_u8 for armv7.
inline uint8x8_t VQTbl3U8(const uint8x16x3_t a, const uint8x8_t index) {
#if defined(__aarch64__)
  return vqtbl3_u8(a, index);
#else
  const uint8x8x4_t b = {vget_low_u8(a.val[0]), vget_high_u8(a.val[0]),
                         vget_low_u8(a.val[1]), vget_high_u8(a.val[1])};
  const uint8x8x2_t c = {vget_low_u8(a.val[2]), vget_high_u8(a.val[2])};
  const uint8x8_t index_ext = vsub_u8(index, vdup_n_u8(32));
  const uint8x8_t partial_lookup = vtbl4_u8(b, index);
  return vtbx2_u8(partial_lookup, c, index_ext);
#endif
}

// Shim vqtbl3q_u8 for armv7.
inline uint8x16_t VQTbl3QU8(const uint8x16x3_t a, const uint8x16_t index) {
#if defined(__aarch64__)
  return vqtbl3q_u8(a, index);
#else
  return vcombine_u8(VQTbl3U8(a, vget_low_u8(index)),
                     VQTbl3U8(a, vget_high_u8(index)));
#endif
}

// Shim vqtbl1_s8 for armv7.
inline int8x8_t VQTbl1S8(const int8x16_t a, const uint8x8_t index) {
#if defined(__aarch64__)
  return vqtbl1_s8(a, index);
#else
  const int8x8x2_t b = {vget_low_s8(a), vget_high_s8(a)};
  return vtbl2_s8(b, vreinterpret_s8_u8(index));
#endif
}

//------------------------------------------------------------------------------
// Saturation helpers.

inline int16x4_t Clip3S16(const int16x4_t val, const int16x4_t low,
                          const int16x4_t high) {
  return vmin_s16(vmax_s16(val, low), high);
}

inline int16x8_t Clip3S16(const int16x8_t val, const int16x8_t low,
                          const int16x8_t high) {
  return vminq_s16(vmaxq_s16(val, low), high);
}

inline uint16x8_t ConvertToUnsignedPixelU16(const int16x8_t val, int bitdepth) {
  const int16x8_t low = vdupq_n_s16(0);
  const uint16x8_t high = vdupq_n_u16((1 << bitdepth) - 1);

  return vminq_u16(vreinterpretq_u16_s16(vmaxq_s16(val, low)), high);
}

//------------------------------------------------------------------------------
// Interleave.

// vzipN is exclusive to A64.
inline uint8x8_t InterleaveLow8(const uint8x8_t a, const uint8x8_t b) {
#if defined(__aarch64__)
  return vzip1_u8(a, b);
#else
  // Discard |.val[1]|
  return vzip_u8(a, b).val[0];
#endif
}

inline uint8x8_t InterleaveLow32(const uint8x8_t a, const uint8x8_t b) {
#if defined(__aarch64__)
  return vreinterpret_u8_u32(
      vzip1_u32(vreinterpret_u32_u8(a), vreinterpret_u32_u8(b)));
#else
  // Discard |.val[1]|
  return vreinterpret_u8_u32(
      vzip_u32(vreinterpret_u32_u8(a), vreinterpret_u32_u8(b)).val[0]);
#endif
}

inline int8x8_t InterleaveLow32(const int8x8_t a, const int8x8_t b) {
#if defined(__aarch64__)
  return vreinterpret_s8_u32(
      vzip1_u32(vreinterpret_u32_s8(a), vreinterpret_u32_s8(b)));
#else
  // Discard |.val[1]|
  return vreinterpret_s8_u32(
      vzip_u32(vreinterpret_u32_s8(a), vreinterpret_u32_s8(b)).val[0]);
#endif
}

inline uint8x8_t InterleaveHigh32(const uint8x8_t a, const uint8x8_t b) {
#if defined(__aarch64__)
  return vreinterpret_u8_u32(
      vzip2_u32(vreinterpret_u32_u8(a), vreinterpret_u32_u8(b)));
#else
  // Discard |.val[0]|
  return vreinterpret_u8_u32(
      vzip_u32(vreinterpret_u32_u8(a), vreinterpret_u32_u8(b)).val[1]);
#endif
}

inline int8x8_t InterleaveHigh32(const int8x8_t a, const int8x8_t b) {
#if defined(__aarch64__)
  return vreinterpret_s8_u32(
      vzip2_u32(vreinterpret_u32_s8(a), vreinterpret_u32_s8(b)));
#else
  // Discard |.val[0]|
  return vreinterpret_s8_u32(
      vzip_u32(vreinterpret_u32_s8(a), vreinterpret_u32_s8(b)).val[1]);
#endif
}

//------------------------------------------------------------------------------
// Sum.

inline uint16_t SumVector(const uint8x8_t a) {
#if defined(__aarch64__)
  return vaddlv_u8(a);
#else
  const uint16x4_t c = vpaddl_u8(a);
  const uint32x2_t d = vpaddl_u16(c);
  const uint64x1_t e = vpaddl_u32(d);
  return static_cast<uint16_t>(vget_lane_u64(e, 0));
#endif  // defined(__aarch64__)
}

inline uint32_t SumVector(const uint32x2_t a) {
#if defined(__aarch64__)
  return vaddv_u32(a);
#else
  const uint64x1_t b = vpaddl_u32(a);
  return vget_lane_u32(vreinterpret_u32_u64(b), 0);
#endif  // defined(__aarch64__)
}

inline uint32_t SumVector(const uint32x4_t a) {
#if defined(__aarch64__)
  return vaddvq_u32(a);
#else
  const uint64x2_t b = vpaddlq_u32(a);
  const uint64x1_t c = vadd_u64(vget_low_u64(b), vget_high_u64(b));
  return static_cast<uint32_t>(vget_lane_u64(c, 0));
#endif
}

//------------------------------------------------------------------------------
// Transpose.

// Transpose 32 bit elements such that:
// a: 00 01
// b: 02 03
// returns
// val[0]: 00 02
// val[1]: 01 03
inline uint8x8x2_t Interleave32(const uint8x8_t a, const uint8x8_t b) {
  const uint32x2_t a_32 = vreinterpret_u32_u8(a);
  const uint32x2_t b_32 = vreinterpret_u32_u8(b);
  const uint32x2x2_t c = vtrn_u32(a_32, b_32);
  const uint8x8x2_t d = {vreinterpret_u8_u32(c.val[0]),
                         vreinterpret_u8_u32(c.val[1])};
  return d;
}

// Swap high and low 32 bit elements.
inline uint8x8_t Transpose32(const uint8x8_t a) {
  const uint32x2_t b = vrev64_u32(vreinterpret_u32_u8(a));
  return vreinterpret_u8_u32(b);
}

// Swap high and low halves.
inline uint16x8_t Transpose64(const uint16x8_t a) { return vextq_u16(a, a, 4); }

// Implement vtrnq_s64().
// Input:
// a0: 00 01 02 03 04 05 06 07
// a1: 16 17 18 19 20 21 22 23
// Output:
// b0.val[0]: 00 01 02 03 16 17 18 19
// b0.val[1]: 04 05 06 07 20 21 22 23
inline int16x8x2_t VtrnqS64(const int32x4_t a0, const int32x4_t a1) {
  int16x8x2_t b0;
  b0.val[0] = vcombine_s16(vreinterpret_s16_s32(vget_low_s32(a0)),
                           vreinterpret_s16_s32(vget_low_s32(a1)));
  b0.val[1] = vcombine_s16(vreinterpret_s16_s32(vget_high_s32(a0)),
                           vreinterpret_s16_s32(vget_high_s32(a1)));
  return b0;
}

inline uint16x8x2_t VtrnqU64(const uint32x4_t a0, const uint32x4_t a1) {
  uint16x8x2_t b0;
  b0.val[0] = vcombine_u16(vreinterpret_u16_u32(vget_low_u32(a0)),
                           vreinterpret_u16_u32(vget_low_u32(a1)));
  b0.val[1] = vcombine_u16(vreinterpret_u16_u32(vget_high_u32(a0)),
                           vreinterpret_u16_u32(vget_high_u32(a1)));
  return b0;
}

// Input:
// 00 01 02 03
// 10 11 12 13
// 20 21 22 23
// 30 31 32 33
// Output:
// 00 10 20 30
// 01 11 21 31
// 02 12 22 32
// 03 13 23 33
inline void Transpose4x4(uint16x4_t a[4]) {
  // b:
  // 00 10 02 12
  // 01 11 03 13
  const uint16x4x2_t b = vtrn_u16(a[0], a[1]);
  // c:
  // 20 30 22 32
  // 21 31 23 33
  const uint16x4x2_t c = vtrn_u16(a[2], a[3]);
  // d:
  // 00 10 20 30
  // 02 12 22 32
  const uint32x2x2_t d =
      vtrn_u32(vreinterpret_u32_u16(b.val[0]), vreinterpret_u32_u16(c.val[0]));
  // e:
  // 01 11 21 31
  // 03 13 23 33
  const uint32x2x2_t e =
      vtrn_u32(vreinterpret_u32_u16(b.val[1]), vreinterpret_u32_u16(c.val[1]));
  a[0] = vreinterpret_u16_u32(d.val[0]);
  a[1] = vreinterpret_u16_u32(e.val[0]);
  a[2] = vreinterpret_u16_u32(d.val[1]);
  a[3] = vreinterpret_u16_u32(e.val[1]);
}

// Input:
// a: 00 01 02 03 10 11 12 13
// b: 20 21 22 23 30 31 32 33
// Output:
// Note that columns [1] and [2] are transposed.
// a: 00 10 20 30 02 12 22 32
// b: 01 11 21 31 03 13 23 33
inline void Transpose4x4(uint8x8_t* a, uint8x8_t* b) {
  const uint16x4x2_t c =
      vtrn_u16(vreinterpret_u16_u8(*a), vreinterpret_u16_u8(*b));
  const uint32x2x2_t d =
      vtrn_u32(vreinterpret_u32_u16(c.val[0]), vreinterpret_u32_u16(c.val[1]));
  const uint8x8x2_t e =
      vtrn_u8(vreinterpret_u8_u32(d.val[0]), vreinterpret_u8_u32(d.val[1]));
  *a = e.val[0];
  *b = e.val[1];
}

// 4x8 Input:
// a[0]: 00 01 02 03 04 05 06 07
// a[1]: 10 11 12 13 14 15 16 17
// a[2]: 20 21 22 23 24 25 26 27
// a[3]: 30 31 32 33 34 35 36 37
// 8x4 Output:
// a[0]: 00 10 20 30 04 14 24 34
// a[1]: 01 11 21 31 05 15 25 35
// a[2]: 02 12 22 32 06 16 26 36
// a[3]: 03 13 23 33 07 17 27 37
inline void Transpose4x8(uint16x8_t a[4]) {
  // b0.val[0]: 00 10 02 12 04 14 06 16
  // b0.val[1]: 01 11 03 13 05 15 07 17
  // b1.val[0]: 20 30 22 32 24 34 26 36
  // b1.val[1]: 21 31 23 33 25 35 27 37
  const uint16x8x2_t b0 = vtrnq_u16(a[0], a[1]);
  const uint16x8x2_t b1 = vtrnq_u16(a[2], a[3]);

  // c0.val[0]: 00 10 20 30 04 14 24 34
  // c0.val[1]: 02 12 22 32 06 16 26 36
  // c1.val[0]: 01 11 21 31 05 15 25 35
  // c1.val[1]: 03 13 23 33 07 17 27 37
  const uint32x4x2_t c0 = vtrnq_u32(vreinterpretq_u32_u16(b0.val[0]),
                                    vreinterpretq_u32_u16(b1.val[0]));
  const uint32x4x2_t c1 = vtrnq_u32(vreinterpretq_u32_u16(b0.val[1]),
                                    vreinterpretq_u32_u16(b1.val[1]));

  a[0] = vreinterpretq_u16_u32(c0.val[0]);
  a[1] = vreinterpretq_u16_u32(c1.val[0]);
  a[2] = vreinterpretq_u16_u32(c0.val[1]);
  a[3] = vreinterpretq_u16_u32(c1.val[1]);
}

// Special transpose for loop filter.
// 4x8 Input:
// p_q:  p3 p2 p1 p0 q0 q1 q2 q3
// a[0]: 00 01 02 03 04 05 06 07
// a[1]: 10 11 12 13 14 15 16 17
// a[2]: 20 21 22 23 24 25 26 27
// a[3]: 30 31 32 33 34 35 36 37
// 8x4 Output:
// a[0]: 03 13 23 33 04 14 24 34  p0q0
// a[1]: 02 12 22 32 05 15 25 35  p1q1
// a[2]: 01 11 21 31 06 16 26 36  p2q2
// a[3]: 00 10 20 30 07 17 27 37  p3q3
// Direct reapplication of the function will reset the high halves, but
// reverse the low halves:
// p_q:  p0 p1 p2 p3 q0 q1 q2 q3
// a[0]: 33 32 31 30 04 05 06 07
// a[1]: 23 22 21 20 14 15 16 17
// a[2]: 13 12 11 10 24 25 26 27
// a[3]: 03 02 01 00 34 35 36 37
// Simply reordering the inputs (3, 2, 1, 0) will reset the low halves, but
// reverse the high halves.
// The standard Transpose4x8 will produce the same reversals, but with the
// order of the low halves also restored relative to the high halves. This is
// preferable because it puts all values from the same source row back together,
// but some post-processing is inevitable.
inline void LoopFilterTranspose4x8(uint16x8_t a[4]) {
  // b0.val[0]: 00 10 02 12 04 14 06 16
  // b0.val[1]: 01 11 03 13 05 15 07 17
  // b1.val[0]: 20 30 22 32 24 34 26 36
  // b1.val[1]: 21 31 23 33 25 35 27 37
  const uint16x8x2_t b0 = vtrnq_u16(a[0], a[1]);
  const uint16x8x2_t b1 = vtrnq_u16(a[2], a[3]);

  // Reverse odd vectors to bring the appropriate items to the front of zips.
  // b0.val[0]: 00 10 02 12 04 14 06 16
  // r0       : 03 13 01 11 07 17 05 15
  // b1.val[0]: 20 30 22 32 24 34 26 36
  // r1       : 23 33 21 31 27 37 25 35
  const uint32x4_t r0 = vrev64q_u32(vreinterpretq_u32_u16(b0.val[1]));
  const uint32x4_t r1 = vrev64q_u32(vreinterpretq_u32_u16(b1.val[1]));

  // Zip to complete the halves.
  // c0.val[0]: 00 10 20 30 02 12 22 32  p3p1
  // c0.val[1]: 04 14 24 34 06 16 26 36  q0q2
  // c1.val[0]: 03 13 23 33 01 11 21 31  p0p2
  // c1.val[1]: 07 17 27 37 05 15 25 35  q3q1
  const uint32x4x2_t c0 = vzipq_u32(vreinterpretq_u32_u16(b0.val[0]),
                                    vreinterpretq_u32_u16(b1.val[0]));
  const uint32x4x2_t c1 = vzipq_u32(r0, r1);

  // d0.val[0]: 00 10 20 30 07 17 27 37  p3q3
  // d0.val[1]: 02 12 22 32 05 15 25 35  p1q1
  // d1.val[0]: 03 13 23 33 04 14 24 34  p0q0
  // d1.val[1]: 01 11 21 31 06 16 26 36  p2q2
  const uint16x8x2_t d0 = VtrnqU64(c0.val[0], c1.val[1]);
  // The third row of c comes first here to swap p2 with q0.
  const uint16x8x2_t d1 = VtrnqU64(c1.val[0], c0.val[1]);

  // 8x4 Output:
  // a[0]: 03 13 23 33 04 14 24 34  p0q0
  // a[1]: 02 12 22 32 05 15 25 35  p1q1
  // a[2]: 01 11 21 31 06 16 26 36  p2q2
  // a[3]: 00 10 20 30 07 17 27 37  p3q3
  a[0] = d1.val[0];  // p0q0
  a[1] = d0.val[1];  // p1q1
  a[2] = d1.val[1];  // p2q2
  a[3] = d0.val[0];  // p3q3
}

// Reversible if the x4 values are packed next to each other.
// x4 input / x8 output:
// a0: 00 01 02 03 40 41 42 43 44
// a1: 10 11 12 13 50 51 52 53 54
// a2: 20 21 22 23 60 61 62 63 64
// a3: 30 31 32 33 70 71 72 73 74
// x8 input / x4 output:
// a0: 00 10 20 30 40 50 60 70
// a1: 01 11 21 31 41 51 61 71
// a2: 02 12 22 32 42 52 62 72
// a3: 03 13 23 33 43 53 63 73
inline void Transpose8x4(uint8x8_t* a0, uint8x8_t* a1, uint8x8_t* a2,
                         uint8x8_t* a3) {
  const uint8x8x2_t b0 = vtrn_u8(*a0, *a1);
  const uint8x8x2_t b1 = vtrn_u8(*a2, *a3);

  const uint16x4x2_t c0 =
      vtrn_u16(vreinterpret_u16_u8(b0.val[0]), vreinterpret_u16_u8(b1.val[0]));
  const uint16x4x2_t c1 =
      vtrn_u16(vreinterpret_u16_u8(b0.val[1]), vreinterpret_u16_u8(b1.val[1]));

  *a0 = vreinterpret_u8_u16(c0.val[0]);
  *a1 = vreinterpret_u8_u16(c1.val[0]);
  *a2 = vreinterpret_u8_u16(c0.val[1]);
  *a3 = vreinterpret_u8_u16(c1.val[1]);
}

// Input:
// a[0]: 00 01 02 03 04 05 06 07
// a[1]: 10 11 12 13 14 15 16 17
// a[2]: 20 21 22 23 24 25 26 27
// a[3]: 30 31 32 33 34 35 36 37
// a[4]: 40 41 42 43 44 45 46 47
// a[5]: 50 51 52 53 54 55 56 57
// a[6]: 60 61 62 63 64 65 66 67
// a[7]: 70 71 72 73 74 75 76 77

// Output:
// a[0]: 00 10 20 30 40 50 60 70
// a[1]: 01 11 21 31 41 51 61 71
// a[2]: 02 12 22 32 42 52 62 72
// a[3]: 03 13 23 33 43 53 63 73
// a[4]: 04 14 24 34 44 54 64 74
// a[5]: 05 15 25 35 45 55 65 75
// a[6]: 06 16 26 36 46 56 66 76
// a[7]: 07 17 27 37 47 57 67 77
inline void Transpose8x8(int8x8_t a[8]) {
  // Swap 8 bit elements. Goes from:
  // a[0]: 00 01 02 03 04 05 06 07
  // a[1]: 10 11 12 13 14 15 16 17
  // a[2]: 20 21 22 23 24 25 26 27
  // a[3]: 30 31 32 33 34 35 36 37
  // a[4]: 40 41 42 43 44 45 46 47
  // a[5]: 50 51 52 53 54 55 56 57
  // a[6]: 60 61 62 63 64 65 66 67
  // a[7]: 70 71 72 73 74 75 76 77
  // to:
  // b0.val[0]: 00 10 02 12 04 14 06 16  40 50 42 52 44 54 46 56
  // b0.val[1]: 01 11 03 13 05 15 07 17  41 51 43 53 45 55 47 57
  // b1.val[0]: 20 30 22 32 24 34 26 36  60 70 62 72 64 74 66 76
  // b1.val[1]: 21 31 23 33 25 35 27 37  61 71 63 73 65 75 67 77
  const int8x16x2_t b0 =
      vtrnq_s8(vcombine_s8(a[0], a[4]), vcombine_s8(a[1], a[5]));
  const int8x16x2_t b1 =
      vtrnq_s8(vcombine_s8(a[2], a[6]), vcombine_s8(a[3], a[7]));

  // Swap 16 bit elements resulting in:
  // c0.val[0]: 00 10 20 30 04 14 24 34  40 50 60 70 44 54 64 74
  // c0.val[1]: 02 12 22 32 06 16 26 36  42 52 62 72 46 56 66 76
  // c1.val[0]: 01 11 21 31 05 15 25 35  41 51 61 71 45 55 65 75
  // c1.val[1]: 03 13 23 33 07 17 27 37  43 53 63 73 47 57 67 77
  const int16x8x2_t c0 = vtrnq_s16(vreinterpretq_s16_s8(b0.val[0]),
                                   vreinterpretq_s16_s8(b1.val[0]));
  const int16x8x2_t c1 = vtrnq_s16(vreinterpretq_s16_s8(b0.val[1]),
                                   vreinterpretq_s16_s8(b1.val[1]));

  // Unzip 32 bit elements resulting in:
  // d0.val[0]: 00 10 20 30 40 50 60 70  01 11 21 31 41 51 61 71
  // d0.val[1]: 04 14 24 34 44 54 64 74  05 15 25 35 45 55 65 75
  // d1.val[0]: 02 12 22 32 42 52 62 72  03 13 23 33 43 53 63 73
  // d1.val[1]: 06 16 26 36 46 56 66 76  07 17 27 37 47 57 67 77
  const int32x4x2_t d0 = vuzpq_s32(vreinterpretq_s32_s16(c0.val[0]),
                                   vreinterpretq_s32_s16(c1.val[0]));
  const int32x4x2_t d1 = vuzpq_s32(vreinterpretq_s32_s16(c0.val[1]),
                                   vreinterpretq_s32_s16(c1.val[1]));

  a[0] = vreinterpret_s8_s32(vget_low_s32(d0.val[0]));
  a[1] = vreinterpret_s8_s32(vget_high_s32(d0.val[0]));
  a[2] = vreinterpret_s8_s32(vget_low_s32(d1.val[0]));
  a[3] = vreinterpret_s8_s32(vget_high_s32(d1.val[0]));
  a[4] = vreinterpret_s8_s32(vget_low_s32(d0.val[1]));
  a[5] = vreinterpret_s8_s32(vget_high_s32(d0.val[1]));
  a[6] = vreinterpret_s8_s32(vget_low_s32(d1.val[1]));
  a[7] = vreinterpret_s8_s32(vget_high_s32(d1.val[1]));
}

// Unsigned.
inline void Transpose8x8(uint8x8_t a[8]) {
  const uint8x16x2_t b0 =
      vtrnq_u8(vcombine_u8(a[0], a[4]), vcombine_u8(a[1], a[5]));
  const uint8x16x2_t b1 =
      vtrnq_u8(vcombine_u8(a[2], a[6]), vcombine_u8(a[3], a[7]));

  const uint16x8x2_t c0 = vtrnq_u16(vreinterpretq_u16_u8(b0.val[0]),
                                    vreinterpretq_u16_u8(b1.val[0]));
  const uint16x8x2_t c1 = vtrnq_u16(vreinterpretq_u16_u8(b0.val[1]),
                                    vreinterpretq_u16_u8(b1.val[1]));

  const uint32x4x2_t d0 = vuzpq_u32(vreinterpretq_u32_u16(c0.val[0]),
                                    vreinterpretq_u32_u16(c1.val[0]));
  const uint32x4x2_t d1 = vuzpq_u32(vreinterpretq_u32_u16(c0.val[1]),
                                    vreinterpretq_u32_u16(c1.val[1]));

  a[0] = vreinterpret_u8_u32(vget_low_u32(d0.val[0]));
  a[1] = vreinterpret_u8_u32(vget_high_u32(d0.val[0]));
  a[2] = vreinterpret_u8_u32(vget_low_u32(d1.val[0]));
  a[3] = vreinterpret_u8_u32(vget_high_u32(d1.val[0]));
  a[4] = vreinterpret_u8_u32(vget_low_u32(d0.val[1]));
  a[5] = vreinterpret_u8_u32(vget_high_u32(d0.val[1]));
  a[6] = vreinterpret_u8_u32(vget_low_u32(d1.val[1]));
  a[7] = vreinterpret_u8_u32(vget_high_u32(d1.val[1]));
}

inline void Transpose8x8(uint8x8_t in[8], uint8x16_t out[4]) {
  const uint8x16x2_t a0 =
      vtrnq_u8(vcombine_u8(in[0], in[4]), vcombine_u8(in[1], in[5]));
  const uint8x16x2_t a1 =
      vtrnq_u8(vcombine_u8(in[2], in[6]), vcombine_u8(in[3], in[7]));

  const uint16x8x2_t b0 = vtrnq_u16(vreinterpretq_u16_u8(a0.val[0]),
                                    vreinterpretq_u16_u8(a1.val[0]));
  const uint16x8x2_t b1 = vtrnq_u16(vreinterpretq_u16_u8(a0.val[1]),
                                    vreinterpretq_u16_u8(a1.val[1]));

  const uint32x4x2_t c0 = vuzpq_u32(vreinterpretq_u32_u16(b0.val[0]),
                                    vreinterpretq_u32_u16(b1.val[0]));
  const uint32x4x2_t c1 = vuzpq_u32(vreinterpretq_u32_u16(b0.val[1]),
                                    vreinterpretq_u32_u16(b1.val[1]));

  out[0] = vreinterpretq_u8_u32(c0.val[0]);
  out[1] = vreinterpretq_u8_u32(c1.val[0]);
  out[2] = vreinterpretq_u8_u32(c0.val[1]);
  out[3] = vreinterpretq_u8_u32(c1.val[1]);
}

// Input:
// a[0]: 00 01 02 03 04 05 06 07
// a[1]: 10 11 12 13 14 15 16 17
// a[2]: 20 21 22 23 24 25 26 27
// a[3]: 30 31 32 33 34 35 36 37
// a[4]: 40 41 42 43 44 45 46 47
// a[5]: 50 51 52 53 54 55 56 57
// a[6]: 60 61 62 63 64 65 66 67
// a[7]: 70 71 72 73 74 75 76 77

// Output:
// a[0]: 00 10 20 30 40 50 60 70
// a[1]: 01 11 21 31 41 51 61 71
// a[2]: 02 12 22 32 42 52 62 72
// a[3]: 03 13 23 33 43 53 63 73
// a[4]: 04 14 24 34 44 54 64 74
// a[5]: 05 15 25 35 45 55 65 75
// a[6]: 06 16 26 36 46 56 66 76
// a[7]: 07 17 27 37 47 57 67 77
inline void Transpose8x8(int16x8_t a[8]) {
  const int16x8x2_t b0 = vtrnq_s16(a[0], a[1]);
  const int16x8x2_t b1 = vtrnq_s16(a[2], a[3]);
  const int16x8x2_t b2 = vtrnq_s16(a[4], a[5]);
  const int16x8x2_t b3 = vtrnq_s16(a[6], a[7]);

  const int32x4x2_t c0 = vtrnq_s32(vreinterpretq_s32_s16(b0.val[0]),
                                   vreinterpretq_s32_s16(b1.val[0]));
  const int32x4x2_t c1 = vtrnq_s32(vreinterpretq_s32_s16(b0.val[1]),
                                   vreinterpretq_s32_s16(b1.val[1]));
  const int32x4x2_t c2 = vtrnq_s32(vreinterpretq_s32_s16(b2.val[0]),
                                   vreinterpretq_s32_s16(b3.val[0]));
  const int32x4x2_t c3 = vtrnq_s32(vreinterpretq_s32_s16(b2.val[1]),
                                   vreinterpretq_s32_s16(b3.val[1]));

  const int16x8x2_t d0 = VtrnqS64(c0.val[0], c2.val[0]);
  const int16x8x2_t d1 = VtrnqS64(c1.val[0], c3.val[0]);
  const int16x8x2_t d2 = VtrnqS64(c0.val[1], c2.val[1]);
  const int16x8x2_t d3 = VtrnqS64(c1.val[1], c3.val[1]);

  a[0] = d0.val[0];
  a[1] = d1.val[0];
  a[2] = d2.val[0];
  a[3] = d3.val[0];
  a[4] = d0.val[1];
  a[5] = d1.val[1];
  a[6] = d2.val[1];
  a[7] = d3.val[1];
}

// Unsigned.
inline void Transpose8x8(uint16x8_t a[8]) {
  const uint16x8x2_t b0 = vtrnq_u16(a[0], a[1]);
  const uint16x8x2_t b1 = vtrnq_u16(a[2], a[3]);
  const uint16x8x2_t b2 = vtrnq_u16(a[4], a[5]);
  const uint16x8x2_t b3 = vtrnq_u16(a[6], a[7]);

  const uint32x4x2_t c0 = vtrnq_u32(vreinterpretq_u32_u16(b0.val[0]),
                                    vreinterpretq_u32_u16(b1.val[0]));
  const uint32x4x2_t c1 = vtrnq_u32(vreinterpretq_u32_u16(b0.val[1]),
                                    vreinterpretq_u32_u16(b1.val[1]));
  const uint32x4x2_t c2 = vtrnq_u32(vreinterpretq_u32_u16(b2.val[0]),
                                    vreinterpretq_u32_u16(b3.val[0]));
  const uint32x4x2_t c3 = vtrnq_u32(vreinterpretq_u32_u16(b2.val[1]),
                                    vreinterpretq_u32_u16(b3.val[1]));

  const uint16x8x2_t d0 = VtrnqU64(c0.val[0], c2.val[0]);
  const uint16x8x2_t d1 = VtrnqU64(c1.val[0], c3.val[0]);
  const uint16x8x2_t d2 = VtrnqU64(c0.val[1], c2.val[1]);
  const uint16x8x2_t d3 = VtrnqU64(c1.val[1], c3.val[1]);

  a[0] = d0.val[0];
  a[1] = d1.val[0];
  a[2] = d2.val[0];
  a[3] = d3.val[0];
  a[4] = d0.val[1];
  a[5] = d1.val[1];
  a[6] = d2.val[1];
  a[7] = d3.val[1];
}

// Input:
// a[0]: 00 01 02 03 04 05 06 07  80 81 82 83 84 85 86 87
// a[1]: 10 11 12 13 14 15 16 17  90 91 92 93 94 95 96 97
// a[2]: 20 21 22 23 24 25 26 27  a0 a1 a2 a3 a4 a5 a6 a7
// a[3]: 30 31 32 33 34 35 36 37  b0 b1 b2 b3 b4 b5 b6 b7
// a[4]: 40 41 42 43 44 45 46 47  c0 c1 c2 c3 c4 c5 c6 c7
// a[5]: 50 51 52 53 54 55 56 57  d0 d1 d2 d3 d4 d5 d6 d7
// a[6]: 60 61 62 63 64 65 66 67  e0 e1 e2 e3 e4 e5 e6 e7
// a[7]: 70 71 72 73 74 75 76 77  f0 f1 f2 f3 f4 f5 f6 f7

// Output:
// a[0]: 00 10 20 30 40 50 60 70  80 90 a0 b0 c0 d0 e0 f0
// a[1]: 01 11 21 31 41 51 61 71  81 91 a1 b1 c1 d1 e1 f1
// a[2]: 02 12 22 32 42 52 62 72  82 92 a2 b2 c2 d2 e2 f2
// a[3]: 03 13 23 33 43 53 63 73  83 93 a3 b3 c3 d3 e3 f3
// a[4]: 04 14 24 34 44 54 64 74  84 94 a4 b4 c4 d4 e4 f4
// a[5]: 05 15 25 35 45 55 65 75  85 95 a5 b5 c5 d5 e5 f5
// a[6]: 06 16 26 36 46 56 66 76  86 96 a6 b6 c6 d6 e6 f6
// a[7]: 07 17 27 37 47 57 67 77  87 97 a7 b7 c7 d7 e7 f7
inline void Transpose8x16(uint8x16_t a[8]) {
  // b0.val[0]: 00 10 02 12 04 14 06 16  80 90 82 92 84 94 86 96
  // b0.val[1]: 01 11 03 13 05 15 07 17  81 91 83 93 85 95 87 97
  // b1.val[0]: 20 30 22 32 24 34 26 36  a0 b0 a2 b2 a4 b4 a6 b6
  // b1.val[1]: 21 31 23 33 25 35 27 37  a1 b1 a3 b3 a5 b5 a7 b7
  // b2.val[0]: 40 50 42 52 44 54 46 56  c0 d0 c2 d2 c4 d4 c6 d6
  // b2.val[1]: 41 51 43 53 45 55 47 57  c1 d1 c3 d3 c5 d5 c7 d7
  // b3.val[0]: 60 70 62 72 64 74 66 76  e0 f0 e2 f2 e4 f4 e6 f6
  // b3.val[1]: 61 71 63 73 65 75 67 77  e1 f1 e3 f3 e5 f5 e7 f7
  const uint8x16x2_t b0 = vtrnq_u8(a[0], a[1]);
  const uint8x16x2_t b1 = vtrnq_u8(a[2], a[3]);
  const uint8x16x2_t b2 = vtrnq_u8(a[4], a[5]);
  const uint8x16x2_t b3 = vtrnq_u8(a[6], a[7]);

  // c0.val[0]: 00 10 20 30 04 14 24 34  80 90 a0 b0 84 94 a4 b4
  // c0.val[1]: 02 12 22 32 06 16 26 36  82 92 a2 b2 86 96 a6 b6
  // c1.val[0]: 01 11 21 31 05 15 25 35  81 91 a1 b1 85 95 a5 b5
  // c1.val[1]: 03 13 23 33 07 17 27 37  83 93 a3 b3 87 97 a7 b7
  // c2.val[0]: 40 50 60 70 44 54 64 74  c0 d0 e0 f0 c4 d4 e4 f4
  // c2.val[1]: 42 52 62 72 46 56 66 76  c2 d2 e2 f2 c6 d6 e6 f6
  // c3.val[0]: 41 51 61 71 45 55 65 75  c1 d1 e1 f1 c5 d5 e5 f5
  // c3.val[1]: 43 53 63 73 47 57 67 77  c3 d3 e3 f3 c7 d7 e7 f7
  const uint16x8x2_t c0 = vtrnq_u16(vreinterpretq_u16_u8(b0.val[0]),
                                    vreinterpretq_u16_u8(b1.val[0]));
  const uint16x8x2_t c1 = vtrnq_u16(vreinterpretq_u16_u8(b0.val[1]),
                                    vreinterpretq_u16_u8(b1.val[1]));
  const uint16x8x2_t c2 = vtrnq_u16(vreinterpretq_u16_u8(b2.val[0]),
                                    vreinterpretq_u16_u8(b3.val[0]));
  const uint16x8x2_t c3 = vtrnq_u16(vreinterpretq_u16_u8(b2.val[1]),
                                    vreinterpretq_u16_u8(b3.val[1]));

  // d0.val[0]: 00 10 20 30 40 50 60 70  80 90 a0 b0 c0 d0 e0 f0
  // d0.val[1]: 04 14 24 34 44 54 64 74  84 94 a4 b4 c4 d4 e4 f4
  // d1.val[0]: 01 11 21 31 41 51 61 71  81 91 a1 b1 c1 d1 e1 f1
  // d1.val[1]: 05 15 25 35 45 55 65 75  85 95 a5 b5 c5 d5 e5 f5
  // d2.val[0]: 02 12 22 32 42 52 62 72  82 92 a2 b2 c2 d2 e2 f2
  // d2.val[1]: 06 16 26 36 46 56 66 76  86 96 a6 b6 c6 d6 e6 f6
  // d3.val[0]: 03 13 23 33 43 53 63 73  83 93 a3 b3 c3 d3 e3 f3
  // d3.val[1]: 07 17 27 37 47 57 67 77  87 97 a7 b7 c7 d7 e7 f7
  const uint32x4x2_t d0 = vtrnq_u32(vreinterpretq_u32_u16(c0.val[0]),
                                    vreinterpretq_u32_u16(c2.val[0]));
  const uint32x4x2_t d1 = vtrnq_u32(vreinterpretq_u32_u16(c1.val[0]),
                                    vreinterpretq_u32_u16(c3.val[0]));
  const uint32x4x2_t d2 = vtrnq_u32(vreinterpretq_u32_u16(c0.val[1]),
                                    vreinterpretq_u32_u16(c2.val[1]));
  const uint32x4x2_t d3 = vtrnq_u32(vreinterpretq_u32_u16(c1.val[1]),
                                    vreinterpretq_u32_u16(c3.val[1]));

  a[0] = vreinterpretq_u8_u32(d0.val[0]);
  a[1] = vreinterpretq_u8_u32(d1.val[0]);
  a[2] = vreinterpretq_u8_u32(d2.val[0]);
  a[3] = vreinterpretq_u8_u32(d3.val[0]);
  a[4] = vreinterpretq_u8_u32(d0.val[1]);
  a[5] = vreinterpretq_u8_u32(d1.val[1]);
  a[6] = vreinterpretq_u8_u32(d2.val[1]);
  a[7] = vreinterpretq_u8_u32(d3.val[1]);
}

inline int16x8_t ZeroExtend(const uint8x8_t in) {
  return vreinterpretq_s16_u16(vmovl_u8(in));
}

}  // namespace dsp
}  // namespace libgav1

#endif  // LIBGAV1_ENABLE_NEON
#endif  // LIBGAV1_SRC_DSP_ARM_COMMON_NEON_H_

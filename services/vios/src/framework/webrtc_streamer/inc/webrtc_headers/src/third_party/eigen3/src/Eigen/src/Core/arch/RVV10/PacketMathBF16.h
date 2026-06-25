// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Chip Kerchner <ckerchner@tenstorrent.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_PACKET_MATH_BF16_RVV10_H
#define EIGEN_PACKET_MATH_BF16_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

typedef eigen_packet_wrapper<vbfloat16m1_t __attribute__((riscv_rvv_vector_bits(EIGEN_RISCV64_RVV_VL))), 26> Packet1Xbf;
typedef eigen_packet_wrapper<vbfloat16m2_t __attribute__((riscv_rvv_vector_bits(EIGEN_RISCV64_RVV_VL * 2))), 27>
    Packet2Xbf;

template <>
struct rvv_half_packet<Packet2Xbf> {
  typedef Packet1Xbf type;
};

template <>
struct unpacket_traits<Packet1Xbf> : rvv_default_unpacket_traits<bfloat16, Packet1Xbf, 1> {
  typedef Packet1Xs integer_packet;
  typedef PacketMask16 packet_mask;
};

template <>
struct unpacket_traits<Packet2Xbf> : rvv_default_unpacket_traits<bfloat16, Packet2Xbf, 2> {
  typedef Packet2Xs integer_packet;
  typedef PacketMask8 packet_mask;
};

#if EIGEN_RISCV64_DEFAULT_LMUL == 1
typedef Packet1Xbf PacketXbf;
#else
typedef Packet2Xbf PacketXbf;
#endif

template <>
struct packet_traits<bfloat16> : rvv_default_packet_traits<bfloat16, PacketXbf> {
  enum {
    HasAdd = 1,
    HasSub = 1,
    HasShift = 1,
    HasMul = 1,
    HasNegate = 1,
    HasAbs = 1,
    HasArg = 0,
    HasAbs2 = 1,
    HasMin = 1,
    HasMax = 1,
    HasConj = 1,
    HasSetLinear = 0,
    HasBlend = 0,
    HasReduxp = 0,
    HasSign = 0,

    HasCmp = 1,
    HasDiv = 1,
    HasRound = 0,

    HasSin = 0,
    HasCos = 0,
    HasLog = 0,
    HasExp = 0,
    HasSqrt = 1,
    HasTanh = 0,
    HasErf = 0
  };
};

/********************************* Packet1Xbf ************************************/

EIGEN_STRONG_INLINE Packet1Xbf __riscv_vreinterpret_v_u32m1_bf16m1(const Packet1Xu& a) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vreinterpret_v_u32m1_u16m1(a));
}

EIGEN_STRONG_INLINE Packet2Xf Bf16ToF32(const Packet1Xbf& a) {
  return __riscv_vfwcvtbf16_f_f_v_f32m2(a, unpacket_traits<Packet1Xbf>::size);
}

EIGEN_STRONG_INLINE Packet1Xbf F32ToBf16(const Packet2Xf& a) {
  return __riscv_vfncvtbf16_f_f_w_bf16m1(a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf ptrue<Packet1Xbf>(const Packet1Xbf& /*a*/) {
  return __riscv_vreinterpret_bf16m1(
      __riscv_vmv_v_x_u16m1(static_cast<numext::uint16_t>(0xffffu), unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pzero<Packet1Xbf>(const Packet1Xbf& /*a*/) {
  return __riscv_vreinterpret_bf16m1(
      __riscv_vmv_v_x_i16m1(numext::bit_cast<int16_t>(static_cast<__bf16>(0.0)), unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pabs(const Packet1Xbf& a) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vand_vx_u16m1(__riscv_vreinterpret_v_bf16m1_u16m1(a),
                                                                   static_cast<numext::uint16_t>(0x7fffu),
                                                                   unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pset1<Packet1Xbf>(const bfloat16& from) {
  return __riscv_vreinterpret_bf16m1(
      __riscv_vmv_v_x_i16m1(numext::bit_cast<int16_t>(from), unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pset1frombits<Packet1Xbf>(numext::uint16_t from) {
  return __riscv_vreinterpret_bf16m1(__riscv_vmv_v_x_u16m1(from, unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf plset<Packet1Xbf>(const bfloat16& a) {
  return F32ToBf16(plset<Packet2Xf>(static_cast<float>(a)));
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet1Xbf>(const bfloat16* a, Packet1Xbf& a0, Packet1Xbf& a1, Packet1Xbf& a2,
                                                 Packet1Xbf& a3) {
  vint16m1_t aa = __riscv_vle16_v_i16m1(reinterpret_cast<const int16_t*>(a), 4);
  a0 = __riscv_vreinterpret_bf16m1(__riscv_vrgather_vx_i16m1(aa, 0, unpacket_traits<Packet1Xs>::size));
  a1 = __riscv_vreinterpret_bf16m1(__riscv_vrgather_vx_i16m1(aa, 1, unpacket_traits<Packet1Xs>::size));
  a2 = __riscv_vreinterpret_bf16m1(__riscv_vrgather_vx_i16m1(aa, 2, unpacket_traits<Packet1Xs>::size));
  a3 = __riscv_vreinterpret_bf16m1(__riscv_vrgather_vx_i16m1(aa, 3, unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf padd<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  // b + (1 * a)
  return F32ToBf16(__riscv_vfwmaccbf16_vf_f32m2(Bf16ToF32(b),
                                                numext::bit_cast<__bf16>(static_cast<numext::int16_t>(0x3f80u)), a,
                                                unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf psub<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  // a + (-1 * b)
  return F32ToBf16(__riscv_vfwmaccbf16_vf_f32m2(Bf16ToF32(a),
                                                numext::bit_cast<__bf16>(static_cast<numext::int16_t>(0xbf80u)), b,
                                                unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pabsdiff(const Packet1Xbf& a, const Packet1Xbf& b) {
  return pabs<Packet1Xbf>(psub<Packet1Xbf>(a, b));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pnegate(const Packet1Xbf& a) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vxor_vx_u16m1(__riscv_vreinterpret_v_bf16m1_u16m1(a),
                                                                   static_cast<numext::uint16_t>(0x8000u),
                                                                   unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf psignbit(const Packet1Xbf& a) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(
      __riscv_vsra_vx_i16m1(__riscv_vreinterpret_v_bf16m1_i16m1(a), 15, unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pconj(const Packet1Xbf& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmul<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  Packet2Xf c;
  return F32ToBf16(__riscv_vfwmaccbf16_vv_f32m2(pzero<Packet2Xf>(c), a, b, unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pdiv<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pdiv<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmadd(const Packet1Xbf& a, const Packet1Xbf& b, const Packet1Xbf& c) {
  return F32ToBf16(__riscv_vfwmaccbf16_vv_f32m2(Bf16ToF32(c), a, b, unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmsub(const Packet1Xbf& a, const Packet1Xbf& b, const Packet1Xbf& c) {
  return F32ToBf16(
      __riscv_vfwmaccbf16_vv_f32m2(Bf16ToF32(pnegate<Packet1Xbf>(c)), a, b, unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pnmadd(const Packet1Xbf& a, const Packet1Xbf& b, const Packet1Xbf& c) {
  return F32ToBf16(
      __riscv_vfwmaccbf16_vv_f32m2(Bf16ToF32(c), pnegate<Packet1Xbf>(a), b, unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pnmsub(const Packet1Xbf& a, const Packet1Xbf& b, const Packet1Xbf& c) {
  return pnegate<Packet1Xbf>(
      F32ToBf16(__riscv_vfwmaccbf16_vv_f32m2(Bf16ToF32(c), a, b, unpacket_traits<Packet1Xbf>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmin<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmin<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmin<PropagateNaN, Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmin<PropagateNaN, Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmin<PropagateNumbers, Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmin<PropagateNumbers, Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmax<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmax<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmax<PropagateNaN, Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmax<PropagateNaN, Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pmax<PropagateNumbers, Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pmax<PropagateNumbers, Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pcmp_le<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pcmp_le<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pcmp_lt<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pcmp_lt<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pcmp_eq<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pcmp_eq<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pcmp_lt_or_nan<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return F32ToBf16(pcmp_lt_or_nan<Packet2Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

EIGEN_STRONG_INLINE Packet1Xbf pselect(const PacketMask16& mask, const Packet1Xbf& a, const Packet1Xbf& b) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(__riscv_vmerge_vvm_i16m1(__riscv_vreinterpret_v_bf16m1_i16m1(b),
                                                                      __riscv_vreinterpret_v_bf16m1_i16m1(a), mask,
                                                                      unpacket_traits<Packet1Xbf>::size));
}

EIGEN_STRONG_INLINE Packet1Xbf pselect(const Packet1Xbf& mask, const Packet1Xbf& a, const Packet1Xbf& b) {
  PacketMask16 mask2 =
      __riscv_vmsne_vx_i16m1_b16(__riscv_vreinterpret_v_bf16m1_i16m1(mask), 0, unpacket_traits<Packet1Xbf>::size);
  return __riscv_vreinterpret_v_i16m1_bf16m1(__riscv_vmerge_vvm_i16m1(__riscv_vreinterpret_v_bf16m1_i16m1(b),
                                                                      __riscv_vreinterpret_v_bf16m1_i16m1(a), mask2,
                                                                      unpacket_traits<Packet1Xbf>::size));
}

// Logical Operations are not supported for bfloat16, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet1Xbf pand<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vand_vv_u16m1(__riscv_vreinterpret_v_bf16m1_u16m1(a),
                                                                   __riscv_vreinterpret_v_bf16m1_u16m1(b),
                                                                   unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf por<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vor_vv_u16m1(__riscv_vreinterpret_v_bf16m1_u16m1(a),
                                                                  __riscv_vreinterpret_v_bf16m1_u16m1(b),
                                                                  unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pxor<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return __riscv_vreinterpret_v_u16m1_bf16m1(__riscv_vxor_vv_u16m1(__riscv_vreinterpret_v_bf16m1_u16m1(a),
                                                                   __riscv_vreinterpret_v_bf16m1_u16m1(b),
                                                                   unpacket_traits<Packet1Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pandnot<Packet1Xbf>(const Packet1Xbf& a, const Packet1Xbf& b) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(
      pandnot<Packet1Xs>(__riscv_vreinterpret_v_bf16m1_i16m1(a), __riscv_vreinterpret_v_bf16m1_i16m1(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pload<Packet1Xbf>(const bfloat16* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_bf16m1(reinterpret_cast<const __bf16*>(from),
                                                         unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf ploadu<Packet1Xbf>(const bfloat16* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_bf16m1(reinterpret_cast<const __bf16*>(from),
                                                           unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf ploaddup<Packet1Xbf>(const bfloat16* from) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(ploaddup<Packet1Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf ploadquad<Packet1Xbf>(const bfloat16* from) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(ploadquad<Packet1Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE void pstore<bfloat16>(bfloat16* to, const Packet1Xbf& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_bf16m1(reinterpret_cast<__bf16*>(to), from,
                                                   unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<bfloat16>(bfloat16* to, const Packet1Xbf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_bf16m1(reinterpret_cast<__bf16*>(to), from,
                                                     unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xbf pgather<bfloat16, Packet1Xbf>(const bfloat16* from, Index stride) {
  return __riscv_vlse16_v_bf16m1(reinterpret_cast<const __bf16*>(from), stride * sizeof(bfloat16),
                                 unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<bfloat16, Packet1Xbf>(bfloat16* to, const Packet1Xbf& from, Index stride) {
  __riscv_vsse16(reinterpret_cast<__bf16*>(to), stride * sizeof(bfloat16), from, unpacket_traits<Packet1Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE bfloat16 pfirst<Packet1Xbf>(const Packet1Xbf& a) {
  return numext::bit_cast<bfloat16>(__riscv_vmv_x_s_i16m1_i16(__riscv_vreinterpret_v_bf16m1_i16m1(a)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf psqrt(const Packet1Xbf& a) {
  return F32ToBf16(psqrt<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf print<Packet1Xbf>(const Packet1Xbf& a) {
  return F32ToBf16(print<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pfloor<Packet1Xbf>(const Packet1Xbf& a) {
  return F32ToBf16(pfloor<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf preverse(const Packet1Xbf& a) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(preverse<Packet1Xs>(__riscv_vreinterpret_v_bf16m1_i16m1(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux<Packet1Xbf>(const Packet1Xbf& a) {
  return static_cast<bfloat16>(predux<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_mul<Packet1Xbf>(const Packet1Xbf& a) {
  return static_cast<bfloat16>(predux_mul<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_min<Packet1Xbf>(const Packet1Xbf& a) {
  return static_cast<bfloat16>(predux_min<Packet2Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_max<Packet1Xbf>(const Packet1Xbf& a) {
  return static_cast<bfloat16>(predux_max<Packet2Xf>(Bf16ToF32(a)));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xbf, N>& kernel) {
  bfloat16 buffer[unpacket_traits<Packet1Xbf>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(reinterpret_cast<__bf16*>(&buffer[i]), N * sizeof(bfloat16), kernel.packet[i],
                   unpacket_traits<Packet1Xbf>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = __riscv_vle16_v_bf16m1(reinterpret_cast<__bf16*>(&buffer[i * unpacket_traits<Packet1Xbf>::size]),
                                              unpacket_traits<Packet1Xbf>::size);
  }
}

/********************************* Packet2Xbf ************************************/

EIGEN_STRONG_INLINE Packet2Xbf __riscv_vreinterpret_v_u32m2_bf16m2(const Packet2Xu& a) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vreinterpret_v_u32m2_u16m2(a));
}

EIGEN_STRONG_INLINE Packet4Xf Bf16ToF32(const Packet2Xbf& a) {
  return __riscv_vfwcvtbf16_f_f_v_f32m4(a, unpacket_traits<Packet2Xbf>::size);
}

EIGEN_STRONG_INLINE Packet2Xbf F32ToBf16(const Packet4Xf& a) {
  return __riscv_vfncvtbf16_f_f_w_bf16m2(a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf ptrue<Packet2Xbf>(const Packet2Xbf& /*a*/) {
  return __riscv_vreinterpret_bf16m2(
      __riscv_vmv_v_x_u16m2(static_cast<numext::uint16_t>(0xffffu), unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pzero<Packet2Xbf>(const Packet2Xbf& /*a*/) {
  return __riscv_vreinterpret_bf16m2(
      __riscv_vmv_v_x_i16m2(numext::bit_cast<int16_t>(static_cast<__bf16>(0.0)), unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pabs(const Packet2Xbf& a) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vand_vx_u16m2(__riscv_vreinterpret_v_bf16m2_u16m2(a),
                                                                   static_cast<numext::uint16_t>(0x7fffu),
                                                                   unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pset1<Packet2Xbf>(const bfloat16& from) {
  return __riscv_vreinterpret_bf16m2(
      __riscv_vmv_v_x_i16m2(numext::bit_cast<int16_t>(from), unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pset1frombits<Packet2Xbf>(numext::uint16_t from) {
  return __riscv_vreinterpret_bf16m2(__riscv_vmv_v_x_u16m2(from, unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf plset<Packet2Xbf>(const bfloat16& a) {
  return F32ToBf16(plset<Packet4Xf>(static_cast<float>(a)));
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet2Xbf>(const bfloat16* a, Packet2Xbf& a0, Packet2Xbf& a1, Packet2Xbf& a2,
                                                 Packet2Xbf& a3) {
  vint16m2_t aa = __riscv_vle16_v_i16m2(reinterpret_cast<const int16_t*>(a), 4);
  a0 = __riscv_vreinterpret_bf16m2(__riscv_vrgather_vx_i16m2(aa, 0, unpacket_traits<Packet2Xs>::size));
  a1 = __riscv_vreinterpret_bf16m2(__riscv_vrgather_vx_i16m2(aa, 1, unpacket_traits<Packet2Xs>::size));
  a2 = __riscv_vreinterpret_bf16m2(__riscv_vrgather_vx_i16m2(aa, 2, unpacket_traits<Packet2Xs>::size));
  a3 = __riscv_vreinterpret_bf16m2(__riscv_vrgather_vx_i16m2(aa, 3, unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf padd<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  // b + (1 * a)
  return F32ToBf16(__riscv_vfwmaccbf16_vf_f32m4(Bf16ToF32(b),
                                                numext::bit_cast<__bf16>(static_cast<numext::int16_t>(0x3f80u)), a,
                                                unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf psub<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  // a + (-1 * b)
  return F32ToBf16(__riscv_vfwmaccbf16_vf_f32m4(Bf16ToF32(a),
                                                numext::bit_cast<__bf16>(static_cast<numext::int16_t>(0xbf80u)), b,
                                                unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pabsdiff(const Packet2Xbf& a, const Packet2Xbf& b) {
  return pabs<Packet2Xbf>(psub<Packet2Xbf>(a, b));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pnegate(const Packet2Xbf& a) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vxor_vx_u16m2(__riscv_vreinterpret_v_bf16m2_u16m2(a),
                                                                   static_cast<numext::uint16_t>(0x8000u),
                                                                   unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf psignbit(const Packet2Xbf& a) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(
      __riscv_vsra_vx_i16m2(__riscv_vreinterpret_v_bf16m2_i16m2(a), 15, unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pconj(const Packet2Xbf& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmul<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  Packet4Xf c;
  return F32ToBf16(__riscv_vfwmaccbf16_vv_f32m4(pzero<Packet4Xf>(c), a, b, unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pdiv<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pdiv<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmadd(const Packet2Xbf& a, const Packet2Xbf& b, const Packet2Xbf& c) {
  return F32ToBf16(__riscv_vfwmaccbf16_vv_f32m4(Bf16ToF32(c), a, b, unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmsub(const Packet2Xbf& a, const Packet2Xbf& b, const Packet2Xbf& c) {
  return F32ToBf16(
      __riscv_vfwmaccbf16_vv_f32m4(Bf16ToF32(pnegate<Packet2Xbf>(c)), a, b, unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pnmadd(const Packet2Xbf& a, const Packet2Xbf& b, const Packet2Xbf& c) {
  return F32ToBf16(
      __riscv_vfwmaccbf16_vv_f32m4(Bf16ToF32(c), pnegate<Packet2Xbf>(a), b, unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pnmsub(const Packet2Xbf& a, const Packet2Xbf& b, const Packet2Xbf& c) {
  return pnegate<Packet2Xbf>(
      F32ToBf16(__riscv_vfwmaccbf16_vv_f32m4(Bf16ToF32(c), a, b, unpacket_traits<Packet2Xbf>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmin<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmin<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmin<PropagateNaN, Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmin<PropagateNaN, Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmin<PropagateNumbers, Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmin<PropagateNumbers, Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmax<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmax<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmax<PropagateNaN, Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmax<PropagateNaN, Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pmax<PropagateNumbers, Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pmax<PropagateNumbers, Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pcmp_le<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pcmp_le<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pcmp_lt<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pcmp_lt<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pcmp_eq<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pcmp_eq<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pcmp_lt_or_nan<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return F32ToBf16(pcmp_lt_or_nan<Packet4Xf>(Bf16ToF32(a), Bf16ToF32(b)));
}

EIGEN_STRONG_INLINE Packet2Xbf pselect(const PacketMask8& mask, const Packet2Xbf& a, const Packet2Xbf& b) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(__riscv_vmerge_vvm_i16m2(__riscv_vreinterpret_v_bf16m2_i16m2(b),
                                                                      __riscv_vreinterpret_v_bf16m2_i16m2(a), mask,
                                                                      unpacket_traits<Packet2Xbf>::size));
}

EIGEN_STRONG_INLINE Packet2Xbf pselect(const Packet2Xbf& mask, const Packet2Xbf& a, const Packet2Xbf& b) {
  PacketMask8 mask2 =
      __riscv_vmsne_vx_i16m2_b8(__riscv_vreinterpret_v_bf16m2_i16m2(mask), 0, unpacket_traits<Packet2Xbf>::size);
  return __riscv_vreinterpret_v_i16m2_bf16m2(__riscv_vmerge_vvm_i16m2(__riscv_vreinterpret_v_bf16m2_i16m2(b),
                                                                      __riscv_vreinterpret_v_bf16m2_i16m2(a), mask2,
                                                                      unpacket_traits<Packet2Xbf>::size));
}

// Logical Operations are not supported for bflaot16, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet2Xbf pand<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vand_vv_u16m2(__riscv_vreinterpret_v_bf16m2_u16m2(a),
                                                                   __riscv_vreinterpret_v_bf16m2_u16m2(b),
                                                                   unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf por<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vor_vv_u16m2(__riscv_vreinterpret_v_bf16m2_u16m2(a),
                                                                  __riscv_vreinterpret_v_bf16m2_u16m2(b),
                                                                  unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pxor<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return __riscv_vreinterpret_v_u16m2_bf16m2(__riscv_vxor_vv_u16m2(__riscv_vreinterpret_v_bf16m2_u16m2(a),
                                                                   __riscv_vreinterpret_v_bf16m2_u16m2(b),
                                                                   unpacket_traits<Packet2Xbf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pandnot<Packet2Xbf>(const Packet2Xbf& a, const Packet2Xbf& b) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(
      pandnot<Packet2Xs>(__riscv_vreinterpret_v_bf16m2_i16m2(a), __riscv_vreinterpret_v_bf16m2_i16m2(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pload<Packet2Xbf>(const bfloat16* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_bf16m2(reinterpret_cast<const __bf16*>(from),
                                                         unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf ploadu<Packet2Xbf>(const bfloat16* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_bf16m2(reinterpret_cast<const __bf16*>(from),
                                                           unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf ploaddup<Packet2Xbf>(const bfloat16* from) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(ploaddup<Packet2Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf ploadquad<Packet2Xbf>(const bfloat16* from) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(ploadquad<Packet2Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE void pstore<bfloat16>(bfloat16* to, const Packet2Xbf& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_bf16m2(reinterpret_cast<__bf16*>(to), from,
                                                   unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<bfloat16>(bfloat16* to, const Packet2Xbf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_bf16m2(reinterpret_cast<__bf16*>(to), from,
                                                     unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xbf pgather<bfloat16, Packet2Xbf>(const bfloat16* from, Index stride) {
  return __riscv_vlse16_v_bf16m2(reinterpret_cast<const __bf16*>(from), stride * sizeof(bfloat16),
                                 unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<bfloat16, Packet2Xbf>(bfloat16* to, const Packet2Xbf& from, Index stride) {
  __riscv_vsse16(reinterpret_cast<__bf16*>(to), stride * sizeof(bfloat16), from, unpacket_traits<Packet2Xbf>::size);
}

template <>
EIGEN_STRONG_INLINE bfloat16 pfirst<Packet2Xbf>(const Packet2Xbf& a) {
  return numext::bit_cast<bfloat16>(__riscv_vmv_x_s_i16m2_i16(__riscv_vreinterpret_v_bf16m2_i16m2(a)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf psqrt(const Packet2Xbf& a) {
  return F32ToBf16(psqrt<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf print<Packet2Xbf>(const Packet2Xbf& a) {
  return F32ToBf16(print<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pfloor<Packet2Xbf>(const Packet2Xbf& a) {
  return F32ToBf16(pfloor<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf preverse(const Packet2Xbf& a) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(preverse<Packet2Xs>(__riscv_vreinterpret_v_bf16m2_i16m2(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux<Packet2Xbf>(const Packet2Xbf& a) {
  return static_cast<bfloat16>(predux<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_mul<Packet2Xbf>(const Packet2Xbf& a) {
  return static_cast<bfloat16>(predux_mul<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_min<Packet2Xbf>(const Packet2Xbf& a) {
  return static_cast<bfloat16>(predux_min<Packet4Xf>(Bf16ToF32(a)));
}

template <>
EIGEN_STRONG_INLINE bfloat16 predux_max<Packet2Xbf>(const Packet2Xbf& a) {
  return static_cast<bfloat16>(predux_max<Packet4Xf>(Bf16ToF32(a)));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xbf, N>& kernel) {
  bfloat16 buffer[unpacket_traits<Packet2Xbf>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(reinterpret_cast<__bf16*>(&buffer[i]), N * sizeof(bfloat16), kernel.packet[i],
                   unpacket_traits<Packet2Xbf>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = __riscv_vle16_v_bf16m2(reinterpret_cast<__bf16*>(&buffer[i * unpacket_traits<Packet2Xbf>::size]),
                                              unpacket_traits<Packet2Xbf>::size);
  }
}

template <typename Packet = Packet2Xbf>
EIGEN_STRONG_INLINE std::enable_if_t<
    std::is_same<Packet, Packet2Xbf>::value && (unpacket_traits<Packet2Xbf>::size % 8) == 0, Packet1Xbf>
predux_half(const Packet2Xbf& a) {
  return padd<Packet1Xbf>(__riscv_vget_v_bf16m2_bf16m1(a, 0), __riscv_vget_v_bf16m2_bf16m1(a, 1));
}

template <>
EIGEN_STRONG_INLINE Packet1Xbf pcast<Packet1Xs, Packet1Xbf>(const Packet1Xs& a) {
  return __riscv_vreinterpret_v_i16m1_bf16m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xbf pcast<Packet2Xs, Packet2Xbf>(const Packet2Xs& a) {
  return __riscv_vreinterpret_v_i16m2_bf16m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pcast<Packet1Xbf, Packet1Xs>(const Packet1Xbf& a) {
  return __riscv_vreinterpret_v_bf16m1_i16m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcast<Packet2Xbf, Packet2Xs>(const Packet2Xbf& a) {
  return __riscv_vreinterpret_v_bf16m2_i16m2(a);
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_PACKET_MATH_BF16_RVV10_H

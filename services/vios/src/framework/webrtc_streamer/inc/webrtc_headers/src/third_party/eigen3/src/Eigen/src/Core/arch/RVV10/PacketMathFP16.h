// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Kseniya Zaytseva <kseniya.zaytseva@syntacore.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_PACKET_MATH_FP16_RVV10_H
#define EIGEN_PACKET_MATH_FP16_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

typedef eigen_packet_wrapper<vfloat16m1_t __attribute__((riscv_rvv_vector_bits(EIGEN_RISCV64_RVV_VL))), 24> Packet1Xh;
typedef eigen_packet_wrapper<vfloat16m2_t __attribute__((riscv_rvv_vector_bits(EIGEN_RISCV64_RVV_VL * 2))), 25>
    Packet2Xh;

template <>
struct rvv_half_packet<Packet2Xh> {
  typedef Packet1Xh type;
};

template <>
struct unpacket_traits<Packet1Xh> : rvv_default_unpacket_traits<Eigen::half, Packet1Xh, 1> {
  typedef Packet1Xs integer_packet;
  typedef PacketMask16 packet_mask;
};

template <>
struct unpacket_traits<Packet2Xh> : rvv_default_unpacket_traits<Eigen::half, Packet2Xh, 2> {
  typedef Packet2Xs integer_packet;
  typedef PacketMask8 packet_mask;
};

#if EIGEN_RISCV64_DEFAULT_LMUL == 1
typedef Packet1Xh PacketXh;
#else
typedef Packet2Xh PacketXh;
#endif

template <>
struct packet_traits<Eigen::half> : rvv_default_packet_traits<Eigen::half, PacketXh> {
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

    HasCmp = 1,
    HasDiv = 1,
    HasRound = 1,

    HasSin = EIGEN_FAST_MATH,
    HasCos = EIGEN_FAST_MATH,
    HasLog = 0,
    HasExp = 0,
    HasSqrt = 1,
    HasTanh = EIGEN_FAST_MATH,
    HasErf = 0
  };
};

/********************************* Packet1Xh ************************************/

EIGEN_STRONG_INLINE Packet1Xh __riscv_vreinterpret_v_u32m1_f16m1(const Packet1Xu& a) {
  return __riscv_vreinterpret_v_u16m1_f16m1(__riscv_vreinterpret_v_u32m1_u16m1(a));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh ptrue<Packet1Xh>(const Packet1Xh& /*a*/) {
  return __riscv_vreinterpret_f16m1(__riscv_vmv_v_x_u16m1(0xffffu, unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pzero<Packet1Xh>(const Packet1Xh& /*a*/) {
  return __riscv_vfmv_v_f_f16m1(static_cast<_Float16>(0.0), unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pabs(const Packet1Xh& a) {
  return __riscv_vfabs_v_f16m1(a, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pabsdiff(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfabs_v_f16m1(__riscv_vfsub_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size),
                               unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pset1<Packet1Xh>(const Eigen::half& from) {
  return __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(from), unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pset1frombits<Packet1Xh>(numext::uint16_t from) {
  return __riscv_vreinterpret_f16m1(__riscv_vmv_v_x_u16m1(from, unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh plset<Packet1Xh>(const Eigen::half& a) {
  Packet1Xh idx = __riscv_vfcvt_f_x_v_f16m1(
      __riscv_vreinterpret_v_u16m1_i16m1(__riscv_vid_v_u16m1(unpacket_traits<Packet1Xs>::size)),
      unpacket_traits<Packet1Xh>::size);
  return __riscv_vfadd_vf_f16m1(idx, numext::bit_cast<_Float16>(a), unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet1Xh>(const Eigen::half* a, Packet1Xh& a0, Packet1Xh& a1, Packet1Xh& a2,
                                                Packet1Xh& a3) {
  vfloat16m1_t aa = __riscv_vle16_v_f16m1(reinterpret_cast<const _Float16*>(a), 4);
  a0 = __riscv_vrgather_vx_f16m1(aa, 0, unpacket_traits<Packet1Xh>::size);
  a1 = __riscv_vrgather_vx_f16m1(aa, 1, unpacket_traits<Packet1Xh>::size);
  a2 = __riscv_vrgather_vx_f16m1(aa, 2, unpacket_traits<Packet1Xh>::size);
  a3 = __riscv_vrgather_vx_f16m1(aa, 3, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh padd<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfadd_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh psub<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfsub_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pnegate(const Packet1Xh& a) {
  return __riscv_vfneg_v_f16m1(a, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh psignbit(const Packet1Xh& a) {
  return __riscv_vreinterpret_v_i16m1_f16m1(
      __riscv_vsra_vx_i16m1(__riscv_vreinterpret_v_f16m1_i16m1(a), 15, unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pconj(const Packet1Xh& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmul<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfmul_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pdiv<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfdiv_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmadd(const Packet1Xh& a, const Packet1Xh& b, const Packet1Xh& c) {
  return __riscv_vfmadd_vv_f16m1(a, b, c, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmsub(const Packet1Xh& a, const Packet1Xh& b, const Packet1Xh& c) {
  return __riscv_vfmsub_vv_f16m1(a, b, c, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pnmadd(const Packet1Xh& a, const Packet1Xh& b, const Packet1Xh& c) {
  return __riscv_vfnmsub_vv_f16m1(a, b, c, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pnmsub(const Packet1Xh& a, const Packet1Xh& b, const Packet1Xh& c) {
  return __riscv_vfnmadd_vv_f16m1(a, b, c, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmin<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  Packet1Xh nans = __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet1Xh>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f16m1_b16(a, a, unpacket_traits<Packet1Xh>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f16m1_b16(b, b, unpacket_traits<Packet1Xh>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet1Xh>::size);

  return __riscv_vfmin_vv_f16m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmin<PropagateNaN, Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return pmin<Packet1Xh>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmin<PropagateNumbers, Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfmin_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmax<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  Packet1Xh nans = __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet1Xh>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f16m1_b16(a, a, unpacket_traits<Packet1Xh>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f16m1_b16(b, b, unpacket_traits<Packet1Xh>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet1Xh>::size);

  return __riscv_vfmax_vv_f16m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmax<PropagateNaN, Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return pmax<Packet1Xh>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pmax<PropagateNumbers, Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vfmax_vv_f16m1(a, b, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pcmp_le<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  PacketMask16 mask = __riscv_vmfle_vv_f16m1_b16(a, b, unpacket_traits<Packet1Xh>::size);
  return __riscv_vmerge_vvm_f16m1(pzero<Packet1Xh>(a), ptrue<Packet1Xh>(a), mask, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pcmp_lt<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  PacketMask16 mask = __riscv_vmflt_vv_f16m1_b16(a, b, unpacket_traits<Packet1Xh>::size);
  return __riscv_vmerge_vvm_f16m1(pzero<Packet1Xh>(a), ptrue<Packet1Xh>(a), mask, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pcmp_eq<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  PacketMask16 mask = __riscv_vmfeq_vv_f16m1_b16(a, b, unpacket_traits<Packet1Xh>::size);
  return __riscv_vmerge_vvm_f16m1(pzero<Packet1Xh>(a), ptrue<Packet1Xh>(a), mask, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pcmp_lt_or_nan<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  PacketMask16 mask = __riscv_vmfge_vv_f16m1_b16(a, b, unpacket_traits<Packet1Xh>::size);
  return __riscv_vfmerge_vfm_f16m1(ptrue<Packet1Xh>(a), static_cast<_Float16>(0.0), mask,
                                   unpacket_traits<Packet1Xh>::size);
}

EIGEN_STRONG_INLINE Packet1Xh pselect(const PacketMask16& mask, const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vmerge_vvm_f16m1(b, a, mask, unpacket_traits<Packet1Xh>::size);
}

EIGEN_STRONG_INLINE Packet1Xh pselect(const Packet1Xh& mask, const Packet1Xh& a, const Packet1Xh& b) {
  PacketMask16 mask2 =
      __riscv_vmsne_vx_i16m1_b16(__riscv_vreinterpret_v_f16m1_i16m1(mask), 0, unpacket_traits<Packet1Xh>::size);
  return __riscv_vreinterpret_v_i16m1_f16m1(__riscv_vmerge_vvm_i16m1(__riscv_vreinterpret_v_f16m1_i16m1(b),
                                                                     __riscv_vreinterpret_v_f16m1_i16m1(a), mask2,
                                                                     unpacket_traits<Packet1Xh>::size));
}

// Logical Operations are not supported for half, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet1Xh pand<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vreinterpret_v_u16m1_f16m1(__riscv_vand_vv_u16m1(
      __riscv_vreinterpret_v_f16m1_u16m1(a), __riscv_vreinterpret_v_f16m1_u16m1(b), unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh por<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vreinterpret_v_u16m1_f16m1(__riscv_vor_vv_u16m1(
      __riscv_vreinterpret_v_f16m1_u16m1(a), __riscv_vreinterpret_v_f16m1_u16m1(b), unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pxor<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vreinterpret_v_u16m1_f16m1(__riscv_vxor_vv_u16m1(
      __riscv_vreinterpret_v_f16m1_u16m1(a), __riscv_vreinterpret_v_f16m1_u16m1(b), unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pandnot<Packet1Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vreinterpret_v_i16m1_f16m1(
      pandnot<Packet1Xs>(__riscv_vreinterpret_v_f16m1_i16m1(a), __riscv_vreinterpret_v_f16m1_i16m1(b)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pload<Packet1Xh>(const Eigen::half* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_f16m1(reinterpret_cast<const _Float16*>(from),
                                                        unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh ploadu<Packet1Xh>(const Eigen::half* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_f16m1(reinterpret_cast<const _Float16*>(from),
                                                          unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh ploaddup<Packet1Xh>(const Eigen::half* from) {
  return __riscv_vreinterpret_v_i16m1_f16m1(ploaddup<Packet1Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh ploadquad<Packet1Xh>(const Eigen::half* from) {
  return __riscv_vreinterpret_v_i16m1_f16m1(ploadquad<Packet1Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE void pstore<Eigen::half>(Eigen::half* to, const Packet1Xh& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_f16m1(reinterpret_cast<_Float16*>(to), from,
                                                  unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<Eigen::half>(Eigen::half* to, const Packet1Xh& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_f16m1(reinterpret_cast<_Float16*>(to), from,
                                                    unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xh pgather<Eigen::half, Packet1Xh>(const Eigen::half* from, Index stride) {
  return __riscv_vlse16_v_f16m1(reinterpret_cast<const _Float16*>(from), stride * sizeof(Eigen::half),
                                unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<Eigen::half, Packet1Xh>(Eigen::half* to, const Packet1Xh& from, Index stride) {
  __riscv_vsse16(reinterpret_cast<_Float16*>(to), stride * sizeof(Eigen::half), from, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Eigen::half pfirst<Packet1Xh>(const Packet1Xh& a) {
  return static_cast<Eigen::half>(__riscv_vfmv_f_s_f16m1_f16(a));
}

template <>
EIGEN_STRONG_INLINE Packet1Xh psqrt(const Packet1Xh& a) {
  return __riscv_vfsqrt_v_f16m1(a, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh print<Packet1Xh>(const Packet1Xh& a) {
  const Packet1Xh limit = pset1<Packet1Xh>(static_cast<Eigen::half>(1 << 10));
  const Packet1Xh abs_a = pabs(a);

  PacketMask16 mask = __riscv_vmfne_vv_f16m1_b16(a, a, unpacket_traits<Packet1Xh>::size);
  const Packet1Xh x = __riscv_vfadd_vv_f16m1_tumu(mask, a, a, a, unpacket_traits<Packet1Xh>::size);
  const Packet1Xh new_x = __riscv_vfcvt_f_x_v_f16m1(__riscv_vfcvt_x_f_v_i16m1(a, unpacket_traits<Packet1Xh>::size),
                                                    unpacket_traits<Packet1Xh>::size);

  mask = __riscv_vmflt_vv_f16m1_b16(abs_a, limit, unpacket_traits<Packet1Xh>::size);
  Packet1Xh signed_x = __riscv_vfsgnj_vv_f16m1(new_x, x, unpacket_traits<Packet1Xh>::size);
  return __riscv_vmerge_vvm_f16m1(x, signed_x, mask, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh pfloor<Packet1Xh>(const Packet1Xh& a) {
  Packet1Xh tmp = print<Packet1Xh>(a);
  // If greater, subtract one.
  PacketMask16 mask = __riscv_vmflt_vv_f16m1_b16(a, tmp, unpacket_traits<Packet1Xh>::size);
  return __riscv_vfsub_vf_f16m1_tumu(mask, tmp, tmp, static_cast<_Float16>(1.0), unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh preverse(const Packet1Xh& a) {
  Packet1Xsu idx = __riscv_vrsub_vx_u16m1(__riscv_vid_v_u16m1(unpacket_traits<Packet1Xh>::size),
                                          unpacket_traits<Packet1Xh>::size - 1, unpacket_traits<Packet1Xh>::size);
  return __riscv_vrgather_vv_f16m1(a, idx, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux<Packet1Xh>(const Packet1Xh& a) {
  return static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredusum_vs_f16m1_f16m1(
      a, __riscv_vfmv_v_f_f16m1(static_cast<_Float16>(0.0), unpacket_traits<Packet1Xh>::size),
      unpacket_traits<Packet1Xh>::size)));
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_mul<Packet1Xh>(const Packet1Xh& a) {
  // Multiply the vector by its reverse
  Packet1Xh prod = __riscv_vfmul_vv_f16m1(preverse(a), a, unpacket_traits<Packet1Xh>::size);
  Packet1Xh half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_f16m1(prod, 16, unpacket_traits<Packet1Xh>::size);
    prod = __riscv_vfmul_vv_f16m1(prod, half_prod, unpacket_traits<Packet1Xh>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_f16m1(prod, 8, unpacket_traits<Packet1Xh>::size);
    prod = __riscv_vfmul_vv_f16m1(prod, half_prod, unpacket_traits<Packet1Xh>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_f16m1(prod, 4, unpacket_traits<Packet1Xh>::size);
    prod = __riscv_vfmul_vv_f16m1(prod, half_prod, unpacket_traits<Packet1Xh>::size);
  }
  // Last reduction
  half_prod = __riscv_vslidedown_vx_f16m1(prod, 2, unpacket_traits<Packet1Xh>::size);
  prod = __riscv_vfmul_vv_f16m1(prod, half_prod, unpacket_traits<Packet1Xh>::size);

  half_prod = __riscv_vslidedown_vx_f16m1(prod, 1, unpacket_traits<Packet1Xh>::size);
  prod = __riscv_vfmul_vv_f16m1(prod, half_prod, unpacket_traits<Packet1Xh>::size);

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_min<Packet1Xh>(const Packet1Xh& a) {
  const Eigen::half max = (std::numeric_limits<Eigen::half>::max)();
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  return (std::min)(static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredmin_vs_f16m1_f16m1(
                        a, __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet1Xh>::size),
                        unpacket_traits<Packet1Xh>::size))),
                    max);
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_max<Packet1Xh>(const Packet1Xh& a) {
  const Eigen::half min = -(std::numeric_limits<Eigen::half>::max)();
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  return (std::max)(static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredmax_vs_f16m1_f16m1(
                        a, __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet1Xh>::size),
                        unpacket_traits<Packet1Xh>::size))),
                    min);
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xh, N>& kernel) {
  Eigen::half buffer[unpacket_traits<Packet1Xh>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(reinterpret_cast<_Float16*>(&buffer[i]), N * sizeof(Eigen::half), kernel.packet[i],
                   unpacket_traits<Packet1Xh>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = __riscv_vle16_v_f16m1(reinterpret_cast<_Float16*>(&buffer[i * unpacket_traits<Packet1Xh>::size]),
                                             unpacket_traits<Packet1Xh>::size);
  }
}

EIGEN_STRONG_INLINE Packet2Xf half2float(const Packet1Xh& a) {
  return __riscv_vfwcvt_f_f_v_f32m2(a, unpacket_traits<Packet2Xf>::size);
}

EIGEN_STRONG_INLINE Packet1Xh float2half(const Packet2Xf& a) {
  return __riscv_vfncvt_f_f_w_f16m1(a, unpacket_traits<Packet1Xh>::size);
}

/********************************* Packet2Xh ************************************/

EIGEN_STRONG_INLINE Packet2Xh __riscv_vreinterpret_v_u32m2_f16m2(const Packet2Xu& a) {
  return __riscv_vreinterpret_v_u16m2_f16m2(__riscv_vreinterpret_v_u32m2_u16m2(a));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh ptrue<Packet2Xh>(const Packet2Xh& /*a*/) {
  return __riscv_vreinterpret_f16m2(__riscv_vmv_v_x_u16m2(0xffffu, unpacket_traits<Packet2Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pzero<Packet2Xh>(const Packet2Xh& /*a*/) {
  return __riscv_vfmv_v_f_f16m2(static_cast<_Float16>(0.0), unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pabs(const Packet2Xh& a) {
  return __riscv_vfabs_v_f16m2(a, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pabsdiff(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfabs_v_f16m2(__riscv_vfsub_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size),
                               unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pset1<Packet2Xh>(const Eigen::half& from) {
  return __riscv_vfmv_v_f_f16m2(numext::bit_cast<_Float16>(from), unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pset1frombits<Packet2Xh>(numext::uint16_t from) {
  return __riscv_vreinterpret_f16m2(__riscv_vmv_v_x_u16m2(from, unpacket_traits<Packet2Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh plset<Packet2Xh>(const Eigen::half& a) {
  Packet2Xh idx = __riscv_vfcvt_f_x_v_f16m2(
      __riscv_vreinterpret_v_u16m2_i16m2(__riscv_vid_v_u16m2(unpacket_traits<Packet4Xs>::size)),
      unpacket_traits<Packet2Xh>::size);
  return __riscv_vfadd_vf_f16m2(idx, numext::bit_cast<_Float16>(a), unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet2Xh>(const Eigen::half* a, Packet2Xh& a0, Packet2Xh& a1, Packet2Xh& a2,
                                                Packet2Xh& a3) {
  vfloat16m2_t aa = __riscv_vle16_v_f16m2(reinterpret_cast<const _Float16*>(a), 4);
  a0 = __riscv_vrgather_vx_f16m2(aa, 0, unpacket_traits<Packet2Xh>::size);
  a1 = __riscv_vrgather_vx_f16m2(aa, 1, unpacket_traits<Packet2Xh>::size);
  a2 = __riscv_vrgather_vx_f16m2(aa, 2, unpacket_traits<Packet2Xh>::size);
  a3 = __riscv_vrgather_vx_f16m2(aa, 3, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh padd<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfadd_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh psub<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfsub_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pnegate(const Packet2Xh& a) {
  return __riscv_vfneg_v_f16m2(a, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh psignbit(const Packet2Xh& a) {
  return __riscv_vreinterpret_v_i16m2_f16m2(
      __riscv_vsra_vx_i16m2(__riscv_vreinterpret_v_f16m2_i16m2(a), 15, unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pconj(const Packet2Xh& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmul<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfmul_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pdiv<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfdiv_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmadd(const Packet2Xh& a, const Packet2Xh& b, const Packet2Xh& c) {
  return __riscv_vfmadd_vv_f16m2(a, b, c, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmsub(const Packet2Xh& a, const Packet2Xh& b, const Packet2Xh& c) {
  return __riscv_vfmsub_vv_f16m2(a, b, c, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pnmadd(const Packet2Xh& a, const Packet2Xh& b, const Packet2Xh& c) {
  return __riscv_vfnmsub_vv_f16m2(a, b, c, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pnmsub(const Packet2Xh& a, const Packet2Xh& b, const Packet2Xh& c) {
  return __riscv_vfnmadd_vv_f16m2(a, b, c, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmin<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  Packet2Xh nans = __riscv_vfmv_v_f_f16m2(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet2Xh>::size);
  PacketMask8 mask = __riscv_vmfeq_vv_f16m2_b8(a, a, unpacket_traits<Packet2Xh>::size);
  PacketMask8 mask2 = __riscv_vmfeq_vv_f16m2_b8(b, b, unpacket_traits<Packet2Xh>::size);
  mask = __riscv_vmand_mm_b8(mask, mask2, unpacket_traits<Packet2Xh>::size);

  return __riscv_vfmin_vv_f16m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmin<PropagateNaN, Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return pmin<Packet2Xh>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmin<PropagateNumbers, Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfmin_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmax<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  Packet2Xh nans = __riscv_vfmv_v_f_f16m2(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet2Xh>::size);
  PacketMask8 mask = __riscv_vmfeq_vv_f16m2_b8(a, a, unpacket_traits<Packet2Xh>::size);
  PacketMask8 mask2 = __riscv_vmfeq_vv_f16m2_b8(b, b, unpacket_traits<Packet2Xh>::size);
  mask = __riscv_vmand_mm_b8(mask, mask2, unpacket_traits<Packet2Xh>::size);

  return __riscv_vfmax_vv_f16m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmax<PropagateNaN, Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return pmax<Packet2Xh>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pmax<PropagateNumbers, Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vfmax_vv_f16m2(a, b, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcmp_le<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  PacketMask8 mask = __riscv_vmfle_vv_f16m2_b8(a, b, unpacket_traits<Packet2Xh>::size);
  return __riscv_vmerge_vvm_f16m2(pzero<Packet2Xh>(a), ptrue<Packet2Xh>(a), mask, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcmp_lt<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  PacketMask8 mask = __riscv_vmflt_vv_f16m2_b8(a, b, unpacket_traits<Packet2Xh>::size);
  return __riscv_vmerge_vvm_f16m2(pzero<Packet2Xh>(a), ptrue<Packet2Xh>(a), mask, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcmp_eq<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  PacketMask8 mask = __riscv_vmfeq_vv_f16m2_b8(a, b, unpacket_traits<Packet2Xh>::size);
  return __riscv_vmerge_vvm_f16m2(pzero<Packet2Xh>(a), ptrue<Packet2Xh>(a), mask, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcmp_lt_or_nan<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  PacketMask8 mask = __riscv_vmfge_vv_f16m2_b8(a, b, unpacket_traits<Packet2Xh>::size);
  return __riscv_vfmerge_vfm_f16m2(ptrue<Packet2Xh>(a), static_cast<_Float16>(0.0), mask,
                                   unpacket_traits<Packet2Xh>::size);
}

EIGEN_STRONG_INLINE Packet2Xh pselect(const PacketMask8& mask, const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vmerge_vvm_f16m2(b, a, mask, unpacket_traits<Packet2Xh>::size);
}

EIGEN_STRONG_INLINE Packet2Xh pselect(const Packet2Xh& mask, const Packet2Xh& a, const Packet2Xh& b) {
  PacketMask8 mask2 =
      __riscv_vmsne_vx_i16m2_b8(__riscv_vreinterpret_v_f16m2_i16m2(mask), 0, unpacket_traits<Packet2Xh>::size);
  return __riscv_vreinterpret_v_i16m2_f16m2(__riscv_vmerge_vvm_i16m2(__riscv_vreinterpret_v_f16m2_i16m2(b),
                                                                     __riscv_vreinterpret_v_f16m2_i16m2(a), mask2,
                                                                     unpacket_traits<Packet2Xh>::size));
}

// Logical Operations are not supported for half, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet2Xh pand<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vreinterpret_v_u16m2_f16m2(__riscv_vand_vv_u16m2(
      __riscv_vreinterpret_v_f16m2_u16m2(a), __riscv_vreinterpret_v_f16m2_u16m2(b), unpacket_traits<Packet2Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh por<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vreinterpret_v_u16m2_f16m2(__riscv_vor_vv_u16m2(
      __riscv_vreinterpret_v_f16m2_u16m2(a), __riscv_vreinterpret_v_f16m2_u16m2(b), unpacket_traits<Packet2Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pxor<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vreinterpret_v_u16m2_f16m2(__riscv_vxor_vv_u16m2(
      __riscv_vreinterpret_v_f16m2_u16m2(a), __riscv_vreinterpret_v_f16m2_u16m2(b), unpacket_traits<Packet2Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pandnot<Packet2Xh>(const Packet2Xh& a, const Packet2Xh& b) {
  return __riscv_vreinterpret_v_i16m2_f16m2(
      pandnot<Packet2Xs>(__riscv_vreinterpret_v_f16m2_i16m2(a), __riscv_vreinterpret_v_f16m2_i16m2(b)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pload<Packet2Xh>(const Eigen::half* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_f16m2(reinterpret_cast<const _Float16*>(from),
                                                        unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh ploadu<Packet2Xh>(const Eigen::half* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_f16m2(reinterpret_cast<const _Float16*>(from),
                                                          unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh ploaddup<Packet2Xh>(const Eigen::half* from) {
  return __riscv_vreinterpret_v_i16m2_f16m2(ploaddup<Packet2Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh ploadquad<Packet2Xh>(const Eigen::half* from) {
  return __riscv_vreinterpret_v_i16m2_f16m2(ploadquad<Packet2Xs>(reinterpret_cast<const numext::int16_t*>(from)));
}

template <>
EIGEN_STRONG_INLINE void pstore<Eigen::half>(Eigen::half* to, const Packet2Xh& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_f16m2(reinterpret_cast<_Float16*>(to), from,
                                                  unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<Eigen::half>(Eigen::half* to, const Packet2Xh& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_f16m2(reinterpret_cast<_Float16*>(to), from,
                                                    unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xh pgather<Eigen::half, Packet2Xh>(const Eigen::half* from, Index stride) {
  return __riscv_vlse16_v_f16m2(reinterpret_cast<const _Float16*>(from), stride * sizeof(Eigen::half),
                                unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<Eigen::half, Packet2Xh>(Eigen::half* to, const Packet2Xh& from, Index stride) {
  __riscv_vsse16(reinterpret_cast<_Float16*>(to), stride * sizeof(Eigen::half), from, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Eigen::half pfirst<Packet2Xh>(const Packet2Xh& a) {
  return numext::bit_cast<Eigen::half>(__riscv_vfmv_f_s_f16m2_f16(a));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh psqrt(const Packet2Xh& a) {
  return __riscv_vfsqrt_v_f16m2(a, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh print<Packet2Xh>(const Packet2Xh& a) {
  const Packet2Xh limit = pset1<Packet2Xh>(static_cast<Eigen::half>(1 << 10));
  const Packet2Xh abs_a = pabs(a);

  PacketMask8 mask = __riscv_vmfne_vv_f16m2_b8(a, a, unpacket_traits<Packet2Xh>::size);
  const Packet2Xh x = __riscv_vfadd_vv_f16m2_tumu(mask, a, a, a, unpacket_traits<Packet2Xh>::size);
  const Packet2Xh new_x = __riscv_vfcvt_f_x_v_f16m2(__riscv_vfcvt_x_f_v_i16m2(a, unpacket_traits<Packet2Xh>::size),
                                                    unpacket_traits<Packet2Xh>::size);

  mask = __riscv_vmflt_vv_f16m2_b8(abs_a, limit, unpacket_traits<Packet2Xh>::size);
  Packet2Xh signed_x = __riscv_vfsgnj_vv_f16m2(new_x, x, unpacket_traits<Packet2Xh>::size);
  return __riscv_vmerge_vvm_f16m2(x, signed_x, mask, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pfloor<Packet2Xh>(const Packet2Xh& a) {
  Packet2Xh tmp = print<Packet2Xh>(a);
  // If greater, subtract one.
  PacketMask8 mask = __riscv_vmflt_vv_f16m2_b8(a, tmp, unpacket_traits<Packet2Xh>::size);
  return __riscv_vfsub_vf_f16m2_tumu(mask, tmp, tmp, static_cast<_Float16>(1.0), unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh preverse(const Packet2Xh& a) {
  Packet2Xsu idx = __riscv_vrsub_vx_u16m2(__riscv_vid_v_u16m2(unpacket_traits<Packet2Xh>::size),
                                          unpacket_traits<Packet2Xh>::size - 1, unpacket_traits<Packet2Xh>::size);
  return __riscv_vrgather_vv_f16m2(a, idx, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux<Packet2Xh>(const Packet2Xh& a) {
  return static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredusum_vs_f16m2_f16m1(
      a, __riscv_vfmv_v_f_f16m1(static_cast<_Float16>(0.0), unpacket_traits<Packet2Xh>::size / 2),
      unpacket_traits<Packet2Xh>::size)));
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_mul<Packet2Xh>(const Packet2Xh& a) {
  return predux_mul<Packet1Xh>(__riscv_vfmul_vv_f16m1(
      __riscv_vget_v_f16m2_f16m1(a, 0), __riscv_vget_v_f16m2_f16m1(a, 1), unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_min<Packet2Xh>(const Packet2Xh& a) {
  const Eigen::half max = (std::numeric_limits<Eigen::half>::max)();
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  return (std::min)(
      static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredmin_vs_f16m2_f16m1(
          a, __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet2Xh>::size / 2),
          unpacket_traits<Packet2Xh>::size))),
      max);
}

template <>
EIGEN_STRONG_INLINE Eigen::half predux_max<Packet2Xh>(const Packet2Xh& a) {
  const Eigen::half min = -(std::numeric_limits<Eigen::half>::max)();
  const Eigen::half nan = (std::numeric_limits<Eigen::half>::quiet_NaN)();
  return (std::max)(
      static_cast<Eigen::half>(__riscv_vfmv_f(__riscv_vfredmax_vs_f16m2_f16m1(
          a, __riscv_vfmv_v_f_f16m1(numext::bit_cast<_Float16>(nan), unpacket_traits<Packet2Xh>::size / 2),
          unpacket_traits<Packet2Xh>::size))),
      min);
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xh, N>& kernel) {
  Eigen::half buffer[unpacket_traits<Packet2Xh>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(reinterpret_cast<_Float16*>(&buffer[i]), N * sizeof(Eigen::half), kernel.packet[i],
                   unpacket_traits<Packet2Xh>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = __riscv_vle16_v_f16m2(reinterpret_cast<_Float16*>(&buffer[i * unpacket_traits<Packet2Xh>::size]),
                                             unpacket_traits<Packet2Xh>::size);
  }
}

EIGEN_STRONG_INLINE Packet4Xf half2float(const Packet2Xh& a) {
  return __riscv_vfwcvt_f_f_v_f32m4(a, unpacket_traits<Packet4Xf>::size);
}

EIGEN_STRONG_INLINE Packet2Xh float2half(const Packet4Xf& a) {
  return __riscv_vfncvt_f_f_w_f16m2(a, unpacket_traits<Packet2Xh>::size);
}

template <typename Packet = Packet2Xh>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xh>::value && (unpacket_traits<Packet2Xh>::size % 8) == 0, Packet1Xh>
    predux_half(const Packet2Xh& a) {
  return __riscv_vfadd_vv_f16m1(__riscv_vget_v_f16m2_f16m1(a, 0), __riscv_vget_v_f16m2_f16m1(a, 1),
                                unpacket_traits<Packet1Xh>::size);
}

F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, pcos)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, pexp)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, pexpm1)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, plog)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, plog1p)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, plog2)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, preciprocal)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, prsqrt)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, psin)
F16_PACKET_FUNCTION(Packet2Xf, Packet1Xh, ptanh)

F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, pcos)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, pexp)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, pexpm1)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, plog)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, plog1p)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, plog2)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, preciprocal)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, prsqrt)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, psin)
F16_PACKET_FUNCTION(Packet4Xf, Packet2Xh, ptanh)

/********************************* casting ************************************/

template <>
struct type_casting_traits<_Float16, numext::int16_t> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
struct type_casting_traits<numext::int16_t, _Float16> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
EIGEN_STRONG_INLINE Packet1Xh pcast<Packet1Xs, Packet1Xh>(const Packet1Xs& a) {
  return __riscv_vfcvt_f_x_v_f16m1(a, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pcast<Packet1Xh, Packet1Xs>(const Packet1Xh& a) {
  return __riscv_vfcvt_rtz_x_f_v_i16m1(a, unpacket_traits<Packet1Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xh preinterpret<Packet1Xh, Packet1Xs>(const Packet1Xs& a) {
  return __riscv_vreinterpret_v_i16m1_f16m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs preinterpret<Packet1Xs, Packet1Xh>(const Packet1Xh& a) {
  return __riscv_vreinterpret_v_f16m1_i16m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcast<Packet2Xs, Packet2Xh>(const Packet2Xs& a) {
  return __riscv_vfcvt_f_x_v_f16m2(a, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcast<Packet2Xh, Packet2Xs>(const Packet2Xh& a) {
  return __riscv_vfcvt_rtz_x_f_v_i16m2(a, unpacket_traits<Packet2Xh>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xh preinterpret<Packet2Xh, Packet2Xs>(const Packet2Xs& a) {
  return __riscv_vreinterpret_v_i16m2_f16m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs preinterpret<Packet2Xs, Packet2Xh>(const Packet2Xh& a) {
  return __riscv_vreinterpret_v_f16m2_i16m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pcast<Packet1Xh, Packet4Xs>(const Packet1Xh& a, const Packet1Xh& b, const Packet1Xh& c,
                                                          const Packet1Xh& d) {
  return __riscv_vcreate_v_i16m1_i16m4(__riscv_vfcvt_rtz_x_f_v_i16m1(a, unpacket_traits<Packet1Xh>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i16m1(b, unpacket_traits<Packet1Xh>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i16m1(c, unpacket_traits<Packet1Xh>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i16m1(d, unpacket_traits<Packet1Xh>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcast<Packet1Xs, Packet2Xh>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vcreate_v_f16m1_f16m2(__riscv_vfcvt_f_x_v_f16m1(a, unpacket_traits<Packet1Xs>::size),
                                       __riscv_vfcvt_f_x_v_f16m1(b, unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xh pcast<Packet1Xh, Packet2Xh>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vcreate_v_f16m1_f16m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcast<Packet1Xh, Packet2Xs>(const Packet1Xh& a, const Packet1Xh& b) {
  return __riscv_vcreate_v_i16m1_i16m2(__riscv_vfcvt_rtz_x_f_v_i16m1(a, unpacket_traits<Packet1Xh>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i16m1(b, unpacket_traits<Packet1Xh>::size));
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_PACKET_MATH_FP16_RVV10_H

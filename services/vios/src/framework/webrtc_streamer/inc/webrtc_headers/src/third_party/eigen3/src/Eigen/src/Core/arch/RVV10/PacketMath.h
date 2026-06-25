// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2024 Kseniya Zaytseva <kseniya.zaytseva@syntacore.com>
// Copyright (C) 2025 Chip Kerchner <ckerchner@tenstorrent.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_PACKET_MATH_RVV10_H
#define EIGEN_PACKET_MATH_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/********************************* Packet1Xi ************************************/

EIGEN_STRONG_INLINE Packet1Xi __riscv_vreinterpret_v_u64m1_i32m1(const Packet1Xul& a) {
  return __riscv_vreinterpret_v_i64m1_i32m1(__riscv_vreinterpret_v_u64m1_i64m1(a));
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pset1<Packet1Xi>(const numext::int32_t& from) {
  return __riscv_vmv_v_x_i32m1(from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi plset<Packet1Xi>(const numext::int32_t& a) {
  Packet1Xi idx = __riscv_vreinterpret_v_u32m1_i32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xi>::size));
  return __riscv_vadd_vx_i32m1(idx, a, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pzero<Packet1Xi>(const Packet1Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m1(0, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi padd<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vadd_vv_i32m1(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi psub<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pnegate(const Packet1Xi& a) {
  return __riscv_vneg(a, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pconj(const Packet1Xi& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pmul<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pdiv<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pmadd(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pmsub(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pnmadd(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c) {
  return __riscv_vnmsub_vv_i32m1(a, b, c, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pnmsub(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c) {
  return __riscv_vnmsub_vv_i32m1(a, b, pnegate(c), unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pmin<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pmax<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pcmp_le<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  PacketMask32 mask = __riscv_vmsle_vv_i32m1_b32(a, b, unpacket_traits<Packet1Xi>::size);
  return __riscv_vmerge_vxm_i32m1(pzero(a), 0xffffffff, mask, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pcmp_lt<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  PacketMask32 mask = __riscv_vmslt_vv_i32m1_b32(a, b, unpacket_traits<Packet1Xi>::size);
  return __riscv_vmerge_vxm_i32m1(pzero(a), 0xffffffff, mask, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pcmp_eq<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  PacketMask32 mask = __riscv_vmseq_vv_i32m1_b32(a, b, unpacket_traits<Packet1Xi>::size);
  return __riscv_vmerge_vxm_i32m1(pzero(a), 0xffffffff, mask, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi ptrue<Packet1Xi>(const Packet1Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m1(0xffffffffu, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pand<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vand_vv_i32m1(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi por<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vor_vv_i32m1(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pxor<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vxor_vv_i32m1(a, b, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pandnot<Packet1Xi>(const Packet1Xi& a, const Packet1Xi& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i32m1(a, __riscv_vnot_v_i32m1(b, unpacket_traits<Packet1Xi>::size),
                               unpacket_traits<Packet1Xi>::size);
#else
  return __riscv_vreinterpret_v_u32m1_i32m1(__riscv_vandn_vv_u32m1(
      __riscv_vreinterpret_v_i32m1_u32m1(a), __riscv_vreinterpret_v_i32m1_u32m1(b), unpacket_traits<Packet1Xi>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xi parithmetic_shift_right(Packet1Xi a) {
  return __riscv_vsra_vx_i32m1(a, N, unpacket_traits<Packet1Xi>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xi plogical_shift_right(Packet1Xi a) {
  return __riscv_vreinterpret_i32m1(
      __riscv_vsrl_vx_u32m1(__riscv_vreinterpret_u32m1(a), N, unpacket_traits<Packet1Xi>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xi plogical_shift_left(Packet1Xi a) {
  return __riscv_vsll_vx_i32m1(a, N, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pload<Packet1Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_i32m1(from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi ploadu<Packet1Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_i32m1(from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi ploaddup<Packet1Xi>(const numext::int32_t* from) {
  Packet1Xul data = __riscv_vlmul_trunc_v_u64m2_u64m1(__riscv_vwcvtu_x_x_v_u64m2(
      __riscv_vreinterpret_v_i32m1_u32m1(pload<Packet1Xi>(from)), unpacket_traits<Packet1Xi>::size));
  return __riscv_vreinterpret_v_u64m1_i32m1(__riscv_vadd_vv_u64m1(
      __riscv_vsll_vx_u64m1(data, 32, unpacket_traits<Packet1Xl>::size), data, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xi ploadquad<Packet1Xi>(const numext::int32_t* from) {
  Packet1Xu idx =
      __riscv_vsrl_vx_u32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xi>::size), 2, unpacket_traits<Packet1Xi>::size);
  return __riscv_vrgather_vv_i32m1(pload<Packet1Xi>(from), idx, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int32_t>(numext::int32_t* to, const Packet1Xi& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_i32m1(to, from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int32_t>(numext::int32_t* to, const Packet1Xi& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_i32m1(to, from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xi pgather<numext::int32_t, Packet1Xi>(const numext::int32_t* from, Index stride) {
  return __riscv_vlse32_v_i32m1(from, stride * sizeof(numext::int32_t), unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int32_t, Packet1Xi>(numext::int32_t* to, const Packet1Xi& from,
                                                                   Index stride) {
  __riscv_vsse32(to, stride * sizeof(numext::int32_t), from, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t pfirst<Packet1Xi>(const Packet1Xi& a) {
  return __riscv_vmv_x_s_i32m1_i32(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi preverse(const Packet1Xi& a) {
  Packet1Xu idx = __riscv_vrsub_vx_u32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xi>::size),
                                         unpacket_traits<Packet1Xi>::size - 1, unpacket_traits<Packet1Xi>::size);
  return __riscv_vrgather_vv_i32m1(a, idx, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pabs(const Packet1Xi& a) {
  Packet1Xi mask = __riscv_vsra_vx_i32m1(a, 31, unpacket_traits<Packet1Xi>::size);
  return __riscv_vsub_vv_i32m1(__riscv_vxor_vv_i32m1(a, mask, unpacket_traits<Packet1Xi>::size), mask,
                               unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux<Packet1Xi>(const Packet1Xi& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i32m1_i32m1(a, __riscv_vmv_v_x_i32m1(0, unpacket_traits<Packet1Xi>::size),
                                                      unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_mul<Packet1Xi>(const Packet1Xi& a) {
  // Multiply the vector by its reverse
  Packet1Xi prod = __riscv_vmul_vv_i32m1(preverse(a), a, unpacket_traits<Packet1Xi>::size);
  Packet1Xi half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_i32m1(prod, 8, unpacket_traits<Packet1Xi>::size);
    prod = __riscv_vmul_vv_i32m1(prod, half_prod, unpacket_traits<Packet1Xi>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_i32m1(prod, 4, unpacket_traits<Packet1Xi>::size);
    prod = __riscv_vmul_vv_i32m1(prod, half_prod, unpacket_traits<Packet1Xi>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_i32m1(prod, 2, unpacket_traits<Packet1Xi>::size);
    prod = __riscv_vmul_vv_i32m1(prod, half_prod, unpacket_traits<Packet1Xi>::size);
  }
  // Last reduction
  half_prod = __riscv_vslidedown_vx_i32m1(prod, 1, unpacket_traits<Packet1Xi>::size);
  prod = __riscv_vmul_vv_i32m1(prod, half_prod, unpacket_traits<Packet1Xi>::size);

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_min<Packet1Xi>(const Packet1Xi& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i32m1_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::max)(), unpacket_traits<Packet1Xi>::size),
      unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_max<Packet1Xi>(const Packet1Xi& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i32m1_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::min)(), unpacket_traits<Packet1Xi>::size),
      unpacket_traits<Packet1Xi>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xi, N>& kernel) {
  numext::int32_t buffer[unpacket_traits<Packet1Xi>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(numext::int32_t), kernel.packet[i], unpacket_traits<Packet1Xi>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_i32m1(&buffer[i * unpacket_traits<Packet1Xi>::size], unpacket_traits<Packet1Xi>::size);
  }
}

/********************************* Packet1Xf ************************************/

EIGEN_STRONG_INLINE Packet1Xf __riscv_vreinterpret_v_u64m1_f32m1(const Packet1Xul& a) {
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vreinterpret_v_u64m1_u32m1(a));
}

EIGEN_STRONG_INLINE Packet2Xf __riscv_vreinterpret_v_u64m2_f32m2(const Packet2Xul& a) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vreinterpret_v_u64m2_u32m2(a));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf ptrue<Packet1Xf>(const Packet1Xf& /*a*/) {
  return __riscv_vreinterpret_f32m1(__riscv_vmv_v_x_u32m1(0xffffffffu, unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pzero<Packet1Xf>(const Packet1Xf& /*a*/) {
  return __riscv_vfmv_v_f_f32m1(0.0f, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pabs(const Packet1Xf& a) {
  return __riscv_vfabs_v_f32m1(a, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pabsdiff(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfabs_v_f32m1(__riscv_vfsub_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size),
                               unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pset1<Packet1Xf>(const float& from) {
  return __riscv_vfmv_v_f_f32m1(from, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pset1frombits<Packet1Xf>(numext::uint32_t from) {
  return __riscv_vreinterpret_f32m1(__riscv_vmv_v_x_u32m1(from, unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf plset<Packet1Xf>(const float& a) {
  Packet1Xf idx = __riscv_vfcvt_f_x_v_f32m1(
      __riscv_vreinterpret_v_u32m1_i32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xi>::size)),
      unpacket_traits<Packet1Xf>::size);
  return __riscv_vfadd_vf_f32m1(idx, a, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet1Xf>(const float* a, Packet1Xf& a0, Packet1Xf& a1, Packet1Xf& a2,
                                                Packet1Xf& a3) {
  Packet1Xf aa = __riscv_vle32_v_f32m1(a, 4);
  a0 = __riscv_vrgather_vx_f32m1(aa, 0, unpacket_traits<Packet1Xf>::size);
  a1 = __riscv_vrgather_vx_f32m1(aa, 1, unpacket_traits<Packet1Xf>::size);
  a2 = __riscv_vrgather_vx_f32m1(aa, 2, unpacket_traits<Packet1Xf>::size);
  a3 = __riscv_vrgather_vx_f32m1(aa, 3, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf padd<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfadd_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf psub<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfsub_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pnegate(const Packet1Xf& a) {
  return __riscv_vfneg_v_f32m1(a, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf psignbit(const Packet1Xf& a) {
  return __riscv_vreinterpret_v_i32m1_f32m1(
      __riscv_vsra_vx_i32m1(__riscv_vreinterpret_v_f32m1_i32m1(a), 31, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pconj(const Packet1Xf& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmul<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfmul_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pdiv<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfdiv_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmadd(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c) {
  return __riscv_vfmadd_vv_f32m1(a, b, c, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmsub(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c) {
  return __riscv_vfmsub_vv_f32m1(a, b, c, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pnmadd(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c) {
  return __riscv_vfnmsub_vv_f32m1(a, b, c, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pnmsub(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c) {
  return __riscv_vfnmadd_vv_f32m1(a, b, c, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmin<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  Packet1Xf nans = __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet1Xf>::size);
  PacketMask32 mask = __riscv_vmfeq_vv_f32m1_b32(a, a, unpacket_traits<Packet1Xf>::size);
  PacketMask32 mask2 = __riscv_vmfeq_vv_f32m1_b32(b, b, unpacket_traits<Packet1Xf>::size);
  mask = __riscv_vmand_mm_b32(mask, mask2, unpacket_traits<Packet1Xf>::size);

  return __riscv_vfmin_vv_f32m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmin<PropagateNaN, Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return pmin<Packet1Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmin<PropagateNumbers, Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfmin_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmax<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  Packet1Xf nans = __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet1Xf>::size);
  PacketMask32 mask = __riscv_vmfeq_vv_f32m1_b32(a, a, unpacket_traits<Packet1Xf>::size);
  PacketMask32 mask2 = __riscv_vmfeq_vv_f32m1_b32(b, b, unpacket_traits<Packet1Xf>::size);
  mask = __riscv_vmand_mm_b32(mask, mask2, unpacket_traits<Packet1Xf>::size);

  return __riscv_vfmax_vv_f32m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmax<PropagateNaN, Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return pmax<Packet1Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pmax<PropagateNumbers, Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vfmax_vv_f32m1(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pcmp_le<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  PacketMask32 mask = __riscv_vmfle_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
  return __riscv_vmerge_vvm_f32m1(pzero<Packet1Xf>(a), ptrue<Packet1Xf>(a), mask, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pcmp_lt<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  PacketMask32 mask = __riscv_vmflt_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
  return __riscv_vmerge_vvm_f32m1(pzero<Packet1Xf>(a), ptrue<Packet1Xf>(a), mask, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pcmp_eq<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  PacketMask32 mask = __riscv_vmfeq_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
  return __riscv_vmerge_vvm_f32m1(pzero<Packet1Xf>(a), ptrue<Packet1Xf>(a), mask, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pcmp_lt_or_nan<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  PacketMask32 mask = __riscv_vmfge_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
  return __riscv_vfmerge_vfm_f32m1(ptrue<Packet1Xf>(a), 0.0f, mask, unpacket_traits<Packet1Xf>::size);
}

// Logical Operations are not supported for float, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet1Xf pand<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vand_vv_u32m1(
      __riscv_vreinterpret_v_f32m1_u32m1(a), __riscv_vreinterpret_v_f32m1_u32m1(b), unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf por<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vor_vv_u32m1(
      __riscv_vreinterpret_v_f32m1_u32m1(a), __riscv_vreinterpret_v_f32m1_u32m1(b), unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pxor<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vxor_vv_u32m1(
      __riscv_vreinterpret_v_f32m1_u32m1(a), __riscv_vreinterpret_v_f32m1_u32m1(b), unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pandnot<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vand_vv_u32m1(
      __riscv_vreinterpret_v_f32m1_u32m1(a),
      __riscv_vnot_v_u32m1(__riscv_vreinterpret_v_f32m1_u32m1(b), unpacket_traits<Packet1Xf>::size),
      unpacket_traits<Packet1Xf>::size));
#else
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vandn_vv_u32m1(
      __riscv_vreinterpret_v_f32m1_u32m1(a), __riscv_vreinterpret_v_f32m1_u32m1(b), unpacket_traits<Packet1Xi>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pload<Packet1Xf>(const float* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_f32m1(from, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf ploadu<Packet1Xf>(const float* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_f32m1(from, unpacket_traits<Packet1Xf>::size);
}

EIGEN_STRONG_INLINE Packet2Xf pdup(const Packet1Xf& a) {
  Packet2Xul data = __riscv_vwcvtu_x_x_v_u64m2(__riscv_vreinterpret_v_f32m1_u32m1(a), unpacket_traits<Packet1Xi>::size);
  return __riscv_vreinterpret_v_u64m2_f32m2(__riscv_vadd_vv_u64m2(
      __riscv_vsll_vx_u64m2(data, 32, unpacket_traits<Packet2Xl>::size), data, unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf ploaddup<Packet1Xf>(const float* from) {
  Packet1Xul data = __riscv_vlmul_trunc_v_u64m2_u64m1(__riscv_vwcvtu_x_x_v_u64m2(
      __riscv_vreinterpret_v_f32m1_u32m1(pload<Packet1Xf>(from)), unpacket_traits<Packet1Xi>::size));
  return __riscv_vreinterpret_v_u64m1_f32m1(__riscv_vadd_vv_u64m1(
      __riscv_vsll_vx_u64m1(data, 32, unpacket_traits<Packet1Xl>::size), data, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xf ploadquad<Packet1Xf>(const float* from) {
  Packet1Xu idx =
      __riscv_vsrl_vx_u32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xf>::size), 2, unpacket_traits<Packet1Xf>::size);
  return __riscv_vrgather_vv_f32m1(pload<Packet1Xf>(from), idx, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<float>(float* to, const Packet1Xf& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_f32m1(to, from, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<float>(float* to, const Packet1Xf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_f32m1(to, from, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xf pgather<float, Packet1Xf>(const float* from, Index stride) {
  return __riscv_vlse32_v_f32m1(from, stride * sizeof(float), unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<float, Packet1Xf>(float* to, const Packet1Xf& from, Index stride) {
  __riscv_vsse32(to, stride * sizeof(float), from, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE float pfirst<Packet1Xf>(const Packet1Xf& a) {
  return __riscv_vfmv_f_s_f32m1_f32(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf psqrt(const Packet1Xf& a) {
  return __riscv_vfsqrt_v_f32m1(a, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf print<Packet1Xf>(const Packet1Xf& a) {
  const Packet1Xf limit = pset1<Packet1Xf>(static_cast<float>(1 << 23));
  const Packet1Xf abs_a = pabs(a);

  PacketMask32 mask = __riscv_vmfne_vv_f32m1_b32(a, a, unpacket_traits<Packet1Xf>::size);
  const Packet1Xf x = __riscv_vfadd_vv_f32m1_tumu(mask, a, a, a, unpacket_traits<Packet1Xf>::size);
  const Packet1Xf new_x = __riscv_vfcvt_f_x_v_f32m1(__riscv_vfcvt_x_f_v_i32m1(a, unpacket_traits<Packet1Xf>::size),
                                                    unpacket_traits<Packet1Xf>::size);

  mask = __riscv_vmflt_vv_f32m1_b32(abs_a, limit, unpacket_traits<Packet1Xf>::size);
  Packet1Xf signed_x = __riscv_vfsgnj_vv_f32m1(new_x, x, unpacket_traits<Packet1Xf>::size);
  return __riscv_vmerge_vvm_f32m1(x, signed_x, mask, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pfloor<Packet1Xf>(const Packet1Xf& a) {
  Packet1Xf tmp = print<Packet1Xf>(a);
  // If greater, subtract one.
  PacketMask32 mask = __riscv_vmflt_vv_f32m1_b32(a, tmp, unpacket_traits<Packet1Xf>::size);
  return __riscv_vfsub_vf_f32m1_tumu(mask, tmp, tmp, 1.0f, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf preverse(const Packet1Xf& a) {
  Packet1Xu idx = __riscv_vrsub_vx_u32m1(__riscv_vid_v_u32m1(unpacket_traits<Packet1Xf>::size),
                                         unpacket_traits<Packet1Xf>::size - 1, unpacket_traits<Packet1Xf>::size);
  return __riscv_vrgather_vv_f32m1(a, idx, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pfrexp<Packet1Xf>(const Packet1Xf& a, Packet1Xf& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE float predux<Packet1Xf>(const Packet1Xf& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f32m1_f32m1(
      a, __riscv_vfmv_v_f_f32m1(0.0f, unpacket_traits<Packet1Xf>::size), unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE float predux_mul<Packet1Xf>(const Packet1Xf& a) {
  // Multiply the vector by its reverse
  Packet1Xf prod = __riscv_vfmul_vv_f32m1(preverse(a), a, unpacket_traits<Packet1Xf>::size);
  Packet1Xf half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_f32m1(prod, 8, unpacket_traits<Packet1Xf>::size);
    prod = __riscv_vfmul_vv_f32m1(prod, half_prod, unpacket_traits<Packet1Xf>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_f32m1(prod, 4, unpacket_traits<Packet1Xf>::size);
    prod = __riscv_vfmul_vv_f32m1(prod, half_prod, unpacket_traits<Packet1Xf>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_f32m1(prod, 2, unpacket_traits<Packet1Xf>::size);
    prod = __riscv_vfmul_vv_f32m1(prod, half_prod, unpacket_traits<Packet1Xf>::size);
  }
  // Last reduction
  half_prod = __riscv_vslidedown_vx_f32m1(prod, 1, unpacket_traits<Packet1Xf>::size);
  prod = __riscv_vfmul_vv_f32m1(prod, half_prod, unpacket_traits<Packet1Xf>::size);

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE float predux_min<Packet1Xf>(const Packet1Xf& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f32m1_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet1Xf>::size),
          unpacket_traits<Packet1Xf>::size)),
      (std::numeric_limits<float>::max)());
}

template <>
EIGEN_STRONG_INLINE float predux_max<Packet1Xf>(const Packet1Xf& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f32m1_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet1Xf>::size),
          unpacket_traits<Packet1Xf>::size)),
      -(std::numeric_limits<float>::max)());
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xf, N>& kernel) {
  float buffer[unpacket_traits<Packet1Xf>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(float), kernel.packet[i], unpacket_traits<Packet1Xf>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_f32m1(&buffer[i * unpacket_traits<Packet1Xf>::size], unpacket_traits<Packet1Xf>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pldexp<Packet1Xf>(const Packet1Xf& a, const Packet1Xf& exponent) {
  return pldexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE PacketMask32 por(const PacketMask32& a, const PacketMask32& b) {
  return __riscv_vmor_mm_b32(a, b, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE PacketMask32 pand(const PacketMask32& a, const PacketMask32& b) {
  return __riscv_vmand_mm_b32(a, b, unpacket_traits<Packet1Xf>::size);
}

EIGEN_STRONG_INLINE PacketMask32 pcmp_eq_mask(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vmfeq_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
}

EIGEN_STRONG_INLINE PacketMask32 pcmp_lt_mask(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vmflt_vv_f32m1_b32(a, b, unpacket_traits<Packet1Xf>::size);
}

EIGEN_STRONG_INLINE Packet1Xf pselect(const PacketMask32& mask, const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vmerge_vvm_f32m1(b, a, mask, unpacket_traits<Packet1Xf>::size);
}

EIGEN_STRONG_INLINE Packet1Xf pselect(const Packet1Xf& mask, const Packet1Xf& a, const Packet1Xf& b) {
  PacketMask32 mask2 =
      __riscv_vmsne_vx_i32m1_b32(__riscv_vreinterpret_v_f32m1_i32m1(mask), 0, unpacket_traits<Packet1Xf>::size);
  return __riscv_vmerge_vvm_f32m1(b, a, mask2, unpacket_traits<Packet1Xf>::size);
}

/********************************* Packet1Xl ************************************/

template <>
EIGEN_STRONG_INLINE Packet1Xl pset1<Packet1Xl>(const numext::int64_t& from) {
  return __riscv_vmv_v_x_i64m1(from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl plset<Packet1Xl>(const numext::int64_t& a) {
  Packet1Xl idx = __riscv_vreinterpret_v_u64m1_i64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size));
  return __riscv_vadd_vx_i64m1(idx, a, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pzero<Packet1Xl>(const Packet1Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m1(0, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl padd<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vadd_vv_i64m1(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl psub<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pnegate(const Packet1Xl& a) {
  return __riscv_vneg(a, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pconj(const Packet1Xl& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pmul<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pdiv<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pmadd(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pmsub(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pnmadd(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c) {
  return __riscv_vnmsub_vv_i64m1(a, b, c, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pnmsub(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c) {
  return __riscv_vnmsub_vv_i64m1(a, b, pnegate(c), unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pmin<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pmax<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pcmp_le<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  PacketMask64 mask = __riscv_vmsle_vv_i64m1_b64(a, b, unpacket_traits<Packet1Xl>::size);
  return __riscv_vmerge_vxm_i64m1(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pcmp_lt<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  PacketMask64 mask = __riscv_vmslt_vv_i64m1_b64(a, b, unpacket_traits<Packet1Xl>::size);
  return __riscv_vmerge_vxm_i64m1(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pcmp_eq<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  PacketMask64 mask = __riscv_vmseq_vv_i64m1_b64(a, b, unpacket_traits<Packet1Xl>::size);
  return __riscv_vmerge_vxm_i64m1(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl ptrue<Packet1Xl>(const Packet1Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m1(0xffffffffffffffffu, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pand<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vand_vv_i64m1(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl por<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vor_vv_i64m1(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pxor<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vxor_vv_i64m1(a, b, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pandnot<Packet1Xl>(const Packet1Xl& a, const Packet1Xl& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i64m1(a, __riscv_vnot_v_i64m1(b, unpacket_traits<Packet1Xl>::size),
                               unpacket_traits<Packet1Xl>::size);
#else
  return __riscv_vreinterpret_v_u64m1_i64m1(__riscv_vandn_vv_u64m1(
      __riscv_vreinterpret_v_i64m1_u64m1(a), __riscv_vreinterpret_v_i64m1_u64m1(b), unpacket_traits<Packet1Xl>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xl parithmetic_shift_right(Packet1Xl a) {
  return __riscv_vsra_vx_i64m1(a, N, unpacket_traits<Packet1Xl>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xl plogical_shift_right(Packet1Xl a) {
  return __riscv_vreinterpret_i64m1(
      __riscv_vsrl_vx_u64m1(__riscv_vreinterpret_u64m1(a), N, unpacket_traits<Packet1Xl>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xl plogical_shift_left(Packet1Xl a) {
  return __riscv_vsll_vx_i64m1(a, N, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pload<Packet1Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_i64m1(from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl ploadu<Packet1Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_i64m1(from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl ploaddup<Packet1Xl>(const numext::int64_t* from) {
  Packet1Xul idx =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size), 1, unpacket_traits<Packet1Xl>::size);
  return __riscv_vrgather_vv_i64m1(pload<Packet1Xl>(from), idx, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl ploadquad<Packet1Xl>(const numext::int64_t* from) {
  Packet1Xul idx =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size), 2, unpacket_traits<Packet1Xl>::size);
  return __riscv_vrgather_vv_i64m1(pload<Packet1Xl>(from), idx, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int64_t>(numext::int64_t* to, const Packet1Xl& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_i64m1(to, from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int64_t>(numext::int64_t* to, const Packet1Xl& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_i64m1(to, from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xl pgather<numext::int64_t, Packet1Xl>(const numext::int64_t* from, Index stride) {
  return __riscv_vlse64_v_i64m1(from, stride * sizeof(numext::int64_t), unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int64_t, Packet1Xl>(numext::int64_t* to, const Packet1Xl& from,
                                                                   Index stride) {
  __riscv_vsse64(to, stride * sizeof(numext::int64_t), from, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t pfirst<Packet1Xl>(const Packet1Xl& a) {
  return __riscv_vmv_x_s_i64m1_i64(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl preverse(const Packet1Xl& a) {
  Packet1Xul idx = __riscv_vrsub_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size),
                                          unpacket_traits<Packet1Xl>::size - 1, unpacket_traits<Packet1Xl>::size);
  return __riscv_vrgather_vv_i64m1(a, idx, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pabs(const Packet1Xl& a) {
  Packet1Xl mask = __riscv_vsra_vx_i64m1(a, 63, unpacket_traits<Packet1Xl>::size);
  return __riscv_vsub_vv_i64m1(__riscv_vxor_vv_i64m1(a, mask, unpacket_traits<Packet1Xl>::size), mask,
                               unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux<Packet1Xl>(const Packet1Xl& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i64m1_i64m1(a, __riscv_vmv_v_x_i64m1(0, unpacket_traits<Packet1Xl>::size),
                                                      unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_mul<Packet1Xl>(const Packet1Xl& a) {
  // Multiply the vector by its reverse
  Packet1Xl prod = __riscv_vmul_vv_i64m1(preverse(a), a, unpacket_traits<Packet1Xl>::size);
  Packet1Xl half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_i64m1(prod, 4, unpacket_traits<Packet1Xl>::size);
    prod = __riscv_vmul_vv_i64m1(prod, half_prod, unpacket_traits<Packet1Xl>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_i64m1(prod, 2, unpacket_traits<Packet1Xl>::size);
    prod = __riscv_vmul_vv_i64m1(prod, half_prod, unpacket_traits<Packet1Xl>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_i64m1(prod, 1, unpacket_traits<Packet1Xl>::size);
    prod = __riscv_vmul_vv_i64m1(prod, half_prod, unpacket_traits<Packet1Xl>::size);
  }

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_min<Packet1Xl>(const Packet1Xl& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i64m1_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::max)(), unpacket_traits<Packet1Xl>::size),
      unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_max<Packet1Xl>(const Packet1Xl& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i64m1_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::min)(), unpacket_traits<Packet1Xl>::size),
      unpacket_traits<Packet1Xl>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xl, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet1Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), kernel.packet[i], unpacket_traits<Packet1Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_i64m1(&buffer[i * unpacket_traits<Packet1Xl>::size], unpacket_traits<Packet1Xl>::size);
  }
}

/********************************* Packet1Xd ************************************/

template <>
EIGEN_STRONG_INLINE Packet1Xd ptrue<Packet1Xd>(const Packet1Xd& /*a*/) {
  return __riscv_vreinterpret_f64m1(__riscv_vmv_v_x_u64m1(0xffffffffffffffffu, unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pzero<Packet1Xd>(const Packet1Xd& /*a*/) {
  return __riscv_vfmv_v_f_f64m1(0.0, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pabs(const Packet1Xd& a) {
  return __riscv_vfabs_v_f64m1(a, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pabsdiff(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfabs_v_f64m1(__riscv_vfsub_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size),
                               unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pset1<Packet1Xd>(const double& from) {
  return __riscv_vfmv_v_f_f64m1(from, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pset1frombits<Packet1Xd>(numext::uint64_t from) {
  return __riscv_vreinterpret_f64m1(__riscv_vmv_v_x_u64m1(from, unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd plset<Packet1Xd>(const double& a) {
  Packet1Xd idx = __riscv_vfcvt_f_x_v_f64m1(
      __riscv_vreinterpret_v_u64m1_i64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size)),
      unpacket_traits<Packet1Xd>::size);
  return __riscv_vfadd_vf_f64m1(idx, a, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet1Xd>(const double* a, Packet1Xd& a0, Packet1Xd& a1, Packet1Xd& a2,
                                                Packet1Xd& a3) {
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    Packet1Xd aa = __riscv_vle64_v_f64m1(a, 4);
    a0 = __riscv_vrgather_vx_f64m1(aa, 0, unpacket_traits<Packet1Xd>::size);
    a1 = __riscv_vrgather_vx_f64m1(aa, 1, unpacket_traits<Packet1Xd>::size);
    a2 = __riscv_vrgather_vx_f64m1(aa, 2, unpacket_traits<Packet1Xd>::size);
    a3 = __riscv_vrgather_vx_f64m1(aa, 3, unpacket_traits<Packet1Xd>::size);
  } else {
    Packet2Xd aa = __riscv_vle64_v_f64m2(a, 4);
    Packet1Xd aa0 = __riscv_vget_v_f64m2_f64m1(aa, 0);
    Packet1Xd aa1 = __riscv_vget_v_f64m2_f64m1(aa, 1);
    a0 = __riscv_vrgather_vx_f64m1(aa0, 0, unpacket_traits<Packet1Xd>::size);
    a1 = __riscv_vrgather_vx_f64m1(aa0, 1, unpacket_traits<Packet1Xd>::size);
    a2 = __riscv_vrgather_vx_f64m1(aa1, 0, unpacket_traits<Packet1Xd>::size);
    a3 = __riscv_vrgather_vx_f64m1(aa1, 1, unpacket_traits<Packet1Xd>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet1Xd padd<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfadd_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd psub<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfsub_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pnegate(const Packet1Xd& a) {
  return __riscv_vfneg_v_f64m1(a, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd psignbit(const Packet1Xd& a) {
  return __riscv_vreinterpret_v_i64m1_f64m1(
      __riscv_vsra_vx_i64m1(__riscv_vreinterpret_v_f64m1_i64m1(a), 63, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pconj(const Packet1Xd& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmul<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfmul_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pdiv<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfdiv_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmadd(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c) {
  return __riscv_vfmadd_vv_f64m1(a, b, c, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmsub(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c) {
  return __riscv_vfmsub_vv_f64m1(a, b, c, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pnmadd(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c) {
  return __riscv_vfnmsub_vv_f64m1(a, b, c, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pnmsub(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c) {
  return __riscv_vfnmadd_vv_f64m1(a, b, c, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmin<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  Packet1Xd nans = __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet1Xd>::size);
  PacketMask64 mask = __riscv_vmfeq_vv_f64m1_b64(a, a, unpacket_traits<Packet1Xd>::size);
  PacketMask64 mask2 = __riscv_vmfeq_vv_f64m1_b64(b, b, unpacket_traits<Packet1Xd>::size);
  mask = __riscv_vmand_mm_b64(mask, mask2, unpacket_traits<Packet1Xd>::size);

  return __riscv_vfmin_vv_f64m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmin<PropagateNaN, Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return pmin<Packet1Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmin<PropagateNumbers, Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfmin_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmax<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  Packet1Xd nans = __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet1Xd>::size);
  PacketMask64 mask = __riscv_vmfeq_vv_f64m1_b64(a, a, unpacket_traits<Packet1Xd>::size);
  PacketMask64 mask2 = __riscv_vmfeq_vv_f64m1_b64(b, b, unpacket_traits<Packet1Xd>::size);
  mask = __riscv_vmand_mm_b64(mask, mask2, unpacket_traits<Packet1Xd>::size);

  return __riscv_vfmax_vv_f64m1_tumu(mask, nans, a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmax<PropagateNaN, Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return pmax<Packet1Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pmax<PropagateNumbers, Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vfmax_vv_f64m1(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pcmp_le<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  PacketMask64 mask = __riscv_vmfle_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
  return __riscv_vmerge_vvm_f64m1(pzero<Packet1Xd>(a), ptrue<Packet1Xd>(a), mask, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pcmp_lt<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  PacketMask64 mask = __riscv_vmflt_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
  return __riscv_vmerge_vvm_f64m1(pzero<Packet1Xd>(a), ptrue<Packet1Xd>(a), mask, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pcmp_eq<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  PacketMask64 mask = __riscv_vmfeq_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
  return __riscv_vmerge_vvm_f64m1(pzero<Packet1Xd>(a), ptrue<Packet1Xd>(a), mask, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pcmp_lt_or_nan<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  PacketMask64 mask = __riscv_vmfge_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
  return __riscv_vfmerge_vfm_f64m1(ptrue<Packet1Xd>(a), 0.0, mask, unpacket_traits<Packet1Xd>::size);
}

// Logical Operations are not supported for double, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet1Xd pand<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vand_vv_u64m1(
      __riscv_vreinterpret_v_f64m1_u64m1(a), __riscv_vreinterpret_v_f64m1_u64m1(b), unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd por<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vor_vv_u64m1(
      __riscv_vreinterpret_v_f64m1_u64m1(a), __riscv_vreinterpret_v_f64m1_u64m1(b), unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pxor<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vxor_vv_u64m1(
      __riscv_vreinterpret_v_f64m1_u64m1(a), __riscv_vreinterpret_v_f64m1_u64m1(b), unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pandnot<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vand_vv_u64m1(
      __riscv_vreinterpret_v_f64m1_u64m1(a),
      __riscv_vnot_v_u64m1(__riscv_vreinterpret_v_f64m1_u64m1(b), unpacket_traits<Packet1Xd>::size),
      unpacket_traits<Packet1Xd>::size));
#else
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vandn_vv_u64m1(
      __riscv_vreinterpret_v_f64m1_u64m1(a), __riscv_vreinterpret_v_f64m1_u64m1(b), unpacket_traits<Packet1Xl>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pload<Packet1Xd>(const double* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_f64m1(from, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd ploadu<Packet1Xd>(const double* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_f64m1(from, unpacket_traits<Packet1Xd>::size);
}

EIGEN_STRONG_INLINE Packet2Xd pdup(const Packet1Xd& a) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 1, unpacket_traits<Packet2Xd>::size);
  return __riscv_vrgather_vv_f64m2(__riscv_vlmul_ext_v_f64m1_f64m2(a), idx, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd ploaddup<Packet1Xd>(const double* from) {
  Packet1Xul idx =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xd>::size), 1, unpacket_traits<Packet1Xd>::size);
  return __riscv_vrgather_vv_f64m1(pload<Packet1Xd>(from), idx, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd ploadquad<Packet1Xd>(const double* from) {
  Packet1Xul idx =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xd>::size), 2, unpacket_traits<Packet1Xd>::size);
  return __riscv_vrgather_vv_f64m1(pload<Packet1Xd>(from), idx, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<double>(double* to, const Packet1Xd& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_f64m1(to, from, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<double>(double* to, const Packet1Xd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_f64m1(to, from, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xd pgather<double, Packet1Xd>(const double* from, Index stride) {
  return __riscv_vlse64_v_f64m1(from, stride * sizeof(double), unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<double, Packet1Xd>(double* to, const Packet1Xd& from, Index stride) {
  __riscv_vsse64(to, stride * sizeof(double), from, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE double pfirst<Packet1Xd>(const Packet1Xd& a) {
  return __riscv_vfmv_f_s_f64m1_f64(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd psqrt(const Packet1Xd& a) {
  return __riscv_vfsqrt_v_f64m1(a, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd print<Packet1Xd>(const Packet1Xd& a) {
  const Packet1Xd limit = pset1<Packet1Xd>(static_cast<double>(1ull << 52));
  const Packet1Xd abs_a = pabs(a);

  PacketMask64 mask = __riscv_vmfne_vv_f64m1_b64(a, a, unpacket_traits<Packet1Xd>::size);
  const Packet1Xd x = __riscv_vfadd_vv_f64m1_tumu(mask, a, a, a, unpacket_traits<Packet1Xd>::size);
  const Packet1Xd new_x = __riscv_vfcvt_f_x_v_f64m1(__riscv_vfcvt_x_f_v_i64m1(a, unpacket_traits<Packet1Xd>::size),
                                                    unpacket_traits<Packet1Xd>::size);

  mask = __riscv_vmflt_vv_f64m1_b64(abs_a, limit, unpacket_traits<Packet1Xd>::size);
  Packet1Xd signed_x = __riscv_vfsgnj_vv_f64m1(new_x, x, unpacket_traits<Packet1Xd>::size);
  return __riscv_vmerge_vvm_f64m1(x, signed_x, mask, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pfloor<Packet1Xd>(const Packet1Xd& a) {
  Packet1Xd tmp = print<Packet1Xd>(a);
  // If greater, subtract one.
  PacketMask64 mask = __riscv_vmflt_vv_f64m1_b64(a, tmp, unpacket_traits<Packet1Xd>::size);
  return __riscv_vfsub_vf_f64m1_tumu(mask, tmp, tmp, 1.0, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd preverse(const Packet1Xd& a) {
  Packet1Xul idx = __riscv_vrsub_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xd>::size),
                                          unpacket_traits<Packet1Xd>::size - 1, unpacket_traits<Packet1Xd>::size);
  return __riscv_vrgather_vv_f64m1(a, idx, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pfrexp<Packet1Xd>(const Packet1Xd& a, Packet1Xd& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE double predux<Packet1Xd>(const Packet1Xd& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f64m1_f64m1(
      a, __riscv_vfmv_v_f_f64m1(0.0, unpacket_traits<Packet1Xd>::size), unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE double predux_mul<Packet1Xd>(const Packet1Xd& a) {
  // Multiply the vector by its reverse
  Packet1Xd prod = __riscv_vfmul_vv_f64m1(preverse(a), a, unpacket_traits<Packet1Xd>::size);
  Packet1Xd half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_f64m1(prod, 4, unpacket_traits<Packet1Xd>::size);
    prod = __riscv_vfmul_vv_f64m1(prod, half_prod, unpacket_traits<Packet1Xd>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_f64m1(prod, 2, unpacket_traits<Packet1Xd>::size);
    prod = __riscv_vfmul_vv_f64m1(prod, half_prod, unpacket_traits<Packet1Xd>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_f64m1(prod, 1, unpacket_traits<Packet1Xd>::size);
    prod = __riscv_vfmul_vv_f64m1(prod, half_prod, unpacket_traits<Packet1Xd>::size);
  }

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE double predux_min<Packet1Xd>(const Packet1Xd& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f64m1_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet1Xd>::size),
          unpacket_traits<Packet1Xd>::size)),
      (std::numeric_limits<double>::max)());
}

template <>
EIGEN_STRONG_INLINE double predux_max<Packet1Xd>(const Packet1Xd& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f64m1_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet1Xd>::size),
          unpacket_traits<Packet1Xd>::size)),
      -(std::numeric_limits<double>::max)());
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xd, N>& kernel) {
  double buffer[unpacket_traits<Packet1Xd>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(double), kernel.packet[i], unpacket_traits<Packet1Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_f64m1(&buffer[i * unpacket_traits<Packet1Xd>::size], unpacket_traits<Packet1Xd>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pldexp<Packet1Xd>(const Packet1Xd& a, const Packet1Xd& exponent) {
  return pldexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE PacketMask64 por(const PacketMask64& a, const PacketMask64& b) {
  return __riscv_vmor_mm_b64(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE PacketMask64 pandnot(const PacketMask64& a, const PacketMask64& b) {
  return __riscv_vmor_mm_b64(a, b, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE PacketMask64 pand(const PacketMask64& a, const PacketMask64& b) {
  return __riscv_vmand_mm_b64(a, b, unpacket_traits<Packet1Xd>::size);
}

EIGEN_STRONG_INLINE PacketMask64 pcmp_eq_mask(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vmfeq_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
}

EIGEN_STRONG_INLINE PacketMask64 pcmp_lt_mask(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vmflt_vv_f64m1_b64(a, b, unpacket_traits<Packet1Xd>::size);
}

EIGEN_STRONG_INLINE Packet1Xd pselect(const PacketMask64& mask, const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vmerge_vvm_f64m1(b, a, mask, unpacket_traits<Packet1Xd>::size);
}

EIGEN_STRONG_INLINE Packet1Xd pselect(const Packet1Xd& mask, const Packet1Xd& a, const Packet1Xd& b) {
  PacketMask64 mask2 =
      __riscv_vmsne_vx_i64m1_b64(__riscv_vreinterpret_v_f64m1_i64m1(mask), 0, unpacket_traits<Packet1Xd>::size);
  return __riscv_vmerge_vvm_f64m1(b, a, mask2, unpacket_traits<Packet1Xd>::size);
}

/********************************* Packet1Xs ************************************/

EIGEN_STRONG_INLINE Packet1Xs __riscv_vreinterpret_v_u32m1_i16m1(const Packet1Xu& a) {
  return __riscv_vreinterpret_v_i32m1_i16m1(__riscv_vreinterpret_v_u32m1_i32m1(a));
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pset1<Packet1Xs>(const numext::int16_t& from) {
  return __riscv_vmv_v_x_i16m1(from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs plset<Packet1Xs>(const numext::int16_t& a) {
  Packet1Xs idx = __riscv_vreinterpret_v_u16m1_i16m1(__riscv_vid_v_u16m1(unpacket_traits<Packet1Xs>::size));
  return __riscv_vadd_vx_i16m1(idx, a, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pzero<Packet1Xs>(const Packet1Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m1(0, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs padd<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vadd_vv_i16m1(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs psub<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pnegate(const Packet1Xs& a) {
  return __riscv_vneg(a, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pconj(const Packet1Xs& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pmul<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pdiv<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pmadd(const Packet1Xs& a, const Packet1Xs& b, const Packet1Xs& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pmsub(const Packet1Xs& a, const Packet1Xs& b, const Packet1Xs& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pnmadd(const Packet1Xs& a, const Packet1Xs& b, const Packet1Xs& c) {
  return __riscv_vnmsub_vv_i16m1(a, b, c, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pnmsub(const Packet1Xs& a, const Packet1Xs& b, const Packet1Xs& c) {
  return __riscv_vnmsub_vv_i16m1(a, b, pnegate(c), unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pmin<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pmax<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pcmp_le<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  PacketMask16 mask = __riscv_vmsle_vv_i16m1_b16(a, b, unpacket_traits<Packet1Xs>::size);
  return __riscv_vmerge_vxm_i16m1(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pcmp_lt<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  PacketMask16 mask = __riscv_vmslt_vv_i16m1_b16(a, b, unpacket_traits<Packet1Xs>::size);
  return __riscv_vmerge_vxm_i16m1(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pcmp_eq<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  PacketMask16 mask = __riscv_vmseq_vv_i16m1_b16(a, b, unpacket_traits<Packet1Xs>::size);
  return __riscv_vmerge_vxm_i16m1(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs ptrue<Packet1Xs>(const Packet1Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m1(static_cast<unsigned short>(0xffffu), unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pand<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vand_vv_i16m1(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs por<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vor_vv_i16m1(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pxor<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vxor_vv_i16m1(a, b, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pandnot<Packet1Xs>(const Packet1Xs& a, const Packet1Xs& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i16m1(a, __riscv_vnot_v_i16m1(b, unpacket_traits<Packet1Xs>::size),
                               unpacket_traits<Packet1Xs>::size);
#else
  return __riscv_vreinterpret_v_u16m1_i16m1(__riscv_vandn_vv_u16m1(
      __riscv_vreinterpret_v_i16m1_u16m1(a), __riscv_vreinterpret_v_i16m1_u16m1(b), unpacket_traits<Packet1Xs>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xs parithmetic_shift_right(Packet1Xs a) {
  return __riscv_vsra_vx_i16m1(a, N, unpacket_traits<Packet1Xs>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xs plogical_shift_right(Packet1Xs a) {
  return __riscv_vreinterpret_i16m1(
      __riscv_vsrl_vx_u16m1(__riscv_vreinterpret_u16m1(a), N, unpacket_traits<Packet1Xs>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet1Xs plogical_shift_left(Packet1Xs a) {
  return __riscv_vsll_vx_i16m1(a, N, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pload<Packet1Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_i16m1(from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs ploadu<Packet1Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_i16m1(from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs ploaddup<Packet1Xs>(const numext::int16_t* from) {
  Packet1Xu data = __riscv_vlmul_trunc_v_u32m2_u32m1(__riscv_vwcvtu_x_x_v_u32m2(
      __riscv_vreinterpret_v_i16m1_u16m1(pload<Packet1Xs>(from)), unpacket_traits<Packet1Xs>::size));
  return __riscv_vreinterpret_v_u32m1_i16m1(__riscv_vadd_vv_u32m1(
      __riscv_vsll_vx_u32m1(data, 16, unpacket_traits<Packet1Xi>::size), data, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xs ploadquad<Packet1Xs>(const numext::int16_t* from) {
  Packet1Xsu idx =
      __riscv_vsrl_vx_u16m1(__riscv_vid_v_u16m1(unpacket_traits<Packet1Xs>::size), 2, unpacket_traits<Packet1Xs>::size);
  return __riscv_vrgather_vv_i16m1(pload<Packet1Xs>(from), idx, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int16_t>(numext::int16_t* to, const Packet1Xs& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_i16m1(to, from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int16_t>(numext::int16_t* to, const Packet1Xs& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_i16m1(to, from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet1Xs pgather<numext::int16_t, Packet1Xs>(const numext::int16_t* from, Index stride) {
  return __riscv_vlse16_v_i16m1(from, stride * sizeof(numext::int16_t), unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int16_t, Packet1Xs>(numext::int16_t* to, const Packet1Xs& from,
                                                                   Index stride) {
  __riscv_vsse16(to, stride * sizeof(numext::int16_t), from, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t pfirst<Packet1Xs>(const Packet1Xs& a) {
  return __riscv_vmv_x_s_i16m1_i16(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs preverse(const Packet1Xs& a) {
  Packet1Xsu idx = __riscv_vrsub_vx_u16m1(__riscv_vid_v_u16m1(unpacket_traits<Packet1Xs>::size),
                                          unpacket_traits<Packet1Xs>::size - 1, unpacket_traits<Packet1Xs>::size);
  return __riscv_vrgather_vv_i16m1(a, idx, unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xs pabs(const Packet1Xs& a) {
  Packet1Xs mask = __riscv_vsra_vx_i16m1(a, 15, unpacket_traits<Packet1Xs>::size);
  return __riscv_vsub_vv_i16m1(__riscv_vxor_vv_i16m1(a, mask, unpacket_traits<Packet1Xs>::size), mask,
                               unpacket_traits<Packet1Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux<Packet1Xs>(const Packet1Xs& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i16m1_i16m1(a, __riscv_vmv_v_x_i16m1(0, unpacket_traits<Packet1Xs>::size),
                                                      unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_mul<Packet1Xs>(const Packet1Xs& a) {
  // Multiply the vector by its reverse
  Packet1Xs prod = __riscv_vmul_vv_i16m1(preverse(a), a, unpacket_traits<Packet1Xs>::size);
  Packet1Xs half_prod;

  if (EIGEN_RISCV64_RVV_VL >= 1024) {
    half_prod = __riscv_vslidedown_vx_i16m1(prod, 16, unpacket_traits<Packet1Xs>::size);
    prod = __riscv_vmul_vv_i16m1(prod, half_prod, unpacket_traits<Packet1Xs>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 512) {
    half_prod = __riscv_vslidedown_vx_i16m1(prod, 8, unpacket_traits<Packet1Xs>::size);
    prod = __riscv_vmul_vv_i16m1(prod, half_prod, unpacket_traits<Packet1Xs>::size);
  }
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    half_prod = __riscv_vslidedown_vx_i16m1(prod, 4, unpacket_traits<Packet1Xs>::size);
    prod = __riscv_vmul_vv_i16m1(prod, half_prod, unpacket_traits<Packet1Xs>::size);
  }
  // Last reduction
  half_prod = __riscv_vslidedown_vx_i16m1(prod, 2, unpacket_traits<Packet1Xs>::size);
  prod = __riscv_vmul_vv_i16m1(prod, half_prod, unpacket_traits<Packet1Xs>::size);

  half_prod = __riscv_vslidedown_vx_i16m1(prod, 1, unpacket_traits<Packet1Xs>::size);
  prod = __riscv_vmul_vv_i16m1(prod, half_prod, unpacket_traits<Packet1Xs>::size);

  // The reduction is done to the first element.
  return pfirst(prod);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_min<Packet1Xs>(const Packet1Xs& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i16m1_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::max)(), unpacket_traits<Packet1Xs>::size),
      unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_max<Packet1Xs>(const Packet1Xs& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i16m1_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::min)(), unpacket_traits<Packet1Xs>::size),
      unpacket_traits<Packet1Xs>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet1Xs, N>& kernel) {
  numext::int16_t buffer[unpacket_traits<Packet1Xs>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(&buffer[i], N * sizeof(numext::int16_t), kernel.packet[i], unpacket_traits<Packet1Xs>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle16_v_i16m1(&buffer[i * unpacket_traits<Packet1Xs>::size], unpacket_traits<Packet1Xs>::size);
  }
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_PACKET_MATH_RVV10_H

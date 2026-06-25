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

#ifndef EIGEN_PACKET2_MATH_RVV10_H
#define EIGEN_PACKET2_MATH_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/********************************* Packet2Xi ************************************/

EIGEN_STRONG_INLINE Packet2Xi __riscv_vreinterpret_v_u64m2_i32m2(const Packet2Xul& a) {
  return __riscv_vreinterpret_v_i64m2_i32m2(__riscv_vreinterpret_v_u64m2_i64m2(a));
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pset1<Packet2Xi>(const numext::int32_t& from) {
  return __riscv_vmv_v_x_i32m2(from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi plset<Packet2Xi>(const numext::int32_t& a) {
  Packet2Xi idx = __riscv_vreinterpret_v_u32m2_i32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet2Xi>::size));
  return __riscv_vadd_vx_i32m2(idx, a, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pzero<Packet2Xi>(const Packet2Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m2(0, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi padd<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vadd_vv_i32m2(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi psub<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pnegate(const Packet2Xi& a) {
  return __riscv_vneg(a, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pconj(const Packet2Xi& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pmul<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pdiv<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pmadd(const Packet2Xi& a, const Packet2Xi& b, const Packet2Xi& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pmsub(const Packet2Xi& a, const Packet2Xi& b, const Packet2Xi& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pnmadd(const Packet2Xi& a, const Packet2Xi& b, const Packet2Xi& c) {
  return __riscv_vnmsub_vv_i32m2(a, b, c, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pnmsub(const Packet2Xi& a, const Packet2Xi& b, const Packet2Xi& c) {
  return __riscv_vnmsub_vv_i32m2(a, b, pnegate(c), unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pmin<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pmax<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcmp_le<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  PacketMask16 mask = __riscv_vmsle_vv_i32m2_b16(a, b, unpacket_traits<Packet2Xi>::size);
  return __riscv_vmerge_vxm_i32m2(pzero(a), 0xffffffff, mask, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcmp_lt<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  PacketMask16 mask = __riscv_vmslt_vv_i32m2_b16(a, b, unpacket_traits<Packet2Xi>::size);
  return __riscv_vmerge_vxm_i32m2(pzero(a), 0xffffffff, mask, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcmp_eq<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  PacketMask16 mask = __riscv_vmseq_vv_i32m2_b16(a, b, unpacket_traits<Packet2Xi>::size);
  return __riscv_vmerge_vxm_i32m2(pzero(a), 0xffffffff, mask, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi ptrue<Packet2Xi>(const Packet2Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m2(0xffffffffu, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pand<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vand_vv_i32m2(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi por<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vor_vv_i32m2(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pxor<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
  return __riscv_vxor_vv_i32m2(a, b, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pandnot<Packet2Xi>(const Packet2Xi& a, const Packet2Xi& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i32m2(a, __riscv_vnot_v_i32m2(b, unpacket_traits<Packet2Xi>::size),
                               unpacket_traits<Packet2Xi>::size);
#else
  return __riscv_vreinterpret_v_u32m2_i32m2(__riscv_vandn_vv_u32m2(
      __riscv_vreinterpret_v_i32m2_u32m2(a), __riscv_vreinterpret_v_i32m2_u32m2(b), unpacket_traits<Packet2Xi>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xi parithmetic_shift_right(Packet2Xi a) {
  return __riscv_vsra_vx_i32m2(a, N, unpacket_traits<Packet2Xi>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xi plogical_shift_right(Packet2Xi a) {
  return __riscv_vreinterpret_i32m2(
      __riscv_vsrl_vx_u32m2(__riscv_vreinterpret_u32m2(a), N, unpacket_traits<Packet2Xi>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xi plogical_shift_left(Packet2Xi a) {
  return __riscv_vsll_vx_i32m2(a, N, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pload<Packet2Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_i32m2(from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi ploadu<Packet2Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_i32m2(from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi ploaddup<Packet2Xi>(const numext::int32_t* from) {
  Packet2Xul data = __riscv_vlmul_trunc_v_u64m4_u64m2(__riscv_vwcvtu_x_x_v_u64m4(
      __riscv_vreinterpret_v_i32m2_u32m2(pload<Packet2Xi>(from)), unpacket_traits<Packet2Xi>::size));
  return __riscv_vreinterpret_v_u64m2_i32m2(__riscv_vadd_vv_u64m2(
      __riscv_vsll_vx_u64m2(data, 32, unpacket_traits<Packet2Xl>::size), data, unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xi ploadquad<Packet2Xi>(const numext::int32_t* from) {
  Packet2Xu idx =
      __riscv_vsrl_vx_u32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet2Xi>::size), 2, unpacket_traits<Packet2Xi>::size);
  return __riscv_vrgather_vv_i32m2(pload<Packet2Xi>(from), idx, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int32_t>(numext::int32_t* to, const Packet2Xi& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_i32m2(to, from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int32_t>(numext::int32_t* to, const Packet2Xi& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_i32m2(to, from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xi pgather<numext::int32_t, Packet2Xi>(const numext::int32_t* from, Index stride) {
  return __riscv_vlse32_v_i32m2(from, stride * sizeof(numext::int32_t), unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int32_t, Packet2Xi>(numext::int32_t* to, const Packet2Xi& from,
                                                                   Index stride) {
  __riscv_vsse32(to, stride * sizeof(numext::int32_t), from, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t pfirst<Packet2Xi>(const Packet2Xi& a) {
  return __riscv_vmv_x_s_i32m2_i32(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi preverse(const Packet2Xi& a) {
  Packet2Xu idx = __riscv_vrsub_vx_u32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet2Xi>::size),
                                         unpacket_traits<Packet2Xi>::size - 1, unpacket_traits<Packet2Xi>::size);
  return __riscv_vrgather_vv_i32m2(a, idx, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pabs(const Packet2Xi& a) {
  Packet2Xi mask = __riscv_vsra_vx_i32m2(a, 31, unpacket_traits<Packet2Xi>::size);
  return __riscv_vsub_vv_i32m2(__riscv_vxor_vv_i32m2(a, mask, unpacket_traits<Packet2Xi>::size), mask,
                               unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux<Packet2Xi>(const Packet2Xi& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i32m2_i32m1(a, __riscv_vmv_v_x_i32m1(0, unpacket_traits<Packet2Xi>::size / 2),
                                                      unpacket_traits<Packet2Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_mul<Packet2Xi>(const Packet2Xi& a) {
  return predux_mul<Packet1Xi>(__riscv_vmul_vv_i32m1(__riscv_vget_v_i32m2_i32m1(a, 0), __riscv_vget_v_i32m2_i32m1(a, 1),
                                                     unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_min<Packet2Xi>(const Packet2Xi& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i32m2_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::max)(), unpacket_traits<Packet2Xi>::size / 2),
      unpacket_traits<Packet2Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_max<Packet2Xi>(const Packet2Xi& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i32m2_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::min)(), unpacket_traits<Packet2Xi>::size / 2),
      unpacket_traits<Packet2Xi>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xi, N>& kernel) {
  numext::int32_t buffer[unpacket_traits<Packet2Xi>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(numext::int32_t), kernel.packet[i], unpacket_traits<Packet2Xi>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_i32m2(&buffer[i * unpacket_traits<Packet2Xi>::size], unpacket_traits<Packet2Xi>::size);
  }
}

template <typename Packet = Packet2Xi>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xi>::value && (unpacket_traits<Packet2Xi>::size % 8) == 0, Packet1Xi>
    predux_half(const Packet2Xi& a) {
  return __riscv_vadd_vv_i32m1(__riscv_vget_v_i32m2_i32m1(a, 0), __riscv_vget_v_i32m2_i32m1(a, 1),
                               unpacket_traits<Packet1Xi>::size);
}

/********************************* Packet2Xf ************************************/

EIGEN_STRONG_INLINE Packet4Xf __riscv_vreinterpret_v_u64m4_f32m4(const Packet4Xul& a) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vreinterpret_v_u64m4_u32m4(a));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf ptrue<Packet2Xf>(const Packet2Xf& /*a*/) {
  return __riscv_vreinterpret_f32m2(__riscv_vmv_v_x_u32m2(0xffffffffu, unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pzero<Packet2Xf>(const Packet2Xf& /*a*/) {
  return __riscv_vfmv_v_f_f32m2(0.0f, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pabs(const Packet2Xf& a) {
  return __riscv_vfabs_v_f32m2(a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pabsdiff(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfabs_v_f32m2(__riscv_vfsub_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size),
                               unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pset1<Packet2Xf>(const float& from) {
  return __riscv_vfmv_v_f_f32m2(from, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pset1frombits<Packet2Xf>(numext::uint32_t from) {
  return __riscv_vreinterpret_f32m2(__riscv_vmv_v_x_u32m2(from, unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf plset<Packet2Xf>(const float& a) {
  Packet2Xf idx = __riscv_vfcvt_f_x_v_f32m2(
      __riscv_vreinterpret_v_u32m2_i32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet4Xi>::size)),
      unpacket_traits<Packet2Xf>::size);
  return __riscv_vfadd_vf_f32m2(idx, a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet2Xf>(const float* a, Packet2Xf& a0, Packet2Xf& a1, Packet2Xf& a2,
                                                Packet2Xf& a3) {
  Packet2Xf aa = __riscv_vlmul_ext_f32m2(__riscv_vle32_v_f32m1(a, 4));
  a0 = __riscv_vrgather_vx_f32m2(aa, 0, unpacket_traits<Packet2Xf>::size);
  a1 = __riscv_vrgather_vx_f32m2(aa, 1, unpacket_traits<Packet2Xf>::size);
  a2 = __riscv_vrgather_vx_f32m2(aa, 2, unpacket_traits<Packet2Xf>::size);
  a3 = __riscv_vrgather_vx_f32m2(aa, 3, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf padd<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfadd_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf psub<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfsub_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pnegate(const Packet2Xf& a) {
  return __riscv_vfneg_v_f32m2(a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf psignbit(const Packet2Xf& a) {
  return __riscv_vreinterpret_v_i32m2_f32m2(
      __riscv_vsra_vx_i32m2(__riscv_vreinterpret_v_f32m2_i32m2(a), 31, unpacket_traits<Packet2Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pconj(const Packet2Xf& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmul<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfmul_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pdiv<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfdiv_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmadd(const Packet2Xf& a, const Packet2Xf& b, const Packet2Xf& c) {
  return __riscv_vfmadd_vv_f32m2(a, b, c, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmsub(const Packet2Xf& a, const Packet2Xf& b, const Packet2Xf& c) {
  return __riscv_vfmsub_vv_f32m2(a, b, c, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pnmadd(const Packet2Xf& a, const Packet2Xf& b, const Packet2Xf& c) {
  return __riscv_vfnmsub_vv_f32m2(a, b, c, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pnmsub(const Packet2Xf& a, const Packet2Xf& b, const Packet2Xf& c) {
  return __riscv_vfnmadd_vv_f32m2(a, b, c, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmin<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  Packet2Xf nans = __riscv_vfmv_v_f_f32m2((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet2Xf>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f32m2_b16(a, a, unpacket_traits<Packet2Xf>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f32m2_b16(b, b, unpacket_traits<Packet2Xf>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet2Xf>::size);

  return __riscv_vfmin_vv_f32m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmin<PropagateNaN, Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return pmin<Packet2Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmin<PropagateNumbers, Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfmin_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmax<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  Packet2Xf nans = __riscv_vfmv_v_f_f32m2((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet2Xf>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f32m2_b16(a, a, unpacket_traits<Packet2Xf>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f32m2_b16(b, b, unpacket_traits<Packet2Xf>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet2Xf>::size);

  return __riscv_vfmax_vv_f32m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmax<PropagateNaN, Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return pmax<Packet2Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pmax<PropagateNumbers, Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vfmax_vv_f32m2(a, b, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcmp_le<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  PacketMask16 mask = __riscv_vmfle_vv_f32m2_b16(a, b, unpacket_traits<Packet2Xf>::size);
  return __riscv_vmerge_vvm_f32m2(pzero<Packet2Xf>(a), ptrue<Packet2Xf>(a), mask, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcmp_lt<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  PacketMask16 mask = __riscv_vmflt_vv_f32m2_b16(a, b, unpacket_traits<Packet2Xf>::size);
  return __riscv_vmerge_vvm_f32m2(pzero<Packet2Xf>(a), ptrue<Packet2Xf>(a), mask, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcmp_eq<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  PacketMask16 mask = __riscv_vmfeq_vv_f32m2_b16(a, b, unpacket_traits<Packet2Xf>::size);
  return __riscv_vmerge_vvm_f32m2(pzero<Packet2Xf>(a), ptrue<Packet2Xf>(a), mask, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcmp_lt_or_nan<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  PacketMask16 mask = __riscv_vmfge_vv_f32m2_b16(a, b, unpacket_traits<Packet2Xf>::size);
  return __riscv_vfmerge_vfm_f32m2(ptrue<Packet2Xf>(a), 0.0f, mask, unpacket_traits<Packet2Xf>::size);
}

EIGEN_STRONG_INLINE Packet2Xf pselect(const PacketMask16& mask, const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vmerge_vvm_f32m2(b, a, mask, unpacket_traits<Packet2Xf>::size);
}

EIGEN_STRONG_INLINE Packet2Xf pselect(const Packet2Xf& mask, const Packet2Xf& a, const Packet2Xf& b) {
  PacketMask16 mask2 =
      __riscv_vmsne_vx_i32m2_b16(__riscv_vreinterpret_v_f32m2_i32m2(mask), 0, unpacket_traits<Packet2Xf>::size);
  return __riscv_vmerge_vvm_f32m2(b, a, mask2, unpacket_traits<Packet2Xf>::size);
}

// Logical Operations are not supported for float, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet2Xf pand<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vand_vv_u32m2(
      __riscv_vreinterpret_v_f32m2_u32m2(a), __riscv_vreinterpret_v_f32m2_u32m2(b), unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf por<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vor_vv_u32m2(
      __riscv_vreinterpret_v_f32m2_u32m2(a), __riscv_vreinterpret_v_f32m2_u32m2(b), unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pxor<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vxor_vv_u32m2(
      __riscv_vreinterpret_v_f32m2_u32m2(a), __riscv_vreinterpret_v_f32m2_u32m2(b), unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pandnot<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vand_vv_u32m2(
      __riscv_vreinterpret_v_f32m2_u32m2(a),
      __riscv_vnot_v_u32m2(__riscv_vreinterpret_v_f32m2_u32m2(b), unpacket_traits<Packet2Xf>::size),
      unpacket_traits<Packet2Xf>::size));
#else
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vandn_vv_u32m2(
      __riscv_vreinterpret_v_f32m2_u32m2(a), __riscv_vreinterpret_v_f32m2_u32m2(b), unpacket_traits<Packet2Xi>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pload<Packet2Xf>(const float* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_f32m2(from, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf ploadu<Packet2Xf>(const float* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_f32m2(from, unpacket_traits<Packet2Xf>::size);
}

EIGEN_STRONG_INLINE Packet4Xf pdup(const Packet2Xf& a) {
  Packet4Xul data = __riscv_vwcvtu_x_x_v_u64m4(__riscv_vreinterpret_v_f32m2_u32m2(a), unpacket_traits<Packet2Xi>::size);
  return __riscv_vreinterpret_v_u64m4_f32m4(__riscv_vadd_vv_u64m4(
      __riscv_vsll_vx_u64m4(data, 32, unpacket_traits<Packet4Xl>::size), data, unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf ploaddup<Packet2Xf>(const float* from) {
  Packet2Xul data = __riscv_vlmul_trunc_v_u64m4_u64m2(__riscv_vwcvtu_x_x_v_u64m4(
      __riscv_vreinterpret_v_f32m2_u32m2(pload<Packet2Xf>(from)), unpacket_traits<Packet2Xi>::size));
  return __riscv_vreinterpret_v_u64m2_f32m2(__riscv_vadd_vv_u64m2(
      __riscv_vsll_vx_u64m2(data, 32, unpacket_traits<Packet2Xl>::size), data, unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf ploadquad<Packet2Xf>(const float* from) {
  Packet2Xu idx =
      __riscv_vsrl_vx_u32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet2Xf>::size), 2, unpacket_traits<Packet2Xf>::size);
  return __riscv_vrgather_vv_f32m2(pload<Packet2Xf>(from), idx, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<float>(float* to, const Packet2Xf& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_f32m2(to, from, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<float>(float* to, const Packet2Xf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_f32m2(to, from, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xf pgather<float, Packet2Xf>(const float* from, Index stride) {
  return __riscv_vlse32_v_f32m2(from, stride * sizeof(float), unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<float, Packet2Xf>(float* to, const Packet2Xf& from, Index stride) {
  __riscv_vsse32(to, stride * sizeof(float), from, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE float pfirst<Packet2Xf>(const Packet2Xf& a) {
  return __riscv_vfmv_f_s_f32m2_f32(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf psqrt(const Packet2Xf& a) {
  return __riscv_vfsqrt_v_f32m2(a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf print<Packet2Xf>(const Packet2Xf& a) {
  const Packet2Xf limit = pset1<Packet2Xf>(static_cast<float>(1 << 23));
  const Packet2Xf abs_a = pabs(a);

  PacketMask16 mask = __riscv_vmfne_vv_f32m2_b16(a, a, unpacket_traits<Packet2Xf>::size);
  const Packet2Xf x = __riscv_vfadd_vv_f32m2_tumu(mask, a, a, a, unpacket_traits<Packet2Xf>::size);
  const Packet2Xf new_x = __riscv_vfcvt_f_x_v_f32m2(__riscv_vfcvt_x_f_v_i32m2(a, unpacket_traits<Packet2Xf>::size),
                                                    unpacket_traits<Packet2Xf>::size);

  mask = __riscv_vmflt_vv_f32m2_b16(abs_a, limit, unpacket_traits<Packet2Xf>::size);
  Packet2Xf signed_x = __riscv_vfsgnj_vv_f32m2(new_x, x, unpacket_traits<Packet2Xf>::size);
  return __riscv_vmerge_vvm_f32m2(x, signed_x, mask, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pfloor<Packet2Xf>(const Packet2Xf& a) {
  Packet2Xf tmp = print<Packet2Xf>(a);
  // If greater, subtract one.
  PacketMask16 mask = __riscv_vmflt_vv_f32m2_b16(a, tmp, unpacket_traits<Packet2Xf>::size);
  return __riscv_vfsub_vf_f32m2_tumu(mask, tmp, tmp, 1.0f, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf preverse(const Packet2Xf& a) {
  Packet2Xu idx = __riscv_vrsub_vx_u32m2(__riscv_vid_v_u32m2(unpacket_traits<Packet2Xf>::size),
                                         unpacket_traits<Packet2Xf>::size - 1, unpacket_traits<Packet2Xf>::size);
  return __riscv_vrgather_vv_f32m2(a, idx, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pfrexp<Packet2Xf>(const Packet2Xf& a, Packet2Xf& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE float predux<Packet2Xf>(const Packet2Xf& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f32m2_f32m1(
      a, __riscv_vfmv_v_f_f32m1(0.0f, unpacket_traits<Packet2Xf>::size / 2), unpacket_traits<Packet2Xf>::size));
}

template <>
EIGEN_STRONG_INLINE float predux_mul<Packet2Xf>(const Packet2Xf& a) {
  return predux_mul<Packet1Xf>(__riscv_vfmul_vv_f32m1(
      __riscv_vget_v_f32m2_f32m1(a, 0), __riscv_vget_v_f32m2_f32m1(a, 1), unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE float predux_min<Packet2Xf>(const Packet2Xf& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f32m2_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet2Xf>::size / 2),
          unpacket_traits<Packet2Xf>::size)),
      (std::numeric_limits<float>::max)());
}

template <>
EIGEN_STRONG_INLINE float predux_max<Packet2Xf>(const Packet2Xf& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f32m2_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet2Xf>::size / 2),
          unpacket_traits<Packet2Xf>::size)),
      -(std::numeric_limits<float>::max)());
}

template <>
EIGEN_STRONG_INLINE Packet1Xf predux_half(const Packet2Xf& a) {
  return Packet1Xf(__riscv_vfadd_vv_f32m1(__riscv_vget_v_f32m2_f32m1(a, 0), __riscv_vget_v_f32m2_f32m1(a, 1),
                                          unpacket_traits<Packet1Xf>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xf, N>& kernel) {
  float buffer[unpacket_traits<Packet2Xf>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(float), kernel.packet[i], unpacket_traits<Packet2Xf>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_f32m2(&buffer[i * unpacket_traits<Packet2Xf>::size], unpacket_traits<Packet2Xf>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pldexp<Packet2Xf>(const Packet2Xf& a, const Packet2Xf& exponent) {
  return pldexp_generic(a, exponent);
}

template <typename Packet = Packet2Xf>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xf>::value && (unpacket_traits<Packet2Xf>::size % 8) == 0, Packet1Xf>
    predux_half(const Packet2Xf& a) {
  return __riscv_vfadd_vv_f32m1(__riscv_vget_v_f32m2_f32m1(a, 0), __riscv_vget_v_f32m2_f32m1(a, 1),
                                unpacket_traits<Packet1Xf>::size);
}

/********************************* Packet2Xl ************************************/

template <>
EIGEN_STRONG_INLINE Packet2Xl pset1<Packet2Xl>(const numext::int64_t& from) {
  return __riscv_vmv_v_x_i64m2(from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl plset<Packet2Xl>(const numext::int64_t& a) {
  Packet2Xl idx = __riscv_vreinterpret_v_u64m2_i64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xl>::size));
  return __riscv_vadd_vx_i64m2(idx, a, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pzero<Packet2Xl>(const Packet2Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m2(0, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl padd<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vadd_vv_i64m2(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl psub<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pnegate(const Packet2Xl& a) {
  return __riscv_vneg(a, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pconj(const Packet2Xl& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pmul<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pdiv<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pmadd(const Packet2Xl& a, const Packet2Xl& b, const Packet2Xl& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pmsub(const Packet2Xl& a, const Packet2Xl& b, const Packet2Xl& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pnmadd(const Packet2Xl& a, const Packet2Xl& b, const Packet2Xl& c) {
  return __riscv_vnmsub_vv_i64m2(a, b, c, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pnmsub(const Packet2Xl& a, const Packet2Xl& b, const Packet2Xl& c) {
  return __riscv_vnmsub_vv_i64m2(a, b, pnegate(c), unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pmin<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pmax<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcmp_le<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  PacketMask32 mask = __riscv_vmsle_vv_i64m2_b32(a, b, unpacket_traits<Packet2Xl>::size);
  return __riscv_vmerge_vxm_i64m2(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcmp_lt<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  PacketMask32 mask = __riscv_vmslt_vv_i64m2_b32(a, b, unpacket_traits<Packet2Xl>::size);
  return __riscv_vmerge_vxm_i64m2(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcmp_eq<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  PacketMask32 mask = __riscv_vmseq_vv_i64m2_b32(a, b, unpacket_traits<Packet2Xl>::size);
  return __riscv_vmerge_vxm_i64m2(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl ptrue<Packet2Xl>(const Packet2Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m2(0xffffffffffffffffu, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pand<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vand_vv_i64m2(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl por<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vor_vv_i64m2(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pxor<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
  return __riscv_vxor_vv_i64m2(a, b, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pandnot<Packet2Xl>(const Packet2Xl& a, const Packet2Xl& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i64m2(a, __riscv_vnot_v_i64m2(b, unpacket_traits<Packet2Xl>::size),
                               unpacket_traits<Packet2Xl>::size);
#else
  return __riscv_vreinterpret_v_u64m2_i64m2(__riscv_vandn_vv_u64m2(
      __riscv_vreinterpret_v_i64m2_u64m2(a), __riscv_vreinterpret_v_i64m2_u64m2(b), unpacket_traits<Packet2Xl>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xl parithmetic_shift_right(Packet2Xl a) {
  return __riscv_vsra_vx_i64m2(a, N, unpacket_traits<Packet2Xl>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xl plogical_shift_right(Packet2Xl a) {
  return __riscv_vreinterpret_i64m2(
      __riscv_vsrl_vx_u64m2(__riscv_vreinterpret_u64m2(a), N, unpacket_traits<Packet2Xl>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xl plogical_shift_left(Packet2Xl a) {
  return __riscv_vsll_vx_i64m2(a, N, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pload<Packet2Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_i64m2(from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl ploadu<Packet2Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_i64m2(from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl ploaddup<Packet2Xl>(const numext::int64_t* from) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xl>::size), 1, unpacket_traits<Packet2Xl>::size);
  return __riscv_vrgather_vv_i64m2(pload<Packet2Xl>(from), idx, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl ploadquad<Packet2Xl>(const numext::int64_t* from) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xl>::size), 2, unpacket_traits<Packet2Xl>::size);
  return __riscv_vrgather_vv_i64m2(pload<Packet2Xl>(from), idx, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int64_t>(numext::int64_t* to, const Packet2Xl& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_i64m2(to, from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int64_t>(numext::int64_t* to, const Packet2Xl& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_i64m2(to, from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xl pgather<numext::int64_t, Packet2Xl>(const numext::int64_t* from, Index stride) {
  return __riscv_vlse64_v_i64m2(from, stride * sizeof(numext::int64_t), unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int64_t, Packet2Xl>(numext::int64_t* to, const Packet2Xl& from,
                                                                   Index stride) {
  __riscv_vsse64(to, stride * sizeof(numext::int64_t), from, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t pfirst<Packet2Xl>(const Packet2Xl& a) {
  return __riscv_vmv_x_s_i64m2_i64(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl preverse(const Packet2Xl& a) {
  Packet2Xul idx = __riscv_vrsub_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xl>::size),
                                          unpacket_traits<Packet2Xl>::size - 1, unpacket_traits<Packet2Xl>::size);
  return __riscv_vrgather_vv_i64m2(a, idx, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pabs(const Packet2Xl& a) {
  Packet2Xl mask = __riscv_vsra_vx_i64m2(a, 63, unpacket_traits<Packet2Xl>::size);
  return __riscv_vsub_vv_i64m2(__riscv_vxor_vv_i64m2(a, mask, unpacket_traits<Packet2Xl>::size), mask,
                               unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux<Packet2Xl>(const Packet2Xl& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i64m2_i64m1(a, __riscv_vmv_v_x_i64m1(0, unpacket_traits<Packet2Xl>::size / 2),
                                                      unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_mul<Packet2Xl>(const Packet2Xl& a) {
  return predux_mul<Packet1Xl>(__riscv_vmul_vv_i64m1(__riscv_vget_v_i64m2_i64m1(a, 0), __riscv_vget_v_i64m2_i64m1(a, 1),
                                                     unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_min<Packet2Xl>(const Packet2Xl& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i64m2_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::max)(), unpacket_traits<Packet2Xl>::size / 2),
      unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_max<Packet2Xl>(const Packet2Xl& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i64m2_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::min)(), unpacket_traits<Packet2Xl>::size / 2),
      unpacket_traits<Packet2Xl>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xl, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet2Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), kernel.packet[i], unpacket_traits<Packet2Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_i64m2(&buffer[i * unpacket_traits<Packet2Xl>::size], unpacket_traits<Packet2Xl>::size);
  }
}

template <typename Packet = Packet2Xl>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xl>::value && (unpacket_traits<Packet2Xl>::size % 8) == 0, Packet1Xl>
    predux_half(const Packet2Xl& a) {
  return __riscv_vadd_vv_i64m1(__riscv_vget_v_i64m2_i64m1(a, 0), __riscv_vget_v_i64m2_i64m1(a, 1),
                               unpacket_traits<Packet1Xl>::size);
}

/********************************* Packet2Xd ************************************/

template <>
EIGEN_STRONG_INLINE Packet2Xd ptrue<Packet2Xd>(const Packet2Xd& /*a*/) {
  return __riscv_vreinterpret_f64m2(__riscv_vmv_v_x_u64m2(0xffffffffffffffffu, unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pzero<Packet2Xd>(const Packet2Xd& /*a*/) {
  return __riscv_vfmv_v_f_f64m2(0.0, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pabs(const Packet2Xd& a) {
  return __riscv_vfabs_v_f64m2(a, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pabsdiff(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfabs_v_f64m2(__riscv_vfsub_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size),
                               unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pset1<Packet2Xd>(const double& from) {
  return __riscv_vfmv_v_f_f64m2(from, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pset1frombits<Packet2Xd>(numext::uint64_t from) {
  return __riscv_vreinterpret_f64m2(__riscv_vmv_v_x_u64m2(from, unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd plset<Packet2Xd>(const double& a) {
  Packet2Xd idx = __riscv_vfcvt_f_x_v_f64m2(
      __riscv_vreinterpret_v_u64m2_i64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet4Xi>::size)),
      unpacket_traits<Packet2Xd>::size);
  return __riscv_vfadd_vf_f64m2(idx, a, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet2Xd>(const double* a, Packet2Xd& a0, Packet2Xd& a1, Packet2Xd& a2,
                                                Packet2Xd& a3) {
  Packet2Xd aa;
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    aa = __riscv_vlmul_ext_f64m2(__riscv_vle64_v_f64m1(a, 4));
  } else {
    aa = __riscv_vle64_v_f64m2(a, 4);
  }
  a0 = __riscv_vrgather_vx_f64m2(aa, 0, unpacket_traits<Packet2Xd>::size);
  a1 = __riscv_vrgather_vx_f64m2(aa, 1, unpacket_traits<Packet2Xd>::size);
  a2 = __riscv_vrgather_vx_f64m2(aa, 2, unpacket_traits<Packet2Xd>::size);
  a3 = __riscv_vrgather_vx_f64m2(aa, 3, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd padd<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfadd_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd psub<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfsub_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pnegate(const Packet2Xd& a) {
  return __riscv_vfneg_v_f64m2(a, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd psignbit(const Packet2Xd& a) {
  return __riscv_vreinterpret_v_i64m2_f64m2(
      __riscv_vsra_vx_i64m2(__riscv_vreinterpret_v_f64m2_i64m2(a), 63, unpacket_traits<Packet2Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pconj(const Packet2Xd& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmul<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfmul_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pdiv<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfdiv_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmadd(const Packet2Xd& a, const Packet2Xd& b, const Packet2Xd& c) {
  return __riscv_vfmadd_vv_f64m2(a, b, c, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmsub(const Packet2Xd& a, const Packet2Xd& b, const Packet2Xd& c) {
  return __riscv_vfmsub_vv_f64m2(a, b, c, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pnmadd(const Packet2Xd& a, const Packet2Xd& b, const Packet2Xd& c) {
  return __riscv_vfnmsub_vv_f64m2(a, b, c, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pnmsub(const Packet2Xd& a, const Packet2Xd& b, const Packet2Xd& c) {
  return __riscv_vfnmadd_vv_f64m2(a, b, c, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmin<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  Packet2Xd nans = __riscv_vfmv_v_f_f64m2((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet2Xd>::size);
  PacketMask32 mask = __riscv_vmfeq_vv_f64m2_b32(a, a, unpacket_traits<Packet2Xd>::size);
  PacketMask32 mask2 = __riscv_vmfeq_vv_f64m2_b32(b, b, unpacket_traits<Packet2Xd>::size);
  mask = __riscv_vmand_mm_b32(mask, mask2, unpacket_traits<Packet2Xd>::size);

  return __riscv_vfmin_vv_f64m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmin<PropagateNaN, Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return pmin<Packet2Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmin<PropagateNumbers, Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfmin_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmax<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  Packet2Xd nans = __riscv_vfmv_v_f_f64m2((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet2Xd>::size);
  PacketMask32 mask = __riscv_vmfeq_vv_f64m2_b32(a, a, unpacket_traits<Packet2Xd>::size);
  PacketMask32 mask2 = __riscv_vmfeq_vv_f64m2_b32(b, b, unpacket_traits<Packet2Xd>::size);
  mask = __riscv_vmand_mm_b32(mask, mask2, unpacket_traits<Packet2Xd>::size);

  return __riscv_vfmax_vv_f64m2_tumu(mask, nans, a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmax<PropagateNaN, Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return pmax<Packet2Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pmax<PropagateNumbers, Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vfmax_vv_f64m2(a, b, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcmp_le<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  PacketMask32 mask = __riscv_vmfle_vv_f64m2_b32(a, b, unpacket_traits<Packet2Xd>::size);
  return __riscv_vmerge_vvm_f64m2(pzero<Packet2Xd>(a), ptrue<Packet2Xd>(a), mask, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcmp_lt<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  PacketMask32 mask = __riscv_vmflt_vv_f64m2_b32(a, b, unpacket_traits<Packet2Xd>::size);
  return __riscv_vmerge_vvm_f64m2(pzero<Packet2Xd>(a), ptrue<Packet2Xd>(a), mask, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcmp_eq<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  PacketMask32 mask = __riscv_vmfeq_vv_f64m2_b32(a, b, unpacket_traits<Packet2Xd>::size);
  return __riscv_vmerge_vvm_f64m2(pzero<Packet2Xd>(a), ptrue<Packet2Xd>(a), mask, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcmp_lt_or_nan<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  PacketMask32 mask = __riscv_vmfge_vv_f64m2_b32(a, b, unpacket_traits<Packet2Xd>::size);
  return __riscv_vfmerge_vfm_f64m2(ptrue<Packet2Xd>(a), 0.0, mask, unpacket_traits<Packet2Xd>::size);
}

EIGEN_STRONG_INLINE Packet2Xd pselect(const PacketMask32& mask, const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vmerge_vvm_f64m2(b, a, mask, unpacket_traits<Packet2Xd>::size);
}

EIGEN_STRONG_INLINE Packet2Xd pselect(const Packet2Xd& mask, const Packet2Xd& a, const Packet2Xd& b) {
  PacketMask32 mask2 =
      __riscv_vmsne_vx_i64m2_b32(__riscv_vreinterpret_v_f64m2_i64m2(mask), 0, unpacket_traits<Packet2Xd>::size);
  return __riscv_vmerge_vvm_f64m2(b, a, mask2, unpacket_traits<Packet2Xd>::size);
}

// Logical Operations are not supported for double, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet2Xd pand<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vand_vv_u64m2(
      __riscv_vreinterpret_v_f64m2_u64m2(a), __riscv_vreinterpret_v_f64m2_u64m2(b), unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd por<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vor_vv_u64m2(
      __riscv_vreinterpret_v_f64m2_u64m2(a), __riscv_vreinterpret_v_f64m2_u64m2(b), unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pxor<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vxor_vv_u64m2(
      __riscv_vreinterpret_v_f64m2_u64m2(a), __riscv_vreinterpret_v_f64m2_u64m2(b), unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pandnot<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vand_vv_u64m2(
      __riscv_vreinterpret_v_f64m2_u64m2(a),
      __riscv_vnot_v_u64m2(__riscv_vreinterpret_v_f64m2_u64m2(b), unpacket_traits<Packet2Xd>::size),
      unpacket_traits<Packet2Xd>::size));
#else
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vandn_vv_u64m2(
      __riscv_vreinterpret_v_f64m2_u64m2(a), __riscv_vreinterpret_v_f64m2_u64m2(b), unpacket_traits<Packet2Xl>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pload<Packet2Xd>(const double* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_f64m2(from, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd ploadu<Packet2Xd>(const double* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_f64m2(from, unpacket_traits<Packet2Xd>::size);
}

EIGEN_STRONG_INLINE Packet4Xd pdup(const Packet2Xd& a) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 1, unpacket_traits<Packet4Xd>::size);
  return __riscv_vrgather_vv_f64m4(__riscv_vlmul_ext_v_f64m2_f64m4(a), idx, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd ploaddup<Packet2Xd>(const double* from) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 1, unpacket_traits<Packet2Xd>::size);
  return __riscv_vrgather_vv_f64m2(__riscv_vlmul_ext_v_f64m1_f64m2(pload<Packet1Xd>(from)), idx,
                                   unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd ploadquad<Packet2Xd>(const double* from) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 2, unpacket_traits<Packet2Xd>::size);
  return __riscv_vrgather_vv_f64m2(__riscv_vlmul_ext_v_f64m1_f64m2(pload<Packet1Xd>(from)), idx,
                                   unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<double>(double* to, const Packet2Xd& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_f64m2(to, from, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<double>(double* to, const Packet2Xd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_f64m2(to, from, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xd pgather<double, Packet2Xd>(const double* from, Index stride) {
  return __riscv_vlse64_v_f64m2(from, stride * sizeof(double), unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<double, Packet2Xd>(double* to, const Packet2Xd& from, Index stride) {
  __riscv_vsse64(to, stride * sizeof(double), from, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE double pfirst<Packet2Xd>(const Packet2Xd& a) {
  return __riscv_vfmv_f_s_f64m2_f64(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd psqrt(const Packet2Xd& a) {
  return __riscv_vfsqrt_v_f64m2(a, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd print<Packet2Xd>(const Packet2Xd& a) {
  const Packet2Xd limit = pset1<Packet2Xd>(static_cast<double>(1ull << 52));
  const Packet2Xd abs_a = pabs(a);

  PacketMask32 mask = __riscv_vmfne_vv_f64m2_b32(a, a, unpacket_traits<Packet2Xd>::size);
  const Packet2Xd x = __riscv_vfadd_vv_f64m2_tumu(mask, a, a, a, unpacket_traits<Packet2Xd>::size);
  const Packet2Xd new_x = __riscv_vfcvt_f_x_v_f64m2(__riscv_vfcvt_x_f_v_i64m2(a, unpacket_traits<Packet2Xd>::size),
                                                    unpacket_traits<Packet2Xd>::size);

  mask = __riscv_vmflt_vv_f64m2_b32(abs_a, limit, unpacket_traits<Packet2Xd>::size);
  Packet2Xd signed_x = __riscv_vfsgnj_vv_f64m2(new_x, x, unpacket_traits<Packet2Xd>::size);
  return __riscv_vmerge_vvm_f64m2(x, signed_x, mask, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pfloor<Packet2Xd>(const Packet2Xd& a) {
  Packet2Xd tmp = print<Packet2Xd>(a);
  // If greater, subtract one.
  PacketMask32 mask = __riscv_vmflt_vv_f64m2_b32(a, tmp, unpacket_traits<Packet2Xd>::size);
  return __riscv_vfsub_vf_f64m2_tumu(mask, tmp, tmp, 1.0, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd preverse(const Packet2Xd& a) {
  Packet2Xul idx = __riscv_vrsub_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size),
                                          unpacket_traits<Packet2Xd>::size - 1, unpacket_traits<Packet2Xd>::size);
  return __riscv_vrgather_vv_f64m2(a, idx, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pfrexp<Packet2Xd>(const Packet2Xd& a, Packet2Xd& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE double predux<Packet2Xd>(const Packet2Xd& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f64m2_f64m1(
      a, __riscv_vfmv_v_f_f64m1(0.0, unpacket_traits<Packet2Xd>::size / 2), unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE double predux_mul<Packet2Xd>(const Packet2Xd& a) {
  return predux_mul<Packet1Xd>(__riscv_vfmul_vv_f64m1(
      __riscv_vget_v_f64m2_f64m1(a, 0), __riscv_vget_v_f64m2_f64m1(a, 1), unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE double predux_min<Packet2Xd>(const Packet2Xd& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f64m2_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet2Xd>::size / 2),
          unpacket_traits<Packet2Xd>::size)),
      (std::numeric_limits<double>::max)());
}

template <>
EIGEN_STRONG_INLINE double predux_max<Packet2Xd>(const Packet2Xd& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f64m2_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet2Xd>::size / 2),
          unpacket_traits<Packet2Xd>::size)),
      -(std::numeric_limits<double>::max)());
}

template <>
EIGEN_STRONG_INLINE Packet1Xd predux_half(const Packet2Xd& a) {
  return Packet1Xd(__riscv_vfadd_vv_f64m1(__riscv_vget_v_f64m2_f64m1(a, 0), __riscv_vget_v_f64m2_f64m1(a, 1),
                                          unpacket_traits<Packet1Xd>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xd, N>& kernel) {
  double buffer[unpacket_traits<Packet2Xd>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(double), kernel.packet[i], unpacket_traits<Packet2Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_f64m2(&buffer[i * unpacket_traits<Packet2Xd>::size], unpacket_traits<Packet2Xd>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pldexp<Packet2Xd>(const Packet2Xd& a, const Packet2Xd& exponent) {
  return pldexp_generic(a, exponent);
}

template <typename Packet = Packet2Xd>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xd>::value && (unpacket_traits<Packet2Xd>::size % 8) == 0, Packet1Xd>
    predux_half(const Packet2Xd& a) {
  return __riscv_vfadd_vv_f64m1(__riscv_vget_v_f64m2_f64m1(a, 0), __riscv_vget_v_f64m2_f64m1(a, 1),
                                unpacket_traits<Packet1Xd>::size);
}

/********************************* Packet2Xs ************************************/

EIGEN_STRONG_INLINE Packet2Xs __riscv_vreinterpret_v_u32m2_i16m2(const Packet2Xu& a) {
  return __riscv_vreinterpret_v_i32m2_i16m2(__riscv_vreinterpret_v_u32m2_i32m2(a));
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pset1<Packet2Xs>(const numext::int16_t& from) {
  return __riscv_vmv_v_x_i16m2(from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs plset<Packet2Xs>(const numext::int16_t& a) {
  Packet2Xs idx = __riscv_vreinterpret_v_u16m2_i16m2(__riscv_vid_v_u16m2(unpacket_traits<Packet2Xs>::size));
  return __riscv_vadd_vx_i16m2(idx, a, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pzero<Packet2Xs>(const Packet2Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m2(0, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs padd<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vadd_vv_i16m2(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs psub<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pnegate(const Packet2Xs& a) {
  return __riscv_vneg(a, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pconj(const Packet2Xs& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pmul<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pdiv<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pmadd(const Packet2Xs& a, const Packet2Xs& b, const Packet2Xs& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pmsub(const Packet2Xs& a, const Packet2Xs& b, const Packet2Xs& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pnmadd(const Packet2Xs& a, const Packet2Xs& b, const Packet2Xs& c) {
  return __riscv_vnmsub_vv_i16m2(a, b, c, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pnmsub(const Packet2Xs& a, const Packet2Xs& b, const Packet2Xs& c) {
  return __riscv_vnmsub_vv_i16m2(a, b, pnegate(c), unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pmin<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pmax<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcmp_le<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  PacketMask8 mask = __riscv_vmsle_vv_i16m2_b8(a, b, unpacket_traits<Packet2Xs>::size);
  return __riscv_vmerge_vxm_i16m2(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcmp_lt<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  PacketMask8 mask = __riscv_vmslt_vv_i16m2_b8(a, b, unpacket_traits<Packet2Xs>::size);
  return __riscv_vmerge_vxm_i16m2(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pcmp_eq<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  PacketMask8 mask = __riscv_vmseq_vv_i16m2_b8(a, b, unpacket_traits<Packet2Xs>::size);
  return __riscv_vmerge_vxm_i16m2(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs ptrue<Packet2Xs>(const Packet2Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m2(static_cast<unsigned short>(0xffffu), unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pand<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vand_vv_i16m2(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs por<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vor_vv_i16m2(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pxor<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
  return __riscv_vxor_vv_i16m2(a, b, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pandnot<Packet2Xs>(const Packet2Xs& a, const Packet2Xs& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i16m2(a, __riscv_vnot_v_i16m2(b, unpacket_traits<Packet2Xs>::size),
                               unpacket_traits<Packet2Xs>::size);
#else
  return __riscv_vreinterpret_v_u16m2_i16m2(__riscv_vandn_vv_u16m2(
      __riscv_vreinterpret_v_i16m2_u16m2(a), __riscv_vreinterpret_v_i16m2_u16m2(b), unpacket_traits<Packet2Xs>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xs parithmetic_shift_right(Packet2Xs a) {
  return __riscv_vsra_vx_i16m2(a, N, unpacket_traits<Packet2Xs>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xs plogical_shift_right(Packet2Xs a) {
  return __riscv_vreinterpret_i16m2(
      __riscv_vsrl_vx_u16m2(__riscv_vreinterpret_u16m2(a), N, unpacket_traits<Packet2Xs>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet2Xs plogical_shift_left(Packet2Xs a) {
  return __riscv_vsll_vx_i16m2(a, N, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pload<Packet2Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_i16m2(from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs ploadu<Packet2Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_i16m2(from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs ploaddup<Packet2Xs>(const numext::int16_t* from) {
  Packet2Xu data = __riscv_vlmul_trunc_v_u32m4_u32m2(__riscv_vwcvtu_x_x_v_u32m4(
      __riscv_vreinterpret_v_i16m2_u16m2(pload<Packet2Xs>(from)), unpacket_traits<Packet2Xs>::size));
  return __riscv_vreinterpret_v_u32m2_i16m2(__riscv_vadd_vv_u32m2(
      __riscv_vsll_vx_u32m2(data, 16, unpacket_traits<Packet2Xi>::size), data, unpacket_traits<Packet2Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xs ploadquad<Packet2Xs>(const numext::int16_t* from) {
  Packet2Xsu idx =
      __riscv_vsrl_vx_u16m2(__riscv_vid_v_u16m2(unpacket_traits<Packet2Xs>::size), 2, unpacket_traits<Packet2Xs>::size);
  return __riscv_vrgather_vv_i16m2(pload<Packet2Xs>(from), idx, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int16_t>(numext::int16_t* to, const Packet2Xs& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_i16m2(to, from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int16_t>(numext::int16_t* to, const Packet2Xs& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_i16m2(to, from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet2Xs pgather<numext::int16_t, Packet2Xs>(const numext::int16_t* from, Index stride) {
  return __riscv_vlse16_v_i16m2(from, stride * sizeof(numext::int16_t), unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int16_t, Packet2Xs>(numext::int16_t* to, const Packet2Xs& from,
                                                                   Index stride) {
  __riscv_vsse16(to, stride * sizeof(numext::int16_t), from, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t pfirst<Packet2Xs>(const Packet2Xs& a) {
  return __riscv_vmv_x_s_i16m2_i16(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs preverse(const Packet2Xs& a) {
  Packet2Xsu idx = __riscv_vrsub_vx_u16m2(__riscv_vid_v_u16m2(unpacket_traits<Packet2Xs>::size),
                                          unpacket_traits<Packet2Xs>::size - 1, unpacket_traits<Packet2Xs>::size);
  return __riscv_vrgather_vv_i16m2(a, idx, unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xs pabs(const Packet2Xs& a) {
  Packet2Xs mask = __riscv_vsra_vx_i16m2(a, 15, unpacket_traits<Packet2Xs>::size);
  return __riscv_vsub_vv_i16m2(__riscv_vxor_vv_i16m2(a, mask, unpacket_traits<Packet2Xs>::size), mask,
                               unpacket_traits<Packet2Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux<Packet2Xs>(const Packet2Xs& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i16m2_i16m1(a, __riscv_vmv_v_x_i16m1(0, unpacket_traits<Packet2Xs>::size / 2),
                                                      unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_mul<Packet2Xs>(const Packet2Xs& a) {
  return predux_mul<Packet1Xs>(__riscv_vmul_vv_i16m1(__riscv_vget_v_i16m2_i16m1(a, 0), __riscv_vget_v_i16m2_i16m1(a, 1),
                                                     unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_min<Packet2Xs>(const Packet2Xs& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i16m2_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::max)(), unpacket_traits<Packet2Xs>::size / 2),
      unpacket_traits<Packet2Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_max<Packet2Xs>(const Packet2Xs& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i16m2_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::min)(), unpacket_traits<Packet2Xs>::size / 2),
      unpacket_traits<Packet2Xs>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet2Xs, N>& kernel) {
  numext::int16_t buffer[unpacket_traits<Packet2Xs>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(&buffer[i], N * sizeof(numext::int16_t), kernel.packet[i], unpacket_traits<Packet2Xs>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle16_v_i16m2(&buffer[i * unpacket_traits<Packet2Xs>::size], unpacket_traits<Packet2Xs>::size);
  }
}

template <typename Packet = Packet2Xs>
EIGEN_STRONG_INLINE
    std::enable_if_t<std::is_same<Packet, Packet2Xs>::value && (unpacket_traits<Packet2Xs>::size % 8) == 0, Packet1Xs>
    predux_half(const Packet2Xs& a) {
  return __riscv_vadd_vv_i16m1(__riscv_vget_v_i16m2_i16m1(a, 0), __riscv_vget_v_i16m2_i16m1(a, 1),
                               unpacket_traits<Packet1Xs>::size);
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_PACKET2_MATH_RVV10_H

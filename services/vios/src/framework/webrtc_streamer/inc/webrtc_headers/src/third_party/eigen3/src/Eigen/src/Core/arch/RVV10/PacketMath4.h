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

#ifndef EIGEN_PACKET4_MATH_RVV10_H
#define EIGEN_PACKET4_MATH_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/********************************* Packet4Xi ************************************/

EIGEN_STRONG_INLINE Packet4Xi __riscv_vreinterpret_v_u64m4_i32m4(const Packet4Xul& a) {
  return __riscv_vreinterpret_v_i64m4_i32m4(__riscv_vreinterpret_v_u64m4_i64m4(a));
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pset1<Packet4Xi>(const numext::int32_t& from) {
  return __riscv_vmv_v_x_i32m4(from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi plset<Packet4Xi>(const numext::int32_t& a) {
  Packet4Xi idx = __riscv_vreinterpret_v_u32m4_i32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xi>::size));
  return __riscv_vadd_vx_i32m4(idx, a, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pzero<Packet4Xi>(const Packet4Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m4(0, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi padd<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vadd_vv_i32m4(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi psub<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pnegate(const Packet4Xi& a) {
  return __riscv_vneg(a, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pconj(const Packet4Xi& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pmul<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pdiv<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pmadd(const Packet4Xi& a, const Packet4Xi& b, const Packet4Xi& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pmsub(const Packet4Xi& a, const Packet4Xi& b, const Packet4Xi& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pnmadd(const Packet4Xi& a, const Packet4Xi& b, const Packet4Xi& c) {
  return __riscv_vnmsub_vv_i32m4(a, b, c, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pnmsub(const Packet4Xi& a, const Packet4Xi& b, const Packet4Xi& c) {
  return __riscv_vnmsub_vv_i32m4(a, b, pnegate(c), unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pmin<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pmax<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcmp_le<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  PacketMask8 mask = __riscv_vmsle_vv_i32m4_b8(a, b, unpacket_traits<Packet4Xi>::size);
  return __riscv_vmerge_vxm_i32m4(pzero(a), 0xffffffff, mask, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcmp_lt<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  PacketMask8 mask = __riscv_vmslt_vv_i32m4_b8(a, b, unpacket_traits<Packet4Xi>::size);
  return __riscv_vmerge_vxm_i32m4(pzero(a), 0xffffffff, mask, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcmp_eq<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  PacketMask8 mask = __riscv_vmseq_vv_i32m4_b8(a, b, unpacket_traits<Packet4Xi>::size);
  return __riscv_vmerge_vxm_i32m4(pzero(a), 0xffffffff, mask, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi ptrue<Packet4Xi>(const Packet4Xi& /*a*/) {
  return __riscv_vmv_v_x_i32m4(0xffffffffu, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pand<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vand_vv_i32m4(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi por<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vor_vv_i32m4(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pxor<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
  return __riscv_vxor_vv_i32m4(a, b, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pandnot<Packet4Xi>(const Packet4Xi& a, const Packet4Xi& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i32m4(a, __riscv_vnot_v_i32m4(b, unpacket_traits<Packet4Xi>::size),
                               unpacket_traits<Packet4Xi>::size);
#else
  return __riscv_vreinterpret_v_u32m4_i32m4(__riscv_vandn_vv_u32m4(
      __riscv_vreinterpret_v_i32m4_u32m4(a), __riscv_vreinterpret_v_i32m4_u32m4(b), unpacket_traits<Packet4Xi>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xi parithmetic_shift_right(Packet4Xi a) {
  return __riscv_vsra_vx_i32m4(a, N, unpacket_traits<Packet4Xi>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xi plogical_shift_right(Packet4Xi a) {
  return __riscv_vreinterpret_i32m4(
      __riscv_vsrl_vx_u32m4(__riscv_vreinterpret_u32m4(a), N, unpacket_traits<Packet4Xi>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xi plogical_shift_left(Packet4Xi a) {
  return __riscv_vsll_vx_i32m4(a, N, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pload<Packet4Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_i32m4(from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi ploadu<Packet4Xi>(const numext::int32_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_i32m4(from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi ploaddup<Packet4Xi>(const numext::int32_t* from) {
  Packet4Xul data = __riscv_vwcvtu_x_x_v_u64m4(__riscv_vreinterpret_v_i32m2_u32m2(pload<Packet2Xi>(from)),
                                               unpacket_traits<Packet2Xi>::size);
  return __riscv_vreinterpret_v_u64m4_i32m4(__riscv_vadd_vv_u64m4(
      __riscv_vsll_vx_u64m4(data, 32, unpacket_traits<Packet4Xl>::size), data, unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xi ploadquad<Packet4Xi>(const numext::int32_t* from) {
  Packet4Xu idx =
      __riscv_vsrl_vx_u32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xi>::size), 2, unpacket_traits<Packet4Xi>::size);
  return __riscv_vrgather_vv_i32m4(__riscv_vlmul_ext_v_i32m1_i32m4(pload<Packet1Xi>(from)), idx,
                                   unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int32_t>(numext::int32_t* to, const Packet4Xi& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_i32m4(to, from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int32_t>(numext::int32_t* to, const Packet4Xi& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_i32m4(to, from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet4Xi pgather<numext::int32_t, Packet4Xi>(const numext::int32_t* from, Index stride) {
  return __riscv_vlse32_v_i32m4(from, stride * sizeof(numext::int32_t), unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int32_t, Packet4Xi>(numext::int32_t* to, const Packet4Xi& from,
                                                                   Index stride) {
  __riscv_vsse32(to, stride * sizeof(numext::int32_t), from, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t pfirst<Packet4Xi>(const Packet4Xi& a) {
  return __riscv_vmv_x_s_i32m4_i32(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi preverse(const Packet4Xi& a) {
  Packet4Xu idx = __riscv_vrsub_vx_u32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xi>::size),
                                         unpacket_traits<Packet4Xi>::size - 1, unpacket_traits<Packet4Xi>::size);
  return __riscv_vrgather_vv_i32m4(a, idx, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pabs(const Packet4Xi& a) {
  Packet4Xi mask = __riscv_vsra_vx_i32m4(a, 31, unpacket_traits<Packet4Xi>::size);
  return __riscv_vsub_vv_i32m4(__riscv_vxor_vv_i32m4(a, mask, unpacket_traits<Packet4Xi>::size), mask,
                               unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux<Packet4Xi>(const Packet4Xi& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i32m4_i32m1(a, __riscv_vmv_v_x_i32m1(0, unpacket_traits<Packet4Xi>::size / 4),
                                                      unpacket_traits<Packet4Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_mul<Packet4Xi>(const Packet4Xi& a) {
  Packet1Xi half1 = __riscv_vmul_vv_i32m1(__riscv_vget_v_i32m4_i32m1(a, 0), __riscv_vget_v_i32m4_i32m1(a, 1),
                                          unpacket_traits<Packet1Xi>::size);
  Packet1Xi half2 = __riscv_vmul_vv_i32m1(__riscv_vget_v_i32m4_i32m1(a, 2), __riscv_vget_v_i32m4_i32m1(a, 3),
                                          unpacket_traits<Packet1Xi>::size);
  return predux_mul<Packet1Xi>(__riscv_vmul_vv_i32m1(half1, half2, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_min<Packet4Xi>(const Packet4Xi& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i32m4_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::max)(), unpacket_traits<Packet4Xi>::size / 4),
      unpacket_traits<Packet4Xi>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int32_t predux_max<Packet4Xi>(const Packet4Xi& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i32m4_i32m1(
      a, __riscv_vmv_v_x_i32m1((std::numeric_limits<numext::int32_t>::min)(), unpacket_traits<Packet4Xi>::size / 4),
      unpacket_traits<Packet4Xi>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet4Xi, N>& kernel) {
  numext::int32_t buffer[unpacket_traits<Packet4Xi>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(numext::int32_t), kernel.packet[i], unpacket_traits<Packet4Xi>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_i32m4(&buffer[i * unpacket_traits<Packet4Xi>::size], unpacket_traits<Packet4Xi>::size);
  }
}

template <typename Packet = Packet4Xi>
EIGEN_STRONG_INLINE
    typename std::enable_if_t<std::is_same<Packet, Packet4Xi>::value && (unpacket_traits<Packet4Xi>::size % 8) == 0,
                              Packet2Xi>
    predux_half(const Packet4Xi& a) {
  return __riscv_vadd_vv_i32m2(__riscv_vget_v_i32m4_i32m2(a, 0), __riscv_vget_v_i32m4_i32m2(a, 1),
                               unpacket_traits<Packet2Xi>::size);
}

/********************************* Packet4Xf ************************************/

template <>
EIGEN_STRONG_INLINE Packet4Xf ptrue<Packet4Xf>(const Packet4Xf& /*a*/) {
  return __riscv_vreinterpret_f32m4(__riscv_vmv_v_x_u32m4(0xffffffffu, unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pzero<Packet4Xf>(const Packet4Xf& /*a*/) {
  return __riscv_vfmv_v_f_f32m4(0.0f, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pabs(const Packet4Xf& a) {
  return __riscv_vfabs_v_f32m4(a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pabsdiff(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfabs_v_f32m4(__riscv_vfsub_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size),
                               unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pset1<Packet4Xf>(const float& from) {
  return __riscv_vfmv_v_f_f32m4(from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pset1frombits<Packet4Xf>(numext::uint32_t from) {
  return __riscv_vreinterpret_f32m4(__riscv_vmv_v_x_u32m4(from, unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf plset<Packet4Xf>(const float& a) {
  Packet4Xf idx = __riscv_vfcvt_f_x_v_f32m4(
      __riscv_vreinterpret_v_u32m4_i32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xi>::size)),
      unpacket_traits<Packet4Xf>::size);
  return __riscv_vfadd_vf_f32m4(idx, a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet4Xf>(const float* a, Packet4Xf& a0, Packet4Xf& a1, Packet4Xf& a2,
                                                Packet4Xf& a3) {
  Packet4Xf aa = __riscv_vlmul_ext_f32m4(__riscv_vle32_v_f32m1(a, 4));
  a0 = __riscv_vrgather_vx_f32m4(aa, 0, unpacket_traits<Packet4Xf>::size);
  a1 = __riscv_vrgather_vx_f32m4(aa, 1, unpacket_traits<Packet4Xf>::size);
  a2 = __riscv_vrgather_vx_f32m4(aa, 2, unpacket_traits<Packet4Xf>::size);
  a3 = __riscv_vrgather_vx_f32m4(aa, 3, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf padd<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfadd_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf psub<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfsub_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pnegate(const Packet4Xf& a) {
  return __riscv_vfneg_v_f32m4(a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf psignbit(const Packet4Xf& a) {
  return __riscv_vreinterpret_v_i32m4_f32m4(
      __riscv_vsra_vx_i32m4(__riscv_vreinterpret_v_f32m4_i32m4(a), 31, unpacket_traits<Packet4Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pconj(const Packet4Xf& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmul<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfmul_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pdiv<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfdiv_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmadd(const Packet4Xf& a, const Packet4Xf& b, const Packet4Xf& c) {
  return __riscv_vfmadd_vv_f32m4(a, b, c, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmsub(const Packet4Xf& a, const Packet4Xf& b, const Packet4Xf& c) {
  return __riscv_vfmsub_vv_f32m4(a, b, c, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pnmadd(const Packet4Xf& a, const Packet4Xf& b, const Packet4Xf& c) {
  return __riscv_vfnmsub_vv_f32m4(a, b, c, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pnmsub(const Packet4Xf& a, const Packet4Xf& b, const Packet4Xf& c) {
  return __riscv_vfnmadd_vv_f32m4(a, b, c, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmin<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  Packet4Xf nans = __riscv_vfmv_v_f_f32m4((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet4Xf>::size);
  PacketMask8 mask = __riscv_vmfeq_vv_f32m4_b8(a, a, unpacket_traits<Packet4Xf>::size);
  PacketMask8 mask2 = __riscv_vmfeq_vv_f32m4_b8(b, b, unpacket_traits<Packet4Xf>::size);
  mask = __riscv_vmand_mm_b8(mask, mask2, unpacket_traits<Packet4Xf>::size);

  return __riscv_vfmin_vv_f32m4_tumu(mask, nans, a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmin<PropagateNaN, Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return pmin<Packet4Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmin<PropagateNumbers, Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfmin_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmax<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  Packet4Xf nans = __riscv_vfmv_v_f_f32m4((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet4Xf>::size);
  PacketMask8 mask = __riscv_vmfeq_vv_f32m4_b8(a, a, unpacket_traits<Packet4Xf>::size);
  PacketMask8 mask2 = __riscv_vmfeq_vv_f32m4_b8(b, b, unpacket_traits<Packet4Xf>::size);
  mask = __riscv_vmand_mm_b8(mask, mask2, unpacket_traits<Packet4Xf>::size);

  return __riscv_vfmax_vv_f32m4_tumu(mask, nans, a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmax<PropagateNaN, Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return pmax<Packet4Xf>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pmax<PropagateNumbers, Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vfmax_vv_f32m4(a, b, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcmp_le<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  PacketMask8 mask = __riscv_vmfle_vv_f32m4_b8(a, b, unpacket_traits<Packet4Xf>::size);
  return __riscv_vmerge_vvm_f32m4(pzero<Packet4Xf>(a), ptrue<Packet4Xf>(a), mask, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcmp_lt<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  PacketMask8 mask = __riscv_vmflt_vv_f32m4_b8(a, b, unpacket_traits<Packet4Xf>::size);
  return __riscv_vmerge_vvm_f32m4(pzero<Packet4Xf>(a), ptrue<Packet4Xf>(a), mask, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcmp_eq<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  PacketMask8 mask = __riscv_vmfeq_vv_f32m4_b8(a, b, unpacket_traits<Packet4Xf>::size);
  return __riscv_vmerge_vvm_f32m4(pzero<Packet4Xf>(a), ptrue<Packet4Xf>(a), mask, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcmp_lt_or_nan<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  PacketMask8 mask = __riscv_vmfge_vv_f32m4_b8(a, b, unpacket_traits<Packet4Xf>::size);
  return __riscv_vfmerge_vfm_f32m4(ptrue<Packet4Xf>(a), 0.0f, mask, unpacket_traits<Packet4Xf>::size);
}

EIGEN_STRONG_INLINE Packet4Xf pselect(const PacketMask8& mask, const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vmerge_vvm_f32m4(b, a, mask, unpacket_traits<Packet4Xf>::size);
}

EIGEN_STRONG_INLINE Packet4Xf pselect(const Packet4Xf& mask, const Packet4Xf& a, const Packet4Xf& b) {
  PacketMask8 mask2 =
      __riscv_vmsne_vx_i32m4_b8(__riscv_vreinterpret_v_f32m4_i32m4(mask), 0, unpacket_traits<Packet4Xf>::size);
  return __riscv_vmerge_vvm_f32m4(b, a, mask2, unpacket_traits<Packet4Xf>::size);
}

// Logical Operations are not supported for float, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet4Xf pand<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vand_vv_u32m4(
      __riscv_vreinterpret_v_f32m4_u32m4(a), __riscv_vreinterpret_v_f32m4_u32m4(b), unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf por<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vor_vv_u32m4(
      __riscv_vreinterpret_v_f32m4_u32m4(a), __riscv_vreinterpret_v_f32m4_u32m4(b), unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pxor<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vxor_vv_u32m4(
      __riscv_vreinterpret_v_f32m4_u32m4(a), __riscv_vreinterpret_v_f32m4_u32m4(b), unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pandnot<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vand_vv_u32m4(
      __riscv_vreinterpret_v_f32m4_u32m4(a),
      __riscv_vnot_v_u32m4(__riscv_vreinterpret_v_f32m4_u32m4(b), unpacket_traits<Packet4Xf>::size),
      unpacket_traits<Packet4Xf>::size));
#else
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vandn_vv_u32m4(
      __riscv_vreinterpret_v_f32m4_u32m4(a), __riscv_vreinterpret_v_f32m4_u32m4(b), unpacket_traits<Packet4Xi>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pload<Packet4Xf>(const float* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle32_v_f32m4(from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf ploadu<Packet4Xf>(const float* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle32_v_f32m4(from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf ploaddup<Packet4Xf>(const float* from) {
  Packet4Xul data = __riscv_vwcvtu_x_x_v_u64m4(__riscv_vreinterpret_v_f32m2_u32m2(pload<Packet2Xf>(from)),
                                               unpacket_traits<Packet2Xi>::size);
  return __riscv_vreinterpret_v_u64m4_f32m4(__riscv_vadd_vv_u64m4(
      __riscv_vsll_vx_u64m4(data, 32, unpacket_traits<Packet4Xl>::size), data, unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf ploadquad<Packet4Xf>(const float* from) {
  Packet4Xu idx =
      __riscv_vsrl_vx_u32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xf>::size), 2, unpacket_traits<Packet4Xf>::size);
  return __riscv_vrgather_vv_f32m4(__riscv_vlmul_ext_v_f32m1_f32m4(pload<Packet1Xf>(from)), idx,
                                   unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<float>(float* to, const Packet4Xf& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse32_v_f32m4(to, from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<float>(float* to, const Packet4Xf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse32_v_f32m4(to, from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet4Xf pgather<float, Packet4Xf>(const float* from, Index stride) {
  return __riscv_vlse32_v_f32m4(from, stride * sizeof(float), unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<float, Packet4Xf>(float* to, const Packet4Xf& from, Index stride) {
  __riscv_vsse32(to, stride * sizeof(float), from, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE float pfirst<Packet4Xf>(const Packet4Xf& a) {
  return __riscv_vfmv_f_s_f32m4_f32(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf psqrt(const Packet4Xf& a) {
  return __riscv_vfsqrt_v_f32m4(a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf print<Packet4Xf>(const Packet4Xf& a) {
  const Packet4Xf limit = pset1<Packet4Xf>(static_cast<float>(1 << 23));
  const Packet4Xf abs_a = pabs(a);

  PacketMask8 mask = __riscv_vmfne_vv_f32m4_b8(a, a, unpacket_traits<Packet4Xf>::size);
  const Packet4Xf x = __riscv_vfadd_vv_f32m4_tumu(mask, a, a, a, unpacket_traits<Packet4Xf>::size);
  const Packet4Xf new_x = __riscv_vfcvt_f_x_v_f32m4(__riscv_vfcvt_x_f_v_i32m4(a, unpacket_traits<Packet4Xf>::size),
                                                    unpacket_traits<Packet4Xf>::size);

  mask = __riscv_vmflt_vv_f32m4_b8(abs_a, limit, unpacket_traits<Packet4Xf>::size);
  Packet4Xf signed_x = __riscv_vfsgnj_vv_f32m4(new_x, x, unpacket_traits<Packet4Xf>::size);
  return __riscv_vmerge_vvm_f32m4(x, signed_x, mask, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pfloor<Packet4Xf>(const Packet4Xf& a) {
  Packet4Xf tmp = print<Packet4Xf>(a);
  // If greater, subtract one.
  PacketMask8 mask = __riscv_vmflt_vv_f32m4_b8(a, tmp, unpacket_traits<Packet4Xf>::size);
  return __riscv_vfsub_vf_f32m4_tumu(mask, tmp, tmp, 1.0f, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf preverse(const Packet4Xf& a) {
  Packet4Xu idx = __riscv_vrsub_vx_u32m4(__riscv_vid_v_u32m4(unpacket_traits<Packet4Xf>::size),
                                         unpacket_traits<Packet4Xf>::size - 1, unpacket_traits<Packet4Xf>::size);
  return __riscv_vrgather_vv_f32m4(a, idx, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pfrexp<Packet4Xf>(const Packet4Xf& a, Packet4Xf& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE float predux<Packet4Xf>(const Packet4Xf& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f32m4_f32m1(
      a, __riscv_vfmv_v_f_f32m1(0.0f, unpacket_traits<Packet4Xf>::size / 4), unpacket_traits<Packet4Xf>::size));
}

template <>
EIGEN_STRONG_INLINE float predux_mul<Packet4Xf>(const Packet4Xf& a) {
  Packet1Xf half1 = __riscv_vfmul_vv_f32m1(__riscv_vget_v_f32m4_f32m1(a, 0), __riscv_vget_v_f32m4_f32m1(a, 1),
                                           unpacket_traits<Packet1Xf>::size);
  Packet1Xf half2 = __riscv_vfmul_vv_f32m1(__riscv_vget_v_f32m4_f32m1(a, 2), __riscv_vget_v_f32m4_f32m1(a, 3),
                                           unpacket_traits<Packet1Xf>::size);
  return predux_mul<Packet1Xf>(__riscv_vfmul_vv_f32m1(half1, half2, unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE float predux_min<Packet4Xf>(const Packet4Xf& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f32m4_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet4Xf>::size / 4),
          unpacket_traits<Packet4Xf>::size)),
      (std::numeric_limits<float>::max)());
}

template <>
EIGEN_STRONG_INLINE float predux_max<Packet4Xf>(const Packet4Xf& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f32m4_f32m1(
          a, __riscv_vfmv_v_f_f32m1((std::numeric_limits<float>::quiet_NaN)(), unpacket_traits<Packet4Xf>::size / 4),
          unpacket_traits<Packet4Xf>::size)),
      -(std::numeric_limits<float>::max)());
}

template <>
EIGEN_STRONG_INLINE Packet2Xf predux_half(const Packet4Xf& a) {
  return Packet2Xf(__riscv_vfadd_vv_f32m2(__riscv_vget_v_f32m4_f32m2(a, 0), __riscv_vget_v_f32m4_f32m2(a, 1),
                                          unpacket_traits<Packet2Xf>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet4Xf, N>& kernel) {
  float buffer[unpacket_traits<Packet4Xf>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse32(&buffer[i], N * sizeof(float), kernel.packet[i], unpacket_traits<Packet4Xf>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle32_v_f32m4(&buffer[i * unpacket_traits<Packet4Xf>::size], unpacket_traits<Packet4Xf>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pldexp<Packet4Xf>(const Packet4Xf& a, const Packet4Xf& exponent) {
  return pldexp_generic(a, exponent);
}

template <typename Packet = Packet4Xf>
EIGEN_STRONG_INLINE
    typename std::enable_if_t<std::is_same<Packet, Packet4Xf>::value && (unpacket_traits<Packet4Xf>::size % 8) == 0,
                              Packet2Xf>
    predux_half(const Packet4Xf& a) {
  return __riscv_vfadd_vv_f32m2(__riscv_vget_v_f32m4_f32m2(a, 0), __riscv_vget_v_f32m4_f32m2(a, 1),
                                unpacket_traits<Packet2Xf>::size);
}

/********************************* Packet4Xl ************************************/

template <>
EIGEN_STRONG_INLINE Packet4Xl pset1<Packet4Xl>(const numext::int64_t& from) {
  return __riscv_vmv_v_x_i64m4(from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl plset<Packet4Xl>(const numext::int64_t& a) {
  Packet4Xl idx = __riscv_vreinterpret_v_u64m4_i64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xl>::size));
  return __riscv_vadd_vx_i64m4(idx, a, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pzero<Packet4Xl>(const Packet4Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m4(0, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl padd<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vadd_vv_i64m4(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl psub<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pnegate(const Packet4Xl& a) {
  return __riscv_vneg(a, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pconj(const Packet4Xl& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pmul<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pdiv<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pmadd(const Packet4Xl& a, const Packet4Xl& b, const Packet4Xl& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pmsub(const Packet4Xl& a, const Packet4Xl& b, const Packet4Xl& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pnmadd(const Packet4Xl& a, const Packet4Xl& b, const Packet4Xl& c) {
  return __riscv_vnmsub_vv_i64m4(a, b, c, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pnmsub(const Packet4Xl& a, const Packet4Xl& b, const Packet4Xl& c) {
  return __riscv_vnmsub_vv_i64m4(a, b, pnegate(c), unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pmin<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pmax<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcmp_le<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  PacketMask16 mask = __riscv_vmsle_vv_i64m4_b16(a, b, unpacket_traits<Packet4Xl>::size);
  return __riscv_vmerge_vxm_i64m4(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcmp_lt<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  PacketMask16 mask = __riscv_vmslt_vv_i64m4_b16(a, b, unpacket_traits<Packet4Xl>::size);
  return __riscv_vmerge_vxm_i64m4(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcmp_eq<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  PacketMask16 mask = __riscv_vmseq_vv_i64m4_b16(a, b, unpacket_traits<Packet4Xl>::size);
  return __riscv_vmerge_vxm_i64m4(pzero(a), 0xffffffffffffffff, mask, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl ptrue<Packet4Xl>(const Packet4Xl& /*a*/) {
  return __riscv_vmv_v_x_i64m4(0xffffffffffffffffu, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pand<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vand_vv_i64m4(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl por<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vor_vv_i64m4(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pxor<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
  return __riscv_vxor_vv_i64m4(a, b, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pandnot<Packet4Xl>(const Packet4Xl& a, const Packet4Xl& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i64m4(a, __riscv_vnot_v_i64m4(b, unpacket_traits<Packet4Xl>::size),
                               unpacket_traits<Packet4Xl>::size);
#else
  return __riscv_vreinterpret_v_u64m4_i64m4(__riscv_vandn_vv_u64m4(
      __riscv_vreinterpret_v_i64m4_u64m4(a), __riscv_vreinterpret_v_i64m4_u64m4(b), unpacket_traits<Packet4Xl>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xl parithmetic_shift_right(Packet4Xl a) {
  return __riscv_vsra_vx_i64m4(a, N, unpacket_traits<Packet4Xl>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xl plogical_shift_right(Packet4Xl a) {
  return __riscv_vreinterpret_i64m4(
      __riscv_vsrl_vx_u64m4(__riscv_vreinterpret_u64m4(a), N, unpacket_traits<Packet4Xl>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xl plogical_shift_left(Packet4Xl a) {
  return __riscv_vsll_vx_i64m4(a, N, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pload<Packet4Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_i64m4(from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl ploadu<Packet4Xl>(const numext::int64_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_i64m4(from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl ploaddup<Packet4Xl>(const numext::int64_t* from) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xl>::size), 1, unpacket_traits<Packet4Xl>::size);
  return __riscv_vrgather_vv_i64m4(__riscv_vlmul_ext_v_i64m2_i64m4(pload<Packet2Xl>(from)), idx,
                                   unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl ploadquad<Packet4Xl>(const numext::int64_t* from) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xl>::size), 2, unpacket_traits<Packet4Xl>::size);
  return __riscv_vrgather_vv_i64m4(__riscv_vlmul_ext_v_i64m1_i64m4(pload<Packet1Xl>(from)), idx,
                                   unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int64_t>(numext::int64_t* to, const Packet4Xl& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_i64m4(to, from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int64_t>(numext::int64_t* to, const Packet4Xl& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_i64m4(to, from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet4Xl pgather<numext::int64_t, Packet4Xl>(const numext::int64_t* from, Index stride) {
  return __riscv_vlse64_v_i64m4(from, stride * sizeof(numext::int64_t), unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int64_t, Packet4Xl>(numext::int64_t* to, const Packet4Xl& from,
                                                                   Index stride) {
  __riscv_vsse64(to, stride * sizeof(numext::int64_t), from, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t pfirst<Packet4Xl>(const Packet4Xl& a) {
  return __riscv_vmv_x_s_i64m4_i64(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl preverse(const Packet4Xl& a) {
  Packet4Xul idx = __riscv_vrsub_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xl>::size),
                                          unpacket_traits<Packet4Xl>::size - 1, unpacket_traits<Packet4Xl>::size);
  return __riscv_vrgather_vv_i64m4(a, idx, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pabs(const Packet4Xl& a) {
  Packet4Xl mask = __riscv_vsra_vx_i64m4(a, 63, unpacket_traits<Packet4Xl>::size);
  return __riscv_vsub_vv_i64m4(__riscv_vxor_vv_i64m4(a, mask, unpacket_traits<Packet4Xl>::size), mask,
                               unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux<Packet4Xl>(const Packet4Xl& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i64m4_i64m1(a, __riscv_vmv_v_x_i64m1(0, unpacket_traits<Packet4Xl>::size / 4),
                                                      unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_mul<Packet4Xl>(const Packet4Xl& a) {
  Packet1Xl half1 = __riscv_vmul_vv_i64m1(__riscv_vget_v_i64m4_i64m1(a, 0), __riscv_vget_v_i64m4_i64m1(a, 1),
                                          unpacket_traits<Packet1Xl>::size);
  Packet1Xl half2 = __riscv_vmul_vv_i64m1(__riscv_vget_v_i64m4_i64m1(a, 2), __riscv_vget_v_i64m4_i64m1(a, 3),
                                          unpacket_traits<Packet1Xl>::size);
  return predux_mul<Packet1Xl>(__riscv_vmul_vv_i64m1(half1, half2, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_min<Packet4Xl>(const Packet4Xl& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i64m4_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::max)(), unpacket_traits<Packet4Xl>::size / 4),
      unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int64_t predux_max<Packet4Xl>(const Packet4Xl& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i64m4_i64m1(
      a, __riscv_vmv_v_x_i64m1((std::numeric_limits<numext::int64_t>::min)(), unpacket_traits<Packet4Xl>::size / 4),
      unpacket_traits<Packet4Xl>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet4Xl, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet4Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), kernel.packet[i], unpacket_traits<Packet4Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_i64m4(&buffer[i * unpacket_traits<Packet4Xl>::size], unpacket_traits<Packet4Xl>::size);
  }
}

template <typename Packet = Packet4Xl>
EIGEN_STRONG_INLINE
    typename std::enable_if_t<std::is_same<Packet, Packet4Xl>::value && (unpacket_traits<Packet4Xl>::size % 8) == 0,
                              Packet2Xl>
    predux_half(const Packet4Xl& a) {
  return __riscv_vadd_vv_i64m2(__riscv_vget_v_i64m4_i64m2(a, 0), __riscv_vget_v_i64m4_i64m2(a, 1),
                               unpacket_traits<Packet2Xl>::size);
}

/********************************* Packet4Xd ************************************/

template <>
EIGEN_STRONG_INLINE Packet4Xd ptrue<Packet4Xd>(const Packet4Xd& /*a*/) {
  return __riscv_vreinterpret_f64m4(__riscv_vmv_v_x_u64m4(0xffffffffffffffffu, unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pzero<Packet4Xd>(const Packet4Xd& /*a*/) {
  return __riscv_vfmv_v_f_f64m4(0.0, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pabs(const Packet4Xd& a) {
  return __riscv_vfabs_v_f64m4(a, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pabsdiff(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfabs_v_f64m4(__riscv_vfsub_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size),
                               unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pset1<Packet4Xd>(const double& from) {
  return __riscv_vfmv_v_f_f64m4(from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pset1frombits<Packet4Xd>(numext::uint64_t from) {
  return __riscv_vreinterpret_f64m4(__riscv_vmv_v_x_u64m4(from, unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd plset<Packet4Xd>(const double& a) {
  Packet4Xd idx = __riscv_vfcvt_f_x_v_f64m4(
      __riscv_vreinterpret_v_u64m4_i64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xi>::size)),
      unpacket_traits<Packet4Xd>::size);
  return __riscv_vfadd_vf_f64m4(idx, a, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pbroadcast4<Packet4Xd>(const double* a, Packet4Xd& a0, Packet4Xd& a1, Packet4Xd& a2,
                                                Packet4Xd& a3) {
  Packet4Xd aa;
  if (EIGEN_RISCV64_RVV_VL >= 256) {
    aa = __riscv_vlmul_ext_f64m4(__riscv_vle64_v_f64m1(a, 4));
  } else {
    aa = __riscv_vlmul_ext_f64m4(__riscv_vle64_v_f64m2(a, 4));
  }
  a0 = __riscv_vrgather_vx_f64m4(aa, 0, unpacket_traits<Packet4Xd>::size);
  a1 = __riscv_vrgather_vx_f64m4(aa, 1, unpacket_traits<Packet4Xd>::size);
  a2 = __riscv_vrgather_vx_f64m4(aa, 2, unpacket_traits<Packet4Xd>::size);
  a3 = __riscv_vrgather_vx_f64m4(aa, 3, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd padd<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfadd_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd psub<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfsub_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pnegate(const Packet4Xd& a) {
  return __riscv_vfneg_v_f64m4(a, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd psignbit(const Packet4Xd& a) {
  return __riscv_vreinterpret_v_i64m4_f64m4(
      __riscv_vsra_vx_i64m4(__riscv_vreinterpret_v_f64m4_i64m4(a), 63, unpacket_traits<Packet4Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pconj(const Packet4Xd& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmul<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfmul_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pdiv<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfdiv_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmadd(const Packet4Xd& a, const Packet4Xd& b, const Packet4Xd& c) {
  return __riscv_vfmadd_vv_f64m4(a, b, c, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmsub(const Packet4Xd& a, const Packet4Xd& b, const Packet4Xd& c) {
  return __riscv_vfmsub_vv_f64m4(a, b, c, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pnmadd(const Packet4Xd& a, const Packet4Xd& b, const Packet4Xd& c) {
  return __riscv_vfnmsub_vv_f64m4(a, b, c, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pnmsub(const Packet4Xd& a, const Packet4Xd& b, const Packet4Xd& c) {
  return __riscv_vfnmadd_vv_f64m4(a, b, c, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmin<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  Packet4Xd nans = __riscv_vfmv_v_f_f64m4((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet4Xd>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f64m4_b16(a, a, unpacket_traits<Packet4Xd>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f64m4_b16(b, b, unpacket_traits<Packet4Xd>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet4Xd>::size);

  return __riscv_vfmin_vv_f64m4_tumu(mask, nans, a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmin<PropagateNaN, Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return pmin<Packet4Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmin<PropagateNumbers, Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfmin_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmax<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  Packet4Xd nans = __riscv_vfmv_v_f_f64m4((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet4Xd>::size);
  PacketMask16 mask = __riscv_vmfeq_vv_f64m4_b16(a, a, unpacket_traits<Packet4Xd>::size);
  PacketMask16 mask2 = __riscv_vmfeq_vv_f64m4_b16(b, b, unpacket_traits<Packet4Xd>::size);
  mask = __riscv_vmand_mm_b16(mask, mask2, unpacket_traits<Packet4Xd>::size);

  return __riscv_vfmax_vv_f64m4_tumu(mask, nans, a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmax<PropagateNaN, Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return pmax<Packet4Xd>(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pmax<PropagateNumbers, Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vfmax_vv_f64m4(a, b, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcmp_le<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  PacketMask16 mask = __riscv_vmfle_vv_f64m4_b16(a, b, unpacket_traits<Packet4Xd>::size);
  return __riscv_vmerge_vvm_f64m4(pzero<Packet4Xd>(a), ptrue<Packet4Xd>(a), mask, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcmp_lt<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  PacketMask16 mask = __riscv_vmflt_vv_f64m4_b16(a, b, unpacket_traits<Packet4Xd>::size);
  return __riscv_vmerge_vvm_f64m4(pzero<Packet4Xd>(a), ptrue<Packet4Xd>(a), mask, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcmp_eq<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  PacketMask16 mask = __riscv_vmfeq_vv_f64m4_b16(a, b, unpacket_traits<Packet4Xd>::size);
  return __riscv_vmerge_vvm_f64m4(pzero<Packet4Xd>(a), ptrue<Packet4Xd>(a), mask, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcmp_lt_or_nan<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  PacketMask16 mask = __riscv_vmfge_vv_f64m4_b16(a, b, unpacket_traits<Packet4Xd>::size);
  return __riscv_vfmerge_vfm_f64m4(ptrue<Packet4Xd>(a), 0.0, mask, unpacket_traits<Packet4Xd>::size);
}

EIGEN_STRONG_INLINE Packet4Xd pselect(const PacketMask16& mask, const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vmerge_vvm_f64m4(b, a, mask, unpacket_traits<Packet4Xd>::size);
}

EIGEN_STRONG_INLINE Packet4Xd pselect(const Packet4Xd& mask, const Packet4Xd& a, const Packet4Xd& b) {
  PacketMask16 mask2 =
      __riscv_vmsne_vx_i64m4_b16(__riscv_vreinterpret_v_f64m4_i64m4(mask), 0, unpacket_traits<Packet4Xd>::size);
  return __riscv_vmerge_vvm_f64m4(b, a, mask2, unpacket_traits<Packet4Xd>::size);
}

// Logical Operations are not supported for double, so reinterpret casts
template <>
EIGEN_STRONG_INLINE Packet4Xd pand<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vreinterpret_v_u64m4_f64m4(__riscv_vand_vv_u64m4(
      __riscv_vreinterpret_v_f64m4_u64m4(a), __riscv_vreinterpret_v_f64m4_u64m4(b), unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd por<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vreinterpret_v_u64m4_f64m4(__riscv_vor_vv_u64m4(
      __riscv_vreinterpret_v_f64m4_u64m4(a), __riscv_vreinterpret_v_f64m4_u64m4(b), unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pxor<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
  return __riscv_vreinterpret_v_u64m4_f64m4(__riscv_vxor_vv_u64m4(
      __riscv_vreinterpret_v_f64m4_u64m4(a), __riscv_vreinterpret_v_f64m4_u64m4(b), unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pandnot<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& b) {
#ifndef __riscv_zvbb
  return __riscv_vreinterpret_v_u64m4_f64m4(__riscv_vand_vv_u64m4(
      __riscv_vreinterpret_v_f64m4_u64m4(a),
      __riscv_vnot_v_u64m4(__riscv_vreinterpret_v_f64m4_u64m4(b), unpacket_traits<Packet4Xd>::size),
      unpacket_traits<Packet4Xd>::size));
#else
  return __riscv_vreinterpret_v_u64m4_f64m4(__riscv_vandn_vv_u64m4(
      __riscv_vreinterpret_v_f64m4_u64m4(a), __riscv_vreinterpret_v_f64m4_u64m4(b), unpacket_traits<Packet4Xl>::size));
#endif
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pload<Packet4Xd>(const double* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle64_v_f64m4(from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd ploadu<Packet4Xd>(const double* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle64_v_f64m4(from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd ploaddup<Packet4Xd>(const double* from) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 1, unpacket_traits<Packet4Xd>::size);
  return __riscv_vrgather_vv_f64m4(__riscv_vlmul_ext_v_f64m2_f64m4(pload<Packet2Xd>(from)), idx,
                                   unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd ploadquad<Packet4Xd>(const double* from) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 2, unpacket_traits<Packet4Xd>::size);
  return __riscv_vrgather_vv_f64m4(__riscv_vlmul_ext_v_f64m1_f64m4(pload<Packet1Xd>(from)), idx,
                                   unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<double>(double* to, const Packet4Xd& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse64_v_f64m4(to, from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<double>(double* to, const Packet4Xd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse64_v_f64m4(to, from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet4Xd pgather<double, Packet4Xd>(const double* from, Index stride) {
  return __riscv_vlse64_v_f64m4(from, stride * sizeof(double), unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<double, Packet4Xd>(double* to, const Packet4Xd& from, Index stride) {
  __riscv_vsse64(to, stride * sizeof(double), from, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE double pfirst<Packet4Xd>(const Packet4Xd& a) {
  return __riscv_vfmv_f_s_f64m4_f64(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd psqrt(const Packet4Xd& a) {
  return __riscv_vfsqrt_v_f64m4(a, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd print<Packet4Xd>(const Packet4Xd& a) {
  const Packet4Xd limit = pset1<Packet4Xd>(static_cast<double>(1ull << 52));
  const Packet4Xd abs_a = pabs(a);

  PacketMask16 mask = __riscv_vmfne_vv_f64m4_b16(a, a, unpacket_traits<Packet4Xd>::size);
  const Packet4Xd x = __riscv_vfadd_vv_f64m4_tumu(mask, a, a, a, unpacket_traits<Packet4Xd>::size);
  const Packet4Xd new_x = __riscv_vfcvt_f_x_v_f64m4(__riscv_vfcvt_x_f_v_i64m4(a, unpacket_traits<Packet4Xd>::size),
                                                    unpacket_traits<Packet4Xd>::size);

  mask = __riscv_vmflt_vv_f64m4_b16(abs_a, limit, unpacket_traits<Packet4Xd>::size);
  Packet4Xd signed_x = __riscv_vfsgnj_vv_f64m4(new_x, x, unpacket_traits<Packet4Xd>::size);
  return __riscv_vmerge_vvm_f64m4(x, signed_x, mask, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pfloor<Packet4Xd>(const Packet4Xd& a) {
  Packet4Xd tmp = print<Packet4Xd>(a);
  // If greater, subtract one.
  PacketMask16 mask = __riscv_vmflt_vv_f64m4_b16(a, tmp, unpacket_traits<Packet4Xd>::size);
  return __riscv_vfsub_vf_f64m4_tumu(mask, tmp, tmp, 1.0, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd preverse(const Packet4Xd& a) {
  Packet4Xul idx = __riscv_vrsub_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size),
                                          unpacket_traits<Packet4Xd>::size - 1, unpacket_traits<Packet4Xd>::size);
  return __riscv_vrgather_vv_f64m4(a, idx, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pfrexp<Packet4Xd>(const Packet4Xd& a, Packet4Xd& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE double predux<Packet4Xd>(const Packet4Xd& a) {
  return __riscv_vfmv_f(__riscv_vfredusum_vs_f64m4_f64m1(
      a, __riscv_vfmv_v_f_f64m1(0.0, unpacket_traits<Packet4Xd>::size / 4), unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE double predux_mul<Packet4Xd>(const Packet4Xd& a) {
  Packet1Xd half1 = __riscv_vfmul_vv_f64m1(__riscv_vget_v_f64m4_f64m1(a, 0), __riscv_vget_v_f64m4_f64m1(a, 1),
                                           unpacket_traits<Packet1Xd>::size);
  Packet1Xd half2 = __riscv_vfmul_vv_f64m1(__riscv_vget_v_f64m4_f64m1(a, 2), __riscv_vget_v_f64m4_f64m1(a, 3),
                                           unpacket_traits<Packet1Xd>::size);
  return predux_mul<Packet1Xd>(__riscv_vfmul_vv_f64m1(half1, half2, unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE double predux_min<Packet4Xd>(const Packet4Xd& a) {
  return (std::min)(
      __riscv_vfmv_f(__riscv_vfredmin_vs_f64m4_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet4Xd>::size / 4),
          unpacket_traits<Packet4Xd>::size)),
      (std::numeric_limits<double>::max)());
}

template <>
EIGEN_STRONG_INLINE double predux_max<Packet4Xd>(const Packet4Xd& a) {
  return (std::max)(
      __riscv_vfmv_f(__riscv_vfredmax_vs_f64m4_f64m1(
          a, __riscv_vfmv_v_f_f64m1((std::numeric_limits<double>::quiet_NaN)(), unpacket_traits<Packet4Xd>::size / 4),
          unpacket_traits<Packet4Xd>::size)),
      -(std::numeric_limits<double>::max)());
}

template <>
EIGEN_STRONG_INLINE Packet2Xd predux_half(const Packet4Xd& a) {
  return Packet2Xd(__riscv_vfadd_vv_f64m2(__riscv_vget_v_f64m4_f64m2(a, 0), __riscv_vget_v_f64m4_f64m2(a, 1),
                                          unpacket_traits<Packet2Xd>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet4Xd, N>& kernel) {
  double buffer[unpacket_traits<Packet4Xd>::size * N];
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(double), kernel.packet[i], unpacket_traits<Packet4Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle64_v_f64m4(&buffer[i * unpacket_traits<Packet4Xd>::size], unpacket_traits<Packet4Xd>::size);
  }
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pldexp<Packet4Xd>(const Packet4Xd& a, const Packet4Xd& exponent) {
  return pldexp_generic(a, exponent);
}

template <typename Packet = Packet4Xd>
EIGEN_STRONG_INLINE
    typename std::enable_if_t<std::is_same<Packet, Packet4Xd>::value && (unpacket_traits<Packet4Xd>::size % 8) == 0,
                              Packet2Xd>
    predux_half(const Packet4Xd& a) {
  return __riscv_vfadd_vv_f64m2(__riscv_vget_v_f64m4_f64m2(a, 0), __riscv_vget_v_f64m4_f64m2(a, 1),
                                unpacket_traits<Packet2Xd>::size);
}

/********************************* Packet4Xs ************************************/

EIGEN_STRONG_INLINE Packet4Xs __riscv_vreinterpret_v_u32m4_i16m4(const Packet4Xu& a) {
  return __riscv_vreinterpret_v_i32m4_i16m4(__riscv_vreinterpret_v_u32m4_i32m4(a));
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pset1<Packet4Xs>(const numext::int16_t& from) {
  return __riscv_vmv_v_x_i16m4(from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs plset<Packet4Xs>(const numext::int16_t& a) {
  Packet4Xs idx = __riscv_vreinterpret_v_u16m4_i16m4(__riscv_vid_v_u16m4(unpacket_traits<Packet4Xs>::size));
  return __riscv_vadd_vx_i16m4(idx, a, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pzero<Packet4Xs>(const Packet4Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m4(0, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs padd<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vadd_vv_i16m4(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs psub<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vsub(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pnegate(const Packet4Xs& a) {
  return __riscv_vneg(a, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pconj(const Packet4Xs& a) {
  return a;
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pmul<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vmul(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pdiv<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vdiv(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pmadd(const Packet4Xs& a, const Packet4Xs& b, const Packet4Xs& c) {
  return __riscv_vmadd(a, b, c, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pmsub(const Packet4Xs& a, const Packet4Xs& b, const Packet4Xs& c) {
  return __riscv_vmadd(a, b, pnegate(c), unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pnmadd(const Packet4Xs& a, const Packet4Xs& b, const Packet4Xs& c) {
  return __riscv_vnmsub_vv_i16m4(a, b, c, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pnmsub(const Packet4Xs& a, const Packet4Xs& b, const Packet4Xs& c) {
  return __riscv_vnmsub_vv_i16m4(a, b, pnegate(c), unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pmin<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vmin(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pmax<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vmax(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pcmp_le<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  PacketMask4 mask = __riscv_vmsle_vv_i16m4_b4(a, b, unpacket_traits<Packet4Xs>::size);
  return __riscv_vmerge_vxm_i16m4(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pcmp_lt<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  PacketMask4 mask = __riscv_vmslt_vv_i16m4_b4(a, b, unpacket_traits<Packet4Xs>::size);
  return __riscv_vmerge_vxm_i16m4(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pcmp_eq<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  PacketMask4 mask = __riscv_vmseq_vv_i16m4_b4(a, b, unpacket_traits<Packet4Xs>::size);
  return __riscv_vmerge_vxm_i16m4(pzero(a), static_cast<short>(0xffff), mask, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs ptrue<Packet4Xs>(const Packet4Xs& /*a*/) {
  return __riscv_vmv_v_x_i16m4(static_cast<unsigned short>(0xffffu), unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pand<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vand_vv_i16m4(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs por<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vor_vv_i16m4(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pxor<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
  return __riscv_vxor_vv_i16m4(a, b, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pandnot<Packet4Xs>(const Packet4Xs& a, const Packet4Xs& b) {
#ifndef __riscv_zvbb
  return __riscv_vand_vv_i16m4(a, __riscv_vnot_v_i16m4(b, unpacket_traits<Packet4Xs>::size),
                               unpacket_traits<Packet4Xs>::size);
#else
  return __riscv_vreinterpret_v_u16m4_i16m4(__riscv_vandn_vv_u16m4(
      __riscv_vreinterpret_v_i16m4_u16m4(a), __riscv_vreinterpret_v_i16m4_u16m4(b), unpacket_traits<Packet4Xs>::size));
#endif
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xs parithmetic_shift_right(Packet4Xs a) {
  return __riscv_vsra_vx_i16m4(a, N, unpacket_traits<Packet4Xs>::size);
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xs plogical_shift_right(Packet4Xs a) {
  return __riscv_vreinterpret_i16m4(
      __riscv_vsrl_vx_u16m4(__riscv_vreinterpret_u16m4(a), N, unpacket_traits<Packet4Xs>::size));
}

template <int N>
EIGEN_STRONG_INLINE Packet4Xs plogical_shift_left(Packet4Xs a) {
  return __riscv_vsll_vx_i16m4(a, N, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pload<Packet4Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_ALIGNED_LOAD return __riscv_vle16_v_i16m4(from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs ploadu<Packet4Xs>(const numext::int16_t* from) {
  EIGEN_DEBUG_UNALIGNED_LOAD return __riscv_vle16_v_i16m4(from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs ploaddup<Packet4Xs>(const numext::int16_t* from) {
  Packet4Xu data = __riscv_vwcvtu_x_x_v_u32m4(__riscv_vreinterpret_v_i16m2_u16m2(pload<Packet2Xs>(from)),
                                              unpacket_traits<Packet2Xs>::size);
  return __riscv_vreinterpret_v_u32m4_i16m4(__riscv_vadd_vv_u32m4(
      __riscv_vsll_vx_u32m4(data, 16, unpacket_traits<Packet4Xi>::size), data, unpacket_traits<Packet4Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xs ploadquad<Packet4Xs>(const numext::int16_t* from) {
  Packet4Xsu idx =
      __riscv_vsrl_vx_u16m4(__riscv_vid_v_u16m4(unpacket_traits<Packet4Xs>::size), 2, unpacket_traits<Packet4Xs>::size);
  return __riscv_vrgather_vv_i16m4(__riscv_vlmul_ext_v_i16m1_i16m4(pload<Packet1Xs>(from)), idx,
                                   unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstore<numext::int16_t>(numext::int16_t* to, const Packet4Xs& from) {
  EIGEN_DEBUG_ALIGNED_STORE __riscv_vse16_v_i16m4(to, from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<numext::int16_t>(numext::int16_t* to, const Packet4Xs& from) {
  EIGEN_DEBUG_UNALIGNED_STORE __riscv_vse16_v_i16m4(to, from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline Packet4Xs pgather<numext::int16_t, Packet4Xs>(const numext::int16_t* from, Index stride) {
  return __riscv_vlse16_v_i16m4(from, stride * sizeof(numext::int16_t), unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_DEVICE_FUNC inline void pscatter<numext::int16_t, Packet4Xs>(numext::int16_t* to, const Packet4Xs& from,
                                                                   Index stride) {
  __riscv_vsse16(to, stride * sizeof(numext::int16_t), from, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t pfirst<Packet4Xs>(const Packet4Xs& a) {
  return __riscv_vmv_x_s_i16m4_i16(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs preverse(const Packet4Xs& a) {
  Packet4Xsu idx = __riscv_vrsub_vx_u16m4(__riscv_vid_v_u16m4(unpacket_traits<Packet4Xs>::size),
                                          unpacket_traits<Packet4Xs>::size - 1, unpacket_traits<Packet4Xs>::size);
  return __riscv_vrgather_vv_i16m4(a, idx, unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pabs(const Packet4Xs& a) {
  Packet4Xs mask = __riscv_vsra_vx_i16m4(a, 15, unpacket_traits<Packet4Xs>::size);
  return __riscv_vsub_vv_i16m4(__riscv_vxor_vv_i16m4(a, mask, unpacket_traits<Packet4Xs>::size), mask,
                               unpacket_traits<Packet4Xs>::size);
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux<Packet4Xs>(const Packet4Xs& a) {
  return __riscv_vmv_x(__riscv_vredsum_vs_i16m4_i16m1(a, __riscv_vmv_v_x_i16m1(0, unpacket_traits<Packet4Xs>::size / 4),
                                                      unpacket_traits<Packet4Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_mul<Packet4Xs>(const Packet4Xs& a) {
  Packet1Xs half1 = __riscv_vmul_vv_i16m1(__riscv_vget_v_i16m4_i16m1(a, 0), __riscv_vget_v_i16m4_i16m1(a, 1),
                                          unpacket_traits<Packet1Xs>::size);
  Packet1Xs half2 = __riscv_vmul_vv_i16m1(__riscv_vget_v_i16m4_i16m1(a, 2), __riscv_vget_v_i16m4_i16m1(a, 3),
                                          unpacket_traits<Packet1Xs>::size);
  return predux_mul<Packet1Xs>(__riscv_vmul_vv_i16m1(half1, half2, unpacket_traits<Packet1Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_min<Packet4Xs>(const Packet4Xs& a) {
  return __riscv_vmv_x(__riscv_vredmin_vs_i16m4_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::max)(), unpacket_traits<Packet4Xs>::size / 4),
      unpacket_traits<Packet4Xs>::size));
}

template <>
EIGEN_STRONG_INLINE numext::int16_t predux_max<Packet4Xs>(const Packet4Xs& a) {
  return __riscv_vmv_x(__riscv_vredmax_vs_i16m4_i16m1(
      a, __riscv_vmv_v_x_i16m1((std::numeric_limits<numext::int16_t>::min)(), unpacket_traits<Packet4Xs>::size / 4),
      unpacket_traits<Packet4Xs>::size));
}

template <int N>
EIGEN_DEVICE_FUNC inline void ptranspose(PacketBlock<Packet4Xs, N>& kernel) {
  numext::int16_t buffer[unpacket_traits<Packet4Xs>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse16(&buffer[i], N * sizeof(numext::int16_t), kernel.packet[i], unpacket_traits<Packet4Xs>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] =
        __riscv_vle16_v_i16m4(&buffer[i * unpacket_traits<Packet4Xs>::size], unpacket_traits<Packet4Xs>::size);
  }
}

template <typename Packet = Packet4Xs>
EIGEN_STRONG_INLINE
    typename std::enable_if_t<std::is_same<Packet, Packet4Xs>::value && (unpacket_traits<Packet4Xs>::size % 8) == 0,
                              Packet2Xs>
    predux_half(const Packet4Xs& a) {
  return __riscv_vadd_vv_i16m2(__riscv_vget_v_i16m4_i16m2(a, 0), __riscv_vget_v_i16m4_i16m2(a, 1),
                               unpacket_traits<Packet2Xs>::size);
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_PACKET4_MATH_RVV10_H

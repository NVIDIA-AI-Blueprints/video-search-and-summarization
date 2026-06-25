// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Chip Kerchner <ckerchner@tenstorrent.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_COMPLEX2_RVV10_H
#define EIGEN_COMPLEX2_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/********************************* float32 ************************************/

template <>
EIGEN_STRONG_INLINE Packet4Xcf pcast<Packet4Xf, Packet4Xcf>(const Packet4Xf& a) {
  return Packet4Xcf(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcast<Packet4Xcf, Packet4Xf>(const Packet4Xcf& a) {
  return a.v;
}

EIGEN_STRONG_INLINE Packet4Xul __riscv_vreinterpret_v_f32m4_u64m4(const Packet4Xf& a) {
  return __riscv_vreinterpret_v_u32m4_u64m4(__riscv_vreinterpret_v_f32m4_u32m4(a));
}

EIGEN_STRONG_INLINE Packet4Xl __riscv_vreinterpret_v_f32m4_i64m4(const Packet4Xf& a) {
  return __riscv_vreinterpret_v_u64m4_i64m4(__riscv_vreinterpret_v_u32m4_u64m4(__riscv_vreinterpret_v_f32m4_u32m4(a)));
}

EIGEN_STRONG_INLINE Packet4Xf __riscv_vreinterpret_v_i64m4_f32m4(const Packet4Xl& a) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vreinterpret_v_u64m4_u32m4(__riscv_vreinterpret_v_i64m4_u64m4(a)));
}

EIGEN_STRONG_INLINE Packet2Xd __riscv_vreinterpret_v_f32m2_f64m2(const Packet2Xf& a) {
  return __riscv_vreinterpret_v_u64m2_f64m2(__riscv_vreinterpret_v_u32m2_u64m2(__riscv_vreinterpret_v_f32m2_u32m2(a)));
}

EIGEN_STRONG_INLINE Packet4Xf __riscv_vreinterpret_v_f64m4_f32m4(const Packet4Xd& a) {
  return __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vreinterpret_v_u64m4_u32m4(__riscv_vreinterpret_v_f64m4_u64m4(a)));
}

EIGEN_STRONG_INLINE void prealimag2(const Packet4Xcf& a, Packet4Xf& real, Packet4Xf& imag) {
  const PacketMask8 mask =
      __riscv_vreinterpret_v_i8m1_b8(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet4Xu res = __riscv_vreinterpret_v_f32m4_u32m4(a.v);
  real = __riscv_vreinterpret_v_u32m4_f32m4(
      __riscv_vslide1up_vx_u32m4_tumu(mask, res, res, 0, unpacket_traits<Packet4Xi>::size));
  imag = __riscv_vreinterpret_v_u32m4_f32m4(__riscv_vslide1down_vx_u32m4_tumu(
      __riscv_vmnot_m_b8(mask, unpacket_traits<Packet1Xc>::size), res, res, 0, unpacket_traits<Packet4Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pset1<Packet4Xcf>(const std::complex<float>& from) {
  const numext::int64_t from2 = *reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(&from));
  Packet4Xf res = __riscv_vreinterpret_v_i64m4_f32m4(pset1<Packet4Xl>(from2));
  return Packet4Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf padd<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(padd<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf psub<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(psub<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pnegate(const Packet4Xcf& a) {
  return Packet4Xcf(pnegate<Packet4Xf>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pconj(const Packet4Xcf& a) {
  return Packet4Xcf(__riscv_vreinterpret_v_u64m4_f32m4(__riscv_vxor_vx_u64m4(
      __riscv_vreinterpret_v_f32m4_u64m4(a.v), 0x8000000000000000ull, unpacket_traits<Packet4Xl>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pcplxflip<Packet4Xcf>(const Packet4Xcf& a) {
#ifndef __riscv_zvbb
  Packet4Xu res = __riscv_vreinterpret_v_f32m4_u32m4(a.v);
  const PacketMask8 mask =
      __riscv_vreinterpret_v_i8m1_b8(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet4Xu data = __riscv_vslide1down_vx_u32m4(res, 0, unpacket_traits<Packet4Xi>::size);
  Packet4Xf res2 = __riscv_vreinterpret_v_u32m4_f32m4(
      __riscv_vslide1up_vx_u32m4_tumu(mask, data, res, 0, unpacket_traits<Packet4Xi>::size));
  return Packet4Xcf(res2);
#else
  Packet4Xf res = __riscv_vreinterpret_v_u64m4_f32m4(
      __riscv_vror_vx_u64m4(__riscv_vreinterpret_v_f32m4_u64m4(a.v), 32, unpacket_traits<Packet4Xl>::size));
  return Packet4Xcf(res);
#endif
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pmul<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  Packet4Xf real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcf(pmadd<Packet4Xf>(imag, pcplxflip<Packet4Xcf>(pconj<Packet4Xcf>(b)).v, pmul<Packet4Xf>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pmadd<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b, const Packet4Xcf& c) {
  Packet4Xf real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcf(
      pmadd<Packet4Xf>(imag, pcplxflip<Packet4Xcf>(pconj<Packet4Xcf>(b)).v, pmadd<Packet4Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pmsub<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b, const Packet4Xcf& c) {
  Packet4Xf real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcf(
      pmadd<Packet4Xf>(imag, pcplxflip<Packet4Xcf>(pconj<Packet4Xcf>(b)).v, pmsub<Packet4Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pcmp_eq(const Packet4Xcf& a, const Packet4Xcf& b) {
  Packet4Xi c = __riscv_vundefined_i32m4();
  PacketMask8 mask = __riscv_vmfeq_vv_f32m4_b8(a.v, b.v, unpacket_traits<Packet4Xf>::size);
  Packet4Xl res = __riscv_vreinterpret_v_i32m4_i64m4(
      __riscv_vmerge_vvm_i32m4(pzero<Packet4Xi>(c), ptrue<Packet4Xi>(c), mask, unpacket_traits<Packet4Xi>::size));
  Packet4Xf res2 = __riscv_vreinterpret_v_i64m4_f32m4(
      __riscv_vsra_vx_i64m4(__riscv_vand_vv_i64m4(__riscv_vsll_vx_i64m4(res, 32, unpacket_traits<Packet4Xl>::size), res,
                                                  unpacket_traits<Packet4Xl>::size),
                            32, unpacket_traits<Packet4Xl>::size));
  return Packet4Xcf(res2);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pand<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(pand<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf por<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(por<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pxor<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(pxor<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pandnot<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return Packet4Xcf(pandnot<Packet4Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pload<Packet4Xcf>(const std::complex<float>* from) {
  Packet4Xf res = pload<Packet4Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet4Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf ploadu<Packet4Xcf>(const std::complex<float>* from) {
  Packet4Xf res = ploadu<Packet4Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet4Xcf(res);
}

EIGEN_STRONG_INLINE Packet4Xcf pdup(const Packet2Xcf& a) {
  Packet4Xul idx =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 1, unpacket_traits<Packet4Xd>::size);
  return Packet4Xcf(__riscv_vreinterpret_v_f64m4_f32m4(
      __riscv_vrgather_vv_f64m4(__riscv_vlmul_ext_v_f64m2_f64m4(__riscv_vreinterpret_v_f32m2_f64m2(a.v)), idx,
                                unpacket_traits<Packet4Xd>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf ploaddup<Packet4Xcf>(const std::complex<float>* from) {
  Packet4Xl res = ploaddup<Packet4Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet4Xcf(__riscv_vreinterpret_v_i64m4_f32m4(res));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf ploadquad<Packet4Xcf>(const std::complex<float>* from) {
  Packet4Xl res = ploadquad<Packet4Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet4Xcf(__riscv_vreinterpret_v_i64m4_f32m4(res));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<float>>(std::complex<float>* to, const Packet4Xcf& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<float>>(std::complex<float>* to, const Packet4Xcf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet4Xcf
pgather<std::complex<float>, Packet4Xcf>(const std::complex<float>* from, Index stride) {
  return Packet4Xcf(__riscv_vreinterpret_v_i64m4_f32m4(pgather<int64_t, Packet4Xl>(
      reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)), stride)));
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<float>, Packet4Xcf>(std::complex<float>* to,
                                                                                     const Packet4Xcf& from,
                                                                                     Index stride) {
  pscatter<int64_t, Packet4Xl>(reinterpret_cast<numext::int64_t*>(reinterpret_cast<void*>(to)),
                               __riscv_vreinterpret_v_f32m4_i64m4(from.v), stride);
}

template <>
EIGEN_STRONG_INLINE std::complex<float> pfirst<Packet4Xcf>(const Packet4Xcf& a) {
  numext::int64_t res = pfirst<Packet4Xl>(__riscv_vreinterpret_v_f32m4_i64m4(a.v));
  return numext::bit_cast<std::complex<float>>(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf preverse(const Packet4Xcf& a) {
  return Packet4Xcf(__riscv_vreinterpret_v_i64m4_f32m4(preverse<Packet4Xl>(__riscv_vreinterpret_v_f32m4_i64m4(a.v))));
}

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<Packet4Xcf>(const Packet4Xcf& a) {
  Packet4Xl res = __riscv_vreinterpret_v_f32m4_i64m4(a.v);
  Packet4Xf real = __riscv_vreinterpret_v_i64m4_f32m4(
      __riscv_vand_vx_i64m4(res, 0x00000000ffffffffull, unpacket_traits<Packet4Xl>::size));
  Packet4Xf imag = __riscv_vreinterpret_v_i64m4_f32m4(
      __riscv_vand_vx_i64m4(res, 0xffffffff00000000ull, unpacket_traits<Packet4Xl>::size));
  return std::complex<float>(predux<Packet4Xf>(real), predux<Packet4Xf>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pdiv<Packet4Xcf>(const Packet4Xcf& a, const Packet4Xcf& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet4Xcf, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet4Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), __riscv_vreinterpret_v_f32m4_i64m4(kernel.packet[i].v),
                   unpacket_traits<Packet4Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet4Xcf(__riscv_vreinterpret_v_i64m4_f32m4(
        __riscv_vle64_v_i64m4(&buffer[i * unpacket_traits<Packet4Xl>::size], unpacket_traits<Packet4Xl>::size)));
  }
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf psqrt<Packet4Xcf>(const Packet4Xcf& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf plog<Packet4Xcf>(const Packet4Xcf& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcf pexp<Packet4Xcf>(const Packet4Xcf& a) {
  return pexp_complex(a);
}

template <typename Packet = Packet4Xcf>
EIGEN_STRONG_INLINE Packet2Xcf predux_half(const Packet4Xcf& a) {
  return Packet2Xcf(__riscv_vfadd_vv_f32m2(__riscv_vget_v_f32m4_f32m2(a.v, 0), __riscv_vget_v_f32m4_f32m2(a.v, 1),
                                           unpacket_traits<Packet2Xf>::size));
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet4Xcf, Packet4Xf)

template <>
EIGEN_STRONG_INLINE Packet1Xcf pcast<Packet1Xf, Packet1Xcf>(const Packet1Xf& a) {
  return Packet1Xcf(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf pcast<Packet1Xcf, Packet1Xf>(const Packet1Xcf& a) {
  return a.v;
}

EIGEN_STRONG_INLINE Packet1Xul __riscv_vreinterpret_v_f32m1_u64m1(const Packet1Xf& a) {
  return __riscv_vreinterpret_v_u32m1_u64m1(__riscv_vreinterpret_v_f32m1_u32m1(a));
}

EIGEN_STRONG_INLINE Packet1Xl __riscv_vreinterpret_v_f32m1_i64m1(const Packet1Xf& a) {
  return __riscv_vreinterpret_v_u64m1_i64m1(__riscv_vreinterpret_v_u32m1_u64m1(__riscv_vreinterpret_v_f32m1_u32m1(a)));
}

EIGEN_STRONG_INLINE Packet1Xf __riscv_vreinterpret_v_i64m1_f32m1(const Packet1Xl& a) {
  return __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vreinterpret_v_u64m1_u32m1(__riscv_vreinterpret_v_i64m1_u64m1(a)));
}

EIGEN_STRONG_INLINE Packet1Xd __riscv_vreinterpret_v_f32m1_f64m1(const Packet1Xf& a) {
  return __riscv_vreinterpret_v_u64m1_f64m1(__riscv_vreinterpret_v_u32m1_u64m1(__riscv_vreinterpret_v_f32m1_u32m1(a)));
}

EIGEN_STRONG_INLINE Packet2Xf __riscv_vreinterpret_v_f64m2_f32m2(const Packet2Xd& a) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vreinterpret_v_u64m2_u32m2(__riscv_vreinterpret_v_f64m2_u64m2(a)));
}

EIGEN_STRONG_INLINE void prealimag2(const Packet1Xcf& a, Packet1Xf& real, Packet1Xf& imag) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet1Xu res = __riscv_vreinterpret_v_f32m1_u32m1(a.v);
  real = __riscv_vreinterpret_v_u32m1_f32m1(
      __riscv_vslide1up_vx_u32m1_tumu(mask, res, res, 0, unpacket_traits<Packet1Xi>::size));
  imag = __riscv_vreinterpret_v_u32m1_f32m1(__riscv_vslide1down_vx_u32m1_tumu(
      __riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size), res, res, 0, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pset1<Packet1Xcf>(const std::complex<float>& from) {
  const numext::int64_t from2 = *reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(&from));
  Packet1Xf res = __riscv_vreinterpret_v_i64m1_f32m1(pset1<Packet1Xl>(from2));
  return Packet1Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf padd<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(padd<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf psub<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(psub<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pnegate(const Packet1Xcf& a) {
  return Packet1Xcf(pnegate<Packet1Xf>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pconj(const Packet1Xcf& a) {
  return Packet1Xcf(__riscv_vreinterpret_v_u64m1_f32m1(__riscv_vxor_vx_u64m1(
      __riscv_vreinterpret_v_f32m1_u64m1(a.v), 0x8000000000000000ull, unpacket_traits<Packet1Xl>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pcplxflip<Packet1Xcf>(const Packet1Xcf& a) {
#ifndef __riscv_zvbb
  Packet1Xu res = __riscv_vreinterpret_v_f32m1_u32m1(a.v);
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet1Xu data = __riscv_vslide1down_vx_u32m1(res, 0, unpacket_traits<Packet1Xi>::size);
  Packet1Xf res2 = __riscv_vreinterpret_v_u32m1_f32m1(
      __riscv_vslide1up_vx_u32m1_tumu(mask, data, res, 0, unpacket_traits<Packet1Xf>::size));
  return Packet1Xcf(res2);
#else
  Packet1Xf res = __riscv_vreinterpret_v_u64m1_f32m1(
      __riscv_vror_vx_u64m1(__riscv_vreinterpret_v_f32m1_u64m1(a.v), 32, unpacket_traits<Packet1Xl>::size));
  return Packet1Xcf(res);
#endif
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pmul<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  Packet1Xf real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcf(pmadd<Packet1Xf>(imag, pcplxflip<Packet1Xcf>(pconj<Packet1Xcf>(b)).v, pmul<Packet1Xf>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pmadd<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b, const Packet1Xcf& c) {
  Packet1Xf real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcf(
      pmadd<Packet1Xf>(imag, pcplxflip<Packet1Xcf>(pconj<Packet1Xcf>(b)).v, pmadd<Packet1Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pmsub<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b, const Packet1Xcf& c) {
  Packet1Xf real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcf(
      pmadd<Packet1Xf>(imag, pcplxflip<Packet1Xcf>(pconj<Packet1Xcf>(b)).v, pmsub<Packet1Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pcmp_eq(const Packet1Xcf& a, const Packet1Xcf& b) {
  Packet1Xi c = __riscv_vundefined_i32m1();
  PacketMask32 mask = __riscv_vmfeq_vv_f32m1_b32(a.v, b.v, unpacket_traits<Packet1Xf>::size);
  Packet1Xl res = __riscv_vreinterpret_v_i32m1_i64m1(
      __riscv_vmerge_vvm_i32m1(pzero<Packet1Xi>(c), ptrue<Packet1Xi>(c), mask, unpacket_traits<Packet1Xi>::size));
  Packet1Xf res2 = __riscv_vreinterpret_v_i64m1_f32m1(
      __riscv_vsra_vx_i64m1(__riscv_vand_vv_i64m1(__riscv_vsll_vx_i64m1(res, 32, unpacket_traits<Packet1Xl>::size), res,
                                                  unpacket_traits<Packet1Xl>::size),
                            32, unpacket_traits<Packet1Xl>::size));
  return Packet1Xcf(res2);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pand<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(pand<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf por<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(por<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pxor<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(pxor<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pandnot<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return Packet1Xcf(pandnot<Packet1Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pload<Packet1Xcf>(const std::complex<float>* from) {
  Packet1Xf res = pload<Packet1Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet1Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf ploadu<Packet1Xcf>(const std::complex<float>* from) {
  Packet1Xf res = ploadu<Packet1Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet1Xcf(res);
}

EIGEN_STRONG_INLINE Packet2Xcf pdup(const Packet1Xcf& a) {
  Packet2Xul idx =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 1, unpacket_traits<Packet2Xd>::size);
  return Packet2Xcf(__riscv_vreinterpret_v_f64m2_f32m2(
      __riscv_vrgather_vv_f64m2(__riscv_vlmul_ext_v_f64m1_f64m2(__riscv_vreinterpret_v_f32m1_f64m1(a.v)), idx,
                                unpacket_traits<Packet2Xd>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf ploaddup<Packet1Xcf>(const std::complex<float>* from) {
  Packet1Xl res = ploaddup<Packet1Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet1Xcf(__riscv_vreinterpret_v_i64m1_f32m1(res));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf ploadquad<Packet1Xcf>(const std::complex<float>* from) {
  Packet1Xl res = ploadquad<Packet1Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet1Xcf(__riscv_vreinterpret_v_i64m1_f32m1(res));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<float>>(std::complex<float>* to, const Packet1Xcf& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<float>>(std::complex<float>* to, const Packet1Xcf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet1Xcf
pgather<std::complex<float>, Packet1Xcf>(const std::complex<float>* from, Index stride) {
  return Packet1Xcf(__riscv_vreinterpret_v_i64m1_f32m1(pgather<int64_t, Packet1Xl>(
      reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)), stride)));
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<float>, Packet1Xcf>(std::complex<float>* to,
                                                                                     const Packet1Xcf& from,
                                                                                     Index stride) {
  pscatter<int64_t, Packet1Xl>(reinterpret_cast<numext::int64_t*>(reinterpret_cast<void*>(to)),
                               __riscv_vreinterpret_v_f32m1_i64m1(from.v), stride);
}

template <>
EIGEN_STRONG_INLINE std::complex<float> pfirst<Packet1Xcf>(const Packet1Xcf& a) {
  numext::int64_t res = pfirst<Packet1Xl>(__riscv_vreinterpret_v_f32m1_i64m1(a.v));
  return numext::bit_cast<std::complex<float>>(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf preverse(const Packet1Xcf& a) {
  return Packet1Xcf(__riscv_vreinterpret_v_i64m1_f32m1(preverse<Packet1Xl>(__riscv_vreinterpret_v_f32m1_i64m1(a.v))));
}

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<Packet1Xcf>(const Packet1Xcf& a) {
  Packet1Xl res = __riscv_vreinterpret_v_f32m1_i64m1(a.v);
  Packet1Xf real = __riscv_vreinterpret_v_i64m1_f32m1(
      __riscv_vand_vx_i64m1(res, 0x00000000ffffffffull, unpacket_traits<Packet1Xl>::size));
  Packet1Xf imag = __riscv_vreinterpret_v_i64m1_f32m1(
      __riscv_vand_vx_i64m1(res, 0xffffffff00000000ull, unpacket_traits<Packet1Xl>::size));
  return std::complex<float>(predux<Packet1Xf>(real), predux<Packet1Xf>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pdiv<Packet1Xcf>(const Packet1Xcf& a, const Packet1Xcf& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet1Xcf, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet1Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), __riscv_vreinterpret_v_f32m1_i64m1(kernel.packet[i].v),
                   unpacket_traits<Packet1Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet1Xcf(__riscv_vreinterpret_v_i64m1_f32m1(
        __riscv_vle64_v_i64m1(&buffer[i * unpacket_traits<Packet1Xl>::size], unpacket_traits<Packet1Xl>::size)));
  }
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf psqrt<Packet1Xcf>(const Packet1Xcf& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf plog<Packet1Xcf>(const Packet1Xcf& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcf pexp<Packet1Xcf>(const Packet1Xcf& a) {
  return pexp_complex(a);
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet1Xcf, Packet1Xf)

/********************************* double ************************************/

template <>
EIGEN_STRONG_INLINE Packet4Xcd pcast<Packet4Xd, Packet4Xcd>(const Packet4Xd& a) {
  return Packet4Xcd(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcast<Packet4Xcd, Packet4Xd>(const Packet4Xcd& a) {
  return a.v;
}

EIGEN_STRONG_INLINE void prealimag2(const Packet4Xcd& a, Packet4Xd& real, Packet4Xd& imag) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  real = __riscv_vfslide1up_vf_f64m4_tumu(mask, a.v, a.v, 0.0, unpacket_traits<Packet4Xd>::size);
  imag = __riscv_vfslide1down_vf_f64m4_tumu(__riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size), a.v, a.v, 0.0,
                                            unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pset1<Packet4Xcd>(const std::complex<double>& from) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet4Xd res = __riscv_vmerge_vvm_f64m4(pset1<Packet4Xd>(from.real()), pset1<Packet4Xd>(from.imag()), mask,
                                           unpacket_traits<Packet4Xd>::size);
  return Packet4Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd padd<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(padd<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd psub<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(psub<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pnegate(const Packet4Xcd& a) {
  return Packet4Xcd(pnegate<Packet4Xd>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pconj(const Packet4Xcd& a) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  return Packet4Xcd(__riscv_vfsgnjn_vv_f64m4_tumu(mask, a.v, a.v, a.v, unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pcplxflip<Packet4Xcd>(const Packet4Xcd& a) {
  Packet4Xul res = __riscv_vreinterpret_v_f64m4_u64m4(a.v);
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet4Xul data = __riscv_vslide1down_vx_u64m4(res, 0, unpacket_traits<Packet4Xl>::size);
  Packet4Xd res2 = __riscv_vreinterpret_v_u64m4_f64m4(
      __riscv_vslide1up_vx_u64m4_tumu(mask, data, res, 0, unpacket_traits<Packet4Xl>::size));
  return Packet4Xcd(res2);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pmul<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  Packet4Xd real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcd(pmadd<Packet4Xd>(imag, pcplxflip<Packet4Xcd>(pconj<Packet4Xcd>(b)).v, pmul<Packet4Xd>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pmadd<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b, const Packet4Xcd& c) {
  Packet4Xd real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcd(
      pmadd<Packet4Xd>(imag, pcplxflip<Packet4Xcd>(pconj<Packet4Xcd>(b)).v, pmadd<Packet4Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pmsub<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b, const Packet4Xcd& c) {
  Packet4Xd real, imag;
  prealimag2(a, real, imag);
  return Packet4Xcd(
      pmadd<Packet4Xd>(imag, pcplxflip<Packet4Xcd>(pconj<Packet4Xcd>(b)).v, pmsub<Packet4Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pcmp_eq(const Packet4Xcd& a, const Packet4Xcd& b) {
  Packet4Xl c = __riscv_vundefined_i64m4();
  Packet1Xsu mask =
      __riscv_vreinterpret_v_b16_u16m1(__riscv_vmfeq_vv_f64m4_b16(a.v, b.v, unpacket_traits<Packet4Xd>::size));
  Packet1Xsu mask_r =
      __riscv_vsrl_vx_u16m1(__riscv_vand_vx_u16m1(mask, static_cast<short>(0xaaaa), unpacket_traits<Packet1Xs>::size),
                            1, unpacket_traits<Packet1Xs>::size);
  mask = __riscv_vand_vv_u16m1(mask, mask_r, unpacket_traits<Packet1Xs>::size);
  mask = __riscv_vor_vv_u16m1(__riscv_vsll_vx_u16m1(mask, 1, unpacket_traits<Packet1Xs>::size), mask,
                              unpacket_traits<Packet1Xs>::size);
  Packet4Xd res = __riscv_vreinterpret_v_i64m4_f64m4(__riscv_vmerge_vvm_i64m4(pzero<Packet4Xl>(c), ptrue<Packet4Xl>(c),
                                                                              __riscv_vreinterpret_v_u16m1_b16(mask),
                                                                              unpacket_traits<Packet4Xl>::size));
  return Packet4Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pand<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(pand<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd por<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(por<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pxor<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(pxor<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pandnot<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return Packet4Xcd(pandnot<Packet4Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pload<Packet4Xcd>(const std::complex<double>* from) {
  Packet4Xd res = pload<Packet4Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet4Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd ploadu<Packet4Xcd>(const std::complex<double>* from) {
  Packet4Xd res = ploadu<Packet4Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet4Xcd(res);
}

EIGEN_STRONG_INLINE Packet4Xcd pdup(const Packet2Xcd& a) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x66), unpacket_traits<Packet1Xc>::size));
  Packet4Xul idx1 =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 1, unpacket_traits<Packet4Xd>::size);
  Packet4Xul idx2 = __riscv_vxor_vx_u64m4_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet4Xl>::size);
  return Packet4Xcd(
      __riscv_vrgather_vv_f64m4(__riscv_vlmul_ext_v_f64m2_f64m4(a.v), idx2, unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd ploaddup<Packet4Xcd>(const std::complex<double>* from) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x66), unpacket_traits<Packet1Xc>::size));
  Packet4Xul idx1 =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 1, unpacket_traits<Packet4Xd>::size);
  Packet4Xul idx2 = __riscv_vxor_vx_u64m4_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet4Xl>::size);
  return Packet4Xcd(__riscv_vrgather_vv_f64m4(
      __riscv_vlmul_ext_v_f64m2_f64m4(pload<Packet2Xd>(reinterpret_cast<const double*>(from))), idx2,
      unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd ploadquad<Packet4Xcd>(const std::complex<double>* from) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x5a), unpacket_traits<Packet1Xc>::size));
  Packet4Xul idx1 =
      __riscv_vsrl_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xd>::size), 2, unpacket_traits<Packet4Xd>::size);
  Packet4Xul idx2 = __riscv_vxor_vx_u64m4_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet4Xl>::size);
  return Packet4Xcd(__riscv_vrgather_vv_f64m4(
      __riscv_vlmul_ext_v_f64m1_f64m4(pload<Packet1Xd>(reinterpret_cast<const double*>(from))), idx2,
      unpacket_traits<Packet4Xd>::size));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<double>>(std::complex<double>* to, const Packet4Xcd& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<double>>(std::complex<double>* to, const Packet4Xcd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet4Xcd
pgather<std::complex<double>, Packet4Xcd>(const std::complex<double>* from, Index stride) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  const double* from2 = reinterpret_cast<const double*>(from);
  Packet4Xd res = __riscv_vundefined_f64m4();
  res = __riscv_vlse64_v_f64m4_tumu(mask, res, &from2[0 - (0 * stride)], stride * sizeof(double),
                                    unpacket_traits<Packet4Xd>::size);
  res =
      __riscv_vlse64_v_f64m4_tumu(__riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size), res,
                                  &from2[1 - (1 * stride)], stride * sizeof(double), unpacket_traits<Packet4Xd>::size);
  return Packet4Xcd(res);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<double>, Packet4Xcd>(std::complex<double>* to,
                                                                                      const Packet4Xcd& from,
                                                                                      Index stride) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  double* to2 = reinterpret_cast<double*>(to);
  __riscv_vsse64_v_f64m4_m(mask, &to2[0 - (0 * stride)], stride * sizeof(double), from.v,
                           unpacket_traits<Packet4Xd>::size);
  __riscv_vsse64_v_f64m4_m(__riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size), &to2[1 - (1 * stride)],
                           stride * sizeof(double), from.v, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> pfirst<Packet4Xcd>(const Packet4Xcd& a) {
  double real = pfirst<Packet4Xd>(a.v);
  double imag = pfirst<Packet4Xd>(__riscv_vfslide1down_vf_f64m4(a.v, 0.0, unpacket_traits<Packet4Xd>::size));
  return std::complex<double>(real, imag);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd preverse(const Packet4Xcd& a) {
  Packet4Xul idx = __riscv_vxor_vx_u64m4(__riscv_vid_v_u64m4(unpacket_traits<Packet4Xl>::size),
                                         unpacket_traits<Packet4Xl>::size - 2, unpacket_traits<Packet4Xl>::size);
  Packet4Xd res = __riscv_vrgather_vv_f64m4(a.v, idx, unpacket_traits<Packet4Xd>::size);
  return Packet4Xcd(res);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<Packet4Xcd>(const Packet4Xcd& a) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet4Xl res = __riscv_vreinterpret_v_f64m4_i64m4(a.v);
  Packet4Xd real = __riscv_vreinterpret_v_i64m4_f64m4(
      __riscv_vand_vx_i64m4_tumu(mask, res, res, 0, unpacket_traits<Packet4Xl>::size));
  Packet4Xd imag = __riscv_vreinterpret_v_i64m4_f64m4(__riscv_vand_vx_i64m4_tumu(
      __riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size), res, res, 0, unpacket_traits<Packet4Xl>::size));
  return std::complex<double>(predux<Packet4Xd>(real), predux<Packet4Xd>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pdiv<Packet4Xcd>(const Packet4Xcd& a, const Packet4Xcd& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet4Xcd, N>& kernel) {
  double buffer[unpacket_traits<Packet4Xd>::size * N];
  int i = 0;

  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));

  for (i = 0; i < N; i++) {
    __riscv_vsse64_v_f64m4_m(mask, &buffer[(i * 2) - (0 * N) + 0], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet4Xd>::size);
    __riscv_vsse64_v_f64m4_m(__riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size),
                             &buffer[(i * 2) - (1 * N) + 1], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet4Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet4Xcd(
        __riscv_vle64_v_f64m4(&buffer[i * unpacket_traits<Packet4Xd>::size], unpacket_traits<Packet4Xd>::size));
  }
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd psqrt<Packet4Xcd>(const Packet4Xcd& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd plog<Packet4Xcd>(const Packet4Xcd& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xcd pexp<Packet4Xcd>(const Packet4Xcd& a) {
  return pexp_complex(a);
}

template <typename Packet = Packet4Xcd>
EIGEN_STRONG_INLINE Packet2Xcd predux_half(const Packet4Xcd& a) {
  return Packet2Xcd(__riscv_vfadd_vv_f64m2(__riscv_vget_v_f64m4_f64m2(a.v, 0), __riscv_vget_v_f64m4_f64m2(a.v, 1),
                                           unpacket_traits<Packet2Xd>::size));
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet4Xcd, Packet4Xd)

template <>
EIGEN_STRONG_INLINE Packet1Xcd pcast<Packet1Xd, Packet1Xcd>(const Packet1Xd& a) {
  return Packet1Xcd(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd pcast<Packet1Xcd, Packet1Xd>(const Packet1Xcd& a) {
  return a.v;
}

EIGEN_STRONG_INLINE void prealimag2(const Packet1Xcd& a, Packet1Xd& real, Packet1Xd& imag) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  real = __riscv_vfslide1up_vf_f64m1_tumu(mask, a.v, a.v, 0.0, unpacket_traits<Packet1Xd>::size);
  imag = __riscv_vfslide1down_vf_f64m1_tumu(__riscv_vmnot_m_b64(mask, unpacket_traits<Packet1Xl>::size), a.v, a.v, 0.0,
                                            unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pset1<Packet1Xcd>(const std::complex<double>& from) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet1Xd res = __riscv_vmerge_vvm_f64m1(pset1<Packet1Xd>(from.real()), pset1<Packet1Xd>(from.imag()), mask,
                                           unpacket_traits<Packet1Xd>::size);
  return Packet1Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd padd<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(padd<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd psub<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(psub<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pnegate(const Packet1Xcd& a) {
  return Packet1Xcd(pnegate<Packet1Xd>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pconj(const Packet1Xcd& a) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  return Packet1Xcd(__riscv_vfsgnjn_vv_f64m1_tumu(mask, a.v, a.v, a.v, unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pcplxflip<Packet1Xcd>(const Packet1Xcd& a) {
  Packet1Xul res = __riscv_vreinterpret_v_f64m1_u64m1(a.v);
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet1Xul data = __riscv_vslide1down_vx_u64m1(res, 0, unpacket_traits<Packet1Xl>::size);
  Packet1Xd res2 = __riscv_vreinterpret_v_u64m1_f64m1(
      __riscv_vslide1up_vx_u64m1_tumu(mask, data, res, 0, unpacket_traits<Packet1Xl>::size));
  return Packet1Xcd(res2);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pmul<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  Packet1Xd real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcd(pmadd<Packet1Xd>(imag, pcplxflip<Packet1Xcd>(pconj<Packet1Xcd>(b)).v, pmul<Packet1Xd>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pmadd<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b, const Packet1Xcd& c) {
  Packet1Xd real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcd(
      pmadd<Packet1Xd>(imag, pcplxflip<Packet1Xcd>(pconj<Packet1Xcd>(b)).v, pmadd<Packet1Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pmsub<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b, const Packet1Xcd& c) {
  Packet1Xd real, imag;
  prealimag2(a, real, imag);
  return Packet1Xcd(
      pmadd<Packet1Xd>(imag, pcplxflip<Packet1Xcd>(pconj<Packet1Xcd>(b)).v, pmsub<Packet1Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pcmp_eq(const Packet1Xcd& a, const Packet1Xcd& b) {
  Packet1Xl c = __riscv_vundefined_i64m1();
  Packet1Xul mask =
      __riscv_vreinterpret_v_b64_u64m1(__riscv_vmfeq_vv_f64m1_b64(a.v, b.v, unpacket_traits<Packet1Xd>::size));
  Packet1Xul mask_r =
      __riscv_vsrl_vx_u64m1(__riscv_vand_vx_u64m1(mask, 0xaaaaaaaaaaaaaaaa, unpacket_traits<Packet1Xl>::size), 1,
                            unpacket_traits<Packet1Xl>::size);
  mask = __riscv_vand_vv_u64m1(mask, mask_r, unpacket_traits<Packet1Xl>::size);
  mask = __riscv_vor_vv_u64m1(__riscv_vsll_vx_u64m1(mask, 1, unpacket_traits<Packet1Xl>::size), mask,
                              unpacket_traits<Packet1Xl>::size);
  Packet1Xd res = __riscv_vreinterpret_v_i64m1_f64m1(__riscv_vmerge_vvm_i64m1(pzero<Packet1Xl>(c), ptrue<Packet1Xl>(c),
                                                                              __riscv_vreinterpret_v_u64m1_b64(mask),
                                                                              unpacket_traits<Packet1Xl>::size));
  return Packet1Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pand<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(pand<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd por<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(por<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pxor<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(pxor<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pandnot<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return Packet1Xcd(pandnot<Packet1Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pload<Packet1Xcd>(const std::complex<double>* from) {
  Packet1Xd res = pload<Packet1Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet1Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd ploadu<Packet1Xcd>(const std::complex<double>* from) {
  Packet1Xd res = ploadu<Packet1Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet1Xcd(res);
}

EIGEN_STRONG_INLINE Packet2Xcd pdup(const Packet1Xcd& a) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x66), unpacket_traits<Packet1Xc>::size));
  Packet2Xul idx1 =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 1, unpacket_traits<Packet2Xd>::size);
  Packet2Xul idx2 = __riscv_vxor_vx_u64m2_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet2Xl>::size);
  return Packet2Xcd(
      __riscv_vrgather_vv_f64m2(__riscv_vlmul_ext_v_f64m1_f64m2(a.v), idx2, unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd ploaddup<Packet1Xcd>(const std::complex<double>* from) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0x66), unpacket_traits<Packet1Xc>::size));
  Packet1Xul idx1 =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xd>::size), 1, unpacket_traits<Packet1Xd>::size);
  Packet1Xul idx2 = __riscv_vxor_vx_u64m1_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet1Xl>::size);
  return Packet1Xcd(__riscv_vrgather_vv_f64m1(pload<Packet1Xd>(reinterpret_cast<const double*>(from)), idx2,
                                              unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd ploadquad<Packet1Xcd>(const std::complex<double>* from) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0x5a), unpacket_traits<Packet1Xc>::size));
  Packet1Xul idx1 =
      __riscv_vsrl_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xd>::size), 2, unpacket_traits<Packet1Xd>::size);
  Packet1Xul idx2 = __riscv_vxor_vx_u64m1_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet1Xl>::size);
  return Packet1Xcd(__riscv_vrgather_vv_f64m1(pload<Packet1Xd>(reinterpret_cast<const double*>(from)), idx2,
                                              unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<double>>(std::complex<double>* to, const Packet1Xcd& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<double>>(std::complex<double>* to, const Packet1Xcd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet1Xcd
pgather<std::complex<double>, Packet1Xcd>(const std::complex<double>* from, Index stride) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  const double* from2 = reinterpret_cast<const double*>(from);
  Packet1Xd res = __riscv_vundefined_f64m1();
  res = __riscv_vlse64_v_f64m1_tumu(mask, res, &from2[0 - (0 * stride)], stride * sizeof(double),
                                    unpacket_traits<Packet1Xd>::size);
  res =
      __riscv_vlse64_v_f64m1_tumu(__riscv_vmnot_m_b64(mask, unpacket_traits<Packet1Xl>::size), res,
                                  &from2[1 - (1 * stride)], stride * sizeof(double), unpacket_traits<Packet1Xd>::size);
  return Packet1Xcd(res);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<double>, Packet1Xcd>(std::complex<double>* to,
                                                                                      const Packet1Xcd& from,
                                                                                      Index stride) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  double* to2 = reinterpret_cast<double*>(to);
  __riscv_vsse64_v_f64m1_m(mask, &to2[0 - (0 * stride)], stride * sizeof(double), from.v,
                           unpacket_traits<Packet1Xd>::size);
  __riscv_vsse64_v_f64m1_m(__riscv_vmnot_m_b64(mask, unpacket_traits<Packet1Xl>::size), &to2[1 - (1 * stride)],
                           stride * sizeof(double), from.v, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> pfirst<Packet1Xcd>(const Packet1Xcd& a) {
  double real = pfirst<Packet1Xd>(a.v);
  double imag = pfirst<Packet1Xd>(__riscv_vfslide1down_vf_f64m1(a.v, 0.0, unpacket_traits<Packet1Xd>::size));
  return std::complex<double>(real, imag);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd preverse(const Packet1Xcd& a) {
  Packet1Xul idx = __riscv_vxor_vx_u64m1(__riscv_vid_v_u64m1(unpacket_traits<Packet1Xl>::size),
                                         unpacket_traits<Packet1Xl>::size - 2, unpacket_traits<Packet1Xl>::size);
  Packet1Xd res = __riscv_vrgather_vv_f64m1(a.v, idx, unpacket_traits<Packet1Xd>::size);
  return Packet1Xcd(res);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<Packet1Xcd>(const Packet1Xcd& a) {
  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet1Xl res = __riscv_vreinterpret_v_f64m1_i64m1(a.v);
  Packet1Xd real = __riscv_vreinterpret_v_i64m1_f64m1(
      __riscv_vand_vx_i64m1_tumu(mask, res, res, 0, unpacket_traits<Packet1Xl>::size));
  Packet1Xd imag = __riscv_vreinterpret_v_i64m1_f64m1(__riscv_vand_vx_i64m1_tumu(
      __riscv_vmnot_m_b64(mask, unpacket_traits<Packet1Xl>::size), res, res, 0, unpacket_traits<Packet1Xl>::size));
  return std::complex<double>(predux<Packet1Xd>(real), predux<Packet1Xd>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pdiv<Packet1Xcd>(const Packet1Xcd& a, const Packet1Xcd& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet1Xcd, N>& kernel) {
  double buffer[unpacket_traits<Packet1Xd>::size * N];
  int i = 0;

  const PacketMask64 mask =
      __riscv_vreinterpret_v_i8m1_b64(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));

  for (i = 0; i < N; i++) {
    __riscv_vsse64_v_f64m1_m(mask, &buffer[(i * 2) - (0 * N) + 0], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet1Xd>::size);
    __riscv_vsse64_v_f64m1_m(__riscv_vmnot_m_b64(mask, unpacket_traits<Packet1Xl>::size),
                             &buffer[(i * 2) - (1 * N) + 1], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet1Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet1Xcd(
        __riscv_vle64_v_f64m1(&buffer[i * unpacket_traits<Packet1Xd>::size], unpacket_traits<Packet1Xd>::size));
  }
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd psqrt<Packet1Xcd>(const Packet1Xcd& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd plog<Packet1Xcd>(const Packet1Xcd& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xcd pexp<Packet1Xcd>(const Packet1Xcd& a) {
  return pexp_complex(a);
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet1Xcd, Packet1Xd)

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_COMPLEX2_RVV10_H

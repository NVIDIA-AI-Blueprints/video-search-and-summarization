// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2024 Kseniya Zaytseva <kseniya.zaytseva@syntacore.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TYPE_CASTING_RVV10_H
#define EIGEN_TYPE_CASTING_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/********************************* 32 bits ************************************/

template <>
struct type_casting_traits<float, numext::int32_t> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
struct type_casting_traits<numext::int32_t, float> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
EIGEN_STRONG_INLINE Packet1Xf pcast<Packet1Xi, Packet1Xf>(const Packet1Xi& a) {
  return __riscv_vfcvt_f_x_v_f32m1(a, unpacket_traits<Packet1Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi pcast<Packet1Xf, Packet1Xi>(const Packet1Xf& a) {
  return __riscv_vfcvt_rtz_x_f_v_i32m1(a, unpacket_traits<Packet1Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xf preinterpret<Packet1Xf, Packet1Xi>(const Packet1Xi& a) {
  return __riscv_vreinterpret_v_i32m1_f32m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xi preinterpret<Packet1Xi, Packet1Xf>(const Packet1Xf& a) {
  return __riscv_vreinterpret_v_f32m1_i32m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcast<Packet4Xi, Packet4Xf>(const Packet4Xi& a) {
  return __riscv_vfcvt_f_x_v_f32m4(a, unpacket_traits<Packet4Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcast<Packet4Xf, Packet4Xi>(const Packet4Xf& a) {
  return __riscv_vfcvt_rtz_x_f_v_i32m4(a, unpacket_traits<Packet4Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf preinterpret<Packet4Xf, Packet4Xi>(const Packet4Xi& a) {
  return __riscv_vreinterpret_v_i32m4_f32m4(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi preinterpret<Packet4Xi, Packet4Xf>(const Packet4Xf& a) {
  return __riscv_vreinterpret_v_f32m4_i32m4(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcast<Packet2Xi, Packet2Xf>(const Packet2Xi& a) {
  return __riscv_vfcvt_f_x_v_f32m2(a, unpacket_traits<Packet2Xi>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcast<Packet2Xf, Packet2Xi>(const Packet2Xf& a) {
  return __riscv_vfcvt_rtz_x_f_v_i32m2(a, unpacket_traits<Packet2Xf>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf preinterpret<Packet2Xf, Packet2Xi>(const Packet2Xi& a) {
  return __riscv_vreinterpret_v_i32m2_f32m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi preinterpret<Packet2Xi, Packet2Xf>(const Packet2Xf& a) {
  return __riscv_vreinterpret_v_f32m2_i32m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcast<Packet1Xi, Packet4Xi>(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c,
                                                          const Packet1Xi& d) {
  return __riscv_vcreate_v_i32m1_i32m4(a, b, c, d);
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcast<Packet1Xi, Packet4Xf>(const Packet1Xi& a, const Packet1Xi& b, const Packet1Xi& c,
                                                          const Packet1Xi& d) {
  return __riscv_vcreate_v_f32m1_f32m4(__riscv_vfcvt_f_x_v_f32m1(a, unpacket_traits<Packet1Xi>::size),
                                       __riscv_vfcvt_f_x_v_f32m1(b, unpacket_traits<Packet1Xi>::size),
                                       __riscv_vfcvt_f_x_v_f32m1(c, unpacket_traits<Packet1Xi>::size),
                                       __riscv_vfcvt_f_x_v_f32m1(d, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xf pcast<Packet1Xf, Packet4Xf>(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c,
                                                          const Packet1Xf& d) {
  return __riscv_vcreate_v_f32m1_f32m4(a, b, c, d);
}

template <>
EIGEN_STRONG_INLINE Packet4Xi pcast<Packet1Xf, Packet4Xi>(const Packet1Xf& a, const Packet1Xf& b, const Packet1Xf& c,
                                                          const Packet1Xf& d) {
  return __riscv_vcreate_v_i32m1_i32m4(__riscv_vfcvt_rtz_x_f_v_i32m1(a, unpacket_traits<Packet1Xf>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i32m1(b, unpacket_traits<Packet1Xf>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i32m1(c, unpacket_traits<Packet1Xf>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i32m1(d, unpacket_traits<Packet1Xf>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcast<Packet1Xi, Packet2Xi>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vcreate_v_i32m1_i32m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcast<Packet1Xi, Packet2Xf>(const Packet1Xi& a, const Packet1Xi& b) {
  return __riscv_vcreate_v_f32m1_f32m2(__riscv_vfcvt_f_x_v_f32m1(a, unpacket_traits<Packet1Xi>::size),
                                       __riscv_vfcvt_f_x_v_f32m1(b, unpacket_traits<Packet1Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcast<Packet1Xf, Packet2Xf>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vcreate_v_f32m1_f32m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xi pcast<Packet1Xf, Packet2Xi>(const Packet1Xf& a, const Packet1Xf& b) {
  return __riscv_vcreate_v_i32m1_i32m2(__riscv_vfcvt_rtz_x_f_v_i32m1(a, unpacket_traits<Packet1Xf>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i32m1(b, unpacket_traits<Packet1Xf>::size));
}

/********************************* 64 bits ************************************/

template <>
struct type_casting_traits<double, numext::int64_t> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
struct type_casting_traits<numext::int64_t, double> {
  enum { VectorizedCast = 1, SrcCoeffRatio = 1, TgtCoeffRatio = 1 };
};

template <>
EIGEN_STRONG_INLINE Packet1Xd pcast<Packet1Xl, Packet1Xd>(const Packet1Xl& a) {
  return __riscv_vfcvt_f_x_v_f64m1(a, unpacket_traits<Packet1Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl pcast<Packet1Xd, Packet1Xl>(const Packet1Xd& a) {
  return __riscv_vfcvt_rtz_x_f_v_i64m1(a, unpacket_traits<Packet1Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet1Xd preinterpret<Packet1Xd, Packet1Xl>(const Packet1Xl& a) {
  return __riscv_vreinterpret_v_i64m1_f64m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet1Xl preinterpret<Packet1Xl, Packet1Xd>(const Packet1Xd& a) {
  return __riscv_vreinterpret_v_f64m1_i64m1(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcast<Packet4Xl, Packet4Xd>(const Packet4Xl& a) {
  return __riscv_vfcvt_f_x_v_f64m4(a, unpacket_traits<Packet4Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcast<Packet4Xd, Packet4Xl>(const Packet4Xd& a) {
  return __riscv_vfcvt_rtz_x_f_v_i64m4(a, unpacket_traits<Packet4Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd preinterpret<Packet4Xd, Packet4Xl>(const Packet4Xl& a) {
  return __riscv_vreinterpret_v_i64m4_f64m4(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl preinterpret<Packet4Xl, Packet4Xd>(const Packet4Xd& a) {
  return __riscv_vreinterpret_v_f64m4_i64m4(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcast<Packet2Xl, Packet2Xd>(const Packet2Xl& a) {
  return __riscv_vfcvt_f_x_v_f64m2(a, unpacket_traits<Packet2Xl>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcast<Packet2Xd, Packet2Xl>(const Packet2Xd& a) {
  return __riscv_vfcvt_rtz_x_f_v_i64m2(a, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd preinterpret<Packet2Xd, Packet2Xl>(const Packet2Xl& a) {
  return __riscv_vreinterpret_v_i64m2_f64m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl preinterpret<Packet2Xl, Packet2Xd>(const Packet2Xd& a) {
  return __riscv_vreinterpret_v_f64m2_i64m2(a);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcast<Packet1Xl, Packet4Xl>(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c,
                                                          const Packet1Xl& d) {
  return __riscv_vcreate_v_i64m1_i64m4(a, b, c, d);
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcast<Packet1Xl, Packet4Xd>(const Packet1Xl& a, const Packet1Xl& b, const Packet1Xl& c,
                                                          const Packet1Xl& d) {
  return __riscv_vcreate_v_f64m1_f64m4(__riscv_vfcvt_f_x_v_f64m1(a, unpacket_traits<Packet1Xl>::size),
                                       __riscv_vfcvt_f_x_v_f64m1(b, unpacket_traits<Packet1Xl>::size),
                                       __riscv_vfcvt_f_x_v_f64m1(c, unpacket_traits<Packet1Xl>::size),
                                       __riscv_vfcvt_f_x_v_f64m1(d, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet4Xd pcast<Packet1Xd, Packet4Xd>(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c,
                                                          const Packet1Xd& d) {
  return __riscv_vcreate_v_f64m1_f64m4(a, b, c, d);
}

template <>
EIGEN_STRONG_INLINE Packet4Xl pcast<Packet1Xd, Packet4Xl>(const Packet1Xd& a, const Packet1Xd& b, const Packet1Xd& c,
                                                          const Packet1Xd& d) {
  return __riscv_vcreate_v_i64m1_i64m4(__riscv_vfcvt_rtz_x_f_v_i64m1(a, unpacket_traits<Packet1Xd>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i64m1(b, unpacket_traits<Packet1Xd>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i64m1(c, unpacket_traits<Packet1Xd>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i64m1(d, unpacket_traits<Packet1Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcast<Packet1Xl, Packet2Xl>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vcreate_v_i64m1_i64m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcast<Packet1Xl, Packet2Xd>(const Packet1Xl& a, const Packet1Xl& b) {
  return __riscv_vcreate_v_f64m1_f64m2(__riscv_vfcvt_f_x_v_f64m1(a, unpacket_traits<Packet1Xl>::size),
                                       __riscv_vfcvt_f_x_v_f64m1(b, unpacket_traits<Packet1Xl>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcast<Packet1Xd, Packet2Xd>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vcreate_v_f64m1_f64m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet2Xl pcast<Packet1Xd, Packet2Xl>(const Packet1Xd& a, const Packet1Xd& b) {
  return __riscv_vcreate_v_i64m1_i64m2(__riscv_vfcvt_rtz_x_f_v_i64m1(a, unpacket_traits<Packet1Xd>::size),
                                       __riscv_vfcvt_rtz_x_f_v_i64m1(b, unpacket_traits<Packet1Xd>::size));
}

/********************************* 16 bits ************************************/

template <>
EIGEN_STRONG_INLINE Packet2Xs pcast<Packet1Xs, Packet2Xs>(const Packet1Xs& a, const Packet1Xs& b) {
  return __riscv_vcreate_v_i16m1_i16m2(a, b);
}

template <>
EIGEN_STRONG_INLINE Packet4Xs pcast<Packet1Xs, Packet4Xs>(const Packet1Xs& a, const Packet1Xs& b, const Packet1Xs& c,
                                                          const Packet1Xs& d) {
  return __riscv_vcreate_v_i16m1_i16m4(a, b, c, d);
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_TYPE_CASTING_RVV10_H

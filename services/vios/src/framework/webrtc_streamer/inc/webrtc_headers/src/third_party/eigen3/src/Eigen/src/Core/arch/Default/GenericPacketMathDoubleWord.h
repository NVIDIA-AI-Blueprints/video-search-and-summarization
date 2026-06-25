// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2018-2025 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ARCH_GENERIC_PACKET_MATH_DOUBLE_WORD_H
#define EIGEN_ARCH_GENERIC_PACKET_MATH_DOUBLE_WORD_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

// This function splits x into the nearest integer n and fractional part r,
// such that x = n + r holds exactly.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void absolute_split(const Packet& x, Packet& n, Packet& r) {
  n = pround(x);
  r = psub(x, n);
}

// This function computes the sum {s, r}, such that x + y = s_hi + s_lo
// holds exactly, and s_hi = fl(x+y), if |x| >= |y|.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void fast_twosum(const Packet& x, const Packet& y, Packet& s_hi, Packet& s_lo) {
  s_hi = padd(x, y);
  const Packet t = psub(s_hi, x);
  s_lo = psub(y, t);
}

#ifdef EIGEN_VECTORIZE_FMA
// This function implements the extended precision product of
// a pair of floating point numbers. Given {x, y}, it computes the pair
// {p_hi, p_lo} such that x * y = p_hi + p_lo holds exactly and
// p_hi = fl(x * y).
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void twoprod(const Packet& x, const Packet& y, Packet& p_hi, Packet& p_lo) {
  p_hi = pmul(x, y);
  p_lo = pmsub(x, y, p_hi);
}

// A version of twoprod that takes x, y, and fl(x*y) as input and returns the p_lo such that
// x * y = xy + p_lo holds exactly.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet twoprod_low(const Packet& x, const Packet& y, const Packet& xy) {
  return pmsub(x, y, xy);
}

#else

// This function implements the Veltkamp splitting. Given a floating point
// number x it returns the pair {x_hi, x_lo} such that x_hi + x_lo = x holds
// exactly and that half of the significant of x fits in x_hi.
// This is Algorithm 3 from Jean-Michel Muller, "Elementary Functions",
// 3rd edition, Birkh\"auser, 2016.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void veltkamp_splitting(const Packet& x, Packet& x_hi, Packet& x_lo) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  constexpr int shift = (NumTraits<Scalar>::digits() + 1) / 2;
  const Scalar shift_scale = Scalar(uint64_t(1) << shift);  // Scalar constructor not necessarily constexpr.
  const Packet gamma = pmul(pset1<Packet>(shift_scale + Scalar(1)), x);
  Packet rho = psub(x, gamma);
  x_hi = padd(rho, gamma);
  x_lo = psub(x, x_hi);
}

// This function implements Dekker's algorithm for products x * y.
// Given floating point numbers {x, y} computes the pair
// {p_hi, p_lo} such that x * y = p_hi + p_lo holds exactly and
// p_hi = fl(x * y).
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void twoprod(const Packet& x, const Packet& y, Packet& p_hi, Packet& p_lo) {
  Packet x_hi, x_lo, y_hi, y_lo;
  veltkamp_splitting(x, x_hi, x_lo);
  veltkamp_splitting(y, y_hi, y_lo);

  p_hi = pmul(x, y);
  p_lo = pmadd(x_hi, y_hi, pnegate(p_hi));
  p_lo = pmadd(x_hi, y_lo, p_lo);
  p_lo = pmadd(x_lo, y_hi, p_lo);
  p_lo = pmadd(x_lo, y_lo, p_lo);
}

// A version of twoprod that takes x, y, and fl(x*y) as input and returns the p_lo such that
// x * y = xy + p_lo holds exactly.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet twoprod_low(const Packet& x, const Packet& y, const Packet& xy) {
  Packet x_hi, x_lo, y_hi, y_lo;
  veltkamp_splitting(x, x_hi, x_lo);
  veltkamp_splitting(y, y_hi, y_lo);

  Packet p_lo = pmadd(x_hi, y_hi, pnegate(xy));
  p_lo = pmadd(x_hi, y_lo, p_lo);
  p_lo = pmadd(x_lo, y_hi, p_lo);
  p_lo = pmadd(x_lo, y_lo, p_lo);
  return p_lo;
}

#endif  // EIGEN_VECTORIZE_FMA

// This function implements Dekker's algorithm for the addition
// of two double word numbers represented by {x_hi, x_lo} and {y_hi, y_lo}.
// It returns the result as a pair {s_hi, s_lo} such that
// x_hi + x_lo + y_hi + y_lo = s_hi + s_lo holds exactly.
// This is Algorithm 5 from Jean-Michel Muller, "Elementary Functions",
// 3rd edition, Birkh\"auser, 2016.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void twosum(const Packet& x_hi, const Packet& x_lo, const Packet& y_hi,
                                                  const Packet& y_lo, Packet& s_hi, Packet& s_lo) {
  const Packet x_greater_mask = pcmp_lt(pabs(y_hi), pabs(x_hi));
  Packet r_hi_1, r_lo_1;
  fast_twosum(x_hi, y_hi, r_hi_1, r_lo_1);
  Packet r_hi_2, r_lo_2;
  fast_twosum(y_hi, x_hi, r_hi_2, r_lo_2);
  const Packet r_hi = pselect(x_greater_mask, r_hi_1, r_hi_2);

  const Packet s1 = padd(padd(y_lo, r_lo_1), x_lo);
  const Packet s2 = padd(padd(x_lo, r_lo_2), y_lo);
  const Packet s = pselect(x_greater_mask, s1, s2);

  fast_twosum(r_hi, s, s_hi, s_lo);
}

// This is a version of twosum for double word numbers,
// which assumes that |x_hi| >= |y_hi|.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void fast_twosum(const Packet& x_hi, const Packet& x_lo, const Packet& y_hi,
                                                       const Packet& y_lo, Packet& s_hi, Packet& s_lo) {
  Packet r_hi, r_lo;
  fast_twosum(x_hi, y_hi, r_hi, r_lo);
  const Packet s = padd(padd(y_lo, r_lo), x_lo);
  fast_twosum(r_hi, s, s_hi, s_lo);
}

// This is a version of twosum for adding a floating point number x to
// double word number {y_hi, y_lo} number, with the assumption
// that |x| >= |y_hi|.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void fast_twosum(const Packet& x, const Packet& y_hi, const Packet& y_lo,
                                                       Packet& s_hi, Packet& s_lo) {
  Packet r_hi, r_lo;
  fast_twosum(x, y_hi, r_hi, r_lo);
  const Packet s = padd(y_lo, r_lo);
  fast_twosum(r_hi, s, s_hi, s_lo);
}

// This function implements the multiplication of a double word
// number represented by {x_hi, x_lo} by a floating point number y.
// It returns the result as a pair {p_hi, p_lo} such that
// (x_hi + x_lo) * y = p_hi + p_lo hold with a relative error
// of less than 2*2^{-2p}, where p is the number of significand bit
// in the floating point type.
// This is Algorithm 7 from Jean-Michel Muller, "Elementary Functions",
// 3rd edition, Birkh\"auser, 2016.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void twoprod(const Packet& x_hi, const Packet& x_lo, const Packet& y,
                                                   Packet& p_hi, Packet& p_lo) {
  Packet c_hi, c_lo1;
  twoprod(x_hi, y, c_hi, c_lo1);
  const Packet c_lo2 = pmul(x_lo, y);
  Packet t_hi, t_lo1;
  fast_twosum(c_hi, c_lo2, t_hi, t_lo1);
  const Packet t_lo2 = padd(t_lo1, c_lo1);
  fast_twosum(t_hi, t_lo2, p_hi, p_lo);
}

// This function implements the multiplication of two double word
// numbers represented by {x_hi, x_lo} and {y_hi, y_lo}.
// It returns the result as a pair {p_hi, p_lo} such that
// (x_hi + x_lo) * (y_hi + y_lo) = p_hi + p_lo holds with a relative error
// of less than 2*2^{-2p}, where p is the number of significand bit
// in the floating point type.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void twoprod(const Packet& x_hi, const Packet& x_lo, const Packet& y_hi,
                                                   const Packet& y_lo, Packet& p_hi, Packet& p_lo) {
  Packet p_hi_hi, p_hi_lo;
  twoprod(x_hi, x_lo, y_hi, p_hi_hi, p_hi_lo);
  Packet p_lo_hi, p_lo_lo;
  twoprod(x_hi, x_lo, y_lo, p_lo_hi, p_lo_lo);
  fast_twosum(p_hi_hi, p_hi_lo, p_lo_hi, p_lo_lo, p_hi, p_lo);
}

// This function implements the division of double word {x_hi, x_lo}
// by float y. This is Algorithm 15 from "Tight and rigorous error bounds
// for basic building blocks of double-word arithmetic", Joldes, Muller, & Popescu,
// 2017. https://hal.archives-ouvertes.fr/hal-01351529
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void doubleword_div_fp(const Packet& x_hi, const Packet& x_lo, const Packet& y,
                                                             Packet& z_hi, Packet& z_lo) {
  const Packet t_hi = pdiv(x_hi, y);
  Packet pi_hi, pi_lo;
  twoprod(t_hi, y, pi_hi, pi_lo);
  const Packet delta_hi = psub(x_hi, pi_hi);
  const Packet delta_t = psub(delta_hi, pi_lo);
  const Packet delta = padd(delta_t, x_lo);
  const Packet t_lo = pdiv(delta, y);
  fast_twosum(t_hi, t_lo, z_hi, z_lo);
}

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_ARCH_GENERIC_PACKET_MATH_DOUBLE_WORD_H

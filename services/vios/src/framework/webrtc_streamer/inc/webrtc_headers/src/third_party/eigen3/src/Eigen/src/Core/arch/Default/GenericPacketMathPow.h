// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2018-2025 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ARCH_GENERIC_PACKET_MATH_POW_H
#define EIGEN_ARCH_GENERIC_PACKET_MATH_POW_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

//----------------------------------------------------------------------
// Cubic Root Functions
//----------------------------------------------------------------------

// This function implements a single step of Halley's iteration for
// computing x = y^(1/3):
//   x_{k+1} = x_k - (x_k^3 - y) x_k / (2x_k^3 + y)
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet cbrt_halley_iteration_step(const Packet& x_k,
                                                                                      const Packet& y) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  Packet x_k_cb = pmul(x_k, pmul(x_k, x_k));
  Packet denom = pmadd(pset1<Packet>(Scalar(2)), x_k_cb, y);
  Packet num = psub(x_k_cb, y);
  Packet r = pdiv(num, denom);
  return pnmadd(x_k, r, x_k);
}

// Decompose the input such that x^(1/3) = y^(1/3) * 2^e_div3, and y is in the
// interval [0.125,1].
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet cbrt_decompose(const Packet& x, Packet& e_div3) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  // Extract the significant s in the range [0.5,1) and exponent e, such that
  // x = 2^e * s.
  Packet e, s;
  s = pfrexp(x, e);

  // Split the exponent into a part divisible by 3 and the remainder.
  // e = 3*e_div3 + e_mod3.
  constexpr Scalar kOneThird = Scalar(1) / 3;
  e_div3 = pceil(pmul(e, pset1<Packet>(kOneThird)));
  Packet e_mod3 = pnmadd(pset1<Packet>(Scalar(3)), e_div3, e);

  // Replace s by y = (s * 2^e_mod3).
  return pldexp_fast(s, e_mod3);
}

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet cbrt_special_cases_and_sign(const Packet& x,
                                                                                       const Packet& abs_root) {
  typedef typename unpacket_traits<Packet>::type Scalar;

  // Set sign.
  const Packet sign_mask = pset1<Packet>(Scalar(-0.0));
  const Packet x_sign = pand(sign_mask, x);
  Packet root = por(x_sign, abs_root);

  // Pass non-finite and zero values of x straight through.
  const Packet is_not_finite = por(pisinf(x), pisnan(x));
  const Packet is_zero = pcmp_eq(pzero(x), x);
  const Packet use_x = por(is_not_finite, is_zero);
  return pselect(use_x, x, root);
}

// Generic implementation of cbrt(x) for float.
//
// The algorithm computes the cubic root of the input by first
// decomposing it into a exponent and significant
//   x = s * 2^e.
//
// We can then write the cube root as
//
//   x^(1/3) = 2^(e/3) * s^(1/3)
//           = 2^((3*e_div3 + e_mod3)/3) * s^(1/3)
//           = 2^(e_div3) * 2^(e_mod3/3) * s^(1/3)
//           = 2^(e_div3) * (s * 2^e_mod3)^(1/3)
//
// where e_div3 = ceil(e/3) and e_mod3 = e - 3*e_div3.
//
// The cube root of the second term y = (s * 2^e_mod3)^(1/3) is coarsely
// approximated using a cubic polynomial and subsequently refined using a
// single step of Halley's iteration, and finally the two terms are combined
// using pldexp_fast.
//
// Note: Many alternatives exist for implementing cbrt. See, for example,
// the excellent discussion in Kahan's note:
//   https://csclub.uwaterloo.ca/~pbarfuss/qbrt.pdf
// This particular implementation was found to be very fast and accurate
// among several alternatives tried, but is probably not "optimal" on all
// platforms.
//
// This is accurate to 2 ULP.
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet pcbrt_float(const Packet& x) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static_assert(std::is_same<Scalar, float>::value, "Scalar type must be float");

  // Decompose the input such that x^(1/3) = y^(1/3) * 2^e_div3, and y is in the
  // interval [0.125,1].
  Packet e_div3;
  const Packet y = cbrt_decompose(pabs(x), e_div3);

  // Compute initial approximation accurate to 5.22e-3.
  // The polynomial was computed using Rminimax.
  constexpr float alpha[] = {5.9220016002655029296875e-01f, -1.3859539031982421875e+00f, 1.4581282138824462890625e+00f,
                             3.408401906490325927734375e-01f};
  Packet r = ppolevl<Packet, 3>::run(y, alpha);

  // Take one step of Halley's iteration.
  r = cbrt_halley_iteration_step(r, y);

  // Finally multiply by 2^(e_div3)
  r = pldexp_fast(r, e_div3);

  return cbrt_special_cases_and_sign(x, r);
}

// Generic implementation of cbrt(x) for double.
//
// The algorithm is identical to the one for float except that a different initial
// approximation is used for y^(1/3) and two Halley iteration steps are performed.
//
// This is accurate to 1 ULP.
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet pcbrt_double(const Packet& x) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static_assert(std::is_same<Scalar, double>::value, "Scalar type must be double");

  // Decompose the input such that x^(1/3) = y^(1/3) * 2^e_div3, and y is in the
  // interval [0.125,1].
  Packet e_div3;
  const Packet y = cbrt_decompose(pabs(x), e_div3);

  // Compute initial approximation accurate to 0.016.
  // The polynomial was computed using Rminimax.
  constexpr double alpha[] = {-4.69470621553356115551736138513660989701747894287109375e-01,
                              1.072314636518546304699839311069808900356292724609375e+00,
                              3.81249427609571867048288140722434036433696746826171875e-01};
  Packet r = ppolevl<Packet, 2>::run(y, alpha);

  // Take two steps of Halley's iteration.
  r = cbrt_halley_iteration_step(r, y);
  r = cbrt_halley_iteration_step(r, y);

  // Finally multiply by 2^(e_div3).
  r = pldexp_fast(r, e_div3);
  return cbrt_special_cases_and_sign(x, r);
}

//----------------------------------------------------------------------
// Power Functions (accurate_log2, generic_pow, unary_pow)
//----------------------------------------------------------------------

// This function computes log2(x) and returns the result as a double word.
template <typename Scalar>
struct accurate_log2 {
  template <typename Packet>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void operator()(const Packet& x, Packet& log2_x_hi, Packet& log2_x_lo) const {
    log2_x_hi = plog2(x);
    log2_x_lo = pzero(x);
  }
};

// This specialization uses a more accurate algorithm to compute log2(x) for
// floats in [1/sqrt(2);sqrt(2)] with a relative accuracy of ~6.56508e-10.
// This additional accuracy is needed to counter the error-magnification
// inherent in multiplying by a potentially large exponent in pow(x,y).
// The minimax polynomial used was calculated using the Rminimax tool,
// see https://gitlab.inria.fr/sfilip/rminimax.
// Command line:
//   $ ratapprox --function="log2(1+x)/x"  --dom='[-0.2929,0.41422]'
//   --type=[10,0]
//       --numF="[D,D,SG]" --denF="[SG]" --log --dispCoeff="dec"
//
// The resulting implementation of pow(x,y) is accurate to 3 ulps.
template <>
struct accurate_log2<float> {
  template <typename Packet>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void operator()(const Packet& z, Packet& log2_x_hi, Packet& log2_x_lo) const {
    // Split the two lowest order constant coefficient into double-word representation.
    constexpr double kC0 = 1.442695041742110273474963832995854318141937255859375e+00;
    constexpr float kC0_hi = static_cast<float>(kC0);
    constexpr float kC0_lo = static_cast<float>(kC0 - static_cast<double>(kC0_hi));
    const Packet c0_hi = pset1<Packet>(kC0_hi);
    const Packet c0_lo = pset1<Packet>(kC0_lo);

    constexpr double kC1 = -7.2134751588268664068692714863573201000690460205078125e-01;
    constexpr float kC1_hi = static_cast<float>(kC1);
    constexpr float kC1_lo = static_cast<float>(kC1 - static_cast<double>(kC1_hi));
    const Packet c1_hi = pset1<Packet>(kC1_hi);
    const Packet c1_lo = pset1<Packet>(kC1_lo);

    constexpr float c[] = {
        9.7010828554630279541015625e-02,  -1.6896486282348632812500000e-01, 1.7200836539268493652343750e-01,
        -1.7892272770404815673828125e-01, 2.0505344867706298828125000e-01,  -2.4046677350997924804687500e-01,
        2.8857553005218505859375000e-01,  -3.6067414283752441406250000e-01, 4.8089790344238281250000000e-01};

    // Evaluate the higher order terms in the polynomial using
    // standard arithmetic.
    const Packet one = pset1<Packet>(1.0f);
    const Packet x = psub(z, one);
    Packet p = ppolevl<Packet, 8>::run(x, c);
    // Evaluate the final two step in Horner's rule using double-word
    // arithmetic.
    Packet p_hi, p_lo;
    twoprod(x, p, p_hi, p_lo);
    fast_twosum(c1_hi, c1_lo, p_hi, p_lo, p_hi, p_lo);
    twoprod(p_hi, p_lo, x, p_hi, p_lo);
    fast_twosum(c0_hi, c0_lo, p_hi, p_lo, p_hi, p_lo);
    // Multiply by x to recover log2(z).
    twoprod(p_hi, p_lo, x, log2_x_hi, log2_x_lo);
  }
};

// This specialization uses a more accurate algorithm to compute log2(x) for
// floats in [1/sqrt(2);sqrt(2)] with a relative accuracy of ~1.27e-18.
// This additional accuracy is needed to counter the error-magnification
// inherent in multiplying by a potentially large exponent in pow(x,y).
// The minimax polynomial used was calculated using the Sollya tool.
// See sollya.org.

template <>
struct accurate_log2<double> {
  template <typename Packet>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void operator()(const Packet& x, Packet& log2_x_hi, Packet& log2_x_lo) const {
    // We use a transformation of variables:
    //    r = c * (x-1) / (x+1),
    // such that
    //    log2(x) = log2((1 + r/c) / (1 - r/c)) = f(r).
    // The function f(r) can be approximated well using an odd polynomial
    // of the form
    //   P(r) = ((Q(r^2) * r^2 + C) * r^2 + 1) * r,
    // For the implementation of log2<double> here, Q is of degree 6 with
    // coefficient represented in working precision (double), while C is a
    // constant represented in extra precision as a double word to achieve
    // full accuracy.
    //
    // The polynomial coefficients were computed by the Sollya script:
    //
    // c = 2 / log(2);
    // trans = c * (x-1)/(x+1);
    // itrans = (1+x/c)/(1-x/c);
    // interval=[trans(sqrt(0.5)); trans(sqrt(2))];
    // print(interval);
    // f = log2(itrans(x));
    // p=fpminimax(f,[|1,3,5,7,9,11,13,15,17|],[|1,DD,double...|],interval,relative,floating);
    const Packet q12 = pset1<Packet>(2.87074255468000586e-9);
    const Packet q10 = pset1<Packet>(2.38957980901884082e-8);
    const Packet q8 = pset1<Packet>(2.31032094540014656e-7);
    const Packet q6 = pset1<Packet>(2.27279857398537278e-6);
    const Packet q4 = pset1<Packet>(2.31271023278625638e-5);
    const Packet q2 = pset1<Packet>(2.47556738444535513e-4);
    const Packet q0 = pset1<Packet>(2.88543873228900172e-3);
    const Packet C_hi = pset1<Packet>(0.0400377511598501157);
    const Packet C_lo = pset1<Packet>(-4.77726582251425391e-19);
    const Packet one = pset1<Packet>(1.0);

    const Packet cst_2_log2e_hi = pset1<Packet>(2.88539008177792677);
    const Packet cst_2_log2e_lo = pset1<Packet>(4.07660016854549667e-17);
    // c * (x - 1)
    Packet t_hi, t_lo;
    // t = c * (x-1)
    twoprod(cst_2_log2e_hi, cst_2_log2e_lo, psub(x, one), t_hi, t_lo);
    // r = c * (x-1) / (x+1),
    Packet r_hi, r_lo;
    doubleword_div_fp(t_hi, t_lo, padd(x, one), r_hi, r_lo);

    // r2 = r * r
    Packet r2_hi, r2_lo;
    twoprod(r_hi, r_lo, r_hi, r_lo, r2_hi, r2_lo);
    // r4 = r2 * r2
    Packet r4_hi, r4_lo;
    twoprod(r2_hi, r2_lo, r2_hi, r2_lo, r4_hi, r4_lo);

    // Evaluate Q(r^2) in working precision. We evaluate it in two parts
    // (even and odd in r^2) to improve instruction level parallelism.
    Packet q_even = pmadd(q12, r4_hi, q8);
    Packet q_odd = pmadd(q10, r4_hi, q6);
    q_even = pmadd(q_even, r4_hi, q4);
    q_odd = pmadd(q_odd, r4_hi, q2);
    q_even = pmadd(q_even, r4_hi, q0);
    Packet q = pmadd(q_odd, r2_hi, q_even);

    // Now evaluate the low order terms of P(x) in double word precision.
    // In the following, due to the increasing magnitude of the coefficients
    // and r being constrained to [-0.5, 0.5] we can use fast_twosum instead
    // of the slower twosum.
    // Q(r^2) * r^2
    Packet p_hi, p_lo;
    twoprod(r2_hi, r2_lo, q, p_hi, p_lo);
    // Q(r^2) * r^2 + C
    Packet p1_hi, p1_lo;
    fast_twosum(C_hi, C_lo, p_hi, p_lo, p1_hi, p1_lo);
    // (Q(r^2) * r^2 + C) * r^2
    Packet p2_hi, p2_lo;
    twoprod(r2_hi, r2_lo, p1_hi, p1_lo, p2_hi, p2_lo);
    // ((Q(r^2) * r^2 + C) * r^2 + 1)
    Packet p3_hi, p3_lo;
    fast_twosum(one, p2_hi, p2_lo, p3_hi, p3_lo);

    // log(z) ~= ((Q(r^2) * r^2 + C) * r^2 + 1) * r
    twoprod(p3_hi, p3_lo, r_hi, r_lo, log2_x_hi, log2_x_lo);
  }
};

// This function implements the non-trivial case of pow(x,y) where x is
// positive and y is (possibly) non-integer.
// Formally, pow(x,y) = exp2(y * log2(x)), where exp2(x) is shorthand for 2^x.
// TODO(rmlarsen): We should probably add this as a packet op 'ppow', to make it
// easier to specialize or turn off for specific types and/or backends.
template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet generic_pow_impl(const Packet& x, const Packet& y) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  // Split x into exponent e_x and mantissa m_x.
  Packet e_x;
  Packet m_x = pfrexp(x, e_x);

  // Adjust m_x to lie in [1/sqrt(2):sqrt(2)] to minimize absolute error in log2(m_x).
  constexpr Scalar sqrt_half = Scalar(0.70710678118654752440);
  const Packet m_x_scale_mask = pcmp_lt(m_x, pset1<Packet>(sqrt_half));
  m_x = pselect(m_x_scale_mask, pmul(pset1<Packet>(Scalar(2)), m_x), m_x);
  e_x = pselect(m_x_scale_mask, psub(e_x, pset1<Packet>(Scalar(1))), e_x);

  // Compute log2(m_x) with 6 extra bits of accuracy.
  Packet rx_hi, rx_lo;
  accurate_log2<Scalar>()(m_x, rx_hi, rx_lo);

  // Compute the two terms {y * e_x, y * r_x} in f = y * log2(x) with doubled
  // precision using double word arithmetic.
  Packet f1_hi, f1_lo, f2_hi, f2_lo;
  twoprod(e_x, y, f1_hi, f1_lo);
  twoprod(rx_hi, rx_lo, y, f2_hi, f2_lo);
  // Sum the two terms in f using double word arithmetic. We know
  // that |e_x| > |log2(m_x)|, except for the case where e_x==0.
  // This means that we can use fast_twosum(f1,f2).
  // In the case e_x == 0, e_x * y = f1 = 0, so we don't lose any
  // accuracy by violating the assumption of fast_twosum, because
  // it's a no-op.
  Packet f_hi, f_lo;
  fast_twosum(f1_hi, f1_lo, f2_hi, f2_lo, f_hi, f_lo);

  // Split f into integer and fractional parts.
  Packet n_z, r_z;
  absolute_split(f_hi, n_z, r_z);
  r_z = padd(r_z, f_lo);
  Packet n_r;
  absolute_split(r_z, n_r, r_z);
  n_z = padd(n_z, n_r);

  // We now have an accurate split of f = n_z + r_z and can compute
  //   x^y = 2**{n_z + r_z) = exp2(r_z) * 2**{n_z}.
  // Multiplication by the second factor can be done exactly using pldexp(), since
  // it is an integer power of 2.
  const Packet e_r = generic_exp2(r_z);

  // Since we know that e_r is in [1/sqrt(2); sqrt(2)], we can use the fast version
  // of pldexp to multiply by 2**{n_z} when |n_z| is sufficiently small.
  constexpr Scalar kPldExpThresh = std::numeric_limits<Scalar>::max_exponent - 2;
  const Packet pldexp_fast_unsafe = pcmp_lt(pset1<Packet>(kPldExpThresh), pabs(n_z));
  if (predux_any(pldexp_fast_unsafe)) {
    return pldexp(e_r, n_z);
  }
  return pldexp_fast(e_r, n_z);
}

// Generic implementation of pow(x,y).
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS std::enable_if_t<!is_scalar<Packet>::value, Packet> generic_pow(
    const Packet& x, const Packet& y) {
  typedef typename unpacket_traits<Packet>::type Scalar;

  const Packet cst_inf = pset1<Packet>(NumTraits<Scalar>::infinity());
  const Packet cst_zero = pset1<Packet>(Scalar(0));
  const Packet cst_one = pset1<Packet>(Scalar(1));
  const Packet cst_nan = pset1<Packet>(NumTraits<Scalar>::quiet_NaN());

  const Packet x_abs = pabs(x);
  Packet result = generic_pow_impl(x_abs, y);

  // In the following we enforce the special case handling prescribed in
  // https://en.cppreference.com/w/cpp/numeric/math/pow.

  // Predicates for sign and magnitude of x.
  const Packet x_is_negative = pcmp_lt(x, cst_zero);
  const Packet x_is_zero = pcmp_eq(x, cst_zero);
  const Packet x_is_one = pcmp_eq(x, cst_one);
  const Packet x_has_signbit = psignbit(x);
  const Packet x_abs_gt_one = pcmp_lt(cst_one, x_abs);
  const Packet x_abs_is_inf = pcmp_eq(x_abs, cst_inf);

  // Predicates for sign and magnitude of y.
  const Packet y_abs = pabs(y);
  const Packet y_abs_is_inf = pcmp_eq(y_abs, cst_inf);
  const Packet y_is_negative = pcmp_lt(y, cst_zero);
  const Packet y_is_zero = pcmp_eq(y, cst_zero);
  const Packet y_is_one = pcmp_eq(y, cst_one);
  // Predicates for whether y is integer and odd/even.
  const Packet y_is_int = pandnot(pcmp_eq(pfloor(y), y), y_abs_is_inf);
  const Packet y_div_2 = pmul(y, pset1<Packet>(Scalar(0.5)));
  const Packet y_is_even = pcmp_eq(pround(y_div_2), y_div_2);
  const Packet y_is_odd_int = pandnot(y_is_int, y_is_even);
  // Smallest exponent for which (1 + epsilon) overflows to infinity.
  constexpr Scalar huge_exponent =
      (NumTraits<Scalar>::max_exponent() * Scalar(EIGEN_LN2)) / NumTraits<Scalar>::epsilon();
  const Packet y_abs_is_huge = pcmp_le(pset1<Packet>(huge_exponent), y_abs);

  // *  pow(base, exp) returns NaN if base is finite and negative
  //    and exp is finite and non-integer.
  result = pselect(pandnot(x_is_negative, y_is_int), cst_nan, result);

  // * pow(±0, exp), where exp is negative, finite, and is an even integer or
  // a non-integer, returns +∞
  // * pow(±0, exp), where exp is positive non-integer or a positive even
  // integer, returns +0
  // * pow(+0, exp), where exp is a negative odd integer, returns +∞
  // * pow(-0, exp), where exp is a negative odd integer, returns -∞
  // * pow(+0, exp), where exp is a positive odd integer, returns +0
  // * pow(-0, exp), where exp is a positive odd integer, returns -0
  // Sign is flipped by the rule below.
  result = pselect(x_is_zero, pselect(y_is_negative, cst_inf, cst_zero), result);

  // pow(base, exp) returns -pow(abs(base), exp) if base has the sign bit set,
  // and exp is an odd integer exponent.
  result = pselect(pand(x_has_signbit, y_is_odd_int), pnegate(result), result);

  // * pow(base, -∞) returns +∞ for any |base|<1
  // * pow(base, -∞) returns +0 for any |base|>1
  // * pow(base, +∞) returns +0 for any |base|<1
  // * pow(base, +∞) returns +∞ for any |base|>1
  // * pow(±0, -∞) returns +∞
  // * pow(-1, +-∞) = 1
  Packet inf_y_val = pselect(pxor(y_is_negative, x_abs_gt_one), cst_inf, cst_zero);
  inf_y_val = pselect(pcmp_eq(x, pset1<Packet>(Scalar(-1.0))), cst_one, inf_y_val);
  result = pselect(y_abs_is_huge, inf_y_val, result);

  // * pow(+∞, exp) returns +0 for any negative exp
  // * pow(+∞, exp) returns +∞ for any positive exp
  // * pow(-∞, exp) returns -0 if exp is a negative odd integer.
  // * pow(-∞, exp) returns +0 if exp is a negative non-integer or negative
  //     even integer.
  // * pow(-∞, exp) returns -∞ if exp is a positive odd integer.
  // * pow(-∞, exp) returns +∞ if exp is a positive non-integer or positive
  //     even integer.
  auto x_pos_inf_value = pselect(y_is_negative, cst_zero, cst_inf);
  auto x_neg_inf_value = pselect(y_is_odd_int, pnegate(x_pos_inf_value), x_pos_inf_value);
  result = pselect(x_abs_is_inf, pselect(x_is_negative, x_neg_inf_value, x_pos_inf_value), result);

  // All cases of NaN inputs return NaN, except the two below.
  result = pselect(por(pisnan(x), pisnan(y)), cst_nan, result);

  // * pow(base, 1) returns base.
  // * pow(base, +/-0) returns 1, regardless of base, even NaN.
  // * pow(+1, exp) returns 1, regardless of exponent, even NaN.
  result = pselect(y_is_one, x, pselect(por(x_is_one, y_is_zero), cst_one, result));

  return result;
}

template <typename Scalar>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS std::enable_if_t<is_scalar<Scalar>::value, Scalar> generic_pow(
    const Scalar& x, const Scalar& y) {
  return numext::pow(x, y);
}

namespace unary_pow {

template <typename ScalarExponent, bool IsInteger = NumTraits<ScalarExponent>::IsInteger>
struct exponent_helper {
  using safe_abs_type = ScalarExponent;
  static constexpr ScalarExponent one_half = ScalarExponent(0.5);
  // these routines assume that exp is an integer stored as a floating point type
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE ScalarExponent safe_abs(const ScalarExponent& exp) {
    return numext::abs(exp);
  }
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE bool is_odd(const ScalarExponent& exp) {
    eigen_assert(((numext::isfinite)(exp) && exp == numext::floor(exp)) && "exp must be an integer");
    ScalarExponent exp_div_2 = exp * one_half;
    ScalarExponent floor_exp_div_2 = numext::floor(exp_div_2);
    return exp_div_2 != floor_exp_div_2;
  }
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE ScalarExponent floor_div_two(const ScalarExponent& exp) {
    ScalarExponent exp_div_2 = exp * one_half;
    return numext::floor(exp_div_2);
  }
};

template <typename ScalarExponent>
struct exponent_helper<ScalarExponent, true> {
  // if `exp` is a signed integer type, cast it to its unsigned counterpart to safely store its absolute value
  // consider the (rare) case where `exp` is an int32_t: abs(-2147483648) != 2147483648
  using safe_abs_type = typename numext::get_integer_by_size<sizeof(ScalarExponent)>::unsigned_type;
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE safe_abs_type safe_abs(const ScalarExponent& exp) {
    ScalarExponent mask = numext::signbit(exp);
    safe_abs_type result = safe_abs_type(exp ^ mask);
    return result + safe_abs_type(ScalarExponent(1) & mask);
  }
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE bool is_odd(const safe_abs_type& exp) {
    return exp % safe_abs_type(2) != safe_abs_type(0);
  }
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE safe_abs_type floor_div_two(const safe_abs_type& exp) {
    return exp >> safe_abs_type(1);
  }
};

template <typename Packet, typename ScalarExponent,
          bool ReciprocateIfExponentIsNegative =
              !NumTraits<typename unpacket_traits<Packet>::type>::IsInteger && NumTraits<ScalarExponent>::IsSigned>
struct reciprocate {
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent& exponent) {
    using Scalar = typename unpacket_traits<Packet>::type;
    const Packet cst_pos_one = pset1<Packet>(Scalar(1));
    return exponent < 0 ? pdiv(cst_pos_one, x) : x;
  }
};

template <typename Packet, typename ScalarExponent>
struct reciprocate<Packet, ScalarExponent, false> {
  // pdiv not defined, nor necessary for integer base types
  // if the exponent is unsigned, then the exponent cannot be negative
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent&) { return x; }
};

template <typename Packet, typename ScalarExponent>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet int_pow(const Packet& x, const ScalarExponent& exponent) {
  using Scalar = typename unpacket_traits<Packet>::type;
  using ExponentHelper = exponent_helper<ScalarExponent>;
  using AbsExponentType = typename ExponentHelper::safe_abs_type;
  const Packet cst_pos_one = pset1<Packet>(Scalar(1));
  if (exponent == ScalarExponent(0)) return cst_pos_one;

  Packet result = reciprocate<Packet, ScalarExponent>::run(x, exponent);
  Packet y = cst_pos_one;
  AbsExponentType m = ExponentHelper::safe_abs(exponent);

  while (m > 1) {
    bool odd = ExponentHelper::is_odd(m);
    if (odd) y = pmul(y, result);
    result = pmul(result, result);
    m = ExponentHelper::floor_div_two(m);
  }

  return pmul(y, result);
}

template <typename Packet>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE std::enable_if_t<!is_scalar<Packet>::value, Packet> gen_pow(
    const Packet& x, const typename unpacket_traits<Packet>::type& exponent) {
  const Packet exponent_packet = pset1<Packet>(exponent);
  // generic_pow_impl requires positive x; sign/error handling is done by the caller.
  return generic_pow_impl(pabs(x), exponent_packet);
}

template <typename Scalar>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE std::enable_if_t<is_scalar<Scalar>::value, Scalar> gen_pow(
    const Scalar& x, const Scalar& exponent) {
  return numext::pow(x, exponent);
}

// Handle special cases for pow(x, exponent) where both base and exponent are
// floating point and the exponent is a non-integer scalar (uniform across all
// SIMD lanes). This allows us to use scalar branches on exponent properties.
template <typename Packet, typename ScalarExponent>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet handle_nonint_nonint_errors(const Packet& x, const Packet& powx,
                                                                         const ScalarExponent& exponent) {
  using Scalar = typename unpacket_traits<Packet>::type;
  const Packet cst_zero = pzero(x);
  const Packet cst_one = pset1<Packet>(Scalar(1));
  const Packet cst_inf = pset1<Packet>(NumTraits<Scalar>::infinity());
  const Packet cst_nan = pset1<Packet>(NumTraits<Scalar>::quiet_NaN());

  const Packet abs_x = pabs(x);

  // x < 0 with non-integer exponent -> NaN.
  Packet result = pselect(pcmp_lt(x, cst_zero), cst_nan, powx);

  if (!(numext::isfinite)(exponent)) {
    if (exponent != exponent) {
      // pow(x, NaN) = NaN, except pow(+1, NaN) = 1.
      result = pselect(pcmp_eq(x, cst_one), cst_one, cst_nan);
    } else {
      // Exponent is +inf or -inf.
      const Packet abs_x_is_one = pcmp_eq(abs_x, cst_one);
      if (exponent > ScalarExponent(0)) {
        // pow(x, +inf): |x| > 1 -> +inf, |x| < 1 -> 0, |x| == 1 -> 1.
        result = pselect(pcmp_lt(cst_one, abs_x), cst_inf, cst_zero);
      } else {
        // pow(x, -inf): |x| < 1 -> +inf, |x| > 1 -> 0, |x| == 1 -> 1.
        result = pselect(pcmp_lt(abs_x, cst_one), cst_inf, cst_zero);
      }
      // pow(+-1, +-inf) = 1.
      result = pselect(abs_x_is_one, cst_one, result);
    }
  } else {
    // Finite non-integer exponent.
    const Packet x_is_zero = pcmp_eq(x, cst_zero);
    const Packet abs_x_is_inf = pcmp_eq(abs_x, cst_inf);
    if (exponent < ScalarExponent(0)) {
      // pow(+-0, negative non-integer) = +inf. pow(+-inf, negative) = +0.
      result = pselect(x_is_zero, cst_inf, result);
      result = pselect(abs_x_is_inf, cst_zero, result);
    } else {
      // pow(+-0, positive non-integer) = +0. pow(+-inf, positive) = +inf.
      result = pselect(x_is_zero, cst_zero, result);
      result = pselect(abs_x_is_inf, cst_inf, result);
    }
  }

  // NaN base produces NaN. This overrides all cases above, but pow(NaN, 0) = 1
  // and pow(NaN, integer) are handled by the integer exponent path and never
  // reach this function.
  result = pselect(pisnan(x), cst_nan, result);

  return result;
}

template <typename Packet, typename ScalarExponent,
          std::enable_if_t<NumTraits<typename unpacket_traits<Packet>::type>::IsSigned, bool> = true>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet handle_negative_exponent(const Packet& x, const ScalarExponent& exponent) {
  using Scalar = typename unpacket_traits<Packet>::type;

  // signed integer base, signed integer exponent case

  // This routine handles negative exponents.
  // The return value is either 0, 1, or -1.
  const Packet cst_pos_one = pset1<Packet>(Scalar(1));
  const bool exponent_is_odd = exponent % ScalarExponent(2) != ScalarExponent(0);
  const Packet exp_is_odd = exponent_is_odd ? ptrue<Packet>(x) : pzero<Packet>(x);

  const Packet abs_x = pabs(x);
  const Packet abs_x_is_one = pcmp_eq(abs_x, cst_pos_one);

  Packet result = pselect(exp_is_odd, x, abs_x);
  result = pselect(abs_x_is_one, result, pzero<Packet>(x));
  return result;
}

template <typename Packet, typename ScalarExponent,
          std::enable_if_t<!NumTraits<typename unpacket_traits<Packet>::type>::IsSigned, bool> = true>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet handle_negative_exponent(const Packet& x, const ScalarExponent&) {
  using Scalar = typename unpacket_traits<Packet>::type;

  // unsigned integer base, signed integer exponent case

  // This routine handles negative exponents.
  // The return value is either 0 or 1

  const Scalar pos_one = Scalar(1);

  const Packet cst_pos_one = pset1<Packet>(pos_one);

  const Packet x_is_one = pcmp_eq(x, cst_pos_one);

  return pand(x_is_one, x);
}

}  // end namespace unary_pow

template <typename Packet, typename ScalarExponent,
          bool BaseIsIntegerType = NumTraits<typename unpacket_traits<Packet>::type>::IsInteger,
          bool ExponentIsIntegerType = NumTraits<ScalarExponent>::IsInteger,
          bool ExponentIsSigned = NumTraits<ScalarExponent>::IsSigned>
struct unary_pow_impl;

template <typename Packet, typename ScalarExponent, bool ExponentIsSigned>
struct unary_pow_impl<Packet, ScalarExponent, false, false, ExponentIsSigned> {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent& exponent) {
    const bool exponent_is_integer = (numext::isfinite)(exponent) && numext::round(exponent) == exponent;
    if (exponent_is_integer) {
      // The simple recursive doubling implementation is only accurate to 3 ulps
      // for integer exponents in [-3:7]. Since this is a common case, we
      // specialize it here.
      bool use_repeated_squaring =
          (exponent <= ScalarExponent(7) && (!ExponentIsSigned || exponent >= ScalarExponent(-3)));
      return use_repeated_squaring ? unary_pow::int_pow(x, exponent) : generic_pow(x, pset1<Packet>(exponent));
    } else {
      Packet result = unary_pow::gen_pow(x, exponent);
      result = unary_pow::handle_nonint_nonint_errors(x, result, exponent);
      return result;
    }
  }
};

template <typename Packet, typename ScalarExponent, bool ExponentIsSigned>
struct unary_pow_impl<Packet, ScalarExponent, false, true, ExponentIsSigned> {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent& exponent) {
    return unary_pow::int_pow(x, exponent);
  }
};

template <typename Packet, typename ScalarExponent>
struct unary_pow_impl<Packet, ScalarExponent, true, true, true> {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent& exponent) {
    if (exponent < ScalarExponent(0)) {
      return unary_pow::handle_negative_exponent(x, exponent);
    } else {
      return unary_pow::int_pow(x, exponent);
    }
  }
};

template <typename Packet, typename ScalarExponent>
struct unary_pow_impl<Packet, ScalarExponent, true, true, false> {
  typedef typename unpacket_traits<Packet>::type Scalar;
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x, const ScalarExponent& exponent) {
    return unary_pow::int_pow(x, exponent);
  }
};

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_ARCH_GENERIC_PACKET_MATH_POW_H

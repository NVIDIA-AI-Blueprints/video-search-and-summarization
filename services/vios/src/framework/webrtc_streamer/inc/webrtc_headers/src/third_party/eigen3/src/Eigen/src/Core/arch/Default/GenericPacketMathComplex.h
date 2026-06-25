// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009-2019 Gael Guennebaud <gael.guennebaud@inria.fr>
// Copyright (C) 2018-2025 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ARCH_GENERIC_PACKET_MATH_COMPLEX_H
#define EIGEN_ARCH_GENERIC_PACKET_MATH_COMPLEX_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

//----------------------------------------------------------------------
// Complex Arithmetic and Functions
//----------------------------------------------------------------------

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet pdiv_complex(const Packet& x, const Packet& y) {
  typedef typename unpacket_traits<Packet>::as_real RealPacket;
  typedef typename unpacket_traits<RealPacket>::type RealScalar;
  // In the following we annotate the code for the case where the inputs
  // are a pair length-2 SIMD vectors representing a single pair of complex
  // numbers x = a + i*b, y = c + i*d.
  const RealPacket one = pset1<RealPacket>(RealScalar(1));
  const RealPacket abs_y = pabs(y.v);
  const RealPacket abs_y_flip = pcplxflip(Packet(abs_y)).v;

  const RealPacket mask = pcmp_lt(abs_y, abs_y_flip);  // |c| < |d|
  RealPacket y_scaled = pselect(mask, pdiv(abs_y, abs_y_flip), one);
  y_scaled = por(y_scaled, pandnot(y.v, abs_y));  // copy signs in case |c| == |d|
  RealPacket denom = pmul(y.v, y_scaled);
  denom = padd(denom, pcplxflip(Packet(denom)).v);  // c * c' + d * d'
  Packet num = pmul(x, pconj(Packet(y_scaled)));    // a * c' + b * d', -a * d + b * c
  return Packet(pdiv(num.v, denom));
}

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet pmul_complex(const Packet& x, const Packet& y) {
  // In the following we annotate the code for the case where the inputs
  // are a pair length-2 SIMD vectors representing a single pair of complex
  // numbers x = a + i*b, y = c + i*d.
  Packet x_re = pdupreal(x);                  // a, a
  Packet x_im = pdupimag(x);                  // b, b
  Packet tmp_re = Packet(pmul(x_re.v, y.v));  // a*c, a*d
  Packet tmp_im = Packet(pmul(x_im.v, y.v));  // b*c, b*d
  tmp_im = pcplxflip(pconj(tmp_im));          // -b*d, d*c
  return padd(tmp_im, tmp_re);                // a*c - b*d, a*d + b*c
}

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet plog_complex(const Packet& x) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename Scalar::value_type RealScalar;
  typedef typename unpacket_traits<Packet>::as_real RealPacket;

  // Real part
  RealPacket x_flip = pcplxflip(x).v;  // b, a
  Packet x_norm = phypot_complex(x);   // sqrt(a^2 + b^2), sqrt(a^2 + b^2)
  RealPacket xlogr = plog(x_norm.v);   // log(sqrt(a^2 + b^2)), log(sqrt(a^2 + b^2))

  // Imag part
  RealPacket ximg = patan2(x.v, x_flip);  // atan2(a, b), atan2(b, a)

  const RealPacket cst_pos_inf = pset1<RealPacket>(NumTraits<RealScalar>::infinity());
  RealPacket x_abs = pabs(x.v);
  RealPacket is_x_pos_inf = pcmp_eq(x_abs, cst_pos_inf);
  RealPacket is_y_pos_inf = pcplxflip(Packet(is_x_pos_inf)).v;
  RealPacket is_any_inf = por(is_x_pos_inf, is_y_pos_inf);
  RealPacket xreal = pselect(is_any_inf, cst_pos_inf, xlogr);

  return Packet(pselect(peven_mask(xreal), xreal, ximg));  // log(sqrt(a^2 + b^2)), atan2(b, a)
}

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet pexp_complex(const Packet& a) {
  typedef typename unpacket_traits<Packet>::as_real RealPacket;
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename Scalar::value_type RealScalar;
  const RealPacket even_mask = peven_mask(a.v);
  const RealPacket odd_mask = pcplxflip(Packet(even_mask)).v;

  // Let a = x + iy.
  // exp(a) = exp(x) * cis(y), plus some special edge-case handling.

  // exp(x):
  RealPacket x = pand(a.v, even_mask);
  x = por(x, pcplxflip(Packet(x)).v);
  RealPacket expx = pexp(x);  // exp(x);

  // cis(y):
  RealPacket y = pand(odd_mask, a.v);
  y = por(y, pcplxflip(Packet(y)).v);
  RealPacket cisy = psincos_selector<RealPacket>(y);
  cisy = pcplxflip(Packet(cisy)).v;  // cos(y) + i * sin(y)

  const RealPacket cst_pos_inf = pset1<RealPacket>(NumTraits<RealScalar>::infinity());
  const RealPacket cst_neg_inf = pset1<RealPacket>(-NumTraits<RealScalar>::infinity());

  // If x is -inf, we know that cossin(y) is bounded,
  //   so the result is (0, +/-0), where the sign of the imaginary part comes
  //   from the sign of cossin(y).
  RealPacket cisy_sign = por(pandnot(cisy, pabs(cisy)), pset1<RealPacket>(RealScalar(1)));
  cisy = pselect(pcmp_eq(x, cst_neg_inf), cisy_sign, cisy);

  // If x is inf, and cos(y) has unknown sign (y is inf or NaN), the result
  // is (+/-inf, NaN), where the signs are undetermined (take the sign of y).
  RealPacket y_sign = por(pandnot(y, pabs(y)), pset1<RealPacket>(RealScalar(1)));
  cisy = pselect(pand(pcmp_eq(x, cst_pos_inf), pisnan(cisy)), pand(y_sign, even_mask), cisy);

  // If exp(x) is +inf and y is finite, replace cisy with copysign(1, cisy) to
  // prevent inf * 0 = NaN. The vectorized sincos may compute exact zero
  // for near-zero values like cos(pi/2), and inf * +-1 = +-inf is correct.
  // The y=0 case is handled separately below.
  RealPacket cisy_sign_one = por(pand(cisy, pset1<RealPacket>(RealScalar(-0.0))), pset1<RealPacket>(RealScalar(1)));
  RealPacket expx_inf_y_finite = pand(pcmp_eq(expx, cst_pos_inf), pcmp_lt(pabs(y), cst_pos_inf));
  cisy = pselect(expx_inf_y_finite, cisy_sign_one, cisy);

  Packet result = Packet(pmul(expx, cisy));

  // If y is +/- 0, the input is real, so take the real result for consistency.
  result = pselect(Packet(pcmp_eq(y, pzero(y))), Packet(por(pand(expx, even_mask), pand(y, odd_mask))), result);

  return result;
}

template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet psqrt_complex(const Packet& a) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename Scalar::value_type RealScalar;
  typedef typename unpacket_traits<Packet>::as_real RealPacket;

  // Computes the principal sqrt of the complex numbers in the input.
  //
  // For example, for packets containing 2 complex numbers stored in interleaved format
  //    a = [a0, a1] = [x0, y0, x1, y1],
  // where x0 = real(a0), y0 = imag(a0) etc., this function returns
  //    b = [b0, b1] = [u0, v0, u1, v1],
  // such that b0^2 = a0, b1^2 = a1.
  //
  // To derive the formula for the complex square roots, let's consider the equation for
  // a single complex square root of the number x + i*y. We want to find real numbers
  // u and v such that
  //    (u + i*v)^2 = x + i*y  <=>
  //    u^2 - v^2 + i*2*u*v = x + i*v.
  // By equating the real and imaginary parts we get:
  //    u^2 - v^2 = x
  //    2*u*v = y.
  //
  // For x >= 0, this has the numerically stable solution
  //    u = sqrt(0.5 * (x + sqrt(x^2 + y^2)))
  //    v = 0.5 * (y / u)
  // and for x < 0,
  //    v = sign(y) * sqrt(0.5 * (-x + sqrt(x^2 + y^2)))
  //    u = 0.5 * (y / v)
  //
  //  To avoid unnecessary over- and underflow, we compute sqrt(x^2 + y^2) as
  //     l = max(|x|, |y|) * sqrt(1 + (min(|x|, |y|) / max(|x|, |y|))^2) ,

  // In the following, without lack of generality, we have annotated the code, assuming
  // that the input is a packet of 2 complex numbers.
  //
  // Step 1. Compute l = [l0, l0, l1, l1], where
  //    l0 = sqrt(x0^2 + y0^2),  l1 = sqrt(x1^2 + y1^2)
  // To avoid over- and underflow, we use the stable formula for each hypotenuse
  //    l0 = (min0 == 0 ? max0 : max0 * sqrt(1 + (min0/max0)**2)),
  // where max0 = max(|x0|, |y0|), min0 = min(|x0|, |y0|), and similarly for l1.

  RealPacket a_abs = pabs(a.v);                        // [|x0|, |y0|, |x1|, |y1|]
  RealPacket a_abs_flip = pcplxflip(Packet(a_abs)).v;  // [|y0|, |x0|, |y1|, |x1|]
  RealPacket a_max = pmax(a_abs, a_abs_flip);
  RealPacket a_min = pmin(a_abs, a_abs_flip);
  RealPacket a_min_zero_mask = pcmp_eq(a_min, pzero(a_min));
  RealPacket a_max_zero_mask = pcmp_eq(a_max, pzero(a_max));
  RealPacket r = pdiv(a_min, a_max);
  const RealPacket cst_one = pset1<RealPacket>(RealScalar(1));
  RealPacket l = pmul(a_max, psqrt(padd(cst_one, pmul(r, r))));  // [l0, l0, l1, l1]
  // Set l to a_max if a_min is zero.
  l = pselect(a_min_zero_mask, a_max, l);

  // Step 2. Compute [rho0, *, rho1, *], where
  // rho0 = sqrt(0.5 * (l0 + |x0|)), rho1 =  sqrt(0.5 * (l1 + |x1|))
  // We don't care about the imaginary parts computed here. They will be overwritten later.
  const RealPacket cst_half = pset1<RealPacket>(RealScalar(0.5));
  Packet rho;
  rho.v = psqrt(pmul(cst_half, padd(a_abs, l)));

  // Step 3. Compute [rho0, eta0, rho1, eta1], where
  // eta0 = (y0 / l0) / 2, and eta1 = (y1 / l1) / 2.
  // set eta = 0 of input is 0 + i0.
  RealPacket eta = pandnot(pmul(cst_half, pdiv(a.v, pcplxflip(rho).v)), a_max_zero_mask);
  RealPacket real_mask = peven_mask(a.v);
  Packet positive_real_result;
  // Compute result for inputs with positive real part.
  positive_real_result.v = pselect(real_mask, rho.v, eta);

  // Step 4. Compute solution for inputs with negative real part:
  //         [|eta0|, sign(y0)*rho0, |eta1|, sign(y1)*rho1]
  const RealPacket cst_imag_sign_mask = pset1<Packet>(Scalar(RealScalar(0.0), RealScalar(-0.0))).v;
  RealPacket imag_signs = pand(a.v, cst_imag_sign_mask);
  Packet negative_real_result;
  // Notice that rho is positive, so taking its absolute value is a noop.
  negative_real_result.v = por(pabs(pcplxflip(positive_real_result).v), imag_signs);

  // Step 5. Select solution branch based on the sign of the real parts.
  Packet negative_real_mask;
  negative_real_mask.v = pcmp_lt(pand(real_mask, a.v), pzero(a.v));
  negative_real_mask.v = por(negative_real_mask.v, pcplxflip(negative_real_mask).v);
  Packet result = pselect(negative_real_mask, negative_real_result, positive_real_result);

  // Step 6. Handle special cases for infinities:
  // * If z is (x,+∞), the result is (+∞,+∞) even if x is NaN
  // * If z is (x,-∞), the result is (+∞,-∞) even if x is NaN
  // * If z is (-∞,y), the result is (0*|y|,+∞) for finite or NaN y
  // * If z is (+∞,y), the result is (+∞,0*|y|) for finite or NaN y
  const RealPacket cst_pos_inf = pset1<RealPacket>(NumTraits<RealScalar>::infinity());
  Packet is_inf;
  is_inf.v = pcmp_eq(a_abs, cst_pos_inf);
  Packet is_real_inf;
  is_real_inf.v = pand(is_inf.v, real_mask);
  is_real_inf = por(is_real_inf, pcplxflip(is_real_inf));
  // prepare packet of (+∞,0*|y|) or (0*|y|,+∞), depending on the sign of the infinite real part.
  Packet real_inf_result;
  real_inf_result.v = pmul(a_abs, pset1<Packet>(Scalar(RealScalar(1.0), RealScalar(0.0))).v);
  real_inf_result.v = pselect(negative_real_mask.v, pcplxflip(real_inf_result).v, real_inf_result.v);
  // prepare packet of (+∞,+∞) or (+∞,-∞), depending on the sign of the infinite imaginary part.
  Packet is_imag_inf;
  is_imag_inf.v = pandnot(is_inf.v, real_mask);
  is_imag_inf = por(is_imag_inf, pcplxflip(is_imag_inf));
  Packet imag_inf_result;
  imag_inf_result.v = por(pand(cst_pos_inf, real_mask), pandnot(a.v, real_mask));
  // unless otherwise specified, if either the real or imaginary component is nan, the entire result is nan
  Packet result_is_nan = pisnan(result);
  result = por(result_is_nan, result);

  return pselect(is_imag_inf, imag_inf_result, pselect(is_real_inf, real_inf_result, result));
}

// \internal \returns the norm of a complex number z = x + i*y, defined as sqrt(x^2 + y^2).
// Implemented using the hypot(a,b) algorithm from https://doi.org/10.48550/arXiv.1904.09481
template <typename Packet>
EIGEN_DEFINE_FUNCTION_ALLOWING_MULTIPLE_DEFINITIONS Packet phypot_complex(const Packet& a) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename Scalar::value_type RealScalar;
  typedef typename unpacket_traits<Packet>::as_real RealPacket;

  const RealPacket cst_zero_rp = pset1<RealPacket>(static_cast<RealScalar>(0.0));
  const RealPacket cst_minus_one_rp = pset1<RealPacket>(static_cast<RealScalar>(-1.0));
  const RealPacket cst_two_rp = pset1<RealPacket>(static_cast<RealScalar>(2.0));
  const RealPacket evenmask = peven_mask(a.v);

  RealPacket a_abs = pabs(a.v);
  RealPacket a_flip = pcplxflip(Packet(a_abs)).v;       // |b|, |a|
  RealPacket a_all = pselect(evenmask, a_abs, a_flip);  // |a|, |a|
  RealPacket b_all = pselect(evenmask, a_flip, a_abs);  // |b|, |b|

  RealPacket a2 = pmul(a.v, a.v);                    // |a^2, b^2|
  RealPacket a2_flip = pcplxflip(Packet(a2)).v;      // |b^2, a^2|
  RealPacket h = psqrt(padd(a2, a2_flip));           // |sqrt(a^2 + b^2), sqrt(a^2 + b^2)|
  RealPacket h_sq = pmul(h, h);                      // |a^2 + b^2, a^2 + b^2|
  RealPacket a_sq = pselect(evenmask, a2, a2_flip);  // |a^2, a^2|
  RealPacket m_h_sq = pmul(h_sq, cst_minus_one_rp);
  RealPacket m_a_sq = pmul(a_sq, cst_minus_one_rp);
  RealPacket x = psub(psub(pmadd(h, h, m_h_sq), pmadd(b_all, b_all, psub(a_sq, h_sq))), pmadd(a_all, a_all, m_a_sq));
  h = psub(h, pdiv(x, pmul(cst_two_rp, h)));  // |h - x/(2*h), h - x/(2*h)|

  // handle zero-case
  RealPacket iszero = pcmp_eq(por(a_abs, a_flip), cst_zero_rp);

  h = pandnot(h, iszero);  // |sqrt(a^2+b^2), sqrt(a^2+b^2)|
  return Packet(h);        // |sqrt(a^2+b^2), sqrt(a^2+b^2)|
}

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_ARCH_GENERIC_PACKET_MATH_COMPLEX_H

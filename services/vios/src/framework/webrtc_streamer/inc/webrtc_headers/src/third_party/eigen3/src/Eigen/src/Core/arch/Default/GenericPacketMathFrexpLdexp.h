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

#ifndef EIGEN_ARCH_GENERIC_PACKET_MATH_FREXP_LDEXP_H
#define EIGEN_ARCH_GENERIC_PACKET_MATH_FREXP_LDEXP_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

// Creates a Scalar integer type with same bit-width.
template <typename T>
struct make_integer;
template <>
struct make_integer<float> {
  typedef numext::int32_t type;
};
template <>
struct make_integer<double> {
  typedef numext::int64_t type;
};
template <>
struct make_integer<half> {
  typedef numext::int16_t type;
};
template <>
struct make_integer<bfloat16> {
  typedef numext::int16_t type;
};

template <typename Packet>
EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC Packet pfrexp_generic_get_biased_exponent(const Packet& a) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename unpacket_traits<Packet>::integer_packet PacketI;
  static constexpr int mantissa_bits = numext::numeric_limits<Scalar>::digits - 1;
  return pcast<PacketI, Packet>(plogical_shift_right<mantissa_bits>(preinterpret<PacketI>(pabs(a))));
}

// Safely applies frexp, correctly handles denormals.
// Assumes IEEE floating point format.
template <typename Packet>
EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC Packet pfrexp_generic(const Packet& a, Packet& exponent) {
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef std::make_unsigned_t<typename make_integer<Scalar>::type> ScalarUI;
  static constexpr int TotalBits = sizeof(Scalar) * CHAR_BIT, MantissaBits = numext::numeric_limits<Scalar>::digits - 1,
                       ExponentBits = TotalBits - MantissaBits - 1;

  constexpr ScalarUI scalar_sign_mantissa_mask =
      ~(((ScalarUI(1) << ExponentBits) - ScalarUI(1)) << MantissaBits);  // ~0x7f800000
  const Packet sign_mantissa_mask = pset1frombits<Packet>(static_cast<ScalarUI>(scalar_sign_mantissa_mask));
  const Packet half = pset1<Packet>(Scalar(0.5));
  const Packet zero = pzero(a);
  const Packet normal_min = pset1<Packet>((numext::numeric_limits<Scalar>::min)());  // Minimum normal value, 2^-126

  // To handle denormals, normalize by multiplying by 2^(int(MantissaBits)+1).
  const Packet is_denormal = pcmp_lt(pabs(a), normal_min);
  constexpr ScalarUI scalar_normalization_offset = ScalarUI(MantissaBits + 1);  // 24
  // The following cannot be constexpr because bfloat16(uint16_t) is not constexpr.
  const Scalar scalar_normalization_factor = Scalar(ScalarUI(1) << int(scalar_normalization_offset));  // 2^24
  const Packet normalization_factor = pset1<Packet>(scalar_normalization_factor);
  const Packet normalized_a = pselect(is_denormal, pmul(a, normalization_factor), a);

  // Determine exponent offset: -126 if normal, -126-24 if denormal
  const Scalar scalar_exponent_offset = -Scalar((ScalarUI(1) << (ExponentBits - 1)) - ScalarUI(2));  // -126
  Packet exponent_offset = pset1<Packet>(scalar_exponent_offset);
  const Packet normalization_offset = pset1<Packet>(-Scalar(scalar_normalization_offset));  // -24
  exponent_offset = pselect(is_denormal, padd(exponent_offset, normalization_offset), exponent_offset);

  // Determine exponent and mantissa from normalized_a.
  exponent = pfrexp_generic_get_biased_exponent(normalized_a);
  // Zero, Inf and NaN return 'a' unmodified, exponent is zero
  // (technically the exponent is unspecified for inf/NaN, but GCC/Clang set it to zero)
  const Scalar scalar_non_finite_exponent = Scalar((ScalarUI(1) << ExponentBits) - ScalarUI(1));  // 255
  const Packet non_finite_exponent = pset1<Packet>(scalar_non_finite_exponent);
  const Packet is_zero_or_not_finite = por(pcmp_eq(a, zero), pcmp_eq(exponent, non_finite_exponent));
  const Packet m = pselect(is_zero_or_not_finite, a, por(pand(normalized_a, sign_mantissa_mask), half));
  exponent = pselect(is_zero_or_not_finite, zero, padd(exponent, exponent_offset));
  return m;
}

// Safely applies ldexp, correctly handles overflows, underflows and denormals.
// Assumes IEEE floating point format.
template <typename Packet>
EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC Packet pldexp_generic(const Packet& a, const Packet& exponent) {
  // We want to return a * 2^exponent, allowing for all possible integer
  // exponents without overflowing or underflowing in intermediate
  // computations.
  //
  // Since 'a' and the output can be denormal, the maximum range of 'exponent'
  // to consider for a float is:
  //   -255-23 -> 255+23
  // Below -278 any finite float 'a' will become zero, and above +278 any
  // finite float will become inf, including when 'a' is the smallest possible
  // denormal.
  //
  // Unfortunately, 2^(278) cannot be represented using either one or two
  // finite normal floats, so we must split the scale factor into at least
  // three parts. It turns out to be faster to split 'exponent' into four
  // factors, since [exponent>>2] is much faster to compute that [exponent/3].
  //
  // Set e = min(max(exponent, -278), 278);
  //     b = floor(e/4);
  //   out = ((((a * 2^(b)) * 2^(b)) * 2^(b)) * 2^(e-3*b))
  //
  // This will avoid any intermediate overflows and correctly handle 0, inf,
  // NaN cases.
  typedef typename unpacket_traits<Packet>::integer_packet PacketI;
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename unpacket_traits<PacketI>::type ScalarI;
  static constexpr int TotalBits = sizeof(Scalar) * CHAR_BIT, MantissaBits = numext::numeric_limits<Scalar>::digits - 1,
                       ExponentBits = TotalBits - MantissaBits - 1;

  const Packet max_exponent = pset1<Packet>(Scalar((ScalarI(1) << ExponentBits) + ScalarI(MantissaBits - 1)));  // 278
  const PacketI bias = pset1<PacketI>((ScalarI(1) << (ExponentBits - 1)) - ScalarI(1));                         // 127
  const PacketI e = pcast<Packet, PacketI>(pmin(pmax(exponent, pnegate(max_exponent)), max_exponent));
  PacketI b = parithmetic_shift_right<2>(e);                                          // floor(e/4);
  Packet c = preinterpret<Packet>(plogical_shift_left<MantissaBits>(padd(b, bias)));  // 2^b
  Packet out = pmul(pmul(pmul(a, c), c), c);                                          // a * 2^(3b)
  b = pnmadd(pset1<PacketI>(3), b, e);                                                // e - 3b
  c = preinterpret<Packet>(plogical_shift_left<MantissaBits>(padd(b, bias)));         // 2^(e-3*b)
  out = pmul(out, c);
  return out;
}

// Explicitly multiplies
//    a * (2^e)
// clamping e to the range
// [NumTraits<Scalar>::min_exponent()-2, NumTraits<Scalar>::max_exponent()]
//
// This is approx 7x faster than pldexp_impl, but will prematurely over/underflow
// if 2^e doesn't fit into a normal floating-point Scalar.
//
// Assumes IEEE floating point format
template <typename Packet>
EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC Packet pldexp_fast(const Packet& a, const Packet& exponent) {
  typedef typename unpacket_traits<Packet>::integer_packet PacketI;
  typedef typename unpacket_traits<Packet>::type Scalar;
  typedef typename unpacket_traits<PacketI>::type ScalarI;
  static constexpr int TotalBits = sizeof(Scalar) * CHAR_BIT, MantissaBits = numext::numeric_limits<Scalar>::digits - 1,
                       ExponentBits = TotalBits - MantissaBits - 1;

  const Packet bias = pset1<Packet>(Scalar((ScalarI(1) << (ExponentBits - 1)) - ScalarI(1)));  // 127
  const Packet limit = pset1<Packet>(Scalar((ScalarI(1) << ExponentBits) - ScalarI(1)));       // 255
  // restrict biased exponent between 0 and 255 for float.
  const PacketI e = pcast<Packet, PacketI>(pmin(pmax(padd(exponent, bias), pzero(limit)), limit));  // exponent + 127
  // return a * (2^e)
  return pmul(a, preinterpret<Packet>(plogical_shift_left<MantissaBits>(e)));
}

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_ARCH_GENERIC_PACKET_MATH_FREXP_LDEXP_H

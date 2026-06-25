// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_COMPLEX_CLANG_H
#define EIGEN_COMPLEX_CLANG_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

template <typename RealScalar, int N>
struct complex_packet_wrapper {
  using RealPacketT = detail::VectorType<RealScalar, 2 * N>;
  complex_packet_wrapper() = default;
  EIGEN_STRONG_INLINE explicit complex_packet_wrapper(const RealPacketT& a) : v(a) {}
  EIGEN_STRONG_INLINE constexpr std::complex<RealScalar> operator[](Index i) const {
    return std::complex<RealScalar>(v[2 * i], v[2 * i + 1]);
  }
  RealPacketT v;
};

// --- Primary complex packet aliases ---
constexpr int kComplexFloatSize = kFloatPacketSize / 2;    // 2, 4, or 8
constexpr int kComplexDoubleSize = kDoublePacketSize / 2;  // 1, 2, or 4
using PacketXcf = complex_packet_wrapper<float, kComplexFloatSize>;
using PacketXcd = complex_packet_wrapper<double, kComplexDoubleSize>;

// Sub-packet types needed for reductions at larger sizes.
// When PacketXcf IS already a given size, we skip the alias to avoid duplicates.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
using Packet2cf = complex_packet_wrapper<float, 2>;
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
using Packet4cf = complex_packet_wrapper<float, 4>;
using Packet2cd = complex_packet_wrapper<double, 2>;
#endif

struct generic_complex_packet_traits : default_packet_traits {
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 1,
    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasArg = 0,
    HasSetLinear = 0,
    HasConj = 1,
    // Math functions
    HasLog = 1,
    HasExp = 1,
    HasSqrt = 1,
  };
};

template <>
struct packet_traits<std::complex<float>> : generic_complex_packet_traits {
  using type = PacketXcf;
  using half = PacketXcf;
  enum {
    size = kComplexFloatSize,
  };
};

template <>
struct unpacket_traits<PacketXcf> : generic_unpacket_traits {
  using type = std::complex<float>;
  using half = PacketXcf;
  using as_real = PacketXf;
  enum {
    size = kComplexFloatSize,
  };
};

template <>
struct packet_traits<std::complex<double>> : generic_complex_packet_traits {
  using type = PacketXcd;
  using half = PacketXcd;
  enum {
    size = kComplexDoubleSize,
  };
};

template <>
struct unpacket_traits<PacketXcd> : generic_unpacket_traits {
  using type = std::complex<double>;
  using half = PacketXcd;
  using as_real = PacketXd;
  enum {
    size = kComplexDoubleSize,
  };
};

// ------------ Load and store ops ----------
#define EIGEN_CLANG_COMPLEX_LOAD_STORE(PACKET_TYPE)                                                       \
  template <>                                                                                             \
  EIGEN_STRONG_INLINE PACKET_TYPE ploadu<PACKET_TYPE>(const unpacket_traits<PACKET_TYPE>::type* from) {   \
    return PACKET_TYPE(ploadu<typename unpacket_traits<PACKET_TYPE>::as_real>(&numext::real_ref(*from))); \
  }                                                                                                       \
  template <>                                                                                             \
  EIGEN_STRONG_INLINE PACKET_TYPE pload<PACKET_TYPE>(const unpacket_traits<PACKET_TYPE>::type* from) {    \
    return PACKET_TYPE(pload<typename unpacket_traits<PACKET_TYPE>::as_real>(&numext::real_ref(*from)));  \
  }                                                                                                       \
  template <>                                                                                             \
  EIGEN_STRONG_INLINE void pstoreu<typename unpacket_traits<PACKET_TYPE>::type, PACKET_TYPE>(             \
      typename unpacket_traits<PACKET_TYPE>::type * to, const PACKET_TYPE& from) {                        \
    pstoreu(&numext::real_ref(*to), from.v);                                                              \
  }                                                                                                       \
  template <>                                                                                             \
  EIGEN_STRONG_INLINE void pstore<typename unpacket_traits<PACKET_TYPE>::type, PACKET_TYPE>(              \
      typename unpacket_traits<PACKET_TYPE>::type * to, const PACKET_TYPE& from) {                        \
    pstore(&numext::real_ref(*to), from.v);                                                               \
  }

EIGEN_CLANG_COMPLEX_LOAD_STORE(PacketXcf);
EIGEN_CLANG_COMPLEX_LOAD_STORE(PacketXcd);
#undef EIGEN_CLANG_COMPLEX_LOAD_STORE

// --- pset1 for complex ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf pset1<PacketXcf>(const std::complex<float>& from) {
  const float re = numext::real(from);
  const float im = numext::imag(from);
  return PacketXcf(PacketXf{re, im, re, im});
}
template <>
EIGEN_STRONG_INLINE PacketXcd pset1<PacketXcd>(const std::complex<double>& from) {
  const double re = numext::real(from);
  const double im = numext::imag(from);
  return PacketXcd(PacketXd{re, im});
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf pset1<PacketXcf>(const std::complex<float>& from) {
  const float re = numext::real(from);
  const float im = numext::imag(from);
  return PacketXcf(PacketXf{re, im, re, im, re, im, re, im});
}
template <>
EIGEN_STRONG_INLINE PacketXcd pset1<PacketXcd>(const std::complex<double>& from) {
  const double re = numext::real(from);
  const double im = numext::imag(from);
  return PacketXcd(PacketXd{re, im, re, im});
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf pset1<PacketXcf>(const std::complex<float>& from) {
  const float re = numext::real(from);
  const float im = numext::imag(from);
  return PacketXcf(PacketXf{re, im, re, im, re, im, re, im, re, im, re, im, re, im, re, im});
}
template <>
EIGEN_STRONG_INLINE PacketXcd pset1<PacketXcd>(const std::complex<double>& from) {
  const double re = numext::real(from);
  const double im = numext::imag(from);
  return PacketXcd(PacketXd{re, im, re, im, re, im, re, im});
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// ----------- Unary ops ------------------
#define DELEGATE_UNARY_TO_REAL_OP(PACKET_TYPE, OP)                        \
  template <>                                                             \
  EIGEN_STRONG_INLINE PACKET_TYPE OP<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return PACKET_TYPE(OP(a.v));                                          \
  }

#define EIGEN_CLANG_COMPLEX_UNARY_CWISE_OPS(PACKET_TYPE)                                             \
  DELEGATE_UNARY_TO_REAL_OP(PACKET_TYPE, pnegate)                                                    \
  DELEGATE_UNARY_TO_REAL_OP(PACKET_TYPE, pzero)                                                      \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type pfirst<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return a[0];                                                                                     \
  }                                                                                                  \
  EIGEN_INSTANTIATE_COMPLEX_MATH_FUNCS(PACKET_TYPE)

EIGEN_CLANG_COMPLEX_UNARY_CWISE_OPS(PacketXcf);
EIGEN_CLANG_COMPLEX_UNARY_CWISE_OPS(PacketXcd);

// --- pconj ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf pconj<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, -a.v, 0, 5, 2, 7));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pconj<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, -a.v, 0, 3));
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf pconj<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, -a.v, 0, 9, 2, 11, 4, 13, 6, 15));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pconj<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, -a.v, 0, 5, 2, 7));
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf pconj<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, -a.v, 0, 17, 2, 19, 4, 21, 6, 23, 8, 25, 10, 27, 12, 29, 14, 31));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pconj<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, -a.v, 0, 9, 2, 11, 4, 13, 6, 15));
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// Sub-packet pconj specializations needed for reductions.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
template <>
EIGEN_STRONG_INLINE Packet2cf pconj<Packet2cf>(const Packet2cf& a) {
  return Packet2cf(__builtin_shufflevector(a.v, -a.v, 0, 5, 2, 7));
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
template <>
EIGEN_STRONG_INLINE Packet4cf pconj<Packet4cf>(const Packet4cf& a) {
  return Packet4cf(__builtin_shufflevector(a.v, -a.v, 0, 9, 2, 11, 4, 13, 6, 15));
}
template <>
EIGEN_STRONG_INLINE Packet2cd pconj<Packet2cd>(const Packet2cd& a) {
  return Packet2cd(__builtin_shufflevector(a.v, -a.v, 0, 5, 2, 7));
}
#endif

#undef DELEGATE_UNARY_TO_REAL_OP
#undef EIGEN_CLANG_COMPLEX_UNARY_CWISE_OPS

// Flip real and imaginary parts, i.e.  {re(a), im(a)} -> {im(a), re(a)}.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf pcplxflip<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pcplxflip<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 0));
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf pcplxflip<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2, 5, 4, 7, 6));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pcplxflip<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2));
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf pcplxflip<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pcplxflip<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2, 5, 4, 7, 6));
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// Sub-packet pcplxflip specializations needed for reductions.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
template <>
EIGEN_STRONG_INLINE Packet2cf pcplxflip<Packet2cf>(const Packet2cf& a) {
  return Packet2cf(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2));
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
template <>
EIGEN_STRONG_INLINE Packet4cf pcplxflip<Packet4cf>(const Packet4cf& a) {
  return Packet4cf(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2, 5, 4, 7, 6));
}
template <>
EIGEN_STRONG_INLINE Packet2cd pcplxflip<Packet2cd>(const Packet2cd& a) {
  return Packet2cd(__builtin_shufflevector(a.v, a.v, 1, 0, 3, 2));
}
#endif

// Copy real to imaginary part, i.e. {re(a), im(a)} -> {re(a), re(a)}.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf pdupreal<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupreal<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 0, 0));
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf pdupreal<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2, 4, 4, 6, 6));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupreal<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2));
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf pdupreal<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2, 4, 4, 6, 6, 8, 8, 10, 10, 12, 12, 14, 14));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupreal<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2, 4, 4, 6, 6));
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// Sub-packet pdupreal specializations needed for reductions.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
template <>
EIGEN_STRONG_INLINE Packet2cf pdupreal<Packet2cf>(const Packet2cf& a) {
  return Packet2cf(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2));
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
template <>
EIGEN_STRONG_INLINE Packet4cf pdupreal<Packet4cf>(const Packet4cf& a) {
  return Packet4cf(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2, 4, 4, 6, 6));
}
template <>
EIGEN_STRONG_INLINE Packet2cd pdupreal<Packet2cd>(const Packet2cd& a) {
  return Packet2cd(__builtin_shufflevector(a.v, a.v, 0, 0, 2, 2));
}
#endif

// Copy imaginary to real part, i.e. {re(a), im(a)} -> {im(a), im(a)}.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf pdupimag<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupimag<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 1));
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf pdupimag<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3, 5, 5, 7, 7));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupimag<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3));
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf pdupimag<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3, 5, 5, 7, 7, 9, 9, 11, 11, 13, 13, 15, 15));
}
template <>
EIGEN_STRONG_INLINE PacketXcd pdupimag<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3, 5, 5, 7, 7));
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// Sub-packet pdupimag specializations needed for reductions.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
template <>
EIGEN_STRONG_INLINE Packet2cf pdupimag<Packet2cf>(const Packet2cf& a) {
  return Packet2cf(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3));
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
template <>
EIGEN_STRONG_INLINE Packet4cf pdupimag<Packet4cf>(const Packet4cf& a) {
  return Packet4cf(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3, 5, 5, 7, 7));
}
template <>
EIGEN_STRONG_INLINE Packet2cd pdupimag<Packet2cd>(const Packet2cd& a) {
  return Packet2cd(__builtin_shufflevector(a.v, a.v, 1, 1, 3, 3));
}
#endif

// --- ploaddup ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf ploaddup<PacketXcf>(const std::complex<float>* from) {
  return pset1<PacketXcf>(*from);
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploaddup<PacketXcd>(const std::complex<double>* from) {
  return pset1<PacketXcd>(*from);
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf ploaddup<PacketXcf>(const std::complex<float>* from) {
  return PacketXcf(PacketXf{std::real(from[0]), std::imag(from[0]), std::real(from[0]), std::imag(from[0]),
                            std::real(from[1]), std::imag(from[1]), std::real(from[1]), std::imag(from[1])});
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploaddup<PacketXcd>(const std::complex<double>* from) {
  return pset1<PacketXcd>(*from);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf ploaddup<PacketXcf>(const std::complex<float>* from) {
  return PacketXcf(PacketXf{std::real(from[0]), std::imag(from[0]), std::real(from[0]), std::imag(from[0]),
                            std::real(from[1]), std::imag(from[1]), std::real(from[1]), std::imag(from[1]),
                            std::real(from[2]), std::imag(from[2]), std::real(from[2]), std::imag(from[2]),
                            std::real(from[3]), std::imag(from[3]), std::real(from[3]), std::imag(from[3])});
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploaddup<PacketXcd>(const std::complex<double>* from) {
  return PacketXcd(PacketXd{std::real(from[0]), std::imag(from[0]), std::real(from[0]), std::imag(from[0]),
                            std::real(from[1]), std::imag(from[1]), std::real(from[1]), std::imag(from[1])});
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- ploadquad ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf ploadquad<PacketXcf>(const std::complex<float>* from) {
  return pset1<PacketXcf>(*from);
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploadquad<PacketXcd>(const std::complex<double>* from) {
  return pset1<PacketXcd>(*from);
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf ploadquad<PacketXcf>(const std::complex<float>* from) {
  return pset1<PacketXcf>(*from);
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploadquad<PacketXcd>(const std::complex<double>* from) {
  return pset1<PacketXcd>(*from);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf ploadquad<PacketXcf>(const std::complex<float>* from) {
  return PacketXcf(PacketXf{std::real(from[0]), std::imag(from[0]), std::real(from[0]), std::imag(from[0]),
                            std::real(from[0]), std::imag(from[0]), std::real(from[0]), std::imag(from[0]),
                            std::real(from[1]), std::imag(from[1]), std::real(from[1]), std::imag(from[1]),
                            std::real(from[1]), std::imag(from[1]), std::real(from[1]), std::imag(from[1])});
}
template <>
EIGEN_STRONG_INLINE PacketXcd ploadquad<PacketXcd>(const std::complex<double>* from) {
  return pset1<PacketXcd>(*from);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- preverse ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXcf preverse<PacketXcf>(const PacketXcf& a) {
  // 2 complex floats: swap pairs (0,1) and (2,3)
  return PacketXcf(__builtin_shufflevector(a.v, a.v, 2, 3, 0, 1));
}
template <>
EIGEN_STRONG_INLINE PacketXcd preverse<PacketXcd>(const PacketXcd& a) {
  // 1 complex double: identity
  return a;
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXcf preverse<PacketXcf>(const PacketXcf& a) {
  // 4 complex floats: reverse pairs
  return PacketXcf(reinterpret_cast<PacketXf>(preverse(reinterpret_cast<PacketXd>(a.v))));
}
template <>
EIGEN_STRONG_INLINE PacketXcd preverse<PacketXcd>(const PacketXcd& a) {
  // 2 complex doubles: swap pairs
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 2, 3, 0, 1));
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXcf preverse<PacketXcf>(const PacketXcf& a) {
  return PacketXcf(reinterpret_cast<PacketXf>(preverse(reinterpret_cast<PacketXd>(a.v))));
}
template <>
EIGEN_STRONG_INLINE PacketXcd preverse<PacketXcd>(const PacketXcd& a) {
  return PacketXcd(__builtin_shufflevector(a.v, a.v, 6, 7, 4, 5, 2, 3, 0, 1));
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// ----------- Binary ops ------------------
#define DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, OP)                                             \
  template <>                                                                                   \
  EIGEN_STRONG_INLINE PACKET_TYPE OP<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return PACKET_TYPE(OP(a.v, b.v));                                                           \
  }

#define EIGEN_CLANG_COMPLEX_BINARY_CWISE_OPS(PACKET_TYPE)                                            \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, psub)                                                      \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, pand)                                                      \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, por)                                                       \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, pxor)                                                      \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, pandnot)                                                   \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pdiv<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {    \
    return pdiv_complex(a, b);                                                                       \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pcmp_eq<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    const PACKET_TYPE t = PACKET_TYPE(pcmp_eq(a.v, b.v));                                            \
    return PACKET_TYPE(pand(pdupreal(t).v, pdupimag(t).v));                                          \
  }

EIGEN_CLANG_COMPLEX_BINARY_CWISE_OPS(PacketXcf);
EIGEN_CLANG_COMPLEX_BINARY_CWISE_OPS(PacketXcd);

// Binary ops that are needed on sub-packets for predux and predux_mul.
#define EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(PACKET_TYPE)                                 \
  DELEGATE_BINARY_TO_REAL_OP(PACKET_TYPE, padd)                                                   \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pmul<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return pmul_complex(a, b);                                                                    \
  }

EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(PacketXcf);
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(Packet2cf);
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(Packet4cf);
#endif
EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(PacketXcd);
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS(Packet2cd);
#endif

#define EIGEN_CLANG_PACKET_SCATTER_GATHER(PACKET_TYPE)                                                               \
  template <>                                                                                                        \
  EIGEN_STRONG_INLINE void pscatter(unpacket_traits<PACKET_TYPE>::type* to, const PACKET_TYPE& from, Index stride) { \
    constexpr int size = unpacket_traits<PACKET_TYPE>::size;                                                         \
    for (int i = 0; i < size; ++i) {                                                                                 \
      to[i * stride] = from[i];                                                                                      \
    }                                                                                                                \
  }                                                                                                                  \
  template <>                                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pgather<typename unpacket_traits<PACKET_TYPE>::type, PACKET_TYPE>(                 \
      const unpacket_traits<PACKET_TYPE>::type* from, Index stride) {                                                \
    constexpr int size = unpacket_traits<PACKET_TYPE>::size;                                                         \
    PACKET_TYPE result;                                                                                              \
    for (int i = 0; i < size; ++i) {                                                                                 \
      const unpacket_traits<PACKET_TYPE>::type from_i = from[i * stride];                                            \
      result.v[2 * i] = numext::real(from_i);                                                                        \
      result.v[2 * i + 1] = numext::imag(from_i);                                                                    \
    }                                                                                                                \
    return result;                                                                                                   \
  }

EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXcf);
EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXcd);
#undef EIGEN_CLANG_PACKET_SCATTER_GATHER

#undef DELEGATE_BINARY_TO_REAL_OP
#undef EIGEN_CLANG_COMPLEX_BINARY_CWISE_OPS
#undef EIGEN_CLANG_COMPLEX_REDUCER_BINARY_CWISE_OPS

// ------------ ternary ops -------------
template <>
EIGEN_STRONG_INLINE PacketXcf pselect<PacketXcf>(const PacketXcf& mask, const PacketXcf& a, const PacketXcf& b) {
  return PacketXcf(reinterpret_cast<PacketXf>(
      pselect(reinterpret_cast<PacketXd>(mask.v), reinterpret_cast<PacketXd>(a.v), reinterpret_cast<PacketXd>(b.v))));
}

// --- zip_in_place for complex ---
namespace detail {

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXcf>(PacketXcf& p1, PacketXcf& p2) {
  PacketXf tmp = __builtin_shufflevector(p1.v, p2.v, 0, 1, 4, 5);
  p2.v = __builtin_shufflevector(p1.v, p2.v, 2, 3, 6, 7);
  p1.v = tmp;
}
// PacketXcd at 16 bytes has 1 element, no zip_in_place needed.

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXcf>(PacketXcf& p1, PacketXcf& p2) {
  PacketXf tmp = __builtin_shufflevector(p1.v, p2.v, 0, 1, 8, 9, 2, 3, 10, 11);
  p2.v = __builtin_shufflevector(p1.v, p2.v, 4, 5, 12, 13, 6, 7, 14, 15);
  p1.v = tmp;
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXcd>(PacketXcd& p1, PacketXcd& p2) {
  PacketXd tmp = __builtin_shufflevector(p1.v, p2.v, 0, 1, 4, 5);
  p2.v = __builtin_shufflevector(p1.v, p2.v, 2, 3, 6, 7);
  p1.v = tmp;
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXcf>(PacketXcf& p1, PacketXcf& p2) {
  PacketXf tmp = __builtin_shufflevector(p1.v, p2.v, 0, 1, 16, 17, 2, 3, 18, 19, 4, 5, 20, 21, 6, 7, 22, 23);
  p2.v = __builtin_shufflevector(p1.v, p2.v, 8, 9, 24, 25, 10, 11, 26, 27, 12, 13, 28, 29, 14, 15, 30, 31);
  p1.v = tmp;
}

template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXcd>(PacketXcd& p1, PacketXcd& p2) {
  PacketXd tmp = __builtin_shufflevector(p1.v, p2.v, 0, 1, 8, 9, 2, 3, 10, 11);
  p2.v = __builtin_shufflevector(p1.v, p2.v, 4, 5, 12, 13, 6, 7, 14, 15);
  p1.v = tmp;
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

}  // namespace detail

// --- ptranspose for complex ---
// PacketXcf: valid block sizes depend on kComplexFloatSize.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXcf, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXcf, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXcf, 8>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif

// PacketXcd: valid block sizes depend on kComplexDoubleSize.
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXcd, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXcd, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(PacketXcf, PacketXf)
EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(PacketXcd, PacketXd)

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_COMPLEX_CLANG_H

// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_REDUCTIONS_CLANG_H
#define EIGEN_REDUCTIONS_CLANG_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

// --- Reductions ---
#if EIGEN_HAS_BUILTIN(__builtin_reduce_min) && EIGEN_HAS_BUILTIN(__builtin_reduce_max) && \
    EIGEN_HAS_BUILTIN(__builtin_reduce_or)
#define EIGEN_CLANG_PACKET_REDUX_MINMAX(PACKET_TYPE)                                        \
  template <>                                                                               \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type predux_min(const PACKET_TYPE& a) { \
    return __builtin_reduce_min(a);                                                         \
  }                                                                                         \
  template <>                                                                               \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type predux_max(const PACKET_TYPE& a) { \
    return __builtin_reduce_max(a);                                                         \
  }                                                                                         \
  template <>                                                                               \
  EIGEN_STRONG_INLINE bool predux_any(const PACKET_TYPE& a) {                               \
    return __builtin_reduce_or(a != 0) != 0;                                                \
  }

EIGEN_CLANG_PACKET_REDUX_MINMAX(PacketXf)
EIGEN_CLANG_PACKET_REDUX_MINMAX(PacketXd)
EIGEN_CLANG_PACKET_REDUX_MINMAX(PacketXi)
EIGEN_CLANG_PACKET_REDUX_MINMAX(PacketXl)
#undef EIGEN_CLANG_PACKET_REDUX_MINMAX
#endif

#if EIGEN_HAS_BUILTIN(__builtin_reduce_add) && EIGEN_HAS_BUILTIN(__builtin_reduce_mul)
#define EIGEN_CLANG_PACKET_REDUX_INT(PACKET_TYPE)                                                        \
  template <>                                                                                            \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type predux<PACKET_TYPE>(const PACKET_TYPE& a) {     \
    return __builtin_reduce_add(a);                                                                      \
  }                                                                                                      \
  template <>                                                                                            \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type predux_mul<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return __builtin_reduce_mul(a);                                                                      \
  }

// __builtin_reduce_{mul,add} are only defined for integer types.
EIGEN_CLANG_PACKET_REDUX_INT(PacketXi)
EIGEN_CLANG_PACKET_REDUX_INT(PacketXl)
#undef EIGEN_CLANG_PACKET_REDUX_INT
#endif

#if EIGEN_HAS_BUILTIN(__builtin_shufflevector)
namespace detail {

// Reduction helpers for different vector sizes.
// Each returns a pair of (even-sum, odd-sum) or (even-product, odd-product).

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceAdd2(
    const VectorT& a) {
  return {a[0], a[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceAdd4(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1) + __builtin_shufflevector(a, a, 2, 3);
  return {t1[0], t1[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceAdd8(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1, 2, 3) + __builtin_shufflevector(a, a, 4, 5, 6, 7);
  const auto t2 = __builtin_shufflevector(t1, t1, 0, 1) + __builtin_shufflevector(t1, t1, 2, 3);
  return {t2[0], t2[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceAdd16(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1, 2, 3, 4, 5, 6, 7) +
                  __builtin_shufflevector(a, a, 8, 9, 10, 11, 12, 13, 14, 15);
  const auto t2 = __builtin_shufflevector(t1, t1, 0, 1, 2, 3) + __builtin_shufflevector(t1, t1, 4, 5, 6, 7);
  const auto t3 = __builtin_shufflevector(t2, t2, 0, 1) + __builtin_shufflevector(t2, t2, 2, 3);
  return {t3[0], t3[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceMul2(
    const VectorT& a) {
  return {a[0], a[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceMul4(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1) * __builtin_shufflevector(a, a, 2, 3);
  return {t1[0], t1[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceMul8(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1, 2, 3) * __builtin_shufflevector(a, a, 4, 5, 6, 7);
  const auto t2 = __builtin_shufflevector(t1, t1, 0, 1) * __builtin_shufflevector(t1, t1, 2, 3);
  return {t2[0], t2[1]};
}

template <typename VectorT>
EIGEN_STRONG_INLINE std::pair<scalar_type_of_vector_t<VectorT>, scalar_type_of_vector_t<VectorT>> ReduceMul16(
    const VectorT& a) {
  const auto t1 = __builtin_shufflevector(a, a, 0, 1, 2, 3, 4, 5, 6, 7) *
                  __builtin_shufflevector(a, a, 8, 9, 10, 11, 12, 13, 14, 15);
  const auto t2 = __builtin_shufflevector(t1, t1, 0, 1, 2, 3) * __builtin_shufflevector(t1, t1, 4, 5, 6, 7);
  const auto t3 = __builtin_shufflevector(t2, t2, 0, 1) * __builtin_shufflevector(t2, t2, 2, 3);
  return {t3[0], t3[1]};
}
}  // namespace detail

// --- predux and predux_mul for float ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE float predux<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceAdd4(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE float predux_mul<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceMul4(a);
  return even * odd;
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE float predux<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceAdd8(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE float predux_mul<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceMul8(a);
  return even * odd;
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE float predux<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceAdd16(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE float predux_mul<PacketXf>(const PacketXf& a) {
  float even, odd;
  std::tie(even, odd) = detail::ReduceMul16(a);
  return even * odd;
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- predux and predux_mul for double ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE double predux<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceAdd2(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE double predux_mul<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceMul2(a);
  return even * odd;
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE double predux<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceAdd4(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE double predux_mul<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceMul4(a);
  return even * odd;
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE double predux<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceAdd8(a);
  return even + odd;
}
template <>
EIGEN_STRONG_INLINE double predux_mul<PacketXd>(const PacketXd& a) {
  double even, odd;
  std::tie(even, odd) = detail::ReduceMul8(a);
  return even * odd;
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- predux for complex<float> ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<PacketXcf>(const PacketXcf& a) {
  float re, im;
  std::tie(re, im) = detail::ReduceAdd4(a.v);
  return std::complex<float>(re, im);
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<PacketXcf>(const PacketXcf& a) {
  float re, im;
  std::tie(re, im) = detail::ReduceAdd8(a.v);
  return std::complex<float>(re, im);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<PacketXcf>(const PacketXcf& a) {
  float re, im;
  std::tie(re, im) = detail::ReduceAdd16(a.v);
  return std::complex<float>(re, im);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- predux for complex<double> ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<PacketXcd>(const PacketXcd& a) {
  // 1 complex double: just return it
  return a[0];
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<PacketXcd>(const PacketXcd& a) {
  double re, im;
  std::tie(re, im) = detail::ReduceAdd4(a.v);
  return std::complex<double>(re, im);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<PacketXcd>(const PacketXcd& a) {
  double re, im;
  std::tie(re, im) = detail::ReduceAdd8(a.v);
  return std::complex<double>(re, im);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- predux_mul for complex<float> ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE std::complex<float> predux_mul<PacketXcf>(const PacketXcf& a) {
  // 2 complex floats: just multiply them
  return a[0] * a[1];
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE std::complex<float> predux_mul<PacketXcf>(const PacketXcf& a) {
  // 4 complex floats: split into 2+2, multiply, then scalar multiply
  const Packet2cf lower2 = Packet2cf(__builtin_shufflevector(a.v, a.v, 0, 1, 2, 3));
  const Packet2cf upper2 = Packet2cf(__builtin_shufflevector(a.v, a.v, 4, 5, 6, 7));
  const Packet2cf prod2 = pmul<Packet2cf>(lower2, upper2);
  return prod2[0] * prod2[1];
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE std::complex<float> predux_mul<PacketXcf>(const PacketXcf& a) {
  // 8 complex floats: 8->4->2->scalar
  const Packet4cf lower4 = Packet4cf(__builtin_shufflevector(a.v, a.v, 0, 1, 2, 3, 4, 5, 6, 7));
  const Packet4cf upper4 = Packet4cf(__builtin_shufflevector(a.v, a.v, 8, 9, 10, 11, 12, 13, 14, 15));
  const Packet4cf prod4 = pmul<Packet4cf>(lower4, upper4);
  const Packet2cf lower2 = Packet2cf(__builtin_shufflevector(prod4.v, prod4.v, 0, 1, 2, 3));
  const Packet2cf upper2 = Packet2cf(__builtin_shufflevector(prod4.v, prod4.v, 4, 5, 6, 7));
  const Packet2cf prod2 = pmul<Packet2cf>(lower2, upper2);
  return prod2[0] * prod2[1];
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- predux_mul for complex<double> ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE std::complex<double> predux_mul<PacketXcd>(const PacketXcd& a) {
  // 1 complex double: just return it
  return a[0];
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE std::complex<double> predux_mul<PacketXcd>(const PacketXcd& a) {
  // 2 complex doubles: just multiply them
  return a[0] * a[1];
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE std::complex<double> predux_mul<PacketXcd>(const PacketXcd& a) {
  // 4 complex doubles: split into 2+2, multiply, then scalar multiply
  const Packet2cd lower2 = Packet2cd(__builtin_shufflevector(a.v, a.v, 0, 1, 2, 3));
  const Packet2cd upper2 = Packet2cd(__builtin_shufflevector(a.v, a.v, 4, 5, 6, 7));
  const Packet2cd prod2 = pmul<Packet2cd>(lower2, upper2);
  return prod2[0] * prod2[1];
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

#endif

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_REDUCTIONS_CLANG_H

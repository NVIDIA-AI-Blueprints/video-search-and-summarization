// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TYPE_CASTING_CLANG_H
#define EIGEN_TYPE_CASTING_CLANG_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

//==============================================================================
// preinterpret
//==============================================================================
template <>
EIGEN_STRONG_INLINE PacketXf preinterpret<PacketXf, PacketXi>(const PacketXi& a) {
  return reinterpret_cast<PacketXf>(a);
}
template <>
EIGEN_STRONG_INLINE PacketXi preinterpret<PacketXi, PacketXf>(const PacketXf& a) {
  return reinterpret_cast<PacketXi>(a);
}

template <>
EIGEN_STRONG_INLINE PacketXd preinterpret<PacketXd, PacketXl>(const PacketXl& a) {
  return reinterpret_cast<PacketXd>(a);
}
template <>
EIGEN_STRONG_INLINE PacketXl preinterpret<PacketXl, PacketXd>(const PacketXd& a) {
  return reinterpret_cast<PacketXl>(a);
}

//==============================================================================
// pcast
//==============================================================================
#if EIGEN_HAS_BUILTIN(__builtin_convertvector)
// Float-to-int conversions: __builtin_convertvector has UB for NaN/inf/
// out-of-range inputs. Replace NaN with 0 before converting so that
// pldexp_fast (which may pass NaN exponents) doesn't trigger UB.
template <>
EIGEN_STRONG_INLINE PacketXi pcast<PacketXf, PacketXi>(const PacketXf& a) {
  const PacketXf safe = a == a ? a : PacketXf(0);
  return __builtin_convertvector(safe, PacketXi);
}
template <>
EIGEN_STRONG_INLINE PacketXf pcast<PacketXi, PacketXf>(const PacketXi& a) {
  return __builtin_convertvector(a, PacketXf);
}

template <>
EIGEN_STRONG_INLINE PacketXl pcast<PacketXd, PacketXl>(const PacketXd& a) {
  const PacketXd safe = a == a ? a : PacketXd(0);
  return __builtin_convertvector(safe, PacketXl);
}
template <>
EIGEN_STRONG_INLINE PacketXd pcast<PacketXl, PacketXd>(const PacketXl& a) {
  return __builtin_convertvector(a, PacketXd);
}

// float -> double: converts lower half of floats to doubles
// double -> float: converts two PacketXd to one PacketXf
// int32 -> int64: converts lower half of int32s to int64s
// int64 -> int32: converts two PacketXl to one PacketXi

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

// float -> double: converts lower 2 floats to 2 doubles
template <>
EIGEN_STRONG_INLINE PacketXd pcast<PacketXf, PacketXd>(const PacketXf& a) {
  using HalfFloat = detail::VectorType<float, 2>;
  HalfFloat lo = __builtin_shufflevector(a, a, 0, 1);
  return __builtin_convertvector(lo, PacketXd);
}

// double -> float: converts two PacketXd (2 doubles each) to one PacketXf (4 floats)
template <>
EIGEN_STRONG_INLINE PacketXf pcast<PacketXd, PacketXf>(const PacketXd& a, const PacketXd& b) {
  using HalfFloat = detail::VectorType<float, 2>;
  HalfFloat lo = __builtin_convertvector(a, HalfFloat);
  HalfFloat hi = __builtin_convertvector(b, HalfFloat);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3);
}

// int32 -> int64: converts lower 2 int32s to 2 int64s
template <>
EIGEN_STRONG_INLINE PacketXl pcast<PacketXi, PacketXl>(const PacketXi& a) {
  using HalfInt = detail::VectorType<int32_t, 2>;
  HalfInt lo = __builtin_shufflevector(a, a, 0, 1);
  return __builtin_convertvector(lo, PacketXl);
}

// int64 -> int32: converts two PacketXl (2 int64s each) to one PacketXi (4 int32s)
template <>
EIGEN_STRONG_INLINE PacketXi pcast<PacketXl, PacketXi>(const PacketXl& a, const PacketXl& b) {
  using HalfInt = detail::VectorType<int32_t, 2>;
  HalfInt lo = __builtin_convertvector(a, HalfInt);
  HalfInt hi = __builtin_convertvector(b, HalfInt);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3);
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

// float -> double: converts lower 4 floats to 4 doubles
template <>
EIGEN_STRONG_INLINE PacketXd pcast<PacketXf, PacketXd>(const PacketXf& a) {
  using HalfFloat = detail::VectorType<float, 4>;
  HalfFloat lo = __builtin_shufflevector(a, a, 0, 1, 2, 3);
  return __builtin_convertvector(lo, PacketXd);
}

// double -> float: converts two PacketXd (4 doubles each) to one PacketXf (8 floats)
template <>
EIGEN_STRONG_INLINE PacketXf pcast<PacketXd, PacketXf>(const PacketXd& a, const PacketXd& b) {
  using HalfFloat = detail::VectorType<float, 4>;
  HalfFloat lo = __builtin_convertvector(a, HalfFloat);
  HalfFloat hi = __builtin_convertvector(b, HalfFloat);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3, 4, 5, 6, 7);
}

// int32 -> int64: converts lower 4 int32s to 4 int64s
template <>
EIGEN_STRONG_INLINE PacketXl pcast<PacketXi, PacketXl>(const PacketXi& a) {
  using HalfInt = detail::VectorType<int32_t, 4>;
  HalfInt lo = __builtin_shufflevector(a, a, 0, 1, 2, 3);
  return __builtin_convertvector(lo, PacketXl);
}

// int64 -> int32: converts two PacketXl (4 int64s each) to one PacketXi (8 int32s)
template <>
EIGEN_STRONG_INLINE PacketXi pcast<PacketXl, PacketXi>(const PacketXl& a, const PacketXl& b) {
  using HalfInt = detail::VectorType<int32_t, 4>;
  HalfInt lo = __builtin_convertvector(a, HalfInt);
  HalfInt hi = __builtin_convertvector(b, HalfInt);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3, 4, 5, 6, 7);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

// float -> double: converts lower 8 floats to 8 doubles
template <>
EIGEN_STRONG_INLINE PacketXd pcast<PacketXf, PacketXd>(const PacketXf& a) {
  using HalfFloat = detail::VectorType<float, 8>;
  HalfFloat lo = __builtin_shufflevector(a, a, 0, 1, 2, 3, 4, 5, 6, 7);
  return __builtin_convertvector(lo, PacketXd);
}

// double -> float: converts two PacketXd to one PacketXf
template <>
EIGEN_STRONG_INLINE PacketXf pcast<PacketXd, PacketXf>(const PacketXd& a, const PacketXd& b) {
  using HalfFloat = detail::VectorType<float, 8>;
  HalfFloat lo = __builtin_convertvector(a, HalfFloat);
  HalfFloat hi = __builtin_convertvector(b, HalfFloat);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
}

// int32 -> int64: converts lower 8 int32s to 8 int64s
template <>
EIGEN_STRONG_INLINE PacketXl pcast<PacketXi, PacketXl>(const PacketXi& a) {
  using HalfInt = detail::VectorType<int32_t, 8>;
  HalfInt lo = __builtin_shufflevector(a, a, 0, 1, 2, 3, 4, 5, 6, 7);
  return __builtin_convertvector(lo, PacketXl);
}

// int64 -> int32: converts two PacketXl to one PacketXi
template <>
EIGEN_STRONG_INLINE PacketXi pcast<PacketXl, PacketXi>(const PacketXl& a, const PacketXl& b) {
  using HalfInt = detail::VectorType<int32_t, 8>;
  HalfInt lo = __builtin_convertvector(a, HalfInt);
  HalfInt hi = __builtin_convertvector(b, HalfInt);
  return __builtin_shufflevector(lo, hi, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES
#endif

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_TYPE_CASTING_CLANG_H

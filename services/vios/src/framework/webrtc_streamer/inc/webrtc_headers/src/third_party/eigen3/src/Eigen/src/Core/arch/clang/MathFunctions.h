// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_MATH_FUNCTIONS_CLANG_H
#define EIGEN_MATH_FUNCTIONS_CLANG_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <>
EIGEN_STRONG_INLINE PacketXf pfrexp<PacketXf>(const PacketXf& a, PacketXf& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE PacketXd pfrexp<PacketXd>(const PacketXd& a, PacketXd& exponent) {
  return pfrexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE PacketXf pldexp<PacketXf>(const PacketXf& a, const PacketXf& exponent) {
  return pldexp_generic(a, exponent);
}

template <>
EIGEN_STRONG_INLINE PacketXd pldexp<PacketXd>(const PacketXd& a, const PacketXd& exponent) {
  return pldexp_generic(a, exponent);
}

EIGEN_INSTANTIATE_GENERIC_MATH_FUNCS_FLOAT(PacketXf)
EIGEN_INSTANTIATE_GENERIC_MATH_FUNCS_DOUBLE(PacketXd)

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_MATH_FUNCTIONS_CLANG_H

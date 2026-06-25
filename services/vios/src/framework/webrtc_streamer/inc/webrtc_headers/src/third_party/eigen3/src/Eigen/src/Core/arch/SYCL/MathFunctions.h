// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Mehdi Goli    Codeplay Software Ltd.
// Ralph Potter  Codeplay Software Ltd.
// Luke Iwanski  Codeplay Software Ltd.
// Contact: <eigen@codeplay.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-FileCopyrightText: The Eigen Authors
// SPDX-License-Identifier: MPL-2.0

/*****************************************************************
 * MathFunctions.h
 *
 * \brief:
 *  MathFunctions
 *
 *****************************************************************/

#ifndef EIGEN_MATH_FUNCTIONS_SYCL_H
#define EIGEN_MATH_FUNCTIONS_SYCL_H
// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

// Make sure this is only available when targeting a GPU: we don't want to
// introduce conflicts between these packet_traits definitions and the ones
// we'll use on the host side (SSE, AVX, ...)
#if defined(SYCL_DEVICE_ONLY)

// Generic macro for unary SYCL math functions.
#define SYCL_PACKET_FUNCTION(EIGEN_FUNC, SYCL_FUNC, PACKET)                          \
  template <>                                                                        \
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PACKET EIGEN_FUNC<PACKET>(const PACKET& a) { \
    return cl::sycl::SYCL_FUNC(a);                                                   \
  }

// Instantiate a unary function for the standard set of SYCL vector types.
#define SYCL_UNARY_FUNCTION(EIGEN_FUNC, SYCL_FUNC)                 \
  SYCL_PACKET_FUNCTION(EIGEN_FUNC, SYCL_FUNC, cl::sycl::cl_half8)  \
  SYCL_PACKET_FUNCTION(EIGEN_FUNC, SYCL_FUNC, cl::sycl::cl_float4) \
  SYCL_PACKET_FUNCTION(EIGEN_FUNC, SYCL_FUNC, cl::sycl::cl_double2)

SYCL_UNARY_FUNCTION(plog, log)
SYCL_UNARY_FUNCTION(plog1p, log1p)
SYCL_UNARY_FUNCTION(plog10, log10)
SYCL_UNARY_FUNCTION(pexpm1, expm1)
SYCL_UNARY_FUNCTION(psqrt, sqrt)
SYCL_UNARY_FUNCTION(prsqrt, rsqrt)
SYCL_UNARY_FUNCTION(psin, sin)
SYCL_UNARY_FUNCTION(pcos, cos)
SYCL_UNARY_FUNCTION(ptan, tan)
SYCL_UNARY_FUNCTION(pasin, asin)
SYCL_UNARY_FUNCTION(pacos, acos)
SYCL_UNARY_FUNCTION(patan, atan)
SYCL_UNARY_FUNCTION(psinh, sinh)
SYCL_UNARY_FUNCTION(pcosh, cosh)
SYCL_UNARY_FUNCTION(ptanh, tanh)
SYCL_UNARY_FUNCTION(pround, round)
SYCL_UNARY_FUNCTION(print, rint)
SYCL_UNARY_FUNCTION(pfloor, floor)

// pexp has additional scalar type instantiations.
SYCL_UNARY_FUNCTION(pexp, exp)
SYCL_PACKET_FUNCTION(pexp, exp, cl::sycl::cl_half)
SYCL_PACKET_FUNCTION(pexp, exp, cl::sycl::cl_float)

// pceil uses cl_half (scalar) instead of cl_half8 (vector) — preserving original behavior.
SYCL_PACKET_FUNCTION(pceil, ceil, cl::sycl::cl_half)
SYCL_PACKET_FUNCTION(pceil, ceil, cl::sycl::cl_float4)
SYCL_PACKET_FUNCTION(pceil, ceil, cl::sycl::cl_double2)

#undef SYCL_UNARY_FUNCTION
#undef SYCL_PACKET_FUNCTION

// Binary min/max functions.
#define SYCL_BINARY_FUNCTION(EIGEN_FUNC, SYCL_FUNC, PACKET)                                           \
  template <>                                                                                         \
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PACKET EIGEN_FUNC<PACKET>(const PACKET& a, const PACKET& b) { \
    return cl::sycl::SYCL_FUNC(a, b);                                                                 \
  }

SYCL_BINARY_FUNCTION(pmin, fmin, cl::sycl::cl_half8)
SYCL_BINARY_FUNCTION(pmin, fmin, cl::sycl::cl_float4)
SYCL_BINARY_FUNCTION(pmin, fmin, cl::sycl::cl_double2)
SYCL_BINARY_FUNCTION(pmax, fmax, cl::sycl::cl_half8)
SYCL_BINARY_FUNCTION(pmax, fmax, cl::sycl::cl_float4)
SYCL_BINARY_FUNCTION(pmax, fmax, cl::sycl::cl_double2)

#undef SYCL_BINARY_FUNCTION

// pldexp requires integer conversion of the exponent.
#define SYCL_PLDEXP(packet_type)                                                                                  \
  template <>                                                                                                     \
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE packet_type pldexp(const packet_type& a, const packet_type& exponent) {   \
    return cl::sycl::ldexp(a, exponent.template convert<cl::sycl::cl_int, cl::sycl::rounding_mode::automatic>()); \
  }

SYCL_PLDEXP(cl::sycl::cl_half8)
SYCL_PLDEXP(cl::sycl::cl_float4)
SYCL_PLDEXP(cl::sycl::cl_double2)
#undef SYCL_PLDEXP

#endif
}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_MATH_FUNCTIONS_SYCL_H

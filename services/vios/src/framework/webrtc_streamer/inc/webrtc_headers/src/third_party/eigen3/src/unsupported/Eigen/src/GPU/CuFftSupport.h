// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// cuFFT support utilities: error checking macro, type mapping.

#ifndef EIGEN_GPU_CUFFT_SUPPORT_H
#define EIGEN_GPU_CUFFT_SUPPORT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSupport.h"
#include <cufft.h>

namespace Eigen {
namespace gpu {
namespace internal {

// ---- Error checking ---------------------------------------------------------

#define EIGEN_CUFFT_CHECK(x)                                       \
  do {                                                             \
    cufftResult _r = (x);                                          \
    eigen_assert(_r == CUFFT_SUCCESS && "cuFFT call failed: " #x); \
    EIGEN_UNUSED_VARIABLE(_r);                                     \
  } while (0)

// ---- Scalar → cufftType traits ----------------------------------------------

template <typename Scalar>
struct cufft_c2c_type;

template <>
struct cufft_c2c_type<float> {
  static constexpr cufftType value = CUFFT_C2C;
};
template <>
struct cufft_c2c_type<double> {
  static constexpr cufftType value = CUFFT_Z2Z;
};

template <typename Scalar>
struct cufft_r2c_type;

template <>
struct cufft_r2c_type<float> {
  static constexpr cufftType value = CUFFT_R2C;
};
template <>
struct cufft_r2c_type<double> {
  static constexpr cufftType value = CUFFT_D2Z;
};

template <typename Scalar>
struct cufft_c2r_type;

template <>
struct cufft_c2r_type<float> {
  static constexpr cufftType value = CUFFT_C2R;
};
template <>
struct cufft_c2r_type<double> {
  static constexpr cufftType value = CUFFT_Z2D;
};

// ---- Type-dispatched cuFFT execution ----------------------------------------

// C2C
inline cufftResult cufftExecC2C_dispatch(cufftHandle plan, std::complex<float>* in, std::complex<float>* out,
                                         int direction) {
  return cufftExecC2C(plan, reinterpret_cast<cufftComplex*>(in), reinterpret_cast<cufftComplex*>(out), direction);
}
inline cufftResult cufftExecC2C_dispatch(cufftHandle plan, std::complex<double>* in, std::complex<double>* out,
                                         int direction) {
  return cufftExecZ2Z(plan, reinterpret_cast<cufftDoubleComplex*>(in), reinterpret_cast<cufftDoubleComplex*>(out),
                      direction);
}

// R2C
inline cufftResult cufftExecR2C_dispatch(cufftHandle plan, float* in, std::complex<float>* out) {
  return cufftExecR2C(plan, in, reinterpret_cast<cufftComplex*>(out));
}
inline cufftResult cufftExecR2C_dispatch(cufftHandle plan, double* in, std::complex<double>* out) {
  return cufftExecD2Z(plan, in, reinterpret_cast<cufftDoubleComplex*>(out));
}

// C2R
inline cufftResult cufftExecC2R_dispatch(cufftHandle plan, std::complex<float>* in, float* out) {
  return cufftExecC2R(plan, reinterpret_cast<cufftComplex*>(in), out);
}
inline cufftResult cufftExecC2R_dispatch(cufftHandle plan, std::complex<double>* in, double* out) {
  return cufftExecZ2D(plan, reinterpret_cast<cufftDoubleComplex*>(in), out);
}

}  // namespace internal
}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_CUFFT_SUPPORT_H
